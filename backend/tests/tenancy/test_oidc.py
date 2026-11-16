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


def _google_verifier(emails: frozenset[str] = frozenset()) -> TokenVerifier:
    v = TokenVerifier(
        issuer=ISSUER, jwks_url="unused", audience="odn", tenant_claim="hd",
        tenant_domains={"cotality.com": "cotality"}, default_tenant="datahop", default_tenant_emails=emails,
    )
    v.__dict__["_jwks"] = _FakeJwks()
    return v


@pytest.mark.parametrize(
    ("claims", "slug"),
    [
        ({"hd": "cotality.com"}, "cotality"),
        ({"hd": "Cotality.COM"}, "cotality"),
        ({}, "datahop"),
        ({"hd": "other.org"}, "datahop"),
    ],
)
def test_domain_mapping_and_default_tenant(claims: dict[str, str], slug: str) -> None:
    assert _google_verifier().verify(_token(tenant=None, **claims)).tenant_slug == slug


def test_without_default_tenant_unmapped_domain_is_rejected() -> None:
    v = _google_verifier()
    object.__setattr__(v, "default_tenant", "")
    with pytest.raises(InvalidToken):
        v.verify(_token(tenant=None, hd="other.org"))


@pytest.mark.parametrize(
    ("claims", "allowed"),
    [
        ({"email": "Pat@Gmail.com", "email_verified": True}, True),
        ({"email": "x@friends.org", "email_verified": True}, True),
        ({"email": "pat@gmail.com", "email_verified": False}, False),
        ({"email": "pat@gmail.com"}, False),
        ({"email": "eve@gmail.com", "email_verified": True}, False),
        ({"email": "pat@gmail.com.evil", "email_verified": True}, False),
        ({"email": "x@sub.friends.org", "email_verified": True}, False),
        # Mapped corporate domains are not subject to the default-tenant allowlist.
        ({"hd": "cotality.com", "email": "a@cotality.com", "email_verified": True}, True),
    ],
)
def test_default_tenant_email_allowlist(claims: dict[str, object], allowed: bool) -> None:
    v = _google_verifier(frozenset({"pat@gmail.com", "@friends.org"}))
    token = _token(tenant=None, **claims)
    if allowed:
        v.verify(token)
    else:
        with pytest.raises(InvalidToken):
            v.verify(token)


def test_parse_tenant_domains() -> None:
    from odn.tenancy.oidc import parse_tenant_domains

    assert parse_tenant_domains(" Cotality.com=cotality , a.org=a ") == {"cotality.com": "cotality", "a.org": "a"}
    assert parse_tenant_domains("") == {}
    with pytest.raises(ValueError):
        parse_tenant_domains("cotality.com")
