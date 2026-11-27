"""Metadata storage isolation against PostgreSQL using the application role."""

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from odn.db.migrate import migrate
from odn.platform.catalog import (
    metadata_dispatch,
    metadata_queue,
    metadata_refresh,
    metadata_search,
    metadata_snapshot,
    metadata_store,
)
from odn.platform.catalog.metadata_store import (
    MetadataContainer,
    MetadataField,
    MetadataTable,
    RefreshLeaseLost,
)
from odn.platform.connectors.base import CatalogNode, ColumnSchema, ConnectorError
from odn.platform.policy.sql_policy import SqlPolicy

OWNER_URL = os.environ.get("ODN_TEST_OWNER_URL", "postgresql://odn:odn_local_dev@localhost:5532/odn")
APP_URL = os.environ.get("ODN_TEST_APP_URL", "postgresql://odn_app:odn_app_local_dev@localhost:5532/odn")


def _reachable() -> bool:
    try:
        psycopg.connect(OWNER_URL, connect_timeout=2).close()
    except psycopg.OperationalError:
        return False
    return True


pytestmark = pytest.mark.skipif(not _reachable(), reason="local Postgres not running")


def _context(conn, tenant, user=None):
    conn.execute("SELECT set_config('odn.tenant_id', %s, true)", (str(tenant),))
    if user is not None:
        conn.execute("SELECT set_config('odn.user_id', %s, true)", (str(user),))


@pytest.fixture
def catalog():
    migrate(OWNER_URL)
    tenant_a, tenant_b, user_a, user_b = (uuid.uuid4() for _ in range(4))
    shared, personal, other, platform, platform_empty = (uuid.uuid4() for _ in range(5))
    with psycopg.connect(OWNER_URL) as conn:
        for tenant in (tenant_a, tenant_b):
            conn.execute("INSERT INTO tenants (id, slug, name) VALUES (%s, %s, 'test')",
                         (tenant, f"meta-{tenant.hex[:12]}"))
        for user, tenant in ((user_a, tenant_a), (user_b, tenant_a)):
            conn.execute("INSERT INTO users (id, tenant_id, subject) VALUES (%s, %s, %s)",
                         (user, tenant, str(user)))
        for cid, tenant, owner in (
            (shared, tenant_a, None), (personal, tenant_a, user_a), (other, tenant_b, None),
            (platform, None, None), (platform_empty, None, None),
        ):
            conn.execute(
                "INSERT INTO connections (id, tenant_id, owner_user_id, name, kind, config, allowed_objects) "
                "VALUES (%s, %s, %s, %s, 'bigquery', %s, '{p.d.*}')",
                (cid, tenant, owner, f"meta-{cid.hex[:12]}", Jsonb({"project": "p"})),
            )
        for cid in (shared, personal, other, platform):
            conn.execute(
                "INSERT INTO metadata_catalogs (connection_id, connection_revision) "
                "SELECT id, metadata_revision FROM connections WHERE id = %s", (cid,),
            )
        conn.execute(
            "INSERT INTO metadata_containers (connection_id, path, name, kind, observed_at) "
            "VALUES (%s, ARRAY['p','d'], 'd', 'dataset', now())", (personal,),
        )
        conn.execute(
            "INSERT INTO metadata_tables (connection_id, path, container_path, name, kind, observed_at) "
            "VALUES (%s, ARRAY['p','d','Orders'], ARRAY['p','d'], 'Orders', 'table', now())", (personal,),
        )
        conn.execute(
            "INSERT INTO metadata_fields (connection_id, table_path, path, ordinal, data_type) "
            "VALUES (%s, ARRAY['p','d','Orders'], ARRAY['customer','id'], 0, 'STRING')", (personal,),
        )
        conn.execute(
            "INSERT INTO metadata_refresh_jobs (connection_id, connection_revision, requested_by, status) "
            "SELECT id, metadata_revision, %s, 'queued' FROM connections WHERE id = %s", (user_a, personal),
        )
    yield tenant_a, tenant_b, user_a, user_b, shared, personal, other, platform, platform_empty
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM connections WHERE id = ANY(%s)", ([platform, platform_empty],))
        conn.execute("DELETE FROM tenants WHERE id = ANY(%s)", ([tenant_a, tenant_b],))


