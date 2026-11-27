"""Authorized, bounded BigQuery column profiling and durable job execution."""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
import sqlglot
from google.api_core import exceptions as gexc
from google.auth import exceptions as auth_exc
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field
from sqlglot import exp

from odn.config import settings
from odn.db import system_transaction, tenant_transaction
from odn.platform.catalog.column_profile_sql import profile_sql
from odn.platform.connections.service import (
    ConnectionProfile,
    CredentialError,
    bigquery_connector,
    build_policy,
    resolve_profile_credentials,
)
from odn.platform.connectors.bigquery import BigQueryConnector
from odn.platform.policy.sql_policy import SqlPolicy
from odn.tenancy.context import RequestContext

log = logging.getLogger(__name__)
_TABLE_LIMIT = 50 * 10**9
_MIN_RESERVATION = 10 * 1024**2


class ProfileNotAllowed(Exception):
    pass


class ProfileUnavailable(Exception):
    pass


class ProfileField(BaseModel):
    path: tuple[str, ...]
    data_type: str
    filled_rows: int
    min_value: str | None
    max_value: str | None
    schema_signature: tuple[str, ...]


class TableProfile(BaseModel):
    sampled_at: datetime | None = None
    sample_rows: int | None = None
    fields: list[ProfileField] = Field(default_factory=list)
    job_status: Literal["queued", "running", "succeeded", "failed", "skipped", "cancelled"] | None = None
    error_code: str | None = None
    credential_changed: bool = False


def _profile_connector(profile: ConnectionProfile) -> tuple[BigQueryConnector, str]:
    credentials, fingerprint = resolve_profile_credentials(profile)
    connector = bigquery_connector(profile.config, credentials)
    if not isinstance(connector, BigQueryConnector):
        raise ProfileUnavailable("BigQuery profiling is unavailable.")
    return connector, fingerprint


def _check_path(profile: ConnectionProfile, path: tuple[str, ...]) -> None:
    if profile.kind != "bigquery":
        raise ProfileUnavailable("Column profiling is available for BigQuery connections only.")
    if len(path) != 3 or any(not part or any(c in part for c in ".`\\") for part in path):
        raise ProfileNotAllowed("Unsupported table path.")
    if not SqlPolicy(dialect="bigquery", allowed_objects=frozenset(profile.allowed_objects)).allows_object(path):
        raise ProfileNotAllowed("Table is not in the connection allowlist.")


def get_profile(ctx: RequestContext, profile: ConnectionProfile, path: tuple[str, ...]) -> TableProfile:
    _check_path(profile, path)
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        row = conn.execute(
            "SELECT sampled_at, sample_rows, fields, credential_fingerprint FROM metadata_column_profiles "
            "WHERE tenant_id = %s AND connection_id = %s AND table_path = %s",
            (ctx.tenant_id, profile.id, list(path)),
        ).fetchone()
        job = conn.execute(
            "SELECT j.status, j.error_code FROM metadata_profile_jobs j "
            "JOIN connections c ON c.id = j.connection_id AND c.metadata_revision = j.connection_revision "
            "WHERE j.tenant_id = %s AND j.connection_id = %s AND j.table_path = %s "
            "ORDER BY j.created_at DESC, j.id DESC LIMIT 1",
            (ctx.tenant_id, profile.id, list(path)),
        ).fetchone()
    changed = False
    if row:
        connector, fingerprint = _profile_connector(profile)
        changed = row["credential_fingerprint"] != fingerprint
        if changed:
            row = None
    fields = [ProfileField.model_validate(item) for item in row["fields"]] if row else []
    if fields:
        try:
            _, current_fields = profile_sql(path, connector.profile_schema(path))
        except ValueError:
            raise ProfileNotAllowed("The current source schema cannot safely be profiled.") from None
        current = {item.path: item.schema_signature for item in current_fields}
        fields = [field for field in fields if current.get(field.path) == field.schema_signature]
    return TableProfile(
        sampled_at=row["sampled_at"] if row else None,
        sample_rows=row["sample_rows"] if row else None,
        fields=fields,
        job_status=job["status"] if job else None,
        error_code=job["error_code"] if job else None,
        credential_changed=changed,
    )


