"""Connection profiles and credential resolution.

Two kinds, both RLS-protected:
- tenant-shared connections (owner_user_id NULL), managed by data leads/operators, credentials in Secret Manager;
- personal connections (owner_user_id = a user), created by that user in Settings, visible only to them, with
  their uploaded service-account key encrypted at rest (secret_ciphertext).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from google.auth.credentials import Credentials
from google.oauth2 import service_account
from pydantic import BaseModel, Field, field_validator, model_validator

from odn.config import settings
from odn.db import tenant_transaction
from odn.platform import crypto
from odn.platform.connectors.base import BaseConnector
from odn.platform.policy.sql_policy import SqlPolicy

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,62}$")
_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_LOCATION_RE = re.compile(r"^[A-Za-z0-9-]{2,40}$")
_DATASET_RE = re.compile(r"^[A-Za-z0-9_]{1,1024}$")
MAX_SA_KEY_BYTES = 20_000


def _check_name(v: str) -> str:
    if not _NAME_RE.match(v):
        raise ValueError("name must be 1-63 letters, digits, spaces, '_', '.' or '-'")
    return v


class ConnectionInput(BaseModel):
    name: str
    kind: Literal["bigquery", "postgres"]
    config: dict[str, Any] = Field(default_factory=dict)
    secret_ref: str
    allowed_objects: list[str] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _check_name(v)

    @model_validator(mode="after")
    def _check(self) -> ConnectionInput:
        if self.kind == "bigquery" and not isinstance(self.config.get("project"), str):
            raise ValueError("bigquery config requires 'project'")
        if self.kind == "postgres":
            if not isinstance(self.config.get("host"), str):
                raise ValueError("postgres config requires 'host'")
            if "database" not in self.config or not isinstance(self.config.get("database"), str):
                raise ValueError("postgres config requires 'database'")
            if "user" not in self.config or not isinstance(self.config.get("user"), str):
                raise ValueError("postgres config requires 'user'")
        for pattern in self.allowed_objects:
            SqlPolicy.validate_pattern(self.kind, pattern)
        return self


class ConnectionProfile(ConnectionInput):
    id: UUID
    tenant_id: UUID
    secret_ref: str | None = None  # type: ignore[assignment]
    owner_user_id: UUID | None = None
    secret_ciphertext: bytes | None = Field(default=None, repr=False, exclude=True)

    @property
    def personal(self) -> bool:
        return self.owner_user_id is not None


def _secret_aad(tenant_id: UUID, owner_user_id: UUID, connection_id: UUID) -> str:
    return f"connection:{tenant_id}:{owner_user_id}:{connection_id}"


def parse_service_account_key(raw: str) -> dict[str, Any]:
    """Validate an uploaded service-account JSON key; returns the parsed key."""
    if len(raw.encode()) > MAX_SA_KEY_BYTES:
        raise ValueError("the key file is too large")
    try:
        info = json.loads(raw)
    except ValueError:
        raise ValueError("the key is not valid JSON") from None
    if not isinstance(info, dict) or info.get("type") != "service_account":
        raise ValueError("the key must be a Google service-account JSON key (\"type\": \"service_account\")")
    for field in ("client_email", "private_key", "token_uri"):
        if not isinstance(info.get(field), str) or not info[field]:
            raise ValueError(f"the key is missing {field!r}")
    try:
        service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    except (ValueError, KeyError):
        raise ValueError("the key could not be loaded (malformed private key?)") from None
    return info


def _qualify(entry: str, project: str) -> str:
    """Allowlist shorthand: "dataset" -> project.dataset.*, "dataset.table" -> project.dataset.table."""
    parts = entry.split(".")
    if len(parts) == 1:
        return f"{project}.{entry}.*"
    if len(parts) == 2:
        return f"{project}.{entry}"
    return entry


class PersonalConnectionInput(BaseModel):
    """A user's own BigQuery connection. On update, an omitted key keeps the stored one."""

    name: str
    kind: Literal["bigquery"] = "bigquery"
    project: str | None = None
    location: str | None = None
    billing_project: str | None = None
    default_dataset: str | None = None
    # Empty = every dataset the key can read in the project.
    allowed_objects: list[str] = Field(default_factory=list, max_length=500)
    credentials_json: str | None = Field(default=None, repr=False)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _check_name(v.strip())

    @field_validator("project", "location", "billing_project", "default_dataset", mode="before")
    @classmethod
    def _blank_is_none(cls, v: Any) -> Any:
        return v.strip() or None if isinstance(v, str) else v

    def resolved(self, key: dict[str, Any] | None, current: ConnectionProfile | None = None) -> ConnectionInput:
        """Validated tenant-agnostic settings (project defaults to the key's project)."""
        project = self.project or (key or {}).get("project_id") or (current.config.get("project") if current else None)
        if not isinstance(project, str) or not _PROJECT_RE.match(project):
            raise ValueError("a valid BigQuery project id is required")
        for value, regex, label in (
            (self.billing_project, _PROJECT_RE, "billing project"),
            (self.location, _LOCATION_RE, "location"),
            (self.default_dataset, _DATASET_RE, "default dataset"),
        ):
            if value is not None and not regex.match(value):
                raise ValueError(f"invalid {label}")
        config: dict[str, Any] = {"project": project}
        for k in ("location", "billing_project", "default_dataset"):
            if getattr(self, k):
                config[k] = getattr(self, k)
        allowed = [_qualify(a.strip(), project) for a in self.allowed_objects if a.strip()] or [f"{project}.*.*"]
        return ConnectionInput(
            name=self.name, kind=self.kind, config=config, secret_ref="(personal)", allowed_objects=allowed
        )


