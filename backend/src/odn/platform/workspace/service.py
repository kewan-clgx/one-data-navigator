"""Result workspace: warehouse results persisted as Parquet artifacts, DuckDB follow-ups via the worker.

Artifacts are scoped to (tenant, user, session) and named q1, q2, ... so follow-up SQL can reference them.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
import uuid
from collections.abc import Iterator
from typing import Any, Literal

import httpx
import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from odn.config import settings
from odn.db import tenant_transaction
from odn.platform.connections.service import ConnectionProfile
from odn.platform.connectors.base import BaseConnector, QueryLimits
from odn.platform.governance.audit import AuditRecord, Source, write_audit
from odn.platform.policy.sql_policy import SqlPolicy
from odn.platform.query.service import QueryFailed, QueryRejected, run_query
from odn.platform.workspace.explain import explain_artifact, explain_chart
from odn.platform.workspace.rows import column_info, json_safe
from odn.platform.workspace.storage import assert_tenant_key, gcs_client, tenant_key
from odn.tenancy.context import RequestContext

SESSION_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
UI_PREVIEW_ROWS = 200


class InvalidSession(ValueError):
    pass


class Artifact(BaseModel):
    id: uuid.UUID
    session_id: str
    name: str
    kind: str
    connection_id: uuid.UUID | None
    parent_ids: list[uuid.UUID]
    sql_text: str
    source_tables: list[str]
    row_count: int
    arrow_bytes: int
    truncated: bool
    columns: list[dict[str, Any]]
    created_at: Any
    explanation: str = ""


class ArtifactResult(BaseModel):
    artifact: Artifact
    preview: list[dict[str, Any]]
    bytes_processed: int | None = None
    duration_ms: int


class ParquetSink:
    """Streams result batches into a local Parquet file and keeps a small preview."""

    def __init__(self, preview_limit: int = UI_PREVIEW_ROWS) -> None:
        fd, self.path = tempfile.mkstemp(suffix=".parquet")
        os.close(fd)
        self._writer: pq.ParquetWriter | None = None
        self._preview_limit = preview_limit
        self.preview: list[dict[str, Any]] = []
        self.schema: pa.Schema | None = None

    def write(self, batch: pa.RecordBatch) -> None:
        if self._writer is None:
            self.schema = batch.schema
            self._writer = pq.ParquetWriter(self.path, batch.schema, compression="zstd")
        self._writer.write_batch(batch)
        if len(self.preview) < self._preview_limit:
            need = self._preview_limit - len(self.preview)
            self.preview.extend(json_safe(r) for r in batch.slice(0, need).to_pylist())

    def finish(self, fallback_schema: pa.Schema) -> None:
        if self._writer is None:
            self.schema = fallback_schema
            self._writer = pq.ParquetWriter(self.path, fallback_schema, compression="zstd")
        self._writer.close()

    def cleanup(self) -> None:
        if os.path.exists(self.path):
            os.remove(self.path)


def _check_session(session_id: str) -> str:
    if not SESSION_RE.match(session_id):
        raise InvalidSession("invalid session id")
    return session_id


def _object_key(ctx: RequestContext, session_id: str, artifact_id: uuid.UUID) -> str:
    return tenant_key(ctx.tenant_id, ctx.user_id, session_id, f"{artifact_id}.parquet")


_ARTIFACT_COLS = (
    "id, session_id, name, kind, connection_id, parent_ids, sql_text, source_tables, "
    "row_count, arrow_bytes, truncated, columns, created_at"
)


def list_artifacts(ctx: RequestContext, session_id: str) -> list[Artifact]:
    with tenant_transaction(ctx.tenant_id) as conn:
        rows = conn.execute(
            f"SELECT {_ARTIFACT_COLS} FROM workspace_artifacts "
            "WHERE user_id = %s AND session_id = %s ORDER BY created_at",
            (ctx.user_id, _check_session(session_id)),
        ).fetchall()
    return [Artifact.model_validate(r) for r in rows]


def _explained(artifact: Artifact, names_by_id: dict[uuid.UUID, str], bytes_processed: int | None = None) -> Artifact:
    artifact.explanation = explain_artifact(
        kind=artifact.kind,
        sql=artifact.sql_text,
        source_tables=artifact.source_tables,
        derived_from=[names_by_id.get(p, "an earlier result") for p in artifact.parent_ids],
        truncated=artifact.truncated,
        row_count=artifact.row_count,
        bytes_processed=bytes_processed,
    )
    return artifact


def list_artifacts_explained(ctx: RequestContext, session_id: str) -> list[Artifact]:
    artifacts = list_artifacts(ctx, session_id)
    names = {a.id: a.name for a in artifacts}
    return [_explained(a, names) for a in artifacts]


def _object_keys(ctx: RequestContext, session_id: str) -> dict[str, tuple[uuid.UUID, str, list[str]]]:
    with tenant_transaction(ctx.tenant_id) as conn:
        rows = conn.execute(
            "SELECT name, id, object_key, source_tables FROM workspace_artifacts "
            "WHERE user_id = %s AND session_id = %s",
            (ctx.user_id, session_id),
        ).fetchall()
    return {
        r["name"]: (r["id"], assert_tenant_key(ctx.tenant_id, ctx.user_id, r["object_key"]), r["source_tables"])
        for r in rows
    }


def _insert_artifact(ctx: RequestContext, session_id: str, artifact_id: uuid.UUID, **fields: Any) -> Artifact:
    for _ in range(3):
        try:
            with tenant_transaction(ctx.tenant_id) as conn:
                count = conn.execute(
                    "SELECT count(*) AS n FROM workspace_artifacts WHERE user_id = %s AND session_id = %s",
                    (ctx.user_id, session_id),
                ).fetchone()["n"]
                row = conn.execute(
                    f"""
                    INSERT INTO workspace_artifacts (id, tenant_id, user_id, session_id, name, kind,
                        connection_id, parent_ids, sql_text, source_tables, object_key, row_count,
                        arrow_bytes, truncated, columns)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING {_ARTIFACT_COLS}
                    """,
                    (
                        artifact_id, ctx.tenant_id, ctx.user_id, session_id, f"q{count + 1}",
                        fields["kind"], fields.get("connection_id"), fields.get("parent_ids", []),
                        fields["sql_text"], fields["source_tables"], fields["object_key"],
                        fields["row_count"], fields["arrow_bytes"], fields["truncated"],
                        Jsonb(fields["columns"]),
                    ),
                ).fetchone()
            return Artifact.model_validate(row)
        except psycopg.errors.UniqueViolation:
            continue  # concurrent run took the same qN name
    raise RuntimeError("could not allocate an artifact name")


def run_warehouse_query(
    ctx: RequestContext,
    profile: ConnectionProfile,
    connector: BaseConnector,
    session_id: str,
    sql: str,
    *,
    confirmed: bool,
    source: Source = "chat",
) -> ArtifactResult:
    """Runs governed warehouse SQL once and persists the full (capped) result as a session artifact."""
    _check_session(session_id)
    artifact_id = uuid.uuid4()
    sink = ParquetSink()
    try:
        outcome = run_query(
            ctx, profile, sql, source=source, sink=sink, confirmed=confirmed, connector=connector,
            limits=QueryLimits(max_bytes_billed=settings.max_bytes_billed, timeout_s=settings.query_timeout_s),
        )
        sink.finish(pa.schema([(c.name, pa.string()) for c in outcome.meta.columns]))
        key = _object_key(ctx, session_id, artifact_id)
        gcs_client().bucket(settings.gcs_bucket).blob(key).upload_from_filename(sink.path)
    finally:
        sink.cleanup()

    artifact = _insert_artifact(
        ctx, session_id, artifact_id,
        kind="warehouse", connection_id=profile.id, sql_text=sql, source_tables=list(outcome.tables),
        object_key=key, row_count=outcome.meta.rows, arrow_bytes=outcome.meta.arrow_bytes,
        truncated=outcome.meta.truncated, columns=column_info(sink.schema) if sink.schema else [],
    )
    return ArtifactResult(
        artifact=_explained(artifact, {}, outcome.meta.bytes_processed), preview=sink.preview,
        bytes_processed=outcome.meta.bytes_processed, duration_ms=outcome.meta.duration_ms,
    )


def run_workspace_query(ctx: RequestContext, session_id: str, sql: str) -> ArtifactResult:
    """Runs read-only DuckDB SQL over this session's artifacts in the isolated worker (no warehouse scan)."""
    _check_session(session_id)
    available = _object_keys(ctx, session_id)
    audit = AuditRecord(
        tenant_id=ctx.tenant_id, user_id=ctx.user_id, connection_id=None, source="workspace",
        dialect="duckdb", sql_text=sql, decision="rejected",
    )
    decision = SqlPolicy(dialect="duckdb", allowed_objects=frozenset(available)).validate(sql)
    if not decision.allowed:
        audit.reason = decision.reason
        write_audit(audit)
        raise QueryRejected(decision.reason or "rejected")
    used = {name: available[name] for name in decision.tables}
    audit.tables = decision.tables

    artifact_id = uuid.uuid4()
    key = _object_key(ctx, session_id, artifact_id)
    start = time.perf_counter()
    resp = httpx.post(
        f"{settings.worker_url}/v1/query",
        headers={"X-ODN-Worker-Token": settings.worker_token},
        json={
            "sql": sql,
            "inputs": {name: obj for name, (_, obj, _) in used.items()},
            "output_key": key,
            "preview_rows": UI_PREVIEW_ROWS,
        },
        timeout=120,
    )
    if resp.status_code != 200:
        detail = resp.json().get("detail") if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        audit.decision = "failed" if resp.status_code != 422 else "rejected"
        audit.reason = str(detail)[:500]
        audit.duration_ms = int((time.perf_counter() - start) * 1000)
        write_audit(audit)
        if resp.status_code == 422:
            raise QueryRejected(str(detail))
        raise QueryFailed(detail.get("failed") if isinstance(detail, dict) else str(detail))
    out = resp.json()

    lineage_tables = sorted({t for (_, _, src) in used.values() for t in src})
    artifact = _insert_artifact(
        ctx, session_id, artifact_id,
        kind="workspace", parent_ids=[aid for (aid, _, _) in used.values()], sql_text=sql,
        source_tables=lineage_tables, object_key=key, row_count=out["row_count"],
        arrow_bytes=out["arrow_bytes"], truncated=out["truncated"], columns=out["columns"],
    )
    audit.decision = "allowed"
    audit.rows_returned = out["row_count"]
    audit.duration_ms = out["duration_ms"]
    write_audit(audit)
    names_by_id = {aid: name for name, (aid, _, _) in used.items()}
    return ArtifactResult(
        artifact=_explained(artifact, names_by_id), preview=out["preview"], duration_ms=out["duration_ms"]
    )


