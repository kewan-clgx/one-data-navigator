"""/v1/artifacts routes: the user's artifacts store (result tables, saved charts, uploaded files)."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from odn.api.deps import Context
from odn.platform.workspace.service import Artifact, ChartResult, InvalidSession
from odn.platform.workspace.store import (
    StoreItem,
    StoreKind,
    get_chart,
    list_store,
    open_file,
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
