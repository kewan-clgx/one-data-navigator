"""PostgreSQL connector: short-lived read-only connections, EXPLAIN row estimates, timeouts, cancellation."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg
import pyarrow as pa
from psycopg import sql
from psycopg.rows import dict_row

from odn.platform.connectors.base import (
    BaseConnector,
    CatalogNode,
    ColumnSchema,
    ConnectionTestStep,
    CostEstimate,
    QueryExecution,
    QueryLimits,
    TableSchema,
)

_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_MAX_LIST = 10_000
# Every connection defaults to read-only transactions: the AST policy cannot see side effects inside
# functions (nextval, user-defined functions), so the database must refuse writes as well.
_READ_ONLY_OPTIONS = "-c default_transaction_read_only=on"

# Standard PostgreSQL type name mappings to canonical names
_TYPE_MAPPING = {
    # Numeric types
    "smallint": "int2",
    "integer": "int4",
    "bigint": "int8",
    "decimal": "numeric",
    "numeric": "numeric",
    "real": "float4",
    "double precision": "float8",
    # String types
    "character varying": "varchar",
    "character": "char",
    "text": "text",
    # Boolean type
    "boolean": "bool",
    # Date/time types
    "timestamp without time zone": "timestamp",
    "timestamp with time zone": "timestamptz",
    "date": "date",
    "time without time zone": "time",
    "time with time zone": "timetz",
    "interval": "interval",
    # Binary types
    "bytea": "bytea",
    # JSON types
    "json": "json",
    "jsonb": "jsonb",
    # UUID type
    "uuid": "uuid",
    # Array types (normalized below)
    # Other common types
    "oid": "oid",
}


@dataclass(frozen=True)
class PostgresQueryJob:
    """Minimal job tracking for Postgres queries (backend_pid for cancellation)."""

    backend_pid: int
    query_start_time: str


class PostgresExecution(QueryExecution):
    """Run a Postgres query on its own read-only connection and return it as Arrow RecordBatches.

    The connection is closed once the batches have been consumed (or on error).
    """

    def __init__(
        self, conn: psycopg.Connection, sql_text: str, job_id: str | None = None, timeout_s: int = 300
    ) -> None:
        self._conn = conn
        self._sql_text = sql_text
        self.job_id = job_id  # backend_pid, used for cancellation
        self.bytes_processed: int | None = None
        self._timeout_s = timeout_s

    def batches(self) -> Iterator[pa.RecordBatch]:
        try:
            with self._conn.cursor() as cur:
                cur.execute(self._sql_text)
                rows = cur.fetchall()
                columns = [desc[0] for desc in cur.description] if cur.description else []
        finally:
            self._conn.close()

        if not rows:
            if columns:
                schema = pa.schema([(col, pa.string()) for col in columns])
                yield pa.RecordBatch.from_pylist([], schema=schema)
            return

        table = pa.Table.from_pylist([dict(zip(columns, row)) for row in rows])
        batch_size = max(1000, len(rows) // 10)
        for i in range(0, table.num_rows, batch_size):
            yield table.slice(i, min(batch_size, table.num_rows - i)).to_batches()[0]


class PostgresConnector(BaseConnector):
    """PostgreSQL connector with async connection pooling and query lifecycle management."""

    dialect = "postgres"
    supports_dry_run = False  # Postgres EXPLAIN is not a full dry-run like BigQuery
    supports_cancel = True

    def __init__(
        self,
        host: str,
        port: int = 5432,
        database: str = "postgres",
        user: str = "postgres",
        password: str | None = None,
        schema: str | None = None,
        ssl_mode: str = "prefer",
        connect_timeout: int = 10,
        metadata_timeout_s: int = 30,
    ) -> None:
        """No connection is held between calls: each call opens one short-lived read-only connection and
        closes it, so a client database never accumulates idle connections from us (connectors are built
        per request)."""
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.schema = schema or "public"
        self.ssl_mode = ssl_mode
        self._connect_timeout = connect_timeout
        self._metadata_timeout_ms = metadata_timeout_s * 1000
        self._conninfo = (
            f"postgresql://{user}"
            + (f":{password}" if password else "")
            + f"@{host}:{port}/{database}"
            + f"?sslmode={ssl_mode}&connect_timeout={connect_timeout}"
        )

    @property
    def default_namespace(self) -> tuple[str, ...]:
        return (self.schema,)

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self._conninfo, options=_READ_ONLY_OPTIONS, application_name="one-data-navigator")

    @contextmanager
    def _metadata(self) -> Iterator[psycopg.Cursor]:
        """A dict-row cursor on a fresh read-only connection with a statement timeout; closed afterwards."""
        with self._connect() as conn:
            conn.execute(sql.SQL("SET statement_timeout = {}").format(sql.Literal(self._metadata_timeout_ms)))
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            with conn.cursor(row_factory=dict_row) as cur:
                yield cur

    def test_connection(self) -> list[ConnectionTestStep]:
        """Diagnostics: Auth → Network → Metadata → Read, stopping at the first failing step."""
        try:
            conn = self._connect()
        except psycopg.OperationalError as exc:
            detail = str(exc.args[0]) if exc.args else type(exc).__name__
            text = detail.lower()
            if "password authentication failed" in text or "authentication failed" in text:
                return [ConnectionTestStep(step="auth", ok=False, detail="authentication failed")]
            if any(k in text for k in ("could not connect", "connection refused", "timeout", "could not translate")):
                return [ConnectionTestStep(step="network", ok=False, detail=detail)]
            return [ConnectionTestStep(step="auth", ok=False, detail=detail)]
        steps = [ConnectionTestStep(step="auth", ok=True), ConnectionTestStep(step="network", ok=True)]
        with conn:
            try:
                conn.execute(sql.SQL("SET statement_timeout = {}").format(sql.Literal(self._metadata_timeout_ms)))
                conn.execute(
                    "SELECT 1 FROM pg_namespace WHERE nspname = %s", (self.schema,)
                ).fetchone()
                steps.append(ConnectionTestStep(step="metadata", ok=True))
            except psycopg.Error as exc:
                steps.append(ConnectionTestStep(step="metadata", ok=False, detail=type(exc).__name__))
                return steps
            try:
                conn.execute("SELECT 1").fetchone()
                steps.append(ConnectionTestStep(step="read", ok=True))
            except psycopg.Error as exc:
                steps.append(ConnectionTestStep(step="read", ok=False, detail=type(exc).__name__))
        return steps

    def list_catalog(self, parent: tuple[str, ...] = ()) -> list[CatalogNode]:
        """Schemas if parent=(), tables and views if parent=(schema,)."""
        if len(parent) == 0:
            with self._metadata() as cur:
                cur.execute(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name NOT IN ('pg_catalog', 'information_schema') AND schema_name NOT LIKE 'pg\\_%%' "
                    "ORDER BY schema_name LIMIT %s",
                    (_MAX_LIST,),
                )
                rows = cur.fetchall()
            return [CatalogNode(kind="schema", path=(r["schema_name"],), name=r["schema_name"]) for r in rows]
        if len(parent) == 1:
            schema = parent[0]
            if not _SCHEMA_RE.match(schema):
                raise ValueError(f"invalid schema name: {schema}")
            with self._metadata() as cur:
                cur.execute(
                    "SELECT table_name, table_type FROM information_schema.tables "
                    "WHERE table_schema = %s ORDER BY table_name LIMIT %s",
                    (schema, _MAX_LIST),
                )
                rows = cur.fetchall()
            return [
                CatalogNode(
                    kind="view" if r["table_type"] == "VIEW" else "table",
                    path=(schema, r["table_name"]),
                    name=r["table_name"],
                )
                for r in rows
            ]
        raise ValueError("parent must be () for schemas or (schema_name,) for tables")

    def get_schema(self, path: tuple[str, ...]) -> TableSchema:
        if len(path) != 2:
            raise ValueError("path must be (schema, table)")
        schema, table = path
        if not (_SCHEMA_RE.match(schema) and _TABLE_RE.match(table)):
            raise ValueError(f"invalid schema or table name: {path}")

        with self._metadata() as cur:
            cur.execute(
                "SELECT c.oid, c.relkind, obj_description(c.oid, 'pg_class') AS description, "
                # reltuples is set by ANALYZE (what the planner uses); -1 = never analyzed, then use live stats.
                "CASE WHEN c.reltuples >= 0 THEN c.reltuples::bigint ELSE s.n_live_tup END AS row_estimate "
                "FROM pg_class c JOIN pg_namespace n ON c.relnamespace = n.oid "
                "LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid "
                "WHERE n.nspname = %s AND c.relname = %s AND c.relkind IN ('r', 'p', 'v', 'm', 'f')",
                (schema, table),
            )
            rel = cur.fetchone()
            if rel is None:
                raise ValueError(f"table {schema}.{table} not found")
            cur.execute(
                "SELECT a.attname, t.typname, NOT a.attnotnull AS is_nullable, "
                "col_description(a.attrelid, a.attnum) AS description "
                "FROM pg_attribute a JOIN pg_type t ON a.atttypid = t.oid "
                "WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum",
                (rel["oid"],),
            )
            col_rows = cur.fetchall()

        return TableSchema(
            path=path,
            kind="view" if rel["relkind"] in ("v", "m") else "table",
            columns=[
                ColumnSchema(
                    name=r["attname"],
                    type=_TYPE_MAPPING.get(r["typname"], r["typname"]),
                    nullable=r["is_nullable"],
                    description=r["description"],
                )
                for r in col_rows
            ],
            description=rel["description"],
            row_count=rel["row_estimate"],
        )

    def list_columns(self, container: tuple[str, ...]) -> dict[tuple[str, ...], list[ColumnSchema]]:
        """All columns of all tables and views in one schema."""
        if len(container) != 1:
            raise ValueError("container must be (schema_name,)")
        schema = container[0]
        if not _SCHEMA_RE.match(schema):
            raise ValueError(f"invalid schema name: {schema}")

        with self._metadata() as cur:
            cur.execute(
                "SELECT c.relname, a.attname, t.typname, NOT a.attnotnull AS is_nullable, "
                "col_description(a.attrelid, a.attnum) AS description "
                "FROM pg_attribute a "
                "JOIN pg_type t ON a.atttypid = t.oid "
                "JOIN pg_class c ON a.attrelid = c.oid "
                "JOIN pg_namespace n ON c.relnamespace = n.oid "
                "WHERE n.nspname = %s AND c.relkind IN ('r', 'p', 'v', 'm', 'f') "
                "AND a.attnum > 0 AND NOT a.attisdropped "
                "ORDER BY c.relname, a.attnum",
                (schema,),
            )
            rows = cur.fetchall()

        out: dict[tuple[str, ...], list[ColumnSchema]] = {}
        for r in rows:
            out.setdefault((schema, r["relname"]), []).append(
                ColumnSchema(
                    name=r["attname"],
                    type=_TYPE_MAPPING.get(r["typname"], r["typname"]),
                    nullable=r["is_nullable"],
                    description=r["description"],
                )
            )
        return out

    def estimate_cost(self, sql_text: str) -> CostEstimate:
        """Row estimate from EXPLAIN (no execution); empty when EXPLAIN fails."""
        import json

        try:
            with self._metadata() as cur:
                cur.execute(f"EXPLAIN (FORMAT JSON) {sql_text}")
                result = cur.fetchone()
        except psycopg.Error:
            return CostEstimate()
        plan = result.get("QUERY PLAN") if result else None
        if isinstance(plan, str):
            plan = json.loads(plan)
        if isinstance(plan, list) and plan:
            plan = plan[0]
        rows = plan.get("Plan", {}).get("Plan Rows") if isinstance(plan, dict) else None
        if rows is None and isinstance(plan, dict):
            rows = plan.get("Plan", {}).get("Rows")
        return CostEstimate(rows_estimated=int(rows)) if rows else CostEstimate()

    def execute_paged(self, sql_text: str, limits: QueryLimits) -> QueryExecution:
        """Open a dedicated read-only connection for the query; the execution closes it."""
        conn = self._connect()
        try:
            # Explicit per transaction too, so the guarantee doesn't depend on the session default alone.
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(self.schema)))
            conn.execute(sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(limits.timeout_s * 1000)))
            row = conn.execute("SELECT pg_backend_pid()").fetchone()
            job_id = str(row[0]) if row else None
        except BaseException:
            conn.close()
            raise
        return PostgresExecution(conn, sql_text, job_id=job_id, timeout_s=limits.timeout_s)

    def cancel_job(self, job_id: str) -> None:
        """Cancel a running query by backend PID (best effort; our own role may only cancel its own backends)."""
        try:
            backend_pid = int(job_id)
        except ValueError:
            return
        try:
            with self._connect() as conn:
                conn.execute("SELECT pg_cancel_backend(%s)", (backend_pid,))
        except psycopg.Error:
            pass
