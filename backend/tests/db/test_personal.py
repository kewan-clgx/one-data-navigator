"""Favorites and table usage: per-user scoping, tenant isolation and allowlist enforcement (skipped if Postgres is down)."""

import uuid

import psycopg
import pytest

from odn.config import settings
from odn.platform.catalog.personal import (
    NotAllowed,
    add_favorite,
    list_favorites,
    recent_tables,
    record_usage,
    remove_favorite,
)
from odn.platform.policy.sql_policy import SqlPolicy
from odn.tenancy.context import RequestContext
from tests.db.test_rls import APP_URL, OWNER_URL, _reachable

pytestmark = pytest.mark.skipif(not _reachable(), reason="local Postgres not running")

POLICY = SqlPolicy(dialect="bigquery", allowed_objects=frozenset({"p.sales.*"}), default_namespace=("p",))


@pytest.fixture(scope="module")
def users():
    from odn.db import pool
    from odn.db.migrate import migrate

    migrate(OWNER_URL)
    settings.database_url = APP_URL
    pool.cache_clear()
    t, c = uuid.uuid4(), uuid.uuid4()
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    t2, c2, u3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(OWNER_URL) as conn:
        for tenant, conn_id, members in ((t, c, (u1, u2)), (t2, c2, (u3,))):
            conn.execute("INSERT INTO tenants (id, slug, name) VALUES (%s, %s, 'x')",
                         (tenant, f"t-{tenant.hex[:12]}"))
            for u in members:
                conn.execute("INSERT INTO users (id, tenant_id, subject) VALUES (%s, %s, %s)", (u, tenant, u.hex))
            conn.execute(
                "INSERT INTO connections (id, tenant_id, name, kind, config, secret_ref, allowed_objects) "
                "VALUES (%s, %s, 'bq', 'bigquery', '{}', 'file:x', '{p.sales.*}')",
                (conn_id, tenant),
            )
    yield (
        RequestContext(tenant_id=t, user_id=u1, subject="a", role="member"),
        RequestContext(tenant_id=t, user_id=u2, subject="b", role="member"),
        RequestContext(tenant_id=t2, user_id=u3, subject="c", role="member"),
        c,
    )
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM tenants WHERE id = ANY(%s)", ([t, t2],))


def test_favorites_are_per_user_and_typed(users) -> None:
    a, b, other_tenant, conn_id = users
    assert add_favorite(a, conn_id, POLICY, ["p", "Sales", "Orders"]).kind == "table"
    assert add_favorite(a, conn_id, POLICY, ["p", "sales"]).kind == "dataset"
    assert {f.path for f in list_favorites(a, conn_id, POLICY)} == {"p.sales.orders", "p.sales"}
    assert list_favorites(b, conn_id, POLICY) == []
    assert list_favorites(other_tenant, conn_id, POLICY) == []
    remove_favorite(a, conn_id, ["p", "sales", "orders"])
    assert [f.path for f in list_favorites(a, conn_id, POLICY)] == ["p.sales"]


def test_favorites_must_be_in_allowlist(users) -> None:
    a, _, _, conn_id = users
    for path in (["p", "hr", "salaries"], ["p", "hr"], ["p"], ["p", "sales", "a.b"]):
        with pytest.raises(NotAllowed):
            add_favorite(a, conn_id, POLICY, path)
    narrower = SqlPolicy(dialect="bigquery", allowed_objects=frozenset({"p.other.*"}))
    assert list_favorites(a, conn_id, narrower) == []


def test_snowflake_favorites_and_usage_preserve_case(users) -> None:
    a, _, _, conn_id = users
    policy = SqlPolicy(dialect="snowflake", allowed_objects=frozenset({"DB.PUBLIC.ORDERS", "DB.PUBLIC.orders"}))
    add_favorite(a, conn_id, policy, ["DB", "PUBLIC", "ORDERS"])
    add_favorite(a, conn_id, policy, ["DB", "PUBLIC", "orders"])
    assert {f.path for f in list_favorites(a, conn_id, policy)} == {"DB.PUBLIC.ORDERS", "DB.PUBLIC.orders"}
    record_usage(a, conn_id, ["DB.PUBLIC.ORDERS", "DB.PUBLIC.orders"], preserve_case=True)
    assert {r.path for r in recent_tables(a, conn_id, policy)} == {"DB.PUBLIC.ORDERS", "DB.PUBLIC.orders"}
    remove_favorite(a, conn_id, ["DB", "PUBLIC", "ORDERS"], preserve_case=True)
    assert [f.path for f in list_favorites(a, conn_id, policy)] == ["DB.PUBLIC.orders"]


def test_usage_counts_and_recency(users) -> None:
    a, b, _, conn_id = users
    record_usage(a, conn_id, ["p.sales.orders", "p.sales.items"])
    record_usage(a, conn_id, ["p.sales.orders"])
    recent = recent_tables(a, conn_id, POLICY)
    assert [(r.path, r.query_count) for r in recent] == [("p.sales.orders", 2), ("p.sales.items", 1)]
    assert recent_tables(b, conn_id, POLICY) == []
