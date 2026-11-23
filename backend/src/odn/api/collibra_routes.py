"""Collibra catalog API (/v1/collibra/*) and EasyQuery's public JWKS (docs/collibra_int_phase5_plan.md).

Every call runs as the signed-in user: the client signs a short-lived token for that user's verified work email,
and Collibra applies that user's permissions. Environments are configured per tenant (RLS) by the operator CLI.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator, model_validator

from odn.api.admin_routes import record_event
from odn.api.deps import Context, require_admin
from odn.api.ratelimit import RateLimiter
from odn.db import tenant_transaction
from odn.platform.collibra import token as tokens
from odn.platform.collibra.client import CollibraClient, CollibraError, Environment, MatchMode
from odn.tenancy.context import RequestContext

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/collibra")
admin_router = APIRouter(prefix="/v1/admin/collibra")
jwks_router = APIRouter()
Admin = Annotated[RequestContext, Depends(require_admin)]
_limit = RateLimiter(limit=120, window_s=60)

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]


@jwks_router.get("/.well-known/jwks.json", include_in_schema=False)
def jwks(response: Response) -> dict[str, Any]:
    """Public keys for verifying EasyQuery-issued tokens (configured in Collibra as the JWKS URL)."""
    response.headers["Cache-Control"] = "public, max-age=300"
    return tokens.jwks()


_COLUMNS = "env, base_url, access_mode, audience, principal_format, enabled"


def _environments(ctx: RequestContext, include_disabled: bool = False) -> list[dict[str, Any]]:
    with tenant_transaction(ctx.tenant_id) as conn:
        return conn.execute(
            f"SELECT {_COLUMNS} FROM collibra_environments"
            + ("" if include_disabled else " WHERE enabled") + " ORDER BY env"
        ).fetchall()


async def _client(ctx: RequestContext, env: str) -> CollibraClient:
    if not _limit.allow(str(ctx.user_id)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many Collibra requests; slow down.")
    rows = await run_in_threadpool(_environments, ctx)
    row = next((r for r in rows if r["env"] == env), None)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Collibra environment '{env}' is not configured")
    if row["access_mode"] != "token":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Collibra '{env}' uses each user's own browser sign-in: use the collibra_browser_* tools "
            "(EasyQuery Collibra Bridge extension).",
        )
    return CollibraClient(
        Environment(env=row["env"], base_url=row["base_url"], audience=row["audience"],
                    principal_format=row["principal_format"]),
        email=ctx.email,
        email_domain_verified=ctx.email_domain_verified,
    )


async def _run(
    ctx: RequestContext, env: str, op: str, call: Callable[[CollibraClient], Awaitable[dict[str, Any]]]
) -> dict[str, Any]:
    client = await _client(ctx, env)
    try:
        result = await call(client)
    except CollibraError as exc:
        log.info("collibra %s env=%s tenant=%s user=%s -> %s", op, env, ctx.tenant_slug, ctx.user_id, exc.status)
        raise HTTPException(exc.status, str(exc)) from None
    log.info("collibra %s env=%s tenant=%s user=%s ok", op, env, ctx.tenant_slug, ctx.user_id)
    return result


@router.get("/environments")
async def environments(ctx: Context) -> dict[str, Any]:
    rows = await run_in_threadpool(_environments, ctx)
    return {"environments": [
        {"env": r["env"], "base_url": r["base_url"], "access_mode": r["access_mode"]} for r in rows
    ]}


@router.get("/{env}/status")
async def collibra_status(env: str, ctx: Context) -> dict[str, Any]:
    client = await _client(ctx, env)
    try:
        return await client.status()
    except CollibraError as exc:
        # Status is informational: report "not connected" with the reason instead of failing.
        return {"environment": env, "authenticated": False, "username": client.principal, "detail": str(exc)}


@router.get("/{env}/assets")
async def search_assets(
    env: str,
    ctx: Context,
    q: Annotated[str, Query(min_length=1, max_length=200, pattern=r"^[^\x00-\x1f]*\S[^\x00-\x1f]*$")],
    match: MatchMode = "ANYWHERE",
    domain_id: UUID | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> dict[str, Any]:
    return await _run(ctx, env, "search", lambda c: c.search_assets(
        q, match=match, domain_id=str(domain_id) if domain_id else None, limit=limit, offset=offset))


@router.get("/{env}/lookup")
async def lookup(
    env: str,
    ctx: Context,
    q: Annotated[str, Query(min_length=1, max_length=200, pattern=r"^[^\x00-\x1f]*\S[^\x00-\x1f]*$")],
    top: Annotated[int, Query(ge=1, le=10)] = 5,
) -> dict[str, Any]:
    return await _run(ctx, env, "lookup", lambda c: c.lookup(q, top=top))


@router.get("/{env}/assets/{asset_id}")
async def get_asset(env: str, asset_id: UUID, ctx: Context) -> dict[str, Any]:
    return await _run(ctx, env, "asset", lambda c: c.get_asset(str(asset_id)))


@router.get("/{env}/assets/{asset_id}/attributes")
async def get_attributes(env: str, asset_id: UUID, ctx: Context, limit: Limit = 20, offset: Offset = 0) -> dict[str, Any]:
    return await _run(ctx, env, "attributes", lambda c: c.get_attributes(str(asset_id), limit=limit, offset=offset))


@router.get("/{env}/assets/{asset_id}/relations")
async def get_relations(
    env: str, asset_id: UUID, ctx: Context,
    direction: Literal["outgoing", "incoming"] = "outgoing", limit: Limit = 20, offset: Offset = 0,
) -> dict[str, Any]:
    return await _run(ctx, env, "relations", lambda c: c.get_relations(
        str(asset_id), direction=direction, limit=limit, offset=offset))


@router.get("/{env}/assets/{asset_id}/fields")
async def get_api_fields(
    env: str, asset_id: UUID, ctx: Context,
    limit: Annotated[int, Query(ge=1, le=20)] = 20, offset: Offset = 0,
) -> dict[str, Any]:
    return await _run(ctx, env, "fields", lambda c: c.get_api_fields(str(asset_id), limit=limit, offset=offset))


class DescribeIn(BaseModel):
    asset_ids: list[UUID] = Field(min_length=1, max_length=20)


@router.post("/{env}/describe")
async def describe_assets(env: str, body: DescribeIn, ctx: Context) -> dict[str, Any]:
    ids = list(dict.fromkeys(str(a) for a in body.asset_ids))
    return await _run(ctx, env, "describe", lambda c: c.describe_assets(ids))


# -- admin: Collibra as an organization data source ------------------------------------------------------------


class EnvironmentIn(BaseModel):
    base_url: str = Field(min_length=9, max_length=200)
    access_mode: Literal["browser", "token"] = "browser"
    audience: str | None = Field(default=None, max_length=200)
    principal_format: Literal["email", "email_local_part"] = "email"
    enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def _origin(cls, value: str) -> str:
        parts = urlsplit(value.strip().rstrip("/"))
        if parts.scheme != "https" or not parts.hostname or parts.path or parts.query or parts.username:
            raise ValueError("use the Collibra address, e.g. https://yourcompany.collibra.com")
        return f"https://{parts.netloc.lower()}"

    @model_validator(mode="after")
    def _token_needs_audience(self) -> EnvironmentIn:
        self.audience = (self.audience or "").strip() or None
        if self.access_mode == "token" and not self.audience:
            raise ValueError("token mode needs the JWT audience configured in Collibra")
        return self


@admin_router.get("/environments")
def admin_environments(ctx: Admin) -> dict[str, Any]:
    """All of the organization's Collibra environments, plus what Collibra needs for token mode."""
    return {
        "environments": [dict(r) for r in _environments(ctx, include_disabled=True)],
        "token_issuer": tokens.issuer(),
        "jwks_url": f"{tokens.issuer()}/.well-known/jwks.json",
    }