class CredentialError(Exception):
    pass


def is_tenant_secret_ref(tenant_id: UUID, secret_ref: str) -> bool:
    """Secret Manager refs must name a secret prefixed with the owning tenant's id."""
    return bool(
        re.fullmatch(rf"projects/[a-z0-9-]+/secrets/odn-{tenant_id}-[A-Za-z0-9_-]+/versions/(latest|\d+)", secret_ref)
    )


def resolve_credentials(profile: ConnectionProfile) -> Credentials:
    """Every connection must carry its own credentials; the platform identity is never used as a fallback."""
    if profile.personal:
        if profile.secret_ciphertext is None or profile.owner_user_id is None:
            raise CredentialError("this connection has no stored key")
        try:
            raw = crypto.decrypt(
                profile.secret_ciphertext, _secret_aad(profile.tenant_id, profile.owner_user_id, profile.id)
            )
        except (crypto.SecretsUnavailable, crypto.SecretDecryptionError) as exc:
            raise CredentialError(str(exc)) from None
        return service_account.Credentials.from_service_account_info(json.loads(raw), scopes=_SCOPES)

    tenant_id, secret_ref = profile.tenant_id, profile.secret_ref or ""
    if is_tenant_secret_ref(tenant_id, secret_ref):
        from google.cloud import secretmanager

        payload = secretmanager.SecretManagerServiceClient().access_secret_version(name=secret_ref).payload.data
        return service_account.Credentials.from_service_account_info(json.loads(payload), scopes=_SCOPES)

    if secret_ref.startswith("file:"):
        if settings.env != "local":
            raise CredentialError("file: secret refs are only allowed in local development")
        keys_dir = Path(settings.keys_dir).resolve()
        path = (keys_dir / secret_ref.removeprefix("file:")).resolve()
        if not path.is_relative_to(keys_dir) or not path.is_file():
            raise CredentialError("credential file not found")
        return service_account.Credentials.from_service_account_file(str(path), scopes=_SCOPES)

    raise CredentialError("secret_ref is not a permitted reference for this tenant")


