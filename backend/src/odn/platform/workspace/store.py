"""The user's artifacts store: result tables, saved charts and uploaded files across all conversations."""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Iterator
from typing import Any, Literal

import pyarrow.parquet as pq
from pydantic import BaseModel

from odn.config import settings
from odn.db import tenant_transaction
from odn.platform.workspace.rows import column_info, preview_rows
from odn.platform.workspace.service import (
    UI_PREVIEW_ROWS,
    Artifact,
    ArtifactResult,
    ChartResult,
    _check_session,
    _explained,
    _insert_artifact,
    _object_key,
)
from odn.platform.workspace.storage import gcs_client, workspace_prefix
from odn.platform.workspace.uploads import (
    INLINE_MIME_TYPES,
    MIME_BY_EXTENSION,
    TABULAR_EXTENSIONS,
    ParsedTable,
    clean_filename,
    extension_of,
    parse_tabular,
)
from odn.tenancy.context import RequestContext

StoreKind = Literal["table", "chart", "file"]


class StoreItem(BaseModel):
    id: uuid.UUID
    kind: StoreKind
    session_id: str
    name: str
    title: str
    created_at: Any
    # table
    origin: str | None = None
    row_count: int | None = None
    column_count: int | None = None
    source_tables: list[str] = []
    # chart
    chart_type: str | None = None
    point_count: int | None = None
    # file
    mime_type: str | None = None
    size_bytes: int | None = None
    table_name: str | None = None
    inline: bool = False


class UploadResult(BaseModel):
    file: StoreItem
    table: ArtifactResult | None = None


def upload_description(filename: str, parsed: ParsedTable | None) -> str:
    """Stored as the table's SQL text, so the agent sees where the header was and any notes above it."""
    text = f"-- uploaded file: {filename}"
    if parsed and parsed.header_row > 1:
        text += f" (header on row {parsed.header_row})"
    if parsed and parsed.preamble:
        text += "\n-- text above the table in the file:\n" + "\n".join(
            f"-- {line}" for line in parsed.preamble.splitlines()
        )
    return text


def _file_item(row: dict[str, Any]) -> StoreItem:
    return StoreItem(
        id=row["id"], kind="file", session_id=row["session_id"], name=row["filename"], title=row["filename"],
        created_at=row["created_at"], mime_type=row["mime_type"], size_bytes=row["size_bytes"],
        table_name=row.get("table_name"), inline=row["mime_type"] in INLINE_MIME_TYPES,
    )


def save_upload(ctx: RequestContext, session_id: str, filename: str, data: bytes) -> UploadResult:
    """Stores an uploaded file; CSV/XLSX files also become a queryable workspace table (kind 'upload')."""
    _check_session(session_id)
    filename = clean_filename(filename)
    ext = extension_of(filename)
    parsed = parse_tabular(ext, data) if ext in TABULAR_EXTENSIONS else None
    table = parsed.table if parsed else None

    file_id = uuid.uuid4()
    bucket = gcs_client().bucket(settings.gcs_bucket)
    file_key = workspace_prefix(str(ctx.tenant_id), str(ctx.user_id), session_id, "files", f"{file_id}{ext}")
    bucket.blob(file_key).upload_from_string(data, content_type=MIME_BY_EXTENSION[ext])

    table_result: ArtifactResult | None = None
    if table is not None:
        artifact_id = uuid.uuid4()
        fd, path = tempfile.mkstemp(suffix=".parquet")
        os.close(fd)
        try:
            pq.write_table(table, path, compression="zstd")
            table_key = _object_key(ctx, session_id, artifact_id)
            bucket.blob(table_key).upload_from_filename(path)
        finally:
            os.remove(path)
        artifact = _insert_artifact(
            ctx, session_id, artifact_id,
            kind="upload", sql_text=upload_description(filename, parsed), source_tables=[filename],
            object_key=table_key, row_count=table.num_rows, arrow_bytes=table.nbytes, truncated=False,
            columns=column_info(table.schema),
        )
        table_result = ArtifactResult(
            artifact=_explained(artifact, {}), preview=preview_rows(table, UI_PREVIEW_ROWS), duration_ms=0
        )

    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        row = conn.execute(
            "INSERT INTO workspace_files (id, tenant_id, user_id, session_id, filename, mime_type, size_bytes, "
            "object_key, table_artifact_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING id, session_id, filename, mime_type, size_bytes, created_at",
            (
                file_id, ctx.tenant_id, ctx.user_id, session_id, filename, MIME_BY_EXTENSION[ext], len(data),
                file_key, table_result.artifact.id if table_result else None,
            ),
        ).fetchone()
    row["table_name"] = table_result.artifact.name if table_result else None
    return UploadResult(file=_file_item(row), table=table_result)


