"""Collibra catalog API (/v1/collibra/*) and QueryMuse's public JWKS (docs/collibra_int_phase5_plan.md).

Every call runs as the signed-in user: the client signs a short-lived token for that user's verified work email,
and Collibra applies that user's permissions. Environments are configured per tenant (RLS) by the operator CLI.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from odn.api.deps import Context
from odn.api.ratelimit import RateLimiter
from odn.db import tenant_transaction
from odn.platform.collibra import token as tokens
from odn.platform.collibra.client import CollibraClient, CollibraError, Environment, MatchMode
from odn.tenancy.context import RequestContext

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/collibra")
jwks_router = APIRouter()
_limit = RateLimiter(limit=120, window_s=60)

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]


@jwks_router.get("/.well-known/jwks.json", include_in_schema=False)
def jwks(response: Response) -> dict[str, Any]:
    """Public keys for verifying QueryMuse-issued tokens (configured in Collibra as the JWKS URL)."""
    response.headers["Cache-Control"] = "public, max-age=300"
    return tokens.jwks()


def _environments(ctx: RequestContext) -> list[dict[str, Any]]:
    with tenant_transaction(ctx.tenant_id) as conn:
        return conn.execute(
            "SELECT env, base_url, audience, principal_format FROM collibra_environments WHERE enabled ORDER BY env"
        ).fetchall()


async def _client(ctx: RequestContext, env: str) -> CollibraClient:
    if not _limit.allow(str(ctx.user_id)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many Collibra requests; slow down.")
    rows = await run_in_threadpool(_environments, ctx)
    row = next((r for r in rows if r["env"] == env), None)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Collibra environment '{env}' is not configured")
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
    return {"environments": [{"env": r["env"], "base_url": r["base_url"]} for r in rows]}


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
