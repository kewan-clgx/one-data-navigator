"""MySQL 8.0/8.4: approved destinations, verified TLS and unbuffered read-only results."""

from __future__ import annotations

import ssl
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

import pyarrow as pa
import pymysql
from pymysql.constants import FIELD_TYPE, FLAG
from pymysql.cursors import SSCursor

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
from odn.platform.net import mysql_socket
from odn.platform.policy.sql_policy import _MYSQL_IDENTIFIER, _MYSQL_SYSTEM_DATABASES

_MAX_LIST = 10_000
_BATCH_ROWS = 1000


def _close_stream(conn, cursor) -> None:
    conn.close()
    if cursor._result is not None:
        cursor._result.unbuffered_active = False
    cursor.close()


def tls_context(ca: str | None = None) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if ca:
        if "PRIVATE KEY" in ca or "-----BEGIN CERTIFICATE-----" not in ca:
            raise ValueError("MySQL CA must contain PEM certificates, not a private key or file path")
        try:
            ctx.load_verify_locations(cadata=ca)
        except (ssl.SSLError, ValueError):
            raise ValueError("invalid MySQL CA certificate bundle") from None
    return ctx


def _safe_error(exc: pymysql.MySQLError) -> ConnectorError:
    code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    if code in (1044, 1045, 1142, 1143):
        message = "MySQL access denied; check credentials and SELECT grants"
    elif code == 2026 or isinstance(getattr(exc, "original_exception", None), ssl.SSLError):
        message = "MySQL verified TLS failed; check CA, certificate hostname and server TLS support"
    elif code in (1205, 1317, 3024):
        message = "MySQL query timed out or was interrupted"
    elif code in (2002, 2003, 2006, 2013):
        message = "MySQL connection failed or timed out; check network, TLS and server availability"
    else:
        message = "MySQL request failed"
    return ConnectorError(f"{message}{f' (error {code})' if code is not None else ''}")


def _arrow_type(field) -> pa.DataType:
    code = field.type_code
    if code in (FIELD_TYPE.TINY, FIELD_TYPE.SHORT, FIELD_TYPE.INT24, FIELD_TYPE.LONG, FIELD_TYPE.YEAR):
        return pa.int64()
    if code == FIELD_TYPE.LONGLONG:
        return pa.uint64() if field.flags & FLAG.UNSIGNED else pa.int64()
    if code in (FIELD_TYPE.DECIMAL, FIELD_TYPE.NEWDECIMAL):
        precision = field.length - (0 if field.flags & FLAG.UNSIGNED else 1) - (1 if field.scale else 0)
        return pa.decimal128(precision, field.scale) if precision <= 38 else pa.decimal256(precision, field.scale)
    if code in (FIELD_TYPE.FLOAT, FIELD_TYPE.DOUBLE):
        return pa.float64()
    if code in (FIELD_TYPE.TIMESTAMP, FIELD_TYPE.DATETIME):
        return pa.timestamp("us")
    if code == FIELD_TYPE.DATE:
        return pa.date32()
    if code == FIELD_TYPE.TIME:
        return pa.duration("us")
    if code == FIELD_TYPE.NULL:
        return pa.null()
    if code in (
        FIELD_TYPE.STRING, FIELD_TYPE.VAR_STRING, FIELD_TYPE.VARCHAR, FIELD_TYPE.BLOB,
        FIELD_TYPE.TINY_BLOB, FIELD_TYPE.MEDIUM_BLOB, FIELD_TYPE.LONG_BLOB,
        FIELD_TYPE.JSON, FIELD_TYPE.ENUM, FIELD_TYPE.SET, FIELD_TYPE.BIT, FIELD_TYPE.GEOMETRY,
    ):
        return pa.binary() if field.charsetnr == 63 and code != FIELD_TYPE.JSON else pa.string()
    raise ConnectorError("MySQL result contains an unsupported column type")


