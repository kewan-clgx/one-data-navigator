"""Bring-your-own LLM keys: one per user, encrypted at rest, readable only by that user and the agent runtime."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, field_validator

from odn.db import tenant_transaction
from odn.platform import crypto
from odn.platform.net import UnsafeUrl, check_public_url
from odn.tenancy.context import RequestContext

log = logging.getLogger(__name__)

Provider = Literal["gemini", "openai", "anthropic", "openai_compatible", "github_copilot"]

# LiteLLM model prefix per provider (an OpenAI-compatible endpoint is called with the OpenAI protocol).
_LITELLM_PREFIX: dict[str, str] = {
    "gemini": "gemini/",
    "openai": "openai/",
    "anthropic": "anthropic/",
    "openai_compatible": "openai/",
    # Not a LiteLLM model: the agent runs these through the GitHub Copilot SDK (odn.agent.copilot_llm).
    "github_copilot": "",
}
_TEST_TIMEOUT_S = 10


class LlmKeyInput(BaseModel):
    provider: Provider
    model: str = Field(min_length=1, max_length=200)
    # Only for openai_compatible, e.g. https://llm.example.com/v1
    api_base: str | None = Field(default=None, max_length=500)
    # Omit when updating provider/model to keep the stored key.
    api_key: str | None = Field(default=None, max_length=1000, repr=False)

    @field_validator("model", "api_base", "api_key", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> Any:
        return v.strip() or None if isinstance(v, str) else v

    def normalized_model(self) -> str:
        """Model id without a LiteLLM provider prefix the user may have typed (e.g. "gemini/x" -> "x")."""
        prefix = _LITELLM_PREFIX[self.provider]
        return (self.model or "").removeprefix(prefix)

    def checked_api_base(self) -> str | None:
        if self.provider != "openai_compatible":
            return None
        if not self.api_base:
            raise ValueError("an endpoint URL is required for an OpenAI-compatible provider")
        return check_public_url(self.api_base)


class LlmKeyTestInput(LlmKeyInput):
    """Listing models needs no model yet: the user picks one from the list it returns."""

    model: str | None = Field(default=None, max_length=200)  # type: ignore[assignment]


class LlmKeyOut(BaseModel):
    provider: Provider
    model: str
    api_base: str | None
    key_hint: str
    updated_at: datetime


class LlmKeyTest(BaseModel):
    ok: bool
    error: str | None = None
    models: list[str] = Field(default_factory=list)
    model_found: bool | None = None


def _aad(ctx: RequestContext) -> str:
    return f"llm-key:{ctx.tenant_id}:{ctx.user_id}"


def _hint(key: str) -> str:
    return f"…{key[-4:]}" if len(key) >= 8 else "…"


def get_llm_key(ctx: RequestContext) -> LlmKeyOut | None:
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        row = conn.execute("SELECT provider, model, api_base, key_hint, updated_at FROM user_llm_keys").fetchone()
    return LlmKeyOut.model_validate(row) if row else None


def save_llm_key(ctx: RequestContext, data: LlmKeyInput, hint: str | None = None) -> LlmKeyOut:
    """Save the user's model settings; `hint` replaces the masked key in the UI (e.g. "@login" for GitHub)."""
    api_base = data.checked_api_base()
    model = data.normalized_model()
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        if data.api_key:
            ciphertext = crypto.encrypt(data.api_key.encode(), _aad(ctx))
            row = conn.execute(
                """
                INSERT INTO user_llm_keys (tenant_id, user_id, provider, model, api_base, key_ciphertext, key_hint)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, user_id) DO UPDATE
                  SET provider = EXCLUDED.provider, model = EXCLUDED.model, api_base = EXCLUDED.api_base,
                      key_ciphertext = EXCLUDED.key_ciphertext, key_hint = EXCLUDED.key_hint, updated_at = now()
                RETURNING provider, model, api_base, key_hint, updated_at
                """,
                (ctx.tenant_id, ctx.user_id, data.provider, model, api_base, ciphertext, hint or _hint(data.api_key)),
            ).fetchone()
        else:
            # A stored key is only reused for the same provider, so it never gets sent to a different service.
            row = conn.execute(
                """
                UPDATE user_llm_keys SET model = %s, api_base = %s, updated_at = now()
                 WHERE provider = %s AND (provider <> 'openai_compatible' OR api_base IS NOT DISTINCT FROM %s)
                RETURNING provider, model, api_base, key_hint, updated_at
                """,
                (model, api_base, data.provider, api_base),
            ).fetchone()
            if row is None:
                raise ValueError("enter the API key (required when adding a key or changing provider/endpoint)")
    assert row is not None
    return LlmKeyOut.model_validate(row)


