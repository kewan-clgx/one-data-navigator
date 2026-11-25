from datetime import datetime
from decimal import Decimal
from time import monotonic
from types import SimpleNamespace
from unittest.mock import MagicMock

import oracledb
import pyarrow as pa
import pytest

from odn.config import settings
from odn.platform.connections.service import PersonalConnectionInput, unsaved_connector
from odn.platform.connectors.base import ConnectorError, QueryLimits
from odn.platform.connectors.oracle import OracleConnector, OracleExecution


@pytest.fixture
def connector():
    return OracleConnector(
        host="oracle.example.com", port=2484, service_name="reports.example.com",
        schema="HR", user="READER", password="test-only",
    )


def test_profile_requires_tcps_service_and_explicit_allowlist():
    data = PersonalConnectionInput(
        name="oracle", kind="oracle", host="ORACLE.EXAMPLE.COM", service_name="reports",
        schema="HR", user="READER", password="test-only", allowed_objects=["HR.*", "REPORTING.Orders"],
    )
    spec = data.resolved(None)
    assert spec.config == {
        "host": "oracle.example.com", "port": 2484, "service_name": "reports", "schema": "HR",
        "user": "READER", "ssl_ca": None,
    }
    assert unsaved_connector(data, None).default_namespace == ("HR",)
    assert "test-only" not in repr(data) and "password" not in spec.config
    for update in (
        {"user": "SYS"}, {"ssl_mode": "disable"}, {"service_name": "(DESCRIPTION=x)"},
        {"schema": "SYS"}, {"allowed_objects": []}, {"allowed_objects": ["SYS.*"]},
        {"ssl_ca": "/etc/ssl/private/key.pem"},
    ):
        with pytest.raises(ValueError):
            data.model_copy(update=update).resolved(None)
    with pytest.raises(ValueError, match="password is required"):
        unsaved_connector(data.model_copy(update={"password": None}), None)


def test_connect_requires_proxy_and_keeps_end_to_end_tls(connector, monkeypatch):
    factory = MagicMock()
    monkeypatch.setattr("odn.platform.connectors.oracle.oracledb.connect", factory)
    monkeypatch.setattr(settings, "oracle_proxy_host", "")
    with pytest.raises(ConnectorError, match="proxy is not configured"):
        connector._connect()
    factory.assert_not_called()
    monkeypatch.setattr(settings, "oracle_proxy_host", "oracle-egress")
    conn = factory.return_value
    conn.thin, conn.version = True, "19.3.0.0.0"
    assert connector._connect(7) is conn
    kwargs = factory.call_args.kwargs
    assert kwargs["protocol"] == "tcps" and kwargs["ssl_server_dn_match"]
    assert kwargs["ssl_context"].check_hostname
    assert kwargs["https_proxy"] == "oracle-egress" and kwargs["host"] == "oracle.example.com"
    statements = conn.cursor.return_value.__enter__.return_value.execute.call_args_list
    assert statements[-1].args == ("SET TRANSACTION READ ONLY",)
    assert conn.call_timeout == 7000


def test_catalog_case_and_synonyms_are_not_authorized(connector, monkeypatch):
    conn, cur = MagicMock(), MagicMock()
    conn.cursor.return_value = cur
    cur.__enter__.return_value = cur
    monkeypatch.setattr(connector, "_connect", lambda *a: conn)
    cur.fetchall.return_value = [("HR",), ("REPORTING",), ("SYS",)]
    assert [n.path for n in connector.list_catalog()] == [("HR",), ("REPORTING",)]
    cur.fetchall.return_value = [("REPORTING", "Orders", "TABLE"), ("REPORTING", "Totals", "VIEW")]
    assert [n.path for n in connector.list_catalog(("REPORTING",))] == [
        ("REPORTING", "Orders"), ("REPORTING", "Totals"),
    ]
    assert cur.execute.call_args.args[1] == {"owner": "REPORTING"}
    cur.fetchone.return_value = None
    with pytest.raises(ConnectorError, match="synonyms are unsupported"):
        connector.execute_paged("SELECT * FROM HR.ALIAS", QueryLimits())
    assert not any(c.args == ("SELECT * FROM HR.ALIAS",) for c in cur.execute.call_args_list)
    conn.close.assert_called()


