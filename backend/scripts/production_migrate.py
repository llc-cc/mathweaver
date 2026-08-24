"""依次执行生产 DDL 与模型凭据数据迁移，并验证不存在残留明文。"""

from __future__ import annotations

import json
from pathlib import Path
import sys

from alembic import command
from alembic.config import Config


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from storage.credential_crypto import CredentialCipher, CredentialKeyring
from storage.database import configure_database, session_scope
from scripts.migrate_llm_credentials import migrate_settings


def run() -> dict[str, int]:
    """DDL 成功后才迁移数据，最终残留明文会阻断后端启动。"""
    alembic_config = Config(str(BACKEND_DIR / "migrations" / "alembic.ini"))
    command.upgrade(alembic_config, "head")
    configure_database()
    cipher = CredentialCipher(CredentialKeyring.from_environment())
    applied = migrate_settings(session_scope, cipher, apply=True)
    residual = migrate_settings(session_scope, cipher, apply=False)
    if residual.pending:
        raise RuntimeError("plaintext credential migration is incomplete")
    return {
        "migrated": applied.migrated,
        "already_secure": residual.already_secure,
        "pending": residual.pending,
    }


def main() -> int:
    try:
        print(json.dumps(run(), sort_keys=True))
        return 0
    except Exception as exc:
        # 部署日志只保留异常类型，避免数据库驱动错误泄露连接串或历史配置。
        print(f"production migration failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