def request_profile(
    ctx: RequestContext, profile: ConnectionProfile, path: tuple[str, ...], *, automatic: bool = False
) -> bool:
    """Queue a visible table, coalescing active requests; return whether a new job was queued."""
    _check_path(profile, path)
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        connection = conn.execute(
            "SELECT c.metadata_revision FROM connections c JOIN users u "
            "ON u.id = %s AND u.tenant_id = %s AND u.status = 'active' "
            "WHERE c.id = %s AND odn_current_tenant_active()",
            (ctx.user_id, ctx.tenant_id, profile.id),
        ).fetchone()
        if connection is None:
            raise ProfileNotAllowed("Connection or user is no longer active.")
        if automatic:
            existing = conn.execute(
                "SELECT credential_fingerprint FROM metadata_column_profiles WHERE tenant_id = %s AND connection_id = %s "
                "AND table_path = %s",
                (ctx.tenant_id, profile.id, list(path)),
            ).fetchone()
            recent_failure = conn.execute(
                "SELECT 1 FROM metadata_profile_jobs WHERE tenant_id = %s AND connection_id = %s "
                "AND table_path = %s AND status IN ('failed', 'skipped') "
                "AND finished_at > clock_timestamp() - interval '24 hours' LIMIT 1",
                (ctx.tenant_id, profile.id, list(path)),
            ).fetchone()
            if existing:
                _, current_fingerprint = resolve_profile_credentials(profile)
                if existing["credential_fingerprint"] == current_fingerprint:
                    return False
            if recent_failure:
                return False
        result = conn.execute(
            "INSERT INTO metadata_profile_jobs "
            "(tenant_id, connection_id, connection_revision, requested_by, table_path, automatic, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'queued') "
            "ON CONFLICT (tenant_id, connection_id, table_path) WHERE status IN ('queued', 'running') "
            "DO NOTHING RETURNING id",
            (ctx.tenant_id, profile.id, connection["metadata_revision"], ctx.user_id, list(path), automatic),
        ).fetchone()
    return result is not None


def request_automatic(ctx: RequestContext, profile: ConnectionProfile, paths: list[tuple[str, ...]]) -> None:
    if not settings.column_profiling_preview or profile.kind != "bigquery":
        return
    for path in paths:
        try:
            request_profile(ctx, profile, path, automatic=True)
        except (ProfileNotAllowed, ProfileUnavailable, CredentialError, gexc.GoogleAPIError, auth_exc.GoogleAuthError,
                psycopg.Error, ValueError) as exc:
            log.warning("automatic column profile could not be queued: %s", type(exc).__name__)


def query_paths(sql: str, connector: BigQueryConnector, approved: tuple[str, ...]) -> list[tuple[str, ...]]:
    root = sqlglot.parse_one(sql, read="bigquery")
    if not isinstance(root, exp.Query):
        raise TypeError("Expected a query")
    ctes = SqlPolicy._cte_reference_ids(root)
    paths: set[tuple[str, ...]] = set()
    for table in root.find_all(exp.Table):
        if id(table) in ctes:
            continue
        parts = tuple(str(part.name) for part in table.parts)
        missing = 3 - len(parts)
        if missing < 0 or len(connector.default_namespace) < missing:
            continue
        path = (*connector.default_namespace[:missing], *parts)
        if ".".join(path).lower() in approved and all("." not in part for part in path):
            paths.add(path)
    # Avoid choosing a spelling when SQL refers to two case-distinct objects.
    counts = {tuple(p.lower() for p in path): 0 for path in paths}
    for path in paths:
        counts[tuple(p.lower() for p in path)] += 1
    return [path for path in paths if counts[tuple(p.lower() for p in path)] == 1]


