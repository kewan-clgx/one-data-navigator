"""Tenant admin API (/v1/admin/*) against the real Postgres; auth is replaced by a fixed request context."""

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from odn.api.deps import request_context
from odn.api.main import app
from odn.config import settings
from odn.db import pool
from odn.db.migrate import migrate
from odn.db.provision import add_domain, upsert_tenant
from odn.tenancy.context import RequestContext, resolve_context
from odn.tenancy.oidc import VerifiedToken
from tests.db.test_rls import APP_URL, OWNER_URL, _reachable

pytestmark = pytest.mark.skipif(not _reachable(), reason="local Postgres not running")

GOOGLE = "https://accounts.google.com"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    migrate(OWNER_URL)
    monkeypatch.setattr(settings, "database_url", APP_URL)
    pool.cache_clear()
    created = []

    def tenant() -> tuple[uuid.UUID, str]:
        slug = f"t-{uuid.uuid4().hex[:12]}"
        tid = upsert_tenant(OWNER_URL, slug, slug, auth="google", join="domain")
        created.append(tid)
        add_domain(OWNER_URL, slug, f"{slug}.com", attest=True)
        return tid, f"{slug}.com"

    def user(dom: str, name: str, role: str = "member") -> RequestContext:
        ctx = resolve_context(VerifiedToken(GOOGLE, f"{name}-{dom}", dom, f"{name}@{dom}", True, name))
        if role != "member":
            with psycopg.connect(OWNER_URL) as conn:
                conn.execute("SELECT set_config('odn.tenant_id', %s, false)", (str(ctx.tenant_id),))
                conn.execute("UPDATE users SET role = %s WHERE id = %s", (role, ctx.user_id))
            ctx = RequestContext(ctx.tenant_id, ctx.user_id, ctx.subject, role, ctx.tenant_slug, ctx.email)  # type: ignore[arg-type]
        return ctx

    def as_(ctx: RequestContext) -> TestClient:
        app.dependency_overrides[request_context] = lambda: ctx
        return TestClient(app)

    yield tenant, user, as_
    app.dependency_overrides.clear()
    pool.cache_clear()
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM tenants WHERE id = ANY(%s)", (created,))


def test_admin_manages_users_and_events_are_recorded(env) -> None:
    tenant, user, as_ = env
    _, dom = tenant()
    admin = user(dom, "boss", "admin")
    member = user(dom, "pat")
    assert as_(member).get("/v1/admin/users").status_code == 403

    c = as_(admin)
    assert c.get("/v1/admin/organization").json()["domains"] == [dom]
    invited = c.post("/v1/admin/users", json={"email": f"New@{dom}", "role": "data_lead"})
    assert (invited.status_code, invited.json()["status"], invited.json()["email"]) == (201, "invited", f"new@{dom}")
    assert c.post("/v1/admin/users", json={"email": "x@elsewhere.com"}).status_code == 409
    assert c.patch(f"/v1/admin/users/{member.user_id}", json={"role": "admin"}).json()["role"] == "admin"
    assert c.patch(f"/v1/admin/users/{admin.user_id}", json={"status": "disabled"}).status_code == 409
    assert c.delete(f"/v1/admin/users/{invited.json()['id']}").status_code == 204
    assert c.delete(f"/v1/admin/users/{member.user_id}").status_code == 409  # only invites can be revoked

    emails = {u["email"]: u for u in c.get("/v1/admin/users").json()}
    assert set(emails) == {f"boss@{dom}", f"pat@{dom}"}
    actions = [e["action"] for e in c.get("/v1/admin/events").json()]
    assert actions == ["user.invite_revoked", "user.role_changed", "user.invited"]


def test_admin_cannot_touch_another_tenants_users(env) -> None:
    tenant, user, as_ = env
    _, dom_a = tenant()
    _, dom_b = tenant()
    admin_a = user(dom_a, "a", "admin")
    victim = user(dom_b, "v")
    c = as_(admin_a)
    assert c.patch(f"/v1/admin/users/{victim.user_id}", json={"status": "disabled"}).status_code == 404
    assert all(u["email"] != f"v@{dom_b}" for u in c.get("/v1/admin/users").json())


def test_shared_connections_are_managed_by_leads_and_visible_to_all(env, monkeypatch: pytest.MonkeyPatch) -> None:
    import base64
    import os

    from odn.platform.connections.service import get_connection, resolve_postgres_credentials

    monkeypatch.setattr(settings, "secrets_key", base64.urlsafe_b64encode(os.urandom(32)).decode())
    tenant, user, as_ = env
    _, dom = tenant()
    _, other_dom = tenant()
    lead, member, outsider = user(dom, "lead", "data_lead"), user(dom, "pat"), user(other_dom, "eve", "admin")
    body = {"name": "warehouse", "kind": "postgres", "host": "db.example.com", "database": "d", "user": "reader",
            "password": "s3cret"}

    assert as_(member).post("/v1/connections/shared", json=body).status_code == 403
    created = as_(lead).post("/v1/connections/shared", json=body)
    assert created.status_code == 201, created.text
    cid = created.json()["id"]

    seen = {c["id"]: c for c in as_(member).get("/v1/connections").json()}
    assert seen[cid]["scope"] == "shared" and "password" not in seen[cid]["config"]
    profile = get_connection(lead.tenant_id, member.user_id, uuid.UUID(cid))
    assert profile is not None and resolve_postgres_credentials(profile) == "s3cret"

    renamed = as_(lead).put(f"/v1/connections/shared/{cid}", json={**body, "name": "warehouse-2", "password": None})
    assert renamed.json()["name"] == "warehouse-2"  # password omitted: the stored one is kept
    profile = get_connection(lead.tenant_id, lead.user_id, uuid.UUID(cid))
    assert profile is not None and resolve_postgres_credentials(profile) == "s3cret"

    assert as_(member).delete(f"/v1/connections/shared/{cid}").status_code == 403
    assert as_(outsider).delete(f"/v1/connections/shared/{cid}").status_code == 404
    assert as_(lead).delete(f"/v1/connections/shared/{cid}").status_code == 204
    admin = user(dom, "boss", "admin")
    actions = [(e["action"], e["target"]) for e in as_(admin).get("/v1/admin/events").json()]
    assert actions == [("connection.deleted", "warehouse-2"), ("connection.updated", "warehouse-2"),
                       ("connection.created", "warehouse")]