@admin_router.put("/environments/{env}")
def save_environment(env: str, body: EnvironmentIn, ctx: Admin) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,15}", env):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "name: lowercase letters, digits, - or _, e.g. prd")
    with tenant_transaction(ctx.tenant_id) as conn:
        row = conn.execute(
            "INSERT INTO collibra_environments (tenant_id, env, base_url, access_mode, audience, principal_format, enabled) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (tenant_id, env) DO UPDATE SET base_url = EXCLUDED.base_url, "
            "access_mode = EXCLUDED.access_mode, audience = EXCLUDED.audience, principal_format = EXCLUDED.principal_format, "
            f"enabled = EXCLUDED.enabled, updated_at = now() RETURNING {_COLUMNS}",
            (ctx.tenant_id, env, body.base_url, body.access_mode, body.audience, body.principal_format, body.enabled),
        ).fetchone()
        record_event(conn, ctx, "collibra.environment_saved", env, base_url=body.base_url,
                     access_mode=body.access_mode, enabled=body.enabled)
    assert row is not None
    return dict(row)


@admin_router.delete("/environments/{env}", status_code=status.HTTP_204_NO_CONTENT)
def delete_environment(env: str, ctx: Admin) -> None:
    with tenant_transaction(ctx.tenant_id) as conn:
        cur = conn.execute("DELETE FROM collibra_environments WHERE env = %s", (env,))
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Collibra environment '{env}' is not configured")
        record_event(conn, ctx, "collibra.environment_removed", env)
