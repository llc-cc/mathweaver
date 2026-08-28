"""服务器现有基础表的 SQLAlchemy 契约测试。"""

from __future__ import annotations

from sqlalchemy import JSON, Text
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from storage.models import Base, History, User


BASE_TABLES = {
    "users",
    "login_sessions",
    "courses",
    "teaching_classes",
    "class_memberships",
    "history",
    "user_settings",
    "proof_workspaces",
    "audit_logs",
}


def test_base_metadata_contains_server_tables() -> None:
    assert set(Base.metadata.tables) == BASE_TABLES


def test_history_keeps_graph_json_and_source_markdown() -> None:
    assert isinstance(History.__table__.c.nodes_json.type, JSON)
    assert isinstance(History.__table__.c.edges_json.type, JSON)
    assert isinstance(History.__table__.c.source_markdown.type, Text)
    assert History.__table__.c.object_storage_prefix.nullable is True


def test_mysql_user_model_uses_exact_student_number_collation() -> None:
    ddl = str(CreateTable(User.__table__).compile(dialect=mysql.dialect()))

    assert "student_no VARCHAR(64) COLLATE utf8mb4_bin" in ddl
    assert "COLLATE utf8mb4_unicode_ci" in ddl


def test_user_factory_normalizes_email_and_rejects_student_without_number() -> None:
    teacher = User.create_account(
        role="teacher",
        student_no=None,
        email="  TEACHER@EXAMPLE.EDU ",
        display_name="Teacher",
        password_hash="hashed-password",
    )

    assert teacher.email == "teacher@example.edu"

    try:
        User.create_account(
            role="student",
            student_no=" ",
            email="student@example.edu",
            display_name="Student",
            password_hash="hashed-password",
        )
    except ValueError as error:
        assert "student_no" in str(error)
    else:
        raise AssertionError("student_no validation did not run")