def delete_llm_key(ctx: RequestContext) -> bool:
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        return conn.execute("DELETE FROM user_llm_keys").rowcount > 0


def _stored_key(ctx: RequestContext) -> tuple[dict[str, Any], str] | None:
    with tenant_transaction(ctx.tenant_id, ctx.user_id) as conn:
        row = conn.execute("SELECT provider, model, api_base, key_ciphertext FROM user_llm_keys").fetchone()
    if row is None:
        return None
    return row, crypto.decrypt(row["key_ciphertext"], _aad(ctx)).decode()


def resolve_llm_config(ctx: RequestContext) -> dict[str, Any] | None:
    """The user's model settings including the decrypted key (agent runtime only), or None for the platform key."""
    stored = _stored_key(ctx)
    if stored is None:
        return None
    row, api_key = stored
    api_base = check_public_url(row["api_base"]) if row["api_base"] else None
    return {
        "provider": row["provider"],
        "model": row["model"],
        "litellm_model": _LITELLM_PREFIX[row["provider"]] + row["model"],
        "api_base": api_base,
        "api_key": api_key,
    }


def check_llm_key(ctx: RequestContext, data: LlmKeyInput) -> LlmKeyTest:
    """Check the key by listing the provider's models (no tokens are spent)."""
    try:
        api_base = data.checked_api_base()
    except (ValueError, UnsafeUrl) as exc:
        return LlmKeyTest(ok=False, error=str(exc))
    api_key = data.api_key
    if not api_key:
        stored = _stored_key(ctx)
        if stored is None or stored[0]["provider"] != data.provider or stored[0]["api_base"] != api_base:
            return LlmKeyTest(ok=False, error="enter the API key to test")
        api_key = stored[1]

    if data.provider == "github_copilot":
        from odn.platform import github_copilot

        try:
            models = github_copilot.list_models(api_key)
        except Exception as exc:  # noqa: BLE001 - any SDK/runtime failure means the seat can't be used
            log.info("Copilot model listing failed: %s", type(exc).__name__)
            return LlmKeyTest(ok=False, error="this GitHub account has no usable GitHub Copilot access; sign in again")
        wanted = data.normalized_model()
        return LlmKeyTest(ok=True, models=models, model_found=wanted in models if models and wanted else None)

    if data.provider == "gemini":
        url, headers = (
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
            {"x-goog-api-key": api_key},
        )
    elif data.provider == "anthropic":
        url, headers = (
            "https://api.anthropic.com/v1/models?limit=1000",
            {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
    elif data.provider == "openai":
        url, headers = "https://api.openai.com/v1/models", {"Authorization": f"Bearer {api_key}"}
    else:
        url, headers = f"{api_base}/models", {"Authorization": f"Bearer {api_key}"}

    try:
        with httpx.Client(timeout=_TEST_TIMEOUT_S, follow_redirects=False) as client:
            resp = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return LlmKeyTest(ok=False, error=f"could not reach the provider ({type(exc).__name__})")
    # Gemini reports an invalid key as HTTP 400 (API_KEY_INVALID).
    if resp.status_code in (401, 403) or (resp.status_code == 400 and "API_KEY_INVALID" in resp.text):
        return LlmKeyTest(ok=False, error="the provider rejected this API key")
    if resp.status_code != 200:
        return LlmKeyTest(ok=False, error=f"the provider returned HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError:
        return LlmKeyTest(ok=False, error="the provider returned an unexpected response")

    if data.provider == "gemini":
        models = [
            m["name"].removeprefix("models/") for m in body.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
    else:
        models = [m["id"] for m in body.get("data", []) if isinstance(m, dict) and isinstance(m.get("id"), str)]
    models.sort()
    wanted = data.normalized_model()
    return LlmKeyTest(ok=True, models=models[:500], model_found=wanted in models if models and wanted else None)
