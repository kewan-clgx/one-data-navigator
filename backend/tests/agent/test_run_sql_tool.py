"""run_sql confirmation gating: the model cannot approve expensive queries itself."""

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from odn.agent import tools
from odn.agent.identity import bearer_token
from odn.agent.tools import run_sql


class _Ctx:
    def __init__(self, confirmation: Any = None) -> None:
        self.tool_confirmation = confirmation
        self.state: dict[str, Any] = {}
        self.session = SimpleNamespace(id="s1")
        self.requested: dict[str, Any] | None = None

    def request_confirmation(self, *, hint: str, payload: Any) -> None:
        self.requested = {"hint": hint, "payload": payload}


@pytest.fixture
def api_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    bearer_token.set("tok")

    async def fake_api(method: str, path: str, **kwargs: Any) -> httpx.Response:
        body = kwargs["json"]
        calls.append(body)
        assert path == "workspace/s1/warehouse-query"
        if not body["confirmed"]:
            return httpx.Response(409, json={"detail": {"confirmation_required": True, "bytes_estimated": 50 * 1024**3}})
        artifact = {
            "id": "a1", "session_id": "s1", "name": "q1", "kind": "warehouse", "parent_ids": [],
            "sql_text": body["sql"], "source_tables": ["p.d.t"], "columns": [{"name": "n", "type": "int64"}],
            "row_count": 10, "truncated": False,
        }
        return httpx.Response(200, json={"artifact": artifact, "preview": [{"n": i} for i in range(10)],
                                         "bytes_processed": 1})

    monkeypatch.setattr(tools, "_api", fake_api)
    return calls


async def test_expensive_query_requests_user_confirmation(api_calls: list[dict[str, Any]]) -> None:
    ctx = _Ctx()
    out = await run_sql("c1", "SELECT n FROM t", ctx)  # type: ignore[arg-type]
    assert out["status"] == "awaiting_user_confirmation"
    assert ctx.requested and ctx.requested["payload"]["sql"] == "SELECT n FROM t"
    assert [c["confirmed"] for c in api_calls] == [False]


async def test_approved_confirmation_runs_and_hides_rows_from_model(api_calls: list[dict[str, Any]]) -> None:
    ctx = _Ctx(SimpleNamespace(confirmed=True, payload={"connection_id": "c1", "sql": "SELECT n FROM t"}))
    out = await run_sql("c1", "SELECT n FROM t", ctx)  # type: ignore[arg-type]
    assert out["status"] == "ok" and len(out["sample_rows"]) == 5
    assert len(ctx.state["last_result"]["rows"]) == 10
    assert api_calls[-1]["confirmed"] is True


@pytest.mark.parametrize(
    "confirmation",
    [
        SimpleNamespace(confirmed=False, payload={"connection_id": "c1", "sql": "SELECT n FROM t"}),
        SimpleNamespace(confirmed=True, payload={"connection_id": "c1", "sql": "SELECT other FROM t"}),
        SimpleNamespace(confirmed=True, payload=None),
    ],
)
async def test_declined_or_mismatched_approval_runs_nothing(
    api_calls: list[dict[str, Any]], confirmation: Any
) -> None:
    out = await run_sql("c1", "SELECT n FROM t", _Ctx(confirmation))  # type: ignore[arg-type]
    assert out["status"] == "cancelled"
    assert api_calls == []
