from unittest.mock import MagicMock

import pyarrow as pa
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from snowflake.connector.errors import (
    BadGatewayError,
    ForbiddenError,
    NonRetryableTlsError,
    OperationalError,
    ProgrammingError,
)

from odn.platform.connections.service import (
    ConnectionProfile,
    PersonalConnectionInput,
    parse_snowflake_key,
    snowflake_connector,
    unsaved_connector,
)
from odn.platform.connectors.base import ConnectorError, QueryLimits
from odn.platform.connectors.snowflake import SnowflakeConnector
from odn.platform.policy.sql_policy import SqlPolicy


@pytest.fixture(scope="module")
def key() -> str:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


@pytest.fixture
def connector(key: str) -> SnowflakeConnector:
    return SnowflakeConnector(account="org-account", user="svc", warehouse="WH", database="DB",
                              schema="PUBLIC", role="READER", private_key=key)


def test_snowflake_input_and_encrypted_private_key(key: str) -> None:
    private = serialization.load_pem_private_key(key.encode(), None)
    encrypted = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"passphrase"),
    ).decode()
    assert parse_snowflake_key(encrypted, "passphrase") == key.encode()
    with pytest.raises(ValueError, match="invalid private key"):
        parse_snowflake_key(encrypted, "wrong")
    data = PersonalConnectionInput(name="sf", kind="snowflake", account="org-account", user="svc@example.com",
                                   warehouse="WH", database="DB", schema="PUBLIC", private_key=encrypted,
                                   private_key_passphrase="passphrase", allowed_objects=["DB.PUBLIC.*", "OTHER.X.T"])
    spec = data.resolved(None)
    assert spec.config["user"] == "svc@example.com"
    assert spec.allowed_objects == ["DB.PUBLIC.*", "OTHER.X.T"]
    assert "private_key" not in repr(data) and "passphrase" not in repr(data)
    with pytest.raises(ValueError, match="allowlist"):
        data.model_copy(update={"allowed_objects": []}).resolved(None)
    with pytest.raises(ValueError, match="uppercase"):
        data.model_copy(update={"database": "db"}).resolved(None)


def test_snowflake_policy_preserves_identifier_case() -> None:
    policy = SqlPolicy(dialect="snowflake", allowed_objects=frozenset({"DB.PUBLIC.ORDERS", "Other.Data.Mixed"}),
                       default_namespace=("DB", "PUBLIC"))
    assert policy.validate("SELECT COUNT(*) FROM orders").tables == ("DB.PUBLIC.ORDERS",)
    assert policy.validate('SELECT * FROM "Other"."Data"."Mixed"').allowed
    for sql in ('SELECT * FROM "db"."PUBLIC"."ORDERS"', 'SELECT * FROM DB.PUBLIC."orders"',
                'SELECT * FROM "DB.PUBLIC"."ORDERS"', "SELECT * FROM OTHER.DATA.MIXED",
                "SELECT SYSTEM$WAIT(5)", "SELECT my_udf(1)", "SELECT DB.PUBLIC.my_udf(1)",
                "SELECT * FROM @stage", "SELECT 1; DELETE FROM DB.PUBLIC.ORDERS"):
        assert not policy.validate(sql).allowed, sql
    assert policy.allows_object(("Other", "Data", "Mixed"))
    assert not policy.allows_object(("OTHER", "DATA", "MIXED"))
    assert policy.allows_container(("Other", "Data"))
    wildcard = SqlPolicy(dialect="snowflake", allowed_objects=frozenset({"*.*.*"}))
    assert not wildcard.allows_container(("DB", "INFORMATION_SCHEMA"))
    assert not wildcard.allows_object(("DB", "INFORMATION_SCHEMA", "TABLES"))
    with pytest.raises(ValueError):
        SqlPolicy.validate_pattern("snowflake", "DB.PUBLIC.bad*name")


def test_root_lists_schemas_from_multiple_databases(connector: SnowflakeConnector, monkeypatch) -> None:
    conn, cursor = MagicMock(), MagicMock()
    conn.cursor.return_value = cursor
    cursor.description = [MagicMock(name="description"), MagicMock(name="description")]
    cursor.description[0].name, cursor.description[1].name = "database_name", "name"
    cursor.fetchmany.return_value = [
        ("DB", "PUBLIC"), ("OTHER", "SALES"), ("DB", "INFORMATION_SCHEMA"), ("BAD.DB", "PUBLIC")
    ]
    monkeypatch.setattr(connector, "_connect", lambda *args: conn)
    nodes = connector.list_catalog()
    assert [(n.name, n.path) for n in nodes] == [("DB.PUBLIC", ("DB", "PUBLIC")), ("OTHER.SALES", ("OTHER", "SALES"))]
    cursor.execute.assert_called_once_with("SHOW TERSE SCHEMAS IN ACCOUNT")
    conn.close.assert_called_once()


