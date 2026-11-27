"""Connection profiles and credential resolution.

Two kinds, both RLS-protected:
- tenant-shared connections (owner_user_id NULL), managed by data leads/operators, credentials in Secret Manager;
- personal connections (owner_user_id = a user), created by that user in Settings, visible only to them, with
  their uploaded service-account key encrypted at rest (secret_ciphertext).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
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
    kind: Literal["bigquery", "postgres", "snowflake", "mysql", "oracle"]
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
        if self.kind == "snowflake":
            _validate_snowflake_config(self.config)
        if self.kind == "mysql":
            _validate_mysql_config(self.config)
        if self.kind == "oracle":
            _validate_oracle_config(self.config)
        for pattern in self.allowed_objects:
            SqlPolicy.validate_pattern(self.kind, pattern)
        return self


class ConnectionProfile(ConnectionInput):
    id: UUID
    tenant_id: UUID | None
    secret_ref: str | None = None  # type: ignore[assignment]
    owner_user_id: UUID | None = None
    secret_ciphertext: bytes | None = Field(default=None, repr=False, exclude=True)
    ssh_key_ciphertext: bytes | None = Field(default=None, repr=False, exclude=True)

    @property
    def personal(self) -> bool:
        return self.owner_user_id is not None


def _secret_aad(tenant_id: UUID | None, owner_user_id: UUID | None, connection_id: UUID) -> str:
    # Shared (organization) connections bind their secret to the tenant only.
    return f"connection:{tenant_id or 'platform'}:{owner_user_id or 'shared'}:{connection_id}"


def _ssh_key_aad(tenant_id: UUID | None, owner_user_id: UUID | None, connection_id: UUID) -> str:
    return f"ssh-key:{tenant_id or 'platform'}:{owner_user_id or 'shared'}:{connection_id}"


def resolve_ssh_key(profile: ConnectionProfile) -> str | None:
    if profile.ssh_key_ciphertext is None:
        return None
    try:
        return crypto.decrypt(
            profile.ssh_key_ciphertext, _ssh_key_aad(profile.tenant_id, profile.owner_user_id, profile.id)
        ).decode()
    except (crypto.SecretsUnavailable, crypto.SecretDecryptionError) as exc:
        raise CredentialError(str(exc)) from None


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


def parse_snowflake_key(raw: str, passphrase: str | None = None) -> bytes:
    if len(raw.encode()) > 20_000:
        raise ValueError("the Snowflake private key is too large")
    try:
        key = serialization.load_pem_private_key(raw.encode(), password=passphrase.encode() if passphrase else None)
    except (ValueError, TypeError):
        raise ValueError("invalid private key or passphrase") from None
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise ValueError("Snowflake requires an RSA private key of at least 2048 bits")
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def _validate_snowflake_config(config: dict[str, Any]) -> None:
    if config.get("auth_method", "key_pair") not in ("key_pair", "password"):
        raise ValueError("invalid Snowflake authentication method")
    account = config.get("account")
    if not isinstance(account, str) or not re.fullmatch(r"[A-Za-z0-9_-]+(?:[.-][A-Za-z0-9_-]+)*", account):
        raise ValueError("a valid Snowflake account identifier is required")
    user = config.get("user")
    if not isinstance(user, str) or not user.strip() or len(user) > 255 or any(ord(c) < 32 for c in user):
        raise ValueError("a valid Snowflake login name is required")
    for name in ("warehouse", "database", "schema", "role"):
        value = config.get(name)
        if name == "role" and value is None:
            continue
        if not isinstance(value, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_$]{0,254}", value):
            raise ValueError(f"Snowflake {name} must be an uppercase unquoted identifier")


def _validate_mysql_config(config: dict[str, Any]) -> None:
    from odn.platform.connectors.mysql import tls_context
    from odn.platform.net import mysql_host
    from odn.platform.policy.sql_policy import _MYSQL_IDENTIFIER, _MYSQL_SYSTEM_DATABASES

    if set(config) - {"host", "port", "database", "user", "ssl_ca"}:
        raise ValueError("unsupported MySQL configuration fields; credentials must be supplied separately")
    host = config.get("host")
    if not isinstance(host, str) or mysql_host(host) != host:
        raise ValueError("a normalized MySQL hostname or IP address is required")
    port = config.get("port", 3306)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("MySQL port must be 1-65535")
    database = config.get("database")
    if (
        not isinstance(database, str) or not _MYSQL_IDENTIFIER.fullmatch(database)
        or database.lower() in _MYSQL_SYSTEM_DATABASES
    ):
        raise ValueError("MySQL default database must be a simple non-system identifier")
    user = config.get("user")
    if not isinstance(user, str) or not user or len(user) > 32 or any(ord(c) < 32 for c in user):
        raise ValueError("a valid MySQL username (at most 32 characters) is required")
    ca = config.get("ssl_ca")
    if ca is not None and (not isinstance(ca, str) or len(ca.encode()) > 100_000):
        raise ValueError("MySQL CA must be a PEM certificate bundle of at most 100,000 bytes")
    tls_context(ca)


def _validate_oracle_config(config: dict[str, Any]) -> None:
    from odn.platform.connectors.oracle import tls_context
    from odn.platform.egress import canonical_host
    from odn.platform.policy.sql_policy import _ORACLE_IDENTIFIER, _ORACLE_SYSTEM_SCHEMAS

    if set(config) - {"host", "port", "service_name", "schema", "user", "ssl_ca"}:
        raise ValueError("unsupported Oracle configuration fields; credentials must be supplied separately")
    host = config.get("host")
    if not isinstance(host, str) or canonical_host(host) != host:
        raise ValueError("a normalized Oracle hostname or IP address is required")
    port = config.get("port", 2484)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Oracle listener port must be 1-65535")
    service = config.get("service_name")
    if not isinstance(service, str) or not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", service) or len(service) > 255:
        raise ValueError("a simple Oracle service name is required; descriptors and SIDs are unsupported")
    for key in ("schema", "user"):
        value = config.get(key)
        if (
            not isinstance(value, str) or not _ORACLE_IDENTIFIER.fullmatch(value)
            or value.upper() in _ORACLE_SYSTEM_SCHEMAS
        ):
            raise ValueError(f"Oracle {key} must be a simple non-administrative identifier")
    ca = config.get("ssl_ca")
    if ca is not None and (not isinstance(ca, str) or len(ca.encode()) > 100_000):
        raise ValueError("Oracle CA must be a PEM certificate bundle of at most 100,000 bytes")
    tls_context(ca)


def _qualify(entry: str, project: str) -> str:
    """Allowlist shorthand: "dataset" -> project.dataset.*, "dataset.table" -> project.dataset.table."""
    parts = entry.split(".")
    if len(parts) == 1:
        return f"{project}.{entry}.*"
    if len(parts) == 2:
        return f"{project}.{entry}"
    return entry


class SSHTunnelInput(BaseModel):
    """Bastion settings. The private key is write-only: stored encrypted, never in config or API responses."""

    bastion_host: str = Field(min_length=1, max_length=253)
    bastion_port: int = Field(default=22, ge=1, le=65535)
    bastion_user: str = Field(min_length=1, max_length=64)
    known_hosts_fingerprint: str = Field(min_length=1, max_length=2000)
    ssh_private_key: str | None = Field(default=None, repr=False, max_length=16_384)

    @field_validator("bastion_host")
    @classmethod
    def _host(cls, v: str) -> str:
        v = v.strip()
        if not re.fullmatch(r"[A-Za-z0-9.-]+|\[?[0-9A-Fa-f:.]+\]?", v):
            raise ValueError("invalid bastion host")
        return v

    @field_validator("known_hosts_fingerprint")
    @classmethod
    def _fingerprint(cls, v: str) -> str:
        from odn.platform.connectors.ssh_tunnel import normalize_fingerprint

        return normalize_fingerprint(v)

    @field_validator("ssh_private_key")
    @classmethod
    def _key(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        from odn.platform.connectors.ssh_tunnel import check_private_key

        check_private_key(v)
        return v.strip() + "\n"

    def public(self) -> dict[str, Any]:
        return self.model_dump(exclude={"ssh_private_key"})


class PersonalConnectionInput(BaseModel):
    """A user's data connection. On update, omitted credentials keep the stored secret for the same kind."""

    name: str
    kind: Literal["bigquery", "postgres", "snowflake", "mysql", "oracle"] = "bigquery"
    # BigQuery fields
    project: str | None = None
    location: str | None = None
    billing_project: str | None = None
    default_dataset: str | None = None
    credentials_json: str | None = Field(default=None, repr=False)
    # Postgres fields
    host: str | None = None
    port: int | None = None
    database: str | None = None
    user: str | None = None
    password: str | None = Field(default=None, repr=False)
    ssl_mode: str | None = None
    schema: str | None = None
    ssh_tunnel: SSHTunnelInput | None = None
    # Snowflake fields
    account: str | None = None
    warehouse: str | None = None
    role: str | None = None
    auth_method: Literal["key_pair", "password"] | None = None
    private_key: str | None = Field(default=None, repr=False)
    private_key_passphrase: str | None = Field(default=None, repr=False)
    # MySQL: public PEM CA certificates, never client private keys or filesystem paths.
    ssl_ca: str | None = None
    service_name: str | None = None
    # All providers
    allowed_objects: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _check_name(v.strip())

    @field_validator("project", "location", "billing_project", "default_dataset", "host", "database", "user", mode="before")
    @classmethod
    def _blank_is_none(cls, v: Any) -> Any:
        return v.strip() or None if isinstance(v, str) else v

    @field_validator("port")
    @classmethod
    def _port(cls, v: int | None) -> int | None:
        if v is not None:
            if not (1 <= v <= 65535):
                raise ValueError("port must be 1-65535")
        return v

    @field_validator("ssl_mode")
    @classmethod
    def _ssl_mode(cls, v: str | None) -> str | None:
        if v and v not in ("disable", "allow", "prefer", "require", "verify-ca", "verify-full"):
            raise ValueError("ssl_mode must be one of: disable, allow, prefer, require, verify-ca, verify-full")
        return v

    def resolved(self, key: dict[str, Any] | None, current: ConnectionProfile | None = None) -> ConnectionInput:
        """Validated tenant-agnostic settings."""
        if self.kind == "bigquery":
            return self._resolved_bigquery(key, current)
        elif self.kind == "postgres":
            return self._resolved_postgres(current)
        elif self.kind == "oracle":
            from odn.platform.egress import canonical_host

            if self.ssh_tunnel is not None or self.ssl_mode not in (None, "verify-full"):
                raise ValueError("Oracle requires verified TCPS through operator-managed egress")
            config = {
                k: getattr(self, k) or (current.config.get(k) if current else None)
                for k in ("host", "service_name", "schema", "user")
            }
            if not isinstance(config["host"], str) or not config["host"]:
                raise ValueError("Oracle host is required")
            config["host"] = canonical_host(config["host"])
            config["port"] = self.port or (current.config.get("port") if current else None) or 2484
            config["ssl_ca"] = (
                self.ssl_ca if "ssl_ca" in self.model_fields_set else current.config.get("ssl_ca") if current else None
            ) or None
            allowed = [a.strip() for a in self.allowed_objects if a.strip()]
            if not allowed:
                raise ValueError("at least one Oracle allowlist entry is required")
            return ConnectionInput(
                name=self.name, kind=self.kind, config=config, secret_ref="(personal)", allowed_objects=allowed
            )
        elif self.kind == "mysql":
            from odn.platform.net import mysql_host

            if self.ssh_tunnel is not None or self.ssl_mode not in (None, "verify-full"):
                raise ValueError("MySQL requires direct verified TLS; tunnels and insecure TLS modes are unsupported")
            config = {
                k: getattr(self, k) or (current.config.get(k) if current else None)
                for k in ("host", "database", "user")
            }
            if not isinstance(config["host"], str) or not config["host"]:
                raise ValueError("MySQL host is required")
            config["host"] = mysql_host(config["host"])
            config["port"] = self.port or (current.config.get("port") if current else None) or 3306
            config["ssl_ca"] = (
                self.ssl_ca if "ssl_ca" in self.model_fields_set else current.config.get("ssl_ca") if current else None
            ) or None
            allowed = [a.strip() for a in self.allowed_objects if a.strip()]
            if not allowed:
                raise ValueError("at least one MySQL allowlist entry is required")
            return ConnectionInput(
                name=self.name, kind=self.kind, config=config, secret_ref="(personal)", allowed_objects=allowed
            )
        elif self.kind == "snowflake":
            config = {
                k: (getattr(self, k) or (current.config.get(k) if current else None))
                for k in ("account", "user", "warehouse", "database", "schema")
            }
            config["role"] = self.role
            config["auth_method"] = self.auth_method or (
                current.config.get("auth_method", "key_pair") if current else "key_pair"
            )
            _validate_snowflake_config(config)
            allowed = [a.strip() for a in self.allowed_objects if a.strip()]
            if not allowed:
                raise ValueError("at least one Snowflake allowlist entry is required")
            return ConnectionInput(
                name=self.name, kind=self.kind, config=config, secret_ref="(personal)", allowed_objects=allowed
            )
        raise ValueError(f"unknown connection kind: {self.kind}")

    def _resolved_bigquery(self, key: dict[str, Any] | None, current: ConnectionProfile | None = None) -> ConnectionInput:
        """BigQuery: project defaults to the key's project."""
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

    def _resolved_postgres(self, current: ConnectionProfile | None = None) -> ConnectionInput:
        """Postgres: requires host, port, database, user. Password and SSH config are stored separately."""
        host = self.host or (current.config.get("host") if current else None)
        database = self.database or (current.config.get("database") if current else None)
        user = self.user or (current.config.get("user") if current else None)
        
        if not isinstance(host, str) or not host:
            raise ValueError("host is required")
        if not isinstance(database, str) or not database:
            raise ValueError("database is required")
        if not isinstance(user, str) or not user:
            raise ValueError("user is required")
        
        port = self.port or (current.config.get("port") if current else None) or 5432
        schema = self.schema or (current.config.get("schema") if current else None) or "public"
        ssl_mode = self.ssl_mode or (current.config.get("ssl_mode") if current else None) or "prefer"
        
        config: dict[str, Any] = {"host": host, "port": port, "database": database, "user": user, "schema": schema, "ssl_mode": ssl_mode}
        if self.ssh_tunnel is not None:
            config["ssh_tunnel"] = self.ssh_tunnel.public()
        
        # Allowlist: for Postgres, use schema.table format (not project.dataset.table)
        allowed = [a.strip() for a in self.allowed_objects if a.strip()] or ["*.*"]
        
        return ConnectionInput(
            name=self.name, kind=self.kind, config=config, secret_ref="(personal)", allowed_objects=allowed
        )


