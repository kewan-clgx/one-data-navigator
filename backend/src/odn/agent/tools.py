"""Typed agent tools. Every call goes through the API as the signed-in user (policy, allowlist, audit)."""

from __future__ import annotations

from typing import Any

import httpx
from google.adk.tools import ToolContext

from odn.agent.identity import bearer_token
from odn.config import settings

LLM_SAMPLE_ROWS = 5
UI_MAX_ROWS = 200
MAX_TABLES_LISTED = 300


class ToolError(Exception):
    pass


async def _api(method: str, path: str, *, headers: dict[str, str] | None = None, **kwargs: Any) -> httpx.Response:
    token = bearer_token.get()
    if not token:
        raise ToolError("no authenticated user for this request")
    async with httpx.AsyncClient(base_url=settings.api_url, timeout=settings.query_timeout_s + 30) as client:
        return await client.request(
            method, f"/v1/{path}", headers={**(headers or {}), "Authorization": f"Bearer {token}"}, **kwargs
        )


def _detail(resp: httpx.Response) -> Any:
    try:
        return resp.json().get("detail", resp.text)
    except ValueError:
        return resp.text


async def list_connections() -> dict[str, Any]:
    """Lists the data connections (warehouses) the user can query.

    Returns:
        A dict with `connections`: each has `id`, `name`, `kind` (e.g. bigquery) and `allowed_objects`
        (the datasets/tables queries may reference).
    """
    resp = await _api("GET", "connections")
    if resp.status_code != 200:
        return {"error": _detail(resp)}
    return {
        "connections": [
            {k: c[k] for k in ("id", "name", "kind", "allowed_objects")} for c in resp.json()
        ]
    }


async def _my_tables(connection_id: str) -> dict[str, Any]:
    resp = await _api("GET", f"connections/{connection_id}/my-tables")
    return resp.json() if resp.status_code == 200 else {"favorites": [], "recent": []}


async def get_my_tables(connection_id: str) -> dict[str, Any]:
    """Gets the user's favorite datasets/tables and the tables they queried most recently.

    Check these first when looking for data: they are the user's preferred sources.

    Args:
        connection_id: The connection id from `list_connections`.

    Returns:
        `favorites` (path, kind dataset|table) and `recent` (path, query_count, last_used_at).
    """
    mine = await _my_tables(connection_id)
    return {
        "favorites": [{"path": f["path"], "kind": f["kind"]} for f in mine["favorites"]],
        "recent": [
            {"path": r["path"], "query_count": r["query_count"], "last_used_at": r["last_used_at"]}
            for r in mine["recent"]
        ],
    }


def _rank_items(items: list[dict[str, Any]], mine: dict[str, Any]) -> list[dict[str, Any]]:
    """Favorites first, then recently queried, then the rest in catalog order."""
    favorites = {f["path"] for f in mine["favorites"]}
    recent = {r["path"]: i for i, r in enumerate(mine["recent"])}
    favorite_parents = {p.rsplit(".", 1)[0] for p in favorites}
    recent_parents = {p.rsplit(".", 1)[0] for p in recent}

    def rank(item: dict[str, Any]) -> tuple[int, int]:
        path = item["path"].lower()
        if path in favorites:
            item["favorite"] = True
        if path in recent:
            item["recently_used"] = True
        if path in favorites or path in favorite_parents:
            return (0, recent.get(path, len(recent)))
        if path in recent or path in recent_parents:
            return (1, recent.get(path, len(recent)))
        return (2, 0)

    return sorted(items, key=rank)


async def list_tables(connection_id: str, dataset: str = "") -> dict[str, Any]:
    """Lists datasets, or the tables in one dataset, for a connection.

    The user's favorite and recently queried datasets/tables come first and are flagged with
    `favorite` / `recently_used`.

    Args:
        connection_id: The connection id from `list_connections`.
        dataset: Optional "project.dataset". When empty, lists the datasets instead of tables.

    Returns:
        A dict with `items`: each has `name`, `kind` and fully qualified `path`.
    """
    params = [("parent", p) for p in dataset.split(".")] if dataset else []
    resp = await _api("GET", f"connections/{connection_id}/catalog", params=params)
    if resp.status_code != 200:
        return {"error": _detail(resp)}
    nodes = resp.json()
    items = [{"name": n["name"], "kind": n["kind"], "path": ".".join(n["path"])} for n in nodes]
    items = _rank_items(items, await _my_tables(connection_id))
    return {"items": items[:MAX_TABLES_LISTED], "truncated": len(items) > MAX_TABLES_LISTED}


