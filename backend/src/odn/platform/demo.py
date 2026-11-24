"""Live demo check-in: name + email (not verified) -> a demo user in the demo tenant and a token signed by us.

The same email within `demo_account_days` of its demo user's creation reopens that user (and its chats); after
that the old user is retired and the email starts fresh. Emails of the tenant's real accounts (admins) are
refused. Tokens are verified like any other issuer's (odn.tenancy.oidc, DEMO_ISSUER) and expire no later than
the demo user does.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import timedelta

import jwt
import psycopg

from odn.config import settings
from odn.db import system_transaction, tenant_transaction
from odn.platform.collibra.token import ALGORITHM, signing_key
from odn.tenancy.oidc import DEMO_AUDIENCE, DEMO_ISSUER


class DemoUnavailable(Exception):
    pass


class EmailReserved(Exception):
    """The email belongs to one of the demo tenant's real accounts."""


class DemoDisabled(Exception):
    """An admin disabled this email's current demo user."""


@dataclass(frozen=True)
class DemoSession:
    token: str
    expires_in: int


def _demo_tenant() -> uuid.UUID:
    if not settings.demo_tenant:
        raise DemoUnavailable("demo is not configured")
    with system_transaction() as conn:
        row = conn.execute("SELECT * FROM odn_demo_tenant(%s, %s)", (DEMO_ISSUER, settings.demo_tenant)).fetchone()
    if row is None:
        raise DemoUnavailable("demo tenant is not provisioned")
    return row["tenant_id"]


def _demo_user(conn: psycopg.Connection, tenant_id: uuid.UUID, name: str, email: str) -> dict:
    window = timedelta(days=settings.demo_account_days)
    user = conn.execute(
        "SELECT id, issuer, subject, status, created_at, created_at > now() - %s AS current "
        "FROM users WHERE lower(email) = %s",
        (window, email),
    ).fetchone()
    if user is not None and user["issuer"] != DEMO_ISSUER:
        raise EmailReserved(email)
    if user is not None and user["current"]:
        if user["status"] != "active":
            raise DemoDisabled(email)
        conn.execute("UPDATE users SET display_name = %s, last_login_at = now() WHERE id = %s", (name, user["id"]))
        return user
    if user is not None:
        # Expired: keep its history attached to a retired row, free the email for a fresh demo user.
        conn.execute("UPDATE users SET email = NULL, status = 'disabled' WHERE id = %s", (user["id"],))
    return conn.execute(
        """
        INSERT INTO users (tenant_id, issuer, subject, email, display_name, last_login_at)
        VALUES (%s, %s, %s, %s, %s, now())
        RETURNING id, subject, created_at
        """,
        (tenant_id, DEMO_ISSUER, uuid.uuid4().hex, email, name),
    ).fetchone()


def check_in(name: str, email: str) -> DemoSession:
    tenant_id = _demo_tenant()
    email = email.strip().lower()
    with tenant_transaction(tenant_id) as conn:
        user = _demo_user(conn, tenant_id, name, email)
        conn.execute(
            "INSERT INTO demo_checkins (tenant_id, user_id, name, email) VALUES (%s, %s, %s, %s)",
            (tenant_id, user["id"], name, email),
        )
    now = int(time.time())
    account_end = int(user["created_at"].timestamp()) + settings.demo_account_days * 86400
    exp = min(now + settings.demo_session_hours * 3600, account_end)
    key = signing_key()
    claims = {
        "iss": DEMO_ISSUER, "aud": DEMO_AUDIENCE, "sub": user["subject"], "tenant": settings.demo_tenant,
        "name": name, "email": email, "email_verified": False, "iat": now, "exp": exp,
    }
    token = jwt.encode(claims, key.private, algorithm=ALGORITHM, headers={"kid": key.kid})
    return DemoSession(token=token, expires_in=exp - now)
