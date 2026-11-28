"""Saved SQL: each user's private SQL repository (docs/sql_repository_plan.md)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import sqlglot
from pydantic import BaseModel
from sqlglot import exp
from sqlglot.errors import SqlglotError

from odn.db import tenant_transaction
from odn.platform.connections.service import get_connection
from odn.tenancy.context import RequestContext

Dialect = Literal["bigquery", "postgres", "snowflake", "mysql", "oracle", "duckdb"]
MAX_QUERIES_PER_USER = 1000
KEEP_VERSIONS = 20
SNIPPET = 200
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")


class SavedQueryError(Exception):
    """A request the user can fix (bad name, limit reached, unknown connection)."""


class NameTaken(Exception):
    def __init__(self, suggestion: str) -> None:
        super().__init__("name_taken")
        self.suggestion = suggestion


class VersionConflict(Exception):
    def __init__(self, current: int) -> None:
        super().__init__("conflict")
        self.current = current


class SavedQueryItem(BaseModel):
    id: UUID
    name: str
    description: str
    connection_id: UUID | None
    dialect: Dialect
    tables: list[str]
    version: int
    created_at: datetime
    updated_at: datetime
    snippet: str = ""


class SavedQuery(SavedQueryItem):
    sql: str


class SavedQueryVersion(BaseModel):
    version: int
    sql: str
    saved_at: datetime


def normalize_name(name: str) -> str:
    """File-style name: lower-case [a-z0-9_-], no ".sql" suffix, at most 80 characters."""
    n = name.strip().lower()
    n = re.sub(r"\.sql$", "", n)
    n = re.sub(r"[^a-z0-9_-]+", "_", n).strip("_-")[:80].rstrip("_-")
    if not NAME_RE.match(n):
        raise SavedQueryError("Use letters, digits, '_' or '-' for the name.")
    return n


def tables_in(sql: str, dialect: Dialect) -> list[str]:
    """Tables a SQL text reads (CTE names excluded); empty when it doesn't parse yet (work in progress is fine)."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except SqlglotError:
        return []
    out: list[str] = []
    for statement in statements:
        ctes = {c.alias_or_name.lower() for c in statement.find_all(exp.CTE)}
        for t in statement.find_all(exp.Table):
            parts = [p for p in (t.catalog, t.db, t.name) if p]
            if not parts or (len(parts) == 1 and parts[0].lower() in ctes):
                continue
            name = ".".join(parts)
            if name not in out:
                out.append(name)
    return out[:20]


def _check_connection(ctx: RequestContext, connection_id: UUID | None) -> None:
    if connection_id is not None and get_connection(ctx.tenant_id, ctx.user_id, connection_id) is None:
        raise SavedQueryError("Unknown connection.")


def _free_name(conn: Any, ctx: RequestContext, name: str) -> str:
    taken = {r["name"] for r in conn.execute(
        "SELECT name FROM saved_queries WHERE tenant_id = %s AND user_id = %s AND name LIKE %s",
        (ctx.tenant_id, ctx.user_id, f"{name[:76]}%"),
    ).fetchall()}
    for i in range(2, 1000):
        candidate = f"{name[:76]}_{i}"
        if candidate not in taken:
            return candidate
    return f"{name[:70]}_{datetime.now():%H%M%S}"


_COLUMNS = "id, name, description, connection_id, dialect, tables, version, created_at, updated_at"


def _item(row: dict[str, Any]) -> SavedQueryItem:
    snippet = re.sub(r"\s+", " ", row.get("sql_text") or "").strip()[:SNIPPET]
    return SavedQueryItem(**{k: row[k] for k in _COLUMNS.split(", ")}, snippet=snippet)


def list_queries(ctx: RequestContext, *, q: str = "", table: str = "", limit: int = 200,
                 full: bool = False) -> list[SavedQueryItem]:
    """The user's saved SQL, recently edited first; `q` matches name, description, SQL text or a table name."""
    where, args = ["tenant_id = %s", "user_id = %s"], [ctx.tenant_id, ctx.user_id]
    if q.strip():
        like = f"%{q.strip()}%"
        where.append("(name ILIKE %s OR description ILIKE %s OR sql_text ILIKE %s "
                     "OR EXISTS (SELECT 1 FROM unnest(tables) t WHERE t ILIKE %s))")
        args += [like, like, like, like]
    if table.strip():
        # A table matches by full path or by its last parts (`dataset.table`, `table`).
        where.append("EXISTS (SELECT 1 FROM unnest(tables) t WHERE lower(t) = lower(%s) OR lower(t) LIKE lower(%s))")
        args += [table.strip(), f"%.{table.strip()}"]
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        rows = conn.execute(
            f"SELECT {_COLUMNS}, {'sql_text' if full else 'left(sql_text, 400) AS sql_text'} FROM saved_queries "
            f"WHERE {' AND '.join(where)} ORDER BY updated_at DESC LIMIT %s", (*args, limit),
        ).fetchall()
    if full:
        return [SavedQuery(**_item(r).model_dump(), sql=r["sql_text"]) for r in rows]
    return [_item(r) for r in rows]


