"""Agent model callbacks: hard stop at the tenant budget (fail closed) and usage reporting."""

from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from odn.agent import budget, tools
from odn.agent.identity import bearer_token
from odn.config import settings


@dataclass
class FakeApi:
    status: dict[str, Any] = field(default_factory=lambda: {
        "daily_token_limit": 100, "tokens_used_today": 20, "remaining": 80, "exceeded": False,
        "resets_at": "2026-11-16T00:00:00Z",
    })
    down: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("down")
        self.calls.append({"method": method, "path": path, **kwargs})
        return httpx.Response(200, json=self.status)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    bearer_token.set("tok")
    monkeypatch.setattr(settings, "agent_token", "agent-secret")
    fake = FakeApi()
    monkeypatch.setattr(tools, "_api", fake)
    return fake


async def test_under_budget_lets_the_model_run(api: FakeApi) -> None:
    assert await budget.enforce_budget(None, None) is None  # type: ignore[arg-type]


async def test_over_budget_replies_without_calling_the_model(api: FakeApi) -> None:
    api.status.update(exceeded=True, tokens_used_today=100, remaining=0)
    reply = await budget.enforce_budget(None, None)  # type: ignore[arg-type]
    assert reply is not None and "100 of 100 tokens" in reply.content.parts[0].text


async def test_budget_check_failure_fails_closed(api: FakeApi) -> None:
    api.down = True
    reply = await budget.enforce_budget(None, None)  # type: ignore[arg-type]
    assert reply is not None and "can't check" in reply.content.parts[0].text


async def test_only_final_responses_are_reported_with_agent_token(api: FakeApi) -> None:
    usage = types.GenerateContentResponseUsageMetadata(prompt_token_count=12, candidates_token_count=5)
    await budget.record_usage(None, LlmResponse(usage_metadata=usage, partial=True))  # type: ignore[arg-type]
    await budget.record_usage(None, LlmResponse())  # type: ignore[arg-type]
    await budget.record_usage(None, LlmResponse(usage_metadata=usage, model_version="m"))  # type: ignore[arg-type]
    [call] = api.calls
    assert call["path"] == "llm-usage" and call["headers"] == {"X-ODN-Agent-Token": "agent-secret"}
    assert call["json"] == {"model": "m", "prompt_tokens": 12, "completion_tokens": 5}
