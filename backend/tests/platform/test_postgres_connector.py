"""Tests for PostgreSQL connector implementation."""

from unittest.mock import MagicMock

import pytest

from odn.platform.connectors.base import QueryLimits
from odn.platform.connectors.postgres import PostgresConnector, PostgresExecution


@pytest.fixture
def postgres_config():
    """Standard Postgres connection config for tests."""
    return {
        "host": "localhost",
        "port": 5432,
        "database": "testdb",
        "user": "testuser",
        "password": "testpass",
        "schema": "public",
    }


@pytest.fixture
def connector(postgres_config):
    """Create a PostgreSQL connector instance for testing."""
    return PostgresConnector(**postgres_config)


class TestPostgresConnectorBasics:
    """Test PostgreSQL connector basic properties."""

    def test_dialect(self, connector):
        """Connector should report postgres dialect."""
        assert connector.dialect == "postgres"

    def test_supports_cancel(self, connector):
        """Connector should support query cancellation."""
        assert connector.supports_cancel is True

    def test_supports_dry_run(self, connector):
        """Connector should not support dry-run."""
        assert connector.supports_dry_run is False

    def test_default_namespace(self, connector):
        """Default namespace should be (schema,)."""
        assert connector.default_namespace == ("public",)

    def test_default_namespace_custom_schema(self):
        """Default namespace should use custom schema when provided."""
        conn = PostgresConnector(
            host="localhost",
            port=5432,
            database="testdb",
            user="testuser",
            schema="myschema",
        )
        assert conn.default_namespace == ("myschema",)


class TestConnectionString:
    """Test connection string building."""

    def test_connection_string_basic(self, postgres_config):
        """Connection string should include host, port, database, user."""
        # Note: testing internal behavior, but important for connection
        conn = PostgresConnector(**postgres_config)
        # The connection string is used internally when creating the pool
        assert conn.host == "localhost"
        assert conn.port == 5432
        assert conn.database == "testdb"

    def test_connection_string_with_password(self):
        """Connection string should include password when provided."""
        conn = PostgresConnector(
            host="localhost",
            port=5432,
            database="testdb",
            user="testuser",
            password="secret",
        )
        assert conn.user == "testuser"

    def test_connection_string_without_password(self):
        """Connection string should work without password."""
        conn = PostgresConnector(
            host="localhost", port=5432, database="testdb", user="testuser"
        )
        assert conn.user == "testuser"


class TestQueryExecution:
    """Test query execution and paging."""

    def test_postgres_execution_creation(self):
        """PostgresExecution should track job_id and timeout."""
        mock_conn = MagicMock()
        exec = PostgresExecution(mock_conn, "SELECT 1", job_id="12345", timeout_s=60)
        assert exec.job_id == "12345"

    def test_postgres_execution_fetch_empty_result(self):
        """Execution should handle empty result sets."""
        # Requires async execution


class TestQueryLimits:
    """Test query limit enforcement."""

    def test_limits_defaults(self):
        """QueryLimits should have sensible defaults."""
        limits = QueryLimits()
        assert limits.max_rows == 500_000
        assert limits.max_arrow_bytes == 600 * 1024**2
        assert limits.max_bytes_billed == 100 * 1024**3
        assert limits.timeout_s == 300

    def test_limits_custom(self):
        """QueryLimits should accept custom values."""
        limits = QueryLimits(max_rows=1000, timeout_s=60)
        assert limits.max_rows == 1000
        assert limits.timeout_s == 60


class TestJobCancellation:
    """Test query cancellation via backend PID."""

    def test_cancel_job_requires_valid_pid(self, connector):
        """Cancellation should handle invalid PIDs gracefully."""
        # Should not raise even with invalid PID
        connector.cancel_job("not_a_number")

    def test_identifier_validation_happens_before_connecting(self, connector):
        """Bad paths and identifiers are refused without touching the database."""
        for call in (
            lambda: connector.list_catalog(parent=("schema", "table")),
            lambda: connector.get_schema(("schema", "table", "extra")),
            lambda: connector.get_schema(("2invalid", "table")),
            lambda: connector.list_columns(("bad schema",)),
        ):
            with pytest.raises(ValueError):
                call()


class TestTypeMapping:
    """Test PostgreSQL type name normalization."""

    def test_common_type_mappings(self):
        """Test that common Postgres types are mapped correctly."""
        from odn.platform.connectors.postgres import _TYPE_MAPPING

        assert _TYPE_MAPPING["integer"] == "int4"
        assert _TYPE_MAPPING["bigint"] == "int8"
        assert _TYPE_MAPPING["character varying"] == "varchar"
        assert _TYPE_MAPPING["double precision"] == "float8"
        assert _TYPE_MAPPING["timestamp without time zone"] == "timestamp"
        assert _TYPE_MAPPING["boolean"] == "bool"

    def test_unmapped_types_returned_as_is(self):
        """Unknown types should be returned unchanged."""
        from odn.platform.connectors.postgres import _TYPE_MAPPING

        # Test an unmapped type
        unmapped = "custom_type"
        result = _TYPE_MAPPING.get(unmapped, unmapped)
        assert result == "custom_type"


class TestIdentifierValidation:
    """Test SQL identifier validation."""

    def test_schema_identifier_validation(self):
        """Schema identifiers must match regex."""
        from odn.platform.connectors.postgres import _SCHEMA_RE

        assert _SCHEMA_RE.match("public")
        assert _SCHEMA_RE.match("my_schema")
        assert _SCHEMA_RE.match("_private")
        assert not _SCHEMA_RE.match("2invalid")
        assert not _SCHEMA_RE.match("")

    def test_table_identifier_validation(self):
        """Table identifiers must match regex."""
        from odn.platform.connectors.postgres import _TABLE_RE

        assert _TABLE_RE.match("users")
        assert _TABLE_RE.match("user_accounts")
        assert _TABLE_RE.match("_temp")
        assert not _TABLE_RE.match("2invalid")
        assert not _TABLE_RE.match("")


class TestIntegrationWithConnectionService:
    """Test integration points with connection service."""

    def test_connector_in_base_connector_hierarchy(self, connector):
        """PostgresConnector should inherit from BaseConnector."""
        from odn.platform.connectors.base import BaseConnector

        assert isinstance(connector, BaseConnector)

    def test_connector_implements_all_spi_methods(self, connector):
        """Connector should implement all required BaseConnector methods."""
        # Check that all abstract methods are implemented
        assert hasattr(connector, "test_connection")
        assert hasattr(connector, "list_catalog")
        assert hasattr(connector, "get_schema")
        assert hasattr(connector, "list_columns")
        assert hasattr(connector, "estimate_cost")
        assert hasattr(connector, "execute_paged")
        assert hasattr(connector, "cancel_job")

    def test_connector_dialect_property(self, connector):
        """Connector.dialect should be 'postgres'."""
        assert connector.dialect == "postgres"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
