import socket
import ssl
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pyarrow as pa
import pymysql
import pytest
from pymysql.constants import FIELD_TYPE, FLAG

from odn.config import settings
from odn.platform.connections.service import PersonalConnectionInput, unsaved_connector
from odn.platform.connectors.base import ConnectorError, QueryLimits
from odn.platform.connectors.mysql import MySQLConnector, MySQLExecution
from odn.platform.net import mysql_socket


@pytest.fixture
def connector():
    return MySQLConnector(host="mysql.example.com", port=3306, database="Sales", user="reader", password="test-only")


def test_mysql_profile_requires_explicit_scope_and_verified_tls():
    data = PersonalConnectionInput(
        name="mysql", kind="mysql", host="MYSQL.EXAMPLE.COM", database="Sales", user="reader",
        password="secret with spaces ", allowed_objects=["Sales.*", "Reporting.Orders"],
    )
    spec = data.resolved(None)
    assert spec.config == {
        "host": "mysql.example.com", "port": 3306, "database": "Sales", "user": "reader", "ssl_ca": None,
    }
    assert spec.allowed_objects == ["Sales.*", "Reporting.Orders"]
    assert "secret with spaces" not in repr(data)
    assert "password" not in spec.config
    assert unsaved_connector(data, None).default_namespace == ("Sales",)
    with pytest.raises(ValueError, match="password is required"):
        unsaved_connector(data.model_copy(update={"password": None}), None)
    for update in (
        {"ssl_mode": "disable"}, {"database": "mysql"}, {"database": "Sales.DB"},
        {"allowed_objects": []}, {"allowed_objects": ["mysql.*"]},
    ):
        with pytest.raises(ValueError):
            data.model_copy(update=update).resolved(None)
    with pytest.raises(ValueError, match="CA must contain"):
        data.model_copy(update={"ssl_ca": "/etc/ssl/private/key.pem"}).resolved(None)


@pytest.fixture
def approved(monkeypatch):
    monkeypatch.setattr(settings, "mysql_allowed_endpoints", "mysql.example.com:3306")
    monkeypatch.setattr(settings, "mysql_allowed_cidrs", "10.20.0.0/16,2001:4860::/32")
    opened = MagicMock()
    monkeypatch.setattr(socket, "create_connection", opened)
    return opened


def test_destination_requires_both_endpoint_and_all_resolved_ips(approved, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.20.1.2", 3306))])
    mysql_socket("mysql.example.com", 3306)
    assert approved.call_count == 1
    assert approved.call_args.args == (("10.20.1.2", 3306),)
    assert 0 < approved.call_args.kwargs["timeout"] <= 10
    approved.reset_mock()
    with pytest.raises(ConnectorError, match="endpoint is not approved"):
        mysql_socket("mysql.example.com", 3307)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (2, 1, 6, "", ("10.20.1.2", 3306)), (2, 1, 6, "", ("10.99.1.2", 3306)),
    ])
    with pytest.raises(ConnectorError, match="unapproved address"):
        mysql_socket("mysql.example.com", 3306)
    approved.assert_not_called()
    monkeypatch.setattr(settings, "mysql_allowed_cidrs", "")
    with pytest.raises(ConnectorError, match="not approved"):
        mysql_socket("mysql.example.com", 3306)


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "224.0.0.1", "0.0.0.0"])
def test_dangerous_addresses_blocked_even_with_broad_operator_range(approved, monkeypatch, address):
    monkeypatch.setattr(settings, "mysql_allowed_cidrs", "0.0.0.0/0,::/0")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (address, 3306))])
    with pytest.raises(ConnectorError, match="blocked"):
        mysql_socket("mysql.example.com", 3306)
    approved.assert_not_called()


def test_connect_pins_socket_preserves_tls_hostname_and_enforces_read_only(connector, monkeypatch):
    conn, sock = MagicMock(), MagicMock()
    conn.get_server_info.return_value = "8.4.0"
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (0,)
    factory = MagicMock(return_value=conn)
    monkeypatch.setattr("odn.platform.connectors.mysql.pymysql.Connection", factory)
    monkeypatch.setattr("odn.platform.connectors.mysql.mysql_socket", lambda *a: sock)
    assert connector._connect(7) is conn
    kwargs = factory.call_args.kwargs
    assert kwargs["host"] == "mysql.example.com"
    assert kwargs["ssl"].check_hostname and kwargs["ssl"].verify_mode == ssl.CERT_REQUIRED
    assert kwargs["defer_connect"] and not kwargs["local_infile"]
    assert kwargs["read_timeout"] == kwargs["write_timeout"] == 7
    conn.connect.assert_called_once_with(sock=sock)
    statements = conn.cursor.return_value.__enter__.return_value.execute.call_args_list
    assert any(c.args == ("SET SESSION TRANSACTION READ ONLY",) for c in statements)
    assert any(c.args == ("START TRANSACTION READ ONLY",) for c in statements)
    assert any(c.args == ("SET SESSION MAX_EXECUTION_TIME = %s", (7000,)) for c in statements)


