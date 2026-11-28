"""The user's artifacts store: result tables, saved charts and uploaded files across all conversations."""

from __future__ import annotations

import base64
import json
import os
import tempfile
import uuid
from collections.abc import Iterator
from datetime import datetime
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
    InvalidSession,
    _check_session,
    _explained,
    _insert_artifact,
    _object_key,
)
from odn.platform.workspace.storage import assert_tenant_key, gcs_client, tenant_key
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
    file_key = tenant_key(ctx.tenant_id, ctx.user_id, session_id, "files", f"{file_id}{ext}")
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
    blob = gcs_client().bucket(settings.gcs_bucket).blob(assert_tenant_key(ctx.tenant_id, ctx.user_id, row["object_key"]))

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


# -- the Artifacts panel: one paged, searchable list over every kind (tables, charts, files, saved SQL) -------------

PanelKind = Literal["table", "chart", "file", "sql"]
PanelSort = Literal["newest", "name"]
SQL_HEAD_LINES = 10


class PanelItem(StoreItem):
    kind: PanelKind  # type: ignore[assignment]
    # table and saved SQL: the first lines of the SQL, for the default preview
    sql_head: str | None = None
    sql_lines: int | None = None
    column_names: list[str] = []
    # saved SQL
    description: str | None = None
    dialect: str | None = None
    version: int | None = None
    tables: list[str] = []
    # archived items (the Archived view): when they'll be deleted, None = kept forever
    archived_at: datetime | None = None
    purge_at: datetime | None = None


class PanelPage(BaseModel):
    items: list[PanelItem]
    next_cursor: str | None = None
    # Per kind, for the same search and conversation filter (first page only).
    counts: dict[str, int] | None = None


def _head(sql: str) -> tuple[str, int]:
    lines = sql.rstrip("\n").split("\n")
    return "\n".join(lines[:SQL_HEAD_LINES]), len(lines)


