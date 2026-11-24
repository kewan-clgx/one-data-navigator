"""Resolve a verified token into the tenant/user request context (tenant never comes from client input).

Rules (docs/Tenant_Isolation_User_system.md §4.2): the tenant comes from the token's (issuer, tenant claim)
via the tenant directory; the tenant must be active; for tenants with domains the email must be verified and
on one of them; the issuer's fallback tenant may restrict emails; disabled users are refused; invited users
are linked by verified email; otherwise `join_policy = domain` admits new members and `invite` refuses them.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Literal
from uuid import UUID

import psycopg

from odn.db import system_transaction, tenant_transaction
from odn.platform.governance.auth_events import record_auth_failure
from odn.tenancy.oidc import VerifiedToken, email_allowed, parse_email_allowlist

Role = Literal["member", "data_lead", "admin"]


class UnknownTenant(Exception):
    pass


class AccessDenied(Exception):
    """The tenant exists but this identity may not use it (message is safe to show)."""

    def __init__(self, message: str, reason: str = "refused") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class RequestContext:
    tenant_id: UUID
    user_id: UUID
    subject: str
    role: Role
    tenant_slug: str = ""
    email: str | None = None
    # True when the email is verified and on one of the tenant's verified domains (gate for identity assertions
    # to other systems, e.g. Collibra tokens).
    email_domain_verified: bool = False
    # Per-tenant cap on bytes a single warehouse query may scan (tenants.max_bytes_billed); None = global limit.
    max_bytes_billed: int | None = None


@cache
def _fallback_allowlist() -> frozenset[str]:
    from odn.config import settings

    return parse_email_allowlist(settings.oidc_default_tenant_emails)


def _method(token: VerifiedToken) -> str:
    return "password" if token.issuer.startswith("https://securetoken.google.com/") else "sso"


def _email_domain(email: str | None) -> str | None:
    return email.rsplit("@", 1)[1].lower() if email and "@" in email else None


def resolve_context(token: VerifiedToken) -> RequestContext:
    with system_transaction() as conn:
        tenant = conn.execute(
            "SELECT * FROM odn_resolve_identity(%s, %s, %s)",
            (token.issuer, token.external_id, _email_domain(token.email)),
        ).fetchone()
    method = _method(token)
    if tenant is None:
        record_auth_failure(None, token.email, "unknown_organization", method)
        raise UnknownTenant(token.external_id)
    tenant_id: UUID = tenant["tenant_id"]

    def deny(reason: str, message: str) -> AccessDenied:
        # Domain mismatches are not attributed: the email may belong to someone outside this organization.
        record_auth_failure(None if reason == "domain_not_allowed" else tenant_id, token.email, reason, method)
        return AccessDenied(message)

    if tenant["status"] != "active":
        raise deny("organization_suspended", "this organization is suspended")
    # Password accounts prove the email through an emailed link (invite, reset or personal sign-up verification).
    if method == "password" and not token.email_verified:
        raise deny("email_not_verified", "verify your email address first, using the link we emailed you")
    if tenant["has_domains"] and not (token.email_verified and tenant["domain_ok"]):
        raise deny("domain_not_allowed", "your email is not on one of this organization's verified domains")
    if tenant["fallback"] and not email_allowed(token.email, token.email_verified, _fallback_allowlist()):
        raise deny("email_not_allowed", "this account is not allowed to sign in")

    try:
        with tenant_transaction(tenant_id) as conn:
            user = _sign_in(conn, tenant_id, token, tenant["join_policy"])
    except AccessDenied as exc:
        raise deny(getattr(exc, "reason", "refused"), str(exc)) from None
    if user["status"] == "disabled":
        raise deny("account_disabled", "your account is disabled; contact your organization's admin")
    return RequestContext(
        tenant_id=tenant_id,
        user_id=user["id"],
        subject=token.subject,
        role=user["role"],
        tenant_slug=tenant["slug"],
        email=token.email,
        email_domain_verified=bool(tenant["has_domains"] and token.email_verified and tenant["domain_ok"]),
        max_bytes_billed=tenant["max_bytes_billed"],
    )


_USER = "id, role, status"


def _sign_in(conn: psycopg.Connection, tenant_id: UUID, token: VerifiedToken, join_policy: str) -> dict:
    user = conn.execute(
        f"""
        UPDATE users SET email = coalesce(%s, email), display_name = coalesce(%s, display_name), last_login_at = now()
        WHERE tenant_id = %s AND issuer IS NOT DISTINCT FROM %s AND subject = %s AND status <> 'disabled'
          AND (last_login_at IS NULL OR last_login_at < now() - interval '5 minutes'
               OR email IS DISTINCT FROM coalesce(%s, email) OR display_name IS DISTINCT FROM coalesce(%s, display_name))
        RETURNING {_USER}
        """,
        (token.email, token.name, tenant_id, token.issuer, token.subject, token.email, token.name),
    ).fetchone() or conn.execute(
        f"SELECT {_USER} FROM users WHERE tenant_id = %s AND issuer IS NOT DISTINCT FROM %s AND subject = %s",
        (tenant_id, token.issuer, token.subject),
    ).fetchone()
    if user is not None:
        return user

    if token.email and token.email_verified:
        invited = conn.execute(
            f"""
            UPDATE users SET issuer = %s, subject = %s, status = 'active', display_name = coalesce(%s, display_name),
                             last_login_at = now()
            WHERE tenant_id = %s AND lower(email) = lower(%s) AND status = 'invited' AND subject IS NULL
            RETURNING {_USER}
            """,
            (token.issuer, token.subject, token.name, tenant_id, token.email),
        ).fetchone()
        if invited is not None:
            return invited

    if join_policy != "domain":
        raise AccessDenied("you have not been invited to this organization; ask its admin for an invite", "not_invited")
    try:
        with conn.transaction():
            created = conn.execute(
                f"""
                INSERT INTO users (tenant_id, issuer, subject, email, display_name, last_login_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (tenant_id, issuer, subject) WHERE subject IS NOT NULL
                  DO UPDATE SET last_login_at = now()
                RETURNING {_USER}
                """,
                (tenant_id, token.issuer, token.subject, token.email, token.name),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        # Same email already registered under another sign-in method in this tenant.
        raise AccessDenied("this email already has an account with a different sign-in method", "other_sign_in_method") from None
    assert created is not None
    return created
