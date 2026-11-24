"""QueryService: policy check -> dry-run cost gate -> capped execution -> audit. Used by chat, explorer, skills."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any, Protocol

import pyarrow as pa
from google.api_core import exceptions as gexc

from odn.config import settings
from odn.platform.catalog.personal import record_usage
from odn.platform.connections.service import ConnectionProfile, build_connector, build_policy
from odn.platform.connectors.base import BaseConnector, ColumnSchema, QueryLimits, QueryResultMeta
from odn.platform.governance.audit import AuditRecord, Source, write_audit
from odn.platform.workspace.rows import json_safe
from odn.tenancy.context import RequestContext


class QueryRejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ConfirmationRequired(Exception):
    def __init__(self, bytes_estimated: int) -> None:
        super().__init__(bytes_estimated)
        self.bytes_estimated = bytes_estimated


class QueryFailed(Exception):
    pass


class ResultSink(Protocol):
    def write(self, batch: pa.RecordBatch) -> None: ...


class PreviewSink:
    """Phase 1 sink: keeps rows in memory for the explorer/chat (Phase 2 adds the Parquet sink)."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def write(self, batch: pa.RecordBatch) -> None:
        self.rows.extend(json_safe(row) for row in batch.to_pylist())


@dataclass(frozen=True)
class QueryOutcome:
    meta: QueryResultMeta
    tables: tuple[str, ...]
    bytes_estimated: int | None


def run_query(
    ctx: RequestContext,
    profile: ConnectionProfile,
    sql: str,
    *,
    source: Source,
    sink: ResultSink,
    limits: QueryLimits,
    confirmed: bool = False,
    connector: BaseConnector | None = None,
) -> QueryOutcome:
    connector = connector or build_connector(profile)
    if ctx.max_bytes_billed is not None and ctx.max_bytes_billed < limits.max_bytes_billed:
        limits = replace(limits, max_bytes_billed=ctx.max_bytes_billed)
    audit = AuditRecord(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        connection_id=profile.id,
        source=source,
        dialect=connector.dialect,
        sql_text=sql,
        decision="rejected",
    )

    decision = build_policy(profile, connector).validate(sql)
    if not decision.allowed:
        audit.reason = decision.reason
        write_audit(audit)
        raise QueryRejected(decision.reason or "rejected")
    audit.tables = decision.tables

    start = time.perf_counter()
    try:
        estimate = connector.estimate_cost(sql)
        audit.bytes_estimated = estimate.bytes_processed
        if (estimate.bytes_processed or 0) > limits.max_bytes_billed:
            audit.reason = "estimated bytes exceed the maximum billed limit"
            write_audit(audit)
            raise QueryRejected(audit.reason)
        if (estimate.bytes_processed or 0) > settings.confirm_bytes_threshold and not confirmed:
            raise ConfirmationRequired(estimate.bytes_processed or 0)

        execution = connector.execute_paged(sql, limits)
        audit.job_id = execution.job_id
        rows = arrow_bytes = 0
        truncated = False
        schema: pa.Schema | None = None
        for batch in execution.batches():
            schema = schema or batch.schema
            if rows + batch.num_rows > limits.max_rows:
                batch = batch.slice(0, limits.max_rows - rows)
                truncated = True
            if arrow_bytes + batch.nbytes > limits.max_arrow_bytes:
                truncated = True
                break
            sink.write(batch)
            rows += batch.num_rows
            arrow_bytes += batch.nbytes
            if truncated:
                break
        if truncated and execution.job_id:
            _cancel_quietly(connector, execution.job_id)
    except gexc.GoogleAPIError as exc:
        audit.decision = "failed"
        audit.reason = _error_message(exc)
        audit.duration_ms = _ms(start)
        write_audit(audit)
        raise QueryFailed(audit.reason) from None

    audit.decision = "allowed"
    audit.bytes_processed = execution.bytes_processed
    audit.rows_returned = rows
    audit.duration_ms = _ms(start)
    write_audit(audit)
    record_usage(ctx, profile.id, decision.tables)
    return QueryOutcome(
        meta=QueryResultMeta(
            job_id=execution.job_id,
            rows=rows,
            arrow_bytes=arrow_bytes,
            bytes_processed=execution.bytes_processed,
            duration_ms=audit.duration_ms,
            truncated=truncated,
            columns=[ColumnSchema(name=f.name, type=str(f.type), nullable=f.nullable) for f in (schema or [])],
        ),
        tables=decision.tables,
        bytes_estimated=audit.bytes_estimated,
    )


def _cancel_quietly(connector: BaseConnector, job_id: str) -> None:
    try:
        connector.cancel_job(job_id)
    except gexc.GoogleAPIError:
        pass  # the job usually finished already; the result stream is what we stopped


def _error_message(exc: gexc.GoogleAPIError) -> str:
    message = getattr(exc, "message", None) or type(exc).__name__
    return str(message)[:500]


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)
