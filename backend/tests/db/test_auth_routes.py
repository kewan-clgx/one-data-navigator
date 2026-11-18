"""Sign-in endpoints (/v1/auth/*) against the real Postgres; Identity Platform calls are stubbed."""

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from odn.api import auth_routes
from odn.api.main import app
from odn.api.ratelimit import RateLimiter
from odn.config import settings
from odn.db import pool
from odn.db.migrate import migrate
from odn.db.provision import add_domain, upsert_tenant
from odn.platform import identity_platform
from tests.db.test_rls import APP_URL, OWNER_URL, _reachable

pytestmark = pytest.mark.skipif(not _reachable(), reason="local Postgres not running")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    migrate(OWNER_URL)
    monkeypatch.setattr(settings, "database_url", APP_URL)
    monkeypatch.setattr(settings, "idp_project", "proj")
    monkeypatch.setattr(settings, "idp_api_key", "key")
    for name in ("_discover_limit", "_password_limit", "_email_limit"):
        old = getattr(auth_routes, name)
        monkeypatch.setattr(auth_routes, name, RateLimiter(old.limit, old.window_s))
    pool.cache_clear()
    yield TestClient(app)
    pool.cache_clear()


@pytest.fixture
def orgs():
    suffix = uuid.uuid4().hex[:8]
    sso = upsert_tenant(OWNER_URL, f"sso-{suffix}", f"Sso {suffix}", auth="google", join="domain")
    pw = upsert_tenant(OWNER_URL, f"pw-{suffix}", f"Pw {suffix}", auth="password", join="invite",
                       idp_tenant_id=f"idp-{suffix}")
    open_ = upsert_tenant(OWNER_URL, f"open-{suffix}", f"Open {suffix}", auth="google", join="domain")
    add_domain(OWNER_URL, f"sso-{suffix}", f"sso-{suffix}.com", attest=True)
    add_domain(OWNER_URL, f"pw-{suffix}", f"pw-{suffix}.com", attest=True)
    yield suffix
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM tenants WHERE id = ANY(%s)", ([sso, pw, open_],))


def test_discover_routes_by_org_and_domain(client: TestClient, orgs: str) -> None:
    s = orgs
    sso = client.post("/v1/auth/discover", json={"organization": f"SSO-{s}", "email": f"a@sso-{s}.com"}).json()
    assert sso == {"method": "google", "organization": f"Sso {s}", "hd": f"sso-{s}.com"}
    pw = client.post("/v1/auth/discover", json={"organization": f"Pw {s}", "email": f"a@PW-{s}.com"}).json()
    assert pw["method"] == "password"
    domainless = client.post("/v1/auth/discover", json={"organization": f"open-{s}", "email": "me@gmail.com"}).json()
    assert (domainless["method"], domainless["hd"]) == ("google", None)
    for org, email in ((f"sso-{s}", f"a@pw-{s}.com"), (f"pw-{s}", "a@gmail.com"), ("nope", f"a@sso-{s}.com")):
        resp = client.post("/v1/auth/discover", json={"organization": org, "email": email})
        assert (resp.status_code, resp.json()["detail"]) == (404, auth_routes.NOT_FOUND)


def test_password_sign_in(client: TestClient, orgs: str, monkeypatch: pytest.MonkeyPatch) -> None:
    s = orgs
    calls = []

    def sign_in(idp_tenant: str, email: str, password: str):
        calls.append((idp_tenant, email))
        if password != "right":
            raise identity_platform.SignInFailed("x")
        return identity_platform.Tokens("id", "refresh", 3600)

    monkeypatch.setattr(identity_platform, "sign_in", sign_in)
    ok = client.post("/v1/auth/password", json={"organization": f"pw-{s}", "email": f"a@pw-{s}.com", "password": "right"})
    assert ok.json() == {"id_token": "id", "refresh_token": "refresh", "expires_in": 3600}
    assert calls == [(f"idp-{s}", f"a@pw-{s}.com")]
    for org, email, pw in ((f"pw-{s}", f"a@pw-{s}.com", "wrong"), (f"sso-{s}", f"a@sso-{s}.com", "right")):
        resp = client.post("/v1/auth/password", json={"organization": org, "email": email, "password": pw})
        assert (resp.status_code, resp.json()["detail"]) == (401, auth_routes.SIGN_IN_FAILED)
    assert len(calls) == 2  # the SSO organization never reaches Identity Platform


def test_password_attempts_are_rate_limited(client: TestClient, orgs: str, monkeypatch: pytest.MonkeyPatch) -> None:
    s = orgs
    monkeypatch.setattr(identity_platform, "sign_in", lambda *a: (_ for _ in ()).throw(identity_platform.SignInFailed()))
    body = {"organization": f"pw-{s}", "email": f"a@pw-{s}.com", "password": "x"}
    codes = [client.post("/v1/auth/password", json=body).status_code for _ in range(11)]
    assert codes == [401] * 10 + [429]


def test_forgot_is_always_accepted_and_only_emails_password_orgs(
    client: TestClient, orgs: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = orgs
    sent = []
    monkeypatch.setattr(identity_platform, "send_password_email", lambda t, e: sent.append((t, e)))
    for org, email in ((f"pw-{s}", f"b@pw-{s}.com"), (f"sso-{s}", f"b@sso-{s}.com"), ("nope", "x@y.com")):
        assert client.post("/v1/auth/forgot", json={"organization": org, "email": email}).status_code == 202
    assert sent == [(f"idp-{s}", f"b@pw-{s}.com")]