def test_stream_preserves_numbers_dates_and_null_leading_schema():
    conn, cur = MagicMock(), MagicMock()
    cur.description = [
        SimpleNamespace(name="amount", type_code=oracledb.DB_TYPE_NUMBER, precision=38, scale=2, internal_size=22),
        SimpleNamespace(name="wide", type_code=oracledb.DB_TYPE_NUMBER, precision=0, scale=-127, internal_size=22),
        SimpleNamespace(name="at", type_code=oracledb.DB_TYPE_DATE, internal_size=7),
        SimpleNamespace(name="data", type_code=oracledb.DB_TYPE_RAW, internal_size=100),
    ]
    at = datetime(2024, 1, 2, 3, 4, 5)
    cur.fetchmany.side_effect = [
        [(None, None, None, None)],
        [(Decimal("1234567890123456789012345678901234.56"), Decimal("1E+125"), at, b"\x00\xff")],
        [],
    ]
    stream = OracleExecution(conn, cur, monotonic() + 10).batches()
    first, second, *rest = stream
    assert not rest and first.schema == second.schema
    assert second.schema.field("amount").type == pa.decimal128(38, 2)
    assert second.column(1).to_pylist() == ["1E+125"]
    assert second.column(2).to_pylist() == [at]
    assert second.column(3).to_pylist() == [b"\x00\xff"]
    conn.close.assert_called_once()
    cur.close.assert_called_once()


def test_lob_limit_checked_before_materialization_and_early_close():
    conn, cur = MagicMock(), MagicMock()
    cur.description = [SimpleNamespace(name="body", type_code=oracledb.DB_TYPE_CLOB, internal_size=None)]
    lob = MagicMock(spec=oracledb.LOB)
    lob.type = oracledb.DB_TYPE_CLOB
    lob.size.return_value = 2 * 1024 * 1024
    cur.fetchmany.return_value = [(lob,)]
    with pytest.raises(ConnectorError, match="LOB exceeds"):
        next(OracleExecution(conn, cur, monotonic() + 10).batches())
    lob.read.assert_not_called()
    conn.close.assert_called_once()
    cur = MagicMock()
    cur.description = [SimpleNamespace(name="s", type_code=oracledb.DB_TYPE_VARCHAR, internal_size=50)]
    cur.fetchmany.return_value = [("one batch",)]
    batches = OracleExecution(MagicMock(), cur, monotonic() + 10).batches()
    next(batches)
    batches.close()
    assert cur.fetchmany.call_count == 1
    cur.close.assert_called_once()


def test_deadline_and_driver_errors_are_sanitized():
    conn, cur = MagicMock(), MagicMock()
    cur.description = []
    with pytest.raises(ConnectorError, match="deadline"):
        next(OracleExecution(conn, cur, monotonic() - 1).batches())
    error = oracledb.DatabaseError(SimpleNamespace(full_code="ORA-01017", message="password=secret SQL=private"))
    cur.fetchmany.side_effect = error
    with pytest.raises(ConnectorError) as failure:
        next(OracleExecution(conn, cur, monotonic() + 10).batches())
    assert "ORA-01017" in str(failure.value)
    assert "secret" not in str(failure.value) and "private" not in str(failure.value)


def test_empty_and_unsupported_result_types_close_resources():
    conn, cur = MagicMock(), MagicMock()
    cur.description = [SimpleNamespace(name="n", type_code=oracledb.DB_TYPE_NUMBER, precision=5, scale=0, internal_size=22)]
    cur.fetchmany.return_value = []
    [batch] = list(OracleExecution(conn, cur, monotonic() + 10).batches())
    assert batch.num_rows == 0 and batch.schema.field("n").type == pa.decimal128(5, 0)
    cur.description = [SimpleNamespace(name="tz", type_code=oracledb.DB_TYPE_TIMESTAMP_TZ)]
    with pytest.raises(ConnectorError, match="unsupported Oracle result type"):
        next(OracleExecution(conn, cur, monotonic() + 10).batches())
    cur.description = [SimpleNamespace(name="ns", type_code=oracledb.DB_TYPE_TIMESTAMP, scale=9)]
    with pytest.raises(ConnectorError, match="microsecond precision"):
        next(OracleExecution(conn, cur, monotonic() + 10).batches())
    cur.description = [
        SimpleNamespace(name=f"s{i}", type_code=oracledb.DB_TYPE_VARCHAR, internal_size=32767) for i in range(10)
    ]
    cur.fetchmany.reset_mock()
    with pytest.raises(ConnectorError, match="bounded fetch buffer"):
        next(OracleExecution(conn, cur, monotonic() + 10).batches())
    cur.fetchmany.assert_not_called()