def _encode_cursor(values: list[Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(values, default=str).encode()).decode()


def _decode_cursor(cursor: str, sort: PanelSort) -> tuple[Any, str, uuid.UUID]:
    try:
        key, kind, item_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return (datetime.fromisoformat(key) if sort == "newest" else str(key)), str(kind), uuid.UUID(item_id)
    except (ValueError, TypeError) as exc:
        raise InvalidSession("invalid cursor") from exc


def page_store(
    ctx: RequestContext, *, kind: PanelKind | None = None, session_id: str | None = None, q: str = "",
    sort: PanelSort = "newest", limit: int = 20, cursor: str | None = None, archived: bool = False,
) -> PanelPage:
    """One page of the user's artifacts, newest first or by name, across kinds, searched on the server. Archived
    artifacts are left out (and not searchable), except in the Archived view (`archived=True`), which shows only them."""
    if session_id is not None:
        _check_session(session_id)
    like = f"%{q.strip()}%" if q.strip() else None
    base = [ctx.tenant_id, ctx.user_id]

    def part(select: str, table: str, search: str, session_col: str | None = "session_id") -> tuple[str, list[Any]]:
        where, args = [f"{table}.tenant_id = %s", f"{table}.user_id = %s",
                       f"{table}.archived_at IS {'NOT ' if archived else ''}NULL"], list(base)
        if session_id is not None and session_col:
            where.append(f"{table}.{session_col} = %s")
            args.append(session_id)
        if like:
            where.append(f"({search})")
            args += [like] * search.count("%s")
        return f"SELECT {select} FROM {table} WHERE {' AND '.join(where)}", args

    parts = [
        part("'table' AS kind, id, created_at AS at, lower(name) AS sort_name", "workspace_artifacts",
             "name ILIKE %s OR sql_text ILIKE %s OR array_to_string(source_tables, ' ') ILIKE %s"),
        part("'chart', id, created_at, lower(coalesce(nullif(title, ''), name))", "workspace_charts",
             "name ILIKE %s OR title ILIKE %s"),
        part("'file', id, created_at, lower(filename)", "workspace_files", "filename ILIKE %s"),
    ]
    if session_id is None:  # saved SQL isn't tied to a conversation
        parts.append(part("'sql', id, updated_at, name", "saved_queries",
                          "name ILIKE %s OR description ILIKE %s OR sql_text ILIKE %s "
                          "OR array_to_string(tables, ' ') ILIKE %s", session_col=None))
    union = " UNION ALL ".join(f"({sql})" for sql, _ in parts)
    union_args = [a for _, args in parts for a in args]

    where, args = [], []
    if kind:
        where.append("kind = %s")
        args.append(kind)
    if cursor:
        key, ckind, cid = _decode_cursor(cursor, sort)
        where.append("(at, kind, id) < (%s, %s, %s)" if sort == "newest" else "(sort_name, kind, id) > (%s, %s, %s)")
        args += [key, ckind, cid]
    order = "at DESC, kind DESC, id DESC" if sort == "newest" else "sort_name ASC, kind ASC, id ASC"
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        rows = conn.execute(
            f"SELECT kind, id, at, sort_name FROM ({union}) items "
            f"{'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY {order} LIMIT %s",
            (*union_args, *args, limit + 1),
        ).fetchall()
        counts = None
        if cursor is None:
            counts = {r["kind"]: r["n"] for r in conn.execute(
                f"SELECT kind, count(*) AS n FROM ({union}) items GROUP BY kind", union_args,
            ).fetchall()}
        more = len(rows) > limit
        rows = rows[:limit]
        details = _panel_details(conn, ctx, rows)
    items = [details[(r["kind"], r["id"])] for r in rows if (r["kind"], r["id"]) in details]
    next_cursor = None
    if more and rows:
        last = rows[-1]
        next_cursor = _encode_cursor([last["at"].isoformat() if sort == "newest" else last["sort_name"], last["kind"],
                                      str(last["id"])])
    return PanelPage(items=items, next_cursor=next_cursor, counts=counts)


def _panel_details(conn: Any, ctx: RequestContext, rows: list[dict[str, Any]]) -> dict[tuple[str, Any], PanelItem]:
    ids: dict[str, list[Any]] = {}
    for r in rows:
        ids.setdefault(r["kind"], []).append(r["id"])
    out: dict[tuple[str, Any], PanelItem] = {}
    if ids.get("table"):
        for r in conn.execute(
            "SELECT id, session_id, name, kind, sql_text, source_tables, row_count, columns, created_at, archived_at, "
            "purge_at FROM workspace_artifacts WHERE user_id = %s AND id = ANY(%s)", (ctx.user_id, ids["table"]),
        ).fetchall():
            title = (f"Uploaded {r['source_tables'][0]}" if r["kind"] == "upload" and r["source_tables"]
                     else "Warehouse query" if r["kind"] == "warehouse" else "Workspace follow-up")
            head, lines = _head(r["sql_text"]) if r["kind"] != "upload" else (None, None)
            out[("table", r["id"])] = PanelItem(
                id=r["id"], kind="table", session_id=r["session_id"], name=r["name"], title=title,
                created_at=r["created_at"], origin=r["kind"], row_count=r["row_count"],
                column_count=len(r["columns"]), source_tables=r["source_tables"], sql_head=head, sql_lines=lines,
                column_names=[c["name"] for c in r["columns"][:200]], archived_at=r["archived_at"],
                purge_at=r["purge_at"],
            )
    if ids.get("chart"):
        for r in conn.execute(
            "SELECT id, session_id, name, title, chart_type, jsonb_array_length(result->'points') AS points, "
            "result->'source_tables' AS source_tables, created_at, archived_at, purge_at FROM workspace_charts "
            "WHERE user_id = %s AND id = ANY(%s)", (ctx.user_id, ids["chart"]),
        ).fetchall():
            out[("chart", r["id"])] = PanelItem(
                id=r["id"], kind="chart", session_id=r["session_id"], name=r["name"],
                title=r["title"] or f"{r['chart_type'].capitalize()} chart", created_at=r["created_at"],
                chart_type=r["chart_type"], point_count=r["points"], source_tables=r["source_tables"] or [],
                archived_at=r["archived_at"], purge_at=r["purge_at"],
            )
    if ids.get("file"):
        for r in conn.execute(
            "SELECT f.id, f.session_id, f.filename, f.mime_type, f.size_bytes, f.created_at, f.archived_at, f.purge_at, "
            "a.name AS table_name FROM workspace_files f LEFT JOIN workspace_artifacts a ON a.id = f.table_artifact_id "
            "WHERE f.user_id = %s AND f.id = ANY(%s)", (ctx.user_id, ids["file"]),
        ).fetchall():
            out[("file", r["id"])] = PanelItem(**_file_item(r).model_dump(), archived_at=r["archived_at"],
                                               purge_at=r["purge_at"])
    if ids.get("sql"):
        for r in conn.execute(
            "SELECT id, name, description, sql_text, dialect, tables, version, updated_at, archived_at, purge_at "
            "FROM saved_queries "
            "WHERE user_id = %s AND id = ANY(%s)", (ctx.user_id, ids["sql"]),
        ).fetchall():
            head, lines = _head(r["sql_text"])
            out[("sql", r["id"])] = PanelItem(
                id=r["id"], kind="sql", session_id="", name=r["name"], title=r["description"],
                created_at=r["updated_at"], description=r["description"], dialect=r["dialect"], tables=r["tables"],
                version=r["version"], sql_head=head, sql_lines=lines, source_tables=r["tables"],
                archived_at=r["archived_at"], purge_at=r["purge_at"],
            )
    return out
