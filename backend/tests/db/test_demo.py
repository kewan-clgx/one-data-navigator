"""Live-demo check-in (/v1/auth/demo) against the real Postgres: demo users, 7-day reuse, tokens, isolation."""

import uuid

import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient

from odn.api import auth_routes
from odn.api.main import app
from odn.api.ratelimit import RateLimiter
from odn.config import settings
from odn.db import pool
from odn.db.migrate import migrate
from odn.db.provision import demo_checkins, setup_demo, upsert_tenant
from odn.platform.collibra.token import signing_key
from odn.tenancy.context import resolve_context
from odn.tenancy.oidc import DEMO_ISSUER, verifier_from_settings
from tests.db.test_rls import APP_URL, OWNER_URL, _reachable

pytestmark = pytest.mark.skipif(not _reachable(), reason="local Postgres not running")


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    from odn.platform import demo_notify

    out: list[tuple] = []
    monkeypatch.setattr(demo_notify, "new_visitor", lambda name, email: out.append(("visitor", name, email)))
    monkeypatch.setattr(demo_notify, "reopen_link", lambda name, email, token: out.append(("link", name, email, token)))
    return out


@pytest.fixture
def demo(monkeypatch: pytest.MonkeyPatch):
    migrate(OWNER_URL)
    monkeypatch.setattr(settings, "database_url", APP_URL)
    for name in ("_demo_email_limit", "_demo_ip_limit", "_demo_global_limit", "_demo_link_limit", "_reset_limit"):
        old = getattr(auth_routes, name)
        monkeypatch.setattr(auth_routes, name, RateLimiter(old.limit, old.window_s))
    pool.cache_clear()
    slug = f"demo-{uuid.uuid4().hex[:8]}"
    tid = upsert_tenant(OWNER_URL, slug, "Demo", join="invite")
    setup_demo(OWNER_URL, slug, max_gb=25)
    monkeypatch.setattr(settings, "demo_tenant", slug)
    _ADMINS.clear()
    yield TestClient(app), slug, tid
    app.dependency_overrides.clear()
    pool.cache_clear()
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM tenants WHERE id = %s", (tid,))


def _check_in(client: TestClient, name: str, email: str, device: str | None = None) -> dict:
    resp = client.post("/v1/auth/demo", json={"name": name, "email": email, "client_ip": "203.0.113.9",
                                              "device_token": device})
    assert resp.status_code == 200, resp.text
    return resp.json()


_ADMINS: dict = {}


def _admin(tid: uuid.UUID) -> dict:
    """Make the next admin-API calls run as an admin of the demo tenant (dependency override, no real token)."""
    from odn.api.deps import request_context
    from odn.tenancy.context import RequestContext

    ctx = RequestContext(tenant_id=tid, user_id=uuid.uuid4(), subject="admin", role="admin")
    app.dependency_overrides[request_context] = lambda: ctx
    return {}


def _context(token: str):
    verifier = verifier_from_settings().verifiers[DEMO_ISSUER]
    key = signing_key()

    class Jwks:
        def get_signing_key_from_jwt(self, _token: str):
            return type("K", (), {"key": key.private.public_key()})()

    verifier.__dict__["_jwks"] = Jwks()  # stands in for {api_url}/.well-known/jwks.json
    return resolve_context(verifier.verify(token))


def test_check_in_signs_in_as_a_demo_member_and_is_recorded(demo) -> None:
    client, slug, tid = demo
    out = _check_in(client, "  Pat   Lee ", "Pat@Example.org")
    assert 0 < out["expires_in"] <= settings.demo_session_hours * 3600
    ctx = _context(out["id_token"])
    assert (ctx.tenant_id, ctx.role, ctx.tenant_slug, ctx.email) == (tid, "member", slug, "pat@example.org")
    assert ctx.max_bytes_billed == 25 * 1024**3
    rows = demo_checkins(OWNER_URL, slug, 10)
    assert [(r["name"], r["email"]) for r in rows] == [("Pat Lee", "pat@example.org")]


