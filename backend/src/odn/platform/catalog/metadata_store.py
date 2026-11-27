"""Atomic publication of fully collected metadata scopes."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import batched
from uuid import UUID

from psycopg.rows import dict_row

from odn.db import tenant_transaction
from odn.platform.catalog.metadata_queue import RefreshJob, _lock_authorized
from odn.platform.policy.sql_policy import SqlPolicy

_WRITE_BATCH_SIZE = 1000


class RefreshLeaseLost(Exception):
    """The job no longer owns a valid lease or the connection has changed."""


@dataclass(frozen=True)
class MetadataField:
    path: tuple[str, ...]
    data_type: str
    nullable: bool | None = None
    mode: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class MetadataTable:
    path: tuple[str, ...]
    name: str
    kind: str
    fields: tuple[MetadataField, ...]
    description: str | None = None


@dataclass(frozen=True)
class MetadataContainer:
    path: tuple[str, ...]
    name: str
    kind: str
    tables: tuple[MetadataTable, ...]
    description: str | None = None


def publish_container(
    tenant_id: UUID,
    user_id: UUID,
    connection_id: UUID,
    job_id: UUID,
    lease_token: UUID,
    container: MetadataContainer,
) -> None:
    """Publish a complete container; source calls must finish before this transaction begins."""
    if not container.path or any(not part for part in container.path) or not container.name:
        raise ValueError("metadata container requires a path and name")
    for table in container.tables:
        if table.path[:-1] != container.path or not table.path[-1:] or not table.name or table.name != table.path[-1]:
            raise ValueError("metadata table must belong to its container")
        if any(not field.path or any(not part for part in field.path) for field in table.fields):
            raise ValueError("metadata field requires a nonempty path")

    with tenant_transaction(tenant_id, user_id) as conn:
        _lock_authorized(conn, connection_id, user_id)
        with conn.cursor(row_factory=dict_row) as cur:
            job = cur.execute(
                "SELECT j.connection_revision, c.metadata_revision, c.kind, c.allowed_objects "
                "FROM metadata_refresh_jobs j JOIN connections c ON c.id = j.connection_id "
                "WHERE j.id = %s AND j.connection_id = %s AND j.requested_by = %s "
                "AND j.status = 'running' AND j.lease_token = %s FOR UPDATE OF j",
                (job_id, connection_id, user_id, lease_token),
            ).fetchone()
            if job is None or job["connection_revision"] != job["metadata_revision"]:
                raise RefreshLeaseLost("metadata refresh lease or connection revision changed")
            policy = SqlPolicy(dialect=job["kind"], allowed_objects=frozenset(job["allowed_objects"]))
            if not policy.allows_container(container.path) or any(
                not policy.allows_object(table.path) for table in container.tables
            ):
                raise ValueError("metadata scope contains an object outside the current allowlist")
            live = cur.execute(
                "SELECT lease_until > clock_timestamp() AS valid FROM metadata_refresh_jobs WHERE id = %s",
                (job_id,),
            ).fetchone()
            if live is None or not live["valid"]:
                raise RefreshLeaseLost("metadata refresh lease expired")
            catalog = cur.execute(
                "SELECT connection_revision FROM metadata_catalogs WHERE connection_id = %s FOR UPDATE",
                (connection_id,),
            ).fetchone()
        if catalog and catalog["connection_revision"] != job["metadata_revision"]:
            conn.execute("DELETE FROM metadata_catalogs WHERE connection_id = %s", (connection_id,))
            catalog = None
        if catalog is None:
            conn.execute(
                "INSERT INTO metadata_catalogs (connection_id, connection_revision) VALUES (%s, %s)",
                (connection_id, job["metadata_revision"]),
            )
        conn.execute(
            "DELETE FROM metadata_containers WHERE connection_id = %s AND path = %s",
            (connection_id, list(container.path)),
        )
        conn.execute(
            "INSERT INTO metadata_containers (connection_id, path, name, kind, description, observed_at) "
            "VALUES (%s, %s, %s, %s, %s, clock_timestamp())",
            (connection_id, list(container.path), container.name, container.kind, container.description),
        )
        with conn.cursor() as cur:
            table_rows = (
                (connection_id, list(table.path), list(container.path), table.name, table.kind, table.description)
                for table in container.tables
            )
            for table_batch in batched(table_rows, _WRITE_BATCH_SIZE):
                cur.executemany(
                    "INSERT INTO metadata_tables "
                    "(connection_id, path, container_path, name, kind, description, observed_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, clock_timestamp())",
                    table_batch,
                )
            field_rows = (
                (connection_id, list(table.path), list(field.path), ordinal, field.data_type, field.nullable,
                 field.mode, field.description)
                for table in container.tables for ordinal, field in enumerate(table.fields)
            )
            for field_batch in batched(field_rows, _WRITE_BATCH_SIZE):
                cur.executemany(
                    "INSERT INTO metadata_fields "
                    "(connection_id, table_path, path, ordinal, data_type, nullable, mode, description) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    field_batch,
                )


def finish_refresh(
    tenant_id: UUID,
    job: RefreshJob,
    roots: tuple[tuple[str, ...], ...],
    failed: frozenset[tuple[str, ...]],
) -> None:
    """Reconcile a complete root listing and finish the job in the same transaction."""
    root_set = frozenset(roots)
    if len(root_set) != len(roots) or not failed <= root_set:
        raise ValueError("invalid metadata root results")
    with tenant_transaction(tenant_id, job.actor_id) as conn:
        connection = _lock_authorized(conn, job.connection_id, job.actor_id)
        if connection["metadata_revision"] != job.revision:
            raise RefreshLeaseLost("metadata connection revision changed")
        with conn.cursor(row_factory=dict_row) as cur:
            current = cur.execute(
                "SELECT j.started_at, c.kind, c.allowed_objects FROM metadata_refresh_jobs j "
                "JOIN connections c ON c.id = j.connection_id "
                "WHERE j.id = %s AND j.connection_id = %s AND j.requested_by = %s "
                "AND j.connection_revision = %s AND j.status = 'running' AND j.lease_token = %s "
                "AND j.lease_until > clock_timestamp() FOR UPDATE OF j",
                (job.id, job.connection_id, job.actor_id, job.revision, job.token),
            ).fetchone()
            if current is None or current["started_at"] is None:
                raise RefreshLeaseLost("metadata refresh lease expired or changed")
            policy = SqlPolicy(dialect=current["kind"], allowed_objects=frozenset(current["allowed_objects"]))
            if any(not path or any(not part for part in path) or not policy.allows_container(path) for path in roots):
                raise ValueError("metadata root contains an object outside the current allowlist")
            catalog = cur.execute(
                "SELECT connection_revision FROM metadata_catalogs WHERE connection_id = %s FOR UPDATE",
                (job.connection_id,),
            ).fetchone()
            if catalog and catalog["connection_revision"] != job.revision:
                cur.execute("DELETE FROM metadata_catalogs WHERE connection_id = %s", (job.connection_id,))
                catalog = None
            if catalog is None:
                cur.execute(
                    "INSERT INTO metadata_catalogs (connection_id, connection_revision) VALUES (%s, %s)",
                    (job.connection_id, job.revision),
                )
            existing = cur.execute(
                "SELECT path, observed_at >= %s AS published FROM metadata_containers WHERE connection_id = %s",
                (current["started_at"], job.connection_id),
            ).fetchall()
            published = {tuple(row["path"]) for row in existing if row["published"]}
            if not (root_set - failed) <= published:
                raise ValueError("metadata refresh has unpublished containers")
            for row in existing:
                if tuple(row["path"]) not in root_set:
                    cur.execute(
                        "DELETE FROM metadata_containers WHERE connection_id = %s AND path = %s",
                        (job.connection_id, row["path"]),
                    )
            if not failed:
                cur.execute(
                    "UPDATE metadata_catalogs SET last_success_at = clock_timestamp(), updated_at = clock_timestamp() "
                    "WHERE connection_id = %s", (job.connection_id,),
                )
            else:
                cur.execute(
                    "UPDATE metadata_catalogs SET updated_at = clock_timestamp() WHERE connection_id = %s",
                    (job.connection_id,),
                )
            cur.execute(
                "UPDATE metadata_refresh_jobs SET status = %s, progress_done = %s, progress_total = %s, "
                "error_code = %s, finished_at = clock_timestamp(), lease_token = NULL, lease_until = NULL "
                "WHERE id = %s",
                ("partial" if failed else "succeeded", len(root_set), len(root_set),
                 "incomplete" if failed else None, job.id),
            )
