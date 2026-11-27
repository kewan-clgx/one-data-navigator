"""Oracle Thin: verified TCPS through approved egress, accessible schemas and bounded read-only results."""

from __future__ import annotations

import re
import ssl
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from time import monotonic
from typing import Literal

import oracledb
import pyarrow as pa

from odn.config import settings
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
from odn.platform.policy.sql_policy import _ORACLE_IDENTIFIER, _ORACLE_SYSTEM_SCHEMAS, SqlPolicy

_MAX_LIST = 10_000
_BATCH_ROWS = 100
_MAX_CELL_BYTES = 1024 * 1024
_MAX_BATCH_BYTES = 8 * 1024 * 1024
_OBJECTS = (
    "SELECT o.owner, o.object_name, o.object_type FROM ALL_OBJECTS o JOIN ALL_USERS u ON u.username = o.owner "
    "WHERE u.oracle_maintained = 'N' AND o.object_type IN ('TABLE', 'VIEW') "
)


def tls_context(ca: str | None = None) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if ca:
        if "PRIVATE KEY" in ca or "-----BEGIN CERTIFICATE-----" not in ca:
            raise ValueError("Oracle CA must contain PEM certificates, not private keys or a file path")
        try:
            ctx.load_verify_locations(cadata=ca)
        except (ssl.SSLError, ValueError):
            raise ValueError("invalid Oracle CA certificate bundle") from None
    return ctx


def _safe_error(exc: oracledb.Error) -> ConnectorError:
    error = exc.args[0] if exc.args else None
    code = getattr(error, "full_code", "")
    if code in ("ORA-01017", "ORA-01031", "ORA-28000", "ORA-28001"):
        message = "Oracle access denied; check credentials, account status and read grants"
    elif code in ("ORA-01013", "DPY-4024"):
        message = "Oracle request timed out or was interrupted"
    else:
        message = "Oracle request failed; check approved egress, verified TCPS, service name and read grants"
    suffix = f" ({code})" if isinstance(code, str) and re.fullmatch(r"(?:ORA|DPY|DPI)-[0-9]+", code) else ""
    return ConnectorError(message + suffix)


def _deadline(conn: oracledb.Connection, deadline: float) -> None:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise ConnectorError("Oracle query deadline exceeded")
    conn.call_timeout = max(1, min(30_000, int(remaining * 1000)))


def _decimal_output(cursor: oracledb.Cursor, metadata: oracledb.FetchInfo):
    if metadata.type_code == oracledb.DB_TYPE_NUMBER:
        return cursor.var(Decimal, arraysize=cursor.arraysize)
    return None


def _arrow_type(info: oracledb.FetchInfo) -> pa.DataType:
    typ = info.type_code
    if typ == oracledb.DB_TYPE_NUMBER:
        precision, scale = info.precision, info.scale
        if not precision or scale is None or scale == -127:
            # Unconstrained Oracle NUMBER has a wider exponent range than Arrow decimals.
            return pa.string()
        p, s = max(precision, scale), scale
        if p > 76:
            raise ConnectorError("Oracle NUMBER precision exceeds Arrow's supported range")
        return pa.decimal128(p, s) if p <= 38 else pa.decimal256(p, s)
    if typ in (oracledb.DB_TYPE_BINARY_FLOAT, oracledb.DB_TYPE_BINARY_DOUBLE):
        return pa.float64()
    if typ in (oracledb.DB_TYPE_DATE, oracledb.DB_TYPE_TIMESTAMP):
        if typ == oracledb.DB_TYPE_TIMESTAMP and info.scale is not None and info.scale > 6:
            raise ConnectorError("Oracle timestamps above microsecond precision require an explicit conversion")
        return pa.timestamp("us")
    if typ in (oracledb.DB_TYPE_CHAR, oracledb.DB_TYPE_VARCHAR, oracledb.DB_TYPE_NCHAR,
               oracledb.DB_TYPE_NVARCHAR, oracledb.DB_TYPE_CLOB, oracledb.DB_TYPE_NCLOB):
        return pa.string()
    if typ in (oracledb.DB_TYPE_RAW, oracledb.DB_TYPE_BLOB):
        return pa.binary()
    raise ConnectorError("unsupported Oracle result type; explicitly convert timezone, interval, LONG or object values")


