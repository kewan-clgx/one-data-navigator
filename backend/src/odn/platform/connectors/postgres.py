"""PostgreSQL connector: async psycopg pool, EXPLAIN cost estimation, timeout enforcement, query cancellation."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pyarrow as pa
import psycopg
from psycopg import AsyncConnection, sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

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
    """Execute a Postgres query and stream results as Arrow RecordBatches."""

    def __init__(
        self, conn: AsyncConnection, sql_text: str, job_id: str | None = None, timeout_s: int = 300
    ) -> None:
        self._conn = conn
        self._sql_text = sql_text
        self.job_id = job_id  # Set from the backend_pid during execution
        self.bytes_processed: int | None = None
        self._timeout_s = timeout_s

    def batches(self) -> Iterator[pa.RecordBatch]:
        """Fetch all rows and convert to Arrow RecordBatches synchronously (run() is sync)."""
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            batches_list = loop.run_until_complete(self._fetch_batches_async())
            yield from batches_list
        finally:
            loop.close()

    async def _fetch_batches_async(self) -> list[pa.RecordBatch]:
        """Async fetch, return list of RecordBatches."""
        batches = []
        try:
            async with self._conn.cursor() as cur:
                await cur.execute(self._sql_text)
                rows = await cur.fetchall()
                # Get column info from cursor description
                columns = [desc[0] for desc in cur.description] if cur.description else []

                if not rows:
                    # Return empty batch with correct schema
                    if columns:
                        schema = pa.schema([(col, pa.string()) for col in columns])
                        batches.append(pa.RecordBatch.from_pylist([], schema=schema))
                    return batches

                # Convert rows to Arrow RecordBatches
                # psycopg returns rows as tuples or namedtuples; convert to list of dicts
                records = [dict(zip(columns, row)) for row in rows]
                table = pa.Table.from_pylist(records)

                # Collect in batches of reasonable size
                batch_size = max(1000, len(rows) // 10)  # At least 1000 rows per batch
                for i in range(0, table.num_rows, batch_size):
                    batches.append(table.slice(i, min(batch_size, table.num_rows - i)).to_batches()[0])
        except psycopg.errors.QueryCanceled:
            raise
        except psycopg.errors.QueryTimedOut:
            raise TimeoutError(f"Query timed out after {self._timeout_s}s") from None
        
        return batches


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
        min_size: int = 2,
        max_size: int = 10,
    ) -> None:
        """Initialize PostgreSQL connector with async connection pool.

        Args:
            host: PostgreSQL server hostname
            port: PostgreSQL server port (default 5432)
            database: Database name
            user: Database user
            password: Database password (optional)
            schema: Default search_path schema (default None → public)
            ssl_mode: SSL mode (disable, allow, prefer, require, verify-ca, verify-full)
            connect_timeout: Connection timeout in seconds
            min_size: Minimum pool size
            max_size: Maximum pool size
        """
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.schema = schema or "public"
        self.ssl_mode = ssl_mode
        self._min_size = min_size
        self._max_size = max_size
        self._connect_timeout = connect_timeout

        # Build connection string (search_path set via options in _get_conn, not URI)
        self._conninfo = (
            f"postgresql://{user}"
            + (f":{password}" if password else "")
            + f"@{host}:{port}/{database}"
            + f"?sslmode={ssl_mode}&connect_timeout={connect_timeout}"
        )

        # Lazy initialization: pool is created on first use
        self._pool: AsyncConnectionPool | None = None

    @property
    def default_namespace(self) -> tuple[str, ...]:
        return (self.schema,)

    @asynccontextmanager
    async def _get_conn(self) -> AsyncConnection:
        """Get a connection from the pool, ensuring pool is open and initialized."""
        if self._pool is None:
            # Lazy initialization: create pool on first use
            # This now happens in the calling event loop context, which is stable
            self._pool = AsyncConnectionPool(
                self._conninfo,
                min_size=self._min_size,
                max_size=self._max_size,
                check=AsyncConnectionPool.check_connection,
            )
            # Open the pool to initialize it
            await self._pool.open()

        async with self._pool.connection() as conn:
            # Set search_path for this connection
            await conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            yield conn

    def test_connection(self) -> list[ConnectionTestStep]:
        """Test connection with 4-step diagnostic: Auth, Network, Metadata, Read."""
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(self._test_connection_async())
        finally:
            loop.close()

    async def _test_connection_async(self) -> list[ConnectionTestStep]:
        """Async connection test implementation."""
        steps: list[ConnectionTestStep] = []

        # Step 1: Auth - Try to connect
        try:
            async with self._get_conn() as conn:
                # Connection successful = auth OK
                steps.append(ConnectionTestStep(step="auth", ok=True))

                # Step 2: Network - Already passed (we have a connection)
                steps.append(ConnectionTestStep(step="network", ok=True))

                # Step 3: Metadata - Query system catalogs
                try:
                    async with conn.cursor() as cur:
                        await cur.execute("SELECT schema_name FROM information_schema.schemata LIMIT 1")
                        await cur.fetchone()
                    steps.append(ConnectionTestStep(step="metadata", ok=True))
                except psycopg.Error as exc:
                    steps.append(ConnectionTestStep(step="metadata", ok=False, detail=type(exc).__name__))
                    return steps

                # Step 4: Read - Execute a simple query
                try:
                    async with conn.cursor() as cur:
                        await cur.execute("SELECT 1")
                        await cur.fetchone()
                    steps.append(ConnectionTestStep(step="read", ok=True))
                except psycopg.Error as exc:
                    steps.append(ConnectionTestStep(step="read", ok=False, detail=type(exc).__name__))

        except psycopg.OperationalError as exc:
            # Connection failed; could be auth or network
            detail = str(exc.args[0]) if exc.args else type(exc).__name__
            if "authentication failed" in detail.lower():
                steps.append(ConnectionTestStep(step="auth", ok=False, detail="authentication failed"))
            elif "could not connect" in detail.lower() or "connection refused" in detail.lower():
                steps.append(ConnectionTestStep(step="network", ok=False, detail=detail))
            else:
                steps.append(ConnectionTestStep(step="auth", ok=False, detail=detail))
        except psycopg.Error as exc:
            steps.append(ConnectionTestStep(step="auth", ok=False, detail=type(exc).__name__))

        return steps

    def list_catalog(self, parent: tuple[str, ...] = ()) -> list[CatalogNode]:
        """List catalog: schemas if parent=(), tables if parent=(schema,)."""
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(self._list_catalog_async(parent))
        finally:
            loop.close()

    async def _list_catalog_async(self, parent: tuple[str, ...] = ()) -> list[CatalogNode]:
        """Async catalog listing."""
        async with self._get_conn() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                if len(parent) == 0:
                    # List schemas
                    await cur.execute(
                        "SELECT schema_name FROM information_schema.schemata "
                        "WHERE schema_name NOT IN ('pg_catalog', 'information_schema') "
                        "ORDER BY schema_name LIMIT %s",
                        (_MAX_LIST,),
                    )
                    rows = await cur.fetchall()
                    return [
                        CatalogNode(kind="schema", path=(row["schema_name"],), name=row["schema_name"])
                        for row in rows
                    ]

                elif len(parent) == 1:
                    # List tables in schema
                    schema = parent[0]
                    if not _SCHEMA_RE.match(schema):
                        raise ValueError(f"invalid schema name: {schema}")
                    await cur.execute(
                        "SELECT table_name, table_type FROM information_schema.tables "
                        "WHERE table_schema = %s ORDER BY table_name LIMIT %s",
                        (schema, _MAX_LIST),
                    )
                    rows = await cur.fetchall()
                    return [
                        CatalogNode(
                            kind="view" if row["table_type"] == "VIEW" else "table",
                            path=(schema, row["table_name"]),
                            name=row["table_name"],
                        )
                        for row in rows
                    ]

        raise ValueError("parent must be () for schemas or (schema_name,) for tables")

    def get_schema(self, path: tuple[str, ...]) -> TableSchema:
        """Get table schema: columns, types, descriptions."""
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(self._get_schema_async(path))
        finally:
            loop.close()

    async def _get_schema_async(self, path: tuple[str, ...]) -> TableSchema:
        """Async schema retrieval."""
        if len(path) != 2:
            raise ValueError("path must be (schema, table)")

        schema, table = path
        if not (_SCHEMA_RE.match(schema) and _TABLE_RE.match(table)):
            raise ValueError(f"invalid schema or table name: {path}")

        async with self._get_conn() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # Get table type (table or view)
                await cur.execute(
                    "SELECT table_type FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_name = %s",
                    (schema, table),
                )
                result = await cur.fetchone()
                if not result:
                    raise ValueError(f"table {schema}.{table} not found")
                kind = "view" if result["table_type"] == "VIEW" else "table"

                # Get columns with descriptions
                await cur.execute(
                    "SELECT a.attname, t.typname, NOT a.attnotnull as is_nullable, d.description "
                    "FROM pg_attribute a "
                    "JOIN pg_type t ON a.atttypid = t.oid "
                    "JOIN pg_class c ON a.attrelid = c.oid "
                    "JOIN pg_namespace n ON c.relnamespace = n.oid "
                    "LEFT JOIN pg_description d ON c.oid = d.objoid AND a.attnum = d.objsubid "
                    "WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 "
                    "ORDER BY a.attnum",
                    (schema, table),
                )
                col_rows = await cur.fetchall()
                columns = [
                    ColumnSchema(
                        name=row["attname"],
                        type=_TYPE_MAPPING.get(row["typname"], row["typname"]),
                        nullable=row["is_nullable"],
                        description=row["description"],
                    )
                    for row in col_rows
                ]

                # Get table description
                await cur.execute(
                    "SELECT d.description FROM pg_class c "
                    "JOIN pg_namespace n ON c.relnamespace = n.oid "
                    "LEFT JOIN pg_description d ON c.oid = d.objoid AND d.objsubid = 0 "
                    "WHERE n.nspname = %s AND c.relname = %s",
                    (schema, table),
                )
                desc_result = await cur.fetchone()
                description = desc_result["description"] if desc_result else None

                # Get row count estimate
                await cur.execute(
                    "SELECT n_live_tup FROM pg_stat_user_tables "
                    "WHERE schemaname = %s AND relname = %s",
                    (schema, table),
                )
                stat_result = await cur.fetchone()
                row_count = stat_result["n_live_tup"] if stat_result else None

        return TableSchema(
            path=path,
            kind=kind,
            columns=columns,
            description=description,
            row_count=row_count,
        )

    def list_columns(self, container: tuple[str, ...]) -> dict[tuple[str, ...], list[ColumnSchema]]:
        """List all columns for all tables in a container (schema)."""
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(self._list_columns_async(container))
        finally:
            loop.close()

    async def _list_columns_async(self, container: tuple[str, ...]) -> dict[tuple[str, ...], list[ColumnSchema]]:
        """Async column listing for entire schema."""
        if len(container) != 1:
            raise ValueError("container must be (schema_name,)")

        schema = container[0]
        if not _SCHEMA_RE.match(schema):
            raise ValueError(f"invalid schema name: {schema}")

        out: dict[tuple[str, ...], list[ColumnSchema]] = {}

        async with self._get_conn() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT c.relname, a.attname, t.typname, NOT a.attnotnull as is_nullable, d.description "
                    "FROM pg_attribute a "
                    "JOIN pg_type t ON a.atttypid = t.oid "
                    "JOIN pg_class c ON a.attrelid = c.oid "
                    "JOIN pg_namespace n ON c.relnamespace = n.oid "
                    "LEFT JOIN pg_description d ON c.oid = d.objoid AND a.attnum = d.objsubid "
                    "WHERE n.nspname = %s AND a.attnum > 0 "
                    "ORDER BY c.relname, a.attnum",
                    (schema,),
                )
                rows = await cur.fetchall()

        for row in rows:
            table_path = (schema, row["relname"])
            if table_path not in out:
                out[table_path] = []
            out[table_path].append(
                ColumnSchema(
                    name=row["attname"],
                    type=_TYPE_MAPPING.get(row["typname"], row["typname"]),
                    nullable=row["is_nullable"],
                    description=row["description"],
                )
            )

        return out

    def estimate_cost(self, sql_text: str) -> CostEstimate:
        """Estimate query cost using EXPLAIN."""
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(self._estimate_cost_async(sql_text))
        finally:
            loop.close()

    async def _estimate_cost_async(self, sql_text: str) -> CostEstimate:
        """Async EXPLAIN parsing."""
        try:
            async with self._get_conn() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    # Use EXPLAIN (FORMAT JSON) for structured output
                    await cur.execute(f"EXPLAIN (FORMAT JSON, ANALYZE false) {sql_text}")
                    result = await cur.fetchone()

                    if result:
                        import json

                        # result is a dict when using row_factory=dict_row
                        # EXPLAIN (FORMAT JSON) returns a column named 'QUERY PLAN'
                        plan_json = result.get("QUERY PLAN")
                        if plan_json:
                            plan = json.loads(plan_json) if isinstance(plan_json, str) else plan_json
                            if isinstance(plan, list) and len(plan) > 0:
                                plan = plan[0]

                            # Extract row estimate from the top-level Plan node
                            if isinstance(plan, dict) and "Plan" in plan:
                                rows_estimate = plan["Plan"].get("Rows", plan["Plan"].get("Actual Rows"))
                                if rows_estimate:
                                    return CostEstimate(rows_estimated=int(rows_estimate))

        except psycopg.Error:
            # EXPLAIN failed; return None
            pass

        return CostEstimate()

    def execute_paged(self, sql_text: str, limits: QueryLimits) -> QueryExecution:
        """Execute query with limits enforcement."""
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Execute the query async and get connection + job info
            conn, job_id = loop.run_until_complete(self._execute_paged_async(sql_text, limits))
            execution = PostgresExecution(conn, sql_text, job_id=job_id, timeout_s=limits.timeout_s)
            return execution
        except Exception:
            loop.close()
            raise

    async def _execute_paged_async(
        self, sql_text: str, limits: QueryLimits
    ) -> tuple[AsyncConnection, str | None]:
        """Execute query and return connection + job ID for tracking."""
        conn = await self._pool.getconn()

        try:
            # Set statement timeout
            async with conn.cursor() as cur:
                await cur.execute(f"SET statement_timeout = {limits.timeout_s * 1000}")

            # Get backend PID for cancellation
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT pg_backend_pid() as pid")
                pid_result = await cur.fetchone()
                job_id = str(pid_result["pid"]) if pid_result else None

            return conn, job_id

        except Exception:
            await self._pool.putconn(conn)
            raise

    def cancel_job(self, job_id: str) -> None:
        """Cancel a running query by backend PID."""
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._cancel_job_async(job_id))
        finally:
            loop.close()

    async def _cancel_job_async(self, job_id: str) -> None:
        """Async job cancellation."""
        try:
            backend_pid = int(job_id)
            async with self._get_conn() as conn:
                async with conn.cursor() as cur:
                    # pg_cancel_backend returns true if signal sent successfully
                    await cur.execute("SELECT pg_cancel_backend(%s)", (backend_pid,))

        except (ValueError, psycopg.Error):
            # Invalid PID or cancellation failed; silently ignore
            pass

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()

    def __del__(self) -> None:
        """Cleanup on deletion."""
        try:
            import asyncio

            asyncio.run(self.close())
        except Exception:
            pass
