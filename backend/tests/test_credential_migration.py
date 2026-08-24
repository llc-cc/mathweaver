"""历史明文模型凭据迁移测试。"""

from __future__ import annotations

import json
import base64
import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import inspect
from sqlalchemy import create_engine
from storage.credential_crypto import CredentialCipher, CredentialKeyring
from storage.database import session_scope
from storage.models import User, UserSettings
from scripts.migrate_llm_credentials import migrate_settings


def _cipher() -> CredentialCipher:
    return CredentialCipher(
        CredentialKeyring(keys={"active": b"a" * 32}, active_key_id="active")
    )


def _seed_legacy_settings() -> None:
    with session_scope() as session:
        session.add(User.create_account("student", "S1001", "student@example.edu", "Student", "hash"))
        session.flush()
        session.add(UserSettings(user_id=1, llm_api_url="https://api.example/v1", llm_model="chat-model", llm_api_key="legacy-primary-secret", llm_configs_json={"configs": [{"name": "Primary", "api_url": "https://api.example/v1", "model_name": "chat-model", "api_key": "legacy-primary-secret", "embedding_api_key": "legacy-embedding-secret"}], "active_index": 0}))


def test_dry_run_reports_plaintext_without_writing():
    _seed_legacy_settings()
    summary = migrate_settings(session_scope, _cipher(), apply=False)
    assert summary.pending == 1
    assert summary.migrated == 0
    with session_scope() as session:
        row = session.get(UserSettings, 1)
        assert row is not None
        assert row.llm_api_key == "legacy-primary-secret"
        assert row.llm_secrets_encrypted_json is None


def test_apply_migration_encrypts_secrets_and_blanks_plaintext_atomically():
    _seed_legacy_settings()
    cipher = _cipher()
    summary = migrate_settings(session_scope, cipher, apply=True)
    assert summary.migrated == 1
    with session_scope() as session:
        row = session.get(UserSettings, 1)
        assert row is not None
        descriptors = json.dumps(row.llm_configs_json, ensure_ascii=False)
        encrypted = json.dumps(row.llm_secrets_encrypted_json)
        assert row.llm_api_key == ""
        assert "legacy-primary-secret" not in descriptors + encrypted
        assert "legacy-embedding-secret" not in descriptors + encrypted
        config_id = row.llm_configs_json["configs"][0]["config_id"]
        secrets = cipher.decrypt_json(row.llm_secrets_encrypted_json, aad="user:1:llm-settings:v1")
        assert secrets[config_id] == {"api_key": "legacy-primary-secret", "embedding_api_key": "legacy-embedding-secret"}


def test_migration_is_idempotent():
    _seed_legacy_settings()
    cipher = _cipher()
    first = migrate_settings(session_scope, cipher, apply=True)
    second = migrate_settings(session_scope, cipher, apply=True)
    assert first.migrated == 1
    assert second.migrated == 0
    assert second.already_secure == 1


def test_production_migration_upgrades_schema_and_verifies_no_plaintext(tmp_path):
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'production.sqlite3').as_posix()}"
    environment = os.environ.copy()
    environment.update(
        {
            "MATHWEAVER_DATABASE_URL": database_url,
            "MATHWEAVER_CREDENTIAL_KEYS_JSON": json.dumps(
                {"active": base64.b64encode(b"a" * 32).decode("ascii")}
            ),
            "MATHWEAVER_CREDENTIAL_ACTIVE_KEY_ID": "active",
        }
    )

    result = subprocess.run(
        [sys.executable, "scripts/production_migrate.py"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert database_url not in result.stdout + result.stderr
    engine = create_engine(database_url)
    assert "llm_secrets_encrypted_json" in {
        column["name"] for column in inspect(engine).get_columns("user_settings")
    }
    engine.dispose()