def test_same_browser_reopens_and_another_browser_starts_fresh_with_a_link(demo, sent) -> None:
    client, _, tid = demo
    first = _check_in(client, "Pat", "pat@example.org")
    owner = _context(first["id_token"]).user_id
    again = _check_in(client, "Pat L", "PAT@example.org", device=first["device_token"])
    assert _context(again["id_token"]).user_id == owner and again["notice"] is None
    # Another browser (no device token), or a stranger typing the email: a fresh demo user, and the inbox gets a link.
    elsewhere = _check_in(client, "Pat", "pat@example.org")
    assert _context(elsewhere["id_token"]).user_id != owner
    assert elsewhere["notice"] == auth_routes.LINK_SENT
    assert [kind for kind, *_ in sent] == ["visitor", "link"]
    link = client.post("/v1/auth/demo/link", json={"token": sent[1][3]})
    assert link.status_code == 200 and _context(link.json()["id_token"]).user_id == owner
    # A device token is bound to the email it was issued for.
    other = _check_in(client, "Sam", "sam@example.org", device=first["device_token"])
    assert _context(other["id_token"]).user_id not in (owner,)
    assert client.post("/v1/auth/demo/link", json={"token": first["device_token"]}).status_code == 410
    assert client.post("/v1/auth/demo/link", json={"token": "not-a-real-token"}).status_code == 410
    kinds = [r["kind"] for r in client.get("/v1/admin/demo/checkins", headers=_admin(tid)).json()]
    assert kinds == ["new", "link", "fresh", "returning", "new"]


def test_an_expired_demo_user_frees_its_email(demo) -> None:
    client, _, tid = demo
    first = _context(_check_in(client, "Pat", "pat@example.org")["id_token"]).user_id
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("SELECT set_config('odn.tenant_id', %s, false)", (str(tid),))
        conn.execute("UPDATE users SET created_at = now() - interval '8 days' WHERE id = %s", (first,))
    fresh = _check_in(client, "Pat", "pat@example.org")
    assert _context(fresh["id_token"]).user_id != first and fresh["notice"] is None
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("SELECT set_config('odn.tenant_id', %s, false)", (str(tid),))
        retired = conn.execute("SELECT email, status FROM users WHERE id = %s", (first,)).fetchone()
    assert retired == (None, "disabled")


def test_a_token_never_outlives_its_demo_user(demo) -> None:
    client, _, tid = demo
    first = _check_in(client, "Pat", "pat@example.org")
    user = _context(first["id_token"]).user_id
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("SELECT set_config('odn.tenant_id', %s, false)", (str(tid),))
        conn.execute("UPDATE users SET created_at = now() - interval '7 days' + interval '1 hour' WHERE id = %s",
                     (user,))
    out = _check_in(client, "Pat", "pat@example.org", device=first["device_token"])
    assert _context(out["id_token"]).user_id == user
    assert out["expires_in"] <= 3600
    assert jwt.decode(out["id_token"], options={"verify_signature": False})["exp"] <= jwt.decode(
        out["device_token"], options={"verify_signature": False})["exp"]


def test_staff_and_disabled_emails_are_refused(demo) -> None:
    client, _, tid = demo
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("SELECT set_config('odn.tenant_id', %s, false)", (str(tid),))
        conn.execute("INSERT INTO users (tenant_id, email, status) VALUES (%s, 'admin@example.org', 'invited')", (tid,))
    resp = client.post("/v1/auth/demo", json={"name": "X", "email": "admin@example.org"})
    assert resp.status_code == 409
    user = _context(_check_in(client, "Pat", "pat@example.org")["id_token"]).user_id
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("SELECT set_config('odn.tenant_id', %s, false)", (str(tid),))
        conn.execute("UPDATE users SET status = 'disabled' WHERE id = %s", (user,))
    assert client.post("/v1/auth/demo", json={"name": "Pat", "email": "pat@example.org"}).status_code == 403


