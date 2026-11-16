import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from odn.tenancy.oidc import InvalidToken, TokenVerifier

ISSUER = "http://localhost:8180/odn"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _FakeJwks:
    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK:
        return jwt.PyJWK.from_dict(
            {**jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True), "alg": "RS256"}
        )


def _verifier() -> TokenVerifier:
    v = TokenVerifier(issuer=ISSUER, jwks_url="unused", audience="odn", tenant_claim="tenant")
    v.__dict__["_jwks"] = _FakeJwks()
    return v


def _token(key: object = KEY, alg: str = "RS256", **overrides: object) -> str:
    now = int(time.time())
    claims = {"iss": ISSUER, "sub": "u1", "aud": "odn", "iat": now, "exp": now + 300, "tenant": "local"}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=alg)


def test_valid_token() -> None:
    t = _verifier().verify(_token(email="a@b.c"))
    assert (t.subject, t.tenant_slug, t.email, t.issuer) == ("u1", "local", "a@b.c", ISSUER)


@pytest.mark.parametrize(
    "token",
    [
        _token(key=OTHER_KEY),
        _token(iss="http://evil/odn"),
        _token(aud="other"),
        _token(exp=int(time.time()) - 3600),
        _token(exp=None),
        _token(tenant=None),
        _token(tenant=""),
        jwt.encode({"iss": ISSUER, "sub": "u1", "aud": "odn", "tenant": "local"}, key=None, algorithm="none"),
        jwt.encode({"iss": ISSUER, "sub": "u1", "aud": "odn", "tenant": "local"}, "secret", algorithm="HS256"),
        "not-a-jwt",
    ],
)
def test_rejected_tokens(token: str) -> None:
    with pytest.raises(InvalidToken):
        _verifier().verify(token)
