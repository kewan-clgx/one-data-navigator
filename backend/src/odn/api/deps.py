"""FastAPI dependencies: bearer token -> verified OIDC identity -> tenant/user context."""

from __future__ import annotations

from functools import cache
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from odn.tenancy.context import AccessDenied, RequestContext, UnknownTenant, resolve_context
from odn.tenancy.oidc import InvalidToken, TrustedIssuers, verifier_from_settings

_bearer = HTTPBearer(auto_error=False)


@cache
def token_verifier() -> TrustedIssuers:
    return verifier_from_settings()


def request_context(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    verifier: Annotated[TrustedIssuers, Depends(token_verifier)],
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
    except AccessDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None


Context = Annotated[RequestContext, Depends(request_context)]


def require_admin(ctx: Context) -> RequestContext:
    if ctx.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")
    return ctx


def require_data_lead(ctx: Context) -> RequestContext:
    if ctx.role not in ("data_lead", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "data lead or admin role required")
    return ctx
