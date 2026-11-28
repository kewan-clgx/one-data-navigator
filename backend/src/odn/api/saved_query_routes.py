"""/v1/saved-queries: the user's private SQL repository (docs/sql_repository_plan.md)."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from odn.api.deps import Context
from odn.platform.workspace import saved_queries as sq

router = APIRouter(prefix="/v1/saved-queries")


class SavedQueryIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    sql: str = Field(min_length=1, max_length=100_000)
    dialect: sq.Dialect
    connection_id: UUID | None = None
    description: str = Field(default="", max_length=300)


class SavedQueryPatch(BaseModel):
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    sql: str | None = Field(default=None, min_length=1, max_length=100_000)
    dialect: sq.Dialect | None = None
    connection_id: UUID | None = None
    description: str | None = Field(default=None, max_length=300)


def _errors(exc: Exception) -> HTTPException | JSONResponse:
    if isinstance(exc, sq.NameTaken):
        return JSONResponse({"detail": "name_taken", "suggestion": exc.suggestion}, status.HTTP_409_CONFLICT)
    if isinstance(exc, sq.VersionConflict):
        return JSONResponse({"detail": "conflict", "version": exc.current}, status.HTTP_409_CONFLICT)
    if isinstance(exc, KeyError):
        return HTTPException(status.HTTP_404_NOT_FOUND, "saved query not found")
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


_HANDLED = (sq.NameTaken, sq.VersionConflict, sq.SavedQueryError, KeyError)


@router.get("")
def list_saved(
    ctx: Context, q: str = "", table: str = "", limit: Annotated[int, Query(ge=1, le=500)] = 200, full: bool = False,
) -> list[sq.SavedQuery] | list[sq.SavedQueryItem]:
    """Saved SQL, recently edited first; `q` searches name, description, SQL and tables; `table` filters by table.
    `full` includes the whole SQL text (the agent's `find_saved_sql`)."""
    return sq.list_queries(ctx, q=q[:200], table=table[:300], limit=min(limit, 20) if full else limit, full=full)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=sq.SavedQuery)
def create_saved(data: SavedQueryIn, ctx: Context):
    try:
        return sq.create_query(ctx, name=data.name, sql=data.sql, dialect=data.dialect,
                               connection_id=data.connection_id, description=data.description)
    except _HANDLED as exc:
        err = _errors(exc)
        if isinstance(err, JSONResponse):
            return err
        raise err from None


@router.get("/{query_id}")
def get_saved(query_id: UUID, ctx: Context) -> sq.SavedQuery:
    try:
        return sq.get_query(ctx, query_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "saved query not found") from None


@router.put("/{query_id}", response_model=sq.SavedQuery)
def update_saved(query_id: UUID, data: SavedQueryPatch, ctx: Context):
    try:
        return sq.update_query(
            ctx, query_id, version=data.version, sql=data.sql, name=data.name, description=data.description,
            dialect=data.dialect, connection_id=data.connection_id,
            set_connection="connection_id" in data.model_fields_set,
        )
    except _HANDLED as exc:
        err = _errors(exc)
        if isinstance(err, JSONResponse):
            return err
        raise err from None


@router.delete("/{query_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_saved(query_id: UUID, ctx: Context) -> None:
    try:
        sq.delete_query(ctx, query_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "saved query not found") from None


@router.get("/{query_id}/versions")
def versions(query_id: UUID, ctx: Context) -> list[sq.SavedQueryVersion]:
    try:
        return sq.list_versions(ctx, query_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "saved query not found") from None


@router.get("/{query_id}/download")
def download(query_id: UUID, ctx: Context) -> Response:
    try:
        saved = sq.get_query(ctx, query_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "saved query not found") from None
    filename = f"{saved.name}.sql"
    return Response(saved.sql, media_type="application/sql", headers={
        "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}",
    })