async def get_table_schema(connection_id: str, table: str) -> dict[str, Any]:
    """Gets columns, types, descriptions, curated business definitions, row count and partitioning.

    Prefer approved business definitions over raw descriptions when choosing columns and filters.

    Args:
        connection_id: The connection id from `list_connections`.
        table: Fully qualified table name "project.dataset.table".

    Returns:
        The table schema, or an `error`.
    """
    params = [("path", p) for p in table.split(".")]
    resp = await _api("GET", f"connections/{connection_id}/schema", params=params)
    if resp.status_code != 200:
        return {"error": _detail(resp)}
    schema = resp.json()
    dict_resp = await _api("GET", f"connections/{connection_id}/dictionary", params=params)
    if dict_resp.status_code == 200:
        curated = {f["name"]: f["curated"] for f in dict_resp.json()["fields"] if f.get("curated")}
        table_def = dict_resp.json().get("curated")
        if table_def:
            schema["business_definition"] = f'{table_def["definition"]} ({table_def["status"]})'
        for col in schema["columns"]:
            if col["name"] in curated:
                c = curated[col["name"]]
                col["business_definition"] = f'{c["definition"]} ({c["status"]})'
    return schema


async def run_sql(connection_id: str, sql: str, tool_context: ToolContext) -> dict[str, Any]:
    """Runs one read-only warehouse query (BigQuery Standard SQL) and saves the full result to the workspace.

    The result becomes a workspace table named q1, q2, ... that `query_workspace` can analyze further
    without scanning the warehouse again. Queries that would scan a lot of data pause for the user's
    approval; you cannot approve on their behalf.

    Args:
        connection_id: The connection id from `list_connections`.
        sql: A single SELECT statement using fully qualified table names.

    Returns:
        The workspace table name, columns, row count, bytes scanned and a small sample of rows.
        The user sees the result grid.
    """
    confirmation = tool_context.tool_confirmation
    confirmed = False
    if confirmation is not None:
        expected = {"connection_id": connection_id, "sql": sql}
        payload = confirmation.payload if isinstance(confirmation.payload, dict) else {}
        if not confirmation.confirmed:
            return {"status": "cancelled", "message": "The user declined to run this query."}
        if {k: payload.get(k) for k in expected} != expected:
            return {"status": "cancelled", "message": "Approval did not match this query; nothing was run."}
        confirmed = True

    resp = await _api(
        "POST",
        f"workspace/{_session_id(tool_context)}/warehouse-query",
        json={"connection_id": connection_id, "sql": sql, "confirmed": confirmed},
    )
    detail = _detail(resp) if resp.status_code != 200 else None

    if resp.status_code == 409 and isinstance(detail, dict) and detail.get("confirmation_required"):
        gb = detail["bytes_estimated"] / 1024**3
        tool_context.request_confirmation(
            hint=f"This query will scan about {gb:,.1f} GB, above the confirmation threshold.\n\n{sql}",
            payload={"connection_id": connection_id, "sql": sql, "bytes_estimated": detail["bytes_estimated"]},
        )
        return {"status": "awaiting_user_confirmation", "bytes_estimated": detail["bytes_estimated"]}
    if resp.status_code == 422 and isinstance(detail, dict):
        return {"status": "rejected", "reason": detail.get("rejected")}
    if resp.status_code != 200:
        return {"status": "failed", "error": detail}
    return _publish(resp.json(), tool_context, {})


async def query_workspace(sql: str, tool_context: ToolContext) -> dict[str, Any]:
    """Analyzes results already in the workspace with DuckDB SQL. Never scans the warehouse.

    Use this for every follow-up on data you already fetched: filtering, grouping, ranking, window
    functions, removing nulls, pivots, joins between workspace tables, etc.

    Args:
        sql: One DuckDB SELECT statement that reads only workspace tables by name (q1, q2, ...).

    Returns:
        A new workspace table name with columns, row count and a small sample of rows.
    """
    resp = await _api("POST", f"workspace/{_session_id(tool_context)}/query", json={"sql": sql})
    if resp.status_code == 422:
        detail = _detail(resp)
        return {"status": "rejected", "reason": detail.get("rejected") if isinstance(detail, dict) else detail}
    if resp.status_code != 200:
        return {"status": "failed", "error": _detail(resp)}
    names = {a["id"]: a["name"] for a in await _artifacts(tool_context)}
    return _publish(resp.json(), tool_context, names)