class OracleExecution(QueryExecution):
    job_id = None

    def __init__(self, conn: oracledb.Connection, cursor: oracledb.Cursor, deadline: float) -> None:
        self._conn, self._cursor, self._deadline = conn, cursor, deadline

    def batches(self) -> Iterator[pa.RecordBatch]:
        try:
            metadata = self._cursor.description or []
            types = [_arrow_type(info) for info in metadata]
            names = [info.name for info in metadata]
            row_bound = sum((info.internal_size or 4000) + 128 for info in metadata)
            if row_bound * _BATCH_ROWS > _MAX_BATCH_BYTES:
                raise ConnectorError("Oracle projected columns exceed the bounded fetch buffer; select fewer columns")
            found = False
            while True:
                _deadline(self._conn, self._deadline)
                rows = self._cursor.fetchmany(_BATCH_ROWS)
                if not rows:
                    break
                found = True
                columns: list[list] = [[] for _ in types]
                batch_bytes = 0
                for row in rows:
                    for i, value in enumerate(row):
                        if isinstance(value, oracledb.LOB):
                            _deadline(self._conn, self._deadline)
                            size = value.size()
                            char_lob = value.type in (oracledb.DB_TYPE_CLOB, oracledb.DB_TYPE_NCLOB)
                            bound = size * 4 if char_lob else size
                            if bound > _MAX_CELL_BYTES or batch_bytes + bound > _MAX_BATCH_BYTES:
                                raise ConnectorError("Oracle LOB exceeds the bounded result size; select a smaller slice")
                            _deadline(self._conn, self._deadline)
                            value = value.read()
                        if isinstance(value, Decimal) and pa.types.is_string(types[i]):
                            value = str(value)
                        size = len(value.encode("utf-8")) if isinstance(value, str) else (
                            len(value) if isinstance(value, bytes) else 32
                        )
                        batch_bytes += size
                        if size > _MAX_CELL_BYTES or batch_bytes > _MAX_BATCH_BYTES:
                            raise ConnectorError("Oracle result batch exceeds the bounded result size")
                        columns[i].append(value)
                yield pa.RecordBatch.from_arrays(
                    [pa.array(values, type=typ) for values, typ in zip(columns, types, strict=True)], names=names
                )
            if not found:
                yield pa.RecordBatch.from_arrays([pa.array([], type=typ) for typ in types], names=names)
        except oracledb.Error as exc:
            raise _safe_error(exc) from None
        except (pa.ArrowException, ValueError, TypeError, OverflowError):
            raise ConnectorError("Oracle result conversion failed; numeric range or timestamp precision is unsupported") from None
        finally:
            try:
                self._cursor.close()
            finally:
                self._conn.close()