def list_store(
    ctx: RequestContext, *, kind: StoreKind | None = None, session_id: str | None = None, limit: int = 200
) -> list[StoreItem]:
    """The user's artifacts, newest first, optionally limited to one kind and/or one conversation."""
    if session_id is not None:
        _check_session(session_id)
    session_filter = " AND session_id = %s" if session_id else ""
    session_arg: tuple[Any, ...] = (session_id,) if session_id else ()
    items: list[StoreItem] = []
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        if kind in (None, "table"):
            for r in conn.execute(
                "SELECT id, session_id, name, kind, sql_text, source_tables, row_count, columns, created_at "
                f"FROM workspace_artifacts WHERE user_id = %s{session_filter} ORDER BY created_at DESC LIMIT %s",
                (ctx.user_id, *session_arg, limit),
            ).fetchall():
                title = (
                    f"Uploaded {r['source_tables'][0]}" if r["kind"] == "upload" and r["source_tables"]
                    else "Warehouse query" if r["kind"] == "warehouse" else "Workspace follow-up"
                )
                items.append(StoreItem(
                    id=r["id"], kind="table", session_id=r["session_id"], name=r["name"], title=title,
                    created_at=r["created_at"], origin=r["kind"], row_count=r["row_count"],
                    column_count=len(r["columns"]), source_tables=r["source_tables"],
                ))
        if kind in (None, "chart"):
            for r in conn.execute(
                "SELECT id, session_id, name, title, chart_type, jsonb_array_length(result->'points') AS points, "
                "result->'source_tables' AS source_tables, created_at "
                f"FROM workspace_charts WHERE user_id = %s{session_filter} ORDER BY created_at DESC LIMIT %s",
                (ctx.user_id, *session_arg, limit),
            ).fetchall():
                items.append(StoreItem(
                    id=r["id"], kind="chart", session_id=r["session_id"], name=r["name"],
                    title=r["title"] or f"{r['chart_type'].capitalize()} chart", created_at=r["created_at"],
                    chart_type=r["chart_type"], point_count=r["points"], source_tables=r["source_tables"] or [],
                ))
        if kind in (None, "file"):
            for r in conn.execute(
                "SELECT f.id, f.session_id, f.filename, f.mime_type, f.size_bytes, f.created_at, a.name AS table_name "
                "FROM workspace_files f LEFT JOIN workspace_artifacts a ON a.id = f.table_artifact_id "
                f"WHERE f.user_id = %s{session_filter.replace('session_id', 'f.session_id')} "
                "ORDER BY f.created_at DESC LIMIT %s",
                (ctx.user_id, *session_arg, limit),
            ).fetchall():
                items.append(_file_item(r))
    items.sort(key=lambda i: i.created_at, reverse=True)
    return items[:limit]


def get_chart(ctx: RequestContext, chart_id: uuid.UUID) -> ChartResult:
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        row = conn.execute(
            "SELECT result FROM workspace_charts WHERE id = %s AND user_id = %s", (chart_id, ctx.user_id)
        ).fetchone()
    if row is None:
        raise KeyError(chart_id)
    return ChartResult.model_validate(row["result"])


def open_file(ctx: RequestContext, file_id: uuid.UUID) -> tuple[StoreItem, Iterator[bytes]]:
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        row = conn.execute(
            "SELECT id, session_id, filename, mime_type, size_bytes, object_key, created_at "
            "FROM workspace_files WHERE id = %s AND user_id = %s",
            (file_id, ctx.user_id),
        ).fetchone()
    if row is None:
        raise KeyError(file_id)
    blob = gcs_client().bucket(settings.gcs_bucket).blob(row["object_key"])

    def stream() -> Iterator[bytes]:
        with blob.open("rb") as fh:
            while chunk := fh.read(256 * 1024):
                yield chunk

    return _file_item(row), stream()


def table_artifact(ctx: RequestContext, artifact_id: uuid.UUID) -> Artifact:
    """Looks up one of the user's result tables by id (to open it from the store)."""
    with tenant_transaction(ctx.tenant_id) as conn:
        row = conn.execute(
            "SELECT id, session_id, name, kind, connection_id, parent_ids, sql_text, source_tables, "
            "row_count, arrow_bytes, truncated, columns, created_at FROM workspace_artifacts "
            "WHERE id = %s AND user_id = %s",
            (artifact_id, ctx.user_id),
        ).fetchone()
    if row is None:
        raise KeyError(artifact_id)
    return Artifact.model_validate(row)
