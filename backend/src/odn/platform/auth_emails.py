"""Branded invite / reset-password emails (subject, HTML and plain text)."""

from __future__ import annotations

from html import escape

from odn.config import settings

PRODUCT = "EasyQuery"


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

    return _render(subject, intro, action, link, note + ignore)


def demo_link_email(link: str, name: str) -> tuple[str, str, str]:
    """"Reopen your demo chats": sent to the inbox an email check-in names, when that email already had chats."""
    intro = (f"Hi {name}, you just started the {PRODUCT} live demo with this email. To keep working in the chats "
             "you started earlier, open them with this link.")
    note = ("The link expires in 1 hour. If you didn't start the demo, you can ignore this email: nobody can open "
            "your earlier chats without it.")
    return _render(f"Reopen your {PRODUCT} demo chats", intro, "Reopen my chats", link, note)


def _render(subject: str, intro: str, action: str, link: str, note: str) -> tuple[str, str, str]:
    text = f"{intro}\n\n{action}:\n{link}\n\n{note}\n\n\u2014 The {PRODUCT} team\n"
    html = f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#f4f4f5;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#18181b">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
<table role="presentation" width="480" cellspacing="0" cellpadding="0" style="max-width:480px;background:#ffffff;border-radius:8px;padding:32px">
<tr><td style="font-size:20px;font-weight:600;padding-bottom:16px">{PRODUCT}</td></tr>
<tr><td style="font-size:15px;line-height:1.5;padding-bottom:24px">{escape(intro)}</td></tr>
<tr><td style="padding-bottom:24px"><a href="{escape(link, quote=True)}" style="display:inline-block;background:#18181b;color:#ffffff;text-decoration:none;padding:12px 20px;border-radius:6px;font-weight:600">{escape(action)}</a></td></tr>
<tr><td style="font-size:13px;line-height:1.5;color:#52525b">{escape(note)}</td></tr>
<tr><td style="font-size:12px;line-height:1.5;color:#71717a;padding-top:16px;word-break:break-all">Button not working? Paste this link into your browser:<br>{escape(link)}</td></tr>
</table>
<p style="font-size:12px;color:#71717a">Questions? Reply to this email or write to {escape(settings.mail_reply_to)}.</p>
</td></tr></table>
</body></html>"""
    return subject, html, text


def demo_visitor_email(name: str, email: str, checkins_url: str) -> tuple[str, str, str]:
    """Internal note to the team: a new visitor checked in to the live demo (email not verified)."""
    subject = f"New {PRODUCT} demo visitor: {name} <{email}>"
    text = (f"{name} <{email}> just started the live demo (email not verified).\n\n"
            f"All check-ins and the demo funnel: {checkins_url}\n")
    html = (f"<p><strong>{escape(name)}</strong> &lt;{escape(email)}&gt; just started the live demo "
            f"(email not verified).</p><p><a href=\"{escape(checkins_url, quote=True)}\">All check-ins and the demo "
            "funnel</a></p>")
    return subject, html, text
