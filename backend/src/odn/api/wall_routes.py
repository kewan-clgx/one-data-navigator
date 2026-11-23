"""/v1/wall and /v1/public/wall: the Insight Wall (docs/roadmap/insight_wall_dashboards_plan.md)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel

from odn.api.deps import Context, require_admin
from odn.platform.sharing import wall as service
from odn.platform.sharing.service import ShareError
from odn.tenancy.context import RequestContext

router = APIRouter(prefix="/v1/wall")
# No sign-in: the public EasyQuery wall (odn_public_wall decides what is listed).
public_router = APIRouter(prefix="/v1/public/wall")
Admin = Annotated[RequestContext, Depends(require_admin)]
ShareId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{22}$")]
Q = Annotated[str | None, Query(max_length=100)]
Offset = Annotated[int, Query(ge=0, le=10_000)]


def _http(exc: ShareError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


class FeatureIn(BaseModel):
    featured: bool


@router.get("")
def wall(ctx: Context, scope: Literal["organization", "public"] = "organization",
         sort: service.Sort = "hot", q: Q = None, offset: Offset = 0) -> dict[str, Any]:
    return service.wall(ctx, scope, sort, q, offset)


@router.post("/{share_id}/vote")
def vote(share_id: ShareId, ctx: Context) -> dict[str, Any]:
    try:
        return service.vote(ctx, share_id, True)
    except ShareError as exc:
        raise _http(exc) from None


@router.delete("/{share_id}/vote")
def unvote(share_id: ShareId, ctx: Context) -> dict[str, Any]:
    try:
        return service.vote(ctx, share_id, False)
    except ShareError as exc:
        raise _http(exc) from None


@router.post("/{share_id}/feature")
def feature(share_id: ShareId, body: FeatureIn, ctx: Admin) -> dict[str, bool]:
    try:
        return service.feature(ctx, share_id, body.featured)
    except ShareError as exc:
        raise _http(exc) from None


@public_router.get("")
def public_wall(response: Response, sort: service.Sort = "hot", q: Q = None, offset: Offset = 0) -> list[dict[str, Any]]:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Robots-Tag"] = "noindex"
    return service.public_wall(sort, q, offset)
