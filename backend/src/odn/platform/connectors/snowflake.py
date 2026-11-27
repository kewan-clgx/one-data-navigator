"""Snowflake connector using a read-only role and key-pair or password authentication."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pyarrow as pa
import snowflake.connector
from cryptography.hazmat.primitives import serialization
from snowflake.connector.errors import (
    BadGatewayError,
    ForbiddenError,
    NonRetryableTlsError,
    ServiceUnavailableError,
)
from snowflake.connector.errors import Error as SnowflakeError

from odn.platform.connectors.base import (
    BaseConnector,
    CatalogNode,
    ColumnSchema,
    ConnectionTestStep,
    ConnectorError,
    CostEstimate,
    QueryExecution,
    QueryLimits,
    TableSchema,
)
from odn.platform.policy.sql_policy import _SNOWFLAKE_IDENTIFIER

_MAX_LIST = 10_000


def _identifier(name: str) -> str:
    if not _SNOWFLAKE_IDENTIFIER.fullmatch(name):
        raise ValueError("unsupported Snowflake identifier")
    return '"' + name + '"'


def _safe_error(exc: SnowflakeError) -> ConnectorError:
    code = getattr(exc, "errno", None)
    if isinstance(exc, NonRetryableTlsError):
        return ConnectorError(
            f"Snowflake TLS connection failed{f' (error {code})' if isinstance(code, int) else ''}; "
            "check certificate trust and network inspection"
        )
    if isinstance(exc, ForbiddenError):
        return ConnectorError(
            f"Snowflake connection was forbidden{f' (error {code})' if isinstance(code, int) else ''}; "
            "check account and network access"
        )
    if isinstance(exc, (BadGatewayError, ServiceUnavailableError)):
        return ConnectorError(
            f"Snowflake service or network gateway unavailable{f' (error {code})' if isinstance(code, int) else ''}"
        )
    if code == 250001:
        return ConnectorError(
            "Snowflake connection or authentication failed (error 250001); "
            "check account identifier, network access, and authentication policy"
        )
    if code == 390186:
        return ConnectorError(
            "Snowflake role is not granted to this user (error 390186); "
            "choose a granted read-only role or clear Role to use the user's default"
        )
    return ConnectorError(f"Snowflake request failed{f' (error {code})' if isinstance(code, int) else ''}")


class SnowflakeExecution(QueryExecution):
    def __init__(self, conn, cursor) -> None:
        self._conn = conn
        self._cursor = cursor
        self.job_id = cursor.sfqid

    def batches(self) -> Iterator[pa.RecordBatch]:
        try:
            found = False
            for table in self._cursor.fetch_arrow_batches():
                found = True
                yield from table.to_batches(max_chunksize=4096)
            if not found and self._cursor.description:
                yield pa.RecordBatch.from_arrays(
                    [pa.array([], type=pa.string()) for _ in self._cursor.description],
                    names=[c.name for c in self._cursor.description],
                )
        except SnowflakeError as exc:
            raise _safe_error(exc) from None
        finally:
            self._cursor.close()
            self._conn.close()


class SnowflakeConnector(BaseConnector):
    dialect = "snowflake"
    supports_dry_run = False
    supports_cancel = False

    def __init__(
        self, *, account: str, user: str, warehouse: str, database: str, schema: str,
        role: str | None, private_key: str | None = None, password: str | None = None,
    ) -> None:
        self.account, self.user, self.warehouse = account, user, warehouse
        self.database, self.schema, self.role = database, schema, role
        if bool(private_key) == bool(password):
            raise ValueError("provide exactly one Snowflake credential")
        self._password = password
        self._key = None
        if private_key:
            key = serialization.load_pem_private_key(private_key.encode(), password=None)
            self._key = key.private_bytes(
                serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )

    @property
    def default_namespace(self) -> tuple[str, ...]:
        return (self.database, self.schema)

    def _connect(self, timeout_s: int = 30):
        try:
            return snowflake.connector.connect(
                account=self.account, user=self.user, warehouse=self.warehouse, database=self.database,
                schema=self.schema, role=self.role, private_key=self._key, password=self._password, login_timeout=30,
                network_timeout=timeout_s, arrow_number_to_decimal=True,
                session_parameters={
                    "STATEMENT_TIMEOUT_IN_SECONDS": timeout_s, "QUERY_TAG": "one-data-navigator",
                    "QUOTED_IDENTIFIERS_IGNORE_CASE": False,
                },
            )
        except SnowflakeError as exc:
            raise _safe_error(exc) from None

    @contextmanager
    def _cursor(self):
        conn = self._connect()
        try:
            cursor = conn.cursor()
            try:
                yield cursor
            except SnowflakeError as exc:
                raise _safe_error(exc) from None
            finally:
                cursor.close()
        finally:
            conn.close()

    def test_connection(self) -> list[ConnectionTestStep]:
        try:
            conn = self._connect()
        except ConnectorError as exc:
            return [ConnectionTestStep(step="auth", ok=False, detail=str(exc))]
        steps = [ConnectionTestStep(step="auth", ok=True), ConnectionTestStep(step="network", ok=True)]
        try:
            cursor = conn.cursor()
            try:
                cursor.execute("SHOW TERSE SCHEMAS IN ACCOUNT")
                cursor.fetchone()
                steps.append(ConnectionTestStep(step="metadata", ok=True))
                cursor.execute("SELECT 1")
                cursor.fetchone()
                steps.append(ConnectionTestStep(step="read", ok=True))
            except SnowflakeError as exc:
                step = "read" if len(steps) == 3 else "metadata"
                steps.append(ConnectionTestStep(step=step, ok=False, detail=str(_safe_error(exc))))
            finally:
                cursor.close()
        finally:
            conn.close()
        return steps

    def list_catalog(self, parent: tuple[str, ...] = ()) -> list[CatalogNode]:
        if not parent:
            with self._cursor() as cur:
                cur.execute("SHOW TERSE SCHEMAS IN ACCOUNT")
                names = [c.name.lower() for c in cur.description]
                rows = cur.fetchmany(_MAX_LIST + 1)
            if len(rows) >= _MAX_LIST:
                raise ConnectorError("Snowflake schema listing reached its 10,000 row limit")
            return [
                CatalogNode(kind="schema", path=(r[names.index("database_name")], r[names.index("name")]),
                            name=f'{r[names.index("database_name")]}.{r[names.index("name")]}')
                for r in rows
                if r[names.index("database_name")] and r[names.index("name")] != "INFORMATION_SCHEMA"
                and _SNOWFLAKE_IDENTIFIER.fullmatch(r[names.index("database_name")])
                and _SNOWFLAKE_IDENTIFIER.fullmatch(r[names.index("name")])
            ]
        if len(parent) != 2:
            raise ValueError("parent must be () or (database, schema)")
        db, schema = parent
        sql = (
            f"SELECT TABLE_NAME, TABLE_TYPE, COMMENT FROM {_identifier(db)}.INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME LIMIT 10001"
        )
        with self._cursor() as cur:
            cur.execute(sql, (schema,))
            rows = cur.fetchmany(_MAX_LIST + 1)
        if len(rows) > _MAX_LIST:
            raise ConnectorError("Snowflake table listing exceeds 10,000 rows")
        return [
            CatalogNode(kind="view" if kind == "VIEW" else "table", path=(db, schema, name),
                        name=name, description=comment)
            for name, kind, comment in rows if _SNOWFLAKE_IDENTIFIER.fullmatch(name)
        ]

    def list_catalog_complete(self, parent: tuple[str, ...] = ()) -> list[CatalogNode]:
        return self.list_catalog(parent)

    def get_schema(self, path: tuple[str, ...]) -> TableSchema:
        if len(path) != 3:
            raise ValueError("path must be (database, schema, table)")
        db, schema, table = path
        for part in path:
            _identifier(part)
        sql = (
            f"SELECT TABLE_TYPE, COMMENT, ROW_COUNT, BYTES FROM {_identifier(db)}.INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s"
        )
        with self._cursor() as cur:
            cur.execute(sql, (schema, table))
            row = cur.fetchone()
        if row is None:
            raise ValueError("Snowflake table not found")
        columns = self._columns((db, schema), table).get(path, [])
        return TableSchema(path=path, kind="view" if row[0] == "VIEW" else "table", columns=columns,
                           description=row[1], row_count=row[2], size_bytes=row[3])

    def _columns(self, container: tuple[str, ...], table: str | None = None) -> dict[tuple[str, ...], list[ColumnSchema]]:
        db, schema = container
        sql = (
            f"SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COMMENT "
            f"FROM {_identifier(db)}.INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = %s"
        )
        params = (schema,)
        if table is not None:
            sql += " AND TABLE_NAME = %s"
            params = (schema, table)
        sql += " ORDER BY TABLE_NAME, ORDINAL_POSITION"
        out: dict[tuple[str, ...], list[ColumnSchema]] = {}
        with self._cursor() as cur:
            cur.execute(sql, params)
            for name, column, typ, nullable, comment in cur:
                if _SNOWFLAKE_IDENTIFIER.fullmatch(name):
                    out.setdefault((db, schema, name), []).append(
                        ColumnSchema(name=column, type=typ, nullable=nullable == "YES", description=comment)
                    )
        return out

    def list_columns(self, container: tuple[str, ...]) -> dict[tuple[str, ...], list[ColumnSchema]]:
        if len(container) != 2:
            raise ValueError("container must be (database, schema)")
        for part in container:
            _identifier(part)
        return self._columns(container)

    def estimate_cost(self, sql: str) -> CostEstimate:
        return CostEstimate()

    def execute_paged(self, sql: str, limits: QueryLimits) -> QueryExecution:
        conn = self._connect(limits.timeout_s)
        cursor = conn.cursor()
        try:
            cursor.execute(sql)
        except SnowflakeError as exc:
            cursor.close()
            conn.close()
            raise _safe_error(exc) from None
        return SnowflakeExecution(conn, cursor)

    def cancel_job(self, job_id: str) -> None:
        raise ConnectorError("Snowflake query cancellation is not supported")
