"""/v1/artifacts routes: the user's artifacts store (result tables, saved charts, uploaded files)."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from odn.api.deps import Context
from odn.platform.workspace import manage
from odn.platform.workspace.service import Artifact, ChartResult, InvalidSession
from odn.platform.workspace.store import (
    PanelKind,
    PanelPage,
    PanelSort,
    StoreItem,
    StoreKind,
    get_chart,
    list_store,
    open_file,
    page_store,
    table_artifact,
)

router = APIRouter(prefix="/v1/artifacts")


@router.get("")
def artifacts(
    ctx: Context,
    kind: StoreKind | None = None,
    session_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[StoreItem]:
    try:
        return list_store(ctx, kind=kind, session_id=session_id, limit=limit)
    except InvalidSession as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None


@router.get("/page")
def artifacts_page(
    ctx: Context,
    kind: PanelKind | None = None,
    session_id: str | None = None,
    q: Annotated[str, Query(max_length=200)] = "",
    sort: PanelSort = "newest",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    archived: bool = False,
) -> PanelPage:
    """The Artifacts panel: one page across tables, charts, files and saved SQL, searched; `next_cursor` continues."""
    try:
        return page_store(ctx, kind=kind, session_id=session_id, q=q, sort=sort, limit=limit, cursor=cursor,
                          archived=archived)
    except InvalidSession as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None


@router.get("/charts/{chart_id}")
def chart(chart_id: UUID, ctx: Context) -> ChartResult:
    try:
        return get_chart(ctx, chart_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chart not found") from None


@router.get("/tables/{artifact_id}")
def table(artifact_id: UUID, ctx: Context) -> Artifact:
    try:
        return table_artifact(ctx, artifact_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "table not found") from None


@router.get("/files/{file_id}")
def file(file_id: UUID, ctx: Context, inline: bool = False) -> StreamingResponse:
    try:
        item, body = open_file(ctx, file_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found") from None
    # Only raster images may render in the browser; anything else is always a download.
    disposition = "inline" if inline and item.inline else "attachment"
    return StreamingResponse(
        body,
        media_type=item.mime_type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(item.name)}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


class ArchiveIn(BaseModel):
    # "30d": deleted for good 30 days after archiving; "forever": kept until restored or deleted.
    keep: manage.Keep = "30d"


@router.delete("/{kind}/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_artifact(kind: manage.Kind, item_id: UUID, ctx: Context) -> None:
    """Deletes one of the caller's artifacts (and its stored data) for good."""
    try:
        manage.delete(ctx, kind, item_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "artifact not found") from None


@router.post("/{kind}/{item_id}/archive")
def archive_artifact(kind: manage.Kind, item_id: UUID, body: ArchiveIn, ctx: Context) -> dict[str, str | None]:
    """Hides the artifact from the list and search; kept for 30 days (then deleted) or forever."""
    try:
        purge_at = manage.archive(ctx, kind, item_id, body.keep)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "artifact not found") from None
    return {"purge_at": purge_at.isoformat() if purge_at else None}


@router.post("/{kind}/{item_id}/restore", status_code=status.HTTP_204_NO_CONTENT)
def restore_artifact(kind: manage.Kind, item_id: UUID, ctx: Context) -> None:
    try:
        manage.restore(ctx, kind, item_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "artifact not found") from None
