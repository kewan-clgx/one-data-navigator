"""/v1/workspace routes: session result artifacts, DuckDB follow-ups and paginated rows."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from google.api_core import exceptions as gexc
from pydantic import BaseModel, Field

from odn.api.deps import Context
from odn.api.routes import _connector, _profile
from odn.platform.query.service import ConfirmationRequired, QueryFailed, QueryRejected
from odn.platform.workspace.service import (
    Artifact,
    ArtifactResult,
    ChartResult,
    ChartSpec,
    InvalidSession,
    artifact_csv,
    artifact_rows,
    list_artifacts_explained,
    render_chart,
    run_warehouse_query,
    run_workspace_query,
)

router = APIRouter(prefix="/v1/workspace")


class WarehouseQueryIn(BaseModel):
    connection_id: UUID
    sql: str = Field(min_length=1, max_length=100_000)
    confirmed: bool = False


class WorkspaceQueryIn(BaseModel):
    sql: str = Field(min_length=1, max_length=100_000)


def _errors(exc: Exception) -> HTTPException:
    if isinstance(exc, InvalidSession):
        return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    if isinstance(exc, QueryRejected):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"rejected": exc.reason})
    if isinstance(exc, ConfirmationRequired):
        return HTTPException(
            status.HTTP_409_CONFLICT, {"confirmation_required": True, "bytes_estimated": exc.bytes_estimated}
        )
    if isinstance(exc, QueryFailed):
        return HTTPException(status.HTTP_400_BAD_REQUEST, {"failed": str(exc)})
    raise exc


@router.post("/{session_id}/warehouse-query")
def warehouse_query(session_id: str, req: WarehouseQueryIn, ctx: Context) -> ArtifactResult:
    profile = _profile(ctx, req.connection_id)
    try:
        return run_warehouse_query(
            ctx, profile, _connector(profile), session_id, req.sql, confirmed=req.confirmed
        )
    except (InvalidSession, QueryRejected, ConfirmationRequired, QueryFailed) as exc:
        raise _errors(exc) from None
    except gexc.GoogleAPIError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, type(exc).__name__) from None


@router.post("/{session_id}/query")
def workspace_query(session_id: str, req: WorkspaceQueryIn, ctx: Context) -> ArtifactResult:
    try:
        return run_workspace_query(ctx, session_id, req.sql)
    except (InvalidSession, QueryRejected, QueryFailed) as exc:
        raise _errors(exc) from None
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "workspace worker unavailable") from None


@router.get("/{session_id}/artifacts")
def artifacts(session_id: str, ctx: Context) -> list[Artifact]:
    try:
        return list_artifacts_explained(ctx, session_id)
    except InvalidSession as exc:
        raise _errors(exc) from None


@router.post("/{session_id}/chart")
def chart(session_id: str, spec: ChartSpec, ctx: Context) -> ChartResult:
    try:
        return render_chart(ctx, session_id, spec)
    except (InvalidSession, QueryFailed) as exc:
        raise _errors(exc) from None
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "artifact not found") from None
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "workspace worker unavailable") from None


@router.get("/{session_id}/artifacts/{name}/csv")
def csv(session_id: str, name: str, ctx: Context) -> StreamingResponse:
    try:
        body = artifact_csv(ctx, session_id, name)
    except InvalidSession as exc:
        raise _errors(exc) from None
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "artifact not found") from None
    return StreamingResponse(
        body, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}.csv"'}
    )


@router.get("/{session_id}/artifacts/{name}/rows")
def rows(
    session_id: str,
    name: str,
    ctx: Context,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    order_by: str | None = None,
    descending: bool = False,
) -> dict[str, Any]:
    try:
        return artifact_rows(
            ctx, session_id, name, offset=offset, limit=limit, order_by=order_by, descending=descending
        )
    except InvalidSession as exc:
        raise _errors(exc) from None
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "artifact not found") from None
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, exc.response.json().get("detail")) from None
