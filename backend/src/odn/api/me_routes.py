"""/v1/me routes: the signed-in user's own data connections and LLM key (Settings)."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from odn.api.deps import Context
from odn.api.routes import ConnectionOut
from odn.api.usage_routes import require_agent
from odn.platform import crypto
from odn.platform.connections.service import (
    CredentialError,
    DuplicateName,
    PersonalConnectionInput,
    delete_personal_connection,
    get_connection,
    save_personal_connection,
    unsaved_connector,
)
from odn.platform.connectors.base import ConnectionTestStep
from odn.platform.governance.llm_budget import budget_status
from odn.platform.llm_keys import (
    LlmKeyInput,
    LlmKeyOut,
    LlmKeyTest,
    check_llm_key,
    delete_llm_key,
    get_llm_key,
    resolve_llm_config,
    save_llm_key,
)
from odn.platform.net import UnsafeUrl

router = APIRouter(prefix="/v1/me")


def _unavailable() -> HTTPException:
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE, "saving credentials is not configured on this server (ODN_SECRETS_KEY)"
    )


# --- Data connections -----------------------------------------------------------------------------


@router.post("/connections", status_code=status.HTTP_201_CREATED)
def create_my_connection(data: PersonalConnectionInput, ctx: Context) -> ConnectionOut:
    return _save(ctx, data, None)


@router.put("/connections/{connection_id}")
def update_my_connection(connection_id: UUID, data: PersonalConnectionInput, ctx: Context) -> ConnectionOut:
    return _save(ctx, data, connection_id)


def _save(ctx: Any, data: PersonalConnectionInput, connection_id: UUID | None) -> ConnectionOut:
    try:
        return ConnectionOut.of(save_personal_connection(ctx.tenant_id, ctx.user_id, data, connection_id))
    except crypto.SecretsUnavailable:
        raise _unavailable() from None
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connection not found") from None
    except DuplicateName:
        raise HTTPException(status.HTTP_409_CONFLICT, "you already have a connection with this name") from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_connection(connection_id: UUID, ctx: Context) -> None:
    if not delete_personal_connection(ctx.tenant_id, ctx.user_id, connection_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connection not found")


@router.post("/connections/test")
def test_unsaved_connection(
    data: PersonalConnectionInput, ctx: Context, connection_id: UUID | None = None
) -> list[ConnectionTestStep]:
    """Test settings before saving (DBeaver's "Test Connection"); when editing, an omitted key uses the stored one."""
    current = None
    if connection_id is not None:
        current = get_connection(ctx.tenant_id, ctx.user_id, connection_id)
        if current is None or current.owner_user_id != ctx.user_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "connection not found")
    from odn.platform.connectors.ssh_tunnel import TunnelError

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


# --- LLM key --------------------------------------------------------------------------------------


@router.get("/llm-key")
def my_llm_key(ctx: Context) -> LlmKeyOut | None:
    return get_llm_key(ctx)


@router.put("/llm-key")
def put_my_llm_key(data: LlmKeyInput, ctx: Context) -> LlmKeyOut:
    try:
        return save_llm_key(ctx, data)
    except crypto.SecretsUnavailable:
        raise _unavailable() from None
    except (ValueError, UnsafeUrl) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None


@router.delete("/llm-key", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_llm_key(ctx: Context) -> None:
    delete_llm_key(ctx)


@router.post("/llm-key/test")
def test_my_llm_key(data: LlmKeyInput, ctx: Context) -> LlmKeyTest:
    try:
        return check_llm_key(ctx, data)
    except (crypto.SecretsUnavailable, crypto.SecretDecryptionError):
        return LlmKeyTest(ok=False, error="the stored key cannot be read; enter it again")


@router.get("/llm-config")
def my_llm_config(ctx: Context, x_odn_agent_token: Annotated[str, Header()] = "") -> dict[str, Any]:
    """Agent runtime only: which model/key to use for this user's next call, plus the trial budget."""
    require_agent(x_odn_agent_token)
    try:
        cfg = resolve_llm_config(ctx)
    except (crypto.SecretsUnavailable, crypto.SecretDecryptionError, UnsafeUrl) as exc:
        return {"source": "user_error", "error": str(exc)}
    if cfg is not None:
        return {"source": "user", **cfg}
    return {"source": "platform", "budget": budget_status(ctx.tenant_id, ctx.user_id).model_dump(mode="json")}


# Demo -> personal account (docs/config/config_demo_tenant.md, upgrade path). Called by the agent, which moves the
# conversations; personal accounts only, and only for a verified email.


class DemoImported(BaseModel):
    subjects: list[str] = Field(max_length=200)


def _demo_importer(ctx: Context) -> str:
    from odn.config import settings

    if not (settings.demo_tenant and settings.personal_tenant and ctx.tenant_slug == settings.personal_tenant
            and ctx.email and ctx.email_verified):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no demo chats to import")
    return ctx.email


@router.get("/demo-chats")
def demo_chats(ctx: Context) -> dict[str, Any]:
    from odn.config import settings
    from odn.platform import demo

    email = _demo_importer(ctx)
    try:
        return {"tenant": settings.demo_tenant, "subjects": demo.importable(email)}
    except demo.DemoUnavailable:
        return {"tenant": settings.demo_tenant, "subjects": []}


@router.post("/demo-chats/imported")
def demo_chats_imported(body: DemoImported, ctx: Context) -> dict[str, int]:
    from odn.platform import demo

    return {"retired": demo.retire_imported(_demo_importer(ctx), body.subjects)}
