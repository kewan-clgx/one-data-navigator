"""Live-demo emails, sent after the response (FastAPI background tasks); failures are logged, never raised."""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from odn.config import settings
from odn.platform import auth_emails, mailer

log = logging.getLogger(__name__)


def _send(to: str, message: tuple[str, str, str], what: str) -> None:
    if not mailer.enabled():
        log.info("demo: mailer not configured; %s not sent", what)
        return
    try:
        mailer.send(to, *message)
    except Exception as exc:  # noqa: BLE001 - a lost email must not affect the visitor
        log.warning("demo: %s failed: %s", what, exc)


def new_visitor(name: str, email: str) -> None:
    if settings.demo_notify_email:
        url = f"{settings.public_url.rstrip('/')}/demo"
        _send(settings.demo_notify_email, auth_emails.demo_visitor_email(name, email, url), "visitor notification")


def reopen_link(name: str, email: str, link_token: str) -> None:
    link = f"{settings.public_url.rstrip('/')}/try/continue?{urlencode({'token': link_token})}"
    _send(email, auth_emails.demo_link_email(link, name), "reopen link")
