from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = "v1"


class SecretConfigurationError(RuntimeError):
    pass


def session_token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def load_data_key() -> bytes:
    path_value = os.environ.get("MATHWEAVER_DATA_KEY_FILE", "").strip()
    if not path_value:
        raise SecretConfigurationError("MATHWEAVER_DATA_KEY_FILE is required")
    raw = Path(path_value).read_text(encoding="utf-8").strip()
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except Exception as exc:
        raise SecretConfigurationError("data key must be URL-safe base64") from exc
    if len(key) != 32:
        raise SecretConfigurationError("data key must decode to exactly 32 bytes")
    return key


def encrypt_secret(value: str, *, aad: str, key: bytes | None = None) -> str:
    if not value:
        return ""
    active_key = key or load_data_key()
    nonce = os.urandom(12)
    ciphertext = AESGCM(active_key).encrypt(nonce, value.encode("utf-8"), aad.encode("utf-8"))
    token = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii").rstrip("=")
    return f"{_VERSION}:{token}"


def decrypt_secret(value: str, *, aad: str, key: bytes | None = None) -> str:
    if not value:
        return ""
    prefix, separator, token = value.partition(":")
    if separator != ":" or prefix != _VERSION:
        raise ValueError("unsupported encrypted secret format")
    payload = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    if len(payload) < 13:
        raise ValueError("encrypted secret payload is truncated")
    active_key = key or load_data_key()
    plaintext = AESGCM(active_key).decrypt(payload[:12], payload[12:], aad.encode("utf-8"))
    return plaintext.decode("utf-8")
