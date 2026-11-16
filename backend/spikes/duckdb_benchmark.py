"""Spike 2 (plan §3, Week 0): Parquet/DuckDB memory benchmark inside the worker container.

Simulates a paged 500k-row warehouse result streamed to tenant-scoped Parquet in GCS (or, with
BENCH_SOURCE=gcs, uses the artifact written by bq_to_parquet.py), then runs 5 chained,
policy-checked DuckDB follow-ups and a typed-spec chart render with external access locked down. Run:

    docker compose run --rm -v ./backend/spikes:/app/spikes:ro worker python /app/spikes/duckdb_benchmark.py
"""

from __future__ import annotations

import io
import os
import resource
import statistics
import sys
import tempfile
import time
from typing import Literal

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from google.auth.credentials import AnonymousCredentials
from google.cloud import storage
from pydantic import BaseModel

from odn.platform.policy import SqlPolicy

ROWS = int(os.environ.get("BENCH_ROWS", "500000"))
PAGE_ROWS = 50_000
ITERATIONS = int(os.environ.get("BENCH_ITERATIONS", "20"))
BUCKET = os.environ.get("ODN_GCS_BUCKET", "odn-workspace-local")
OBJECT = "tenant_bench/session_bench/query_1.parquet"
MEMORY_LIMIT = os.environ.get("ODN_WORKER_MEMORY_LIMIT", "1536MB")
SOURCE = os.environ.get("BENCH_SOURCE", "synthetic")
D1, D2 = os.environ.get("BENCH_DIMS", "region,category").split(",")
MEASURE = os.environ.get("BENCH_MEASURE", "amount")

FOLLOW_UPS = [
    f'SELECT * FROM q_1 WHERE "{MEASURE}" IS NOT NULL',
    f'SELECT "{D1}", "{D2}", SUM("{MEASURE}") AS total, COUNT(*) AS n FROM q_2 GROUP BY ALL',
    f'SELECT *, total / NULLIF(SUM(total) OVER (PARTITION BY "{D1}"), 0) AS share FROM q_3',
    f'SELECT *, RANK() OVER (PARTITION BY "{D1}" ORDER BY total DESC) AS rnk FROM q_4',
    (
        f'SELECT "{D1}", quantile_cont(total, 0.95) AS p95_total, SUM(n) AS row_count FROM q_5 '
        f'GROUP BY "{D1}" ORDER BY p95_total DESC'
    ),
]


class ChartSpec(BaseModel):
    type: Literal["bar", "line"]
    source: str
    x: str
    y: str
    agg: Literal["sum", "avg", "count"] = "sum"


def gcs_client() -> storage.Client:
    return storage.Client(project="local", credentials=AnonymousCredentials())


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux reports KiB


def synthetic_page(offset: int, n: int) -> pa.Table:
    con = duckdb.connect()
    return con.sql(
        f"""
        SELECT i AS order_id,
               (i * 7) % 100000 AS customer_id,
               ['West','East','Central','South'][1 + i % 4] AS region,
               'cat_' || (i % 25) AS category,
               TIMESTAMP '2024-01-01' + INTERVAL (i % 540) DAY + INTERVAL (i % 86400) SECOND AS order_ts,
               CASE WHEN i % 97 = 0 THEN NULL ELSE round(10 + (i * 37) % 9990 + random(), 2) END AS amount,
               md5(i::VARCHAR) AS note
        FROM range({offset}, {offset + n}) t(i)
        """
    ).arrow().read_all()


def write_result_to_gcs() -> float:
    """Stream pages into a Parquet file and upload to the tenant/session path."""
    start = time.perf_counter()
    with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
        writer: pq.ParquetWriter | None = None
        for offset in range(0, ROWS, PAGE_ROWS):
            page = synthetic_page(offset, min(PAGE_ROWS, ROWS - offset))
            writer = writer or pq.ParquetWriter(tmp.name, page.schema, compression="zstd")
            writer.write_table(page)
        assert writer is not None
        writer.close()
        size_mb = os.path.getsize(tmp.name) / 1e6
        gcs_client().bucket(BUCKET).blob(OBJECT).upload_from_filename(tmp.name)
    print(f"write+upload: {time.perf_counter() - start:.2f}s, parquet {size_mb:.1f} MB, rss {rss_mb():.0f} MB")
    return size_mb


def load_artifact() -> pa.Table:
    data = gcs_client().bucket(BUCKET).blob(OBJECT).download_as_bytes()
    return pq.read_table(io.BytesIO(data))


def locked_connection(artifact: pa.Table) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(config={"memory_limit": MEMORY_LIMIT, "threads": 2})
    con.register("q_1", artifact)
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


