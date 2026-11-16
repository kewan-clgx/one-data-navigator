"""list_tables puts the user's favorite and recently queried datasets/tables first."""

from typing import Any

import httpx
import pytest

from odn.agent import tools
from odn.agent.identity import bearer_token

MINE = {
    "favorites": [{"path": "p.b.t2", "kind": "table"}, {"path": "p.c", "kind": "dataset"}],
    "recent": [{"path": "p.b.t3", "query_count": 4, "last_used_at": "2026-11-15T00:00:00Z"},
               {"path": "p.d.t1", "query_count": 1, "last_used_at": "2026-11-14T00:00:00Z"}],
}


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> None:
    bearer_token.set("tok")

    async def api(method: str, path: str, **kwargs: Any) -> httpx.Response:
        if path.endswith("/my-tables"):
            return httpx.Response(200, json=MINE)
        parent = [v for _, v in kwargs.get("params", [])]
        if not parent:
            nodes = [{"name": n, "kind": "dataset", "path": ["p", n]} for n in ("a", "b", "c", "d")]
        else:
            nodes = [{"name": n, "kind": "table", "path": [*parent, n]} for n in ("t1", "t2", "t3")]
        return httpx.Response(200, json=nodes)

    monkeypatch.setattr(tools, "_api", api)


async def test_datasets_with_favorites_then_recent_first(fake_api: None) -> None:
    out = await tools.list_tables("c1")
    assert [i["path"] for i in out["items"]] == ["p.b", "p.c", "p.d", "p.a"]
    assert out["items"][1]["favorite"] is True


async def test_tables_favorite_then_recent(fake_api: None) -> None:
    out = await tools.list_tables("c1", "p.b")
    assert [i["path"] for i in out["items"]] == ["p.b.t2", "p.b.t3", "p.b.t1"]
    assert out["items"][0]["favorite"] and out["items"][1]["recently_used"]
