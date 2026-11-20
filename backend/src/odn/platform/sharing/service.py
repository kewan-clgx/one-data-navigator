"""Sharing a conversation with teammates (docs/chat_session_share_plan.md).

A share is a frozen snapshot: the conversation's AG-UI messages (read from the agent with the owner's own token)
plus copies of its result tables, charts and files under the share's own storage prefix. It is readable only by
signed-in members of the owner's organization who are the owner, a chosen recipient, or (audience
"organization") anyone there, until it expires or is revoked.
"""

from __future__ import annotations

import json
import secrets
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from psycopg.types.json import Jsonb

from odn.config import settings
from odn.db import tenant_transaction
from odn.platform.workspace.storage import (
    assert_share_key,
    assert_tenant_key,
    copy_object,
    gcs_client,
    share_key,
)
from odn.tenancy.context import RequestContext

EXPIRY_DAYS = (7, 30, 90)
MAX_MESSAGES = 300
MAX_SNAPSHOT_BYTES = 3 * 1024 * 1024
MAX_ARTIFACTS = 60
RESULTS_OMITTED = '{"omitted": "Results were not included in this share."}'

Audience = Literal["people", "organization"]


class ShareError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def new_share_id() -> str:
    return secrets.token_urlsafe(16)[:22].ljust(22, "A")


# -- agent: read the owner's conversation with their own token ---------------------------------------------


def _agent(method: str, path: str, token: str, body: dict[str, Any] | None = None) -> httpx.Response:
    return httpx.request(
        method, f"{settings.agent_url}{path}", json=body, timeout=30,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )


def fetch_conversation(token: str, thread_id: str) -> dict[str, Any]:
    """The owner's conversation: {thread_id, session_id, title, messages}. Raises ShareError(404) if not theirs."""
    listing = _agent("GET", "/conversations", token)
    if listing.status_code != 200:
        raise ShareError("Could not read your conversations right now.", 502)
    match = next((c for c in listing.json().get("conversations", []) if c.get("threadId") == thread_id), None)
    if match is None:
        raise ShareError("Conversation not found.", 404)
    state = _agent("POST", "/agents/state", token, {"threadId": thread_id})
    data = state.json() if state.status_code == 200 else {}
    if not data.get("threadExists"):
        raise ShareError("Conversation not found.", 404)
    messages = data.get("messages") or []
    if not isinstance(messages, list) or not messages:
        raise ShareError("This conversation has no messages yet.")
    return {"thread_id": thread_id, "session_id": match["sessionId"], "title": match.get("title") or "", "messages": messages}


