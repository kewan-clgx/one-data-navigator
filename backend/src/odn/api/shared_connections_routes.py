"""Organization-wide (shared) data connections managed in the UI by admins and data leads.

Same form and validation as personal connections; credentials are encrypted and bound to the tenant
(AAD 'shared'), visible to every user in the tenant through RLS, and every change is an admin event.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from odn.api.admin_routes import record_event
from odn.api.deps import require_data_lead
from odn.api.routes import ConnectionOut
from odn.db import tenant_transaction
from odn.platform import crypto
from odn.platform.connections.service import (
    CredentialError,
    DuplicateName,
    PersonalConnectionInput,
    delete_connection,
    get_connection,
    save_connection,
    unsaved_connector,
)
from odn.platform.connectors.base import ConnectionTestStep
from odn.platform.connectors.ssh_tunnel import TunnelError
from odn.tenancy.context import RequestContext

router = APIRouter(prefix="/v1/connections/shared")
Manager = Annotated[RequestContext, Depends(require_data_lead)]


def _event(ctx: RequestContext, action: str, name: str, **detail: object) -> None:
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        record_event(conn, ctx, action, name, **detail)


def _save(ctx: RequestContext, data: PersonalConnectionInput, connection_id: UUID | None) -> ConnectionOut:
    try:
        saved = save_connection(ctx.tenant_id, ctx.user_id, data, connection_id, shared=True)
    except crypto.SecretsUnavailable:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "saving credentials is not configured on this server (ODN_SECRETS_KEY)"
        ) from None
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connection not found") from None
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    except DuplicateName:
        raise HTTPException(status.HTTP_409_CONFLICT, "your organization already has a connection with this name") from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    _event(ctx, "connection.updated" if connection_id else "connection.created", saved.name,
           kind=saved.kind, tunnel=bool(saved.config.get("ssh_tunnel")))
    return ConnectionOut.of(saved)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_shared(data: PersonalConnectionInput, ctx: Manager) -> ConnectionOut:
    return _save(ctx, data, None)


@router.put("/{connection_id}")
def update_shared(connection_id: UUID, data: PersonalConnectionInput, ctx: Manager) -> ConnectionOut:
    return _save(ctx, data, connection_id)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shared(connection_id: UUID, ctx: Manager) -> None:
    name = delete_connection(ctx.tenant_id, ctx.user_id, connection_id, shared=True)
    if name is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connection not found")
    _event(ctx, "connection.deleted", name)


@router.post("/test")
def test_shared(data: PersonalConnectionInput, ctx: Manager, connection_id: UUID | None = None) -> list[ConnectionTestStep]:
    current = None
    if connection_id is not None:
        current = get_connection(ctx.tenant_id, ctx.user_id, connection_id)
        if current is None or current.owner_user_id is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "connection not found")
    try:
        connector = unsaved_connector(data, current)
    except CredentialError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"stored key unavailable: {exc}") from None
    except TunnelError as exc:
        return [ConnectionTestStep(step="tunnel", ok=False, detail=str(exc))]
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    steps = connector.test_connection()
    if data.kind == "postgres" and data.ssh_tunnel is not None:
        steps = [ConnectionTestStep(step="tunnel", ok=True, detail=f"via {data.ssh_tunnel.bastion_host}"), *steps]
    return steps
