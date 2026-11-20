"""Collibra option A: QueryMuse-signed per-user tokens, public JWKS, and the read-only REST client."""

import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from odn.config import settings
from odn.platform.collibra import client as collibra_client
from odn.platform.collibra import token as tokens
from odn.platform.collibra.client import CollibraClient, CollibraError, Environment, rank

ASSET = "0192aaaa-0000-7000-8000-000000000001"
FIELD = "0192aaaa-0000-7000-8000-000000000002"
ENV = Environment(env="prd", base_url="https://catalog.example.com", audience="querymuse-collibra")


@pytest.fixture(autouse=True)
def fresh_key(monkeypatch: pytest.MonkeyPatch):
    collibra_client.clear_cache()
    monkeypatch.setattr(settings, "public_url", "https://app.example.com")
    monkeypatch.setattr(settings, "collibra_signing_key", "")
    monkeypatch.setattr(settings, "collibra_signing_key_secret", "")
    tokens.signing_key.cache_clear()
    yield
    tokens.signing_key.cache_clear()


def verify(token: str, audience: str = ENV.audience) -> dict:
    """What Collibra does: check the signature against our JWKS, issuer, audience and expiry."""
    keys = {k["kid"]: jwt.PyJWK(k) for k in tokens.jwks()["keys"]}
    kid = jwt.get_unverified_header(token)["kid"]
    return jwt.decode(token, keys[kid].key, algorithms=["RS256"], audience=audience, issuer="https://app.example.com")


def test_token_verifies_against_published_jwks() -> None:
    token, expires = tokens.mint(
        email="Ann@Acme.com", email_domain_verified=True, audience=ENV.audience, principal_format="email"
    )
    claims = verify(token)
    assert claims["sub"] == "ann@acme.com" and claims["email"] == "ann@acme.com"
    assert claims["exp"] == expires and 0 < expires - time.time() <= settings.collibra_token_ttl_s
    with pytest.raises(jwt.InvalidAudienceError):
        verify(token, audience="another-system")


def test_local_part_principal() -> None:
    token, _ = tokens.mint(email="ann@acme.com", email_domain_verified=True, audience="a", principal_format="email_local_part")
    assert verify(token, audience="a")["sub"] == "ann"


@pytest.mark.parametrize("email,verified", [(None, True), ("ann@acme.com", False)])
def test_no_token_without_a_verified_work_email(email, verified) -> None:
    with pytest.raises(tokens.TokenUnavailable):
        tokens.mint(email=email, email_domain_verified=verified, audience="a", principal_format="email")


def test_configured_key_has_a_stable_kid_and_prod_requires_one(monkeypatch: pytest.MonkeyPatch) -> None:
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    monkeypatch.setattr(settings, "collibra_signing_key", pem)
    kid = tokens.jwks()["keys"][0]["kid"]
    tokens.signing_key.cache_clear()
    assert tokens.jwks()["keys"][0]["kid"] == kid
    assert "d" not in tokens.jwks()["keys"][0]  # public part only

    monkeypatch.setattr(settings, "collibra_signing_key", "")
    monkeypatch.setattr(settings, "env", "prod")
    tokens.signing_key.cache_clear()
    assert tokens.jwks() == {"keys": []}
    with pytest.raises(tokens.TokenUnavailable):
        tokens.mint(email="ann@acme.com", email_domain_verified=True, audience="a", principal_format="email")


