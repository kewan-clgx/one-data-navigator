"""Isolated result-workspace worker: DuckDB over Parquet artifacts, locked down per request.

Internal only: called by the API, which decides which artifacts a caller may touch.
"""

from __future__ import annotations

import hmac
import io
import os
import re
import tempfile
import threading
import time
from collections.abc import Iterator
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from odn.config import settings

app = FastAPI(title="One Data Navigator Result Workspace Worker")

_NAME_RE = re.compile(r"^q[0-9]{1,6}$")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "worker"}


def _require_caller(x_odn_worker_token: Annotated[str | None, Header()] = None) -> None:
    if not settings.worker_token or not hmac.compare_digest(x_odn_worker_token or "", settings.worker_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unauthorized")


class QueryIn(BaseModel):
    sql: str = Field(min_length=1, max_length=100_000)
    inputs: dict[str, str]
    output_key: str
    preview_rows: int = Field(default=200, ge=0, le=1000)
    max_rows: int = Field(default=500_000, ge=1)
    max_arrow_bytes: int = Field(default=600 * 1024**2, ge=1)
    timeout_s: float = Field(default=60, gt=0, le=600)


class QueryOut(BaseModel):
    row_count: int
    arrow_bytes: int
    truncated: bool
    columns: list[dict[str, Any]]
    preview: list[dict[str, Any]]
    duration_ms: int


class RowsIn(BaseModel):
    object_key: str
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1000)
    order_by: str | None = None
    descending: bool = False


class RowsOut(BaseModel):
    total: int
    rows: list[dict[str, Any]]


class ChartIn(BaseModel):
    object_key: str
    type: Literal["bar", "line", "scatter"]
    x: str
    y: str | None = None
    agg: Literal["none", "sum", "avg", "count", "min", "max"] = "sum"
    series: str | None = None
    sort: Literal["x", "y_desc", "y_asc"] = "x"
    limit: int = Field(default=50, ge=1, le=500)


class ChartOut(BaseModel):
    sql: str
    points: list[dict[str, Any]]
    truncated: bool


class CsvIn(BaseModel):
    object_key: str


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def chart_sql(spec: ChartIn, columns: list[str]) -> str:
    """Platform-built aggregation SQL over table `t`; every identifier must be an artifact column."""
    for col in (spec.x, spec.y, spec.series):
        if col is not None and col not in columns:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown column: {col}")
    if spec.agg not in ("none", "count") and spec.y is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "y is required for this aggregation")
    x = _ident(spec.x)
    keys = [f"{x} AS x"] + ([f"{_ident(spec.series)} AS series"] if spec.series else [])
    if spec.agg == "none":
        y = f"{_ident(spec.y)} AS y" if spec.y else "1 AS y"
        select, group = ", ".join([*keys, y]), ""
    else:
        measure = "COUNT(*)" if spec.agg == "count" else f"{spec.agg.upper()}({_ident(spec.y or '')})"
        select = ", ".join([*keys, f"{measure} AS y"])
        group = " GROUP BY ALL"
    order = {"x": "x", "y_desc": "y DESC NULLS LAST", "y_asc": "y ASC NULLS LAST"}[spec.sort]
    # One extra row tells us whether the chart was truncated.
    return f"SELECT {select} FROM t{group} ORDER BY {order} LIMIT {spec.limit + 1}"


def _bucket():
    from odn.platform.workspace.storage import gcs_client

    return gcs_client().bucket(settings.gcs_bucket)


def _check_key(key: str) -> str:
    prefix = settings.gcs_prefix.strip("/") + "/"
    if not key.startswith(prefix) or ".." in key or not key.endswith(".parquet"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid object key")
    return key


def _load(key: str):
    import pyarrow.parquet as pq

    # Read from a temp file, then drop it, so Parquet bytes and Arrow are not both held in memory.
    fd, path = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)
    try:
        _bucket().blob(_check_key(key)).download_to_filename(path)
        return pq.read_table(path)
    finally:
        os.remove(path)


def _locked_connection(tables: dict[str, Any]):
    import duckdb

    con = duckdb.connect(config={"memory_limit": settings.worker_memory_limit, "threads": 2})
    for name, table in tables.items():
        con.register(name, table)
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


