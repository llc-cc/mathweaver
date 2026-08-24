"""模型凭据认证加密边界测试。"""

from __future__ import annotations

import base64
import json

import pytest

from storage.credential_crypto import (
    CredentialCipher,
    CredentialConfigurationError,
    CredentialDecryptionError,
    CredentialKeyring,
)


def _encoded_key(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def test_keyring_rejects_missing_active_key_without_echoing_key_material():
    secret = _encoded_key(b"s" * 32)

    with pytest.raises(CredentialConfigurationError) as caught:
        CredentialKeyring.from_environment(
            {
                "MATHWEAVER_CREDENTIAL_KEYS_JSON": json.dumps({"stored": secret}),
                "MATHWEAVER_CREDENTIAL_ACTIVE_KEY_ID": "missing",
            }
        )

    assert secret not in str(caught.value)


def test_cipher_reads_old_key_and_writes_active_key():
    keys = {"old": b"o" * 32, "current": b"c" * 32}
    old = CredentialCipher(CredentialKeyring(keys=keys, active_key_id="old"))
    current = CredentialCipher(CredentialKeyring(keys=keys, active_key_id="current"))
    aad = "user:7:llm-settings:v1"

    envelope = old.encrypt_json({"api_key": "sk-secret"}, aad=aad)

    assert current.decrypt_json(envelope, aad=aad) == {"api_key": "sk-secret"}
    assert current.encrypt_json({"api_key": "next"}, aad=aad)["key_id"] == "current"
    assert "sk-secret" not in json.dumps(envelope)


def test_cipher_rejects_cross_user_ciphertext_without_echoing_secret():
    cipher = CredentialCipher(
        CredentialKeyring(keys={"active": b"a" * 32}, active_key_id="active")
    )
    envelope = cipher.encrypt_json(
        {"api_key": "must-not-leak"}, aad="user:7:llm-settings:v1"
    )

    with pytest.raises(CredentialDecryptionError) as caught:
        cipher.decrypt_json(envelope, aad="user:8:llm-settings:v1")

    assert "must-not-leak" not in str(caught.value)


def test_cipher_rejects_tampered_ciphertext():
    cipher = CredentialCipher(
        CredentialKeyring(keys={"active": b"a" * 32}, active_key_id="active")
    )
    envelope = cipher.encrypt_json(
        {"api_key": "must-not-leak"}, aad="user:7:llm-settings:v1"
    )
    envelope["ciphertext"] = _encoded_key(b"tampered")

    with pytest.raises(CredentialDecryptionError, match="credential decryption failed"):
        cipher.decrypt_json(envelope, aad="user:7:llm-settings:v1")
