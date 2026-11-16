"""Verify OIDC ID/access tokens against the issuer's JWKS."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
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
    # Optional claim value -> tenant slug map (e.g. Google "hd" domain "cotality.com" -> "cotality").
    # When set, the claim is looked up here instead of being used as the slug directly.
    tenant_domains: Mapping[str, str] = field(default_factory=dict)
    # Tenant for identities whose claim is missing or unmapped (e.g. personal Google accounts).
    default_tenant: str = ""
    # Verified emails (or "@domain" suffixes) allowed into the default tenant; empty = any.
    default_tenant_emails: frozenset[str] = frozenset()
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

        return VerifiedToken(
            issuer=claims["iss"],
            subject=str(claims["sub"]),
            tenant_slug=self._tenant_slug(claims),
            email=claims.get("email"),
            name=claims.get("name"),
        )

    def _tenant_slug(self, claims: Mapping[str, object]) -> str:
        value = claims.get(self.tenant_claim)
        claim = value if isinstance(value, str) and value else None
        if not self.tenant_domains and not self.default_tenant:
            if claim is None:
                raise InvalidToken(f"missing {self.tenant_claim} claim")
            return claim

        mapped = self.tenant_domains.get(claim.lower()) if claim else None
        if mapped:
            return mapped
        if not self.default_tenant:
            raise InvalidToken(f"{self.tenant_claim} claim does not map to a tenant")
        if self.default_tenant_emails and not self._email_allowed(claims):
            raise InvalidToken("email is not allowed")
        return self.default_tenant

    def _email_allowed(self, claims: Mapping[str, object]) -> bool:
        email = claims.get("email")
        if claims.get("email_verified") is not True or not isinstance(email, str) or "@" not in email:
            return False
        email = email.lower()
        return email in self.default_tenant_emails or "@" + email.split("@", 1)[1] in self.default_tenant_emails


def parse_tenant_domains(raw: str) -> dict[str, str]:
    """"cotality.com=cotality, example.org=example" -> {"cotality.com": "cotality", ...}."""
    pairs: dict[str, str] = {}
    for item in filter(None, (p.strip() for p in raw.split(","))):
        domain, sep, slug = item.partition("=")
        if not sep or not domain.strip() or not slug.strip():
            raise ValueError(f"invalid tenant domain mapping: {item!r} (expected domain=slug)")
        pairs[domain.strip().lower()] = slug.strip()
    return pairs


def parse_email_allowlist(raw: str) -> frozenset[str]:
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


def verifier_from_settings() -> TokenVerifier:
    from odn.config import settings

    return TokenVerifier(
        issuer=settings.oidc_issuer,
        jwks_url=settings.oidc_jwks_url,
        audience=settings.oidc_audience,
        tenant_claim=settings.oidc_tenant_claim,
        tenant_domains=parse_tenant_domains(settings.oidc_tenant_domains),
        default_tenant=settings.oidc_default_tenant,
        default_tenant_emails=parse_email_allowlist(settings.oidc_default_tenant_emails),
    )
