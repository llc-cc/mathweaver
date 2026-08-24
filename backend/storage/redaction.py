"""持久化、日志和导出边界共用的敏感信息脱敏工具。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SENSITIVE_KEYS = {
    "api_key",
    "embedding_api_key",
    "llm_api_key",
    "authorization",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "secret_key",
    "access_key_secret",
}
_SIGNED_QUERY_KEYS = {
    "signature",
    "ossaccesskeyid",
    "accesskeyid",
    "x-oss-signature",
    "x-oss-credential",
    "x-amz-signature",
    "x-amz-credential",
    "expires",
    "x-oss-expires",
    "x-amz-date",
    "token",
}
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def _normalise_key(value: object) -> str:
    return str(value).strip().lower().replace("-", "_")


def redact_url(value: str) -> str:
    """移除签名参数并保留无敏感性的下载选项，便于安全排障。"""
    try:
        parts = urlsplit(value)
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        safe_pairs = [(key, item) for key, item in pairs if key.lower() not in _SIGNED_QUERY_KEYS]
        if len(safe_pairs) != len(pairs):
            return urlunsplit(
                (parts.scheme, parts.netloc, parts.path, urlencode(safe_pairs), parts.fragment)
            )
    except ValueError:
        return value
    return value


def redact_text(value: object, *, secrets: tuple[object, ...] = ()) -> str:
    text = str(value or "")
    for secret in secrets:
        secret_text = str(secret or "")
        if secret_text:
            text = text.replace(secret_text, "***")
    return _URL_PATTERN.sub(lambda match: redact_url(match.group(0)), text)


def redact_structure(value, _seen: set[int] | None = None):
    """递归复制并脱敏，避免审计或导出处理意外修改业务对象。"""
    seen = _seen if _seen is not None else set()
    if isinstance(value, (Mapping, list, tuple)):
        object_id = id(value)
        if object_id in seen:
            return "[REDACTED:CYCLE]"
        seen.add(object_id)
    if isinstance(value, Mapping):
        try:
            return {
                key: "***" if _normalise_key(key) in _SENSITIVE_KEYS else redact_structure(item, seen)
                for key, item in value.items()
            }
        finally:
            seen.remove(id(value))
    if isinstance(value, list):
        try:
            return [redact_structure(item, seen) for item in value]
        finally:
            seen.remove(id(value))
    if isinstance(value, tuple):
        try:
            return tuple(redact_structure(item, seen) for item in value)
        finally:
            seen.remove(id(value))
    if isinstance(value, str):
        return redact_text(value)
    return value


# 保留计划中的公共命名，避免后续审计/日志模块各自实现不一致的脱敏逻辑。
redact_sensitive = redact_structure
