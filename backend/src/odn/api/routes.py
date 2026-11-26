"""/v1 routes: identity, connections, explorer catalog/schema, and queries."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from google.api_core import exceptions as gexc
from pydantic import BaseModel, Field

from odn.api.deps import Context, require_data_lead
from odn.config import settings
from odn.db import tenant_transaction
from odn.platform.catalog.dictionary import (
    CuratedDefinition,
    DefinitionInput,
    NotAllowed,
    TableDictionary,
    all_definitions,
    table_dictionary,
    upsert_definition,
)
from odn.platform.catalog.personal import (
    Favorite,
    TableUsage,
    add_favorite,
    list_favorites,
    recent_tables,
    remove_favorite,
)
from odn.platform.catalog.personal import NotAllowed as FavoriteNotAllowed
from odn.platform.catalog.search_index import SearchIndex, get_search_index
from odn.platform.connections.service import (
    ConnectionInput,
    ConnectionProfile,
    CredentialError,
    build_connector,
    build_policy,
    create_connection,
    get_connection,
    list_connections,
)
from odn.platform.connectors.base import (
    BaseConnector,
    CatalogNode,
    ConnectionTestStep,
    ConnectorError,
    QueryLimits,
    QueryResultMeta,
    TableSchema,
)
from odn.platform.governance.audit import Source
from odn.platform.policy.sql_policy import SqlPolicy
from odn.platform.query.service import (
    ConfirmationRequired,
    PreviewSink,
    QueryFailed,
    QueryRejected,
    run_query,
    scan_cap,
)
from odn.tenancy.context import RequestContext

router = APIRouter(prefix="/v1")

EXPLORER_MAX_ROWS = 1000


_SECRET_KEYS = {"password", "ssh_private_key", "private_key", "private_key_passphrase", "credentials_json", "api_key"}


def _public_config(value: Any) -> Any:
    """Connection config as shown to the browser: secret-looking keys removed at any depth (defence in depth)."""
    if isinstance(value, dict):
        return {k: _public_config(v) for k, v in value.items() if k not in _SECRET_KEYS}
    if isinstance(value, list):
        return [_public_config(v) for v in value]
    return value


class ConnectionOut(BaseModel):
    id: UUID
    name: str
    kind: str
    config: dict[str, Any]
    allowed_objects: list[str]
    # "shared": managed for the whole organization; "personal": the user's own (editable in Settings).
    scope: Literal["shared", "personal"]

    @classmethod
    def of(cls, p: ConnectionProfile) -> ConnectionOut:
        return cls(
            id=p.id, name=p.name, kind=p.kind, config=_public_config(p.config), allowed_objects=p.allowed_objects,
            scope="personal" if p.personal else "shared",
        )


class QueryRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=100_000)
    source: Source = "explorer"
    confirmed: bool = False
    max_rows: int = Field(default=EXPLORER_MAX_ROWS, ge=1, le=EXPLORER_MAX_ROWS)


class QueryResponse(BaseModel):
    meta: QueryResultMeta
    tables: list[str]
    bytes_estimated: int | None
    rows: list[dict[str, Any]]


@router.get("/me")
def me(ctx: Context) -> dict[str, Any]:
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        user = conn.execute("SELECT email, display_name FROM users WHERE id = %s", (ctx.user_id,)).fetchone() or {}
        tenant = conn.execute("SELECT * FROM odn_tenant_profile()").fetchone() or {}
    return {
        "tenant_id": ctx.tenant_id,
        "user_id": ctx.user_id,
        "subject": ctx.subject,
        "role": ctx.role,
        "user": {"email": user.get("email"), "name": user.get("display_name")},
        "tenant": {
            "slug": tenant.get("slug", ctx.tenant_slug),
            "name": tenant.get("name"),
            "theme": tenant.get("theme"),
            "auth_method": tenant.get("auth_method"),
            "domains": tenant.get("domains", []),
        },
    }


@router.get("/connections")
def connections(ctx: Context) -> list[ConnectionOut]:
    return [ConnectionOut.of(p) for p in list_connections(ctx.tenant_id, ctx.user_id)]


@router.post("/connections", status_code=status.HTTP_201_CREATED)
def add_connection(
    data: ConnectionInput, ctx: Annotated[RequestContext, Depends(require_data_lead)]
) -> ConnectionOut:
    return ConnectionOut.of(create_connection(ctx.tenant_id, ctx.user_id, data))


def _profile(ctx: RequestContext, connection_id: UUID) -> ConnectionProfile:
    profile = get_connection(ctx.tenant_id, ctx.user_id, connection_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connection not found")
    return profile


def _connector(profile: ConnectionProfile) -> BaseConnector:
    try:
        return build_connector(profile)
    except CredentialError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"connection credentials unavailable: {exc}") from None


@router.post("/connections/{connection_id}/test")
def test_connection(connection_id: UUID, ctx: Context) -> list[ConnectionTestStep]:
    return _connector(_profile(ctx, connection_id)).test_connection()


@router.get("/connections/{connection_id}/catalog")
def catalog(
    connection_id: UUID, ctx: Context, parent: Annotated[list[str] | None, Query()] = None
) -> list[CatalogNode]:
    profile = _profile(ctx, connection_id)
    connector = _connector(profile)
    policy = build_policy(profile, connector)
    try:
        nodes = connector.list_catalog(tuple(parent or ()))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    except gexc.GoogleAPIError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, type(exc).__name__) from None
    except ConnectorError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    # Explorer metadata is limited to the same allowlist as queries.
    return [
        n
        for n in nodes
        if (policy.allows_object(n.path) if n.kind in ("table", "view") else policy.allows_container(n.path))
    ]


@router.get("/connections/{connection_id}/schema")
def schema(connection_id: UUID, ctx: Context, path: Annotated[list[str], Query()]) -> TableSchema:
    profile = _profile(ctx, connection_id)
    connector = _connector(profile)
    if not build_policy(profile, connector).allows_object(tuple(path)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "object not in allowlist")
    try:
        return connector.get_schema(tuple(path))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    except gexc.NotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "table not found") from None
    except ConnectorError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None


@router.get("/connections/{connection_id}/search-index")
def search_index(connection_id: UUID, ctx: Context, refresh: bool = False) -> SearchIndex:
    """Allowlisted datasets, tables and columns for client-side keyword search (cached for an hour)."""
    profile = _profile(ctx, connection_id)
    connector = _connector(profile)
    try:
        return get_search_index(
            ctx.tenant_id, profile.id, connector, build_policy(profile, connector), refresh=refresh
        )
    except gexc.GoogleAPIError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, type(exc).__name__) from None
    except ConnectorError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None


@router.get("/connections/{connection_id}/dictionary")
def dictionary(connection_id: UUID, ctx: Context, path: Annotated[list[str], Query()]) -> TableDictionary:
    """Source descriptions (live) merged with curated business definitions for one approved table."""
    profile = _profile(ctx, connection_id)
    connector = _connector(profile)
    try:
        return table_dictionary(ctx, profile.id, connector, build_policy(profile, connector), tuple(path))
    except NotAllowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "object not in allowlist") from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    except ConnectorError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None


@router.get("/connections/{connection_id}/definitions")
def definitions(connection_id: UUID, ctx: Context) -> list[dict[str, str]]:
    profile = _profile(ctx, connection_id)
    rows = all_definitions(ctx, profile.id)
    if profile.kind not in ("snowflake", "mysql", "oracle"):
        return rows
    policy = SqlPolicy(dialect=profile.kind, allowed_objects=frozenset(profile.allowed_objects))
    return [
        row for row in rows
        if policy.allows_object(tuple(row["table_path"].split(".")))
    ]


@router.put("/connections/{connection_id}/definitions")
def put_definition(connection_id: UUID, data: DefinitionInput, ctx: Context) -> CuratedDefinition:
    profile = _profile(ctx, connection_id)
    # Shared connections: data leads/admins. Personal connections: their owner curates their own definitions.
    if profile.owner_user_id != ctx.user_id and ctx.role not in ("data_lead", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "data lead or admin role required")
    try:
        return upsert_definition(ctx, profile.id, build_policy(profile, _connector(profile)), data)
    except NotAllowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "object not in allowlist") from None


class FavoriteInput(BaseModel):
    path: list[str] = Field(min_length=1, max_length=4)


class MyTables(BaseModel):
    favorites: list[Favorite]
    recent: list[TableUsage]
    case_sensitive: bool = False


@router.get("/connections/{connection_id}/my-tables")
def my_tables(connection_id: UUID, ctx: Context) -> MyTables:
    """The signed-in user's favorite datasets/tables and recently queried tables (within the allowlist)."""
    profile = _profile(ctx, connection_id)
    policy = build_policy(profile, _connector(profile))
    return MyTables(
        favorites=list_favorites(ctx, profile.id, policy), recent=recent_tables(ctx, profile.id, policy),
        case_sensitive=policy.case_sensitive,
    )


