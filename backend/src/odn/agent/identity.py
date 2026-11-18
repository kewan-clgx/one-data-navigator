"""Per-request caller identity for the agent runtime.

The user's bearer token lives only in a context variable (never in ADK session state, which is
persisted and streamed to the browser); tools forward it so the API enforces tenancy and policy.
"""

from __future__ import annotations

import hashlib
import json
import time
from contextvars import ContextVar
from functools import cache

import httpx
from starlette.types import ASGIApp, Receive, Scope, Send

from odn.config import settings
from odn.tenancy.oidc import InvalidToken, TrustedIssuers, verifier_from_settings

bearer_token: ContextVar[str | None] = ContextVar("odn_bearer_token", default=None)
user_key: ContextVar[str | None] = ContextVar("odn_user_key", default=None)

PUBLIC_PATHS = {"/healthz"}


# The tenant comes from the API's tenant directory (the agent has no access to it). Cached per token so a
# chat turn's many requests cost one lookup; short TTL so disabling a user takes effect quickly.
_KEY_TTL_S = 60
_KEY_CACHE_MAX = 2048
_keys: dict[str, tuple[float, str]] = {}


class AccessRefused(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@cache
def _verifier() -> TrustedIssuers:
    return verifier_from_settings()


async def _session_key(token: str, subject: str) -> str:
    digest = hashlib.sha256(token.encode()).hexdigest()
    hit = _keys.get(digest)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    try:
        async with httpx.AsyncClient(base_url=settings.api_url, timeout=15) as client:
            resp = await client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError:
        raise AccessRefused(503, "identity service unavailable") from None
    if resp.status_code != 200:
        detail = resp.json().get("detail", "access denied") if resp.headers.get("content-type", "").startswith(
            "application/json") else "access denied"
        raise AccessRefused(resp.status_code if resp.status_code in (401, 403) else 503, str(detail))
    # Same key format as before the tenant directory, so existing conversations stay attached to their users.
    key = f"{resp.json()['tenant']['slug']}:{subject}"
    if len(_keys) >= _KEY_CACHE_MAX:
        _keys.clear()
    _keys[digest] = (time.monotonic() + _KEY_TTL_S, key)
    return key


class BearerAuthMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware) so the context variables propagate into agent tasks."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        auth = headers.get(b"authorization", b"").decode()
        scheme, _, token = auth.partition(" ")
        try:
            if scheme.lower() != "bearer" or not token:
                raise InvalidToken("missing bearer token")
            verified = _verifier().verify(token)
            key = await _session_key(token, verified.subject)
        except InvalidToken:
            await _unauthorized(send)
            return
        except AccessRefused as exc:
            await _unauthorized(send, exc.status, exc.detail)
            return
        bearer_token.set(token)
        user_key.set(key)
        await self.app(scope, receive, send)


async def _unauthorized(send: Send, status: int = 401, detail: str = "invalid or missing token") -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")],
        }
    )
    await send({"type": "http.response.body", "body": body})
