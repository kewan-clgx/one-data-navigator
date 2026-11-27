"""Tenant-scoped refresh job primitives. No source credentials are stored in jobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

from psycopg.rows import dict_row

from odn.db import tenant_transaction

LEASE_SECONDS = 180
MAX_ATTEMPTS = 3


class RefreshNotAuthorized(Exception):
    """The actor or connection is not eligible for metadata refresh."""


@dataclass(frozen=True)
class RefreshJob:
    id: UUID
    connection_id: UUID
    revision: UUID
    actor_id: UUID
    token: UUID
    attempt: int


def _lock_authorized(conn, connection_id: UUID, actor_id: UUID) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(
            "SELECT c.id, c.tenant_id, c.owner_user_id, c.metadata_revision, u.role "
            "FROM connections c JOIN users u ON u.id = %s AND u.tenant_id = c.tenant_id "
            "WHERE c.id = %s AND u.status = 'active' AND odn_current_tenant_active() "
            "FOR UPDATE OF c FOR SHARE OF u",
            (actor_id, connection_id),
        ).fetchone()
    if row is None or (
        row["owner_user_id"] != actor_id
        and (row["owner_user_id"] is not None or row["role"] not in ("admin", "data_lead"))
    ):
        raise RefreshNotAuthorized("connection refresh is not authorized")
    return row


def enqueue(tenant_id: UUID, actor_id: UUID, connection_id: UUID) -> tuple[UUID, bool]:
    """Queue or coalesce a job; platform connections require a separate operator mechanism."""
    with tenant_transaction(tenant_id, actor_id) as conn:
        connection = _lock_authorized(conn, connection_id, actor_id)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "UPDATE metadata_refresh_jobs SET status = 'failed', finished_at = clock_timestamp(), "
                "lease_token = NULL, lease_until = NULL, error_code = 'attempts_exhausted' "
                "WHERE connection_id = %s AND status = 'running' AND attempt >= %s "
                "AND lease_until <= clock_timestamp()",
                (connection_id, MAX_ATTEMPTS),
            )
            active = cur.execute(
                "SELECT j.id, j.connection_revision, "
                "EXISTS (SELECT 1 FROM users u WHERE u.id = j.requested_by AND u.status = 'active' "
                "AND (u.id = %s OR (%s::uuid IS NULL AND u.role IN ('admin', 'data_lead')))) AS actor_valid "
                "FROM metadata_refresh_jobs j WHERE j.connection_id = %s "
                "AND j.status IN ('queued', 'running') FOR UPDATE OF j",
                (connection["owner_user_id"], connection["owner_user_id"], connection_id),
            ).fetchone()
            if active and active["actor_valid"] and active["connection_revision"] == connection["metadata_revision"]:
                return active["id"], False
            if active:
                cur.execute(
                    "UPDATE metadata_refresh_jobs SET status = 'cancelled', finished_at = clock_timestamp(), "
                    "lease_token = NULL, lease_until = NULL, error_code = 'context_changed' WHERE id = %s",
                    (active["id"],),
                )
            row = cur.execute(
                "INSERT INTO metadata_refresh_jobs (connection_id, connection_revision, requested_by, status) "
                "VALUES (%s, %s, %s, 'queued') RETURNING id",
                (connection_id, connection["metadata_revision"], actor_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("metadata refresh job was not created")
            return row["id"], True


def claim(tenant_id: UUID, actor_id: UUID, connection_id: UUID, job_id: UUID) -> RefreshJob | None:
    """Claim a queued or expired job under the original actor's current authorization."""
    token = uuid4()
    with tenant_transaction(tenant_id, actor_id) as conn:
        connection = _lock_authorized(conn, connection_id, actor_id)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "UPDATE metadata_refresh_jobs SET status = 'failed', finished_at = clock_timestamp(), "
                "lease_token = NULL, lease_until = NULL, error_code = 'attempts_exhausted' "
                "WHERE id = %s AND connection_id = %s AND requested_by = %s AND status = 'running' "
                "AND attempt >= %s AND lease_until <= clock_timestamp()",
                (job_id, connection_id, actor_id, MAX_ATTEMPTS),
            )
            row = cur.execute(
                "UPDATE metadata_refresh_jobs SET status = 'running', lease_token = %s, "
                "lease_until = clock_timestamp() + %s * interval '1 second', attempt = attempt + 1, "
                "started_at = clock_timestamp(), progress_done = 0, progress_total = NULL "
                "WHERE id = %s AND connection_id = %s AND requested_by = %s AND connection_revision = %s "
                "AND attempt < %s AND (status = 'queued' OR "
                "(status = 'running' AND lease_until <= clock_timestamp())) "
                "RETURNING id, connection_id, connection_revision, requested_by, attempt",
                (token, LEASE_SECONDS, job_id, connection_id, actor_id, connection["metadata_revision"], MAX_ATTEMPTS),
            ).fetchone()
            if row is None:
                return None
            return RefreshJob(row["id"], row["connection_id"], row["connection_revision"],
                              row["requested_by"], token, row["attempt"])


