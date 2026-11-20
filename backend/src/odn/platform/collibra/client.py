"""Read-only Collibra REST client acting as the signed-in user (bounded same-origin GETs, no mutation)."""

from __future__ import annotations

import asyncio
import html
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from odn.config import settings
from odn.platform.collibra import token as tokens

MatchMode = Literal["ANYWHERE", "START", "EXACT"]
_TAG = re.compile(r"<[^>]+>")
_MAX_TEXT = 2000
_DEFINITION_TYPES = ("definition", "description")
# Data-profiling statistics Collibra attaches to columns: useful for analysts, noise for definitions/mapping.
_PROFILING = re.compile(
    r"percentile|quartile|decile|^mean$|^median$|variance|standard deviation|minimum value|maximum value|"
    r"distinct values|row count|empty values|categorical data",
    re.IGNORECASE,
)


# Successful reads are cached briefly per user (key includes the Collibra user), never shared across users because
# Collibra permissions differ per user.
CACHE_TTL_S = 600
_CACHE_MAX = 2000
_cache: dict[tuple[str, ...], tuple[float, dict[str, Any]]] = {}


def clear_cache() -> None:
    _cache.clear()


def _cache_get(key: tuple[str, ...]) -> dict[str, Any] | None:
    hit = _cache.get(key)
    if hit is None:
        return None
    if hit[0] < time.monotonic():
        _cache.pop(key, None)
        return None
    return hit[1]


