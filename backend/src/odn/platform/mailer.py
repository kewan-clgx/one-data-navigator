"""Transactional email through Resend (https://resend.com/docs/api-reference/emails/send-email).

The API key comes from `ODN_RESEND_API_KEY` or, in production, Secret Manager (`ODN_RESEND_API_KEY_SECRET`,
read with the runtime's Google credentials). Disabled unless a key source and `ODN_PUBLIC_URL` are set;
callers then fall back to Identity Platform's own emails.
"""

from __future__ import annotations

import logging

import httpx

from odn.config import settings

log = logging.getLogger(__name__)

_API = "https://api.resend.com/emails"
_TIMEOUT = 15
_cached_key: str | None = None


class MailError(Exception):
    pass


def enabled() -> bool:
    return bool(settings.public_url and (settings.resend_api_key or settings.resend_api_key_secret))


def _api_key() -> str:
    global _cached_key
    if settings.resend_api_key:
        return settings.resend_api_key
    if _cached_key is None:
        from google.cloud import secretmanager

        payload = secretmanager.SecretManagerServiceClient().access_secret_version(
            name=settings.resend_api_key_secret
        ).payload.data
        _cached_key = payload.decode().strip()
    return _cached_key


def send(to: str, subject: str, html: str, text: str) -> str:
    """Send one email; returns the Resend message id."""
    try:
        resp = httpx.post(
            _API,
            headers={"Authorization": f"Bearer {_api_key()}"},
            json={"from": settings.mail_from, "to": [to], "reply_to": settings.mail_reply_to,
                  "subject": subject, "html": html, "text": text},
            timeout=_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 - network or Secret Manager failure; never leak the key
        raise MailError(f"email provider unavailable: {type(exc).__name__}") from None
    if resp.status_code >= 300:
        try:
            detail = resp.json().get("message", resp.status_code)
        except ValueError:
            detail = resp.status_code
        raise MailError(f"email provider rejected the message: {detail}")
    return str(resp.json().get("id", ""))
