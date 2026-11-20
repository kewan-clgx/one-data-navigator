"""Signing key, public JWKS and per-user Collibra tokens."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from functools import cache
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from odn.config import settings

log = logging.getLogger(__name__)

ALGORITHM = "RS256"


class TokenUnavailable(Exception):
    """No Collibra token can be issued (key not configured, or the user's identity is not verified)."""


@dataclass(frozen=True)
class SigningKey:
    private: rsa.RSAPrivateKey
    kid: str

    def public_jwk(self) -> dict[str, Any]:
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key()))
        return {"kty": "RSA", "n": jwk["n"], "e": jwk["e"], "kid": self.kid, "use": "sig", "alg": ALGORITHM}


def _thumbprint(public: rsa.RSAPublicKey) -> str:
    """RFC 7638 JWK thumbprint, used as the key id so a rotated key gets a new kid."""
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(public))
    canonical = json.dumps({"e": jwk["e"], "kty": "RSA", "n": jwk["n"]}, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(hashlib.sha256(canonical.encode()).digest()).rstrip(b"=").decode()


def _load_pem() -> bytes | None:
    if settings.collibra_signing_key:
        return settings.collibra_signing_key.encode()
    if settings.collibra_signing_key_secret:
        from google.cloud import secretmanager

        return secretmanager.SecretManagerServiceClient().access_secret_version(
            name=settings.collibra_signing_key_secret
        ).payload.data
    return None


@cache
def signing_key() -> SigningKey:
    pem = _load_pem()
    if pem is None:
        if settings.env == "prod":
            raise TokenUnavailable("Collibra signing key is not configured")
        log.warning("collibra: no signing key configured; using an ephemeral development key")
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    else:
        loaded = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(loaded, rsa.RSAPrivateKey) or loaded.key_size < 2048:
            raise TokenUnavailable("Collibra signing key must be an RSA key of at least 2048 bits")
        private = loaded
    return SigningKey(private=private, kid=_thumbprint(private.public_key()))


def issuer() -> str:
    return settings.public_url.rstrip("/") or "http://localhost:3100"


def jwks() -> dict[str, Any]:
    """Public keys Collibra uses to verify our tokens (empty if no key is available)."""
    try:
        return {"keys": [signing_key().public_jwk()]}
    except TokenUnavailable:
        return {"keys": []}


def principal(email: str, principal_format: str) -> str:
    return email.split("@", 1)[0] if principal_format == "email_local_part" else email


def mint(*, email: str | None, email_domain_verified: bool, audience: str, principal_format: str) -> tuple[str, int]:
    """Sign a token for one user and one Collibra environment; returns (token, expires_at)."""
    if not email or not email_domain_verified:
        raise TokenUnavailable("Collibra needs a verified work email on one of your organization's domains")
    key = signing_key()
    now = int(time.time())
    expires = now + settings.collibra_token_ttl_s
    claims = {
        "iss": issuer(),
        "aud": audience,
        "sub": principal(email.lower(), principal_format),
        "email": email.lower(),
        "iat": now,
        "nbf": now - 5,
        "exp": expires,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(claims, key.private, algorithm=ALGORITHM, headers={"kid": key.kid, "typ": "JWT"})
    return token, expires