def resolve_postgres_credentials(profile: ConnectionProfile) -> str | None:
    """Resolve Postgres database password from encrypted storage.
    
    Returns:
        Password string or None if not set (will use connection without password).
        
    Raises:
        CredentialError: If decryption fails
    """
    if profile.personal:
        if profile.secret_ciphertext is None or profile.owner_user_id is None:
            # No password stored; Postgres will attempt password-less auth
            return None
        try:
            password = crypto.decrypt(
                profile.secret_ciphertext, _secret_aad(profile.tenant_id, profile.owner_user_id, profile.id)
            ).decode("utf-8")
            return password
        except (crypto.SecretsUnavailable, crypto.SecretDecryptionError) as exc:
            raise CredentialError(str(exc)) from None

    tenant_id, secret_ref = profile.tenant_id, profile.secret_ref or ""
    if is_tenant_secret_ref(tenant_id, secret_ref):
        from google.cloud import secretmanager

        payload = secretmanager.SecretManagerServiceClient().access_secret_version(name=secret_ref).payload.data
        return payload.decode("utf-8")

    if secret_ref.startswith("file:"):
        if settings.env != "local":
            raise CredentialError("file: secret refs are only allowed in local development")
        keys_dir = Path(settings.keys_dir).resolve()
        path = (keys_dir / secret_ref.removeprefix("file:")).resolve()
        if not path.is_relative_to(keys_dir) or not path.is_file():
            raise CredentialError("credential file not found")
        with open(path) as f:
            return f.read().strip()

    raise CredentialError("secret_ref is not a permitted reference for this tenant")


def build_connector(profile: ConnectionProfile) -> BaseConnector:
    if profile.kind == "postgres":
        return postgres_connector(profile.config, resolve_postgres_credentials(profile))
    else:
        return bigquery_connector(profile.config, resolve_credentials(profile))


def bigquery_connector(cfg: dict[str, Any], credentials: Credentials) -> BaseConnector:
    from odn.platform.connectors.bigquery import BigQueryConnector

    return BigQueryConnector(
        project=cfg["project"],
        credentials=credentials,
        location=cfg.get("location"),
        billing_project=cfg.get("billing_project"),
        default_dataset=cfg.get("default_dataset"),
    )


def postgres_connector(cfg: dict[str, Any], password: str | None = None) -> BaseConnector:
    """Create a PostgreSQL connector from configuration.
    
    Args:
        cfg: Connection config with host, port, database, user, schema, ssl_mode, ssh_tunnel_config
        password: Database password (optional)
    
    Returns:
        PostgresConnector instance with optional SSH tunnel support
    """
    from odn.platform.connectors.postgres import PostgresConnector
    from odn.platform.connectors.ssh_tunnel import SSHTunnelConfig, SSHTunnelManager

    host = cfg["host"]
    port = cfg.get("port", 5432)
    database = cfg["database"]
    user = cfg["user"]
    schema = cfg.get("schema", "public")
    ssl_mode = cfg.get("ssl_mode", "prefer")

    # Create the PostgreSQL connector
    connector = PostgresConnector(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        schema=schema,
        ssl_mode=ssl_mode,
    )

    # Note: SSH tunnel is managed separately via SSHTunnelManager
    # and would be opened before connection attempts in the API layer
    return connector


def build_policy(profile: ConnectionProfile, connector: BaseConnector) -> SqlPolicy:
    return SqlPolicy(
        dialect=connector.dialect,
        allowed_objects=frozenset(profile.allowed_objects),
        default_namespace=connector.default_namespace,
    )


_COLUMNS = "id, tenant_id, name, kind, config, secret_ref, allowed_objects, owner_user_id, secret_ciphertext"


def list_connections(tenant_id: UUID, user_id: UUID) -> list[ConnectionProfile]:
    """Tenant-shared connections plus the user's own (RLS hides other users' personal connections)."""
    with tenant_transaction(tenant_id, user_id) as conn:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM connections ORDER BY owner_user_id IS NULL DESC, lower(name)"
        ).fetchall()
    return [ConnectionProfile.model_validate(r) for r in rows]