def test_query_stream_closes_on_early_stop(connector: SnowflakeConnector, monkeypatch) -> None:
    conn, cursor = MagicMock(), MagicMock()
    conn.cursor.return_value = cursor
    cursor.sfqid = "query-id"
    cursor.fetch_arrow_batches.return_value = iter([pa.table({"X": list(range(5000))})])
    monkeypatch.setattr(connector, "_connect", lambda *args: conn)
    result = connector.execute_paged("SELECT X FROM DB.PUBLIC.T", QueryLimits())
    batches = result.batches()
    assert next(batches).num_rows == 4096
    batches.close()
    cursor.close.assert_called_once()
    conn.close.assert_called_once()


def test_query_errors_are_sanitized_and_resources_closed(connector: SnowflakeConnector, monkeypatch) -> None:
    conn, cursor = MagicMock(), MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.side_effect = ProgrammingError(msg="sensitive raw SQL", errno=123)
    monkeypatch.setattr(connector, "_connect", lambda *args: conn)
    with pytest.raises(ConnectorError, match="error 123") as exc:
        connector.execute_paged("SELECT 1", QueryLimits())
    assert "sensitive" not in str(exc.value)
    conn.close.assert_called_once()
    cursor.close.assert_called_once()


def test_catalog_metadata_is_parameterized(connector: SnowflakeConnector, monkeypatch) -> None:
    conn, cursor = MagicMock(), MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchmany.return_value = [("ORDERS", "BASE TABLE", "sales"), ("report", "VIEW", None)]
    monkeypatch.setattr(connector, "_connect", lambda *args: conn)
    nodes = connector.list_catalog(("OTHER", "SALES"))
    assert [(n.kind, n.path) for n in nodes] == [
        ("table", ("OTHER", "SALES", "ORDERS")), ("view", ("OTHER", "SALES", "report"))
    ]
    sql, params = cursor.execute.call_args.args
    assert '"OTHER".INFORMATION_SCHEMA.TABLES' in sql
    assert params == ("SALES",)
    with pytest.raises(ValueError):
        connector.list_catalog(('DB"; DROP TABLE X;--', "SALES"))


def test_factory_uses_key_without_network(key: str) -> None:
    connector = snowflake_connector({"account": "org-account", "user": "svc", "warehouse": "WH",
                                     "database": "DB", "schema": "PUBLIC"}, key)
    assert connector.default_namespace == ("DB", "PUBLIC")


def test_password_authentication_uses_password_not_key(monkeypatch) -> None:
    data = PersonalConnectionInput(name="sf", kind="snowflake", account="org-account", user="svc",
                                   warehouse="WH", database="DB", schema="PUBLIC", auth_method="password",
                                   password="password with spaces", allowed_objects=["DB.*.*"])
    connector = unsaved_connector(data, None)
    connect = MagicMock()
    monkeypatch.setattr("odn.platform.connectors.snowflake.snowflake.connector.connect", connect)
    connector.test_connection()
    assert connect.call_args.kwargs["password"] == "password with spaces"
    assert connect.call_args.kwargs["private_key"] is None
    assert data.resolved(None).config["auth_method"] == "password"
    assert "password with spaces" not in repr(data)


def test_tls_failure_reports_safe_diagnostic(connector: SnowflakeConnector, monkeypatch) -> None:
    connect = MagicMock(side_effect=NonRetryableTlsError(msg="sensitive connection details", errno=250003))
    monkeypatch.setattr("odn.platform.connectors.snowflake.snowflake.connector.connect", connect)
    result = connector.test_connection()
    assert len(result) == 1 and not result[0].ok
    assert "TLS connection failed" in result[0].detail
    assert "250003" in result[0].detail
    assert "sensitive" not in result[0].detail


@pytest.mark.parametrize(("error_type", "expected"), [
    (ForbiddenError, "connection was forbidden"),
    (BadGatewayError, "gateway unavailable"),
    (OperationalError, "connection or authentication failed"),
])
def test_connection_failure_reports_safe_category(connector, monkeypatch, error_type, expected) -> None:
    connect = MagicMock(side_effect=error_type(msg="sensitive connection details", errno=250001))
    monkeypatch.setattr("odn.platform.connectors.snowflake.snowflake.connector.connect", connect)
    result = connector.test_connection()
    assert len(result) == 1 and not result[0].ok
    assert expected in result[0].detail
    assert "sensitive" not in result[0].detail


def test_snowflake_auth_switch_requires_new_secret(key: str) -> None:
    from uuid import uuid4

    profile = ConnectionProfile(id=uuid4(), tenant_id=uuid4(), name="sf", kind="snowflake",
                                config={"account": "org-account", "user": "svc", "warehouse": "WH",
                                        "database": "DB", "schema": "PUBLIC"},
                                secret_ref=None, allowed_objects=["DB.*.*"])
    data = PersonalConnectionInput(name="sf", kind="snowflake", auth_method="password",
                                   allowed_objects=["DB.*.*"])
    with pytest.raises(ValueError, match="password is required"):
        unsaved_connector(data, profile)
    with pytest.raises(ValueError, match="private keys cannot"):
        unsaved_connector(data.model_copy(update={"password": "x", "private_key": key}), profile)
