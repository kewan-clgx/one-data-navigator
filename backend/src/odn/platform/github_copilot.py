"""GitHub Copilot as a user's AI model: GitHub device-flow sign-in and the models their Copilot seat allows.

The user signs in through EasyQuery's GitHub OAuth App (Device Flow, like DBeaver or the GitHub CLI). The resulting
token is stored like any bring-your-own key (odn.platform.llm_keys) and only ever sent to GitHub. Model calls go
through the official GitHub Copilot SDK (odn.agent.copilot_llm), never undocumented Copilot endpoints.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import tempfile
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from odn.config import settings

_GITHUB = "https://github.com"
_GITHUB_API = "https://api.github.com"
_TIMEOUT_S = 10
# Reads the user's login only; Copilot entitlement comes from their seat, not from token scopes.
SCOPE = "read:user"


class GitHubUnavailable(Exception):
    """Sign in with GitHub isn't configured (no ODN_GITHUB_CLIENT_ID) or GitHub can't be reached."""


class DeviceCode(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


class DevicePoll(BaseModel):
    status: Literal["pending", "slow_down", "expired", "denied", "connected"]
    interval: int | None = None
    login: str | None = None
    token: str | None = None


def enabled() -> bool:
    return bool(settings.github_client_id)


def _client_id() -> str:
    if not settings.github_client_id:
        raise GitHubUnavailable("Sign in with GitHub isn't set up on this server")
    return settings.github_client_id


def start_device_flow() -> DeviceCode:
    try:
        resp = httpx.post(
            f"{_GITHUB}/login/device/code",
            data={"client_id": _client_id(), "scope": SCOPE},
            headers={"Accept": "application/json"},
            timeout=_TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        raise GitHubUnavailable(f"could not reach GitHub ({type(exc).__name__})") from None
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code != 200 or "device_code" not in body:
        raise GitHubUnavailable(body.get("error_description") or f"GitHub returned HTTP {resp.status_code}")
    return DeviceCode.model_validate(body)


def poll_device_flow(device_code: str) -> DevicePoll:
    """One poll of GitHub's token endpoint; the browser calls this at GitHub's interval until it's not pending."""
    try:
        resp = httpx.post(
            f"{_GITHUB}/login/oauth/access_token",
            data={
                "client_id": _client_id(),
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
            timeout=_TIMEOUT_S,
        )
        body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise GitHubUnavailable(f"could not reach GitHub ({type(exc).__name__})") from None
    token = body.get("access_token")
    if token:
        return DevicePoll(status="connected", token=token, login=github_login(token))
    error = body.get("error")
    if error == "authorization_pending":
        return DevicePoll(status="pending")
    if error == "slow_down":
        return DevicePoll(status="slow_down", interval=int(body.get("interval") or 10))
    if error == "access_denied":
        return DevicePoll(status="denied")
    if error in ("expired_token", "incorrect_device_code", "token_expired"):
        return DevicePoll(status="expired")
    raise GitHubUnavailable(body.get("error_description") or error or f"GitHub returned HTTP {resp.status_code}")


def github_login(token: str) -> str | None:
    try:
        resp = httpx.get(
            f"{_GITHUB_API}/user",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=_TIMEOUT_S,
        )
    except httpx.HTTPError:
        return None
    return resp.json().get("login") if resp.status_code == 200 else None


class CopilotModel(BaseModel):
    id: str
    name: str | None = None
    # GitHub's own grouping: "low" | "medium" | "high" (None when not reported, e.g. "auto").
    price_category: str | None = None
    # AI credits per `batch_size` tokens, as GitHub reports them (read at runtime; prices change).
    input_price: float | None = None
    output_price: float | None = None
    cache_read_price: float | None = None
    batch_size: int | None = None


class CopilotQuota(BaseModel):
    """The user's monthly premium allowance ("premium_interactions"), as GitHub reports it."""

    used: float | None = None
    entitlement: float | None = None
    remaining_percentage: float | None = None
    unlimited: bool = False
    overage_allowed: bool = False
    reset_date: str | None = None


class CopilotOverview(BaseModel):
    models: list[CopilotModel]
    quota: CopilotQuota | None = None


def _model_info(m: Any) -> CopilotModel:
    prices = getattr(getattr(m, "billing", None), "token_prices", None)
    category = getattr(m, "model_picker_price_category", None)
    return CopilotModel(
        id=str(getattr(m, "id", None) or getattr(m, "name", m)),
        name=getattr(m, "name", None),
        price_category=(getattr(category, "value", None) or str(category)).lower() if category else None,
        input_price=getattr(prices, "input_price", None),
        output_price=getattr(prices, "output_price", None),
        cache_read_price=getattr(prices, "cache_read_price", None),
        batch_size=getattr(prices, "batch_size", None),
    )


def recommended_model(models: list[CopilotModel]) -> str | None:
    """The strongest low-cost model: the most capable (priciest) one GitHub lists as "low" cost."""
    low = [m for m in models if m.price_category == "low" and m.input_price is not None]
    if low:
        return max(low, key=lambda m: (m.input_price or 0, m.output_price or 0)).id
    priced = [m for m in models if m.input_price is not None]
    return min(priced, key=lambda m: m.input_price or 0).id if priced else None


async def _overview_async(token: str, with_quota: bool) -> CopilotOverview:
    from copilot import ClientGitHubOptions, CopilotClient, RuntimeConnection

    # A throwaway runtime home: listing models keeps no state, and no other user's data is ever in reach.
    home = tempfile.mkdtemp(prefix="odn-copilot-models-")
    client = CopilotClient(
        mode="empty",
        runtime=RuntimeConnection.for_spawned_stdio(base_directory=home),
        github=ClientGitHubOptions(token=token, use_logged_in_user=False),
    )
    try:
        await client.start()
        models = sorted((_model_info(m) for m in await client.list_models()), key=lambda m: m.id)
        quota = await _quota(client, token) if with_quota else None
        return CopilotOverview(models=models, quota=quota)
    finally:
        with contextlib.suppress(Exception):
            await client.stop()
        shutil.rmtree(home, ignore_errors=True)


async def _quota(client: Any, token: str) -> CopilotQuota | None:
    from copilot.rpc import AccountGetQuotaRequest

    try:
        result = await client.rpc.account.get_quota(AccountGetQuotaRequest(git_hub_token=token))
    except Exception:  # noqa: BLE001 - quota is informational; never block the model list on it
        return None
    snap = (getattr(result, "quota_snapshots", None) or {}).get("premium_interactions")
    if snap is None:
        return None
    return CopilotQuota(
        used=getattr(snap, "used_requests", None),
        entitlement=getattr(snap, "entitlement_requests", None),
        remaining_percentage=getattr(snap, "remaining_percentage", None),
        unlimited=bool(getattr(snap, "is_unlimited_entitlement", False)),
        overage_allowed=bool(getattr(snap, "overage_allowed_with_exhausted_quota", False)),
        reset_date=getattr(snap, "reset_date", None),
    )


def overview(token: str, with_quota: bool = True) -> CopilotOverview:
    """Models (with prices) this user's Copilot seat can use, and their allowance. Raises without Copilot access."""
    return asyncio.run(asyncio.wait_for(_overview_async(token, with_quota), timeout=45))


def list_models(token: str) -> list[str]:
    return [m.id for m in overview(token, with_quota=False).models]
