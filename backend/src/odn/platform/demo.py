"""Live demo check-in: name + email (not verified) -> a demo user in the demo tenant and a token signed by us.

Check-in always starts instantly; only the inbox owner gets back to older chats:
- the same browser presents its device token (issued at the previous check-in) -> that demo user reopens;
- otherwise, if the email already has a current demo user, a fresh demo user starts (no email on it) and the
  inbox gets a one-hour link that reopens the older one;
- otherwise the email gets its own demo user (a new visitor).
A demo user lasts `demo_account_days` from its creation; after that the email starts fresh. Emails of the tenant's
real accounts (admins) are refused. Session tokens are verified like any other issuer's (odn.tenancy.oidc,
DEMO_ISSUER); device and link tokens use other audiences, so they never work as API bearer tokens.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import jwt
import psycopg

from odn.config import settings
from odn.db import system_transaction, tenant_transaction
from odn.platform.collibra.token import ALGORITHM, signing_key
from odn.tenancy.oidc import DEMO_AUDIENCE, DEMO_ISSUER

DEVICE_AUDIENCE = "odn-demo-device"
LINK_AUDIENCE = "odn-demo-link"
LINK_TTL_S = 3600

Kind = Literal["new", "returning", "fresh", "link"]


class DemoUnavailable(Exception):
    pass


class EmailReserved(Exception):
    """The email belongs to one of the demo tenant's real accounts."""


class DemoDisabled(Exception):
    """An admin disabled this demo user."""


class LinkInvalid(Exception):
    """The emailed link is expired, malformed, or its demo user is gone."""


@dataclass(frozen=True)
class DemoSession:
    token: str
    expires_in: int
    device_token: str
    kind: Kind
    # Set when the email already had demo chats: a one-hour link that reopens them (to email to that inbox).
    link_token: str | None = None


def _demo_tenant() -> uuid.UUID:
    if not settings.demo_tenant:
        raise DemoUnavailable("demo is not configured")
    with system_transaction() as conn:
        row = conn.execute("SELECT * FROM odn_demo_tenant(%s, %s)", (DEMO_ISSUER, settings.demo_tenant)).fetchone()
    if row is None:
        raise DemoUnavailable("demo tenant is not provisioned")
    return row["tenant_id"]


def _sign(claims: dict) -> str:
    key = signing_key()
    return jwt.encode(claims, key.private, algorithm=ALGORITHM, headers={"kid": key.kid})


