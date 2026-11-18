import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from odn.tenancy.oidc import InvalidToken, TokenVerifier, TrustedIssuers, email_allowed, parse_email_allowlist

ISSUER = "http://localhost:8180/odn"
IDP_ISSUER = "https://securetoken.google.com/proj"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _FakeJwks:
    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK:
        return jwt.PyJWK.from_dict(
            {**jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True), "alg": "RS256"}
        )


def _with_fake_jwks(v: TokenVerifier) -> TokenVerifier:
    v.__dict__["_jwks"] = _FakeJwks()
    return v


def _verifier(claim: str = "tenant", required: bool = True, issuer: str = ISSUER, aud: str = "odn") -> TokenVerifier:
    return _with_fake_jwks(
        TokenVerifier(issuer=issuer, jwks_url="unused", audience=aud, tenant_claim=claim, tenant_claim_required=required)
    )


def _token(key: object = KEY, alg: str = "RS256", **overrides: object) -> str:
    now = int(time.time())
    claims = {"iss": ISSUER, "sub": "u1", "aud": "odn", "iat": now, "exp": now + 300, "tenant": "local"}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=alg)


def test_valid_token() -> None:
    t = _verifier().verify(_token(email="a@b.c", email_verified=True, name="A"))
    assert (t.subject, t.external_id, t.email, t.email_verified, t.name, t.issuer) == (
        "u1", "local", "a@b.c", True, "A", ISSUER,
    )


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


def test_google_hd_is_optional_and_raw() -> None:
    v = _verifier(claim="hd", required=False)
    assert v.verify(_token(tenant=None, hd="Cotality.com")).external_id == "Cotality.com"
    assert v.verify(_token(tenant=None)).external_id == ""


def test_nested_identity_platform_tenant_claim() -> None:
    v = _verifier(claim="firebase.tenant", issuer=IDP_ISSUER, aud="proj")
    token = _token(iss=IDP_ISSUER, aud="proj", tenant=None, firebase={"tenant": "indulge-x1", "sign_in_provider": "password"})
    assert v.verify(token).external_id == "indulge-x1"
    with pytest.raises(InvalidToken):
        v.verify(_token(iss=IDP_ISSUER, aud="proj", tenant=None, firebase={"sign_in_provider": "password"}))


def test_trusted_issuers_route_by_issuer_and_reject_unknown() -> None:
    issuers = TrustedIssuers(
        {ISSUER: _verifier(), IDP_ISSUER: _verifier(claim="firebase.tenant", issuer=IDP_ISSUER, aud="proj")}
    )
    assert issuers.verify(_token()).issuer == ISSUER
    assert issuers.verify(_token(iss=IDP_ISSUER, aud="proj", firebase={"tenant": "t1"})).external_id == "t1"
    for bad in (_token(iss="https://evil"), "not-a-jwt", _token(iss=IDP_ISSUER, aud="odn", firebase={"tenant": "t1"})):
        with pytest.raises(InvalidToken):
            issuers.verify(bad)


@pytest.mark.parametrize(
    ("email", "verified", "allowed"),
    [
        ("Pat@Gmail.com", True, True),
        ("x@friends.org", True, True),
        ("pat@gmail.com", False, False),
        (None, True, False),
        ("eve@gmail.com", True, False),
        ("pat@gmail.com.evil", True, False),
        ("x@sub.friends.org", True, False),
    ],
)
def test_fallback_email_allowlist(email: str | None, verified: bool, allowed: bool) -> None:
    assert email_allowed(email, verified, parse_email_allowlist("pat@gmail.com, @friends.org")) is allowed


def test_empty_allowlist_allows_anyone() -> None:
    assert email_allowed(None, False, frozenset()) is True


def test_parse_tenant_domains() -> None:
    from odn.tenancy.oidc import parse_tenant_domains

    assert parse_tenant_domains(" Cotality.com=cotality , a.org=a ") == {"cotality.com": "cotality", "a.org": "a"}
    assert parse_tenant_domains("") == {}
    with pytest.raises(ValueError):
        parse_tenant_domains("cotality.com")
