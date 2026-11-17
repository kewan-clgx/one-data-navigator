"""BigQuery connector: dry-run costing, capped execution, Storage Read API (Arrow) result streaming."""

from __future__ import annotations

import re
from collections.abc import Iterator

import pyarrow as pa
from google.api_core import exceptions as gexc
from google.auth import exceptions as auth_exc
from google.auth.credentials import Credentials
from google.cloud import bigquery, bigquery_storage

from odn.platform.connectors.base import (
    BaseConnector,
    CatalogNode,
    ColumnSchema,
    ConnectionTestStep,
    CostEstimate,
    QueryExecution,
    QueryLimits,
    TableSchema,
)

_MAX_LIST = 10_000
_LABELS = {"app": "one-data-navigator"}
_PROJECT_RE = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")
_DATASET_RE = re.compile(r"[A-Za-z0-9_]{1,1024}")


class BigQueryExecution(QueryExecution):
    def __init__(
        self, job: bigquery.QueryJob, read_client: bigquery_storage.BigQueryReadClient, timeout_s: int
    ) -> None:
        self._job = job
        self._read_client = read_client
        self._timeout_s = timeout_s
        self.job_id = job.job_id

    def batches(self) -> Iterator[pa.RecordBatch]:
        rows = self._job.result(timeout=self._timeout_s)
        self.bytes_processed = self._job.total_bytes_processed
        yield from rows.to_arrow_iterable(bqstorage_client=self._read_client)


class BigQueryConnector(BaseConnector):
    dialect = "bigquery"
    supports_dry_run = True
    supports_cancel = True

    def __init__(
        self,
        project: str,
        *,
        credentials: Credentials | None = None,
        location: str | None = None,
        billing_project: str | None = None,
        default_dataset: str | None = None,
    ) -> None:
        self.project = project
        self.location = location
        self.default_dataset = default_dataset
        self._client = bigquery.Client(
            project=billing_project or project, credentials=credentials, location=location
        )
        self._read_client = bigquery_storage.BigQueryReadClient(credentials=credentials)

    @property
    def default_namespace(self) -> tuple[str, ...]:
        return (self.project, self.default_dataset) if self.default_dataset else (self.project,)

    def test_connection(self) -> list[ConnectionTestStep]:
        steps: list[ConnectionTestStep] = []
        try:
            self._client.query("SELECT 1", job_config=bigquery.QueryJobConfig(dry_run=True))
            steps.append(ConnectionTestStep(step="auth", ok=True))
            steps.append(ConnectionTestStep(step="network", ok=True))
        except auth_exc.RefreshError as exc:
            # Google rejected the key itself (deleted/disabled service account or key, wrong key).
            steps.append(ConnectionTestStep(step="auth", ok=False, detail=f"key rejected by Google: {exc.args[0]}"))
            return steps
        except auth_exc.TransportError as exc:
            steps.append(ConnectionTestStep(step="network", ok=False, detail=type(exc).__name__))
            return steps
        except gexc.GoogleAPIError as exc:
            steps.append(ConnectionTestStep(step="auth", ok=False, detail=type(exc).__name__))
            return steps
        try:
            next(iter(self._client.list_datasets(self.project, max_results=1)), None)
            steps.append(ConnectionTestStep(step="metadata", ok=True))
        except gexc.GoogleAPIError as exc:
            steps.append(ConnectionTestStep(step="metadata", ok=False, detail=type(exc).__name__))
            return steps
        try:
            self._client.query("SELECT 1", job_config=self._job_config(QueryLimits())).result(timeout=30)
            steps.append(ConnectionTestStep(step="read", ok=True))
        except gexc.GoogleAPIError as exc:
            steps.append(ConnectionTestStep(step="read", ok=False, detail=type(exc).__name__))
        return steps

    def list_catalog(self, parent: tuple[str, ...] = ()) -> list[CatalogNode]:
        if len(parent) == 0:
            return [
                CatalogNode(kind="dataset", path=(self.project, ds.dataset_id), name=ds.dataset_id)
                for ds in self._client.list_datasets(self.project, max_results=_MAX_LIST)
            ]
        if len(parent) == 2:
            project, dataset = parent
            return [
                CatalogNode(
                    kind="view" if t.table_type == "VIEW" else "table",
                    path=(project, dataset, t.table_id),
                    name=t.table_id,
                )
                for t in self._client.list_tables(f"{project}.{dataset}", max_results=_MAX_LIST)
            ]
        raise ValueError("parent must be () or (project, dataset)")

    def get_schema(self, path: tuple[str, ...]) -> TableSchema:
        if len(path) != 3:
            raise ValueError("path must be (project, dataset, table)")
        t = self._client.get_table(".".join(path))
        return TableSchema(
            path=path,
            kind="view" if t.table_type == "VIEW" else "table",
            columns=[
                ColumnSchema(
                    name=f.name, type=f.field_type, nullable=f.mode != "REQUIRED", description=f.description
                )
                for f in t.schema
            ],
            description=t.description,
            row_count=t.num_rows,
            size_bytes=t.num_bytes,
            partitioning=t.time_partitioning.field if t.time_partitioning else None,
            clustering=t.clustering_fields,
        )

    def list_columns(self, container: tuple[str, ...]) -> dict[tuple[str, ...], list[ColumnSchema]]:
        if len(container) != 2:
            raise ValueError("container must be (project, dataset)")
        project, dataset = container
        if not (_PROJECT_RE.fullmatch(project) and _DATASET_RE.fullmatch(dataset)):
            raise ValueError("invalid project or dataset identifier")
        # Platform-owned metadata SQL (never user input): identifiers come from the catalog listing.
        sql = (
            "SELECT table_name, field_path, data_type, description "
            f"FROM `{project}.{dataset}`.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS "
            "ORDER BY table_name, field_path"
        )
        config = bigquery.QueryJobConfig(maximum_bytes_billed=1024**3, use_legacy_sql=False, labels=_LABELS)
        out: dict[tuple[str, ...], list[ColumnSchema]] = {}
        for row in self._client.query(sql, job_config=config).result(timeout=120):
            out.setdefault((project, dataset, row.table_name), []).append(
                ColumnSchema(name=row.field_path, type=row.data_type, description=row.description or None)
            )
        return out

    def estimate_cost(self, sql: str) -> CostEstimate:
        job = self._client.query(
            sql, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False, labels=_LABELS)
        )
        return CostEstimate(bytes_processed=job.total_bytes_processed)

    def execute_paged(self, sql: str, limits: QueryLimits) -> QueryExecution:
        job = self._client.query(sql, job_config=self._job_config(limits), job_id_prefix="odn_")
        return BigQueryExecution(job, self._read_client, limits.timeout_s)

    def cancel_job(self, job_id: str) -> None:
        self._client.cancel_job(job_id, location=self.location)

    @staticmethod
    def _job_config(limits: QueryLimits) -> bigquery.QueryJobConfig:
        config = bigquery.QueryJobConfig(
            maximum_bytes_billed=limits.max_bytes_billed, use_legacy_sql=False, labels=_LABELS
        )
        config.job_timeout_ms = limits.timeout_s * 1000
        return config
