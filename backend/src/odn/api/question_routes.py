"""/v1 table questions: suggested and asked questions per table, cached answers, curation (table_questions_plan)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from google.api_core import exceptions as gexc
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from odn.api.deps import Context, require_data_lead
from odn.api.routes import _connector, _profile
from odn.api.usage_routes import require_agent
from odn.platform.catalog import table_questions as tq
from odn.platform.connections.service import build_policy
from odn.platform.connectors.base import ConnectorError
from odn.platform.sharing.service import _agent
from odn.tenancy.context import RequestContext

router = APIRouter(prefix="/v1")
DataLead = Annotated[RequestContext, Depends(require_data_lead)]


def _allowed_path(ctx: RequestContext, connection_id: UUID, path: list[str]):
    profile = _profile(ctx, connection_id)
    connector = _connector(profile)
    if not build_policy(profile, connector).allows_object(tuple(path)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "object not in allowlist")
    return profile, connector


def _source_error(exc: Exception) -> HTTPException:
    if isinstance(exc, gexc.NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, "table not found")
    if isinstance(exc, ValueError):
        return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return HTTPException(status.HTTP_502_BAD_GATEWAY, f"source metadata unavailable: {type(exc).__name__}")


@router.get("/connections/{connection_id}/questions")
def list_questions(
    connection_id: UUID, request: Request, ctx: Context, path: Annotated[list[str], Query()]
) -> tq.QuestionList:
    """Up to five questions for a table (curated, popular, yours, generated); generates the suggested set once."""
    profile, connector = _allowed_path(ctx, connection_id, path)
    try:
        return tq.list_questions(ctx, profile, connector, tuple(path), can_edit=ctx.role in ("data_lead", "admin"),
                                 llm=tq.platform_llm(_token(request)))
    except (gexc.GoogleAPIError, ConnectorError, ValueError) as exc:
        raise _source_error(exc) from None


class QuestionIn(BaseModel):
    path: list[str] = Field(min_length=2, max_length=3)
    title: str = Field(min_length=1, max_length=60)
    question: str = Field(min_length=12, max_length=300)


class QuestionPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=60)
    question: str | None = Field(default=None, min_length=12, max_length=300)
    hidden: bool | None = None
    pinned: bool | None = None


@router.post("/connections/{connection_id}/questions", status_code=status.HTTP_201_CREATED)
def add_question(connection_id: UUID, data: QuestionIn, ctx: DataLead) -> dict[str, str]:
    profile, _ = _allowed_path(ctx, connection_id, data.path)
    return {"id": str(tq.add_curated(ctx, profile, tuple(data.path), data.title, data.question))}


@router.patch("/connections/{connection_id}/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
def edit_question(connection_id: UUID, question_id: UUID, data: QuestionPatch, ctx: DataLead) -> None:
    try:
        tq.update(ctx, _profile(ctx, connection_id), question_id, **data.model_dump())
    except tq.QuestionNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "question not found") from None


@router.post("/connections/{connection_id}/questions/regenerate", status_code=status.HTTP_202_ACCEPTED)
def regenerate(
    connection_id: UUID, request: Request, ctx: DataLead, path: Annotated[list[str], Query()]
) -> dict[str, str]:
    profile, connector = _allowed_path(ctx, connection_id, path)
    try:
        info = tq.table_info(profile, connector, tuple(path))
    except (gexc.GoogleAPIError, ConnectorError, ValueError) as exc:
        raise _source_error(exc) from None
    return {"status": tq.ensure_generated(ctx, profile, tuple(path), info, llm=tq.platform_llm(_token(request)),
                                          force=True)}


def _token(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing token")
    return token


def _open(ctx: RequestContext, token: str, connection_id: UUID, question_id: UUID) -> dict[str, str]:
    profile = _profile(ctx, connection_id)
    connector = _connector(profile)
    try:
        saved = tq.cached_answer(ctx, profile, connector, question_id)
    except tq.QuestionNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no saved answer for this question") from None
    thread_id = str(uuid.uuid4())
    resp = _agent("POST", "/conversations/import", token, {
        "threadId": thread_id, "title": saved["title"], "owner": "EasyQuery FAQ (a saved answer)",
        "sharedAt": saved["answered_at"].date().isoformat(), "messages": saved["messages"],
    })
    if resp.status_code != 201:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "couldn't open the saved answer; try again")
    return {"thread_id": thread_id}


@router.post("/connections/{connection_id}/questions/{question_id}/open")
async def open_answer(connection_id: UUID, question_id: UUID, request: Request, ctx: Context) -> dict[str, str]:
    """Opens a saved answer as the caller's own new conversation (no tokens, no scan); follow-ups work as usual."""
    return await run_in_threadpool(_open, ctx, _token(request), connection_id, question_id)


class CaptureIn(BaseModel):
    connection_id: UUID
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=50_000)
    sql: list[str] = Field(default_factory=list, max_length=10)
    tables: list[str] = Field(default_factory=list, max_length=10)
    bytes_scanned: int | None = Field(default=None, ge=0)


@router.post("/questions/capture")
def capture(data: CaptureIn, ctx: Context, x_odn_agent_token: Annotated[str, Header()] = "") -> dict[str, Any]:
    """Agent only: the first question of a conversation and its answer, stored under the tables it read."""
    require_agent(x_odn_agent_token)
    profile = _profile(ctx, data.connection_id)
    try:
        return tq.capture(
            ctx, profile, _connector(profile), question=data.question, answer=data.answer, sqls=data.sql,
            tables=data.tables, bytes_scanned=data.bytes_scanned,
        )
    except (gexc.GoogleAPIError, ConnectorError) as exc:
        raise _source_error(exc) from None