def get_connection(tenant_id: UUID, user_id: UUID, connection_id: UUID) -> ConnectionProfile | None:
    with tenant_transaction(tenant_id, user_id) as conn:
        row = conn.execute(f"SELECT {_COLUMNS} FROM connections WHERE id = %s", (connection_id,)).fetchone()
    return ConnectionProfile.model_validate(row) if row else None


class DuplicateName(Exception):
    pass


def save_personal_connection(
    tenant_id: UUID, user_id: UUID, data: PersonalConnectionInput, connection_id: UUID | None = None
) -> ConnectionProfile:
    """Create (connection_id None) or update one of the user's own connections."""
    import psycopg
    from psycopg.types.json import Jsonb

    current = get_connection(tenant_id, user_id, connection_id) if connection_id else None
    if connection_id and (current is None or current.owner_user_id != user_id):
        raise LookupError("connection not found")
    key = parse_service_account_key(data.credentials_json) if data.credentials_json else None
    if key is None and current is None:
        raise ValueError("a service-account JSON key is required")
    spec = data.resolved(key, current)
    cid = connection_id or uuid4()
    ciphertext = (
        crypto.encrypt(json.dumps(key).encode(), _secret_aad(tenant_id, user_id, cid)) if key is not None else None
    )
    try:
        with tenant_transaction(tenant_id, user_id) as conn:
            if current is None:
                row = conn.execute(
                    f"""
                    INSERT INTO connections (id, tenant_id, owner_user_id, name, kind, config, allowed_objects,
                                             secret_ciphertext, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING {_COLUMNS}
                    """,
                    (cid, tenant_id, user_id, spec.name, spec.kind, Jsonb(spec.config), spec.allowed_objects,
                     ciphertext, user_id),
                ).fetchone()
            else:
                row = conn.execute(
                    f"""
                    UPDATE connections
                       SET name = %s, config = %s, allowed_objects = %s,
                           secret_ciphertext = coalesce(%s, secret_ciphertext), updated_at = now()
                     WHERE id = %s AND owner_user_id = %s
                    RETURNING {_COLUMNS}
                    """,
                    (spec.name, Jsonb(spec.config), spec.allowed_objects, ciphertext, cid, user_id),
                ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise DuplicateName(spec.name) from None
    if row is None:
        raise LookupError("connection not found")
    return ConnectionProfile.model_validate(row)


def delete_personal_connection(tenant_id: UUID, user_id: UUID, connection_id: UUID) -> bool:
    with tenant_transaction(tenant_id, user_id) as conn:
        cur = conn.execute(
            "DELETE FROM connections WHERE id = %s AND owner_user_id = %s", (connection_id, user_id)
        )
    return cur.rowcount > 0


def unsaved_connector(data: PersonalConnectionInput, current: ConnectionProfile | None) -> BaseConnector:
    """Connector for 'Test connection' before saving: the submitted key, or the stored one when editing."""
    key = parse_service_account_key(data.credentials_json) if data.credentials_json else None
    spec = data.resolved(key, current)
    if key is not None:
        credentials: Credentials = service_account.Credentials.from_service_account_info(key, scopes=_SCOPES)
    elif current is not None:
        credentials = resolve_credentials(current)
    else:
        raise ValueError("a service-account JSON key is required")
    return bigquery_connector(spec.config, credentials)


def create_connection(tenant_id: UUID, user_id: UUID, data: ConnectionInput) -> ConnectionProfile:
    from psycopg.types.json import Jsonb

    with tenant_transaction(tenant_id) as conn:
        row = conn.execute(
            f"""
            INSERT INTO connections (tenant_id, name, kind, config, secret_ref, allowed_objects, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING {_COLUMNS}
            """,
            (tenant_id, data.name, data.kind, Jsonb(data.config), data.secret_ref, data.allowed_objects, user_id),
        ).fetchone()
    return ConnectionProfile.model_validate(row)
