"""Tenant admin console API (/v1/admin/*): organization profile, users and invites, admin audit trail.

Admins only. All work runs in the caller's tenant RLS context; the directory module enforces the rules.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from odn.api.deps import require_admin
from odn.db import tenant_transaction
from odn.platform.identity_platform import IdentityPlatformError
from odn.tenancy import directory
from odn.tenancy.context import RequestContext

router = APIRouter(prefix="/v1/admin")
Admin = Annotated[RequestContext, Depends(require_admin)]

Role = Literal["member", "data_lead", "admin"]


class InviteIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: Role = "member"


class UserPatch(BaseModel):
    role: Role | None = None
    status: Literal["active", "disabled"] | None = None


def record_event(conn: Any, ctx: RequestContext, action: str, target: str | None, **detail: Any) -> None:
    conn.execute(
        "INSERT INTO admin_events (tenant_id, actor_user_id, action, target, detail) VALUES (%s, %s, %s, %s, %s)",
        (ctx.tenant_id, ctx.user_id, action, target, Jsonb(detail)),
    )


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, directory.DirectoryError):
        code = status.HTTP_404_NOT_FOUND if "not found" in str(exc) else status.HTTP_409_CONFLICT
        return HTTPException(code, str(exc))
    return HTTPException(status.HTTP_502_BAD_GATEWAY, "the sign-in provider could not be reached; try again")


@router.get("/organization")
def organization(ctx: Admin) -> dict[str, Any]:
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        profile = conn.execute("SELECT * FROM odn_tenant_profile()").fetchone() or {}
    return {k: profile.get(k) for k in ("slug", "name", "theme", "auth_method", "join_policy", "domains")}


@router.get("/users")
def users(ctx: Admin) -> list[dict[str, Any]]:
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        return directory.list_users(conn, ctx.tenant_id)


@router.post("/users", status_code=status.HTTP_201_CREATED)
def invite(body: InviteIn, ctx: Admin) -> dict[str, Any]:
    try:
        with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
            user = directory.invite(conn, ctx.tenant_id, body.email, body.role, invited_by=ctx.user_id)
            record_event(conn, ctx, "user.invited", user["email"], role=body.role)
    except (directory.DirectoryError, IdentityPlatformError) as exc:
        raise _fail(exc) from None
    return user


@router.patch("/users/{user_id}")
def update(user_id: UUID, body: UserPatch, ctx: Admin) -> dict[str, Any]:
    if body.role is None and body.status is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "nothing to change")
    if user_id == ctx.user_id and (body.status == "disabled" or (body.role and body.role != "admin")):
        raise HTTPException(status.HTTP_409_CONFLICT, "you cannot remove your own admin access")
    try:
        with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
            user = directory.update_user(conn, ctx.tenant_id, user_id, role=body.role, status=body.status)
            if body.role:
                record_event(conn, ctx, "user.role_changed", user["email"], role=body.role)
            if body.status:
                record_event(conn, ctx, "user.status_changed", user["email"], status=body.status)
    except directory.DirectoryError as exc:
        raise _fail(exc) from None
    return user


@router.post("/users/{user_id}/resend", status_code=status.HTTP_202_ACCEPTED)
def resend(user_id: UUID, ctx: Admin) -> dict[str, str]:
    try:
        with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
            email = directory.resend_invite(conn, ctx.tenant_id, user_id)
            record_event(conn, ctx, "user.invite_resent", email)
    except (directory.DirectoryError, IdentityPlatformError) as exc:
        raise _fail(exc) from None
    return {"detail": "invite sent again"}


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke(user_id: UUID, ctx: Admin) -> None:
    try:
        with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
            email = directory.revoke_invite(conn, ctx.tenant_id, user_id)
            record_event(conn, ctx, "user.invite_revoked", email)
    except directory.DirectoryError as exc:
        raise _fail(exc) from None


@router.get("/events")
def events(ctx: Admin, limit: int = 100) -> list[dict[str, Any]]:
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        return [
            dict(r)
            for r in conn.execute(
                """
                SELECT e.occurred_at, e.action, e.target, e.detail, u.email AS actor
                FROM admin_events e LEFT JOIN users u ON u.id = e.actor_user_id
                WHERE e.tenant_id = %s ORDER BY e.occurred_at DESC LIMIT %s
                """,
                (ctx.tenant_id, max(1, min(limit, 500))),
            )
        ]