def test_metadata_inherits_connection_visibility_and_platform_is_read_only(catalog):
    tenant_a, tenant_b, user_a, user_b, shared, personal, other, platform, platform_empty = catalog
    with psycopg.connect(APP_URL) as conn:
        with conn.transaction():
            assert conn.execute("SELECT count(*) FROM metadata_catalogs").fetchone()[0] == 0
        with conn.transaction():
            _context(conn, tenant_a, user_a)
            ids = {row[0] for row in conn.execute("SELECT connection_id FROM metadata_catalogs")}
            assert ids == {shared, personal, platform}
            for table in ("metadata_containers", "metadata_tables", "metadata_fields", "metadata_refresh_jobs"):
                assert conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0] == 1
            assert conn.execute("UPDATE metadata_catalogs SET last_success_at = now() WHERE connection_id = %s",
                                (platform,)).rowcount == 0
            assert conn.execute("DELETE FROM metadata_catalogs WHERE connection_id = %s", (platform,)).rowcount == 0
        with conn.transaction():
            _context(conn, tenant_a, user_b)
            ids = {row[0] for row in conn.execute("SELECT connection_id FROM metadata_catalogs")}
            assert ids == {shared, platform}
            for table in ("metadata_containers", "metadata_tables", "metadata_fields", "metadata_refresh_jobs"):
                assert conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0] == 0
        with conn.transaction():
            _context(conn, tenant_a)
            ids = {row[0] for row in conn.execute("SELECT connection_id FROM metadata_catalogs")}
            assert ids == {shared, platform}
        with conn.transaction():
            _context(conn, tenant_b)
            ids = {row[0] for row in conn.execute("SELECT connection_id FROM metadata_catalogs")}
            assert ids == {other, platform}
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            _context(conn, tenant_a, user_b)
            conn.execute("INSERT INTO metadata_catalogs VALUES (%s, %s, NULL, now())", (platform_empty, uuid.uuid4()))
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            _context(conn, tenant_a, user_a)
            conn.execute("INSERT INTO metadata_containers VALUES (%s, ARRAY['p','x'], 'x', 'dataset', NULL, now())",
                         (platform,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            _context(conn, tenant_b)
            conn.execute("INSERT INTO metadata_containers VALUES (%s, ARRAY['p','x'], 'x', 'dataset', NULL, now())",
                         (personal,))


def test_profile_platform_budget_is_shared_atomic_and_not_directly_mutable(catalog):
    tenant_a, tenant_b, user_a, _, _, _, _, platform, _ = catalog
    user_b = uuid.uuid4()
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("INSERT INTO users (id, tenant_id, subject) VALUES (%s, %s, %s)",
                     (user_b, tenant_b, str(user_b)))
        for i in range(42):
            tenant, user = (tenant_a, user_a) if i % 2 == 0 else (tenant_b, user_b)
            conn.execute(
                "INSERT INTO metadata_profile_jobs "
                "(tenant_id, connection_id, connection_revision, requested_by, table_path, status) "
                "SELECT %s, id, metadata_revision, %s, ARRAY['p','d',%s], 'queued' "
                "FROM connections WHERE id = %s",
                (tenant, user, f"t{i}", platform),
            )
    claims = []
    with psycopg.connect(APP_URL) as conn:
        for _ in range(42):
            claims.append(conn.execute("SELECT * FROM odn_claim_profile_job()").fetchone())
        conn.commit()
        job, job_tenant, _, job_user, token = claims[0]
        outsider_tenant, outsider_user = (tenant_b, user_b) if job_tenant == tenant_a else (tenant_a, user_a)
        with conn.transaction():
            _context(conn, outsider_tenant, outsider_user)
            assert not conn.execute(
                "SELECT odn_reserve_profile_budget(%s, %s, %s, %s)",
                (job, token, 1, f"odn_profile_{uuid.uuid4().hex}"),
            ).fetchone()[0]
        with conn.transaction():
            _context(conn, job_tenant, job_user)
            assert not conn.execute(
                "SELECT odn_reserve_profile_budget(%s, %s, %s, %s)",
                (job, token, 50_000_000_001, f"odn_profile_{uuid.uuid4().hex}"),
            ).fetchone()[0]
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute("UPDATE metadata_profile_budget SET reserved_bytes = 0")

    def reserve(claim):
        job, tenant, _, user, token = claim
        with psycopg.connect(APP_URL) as conn:
            _context(conn, tenant, user)
            return conn.execute("SELECT odn_reserve_profile_budget(%s, %s, %s, %s)",
                                (job, token, 50_000_000_000, f"odn_profile_{uuid.uuid4().hex}")).fetchone()[0]

    with ThreadPoolExecutor(max_workers=12) as executor:
        accepted = list(executor.map(reserve, claims))
    assert sum(accepted) == 40
    with psycopg.connect(OWNER_URL) as conn:
        assert conn.execute(
            "SELECT reserved_bytes FROM metadata_profile_budget WHERE connection_id = %s", (platform,)
        ).fetchone()[0] == 2_000_000_000_000


def test_profile_job_publishes_preserves_failure_and_coalesces(catalog, monkeypatch):
    from google.cloud.bigquery import SchemaField

    from odn import db
    from odn.config import settings
    from odn.platform.catalog import column_profiles
    from odn.platform.connections import service as connections
    from odn.platform.connectors.base import CostEstimate
    from odn.platform.connectors.bigquery import BigQueryConnector
    from odn.tenancy.context import RequestContext

    tenant, other_tenant, user, _, _, _, _, platform, _ = catalog
    ctx = RequestContext(tenant_id=tenant, user_id=user, subject="test", role="member")
    profile = SimpleNamespace(id=platform, kind="bigquery", allowed_objects=["p.d.*"], secret_ref=None)

    class FakeBigQuery(BigQueryConnector):
        project = "p"
        default_dataset = "d"

        def __init__(self):
            pass

        def profile_schema(self, path):
            return [SchemaField("amount", "INTEGER")]

        def estimate_cost(self, sql):
            return CostEstimate(bytes_processed=1000)

        def profile_aggregate(self, sql, max_bytes_billed, job_id):
            assert max_bytes_billed == 10 * 1024**2
            assert job_id.startswith("odn_profile_")
            return {"sample_rows": 4, "filled_0": 3, "min_0": "2", "max_0": "20"}

    db.pool.cache_clear()
    monkeypatch.setattr(settings, "database_url", APP_URL)
    monkeypatch.setattr(connections, "get_connection", lambda *args: profile)
    fingerprint = "a" * 64
    monkeypatch.setattr(column_profiles, "_profile_connector", lambda _: (FakeBigQuery(), fingerprint))
    monkeypatch.setattr(column_profiles, "resolve_profile_credentials", lambda _: (None, fingerprint))
    path = ("p", "d", "Orders")
    try:
        assert column_profiles.request_profile(ctx, profile, path)
        assert not column_profiles.request_profile(ctx, profile, path)
        with db.system_transaction() as conn:
            claim = conn.execute("SELECT * FROM odn_claim_profile_job()").fetchone()
        column_profiles._run(tenant, user, platform, claim["id"], claim["token"])
        result = column_profiles.get_profile(ctx, profile, path)
        assert result.job_status == "succeeded"
        assert result.sample_rows == 4
        assert result.fields[0].filled_rows == 3
        assert (result.fields[0].min_value, result.fields[0].max_value) == ("2", "20")
        assert not column_profiles.request_profile(ctx, profile, path, automatic=True)
        assert column_profiles.request_profile(ctx, profile, path)
        with db.system_transaction() as conn:
            claim = conn.execute("SELECT * FROM odn_claim_profile_job()").fetchone()
        column_profiles._finish(tenant, user, claim["id"], claim["token"], "failed", "profile_failed")
        result = column_profiles.get_profile(ctx, profile, path)
        assert result.job_status == "failed"
        assert result.error_code == "profile_failed"
        assert result.fields[0].filled_rows == 3
        fingerprint = "b" * 64
        result = column_profiles.get_profile(ctx, profile, path)
        assert result.credential_changed and result.sampled_at is None and not result.fields
        fingerprint = "a" * 64
        other_ctx = RequestContext(tenant_id=other_tenant, user_id=uuid.uuid4(), subject="test", role="member")
        assert column_profiles.get_profile(other_ctx, profile, path).fields == []
        monkeypatch.setattr(FakeBigQuery, "profile_schema", lambda *_: [SchemaField("amount", "STRING")])
        assert column_profiles.get_profile(ctx, profile, path).fields == []
        with psycopg.connect(OWNER_URL) as conn:
            conn.execute("UPDATE connections SET allowed_objects = ARRAY['p.d.Orders'] WHERE id = %s", (platform,))
        assert column_profiles.get_profile(ctx, profile, path).sampled_at is None
    finally:
        db.pool().close()
        db.pool.cache_clear()


def test_profile_api_requires_opt_in_and_enforces_allowlist(catalog, monkeypatch):
    from fastapi.testclient import TestClient

    from odn import db
    from odn.api import routes
    from odn.api.deps import request_context
    from odn.api.main import app
    from odn.config import settings
    from odn.tenancy.context import RequestContext

    tenant, _, user, _, _, _, _, platform, _ = catalog
    ctx = RequestContext(tenant_id=tenant, user_id=user, subject="test", role="member")
    profile = SimpleNamespace(id=platform, kind="bigquery", allowed_objects=["p.d.*"])
    db.pool.cache_clear()
    monkeypatch.setattr(settings, "database_url", APP_URL)
    monkeypatch.setattr(routes, "_profile", lambda *_: profile)
    monkeypatch.setitem(app.dependency_overrides, request_context, lambda: ctx)
    client = TestClient(app)
    url = f"/v1/connections/{platform}/column-profile"
    try:
        monkeypatch.setattr(settings, "column_profiling_preview", False)
        assert client.get(url, params=[("path", p) for p in ("p", "d", "Orders")]).status_code == 404
        monkeypatch.setattr(settings, "column_profiling_preview", True)
        assert client.post(url, params=[("path", p) for p in ("p", "x", "Orders")]).status_code == 403
        queued = client.post(url, params=[("path", p) for p in ("p", "d", "Orders")])
        assert queued.status_code == 202
        assert queued.json()["job_status"] == "queued"
        result = client.get(url, params=[("path", p) for p in ("p", "d", "Orders")])
        assert result.status_code == 200 and result.headers["cache-control"] == "private, no-store"
        assert result.json()["sampled_at"] is None
    finally:
        client.close()
        db.pool().close()
        db.pool.cache_clear()


def test_connection_revision_changes_for_scope_or_credentials(catalog):
    tenant, _, user, _, _, personal, _, _, _ = catalog
    with psycopg.connect(APP_URL) as conn, conn.transaction():
        _context(conn, tenant, user)
        old = conn.execute("SELECT metadata_revision FROM connections WHERE id = %s", (personal,)).fetchone()[0]
        same = conn.execute(
            "UPDATE connections SET name = name || '-renamed', metadata_revision = %s "
            "WHERE id = %s RETURNING metadata_revision", (uuid.uuid4(), personal),
        ).fetchone()[0]
        assert same == old
        changed = conn.execute(
            "UPDATE connections SET allowed_objects = ARRAY['p.d.Orders'] WHERE id = %s RETURNING metadata_revision",
            (personal,),
        ).fetchone()[0]
        assert changed != old
        for table in ("metadata_containers", "metadata_tables", "metadata_fields"):
            assert conn.execute(sql.SQL("SELECT count(*) FROM {} WHERE connection_id = %s")
                                .format(sql.Identifier(table)), (personal,)).fetchone()[0] == 0
        changed_again = conn.execute(
            "UPDATE connections SET secret_ciphertext = %s WHERE id = %s RETURNING metadata_revision",
            (b"new encrypted value", personal),
        ).fetchone()[0]
        assert changed_again != changed


def test_active_jobs_are_single_flight_and_connection_deletion_cascades(catalog):
    tenant, _, user, _, _, personal, _, _, _ = catalog
    with psycopg.connect(APP_URL) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
            _context(conn, tenant, user)
            conn.execute(
                "INSERT INTO metadata_refresh_jobs (connection_id, connection_revision, status) "
                "SELECT id, metadata_revision, 'running' FROM connections WHERE id = %s", (personal,),
            )
        with conn.transaction():
            _context(conn, tenant, user)
            conn.execute("DELETE FROM connections WHERE id = %s", (personal,))
            for table in ("metadata_catalogs", "metadata_containers", "metadata_tables",
                          "metadata_fields", "metadata_refresh_jobs"):
                assert conn.execute(sql.SQL("SELECT count(*) FROM {} WHERE connection_id = %s")
                                    .format(sql.Identifier(table)), (personal,)).fetchone()[0] == 0


def test_platform_owner_can_write_without_bypassing_rls(catalog):
    tenant, _, user, _, _, _, _, _, platform = catalog
    role = f"odn_metadata_owner_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role)))
        conn.execute(sql.SQL("ALTER TABLE metadata_catalogs OWNER TO {}").format(sql.Identifier(role)))
        conn.execute(sql.SQL("GRANT SELECT ON connections TO {}").format(sql.Identifier(role)))
        _context(conn, tenant, user)
        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
        conn.execute(
            "INSERT INTO metadata_catalogs (connection_id, connection_revision) VALUES (%s, %s)",
            (platform, uuid.uuid4()),
        )
        conn.rollback()


@pytest.fixture
def search_transaction(monkeypatch):
    @contextmanager
    def transaction(tenant_id, user_id):
        with psycopg.connect(APP_URL, row_factory=psycopg.rows.dict_row) as conn, conn.transaction():
            _context(conn, tenant_id, user_id)
            yield conn

    monkeypatch.setattr(metadata_search, "tenant_transaction", transaction)


@pytest.fixture
def snapshot_transaction(monkeypatch):
    @contextmanager
    def transaction(tenant_id, user_id, *, repeatable_read=False):
        with psycopg.connect(APP_URL, row_factory=psycopg.rows.dict_row) as conn, conn.transaction():
            if repeatable_read:
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            _context(conn, tenant_id, user_id)
            yield conn

    monkeypatch.setattr(metadata_snapshot, "tenant_transaction", transaction)


def test_snapshot_authorization_fidelity_and_states(catalog, snapshot_transaction, monkeypatch):
    tenant, other_tenant, user, other_user, shared, personal, _, platform, empty = catalog
    with pytest.raises(metadata_snapshot.SnapshotUnavailable):
        metadata_snapshot.get_snapshot(tenant, user, personal)
    with pytest.raises(metadata_snapshot.SnapshotUnavailable):
        metadata_snapshot.get_snapshot(tenant, user, empty)
    with pytest.raises(metadata_search.MetadataConnectionNotFound):
        metadata_snapshot.get_snapshot(tenant, other_user, personal)
    with pytest.raises(metadata_search.MetadataConnectionNotFound):
        metadata_snapshot.get_snapshot(other_tenant, user, personal)

    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE connections SET allowed_objects = ARRAY['p.d.Orders'] WHERE id = %s", (personal,))
        conn.execute(
            "UPDATE metadata_catalogs SET connection_revision = (SELECT metadata_revision FROM connections WHERE id = %s), "
            "last_success_at = now() WHERE connection_id = %s", (personal, personal),
        )
        conn.execute(
            "UPDATE metadata_refresh_jobs SET status = 'succeeded', finished_at = now(), "
            "connection_revision = (SELECT metadata_revision FROM connections WHERE id = %s) WHERE connection_id = %s",
            (personal, personal),
        )
        conn.execute("UPDATE metadata_fields SET description = 'source description' WHERE connection_id = %s", (personal,))
        conn.execute(
            "INSERT INTO metadata_tables (connection_id, path, container_path, name, kind, observed_at) "
            "VALUES (%s, ARRAY['p','d','Secret'], ARRAY['p','d'], 'Secret', 'table', now())", (personal,),
        )
        conn.execute(
            "INSERT INTO metadata_fields (connection_id, table_path, path, ordinal, data_type, description) "
            "VALUES (%s, ARRAY['p','d','Secret'], ARRAY['secret'], 0, 'STRING', 'private')", (personal,),
        )
        conn.execute(
            "INSERT INTO data_definitions (tenant_id, connection_id, table_path, field_path, definition) "
            "VALUES (%s, %s, 'p.d.Orders', 'customer.id', 'curated definition'), "
            "(%s, %s, 'p.d.Secret', 'secret', 'hidden definition')", (tenant, personal, tenant, personal),
        )
        for cid in (shared, platform):
            conn.execute("UPDATE metadata_catalogs SET last_success_at = now() WHERE connection_id = %s", (cid,))

    snapshot = metadata_snapshot.get_snapshot(tenant, user, personal)
    assert snapshot["status"].state == "ready"
    assert [table["name"] for table in snapshot["datasets"][0]["tables"]] == ["Orders"]
    assert snapshot["datasets"][0]["tables"][0]["columns"] == [{
        "name": "customer.id", "type": "STRING", "description": "curated definition — source description",
    }]
    assert metadata_snapshot.get_snapshot(tenant, other_user, shared)["status"].state == "ready"
    assert metadata_snapshot.get_snapshot(tenant, other_user, platform)["status"].state == "ready"
    with monkeypatch.context() as patch:
        patch.setattr(metadata_snapshot, "_MAX_FIELDS", 0)
        with pytest.raises(metadata_snapshot.SnapshotTooLarge):
            metadata_snapshot.get_snapshot(tenant, user, personal)

    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE metadata_refresh_jobs SET status = 'partial', error_code = 'source_error' "
                     "WHERE connection_id = %s", (personal,))
    assert metadata_snapshot.get_snapshot(tenant, user, personal)["status"].state == "partial"
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE metadata_refresh_jobs SET status = 'failed' WHERE connection_id = %s", (personal,))
    assert metadata_snapshot.get_snapshot(tenant, user, personal)["status"].state == "stale"
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE connections SET allowed_objects = ARRAY['p.d.*'] WHERE id = %s", (personal,))
    with pytest.raises(metadata_snapshot.SnapshotUnavailable):
        metadata_snapshot.get_snapshot(tenant, user, personal)


def test_snapshot_api_flag_errors_and_private_cache(catalog, snapshot_transaction, monkeypatch):
    from fastapi.testclient import TestClient

    from odn.api.deps import request_context
    from odn.api.main import app
    from odn.config import settings
    from odn.tenancy.context import RequestContext

    tenant, _, user, other_user, _, personal, _, _, _ = catalog
    ctx = RequestContext(tenant_id=tenant, user_id=user, subject="test", role="member")
    app.dependency_overrides[request_context] = lambda: ctx
    client = TestClient(app)
    url = f"/v1/connections/{personal}/metadata-index"
    try:
        monkeypatch.setattr(settings, "metadata_search_preview", False)
        assert client.get(url).status_code == 404
        monkeypatch.setattr(settings, "metadata_search_preview", True)
        assert client.get(url).status_code == 409
        with psycopg.connect(OWNER_URL) as conn:
            conn.execute("UPDATE metadata_refresh_jobs SET status = 'succeeded' WHERE connection_id = %s", (personal,))
            conn.execute("UPDATE metadata_catalogs SET last_success_at = now() WHERE connection_id = %s", (personal,))
        response = client.get(url)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json()["datasets"][0]["tables"][0]["columns"][0]["name"] == "customer.id"
        monkeypatch.setattr(metadata_snapshot, "_MAX_FIELDS", 0)
        assert client.get(url).status_code == 413
        ctx = RequestContext(tenant_id=tenant, user_id=other_user, subject="test", role="member")
        assert client.get(url).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_snapshot_is_consistent_when_catalog_changes_during_download(catalog, snapshot_transaction, monkeypatch):
    tenant, _, user, _, _, personal, _, _, _ = catalog
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE metadata_refresh_jobs SET status = 'succeeded' WHERE connection_id = %s", (personal,))
        conn.execute("UPDATE metadata_catalogs SET last_success_at = now() WHERE connection_id = %s", (personal,))
        conn.execute("UPDATE metadata_fields SET description = 'before' WHERE connection_id = %s", (personal,))

    read_status = metadata_snapshot._read_status

    def publish_after_status(cur, connection_id):
        result = read_status(cur, connection_id)
        with psycopg.connect(OWNER_URL) as conn:
            conn.execute("UPDATE metadata_fields SET description = 'after' WHERE connection_id = %s", (personal,))
            conn.execute("UPDATE metadata_refresh_jobs SET status = 'partial' WHERE connection_id = %s", (personal,))
        return result

    monkeypatch.setattr(metadata_snapshot, "_read_status", publish_after_status)
    snapshot = metadata_snapshot.get_snapshot(tenant, user, personal)
    assert snapshot["status"].state == "ready"
    assert snapshot["datasets"][0]["tables"][0]["columns"][0]["description"] == "before"


def test_search_pagination_snapshots_fields_and_revision(catalog, search_transaction):
    tenant, _, user, other_user, _, personal, _, _, _ = catalog
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute(
            "INSERT INTO metadata_tables (connection_id, path, container_path, name, kind, observed_at) "
            "VALUES (%s, ARRAY['p','d', %s], ARRAY['p','d'], 'Orders_20261022', 'table', now())",
            (personal, "Orders_20261022"),
        )
        conn.execute(
            "UPDATE metadata_fields SET description = 'customer billing identifier' WHERE connection_id = %s",
            (personal,),
        )
    page = metadata_search.search(tenant, user, personal, "d customer billing", limit=1)
    assert [item.name for item in page.items] == ["Orders"]
    assert page.items[0].matching_fields[0].path == ("customer", "id")
    assert page.status.state == "building"
    assert page.next_cursor is None

    first = metadata_search.search(tenant, user, personal, "orders", limit=1, include_snapshots=True)
    assert first.next_cursor
    second = metadata_search.search(
        tenant, user, personal, "orders", limit=1, include_snapshots=True, cursor=first.next_cursor,
    )
    assert [first.items[0].name, second.items[0].name] == ["Orders", "Orders_20261022"]
    assert second.next_cursor is None
    with pytest.raises(metadata_search.InvalidCursor):
        metadata_search.search(tenant, user, personal, "different", cursor=first.next_cursor)
    with pytest.raises(metadata_search.MetadataConnectionNotFound):
        metadata_search.search(tenant, other_user, personal, "orders")
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE connections SET allowed_objects = ARRAY['p.d.Orders'] WHERE id = %s", (personal,))
    with pytest.raises(metadata_search.InvalidCursor):
        metadata_search.search(tenant, user, personal, "orders", include_snapshots=True, cursor=first.next_cursor)
    after_revision = metadata_search.search(tenant, user, personal, "orders")
    assert after_revision.items == []
    assert after_revision.status.state == "not_indexed"
    assert after_revision.status.job_status is None
    assert after_revision.status.last_completed_status is None


def test_search_reports_stale_and_fails_closed_on_filter_disagreement(catalog, search_transaction, monkeypatch):
    tenant, _, user, _, _, personal, _, _, _ = catalog
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE metadata_catalogs SET last_success_at = now() WHERE connection_id = %s", (personal,))
        conn.execute("UPDATE metadata_refresh_jobs SET status = 'failed', error_code = 'source_error' "
                     "WHERE connection_id = %s", (personal,))
    result = metadata_search.search(tenant, user, personal, "orders")
    assert result.status.state == "stale"
    assert result.status.error_code == "source_error"
    assert result.items[0].name == "Orders"
    monkeypatch.setattr(SqlPolicy, "allows_object", lambda self, path: False)
    with pytest.raises(metadata_search.MetadataAuthorizationMismatch):
        metadata_search.search(tenant, user, personal, "orders")


def test_search_paginates_without_duplicates_or_empty_final_page(catalog, search_transaction):
    tenant, _, user, _, _, personal, _, _, _ = catalog
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute(
            "INSERT INTO metadata_tables (connection_id, path, container_path, name, kind, observed_at) "
            "SELECT %s, ARRAY['p','d','result_' || n], ARRAY['p','d'], 'result_' || n, 'table', now() "
            "FROM generate_series(1, 40) n",
            (personal,),
        )
    first = metadata_search.search(tenant, user, personal, "result", limit=20)
    assert len(first.items) == 20 and first.next_cursor
    second = metadata_search.search(tenant, user, personal, "result", limit=20, cursor=first.next_cursor)
    assert len(second.items) == 20 and second.next_cursor is None
    assert len({item.path for item in first.items + second.items}) == 40


def test_search_uses_current_user_preferences_and_non_deprecated_definitions(catalog, search_transaction):
    tenant, _, user, _, _, personal, _, _, _ = catalog
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute(
            "INSERT INTO metadata_tables (connection_id, path, container_path, name, kind, observed_at) "
            "VALUES (%s, ARRAY['p','d','Invoices'], ARRAY['p','d'], 'Invoices', 'table', now())", (personal,),
        )
        conn.execute(
            "INSERT INTO user_favorites (tenant_id, user_id, connection_id, object_path, kind) "
            "VALUES (%s, %s, %s, 'p.d.orders', 'table')", (tenant, user, personal),
        )
        conn.execute(
            "INSERT INTO data_definitions (tenant_id, connection_id, table_path, field_path, definition, status) "
            "VALUES (%s, %s, 'p.d.Orders', 'customer.id', 'billing account code', 'draft')", (tenant, personal),
        )
    page = metadata_search.search(tenant, user, personal, "d", limit=1)
    assert page.items[0].name == "Orders"
    assert page.next_cursor
    assert metadata_search.search(tenant, user, personal, "d", limit=1, cursor=page.next_cursor).items[0].name == "Invoices"
    defined = metadata_search.search(tenant, user, personal, "billing")
    assert defined.items[0].matching_fields[0].description == "billing account code"
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE data_definitions SET status = 'deprecated' WHERE connection_id = %s", (personal,))
    assert metadata_search.search(tenant, user, personal, "billing").items == []


def test_search_orders_matching_fields_by_relevance_and_ignores_table_only_descriptions(catalog, search_transaction):
    tenant, _, user, _, _, personal, _, _, _ = catalog
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute(
            "INSERT INTO metadata_fields (connection_id, table_path, path, ordinal, data_type, description) "
            "VALUES (%s, ARRAY['p','d','Orders'], ARRAY['note'], 1, 'STRING', 'account details'), "
            "(%s, ARRAY['p','d','Orders'], ARRAY['account_number'], 2, 'STRING', NULL), "
            "(%s, ARRAY['p','d','Orders'], ARRAY['other'], 3, 'STRING', 'orders information')",
            (personal, personal, personal),
        )
    page = metadata_search.search(tenant, user, personal, "account")
    assert [field.path for field in page.items[0].matching_fields] == [("account_number",), ("note",)]
    assert metadata_search.search(tenant, user, personal, "orders").items[0].matching_fields == []


def test_search_fetches_fields_for_the_correct_paged_table(catalog, search_transaction):
    tenant, _, user, _, _, personal, _, _, _ = catalog
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute(
            "INSERT INTO metadata_tables (connection_id, path, container_path, name, kind, observed_at) "
            "VALUES (%s, ARRAY['p','d','Invoices'], ARRAY['p','d'], 'Invoices', 'table', now())", (personal,),
        )
        conn.execute(
            "INSERT INTO metadata_fields (connection_id, table_path, path, ordinal, data_type, description) "
            "VALUES (%s, ARRAY['p','d','Invoices'], ARRAY['billing'], 0, 'STRING', 'customer details')",
            (personal,),
        )
    first = metadata_search.search(tenant, user, personal, "customer", limit=1)
    assert first.next_cursor
    second = metadata_search.search(tenant, user, personal, "customer", limit=1, cursor=first.next_cursor)
    fields = {item.name: [field.path for field in item.matching_fields] for item in first.items + second.items}
    assert fields == {"Invoices": [("billing",)], "Orders": [("customer", "id")]}


def test_field_path_key_preserves_identifier_boundaries(catalog):
    with psycopg.connect(APP_URL) as conn:
        keys = conn.execute(
            "SELECT odn_metadata_path_key(ARRAY['a.b', 'c']), "
            "odn_metadata_path_key(ARRAY['a', 'b.c']), "
            "odn_metadata_path_key(ARRAY['a:b', 'c'])",
        ).fetchone()
    assert len(set(keys)) == 3


@pytest.mark.parametrize(
    ("kind", "container", "names", "patterns"),
    [
        ("bigquery", ("p", "d"), ("Orders", "secret"), ("P.D.orders",)),
        ("postgres", ("sales",), ("Orders", "secret"), ("SALES.orders",)),
        ("mysql", ("sales",), ("Orders", "orders", "bad-name"), ("sales.*",)),
        ("oracle", ("SALES",), ("Orders", "orders", "DUAL", "bad-name"), ("SALES.*",)),
        ("snowflake", ("DB", "SALES"), ("Orders", "orders", "pg_catalog_x", "pgXcatalog", "bad-name"),
         ("DB.SALES.*",)),
        ("mysql", ("mysql",), ("Orders",), ("mysql.*",)),
        ("oracle", ("SYS",), ("Orders",), ("SYS.*",)),
    ],
)
def test_search_filters_like_current_policy(catalog, search_transaction, snapshot_transaction, kind, container, names, patterns):
    tenant, _, user, _, _, personal, _, _, _ = catalog
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM metadata_containers WHERE connection_id = %s", (personal,))
        revision = conn.execute(
            "UPDATE connections SET kind = %s, allowed_objects = %s WHERE id = %s RETURNING metadata_revision",
            (kind, list(patterns), personal),
        ).fetchone()[0]
        conn.execute("UPDATE metadata_catalogs SET connection_revision = %s WHERE connection_id = %s",
                     (revision, personal))
        conn.execute("UPDATE metadata_catalogs SET last_success_at = now() WHERE connection_id = %s", (personal,))
        conn.execute("UPDATE metadata_refresh_jobs SET status = 'succeeded', connection_revision = %s "
                     "WHERE connection_id = %s", (revision, personal))
        conn.execute(
            "INSERT INTO metadata_containers (connection_id, path, name, kind, observed_at) "
            "VALUES (%s, %s, %s, 'schema', now())", (personal, list(container), container[-1]),
        )
        for name in names:
            conn.execute(
                "INSERT INTO metadata_tables (connection_id, path, container_path, name, kind, observed_at) "
                "VALUES (%s, %s, %s, %s, 'table', now())",
                (personal, [*container, name], list(container), name),
            )
    policy = SqlPolicy(dialect=kind, allowed_objects=frozenset(patterns))
    expected = {(*container, name) for name in names if policy.allows_object((*container, name))}
    result = metadata_search.search(tenant, user, personal, container[-1], limit=1)
    actual = []
    while True:
        actual.extend(tuple(item.path) for item in result.items)
        if not result.next_cursor:
            break
        result = metadata_search.search(tenant, user, personal, container[-1], limit=1, cursor=result.next_cursor)
    assert set(actual) == expected
    snapshot = metadata_snapshot.get_snapshot(tenant, user, personal)
    snapshot_paths = {tuple(table["path"]) for dataset in snapshot["datasets"] for table in dataset["tables"]}
    assert snapshot_paths == expected
    assert bool(snapshot["datasets"]) == policy.allows_container(container)


def test_metadata_search_api_flag_and_validation(catalog, search_transaction, monkeypatch):
    from fastapi.testclient import TestClient

    from odn.api.deps import request_context
    from odn.api.main import app
    from odn.config import settings
    from odn.tenancy.context import RequestContext

    tenant, _, user, other_user, _, personal, _, _, _ = catalog
    ctx = RequestContext(tenant_id=tenant, user_id=user, subject="test", role="member")
    app.dependency_overrides[request_context] = lambda: ctx
    client = TestClient(app)
    url = f"/v1/connections/{personal}/metadata-search"
    status_url = f"/v1/connections/{personal}/metadata-status"
    try:
        monkeypatch.setattr(settings, "metadata_search_preview", False)
        assert client.get(url, params={"q": "orders"}).status_code == 404
        assert client.get(status_url).status_code == 404
        monkeypatch.setattr(settings, "metadata_search_preview", True)
        response = client.get(url, params={"q": "orders"})
        assert response.status_code == 200
        assert response.json()["items"][0]["name"] == "Orders"
        status = client.get(status_url)
        assert status.status_code == 200
        assert status.json()["state"] == "building"
        assert status.json()["progress_total"] is None
        assert status.json()["can_refresh"] is True
        assert client.get(url, params={"q": " ", "cursor": "broken"}).status_code == 400
        assert client.get(url, params={"q": "orders", "limit": 101}).status_code == 422
        monkeypatch.setattr(SqlPolicy, "allows_object", lambda self, path: False)
        assert client.get(url, params={"q": "orders"}).status_code == 503
        ctx = RequestContext(tenant_id=tenant, user_id=other_user, subject="test", role="member")
        assert client.get(url, params={"q": "orders"}).status_code == 404
        assert client.get(status_url).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_preview_refresh_runs_and_is_bounded(catalog, queue_transactions, search_transaction, monkeypatch):
    from fastapi.testclient import TestClient

    from odn.api import routes
    from odn.api.deps import request_context
    from odn.api.main import app
    from odn.config import settings
    from odn.tenancy.context import RequestContext

    tenant, _, owner, member, shared, personal, _, platform, _ = catalog
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()

    class Connector:
        dialect = "bigquery"

        def list_catalog_complete(self, parent=()):
            if not parent:
                entered.set()
                assert release.wait(5)
                return [CatalogNode(kind="dataset", path=("p", "d"), name="d")]
            return [CatalogNode(kind="table", path=("p", "d", "new"), name="new")]

        def list_columns(self, container):
            return {(*container, "new"): [ColumnSchema(name="id", type="STRING")]}

    monkeypatch.setattr(settings, "metadata_search_preview", True)
    monkeypatch.setattr(metadata_dispatch, "tenant_transaction", metadata_queue.tenant_transaction)
    monkeypatch.setattr(metadata_store, "tenant_transaction", metadata_queue.tenant_transaction)
    monkeypatch.setattr(metadata_refresh, "get_connection", lambda *args: SimpleNamespace(secret_ref=None))
    monkeypatch.setattr(metadata_refresh, "build_connector", lambda profile: Connector())
    monkeypatch.setattr(metadata_refresh, "build_policy", lambda *args: SqlPolicy("bigquery", frozenset({"p.d.*"})))
    monkeypatch.setattr(routes, "_profile", lambda *args: object())
    original_run = metadata_refresh.run_job

    def run(*args):
        try:
            return original_run(*args)
        finally:
            completed.set()

    monkeypatch.setattr(metadata_refresh, "run_job", run)
    ctx = RequestContext(tenant_id=tenant, user_id=owner, subject="test", role="member")
    app.dependency_overrides[request_context] = lambda: ctx
    client = TestClient(app)
    try:
        response = client.post(f"/v1/connections/{personal}/metadata-refresh")
        assert response.status_code == 202
        assert response.json()["created"] is False
        assert entered.wait(5)
        busy = client.post(f"/v1/connections/{personal}/metadata-refresh")
        assert busy.status_code == 503
        assert busy.headers["retry-after"] == "10"
        release.set()
        assert completed.wait(5)
        assert metadata_dispatch._slot.acquire(timeout=5)
        metadata_dispatch._slot.release()
        with psycopg.connect(OWNER_URL) as conn:
            assert conn.execute("SELECT status FROM metadata_refresh_jobs WHERE id = %s",
                                (response.json()["job_id"],)).fetchone()[0] == "succeeded"
        assert client.get(f"/v1/connections/{personal}/metadata-search", params={"q": "new"}).json()["items"][0][
            "name"
        ] == "new"
        ctx = RequestContext(tenant_id=tenant, user_id=member, subject="test", role="member")
        assert client.post(f"/v1/connections/{shared}/metadata-refresh").status_code == 403
        assert client.post(f"/v1/connections/{platform}/metadata-refresh").status_code == 403
    finally:
        release.set()
        app.dependency_overrides.clear()


def test_publishing_is_atomic_and_requires_current_unexpired_lease(catalog, monkeypatch):
    tenant, _, user, _, _, personal, _, _, _ = catalog
    token = uuid.uuid4()
    with psycopg.connect(OWNER_URL) as conn:
        job = conn.execute(
            "UPDATE metadata_refresh_jobs SET status = 'running', lease_token = %s, "
            "lease_until = now() + interval '1 minute' WHERE connection_id = %s RETURNING id",
            (token, personal),
        ).fetchone()[0]

    @contextmanager
    def transaction(tenant_id, user_id):
        with psycopg.connect(APP_URL, row_factory=psycopg.rows.dict_row) as conn, conn.transaction():
            _context(conn, tenant_id, user_id)
            yield conn

    monkeypatch.setattr(metadata_store, "tenant_transaction", transaction)
    container = MetadataContainer(
        path=("p", "d"), name="d", kind="dataset",
        tables=(MetadataTable(
            path=("p", "d", "Orders"), name="Orders", kind="table",
            fields=(MetadataField(path=("customer", "id"), data_type="STRING"),),
        ),),
    )
    metadata_store.publish_container(tenant, user, personal, job, token, container)
    invalid = MetadataContainer(
        path=("p", "d"), name="d", kind="dataset",
        tables=(MetadataTable(
            path=("p", "d", "Orders"), name="Orders", kind="table",
            fields=(MetadataField(path=("duplicate",), data_type="STRING"),
                    MetadataField(path=("duplicate",), data_type="STRING")),
        ),),
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        metadata_store.publish_container(tenant, user, personal, job, token, invalid)
    with psycopg.connect(APP_URL) as conn, conn.transaction():
        _context(conn, tenant, user)
        assert conn.execute("SELECT path FROM metadata_fields WHERE connection_id = %s", (personal,)).fetchall() == [
            (["customer", "id"],)
        ]
    with pytest.raises(RefreshLeaseLost):
        metadata_store.publish_container(tenant, user, personal, job, uuid.uuid4(), container)
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE metadata_refresh_jobs SET lease_until = now() - interval '1 second' WHERE id = %s",
                     (job,))
    with pytest.raises(RefreshLeaseLost):
        metadata_store.publish_container(tenant, user, personal, job, token, container)
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE metadata_refresh_jobs SET lease_until = now() + interval '1 minute' WHERE id = %s",
                     (job,))
        conn.execute("UPDATE connections SET allowed_objects = ARRAY['p.d.secret'] WHERE id = %s", (personal,))
    with pytest.raises(RefreshLeaseLost):
        metadata_store.publish_container(tenant, user, personal, job, token, container)
    with psycopg.connect(OWNER_URL) as conn:
        assert conn.execute("SELECT count(*) FROM metadata_fields WHERE connection_id = %s", (personal,)).fetchone()[0] == 1
        conn.execute(
            "UPDATE metadata_refresh_jobs SET connection_revision = "
            "(SELECT metadata_revision FROM connections WHERE id = %s) WHERE id = %s",
            (personal, job),
        )
    with pytest.raises(ValueError, match="outside the current allowlist"):
        metadata_store.publish_container(tenant, user, personal, job, token, container)
    allowed_container = MetadataContainer(
        path=("p", "d"), name="d", kind="dataset",
        tables=(MetadataTable(
            path=("p", "d", "secret"), name="secret", kind="table",
            fields=(MetadataField(path=("customer", "id"), data_type="STRING"),),
        ),),
    )
    metadata_store.publish_container(tenant, user, personal, job, token, allowed_container)
    with psycopg.connect(APP_URL) as conn, conn.transaction():
        _context(conn, tenant, user)
        assert conn.execute("SELECT path FROM metadata_fields WHERE connection_id = %s", (personal,)).fetchall() == [
            (["customer", "id"],)
        ]


