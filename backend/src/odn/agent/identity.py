"""Per-request caller identity for the agent runtime.

The user's bearer token lives only in a context variable (never in ADK session state, which is
persisted and streamed to the browser); tools forward it so the API enforces tenancy and policy.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from functools import cache

from starlette.types import ASGIApp, Receive, Scope, Send

from odn.config import settings
from odn.tenancy.oidc import InvalidToken, TokenVerifier

bearer_token: ContextVar[str | None] = ContextVar("odn_bearer_token", default=None)
user_key: ContextVar[str | None] = ContextVar("odn_user_key", default=None)

PUBLIC_PATHS = {"/healthz"}


@cache
def _verifier() -> TokenVerifier:
    return TokenVerifier(
        issuer=settings.oidc_issuer,
        jwks_url=settings.oidc_jwks_url,
        audience=settings.oidc_audience,
        tenant_claim=settings.oidc_tenant_claim,
    )


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
        except InvalidToken:
            await _unauthorized(send)
            return
        bearer_token.set(token)
        user_key.set(f"{verified.tenant_slug}:{verified.subject}")
        await self.app(scope, receive, send)


async def _unauthorized(send: Send) -> None:
    body = json.dumps({"detail": "invalid or missing token"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")],
        }
    )
    await send({"type": "http.response.body", "body": body})
