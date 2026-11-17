"""Credential encryption at rest and the user-URL (SSRF) guard."""

import base64
import os
import socket

import pytest

from odn.config import settings
from odn.platform import crypto
from odn.platform.net import UnsafeUrl, check_public_url


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "secrets_key", base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode())


def test_roundtrip_is_bound_to_its_owner() -> None:
    blob = crypto.encrypt(b"sk-secret", "llm-key:t1:u1")
    assert b"sk-secret" not in blob and crypto.decrypt(blob, "llm-key:t1:u1") == b"sk-secret"
    with pytest.raises(crypto.SecretDecryptionError):
        crypto.decrypt(blob, "llm-key:t1:u2")
    with pytest.raises(crypto.SecretDecryptionError):
        crypto.decrypt(blob[:-1] + bytes([blob[-1] ^ 1]), "llm-key:t1:u1")


def test_other_master_key_cannot_decrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    blob = crypto.encrypt(b"x", "a")
    monkeypatch.setattr(settings, "secrets_key", base64.urlsafe_b64encode(os.urandom(32)).decode())
    with pytest.raises(crypto.SecretDecryptionError):
        crypto.decrypt(blob, "a")


@pytest.mark.parametrize("bad", ["", "short", base64.urlsafe_b64encode(b"x" * 16).decode()])
def test_missing_or_bad_master_key(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setattr(settings, "secrets_key", bad)
    with pytest.raises(crypto.SecretsUnavailable):
        crypto.encrypt(b"x", "a")


def _resolve_to(monkeypatch: pytest.MonkeyPatch, ip: str) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))])


@pytest.mark.parametrize(
    ("url", "ip"),
    [
        ("http://llm.example.com/v1", "93.184.215.14"),
        ("https://user:pw@llm.example.com/v1", "93.184.215.14"),
        ("https://llm.example.com/v1?x=1", "93.184.215.14"),
        ("https://metadata.google.internal/", "169.254.169.254"),
        ("https://worker:8000/", "172.18.0.5"),
        ("https://localhost/", "127.0.0.1"),
        ("https://intranet.example.com/", "10.0.0.8"),
        ("file:///etc/passwd", "93.184.215.14"),
    ],
)
def test_rejects_non_public_or_non_https(monkeypatch: pytest.MonkeyPatch, url: str, ip: str) -> None:
    monkeypatch.setattr(settings, "env", "prod")
    _resolve_to(monkeypatch, ip)
    with pytest.raises(UnsafeUrl):
        check_public_url(url)


def test_accepts_public_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "env", "prod")
    _resolve_to(monkeypatch, "93.184.215.14")
    assert check_public_url(" https://llm.example.com/v1/ ") == "https://llm.example.com/v1"
