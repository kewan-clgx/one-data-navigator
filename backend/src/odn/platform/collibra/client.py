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
        page["results"] = [self._summary(a) for a in page["results"] if isinstance(a, dict)]
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
        attributes = attrs["results"]
        definition = next(
            (a["value"] for t in _DEFINITION_TYPES for a in attributes
             if isinstance(a.get("type"), str) and t in a["type"].lower() and a.get("value")),
            None,
        )
        summary.pop("environment", None)
        summary.pop("retrieved_at", None)
        return {**summary, "definition": definition, "attributes": attributes,
                "attributes_truncated": attrs["has_more"]}

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
            {k: d.get(k) for k in ("id", "name", "type", "domain", "status", "definition", "url", "error") if d.get(k) is not None}
            for d in described
        ]
        return self._stamp({
            "api_asset_id": api_asset_id, "fields": fields, "total": page["total"], "offset": page["offset"],
            "has_more": page["has_more"], "next_offset": page["next_offset"],
        })
