"""Dictionary writes: tenant isolation and allowlist enforcement against the real Postgres (skipped if down)."""

import uuid

import psycopg
import pytest

from odn.config import settings
from odn.platform.catalog.dictionary import DefinitionInput, NotAllowed, all_definitions, upsert_definition
from odn.platform.policy.sql_policy import SqlPolicy
from odn.tenancy.context import RequestContext
from tests.db.test_rls import APP_URL, OWNER_URL, _reachable

pytestmark = pytest.mark.skipif(not _reachable(), reason="local Postgres not running")

POLICY = SqlPolicy(dialect="bigquery", allowed_objects=frozenset({"p.sales.*"}), default_namespace=("p",))


@pytest.fixture(scope="module")
def two_tenants() -> tuple[RequestContext, RequestContext, uuid.UUID, uuid.UUID]:
    from odn.db import pool
    from odn.db.migrate import migrate

    migrate(OWNER_URL)
    settings.database_url = APP_URL
    pool.cache_clear()
    ids = []
    with psycopg.connect(OWNER_URL) as conn:
        for _ in range(2):
            t, u, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            conn.execute("INSERT INTO tenants (id, slug, name, oidc_issuer) VALUES (%s, %s, 'x', 'iss')", (t, f"t-{t.hex[:12]}"))
            conn.execute("INSERT INTO users (id, tenant_id, subject, role) VALUES (%s, %s, 'lead', 'data_lead')", (u, t))
            conn.execute(
                "INSERT INTO connections (id, tenant_id, name, kind, config, secret_ref, allowed_objects) "
                "VALUES (%s, %s, 'bq', 'bigquery', '{}', 'file:x', '{p.sales.*}')",
                (c, t),
            )
            ids.append((t, u, c))
    (ta, ua, ca), (tb, ub, cb) = ids
    yield (
        RequestContext(tenant_id=ta, user_id=ua, subject="lead-a", role="data_lead"),
        RequestContext(tenant_id=tb, user_id=ub, subject="lead-b", role="data_lead"),
        ca,
        cb,
    )
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM tenants WHERE id = ANY(%s)", ([ta, tb],))


def test_upsert_versioned_and_tenant_isolated(two_tenants) -> None:
    a, b, conn_a, _ = two_tenants
    upsert_definition(a, conn_a, POLICY, DefinitionInput(table_path="p.sales.orders", field_path="amount",
                                                          definition="Order value in USD", status="draft"))
    upsert_definition(a, conn_a, POLICY, DefinitionInput(table_path="p.sales.orders", field_path="amount",
                                                          definition="Order value in USD, tax included",
                                                          status="approved"))
    defs = all_definitions(a, conn_a)
    assert defs == [{"table_path": "p.sales.orders", "field_path": "amount",
                     "definition": "Order value in USD, tax included", "status": "approved"}]
    assert all_definitions(b, conn_a) == []
    with psycopg.connect(OWNER_URL) as conn:
        n = conn.execute("SELECT count(*) FROM data_definition_history WHERE tenant_id = %s", (a.tenant_id,)).fetchone()[0]
    assert n == 2


def test_cannot_define_objects_outside_allowlist(two_tenants) -> None:
    a, _, conn_a, _ = two_tenants
    with pytest.raises(NotAllowed):
        upsert_definition(a, conn_a, POLICY, DefinitionInput(table_path="p.hr.salaries", definition="x"))