class CredentialError(Exception):
    pass


def is_tenant_secret_ref(tenant_id: UUID | None, secret_ref: str) -> bool:
    """Secret Manager refs must name a secret prefixed with the owning tenant's id."""
    return bool(
        re.fullmatch(rf"projects/[a-z0-9-]+/secrets/odn-{tenant_id}-[A-Za-z0-9_-]+/versions/(latest|\d+)", secret_ref)
    )


def _service_account_info(profile: ConnectionProfile) -> dict[str, Any]:
    if profile.secret_ciphertext is not None or profile.personal:
        if profile.secret_ciphertext is None:
            raise CredentialError("this connection has no stored key")
        try:
            raw = crypto.decrypt(
                profile.secret_ciphertext, _secret_aad(profile.tenant_id, profile.owner_user_id, profile.id)
            )
        except (crypto.SecretsUnavailable, crypto.SecretDecryptionError) as exc:
            raise CredentialError(str(exc)) from None
        info = json.loads(raw)
        if not isinstance(info, dict):
            raise CredentialError("invalid service account credentials")
        return info

    tenant_id, secret_ref = profile.tenant_id, profile.secret_ref or ""
    if is_tenant_secret_ref(tenant_id, secret_ref):
        from google.cloud import secretmanager

        payload = secretmanager.SecretManagerServiceClient().access_secret_version(name=secret_ref).payload.data
        info = json.loads(payload)
        if not isinstance(info, dict):
            raise CredentialError("invalid service account credentials")
        return info

    if secret_ref.startswith("file:"):
        if settings.env != "local":
            raise CredentialError("file: secret refs are only allowed in local development")
        keys_dir = Path(settings.keys_dir).resolve()
        path = (keys_dir / secret_ref.removeprefix("file:")).resolve()
        if not path.is_relative_to(keys_dir) or not path.is_file():
            raise CredentialError("credential file not found")
        info = json.loads(path.read_bytes())
        if not isinstance(info, dict):
            raise CredentialError("invalid service account credentials")
        return info

    raise CredentialError("secret_ref is not a permitted reference for this tenant")


