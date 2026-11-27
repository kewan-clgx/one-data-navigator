"""Bounded, explicitly requested refresh execution for the Explorer preview."""

from __future__ import annotations

import logging
import threading
from uuid import UUID

from psycopg.rows import dict_row

from odn.db import tenant_transaction
from odn.platform.catalog import metadata_queue, metadata_refresh

log = logging.getLogger(__name__)
_slot = threading.Lock()


class RefreshBusy(Exception):
    """This process is already running a preview refresh."""


def _job_actor(tenant_id: UUID, actor_id: UUID, connection_id: UUID, job_id: UUID) -> UUID:
    with tenant_transaction(tenant_id, actor_id) as conn:
        metadata_queue._lock_authorized(conn, connection_id, actor_id)
        with conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                "SELECT requested_by FROM metadata_refresh_jobs WHERE id = %s AND connection_id = %s "
                "AND status IN ('queued', 'running')",
                (job_id, connection_id),
            ).fetchone()
    if row is None or row["requested_by"] is None:
        raise metadata_queue.RefreshNotAuthorized("refresh job is no longer available")
    return row["requested_by"]


def request_refresh(tenant_id: UUID, actor_id: UUID, connection_id: UUID) -> tuple[UUID, bool]:
    """Enqueue and attempt a refresh; retry the request after a process restart or expired lease."""
    if not _slot.acquire(blocking=False):
        raise RefreshBusy("another metadata refresh is running; retry later")
    started = False
    try:
        job_id, created = metadata_queue.enqueue(tenant_id, actor_id, connection_id)
        job_actor = _job_actor(tenant_id, actor_id, connection_id, job_id)

        def run() -> None:
            try:
                metadata_refresh.run_job(tenant_id, job_actor, connection_id, job_id)
            except Exception as exc:  # noqa: BLE001 - background failures must be logged, not lost
                log.error("metadata preview worker stopped: %s", type(exc).__name__)
            finally:
                _slot.release()

        thread = threading.Thread(target=run, name="metadata-preview-refresh", daemon=True)
        thread.start()
        started = True
        return job_id, created
    finally:
        if not started:
            _slot.release()
