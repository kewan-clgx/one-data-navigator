"""Branded invite/reset emails: our link + Resend, falling back to Identity Platform's own email."""

import httpx
import pytest

from odn.config import settings
from odn.platform import identity_platform, mailer


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "idp_project", "proj")
    monkeypatch.setattr(settings, "idp_api_key", "key")
    monkeypatch.setattr(settings, "public_url", "https://app.example.com/")
    monkeypatch.setattr(settings, "resend_api_key", "re_test")
    log: dict[str, list] = {"admin": [], "key": [], "sent": []}

    def admin_call(method, path, body=None):
        log["admin"].append(body)
        if body["email"] == "nobody@acme.com":
            raise identity_platform.IdentityPlatformError("EMAIL_NOT_FOUND")
        return {"oobLink": "https://proj.firebaseapp.com/__/auth/action?mode=resetPassword&oobCode=CODE1&apiKey=k"}

    monkeypatch.setattr(identity_platform, "_admin_call", admin_call)
    monkeypatch.setattr(identity_platform, "_key_call", lambda path, body: log["key"].append(body) or {})
    monkeypatch.setattr(mailer, "send", lambda to, subject, html, text: log["sent"].append((to, subject, html, text)))
    return log


def test_invite_is_sent_by_us_with_link_to_our_action_page(calls):
    identity_platform.send_password_email("t1", "ann@acme.com", invite=True, org_name="Acme <Labs>")
    assert calls["admin"][0]["returnOobLink"] is True and calls["key"] == []
    to, subject, html, text = calls["sent"][0]
    assert to == "ann@acme.com"
    assert subject == "You're invited to Acme <Labs> on EasyQuery"
    link = "https://app.example.com/auth/action?mode=resetPassword&oobCode=CODE1&tenantId=t1"
    assert link in text and "Acme &lt;Labs&gt;" in html and "Acme <Labs>" not in html


def test_reset_subject(calls):
    identity_platform.send_password_email("t1", "ann@acme.com")
    assert calls["sent"][0][1] == "Reset your EasyQuery password"


def test_personal_signup_creates_an_unusable_account_and_emails_a_set_password_link(calls):
    identity_platform.invite("t1", "pat@example.org", display_name="Pat", signup=True)
    signup = calls["key"][0]
    assert (signup["email"], signup["tenantId"], signup["displayName"]) == ("pat@example.org", "t1", "Pat")
    assert len(signup["password"]) > 40  # random; nobody knows it until the link sets a new one
    assert calls["sent"][0][1] == "Finish creating your EasyQuery account"


def test_unknown_email_sends_nothing(calls):
    identity_platform.send_password_email("t1", "nobody@acme.com")
    assert calls["sent"] == [] and calls["key"] == []


def test_provider_failure_falls_back_to_identity_platform(calls, monkeypatch: pytest.MonkeyPatch):
    def fail(*args):
        raise mailer.MailError("email provider rejected the message: domain not verified")

    monkeypatch.setattr(mailer, "send", fail)
    identity_platform.send_password_email("t1", "ann@acme.com")
    assert calls["key"] == [{"requestType": "PASSWORD_RESET", "email": "ann@acme.com", "tenantId": "t1"}]


def test_not_configured_uses_identity_platform(calls, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "resend_api_key", "")
    identity_platform.send_password_email("t1", "ann@acme.com")
    assert calls["admin"] == [] and calls["sent"] == [] and len(calls["key"]) == 1


def test_mailer_reports_provider_rejection(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "resend_api_key", "re_test")
    seen = {}

    def post(url, headers, json, timeout):
        seen.update(headers=headers, json=json)
        return httpx.Response(403, json={"message": "The easyquery.ai domain is not verified"})

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(mailer.MailError, match="not verified"):
        mailer.send("ann@acme.com", "s", "<p>h</p>", "t")
    assert seen["headers"]["Authorization"] == "Bearer re_test"
    assert seen["json"]["from"] == settings.mail_from and seen["json"]["reply_to"] == settings.mail_reply_to
