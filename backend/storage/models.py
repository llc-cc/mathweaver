"""MathWeaver Web 认证与学习数据的 SQLAlchemy 模型。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
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
    # MySQL 默认排序规则不区分大小写，学号列单独使用二进制排序保证精确匹配。
    student_no: Mapped[str | None] = mapped_column(
        String(64).with_variant(mysql.VARCHAR(length=64, collation="utf8mb4_bin"), "mysql"),
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
        Index("ix_teaching_classes_public_id", "public_id", unique=True),
        Index("ix_teaching_classes_invite_code", "invite_code", unique=True),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    term: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invite_code: Mapped[str] = mapped_column(String(32), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ClassMembership(Base):
    __tablename__ = "class_memberships"
    __table_args__ = (
        Index("ix_class_memberships_class_student", "teaching_class_id", "student_id", unique=True),
        Index(
            "ix_class_memberships_class_student_number",
            "teaching_class_id",
            "student_number",
            unique=True,
        ),
        CheckConstraint("role IN ('teacher', 'student')", name="ck_class_memberships_role"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    teaching_class_id: Mapped[int] = mapped_column(
        ForeignKey("teaching_classes.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="student")
    student_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    student_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    source_markdown: Mapped[str | None] = mapped_column(
        Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=True
    )
    latex_macros: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_pdf_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="done")
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stage_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stage_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_stages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stages_done_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source_format: Mapped[str] = mapped_column(String(32), nullable=False, default="markdown")
    source_origin: Mapped[str] = mapped_column(String(64), nullable=False, default="markdown")
    experimental_logic_ir: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 只记录所属任务前缀，访问凭据和服务器绝对路径都不进入业务数据库。
    object_storage_prefix: Mapped[str | None] = mapped_column(String(1024), nullable=True)
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


class EducationSnapshot(Base):
    __tablename__ = "education_snapshots"
    __table_args__ = (
        Index("ix_education_snapshots_class_created", "teaching_class_id", "created_at"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    teaching_class_id: Mapped[int] = mapped_column(
        ForeignKey("teaching_classes.id", ondelete="CASCADE"), nullable=False
    )
    source_graph_id: Mapped[str | None] = mapped_column(
        ForeignKey("history.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    nodes_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    edges_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    source_markdown: Mapped[str | None] = mapped_column(
        Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=True
    )
    latex_macros_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    source_pdf_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class EducationAssignment(Base):
    __tablename__ = "education_assignments"
    __table_args__ = (
        Index("ix_education_assignments_class_updated", "teaching_class_id", "updated_at"),
        CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_education_assignments_status"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    teaching_class_id: Mapped[int] = mapped_column(
        ForeignKey("teaching_classes.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("education_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    target_node_id: Mapped[int] = mapped_column(Integer, nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    base_path_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    grades_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class EducationStudentPath(Base):
    __tablename__ = "education_student_paths"
    __table_args__ = (MYSQL_TABLE_OPTIONS,)

    assignment_id: Mapped[str] = mapped_column(
        ForeignKey("education_assignments.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    path_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class EducationNodeProgress(Base):
    __tablename__ = "education_node_progress"
    __table_args__ = (
        Index("ix_education_progress_assignment_user", "assignment_id", "user_id"),
        CheckConstraint(
            "state IN ('not_started', 'in_progress', 'mastered', 'needs_review')",
            name="ck_education_node_progress_state",
        ),
        CheckConstraint(
            "mastery_source IN ('self', 'diagnostic', 'assessment', 'teacher')",
            name="ck_education_node_progress_source",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    assignment_id: Mapped[str] = mapped_column(
        ForeignKey("education_assignments.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    node_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    mastery_source: Mapped[str] = mapped_column(String(32), nullable=False, default="self")
    diagnostic_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class EducationDiagnostic(Base):
    __tablename__ = "education_diagnostics"
    __table_args__ = (
        Index("ix_education_diagnostics_assignment_user_node", "assignment_id", "user_id", "node_id"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    assignment_id: Mapped[str] = mapped_column(
        ForeignKey("education_assignments.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    node_id: Mapped[int] = mapped_column(Integer, nullable=False)
    question_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class EducationAssessmentNode(Base):
    __tablename__ = "education_assessment_nodes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'ready', 'failed', 'exempt')",
            name="ck_education_assessment_nodes_status",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    assignment_id: Mapped[str] = mapped_column(
        ForeignKey("education_assignments.id", ondelete="CASCADE"), primary_key=True
    )
    node_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class EducationAssessmentQuestion(Base):
    __tablename__ = "education_assessment_questions"
    __table_args__ = (
        UniqueConstraint("assignment_id", "node_id", "sort_order", name="uq_education_question_order"),
        Index("ix_education_questions_assignment_node", "assignment_id", "node_id", "sort_order"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    assignment_id: Mapped[str] = mapped_column(
        ForeignKey("education_assignments.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    focus: Mapped[str] = mapped_column(Text, nullable=False)
    expected_points_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    reference_answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    max_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class EducationAssessmentAttempt(Base):
    __tablename__ = "education_assessment_attempts"
    __table_args__ = (
        UniqueConstraint("assignment_id", "user_id", "node_id", name="uq_education_attempt_node"),
        Index("ix_education_attempts_assignment_user_node", "assignment_id", "user_id", "node_id"),
        CheckConstraint("status IN ('draft', 'completed')", name="ck_education_attempts_status"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    assignment_id: Mapped[str] = mapped_column(
        ForeignKey("education_assignments.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    node_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    answers_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EducationAssignmentSubmission(Base):
    __tablename__ = "education_assignment_submissions"
    __table_args__ = (
        UniqueConstraint("assignment_id", "user_id", name="uq_education_submission_student"),
        Index("ix_education_submissions_assignment_status_user", "assignment_id", "status", "user_id"),
        CheckConstraint(
            "status IN ('submitted', 'review_draft', 'finalized', 'released')",
            name="ck_education_submissions_status",
        ),
        CheckConstraint(
            "ai_status IN ('not_started', 'running', 'ready', 'failed')",
            name="ck_education_submissions_ai_status",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    assignment_id: Mapped[str] = mapped_column(
        ForeignKey("education_assignments.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    ai_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_started")
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    ai_suggested_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    teacher_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    teacher_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ai_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EducationSubmissionQuestionGrade(Base):
    __tablename__ = "education_submission_question_grades"
    __table_args__ = (
        Index("ix_education_submission_grades_submission_node", "submission_id", "node_id"),
        MYSQL_TABLE_OPTIONS,
    )

    submission_id: Mapped[str] = mapped_column(
        ForeignKey("education_assignment_submissions.id", ondelete="CASCADE"), primary_key=True
    )
    question_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_id: Mapped[int] = mapped_column(Integer, nullable=False)
    max_score: Mapped[float] = mapped_column(Float, nullable=False)
    student_answer: Mapped[str] = mapped_column(Text, nullable=False)
    reference_answer: Mapped[str] = mapped_column(Text, nullable=False)
    expected_points_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    matrix_report_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    ai_result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    ai_suggested_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    teacher_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    teacher_feedback: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class EducationAiUsage(Base):
    __tablename__ = "education_ai_usage"
    __table_args__ = (MYSQL_TABLE_OPTIONS,)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    usage_day: Mapped[date] = mapped_column(Date, primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class EducationAiTask(Base):
    __tablename__ = "education_ai_tasks"
    __table_args__ = (
        Index("ix_education_ai_tasks_user_created", "user_id", "created_at"),
        Index("ix_education_ai_tasks_task_key", "task_key", unique=True),
        CheckConstraint("status IN ('running', 'done', 'failed')", name="ck_education_ai_tasks_status"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_key: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    task_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class EducationNodeIdentity(Base):
    __tablename__ = "education_node_identities"
    __table_args__ = (
        Index(
            "ix_education_node_identities_class_global",
            "teaching_class_id",
            "global_id",
            unique=True,
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    teaching_class_id: Mapped[int] = mapped_column(
        ForeignKey("teaching_classes.id", ondelete="CASCADE"), nullable=False
    )
    global_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class EducationNodeOccurrence(Base):
    __tablename__ = "education_node_occurrences"
    __table_args__ = (
        Index("ix_education_node_occurrences_identity", "canonical_node_id"),
        MYSQL_TABLE_OPTIONS,
    )

    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("education_snapshots.id", ondelete="CASCADE"), primary_key=True
    )
    node_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_node_id: Mapped[str] = mapped_column(
        ForeignKey("education_node_identities.id", ondelete="CASCADE"), nullable=False
    )
    global_id: Mapped[str] = mapped_column(String(128), nullable=False)


class LearningInteraction(Base):
    __tablename__ = "learning_interactions"
    __table_args__ = (
        UniqueConstraint("user_id", "assignment_id", "client_interaction_id", name="uq_learning_client_interaction"),
        Index("ix_learning_interactions_class_user_created", "teaching_class_id", "user_id", "created_at"),
        Index(
            "ix_learning_interactions_node",
            "teaching_class_id",
            "user_id",
            "canonical_node_id",
            "created_at",
        ),
        CheckConstraint(
            "classification_status IN ('classified', 'pending')",
            name="ck_learning_interactions_classification",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_interaction_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    teaching_class_id: Mapped[int] = mapped_column(
        ForeignKey("teaching_classes.id", ondelete="CASCADE"), nullable=False
    )
    assignment_id: Mapped[str] = mapped_column(
        ForeignKey("education_assignments.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("education_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    canonical_node_id: Mapped[str] = mapped_column(
        ForeignKey("education_node_identities.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    user_proof: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_response: Mapped[str] = mapped_column(Text, nullable=False)
    context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    context_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    classification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class LearningEvidence(Base):
    __tablename__ = "learning_evidence"
    __table_args__ = (
        Index("ix_learning_evidence_class_user_status", "teaching_class_id", "user_id", "status", "updated_at"),
        CheckConstraint(
            "kind IN ('goal', 'understanding', 'misconception', 'gap', 'used_node', 'hint', 'unresolved_question', 'strategy')",
            name="ck_learning_evidence_kind",
        ),
        CheckConstraint(
            "status IN ('open', 'confirmed', 'resolved', 'retracted')",
            name="ck_learning_evidence_status",
        ),
        CheckConstraint("severity IN ('low', 'medium', 'high')", name="ck_learning_evidence_severity"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    interaction_id: Mapped[str] = mapped_column(
        ForeignKey("learning_interactions.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    teaching_class_id: Mapped[int] = mapped_column(
        ForeignKey("teaching_classes.id", ondelete="CASCADE"), nullable=False
    )
    canonical_node_id: Mapped[str] = mapped_column(
        ForeignKey("education_node_identities.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class LearningEvidenceNode(Base):
    __tablename__ = "learning_evidence_nodes"
    __table_args__ = (
        Index("ix_learning_evidence_nodes_node_role", "canonical_node_id", "relation_role", "weight"),
        CheckConstraint(
            "relation_role IN ('direct', 'prerequisite_risk', 'successor_risk', 'related')",
            name="ck_learning_evidence_nodes_role",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("learning_evidence.id", ondelete="CASCADE"), primary_key=True
    )
    canonical_node_id: Mapped[str] = mapped_column(
        ForeignKey("education_node_identities.id", ondelete="CASCADE"), primary_key=True
    )
    relation_role: Mapped[str] = mapped_column(String(32), nullable=False)
    relation_path_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    weight: Mapped[float] = mapped_column(Float, nullable=False)


class LearningEvidenceFeedback(Base):
    __tablename__ = "learning_evidence_feedback"
    __table_args__ = (MYSQL_TABLE_OPTIONS,)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("learning_evidence.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_status: Mapped[str] = mapped_column(String(32), nullable=False)
    new_status: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class StudentNodeModel(Base):
    __tablename__ = "student_node_models"
    __table_args__ = (
        Index("ix_student_node_models_class_user", "teaching_class_id", "user_id", "updated_at"),
        CheckConstraint(
            "mastery_state IN ('unknown', 'learning', 'mastered', 'needs_review')",
            name="ck_student_node_models_mastery",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    teaching_class_id: Mapped[int] = mapped_column(
        ForeignKey("teaching_classes.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    canonical_node_id: Mapped[str] = mapped_column(
        ForeignKey("education_node_identities.id", ondelete="CASCADE"), primary_key=True
    )
    mastery_state: Mapped[str] = mapped_column(String(32), nullable=False)
    direct_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    risk_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    open_evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class LearningContextSummary(Base):
    __tablename__ = "learning_context_summaries"
    __table_args__ = (
        CheckConstraint("scope_type IN ('node', 'course')", name="ck_learning_context_scope"),
        MYSQL_TABLE_OPTIONS,
    )

    teaching_class_id: Mapped[int] = mapped_column(
        ForeignKey("teaching_classes.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    source_watermark: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
