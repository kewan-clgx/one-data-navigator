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
def demo(monkeypatch: pytest.MonkeyPatch):
    migrate(OWNER_URL)
    monkeypatch.setattr(settings, "database_url", APP_URL)
    for name in ("_demo_email_limit", "_demo_ip_limit", "_demo_global_limit"):
        old = getattr(auth_routes, name)
        monkeypatch.setattr(auth_routes, name, RateLimiter(old.limit, old.window_s))
    pool.cache_clear()
    slug = f"demo-{uuid.uuid4().hex[:8]}"
    tid = upsert_tenant(OWNER_URL, slug, "Demo", join="invite")
    setup_demo(OWNER_URL, slug, max_gb=25)
    monkeypatch.setattr(settings, "demo_tenant", slug)
    yield TestClient(app), slug, tid
    pool.cache_clear()
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM tenants WHERE id = %s", (tid,))


def _check_in(client: TestClient, name: str, email: str) -> dict:
    resp = client.post("/v1/auth/demo", json={"name": name, "email": email, "client_ip": "203.0.113.9"})
    assert resp.status_code == 200, resp.text
    return resp.json()


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


def test_same_email_reopens_the_demo_user_until_it_expires(demo) -> None:
    client, _, tid = demo
    first = _context(_check_in(client, "Pat", "pat@example.org")["id_token"]).user_id
    again = _context(_check_in(client, "Pat L", "PAT@example.org")["id_token"]).user_id
    other = _context(_check_in(client, "Sam", "sam@example.org")["id_token"]).user_id
    assert first == again != other
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("SELECT set_config('odn.tenant_id', %s, false)", (str(tid),))
        conn.execute("UPDATE users SET created_at = now() - interval '8 days' WHERE id = %s", (first,))
    fresh = _context(_check_in(client, "Pat", "pat@example.org")["id_token"]).user_id
    assert fresh != first
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("SELECT set_config('odn.tenant_id', %s, false)", (str(tid),))
        retired = conn.execute("SELECT email, status FROM users WHERE id = %s", (first,)).fetchone()
    assert retired == (None, "disabled")


def test_a_token_never_outlives_its_demo_user(demo) -> None:
    client, _, tid = demo
    user = _context(_check_in(client, "Pat", "pat@example.org")["id_token"]).user_id
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("SELECT set_config('odn.tenant_id', %s, false)", (str(tid),))
        conn.execute("UPDATE users SET created_at = now() - interval '7 days' + interval '1 hour' WHERE id = %s",
                     (user,))
    out = _check_in(client, "Pat", "pat@example.org")
    assert out["expires_in"] <= 3600
    assert jwt.decode(out["id_token"], options={"verify_signature": False})["sub"]


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
