"""Unauthenticated sign-in endpoints, called server-to-server by the Next.js BFF (not exposed via its proxy).

The typed organization and email only route the sign-in; the tenant a user ends up in is always derived
from the verified token (odn.tenancy.context). Responses never reveal whether an account exists.
"""

from __future__ import annotations

from typing import Annotated, Literal

import jwt
from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, Field

from odn.api.ratelimit import RateLimiter
from odn.config import settings
from odn.db import system_transaction
from odn.platform import identity_platform
from odn.platform.governance.auth_events import record_auth_failure

router = APIRouter(prefix="/v1/auth")

NOT_FOUND = "We couldn't find that organization for this email. Check both, or ask your organization's admin."
SIGN_IN_FAILED = "Email or password is incorrect, or the account is not active."
TOO_MANY = "Too many attempts. Please wait a few minutes and try again."

_discover_limit = RateLimiter(limit=30, window_s=60)
_password_limit = RateLimiter(limit=10, window_s=15 * 60)
_email_limit = RateLimiter(limit=3, window_s=15 * 60)


class DiscoverIn(BaseModel):
    organization: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)


class PasswordIn(DiscoverIn):
    password: str = Field(min_length=1, max_length=256)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=4096)


class ResetIn(BaseModel):
    oob_code: str = Field(min_length=10, max_length=2000)
    tenant_id: str = Field(pattern=r"^[A-Za-z0-9-]{3,64}$")
    new_password: str | None = Field(default=None, min_length=1, max_length=256)


class ResetOut(BaseModel):
    email: str
    organization: str | None = None
    personal: bool = False


class EntryOut(BaseModel):
    slug: str
    organization: str
    theme: str
    method: Literal["google", "oidc", "password"]
    # Google Workspace hint: the organization's only verified email domain, if it has exactly one.
    hd: str | None = None
    personal: bool = False


class DiscoverOut(BaseModel):
    method: Literal["google", "oidc", "password"]
    organization: str
    hd: str | None = None


class TokensOut(BaseModel):
    id_token: str
    refresh_token: str
    expires_in: int


def _domain(email: str, what: str = "work email") -> str:
    local, _, domain = email.strip().lower().rpartition("@")
    if not local or "." not in domain:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"enter a valid {what}")
    return domain


def _discover(organization: str, email: str) -> dict | None:
    with system_transaction() as conn:
        return conn.execute(
            "SELECT * FROM odn_discover_tenant(%s, %s)", (organization, _domain(email))
        ).fetchone()


@router.post("/discover")
def discover(body: DiscoverIn) -> DiscoverOut:
    if not _discover_limit.allow(body.email.lower()):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY)
    tenant = _discover(body.organization, body.email)
    if tenant is None or tenant["auth_method"] == "saml":
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    if tenant["auth_method"] == "password" and not (tenant["idp_tenant_id"] and identity_platform.enabled()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    domain = _domain(body.email)
    return DiscoverOut(
        method=tenant["auth_method"],
        organization=tenant["name"],
        # Google Workspace hint, only for real company domains (personal accounts have none).
        hd=domain if tenant["auth_method"] == "google" and domain not in ("gmail.com", "googlemail.com") else None,
    )


@router.get("/entry/{slug}")
def entry(slug: Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{1,62}$")]) -> EntryOut:
    """An organization's sign-in link (/<slug>): what its branded sign-in page needs. 404 if unknown or inactive."""
    with system_transaction() as conn:
        tenant = conn.execute("SELECT * FROM odn_tenant_entry(%s)", (slug,)).fetchone()
    if tenant is None or tenant["auth_method"] == "saml":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    if tenant["auth_method"] == "password" and not (tenant["idp_tenant_id"] and identity_platform.enabled()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    domains = tenant["domains"] or []
    return EntryOut(
        slug=tenant["slug"], organization=tenant["name"], theme=tenant["theme"], method=tenant["auth_method"],
        hd=domains[0] if tenant["auth_method"] == "google" and len(domains) == 1 else None,
        personal=tenant["slug"] == settings.personal_tenant,
    )


@router.post("/password")
def password(body: PasswordIn) -> TokensOut:
    if not _password_limit.allow(body.email.lower()):
        tenant = _discover(body.organization, body.email)
        record_auth_failure(tenant["tenant_id"] if tenant else None, body.email, "too_many_attempts", "password")
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY)
    tenant = _discover(body.organization, body.email)
    if tenant is None or tenant["auth_method"] != "password" or not tenant["idp_tenant_id"]:
        record_auth_failure(None, body.email, "unknown_organization", "password")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, SIGN_IN_FAILED)
    try:
        tokens = identity_platform.sign_in(tenant["idp_tenant_id"], body.email.strip(), body.password)
    except identity_platform.SignInFailed:
        # Every wrong password counts (the per-email rate limit caps how many can be logged).
        record_auth_failure(tenant["tenant_id"], body.email, "wrong_password_or_inactive", "password", dedupe=False)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, SIGN_IN_FAILED) from None
    except identity_platform.IdentityPlatformError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in is temporarily unavailable.") from None
    return TokensOut(id_token=tokens.id_token, refresh_token=tokens.refresh_token, expires_in=tokens.expires_in)