def fake_collibra(users: set[str], seen: list[str] | None = None, audience: str = ENV.audience) -> httpx.MockTransport:
    """A Collibra environment that trusts QueryMuse's JWKS and knows `users`."""

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        try:
            sub = verify(auth.removeprefix("Bearer "), audience)["sub"]
        except jwt.PyJWTError:
            return httpx.Response(401, json={"errorCode": "invalidToken"})
        if sub not in users:
            return httpx.Response(401, json={"errorCode": "userNotFound"})
        path, q = request.url.path, request.url.params
        if seen is not None:
            seen.append(f"{path}?{q}")
        if path == "/rest/2.0/users/current":
            return httpx.Response(200, json={"id": "u1", "userName": sub})
        if path == "/rest/2.0/assets":
            assert q["nameMatchMode"] == "ANYWHERE" and q["limit"] in ("2", "25")
            return httpx.Response(200, json={"total": 3, "offset": 0, "limit": 2, "results": [
                {"id": ASSET, "name": "Corporate Revenue", "type": {"name": "Business Term"},
                 "domain": {"name": "Firmographics"}, "status": {"name": "Accepted"}},
                {"id": FIELD, "name": "corp_revenue_amt", "type": {"name": "Column"}},
            ]})
        if path == f"/rest/2.0/assets/{ASSET}":
            return httpx.Response(200, json={"id": ASSET, "name": "Corporate Revenue", "type": {"name": "Business Term"}})
        if path == f"/rest/2.0/assets/{FIELD}":
            return httpx.Response(200, json={"id": FIELD, "name": "corp_revenue_amt", "type": {"name": "Column"}})
        if path == "/rest/2.0/attributes":
            value = "<p>Total annual revenue &amp; sales of the <b>corporate</b> entity.</p>"
            return httpx.Response(200, json={"total": 3, "offset": 0, "limit": 100, "results": [
                {"type": {"name": "Definition"}, "value": value}, {"type": {"name": "95th Percentile"}, "value": "6.6E6"},
                {"type": {"name": "Technical Data Type"}, "value": "INT64"}]})
        if path == "/rest/2.0/relations":
            if q.get("sourceId") != ASSET:
                return httpx.Response(200, json={"total": 0, "offset": 0, "limit": 20, "results": []})
            return httpx.Response(200, json={"total": 1, "offset": 0, "limit": 20, "results": [
                {"type": {"id": "rt1"}, "source": {"id": ASSET, "name": "Corporate Revenue"},
                 "target": {"id": FIELD, "name": "corp_revenue_amt"}}]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def client(email: str = "ann@acme.com", users: set[str] | None = None, **kw) -> CollibraClient:
    return CollibraClient(ENV, email=email, email_domain_verified=True,
                          transport=fake_collibra({"ann@acme.com"} if users is None else users, **kw))


async def test_calls_run_as_the_user_and_are_summarised() -> None:
    c = client()
    assert (await c.status())["username"] == "ann@acme.com"
    page = await c.search_assets("revenue", limit=2)
    assert page["results"][0] == {
        "id": ASSET, "name": "Corporate Revenue", "display_name": None, "type": "Business Term",
        "domain": "Firmographics", "status": "Accepted", "url": f"https://catalog.example.com/asset/{ASSET}",
    }
    assert page["has_more"] and page["next_offset"] == 2 and page["environment"] == "prd"

    described = (await c.describe_assets([ASSET]))["assets"][0]
    assert described["definition"] == "Total annual revenue & sales of the corporate entity."
    assert [a["type"] for a in described["attributes"]] == ["Definition", "Technical Data Type"]
    assert described["profiling_attributes_omitted"] == 1

    fields = await c.get_api_fields(ASSET)
    assert [f["name"] for f in fields["fields"]] == ["corp_revenue_amt"] and not fields["has_more"]


async def test_unknown_collibra_user_gets_a_clear_message() -> None:
    with pytest.raises(CollibraError) as err:
        await client(users=set()).status()
    assert err.value.status == 403 and "Collibra user named ann@acme.com" in str(err.value)


async def test_unverified_identity_never_reaches_collibra() -> None:
    seen: list[str] = []
    c = CollibraClient(ENV, email="ann@acme.com", email_domain_verified=False,
                       transport=fake_collibra({"ann@acme.com"}, seen))
    with pytest.raises(CollibraError) as err:
        await c.search_assets("revenue")
    assert err.value.status == 403 and seen == []


async def test_invalid_page_and_timeouts_are_reported() -> None:
    bad = httpx.MockTransport(lambda r: httpx.Response(200, json={"results": "nope"}))
    c = CollibraClient(ENV, email="ann@acme.com", email_domain_verified=True, transport=bad)
    with pytest.raises(CollibraError, match="invalid catalog page"):
        await c.search_assets("x")

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    c = CollibraClient(ENV, email="ann@acme.com", email_domain_verified=True, transport=httpx.MockTransport(slow))
    with pytest.raises(CollibraError) as err:
        await c.get_asset(ASSET)
    assert err.value.status == 504


def test_rank_prefers_business_terms_accepted_and_readable_names() -> None:
    assets = [
        {"id": "1", "name": "clgx.prd.edr.firmographics.vw_enterprise>corporate_revenue_sales_amount(column)",
         "display_name": None, "type": "Column", "status": "Candidate"},
        {"id": "2", "name": "Corporate Revenue", "display_name": None, "type": "Business Term", "status": "Accepted"},
        {"id": "3", "name": "CORPORATE SALES REVENUE", "display_name": None, "type": "Product Field", "status": "Candidate"},
        {"id": "4", "name": "Corporate Revenue", "display_name": None, "type": "Business Term", "status": "Obsolete"},
    ]
    assert [a["id"] for a in rank(assets, "corporate revenue")] == ["2", "3", "4", "1"]


async def test_lookup_is_one_call_with_definitions_and_related() -> None:
    page = await client().lookup("revenue", top=2)
    assert [m["name"] for m in page["matches"]] == ["Corporate Revenue", "corp_revenue_amt"]
    first = page["matches"][0]
    assert first["definition"].startswith("Total annual revenue")
    assert first["related"] == [{"direction": "outgoing", "id": FIELD, "name": "corp_revenue_amt",
                                 "url": f"https://catalog.example.com/asset/{FIELD}"}]
    assert all(a["type"] != "95th Percentile" for a in first["attributes"])


async def test_reads_are_cached_per_user_and_identity_is_still_checked() -> None:
    seen: list[str] = []
    ann = CollibraClient(ENV, email="ann@acme.com", email_domain_verified=True,
                         transport=fake_collibra({"ann@acme.com", "bob@acme.com"}, seen))
    await ann.get_asset(ASSET)
    await ann.get_asset(ASSET)
    assert len(seen) == 1  # second read from cache
    bob = CollibraClient(ENV, email="bob@acme.com", email_domain_verified=True,
                         transport=fake_collibra({"ann@acme.com", "bob@acme.com"}, seen))
    await bob.get_asset(ASSET)
    assert len(seen) == 2  # not shared across users
    unverified = CollibraClient(ENV, email="ann@acme.com", email_domain_verified=False,
                                transport=fake_collibra({"ann@acme.com"}, seen))
    with pytest.raises(CollibraError):
        await unverified.get_asset(ASSET)  # cached for ann, but an unverified identity never gets it
