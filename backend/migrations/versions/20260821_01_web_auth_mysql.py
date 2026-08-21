"""Create the Web authentication and learning persistence foundation.

Revision ID: 20260821_01
Revises:
Create Date: 2026-08-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "20260821_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_OPTIONS = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "student_no",
            sa.String(length=64).with_variant(
                mysql.VARCHAR(length=64, collation="utf8mb4_bin"), "mysql"
            ),
            nullable=True,
        ),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("initial_password_pending", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('student', 'teacher', 'admin')", name="ck_users_role"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_users_student_no", "users", ["student_no"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "login_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_login_sessions_user_expires", "login_sessions", ["user_id", "expires_at"])

    op.create_table(
        "courses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_courses_code", "courses", ["code"], unique=True)

    op.create_table(
        "teaching_classes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("teacher_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("term", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["teacher_id"], ["users.id"], ondelete="RESTRICT"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_teaching_classes_course_id", "teaching_classes", ["course_id"])
    op.create_index("ix_teaching_classes_teacher_id", "teaching_classes", ["teacher_id"])

    op.create_table(
        "class_memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("teaching_class_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["teaching_class_id"], ["teaching_classes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_class_memberships_class_student",
        "class_memberships",
        ["teaching_class_id", "student_id"],
        unique=True,
    )

    op.create_table(
        "history",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("node_count", sa.Integer(), nullable=False),
        sa.Column("edge_count", sa.Integer(), nullable=False),
        sa.Column("nodes_json", sa.JSON(), nullable=False),
        sa.Column("edges_json", sa.JSON(), nullable=False),
        sa.Column("source_markdown", sa.Text(), nullable=True),
        sa.Column("latex_macros", sa.Text(), nullable=True),
        sa.Column("source_pdf_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=True),
        sa.Column("stage_label", sa.String(length=255), nullable=True),
        sa.Column("stage_index", sa.Integer(), nullable=False),
        sa.Column("total_stages", sa.Integer(), nullable=False),
        sa.Column("stages_done_json", sa.JSON(), nullable=False),
        sa.Column("source_format", sa.String(length=32), nullable=False),
        sa.Column("experimental_logic_ir", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_history_user_created_at", "history", ["user_id", "created_at"])

    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("llm_api_url", sa.String(length=2048), nullable=False),
        sa.Column("llm_model", sa.String(length=255), nullable=False),
        sa.Column("llm_api_key", sa.Text(), nullable=False),
        sa.Column("llm_configs_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        **TABLE_OPTIONS,
    )

    op.create_table(
        "proof_workspaces",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("graph_id", sa.String(length=64), primary_key=True),
        sa.Column("node_id", sa.Integer(), primary_key=True),
        sa.Column("user_proof", sa.Text(), nullable=False),
        sa.Column("versions_json", sa.JSON(), nullable=False),
        sa.Column("ai_messages_json", sa.JSON(), nullable=False),
        sa.Column("imports_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        **TABLE_OPTIONS,
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("subject_type", sa.String(length=128), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_audit_logs_actor_created_at", "audit_logs", ["actor_id", "created_at"])
    op.create_index("ix_audit_logs_subject", "audit_logs", ["subject_type", "subject_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_subject", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("proof_workspaces")
    op.drop_table("user_settings")
    op.drop_index("ix_history_user_created_at", table_name="history")
    op.drop_table("history")
    op.drop_index("ix_class_memberships_class_student", table_name="class_memberships")
    op.drop_table("class_memberships")
    op.drop_index("ix_teaching_classes_teacher_id", table_name="teaching_classes")
    op.drop_index("ix_teaching_classes_course_id", table_name="teaching_classes")
    op.drop_table("teaching_classes")
    op.drop_index("ix_courses_code", table_name="courses")
    op.drop_table("courses")
    op.drop_index("ix_login_sessions_user_expires", table_name="login_sessions")
    op.drop_table("login_sessions")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_student_no", table_name="users")
    op.drop_table("users")
