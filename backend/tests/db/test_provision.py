"""Operator provisioning CLI against Postgres (skipped if Postgres is down)."""

import uuid

import psycopg
import pytest

from odn.db.migrate import migrate
from odn.db.provision import set_role, upsert_connection, upsert_tenant
from tests.db.test_rls import OWNER_URL, _reachable

pytestmark = pytest.mark.skipif(not _reachable(), reason="local Postgres not running")


@pytest.fixture
def tenant():
    migrate(OWNER_URL)
    slug = f"t-{uuid.uuid4().hex[:12]}"
    tenant_id = upsert_tenant(OWNER_URL, slug, "Test", auth="google")
    yield slug, tenant_id
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))


def test_connection_requires_this_tenants_secret(tenant) -> None:
    slug, tenant_id = tenant
    allow = ["p.ds.*"]
    upsert_connection(OWNER_URL, slug, "bq", {"project": "p"},
                      f"projects/x/secrets/odn-{tenant_id}-bq/versions/latest", allow)
    for bad in (f"projects/x/secrets/odn-{uuid.uuid4()}-bq/versions/latest", "file:sa.json"):
        with pytest.raises(SystemExit):
            upsert_connection(OWNER_URL, slug, "bq2", {"project": "p"}, bad, allow)


def test_role_updates_only_existing_users_of_that_tenant(tenant) -> None:
    slug, tenant_id = tenant
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("SELECT set_config('odn.tenant_id', %s, false)", (str(tenant_id),))
        conn.execute("INSERT INTO users (tenant_id, subject, email) VALUES (%s, 's1', 'Lead@Example.com')", (tenant_id,))
    assert set_role(OWNER_URL, slug, "lead@example.com", "data_lead") == 1
    assert set_role(OWNER_URL, slug, "nobody@example.com", "data_lead") == 0