def _finish(tenant: UUID, actor: UUID, job: UUID, token: UUID, status: str, error: str | None) -> None:
    with tenant_transaction(tenant, actor) as conn:
        updated = conn.execute(
            "UPDATE metadata_profile_jobs SET status = %s, error_code = %s, finished_at = clock_timestamp(), "
            "lease_token = NULL, lease_until = NULL WHERE id = %s AND requested_by = %s "
            "AND status = 'running' AND lease_token = %s AND lease_until > clock_timestamp() RETURNING id",
            (status, error, job, actor, token),
        ).fetchone()
        if updated is None:
            log.warning("column profile job could not record its outcome; lease changed")


def _reservation(tenant: UUID, actor: UUID, job: UUID, token: UUID, estimate: int, source_job_id: str) -> int | None:
    if estimate <= 0:
        return None
    amount = max(_MIN_RESERVATION, ((estimate + 1024**2 - 1) // 1024**2) * 1024**2)
    with tenant_transaction(tenant, actor) as conn:
        cap = conn.execute("SELECT * FROM odn_profile_actor_cap(%s, %s)", (job, token)).fetchone()
        if cap is None:
            raise ProfileNotAllowed("Profiling actor, connection or lease is no longer valid.")
        role_limit = settings.admin_max_bytes_billed if cap["role"] == "admin" else settings.max_bytes_billed
        limit = min(_TABLE_LIMIT, role_limit)
        if cap["max_bytes_billed"] is not None:
            limit = min(limit, cap["max_bytes_billed"])
        if amount > limit:
            return None
        reserved = conn.execute(
            "SELECT odn_reserve_profile_budget(%s, %s, %s, %s) AS reserved", (job, token, amount, source_job_id),
        ).fetchone()
    return amount if reserved and reserved["reserved"] else None


def _run(tenant: UUID, actor: UUID, connection_id: UUID, job: UUID, token: UUID) -> None:
    from odn.platform.connections.service import get_connection

    with tenant_transaction(tenant, actor) as conn:
        cap = conn.execute("SELECT max_bytes_billed FROM odn_profile_actor_cap(%s, %s)", (job, token)).fetchone()
        record = conn.execute(
            "SELECT table_path, automatic FROM metadata_profile_jobs WHERE id = %s AND lease_token = %s",
            (job, token),
        ).fetchone()
        if cap is None or record is None:
            raise ProfileNotAllowed("Profiling actor, connection or lease is no longer valid.")
        path = tuple(record["table_path"])
    profile = get_connection(tenant, actor, connection_id)
    if profile is None or profile.kind != "bigquery":
        raise ProfileNotAllowed("Connection is no longer available.")
    connector, fingerprint = _profile_connector(profile)
    if not build_policy(profile, connector).allows_object(path):
        raise ProfileNotAllowed("Table is no longer authorized.")
    if record["automatic"]:
        with tenant_transaction(tenant, actor) as conn:
            existing = conn.execute(
                "SELECT credential_fingerprint FROM metadata_column_profiles WHERE tenant_id = %s "
                "AND connection_id = %s AND table_path = %s", (tenant, connection_id, list(path)),
            ).fetchone()
        if existing and existing["credential_fingerprint"] == fingerprint:
            _finish(tenant, actor, job, token, "cancelled", "already_profiled")
            return
    sql, fields = profile_sql(path, connector.profile_schema(path))
    estimate = connector.estimate_cost(sql).bytes_processed
    if estimate is None or estimate <= 0:
        _finish(tenant, actor, job, token, "skipped", "estimate_unavailable")
        return
    source_job_id = f"odn_profile_{uuid4().hex}"
    reserved = _reservation(tenant, actor, job, token, estimate, source_job_id)
    if reserved is None:
        _finish(tenant, actor, job, token, "skipped", "budget_exceeded")
        return
    result = connector.profile_aggregate(sql, reserved, source_job_id)
    sample_rows = result["sample_rows"]
    if not isinstance(sample_rows, int):
        raise TypeError("Invalid sample count")
    if sample_rows <= 0:
        _finish(tenant, actor, job, token, "skipped", "empty_sample")
        return
    values: list[dict[str, Any]] = []
    for i, field in enumerate(fields):
        filled = result[f"filled_{i}"]
        if not isinstance(filled, int):
            raise TypeError("Invalid sampled field count")
        if not 0 <= filled <= sample_rows:
            raise ValueError("Invalid sampled field count")
        values.append({
            "path": list(field.path), "data_type": field.data_type, "filled_rows": filled,
            "min_value": result[f"min_{i}"] if field.has_range else None,
            "max_value": result[f"max_{i}"] if field.has_range else None,
            "schema_signature": list(field.schema_signature),
        })
    with tenant_transaction(tenant, actor) as conn:
        if conn.execute("SELECT 1 FROM odn_profile_actor_cap(%s, %s)", (job, token)).fetchone() is None:
            raise ProfileNotAllowed("Profiling actor, connection or lease changed.")
        # Re-read policy immediately before publishing data-derived values.
        current = conn.execute(
            "SELECT kind, allowed_objects, metadata_revision FROM connections WHERE id = %s", (connection_id,),
        ).fetchone()
        if current is None or not build_policy(profile, connector).allows_object(path):
            raise ProfileNotAllowed("Table is no longer authorized.")
        if not SqlPolicy(dialect=current["kind"], allowed_objects=frozenset(current["allowed_objects"])).allows_object(path):
            raise ProfileNotAllowed("Table is no longer authorized.")
        current_profile = get_connection(tenant, actor, connection_id)
        if current_profile is None or resolve_profile_credentials(current_profile)[1] != fingerprint:
            raise ProfileNotAllowed("Source credentials changed before publication.")
        conn.execute(
            "INSERT INTO metadata_column_profiles "
            "(tenant_id, connection_id, connection_revision, credential_fingerprint, table_path, "
            "sampled_at, sample_rows, fields) "
            "VALUES (%s, %s, %s, %s, %s, clock_timestamp(), %s, %s) "
            "ON CONFLICT (tenant_id, connection_id, table_path) DO UPDATE "
            "SET connection_revision = EXCLUDED.connection_revision, "
            "credential_fingerprint = EXCLUDED.credential_fingerprint, sampled_at = EXCLUDED.sampled_at, "
            "sample_rows = EXCLUDED.sample_rows, fields = EXCLUDED.fields",
            (tenant, connection_id, current["metadata_revision"], fingerprint, list(path), sample_rows, Jsonb(values)),
        )
        updated = conn.execute(
            "UPDATE metadata_profile_jobs SET status = 'succeeded', finished_at = clock_timestamp(), "
            "error_code = NULL, lease_token = NULL, lease_until = NULL WHERE id = %s AND lease_token = %s "
            "AND status = 'running' AND lease_until > clock_timestamp() RETURNING id",
            (job, token),
        ).fetchone()
        if updated is None:
            raise ProfileNotAllowed("Profiling lease changed before publication.")


def run_worker(stop: threading.Event) -> None:
    """Claim durable jobs until shutdown; no metadata or raw source values are logged."""
    while not stop.is_set():
        try:
            with system_transaction() as conn:
                claimed = conn.execute("SELECT * FROM odn_claim_profile_job()").fetchone()
            if claimed is None:
                stop.wait(2)
                continue
            try:
                _run(claimed["tenant_id"], claimed["requested_by"], claimed["connection_id"],
                     claimed["id"], claimed["token"])
            except (ProfileNotAllowed, ProfileUnavailable, CredentialError, gexc.GoogleAPIError,
                    auth_exc.GoogleAuthError, psycopg.Error, TypeError, ValueError, TimeoutError) as exc:
                log.warning("column profiling failed: %s", type(exc).__name__)
                _finish(claimed["tenant_id"], claimed["requested_by"], claimed["id"], claimed["token"],
                        "failed", "profile_failed")
        except Exception as exc:  # noqa: BLE001 - keep the worker alive and report unexpected failures
            log.error("column profiling worker error: %s", type(exc).__name__)
            stop.wait(2)
