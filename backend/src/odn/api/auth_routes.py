"""Unauthenticated sign-in endpoints, called server-to-server by the Next.js BFF (not exposed via its proxy).

The typed organization and email only route the sign-in; the tenant a user ends up in is always derived
from the verified token (odn.tenancy.context). Responses never reveal whether an account exists.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from odn.api.ratelimit import RateLimiter
from odn.db import system_transaction
from odn.platform import identity_platform

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


class DiscoverOut(BaseModel):
    method: Literal["google", "oidc", "password"]
    organization: str
    hd: str | None = None


class TokensOut(BaseModel):
    id_token: str
    refresh_token: str
    expires_in: int


def _domain(email: str) -> str:
    local, _, domain = email.strip().lower().rpartition("@")
    if not local or "." not in domain:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "enter a valid work email")
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


@router.post("/password")
def password(body: PasswordIn) -> TokensOut:
    if not _password_limit.allow(body.email.lower()):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY)
    tenant = _discover(body.organization, body.email)
    if tenant is None or tenant["auth_method"] != "password" or not tenant["idp_tenant_id"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, SIGN_IN_FAILED)
    try:
        tokens = identity_platform.sign_in(tenant["idp_tenant_id"], body.email.strip(), body.password)
    except identity_platform.SignInFailed:
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
            identity_platform.send_password_email(tenant["idp_tenant_id"], body.email.strip())
        except identity_platform.IdentityPlatformError:
            pass
    return message
