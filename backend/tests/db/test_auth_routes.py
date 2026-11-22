"""Sign-in endpoints (/v1/auth/*) against the real Postgres; Identity Platform calls are stubbed."""

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
    for name in ("_discover_limit", "_password_limit", "_email_limit", "_signup_limit"):
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
    monkeypatch.setattr(identity_platform, "send_password_email", lambda t, e, **kw: sent.append((t, e)))
    for org, email in ((f"pw-{s}", f"b@pw-{s}.com"), (f"sso-{s}", f"b@sso-{s}.com"), ("nope", "x@y.com")):
        assert client.post("/v1/auth/forgot", json={"organization": org, "email": email}).status_code == 202
    assert sent == [(f"idp-{s}", f"b@pw-{s}.com")]


def test_failed_password_sign_ins_are_logged_for_the_organization(
    client: TestClient, orgs: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odn.platform.governance import auth_events
    from tests.db.test_tenant_directory import _auth_events

    auth_events._recent.clear()
    s = orgs
    monkeypatch.setattr(identity_platform, "sign_in", lambda *a: (_ for _ in ()).throw(identity_platform.SignInFailed()))
    body = {"organization": f"pw-{s}", "email": f"A@pw-{s}.com", "password": "never-logged-x1"}
    for _ in range(2):
        client.post("/v1/auth/password", json=body)
    client.post("/v1/auth/password", json={**body, "organization": "nope"})
    with psycopg.connect(OWNER_URL) as conn:
        tid = conn.execute("SELECT id FROM tenants WHERE slug = %s", (f"pw-{s}",)).fetchone()[0]
        conn.execute("SELECT set_config('odn.tenant_id', %s, false)", (str(tid),))
        raw = conn.execute("SELECT detail::text FROM admin_events WHERE tenant_id = %s", (tid,)).fetchall()
    assert _auth_events(tid) == [(f"a@pw-{s}.com", "wrong_password_or_inactive")] * 2
    assert all("never-logged" not in r[0] for r in raw)


def test_set_password_link(client: TestClient, orgs: str, monkeypatch: pytest.MonkeyPatch) -> None:
    s = orgs

    def confirm(tenant: str, code: str, password: str) -> str:
        if password == "weak":
            raise identity_platform.WeakPassword("the password needs to be stronger")
        return f"new@pw-{s}.com"

    def check(tenant: str, code: str) -> str:
        if code.startswith("used"):
            raise identity_platform.ResetLinkInvalid("this link has expired or was already used")
        return f"new@pw-{s}.com"

    monkeypatch.setattr(identity_platform, "check_reset_code", check)
    monkeypatch.setattr(identity_platform, "confirm_reset", confirm)
    base = {"oob_code": "abcdefghijkl", "tenant_id": f"idp-{s}"}
    assert client.post("/v1/auth/reset", json=base).json() == {
        "email": f"new@pw-{s}.com", "organization": f"pw-{s}", "personal": False}
    assert client.post("/v1/auth/reset", json={**base, "new_password": "weak"}).status_code == 422
    assert client.post("/v1/auth/reset", json={**base, "new_password": "Strong-Pass-123"}).status_code == 200
    assert client.post("/v1/auth/reset", json={**base, "oob_code": "used-abcdefgh"}).status_code == 410
    assert client.post("/v1/auth/reset", json={**base, "tenant_id": "bad id!"}).status_code == 422


@pytest.fixture
def personal(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    suffix = uuid.uuid4().hex[:8]
    tid = upsert_tenant(OWNER_URL, f"me-{suffix}", "Personal", auth="google", join="domain",
                        idp_tenant_id=f"idp-me-{suffix}")
    monkeypatch.setattr(settings, "personal_tenant", f"me-{suffix}")
    yield f"idp-me-{suffix}"
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM tenants WHERE id = %s", (tid,))


def _id_token(verified: bool) -> str:
    return jwt.encode({"email_verified": verified}, "test-secret-not-used-for-verification", algorithm="HS256")


def test_personal_signup_only_emails_a_set_password_link(
    client: TestClient, personal: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    invited = []
    monkeypatch.setattr(identity_platform, "invite", lambda idp, e, **kw: invited.append((idp, e, kw)))
    for body in ({"email": "New@Example.org", "name": " Pat "}, {"email": "taken@example.org"}):
        resp = client.post("/v1/auth/personal/signup", json=body)
        assert (resp.status_code, resp.json()) == (202, {"detail": auth_routes.CHECK_INBOX})
    assert invited == [
        (personal, "new@example.org", {"display_name": "Pat", "signup": True}),
        (personal, "taken@example.org", {"display_name": None, "signup": True}),
    ]
    assert client.post("/v1/auth/personal/signup", json={"email": "not-an-email"}).status_code == 422


def test_personal_sign_up_is_closed_unless_the_tenant_is_open(
    client: TestClient, personal: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = {"email": "a@example.org"}
    monkeypatch.setattr(identity_platform, "invite", lambda *a, **kw: pytest.fail("must not create accounts"))
    add_domain(OWNER_URL, settings.personal_tenant, f"{settings.personal_tenant}.org", attest=True)
    assert client.post("/v1/auth/personal/signup", json=body).status_code == 404
    monkeypatch.setattr(settings, "personal_tenant", "")
    assert client.post("/v1/auth/personal/signup", json=body).status_code == 404


def test_personal_password_sign_in_requires_a_verified_email(
    client: TestClient, personal: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    verified = {"pat@example.org": True, "new@example.org": False}

    def sign_in(idp: str, email: str, password: str):
        assert idp == personal
        if password != "right":
            raise identity_platform.SignInFailed("x")
        return identity_platform.Tokens(_id_token(verified[email]), "refresh", 3600)

    monkeypatch.setattr(identity_platform, "sign_in", sign_in)
    ok = client.post("/v1/auth/personal/password", json={"email": "Pat@example.org", "password": "right"})
    assert ok.status_code == 200 and ok.json()["refresh_token"] == "refresh"
    wrong = client.post("/v1/auth/personal/password", json={"email": "pat@example.org", "password": "nope"})
    assert (wrong.status_code, wrong.json()["detail"]) == (401, auth_routes.SIGN_IN_FAILED)
    unverified = client.post("/v1/auth/personal/password", json={"email": "new@example.org", "password": "right"})
    assert (unverified.status_code, unverified.json()["detail"]) == (403, auth_routes.NOT_VERIFIED)


def test_personal_reset_links_are_reported_as_personal(
    client: TestClient, personal: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = []
    monkeypatch.setattr(identity_platform, "send_password_email", lambda idp, e, **kw: sent.append((idp, e)))
    assert client.post("/v1/auth/personal/forgot", json={"email": "pat@example.org"}).status_code == 202
    assert sent == [(personal, "pat@example.org")]
    monkeypatch.setattr(identity_platform, "check_reset_code", lambda idp, code: "pat@example.org")
    ok = client.post("/v1/auth/reset", json={"oob_code": "abcdefghijkl", "tenant_id": personal})
    assert ok.json() == {"email": "pat@example.org", "organization": None, "personal": True}