@router.put("/connections/{connection_id}/favorites")
def put_favorite(connection_id: UUID, data: FavoriteInput, ctx: Context) -> Favorite:
    profile = _profile(ctx, connection_id)
    try:
        return add_favorite(ctx, profile.id, build_policy(profile, _connector(profile)), data.path)
    except FavoriteNotAllowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "object not in allowlist") from None


@router.delete("/connections/{connection_id}/favorites", status_code=status.HTTP_204_NO_CONTENT)
def delete_favorite(connection_id: UUID, ctx: Context, path: Annotated[list[str], Query()]) -> None:
    profile = _profile(ctx, connection_id)
    remove_favorite(ctx, profile.id, path, preserve_case=profile.kind in ("snowflake", "mysql", "oracle"))


@router.post("/connections/{connection_id}/query")
def query(connection_id: UUID, req: QueryRequest, ctx: Context) -> QueryResponse:
    profile = _profile(ctx, connection_id)
    sink = PreviewSink()
    limits = QueryLimits(
        max_rows=req.max_rows,
        max_bytes_billed=scan_cap(ctx),
        timeout_s=settings.query_timeout_s,
    )
    try:
        outcome = run_query(
            ctx, profile, req.sql, source=req.source, sink=sink, limits=limits,
            confirmed=req.confirmed, connector=_connector(profile),
        )
    except QueryRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"rejected": exc.reason}) from None
    except ConfirmationRequired as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"confirmation_required": True, "bytes_estimated": exc.bytes_estimated}
        ) from None
    except QueryFailed as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {"failed": str(exc)}) from None
    return QueryResponse(
        meta=outcome.meta, tables=list(outcome.tables), bytes_estimated=outcome.bytes_estimated, rows=sink.rows
    )
