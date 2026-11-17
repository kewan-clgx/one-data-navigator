"""/v1 usage routes: LLM budget checks/recording (agent) and tenant telemetry (data leads)."""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from odn.api.deps import Context, require_data_lead
from odn.config import settings
from odn.platform.governance.llm_budget import (
    BudgetStatus,
    KeySource,
    budget_status,
    record_llm_usage,
    telemetry_summary,
)
from odn.tenancy.context import RequestContext

router = APIRouter(prefix="/v1")


def require_agent(token: str) -> None:
    if not settings.agent_token or not hmac.compare_digest(token, settings.agent_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "agent token required")

MAX_TOKENS_PER_CALL = 10_000_000


class LlmUsageInput(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    prompt_tokens: int = Field(ge=0, le=MAX_TOKENS_PER_CALL)
    completion_tokens: int = Field(ge=0, le=MAX_TOKENS_PER_CALL)
    key_source: KeySource = "platform"


@router.get("/llm-budget")
def llm_budget(ctx: Context) -> BudgetStatus:
    return budget_status(ctx.tenant_id, ctx.user_id)


@router.post("/llm-usage")
def llm_usage(
    data: LlmUsageInput, ctx: Context, x_odn_agent_token: Annotated[str, Header()] = ""
) -> BudgetStatus:
    # Only the agent runtime reports usage (the user's bearer token identifies whose usage it is).
    require_agent(x_odn_agent_token)
    return record_llm_usage(ctx, data.model, data.prompt_tokens, data.completion_tokens, data.key_source)


@router.get("/telemetry")
def telemetry(
    ctx: Annotated[RequestContext, Depends(require_data_lead)],
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> dict[str, Any]:
    return telemetry_summary(ctx, days)