def get_query(ctx: RequestContext, query_id: UUID) -> SavedQuery:
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        row = conn.execute(
            f"SELECT {_COLUMNS}, sql_text FROM saved_queries WHERE id = %s AND tenant_id = %s AND user_id = %s",
            (query_id, ctx.tenant_id, ctx.user_id),
        ).fetchone()
    if row is None:
        raise KeyError(str(query_id))
    return SavedQuery(**_item(row).model_dump(), sql=row["sql_text"])


def _keep_version(conn: Any, ctx: RequestContext, query_id: UUID, version: int, sql: str) -> None:
    conn.execute(
        "INSERT INTO saved_query_versions (query_id, tenant_id, user_id, version, sql_text) VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (query_id, version) DO UPDATE SET sql_text = EXCLUDED.sql_text, saved_at = now()",
        (query_id, ctx.tenant_id, ctx.user_id, version, sql),
    )
    conn.execute(
        "DELETE FROM saved_query_versions WHERE query_id = %s AND version <= %s", (query_id, version - KEEP_VERSIONS),
    )


def create_query(ctx: RequestContext, *, name: str, sql: str, dialect: Dialect, connection_id: UUID | None,
                 description: str = "") -> SavedQuery:
    n = normalize_name(name)
    _check_connection(ctx, connection_id)
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        count = conn.execute(
            "SELECT count(*) AS n FROM saved_queries WHERE tenant_id = %s AND user_id = %s", (ctx.tenant_id, ctx.user_id),
        ).fetchone()["n"]
        if count >= MAX_QUERIES_PER_USER:
            raise SavedQueryError(f"You can keep up to {MAX_QUERIES_PER_USER} saved queries; delete some first.")
        row = conn.execute(
            "INSERT INTO saved_queries (tenant_id, user_id, name, description, sql_text, connection_id, dialect, tables) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (tenant_id, user_id, name) DO NOTHING "
            f"RETURNING {_COLUMNS}, sql_text",
            (ctx.tenant_id, ctx.user_id, n, description.strip()[:300], sql, connection_id, dialect,
             tables_in(sql, dialect)),
        ).fetchone()
        if row is None:
            raise NameTaken(_free_name(conn, ctx, n))
        _keep_version(conn, ctx, row["id"], 1, sql)
    return SavedQuery(**_item(row).model_dump(), sql=row["sql_text"])


def update_query(ctx: RequestContext, query_id: UUID, *, version: int, sql: str | None = None, name: str | None = None,
                 description: str | None = None, dialect: Dialect | None = None,
                 connection_id: UUID | None = None, set_connection: bool = False) -> SavedQuery:
    """Saves changes made to `version`; a newer stored version (saved elsewhere meanwhile) raises VersionConflict."""
    if set_connection:
        _check_connection(ctx, connection_id)
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        current = conn.execute(
            "SELECT version, sql_text, dialect FROM saved_queries WHERE id = %s AND tenant_id = %s AND user_id = %s "
            "FOR UPDATE", (query_id, ctx.tenant_id, ctx.user_id),
        ).fetchone()
        if current is None:
            raise KeyError(str(query_id))
        if current["version"] != version:
            raise VersionConflict(current["version"])
        sets, args = ["updated_at = now()"], []
        if name is not None:
            n = normalize_name(name)
            if conn.execute(
                "SELECT 1 FROM saved_queries WHERE tenant_id = %s AND user_id = %s AND name = %s AND id <> %s",
                (ctx.tenant_id, ctx.user_id, n, query_id),
            ).fetchone():
                raise NameTaken(_free_name(conn, ctx, n))
            sets.append("name = %s")
            args.append(n)
        if description is not None:
            sets.append("description = %s")
            args.append(description.strip()[:300])
        if set_connection:
            sets.append("connection_id = %s")
            args.append(connection_id)
        if dialect is not None:
            sets.append("dialect = %s")
            args.append(dialect)
        new_sql = sql is not None and sql != current["sql_text"]
        if new_sql or dialect is not None:
            text = sql if sql is not None else current["sql_text"]
            sets += ["sql_text = %s", "tables = %s"]
            args += [text, tables_in(text, dialect or current["dialect"])]
        if new_sql:
            sets.append("version = version + 1")
        row = conn.execute(
            f"UPDATE saved_queries SET {', '.join(sets)} WHERE id = %s RETURNING {_COLUMNS}, sql_text",
            (*args, query_id),
        ).fetchone()
        if new_sql:
            _keep_version(conn, ctx, query_id, row["version"], row["sql_text"])
    return SavedQuery(**_item(row).model_dump(), sql=row["sql_text"])


def delete_query(ctx: RequestContext, query_id: UUID) -> None:
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        cur = conn.execute(
            "DELETE FROM saved_queries WHERE id = %s AND tenant_id = %s AND user_id = %s",
            (query_id, ctx.tenant_id, ctx.user_id),
        )
        if cur.rowcount == 0:
            raise KeyError(str(query_id))


def list_versions(ctx: RequestContext, query_id: UUID) -> list[SavedQueryVersion]:
    get_query(ctx, query_id)  # 404 when not the user's
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        rows = conn.execute(
            "SELECT version, sql_text, saved_at FROM saved_query_versions WHERE query_id = %s ORDER BY version DESC",
            (query_id,),
        ).fetchall()
    return [SavedQueryVersion(version=r["version"], sql=r["sql_text"], saved_at=r["saved_at"]) for r in rows]
