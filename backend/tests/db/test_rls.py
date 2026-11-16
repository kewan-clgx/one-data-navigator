"""Cross-tenant isolation against the real Postgres (docker compose stack). Skipped if it is not running."""

import os
import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb

from odn.db.migrate import migrate

OWNER_URL = os.environ.get("ODN_TEST_OWNER_URL", "postgresql://odn:odn_local_dev@localhost:5532/odn")
APP_URL = os.environ.get("ODN_TEST_APP_URL", "postgresql://odn_app:odn_app_local_dev@localhost:5532/odn")


def _reachable() -> bool:
    try:
        psycopg.connect(OWNER_URL, connect_timeout=2).close()
    except psycopg.OperationalError:
        return False
    return True


pytestmark = pytest.mark.skipif(not _reachable(), reason="local Postgres not running")


@pytest.fixture(scope="module")
def tenants() -> tuple[uuid.UUID, uuid.UUID]:
    migrate(OWNER_URL)
    a, b = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(OWNER_URL) as conn:
        for t in (a, b):
            conn.execute(
                "INSERT INTO tenants (id, slug, name, oidc_issuer) VALUES (%s, %s, %s, 'test-issuer')",
                (t, f"t-{t.hex[:12]}", "test"),
            )
    yield a, b
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM tenants WHERE id = ANY(%s)", ([a, b],))


def _as_tenant(conn: psycopg.Connection, tenant: uuid.UUID | None) -> None:
    if tenant:
        conn.execute("SELECT set_config('odn.tenant_id', %s, true)", (str(tenant),))


def _insert_connection(conn: psycopg.Connection, tenant: uuid.UUID, name: str) -> None:
    conn.execute(
        "INSERT INTO connections (tenant_id, name, kind, config, secret_ref, allowed_objects) "
        "VALUES (%s, %s, 'bigquery', %s, 'file:x.json', '{p.d.*}')",
        (tenant, name, Jsonb({"project": "p"})),
    )


def test_tenant_sees_only_own_rows(tenants: tuple[uuid.UUID, uuid.UUID]) -> None:
    a, b = tenants
    with psycopg.connect(APP_URL) as conn:
        with conn.transaction():
            _as_tenant(conn, a)
            _insert_connection(conn, a, "conn-a")
        with conn.transaction():
            _as_tenant(conn, b)
            _insert_connection(conn, b, "conn-b")
        with conn.transaction():
            _as_tenant(conn, a)
            names = {r[0] for r in conn.execute("SELECT name FROM connections")}
        assert names == {"conn-a"}


def test_no_tenant_context_sees_nothing(tenants: tuple[uuid.UUID, uuid.UUID]) -> None:
    with psycopg.connect(APP_URL) as conn, conn.transaction():
        assert conn.execute("SELECT count(*) FROM connections").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 0


def test_cannot_write_into_other_tenant(tenants: tuple[uuid.UUID, uuid.UUID]) -> None:
    a, b = tenants
    with (
        psycopg.connect(APP_URL) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        conn.transaction(),
    ):
        _as_tenant(conn, a)
        _insert_connection(conn, b, "sneaky")


def test_cannot_update_other_tenant_rows(tenants: tuple[uuid.UUID, uuid.UUID]) -> None:
    a, _ = tenants
    with psycopg.connect(APP_URL) as conn, conn.transaction():
        _as_tenant(conn, a)
        updated = conn.execute("UPDATE connections SET name = 'x' WHERE name = 'conn-b'").rowcount
    assert updated == 0


def test_app_cannot_read_tenant_directory_or_delete_audit(tenants: tuple[uuid.UUID, uuid.UUID]) -> None:
    a, _ = tenants
    with psycopg.connect(APP_URL) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute("SELECT * FROM tenants")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            _as_tenant(conn, a)
            conn.execute("DELETE FROM audit_logs")


def test_resolve_tenant_requires_matching_issuer(tenants: tuple[uuid.UUID, uuid.UUID]) -> None:
    a, _ = tenants
    slug = f"t-{a.hex[:12]}"
    with psycopg.connect(APP_URL) as conn:
        assert conn.execute("SELECT odn_resolve_tenant(%s, 'test-issuer')", (slug,)).fetchone()[0] == a
        assert conn.execute("SELECT odn_resolve_tenant(%s, 'other-issuer')", (slug,)).fetchone()[0] is None
