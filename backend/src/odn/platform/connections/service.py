"""Connection profiles (tenant-scoped, RLS-protected) and credential resolution."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from google.auth.credentials import Credentials
from google.oauth2 import service_account
from pydantic import BaseModel, Field, field_validator, model_validator

from odn.config import settings
from odn.db import tenant_transaction
from odn.platform.connectors.base import BaseConnector
from odn.platform.policy.sql_policy import SqlPolicy

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,62}$")


class ConnectionInput(BaseModel):
    name: str
    kind: Literal["bigquery"]
    config: dict[str, Any] = Field(default_factory=dict)
    secret_ref: str
    allowed_objects: list[str] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError("invalid connection name")
        return v

    @model_validator(mode="after")
    def _check(self) -> ConnectionInput:
        if self.kind == "bigquery" and not isinstance(self.config.get("project"), str):
            raise ValueError("bigquery config requires 'project'")
        for pattern in self.allowed_objects:
            SqlPolicy.validate_pattern(self.kind, pattern)
        return self


class ConnectionProfile(ConnectionInput):
    id: UUID
    tenant_id: UUID


class CredentialError(Exception):
    pass


def resolve_credentials(tenant_id: UUID, secret_ref: str) -> Credentials:
    """Every connection must carry its own credentials; the platform identity is never used as a fallback."""
    sm = re.fullmatch(
        rf"projects/[a-z0-9-]+/secrets/odn-{tenant_id}-[A-Za-z0-9_-]+/versions/(latest|\d+)", secret_ref
    )
    if sm:
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


def build_connector(profile: ConnectionProfile) -> BaseConnector:
    from odn.platform.connectors.bigquery import BigQueryConnector

    credentials = resolve_credentials(profile.tenant_id, profile.secret_ref)
    cfg = profile.config
    return BigQueryConnector(
        project=cfg["project"],
        credentials=credentials,
        location=cfg.get("location"),
        billing_project=cfg.get("billing_project"),
        default_dataset=cfg.get("default_dataset"),
    )


def build_policy(profile: ConnectionProfile, connector: BaseConnector) -> SqlPolicy:
    return SqlPolicy(
        dialect=connector.dialect,
        allowed_objects=frozenset(profile.allowed_objects),
        default_namespace=connector.default_namespace,
    )


_COLUMNS = "id, tenant_id, name, kind, config, secret_ref, allowed_objects"


def list_connections(tenant_id: UUID) -> list[ConnectionProfile]:
    with tenant_transaction(tenant_id) as conn:
        rows = conn.execute(f"SELECT {_COLUMNS} FROM connections ORDER BY name").fetchall()
    return [ConnectionProfile.model_validate(r) for r in rows]


def get_connection(tenant_id: UUID, connection_id: UUID) -> ConnectionProfile | None:
    with tenant_transaction(tenant_id) as conn:
        row = conn.execute(f"SELECT {_COLUMNS} FROM connections WHERE id = %s", (connection_id,)).fetchone()
    return ConnectionProfile.model_validate(row) if row else None


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