@pytest.fixture
def queue_transactions(monkeypatch):
    @contextmanager
    def transaction(tenant_id, user_id):
        with psycopg.connect(APP_URL, row_factory=psycopg.rows.dict_row) as conn, conn.transaction():
            _context(conn, tenant_id, user_id)
            yield conn

    monkeypatch.setattr(metadata_queue, "tenant_transaction", transaction)


def test_queue_coalesces_concurrent_requests_and_recovers_expired_leases(catalog, queue_transactions):
    tenant, _, user, _, _, personal, _, _, _ = catalog
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM metadata_refresh_jobs WHERE connection_id = %s", (personal,))
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: metadata_queue.enqueue(tenant, user, personal), range(4)))
    assert len({job_id for job_id, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    job_id = results[0][0]
    first = metadata_queue.claim(tenant, user, personal, job_id)
    assert first is not None and first.attempt == 1
    assert metadata_queue.claim(tenant, user, personal, job_id) is None
    assert metadata_queue.heartbeat(tenant, first)
    assert metadata_queue.set_progress(tenant, first, 0, 3)
    assert metadata_queue.set_progress(tenant, first, 2, 3)
    assert not metadata_queue.set_progress(tenant, first, 1, 3)
    assert not metadata_queue.set_progress(tenant, first, 2, 4)
    with pytest.raises(ValueError):
        metadata_queue.set_progress(tenant, first, 4, 3)
    with psycopg.connect(OWNER_URL) as conn:
        first_started = conn.execute(
            "UPDATE metadata_refresh_jobs SET lease_until = now() - interval '1 second' WHERE id = %s "
            "RETURNING started_at", (job_id,),
        ).fetchone()[0]
    assert not metadata_queue.heartbeat(tenant, first)
    assert not metadata_queue.set_progress(tenant, first, 3, 3)
    second = metadata_queue.claim(tenant, user, personal, job_id)
    assert second is not None and second.attempt == 2 and second.token != first.token
    with psycopg.connect(OWNER_URL) as conn:
        started, done, total = conn.execute(
            "SELECT started_at, progress_done, progress_total FROM metadata_refresh_jobs WHERE id = %s", (job_id,),
        ).fetchone()
        assert started > first_started
        assert (done, total) == (0, None)
    assert not metadata_queue.heartbeat(tenant, first)
    assert not metadata_queue.set_progress(tenant, first, 3, 3)
    for expected in (3, None):
        with psycopg.connect(OWNER_URL) as conn:
            conn.execute("UPDATE metadata_refresh_jobs SET lease_until = now() - interval '1 second' WHERE id = %s",
                         (job_id,))
        claimed = metadata_queue.claim(tenant, user, personal, job_id)
        assert (claimed.attempt if claimed else None) == expected
    with psycopg.connect(OWNER_URL) as conn:
        assert conn.execute("SELECT status, error_code FROM metadata_refresh_jobs WHERE id = %s",
                            (job_id,)).fetchone() == ("failed", "attempts_exhausted")
    new_id, created = metadata_queue.enqueue(tenant, user, personal)
    assert created
    new_job = metadata_queue.claim(tenant, user, personal, new_id)
    assert new_job is not None
    assert not metadata_queue.fail(tenant, first, "source_error")
    assert metadata_queue.fail(tenant, new_job, "source_error")
    with psycopg.connect(OWNER_URL) as conn:
        assert conn.execute("SELECT status, error_code FROM metadata_refresh_jobs WHERE id = %s",
                            (new_id,)).fetchone() == ("failed", "source_error")
        assert conn.execute("SELECT count(*) FROM metadata_fields WHERE connection_id = %s",
                            (personal,)).fetchone()[0] == 1


def test_queue_rechecks_roles_revision_and_tenant_status(catalog, queue_transactions):
    tenant, _, owner, member, shared, personal, _, platform, _ = catalog
    with pytest.raises(metadata_queue.RefreshNotAuthorized):
        metadata_queue.enqueue(tenant, member, personal)
    with pytest.raises(metadata_queue.RefreshNotAuthorized):
        metadata_queue.enqueue(tenant, member, shared)
    with pytest.raises(metadata_queue.RefreshNotAuthorized):
        metadata_queue.enqueue(tenant, owner, platform)
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE users SET role = 'data_lead' WHERE id = %s", (owner,))
    job_id, created = metadata_queue.enqueue(tenant, owner, shared)
    assert created
    job = metadata_queue.claim(tenant, owner, shared, job_id)
    assert job is not None
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE users SET role = 'member' WHERE id = %s", (owner,))
        conn.execute("UPDATE users SET role = 'data_lead' WHERE id = %s", (member,))
    with pytest.raises(metadata_queue.RefreshNotAuthorized):
        metadata_queue.heartbeat(tenant, job)
    replacement, created = metadata_queue.enqueue(tenant, member, shared)
    assert created and replacement != job_id
    claimed = metadata_queue.claim(tenant, member, shared, replacement)
    assert claimed is not None
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE connections SET allowed_objects = ARRAY['p.d.secret'] WHERE id = %s", (shared,))
    assert not metadata_queue.heartbeat(tenant, claimed)
    next_id, created = metadata_queue.enqueue(tenant, member, shared)
    assert created and next_id != replacement
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE tenants SET status = 'suspended' WHERE id = %s", (tenant,))
    with pytest.raises(metadata_queue.RefreshNotAuthorized):
        metadata_queue.claim(tenant, member, shared, next_id)


def test_retry_must_republish_scopes_from_the_current_attempt(catalog, queue_transactions, monkeypatch):
    tenant, _, user, _, _, personal, _, _, _ = catalog
    monkeypatch.setattr(metadata_store, "tenant_transaction", metadata_queue.tenant_transaction)
    container = MetadataContainer(path=("p", "d"), name="d", kind="dataset", tables=())
    job_id, _ = metadata_queue.enqueue(tenant, user, personal)
    first = metadata_queue.claim(tenant, user, personal, job_id)
    assert first is not None
    metadata_store.publish_container(tenant, user, personal, job_id, first.token, container)
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE metadata_refresh_jobs SET lease_until = now() - interval '1 second' WHERE id = %s",
                     (job_id,))
    second = metadata_queue.claim(tenant, user, personal, job_id)
    assert second is not None
    with pytest.raises(ValueError, match="unpublished"):
        metadata_store.finish_refresh(tenant, second, (container.path,), frozenset())
    metadata_store.publish_container(tenant, user, personal, job_id, second.token, container)
    metadata_store.finish_refresh(tenant, second, (container.path,), frozenset())


def test_publication_rechecks_actor_and_tenant_access(catalog, monkeypatch):
    tenant, _, owner, member, shared, _, _, _, _ = catalog
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE users SET role = 'data_lead' WHERE id = ANY(%s)", ([owner, member],))
        job_id, token = conn.execute(
            "INSERT INTO metadata_refresh_jobs (connection_id, connection_revision, requested_by, status, "
            "lease_token, lease_until) SELECT id, metadata_revision, %s, 'running', gen_random_uuid(), "
            "now() + interval '1 minute' FROM connections WHERE id = %s RETURNING id, lease_token",
            (owner, shared),
        ).fetchone()

    @contextmanager
    def transaction(tenant_id, user_id):
        with psycopg.connect(APP_URL, row_factory=psycopg.rows.dict_row) as conn, conn.transaction():
            _context(conn, tenant_id, user_id)
            yield conn

    monkeypatch.setattr(metadata_store, "tenant_transaction", transaction)
    container = MetadataContainer(path=("p", "d"), name="d", kind="dataset", tables=())
    with pytest.raises(RefreshLeaseLost):
        metadata_store.publish_container(tenant, member, shared, job_id, token, container)
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE users SET status = 'disabled' WHERE id = %s", (owner,))
    with pytest.raises(metadata_queue.RefreshNotAuthorized):
        metadata_store.publish_container(tenant, owner, shared, job_id, token, container)
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE users SET status = 'active' WHERE id = %s", (owner,))
        conn.execute("UPDATE tenants SET status = 'suspended' WHERE id = %s", (tenant,))
    with pytest.raises(metadata_queue.RefreshNotAuthorized):
        metadata_store.publish_container(tenant, owner, shared, job_id, token, container)


def test_finish_reconciles_complete_roots_and_preserves_failed_scope(catalog, queue_transactions, monkeypatch):
    tenant, _, user, _, _, personal, _, _, _ = catalog
    monkeypatch.setattr(metadata_store, "tenant_transaction", metadata_queue.tenant_transaction)
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE connections SET allowed_objects = ARRAY['p.*.*'] WHERE id = %s", (personal,))
        conn.execute(
            "UPDATE metadata_catalogs SET connection_revision = "
            "(SELECT metadata_revision FROM connections WHERE id = %s), last_success_at = now() - interval '1 day' "
            "WHERE connection_id = %s", (personal, personal),
        )
        conn.execute(
            "INSERT INTO metadata_containers (connection_id, path, name, kind, observed_at) "
            "VALUES (%s, ARRAY['p','removed'], 'removed', 'dataset', now())", (personal,),
        )
    job_id, created = metadata_queue.enqueue(tenant, user, personal)
    assert created
    job = metadata_queue.claim(tenant, user, personal, job_id)
    assert job is not None
    roots = (("p", "d"), ("p", "new"))
    with pytest.raises(ValueError, match="unpublished"):
        metadata_store.finish_refresh(tenant, job, roots, frozenset())
    with psycopg.connect(OWNER_URL) as conn:
        assert conn.execute("SELECT count(*) FROM metadata_containers WHERE connection_id = %s",
                            (personal,)).fetchone()[0] == 2
    metadata_store.publish_container(
        tenant, user, personal, job.id, job.token,
        MetadataContainer(path=("p", "new"), name="new", kind="dataset", tables=()),
    )
    metadata_store.finish_refresh(tenant, job, roots, frozenset({("p", "d")}))
    with psycopg.connect(OWNER_URL) as conn:
        paths = {tuple(row[0]) for row in conn.execute(
            "SELECT path FROM metadata_containers WHERE connection_id = %s", (personal,),
        )}
        assert paths == set(roots)
        assert conn.execute("SELECT count(*) FROM metadata_fields WHERE connection_id = %s",
                            (personal,)).fetchone()[0] == 1
        assert conn.execute("SELECT status, error_code, progress_done, progress_total "
                            "FROM metadata_refresh_jobs WHERE id = %s", (job.id,)).fetchone() == (
                                "partial", "incomplete", 2, 2
                            )
        assert conn.execute(
            "SELECT last_success_at < now() - interval '23 hours' FROM metadata_catalogs WHERE connection_id = %s",
            (personal,),
        ).fetchone()[0]
    with pytest.raises(RefreshLeaseLost):
        metadata_store.finish_refresh(tenant, job, roots, frozenset())
    next_id, _ = metadata_queue.enqueue(tenant, user, personal)
    next_job = metadata_queue.claim(tenant, user, personal, next_id)
    assert next_job is not None
    metadata_store.finish_refresh(tenant, next_job, (), frozenset())
    with psycopg.connect(OWNER_URL) as conn:
        assert conn.execute("SELECT count(*) FROM metadata_containers WHERE connection_id = %s",
                            (personal,)).fetchone()[0] == 0
        assert conn.execute("SELECT status FROM metadata_refresh_jobs WHERE id = %s",
                            (next_id,)).fetchone()[0] == "succeeded"


@pytest.mark.parametrize("failure,expected", [("none", "succeeded"), ("root", "failed"), ("scope", "partial")])
def test_refresh_runner_preserves_failed_scopes(catalog, queue_transactions, monkeypatch, failure, expected):
    tenant, _, user, _, _, personal, _, _, _ = catalog
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE connections SET allowed_objects = ARRAY['p.*.*'] WHERE id = %s", (personal,))
        conn.execute(
            "UPDATE metadata_catalogs SET connection_revision = "
            "(SELECT metadata_revision FROM connections WHERE id = %s) WHERE connection_id = %s",
            (personal, personal),
        )

    class Connector:
        dialect = "bigquery"

        def list_catalog_complete(self, parent=()):
            if not parent:
                if failure == "root":
                    raise ConnectorError("sensitive source details")
                return [CatalogNode(kind="dataset", path=("p", "d"), name="d")]
            return [CatalogNode(kind="table", path=("p", "d", "new"), name="new")]

        def list_columns(self, container):
            if failure == "scope":
                raise ConnectorError("sensitive source details")
            return {(*container, "new"): [ColumnSchema(name="id", type="STRING")]}

    connector = Connector()
    monkeypatch.setattr(metadata_store, "tenant_transaction", metadata_queue.tenant_transaction)
    monkeypatch.setattr(metadata_refresh, "get_connection", lambda *args: SimpleNamespace(secret_ref=None))
    monkeypatch.setattr(metadata_refresh, "build_connector", lambda profile: connector)
    monkeypatch.setattr(metadata_refresh, "build_policy", lambda profile, conn: SqlPolicy("bigquery", frozenset({"p.*.*"})))
    job_id, _ = metadata_queue.enqueue(tenant, user, personal)
    assert metadata_refresh.run_job(tenant, user, personal, job_id) == expected
    assert metadata_refresh.run_job(tenant, user, personal, job_id) == "not_claimed"
    with psycopg.connect(OWNER_URL) as conn:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM metadata_tables WHERE connection_id = %s", (personal,),
        )}
        assert names == ({"new"} if failure == "none" else {"Orders"})
        status, error_code = conn.execute(
            "SELECT status, error_code FROM metadata_refresh_jobs WHERE id = %s", (job_id,),
        ).fetchone()
        assert status == expected
        assert error_code == (None if failure == "none" else "source_error" if failure == "root" else "incomplete")


