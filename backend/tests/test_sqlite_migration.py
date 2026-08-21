"""旧 SQLite 到集中式数据库迁移脚本的集成测试。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import subprocess
import sys

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from scripts import migrate_sqlite_to_mysql as migration
from storage.models import Base, History, LoginSession, ProofWorkspace, User, UserSettings


BACKEND_DIR = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND_DIR / "scripts" / "migrate_sqlite_to_mysql.py"


def _create_legacy_database(path: Path) -> None:
    """建立最早版本表结构，确保新增列缺失时仍能迁移。"""
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE history (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                node_count INTEGER NOT NULL DEFAULT 0,
                edge_count INTEGER NOT NULL DEFAULT 0,
                nodes_json TEXT NOT NULL,
                edges_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE user_settings (
                user_id INTEGER PRIMARY KEY,
                llm_api_url TEXT NOT NULL DEFAULT '',
                llm_model TEXT NOT NULL DEFAULT '',
                llm_api_key TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE proof_workspaces (
                user_id INTEGER NOT NULL,
                graph_id TEXT NOT NULL,
                node_id INTEGER NOT NULL,
                user_proof TEXT NOT NULL DEFAULT '',
                versions_json TEXT NOT NULL DEFAULT '[]',
                ai_messages_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, graph_id, node_id)
            );
            """
        )
        connection.executemany(
            "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            [
                (1, "student@example.com", "student-hash", "2025-01-01T00:00:00+00:00"),
                (2, "teacher@example.com", "teacher-hash", "2025-01-02T00:00:00+00:00"),
            ],
        )
        connection.execute(
            "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
            ("raw-session-token", 1, "2025-01-03T00:00:00+00:00"),
        )
        connection.execute(
            """INSERT INTO history
               (id, user_id, filename, node_count, edge_count, nodes_json, edges_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "history-1", 1, "lesson.pdf", 1, 1,
                '[{"id": 1}]', '[{"source": 1, "target": 1}]',
                "2025-01-04T00:00:00+00:00",
            ),
        )
        connection.execute(
            """INSERT INTO user_settings
               (user_id, llm_api_url, llm_model, llm_api_key, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (1, "https://llm.example/v1", "test-model", "secret-api-key", "2025-01-05T00:00:00+00:00"),
        )
        connection.execute(
            """INSERT INTO proof_workspaces
               (user_id, graph_id, node_id, user_proof, versions_json, ai_messages_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (2, "graph-1", 7, "proof", '[{"v": 1}]', '[{"role": "user"}]', "2025-01-06T00:00:00+00:00"),
        )


def _run_cli(source: Path, *extra: str, target_url: str | None = None, env_name: str = "TEST_MIGRATION_URL"):
    environment = os.environ.copy()
    environment.pop(env_name, None)
    if target_url is not None:
        environment[env_name] = target_url
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--sqlite",
            str(source),
            "--database-url-env",
            env_name,
            *extra,
        ],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _create_target(
    path: Path,
    *,
    ignore_history_inserts: bool = False,
    corrupt_history_owner: bool = False,
) -> str:
    url = f"sqlite+pysqlite:///{path.as_posix()}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    if ignore_history_inserts:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """CREATE TRIGGER ignore_history BEFORE INSERT ON history
                   BEGIN SELECT RAISE(IGNORE); END"""
            )
    if corrupt_history_owner:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """CREATE TRIGGER corrupt_history_owner AFTER INSERT ON history
                   BEGIN UPDATE history SET user_id = 999 WHERE id = NEW.id; END"""
            )
    engine.dispose()
    return url


def _counts(target_url: str) -> dict[str, int]:
    engine = create_engine(target_url)
    try:
        with engine.connect() as connection:
            return {
                "users": int(connection.scalar(select(func.count()).select_from(User)) or 0),
                "sessions": int(connection.scalar(select(func.count()).select_from(LoginSession)) or 0),
                "history": int(connection.scalar(select(func.count()).select_from(History)) or 0),
                "settings": int(connection.scalar(select(func.count()).select_from(UserSettings)) or 0),
                "proof": int(connection.scalar(select(func.count()).select_from(ProofWorkspace)) or 0),
            }
    finally:
        engine.dispose()


def test_dry_run_reads_and_prints_counts_without_target_or_backup(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    _create_legacy_database(source)
    original = source.read_bytes()

    result = _run_cli(source, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "users: 2" in result.stdout
    assert "sessions: 1 (excluded)" in result.stdout
    assert "history: 1" in result.stdout
    assert "user_settings: 1" in result.stdout
    assert "proof_workspaces: 1" in result.stdout
    assert source.read_bytes() == original
    assert list(tmp_path.glob("legacy.sqlite3.*.bak")) == []


def test_success_preserves_data_applies_roles_and_excludes_sessions(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    target_url = _create_target(tmp_path / "target.sqlite3")
    mapping = tmp_path / "users.json"
    _create_legacy_database(source)
    mapping.write_text(
        json.dumps(
            {
                "student@example.com": {
                    "student_no": "S20250001",
                    "display_name": "学生甲",
                    "is_active": True,
                },
                "teacher@example.com": {
                    "role": "teacher",
                    "display_name": "教师乙",
                    "is_active": True,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    source_bytes = source.read_bytes()

    result = _run_cli(
        source,
        "--user-mapping",
        str(mapping),
        target_url=target_url,
    )

    assert result.returncode == 0, result.stderr
    assert _counts(target_url) == {
        "users": 2,
        "sessions": 0,
        "history": 1,
        "settings": 1,
        "proof": 1,
    }
    engine = create_engine(target_url)
    with Session(engine) as session:
        users = session.scalars(select(User).order_by(User.id)).all()
        history = session.scalar(select(History))
        settings = session.scalar(select(UserSettings))
        workspace = session.scalar(select(ProofWorkspace))
        assert [(user.id, user.role, user.student_no, user.is_active) for user in users] == [
            (1, "student", "S20250001", True),
            (2, "teacher", None, True),
        ]
        assert [user.password_hash for user in users] == ["student-hash", "teacher-hash"]
        assert [user.display_name for user in users] == ["学生甲", "教师乙"]
        assert history.nodes_json == [{"id": 1}]
        assert settings.llm_api_key == "secret-api-key"
        assert settings.llm_configs_json["configs"][0]["model_name"] == "test-model"
        assert workspace.imports_json == []
    engine.dispose()
    assert source.read_bytes() == source_bytes
    assert len(list(tmp_path.glob("legacy.sqlite3.*.bak"))) == 1
    combined_output = result.stdout + result.stderr
    assert "student-hash" not in combined_output
    assert "secret-api-key" not in combined_output
    assert "raw-session-token" not in combined_output
    assert target_url not in combined_output


def test_unmapped_user_is_inactive_student_with_unique_placeholder(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    target_url = _create_target(tmp_path / "target.sqlite3")
    mapping = tmp_path / "users.json"
    _create_legacy_database(source)
    mapping.write_text(
        json.dumps({"student@example.com": {"student_no": "legacy-2"}}),
        encoding="utf-8",
    )

    result = _run_cli(source, "--user-mapping", str(mapping), target_url=target_url)

    assert result.returncode == 0, result.stderr
    engine = create_engine(target_url)
    with Session(engine) as session:
        users = session.scalars(select(User).order_by(User.id)).all()
        assert users[0].student_no == "legacy-2"
        assert users[0].is_active is True
        assert users[1].role == "student"
        assert users[1].student_no == "legacy-2-2"
        assert users[1].is_active is False
    engine.dispose()


def test_student_without_number_cannot_be_activated(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    target_url = _create_target(tmp_path / "target.sqlite3")
    mapping = tmp_path / "users.json"
    _create_legacy_database(source)
    mapping.write_text(
        json.dumps({"student@example.com": {"role": "student", "is_active": True}}),
        encoding="utf-8",
    )

    result = _run_cli(source, "--user-mapping", str(mapping), target_url=target_url)

    assert result.returncode == 0, result.stderr
    engine = create_engine(target_url)
    with Session(engine) as session:
        student = session.get(User, 1)
        assert student.role == "student"
        assert student.student_no.startswith("legacy-")
        assert student.is_active is False
    engine.dispose()


def test_only_explicit_mapping_can_assign_admin_role(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    target_url = _create_target(tmp_path / "target.sqlite3")
    mapping = tmp_path / "users.json"
    _create_legacy_database(source)
    mapping.write_text(
        json.dumps(
            {
                "student@example.com": {"student_no": "S1"},
                "teacher@example.com": {"role": "admin", "is_active": False},
            }
        ),
        encoding="utf-8",
    )

    result = _run_cli(source, "--user-mapping", str(mapping), target_url=target_url)

    assert result.returncode == 0, result.stderr
    engine = create_engine(target_url)
    with Session(engine) as session:
        admin = session.get(User, 2)
        assert admin.role == "admin"
        assert admin.is_active is False
    engine.dispose()


def test_bad_mapping_json_fails_without_writing_target(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    target_url = _create_target(tmp_path / "target.sqlite3")
    mapping = tmp_path / "users.json"
    _create_legacy_database(source)
    mapping.write_text("{broken", encoding="utf-8")

    result = _run_cli(source, "--user-mapping", str(mapping), target_url=target_url)

    assert result.returncode != 0
    assert _counts(target_url)["users"] == 0
    assert list(tmp_path.glob("legacy.sqlite3.*.bak")) == []


def test_duplicate_mapped_student_numbers_fail_before_target_write(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    target_url = _create_target(tmp_path / "target.sqlite3")
    mapping = tmp_path / "users.json"
    _create_legacy_database(source)
    mapping.write_text(
        json.dumps(
            {
                "student@example.com": {"student_no": "SAME"},
                "teacher@example.com": {"student_no": "SAME"},
            }
        ),
        encoding="utf-8",
    )

    result = _run_cli(source, "--user-mapping", str(mapping), target_url=target_url)

    assert result.returncode != 0
    assert _counts(target_url)["users"] == 0


def test_orphan_foreign_key_fails_without_partial_import(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    target_url = _create_target(tmp_path / "target.sqlite3")
    _create_legacy_database(source)
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE history SET user_id = 999")

    result = _run_cli(source, target_url=target_url)

    assert result.returncode != 0
    assert _counts(target_url) == {
        "users": 0,
        "sessions": 0,
        "history": 0,
        "settings": 0,
        "proof": 0,
    }


def test_bad_source_json_rolls_back_all_target_rows(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    target_url = _create_target(tmp_path / "target.sqlite3")
    _create_legacy_database(source)
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE history SET nodes_json = '{broken'")

    result = _run_cli(source, target_url=target_url)

    assert result.returncode != 0
    assert _counts(target_url)["users"] == 0
    # 非 dry-run 的恢复快照必须先于解析建立，解析失败也不能删掉它。
    backup = next(tmp_path.glob("legacy.sqlite3.*.bak"))
    with sqlite3.connect(backup) as connection:
        assert connection.execute(
            "SELECT nodes_json FROM history WHERE id = 'history-1'"
        ).fetchone()[0] == "{broken"


def test_count_mismatch_rolls_back_the_single_transaction(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    target_url = _create_target(tmp_path / "target.sqlite3", ignore_history_inserts=True)
    _create_legacy_database(source)

    result = _run_cli(source, target_url=target_url)

    assert result.returncode != 0
    assert _counts(target_url) == {
        "users": 0,
        "sessions": 0,
        "history": 0,
        "settings": 0,
        "proof": 0,
    }
    backup = next(tmp_path.glob("legacy.sqlite3.*.bak"))
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2


def test_target_foreign_key_mutation_rolls_back_even_when_counts_match(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    target_url = _create_target(
        tmp_path / "target.sqlite3",
        corrupt_history_owner=True,
    )
    _create_legacy_database(source)

    result = _run_cli(source, target_url=target_url)

    assert result.returncode != 0
    assert _counts(target_url) == {
        "users": 0,
        "sessions": 0,
        "history": 0,
        "settings": 0,
        "proof": 0,
    }


def test_second_run_fails_without_overwriting_existing_rows(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    target_url = _create_target(tmp_path / "target.sqlite3")
    _create_legacy_database(source)

    first = _run_cli(source, target_url=target_url)
    second = _run_cli(source, target_url=target_url)

    assert first.returncode == 0, first.stderr
    assert second.returncode != 0
    assert _counts(target_url)["users"] == 2
    backups = list(tmp_path.glob("legacy.sqlite3.*.bak"))
    assert len(backups) == 2
    for backup in backups:
        with sqlite3.connect(backup) as connection:
            assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2


def test_backup_includes_committed_wal_rows(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    target_url = _create_target(tmp_path / "target.sqlite3")
    _create_legacy_database(source)
    writer = sqlite3.connect(source)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        writer.execute(
            "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (3, "wal@example.com", "wal-hash", "2025-01-07T00:00:00+00:00"),
        )
        writer.commit()

        result = _run_cli(source, target_url=target_url)

        assert result.returncode == 0, result.stderr
        backup = next(tmp_path.glob("legacy.sqlite3.*.bak"))
        with sqlite3.connect(backup) as connection:
            assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3
    finally:
        writer.close()


def test_non_dry_run_loads_only_the_backup_snapshot(monkeypatch, tmp_path, capsys):
    source = tmp_path / "legacy.sqlite3"
    target_url = _create_target(tmp_path / "target.sqlite3")
    _create_legacy_database(source)
    original_backup = migration._create_backup

    def concurrent_write_then_backup(path: Path) -> Path:
        # 精确模拟旧桌面进程在“准备备份”边界刚提交一条记录。
        with sqlite3.connect(path) as connection:
            connection.execute(
                "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (3, "concurrent@example.com", "concurrent-hash", "2025-01-07T00:00:00+00:00"),
            )
        return original_backup(path)

    monkeypatch.setattr(migration, "_create_backup", concurrent_write_then_backup)
    monkeypatch.setenv("SNAPSHOT_TARGET_URL", target_url)

    exit_code = migration.main(
        [
            "--sqlite",
            str(source),
            "--database-url-env",
            "SNAPSHOT_TARGET_URL",
        ]
    )

    backup = next(tmp_path.glob("legacy.sqlite3.*.bak"))
    with sqlite3.connect(backup) as connection:
        backup_users = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    assert exit_code == 0
    assert backup_users == 3
    assert _counts(target_url)["users"] == backup_users
    assert "users: 3" in capsys.readouterr().out


def test_source_pdf_paths_are_sanitized_before_central_storage(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    target_url = _create_target(tmp_path / "target.sqlite3")
    _create_legacy_database(source)
    unsafe_meta = {
        "status": "ready",
        "available": True,
        "pdf_path": r"C:\Users\alice\private\secret.pdf",
        "source_path": "/home/alice/private/source.tex",
        "log_path": r"D:\build\private\compile.log",
        "pdf_url": "/api/v2/jobs/history-1/source-pdf",
        "compile_log_url": "/api/v2/jobs/history-1/source-pdf/log",
        "internal_directory": "/srv/private/jobs/history-1",
    }
    with sqlite3.connect(source) as connection:
        connection.execute("ALTER TABLE history ADD COLUMN source_pdf_json TEXT")
        connection.execute(
            "UPDATE history SET source_pdf_json = ? WHERE id = ?",
            (json.dumps(unsafe_meta), "history-1"),
        )

    result = _run_cli(source, target_url=target_url)

    assert result.returncode == 0, result.stderr
    engine = create_engine(target_url)
    with Session(engine) as session:
        stored = session.get(History, "history-1").source_pdf_json
        assert stored == {
            "status": "ready",
            "available": True,
            "error": None,
            "pdf_url": "/api/v2/jobs/history-1/source-pdf",
            "compile_log_url": "/api/v2/jobs/history-1/source-pdf/log",
            "pdf_name": "secret.pdf",
            "source_name": "source.tex",
            "log_name": "compile.log",
        }
    engine.dispose()


def test_nonempty_target_fails_before_overwriting_unrelated_user(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    target_url = _create_target(tmp_path / "target.sqlite3")
    _create_legacy_database(source)
    engine = create_engine(target_url)
    with engine.begin() as connection:
        connection.execute(
            User.__table__.insert(),
            {
                "id": 99,
                "student_no": "EXISTING",
                "email": "existing@example.com",
                "display_name": "existing",
                "role": "student",
                "password_hash": "existing-hash",
                "initial_password_pending": False,
                "is_active": True,
                "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
                "updated_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
            },
        )
    engine.dispose()

    result = _run_cli(source, target_url=target_url)

    assert result.returncode != 0
    engine = create_engine(target_url)
    with Session(engine) as session:
        users = session.scalars(select(User)).all()
        assert [(user.id, user.student_no) for user in users] == [(99, "EXISTING")]
    engine.dispose()


def test_missing_required_source_table_fails(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    target_url = _create_target(tmp_path / "target.sqlite3")
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")

    result = _run_cli(source, target_url=target_url)

    assert result.returncode != 0
    assert _counts(target_url)["users"] == 0


def test_missing_database_environment_variable_fails_without_backup(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    _create_legacy_database(source)

    result = _run_cli(source, env_name="DEFINITELY_MISSING_DATABASE_URL")

    assert result.returncode != 0
    assert "DEFINITELY_MISSING_DATABASE_URL" in result.stderr
    assert list(tmp_path.glob("legacy.sqlite3.*.bak")) == []
