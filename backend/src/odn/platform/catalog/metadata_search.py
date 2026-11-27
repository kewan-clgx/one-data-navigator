"""Bounded lexical search over the caller's visible persisted metadata."""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from psycopg import Cursor
from psycopg.rows import dict_row
from pydantic import BaseModel

from odn.db import tenant_transaction
from odn.platform.policy.sql_policy import SqlPolicy

_SNAPSHOT = r"(^|_)(19|20)[0-9]{2}(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])([0-9]{2}){0,3}(_|$)"


class MetadataConnectionNotFound(Exception):
    pass


class InvalidCursor(ValueError):
    pass


class MetadataAuthorizationMismatch(Exception):
    pass


class MetadataStatus(BaseModel):
    state: Literal["not_indexed", "building", "ready", "stale", "partial", "failed"]
    last_success_at: datetime | None
    job_status: str | None
    error_code: str | None
    progress_done: int | None
    progress_total: int | None
    started_at: datetime | None
    finished_at: datetime | None
    last_completed_status: str | None
    last_completed_error_code: str | None
    can_refresh: bool


class FieldHit(BaseModel):
    path: tuple[str, ...]
    data_type: str
    description: str | None


class TableHit(BaseModel):
    path: tuple[str, ...]
    container_path: tuple[str, ...]
    container_name: str
    name: str
    kind: str
    description: str | None
    observed_at: datetime
    score: int
    matching_fields: list[FieldHit]


class SearchPage(BaseModel):
    status: MetadataStatus
    items: list[TableHit]
    next_cursor: str | None


def _read_status(cur: Cursor[dict[str, Any]], connection_id: UUID) -> tuple[dict[str, Any], MetadataStatus]:
    connection = cur.execute(
        "SELECT c.metadata_revision, c.kind, c.allowed_objects, "
        "CASE WHEN m.connection_revision = c.metadata_revision THEN m.last_success_at END AS last_success_at, "
        "j.status AS job_status, j.error_code, j.progress_done, j.progress_total, j.started_at, j.finished_at, "
        "completed.status AS last_completed_status, completed.error_code AS last_completed_error_code, "
        "EXISTS (SELECT 1 FROM users u WHERE u.id = odn_current_user() AND u.tenant_id = c.tenant_id "
        "AND u.status = 'active' AND odn_current_tenant_active() AND "
        "(c.owner_user_id = u.id OR (c.owner_user_id IS NULL AND u.role IN ('admin', 'data_lead')))) "
        "AS can_refresh "
        "FROM connections c LEFT JOIN metadata_catalogs m ON m.connection_id = c.id "
        "LEFT JOIN LATERAL (SELECT status, error_code, progress_done, progress_total, started_at, finished_at "
        "FROM metadata_refresh_jobs WHERE connection_id = c.id AND connection_revision = c.metadata_revision "
        "ORDER BY created_at DESC, id DESC LIMIT 1) j ON true "
        "LEFT JOIN LATERAL (SELECT status, error_code FROM metadata_refresh_jobs "
        "WHERE connection_id = c.id AND connection_revision = c.metadata_revision "
        "AND status IN ('succeeded', 'partial', 'failed', 'cancelled') "
        "ORDER BY finished_at DESC NULLS LAST, created_at DESC, id DESC LIMIT 1) completed ON true "
        "WHERE c.id = %s",
        (connection_id,),
    ).fetchone()
    if connection is None:
        raise MetadataConnectionNotFound("connection not found")
    job_status, last_success = connection["job_status"], connection["last_success_at"]
    state: Literal["not_indexed", "building", "ready", "stale", "partial", "failed"]
    if job_status in ("queued", "running"):
        state = "building"
    elif job_status == "partial":
        state = "partial"
    elif job_status in ("failed", "cancelled"):
        state = "stale" if last_success else "failed"
    else:
        state = "ready" if last_success else "not_indexed"
    return connection, MetadataStatus(
        state=state,
        last_success_at=last_success,
        job_status=job_status,
        error_code=connection["error_code"],
        progress_done=connection["progress_done"],
        progress_total=connection["progress_total"],
        started_at=connection["started_at"],
        finished_at=connection["finished_at"],
        last_completed_status=connection["last_completed_status"],
        last_completed_error_code=connection["last_completed_error_code"],
        can_refresh=connection["can_refresh"],
    )


