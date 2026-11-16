"""Verify OIDC ID/access tokens against the issuer's JWKS."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import jwt

# Asymmetric only: never accept "none" or shared-secret algorithms.
ALLOWED_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]


class InvalidToken(Exception):
    pass


@dataclass(frozen=True)
class VerifiedToken:
    issuer: str
    subject: str
    tenant_slug: str
    email: str | None
    name: str | None


@dataclass(frozen=True)
class TokenVerifier:
    issuer: str
    jwks_url: str
    audience: str
    tenant_claim: str
    leeway_s: int = 30

    @cached_property
    def _jwks(self) -> jwt.PyJWKClient:
        return jwt.PyJWKClient(self.jwks_url, cache_keys=True, lifespan=600, timeout=5)

    def verify(self, token: str) -> VerifiedToken:
        try:
            key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key=key.key,
                algorithms=ALLOWED_ALGORITHMS,
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway_s,
                options={"require": ["exp", "iat", "iss", "sub", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise InvalidToken(type(exc).__name__) from None

        tenant = claims.get(self.tenant_claim)
        if not isinstance(tenant, str) or not tenant:
            raise InvalidToken(f"missing {self.tenant_claim} claim")
        return VerifiedToken(
            issuer=claims["iss"],
            subject=str(claims["sub"]),
            tenant_slug=tenant,
            email=claims.get("email"),
            name=claims.get("name"),
        )
