"""Encryption at rest for user-supplied credentials (AES-256-GCM with the app master key).

Each value is bound to its owner and purpose through associated data, so a ciphertext copied to another
row or user fails to decrypt.
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from odn.config import settings

_VERSION = b"\x01"
_NONCE_BYTES = 12


class SecretsUnavailable(Exception):
    """ODN_SECRETS_KEY is not configured, so user credentials cannot be stored or read."""


class SecretDecryptionError(Exception):
    pass


def _cipher() -> AESGCM:
    raw = settings.secrets_key
    if not raw:
        raise SecretsUnavailable("ODN_SECRETS_KEY is not set")
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except ValueError:
        raise SecretsUnavailable("ODN_SECRETS_KEY is not valid base64url") from None
    if len(key) != 32:
        raise SecretsUnavailable("ODN_SECRETS_KEY must be 32 bytes")
    return AESGCM(key)


def encrypt(plaintext: bytes, aad: str) -> bytes:
    nonce = os.urandom(_NONCE_BYTES)
    return _VERSION + nonce + _cipher().encrypt(nonce, plaintext, aad.encode())


def decrypt(blob: bytes, aad: str) -> bytes:
    if len(blob) < 1 + _NONCE_BYTES + 16 or blob[:1] != _VERSION:
        raise SecretDecryptionError("unsupported ciphertext")
    nonce, body = blob[1 : 1 + _NONCE_BYTES], blob[1 + _NONCE_BYTES :]
    try:
        return _cipher().decrypt(nonce, body, aad.encode())
    except InvalidTag:
        raise SecretDecryptionError("credential cannot be decrypted") from None