class OracleConnector(BaseConnector):
    dialect = "oracle"
    supports_dry_run = False
    supports_cancel = False

    def __init__(
        self, *, host: str, port: int, service_name: str, schema: str, user: str,
        password: str, ssl_ca: str | None = None,
    ) -> None:
        self._path((schema,), 1)
        self.host, self.port, self.service_name, self.schema, self.user = host, port, service_name, schema, user
        self._password, self._tls = password, tls_context(ssl_ca)

    @property
    def default_namespace(self) -> tuple[str, ...]:
        return (self.schema,)

    def _connect(self, timeout_s: int = 30) -> oracledb.Connection:
        if not settings.oracle_proxy_host:
            raise ConnectorError("Oracle egress proxy is not configured; contact an operator")
        conn = None
        try:
            conn = oracledb.connect(
                host=self.host, port=self.port, service_name=self.service_name, user=self.user, password=self._password,
                protocol="tcps", ssl_context=self._tls, ssl_server_dn_match=True,
                https_proxy=settings.oracle_proxy_host, https_proxy_port=settings.oracle_proxy_port,
                server_type="dedicated", retry_count=0, tcp_connect_timeout=min(10, timeout_s),
                stmtcachesize=0, disable_oob=True,
            )
            if not conn.thin or not conn.version.startswith(("19.", "23.", "26.")):
                raise ConnectorError("Oracle requires Thin mode and a supported 19c/Free server")
            conn.call_timeout = min(30, timeout_s) * 1000
            with conn.cursor() as cur:
                cur.execute(f'ALTER SESSION SET CURRENT_SCHEMA = "{self.schema}"')
                cur.execute("ALTER SESSION SET TIME_ZONE = '+00:00'")
                cur.execute("SET TRANSACTION READ ONLY")
            return conn
        except oracledb.Error as exc:
            if conn is not None:
                conn.close()
            raise _safe_error(exc) from None
        except ConnectorError:
            if conn is not None:
                conn.close()
            raise

    @contextmanager
    def _cursor(self):
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.arraysize = _BATCH_ROWS
                try:
                    yield cur
                except oracledb.Error as exc:
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
                cur.execute("SELECT owner FROM ALL_TABLES FETCH FIRST 1 ROWS ONLY")
                cur.fetchone()
                steps.append(ConnectionTestStep(step=step, ok=True))
                step = "read"
                cur.execute("SELECT 1 FROM SYS.DUAL")
                cur.fetchone()
                steps.append(ConnectionTestStep(step=step, ok=True))
        except oracledb.Error as exc:
            steps.append(ConnectionTestStep(step=step, ok=False, detail=str(_safe_error(exc))))
        finally:
            conn.close()
        return steps

    @staticmethod
    def _path(path: tuple[str, ...], length: int) -> None:
        if (
            len(path) != length or any(not _ORACLE_IDENTIFIER.fullmatch(p) for p in path)
            or path[0].upper() in _ORACLE_SYSTEM_SCHEMAS or path[-1].upper() == "DUAL"
        ):
            raise ValueError("unsupported Oracle schema or table path")

    def list_catalog(self, parent: tuple[str, ...] = ()) -> list[CatalogNode]:
        with self._cursor() as cur:
            if not parent:
                cur.execute(f"SELECT DISTINCT owner FROM ({_OBJECTS}) ORDER BY owner FETCH FIRST 10001 ROWS ONLY")
                rows = cur.fetchall()
                if len(rows) > _MAX_LIST:
                    raise ConnectorError("Oracle schema listing exceeds 10,000 rows")
                return [
                    CatalogNode(kind="schema", path=(owner,), name=owner) for (owner,) in rows
                    if _ORACLE_IDENTIFIER.fullmatch(owner) and owner.upper() not in _ORACLE_SYSTEM_SCHEMAS
                ]
            self._path(parent, 1)
            cur.execute(
                _OBJECTS + "AND o.owner = :owner ORDER BY o.object_name FETCH FIRST 10001 ROWS ONLY",
                {"owner": parent[0]},
            )
            rows = cur.fetchall()
        if len(rows) > _MAX_LIST:
            raise ConnectorError("Oracle object listing exceeds 10,000 rows")
        return [
            CatalogNode(kind="view" if kind == "VIEW" else "table", path=(owner, name), name=name)
            for owner, name, kind in rows if _ORACLE_IDENTIFIER.fullmatch(name) and name.upper() != "DUAL"
        ]

    def list_catalog_complete(self, parent: tuple[str, ...] = ()) -> list[CatalogNode]:
        return self.list_catalog(parent)

    def _columns(self, owner: str, table: str | None = None) -> dict[tuple[str, ...], list[ColumnSchema]]:
        sql = (
            "SELECT c.table_name, c.column_name, c.data_type, c.nullable, cm.comments "
            "FROM ALL_TAB_COLUMNS c LEFT JOIN ALL_COL_COMMENTS cm ON cm.owner = c.owner "
            "AND cm.table_name = c.table_name AND cm.column_name = c.column_name "
            "WHERE c.owner = :owner "
            "AND c.table_name IN (SELECT object_name FROM (" + _OBJECTS + "AND o.owner = :owner)) "
        )
        params = {"owner": owner}
        if table is not None:
            sql += "AND c.table_name = :table_name "
            params["table_name"] = table
        sql += "ORDER BY c.table_name, c.column_id FETCH FIRST 100001 ROWS ONLY"
        out: dict[tuple[str, ...], list[ColumnSchema]] = {}
        with self._cursor() as cur:
            cur.execute(sql, params)
            for i, (name, column, typ, nullable, comment) in enumerate(cur):
                if i >= 100_000:
                    raise ConnectorError("Oracle column listing exceeds 100,000 rows")
                if _ORACLE_IDENTIFIER.fullmatch(name) and name.upper() != "DUAL":
                    out.setdefault((owner, name), []).append(
                        ColumnSchema(name=column, type=typ, nullable=nullable == "Y", description=comment)
                    )
        return out

    def get_schema(self, path: tuple[str, ...]) -> TableSchema:
        self._path(path, 2)
        with self._cursor() as cur:
            cur.execute(
                _OBJECTS + "AND o.owner = :owner AND o.object_name = :name", {"owner": path[0], "name": path[1]}
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("Oracle table or view not found")
            cur.execute(
                "SELECT comments FROM ALL_TAB_COMMENTS WHERE owner = :owner AND table_name = :name",
                {"owner": path[0], "name": path[1]},
            )
            comment = cur.fetchone()
        return TableSchema(
            path=path, kind="view" if row[2] == "VIEW" else "table",
            columns=self._columns(*path).get(path, []), description=comment[0] if comment else None,
        )

    def list_columns(self, container: tuple[str, ...]) -> dict[tuple[str, ...], list[ColumnSchema]]:
        self._path(container, 1)
        return self._columns(container[0])

    def estimate_cost(self, sql: str) -> CostEstimate:
        return CostEstimate()

    def execute_paged(self, sql: str, limits: QueryLimits) -> QueryExecution:
        deadline = monotonic() + limits.timeout_s
        conn = self._connect(limits.timeout_s)
        cur = conn.cursor()
        try:
            # Resolve real tables/views before user SQL: allowlists must not authorize synonyms or system owners.
            decision = SqlPolicy("oracle", frozenset({"*.*"}), self.default_namespace).validate(sql)
            if not decision.allowed:
                raise ConnectorError(decision.reason or "Oracle query policy rejected")
            for path in decision.tables:
                owner, name = path.split(".")
                _deadline(conn, deadline)
                cur.execute(_OBJECTS + "AND o.owner = :owner AND o.object_name = :name", {"owner": owner, "name": name})
                if cur.fetchone() is None:
                    raise ConnectorError("Oracle query object is not an accessible non-system table/view; synonyms are unsupported")
            cur.prefetchrows = 0
            cur.arraysize = _BATCH_ROWS
            cur.outputtypehandler = _decimal_output
            _deadline(conn, deadline)
            cur.execute(sql)
            return OracleExecution(conn, cur, deadline)
        except oracledb.Error as exc:
            cur.close()
            conn.close()
            raise _safe_error(exc) from None
        except ConnectorError:
            cur.close()
            conn.close()
            raise

    def cancel_job(self, job_id: str) -> None:
        raise ConnectorError("Oracle query cancellation is not supported")
