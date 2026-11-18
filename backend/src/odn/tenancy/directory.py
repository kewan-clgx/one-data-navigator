"""Tenant user directory: invites, roles and status. Shared by the operator CLI and the admin API.

Every function takes a connection whose RLS context is already set to `tenant_id` and also filters by it
explicitly (the owner role used by the CLI may bypass RLS). Database triggers enforce the admin rules
(admin email on a verified domain; never remove the last active admin); their errors become `DirectoryError`.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import psycopg

from odn.platform import identity_platform

log = logging.getLogger(__name__)

ROLES = ("member", "data_lead", "admin")
STATUSES = ("active", "disabled")

_USER_COLUMNS = "id, email, display_name, role, status, issuer IS NOT NULL AS signed_in, invited_at, last_login_at"


class DirectoryError(Exception):
    """A rule was broken (message is safe to show to an admin)."""


def _profile(conn: psycopg.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM odn_tenant_profile()").fetchone()
    if row is None:
        raise DirectoryError("tenant context is not set")
    return dict(row)


def _normalize_email(email: str) -> str:
    email = email.strip().lower()
    local, sep, domain = email.partition("@")
    if not sep or not local or "." not in domain or " " in email:
        raise DirectoryError("enter a valid email address")
    return email


def list_users(conn: psycopg.Connection, tenant_id: UUID) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            f"SELECT {_USER_COLUMNS} FROM users WHERE tenant_id = %s "
            "ORDER BY role = 'admin' DESC, lower(coalesce(email, '')), created_at",
            (tenant_id,),
        )
    ]


def invite(
    conn: psycopg.Connection, tenant_id: UUID, email: str, role: str = "member", invited_by: UUID | None = None
) -> dict[str, Any]:
    """Create (or refresh) an invite. Password tenants also get an Identity Platform set-password email."""
    if role not in ROLES:
        raise DirectoryError(f"role must be one of {', '.join(ROLES)}")
    email = _normalize_email(email)
    profile = _profile(conn)
    domains = profile["domains"] or []
    if domains and email.rsplit("@", 1)[1] not in domains:
        raise DirectoryError(f"email must be on {' or '.join('@' + d for d in domains)}")
    existing = conn.execute(
        "SELECT id, status FROM users WHERE tenant_id = %s AND lower(email) = %s", (tenant_id, email)
    ).fetchone()
    if existing and existing["status"] != "invited":
        raise DirectoryError("this person already has an account")
    try:
        with conn.transaction():
            if existing:
                row = conn.execute(
                    f"UPDATE users SET role = %s, invited_by = %s, invited_at = now() WHERE id = %s RETURNING {_USER_COLUMNS}",
                    (role, invited_by, existing["id"]),
                ).fetchone()
            else:
                row = conn.execute(
                    "INSERT INTO users (tenant_id, email, role, status, invited_by, invited_at) "
                    f"VALUES (%s, %s, %s, 'invited', %s, now()) RETURNING {_USER_COLUMNS}",
                    (tenant_id, email, role, invited_by),
                ).fetchone()
    except psycopg.errors.CheckViolation as exc:
        raise DirectoryError(_rule_message(exc)) from None
    if profile["auth_method"] == "password":
        if not profile["idp_tenant_id"]:
            raise DirectoryError("this organization has no password sign-in configured")
        identity_platform.invite(profile["idp_tenant_id"], email)
    assert row is not None
    return dict(row)


def update_user(
    conn: psycopg.Connection, tenant_id: UUID, user_id: UUID, *, role: str | None = None, status: str | None = None
) -> dict[str, Any]:
    if role is not None and role not in ROLES:
        raise DirectoryError(f"role must be one of {', '.join(ROLES)}")
    if status is not None and status not in STATUSES:
        raise DirectoryError(f"status must be one of {', '.join(STATUSES)}")
    current = conn.execute(
        "SELECT email, status FROM users WHERE tenant_id = %s AND id = %s", (tenant_id, user_id)
    ).fetchone()
    if current is None:
        raise DirectoryError("user not found")
    if status is not None and current["status"] == "invited":
        raise DirectoryError("revoke the invite instead")
    try:
        with conn.transaction():
            row = conn.execute(
                f"""
                UPDATE users SET role = coalesce(%s, role), status = coalesce(%s, status)
                WHERE tenant_id = %s AND id = %s RETURNING {_USER_COLUMNS}
                """,
                (role, status, tenant_id, user_id),
            ).fetchone()
    except psycopg.errors.CheckViolation as exc:
        raise DirectoryError(_rule_message(exc)) from None
    if status is not None and status != current["status"]:
        _sync_idp_disabled(conn, current["email"], status == "disabled")
    assert row is not None
    return dict(row)


def revoke_invite(conn: psycopg.Connection, tenant_id: UUID, user_id: UUID) -> str:
    """Delete a pending invite; returns its email."""
    row = conn.execute(
        "DELETE FROM users WHERE tenant_id = %s AND id = %s AND status = 'invited' RETURNING email", (tenant_id, user_id)
    ).fetchone()
    if row is None:
        raise DirectoryError("no pending invite for this user")
    return row["email"]


def resend_invite(conn: psycopg.Connection, tenant_id: UUID, user_id: UUID) -> str:
    """Send the invite again; returns its email."""
    row = conn.execute(
        "SELECT email, role FROM users WHERE tenant_id = %s AND id = %s AND status = 'invited'", (tenant_id, user_id)
    ).fetchone()
    if row is None:
        raise DirectoryError("no pending invite for this user")
    invite(conn, tenant_id, row["email"], row["role"])
    return row["email"]


def _sync_idp_disabled(conn: psycopg.Connection, email: str | None, disabled: bool) -> None:
    """Best effort: our users.status already blocks every request; the IdP flag also stops new sign-ins."""
    profile = _profile(conn)
    if profile["auth_method"] != "password" or not profile["idp_tenant_id"] or not email:
        return
    try:
        identity_platform.set_disabled(profile["idp_tenant_id"], email, disabled)
    except Exception as exc:  # noqa: BLE001 - e.g. no admin credentials locally; status already blocks access
        log.warning("could not sync Identity Platform disabled=%s: %s", disabled, type(exc).__name__)


def _rule_message(exc: psycopg.errors.CheckViolation) -> str:
    text = str(exc.diag.message_primary or exc)
    if "last active admin" in text:
        return "an organization needs at least one active admin"
    if "verified domain" in text:
        return "admins must use an email on one of the organization's verified domains"
    return "this change breaks a directory rule"
