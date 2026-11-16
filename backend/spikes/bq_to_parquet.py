"""Spike 2, BigQuery leg: stream rows via the Storage Read API (Arrow) into tenant-scoped Parquet in GCS.

Runs in the api container (it holds the BigQuery credentials; the worker never does):

    docker compose exec -e BENCH_TABLE=<dataset.table> api python /app/spikes/bq_to_parquet.py

Stops after BENCH_ROWS rows, so only those bytes are read (no query scan).
"""

from __future__ import annotations

import os
import resource
import tempfile
import time

import pyarrow.parquet as pq
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery_storage, storage
from google.cloud.bigquery_storage import types

ROWS = int(os.environ.get("BENCH_ROWS", "500000"))
PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]
TABLE = os.environ.get("BENCH_TABLE", "stg_property_firmographics.stg_combined_bus_name")
BUCKET = os.environ.get("ODN_GCS_BUCKET", "odn-workspace-local")
OBJECT = "tenant_bench/session_bench/query_1.parquet"


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def main() -> None:
    dataset, table = TABLE.split(".")
    client = bigquery_storage.BigQueryReadClient()
    session = client.create_read_session(
        parent=f"projects/{PROJECT}",
        read_session=types.ReadSession(
            table=f"projects/{PROJECT}/datasets/{dataset}/tables/{table}",
            data_format=types.DataFormat.ARROW,
        ),
        max_stream_count=1,
    )
    print(f"read session: {len(session.streams)} stream(s), table {PROJECT}.{TABLE}")

    start = time.perf_counter()
    rows = 0
    with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
        writer: pq.ParquetWriter | None = None
        reader = client.read_rows(session.streams[0].name)
        for page in reader.rows(session).pages:
            batch = page.to_arrow()
            if rows + batch.num_rows > ROWS:
                batch = batch.slice(0, ROWS - rows)
            writer = writer or pq.ParquetWriter(tmp.name, batch.schema, compression="zstd")
            writer.write_batch(batch)
            rows += batch.num_rows
            if rows >= ROWS:
                break
        assert writer is not None
        writer.close()
        read_s = time.perf_counter() - start
        size_mb = os.path.getsize(tmp.name) / 1e6

        up = time.perf_counter()
        storage.Client(project="local", credentials=AnonymousCredentials()).bucket(BUCKET).blob(
            OBJECT
        ).upload_from_filename(tmp.name)
        upload_s = time.perf_counter() - up

    print(f"rows: {rows}, stream->parquet: {read_s:.2f}s ({rows / read_s:,.0f} rows/s), "
          f"parquet {size_mb:.1f} MB, upload {upload_s:.2f}s, peak rss {rss_mb():.0f} MB")
    print(f"artifact: gs://{BUCKET}/{OBJECT}")


if __name__ == "__main__":
    main()