def test_demo_is_closed_unless_configured(demo, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, _ = demo
    monkeypatch.setattr(settings, "demo_tenant", "no-such-demo")
    assert client.post("/v1/auth/demo", json={"name": "Pat", "email": "pat@example.org"}).status_code == 404
    monkeypatch.setattr(settings, "demo_tenant", "")
    assert client.post("/v1/auth/demo", json={"name": "Pat", "email": "pat@example.org"}).status_code == 404
    assert client.post("/v1/auth/demo", json={"name": " ", "email": "pat@example.org"}).status_code == 422


def test_check_ins_are_rate_limited_per_email(demo) -> None:
    client, _, _ = demo
    codes = [client.post("/v1/auth/demo", json={"name": "Pat", "email": "pat@example.org"}).status_code
             for _ in range(11)]
    assert codes == [200] * 10 + [429]


def test_admin_sees_check_ins_csv_and_funnel(demo, sent) -> None:
    client, _, tid = demo
    pat = _check_in(client, "Pat", "pat@example.org")
    _check_in(client, "=HYPERLINK(1)", "eve@example.org")
    _check_in(client, "Pat", "pat@example.org", device=pat["device_token"])
    pat_user = _context(pat["id_token"]).user_id
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("SELECT set_config('odn.tenant_id', %s, false)", (str(tid),))
        conn.execute("INSERT INTO llm_usage (tenant_id, user_id, model, requests) VALUES (%s, %s, 'm', 3)",
                     (tid, pat_user))
        conn.execute("INSERT INTO audit_logs (tenant_id, user_id, source, dialect, sql_text, decision) "
                     "VALUES (%s, %s, 'chat', 'bigquery', 'SELECT 1', 'allowed')", (tid, pat_user))
    headers = _admin(tid)
    rows = client.get("/v1/admin/demo/checkins", headers=headers).json()
    assert [(r["email"], r["kind"], r["questions"], r["queries"]) for r in rows] == [
        ("pat@example.org", "returning", 3, 1), ("eve@example.org", "new", 0, 0), ("pat@example.org", "new", 3, 1)]
    csv_text = client.get("/v1/admin/demo/checkins.csv", headers=headers).text
    assert csv_text.splitlines()[0] == "checked_in_at,name,email,kind,questions,queries"
    assert ",'=HYPERLINK(1),eve@example.org," in csv_text
    funnel = client.get("/v1/admin/demo/funnel", headers=headers).json()
    assert funnel == {"visitors": 2, "new_visitors": 2, "asked": 1, "answered": 1, "returned": 1, "checkins": 3}


def test_only_a_verified_personal_account_can_import_its_demo_chats(demo, sent, monkeypatch) -> None:
    from odn.api.deps import request_context
    from odn.platform import demo as demo_service
    from odn.tenancy.context import RequestContext

    client, _, _ = demo
    pat = _context(_check_in(client, "Pat", "pat@example.org")["id_token"])
    monkeypatch.setattr(settings, "personal_tenant", "personal-test")

    def as_user(slug: str, verified: bool) -> None:
        ctx = RequestContext(tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), subject="p", role="member",
                             tenant_slug=slug, email="Pat@example.org", email_verified=verified)
        app.dependency_overrides[request_context] = lambda: ctx

    as_user("personal-test", False)
    assert client.get("/v1/me/demo-chats").status_code == 404
    as_user("some-company", True)
    assert client.get("/v1/me/demo-chats").status_code == 404
    as_user("personal-test", True)
    assert client.get("/v1/me/demo-chats").json() == {"tenant": settings.demo_tenant, "subjects": [pat.subject]}
    done = client.post("/v1/me/demo-chats/imported", json={"subjects": [pat.subject, "someone-else"]})
    assert done.json() == {"retired": 1}
    assert demo_service.importable("pat@example.org") == []
