from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from storage import database
from storage.database import configure_database, get_engine, session_scope
from storage.models import History, StorageOutbox, User


def create_teacher(email: str = "teacher@example.edu") -> User:
    return User.create_account(
        role="teacher",
        student_no=None,
        email=email,
        display_name="Teacher",
        password_hash="hashed-password",
    )


def test_duplicate_non_null_student_numbers_fail():
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add_all(
                [
                    User.create_account("student", "S1001", "a@example.edu", "A", "hash-a"),
                    User.create_account("student", "S1001", "b@example.edu", "B", "hash-b"),
                ]
            )


def test_duplicate_non_null_emails_fail():
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add_all([create_teacher(), create_teacher("teacher@example.edu")])


def test_multiple_null_emails_are_allowed():
    with session_scope() as session:
        session.add_all(
            [
                User.create_account("student", "S1002", None, "A", "hash-a"),
                User.create_account("student", "S1003", None, "B", "hash-b"),
            ]
        )

    with session_scope() as session:
        assert len(session.scalars(select(User)).all()) == 2


def test_history_object_storage_prefix_is_optional() -> None:
    column = History.__table__.c.object_storage_prefix

    assert column.nullable is True
    assert column.type.length == 1024


def test_history_has_versioned_storage_state_columns() -> None:
    columns = History.__table__.c

    assert columns.storage_version.type.length == 32
    assert columns.storage_status.default.arg == "legacy"
    assert columns.storage_checksum.type.length == 64
    assert columns.deleted_at.nullable is True


def test_storage_outbox_idempotency_key_is_unique() -> None:
    duplicate = {
        "user_id": 7,
        "history_id": "job-1",
        "operation": "delete_version",
        "idempotency_key": "delete-version:7:job-1:v1",
        "payload_json": {"version_id": "a" * 32},
    }
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add_all([StorageOutbox(**duplicate), StorageOutbox(**duplicate)])


def test_whitespace_emails_normalize_before_unique_indexing():
    with session_scope() as session:
        session.add_all(
            [
                User.create_account("student", "S1004", "  Student@Example.edu ", "A", "hash-a"),
                User.create_account("student", "S1005", "   ", "B", "hash-b"),
                User.create_account("student", "S1006", "\t", "C", "hash-c"),
            ]
        )

    with session_scope() as session:
        assert set(session.scalars(select(User.email)).all()) == {"student@example.edu", None}


def test_mysql_user_model_and_migration_use_case_sensitive_student_numbers():
    model_ddl = str(CreateTable(User.__table__).compile(dialect=mysql.dialect()))
    assert "student_no VARCHAR(64) COLLATE utf8mb4_bin" in model_ddl
    assert "email VARCHAR(255)" in model_ddl
    assert "COLLATE utf8mb4_unicode_ci" in model_ddl

    environment = os.environ.copy()
    environment["MATHWEAVER_DATABASE_URL"] = "mysql+pymysql://user:password@localhost/mathweaver"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "migrations/alembic.ini",
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "student_no VARCHAR(64) COLLATE utf8mb4_bin" in result.stdout
    assert "email VARCHAR(255)" in result.stdout


def test_student_creation_requires_nonblank_student_number():
    with pytest.raises(ValueError, match="student_no"):
        User.create_account("student", None, "student@example.edu", "Student", "hash")


def test_teacher_and_admin_can_be_created_without_student_number_when_email_present():
    with session_scope() as session:
        session.add_all(
            [
                create_teacher(),
                User.create_account("admin", None, "admin@example.edu", "Admin", "hash-admin"),
            ]
        )

    with session_scope() as session:
        assert {user.role for user in session.scalars(select(User)).all()} == {"teacher", "admin"}


def test_unknown_role_raises_value_error():
    with pytest.raises(ValueError, match="role"):
        User.create_account("guest", None, "guest@example.edu", "Guest", "hash")


def test_session_scope_commits_on_success():
    with session_scope() as session:
        session.add(create_teacher())

    with session_scope() as session:
        assert session.scalar(select(User.email)) == "teacher@example.edu"


def test_session_scope_rolls_back_after_exception():
    with pytest.raises(RuntimeError, match="stop"):
        with session_scope() as session:
            session.add(create_teacher())
            session.flush()
            assert session.scalar(select(User.id)) is not None
            raise RuntimeError("stop")

    with session_scope() as session:
        assert session.scalar(select(User.id)) is None


def test_configure_database_requires_explicit_url(monkeypatch):
    monkeypatch.delenv("MATHWEAVER_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="MATHWEAVER_DATABASE_URL"):
        configure_database()


def test_reconfiguration_disposes_prior_engine_after_success(monkeypatch):
    configure_database("sqlite+pysqlite:///:memory:")
    prior_engine = get_engine()
    disposed = False

    def track_dispose():
        nonlocal disposed
        disposed = True

    monkeypatch.setattr(prior_engine, "dispose", track_dispose)
    original_create_engine = database.create_engine
    monkeypatch.setattr(database, "create_engine", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad URL")))
    with pytest.raises(RuntimeError, match="bad URL"):
        configure_database("sqlite+pysqlite:///:memory:")
    assert get_engine() is prior_engine
    assert not disposed

    monkeypatch.setattr(database, "create_engine", original_create_engine)
    configure_database("sqlite+pysqlite:///:memory:")
    assert disposed


def test_online_alembic_requires_database_url():
    environment = os.environ.copy()
    environment.pop("MATHWEAVER_DATABASE_URL", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "migrations/alembic.ini",
            "upgrade",
            "head",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "MATHWEAVER_DATABASE_URL" in result.stderr
