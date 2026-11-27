"""get_table_schema answers table questions (fill rates, freshness) from stored metadata, without SQL."""

from typing import Any

import httpx
import pytest

from odn.agent import tools
from odn.agent.tools import get_table_schema

SCHEMA = {
    "path": ["p", "d", "t"], "kind": "table", "row_count": 100, "modified_at": "2026-05-20T21:39:27Z",
    "columns": [{"name": "id", "type": "INTEGER"}, {"name": "status", "type": "STRING"}],
}


def _api_with(profile: httpx.Response):
    async def fake_api(method: str, path: str, **kwargs: Any) -> httpx.Response:
        if path.endswith("/schema"):
            return httpx.Response(200, json=SCHEMA)
        if path.endswith("/dictionary"):
            return httpx.Response(200, json={"fields": [], "curated": None})
        assert path.endswith("/column-profile"), path
        return profile

    return fake_api


async def test_profile_adds_fill_rate_and_range(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = {"sample_rows": 200, "sampled_at": "2026-11-26T05:51:44Z", "fields": [
        {"path": ["id"], "filled_rows": 200, "min_value": "1", "max_value": "99"},
        {"path": ["status"], "filled_rows": 50, "min_value": None, "max_value": None},
    ]}
    monkeypatch.setattr(tools, "_api", _api_with(httpx.Response(200, json=profile)))
    out = await get_table_schema("c1", "p.d.t")
    cols = {c["name"]: c for c in out["columns"]}
    assert (cols["id"]["fill_rate"], cols["id"]["min"], cols["id"]["max"]) == ("100.0%", "1", "99")
    assert cols["status"]["fill_rate"] == "25.0%" and "min" not in cols["status"]
    assert out["modified_at"] == "2026-05-20T21:39:27Z"
    assert out["profile"]["sample_rows"] == 200 and "NOT the number of rows" in out["profile"]["note"]
    assert "row_count_estimate" not in out  # the table reports its own row count


async def test_views_get_an_estimated_total_never_the_sample_size(monkeypatch: pytest.MonkeyPatch) -> None:
    view = {**SCHEMA, "kind": "view", "row_count": None}
    profile = {"sample_rows": 10_283_058, "sampled_at": "2026-11-27T05:35:00Z",
               "fields": [{"path": ["id"], "filled_rows": 10_283_058, "min_value": None, "max_value": None}]}

    async def fake_api(method: str, path: str, **kwargs: Any) -> httpx.Response:
        if path.endswith("/schema"):
            return httpx.Response(200, json=view)
        if path.endswith("/dictionary"):
            return httpx.Response(200, json={"fields": [], "curated": None})
        return httpx.Response(200, json=profile)

    monkeypatch.setattr(tools, "_api", fake_api)
    out = await get_table_schema("c1", "p.d.v")
    assert out["row_count_estimate"]["value"] == 102_830_580 and out["row_count"] is None


async def test_missing_profile_says_so_instead_of_inviting_a_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "_api", _api_with(httpx.Response(200, json={"sample_rows": None, "fields": []})))
    out = await get_table_schema("c1", "p.d.t")
    assert out["profile"].startswith("Not profiled yet") and "fill_rate" not in out["columns"][0]


async def test_connections_without_profiling_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "_api", _api_with(httpx.Response(404, json={"detail": "not supported"})))
    out = await get_table_schema("c1", "p.d.t")
    assert "profile" not in out