def test_refresh_runner_exposes_progress_and_previous_failure(catalog, queue_transactions, search_transaction, monkeypatch):
    tenant, _, user, other_user, _, personal, _, _, _ = catalog
    entered, release = threading.Event(), threading.Event()
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("UPDATE connections SET allowed_objects = ARRAY['p.*.*'] WHERE id = %s", (personal,))
        conn.execute(
            "UPDATE metadata_catalogs SET connection_revision = "
            "(SELECT metadata_revision FROM connections WHERE id = %s), last_success_at = now() "
            "WHERE connection_id = %s", (personal, personal),
        )
        conn.execute(
            "UPDATE metadata_refresh_jobs SET connection_revision = "
            "(SELECT metadata_revision FROM connections WHERE id = %s), status = 'failed', "
            "error_code = 'source_error', finished_at = now() WHERE connection_id = %s",
            (personal, personal),
        )

    class Connector:
        dialect = "bigquery"

        def list_catalog_complete(self, parent=()):
            if not parent:
                return [CatalogNode(kind="dataset", path=("p", name), name=name) for name in ("d", "e")]
            return [CatalogNode(kind="table", path=(*parent, "orders"), name="orders")]

        def list_columns(self, container):
            if container == ("p", "e"):
                entered.set()
                assert release.wait(5)
                raise ConnectorError("sensitive source details")
            return {(*container, "orders"): [ColumnSchema(name="id", type="STRING")]}

    monkeypatch.setattr(metadata_store, "tenant_transaction", metadata_queue.tenant_transaction)
    monkeypatch.setattr(metadata_refresh, "get_connection", lambda *args: SimpleNamespace(secret_ref=None))
    monkeypatch.setattr(metadata_refresh, "build_connector", lambda profile: Connector())
    monkeypatch.setattr(metadata_refresh, "build_policy", lambda *args: SqlPolicy("bigquery", frozenset({"p.*.*"})))
    job_id, _ = metadata_queue.enqueue(tenant, user, personal)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(metadata_refresh.run_job, tenant, user, personal, job_id)
        try:
            assert entered.wait(5)
            status = metadata_search.get_status(tenant, user, personal)
            assert status.state == "building"
            assert (status.progress_done, status.progress_total) == (1, 2)
            assert status.last_success_at is not None
            assert (status.last_completed_status, status.last_completed_error_code) == ("failed", "source_error")
            assert metadata_search.search(tenant, user, personal, "orders").status == status
            with pytest.raises(metadata_search.MetadataConnectionNotFound):
                metadata_search.get_status(tenant, other_user, personal)
        finally:
            release.set()
        assert future.result(timeout=5) == "partial"
    status = metadata_search.get_status(tenant, user, personal)
    assert status.state == "partial"
    assert (status.progress_done, status.progress_total) == (2, 2)
    assert (status.last_completed_status, status.last_completed_error_code) == ("partial", "incomplete")