@router.post("/refresh")
def refresh(body: RefreshIn) -> TokensOut:
    try:
        tokens = identity_platform.refresh(body.refresh_token)
    except identity_platform.SignInFailed:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired") from None
    except identity_platform.IdentityPlatformError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in is temporarily unavailable.") from None
    return TokensOut(id_token=tokens.id_token, refresh_token=tokens.refresh_token, expires_in=tokens.expires_in)


@router.post("/forgot", status_code=status.HTTP_202_ACCEPTED)
def forgot(body: DiscoverIn) -> dict[str, str]:
    """Always 202: the reset email is only sent when the organization uses password sign-in."""
    message = {"detail": "If the account exists, a reset link is on its way."}
    if not _email_limit.allow(body.email.lower()):
        return message
    tenant = _discover(body.organization, body.email)
    if tenant and tenant["auth_method"] == "password" and tenant["idp_tenant_id"]:
        try:
            identity_platform.send_password_email(tenant["idp_tenant_id"], body.email.strip(), org_name=tenant["name"])
        except identity_platform.IdentityPlatformError:
            pass
    return message


_reset_limit = RateLimiter(limit=20, window_s=15 * 60)


def _organization_of(idp_tenant_id: str) -> str | None:
    from odn.config import settings

    with system_transaction() as conn:
        row = conn.execute(
            "SELECT slug FROM odn_resolve_identity(%s, %s, NULL)",
            (f"https://securetoken.google.com/{settings.idp_project}", idp_tenant_id),
        ).fetchone()
    return row["slug"] if row else None


def _link_owner(idp_tenant_id: str, email: str) -> ResetOut:
    from odn.config import settings

    org = _organization_of(idp_tenant_id)
    if org is not None and org == settings.personal_tenant:
        return ResetOut(email=email, personal=True)
    return ResetOut(email=email, organization=org)


@router.post("/reset")
def reset(body: ResetIn) -> ResetOut:
    """Branded set-password page: without new_password only checks the emailed link, with it sets the password."""
    if not _reset_limit.allow(body.oob_code[:64]):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY)
    try:
        if body.new_password is None:
            email = identity_platform.check_reset_code(body.tenant_id, body.oob_code)
        else:
            email = identity_platform.confirm_reset(body.tenant_id, body.oob_code, body.new_password)
    except identity_platform.ResetLinkInvalid as exc:
        raise HTTPException(status.HTTP_410_GONE, f"{str(exc).capitalize()}. Ask for a new one.") from None
    except identity_platform.WeakPassword as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{str(exc).capitalize()}.") from None
    except identity_platform.IdentityPlatformError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in is temporarily unavailable.") from None
    return _link_owner(body.tenant_id, email)


