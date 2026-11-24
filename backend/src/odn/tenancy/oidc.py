"""Verify OIDC ID tokens from the trusted issuers (Google, Identity Platform, local mock IdP) against their JWKS.

Verification only proves who signed the token and extracts the raw tenant claim (`external_id`); which tenant
that is, and whether the user may enter it, is decided from the tenant directory (odn.tenancy.context).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property

import jwt

# Asymmetric only: never accept "none" or shared-secret algorithms.
ALLOWED_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]

IDP_JWKS_URL = "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"

# Live-demo tokens, signed by the API itself (odn.platform.demo) with the key behind /.well-known/jwks.json.
DEMO_ISSUER = "urn:querymuse:demo"
DEMO_AUDIENCE = "odn-demo"


class InvalidToken(Exception):
    pass


@dataclass(frozen=True)
class VerifiedToken:
    issuer: str
    subject: str
    external_id: str  # raw tenant claim: Google hd domain, Identity Platform tenant id, mock tenant slug; "" if absent
    email: str | None
    email_verified: bool
    name: str | None


@dataclass(frozen=True)
class TokenVerifier:
    """One trusted issuer. `tenant_claim` may be a dotted path (Identity Platform: "firebase.tenant")."""

    issuer: str
    jwks_url: str
    audience: str
    tenant_claim: str
    # A token without the tenant claim is invalid unless the issuer has a fallback tenant (Google personal accounts).
    tenant_claim_required: bool = True
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

        external_id = _claim(claims, self.tenant_claim)
        if not external_id and self.tenant_claim_required:
            raise InvalidToken(f"missing {self.tenant_claim} claim")
        email = claims.get("email")
        name = claims.get("name")
        return VerifiedToken(
            issuer=claims["iss"],
            subject=str(claims["sub"]),
            external_id=external_id,
            email=email if isinstance(email, str) and email else None,
            email_verified=claims.get("email_verified") is True,
            name=name if isinstance(name, str) and name else None,
        )


def _claim(claims: Mapping[str, object], path: str) -> str:
    value: object = claims
    for part in path.split("."):
        value = value.get(part) if isinstance(value, Mapping) else None
    return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class TrustedIssuers:
    """Routes a token to its issuer's verifier by the (unverified) `iss` claim; the verifier re-checks it."""

    verifiers: Mapping[str, TokenVerifier] = field(default_factory=dict)

    def verify(self, token: str) -> VerifiedToken:
        try:
            issuer = jwt.decode(token, options={"verify_signature": False}).get("iss")
        except jwt.PyJWTError as exc:
            raise InvalidToken(type(exc).__name__) from None
        verifier = self.verifiers.get(issuer) if isinstance(issuer, str) else None
        if verifier is None:
            raise InvalidToken("untrusted issuer")
        return verifier.verify(token)


def parse_email_allowlist(raw: str) -> frozenset[str]:
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


def email_allowed(email: str | None, email_verified: bool, allowlist: frozenset[str]) -> bool:
    """Empty allowlist = anyone; else a verified email listed exactly or by "@domain"."""
    if not allowlist:
        return True
    if not email_verified or not email or "@" not in email:
        return False
    email = email.lower()
    return email in allowlist or "@" + email.split("@", 1)[1] in allowlist


def verifier_from_settings() -> TrustedIssuers:
    from odn.config import settings

    verifiers = {
        settings.oidc_issuer: TokenVerifier(
            issuer=settings.oidc_issuer,
            jwks_url=settings.oidc_jwks_url,
            audience=settings.oidc_audience,
            tenant_claim=settings.oidc_tenant_claim,
            # Google: personal accounts have no hd claim and fall back to the issuer's '*' tenant.
            tenant_claim_required=settings.oidc_tenant_claim != "hd",
        )
    }
    if settings.idp_project:
        issuer = f"https://securetoken.google.com/{settings.idp_project}"
        verifiers[issuer] = TokenVerifier(
            issuer=issuer, jwks_url=IDP_JWKS_URL, audience=settings.idp_project, tenant_claim="firebase.tenant"
        )
    if settings.demo_tenant:
        verifiers[DEMO_ISSUER] = TokenVerifier(
            issuer=DEMO_ISSUER, jwks_url=f"{settings.api_url.rstrip('/')}/.well-known/jwks.json",
            audience=DEMO_AUDIENCE, tenant_claim="tenant",
        )
    return TrustedIssuers(verifiers)