def _read(token: str, audience: str) -> dict | None:
    try:
        return jwt.decode(token, signing_key().private.public_key(), algorithms=[ALGORITHM], audience=audience,
                          issuer=DEMO_ISSUER, options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        return None


_USER = "id, subject, email, display_name, status, created_at, created_at > now() - %s AS current"


def _window() -> timedelta:
    return timedelta(days=settings.demo_account_days)


def _demo_user_by_subject(conn: psycopg.Connection, subject: str) -> dict | None:
    return conn.execute(f"SELECT {_USER} FROM users WHERE issuer = %s AND subject = %s",
                        (_window(), DEMO_ISSUER, subject)).fetchone()


def _create(conn: psycopg.Connection, tenant_id: uuid.UUID, name: str, email: str | None) -> dict:
    return conn.execute(
        f"""
        INSERT INTO users (tenant_id, issuer, subject, email, display_name, last_login_at)
        VALUES (%s, %s, %s, %s, %s, now())
        RETURNING {_USER}
        """,
        (tenant_id, DEMO_ISSUER, uuid.uuid4().hex, email, name, _window()),
    ).fetchone()


def _reopen(conn: psycopg.Connection, user: dict, name: str | None) -> dict:
    if user["status"] != "active":
        raise DemoDisabled(user["subject"])
    conn.execute("UPDATE users SET display_name = coalesce(%s, display_name), last_login_at = now() WHERE id = %s",
                 (name, user["id"]))
    return user


def _session(user: dict, email: str, name: str, kind: Kind, link_for: dict | None = None) -> DemoSession:
    now = int(time.time())
    account_end = int(user["created_at"].timestamp()) + settings.demo_account_days * 86400
    exp = min(now + settings.demo_session_hours * 3600, account_end)
    base = {"iss": DEMO_ISSUER, "sub": user["subject"], "email": email, "iat": now}
    # The session carries only the user's own email: a fresh demo user (the email had chats) has none, and sign-in
    # copies the token's email onto the user row.
    session_claims = {**base, "aud": DEMO_AUDIENCE, "tenant": settings.demo_tenant, "name": name,
                      "email_verified": False, "exp": exp}
    if user["email"] is None:
        session_claims.pop("email")
    token = _sign(session_claims)
    device = _sign({**base, "aud": DEVICE_AUDIENCE, "exp": account_end})
    link = None
    if link_for is not None:
        link = _sign({"iss": DEMO_ISSUER, "aud": LINK_AUDIENCE, "sub": link_for["subject"], "email": email,
                      "iat": now, "exp": now + LINK_TTL_S})
    return DemoSession(token=token, expires_in=exp - now, device_token=device, kind=kind, link_token=link)


def _record(conn: psycopg.Connection, tenant_id: uuid.UUID, user: dict, name: str, email: str, kind: Kind) -> None:
    conn.execute(
        "INSERT INTO demo_checkins (tenant_id, user_id, name, email, kind) VALUES (%s, %s, %s, %s, %s)",
        (tenant_id, user["id"], name, email, kind),
    )


def check_in(name: str, email: str, device_token: str | None = None) -> DemoSession:
    tenant_id = _demo_tenant()
    email = email.strip().lower()
    device = _read(device_token, DEVICE_AUDIENCE) if device_token else None
    with tenant_transaction(tenant_id) as conn:
        owner = conn.execute(f"SELECT {_USER}, issuer FROM users WHERE lower(email) = %s",
                             (_window(), email)).fetchone()
        if owner is not None and owner["issuer"] != DEMO_ISSUER:
            raise EmailReserved(email)
        mine = _demo_user_by_subject(conn, device["sub"]) if device and device.get("email") == email else None
        link_for = None
        if mine is not None and mine["current"]:
            user, kind = _reopen(conn, mine, name), "returning"
        elif owner is not None and owner["current"]:
            if owner["status"] != "active":
                raise DemoDisabled(email)
            user, kind, link_for = _create(conn, tenant_id, name, None), "fresh", owner
        else:
            if owner is not None:
                # Expired: keep its history attached to a retired row, free the email for a new demo user.
                conn.execute("UPDATE users SET email = NULL, status = 'disabled' WHERE id = %s", (owner["id"],))
            user, kind = _create(conn, tenant_id, name, email), "new"
        _record(conn, tenant_id, user, name, email, kind)
    return _session(user, email, name, kind, link_for)


def open_link(link_token: str) -> DemoSession:
    """Reopen the demo user an emailed link points to (within the link's hour and the user's lifetime)."""
    tenant_id = _demo_tenant()
    claims = _read(link_token, LINK_AUDIENCE)
    if claims is None:
        raise LinkInvalid("link expired or invalid")
    with tenant_transaction(tenant_id) as conn:
        user = _demo_user_by_subject(conn, claims["sub"])
        if user is None or not user["current"]:
            raise LinkInvalid("demo account expired")
        user = _reopen(conn, user, None)
        name = user["display_name"] or claims["email"]
        _record(conn, tenant_id, user, name, claims["email"], "link")
    return _session(user, claims["email"], name, "link")


# Upgrade path: a personal account with a verified email can import that email's current demo chats. The agent
# moves the conversations (it owns the session store); these say which demo users qualify and retire them after.

_IMPORTABLE = """
SELECT u.subject FROM users u
WHERE u.issuer = %s AND u.status = 'active' AND u.created_at > now() - %s
  AND (lower(u.email) = %s
       OR EXISTS (SELECT 1 FROM demo_checkins c WHERE c.user_id = u.id AND c.email = %s))
"""


def importable(email: str) -> list[str]:
    """Subjects of the demo users that hold chats started with this (verified) email."""
    tenant_id = _demo_tenant()
    email = email.strip().lower()
    with tenant_transaction(tenant_id) as conn:
        return [r["subject"] for r in conn.execute(_IMPORTABLE, (DEMO_ISSUER, _window(), email, email))]


def retire_imported(email: str, subjects: list[str]) -> int:
    """After the import: the demo users are closed (their chats now live in the personal account)."""
    tenant_id = _demo_tenant()
    allowed = set(importable(email)) & set(subjects)
    if not allowed:
        return 0
    with tenant_transaction(tenant_id) as conn:
        return conn.execute(
            "UPDATE users SET status = 'disabled', email = NULL WHERE issuer = %s AND subject = ANY(%s)",
            (DEMO_ISSUER, list(allowed)),
        ).rowcount
