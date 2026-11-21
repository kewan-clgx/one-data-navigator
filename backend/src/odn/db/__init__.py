"""Platform metadata database access. Tenant-scoped work must go through `tenant_transaction`."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from odn.config import settings


@cache
def pool() -> ConnectionPool:
    return ConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=10,
        kwargs={"row_factory": dict_row},
        # Drop connections the database closed (e.g. Postgres restarted by a deploy) instead of failing requests.
        check=ConnectionPool.check_connection,
        open=True,
    )


@contextmanager
def tenant_transaction(tenant_id: UUID, user_id: UUID | None = None) -> Iterator[psycopg.Connection]:
    """Transaction with RLS scoped to one tenant (and, if given, one user: personal connections and LLM keys).

    The settings are discarded at commit/rollback.
    """
    with pool().connection() as conn, conn.transaction():
        conn.execute("SELECT set_config('odn.tenant_id', %s, true)", (str(tenant_id),))
        if user_id is not None:
            conn.execute("SELECT set_config('odn.user_id', %s, true)", (str(user_id),))
        yield conn


@contextmanager
def system_transaction() -> Iterator[psycopg.Connection]:
    """Transaction with no tenant context: RLS hides all tenant rows (used for login-time lookups)."""
    with pool().connection() as conn, conn.transaction():
        yield conn
