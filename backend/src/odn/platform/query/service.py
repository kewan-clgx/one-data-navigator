"""QueryService: policy check -> dry-run cost gate -> capped execution -> audit. Used by chat, explorer, skills."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol

import pyarrow as pa
from google.api_core import exceptions as gexc

from odn.config import settings
from odn.platform.catalog.personal import record_usage
from odn.platform.connections.service import ConnectionProfile, build_connector, build_policy
from odn.platform.connectors.base import (
    BaseConnector,
    ColumnSchema,
    ConnectorError,
    QueryLimits,
    QueryResultMeta,
)
from odn.platform.governance.audit import AuditRecord, Source, write_audit
from odn.platform.workspace.rows import json_safe
from odn.tenancy.context import RequestContext

log = logging.getLogger(__name__)


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


def scan_cap(ctx: RequestContext) -> int:
    """Bytes one warehouse query may scan for this caller: by role, never above the tenant's own cap."""
    cap = settings.admin_max_bytes_billed if ctx.role == "admin" else settings.max_bytes_billed
    return min(cap, ctx.max_bytes_billed) if ctx.max_bytes_billed is not None else cap


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
        execution = connector.free_preview(sql, limits)
        if execution is None:
            estimate = connector.estimate_cost(sql)
            audit.bytes_estimated = estimate.bytes_processed
            if (estimate.bytes_processed or 0) > limits.max_bytes_billed:
                audit.reason = "estimated bytes exceed the maximum billed limit"
                write_audit(audit)
                raise QueryRejected(_over_cap_message(estimate.bytes_processed or 0, limits.max_bytes_billed))
            if (estimate.bytes_processed or 0) > settings.confirm_bytes_threshold and not confirmed:
                raise ConfirmationRequired(estimate.bytes_processed or 0)
            execution = connector.execute_paged(sql, limits)
        audit.job_id = execution.job_id
        rows = arrow_bytes = 0
        truncated = False
        schema: pa.Schema | None = None
        batches = execution.batches()
        try:
            for batch in batches:
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
        finally:
            close = getattr(batches, "close", None)
            if close is not None:
                close()
        if truncated and execution.job_id and connector.supports_cancel:
            _cancel_quietly(connector, execution.job_id)
    except (gexc.GoogleAPIError, ConnectorError) as exc:
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
    if build_policy(profile, connector).case_sensitive:
        record_usage(ctx, profile.id, decision.tables, preserve_case=True)
    else:
        record_usage(ctx, profile.id, decision.tables)
    if settings.column_profiling_preview and profile.kind == "bigquery":
        from odn.platform.catalog.column_profiles import query_paths, request_automatic
        from odn.platform.connectors.bigquery import BigQueryConnector

        if isinstance(connector, BigQueryConnector):
            try:
                request_automatic(ctx, profile, query_paths(sql, connector, decision.tables))
            except Exception as exc:  # noqa: BLE001 - a successful query must remain successful
                log.warning("automatic column profiling could not inspect query: %s", type(exc).__name__)
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


CheckStatus = Literal["ok", "free_preview", "confirm", "over_cap", "rejected", "error"]


@dataclass(frozen=True)
class QueryCheck:
    status: CheckStatus
    message: str
    bytes_estimated: int | None = None
    tables: tuple[str, ...] = ()
    cost_known: bool = False


def check_query(
    ctx: RequestContext, profile: ConnectionProfile, sql: str, *, connector: BaseConnector | None = None
) -> QueryCheck:
    """What `run_query` would decide for this SQL, without reading any data: policy, free preview, dry-run cost, cap.

    Backs the workbench's live check line, so users see scan size, limits and allowlist problems before they run.
    Not audited (no data is read); callers rate-limit it.
    """
    connector = connector or build_connector(profile)
    decision = build_policy(profile, connector).validate(sql)
    if not decision.allowed:
        return QueryCheck("rejected", decision.reason or "Not allowed by the connection's SQL policy.")
    cap = scan_cap(ctx)
    try:
        if connector.free_preview(sql, QueryLimits(max_bytes_billed=cap)) is not None:
            return QueryCheck("free_preview", "Free preview: reads stored rows, no scan.", 0, decision.tables, True)
        if not connector.supports_dry_run:
            return QueryCheck("ok", "Allowed. Cost estimate isn't available for this database.", None, decision.tables)
        estimated = connector.estimate_cost(sql).bytes_processed or 0
    except (gexc.GoogleAPIError, ConnectorError) as exc:
        return QueryCheck("error", _error_message(exc), None, decision.tables)
    if estimated > cap:
        return QueryCheck("over_cap", _over_cap_message(estimated, cap), estimated, decision.tables, True)
    if estimated > settings.confirm_bytes_threshold:
        return QueryCheck("confirm", f"This query will scan about {_size(estimated)} and will ask to confirm.",
                          estimated, decision.tables, True)
    return QueryCheck("ok", f"This query will scan about {_size(estimated)}.", estimated, decision.tables, True)


def _cancel_quietly(connector: BaseConnector, job_id: str) -> None:
    try:
        connector.cancel_job(job_id)
    except gexc.GoogleAPIError:
        pass  # the job usually finished already; the result stream is what we stopped


def _error_message(exc: gexc.GoogleAPIError | ConnectorError) -> str:
    if isinstance(exc, ConnectorError):
        return str(exc)[:500]
    message = getattr(exc, "message", None) or type(exc).__name__
    return str(message)[:500]


def _over_cap_message(estimated: int, cap: int) -> str:
    # Shown to analysts in the workbench and to the agent, which rewrites its query from it.
    return (
        f"This query would scan about {_gb(estimated)}, over the {_gb(cap)} per-query limit. "
        "LIMIT does not reduce the bytes scanned: name only the columns you need instead of SELECT *, "
        "and filter on the table's partition or clustering columns."
    )


def _gb(n: int) -> str:
    gb = n / 1e9
    return f"{gb:,.0f} GB" if gb >= 10 else f"{gb:,.1f} GB"


def _size(n: int) -> str:
    for unit, scale in (("TB", 1e12), ("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= scale:
            v = n / scale
            return f"{v:,.1f} {unit}" if v < 10 else f"{v:,.0f} {unit}"
    return f"{n} B"


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)