def _tool_names(messages: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for m in messages:
        for call in m.get("toolCalls") or []:
            fn = call.get("function") or {}
            if isinstance(fn.get("name"), str):
                names.add(fn["name"])
    return names


def _clean_messages(messages: list[dict[str, Any]], include_results: bool) -> list[dict[str, Any]]:
    """Only what the chat renders; tool results blanked when results are not shared."""
    out = []
    for m in messages[-MAX_MESSAGES:]:
        role = m.get("role")
        if role not in ("user", "assistant", "tool"):
            continue
        item: dict[str, Any] = {"id": str(m.get("id") or uuid.uuid4()), "role": role}
        content = m.get("content")
        if role == "tool":
            item["toolCallId"] = m.get("toolCallId")
            item["content"] = content if include_results and isinstance(content, str) else RESULTS_OMITTED
        else:
            if isinstance(content, str):
                item["content"] = content
            elif isinstance(content, list):  # multimodal user input: keep the text parts only
                item["content"] = " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
            calls = [c for c in m.get("toolCalls") or [] if isinstance(c, dict)]
            if calls:
                item["toolCalls"] = [
                    {"id": c.get("id"), "type": "function", "function": {
                        "name": (c.get("function") or {}).get("name"),
                        "arguments": (c.get("function") or {}).get("arguments") or "{}"}}
                    for c in calls
                ]
        out.append(item)
    return out


# -- artifacts ------------------------------------------------------------------------------------------------


def _session_artifacts(conn: Any, ctx: RequestContext, session_id: str) -> dict[str, list[dict[str, Any]]]:
    tables = conn.execute(
        "SELECT a.id, a.name, a.kind, a.sql_text, a.source_tables, a.object_key, a.row_count, a.truncated, a.columns, "
        "a.created_at, (c.owner_user_id IS NOT NULL) AS personal FROM workspace_artifacts a "
        "LEFT JOIN connections c ON c.id = a.connection_id WHERE a.user_id = %s AND a.session_id = %s "
        "ORDER BY a.created_at",
        (ctx.user_id, session_id),
    ).fetchall()
    charts = conn.execute(
        "SELECT id, name, title, result, created_at FROM workspace_charts WHERE user_id = %s AND session_id = %s "
        "ORDER BY created_at",
        (ctx.user_id, session_id),
    ).fetchall()
    files = conn.execute(
        "SELECT id, filename, mime_type, size_bytes, object_key, created_at FROM workspace_files "
        "WHERE user_id = %s AND session_id = %s ORDER BY created_at",
        (ctx.user_id, session_id),
    ).fetchall()
    return {"tables": tables, "charts": charts, "files": files}


def preview(ctx: RequestContext, token: str, thread_id: str) -> dict[str, Any]:
    """What the Share dialog needs before creating: default title, counts and warnings."""
    convo = fetch_conversation(token, thread_id)
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        arts = _session_artifacts(conn, ctx, convo["session_id"])
    return {
        "title": convo["title"] or "Shared conversation",
        "messages": sum(1 for m in convo["messages"] if m.get("role") in ("user", "assistant") and m.get("content")),
        "tables": len(arts["tables"]), "charts": len(arts["charts"]), "files": len(arts["files"]),
        "used_personal_connections": any(t["personal"] for t in arts["tables"]),
        "used_collibra": any(n.startswith("collibra") for n in _tool_names(convo["messages"])),
        "sharing_enabled": sharing_enabled(ctx),
    }


def sharing_enabled(ctx: RequestContext) -> bool:
    with tenant_transaction(ctx.tenant_id) as conn:
        row = conn.execute("SELECT enabled FROM tenant_sharing_settings").fetchone()
    return row is None or bool(row["enabled"])


def _active_recipients(conn: Any, ctx: RequestContext, ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    if not ids:
        return []
    rows = conn.execute(
        "SELECT id, email, display_name FROM users WHERE id = ANY(%s) AND status = 'active' AND id <> %s",
        (ids, ctx.user_id),
    ).fetchall()
    if len(rows) != len(set(ids) - {ctx.user_id}):
        raise ShareError("Pick active people in your organization.", 422)
    return rows


def create(
    ctx: RequestContext, token: str, *, thread_id: str, title: str, audience: Audience,
    recipient_ids: list[uuid.UUID], include_results: bool, expires_days: int,
) -> dict[str, Any]:
    if not sharing_enabled(ctx):
        raise ShareError("Sharing is turned off for your organization.", 403)
    if expires_days not in EXPIRY_DAYS:
        raise ShareError(f"Expiry must be one of {EXPIRY_DAYS} days.", 422)
    if audience == "people" and not recipient_ids:
        raise ShareError("Pick at least one person to share with.", 422)
    convo = fetch_conversation(token, thread_id)
    messages = _clean_messages(convo["messages"], include_results)
    if len(json.dumps(messages)) > MAX_SNAPSHOT_BYTES:
        raise ShareError("This conversation is too long to share; start a new one with the key questions.", 413)
    share_id = new_share_id()
    final_title = (title.strip() or convo["title"] or "Shared conversation")[:200]
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        recipients = _active_recipients(conn, ctx, recipient_ids) if audience == "people" else []
        arts = _session_artifacts(conn, ctx, convo["session_id"])
        used_personal = any(t["personal"] for t in arts["tables"])
        artifacts: list[dict[str, Any]] = []
        if include_results:
            if sum(len(v) for v in arts.values()) > MAX_ARTIFACTS:
                raise ShareError(f"This conversation has more than {MAX_ARTIFACTS} results to share.", 413)
            for t in arts["tables"]:
                dst = share_key(ctx.tenant_id, share_id, f"{t['name']}.parquet")
                copy_object(assert_tenant_key(ctx.tenant_id, ctx.user_id, t["object_key"]), dst)
                artifacts.append({
                    "kind": "table", "id": str(t["id"]), "name": t["name"], "table_kind": t["kind"], "sql": t["sql_text"],
                    "source_tables": t["source_tables"], "row_count": t["row_count"], "truncated": t["truncated"],
                    "columns": t["columns"], "object_key": dst,
                })
            for c in arts["charts"]:
                artifacts.append({"kind": "chart", "id": str(c["id"]), "name": c["name"], "title": c["title"],
                                  "result": c["result"]})
            for f in arts["files"]:
                dst = share_key(ctx.tenant_id, share_id, "files", str(f["id"]))
                copy_object(assert_tenant_key(ctx.tenant_id, ctx.user_id, f["object_key"]), dst)
                artifacts.append({"kind": "file", "id": str(f["id"]), "name": f["filename"], "mime_type": f["mime_type"],
                                  "size_bytes": f["size_bytes"], "object_key": dst})
        expires = datetime.now(UTC) + timedelta(days=expires_days)
        conn.execute(
            "INSERT INTO shared_conversations (id, tenant_id, owner_user_id, source_thread_id, source_session_id, title, "
            "audience, include_results, messages, artifacts, used_personal_connections, used_collibra, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (share_id, ctx.tenant_id, ctx.user_id, thread_id, convo["session_id"],
             final_title, audience, include_results,
             Jsonb(messages), Jsonb(artifacts), used_personal,
             any(n.startswith("collibra") for n in _tool_names(messages)), expires),
        )
        for r in recipients:
            conn.execute(
                "INSERT INTO shared_conversation_recipients (share_id, tenant_id, user_id) VALUES (%s, %s, %s)",
                (share_id, ctx.tenant_id, r["id"]),
            )
        conn.execute(
            "INSERT INTO admin_events (tenant_id, actor_user_id, action, target, detail) VALUES (%s, %s, %s, %s, %s)",
            (ctx.tenant_id, ctx.user_id, "share.created", share_id,
             Jsonb({"audience": audience, "recipients": [r["email"] for r in recipients],
                    "include_results": include_results, "expires_days": expires_days})),
        )
    return {"id": share_id, "title": final_title, "expires_at": expires.isoformat(),
            "recipients": [dict(r) for r in recipients]}


# -- reading ----------------------------------------------------------------------------------------------------

_SUMMARY = (
    "s.id, s.title, s.audience, s.include_results, s.created_at, s.expires_at, s.revoked_at, s.source_thread_id, "
    "s.used_personal_connections, s.used_collibra, s.owner_user_id, u.display_name AS owner_name, u.email AS owner_email"
)


def _load(conn: Any, share_id: str) -> dict[str, Any] | None:
    return conn.execute(
        f"SELECT {_SUMMARY}, s.messages, s.artifacts FROM shared_conversations s "
        "JOIN users u ON u.id = s.owner_user_id WHERE s.id = %s",
        (share_id,),
    ).fetchone()


def _allowed(conn: Any, ctx: RequestContext, share: dict[str, Any]) -> bool:
    if share["owner_user_id"] == ctx.user_id:
        return True
    if share["revoked_at"] is not None or share["expires_at"] <= datetime.now(UTC):
        return False
    if share["audience"] == "organization":
        return True
    return conn.execute(
        "SELECT 1 FROM shared_conversation_recipients WHERE share_id = %s AND user_id = %s", (share["id"], ctx.user_id)
    ).fetchone() is not None


def get(ctx: RequestContext, share_id: str) -> dict[str, Any]:
    """The share for a permitted reader; records the first view per user. 404 for anyone else (no existence leak)."""
    with tenant_transaction(ctx.tenant_id) as conn:
        share = _load(conn, share_id)
        if share is None or not _allowed(conn, ctx, share):
            raise ShareError("This conversation wasn't shared with you, or the link has expired.", 404)
        if share["owner_user_id"] != ctx.user_id:
            first = conn.execute(
                "INSERT INTO shared_conversation_views (share_id, tenant_id, user_id) VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING RETURNING share_id",
                (share_id, ctx.tenant_id, ctx.user_id),
            ).fetchone()
            if first:
                conn.execute(
                    "INSERT INTO admin_events (tenant_id, actor_user_id, action, target) VALUES (%s, %s, %s, %s)",
                    (ctx.tenant_id, ctx.user_id, "share.viewed", share_id),
                )
    out = {k: v for k, v in share.items() if k not in ("owner_user_id",)}
    out["artifacts"] = [{k: v for k, v in a.items() if k != "object_key"} for a in share["artifacts"]]
    out["is_owner"] = share["owner_user_id"] == ctx.user_id
    out["status"] = _status(share)
    return out


def _status(share: dict[str, Any]) -> str:
    if share["revoked_at"] is not None:
        return "revoked"
    return "expired" if share["expires_at"] <= datetime.now(UTC) else "active"


def _artifact(ctx: RequestContext, share_id: str, kind: str, key: str, field: str) -> dict[str, Any]:
    with tenant_transaction(ctx.tenant_id) as conn:
        share = _load(conn, share_id)
        if share is None or not _allowed(conn, ctx, share):
            raise ShareError("This conversation wasn't shared with you, or the link has expired.", 404)
    item = next((a for a in share["artifacts"] if a["kind"] == kind and a.get(field) == key), None)
    if item is None:
        raise ShareError("Not found in this share.", 404)
    return item


def table_rows(
    ctx: RequestContext, share_id: str, name: str, *, offset: int, limit: int, order_by: str | None, descending: bool
) -> dict[str, Any]:
    item = _artifact(ctx, share_id, "table", name, "name")
    resp = httpx.post(
        f"{settings.worker_url}/v1/rows",
        headers={"X-ODN-Worker-Token": settings.worker_token},
        json={"object_key": assert_share_key(ctx.tenant_id, share_id, item["object_key"]), "offset": offset,
              "limit": limit, "order_by": order_by, "descending": descending},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def open_file(ctx: RequestContext, share_id: str, file_id: str) -> tuple[dict[str, Any], Iterator[bytes]]:
    item = _artifact(ctx, share_id, "file", file_id, "id")
    blob = gcs_client().bucket(settings.gcs_bucket).blob(assert_share_key(ctx.tenant_id, share_id, item["object_key"]))

    def stream() -> Iterator[bytes]:
        with blob.open("rb") as fh:
            while chunk := fh.read(256 * 1024):
                yield chunk

    return item, stream()


def list_shares(ctx: RequestContext, scope: Literal["mine", "with_me"], thread_id: str | None = None) -> list[dict[str, Any]]:
    with tenant_transaction(ctx.tenant_id) as conn:
        if scope == "mine":
            rows = conn.execute(
                f"SELECT {_SUMMARY}, (SELECT count(*) FROM shared_conversation_views v WHERE v.share_id = s.id) AS views, "
                "(SELECT coalesce(json_agg(json_build_object('id', r.user_id, 'email', ru.email, 'name', ru.display_name)), "
                "'[]') FROM shared_conversation_recipients r JOIN users ru ON ru.id = r.user_id WHERE r.share_id = s.id) "
                "AS recipients FROM shared_conversations s JOIN users u ON u.id = s.owner_user_id "
                "WHERE s.owner_user_id = %s AND (%s::text IS NULL OR s.source_thread_id = %s) ORDER BY s.created_at DESC LIMIT 100",
                (ctx.user_id, thread_id, thread_id),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {_SUMMARY}, v.first_viewed_at FROM shared_conversations s JOIN users u ON u.id = s.owner_user_id "
                "LEFT JOIN shared_conversation_views v ON v.share_id = s.id AND v.user_id = %s "
                "WHERE s.owner_user_id <> %s AND s.revoked_at IS NULL AND s.expires_at > now() AND (s.audience = 'organization' "
                "OR EXISTS (SELECT 1 FROM shared_conversation_recipients r WHERE r.share_id = s.id AND r.user_id = %s)) "
                "ORDER BY s.created_at DESC LIMIT 100",
                (ctx.user_id, ctx.user_id, ctx.user_id),
            ).fetchall()
    out = []
    for r in rows:
        item = {k: v for k, v in r.items() if k != "owner_user_id"}
        item["status"] = _status(r)
        out.append(item)
    return out


def people(ctx: RequestContext, query: str, limit: int = 10) -> list[dict[str, Any]]:
    like = f"%{query.strip().lower()}%"
    with tenant_transaction(ctx.tenant_id) as conn:
        return conn.execute(
            "SELECT id, email, display_name FROM users WHERE status = 'active' AND id <> %s AND email IS NOT NULL "
            "AND (lower(email) LIKE %s OR lower(coalesce(display_name, '')) LIKE %s) ORDER BY display_name NULLS LAST, email "
            "LIMIT %s",
            (ctx.user_id, like, like, limit),
        ).fetchall()


def revoke(ctx: RequestContext, share_id: str) -> None:
    with tenant_transaction(ctx.tenant_id) as conn:
        cur = conn.execute(
            "UPDATE shared_conversations SET revoked_at = coalesce(revoked_at, now()) WHERE id = %s "
            "AND (owner_user_id = %s OR %s) RETURNING id",
            (share_id, ctx.user_id, ctx.role == "admin"),
        ).fetchone()
        if cur is None:
            raise ShareError("Share not found.", 404)
        conn.execute(
            "INSERT INTO admin_events (tenant_id, actor_user_id, action, target) VALUES (%s, %s, %s, %s)",
            (ctx.tenant_id, ctx.user_id, "share.revoked", share_id),
        )


def extend(ctx: RequestContext, share_id: str, days: int) -> str:
    if days not in EXPIRY_DAYS:
        raise ShareError(f"Expiry must be one of {EXPIRY_DAYS} days.", 422)
    with tenant_transaction(ctx.tenant_id) as conn:
        row = conn.execute(
            "UPDATE shared_conversations SET expires_at = now() + make_interval(days => %s) "
            "WHERE id = %s AND owner_user_id = %s AND revoked_at IS NULL RETURNING expires_at",
            (days, share_id, ctx.user_id),
        ).fetchone()
    if row is None:
        raise ShareError("Share not found.", 404)
    return row["expires_at"].isoformat()
