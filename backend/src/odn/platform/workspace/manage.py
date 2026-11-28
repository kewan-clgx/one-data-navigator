"""Delete, archive and restore for every artifact kind (docs/artifact_actions_plan.md).

Delete removes the row (and its stored object) at once. Archive hides an artifact from the Artifacts list and search
but keeps it, for 30 days or forever; `run_purger` deletes the ones whose time is up.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from google.api_core import exceptions as gexc

from odn.config import settings
from odn.db import system_transaction, tenant_transaction
from odn.platform.workspace.storage import gcs_client, workspace_prefix
from odn.tenancy.context import RequestContext

log = logging.getLogger(__name__)

Kind = Literal["table", "chart", "file", "sql"]
Keep = Literal["30d", "forever"]
ARCHIVE_DAYS = 30
PURGE_EVERY_S = 3600

_TABLE: dict[str, str] = {
    "table": "workspace_artifacts", "chart": "workspace_charts", "file": "workspace_files", "sql": "saved_queries",
}


def _delete_object(tenant_id: object, key: str | None) -> None:
    """Best effort: a leftover object is unreachable (no row points at it) and only costs storage."""
    if not key or not key.startswith(workspace_prefix(str(tenant_id)) + "/"):
        return
    try:
        gcs_client().bucket(settings.gcs_bucket).blob(key).delete()
    except gexc.NotFound:
        pass
    except Exception as exc:  # noqa: BLE001 - never fail the user's delete on storage hiccups
        log.warning("artifact object not deleted: %s", type(exc).__name__)


def _raise_floor(conn: Any, ctx: RequestContext, session_id: str, prefix: str, name: str) -> None:
    """Deleted q/c numbers are never reused in the conversation (see migration 0041)."""
    try:
        n = int(name[1:])
    except ValueError:
        return
    conn.execute(
        "INSERT INTO workspace_name_floor AS f (tenant_id, user_id, session_id, prefix, last_n) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (tenant_id, user_id, session_id, prefix) "
        "DO UPDATE SET last_n = greatest(f.last_n, EXCLUDED.last_n)",
        (ctx.tenant_id, ctx.user_id, session_id, prefix, n),
    )


def delete(ctx: RequestContext, kind: Kind, item_id: UUID) -> None:
    """Deletes one of the user's artifacts for good. Raises KeyError when it isn't theirs."""
    key: str | None = None
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        cols = {"table": "session_id, name, object_key", "chart": "session_id, name", "file": "object_key",
                "sql": "id"}[kind]
        row = conn.execute(
            f"DELETE FROM {_TABLE[kind]} WHERE id = %s AND tenant_id = %s AND user_id = %s RETURNING {cols}",
            (item_id, ctx.tenant_id, ctx.user_id),
        ).fetchone()
        if row is None:
            raise KeyError(str(item_id))
        if kind in ("table", "chart"):
            _raise_floor(conn, ctx, row["session_id"], "q" if kind == "table" else "c", row["name"])
        key = row.get("object_key")
    _delete_object(ctx.tenant_id, key)


def archive(ctx: RequestContext, kind: Kind, item_id: UUID, keep: Keep) -> datetime | None:
    """Hides the artifact from the list and search; returns when it will be deleted (None: kept forever)."""
    purge_at = datetime.now(UTC) + timedelta(days=ARCHIVE_DAYS) if keep == "30d" else None
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        cur = conn.execute(
            f"UPDATE {_TABLE[kind]} SET archived_at = now(), purge_at = %s "
            "WHERE id = %s AND tenant_id = %s AND user_id = %s",
            (purge_at, item_id, ctx.tenant_id, ctx.user_id),
        )
        if cur.rowcount == 0:
            raise KeyError(str(item_id))
    return purge_at


def restore(ctx: RequestContext, kind: Kind, item_id: UUID) -> None:
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        cur = conn.execute(
            f"UPDATE {_TABLE[kind]} SET archived_at = NULL, purge_at = NULL "
            "WHERE id = %s AND tenant_id = %s AND user_id = %s",
            (item_id, ctx.tenant_id, ctx.user_id),
        )
        if cur.rowcount == 0:
            raise KeyError(str(item_id))


def purge_due(limit: int = 500) -> int:
    """Deletes archived artifacts whose 30 days are up (all tenants) and their stored objects; returns the count."""
    with system_transaction() as conn:
        rows = conn.execute("SELECT * FROM odn_purge_archived_artifacts(%s)", (limit,)).fetchall()
    for r in rows:
        _delete_object(r["tenant_id"], r["object_key"])
    return len(rows)


def run_purger(stop: threading.Event) -> None:
    """Hourly: purge archived artifacts that are due (a few hours late is fine; they're already hidden)."""
    while not stop.is_set():
        try:
            while purge_due() > 0 and not stop.is_set():
                pass
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            log.error("artifact purge failed: %s", type(exc).__name__)
        stop.wait(PURGE_EVERY_S)
