"""Keyword-search index for the explorer: allowlisted datasets -> tables -> columns, cached per connection."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal
from uuid import UUID

from google.api_core import exceptions as gexc
from pydantic import BaseModel

from odn.platform.connectors.base import BaseConnector, ColumnSchema, ConnectorError
from odn.platform.policy.sql_policy import SqlPolicy

TTL_S = 3600
_CONCURRENCY = 4


class IndexedTable(BaseModel):
    path: tuple[str, ...]
    name: str
    kind: str
    columns: list[ColumnSchema]


class IndexedDataset(BaseModel):
    path: tuple[str, ...]
    name: str
    tables: list[IndexedTable]


class SearchIndex(BaseModel):
    status: Literal["success", "partial"]
    built_at: float
    datasets: list[IndexedDataset]
    failed: list[str]


_cache: dict[tuple[UUID, UUID, frozenset[str]], SearchIndex] = {}
_lock = threading.Lock()


def get_search_index(
    tenant_id: UUID,
    connection_id: UUID,
    connector: BaseConnector,
    policy: SqlPolicy,
    *,
    refresh: bool = False,
) -> SearchIndex:
    # Keyed by allowlist too, so an allowlist change never serves stale (wider) metadata.
    key = (tenant_id, connection_id, policy.allowed_objects)
    with _lock:
        cached = _cache.get(key)
        if cached and not refresh and time.time() - cached.built_at < TTL_S:
            return cached

    index = _build(connector, policy)
    if index.status == "success":
        with _lock:
            _cache[key] = index
    return index


def _build(connector: BaseConnector, policy: SqlPolicy) -> SearchIndex:
    datasets = [d for d in connector.list_catalog(()) if policy.allows_container(d.path)]

    def index_dataset(path: tuple[str, ...]) -> list[IndexedTable]:
        tables = [t for t in connector.list_catalog(path) if policy.allows_object(t.path)]
        columns = connector.list_columns(path)
        return [
            IndexedTable(path=t.path, name=t.name, kind=t.kind, columns=columns.get(t.path, []))
            for t in tables
        ]

    out: list[IndexedDataset] = []
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=_CONCURRENCY) as pool:
        futures = {d.path: pool.submit(index_dataset, d.path) for d in datasets}
        for d in datasets:
            try:
                tables = futures[d.path].result()
            except (gexc.GoogleAPIError, ConnectorError, ValueError):
                failed.append(".".join(d.path))
                continue
            out.append(IndexedDataset(path=d.path, name=d.name, tables=tables))
    return SearchIndex(
        status="partial" if failed else "success", built_at=time.time(), datasets=out, failed=failed
    )