async def list_workspace(tool_context: ToolContext) -> dict[str, Any]:
    """Lists the workspace tables (q1, q2, ...) in this conversation with their columns and lineage.

    Returns:
        Each table's name, kind (warehouse result or workspace follow-up), row count, columns,
        the SQL that produced it, which workspace tables it was derived from, and its source tables.
    """
    artifacts = await _artifacts(tool_context)
    names = {a["id"]: a["name"] for a in artifacts}
    return {
        "tables": [
            {
                "name": a["name"],
                "kind": a["kind"],
                "row_count": a["row_count"],
                "truncated": a["truncated"],
                "columns": [f'{c["name"]} {c["type"]}' for c in a["columns"]],
                "sql": a["sql_text"],
                "explanation": a.get("explanation", ""),
                "derived_from": [names.get(p, "?") for p in a["parent_ids"]],
                "source_tables": a["source_tables"],
            }
            for a in artifacts
        ]
    }


async def generate_chart(
    source: str,
    chart_type: str,
    x: str,
    tool_context: ToolContext,
    y: str = "",
    agg: str = "sum",
    series: str = "",
    sort: str = "x",
    limit: int = 50,
    title: str = "",
) -> dict[str, Any]:
    """Draws a chart from one workspace table and shows it to the user. Never scans the warehouse.

    Prepare the data first with `query_workspace` if it needs filtering or reshaping; this tool only
    groups/aggregates one table.

    Args:
        source: Workspace table name, e.g. "q3".
        chart_type: "bar", "line" or "scatter".
        x: Column for the x axis (categories or dates).
        y: Numeric column for the y axis. Leave empty when agg is "count".
        agg: How to combine y per x (and series): "sum", "avg", "count", "min", "max", or "none" for raw points.
        series: Optional column to split into colored series.
        sort: "x" (by x), "y_desc" (largest first, good for top-N bars) or "y_asc".
        limit: Maximum points/bars to plot (1-500).
        title: Short human-readable chart title.

    Returns:
        Number of points plotted, whether the chart was truncated, and the first few points.
    """
    spec = {
        "source": source, "type": chart_type, "x": x, "y": y or None, "agg": agg,
        "series": series or None, "sort": sort, "limit": limit, "title": title,
    }
    resp = await _api("POST", f"workspace/{_session_id(tool_context)}/chart", json=spec)
    if resp.status_code != 200:
        return {"status": "failed", "error": _detail(resp)}
    body = resp.json()
    tool_context.state["last_chart"] = body
    return {
        "status": "ok",
        "points_plotted": len(body["points"]),
        "truncated": body["truncated"],
        "explanation": body.get("explanation", ""),
        "first_points": body["points"][:LLM_SAMPLE_ROWS],
    }


def _session_id(tool_context: ToolContext) -> str:
    return tool_context.session.id


async def _artifacts(tool_context: ToolContext) -> list[dict[str, Any]]:
    resp = await _api("GET", f"workspace/{_session_id(tool_context)}/artifacts")
    return resp.json() if resp.status_code == 200 else []


def _publish(body: dict[str, Any], tool_context: ToolContext, names_by_id: dict[str, str]) -> dict[str, Any]:
    artifact = body["artifact"]
    derived_from = [names_by_id.get(p, "?") for p in artifact["parent_ids"]]
    # The UI renders the grid and lineage from session state; the model only sees a small sample.
    tool_context.state["last_result"] = {
        "session_id": artifact["session_id"],
        "name": artifact["name"],
        "kind": artifact["kind"],
        "sql": artifact["sql_text"],
        "explanation": artifact.get("explanation", ""),
        "derived_from": derived_from,
        "source_tables": artifact["source_tables"],
        "columns": [c["name"] for c in artifact["columns"]],
        "rows": body["preview"][:UI_MAX_ROWS],
        "row_count": artifact["row_count"],
        "truncated": artifact["truncated"],
        "bytes_processed": body.get("bytes_processed"),
    }
    return {
        "status": "ok",
        "workspace_table": artifact["name"],
        "explanation": artifact.get("explanation", ""),
        "derived_from": derived_from,
        "columns": artifact["columns"],
        "row_count": artifact["row_count"],
        "truncated": artifact["truncated"],
        "bytes_processed": body.get("bytes_processed"),
        "sample_rows": body["preview"][:LLM_SAMPLE_ROWS],
    }