# Personal accounts ------------------------------------------------------------------------------------
# Email + password accounts in the personal tenant's Identity Platform tenant (ODN_PERSONAL_TENANT). Personal
# Google accounts need no endpoint: they use the OIDC redirect and land in the issuer's '*' fallback tenant.

PERSONAL_UNAVAILABLE = "Personal accounts aren't available yet. Use Google or your enterprise account."
CHECK_INBOX = "Check your inbox: we've sent a link to set your password and finish creating your account."
NOT_VERIFIED = "Finish creating your account with the link we emailed you, or use “Forgot password?” for a new one."

_signup_limit = RateLimiter(limit=5, window_s=60 * 60)


class PersonalEmailIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class PersonalPasswordIn(PersonalEmailIn):
    password: str = Field(min_length=1, max_length=256)


class SignUpIn(PersonalEmailIn):
    name: str | None = Field(default=None, max_length=200)


def _personal_tenant() -> dict:
    from odn.config import settings

    if not (settings.personal_tenant and identity_platform.enabled()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, PERSONAL_UNAVAILABLE)
    with system_transaction() as conn:
        row = conn.execute("SELECT * FROM odn_personal_tenant(%s)", (settings.personal_tenant,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PERSONAL_UNAVAILABLE)
    return row


def _email_verified(id_token: str) -> bool:
    # Read without signature checks: the token came straight from Identity Platform over TLS, and the API
    # verifies it on every request anyway.
    try:
        return jwt.decode(id_token, options={"verify_signature": False}).get("email_verified") is True
    except jwt.PyJWTError:
        return False


@router.post("/personal/signup", status_code=status.HTTP_202_ACCEPTED)
def personal_signup(body: SignUpIn) -> dict[str, str]:
    """Like an invite: the account starts with an unusable password and whoever opens the emailed link sets it.

    New and existing emails get the same answer and the same email (no enumeration, no pre-registration takeover).
    """
    _domain(body.email, "email address")
    email = body.email.strip().lower()
    if not _signup_limit.allow(email):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY)
    tenant = _personal_tenant()
    if _email_limit.allow(email):
        try:
            identity_platform.invite(
                tenant["idp_tenant_id"], email, display_name=(body.name or "").strip() or None, signup=True
            )
        except identity_platform.IdentityPlatformError:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-up is temporarily unavailable.") from None
    return {"detail": CHECK_INBOX}


@router.post("/personal/password")
def personal_password(body: PersonalPasswordIn) -> TokensOut:
    email = body.email.strip().lower()
    tenant = _personal_tenant()
    if not _password_limit.allow(email):
        record_auth_failure(tenant["tenant_id"], email, "too_many_attempts", "password")
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY)
    try:
        tokens = identity_platform.sign_in(tenant["idp_tenant_id"], email, body.password)
    except identity_platform.SignInFailed:
        record_auth_failure(tenant["tenant_id"], email, "wrong_password_or_inactive", "password", dedupe=False)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, SIGN_IN_FAILED) from None
    except identity_platform.IdentityPlatformError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in is temporarily unavailable.") from None
    if not _email_verified(tokens.id_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_VERIFIED)
    return TokensOut(id_token=tokens.id_token, refresh_token=tokens.refresh_token, expires_in=tokens.expires_in)


@router.post("/personal/forgot", status_code=status.HTTP_202_ACCEPTED)
def personal_forgot(body: PersonalEmailIn) -> dict[str, str]:
    message = {"detail": "If the account exists, a reset link is on its way."}
    email = body.email.strip().lower()
    tenant = _personal_tenant()
    if _email_limit.allow(email):
        try:
            identity_platform.send_password_email(tenant["idp_tenant_id"], email)
        except identity_platform.IdentityPlatformError:
            pass
    return message