def _cache_put(key: tuple[str, ...], value: dict[str, Any]) -> None:
    if len(_cache) >= _CACHE_MAX:
        now = time.monotonic()
        for k in [k for k, (exp, _) in _cache.items() if exp < now] or list(_cache)[: _CACHE_MAX // 4]:
            _cache.pop(k, None)
    _cache[key] = (time.monotonic() + CACHE_TTL_S, value)


# Ranking: business-facing assets first, accepted before candidates, readable names before physical paths.
_TYPE_WEIGHT = {
    "business term": 40, "product field": 35, "data attribute": 30, "data element": 28, "report attribute": 25,
    "api field": 22, "field": 20, "column": 12, "table": 10, "database view": 10, "schema": 4,
}
_STATUS_WEIGHT = {"accepted": 15, "approved": 15, "reviewed": 8, "candidate": 2, "obsolete": -60, "deprecated": -60}
_TECHNICAL = re.compile(r"[>.\/]|\(column\)|\(table\)")


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", text.lower()) if w]


def rank_score(asset: dict[str, Any], query: str) -> float:
    """Higher is better. `asset` is a summary (name, display_name, type, status)."""
    label = (asset.get("display_name") or asset.get("name") or "").strip()
    name = (asset.get("name") or "").strip()
    q = " ".join(_words(query))
    lw = " ".join(_words(label))
    score = float(_TYPE_WEIGHT.get((asset.get("type") or "").lower(), 15))
    score += _STATUS_WEIGHT.get((asset.get("status") or "").lower(), 0)
    if q and lw == q:
        score += 40
    elif q and lw.startswith(q):
        score += 25
    elif q and q in lw:
        score += 15
    qw = set(_words(query))
    if qw:
        score += 15 * len(qw & set(_words(label))) / len(qw)
    if _TECHNICAL.search(name) and not asset.get("display_name"):
        score -= 15
    score -= min(len(label), 160) / 16
    return round(score, 2)


def rank(assets: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    return sorted(assets, key=lambda a: rank_score(a, query), reverse=True)


@dataclass(frozen=True)
class Environment:
    env: str
    base_url: str
    audience: str
    principal_format: str = "email"
    enabled: bool = True


class CollibraError(Exception):
    """A safe, user-facing error; `status` is the HTTP status our API returns."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def plain(value: Any) -> Any:
    """Collibra rich text (HTML) as bounded plain text; other values unchanged."""
    if not isinstance(value, str):
        return value
    text = html.unescape(_TAG.sub(" ", value))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + "…"


def _name(ref: Any) -> str | None:
    return ref.get("name") if isinstance(ref, dict) and isinstance(ref.get("name"), str) else None


class CollibraClient:
    def __init__(
        self,
        environment: Environment,
        *,
        email: str | None,
        email_domain_verified: bool,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.environment = environment
        self._email = email
        self._verified = email_domain_verified
        self._transport = transport
        self._token: tuple[str, int] | None = None

    # -- plumbing ------------------------------------------------------------------------------------------

    @property
    def principal(self) -> str | None:
        return tokens.principal(self._email.lower(), self.environment.principal_format) if self._email else None

    def _bearer(self) -> str:
        if self._token is None or self._token[1] - 30 < time.time():
            try:
                self._token = tokens.mint(
                    email=self._email,
                    email_domain_verified=self._verified,
                    audience=self.environment.audience,
                    principal_format=self.environment.principal_format,
                )
            except tokens.TokenUnavailable as exc:
                raise CollibraError(str(exc), 403) from None
        return self._token[0]

    def asset_url(self, asset_id: str) -> str:
        return f"{self.environment.base_url}/asset/{asset_id}"

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._email or not self._verified:
            self._bearer()  # raises the same 403 as an uncached call; the cache never bypasses identity checks
        key = (self.environment.base_url, self.environment.audience or "", self.principal or "", path,
               repr(sorted((params or {}).items())))
        cached = _cache_get(key)
        if cached is not None:
            return cached
        data = await self._fetch(path, params)
        _cache_put(key, data)
        return data

    async def _fetch(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.environment.base_url}/rest/2.0/{path}"
        try:
            async with httpx.AsyncClient(
                timeout=settings.collibra_timeout_s, transport=self._transport, follow_redirects=False
            ) as client:
                resp = await client.get(
                    url, params=params, headers={"Authorization": f"Bearer {self._bearer()}", "Accept": "application/json"}
                )
        except httpx.TimeoutException:
            raise CollibraError("Collibra did not respond in time; try again.", 504) from None
        except httpx.HTTPError:
            raise CollibraError("Collibra is not reachable.", 502) from None
        if resp.status_code == 401:
            raise CollibraError(
                f"Collibra ({self.environment.env}) did not accept your QueryMuse sign-in. Its admin must trust "
                f"QueryMuse's token issuer, and you need a Collibra user named {self.principal}.",
                403,
            )
        if resp.status_code == 403:
            raise CollibraError("Your Collibra account does not have permission to read this.", 403)
        if resp.status_code == 404:
            raise CollibraError("Not found in Collibra.", 404)
        if resp.status_code == 429:
            raise CollibraError("Collibra is rate-limiting requests; try again shortly.", 429)
        if resp.status_code != 200:
            raise CollibraError(f"Collibra returned an error ({resp.status_code}).", 502)
        try:
            data = resp.json()
        except ValueError:
            raise CollibraError("Collibra returned an invalid response.", 502) from None
        if not isinstance(data, dict):
            raise CollibraError("Collibra returned an invalid response.", 502)
        return data

    async def _page(self, resource: str, params: dict[str, Any]) -> dict[str, Any]:
        data = await self._get(resource, params)
        results, total, offset = data.get("results"), data.get("total"), data.get("offset", params["offset"])
        if not isinstance(results, list) or not isinstance(total, int) or not isinstance(offset, int):
            raise CollibraError("Collibra returned an invalid catalog page.", 502)
        results = results[: params["limit"]]
        consumed = offset + len(results)
        has_more = consumed < total and bool(results)
        return {
            "results": results,
            "total": total,
            "offset": offset,
            "limit": params["limit"],
            "has_more": has_more,
            "next_offset": consumed if has_more else None,
        }

    def _stamp(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"environment": self.environment.env, **payload, "retrieved_at": datetime.now(UTC).isoformat()}

    def _summary(self, asset: dict[str, Any]) -> dict[str, Any]:
        asset_id = asset.get("id")
        return {
            "id": asset_id,
            "name": asset.get("name"),
            "display_name": asset.get("displayName"),
            "type": _name(asset.get("type")),
            "domain": _name(asset.get("domain")),
            "status": _name(asset.get("status")),
            "url": self.asset_url(asset_id) if isinstance(asset_id, str) else None,
        }

    # -- operations ----------------------------------------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        """Whether Collibra accepts the user's QueryMuse identity (protected current-user resource)."""
        user = await self._get("users/current")
        if not isinstance(user.get("id"), str):
            raise CollibraError("Collibra returned an invalid user.", 502)
        return self._stamp({"authenticated": True, "username": user.get("userName") or self.principal})

    async def search_assets(
        self, text: str, *, match: MatchMode = "ANYWHERE", domain_id: str | None = None, limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"name": text, "nameMatchMode": match, "limit": limit, "offset": offset}
        if domain_id:
            params["domainId"] = domain_id
        page = await self._page("assets", params)
        page["results"] = rank([self._summary(a) for a in page["results"] if isinstance(a, dict)], text)
        return self._stamp(page)

    async def get_asset(self, asset_id: str) -> dict[str, Any]:
        asset = await self._get(f"assets/{asset_id}")
        if asset.get("id") != asset_id:
            raise CollibraError("Collibra returned an invalid asset.", 502)
        return self._stamp(self._summary(asset))

    async def get_attributes(self, asset_id: str, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        page = await self._page("attributes", {"assetId": asset_id, "limit": limit, "offset": offset})
        page["results"] = [
            {"type": _name(a.get("type")), "value": plain(a.get("value"))} for a in page["results"] if isinstance(a, dict)
        ]
        return self._stamp({"asset_id": asset_id, **page})

    async def get_relations(
        self, asset_id: str, *, direction: Literal["outgoing", "incoming"] = "outgoing", limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        key = "sourceId" if direction == "outgoing" else "targetId"
        page = await self._page("relations", {key: asset_id, "limit": limit, "offset": offset})

        def ref(r: Any) -> dict[str, Any] | None:
            if not isinstance(r, dict):
                return None
            return {"id": r.get("id"), "name": r.get("name") or r.get("displayName"),
                    "url": self.asset_url(r["id"]) if isinstance(r.get("id"), str) else None}

        page["results"] = [
            {"relation_type_id": (r.get("type") or {}).get("id") if isinstance(r.get("type"), dict) else None,
             "source": ref(r.get("source")), "target": ref(r.get("target"))}
            for r in page["results"] if isinstance(r, dict)
        ]
        return self._stamp({"asset_id": asset_id, "direction": direction, **page})

    async def _describe_one(self, asset_id: str) -> dict[str, Any]:
        try:
            summary = (await self.get_asset(asset_id))
            attrs = await self.get_attributes(asset_id, limit=100, offset=0)
        except CollibraError as exc:
            return {"id": asset_id, "error": str(exc)}
        profiling = [a for a in attrs["results"] if isinstance(a.get("type"), str) and _PROFILING.search(a["type"])]
        attributes = [a for a in attrs["results"] if a not in profiling]
        definition = next(
            (a["value"] for t in _DEFINITION_TYPES for a in attributes
             if isinstance(a.get("type"), str) and t in a["type"].lower() and a.get("value")),
            None,
        )
        summary.pop("environment", None)
        summary.pop("retrieved_at", None)
        return {**summary, "definition": definition, "attributes": attributes,
                "profiling_attributes_omitted": len(profiling), "attributes_truncated": attrs["has_more"]}

    async def describe_assets(self, asset_ids: list[str]) -> dict[str, Any]:
        sem = asyncio.Semaphore(4)

        async def one(asset_id: str) -> dict[str, Any]:
            async with sem:
                return await self._describe_one(asset_id)

        return self._stamp({"assets": await asyncio.gather(*(one(a) for a in asset_ids))})

    async def get_api_fields(self, api_asset_id: str, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        """Fields linked from an API/table asset (outgoing relations), each with its definition.

        Relations are catalog candidates, not proof of schema membership; nested containers are listed, not expanded.
        """
        page = await self.get_relations(api_asset_id, direction="outgoing", limit=limit, offset=offset)
        targets = [r["target"]["id"] for r in page["results"] if r.get("target") and isinstance(r["target"].get("id"), str)]
        described = (await self.describe_assets(targets))["assets"] if targets else []
        fields = [
            {k: d.get(k) for k in ("id", "name", "display_name", "type", "domain", "status", "definition", "url", "error")
             if d.get(k) is not None}
            for d in described
        ]
        return self._stamp({
            "api_asset_id": api_asset_id, "fields": fields, "total": page["total"], "offset": page["offset"],
            "has_more": page["has_more"], "next_offset": page["next_offset"],
        })

    async def lookup(self, text: str, *, top: int = 5, related: int = 3) -> dict[str, Any]:
        """One call for the agent: search, rank, describe the best matches and list their related assets."""
        top = max(1, min(top, 10))
        found = await self.search_assets(text, limit=25)
        ranked = found["results"]
        best = ranked[:top]
        described = {d["id"]: d for d in (await self.describe_assets([a["id"] for a in best]))["assets"]} if best else {}

        async def links(asset_id: str) -> list[dict[str, Any]]:
            try:
                out = await self.get_relations(asset_id, direction="outgoing", limit=10)
                inc = await self.get_relations(asset_id, direction="incoming", limit=10)
            except CollibraError:
                return []
            refs = [{"direction": "outgoing", **r["target"]} for r in out["results"] if r.get("target")]
            refs += [{"direction": "incoming", **r["source"]} for r in inc["results"] if r.get("source")]
            return refs[:15]

        rel = await asyncio.gather(*(links(a["id"]) for a in best[:related]))
        matches = []
        for i, a in enumerate(best):
            d = described.get(a["id"], {})
            attrs = [x for x in d.get("attributes", []) if (x.get("type") or "").lower() not in ("definition", "description")]
            match = {**a, "definition": d.get("definition"), "attributes": attrs[:8], "score": rank_score(a, text)}
            if d.get("error"):
                match["error"] = d["error"]
            if i < related:
                match["related"] = rel[i]
            matches.append(match)
        others = [{k: a.get(k) for k in ("id", "name", "display_name", "type", "status")} for a in ranked[top:]]
        return self._stamp({
            "query": text, "total": found["total"], "matches": matches, "other_results": others,
            "note": "Ranked: business terms / product fields / accepted assets and readable names first; score is a heuristic.",
        })
