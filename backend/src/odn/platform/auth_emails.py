"""Branded invite / reset-password emails (subject, HTML and plain text)."""

from __future__ import annotations

from html import escape

from odn.config import settings

PRODUCT = "QueryMuse"


def password_email(link: str, *, invite: bool, org_name: str | None, signup: bool = False) -> tuple[str, str, str]:
    org = (org_name or "").strip()
    if signup:
        # Same email whether or not the account already exists (sign-up must not reveal existing accounts).
        subject = f"Finish creating your {PRODUCT} account"
        intro = (f"Choose a password to finish creating your personal {PRODUCT} account. If you already have one, "
                 "this sets a new password for it.")
        action = "Set your password"
    elif invite:
        subject = f"You're invited to {org} on {PRODUCT}" if org else f"You're invited to {PRODUCT}"
        intro = (f"You've been invited to join {org} on {PRODUCT}, the conversational way to explore your data."
                 if org else f"You've been invited to {PRODUCT}, the conversational way to explore your data.")
        action = "Set your password"
    else:
        subject = f"Reset your {PRODUCT} password"
        intro = (f"We received a request to reset the password for your {org} account on {PRODUCT}."
                 if org else f"We received a request to reset your {PRODUCT} password.")
        action = "Choose a new password"
    note = ("This link works once and expires in 1 hour. If it has expired, use \u201cForgot password\u201d "
            "on the sign-in page to get a new one.")
    ignore = "" if invite and not signup else " If you didn't ask for this, you can ignore this email; your password stays the same."

    text = f"{intro}\n\n{action}:\n{link}\n\n{note}{ignore}\n\n\u2014 The {PRODUCT} team\n"
    html = f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#f4f4f5;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#18181b">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
<table role="presentation" width="480" cellspacing="0" cellpadding="0" style="max-width:480px;background:#ffffff;border-radius:8px;padding:32px">
<tr><td style="font-size:20px;font-weight:600;padding-bottom:16px">{PRODUCT}</td></tr>
<tr><td style="font-size:15px;line-height:1.5;padding-bottom:24px">{escape(intro)}</td></tr>
<tr><td style="padding-bottom:24px"><a href="{escape(link, quote=True)}" style="display:inline-block;background:#18181b;color:#ffffff;text-decoration:none;padding:12px 20px;border-radius:6px;font-weight:600">{action}</a></td></tr>
<tr><td style="font-size:13px;line-height:1.5;color:#52525b">{escape(note + ignore)}</td></tr>
<tr><td style="font-size:12px;line-height:1.5;color:#71717a;padding-top:16px;word-break:break-all">Button not working? Paste this link into your browser:<br>{escape(link)}</td></tr>
</table>
<p style="font-size:12px;color:#71717a">Questions? Reply to this email or write to {escape(settings.mail_reply_to)}.</p>
</td></tr></table>
</body></html>"""
    return subject, html, text