def render_chart(con: duckdb.DuckDBPyConnection, spec: ChartSpec, policy: SqlPolicy) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    agg = {"sum": "SUM", "avg": "AVG", "count": "COUNT"}[spec.agg]
    columns = {c.lower() for c in con.table(spec.source).columns}
    if spec.x.lower() not in columns or spec.y.lower() not in columns:
        raise ValueError("chart fields not in artifact schema")
    sql = f'SELECT "{spec.x}" AS x, {agg}("{spec.y}") AS y FROM {spec.source} GROUP BY 1 ORDER BY 1'
    decision = policy.validate(sql)
    if not decision.allowed:
        raise ValueError(decision.reason)
    rows = con.execute(sql).fetchall()
    fig, ax = plt.subplots(figsize=(8, 4))
    xs, ys = [str(r[0]) for r in rows], [r[1] or 0 for r in rows]
    (ax.bar if spec.type == "bar" else ax.plot)(xs, ys)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    plt.close(fig)
    return buf.getvalue()


def escape_attempts(con: duckdb.DuckDBPyConnection) -> None:
    """Engine-level lockdown must hold even if the AST policy were bypassed."""
    for sql in [
        "SELECT * FROM read_text('/etc/passwd')",
        "SELECT * FROM '/etc/hostname'",
        "SET enable_external_access = true",
        "ATTACH '/tmp/x.db' AS x",
    ]:
        try:
            con.execute(sql)
            print(f"ESCAPE NOT BLOCKED: {sql}")
            sys.exit(1)
        except duckdb.Error as exc:
            print(f"blocked by engine: {sql!r} -> {type(exc).__name__}")


def main() -> None:
    print(f"rows={ROWS} iterations={ITERATIONS} memory_limit={MEMORY_LIMIT} source={SOURCE}")
    if SOURCE == "gcs":
        blob = gcs_client().bucket(BUCKET).get_blob(OBJECT)
        size_mb = (blob.size or 0) / 1e6 if blob else 0.0
    else:
        size_mb = write_result_to_gcs()

    t0 = time.perf_counter()
    artifact = load_artifact()
    print(f"download+read: {time.perf_counter() - t0:.2f}s, {artifact.num_rows} rows, "
          f"{artifact.num_columns} cols, arrow {artifact.nbytes / 1e6:.0f} MB in memory, rss {rss_mb():.0f} MB")

    con = locked_connection(artifact)
    escape_attempts(con)

    chain_times: list[float] = []
    step_times: list[list[float]] = [[] for _ in FOLLOW_UPS]
    chart_times: list[float] = []
    png = b""
    for _ in range(ITERATIONS):
        chain_start = time.perf_counter()
        allowed = {"q_1"}
        for i, sql in enumerate(FOLLOW_UPS):
            policy = SqlPolicy(dialect="duckdb", allowed_objects=frozenset(allowed))
            decision = policy.validate(sql)
            if not decision.allowed:
                raise SystemExit(f"follow-up {i + 1} rejected: {decision.reason}")
            s = time.perf_counter()
            result = con.sql(sql).arrow().read_all()
            step_times[i].append(time.perf_counter() - s)
            name = f"q_{i + 2}"
            con.register(name, result)
            allowed.add(name)
        chain_times.append(time.perf_counter() - chain_start)

        s = time.perf_counter()
        png = render_chart(
            con,
            ChartSpec(type="bar", source="q_3", x=D1, y="total"),
            SqlPolicy(dialect="duckdb", allowed_objects=frozenset(allowed)),
        )
        chart_times.append(time.perf_counter() - s)
        for name in allowed - {"q_1"}:
            con.unregister(name)

    def p95(xs: list[float]) -> float:
        return statistics.quantiles(xs, n=20)[18] if len(xs) >= 2 else xs[0]

    print("\n--- results ---")
    print(f"parquet size: {size_mb:.1f} MB")
    for i, ts in enumerate(step_times):
        print(f"follow-up {i + 1}: p50 {statistics.median(ts) * 1000:.0f} ms, p95 {p95(ts) * 1000:.0f} ms")
    print(f"5-step chain:  p50 {statistics.median(chain_times) * 1000:.0f} ms, p95 {p95(chain_times) * 1000:.0f} ms")
    print(f"chart render:  p50 {statistics.median(chart_times) * 1000:.0f} ms, "
          f"p95 {p95(chart_times) * 1000:.0f} ms, png {len(png) / 1024:.0f} KB")
    print(f"peak RSS: {rss_mb():.0f} MB")


if __name__ == "__main__":
    main()