def heartbeat(tenant_id: UUID, job: RefreshJob) -> bool:
    """Extend only a still-live lease; expired leases cannot be revived."""
    with tenant_transaction(tenant_id, job.actor_id) as conn:
        connection = _lock_authorized(conn, job.connection_id, job.actor_id)
        if connection["metadata_revision"] != job.revision:
            return False
        result = conn.execute(
            "UPDATE metadata_refresh_jobs SET lease_until = clock_timestamp() + %s * interval '1 second' "
            "WHERE id = %s AND connection_id = %s AND requested_by = %s AND connection_revision = %s "
            "AND status = 'running' AND lease_token = %s AND lease_until > clock_timestamp()",
            (LEASE_SECONDS, job.id, job.connection_id, job.actor_id, job.revision, job.token),
        )
        return result.rowcount == 1


def set_progress(tenant_id: UUID, job: RefreshJob, done: int, total: int) -> bool:
    """Report processed scopes while the actor, revision and lease remain valid."""
    if not 0 <= done <= total:
        raise ValueError("invalid metadata refresh progress")
    with tenant_transaction(tenant_id, job.actor_id) as conn:
        connection = _lock_authorized(conn, job.connection_id, job.actor_id)
        if connection["metadata_revision"] != job.revision:
            return False
        result = conn.execute(
            "UPDATE metadata_refresh_jobs SET progress_done = %s, progress_total = %s "
            "WHERE id = %s AND connection_id = %s AND requested_by = %s AND connection_revision = %s "
            "AND status = 'running' AND lease_token = %s AND lease_until > clock_timestamp() "
            "AND progress_done <= %s AND (progress_total IS NULL OR progress_total = %s)",
            (done, total, job.id, job.connection_id, job.actor_id, job.revision, job.token, done, total),
        )
        return result.rowcount == 1


def fail(
    tenant_id: UUID, job: RefreshJob, reason: Literal["source_error", "incomplete", "credential_error"]
) -> bool:
    """Finish a failed job without storing source errors or modifying the published catalog."""
    if reason not in ("source_error", "incomplete", "credential_error"):
        raise ValueError("invalid metadata refresh failure code")
    with tenant_transaction(tenant_id, job.actor_id) as conn:
        connection = _lock_authorized(conn, job.connection_id, job.actor_id)
        if connection["metadata_revision"] != job.revision:
            return False
        result = conn.execute(
            "UPDATE metadata_refresh_jobs SET status = 'failed', finished_at = clock_timestamp(), "
            "lease_token = NULL, lease_until = NULL, error_code = %s "
            "WHERE id = %s AND connection_id = %s AND requested_by = %s AND connection_revision = %s "
            "AND status = 'running' AND lease_token = %s AND lease_until > clock_timestamp()",
            (reason, job.id, job.connection_id, job.actor_id, job.revision, job.token),
        )
        return result.rowcount == 1
