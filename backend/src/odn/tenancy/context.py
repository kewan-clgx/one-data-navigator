"""Resolve a verified token into the tenant/user request context (tenant never comes from client input)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from odn.db import system_transaction, tenant_transaction
from odn.tenancy.oidc import VerifiedToken

Role = Literal["member", "data_lead", "admin"]


class UnknownTenant(Exception):
    pass


@dataclass(frozen=True)
class RequestContext:
    tenant_id: UUID
    user_id: UUID
    subject: str
    role: Role


def resolve_context(token: VerifiedToken) -> RequestContext:
    with system_transaction() as conn:
        row = conn.execute(
            "SELECT odn_resolve_tenant(%s, %s) AS id", (token.tenant_slug, token.issuer)
        ).fetchone()
    tenant_id = row["id"] if row else None
    if tenant_id is None:
        raise UnknownTenant(token.tenant_slug)

    with tenant_transaction(tenant_id) as conn:
        user = conn.execute(
            """
            INSERT INTO users (tenant_id, subject, email, display_name, last_login_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (tenant_id, subject) DO UPDATE
              SET email = EXCLUDED.email, display_name = EXCLUDED.display_name, last_login_at = now()
            RETURNING id, role
            """,
            (tenant_id, token.subject, token.email, token.name),
        ).fetchone()
    assert user is not None
    return RequestContext(tenant_id=tenant_id, user_id=user["id"], subject=token.subject, role=user["role"])