def get_status(tenant_id: UUID, user_id: UUID, connection_id: UUID) -> MetadataStatus:
    with tenant_transaction(tenant_id, user_id) as conn, conn.cursor(row_factory=dict_row) as cur:
        return _read_status(cur, connection_id)[1]


def _cursor_encode(revision: UUID, terms: list[str], snapshots: bool, score: int, path: tuple[str, ...]) -> str:
    data = json.dumps([str(revision), terms, snapshots, score, path], separators=(",", ":"), ensure_ascii=False)
    return base64.urlsafe_b64encode(data.encode()).decode().rstrip("=")


def _cursor_decode(
    cursor: str, revision: UUID, terms: list[str], snapshots: bool
) -> tuple[int, tuple[str, ...]]:
    try:
        value = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
        if (
            not isinstance(value, list) or len(value) != 5 or value[0] != str(revision)
            or value[1] != terms or value[2] is not snapshots or type(value[3]) is not int
            or not 0 <= value[3] <= 3000 + len(terms) or not isinstance(value[4], list)
            or not 1 <= len(value[4]) <= 3
            or any(not isinstance(p, str) or not p or "\x00" in p for p in value[4])
        ):
            raise InvalidCursor("cursor does not match this search or connection revision")
        return value[3], tuple(value[4])
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error) as exc:
        raise InvalidCursor("invalid or outdated search cursor") from exc