class MySQLExecution(QueryExecution):
    job_id = None

    def __init__(self, conn, cursor) -> None:
        self._conn, self._cursor = conn, cursor

    def batches(self) -> Iterator[pa.RecordBatch]:
        try:
            # DB-API description omits unsigned/binary flags; use PyMySQL's field metadata for stable batches.
            fields = self._cursor._result.fields
            types = [_arrow_type(field) for field in fields]
            names = [field.name for field in fields]
            found = False
            while rows := self._cursor.fetchmany(_BATCH_ROWS):
                found = True
                yield pa.RecordBatch.from_arrays(
                    [pa.array([row[i] for row in rows], type=typ) for i, typ in enumerate(types)], names=names
                )
            if not found:
                yield pa.RecordBatch.from_arrays([pa.array([], type=typ) for typ in types], names=names)
        except pymysql.MySQLError as exc:
            raise _safe_error(exc) from None
        except (pa.ArrowException, ValueError, TypeError, OverflowError):
            raise ConnectorError("MySQL result conversion failed; check unsupported values such as zero dates") from None
        finally:
            # SSCursor.close drains unread rows on a live connection. Disconnect first on truncation or failure.
            _close_stream(self._conn, self._cursor)


class MySQLConnector(BaseConnector):
    dialect = "mysql"
    supports_dry_run = False
    supports_cancel = False

    def __init__(
        self, *, host: str, port: int, database: str, user: str, password: str, ssl_ca: str | None = None,
    ) -> None:
        self.host, self.port, self.database, self.user = host, port, database, user
        self._password = password
        self._tls = tls_context(ssl_ca)

    @property
    def default_namespace(self) -> tuple[str, ...]:
        return (self.database,)

    def _connect(self, timeout_s: int = 30):
        conn = pymysql.Connection(
            host=self.host, port=self.port, database=self.database, user=self.user, password=self._password,
            ssl=self._tls, charset="utf8mb4", defer_connect=True, local_infile=False,
            connect_timeout=10, read_timeout=timeout_s, write_timeout=timeout_s,
            sql_mode="STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION",
        )
        try:
            conn.connect(sock=mysql_socket(self.host, self.port))
            version = conn.get_server_info()
            if not version.startswith(("8.0.", "8.4.")) or "mariadb" in version.lower():
                raise ConnectorError("only MySQL 8.0 and 8.4 are supported")
            with conn.cursor() as cur:
                cur.execute("SELECT @@lower_case_table_names")
                if cur.fetchone()[0] not in (0, 1, 2):
                    raise ConnectorError("unsupported MySQL identifier case mode")
                cur.execute("SET SESSION TRANSACTION READ ONLY")
                cur.execute("SET SESSION MAX_EXECUTION_TIME = %s", (timeout_s * 1000,))
                cur.execute("SET SESSION time_zone = '+00:00'")
                cur.execute("START TRANSACTION READ ONLY")
            return conn
        except pymysql.MySQLError as exc:
            if conn.open:
                conn.close()
            raise _safe_error(exc) from None
        except ConnectorError:
            if conn.open:
                conn.close()
            raise

    @contextmanager
    def _cursor(self):
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                try:
                    yield cur
                except pymysql.MySQLError as exc:
                    raise _safe_error(exc) from None
        finally:
            conn.close()

    def test_connection(self) -> list[ConnectionTestStep]:
        try:
            conn = self._connect()
        except ConnectorError as exc:
            return [ConnectionTestStep(step="auth", ok=False, detail=str(exc))]
        steps = [ConnectionTestStep(step="auth", ok=True), ConnectionTestStep(step="network", ok=True)]
        step: Literal["metadata", "read"] = "metadata"
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA LIMIT 1")
                cur.fetchone()
                steps.append(ConnectionTestStep(step=step, ok=True))
                step = "read"
                cur.execute("SELECT 1")
                cur.fetchone()
                steps.append(ConnectionTestStep(step=step, ok=True))
        except pymysql.MySQLError as exc:
            steps.append(ConnectionTestStep(step=step, ok=False, detail=str(_safe_error(exc))))
        finally:
            conn.close()
        return steps

    @staticmethod
    def _path(path: tuple[str, ...], length: int) -> None:
        if (
            len(path) != length or any(not _MYSQL_IDENTIFIER.fullmatch(p) for p in path)
            or path[0].lower() in _MYSQL_SYSTEM_DATABASES
        ):
            raise ValueError("unsupported MySQL database or table path")

    def list_catalog(self, parent: tuple[str, ...] = ()) -> list[CatalogNode]:
        if not parent:
            with self._cursor() as cur:
                cur.execute("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA ORDER BY SCHEMA_NAME LIMIT 10001")
                rows = cur.fetchall()
            if len(rows) > _MAX_LIST:
                raise ConnectorError("MySQL database listing exceeds 10,000 rows")
            return [
                CatalogNode(kind="schema", path=(name,), name=name) for (name,) in rows
                if _MYSQL_IDENTIFIER.fullmatch(name) and name.lower() not in _MYSQL_SYSTEM_DATABASES
            ]
        self._path(parent, 1)
        with self._cursor() as cur:
            cur.execute(
                "SELECT TABLE_NAME, TABLE_TYPE, TABLE_COMMENT FROM INFORMATION_SCHEMA.TABLES "
                "WHERE BINARY TABLE_SCHEMA = %s ORDER BY TABLE_NAME LIMIT 10001", (parent[0],),
            )
            rows = cur.fetchall()
        if len(rows) > _MAX_LIST:
            raise ConnectorError("MySQL table listing exceeds 10,000 rows")
        return [
            CatalogNode(kind="view" if kind == "VIEW" else "table", path=(*parent, name),
                        name=name, description=comment or None)
            for name, kind, comment in rows if _MYSQL_IDENTIFIER.fullmatch(name)
        ]

    def list_catalog_complete(self, parent: tuple[str, ...] = ()) -> list[CatalogNode]:
        return self.list_catalog(parent)

    def _columns(self, database: str, table: str | None = None) -> dict[tuple[str, ...], list[ColumnSchema]]:
        sql = (
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_COMMENT "
            "FROM INFORMATION_SCHEMA.COLUMNS WHERE BINARY TABLE_SCHEMA = %s"
        )
        params: tuple[str, ...] = (database,)
        if table is not None:
            sql += " AND BINARY TABLE_NAME = %s"
            params = (database, table)
        sql += " ORDER BY TABLE_NAME, ORDINAL_POSITION"
        out: dict[tuple[str, ...], list[ColumnSchema]] = {}
        with self._cursor() as cur:
            cur.execute(sql, params)
            for name, column, typ, nullable, comment in cur:
                if _MYSQL_IDENTIFIER.fullmatch(name):
                    out.setdefault((database, name), []).append(
                        ColumnSchema(name=column, type=typ, nullable=nullable == "YES", description=comment or None)
                    )
        return out

    def get_schema(self, path: tuple[str, ...]) -> TableSchema:
        self._path(path, 2)
        with self._cursor() as cur:
            cur.execute(
                # UPDATE_TIME is NULL for some engines/versions (often InnoDB); the UI then omits it.
                "SELECT TABLE_TYPE, TABLE_COMMENT, UPDATE_TIME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE BINARY TABLE_SCHEMA = %s AND BINARY TABLE_NAME = %s", path,
            )
            row = cur.fetchone()
        if row is None:
            raise ValueError("MySQL table not found")
        return TableSchema(
            path=path, kind="view" if row[0] == "VIEW" else "table", description=row[1] or None,
            columns=self._columns(*path).get(path, []), modified_at=row[2],
        )

    def list_columns(self, container: tuple[str, ...]) -> dict[tuple[str, ...], list[ColumnSchema]]:
        self._path(container, 1)
        return self._columns(container[0])

    def estimate_cost(self, sql: str) -> CostEstimate:
        return CostEstimate()

    def execute_paged(self, sql: str, limits: QueryLimits) -> QueryExecution:
        conn = self._connect(limits.timeout_s)
        cur = conn.cursor(SSCursor)
        try:
            cur.execute(sql)
        except pymysql.MySQLError as exc:
            _close_stream(conn, cur)
            raise _safe_error(exc) from None
        return MySQLExecution(conn, cur)

    def cancel_job(self, job_id: str) -> None:
        raise ConnectorError("MySQL query cancellation is not supported")
