"""模型凭据的认证加密边界；业务层不直接接触主密钥格式。"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class CredentialConfigurationError(RuntimeError):
    """凭据密钥配置无效，错误内容不得包含任何密钥材料。"""


class CredentialDecryptionError(RuntimeError):
    """密文不可验证或不可解密，对外只提供稳定错误。"""


def _decode_base64(value: object) -> bytes:
    if not isinstance(value, str):
        raise ValueError
    return base64.b64decode(value.encode("ascii"), validate=True)


def _encode_base64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


@dataclass(frozen=True)
class CredentialKeyring:
    """保存当前加密密钥及历史解密密钥，active key 只用于新写入。"""

    keys: Mapping[str, bytes]
    active_key_id: str

    def __post_init__(self) -> None:
        if (
            not self.keys
            or not _KEY_ID.fullmatch(self.active_key_id)
            or self.active_key_id not in self.keys
            or any(
                not _KEY_ID.fullmatch(key_id) or len(key) != 32
                for key_id, key in self.keys.items()
            )
        ):
            raise CredentialConfigurationError("credential key configuration is invalid")

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] = os.environ
    ) -> "CredentialKeyring":
        try:
            raw = json.loads(environment["MATHWEAVER_CREDENTIAL_KEYS_JSON"])
            active_key_id = environment["MATHWEAVER_CREDENTIAL_ACTIVE_KEY_ID"].strip()
            if not isinstance(raw, dict):
                raise ValueError
            keys = {str(key_id): _decode_base64(value) for key_id, value in raw.items()}
            return cls(keys=keys, active_key_id=active_key_id)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeError, binascii.Error):
            raise CredentialConfigurationError(
                "credential key configuration is invalid"
            ) from None


class CredentialCipher:
    """使用 AES-256-GCM 加密 JSON，并通过 AAD 绑定用户与用途。"""

    def __init__(self, keyring: CredentialKeyring) -> None:
        self._keyring = keyring

    def encrypt_json(self, value: dict[str, Any], *, aad: str) -> dict[str, Any]:
        if not isinstance(value, dict) or not aad:
            raise ValueError("credential payload and aad are required")
        key_id = self._keyring.active_key_id
        nonce = os.urandom(12)
        plaintext = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        ciphertext = AESGCM(self._keyring.keys[key_id]).encrypt(
            nonce, plaintext, aad.encode("utf-8")
        )
        return {
            "version": 1,
            "key_id": key_id,
            "nonce": _encode_base64(nonce),
            "ciphertext": _encode_base64(ciphertext),
        }

    def decrypt_json(self, envelope: dict[str, Any], *, aad: str) -> dict[str, Any]:
        try:
            if not isinstance(envelope, dict) or envelope.get("version") != 1 or not aad:
                raise ValueError
            key_id = envelope["key_id"]
            if not isinstance(key_id, str) or key_id not in self._keyring.keys:
                raise ValueError
            nonce = _decode_base64(envelope["nonce"])
            ciphertext = _decode_base64(envelope["ciphertext"])
            if len(nonce) != 12:
                raise ValueError
            plaintext = AESGCM(self._keyring.keys[key_id]).decrypt(
                nonce, ciphertext, aad.encode("utf-8")
            )
            value = json.loads(plaintext.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeError,
            binascii.Error,
            json.JSONDecodeError,
            InvalidTag,
        ):
            # 解密失败不能透传底层异常；其中可能携带密文、AAD 或调用上下文。
            raise CredentialDecryptionError("credential decryption failed") from None