def resolve_credentials(profile: ConnectionProfile) -> Credentials:
    """Every connection must carry its own credentials; the platform identity is never used as a fallback."""
    return service_account.Credentials.from_service_account_info(_service_account_info(profile), scopes=_SCOPES)


def resolve_profile_credentials(profile: ConnectionProfile) -> tuple[Credentials, str]:
    """Bind persisted profiles to the exact public identity of the resolved service-account key."""
    info = _service_account_info(profile)
    credentials = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    key = serialization.load_pem_private_key(info["private_key"].encode(), password=None)
    public_key = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    public_info = {name: value for name, value in info.items() if name != "private_key"}
    identity = json.dumps(public_info, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    fingerprint = hashlib.sha256(public_key + b"\0" + identity).hexdigest()
    return credentials, fingerprint


def resolve_postgres_credentials(profile: ConnectionProfile) -> str | None:
    """Resolve Postgres database password from encrypted storage.
    
    Returns:
        Password string or None if not set (will use connection without password).
        
    Raises:
        CredentialError: If decryption fails
    """
    if profile.secret_ciphertext is not None or profile.personal:
        if profile.secret_ciphertext is None:
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
        return postgres_connector(profile.config, resolve_postgres_credentials(profile), resolve_ssh_key(profile))
    if profile.kind == "snowflake":
        secret = resolve_postgres_credentials(profile)
        if not secret:
            raise CredentialError("this connection has no stored Snowflake credentials")
        return snowflake_connector(profile.config, secret)
    if profile.kind == "mysql":
        secret = resolve_postgres_credentials(profile)
        if not secret:
            raise CredentialError("this connection has no stored MySQL password")
        return mysql_connector(profile.config, secret)
    if profile.kind == "oracle":
        secret = resolve_postgres_credentials(profile)
        if not secret:
            raise CredentialError("this connection has no stored Oracle password")
        return oracle_connector(profile.config, secret)
    if profile.kind == "bigquery":
        return bigquery_connector(profile.config, resolve_credentials(profile))
    raise ValueError(f"unsupported connection kind: {profile.kind}")


def mysql_connector(cfg: dict[str, Any], password: str) -> BaseConnector:
    from odn.platform.connectors.mysql import MySQLConnector

    return MySQLConnector(
        host=cfg["host"], port=cfg.get("port", 3306), database=cfg["database"],
        user=cfg["user"], password=password, ssl_ca=cfg.get("ssl_ca"),
    )


def _mysql_secret(data: PersonalConnectionInput, current: ConnectionProfile | None) -> bytes | None:
    if data.password:
        if len(data.password.encode()) > 16_384:
            raise ValueError("MySQL password is too large")
        return data.password.encode()
    if current is None:
        raise ValueError("a MySQL password is required")
    return None


def oracle_connector(cfg: dict[str, Any], password: str) -> BaseConnector:
    from odn.platform.connectors.oracle import OracleConnector

    return OracleConnector(
        host=cfg["host"], port=cfg.get("port", 2484), service_name=cfg["service_name"], schema=cfg["schema"],
        user=cfg["user"], password=password, ssl_ca=cfg.get("ssl_ca"),
    )


def _oracle_secret(data: PersonalConnectionInput, current: ConnectionProfile | None) -> bytes | None:
    if data.password:
        if len(data.password.encode()) > 16_384:
            raise ValueError("Oracle password is too large")
        return data.password.encode()
    if current is None:
        raise ValueError("an Oracle password is required")
    return None


def snowflake_connector(cfg: dict[str, Any], secret: str) -> BaseConnector:
    from odn.platform.connectors.snowflake import SnowflakeConnector

    try:
        auth_method = cfg.get("auth_method", "key_pair")
        if auth_method not in ("key_pair", "password"):
            raise ValueError("invalid authentication method")
        return SnowflakeConnector(
            account=cfg["account"], user=cfg["user"], warehouse=cfg["warehouse"],
            database=cfg["database"], schema=cfg["schema"], role=cfg.get("role"),
            private_key=secret if auth_method == "key_pair" else None,
            password=secret if auth_method == "password" else None,
        )
    except (ValueError, TypeError):
        raise CredentialError("the stored Snowflake credentials are invalid") from None


def _snowflake_secret(data: PersonalConnectionInput, current: ConnectionProfile | None) -> bytes | None:
    method = data.auth_method or (current.config.get("auth_method", "key_pair") if current else "key_pair")
    previous = current.config.get("auth_method", "key_pair") if current else None
    if method == "password":
        if data.private_key or data.private_key_passphrase:
            raise ValueError("private keys cannot be used with password authentication")
        secret = data.password.encode() if data.password else None
        label = "password"
    else:
        if data.password:
            raise ValueError("a password cannot be used with key-pair authentication")
        if data.private_key_passphrase and not data.private_key:
            raise ValueError("supply a private key when changing its passphrase")
        secret = parse_snowflake_key(data.private_key, data.private_key_passphrase) if data.private_key else None
        label = "private key"
    if secret is None and (current is None or method != previous):
        raise ValueError(f"a Snowflake {label} is required")
    return secret


def bigquery_connector(cfg: dict[str, Any], credentials: Credentials) -> BaseConnector:
    from odn.platform.connectors.bigquery import BigQueryConnector

    return BigQueryConnector(
        project=cfg["project"],
        credentials=credentials,
        location=cfg.get("location"),
        billing_project=cfg.get("billing_project"),
        default_dataset=cfg.get("default_dataset"),
    )


def postgres_connector(cfg: dict[str, Any], password: str | None = None, ssh_key: str | None = None) -> BaseConnector:
    """PostgreSQL connector; with `ssh_tunnel` in the config it connects through the bastion tunnel.

    Raises TunnelError (safe message) when the tunnel cannot be opened.
    """
    from odn.platform.connectors import ssh_tunnel
    from odn.platform.connectors.postgres import PostgresConnector

    host, port = cfg["host"], int(cfg.get("port", 5432))
    tunnel = cfg.get("ssh_tunnel")
    if tunnel:
        if not ssh_key:
            raise ssh_tunnel.TunnelError("the SSH private key for this connection is missing; enter it again")
        port = ssh_tunnel.pool.local_port(
            ssh_tunnel.TunnelSpec(
                bastion_host=tunnel["bastion_host"],
                bastion_port=int(tunnel.get("bastion_port", 22)),
                bastion_user=tunnel["bastion_user"],
                host_key_fingerprint=tunnel["known_hosts_fingerprint"],
                db_host=host,
                db_port=port,
                private_key=ssh_key,
            )
        )
        host = "127.0.0.1"
    return PostgresConnector(
        host=host,
        port=port,
        database=cfg["database"],
        user=cfg["user"],
        password=password,
        schema=cfg.get("schema", "public"),
        ssl_mode=cfg.get("ssl_mode", "prefer"),
    )


def build_policy(profile: ConnectionProfile, connector: BaseConnector) -> SqlPolicy:
    return SqlPolicy(
        dialect=connector.dialect,
        allowed_objects=frozenset(profile.allowed_objects),
        default_namespace=connector.default_namespace,
    )


_COLUMNS = (
    "id, tenant_id, name, kind, config, secret_ref, allowed_objects, owner_user_id, secret_ciphertext, ssh_key_ciphertext"
)


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
    """Create (connection_id None) or update one of the user's own connections (BigQuery or Postgres)."""
    return save_connection(tenant_id, user_id, data, connection_id, shared=False)


def save_connection(
    tenant_id: UUID, user_id: UUID, data: PersonalConnectionInput, connection_id: UUID | None = None,
    *, shared: bool = False,
) -> ConnectionProfile:
    """Create/update a personal connection, or (shared=True; callers check the role) an organization-wide one.

    Secrets are encrypted with AAD bound to the tenant, the owner ('shared' for organization connections) and the
    connection. Editing an operator-provisioned shared connection keeps its Secret Manager reference unless a new
    key is supplied."""
    import psycopg
    from psycopg.types.json import Jsonb

    owner = None if shared else user_id
    current = get_connection(tenant_id, user_id, connection_id) if connection_id else None
    if connection_id and (current is None or current.owner_user_id != owner):
        raise LookupError("connection not found")
    if current is not None and current.tenant_id is None:
        raise PermissionError("platform connections are managed by the EasyQuery team and can't be edited here")
    if current is not None and current.kind != data.kind and (
        {"snowflake", "mysql", "oracle"} & {current.kind, data.kind}
    ):
        raise ValueError("connection kind cannot be changed; create a new connection")
    
    # Validate and extract credentials/secrets based on kind
    if data.kind == "bigquery":
        key = parse_service_account_key(data.credentials_json) if data.credentials_json else None
        if key is None and current is None:
            raise ValueError("a service-account JSON key is required for BigQuery")
        if key is None and current is not None and current.kind != "bigquery":
            raise ValueError("a service-account JSON key is required when switching to BigQuery")
        secret_to_encrypt = json.dumps(key).encode() if key is not None else None
    elif data.kind == "postgres":
        # For Postgres, encrypt the password if provided
        secret_to_encrypt = data.password.encode() if data.password else None
        if secret_to_encrypt is None and current is None:
            # Password is optional for some Postgres auth methods
            pass
        key = None  # No key dict for Postgres
    elif data.kind == "snowflake":
        secret_to_encrypt = _snowflake_secret(data, current)
        key = None
    elif data.kind == "mysql":
        secret_to_encrypt = _mysql_secret(data, current)
        key = None
    elif data.kind == "oracle":
        secret_to_encrypt = _oracle_secret(data, current)
        key = None
    else:
        raise ValueError(f"unsupported connection kind: {data.kind}")
    
    spec = data.resolved(key, current)
    cid = connection_id or uuid4()
    ciphertext = (
        crypto.encrypt(secret_to_encrypt, _secret_aad(tenant_id, owner, cid)) if secret_to_encrypt is not None else None
    )
    tunnel = data.ssh_tunnel if data.kind == "postgres" else None
    ssh_key = tunnel.ssh_private_key if tunnel else None
    if tunnel and ssh_key is None and (current is None or current.ssh_key_ciphertext is None):
        raise ValueError("an SSH private key is required for the tunnel")
    ssh_ciphertext = crypto.encrypt(ssh_key.encode(), _ssh_key_aad(tenant_id, owner, cid)) if ssh_key else None
    # No tunnel any more: drop the stored key. Tunnel without a new key: keep the stored one.
    keep_ssh_key = tunnel is not None and ssh_key is None
    
    try:
        with tenant_transaction(tenant_id, user_id) as conn:
            if current is None:
                row = conn.execute(
                    f"""
                    INSERT INTO connections (id, tenant_id, owner_user_id, name, kind, config, allowed_objects,
                                             secret_ciphertext, ssh_key_ciphertext, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING {_COLUMNS}
                    """,
                    (cid, tenant_id, owner, spec.name, spec.kind, Jsonb(spec.config), spec.allowed_objects,
                     ciphertext, ssh_ciphertext, user_id),
                ).fetchone()
            else:
                row = conn.execute(
                    f"""
                    UPDATE connections
                       SET name = %s, config = %s, allowed_objects = %s,
                           secret_ciphertext = coalesce(%s, secret_ciphertext),
                           secret_ref = CASE WHEN %s::bytea IS NULL THEN secret_ref END,
                           ssh_key_ciphertext = CASE WHEN %s THEN ssh_key_ciphertext ELSE %s END,
                           updated_at = now()
                     WHERE id = %s AND owner_user_id IS NOT DISTINCT FROM %s
                    RETURNING {_COLUMNS}
                    """,
                    (spec.name, Jsonb(spec.config), spec.allowed_objects, ciphertext, ciphertext, keep_ssh_key,
                     ssh_ciphertext, cid, owner),
                ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise DuplicateName(spec.name) from None
    if row is None:
        raise LookupError("connection not found")
    return ConnectionProfile.model_validate(row)


def delete_personal_connection(tenant_id: UUID, user_id: UUID, connection_id: UUID) -> bool:
    return delete_connection(tenant_id, user_id, connection_id, shared=False) is not None


def delete_connection(tenant_id: UUID, user_id: UUID, connection_id: UUID, *, shared: bool = False) -> str | None:
    """Delete a personal (or, shared=True, an organization) connection; returns its name if it existed."""
    with tenant_transaction(tenant_id, user_id) as conn:
        row = conn.execute(
            "DELETE FROM connections WHERE id = %s AND owner_user_id IS NOT DISTINCT FROM %s RETURNING name",
            (connection_id, None if shared else user_id),
        ).fetchone()
    return row["name"] if row else None


def unsaved_connector(data: PersonalConnectionInput, current: ConnectionProfile | None) -> BaseConnector:
    """Connector for 'Test connection' before saving: the submitted credentials, or the stored ones when editing."""
    spec = None
    if current is not None and current.kind != data.kind and (
        {"snowflake", "mysql", "oracle"} & {current.kind, data.kind}
    ):
        raise ValueError("connection kind cannot be changed; create a new connection")
    
    if data.kind == "bigquery":
        key = parse_service_account_key(data.credentials_json) if data.credentials_json else None
        spec = data.resolved(key, current)
        if key is not None:
            credentials: Credentials = service_account.Credentials.from_service_account_info(key, scopes=_SCOPES)
        elif current is not None:
            credentials = resolve_credentials(current)
        else:
            raise ValueError("a service-account JSON key is required for BigQuery")
        return bigquery_connector(spec.config, credentials)
    
    elif data.kind == "postgres":
        # For Postgres: use submitted password or the stored one when editing
        password = data.password
        if password is None and current is not None:
            password = resolve_postgres_credentials(current)
        spec = data.resolved(None, current)
        ssh_key = None
        if data.ssh_tunnel is not None:
            ssh_key = data.ssh_tunnel.ssh_private_key or (resolve_ssh_key(current) if current is not None else None)
        return postgres_connector(spec.config, password, ssh_key)
    elif data.kind == "snowflake":
        secret = _snowflake_secret(data, current)
        value = secret.decode() if secret is not None else resolve_postgres_credentials(current) if current else None
        if not value:
            raise ValueError("Snowflake credentials are required")
        return snowflake_connector(data.resolved(None, current).config, value)
    elif data.kind == "mysql":
        secret = _mysql_secret(data, current)
        value = secret.decode() if secret is not None else resolve_postgres_credentials(current) if current else None
        if not value:
            raise ValueError("a MySQL password is required")
        return mysql_connector(data.resolved(None, current).config, value)
    elif data.kind == "oracle":
        secret = _oracle_secret(data, current)
        value = secret.decode() if secret is not None else resolve_postgres_credentials(current) if current else None
        if not value:
            raise ValueError("an Oracle password is required")
        return oracle_connector(data.resolved(None, current).config, value)
    
    else:
        raise ValueError(f"unsupported connection kind: {data.kind}")


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