def search(
    tenant_id: UUID,
    user_id: UUID,
    connection_id: UUID,
    query: str,
    *,
    limit: int = 20,
    include_snapshots: bool = False,
    cursor: str | None = None,
) -> SearchPage:
    terms = [term.lower() for term in query.split()]
    if not terms or "\x00" in query or len(terms) > 8 or len(query) > 300 or not 1 <= limit <= 100:
        raise ValueError("search requires 1-8 terms, at most 300 characters, and a limit of 1-100")
    if cursor is not None and len(cursor) > 16384:
        raise InvalidCursor("search cursor is too long")
    with tenant_transaction(tenant_id, user_id) as conn, conn.cursor(row_factory=dict_row) as cur:
        # RLS inflates planner costs; compiling this short-lived query can dominate its execution time.
        cur.execute("SET LOCAL jit = off")
        cur.execute(
            "SELECT set_config('statement_timeout', "
            "least(CASE WHEN setting::int = 0 THEN 5000 ELSE setting::int END, 5000)::text, true) "
            "FROM pg_settings WHERE name = 'statement_timeout'"
        )
        connection, status = _read_status(cur, connection_id)
        revision = connection["metadata_revision"]
        policy = SqlPolicy(dialect=connection["kind"], allowed_objects=frozenset(connection["allowed_objects"]))
        position = _cursor_decode(cursor, revision, terms, include_snapshots) if cursor else None
        rows = cur.execute(
                """
                WITH favorites AS MATERIALIZED (
                  SELECT object_path, kind FROM user_favorites
                  WHERE connection_id = %(connection_id)s AND user_id = %(user_id)s
                ),
                recent AS MATERIALIZED (
                  SELECT table_path, (row_number() OVER (ORDER BY last_used_at DESC, table_path) - 1)::int AS pos
                  FROM user_table_usage WHERE connection_id = %(connection_id)s AND user_id = %(user_id)s
                  ORDER BY last_used_at DESC, table_path LIMIT 50
                ),
                field_matches AS MATERIALIZED (
                  SELECT f.table_path, term
                  FROM metadata_fields f
                  LEFT JOIN data_definitions d
                    ON d.connection_id = f.connection_id AND d.tenant_id = odn_current_tenant()
                    AND d.table_path = array_to_string(f.table_path, '.')
                    AND d.field_path = array_to_string(f.path, '.') AND d.status <> 'deprecated'
                  CROSS JOIN unnest(%(terms)s::text[]) term
                  WHERE f.connection_id = %(connection_id)s
                    AND (strpos(lower(array_to_string(f.path, '.')), term) > 0
                      OR strpos(lower(coalesce(
                           CASE WHEN d.definition IS NULL THEN f.description
                                WHEN f.description IS NULL OR f.description = '' THEN d.definition
                                ELSE d.definition || ' ' || chr(8212) || ' ' || f.description END, '')), term) > 0)
                ),
                candidates AS (
                  SELECT t.path, t.container_path, d.name AS container_name, t.name, t.kind,
                         left(t.description, 500) AS description, t.observed_at,
                         (SELECT count(*)::int FROM unnest(%(terms)s::text[]) term
                          WHERE strpos(lower(t.name), term) > 0)
                         + coalesce(
                           (SELECT 3000 FROM favorites fav WHERE fav.kind = 'table'
                            AND fav.object_path = CASE WHEN c.kind IN ('snowflake', 'mysql', 'oracle')
                              THEN array_to_string(t.path, '.') ELSE lower(array_to_string(t.path, '.')) END LIMIT 1),
                           (SELECT 2000 - r.pos FROM recent r WHERE r.table_path =
                            CASE WHEN c.kind IN ('snowflake', 'mysql', 'oracle')
                              THEN array_to_string(t.path, '.') ELSE lower(array_to_string(t.path, '.')) END LIMIT 1),
                           (SELECT 1000 FROM favorites fav WHERE fav.kind = 'dataset'
                            AND fav.object_path = CASE WHEN c.kind IN ('snowflake', 'mysql', 'oracle')
                              THEN array_to_string(t.container_path, '.')
                              ELSE lower(array_to_string(t.container_path, '.')) END LIMIT 1),
                           0) AS score
                  FROM metadata_tables t
                  JOIN metadata_containers d ON d.connection_id = t.connection_id AND d.path = t.container_path
                  JOIN connections c ON c.id = t.connection_id
                  WHERE t.connection_id = %(connection_id)s AND c.metadata_revision = %(revision)s
                    AND (%(snapshots)s OR t.name !~ %(snapshot_pattern)s)
                    AND CASE c.kind
                      WHEN 'mysql' THEN cardinality(t.path) = 2
                        AND lower(t.path[1]) NOT IN ('information_schema', 'performance_schema', 'mysql', 'sys')
                        AND NOT EXISTS (SELECT 1 FROM unnest(t.path) part
                                        WHERE part !~ '^[A-Za-z_][A-Za-z0-9_$]{0,63}$')
                      WHEN 'oracle' THEN cardinality(t.path) = 2
                        AND upper(t.path[1]) NOT IN (
                          'SYS', 'SYSTEM', 'OUTLN', 'XDB', 'DBSNMP', 'CTXSYS', 'MDSYS', 'ORDSYS', 'ORDDATA',
                          'OLAPSYS', 'WMSYS', 'LBACSYS', 'DVSYS', 'DVF', 'AUDSYS', 'GSMADMIN_INTERNAL',
                          'APPQOSSYS', 'OJVMSYS', 'REMOTE_SCHEDULER_AGENT', 'ANONYMOUS')
                        AND upper(t.path[2]) <> 'DUAL'
                        AND NOT EXISTS (SELECT 1 FROM unnest(t.path) part
                                        WHERE part !~ '^[A-Za-z][A-Za-z0-9_$]{0,127}$')
                      WHEN 'snowflake' THEN NOT EXISTS (
                        SELECT 1 FROM unnest(t.path) part
                        WHERE part !~ '^[A-Za-z_][A-Za-z0-9_$]{0,254}$'
                          OR strpos(lower(part), 'information_schema') > 0
                          OR strpos(lower(part), 'pg_catalog') > 0
                          OR strpos(lower(part), 'pg_toast') > 0
                      )
                      ELSE true
                    END
                    AND EXISTS (
                      SELECT 1 FROM unnest(c.allowed_objects) AS a(pattern)
                      WHERE cardinality(string_to_array(a.pattern, '.')) = cardinality(t.path)
                        AND NOT EXISTS (
                          SELECT 1 FROM unnest(t.path) WITH ORDINALITY AS part(value, n)
                          WHERE (string_to_array(a.pattern, '.'))[part.n::int] <> '*'
                            AND CASE WHEN c.kind IN ('snowflake', 'mysql', 'oracle')
                              THEN part.value <> (string_to_array(a.pattern, '.'))[part.n::int]
                              ELSE lower(part.value) <> lower((string_to_array(a.pattern, '.'))[part.n::int])
                            END
                        )
                    )
                    AND NOT EXISTS (
                      SELECT 1 FROM unnest(%(terms)s::text[]) term
                      WHERE strpos(lower(d.name || ' ' || t.name), term) = 0
                        AND NOT EXISTS (
                          SELECT 1 FROM field_matches fm
                          WHERE fm.table_path = t.path AND fm.term = term
                        )
                    )
                ),
                page AS MATERIALIZED (
                  SELECT * FROM candidates x
                  WHERE (%(after_score)s::int IS NULL OR x.score < %(after_score)s
                         OR (x.score = %(after_score)s AND x.path > %(after_path)s::text[]))
                  ORDER BY x.score DESC, x.path LIMIT %(batch)s
                ),
                page_fields AS MATERIALIZED (
                  SELECT f.table_path, f.path, f.ordinal, f.data_type,
                         CASE WHEN d.definition IS NULL THEN f.description
                              WHEN f.description IS NULL OR f.description = '' THEN d.definition
                              ELSE d.definition || ' ' || chr(8212) || ' ' || f.description END AS description
                  FROM metadata_fields f
                  LEFT JOIN data_definitions d
                    ON d.connection_id = f.connection_id AND d.tenant_id = odn_current_tenant()
                    AND d.table_path = array_to_string(f.table_path, '.')
                    AND d.field_path = array_to_string(f.path, '.') AND d.status <> 'deprecated'
                  WHERE f.connection_id = %(connection_id)s
                    AND f.table_path_key = ANY(ARRAY(SELECT odn_metadata_path_key(path) FROM page))
                ),
                ranked_fields AS (
                  SELECT matched.*, row_number() OVER (
                    PARTITION BY matched.table_path ORDER BY matched.relevance DESC, matched.ordinal, matched.path
                  ) AS position
                  FROM (
                    SELECT x.path AS table_path, f.path, f.data_type, left(f.description, 500) AS description,
                           f.ordinal,
                           (SELECT coalesce(sum(
                             CASE WHEN strpos(lower(x.container_name || ' ' || x.name), term) = 0 THEN
                               6 * (strpos(lower(array_to_string(f.path, '.')), term) > 0)::int
                               + 2 * (strpos(lower(coalesce(f.description, '')), term) > 0)::int
                             ELSE 0 END
                             + (strpos(lower(array_to_string(f.path, '.')), term) > 0)::int
                           ), 0) FROM unnest(%(terms)s::text[]) term) AS relevance
                    FROM page x JOIN page_fields f ON f.table_path = x.path
                  ) matched WHERE matched.relevance > 0
                ),
                field_hits AS (
                  SELECT table_path, jsonb_agg(jsonb_build_object(
                    'path', path, 'data_type', data_type, 'description', description
                  ) ORDER BY position) AS fields
                  FROM ranked_fields WHERE position <= 6 GROUP BY table_path
                )
                SELECT x.*, coalesce(h.fields, '[]'::jsonb) AS matching_fields
                FROM page x
                LEFT JOIN field_hits h ON h.table_path = x.path
                ORDER BY x.score DESC, x.path
                """,
                {"connection_id": connection_id, "user_id": user_id, "revision": revision, "terms": terms,
                 "snapshots": include_snapshots, "snapshot_pattern": _SNAPSHOT,
                 "after_score": position[0] if position else None,
                 "after_path": list(position[1]) if position else None, "batch": limit + 1},
        ).fetchall()
        if any(not policy.allows_object(tuple(row["path"])) for row in rows):
            raise MetadataAuthorizationMismatch("metadata authorization filter mismatch")
        items = [TableHit.model_validate(row) for row in rows[:limit]]
        next_cursor = (
            _cursor_encode(revision, terms, include_snapshots, items[-1].score, items[-1].path)
            if len(rows) > limit else None
        )
        return SearchPage(status=status, items=items, next_cursor=next_cursor)
