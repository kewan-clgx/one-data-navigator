"""Per-user favorites and recently queried tables, used to rank explorer search and agent table choices."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from typing import Literal
from uuid import UUID

import psycopg
from pydantic import BaseModel

from odn.db import tenant_transaction
from odn.platform.policy.sql_policy import _QUALIFIED_PARTS, SqlPolicy
from odn.tenancy.context import RequestContext

log = logging.getLogger(__name__)

RECENT_LIMIT = 50


class Favorite(BaseModel):
    path: str
    kind: Literal["dataset", "table"]
    created_at: datetime


class TableUsage(BaseModel):
    path: str
    query_count: int
    last_used_at: datetime


class NotAllowed(Exception):
    pass


def _kind(policy: SqlPolicy, parts: tuple[str, ...]) -> Literal["dataset", "table"]:
    full = _QUALIFIED_PARTS[policy.dialect]
    if len(parts) == full and policy.allows_object(parts):
        return "table"
    if len(parts) == full - 1 and policy.allows_container(parts):
        return "dataset"
    raise NotAllowed(".".join(parts))


def _visible(policy: SqlPolicy, path: str) -> bool:
    parts = tuple(path.split("."))
    try:
        _kind(policy, parts)
    except NotAllowed:
        return False
    return True


def list_favorites(ctx: RequestContext, connection_id: UUID, policy: SqlPolicy) -> list[Favorite]:
    with tenant_transaction(ctx.tenant_id) as conn:
        rows = conn.execute(
            "SELECT object_path AS path, kind, created_at FROM user_favorites "
            "WHERE user_id = %s AND connection_id = %s ORDER BY created_at DESC",
            (ctx.user_id, connection_id),
        ).fetchall()
    # Hidden once the connection's allowlist no longer covers them.
    return [Favorite.model_validate(r) for r in rows if _visible(policy, r["path"])]


def add_favorite(ctx: RequestContext, connection_id: UUID, policy: SqlPolicy, path: list[str]) -> Favorite:
    parts = tuple(path) if policy.dialect == "snowflake" else tuple(p.lower() for p in path)
    if any(not p or "." in p for p in parts):
        raise NotAllowed(".".join(parts))
    kind = _kind(policy, parts)
    key = ".".join(parts)
    with tenant_transaction(ctx.tenant_id) as conn:
        conn.execute(
            "INSERT INTO user_favorites (tenant_id, user_id, connection_id, object_path, kind) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (ctx.tenant_id, ctx.user_id, connection_id, key, kind),
        )
        row = conn.execute(
            "SELECT object_path AS path, kind, created_at FROM user_favorites "
            "WHERE user_id = %s AND connection_id = %s AND object_path = %s",
            (ctx.user_id, connection_id, key),
        ).fetchone()
    return Favorite.model_validate(row)


def remove_favorite(ctx: RequestContext, connection_id: UUID, path: list[str], *, preserve_case: bool = False) -> None:
    with tenant_transaction(ctx.tenant_id) as conn:
        conn.execute(
            "DELETE FROM user_favorites WHERE user_id = %s AND connection_id = %s AND object_path = %s",
            (ctx.user_id, connection_id, ".".join(path if preserve_case else [p.lower() for p in path])),
        )


def recent_tables(
    ctx: RequestContext, connection_id: UUID, policy: SqlPolicy, limit: int = RECENT_LIMIT
) -> list[TableUsage]:
    with tenant_transaction(ctx.tenant_id) as conn:
        rows = conn.execute(
            "SELECT table_path AS path, query_count, last_used_at FROM user_table_usage "
            "WHERE user_id = %s AND connection_id = %s ORDER BY last_used_at DESC LIMIT %s",
            (ctx.user_id, connection_id, limit),
        ).fetchall()
    return [TableUsage.model_validate(r) for r in rows if _visible(policy, r["path"])]


def record_usage(ctx: RequestContext, connection_id: UUID, tables: Iterable[str], *, preserve_case: bool = False) -> None:
    """Best effort: a usage-tracking failure never fails the user's query."""
    paths = sorted({t if preserve_case else t.lower() for t in tables})
    if not paths:
        return
    try:
        with tenant_transaction(ctx.tenant_id) as conn:
            for path in paths:
                conn.execute(
                    """
                    INSERT INTO user_table_usage (tenant_id, user_id, connection_id, table_path)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (tenant_id, user_id, connection_id, table_path) DO UPDATE
                      SET query_count = user_table_usage.query_count + 1, last_used_at = now()
                    """,
                    (ctx.tenant_id, ctx.user_id, connection_id, path),
                )
    except psycopg.Error:
        log.warning("could not record table usage", exc_info=True)
