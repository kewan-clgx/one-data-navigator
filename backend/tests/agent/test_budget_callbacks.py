"""Agent model routing: the user's own key, the platform trial with a hard stop (fail closed), usage reporting."""

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from odn.agent import budget, tools
from odn.agent.identity import bearer_token
from odn.config import settings

PLATFORM_OK = {
    "source": "platform",
    "budget": {
        "key_source": "platform", "daily_token_limit": 1000, "tokens_used_today": 20, "remaining": 980,
        "user_daily_token_limit": 100, "user_tokens_used_today": 10, "exceeded": False, "exceeded_scope": None,
        "resets_at": "2026-11-16T00:00:00Z",
    },
}
USER_KEY = {
    "source": "user", "provider": "anthropic", "model": "claude-x", "litellm_model": "anthropic/claude-x",
    "api_base": None, "api_key": "sk-user-secret",
}


@dataclass
class FakeApi:
    config: dict[str, Any] = field(default_factory=lambda: PLATFORM_OK)
    down: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("down")
        self.calls.append({"method": method, "path": path, **kwargs})
        return httpx.Response(200, json=self.config if path == "me/llm-config" else {})


@dataclass
class Delegates:
    """Records which LiteLlm (platform instance or a per-request one) generated content, and with what."""

    used: list[dict[str, Any]] = field(default_factory=list)
    fail: Exception | None = None


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    bearer_token.set("tok")
    monkeypatch.setattr(settings, "agent_token", "agent-secret")
    fake = FakeApi()
    monkeypatch.setattr(tools, "_api", fake)
    return fake


@pytest.fixture
def delegates(monkeypatch: pytest.MonkeyPatch) -> Delegates:
    rec = Delegates()

    async def fake_generate(self: LiteLlm, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator:
        rec.used.append({"model": llm_request.model, "args": dict(self._additional_args)})
        if rec.fail:
            raise rec.fail
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="hi")]))

    monkeypatch.setattr(LiteLlm, "generate_content_async", fake_generate)
    return rec


def _model() -> budget.RoutedLiteLlm:
    return budget.RoutedLiteLlm(model="gemini/platform", api_key="platform-key")


async def _run(model: budget.RoutedLiteLlm) -> list[LlmResponse]:
    return [r async for r in model.generate_content_async(LlmRequest(model="gemini/platform"))]


async def test_platform_trial_under_budget_uses_platform_key(api: FakeApi, delegates: Delegates) -> None:
    [resp] = await _run(_model())
    assert delegates.used[0]["args"]["api_key"] == "platform-key"
    assert resp.custom_metadata == {budget.KEY_SOURCE: "platform"}
    assert api.calls[0]["headers"] == {"X-ODN-Agent-Token": "agent-secret"}


@pytest.mark.parametrize(("scope", "expected"), [("user", "10 of 100 tokens"), ("tenant", "20 of 1,000 tokens")])
async def test_exhausted_trial_asks_for_own_key_without_calling_the_model(
    api: FakeApi, delegates: Delegates, scope: str, expected: str
) -> None:
    api.config = {"source": "platform", "budget": {**PLATFORM_OK["budget"], "exceeded": True, "exceeded_scope": scope}}
    [resp] = await _run(_model())
    text = resp.content.parts[0].text
    assert expected in text and "Settings → AI model" in text and not delegates.used


async def test_config_lookup_failure_fails_closed(api: FakeApi, delegates: Delegates) -> None:
    api.down = True
    [resp] = await _run(_model())
    assert "can't check" in resp.content.parts[0].text and not delegates.used


async def test_own_key_routes_to_the_users_provider(api: FakeApi, delegates: Delegates) -> None:
    api.config = USER_KEY
    [resp] = await _run(_model())
    [used] = delegates.used
    assert used["model"] == "anthropic/claude-x" and used["args"]["api_key"] == "sk-user-secret"
    assert resp.custom_metadata == {budget.KEY_SOURCE: "user"}


async def test_own_key_provider_error_becomes_a_settings_hint(api: FakeApi, delegates: Delegates) -> None:
    api.config = USER_KEY
    delegates.fail = RuntimeError("401 bad key sk-user-secret")
    [resp] = await _run(_model())
    text = resp.content.parts[0].text
    assert "RuntimeError" in text and "Settings → AI model" in text and "sk-user-secret" not in text


async def test_unreadable_own_key_does_not_fall_back_to_platform(api: FakeApi, delegates: Delegates) -> None:
    api.config = {"source": "user_error", "error": "credential cannot be decrypted"}
    [resp] = await _run(_model())
    assert "enter it again" in resp.content.parts[0].text and not delegates.used


async def test_only_final_responses_are_reported_with_agent_token_and_key_source(api: FakeApi) -> None:
    usage = types.GenerateContentResponseUsageMetadata(prompt_token_count=12, candidates_token_count=5)
    await budget.record_usage(None, LlmResponse(usage_metadata=usage, partial=True))  # type: ignore[arg-type]
    await budget.record_usage(None, LlmResponse())  # type: ignore[arg-type]
    await budget.record_usage(  # type: ignore[arg-type]
        None, LlmResponse(usage_metadata=usage, model_version="m", custom_metadata={budget.KEY_SOURCE: "user"})
    )
    [call] = api.calls
    assert call["path"] == "llm-usage" and call["headers"] == {"X-ODN-Agent-Token": "agent-secret"}
    assert call["json"] == {"model": "m", "prompt_tokens": 12, "completion_tokens": 5, "key_source": "user"}
