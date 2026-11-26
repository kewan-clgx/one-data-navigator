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
from typing import Literal

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


async def _list_models_async(token: str) -> list[str]:
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
        models = await client.list_models()
        return sorted(str(getattr(m, "id", None) or getattr(m, "name", m)) for m in models)
    finally:
        with contextlib.suppress(Exception):
            await client.stop()
        shutil.rmtree(home, ignore_errors=True)


def list_models(token: str) -> list[str]:
    """The models this user's Copilot seat can use (raises if the token has no Copilot access)."""
    return asyncio.run(asyncio.wait_for(_list_models_async(token), timeout=45))
