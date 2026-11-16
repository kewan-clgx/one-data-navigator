"""Curated data dictionary: live source descriptions (from the connector) + tenant business definitions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from google.api_core import exceptions as gexc
from pydantic import BaseModel, Field

from odn.db import tenant_transaction
from odn.platform.connectors.base import BaseConnector
from odn.platform.policy.sql_policy import SqlPolicy
from odn.tenancy.context import RequestContext

Status = Literal["draft", "approved", "deprecated"]


class CuratedDefinition(BaseModel):
    definition: str
    status: Status
    updated_by: str | None
    updated_at: datetime


class FieldEntry(BaseModel):
    name: str
    type: str
    source_description: str | None
    curated: CuratedDefinition | None


class TableDictionary(BaseModel):
    table_path: str
    source: Literal["ok", "not_found", "permission_denied", "unavailable"]
    source_description: str | None
    curated: CuratedDefinition | None
    fields: list[FieldEntry]


class DefinitionInput(BaseModel):
    table_path: str = Field(min_length=1, max_length=1024)
    field_path: str = Field(default="", max_length=1024)
    definition: str = Field(min_length=1, max_length=4000)
    status: Status = "draft"


class NotAllowed(Exception):
    pass


def _curated(tenant_id: UUID, connection_id: UUID, table_path: str) -> dict[str, CuratedDefinition]:
    with tenant_transaction(tenant_id) as conn:
        rows = conn.execute(
            """
            SELECT d.field_path, d.definition, d.status, d.updated_at,
                   coalesce(u.display_name, u.email, u.subject) AS updated_by
            FROM data_definitions d LEFT JOIN users u ON u.id = d.updated_by
            WHERE d.connection_id = %s AND d.table_path = %s
            """,
            (connection_id, table_path),
        ).fetchall()
    return {
        r["field_path"]: CuratedDefinition(
            definition=r["definition"], status=r["status"], updated_by=r["updated_by"], updated_at=r["updated_at"]
        )
        for r in rows
    }


def table_dictionary(
    ctx: RequestContext, connection_id: UUID, connector: BaseConnector, policy: SqlPolicy, path: tuple[str, ...]
) -> TableDictionary:
    if not policy.allows_object(path):
        raise NotAllowed(".".join(path))
    table_path = ".".join(path)
    curated = _curated(ctx.tenant_id, connection_id, table_path)
    try:
        schema = connector.get_schema(path)
    except gexc.NotFound:
        source, schema = "not_found", None
    except gexc.Forbidden:
        source, schema = "permission_denied", None
    except gexc.GoogleAPIError:
        source, schema = "unavailable", None
    else:
        source = "ok"

    fields = [
        FieldEntry(name=c.name, type=c.type, source_description=c.description, curated=curated.get(c.name))
        for c in (schema.columns if schema else [])
    ]
    # Curated definitions survive schema read failures (or nested fields) and are still shown.
    known = {f.name for f in fields}
    fields += [
        FieldEntry(name=name, type="", source_description=None, curated=d)
        for name, d in sorted(curated.items())
        if name and name not in known
    ]
    return TableDictionary(
        table_path=table_path,
        source=source,  # type: ignore[arg-type]
        source_description=schema.description if schema else None,
        curated=curated.get(""),
        fields=fields,
    )


def all_definitions(ctx: RequestContext, connection_id: UUID) -> list[dict[str, str]]:
    """Compact list for client-side search: table, field, definition, status."""
    with tenant_transaction(ctx.tenant_id) as conn:
        rows = conn.execute(
            "SELECT table_path, field_path, definition, status FROM data_definitions WHERE connection_id = %s",
            (connection_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_definition(
    ctx: RequestContext, connection_id: UUID, policy: SqlPolicy, data: DefinitionInput
) -> CuratedDefinition:
    if not policy.allows_object(tuple(data.table_path.split("."))):
        raise NotAllowed(data.table_path)
    with tenant_transaction(ctx.tenant_id) as conn:
        row = conn.execute(
            """
            INSERT INTO data_definitions (tenant_id, connection_id, table_path, field_path, definition, status,
                                          updated_by, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (tenant_id, connection_id, table_path, field_path) DO UPDATE
              SET definition = EXCLUDED.definition, status = EXCLUDED.status,
                  updated_by = EXCLUDED.updated_by, updated_at = now()
            RETURNING id, definition, status, updated_at
            """,
            (ctx.tenant_id, connection_id, data.table_path, data.field_path, data.definition.strip(),
             data.status, ctx.user_id),
        ).fetchone()
        conn.execute(
            "INSERT INTO data_definition_history (tenant_id, definition_id, definition, status, changed_by) "
            "VALUES (%s, %s, %s, %s, %s)",
            (ctx.tenant_id, row["id"], row["definition"], row["status"], ctx.user_id),
        )
    return CuratedDefinition(
        definition=row["definition"], status=row["status"], updated_by=ctx.subject, updated_at=row["updated_at"]
    )
