"""将历史明文模型凭据迁移为认证密文。"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import AbstractContextManager
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from storage.credential_crypto import CredentialCipher, CredentialKeyring
from storage.database import session_scope
from storage.models import UserSettings


SessionFactory = Callable[[], AbstractContextManager[Session]]
_SECRET_FIELDS = ("api_key", "embedding_api_key")


class CredentialMigrationError(RuntimeError):
    """迁移数据不满足安全转换条件，消息中不包含原始配置。"""


@dataclass(frozen=True)
class MigrationSummary:
    pending: int = 0
    migrated: int = 0
    already_secure: int = 0


def _contains_plaintext(row: UserSettings) -> bool:
    if str(row.llm_api_key or ""):
        return True
    data = row.llm_configs_json if isinstance(row.llm_configs_json, dict) else {}
    configs = data.get("configs") if isinstance(data.get("configs"), list) else []
    return any(
        isinstance(config, dict) and any(str(config.get(field) or "") for field in _SECRET_FIELDS)
        for config in configs
    )


def _secure_values(row: UserSettings) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    data = deepcopy(row.llm_configs_json) if isinstance(row.llm_configs_json, dict) else {}
    configs = data.get("configs") if isinstance(data.get("configs"), list) else []
    if not configs and (row.llm_api_url or row.llm_model or row.llm_api_key):
        configs = [{
            "name": "默认配置",
            "api_url": str(row.llm_api_url or ""),
            "model_name": str(row.llm_model or ""),
            "api_key": str(row.llm_api_key or ""),
        }]
    if any(not isinstance(config, dict) for config in configs):
        raise CredentialMigrationError("stored model configuration is invalid")

    descriptors: list[dict[str, Any]] = []
    secrets: dict[str, dict[str, str]] = {}
    used_ids: set[str] = set()
    active_index = data.get("active_index", 0)
    if not isinstance(active_index, int) or isinstance(active_index, bool):
        active_index = 0
    for index, original in enumerate(configs):
        descriptor = deepcopy(original)
        config_id = str(descriptor.get("config_id") or "").strip()
        if not config_id or config_id in used_ids:
            config_id = uuid.uuid4().hex
        used_ids.add(config_id)
        descriptor["config_id"] = config_id
        config_secrets: dict[str, str] = {}
        for field in _SECRET_FIELDS:
            value = str(descriptor.pop(field, "") or "")
            if value:
                config_secrets[field] = value
        if index == active_index and row.llm_api_key and "api_key" not in config_secrets:
            config_secrets["api_key"] = str(row.llm_api_key)
        if config_secrets:
            secrets[config_id] = config_secrets
        descriptors.append(descriptor)
    return {"configs": descriptors, "active_index": active_index}, secrets


def migrate_settings(
    session_factory: SessionFactory,
    cipher: CredentialCipher,
    *,
    apply: bool,
) -> MigrationSummary:
    pending_ids: list[int] = []
    already_secure = 0
    with session_factory() as session:
        rows = session.scalars(select(UserSettings).order_by(UserSettings.user_id)).all()
        for row in rows:
            if row.llm_secrets_encrypted_json is not None and not _contains_plaintext(row):
                already_secure += 1
            else:
                pending_ids.append(int(row.user_id))

    if not apply:
        return MigrationSummary(pending=len(pending_ids), already_secure=already_secure)

    migrated = 0
    for user_id in pending_ids:
        with session_factory() as session:
            row = session.get(UserSettings, user_id)
            if row is None:
                continue
            descriptors, secrets = _secure_values(row)
            envelope = cipher.encrypt_json(
                secrets,
                aad=f"user:{user_id}:llm-settings:v1",
            )
            # 密文与清空明文位于同一事务，任何异常都不会留下半迁移状态。
            row.llm_configs_json = descriptors
            row.llm_secrets_encrypted_json = envelope
            row.llm_api_key = ""
            migrated += 1
    return MigrationSummary(migrated=migrated, already_secure=already_secure)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        cipher = CredentialCipher(CredentialKeyring.from_environment(os.environ))
        summary = migrate_settings(session_scope, cipher, apply=args.apply)
        print(json.dumps(summary.__dict__, sort_keys=True))
        return 0
    except Exception as exc:
        # 未知数据库/驱动异常只输出类型，避免连接串或历史凭据进入部署日志。
        print(f"credential migration failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
