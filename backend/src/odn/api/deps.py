"""FastAPI dependencies: bearer token -> verified OIDC identity -> tenant/user context."""

from __future__ import annotations

from functools import cache
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from odn.config import settings
from odn.tenancy.context import RequestContext, UnknownTenant, resolve_context
from odn.tenancy.oidc import InvalidToken, TokenVerifier

_bearer = HTTPBearer(auto_error=False)


@cache
def token_verifier() -> TokenVerifier:
    return TokenVerifier(
        issuer=settings.oidc_issuer,
        jwks_url=settings.oidc_jwks_url,
        audience=settings.oidc_audience,
        tenant_claim=settings.oidc_tenant_claim,
    )


def request_context(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    verifier: Annotated[TokenVerifier, Depends(token_verifier)],
) -> RequestContext:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "invalid or missing token", headers={"WWW-Authenticate": "Bearer"}
    )
    if credentials is None:
        raise unauthorized
    try:
        token = verifier.verify(credentials.credentials)
    except InvalidToken:
        raise unauthorized from None
    try:
        return resolve_context(token)
    except UnknownTenant:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant is not provisioned") from None


Context = Annotated[RequestContext, Depends(request_context)]


def require_data_lead(ctx: Context) -> RequestContext:
    if ctx.role not in ("data_lead", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "data lead or admin role required")
    return ctx
