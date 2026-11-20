"""Agent tools for the Collibra data catalog, run as the signed-in user through /v1/collibra/* (read-only)."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote

from odn.agent.tools import _api, _detail

MAX_DESCRIBE = 20


def _env(environment: str) -> str:
    return quote(environment.strip().lower(), safe="")


async def collibra_environments() -> dict[str, Any]:
    """Lists the Collibra catalog environments (e.g. prd, int) configured for the user's organization.

    Returns:
        `environments` with `env` names to pass to the other collibra_* tools; empty if Collibra is not connected.
    """
    resp = await _api("GET", "collibra/environments")
    return resp.json() if resp.status_code == 200 else {"error": _detail(resp)}


async def collibra_search_assets(
    environment: str,
    text: str,
    match: Literal["ANYWHERE", "START", "EXACT"] = "ANYWHERE",
    limit: int = 10,
    offset: int = 0,
) -> dict[str, Any]:
    """Searches Collibra assets (business terms, columns, tables, API fields...) by name, as the user.

    Use short name fragments ("revenue", "employee count"); try synonyms if nothing matches. Use START for
    qualified prefixes and EXACT for exact names.

    Args:
        environment: Environment from `collibra_environments`, e.g. "prd".
        text: Name text to search for.
        match: ANYWHERE (default), START or EXACT.
        limit: Results per page (1-50).
        offset: Paging offset; use `next_offset` from the previous page.

    Returns:
        `results` (id, name, type, domain, status, url), `total`, `has_more`, `next_offset`, or an `error`.
    """
    params = {"q": text, "match": match, "limit": max(1, min(limit, 50)), "offset": max(0, offset)}
    resp = await _api("GET", f"collibra/{_env(environment)}/assets", params=params)
    return resp.json() if resp.status_code == 200 else {"error": _detail(resp)}


async def collibra_describe_assets(environment: str, asset_ids: list[str]) -> dict[str, Any]:
    """Reads definitions and attributes of up to 20 Collibra assets (ids from search or relations).

    Args:
        environment: Environment from `collibra_environments`.
        asset_ids: 1-20 asset ids.

    Returns:
        `assets`, each with id, name, type, domain, status, url, `definition` (plain text, if any) and
        `attributes`, or a per-asset `error`.
    """
    ids = [a for a in dict.fromkeys(asset_ids) if a][:MAX_DESCRIBE]
    if not ids:
        return {"error": "give 1-20 asset ids"}
    resp = await _api("POST", f"collibra/{_env(environment)}/describe", json={"asset_ids": ids})
    return resp.json() if resp.status_code == 200 else {"error": _detail(resp)}


async def collibra_get_relations(
    environment: str,
    asset_id: str,
    direction: Literal["outgoing", "incoming"] = "outgoing",
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Follows Collibra relations of one asset (lineage, term-to-column links, table-to-column).

    Args:
        environment: Environment from `collibra_environments`.
        asset_id: The asset id.
        direction: "outgoing" (asset is the source) or "incoming" (asset is the target).
        limit: Results per page (1-100).
        offset: Paging offset.

    Returns:
        `results` with `source` and `target` assets (id, name, url), paging fields, or an `error`.
    """
    params = {"direction": direction, "limit": max(1, min(limit, 100)), "offset": max(0, offset)}
    resp = await _api("GET", f"collibra/{_env(environment)}/assets/{quote(asset_id, safe='')}/relations", params=params)
    return resp.json() if resp.status_code == 200 else {"error": _detail(resp)}


async def collibra_get_api_fields(environment: str, asset_id: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """Lists the fields linked from a table/API asset with each field's definition (one page).

    Relations are catalog candidates, not proof of schema membership. Continue with `next_offset` while
    `has_more` is true.

    Args:
        environment: Environment from `collibra_environments`.
        asset_id: The table or API asset id.
        limit: Fields per page (1-20).
        offset: Paging offset.

    Returns:
        `fields` (id, name, type, definition, url), `total`, `has_more`, `next_offset`, or an `error`.
    """
    params = {"limit": max(1, min(limit, 20)), "offset": max(0, offset)}
    resp = await _api("GET", f"collibra/{_env(environment)}/assets/{quote(asset_id, safe='')}/fields", params=params)
    return resp.json() if resp.status_code == 200 else {"error": _detail(resp)}


# Run in the user's browser through the QueryMuse Collibra Bridge extension (frontend/app/components/
# CollibraBrowserTools.tsx), with the user's own Collibra session. Only these frontend tools reach the agent.
BROWSER_TOOL_NAMES = [
    "collibra_browser_status", "collibra_browser_search_assets", "collibra_browser_describe_assets",
    "collibra_browser_get_relations", "collibra_browser_get_api_fields",
]

COLLIBRA_TOOLS = [
    collibra_environments, collibra_search_assets, collibra_describe_assets, collibra_get_relations,
    collibra_get_api_fields,
]
