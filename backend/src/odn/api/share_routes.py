"""/v1/shares: share a conversation with teammates in the same organization (docs/chat_session_share_plan.md)."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from odn.api.admin_routes import record_event
from odn.api.deps import Context, require_admin
from odn.db import tenant_transaction
from odn.platform.sharing import service, wall
from odn.platform.sharing.notify import notify_recipients, share_url
from odn.tenancy.context import RequestContext

router = APIRouter(prefix="/v1/shares")
admin_router = APIRouter(prefix="/v1/admin/sharing")
# No sign-in: "anyone with the link" shares only (odn_public_share decides), for the page and link previews.
public_router = APIRouter(prefix="/v1/public/shares")
Admin = Annotated[RequestContext, Depends(require_admin)]
ShareId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{22}$")]


def _token(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing token")
    return token


def _http(exc: service.ShareError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


class ShareIn(BaseModel):
    thread_id: str = Field(min_length=1, max_length=200)
    title: str = Field(default="", max_length=200)
    audience: service.Audience = "people"
    recipient_ids: list[UUID] = Field(default_factory=list, max_length=50)
    include_results: bool = True
    expires_days: int = 30
    mode: service.Mode = "conversation"
    turn_id: str | None = Field(default=None, max_length=200)
    artifact_names: list[str] = Field(default_factory=list, max_length=20)
    wall: service.Wall = "none"


class ExtendIn(BaseModel):
    days: int = 30


class WallIn(BaseModel):
    wall: service.Wall


@router.get("/preview")
async def preview(request: Request, ctx: Context, thread_id: Annotated[str, Query(min_length=1, max_length=200)]) -> dict[str, Any]:
    try:
        return await run_in_threadpool(service.preview, ctx, _token(request), thread_id)
    except service.ShareError as exc:
        raise _http(exc) from None


@router.get("/people")
def people(ctx: Context, q: Annotated[str, Query(min_length=1, max_length=100)]) -> list[dict[str, Any]]:
    return service.people(ctx, q)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(body: ShareIn, request: Request, ctx: Context) -> dict[str, Any]:
    try:
        share = await run_in_threadpool(
            service.create, ctx, _token(request), thread_id=body.thread_id, title=body.title, audience=body.audience,
            recipient_ids=body.recipient_ids, include_results=body.include_results, expires_days=body.expires_days,
            mode=body.mode, turn_id=body.turn_id, artifact_names=body.artifact_names, wall=body.wall,
        )
    except service.ShareError as exc:
        raise _http(exc) from None
    owner = await run_in_threadpool(_owner_name, ctx)
    share["emailed"] = await run_in_threadpool(notify_recipients, owner, share["title"], share["id"], share["recipients"])
    share["url"] = share_url(share["id"])
    return share


def _owner_name(ctx: RequestContext) -> str:
    with tenant_transaction(ctx.tenant_id) as conn:
        row = conn.execute("SELECT display_name, email FROM users WHERE id = %s", (ctx.user_id,)).fetchone()
    return (row and (row["display_name"] or row["email"])) or "A teammate"


@router.get("")
def list_shares(ctx: Context, scope: Literal["mine", "with_me", "all"] = "mine", thread_id: str | None = None) -> list[dict[str, Any]]:
    return service.list_shares(ctx, scope, thread_id)


@router.get("/{share_id}")
def get_share(share_id: ShareId, ctx: Context) -> dict[str, Any]:
    try:
        share = service.get(ctx, share_id)
    except service.ShareError as exc:
        raise _http(exc) from None
    share["url"] = share_url(share_id)
    return share


@router.get("/{share_id}/tables/{name}/rows")
def table_rows(
    share_id: ShareId, name: Annotated[str, Path(pattern=r"^q[0-9]{1,6}$")], ctx: Context,
    offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    order_by: str | None = None, descending: bool = False,
) -> dict[str, Any]:
    try:
        return service.table_rows(ctx, share_id, name, offset=offset, limit=limit, order_by=order_by, descending=descending)
    except service.ShareError as exc:
        raise _http(exc) from None


@router.get("/{share_id}/files/{file_id}")
def file(share_id: ShareId, file_id: UUID, ctx: Context) -> StreamingResponse:
    try:
        item, body = service.open_file(ctx, share_id, str(file_id))
    except service.ShareError as exc:
        raise _http(exc) from None
    return StreamingResponse(body, media_type=item["mime_type"], headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(item['name'])}",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    })


@router.post("/{share_id}/continue")
async def continue_share(share_id: ShareId, request: Request, ctx: Context) -> dict[str, str]:
    try:
        return await run_in_threadpool(service.continue_share, ctx, _token(request), share_id)
    except service.ShareError as exc:
        raise _http(exc) from None


@public_router.get("/{share_id}")
def public_share(share_id: ShareId, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Robots-Tag"] = "noindex"
    try:
        return service.public_get(share_id)
    except service.ShareError as exc:
        raise _http(exc) from None


@router.post("/{share_id}/extend")
def extend(share_id: ShareId, body: ExtendIn, ctx: Context) -> dict[str, str]:
    try:
        return {"expires_at": service.extend(ctx, share_id, body.days)}
    except service.ShareError as exc:
        raise _http(exc) from None


@router.put("/{share_id}/wall")
def set_wall(share_id: ShareId, body: WallIn, ctx: Context) -> dict[str, str]:
    try:
        return wall.set_wall(ctx, share_id, body.wall)
    except service.ShareError as exc:
        raise _http(exc) from None


@router.delete("/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke(share_id: ShareId, ctx: Context) -> None:
    try:
        service.revoke(ctx, share_id)
    except service.ShareError as exc:
        raise _http(exc) from None


class SharingSettingsIn(BaseModel):
    enabled: bool
    public_links: bool = True
    # None keeps the current value (or the default for the kind of organization).
    org_wall: bool | None = None
    public_wall: bool | None = None


@admin_router.get("")
def sharing_settings(ctx: Admin) -> dict[str, bool]:
    return service.settings_for(ctx)


@admin_router.put("")
def save_sharing_settings(body: SharingSettingsIn, ctx: Admin) -> dict[str, bool]:
    with tenant_transaction(ctx.tenant_id) as conn:
        conn.execute(
            "INSERT INTO tenant_sharing_settings (tenant_id, enabled, public_links, org_wall, public_wall) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (tenant_id) DO UPDATE SET enabled = EXCLUDED.enabled, public_links = EXCLUDED.public_links, "
            "org_wall = coalesce(EXCLUDED.org_wall, tenant_sharing_settings.org_wall), "
            "public_wall = coalesce(EXCLUDED.public_wall, tenant_sharing_settings.public_wall), updated_at = now()",
            (ctx.tenant_id, body.enabled, body.public_links, body.org_wall, body.public_wall),
        )
        record_event(conn, ctx, "share.settings_saved", None, enabled=body.enabled, public_links=body.public_links,
                     org_wall=body.org_wall, public_wall=body.public_wall)
    return service.settings_for(ctx)
