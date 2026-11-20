"""Email recipients when a conversation is shared with them (best effort; only the title the owner chose)."""

from __future__ import annotations

import logging
from html import escape
from typing import Any

from odn.config import settings
from odn.platform import mailer

log = logging.getLogger(__name__)


def share_url(share_id: str) -> str:
    return f"{settings.public_url.rstrip('/')}/s/{share_id}"


def share_email(owner: str, title: str, link: str) -> tuple[str, str, str]:
    subject = f"{owner} shared “{title}” with you on QueryMuse"
    intro = f"{owner} shared a QueryMuse conversation with you: “{title}”."
    note = "Open it to see the questions, answers and results, then continue the conversation with your own follow-up questions."
    text = f"{intro}\n\nOpen in QueryMuse:\n{link}\n\n{note}\n\n— The QueryMuse team\n"
    html = f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#f4f4f5;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#18181b">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
<table role="presentation" width="480" cellspacing="0" cellpadding="0" style="max-width:480px;background:#ffffff;border-radius:8px;padding:32px">
<tr><td style="font-size:20px;font-weight:600;padding-bottom:16px">QueryMuse</td></tr>
<tr><td style="font-size:15px;line-height:1.5;padding-bottom:24px">{escape(intro)}</td></tr>
<tr><td style="padding-bottom:24px"><a href="{escape(link, quote=True)}" style="display:inline-block;background:#18181b;color:#ffffff;text-decoration:none;padding:12px 20px;border-radius:6px;font-weight:600">Open in QueryMuse</a></td></tr>
<tr><td style="font-size:13px;line-height:1.5;color:#52525b">{escape(note)}</td></tr>
</table></td></tr></table>
</body></html>"""
    return subject, html, text


def notify_recipients(owner: str, title: str, share_id: str, recipients: list[dict[str, Any]]) -> int:
    """Emails each recipient; returns how many were sent. Never raises."""
    if not mailer.enabled():
        return 0
    sent = 0
    link = share_url(share_id)
    for r in recipients:
        try:
            mailer.send(r["email"], *share_email(owner, title, link))
            sent += 1
        except Exception as exc:  # noqa: BLE001 - a failed email must not fail the share
            log.warning("share email to a recipient failed: %s", exc)
    return sent
