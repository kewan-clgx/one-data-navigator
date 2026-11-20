"""/v1/collibra/* against the real Postgres: per-tenant environments (RLS), user identity, JWKS."""

import dataclasses
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from odn.api import collibra_routes
from odn.api.deps import request_context
from odn.api.main import app
from odn.config import settings
from odn.db import pool
from odn.db.migrate import migrate
from odn.db.provision import add_domain, upsert_collibra, upsert_tenant
from odn.platform.collibra import token as tokens
from odn.platform.collibra.client import CollibraClient
from odn.tenancy.context import resolve_context
from odn.tenancy.oidc import VerifiedToken
from tests.db.test_rls import APP_URL, OWNER_URL, _reachable
from tests.platform.test_collibra import ASSET, fake_collibra

pytestmark = pytest.mark.skipif(not _reachable(), reason="local Postgres not running")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    migrate(OWNER_URL)
    monkeypatch.setattr(settings, "database_url", APP_URL)
    monkeypatch.setattr(settings, "public_url", "https://app.example.com")
    monkeypatch.setattr(settings, "collibra_signing_key", "")
    tokens.signing_key.cache_clear()
    pool.cache_clear()
    created: list[uuid.UUID] = []
    seen: list[str] = []

    class FakeCollibra(CollibraClient):
        def __init__(self, environment, **kw):
            super().__init__(environment, transport=fake_collibra({"ann@" + environment.audience}, seen, environment.audience), **kw)

    monkeypatch.setattr(collibra_routes, "CollibraClient", FakeCollibra)

    def tenant(with_collibra: bool) -> str:
        slug = f"t-{uuid.uuid4().hex[:12]}"
        created.append(upsert_tenant(OWNER_URL, slug, slug, auth="google", join="domain"))
        add_domain(OWNER_URL, slug, f"{slug}.com", attest=True)
        if with_collibra:
            # The fake Collibra knows "ann@<audience>"; use the tenant's domain as audience to keep users apart.
            upsert_collibra(OWNER_URL, slug, "prd", "https://catalog.example.com", f"{slug}.com")
        return slug

    def as_(slug: str, name: str = "ann", role: str = "member") -> TestClient:
        dom = f"{slug}.com"
        ctx = resolve_context(VerifiedToken("https://accounts.google.com", f"{name}-{dom}", dom, f"{name}@{dom}", True, name))
        if role != "member":
            ctx = dataclasses.replace(ctx, role=role)
        app.dependency_overrides[request_context] = lambda: ctx
        return TestClient(app)

    yield tenant, as_, seen
    app.dependency_overrides.clear()
    pool.cache_clear()
    tokens.signing_key.cache_clear()
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM tenants WHERE id = ANY(%s)", (created,))


def test_environments_are_per_tenant_and_calls_run_as_the_user(env) -> None:
    tenant, as_, seen = env
    a, b = tenant(True), tenant(False)

    ann = as_(a)
    assert ann.get("/v1/collibra/environments").json() == {
        "environments": [{"env": "prd", "base_url": "https://catalog.example.com", "access_mode": "token"}]
    }
    status = ann.get("/v1/collibra/prd/status").json()
    assert status["authenticated"] is True and status["username"] == f"ann@{a}.com"
    found = ann.get("/v1/collibra/prd/assets", params={"q": "revenue", "limit": 2}).json()
    assert found["results"][0]["id"] == ASSET
    described = ann.post("/v1/collibra/prd/describe", json={"asset_ids": [ASSET]}).json()
    assert described["assets"][0]["definition"].startswith("Total annual revenue")

    # Another tenant sees no environment and cannot reach the first tenant's Collibra.
    other = as_(b)
    assert other.get("/v1/collibra/environments").json() == {"environments": []}
    calls = len(seen)
    assert other.get("/v1/collibra/prd/assets", params={"q": "revenue"}).status_code == 404
    assert len(seen) == calls


def test_user_without_a_collibra_account_is_told_why(env) -> None:
    tenant, as_, _ = env
    bob = as_(tenant(True), "bob")
    status = bob.get("/v1/collibra/prd/status").json()
    assert status["authenticated"] is False and "Collibra user named bob@" in status["detail"]
    assert bob.get("/v1/collibra/prd/assets", params={"q": "revenue"}).status_code == 403


def test_input_bounds_and_public_jwks(env) -> None:
    tenant, as_, _ = env
    ann = as_(tenant(True))
    assert ann.get("/v1/collibra/prd/assets", params={"q": "x", "limit": 500}).status_code == 422
    assert ann.get("/v1/collibra/prd/assets/not-a-uuid").status_code == 422
    assert ann.post("/v1/collibra/prd/describe", json={"asset_ids": []}).status_code == 422

    app.dependency_overrides.clear()
    keys = TestClient(app).get("/.well-known/jwks.json")
    assert keys.status_code == 200 and keys.json()["keys"][0]["alg"] == "RS256"


def test_admin_manages_collibra_as_a_data_source(env) -> None:
    tenant, as_, seen = env
    slug = tenant(False)

    member = as_(slug, "ann")  # the override applies to the most recent as_() call
    assert member.get("/v1/admin/collibra/environments").status_code == 403
    assert member.put("/v1/admin/collibra/environments/prd", json={"base_url": "https://x.collibra.com"}).status_code == 403

    admin = as_(slug, "boss", role="admin")
    listing = admin.get("/v1/admin/collibra/environments").json()
    assert listing["environments"] == [] and listing["jwks_url"] == "https://app.example.com/.well-known/jwks.json"
    saved = admin.put("/v1/admin/collibra/environments/prd", json={"base_url": "https://Corp.Collibra.com/"}).json()
    assert saved["base_url"] == "https://corp.collibra.com" and saved["access_mode"] == "browser"
    assert admin.put("/v1/admin/collibra/environments/int",
                     json={"base_url": "https://x.collibra.com", "access_mode": "token"}).status_code == 422
    assert admin.put("/v1/admin/collibra/environments/prd", json={"base_url": "http://x.collibra.com"}).status_code == 422
    assert admin.put("/v1/admin/collibra/environments/Bad", json={"base_url": "https://x.collibra.com"}).status_code == 422

    # Members see it as a data source; server-side reads are refused for browser mode (no Collibra call made).
    member = as_(slug, "ann")
    assert member.get("/v1/collibra/environments").json()["environments"] == [
        {"env": "prd", "base_url": "https://corp.collibra.com", "access_mode": "browser"}
    ]
    calls = len(seen)
    assert member.get("/v1/collibra/prd/assets", params={"q": "revenue"}).status_code == 409
    assert len(seen) == calls

    admin = as_(slug, "boss", role="admin")
    assert admin.delete("/v1/admin/collibra/environments/prd").status_code == 204
    assert admin.delete("/v1/admin/collibra/environments/prd").status_code == 404
    actions = {e["action"] for e in admin.get("/v1/admin/events").json()}
    assert {"collibra.environment_saved", "collibra.environment_removed"} <= actions
