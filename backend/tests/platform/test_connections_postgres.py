"""Tests for PostgreSQL connection service integration."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from odn.platform.connections.service import (
    ConnectionInput,
    ConnectionProfile,
    CredentialError,
    build_connector,
    postgres_connector,
    resolve_postgres_credentials,
)
from odn.platform.policy.sql_policy import SqlPolicy


@pytest.fixture
def postgres_connection_input():
    """Create a Postgres connection input for testing."""
    return ConnectionInput(
        name="Production Postgres",
        kind="postgres",
        config={
            "host": "postgres.example.com",
            "port": 5432,
            "database": "production",
            "user": "app_user",
            "schema": "public",
            "ssl_mode": "require",
        },
        secret_ref="projects/my-project/secrets/odn-prod-postgres-pwd/versions/latest",
        allowed_objects=["public.users", "public.orders"],
    )


@pytest.fixture
def postgres_connection_profile(postgres_connection_input):
    """Create a Postgres connection profile for testing."""
    tenant_id = uuid4()
    data = postgres_connection_input.model_dump()
    return ConnectionProfile(
        **data,
        id=uuid4(),
        tenant_id=tenant_id,
    )


@pytest.fixture
def personal_postgres_profile():
    """Create a personal Postgres connection profile."""
    tenant_id = uuid4()
    user_id = uuid4()
    return ConnectionProfile(
        name="My Postgres",
        kind="postgres",
        config={
            "host": "localhost",
            "port": 5432,
            "database": "mydb",
            "user": "myuser",
        },
        secret_ref="(personal)",
        allowed_objects=["public.users"],
        id=uuid4(),
        tenant_id=tenant_id,
        owner_user_id=user_id,
        secret_ciphertext=b"encrypted_password",
    )


class TestPostgresConnectionInput:
    """Test Postgres connection input validation."""

    def test_valid_postgres_input(self, postgres_connection_input):
        """Valid Postgres input should pass validation."""
        assert postgres_connection_input.kind == "postgres"
        assert postgres_connection_input.config["host"] == "postgres.example.com"
        assert postgres_connection_input.config["database"] == "production"

    def test_missing_host(self, postgres_connection_input):
        """Missing host should fail validation."""
        config = postgres_connection_input.model_dump()
        config["config"].pop("host")
        with pytest.raises(ValueError, match="postgres config requires 'host'"):
            ConnectionInput(**config)

    def test_missing_database(self, postgres_connection_input):
        """Missing database should fail validation."""
        config = postgres_connection_input.model_dump()
        config["config"].pop("database")
        with pytest.raises(ValueError, match="postgres config requires 'database'"):
            ConnectionInput(**config)

    def test_missing_user(self, postgres_connection_input):
        """Missing user should fail validation."""
        config = postgres_connection_input.model_dump()
        config["config"].pop("user")
        with pytest.raises(ValueError, match="postgres config requires 'user'"):
            ConnectionInput(**config)

    def test_postgres_allowlist_patterns(self, postgres_connection_input):
        """Postgres allowlist patterns should be schema.table format."""
        # Valid: schema.table (not schema.table.*)
        assert postgres_connection_input.allowed_objects == ["public.users", "public.orders"]

    def test_postgres_kind_literal(self, postgres_connection_input):
        """Kind should be limited to postgres and bigquery."""
        config = postgres_connection_input.model_dump()
        config["kind"] = "snowflake"
        with pytest.raises(ValueError):
            ConnectionInput(**config)


class TestPostgresConnector:
    """Test PostgreSQL connector factory."""

    def test_postgres_connector_creation(self, postgres_connection_profile):
        """postgres_connector should create a PostgresConnector instance."""
        from odn.platform.connectors.postgres import PostgresConnector

        connector = postgres_connector(postgres_connection_profile.config, password="test123")
        assert isinstance(connector, PostgresConnector)
        assert connector.host == "postgres.example.com"
        assert connector.port == 5432
        assert connector.database == "production"
        assert connector.user == "app_user"
        assert connector.schema == "public"

    def test_postgres_connector_defaults(self):
        """postgres_connector should use sensible defaults."""
        from odn.platform.connectors.postgres import PostgresConnector

        cfg = {
            "host": "localhost",
            "database": "testdb",
            "user": "testuser",
        }
        connector = postgres_connector(cfg)
        assert isinstance(connector, PostgresConnector)
        assert connector.port == 5432  # Default
        assert connector.schema == "public"  # Default
        assert connector.ssl_mode == "prefer"  # Default

    def test_postgres_connector_no_password(self):
        """postgres_connector should work without password."""
        from odn.platform.connectors.postgres import PostgresConnector

        cfg = {
            "host": "localhost",
            "database": "testdb",
            "user": "testuser",
        }
        connector = postgres_connector(cfg, password=None)
        assert isinstance(connector, PostgresConnector)


class TestResolvePostgresCredentials:
    """Test Postgres password resolution."""

    def test_resolve_personal_connection_with_password(self, personal_postgres_profile):
        """Personal connection should decrypt stored password."""
        with patch("odn.platform.connections.service.crypto.decrypt") as mock_decrypt:
            mock_decrypt.return_value = b"secret_password"
            password = resolve_postgres_credentials(personal_postgres_profile)
            assert password == "secret_password"
            mock_decrypt.assert_called_once()

    def test_resolve_personal_connection_no_ciphertext(self):
        """Personal connection without stored ciphertext should return None."""
        tenant_id = uuid4()
        user_id = uuid4()
        profile = ConnectionProfile(
            name="No Password",
            kind="postgres",
            config={"host": "localhost", "database": "testdb", "user": "testuser"},
            secret_ref="(personal)",
            allowed_objects=["public.*"],
            id=uuid4(),
            tenant_id=tenant_id,
            owner_user_id=user_id,
            secret_ciphertext=None,
        )
        password = resolve_postgres_credentials(profile)
        assert password is None

    def test_resolve_tenant_connection_from_secret_manager(self):
        """Tenant connection should fetch password from Secret Manager."""
        tenant_id = uuid4()
        secret_ref = f"projects/my-project/secrets/odn-{tenant_id}-postgres-pwd/versions/latest"
        
        profile = ConnectionProfile(
            name="Production Postgres",
            kind="postgres",
            config={
                "host": "postgres.example.com",
                "port": 5432,
                "database": "production",
                "user": "app_user",
            },
            secret_ref=secret_ref,
            allowed_objects=["public.users", "public.orders"],
            id=uuid4(),
            tenant_id=tenant_id,
        )
        
        with patch("google.cloud.secretmanager.SecretManagerServiceClient") as MockSM:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.payload.data = b"secret_from_manager"
            mock_client.access_secret_version.return_value = mock_response
            MockSM.return_value = mock_client

            password = resolve_postgres_credentials(profile)
            assert password == "secret_from_manager"
            mock_client.access_secret_version.assert_called_once()

    def test_resolve_local_file_reference(self):
        """Local file secret refs should be read from filesystem."""
        tenant_id = uuid4()
        profile = ConnectionProfile(
            name="Local File",
            kind="postgres",
            config={"host": "localhost", "database": "testdb", "user": "testuser"},
            secret_ref="file:postgres_password.txt",
            allowed_objects=["public.users"],
            id=uuid4(),
            tenant_id=tenant_id,
        )
        
        with patch("odn.platform.connections.service.settings.env", "local"):
            with patch("odn.platform.connections.service.Path") as mock_path:
                mock_instance = MagicMock()
                mock_instance.is_relative_to.return_value = True
                mock_instance.is_file.return_value = True
                mock_path.return_value = mock_instance
                mock_path.resolve.return_value = mock_instance

                with patch("builtins.open", create=True) as mock_open:
                    mock_open.return_value.__enter__.return_value.read.return_value = "file_password\n"
                    password = resolve_postgres_credentials(profile)
                    assert password == "file_password"

    def test_resolve_failed_decryption(self, personal_postgres_profile):
        """Failed decryption should raise CredentialError."""
        from odn.platform import crypto

        with patch("odn.platform.connections.service.crypto.decrypt") as mock_decrypt:
            mock_decrypt.side_effect = crypto.SecretDecryptionError("key unavailable")
            with pytest.raises(CredentialError, match="key unavailable"):
                resolve_postgres_credentials(personal_postgres_profile)

    def test_resolve_invalid_secret_ref(self):
        """Invalid secret ref should raise CredentialError."""
        tenant_id = uuid4()
        invalid_profile = ConnectionProfile(
            name="Invalid",
            kind="postgres",
            config={"host": "localhost", "database": "testdb", "user": "testuser"},
            secret_ref="invalid_reference",
            allowed_objects=["public.users"],
            id=uuid4(),
            tenant_id=tenant_id,
        )
        with pytest.raises(CredentialError, match="not a permitted reference"):
            resolve_postgres_credentials(invalid_profile)


class TestBuildConnector:
    """Test connector factory dispatch."""

    def test_build_bigquery_connector(self):
        """build_connector should create BigQueryConnector for bigquery kind."""
        from odn.platform.connectors.bigquery import BigQueryConnector

        profile = ConnectionProfile(
            name="Production BQ",
            kind="bigquery",
            config={"project": "my-project", "default_dataset": "analytics"},
            secret_ref="file:bq_key.json",
            allowed_objects=["my-project.analytics.users"],
            id=uuid4(),
            tenant_id=uuid4(),
        )

        mock_creds = MagicMock()
        with patch("odn.platform.connections.service.resolve_credentials") as mock_resolve:
            with patch("odn.platform.connectors.bigquery.bigquery.Client"):
                with patch("odn.platform.connectors.bigquery.bigquery_storage.BigQueryReadClient"):
                    mock_resolve.return_value = mock_creds
                    connector = build_connector(profile)
                    assert isinstance(connector, BigQueryConnector)
                    mock_resolve.assert_called_once()

    def test_build_postgres_connector(self, postgres_connection_profile):
        """build_connector should create PostgresConnector for postgres kind."""
        from odn.platform.connectors.postgres import PostgresConnector

        with patch("odn.platform.connections.service.resolve_postgres_credentials") as mock_resolve:
            mock_resolve.return_value = "password123"
            connector = build_connector(postgres_connection_profile)
            assert isinstance(connector, PostgresConnector)
            mock_resolve.assert_called_once()


class TestPostgresDialectSupport:
    """Test that Postgres dialect is properly supported in SQL policy."""

    def test_postgres_dialect_in_policy(self, postgres_connection_profile):
        """SQL policy should handle postgres dialect."""
        policy = SqlPolicy(
            dialect="postgres",
            allowed_objects=frozenset(postgres_connection_profile.allowed_objects),
            default_namespace=("public",),
        )
        assert policy.dialect == "postgres"

    def test_postgres_allowlist_validation(self, postgres_connection_input):
        """Postgres patterns should be schema.table format."""
        # Valid patterns should pass
        for pattern in postgres_connection_input.allowed_objects:
            # This should not raise
            SqlPolicy.validate_pattern("postgres", pattern)

    def test_postgres_pattern_format_error(self):
        """Invalid Postgres patterns should fail."""
        # Postgres should require exactly 2 parts (schema.table)
        with pytest.raises(ValueError):
            # 3 parts would be invalid for Postgres (that's BigQuery format)
            SqlPolicy.validate_pattern("postgres", "project.schema.table")


class TestConnectionIntegration:
    """Integration tests for connection workflow."""

    def test_connection_input_to_profile_roundtrip(self, postgres_connection_input):
        """Connection input should be convertible to profile and back."""
        profile = ConnectionProfile(
            **postgres_connection_input.model_dump(),
            id=uuid4(),
            tenant_id=uuid4(),
        )
        
        assert profile.kind == postgres_connection_input.kind
        assert profile.name == postgres_connection_input.name
        assert profile.config == postgres_connection_input.config
        assert profile.allowed_objects == postgres_connection_input.allowed_objects

    def test_postgres_connector_full_pipeline(self, postgres_connection_profile):
        """Full pipeline: profile → connector → policy."""
        from odn.platform.connectors.postgres import PostgresConnector

        with patch("odn.platform.connections.service.resolve_postgres_credentials", return_value="pwd"):
            connector = build_connector(postgres_connection_profile)
            assert isinstance(connector, PostgresConnector)
            assert connector.dialect == "postgres"

            policy = SqlPolicy(
                dialect=connector.dialect,
                allowed_objects=frozenset(postgres_connection_profile.allowed_objects),
                default_namespace=connector.default_namespace,
            )
            assert policy.dialect == "postgres"
            assert len(policy.allowed_objects) == len(postgres_connection_profile.allowed_objects)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
