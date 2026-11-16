"""Model callbacks: stop LLM calls once the tenant's daily token budget is used, and record token usage."""

from __future__ import annotations

import logging
import os
from datetime import datetime

import httpx
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from odn.agent import tools
from odn.config import settings

log = logging.getLogger(__name__)

BUDGET_EXCEEDED = (
    "Your organization has used today's AI assistant budget ({used:,} of {limit:,} tokens), so I can't answer "
    "more questions until it resets at {resets} UTC. The Explorer, saved results and charts still work. "
    "Ask your administrator if you need a higher limit."
)
BUDGET_UNAVAILABLE = (
    "I can't check your organization's AI usage budget right now, so I've paused to avoid uncapped usage. "
    "Please try again in a moment."
)


def _reply(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


async def enforce_budget(callback_context: CallbackContext, llm_request: LlmRequest) -> LlmResponse | None:
    """Hard stop before each model call; fails closed if the budget cannot be checked."""
    try:
        resp = await tools._api("GET", "llm-budget")
    except (httpx.HTTPError, tools.ToolError):
        log.warning("LLM budget check failed", exc_info=True)
        return _reply(BUDGET_UNAVAILABLE)
    if resp.status_code != 200:
        return _reply(BUDGET_UNAVAILABLE)
    budget = resp.json()
    if budget["exceeded"]:
        resets = datetime.fromisoformat(budget["resets_at"]).strftime("%H:%M")
        return _reply(
            BUDGET_EXCEEDED.format(used=budget["tokens_used_today"], limit=budget["daily_token_limit"], resets=resets)
        )
    return None


async def record_usage(callback_context: CallbackContext, llm_response: LlmResponse) -> LlmResponse | None:
    usage = llm_response.usage_metadata
    if usage is None or llm_response.partial:
        return None
    try:
        resp = await tools._api(
            "POST",
            "llm-usage",
            headers={"X-ODN-Agent-Token": settings.agent_token},
            json={
                "model": llm_response.model_version or os.environ.get("LITELLM_MODEL", "unknown"),
                "prompt_tokens": usage.prompt_token_count or 0,
                "completion_tokens": (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0),
            },
        )
        if resp.status_code != 200:
            log.warning("LLM usage not recorded: HTTP %s", resp.status_code)
    except (httpx.HTTPError, tools.ToolError):
        log.warning("LLM usage not recorded", exc_info=True)
    return None
