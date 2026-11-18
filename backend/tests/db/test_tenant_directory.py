"""Tenant directory and sign-in rules against the real Postgres (skipped if it is not running)."""

import uuid

import psycopg
import pytest

from odn.config import settings
from odn.db import pool, tenant_transaction
from odn.db.migrate import migrate
from odn.db.provision import add_domain, upsert_tenant
from odn.tenancy import context, directory
from odn.tenancy.context import AccessDenied, UnknownTenant, resolve_context
from odn.tenancy.oidc import VerifiedToken
from tests.db.test_rls import APP_URL, OWNER_URL, _reachable

pytestmark = pytest.mark.skipif(not _reachable(), reason="local Postgres not running")

GOOGLE = "https://accounts.google.com"


@pytest.fixture(autouse=True)
def _app_pool(monkeypatch: pytest.MonkeyPatch):
    migrate(OWNER_URL)
    monkeypatch.setattr(settings, "database_url", APP_URL)
    pool.cache_clear()
    context._fallback_allowlist.cache_clear()
    yield
    pool.cache_clear()
    context._fallback_allowlist.cache_clear()


@pytest.fixture
def make_tenant():
    created: list[uuid.UUID] = []

    def make(join: str = "domain", domain: bool = True, status: str = "active") -> tuple[str, uuid.UUID, str]:
        slug = f"t-{uuid.uuid4().hex[:12]}"
        tid = upsert_tenant(OWNER_URL, slug, slug.upper(), auth="google", join=join, status=status)
        created.append(tid)
        dom = f"{slug}.example.com"
        if domain:
            assert add_domain(OWNER_URL, slug, dom, attest=True)
        return slug, tid, dom

    yield make
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("DELETE FROM tenants WHERE id = ANY(%s)", (created,))


def _token(dom: str, user: str = "pat", verified: bool = True, sub: str | None = None, iss: str = GOOGLE) -> VerifiedToken:
    return VerifiedToken(issuer=iss, subject=sub or f"sub-{user}-{dom}", external_id=dom, email=f"{user}@{dom}",
                         email_verified=verified, name=user.title())


def test_domain_join_creates_member_once(make_tenant) -> None:
    _, tid, dom = make_tenant()
    first = resolve_context(_token(dom))
    again = resolve_context(_token(dom))
    assert (first.tenant_id, first.role, first.user_id) == (tid, "member", again.user_id)


@pytest.mark.parametrize("verified", [False])
def test_unverified_email_is_refused(make_tenant, verified: bool) -> None:
    _, _, dom = make_tenant()
    with pytest.raises(AccessDenied):
        resolve_context(_token(dom, verified=verified))


def test_email_outside_tenant_domains_is_refused(make_tenant) -> None:
    _, _, dom = make_tenant()
    token = VerifiedToken(issuer=GOOGLE, subject="s", external_id=dom, email="eve@evil.com", email_verified=True, name=None)
    with pytest.raises(AccessDenied):
        resolve_context(token)


def test_unmapped_identity_is_unknown(make_tenant) -> None:
    with pytest.raises(UnknownTenant):
        resolve_context(_token("nobody.example.org", iss="https://unmapped-issuer"))


def test_suspended_tenant_is_refused(make_tenant) -> None:
    _, _, dom = make_tenant(status="suspended")
    with pytest.raises(AccessDenied):
        resolve_context(_token(dom))


def test_invite_only_tenant_admits_invited_users(make_tenant) -> None:
    _, tid, dom = make_tenant(join="invite")
    with pytest.raises(AccessDenied):
        resolve_context(_token(dom, user="stranger"))
    with tenant_transaction(tid) as conn:
        directory.invite(conn, tid, f"Alice@{dom}", "data_lead")
        with pytest.raises(directory.DirectoryError):
            directory.invite(conn, tid, "alice@elsewhere.com")
    with pytest.raises(AccessDenied):  # an unverified email never claims an invite
        resolve_context(_token(dom, user="alice", verified=False))
    ctx = resolve_context(_token(dom, user="alice"))
    assert ctx.role == "data_lead"
    with tenant_transaction(tid) as conn:
        user = next(u for u in directory.list_users(conn, tid) if u["id"] == ctx.user_id)
        assert (user["status"], user["signed_in"]) == ("active", True)
        with pytest.raises(directory.DirectoryError):
            directory.invite(conn, tid, f"alice@{dom}")


def test_disabled_user_is_refused_and_last_admin_is_kept(make_tenant) -> None:
    _, tid, dom = make_tenant()
    admin = resolve_context(_token(dom, user="admin"))
    member = resolve_context(_token(dom, user="member"))
    with tenant_transaction(tid) as conn:
        directory.update_user(conn, tid, admin.user_id, role="admin")
        for change in ({"role": "member"}, {"status": "disabled"}):
            with pytest.raises(directory.DirectoryError, match="at least one active admin"):
                directory.update_user(conn, tid, admin.user_id, **change)
        directory.update_user(conn, tid, member.user_id, status="disabled")
    with pytest.raises(AccessDenied):
        resolve_context(_token(dom, user="member"))
    with tenant_transaction(tid) as conn:
        directory.update_user(conn, tid, member.user_id, status="active", role="admin")
        directory.update_user(conn, tid, admin.user_id, role="member")  # another admin exists now
    assert resolve_context(_token(dom, user="member")).role == "admin"


def test_admin_must_be_on_a_verified_domain(make_tenant) -> None:
    _, tid, _ = make_tenant()
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("SELECT set_config('odn.tenant_id', %s, false)", (str(tid),))
        uid = conn.execute(
            "INSERT INTO users (tenant_id, issuer, subject, email) VALUES (%s, %s, 'x', 'x@other.com') RETURNING id",
            (tid, GOOGLE),
        ).fetchone()[0]
    with tenant_transaction(tid) as conn, pytest.raises(directory.DirectoryError, match="verified domains"):
        directory.update_user(conn, tid, uid, role="admin")


def test_same_email_with_another_identity_is_refused(make_tenant) -> None:
    _, _, dom = make_tenant()
    resolve_context(_token(dom, sub="first"))
    with pytest.raises(AccessDenied):
        resolve_context(_token(dom, sub="second"))


def test_fallback_tenant_applies_email_allowlist(make_tenant, monkeypatch: pytest.MonkeyPatch) -> None:
    slug, tid, _ = make_tenant(domain=False)
    issuer = f"https://fallback-{slug}"
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("INSERT INTO tenant_identity_providers (issuer, external_id, tenant_id) VALUES (%s, '*', %s)",
                     (issuer, tid))
    monkeypatch.setattr(settings, "oidc_default_tenant_emails", "pat@gmail.com")
    ok = VerifiedToken(issuer=issuer, subject="p", external_id="", email="pat@gmail.com", email_verified=True, name=None)
    assert resolve_context(ok).tenant_id == tid
    with pytest.raises(AccessDenied):
        resolve_context(VerifiedToken(issuer=issuer, subject="e", external_id="", email="eve@gmail.com",
                                      email_verified=True, name=None))


def test_domain_verification_by_dns_txt(make_tenant) -> None:
    slug, tid, _ = make_tenant(domain=False)
    assert add_domain(OWNER_URL, slug, f"{slug}.org", txt_lookup=lambda _: ["other"]) is False
    assert add_domain(OWNER_URL, slug, f"{slug}.org", txt_lookup=lambda _: [f"odn-domain-verification={tid}"]) is True
    other, _, _ = make_tenant(domain=False)
    with pytest.raises(SystemExit):
        add_domain(OWNER_URL, other, f"{slug}.org", attest=True)