def artifact_rows(
    ctx: RequestContext, session_id: str, name: str, *, offset: int, limit: int,
    order_by: str | None, descending: bool,
) -> dict[str, Any]:
    keys = _object_keys(ctx, _check_session(session_id))
    if name not in keys:
        raise KeyError(name)
    resp = httpx.post(
        f"{settings.worker_url}/v1/rows",
        headers={"X-ODN-Worker-Token": settings.worker_token},
        json={"object_key": keys[name][1], "offset": offset, "limit": limit, "order_by": order_by,
              "descending": descending},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


class ChartSpec(BaseModel):
    source: str
    type: Literal["bar", "line", "scatter"]
    x: str
    y: str | None = None
    agg: Literal["none", "sum", "avg", "count", "min", "max"] = "sum"
    series: str | None = None
    sort: Literal["x", "y_desc", "y_asc"] = "x"
    limit: int = Field(default=50, ge=1, le=500)
    title: str = Field(default="", max_length=200)


class ChartResult(BaseModel):
    spec: ChartSpec
    sql: str
    points: list[dict[str, Any]]
    truncated: bool
    source_tables: list[str]
    source_truncated: bool
    explanation: str = ""
    # Saved chart name in the conversation (c1, c2, ...); empty for results rendered before saving existed.
    name: str = ""


def save_chart(ctx: RequestContext, session_id: str, result: ChartResult, source_artifact_id: uuid.UUID | None) -> str:
    """Persists a rendered chart as c1, c2, ... in its conversation (the artifacts store) and returns the name."""
    for _ in range(3):
        try:
            with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
                count = conn.execute(
                    "SELECT count(*) AS n FROM workspace_charts WHERE user_id = %s AND session_id = %s",
                    (ctx.user_id, session_id),
                ).fetchone()["n"]
                name = f"c{count + 1}"
                conn.execute(
                    "INSERT INTO workspace_charts (tenant_id, user_id, session_id, name, source_artifact_id, "
                    "title, chart_type, result) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        ctx.tenant_id, ctx.user_id, session_id, name, source_artifact_id,
                        result.spec.title, result.spec.type,
                        Jsonb(result.model_copy(update={"name": name}).model_dump(mode="json")),
                    ),
                )
            return name
        except psycopg.errors.UniqueViolation:
            continue  # a concurrent chart took the same cN name
    raise RuntimeError("could not allocate a chart name")


