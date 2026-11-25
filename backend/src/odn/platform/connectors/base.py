"""Connector SPI: the only way platform services talk to source databases."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

import pyarrow as pa
from pydantic import BaseModel

from odn.platform.policy.sql_policy import Dialect


class ConnectorError(Exception):
    """A safe, user-facing source connection or query failure."""


class CatalogNode(BaseModel):
    kind: Literal["project", "dataset", "schema", "table", "view"]
    path: tuple[str, ...]
    name: str
    description: str | None = None


class ColumnSchema(BaseModel):
    name: str
    type: str
    nullable: bool = True
    description: str | None = None


class TableSchema(BaseModel):
    path: tuple[str, ...]
    kind: Literal["table", "view"]
    columns: list[ColumnSchema]
    description: str | None = None
    row_count: int | None = None
    size_bytes: int | None = None
    partitioning: str | None = None
    clustering: list[str] | None = None


class CostEstimate(BaseModel):
    bytes_processed: int | None = None
    rows_estimated: int | None = None


class ConnectionTestStep(BaseModel):
    step: Literal["tunnel", "auth", "network", "metadata", "read"]
    ok: bool
    detail: str | None = None


class QueryResultMeta(BaseModel):
    job_id: str | None
    rows: int
    arrow_bytes: int
    bytes_processed: int | None
    duration_ms: int
    truncated: bool
    columns: list[ColumnSchema]


@dataclass(frozen=True)
class QueryLimits:
    max_rows: int = 500_000
    max_arrow_bytes: int = 600 * 1024**2
    max_bytes_billed: int = 100 * 1024**3
    timeout_s: int = 300


class QueryExecution(ABC):
    """A running query. Iterate `batches()` once; `bytes_processed` is final after iteration."""

    job_id: str | None
    bytes_processed: int | None = None

    @abstractmethod
    def batches(self) -> Iterator[pa.RecordBatch]: ...


class BaseConnector(ABC):
    dialect: Dialect
    supports_dry_run: bool
    supports_cancel: bool

    @property
    @abstractmethod
    def default_namespace(self) -> tuple[str, ...]:
        """Leading name parts used to qualify partially qualified table references."""

    @abstractmethod
    def test_connection(self) -> list[ConnectionTestStep]: ...

    @abstractmethod
    def list_catalog(self, parent: tuple[str, ...] = ()) -> list[CatalogNode]: ...

    @abstractmethod
    def get_schema(self, path: tuple[str, ...]) -> TableSchema: ...

    @abstractmethod
    def list_columns(self, container: tuple[str, ...]) -> dict[tuple[str, ...], list[ColumnSchema]]:
        """All columns of every table in a container (e.g. a dataset) in one metadata call, keyed by table path."""

    @abstractmethod
    def estimate_cost(self, sql: str) -> CostEstimate: ...

    @abstractmethod
    def execute_paged(self, sql: str, limits: QueryLimits) -> QueryExecution: ...

    @abstractmethod
    def cancel_job(self, job_id: str) -> None: ...