@pytest.mark.parametrize("version", ["5.7.44", "9.0.1", "8.0.0-MariaDB"])
def test_unsupported_server_is_rejected_and_closed(connector, monkeypatch, version):
    conn = MagicMock()
    conn.get_server_info.return_value = version
    monkeypatch.setattr("odn.platform.connectors.mysql.pymysql.Connection", MagicMock(return_value=conn))
    monkeypatch.setattr("odn.platform.connectors.mysql.mysql_socket", MagicMock())
    with pytest.raises(ConnectorError, match="only MySQL 8.0 and 8.4"):
        connector._connect()
    conn.close.assert_called_once()


def test_tls_is_required_before_sending_credentials():
    conn = pymysql.Connection(
        user="test", password="must-not-send", host="mysql.example.com",
        ssl=ssl.create_default_context(), defer_connect=True,
    )
    conn.server_version = "8.4.0"
    conn.server_capabilities = 0
    conn.write_packet = MagicMock()
    with pytest.raises(pymysql.OperationalError) as error:
        conn._request_authentication()
    assert error.value.args[0] == 2026
    conn.write_packet.assert_not_called()


def test_catalog_discovers_databases_and_parameterizes_case_sensitive_metadata(connector, monkeypatch):
    conn, cur = MagicMock(), MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    monkeypatch.setattr(connector, "_connect", lambda *a: conn)
    cur.fetchall.return_value = [("Sales",), ("Reporting",), ("mysql",), ("bad.name",)]
    assert [n.path for n in connector.list_catalog()] == [("Sales",), ("Reporting",)]
    cur.fetchall.return_value = [("Orders", "BASE TABLE", "sales"), ("Totals", "VIEW", "")]
    assert [n.path for n in connector.list_catalog(("Reporting",))] == [
        ("Reporting", "Orders"), ("Reporting", "Totals"),
    ]
    sql, params = cur.execute.call_args.args
    assert "BINARY TABLE_SCHEMA = %s" in sql and params == ("Reporting",)
    with pytest.raises(ValueError, match="unsupported MySQL"):
        connector.list_catalog(("mysql",))


def test_stream_stable_arrow_types_and_early_cleanup_without_draining():
    conn, cur = MagicMock(), MagicMock()
    fields = [
        SimpleNamespace(name="id", type_code=FIELD_TYPE.LONGLONG, flags=FLAG.UNSIGNED),
        SimpleNamespace(name="amount", type_code=FIELD_TYPE.NEWDECIMAL, scale=2, length=22, flags=0),
        SimpleNamespace(name="elapsed", type_code=FIELD_TYPE.TIME),
        SimpleNamespace(name="text", type_code=FIELD_TYPE.VAR_STRING, charsetnr=45),
    ]
    cur._result.fields = fields
    cur.fetchmany.side_effect = [
        [(2**64 - 1, None, timedelta(seconds=-1), None)],
        [(1, Decimal("3.14"), timedelta(hours=26), "hello")],
        [],
    ]
    execution = MySQLExecution(conn, cur)
    stream = execution.batches()
    first, second = next(stream), next(stream)
    assert first.schema == second.schema
    assert first.column(0).type == pa.uint64()
    assert second.to_pylist()[0]["amount"] == Decimal("3.14")
    stream.close()
    conn.close.assert_called_once()
    assert cur._result.unbuffered_active is False
    cur.close.assert_called_once()
    cur.fetchall.assert_not_called()
    assert cur.fetchmany.call_count == 2


@pytest.mark.parametrize("code", [1045, 2026, 2013, 3024])
def test_driver_errors_never_expose_credentials_or_sql(connector, monkeypatch, code):
    conn = MagicMock()
    conn.connect.side_effect = pymysql.OperationalError(code, "sensitive password or raw SQL")
    monkeypatch.setattr("odn.platform.connectors.mysql.pymysql.Connection", MagicMock(return_value=conn))
    monkeypatch.setattr("odn.platform.connectors.mysql.mysql_socket", MagicMock())
    steps = connector.test_connection()
    assert len(steps) == 1 and not steps[0].ok
    assert str(code) in steps[0].detail and "sensitive" not in steps[0].detail


def test_query_failure_closes_resources(connector, monkeypatch):
    conn, cur = MagicMock(), MagicMock()
    conn.cursor.return_value = cur
    cur.execute.side_effect = pymysql.ProgrammingError(1064, "sensitive SQL")
    monkeypatch.setattr(connector, "_connect", lambda *a: conn)
    with pytest.raises(ConnectorError, match="1064") as error:
        connector.execute_paged("SELECT 1", QueryLimits())
    assert "sensitive" not in str(error.value)
    conn.close.assert_called_once()
    cur.close.assert_called_once()
