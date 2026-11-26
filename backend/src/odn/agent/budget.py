"""Per-user model routing and LLM usage reporting.

Every model call first asks the API which key to use for the signed-in user:
- their own key (Settings → AI model): call their provider with it, no platform limit;
- otherwise the platform key as a daily trial, stopped with a message once the user's or the tenant's
  trial budget is used up (fails closed when the budget cannot be checked).
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

import httpx
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from odn.agent import copilot_llm, tools
from odn.config import settings

log = logging.getLogger(__name__)

KEY_SOURCE = "odn_key_source"
SETTINGS_HINT = (
    "Sign in with GitHub Copilot, or add your own API key (Gemini, OpenAI, Anthropic or an OpenAI-compatible "
    "endpoint), in Settings → AI model "
    "to keep chatting now, at your own provider's cost."
)
TRIAL_EXCEEDED = (
    "You've used today's free trial of the shared AI model ({used:,} of {limit:,} tokens). " + SETTINGS_HINT
    + " The trial resets at {resets} UTC. The Explorer, saved results and charts still work."
)
TENANT_EXCEEDED = (
    "Your organization has used today's shared AI model budget ({used:,} of {limit:,} tokens). " + SETTINGS_HINT
    + " The shared budget resets at {resets} UTC. The Explorer, saved results and charts still work."
)
BUDGET_UNAVAILABLE = (
    "I can't check your AI usage budget right now, so I've paused to avoid uncapped usage. "
    "Please try again in a moment."
)
USER_KEY_UNREADABLE = (
    "Your saved AI model key can't be used ({error}). Please enter it again in Settings → AI model."
)
COPILOT_FAILED = (
    "GitHub Copilot couldn't answer with model \"{model}\" ({error}). In Settings → AI model, sign in with GitHub "
    "again or pick another model. Your organization's Copilot policy decides which models you can use."
)
USER_KEY_FAILED = (
    "Your AI provider ({provider}) returned an error for model \"{model}\": {error}. Check the API key, model "
    "name and your provider account (billing/quota) in Settings → AI model."
)


def _reply(text: str, source: str = "platform") -> LlmResponse:
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=text)]), custom_metadata={KEY_SOURCE: source}
    )


async def _llm_config() -> dict[str, Any] | None:
    try:
        resp = await tools._api("GET", "me/llm-config", headers={"X-ODN-Agent-Token": settings.agent_token})
    except (httpx.HTTPError, tools.ToolError):
        log.warning("LLM config lookup failed", exc_info=True)
        return None
    if resp.status_code != 200:
        log.warning("LLM config lookup failed: HTTP %s", resp.status_code)
        return None
    return resp.json()


def _exceeded_message(budget: dict[str, Any]) -> str:
    resets = datetime.fromisoformat(budget["resets_at"]).strftime("%H:%M")
    if budget.get("exceeded_scope") == "user":
        return TRIAL_EXCEEDED.format(
            used=budget["user_tokens_used_today"], limit=budget["user_daily_token_limit"], resets=resets
        )
    return TENANT_EXCEEDED.format(used=budget["tokens_used_today"], limit=budget["daily_token_limit"], resets=resets)


def _provider_error(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    kind = type(exc).__name__
    # Provider messages can echo parts of the request; keep only the error type and status.
    return f"{kind} (HTTP {status})" if status else kind


class RoutedLiteLlm(LiteLlm):
    """The platform model by default; a per-request LiteLlm with the user's own key when they have one."""

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        cfg = await _llm_config()
        if cfg is None:
            yield _reply(BUDGET_UNAVAILABLE)
            return
        if cfg["source"] == "user_error":
            yield _reply(USER_KEY_UNREADABLE.format(error=cfg.get("error", "unknown error")), "user")
            return

        if cfg["source"] == "user" and cfg["provider"] == "github_copilot":
            source = "user"
            responses = copilot_llm.generate(llm_request, cfg["model"], cfg["api_key"])
        elif cfg["source"] == "user":
            source = "user"
            delegate: LiteLlm = LiteLlm(
                model=cfg["litellm_model"],
                api_key=cfg["api_key"],
                api_base=cfg.get("api_base") or None,
                num_retries=1,
                timeout=120,
            )
            llm_request.model = cfg["litellm_model"]
            responses = delegate.generate_content_async(llm_request, stream=stream)
        else:
            source = "platform"
            budget = cfg.get("budget") or {}
            if budget.get("exceeded"):
                yield _reply(_exceeded_message(budget))
                return
            responses = super().generate_content_async(llm_request, stream=stream)

        try:
            async for response in responses:
                response.custom_metadata = {**(response.custom_metadata or {}), KEY_SOURCE: source}
                yield response
        except Exception as exc:
            if source != "user":
                raise
            log.info("user LLM call failed: %s", _provider_error(exc))
            if cfg["provider"] == "github_copilot":
                yield _reply(COPILOT_FAILED.format(model=cfg["model"], error=str(exc) or type(exc).__name__), "user")
                return
            yield _reply(
                USER_KEY_FAILED.format(provider=cfg["provider"], model=cfg["model"], error=_provider_error(exc)),
                "user",
            )


async def record_usage(callback_context: CallbackContext, llm_response: LlmResponse) -> LlmResponse | None:
    usage = llm_response.usage_metadata
    if usage is None or llm_response.partial:
        return None
    source = (llm_response.custom_metadata or {}).get(KEY_SOURCE, "platform")
    try:
        resp = await tools._api(
            "POST",
            "llm-usage",
            headers={"X-ODN-Agent-Token": settings.agent_token},
            json={
                "model": llm_response.model_version or os.environ.get("LITELLM_MODEL", "unknown"),
                "prompt_tokens": usage.prompt_token_count or 0,
                "completion_tokens": (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0),
                "cached_tokens": usage.cached_content_token_count or 0,
                "key_source": source if source in ("platform", "user") else "platform",
            },
        )
        if resp.status_code != 200:
            log.warning("LLM usage not recorded: HTTP %s", resp.status_code)
    except (httpx.HTTPError, tools.ToolError):
        log.warning("LLM usage not recorded", exc_info=True)
    return None
