"""Tests for the /v1/me/connections API endpoints with Postgres support."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from odn.platform.connections.service import PersonalConnectionInput, resolve_postgres_credentials
from odn.platform.connectors.base import ConnectionTestStep


@pytest.fixture
def postgres_input_data() -> dict:
    """Minimal valid Postgres connection input."""
    return {
        "name": "test-postgres",
        "kind": "postgres",
        "host": "localhost",
        "port": 5432,
        "database": "testdb",
        "user": "testuser",
        "password": "testpass",
        "ssl_mode": "prefer",
        "schema": "public",
        "allowed_objects": ["public.users", "public.orders"],
    }


@pytest.fixture
def bigquery_input_data() -> dict:
    """Minimal valid BigQuery connection input (for comparison)."""
    return {
        "name": "test-bigquery",
        "kind": "bigquery",
        "project": "my-project",
        "credentials_json": json.dumps({
            "type": "service_account",
            "project_id": "my-project",
            "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDU5Z8kZ1Z8kZ1Z\n8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1\nZ8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ\n1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ\n1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ1Z8kZ\nqQIDAQABAoIBACZk5Q1BG/W5kH5H6Q7c\n-----END PRIVATE KEY-----\n",
            "client_email": "test@my-project.iam.gserviceaccount.com",
            "token_uri": "https://oauth2.googleapis.com/token",
        }),
    }


class TestPostgresConnectionInput:
    """Test PersonalConnectionInput validation for Postgres."""

    def test_valid_postgres_connection_input(self, postgres_input_data):
        """Test creating a valid Postgres connection input."""
        data = PersonalConnectionInput(**postgres_input_data)
        assert data.name == "test-postgres"
        assert data.kind == "postgres"
        assert data.host == "localhost"
        assert data.port == 5432
        assert data.database == "testdb"
        assert data.user == "testuser"
        assert data.password == "testpass"
        assert data.ssl_mode == "prefer"
        assert data.schema == "public"
        assert len(data.allowed_objects) == 2

    def test_postgres_port_validation(self, postgres_input_data):
        """Test port number validation."""
        # Valid ports
        for port in [1, 5432, 65535]:
            postgres_input_data["port"] = port
            data = PersonalConnectionInput(**postgres_input_data)
            assert data.port == port

        # Invalid ports
        for port in [0, 65536]:
            postgres_input_data["port"] = port
            with pytest.raises(ValueError, match="port must be"):
                PersonalConnectionInput(**postgres_input_data)

    def test_postgres_ssl_mode_validation(self, postgres_input_data):
        """Test SSL mode validation."""
        # Valid modes
        for mode in ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]:
            postgres_input_data["ssl_mode"] = mode
            data = PersonalConnectionInput(**postgres_input_data)
            assert data.ssl_mode == mode

        # Invalid mode
        postgres_input_data["ssl_mode"] = "invalid_mode"
        with pytest.raises(ValueError, match="ssl_mode must be"):
            PersonalConnectionInput(**postgres_input_data)

    def test_postgres_required_fields(self, postgres_input_data):
        """Test that host, database, user are required for Postgres."""
        for field in ["host", "database", "user"]:
            data = postgres_input_data.copy()
            data[field] = None
            input_obj = PersonalConnectionInput(**data)
            # Validation should happen in resolved()
            with pytest.raises(ValueError, match=f"{field} is required"):
                input_obj.resolved(None)

    def test_postgres_optional_fields(self, postgres_input_data):
        """Test that password, ssl_mode, schema are optional for Postgres."""
        data = postgres_input_data.copy()
        data["password"] = None
        data["ssl_mode"] = None
        data["schema"] = None
        input_obj = PersonalConnectionInput(**data)
        # Should resolve successfully with defaults
        resolved = input_obj.resolved(None)
        assert resolved.kind == "postgres"
        assert resolved.config["ssl_mode"] == "prefer"  # default
        assert resolved.config["schema"] == "public"  # default

    def test_postgres_kind_field(self, postgres_input_data):
        """Test that kind field is correctly set to postgres."""
        data = PersonalConnectionInput(**postgres_input_data)
        assert data.kind == "postgres"


class TestBigQueryConnectionInput:
    """Test PersonalConnectionInput validation for BigQuery (backward compatibility)."""

    def test_valid_bigquery_connection_input(self, bigquery_input_data):
        """Test creating a valid BigQuery connection input."""
        data = PersonalConnectionInput(**bigquery_input_data)
        assert data.name == "test-bigquery"
        assert data.kind == "bigquery"
        assert data.project == "my-project"
        assert data.credentials_json is not None

    def test_bigquery_default_kind(self):
        """Test that kind defaults to bigquery."""
        data = PersonalConnectionInput(
            name="default-kind",
            project="my-project",
            credentials_json='{"type": "service_account", "project_id": "my-project", "private_key": "...", "client_email": "test@test.iam.gserviceaccount.com", "token_uri": "https://oauth2.googleapis.com/token"}',
        )
        assert data.kind == "bigquery"

    def test_bigquery_still_works(self, bigquery_input_data):
        """Test that BigQuery connections still work as before."""
        data = PersonalConnectionInput(**bigquery_input_data)
        resolved = data.resolved(json.loads(bigquery_input_data["credentials_json"]))
        assert resolved.kind == "bigquery"
        assert resolved.config["project"] == "my-project"


class TestPostgresConnectionResolution:
    """Test PersonalConnectionInput.resolved() for Postgres."""

    def test_postgres_resolved_config(self, postgres_input_data):
        """Test that resolved() produces correct Postgres config."""
        input_obj = PersonalConnectionInput(**postgres_input_data)
        resolved = input_obj.resolved(None)
        
        assert resolved.kind == "postgres"
        assert resolved.config["host"] == "localhost"
        assert resolved.config["port"] == 5432
        assert resolved.config["database"] == "testdb"
        assert resolved.config["user"] == "testuser"
        assert resolved.config["schema"] == "public"
        assert resolved.config["ssl_mode"] == "prefer"

    def test_postgres_allowlist_format(self, postgres_input_data):
        """Test Postgres allowlist pattern format (schema.table)."""
        input_obj = PersonalConnectionInput(**postgres_input_data)
        resolved = input_obj.resolved(None)
        
        # Allowlist should be preserved as-is for Postgres (schema.table)
        assert "public.users" in resolved.allowed_objects
        assert "public.orders" in resolved.allowed_objects

    def test_postgres_empty_allowlist_defaults(self, postgres_input_data):
        """Test Postgres with empty allowlist gets wildcard."""
        postgres_input_data["allowed_objects"] = []
        input_obj = PersonalConnectionInput(**postgres_input_data)
        resolved = input_obj.resolved(None)
        
        # Empty allowlist should become *.*
        assert "*.*" in resolved.allowed_objects

    def test_postgres_allowlist_normalization(self, postgres_input_data):
        """Test that allowlist entries are stripped of whitespace."""
        postgres_input_data["allowed_objects"] = ["  public.users  ", " public.orders ", ""]
        input_obj = PersonalConnectionInput(**postgres_input_data)
        resolved = input_obj.resolved(None)
        
        assert "public.users" in resolved.allowed_objects
        assert "public.orders" in resolved.allowed_objects
        assert len(resolved.allowed_objects) == 2  # Empty string filtered out

    def test_postgres_port_defaults(self, postgres_input_data):
        """Test Postgres port defaults to 5432."""
        postgres_input_data["port"] = None
        input_obj = PersonalConnectionInput(**postgres_input_data)
        resolved = input_obj.resolved(None)
        
        assert resolved.config["port"] == 5432

    def test_postgres_ssl_mode_defaults(self, postgres_input_data):
        """Test Postgres ssl_mode defaults to prefer."""
        postgres_input_data["ssl_mode"] = None
        input_obj = PersonalConnectionInput(**postgres_input_data)
        resolved = input_obj.resolved(None)
        
        assert resolved.config["ssl_mode"] == "prefer"

    def test_postgres_schema_defaults(self, postgres_input_data):
        """Test Postgres schema defaults to public."""
        postgres_input_data["schema"] = None
        input_obj = PersonalConnectionInput(**postgres_input_data)
        resolved = input_obj.resolved(None)
        
        assert resolved.config["schema"] == "public"


class TestConnectionKindMixing:
    """Test that BigQuery and Postgres connections coexist."""

    def test_kind_field_is_required_in_input(self):
        """Test that kind can be explicitly set."""
        # BigQuery
        bq = PersonalConnectionInput(
            name="bq",
            kind="bigquery",
            project="p",
            credentials_json='{"type": "service_account", "project_id": "p", "private_key": "...", "client_email": "t@t.iam.gserviceaccount.com", "token_uri": "https://oauth2.googleapis.com/token"}',
        )
        assert bq.kind == "bigquery"

        # Postgres
        pg = PersonalConnectionInput(
            name="pg",
            kind="postgres",
            host="localhost",
            database="db",
            user="user",
        )
        assert pg.kind == "postgres"

    def test_unsupported_kind_rejected(self):
        """Test that unsupported kind values are rejected."""
        with pytest.raises(ValueError):
            PersonalConnectionInput(
                name="unsupported",
                kind="snowflake",  # Not yet supported
                project="p",
            )


class TestPostgresPasswordHandling:
    """Test password encryption and storage for Postgres."""

    def test_password_field_is_secret(self, postgres_input_data):
        """Test that password field is marked as sensitive."""
        input_obj = PersonalConnectionInput(**postgres_input_data)
        
        # Password should not appear in repr
        repr_str = repr(input_obj)
        assert "testpass" not in repr_str
        # But the field should be accessible
        assert input_obj.password == "testpass"

    def test_password_optional(self, postgres_input_data):
        """Test that password is optional for Postgres."""
        postgres_input_data["password"] = None
        input_obj = PersonalConnectionInput(**postgres_input_data)
        
        assert input_obj.password is None
        resolved = input_obj.resolved(None)
        assert resolved.kind == "postgres"


class TestPostgresConnectionUpdate:
    """Test updating Postgres connections (password handling)."""

    def test_update_preserves_unsubmitted_password(self):
        """When updating, omitting password should keep the stored one."""
        # Simulate a stored connection
        from odn.platform.connections.service import ConnectionProfile
        from uuid import uuid4

        stored = ConnectionProfile(
            id=uuid4(),
            tenant_id=uuid4(),
            name="stored-postgres",
            kind="postgres",
            config={"host": "localhost", "port": 5432, "database": "db", "user": "user", "schema": "public", "ssl_mode": "prefer"},
            secret_ref="(personal)",
            allowed_objects=["public.*"],
            owner_user_id=uuid4(),
            secret_ciphertext=b"encrypted_password",
        )

        # Update without password
        update_data = PersonalConnectionInput(
            name="stored-postgres",
            kind="postgres",
            host="localhost",
            port=5432,
            database="db",
            user="user",
            password=None,
        )

        # When resolving with current, password field should be None in the resolved config
        # but encryption logic should preserve the stored ciphertext
        resolved = update_data.resolved(None, stored)
        assert resolved.kind == "postgres"