def test_refresh_runner_rejects_mutable_secrets(catalog, queue_transactions, monkeypatch):
    tenant, _, user, _, _, personal, _, _, _ = catalog
    monkeypatch.setattr(metadata_refresh, "get_connection", lambda *args: SimpleNamespace(secret_ref="file:key.json"))
    job_id, _ = metadata_queue.enqueue(tenant, user, personal)
    assert metadata_refresh.run_job(tenant, user, personal, job_id) == "failed"
    with psycopg.connect(OWNER_URL) as conn:
        assert conn.execute("SELECT status, error_code FROM metadata_refresh_jobs WHERE id = %s",
                            (job_id,)).fetchone() == ("failed", "credential_error")
        assert conn.execute("SELECT count(*) FROM metadata_fields WHERE connection_id = %s",
                            (personal,)).fetchone()[0] == 1


def test_refresh_runner_does_not_publish_after_heartbeat_loss(catalog, queue_transactions, monkeypatch):
    tenant, _, user, _, _, personal, _, _, _ = catalog
    renewed = threading.Event()

    class Connector:
        dialect = "bigquery"

        def list_catalog_complete(self, parent=()):
            assert renewed.wait(3)
            return [CatalogNode(kind="dataset", path=("p", "d"), name="d")]

    def lose_lease(*args):
        renewed.set()
        return False

    monkeypatch.setattr(metadata_refresh, "_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(metadata_queue, "heartbeat", lose_lease)
    monkeypatch.setattr(metadata_refresh, "get_connection", lambda *args: SimpleNamespace(secret_ref=None))
    monkeypatch.setattr(metadata_refresh, "build_connector", lambda profile: Connector())
    monkeypatch.setattr(metadata_refresh, "build_policy", lambda *args: SqlPolicy("bigquery", frozenset({"p.d.*"})))
    job_id, _ = metadata_queue.enqueue(tenant, user, personal)
    with pytest.raises(RefreshLeaseLost):
        metadata_refresh.run_job(tenant, user, personal, job_id)
    with psycopg.connect(OWNER_URL) as conn:
        assert conn.execute("SELECT count(*) FROM metadata_fields WHERE connection_id = %s",
                            (personal,)).fetchone()[0] == 1
        assert conn.execute("SELECT status FROM metadata_refresh_jobs WHERE id = %s",
                            (job_id,)).fetchone()[0] == "running"


def test_refresh_runner_rejects_connection_change_during_source_fetch(catalog, queue_transactions, monkeypatch):
    tenant, _, user, _, _, personal, _, _, _ = catalog

    class Connector:
        dialect = "bigquery"

        def list_catalog_complete(self, parent=()):
            if not parent:
                return [CatalogNode(kind="dataset", path=("p", "d"), name="d")]
            return [CatalogNode(kind="table", path=("p", "d", "new"), name="new")]

        def list_columns(self, container):
            with psycopg.connect(OWNER_URL) as conn:
                conn.execute("UPDATE connections SET allowed_objects = ARRAY['p.d.secret'] WHERE id = %s",
                             (personal,))
            return {(*container, "new"): [ColumnSchema(name="id", type="STRING")]}

    monkeypatch.setattr(metadata_store, "tenant_transaction", metadata_queue.tenant_transaction)
    monkeypatch.setattr(metadata_refresh, "get_connection", lambda *args: SimpleNamespace(secret_ref=None))
    monkeypatch.setattr(metadata_refresh, "build_connector", lambda profile: Connector())
    monkeypatch.setattr(metadata_refresh, "build_policy", lambda *args: SqlPolicy("bigquery", frozenset({"p.d.*"})))
    job_id, _ = metadata_queue.enqueue(tenant, user, personal)
    with pytest.raises(RefreshLeaseLost):
        metadata_refresh.run_job(tenant, user, personal, job_id)
    with psycopg.connect(OWNER_URL) as conn:
        assert conn.execute("SELECT name FROM metadata_tables WHERE connection_id = %s",
                            (personal,)).fetchall() == [("Orders",)]