@app.post("/v1/query", dependencies=[Depends(_require_caller)])
def query(req: QueryIn) -> QueryOut:
    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq

    from odn.platform.policy.sql_policy import SqlPolicy
    from odn.platform.workspace.rows import column_info, preview_rows

    if not all(_NAME_RE.match(n) for n in req.inputs):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid artifact name")
    # Defense in depth: the API validated already; the worker re-checks against exactly its inputs.
    decision = SqlPolicy(dialect="duckdb", allowed_objects=frozenset(req.inputs)).validate(req.sql)
    if not decision.allowed:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"rejected": decision.reason})
    output_key = _check_key(req.output_key)

    start = time.perf_counter()
    con = _locked_connection({name: _load(key) for name, key in req.inputs.items()})
    timer = threading.Timer(req.timeout_s, con.interrupt)
    timer.start()
    batches: list[pa.RecordBatch] = []
    rows = nbytes = 0
    truncated = False
    try:
        reader = con.sql(req.sql).arrow()
        schema = reader.schema
        for batch in reader:
            if rows + batch.num_rows > req.max_rows:
                batch = batch.slice(0, req.max_rows - rows)
                truncated = True
            if nbytes + batch.nbytes > req.max_arrow_bytes:
                truncated = True
                break
            batches.append(batch)
            rows += batch.num_rows
            nbytes += batch.nbytes
            if truncated:
                break
    except duckdb.InterruptException:
        raise HTTPException(status.HTTP_408_REQUEST_TIMEOUT, {"failed": "query timed out"}) from None
    except duckdb.Error as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {"failed": str(exc)[:500]}) from None
    finally:
        timer.cancel()
        con.close()

    table = pa.Table.from_batches(batches, schema=schema)
    fd, path = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)
    try:
        pq.write_table(table, path, compression="zstd")
        _bucket().blob(output_key).upload_from_filename(path)
    finally:
        os.remove(path)

    return QueryOut(
        row_count=rows,
        arrow_bytes=nbytes,
        truncated=truncated,
        columns=column_info(schema),
        preview=preview_rows(table, req.preview_rows),
        duration_ms=int((time.perf_counter() - start) * 1000),
    )


@app.post("/v1/rows", dependencies=[Depends(_require_caller)])
def rows(req: RowsIn) -> RowsOut:
    from odn.platform.workspace.rows import preview_rows

    table = _load(req.object_key)
    if req.order_by:
        if req.order_by not in table.column_names:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown sort column")
        table = table.sort_by([(req.order_by, "descending" if req.descending else "ascending")])
    return RowsOut(total=table.num_rows, rows=preview_rows(table.slice(req.offset, req.limit), req.limit))


@app.post("/v1/chart", dependencies=[Depends(_require_caller)])
def chart(req: ChartIn) -> ChartOut:
    import duckdb

    from odn.platform.policy.sql_policy import SqlPolicy
    from odn.platform.workspace.rows import json_safe

    table = _load(req.object_key)
    sql = chart_sql(req, table.column_names)
    if not SqlPolicy(dialect="duckdb", allowed_objects=frozenset({"t"})).validate(sql).allowed:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"rejected": "chart query rejected"})
    con = _locked_connection({"t": table})
    timer = threading.Timer(60, con.interrupt)
    timer.start()
    try:
        rows = con.sql(sql).arrow().read_all().to_pylist()
    except duckdb.Error as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {"failed": str(exc)[:500]}) from None
    finally:
        timer.cancel()
        con.close()
    return ChartOut(
        sql=sql.replace(f" LIMIT {req.limit + 1}", f" LIMIT {req.limit}"),
        points=[json_safe(r) for r in rows[: req.limit]],
        truncated=len(rows) > req.limit,
    )


@app.post("/v1/csv", dependencies=[Depends(_require_caller)])
def csv(req: CsvIn) -> StreamingResponse:
    import pyarrow.csv as pacsv

    table = _load(req.object_key)

    def chunks() -> Iterator[bytes]:
        for i, batch in enumerate(table.to_batches(max_chunksize=10_000)):
            buf = io.BytesIO()
            pacsv.write_csv(batch, buf, pacsv.WriteOptions(include_header=i == 0))
            yield buf.getvalue()
        if table.num_rows == 0:
            yield (",".join(table.column_names) + "\n").encode()

    return StreamingResponse(chunks(), media_type="text/csv")
