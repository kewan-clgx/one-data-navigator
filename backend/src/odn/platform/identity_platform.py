"""Google Identity Platform client for password-sign-in tenants (docs/Tenant_Isolation_User_system.md §3).

User-level calls use the restricted API key (`ODN_IDP_API_KEY`); admin calls (create IdP tenants, disable
accounts) use the runtime's Google credentials and need `roles/identitytoolkit.admin`. Error codes are
mapped to `SignInFailed` without saying whether the account exists.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

import httpx

from odn.config import settings

_ITK = "https://identitytoolkit.googleapis.com"
_STS = "https://securetoken.googleapis.com/v1/token"
_TIMEOUT = 15


class IdentityPlatformError(Exception):
    pass


class SignInFailed(IdentityPlatformError):
    """Wrong email/password, unknown or disabled account, or too many attempts (deliberately not distinguished)."""


@dataclass(frozen=True)
class Tokens:
    id_token: str
    refresh_token: str
    expires_in: int


def enabled() -> bool:
    return bool(settings.idp_project and settings.idp_api_key)


def _key_call(path: str, body: dict[str, Any]) -> dict[str, Any]:
    if not enabled():
        raise IdentityPlatformError("password sign-in is not configured")
    resp = httpx.post(f"{_ITK}/v1/{path}", params={"key": settings.idp_api_key}, json=body, timeout=_TIMEOUT)
    data = resp.json()
    if resp.status_code != 200:
        raise IdentityPlatformError(str(data.get("error", {}).get("message", resp.status_code)))
    return data


def sign_in(idp_tenant_id: str, email: str, password: str) -> Tokens:
    try:
        data = _key_call(
            "accounts:signInWithPassword",
            {"email": email, "password": password, "tenantId": idp_tenant_id, "returnSecureToken": True},
        )
    except IdentityPlatformError as exc:
        if str(exc) == "password sign-in is not configured":
            raise
        raise SignInFailed("sign-in failed") from None
    return Tokens(data["idToken"], data["refreshToken"], int(data["expiresIn"]))


def refresh(refresh_token: str) -> Tokens:
    if not enabled():
        raise IdentityPlatformError("password sign-in is not configured")
    resp = httpx.post(
        _STS,
        params={"key": settings.idp_api_key},
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=_TIMEOUT,
    )
    if resp.status_code != 200:
        raise SignInFailed("session expired")
    data = resp.json()
    return Tokens(data["id_token"], data["refresh_token"], int(data["expires_in"]))


class ResetLinkInvalid(IdentityPlatformError):
    """The emailed link is expired, already used or malformed."""


class WeakPassword(IdentityPlatformError):
    """The new password does not meet the project password policy (message lists what is missing)."""


def _reset_call(body: dict[str, Any]) -> dict[str, Any]:
    try:
        return _key_call("accounts:resetPassword", body)
    except IdentityPlatformError as exc:
        code = str(exc)
        if code.startswith(("EXPIRED_OOB_CODE", "INVALID_OOB_CODE", "USER_DISABLED", "USER_NOT_FOUND")):
            raise ResetLinkInvalid("this link has expired or was already used") from None
        if code.startswith(("PASSWORD_DOES_NOT_MEET_REQUIREMENTS", "WEAK_PASSWORD")):
            missing = code.partition("[")[2].rstrip("]").strip()
            raise WeakPassword("the password needs " + (missing.lower() if missing else "to be stronger")) from None
        raise


def check_reset_code(idp_tenant_id: str, oob_code: str) -> str:
    """Email the set/reset-password link belongs to (does not use up the code)."""
    return _reset_call({"oobCode": oob_code, "tenantId": idp_tenant_id})["email"]


def confirm_reset(idp_tenant_id: str, oob_code: str, new_password: str) -> str:
    """Set the password from an emailed link; returns the account email (now verified)."""
    return _reset_call({"oobCode": oob_code, "tenantId": idp_tenant_id, "newPassword": new_password})["email"]


def send_password_email(idp_tenant_id: str, email: str) -> None:
    """Identity Platform emails a set/reset-password link. Silent for unknown emails (no enumeration)."""
    try:
        _key_call("accounts:sendOobCode", {"requestType": "PASSWORD_RESET", "email": email, "tenantId": idp_tenant_id})
    except IdentityPlatformError as exc:
        if "EMAIL_NOT_FOUND" not in str(exc):
            raise


def invite(idp_tenant_id: str, email: str) -> None:
    """Create the IdP account (unusable random password) if needed, then email the set-password link.

    Setting the password through the link marks the email verified, which is what lets the invited user in."""
    try:
        _key_call(
            "accounts:signUp",
            {"email": email, "password": secrets.token_urlsafe(32) + "aA1", "tenantId": idp_tenant_id},
        )
    except IdentityPlatformError as exc:
        if "EMAIL_EXISTS" not in str(exc):
            raise
    send_password_email(idp_tenant_id, email)


# Admin calls ------------------------------------------------------------------------------------------


def _admin_headers() -> dict[str, str]:
    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    headers = {"Authorization": f"Bearer {creds.token}"}
    if getattr(creds, "quota_project_id", None):
        headers["x-goog-user-project"] = creds.quota_project_id
    return headers


def _admin_call(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    if not settings.idp_project:
        raise IdentityPlatformError("ODN_IDP_PROJECT is not set")
    resp = httpx.request(method, f"{_ITK}/{path}", headers=_admin_headers(), json=body, timeout=_TIMEOUT)
    data = resp.json() if resp.content else {}
    if resp.status_code != 200:
        raise IdentityPlatformError(str(data.get("error", {}).get("message", resp.status_code)))
    return data


def create_tenant(display_name: str) -> str:
    """Create an IdP tenant with email/password sign-in and enumeration protection; returns its id."""
    data = _admin_call(
        "POST",
        f"v2/projects/{settings.idp_project}/tenants",
        {"displayName": display_name[:20], "allowPasswordSignup": True, "enableEmailLinkSignin": False,
         "emailPrivacyConfig": {"enableImprovedEmailPrivacy": True}},
    )
    return data["name"].rsplit("/", 1)[1]


def set_disabled(idp_tenant_id: str, email: str, disabled: bool) -> bool:
    """Disable/enable the IdP account; False if it does not exist. Our users.status already blocks access."""
    base = f"v1/projects/{settings.idp_project}/tenants/{idp_tenant_id}"
    users = _admin_call("POST", f"{base}/accounts:lookup", {"email": [email]}).get("users", [])
    if not users:
        return False
    _admin_call("POST", f"{base}/accounts:update", {"localId": users[0]["localId"], "disableUser": disabled})
    return True
