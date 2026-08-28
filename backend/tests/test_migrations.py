"""Alembic 基线的离线审计测试。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _offline_upgrade_sql() -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["MATHWEAVER_DATABASE_NAME"] = "mathweaver"
    environment["MATHWEAVER_DATABASE_URL"] = (
        "mysql+pymysql://migration-user:do-not-print@127.0.0.1:3306/mathweaver"
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "backend/migrations/alembic.ini",
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_alembic_upgrade_sql_targets_mysql_base_schema() -> None:
    result = _offline_upgrade_sql()

    assert result.returncode == 0, result.stderr
    assert "CREATE TABLE users" in result.stdout
    assert "CREATE TABLE history" in result.stdout
    assert "object_storage_prefix VARCHAR(1024)" in result.stdout
    assert "20260821_02" in result.stdout


def test_alembic_offline_sql_does_not_expose_runtime_password() -> None:
    result = _offline_upgrade_sql()

    assert result.returncode == 0, result.stderr
    assert "do-not-print" not in result.stdout
    assert "do-not-print" not in result.stderr
