"""MathWeaver Web 认证与学习数据的 SQLAlchemy 模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, validates


def utc_now() -> datetime:
    """统一存储带 UTC 时区的时间，避免服务器本地时区影响审计结果。"""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """所有持久化表共享的 SQLAlchemy 2 声明式基类。"""


MYSQL_TABLE_OPTIONS = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}
USER_ROLES = {"student", "teacher", "admin"}


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_student_no", "student_no", unique=True),
        Index("ix_users_email", "email", unique=True),
        CheckConstraint("role IN ('student', 'teacher', 'admin')", name="ck_users_role"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # MySQL 表默认排序规则不区分大小写，学号列单独使用二进制排序保证精确匹配。
    student_no: Mapped[str | None] = mapped_column(
        String(64).with_variant(
            mysql.VARCHAR(length=64, collation="utf8mb4_bin"), "mysql"
        ),
        nullable=True,
    )
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    initial_password_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    @validates("email")
    def normalize_email(self, _key: str, value: str | None) -> str | None:
        normalized = value.strip().lower() if value else ""
        return normalized or None

    @classmethod
    def create_account(
        cls,
        role: str,
        student_no: str | None,
        email: str | None,
        display_name: str,
        password_hash: str,
    ) -> "User":
        """构造经角色约束校验的新账号，持久化由调用方事务负责。"""
        if role not in USER_ROLES:
            raise ValueError("role must be student, teacher, or admin")
        normalized_student_no = student_no.strip() if student_no else None
        if role == "student" and not normalized_student_no:
            raise ValueError("student_no is required for student accounts")
        return cls(
            role=role,
            student_no=normalized_student_no,
            email=email,
            display_name=display_name,
            password_hash=password_hash,
        )


class LoginSession(Base):
    __tablename__ = "login_sessions"
    __table_args__ = (
        Index("ix_login_sessions_user_expires", "user_id", "expires_at"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 会话令牌只存摘要，原始 Bearer 值不得进入持久化数据或审计记录。
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (Index("ix_courses_code", "code", unique=True), MYSQL_TABLE_OPTIONS)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class TeachingClass(Base):
    __tablename__ = "teaching_classes"
    __table_args__ = (
        Index("ix_teaching_classes_course_id", "course_id"),
        Index("ix_teaching_classes_teacher_id", "teacher_id"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    term: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ClassMembership(Base):
    __tablename__ = "class_memberships"
    __table_args__ = (
        Index("ix_class_memberships_class_student", "teaching_class_id", "student_id", unique=True),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    teaching_class_id: Mapped[int] = mapped_column(
        ForeignKey("teaching_classes.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class History(Base):
    __tablename__ = "history"
    __table_args__ = (
        Index("ix_history_user_created_at", "user_id", "created_at"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    node_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    edge_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    nodes_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    edges_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    source_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    latex_macros: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_pdf_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="done")
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stage_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stage_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_stages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stages_done_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source_format: Mapped[str] = mapped_column(String(32), nullable=False, default="markdown")
    experimental_logic_ir: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class UserSettings(Base):
    __tablename__ = "user_settings"
    __table_args__ = (MYSQL_TABLE_OPTIONS,)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    llm_api_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    llm_model: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    llm_api_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    llm_configs_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ProofWorkspace(Base):
    __tablename__ = "proof_workspaces"
    __table_args__ = (MYSQL_TABLE_OPTIONS,)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    graph_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_proof: Mapped[str] = mapped_column(Text, nullable=False, default="")
    versions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    ai_messages_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    imports_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_actor_created_at", "actor_id", "created_at"),
        Index("ix_audit_logs_subject", "subject_type", "subject_id"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
