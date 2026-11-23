"""Cross-tenant isolation against the real Postgres (docker compose stack). Skipped if it is not running."""

import os
import uuid

import psycopg
import psycopg.rows
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
                "INSERT INTO tenants (id, slug, name) VALUES (%s, %s, %s)",
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
            names = {r[0] for r in conn.execute("SELECT name FROM connections WHERE tenant_id IS NOT NULL")}
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
        for table in ("tenants", "tenant_domains", "tenant_identity_providers"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                conn.execute(f"SELECT * FROM {table}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            _as_tenant(conn, a)
            conn.execute("DELETE FROM audit_logs")


def test_resolve_identity(tenants: tuple[uuid.UUID, uuid.UUID]) -> None:
    a, b = tenants
    issuer = f"test-issuer-{a.hex[:8]}"
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute(
            "INSERT INTO tenant_identity_providers (issuer, external_id, tenant_id) VALUES (%s, 'acme.com', %s), (%s, '*', %s)",
            (issuer, a, issuer, b),
        )
        conn.execute("INSERT INTO tenant_domains (domain, tenant_id, verified_at) VALUES (%s, %s, now())", (f"{a.hex[:8]}.acme.com", a))

    def resolve(external_id: str, domain: str | None, iss: str = issuer):
        with psycopg.connect(APP_URL, row_factory=psycopg.rows.dict_row) as conn:
            return conn.execute("SELECT * FROM odn_resolve_identity(%s, %s, %s)", (iss, external_id, domain)).fetchone()

    exact = resolve("ACME.com", f"{a.hex[:8]}.acme.com")
    assert (exact["tenant_id"], exact["fallback"], exact["has_domains"], exact["domain_ok"]) == (a, False, True, True)
    assert resolve("acme.com", "evil.com")["domain_ok"] is False
    fallback = resolve("other.org", None)
    assert (fallback["tenant_id"], fallback["fallback"], fallback["domain_ok"]) == (b, True, True)
    assert resolve("acme.com", None, iss="other-issuer") is None


# Tables the app role cannot read at all; every other table must force RLS.
DIRECTORY_TABLES = {"tenants", "tenant_domains", "tenant_identity_providers", "schema_migrations"}


def test_every_table_forces_rls_or_is_a_directory_table() -> None:
    migrate(OWNER_URL)
    with psycopg.connect(OWNER_URL) as conn:
        rows = conn.execute(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, has_table_privilege('odn_app', c.oid, 'SELECT') "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')"
        ).fetchall()
    unprotected = [
        name for name, rls, forced, app_reads in rows
        if not (rls and forced) and (name not in DIRECTORY_TABLES or app_reads)
    ]
    assert unprotected == [], f"tables without FORCE ROW LEVEL SECURITY: {unprotected}"


def test_platform_connections_are_read_only_for_tenants(tenants: tuple[uuid.UUID, uuid.UUID]) -> None:
    a, _ = tenants
    name = f"platform-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(OWNER_URL) as owner:
        pid = owner.execute(
            "INSERT INTO connections (tenant_id, name, kind, config, allowed_objects) "
            "VALUES (NULL, %s, 'bigquery', '{}', '{}') RETURNING id", (name,),
        ).fetchone()[0]
    try:
        with psycopg.connect(APP_URL) as conn:
            with conn.transaction():
                assert conn.execute("SELECT count(*) FROM connections WHERE id = %s", (pid,)).fetchone()[0] == 0
            with conn.transaction():
                _as_tenant(conn, a)
                assert conn.execute("SELECT name FROM connections WHERE id = %s", (pid,)).fetchone()[0] == name
                assert conn.execute("UPDATE connections SET name = 'hijacked' WHERE id = %s", (pid,)).rowcount == 0
                assert conn.execute("DELETE FROM connections WHERE id = %s", (pid,)).rowcount == 0
            with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                _as_tenant(conn, a)
                conn.execute(
                    "INSERT INTO connections (tenant_id, name, kind, config, allowed_objects) "
                    "VALUES (NULL, 'rogue', 'bigquery', '{}', '{}')"
                )
    finally:
        with psycopg.connect(OWNER_URL) as owner:
            owner.execute("DELETE FROM connections WHERE id = %s", (pid,))