def render_chart(ctx: RequestContext, session_id: str, spec: ChartSpec) -> ChartResult:
    """Aggregates one artifact into chart points in the worker and saves the chart; it is drawn client-side."""
    keys = _object_keys(ctx, _check_session(session_id))
    if spec.source not in keys:
        raise KeyError(spec.source)
    source_id, object_key, source_tables = keys[spec.source]
    audit = AuditRecord(
        tenant_id=ctx.tenant_id, user_id=ctx.user_id, connection_id=None, source="workspace",
        dialect="duckdb", sql_text=f"-- chart over {spec.source}", decision="failed", tables=(spec.source,),
    )
    resp = httpx.post(
        f"{settings.worker_url}/v1/chart",
        headers={"X-ODN-Worker-Token": settings.worker_token},
        json={"object_key": object_key, **spec.model_dump(exclude={"source", "title"})},
        timeout=90,
    )
    if resp.status_code != 200:
        detail = resp.json().get("detail") if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        audit.reason = str(detail)[:500]
        write_audit(audit)
        raise QueryFailed(detail.get("failed") if isinstance(detail, dict) else str(detail))
    out = resp.json()
    audit.decision = "allowed"
    audit.sql_text = f"-- chart over {spec.source}\n{out['sql']}"
    audit.rows_returned = len(out["points"])
    write_audit(audit)
    with tenant_transaction(ctx.tenant_id) as conn:
        truncated_src = conn.execute(
            "SELECT truncated FROM workspace_artifacts WHERE user_id = %s AND session_id = %s AND name = %s",
            (ctx.user_id, session_id, spec.source),
        ).fetchone()["truncated"]
    result = ChartResult(
        spec=spec, sql=out["sql"], points=out["points"], truncated=out["truncated"],
        source_tables=source_tables, source_truncated=truncated_src,
        explanation=explain_chart(
            chart_type=spec.type, x=spec.x, y=spec.y, agg=spec.agg, series=spec.series, sort=spec.sort,
            limit=spec.limit, points=len(out["points"]), truncated=out["truncated"], source=spec.source,
            source_truncated=truncated_src,
        ),
    )
    result.name = save_chart(ctx, session_id, result, source_id)
    return result


def artifact_csv(ctx: RequestContext, session_id: str, name: str) -> Iterator[bytes]:
    keys = _object_keys(ctx, _check_session(session_id))
    if name not in keys:
        raise KeyError(name)

    def stream() -> Iterator[bytes]:
        with httpx.stream(
            "POST", f"{settings.worker_url}/v1/csv",
            headers={"X-ODN-Worker-Token": settings.worker_token},
            json={"object_key": keys[name][1]}, timeout=300,
        ) as resp:
            resp.raise_for_status()
            yield from resp.iter_bytes()

    return stream()
