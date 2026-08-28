"""Add the teaching, assessment, and learning-context domain.

Revision ID: 20260828_03
Revises: 20260821_02
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260828_03"
down_revision: str | None = "20260821_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_OPTIONS = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}
TEACHING_TABLES = (
    "education_snapshots",
    "education_assignments",
    "education_student_paths",
    "education_node_progress",
    "education_diagnostics",
    "education_assessment_nodes",
    "education_assessment_questions",
    "education_assessment_attempts",
    "education_assignment_submissions",
    "education_submission_question_grades",
    "education_ai_usage",
    "education_ai_tasks",
    "education_node_identities",
    "education_node_occurrences",
    "learning_interactions",
    "learning_evidence",
    "learning_evidence_nodes",
    "learning_evidence_feedback",
    "student_node_models",
    "learning_context_summaries",
)


def upgrade() -> None:
    _extend_base_tables()
    _create_assignment_tables()
    _create_learning_tables()


def downgrade() -> None:
    for table_name in reversed(TEACHING_TABLES):
        op.drop_table(table_name)

    op.drop_index("ix_class_memberships_class_student_number", table_name="class_memberships")
    op.drop_column("class_memberships", "removed_at")
    op.drop_column("class_memberships", "student_number")
    op.drop_column("class_memberships", "student_name")
    op.drop_constraint("ck_class_memberships_role", "class_memberships", type_="check")
    op.drop_column("class_memberships", "role")
    op.drop_index("ix_teaching_classes_invite_code", table_name="teaching_classes")
    op.drop_index("ix_teaching_classes_public_id", table_name="teaching_classes")
    op.drop_column("teaching_classes", "archived_at")
    op.drop_column("teaching_classes", "invite_code")
    op.drop_column("teaching_classes", "public_id")
    op.drop_column("history", "source_origin")


def _extend_base_tables() -> None:
    """只增加兼容列；先回填再收紧非空约束，保护已有班级数据。"""
    op.add_column("teaching_classes", sa.Column("public_id", sa.String(length=64), nullable=True))
    op.add_column("teaching_classes", sa.Column("invite_code", sa.String(length=32), nullable=True))
    op.add_column("teaching_classes", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text("UPDATE teaching_classes SET public_id = CONCAT('legacy-', id) WHERE public_id IS NULL"))
    op.execute(
        sa.text(
            "UPDATE teaching_classes "
            "SET invite_code = UPPER(CONCAT('L', LPAD(HEX(id), 10, '0'))) "
            "WHERE invite_code IS NULL"
        )
    )
    op.alter_column("teaching_classes", "public_id", existing_type=sa.String(length=64), nullable=False)
    op.alter_column("teaching_classes", "invite_code", existing_type=sa.String(length=32), nullable=False)
    op.create_index("ix_teaching_classes_public_id", "teaching_classes", ["public_id"], unique=True)
    op.create_index("ix_teaching_classes_invite_code", "teaching_classes", ["invite_code"], unique=True)

    op.add_column(
        "class_memberships",
        sa.Column("role", sa.String(length=16), nullable=False, server_default="student"),
    )
    op.add_column("class_memberships", sa.Column("student_name", sa.String(length=255), nullable=True))
    op.add_column("class_memberships", sa.Column("student_number", sa.String(length=64), nullable=True))
    op.add_column("class_memberships", sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_class_memberships_role",
        "class_memberships",
        "role IN ('teacher', 'student')",
    )
    op.create_index(
        "ix_class_memberships_class_student_number",
        "class_memberships",
        ["teaching_class_id", "student_number"],
        unique=True,
    )

    op.add_column(
        "history",
        sa.Column("source_origin", sa.String(length=64), nullable=False, server_default="markdown"),
    )
    # 正式教材原文超过 MySQL TEXT 上限，使用无损扩容保证图谱可完整导入。
    op.alter_column(
        "history",
        "source_markdown",
        existing_type=sa.Text(),
        type_=mysql.LONGTEXT(),
        existing_nullable=True,
    )


def _create_assignment_tables() -> None:
    op.create_table(
        "education_snapshots",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("teaching_class_id", sa.Integer(), nullable=False),
        sa.Column("source_graph_id", sa.String(length=64), nullable=True),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("nodes_json", sa.JSON(), nullable=False),
        sa.Column("edges_json", sa.JSON(), nullable=False),
        sa.Column("source_markdown", mysql.LONGTEXT(), nullable=True),
        sa.Column("latex_macros_json", sa.JSON(), nullable=True),
        sa.Column("source_pdf_json", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["teaching_class_id"], ["teaching_classes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_graph_id"], ["history.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_education_snapshots_class_created",
        "education_snapshots",
        ["teaching_class_id", "created_at"],
    )

    op.create_table(
        "education_assignments",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("teaching_class_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("target_node_id", sa.Integer(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("base_path_json", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grades_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_education_assignments_status",
        ),
        sa.ForeignKeyConstraint(["teaching_class_id"], ["teaching_classes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["education_snapshots.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_education_assignments_class_updated",
        "education_assignments",
        ["teaching_class_id", "updated_at"],
    )

    op.create_table(
        "education_student_paths",
        sa.Column("assignment_id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("path_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assignment_id"], ["education_assignments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        **TABLE_OPTIONS,
    )

    op.create_table(
        "education_node_progress",
        sa.Column("assignment_id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("node_id", sa.Integer(), primary_key=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("mastery_source", sa.String(length=32), nullable=False),
        sa.Column("diagnostic_summary", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('not_started', 'in_progress', 'mastered', 'needs_review')",
            name="ck_education_node_progress_state",
        ),
        sa.CheckConstraint(
            "mastery_source IN ('self', 'diagnostic', 'assessment', 'teacher')",
            name="ck_education_node_progress_source",
        ),
        sa.ForeignKeyConstraint(["assignment_id"], ["education_assignments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_education_progress_assignment_user",
        "education_node_progress",
        ["assignment_id", "user_id"],
    )

    op.create_table(
        "education_diagnostics",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("assignment_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("question_json", sa.JSON(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("result", sa.String(length=64), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assignment_id"], ["education_assignments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_education_diagnostics_assignment_user_node",
        "education_diagnostics",
        ["assignment_id", "user_id", "node_id"],
    )

    op.create_table(
        "education_assessment_nodes",
        sa.Column("assignment_id", sa.String(length=64), primary_key=True),
        sa.Column("node_id", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'failed', 'exempt')",
            name="ck_education_assessment_nodes_status",
        ),
        sa.ForeignKeyConstraint(["assignment_id"], ["education_assignments.id"], ondelete="CASCADE"),
        **TABLE_OPTIONS,
    )

    op.create_table(
        "education_assessment_questions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("assignment_id", sa.String(length=64), nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("focus", sa.Text(), nullable=False),
        sa.Column("expected_points_json", sa.JSON(), nullable=False),
        sa.Column("reference_answer", sa.Text(), nullable=False),
        sa.Column("max_score", sa.Float(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assignment_id"], ["education_assignments.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("assignment_id", "node_id", "sort_order", name="uq_education_question_order"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_education_questions_assignment_node",
        "education_assessment_questions",
        ["assignment_id", "node_id", "sort_order"],
    )

    op.create_table(
        "education_assessment_attempts",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("assignment_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("answers_json", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('draft', 'completed')", name="ck_education_attempts_status"),
        sa.ForeignKeyConstraint(["assignment_id"], ["education_assignments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("assignment_id", "user_id", "node_id", name="uq_education_attempt_node"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_education_attempts_assignment_user_node",
        "education_assessment_attempts",
        ["assignment_id", "user_id", "node_id"],
    )

    op.create_table(
        "education_assignment_submissions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("assignment_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("ai_status", sa.String(length=32), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("ai_suggested_total", sa.Float(), nullable=True),
        sa.Column("teacher_total", sa.Float(), nullable=True),
        sa.Column("teacher_summary", sa.Text(), nullable=False),
        sa.Column("ai_error", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('submitted', 'review_draft', 'finalized', 'released')",
            name="ck_education_submissions_status",
        ),
        sa.CheckConstraint(
            "ai_status IN ('not_started', 'running', 'ready', 'failed')",
            name="ck_education_submissions_ai_status",
        ),
        sa.ForeignKeyConstraint(["assignment_id"], ["education_assignments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("assignment_id", "user_id", name="uq_education_submission_student"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_education_submissions_assignment_status_user",
        "education_assignment_submissions",
        ["assignment_id", "status", "user_id"],
    )

    op.create_table(
        "education_submission_question_grades",
        sa.Column("submission_id", sa.String(length=64), primary_key=True),
        sa.Column("question_id", sa.String(length=64), primary_key=True),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("max_score", sa.Float(), nullable=False),
        sa.Column("student_answer", sa.Text(), nullable=False),
        sa.Column("reference_answer", sa.Text(), nullable=False),
        sa.Column("expected_points_json", sa.JSON(), nullable=False),
        sa.Column("matrix_report_json", sa.JSON(), nullable=False),
        sa.Column("ai_result_json", sa.JSON(), nullable=False),
        sa.Column("ai_suggested_score", sa.Float(), nullable=True),
        sa.Column("teacher_score", sa.Float(), nullable=True),
        sa.Column("teacher_feedback", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["education_assignment_submissions.id"],
            ondelete="CASCADE",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_education_submission_grades_submission_node",
        "education_submission_question_grades",
        ["submission_id", "node_id"],
    )

    op.create_table(
        "education_ai_usage",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("usage_day", sa.Date(), primary_key=True),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        **TABLE_OPTIONS,
    )

    op.create_table(
        "education_ai_tasks",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("task_key", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_kind", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('running', 'done', 'failed')", name="ck_education_ai_tasks_status"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        **TABLE_OPTIONS,
    )
    op.create_index("ix_education_ai_tasks_user_created", "education_ai_tasks", ["user_id", "created_at"])
    op.create_index("ix_education_ai_tasks_task_key", "education_ai_tasks", ["task_key"], unique=True)


def _create_learning_tables() -> None:
    op.create_table(
        "education_node_identities",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("teaching_class_id", sa.Integer(), nullable=False),
        sa.Column("global_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["teaching_class_id"], ["teaching_classes.id"], ondelete="CASCADE"),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_education_node_identities_class_global",
        "education_node_identities",
        ["teaching_class_id", "global_id"],
        unique=True,
    )

    op.create_table(
        "education_node_occurrences",
        sa.Column("snapshot_id", sa.String(length=64), primary_key=True),
        sa.Column("node_id", sa.Integer(), primary_key=True),
        sa.Column("canonical_node_id", sa.String(length=64), nullable=False),
        sa.Column("global_id", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["education_snapshots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["canonical_node_id"],
            ["education_node_identities.id"],
            ondelete="CASCADE",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_education_node_occurrences_identity",
        "education_node_occurrences",
        ["canonical_node_id"],
    )

    op.create_table(
        "learning_interactions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("client_interaction_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("teaching_class_id", sa.Integer(), nullable=False),
        sa.Column("assignment_id", sa.String(length=64), nullable=False),
        sa.Column("snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("canonical_node_id", sa.String(length=64), nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("user_proof", sa.Text(), nullable=False),
        sa.Column("assistant_response", sa.Text(), nullable=False),
        sa.Column("context_version", sa.Integer(), nullable=False),
        sa.Column("context_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("classification_status", sa.String(length=32), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "classification_status IN ('classified', 'pending')",
            name="ck_learning_interactions_classification",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["teaching_class_id"], ["teaching_classes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assignment_id"], ["education_assignments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["education_snapshots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["canonical_node_id"],
            ["education_node_identities.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "user_id",
            "assignment_id",
            "client_interaction_id",
            name="uq_learning_client_interaction",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_learning_interactions_class_user_created",
        "learning_interactions",
        ["teaching_class_id", "user_id", "created_at"],
    )
    op.create_index(
        "ix_learning_interactions_node",
        "learning_interactions",
        ["teaching_class_id", "user_id", "canonical_node_id", "created_at"],
    )

    op.create_table(
        "learning_evidence",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("interaction_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("teaching_class_id", sa.Integer(), nullable=False),
        sa.Column("canonical_node_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("evidence_excerpt", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('goal', 'understanding', 'misconception', 'gap', 'used_node', 'hint', 'unresolved_question', 'strategy')",
            name="ck_learning_evidence_kind",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'confirmed', 'resolved', 'retracted')",
            name="ck_learning_evidence_status",
        ),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high')",
            name="ck_learning_evidence_severity",
        ),
        sa.ForeignKeyConstraint(["interaction_id"], ["learning_interactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["teaching_class_id"], ["teaching_classes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["canonical_node_id"],
            ["education_node_identities.id"],
            ondelete="CASCADE",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_learning_evidence_class_user_status",
        "learning_evidence",
        ["teaching_class_id", "user_id", "status", "updated_at"],
    )

    op.create_table(
        "learning_evidence_nodes",
        sa.Column("evidence_id", sa.String(length=64), primary_key=True),
        sa.Column("canonical_node_id", sa.String(length=64), primary_key=True),
        sa.Column("relation_role", sa.String(length=32), nullable=False),
        sa.Column("relation_path_json", sa.JSON(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "relation_role IN ('direct', 'prerequisite_risk', 'successor_risk', 'related')",
            name="ck_learning_evidence_nodes_role",
        ),
        sa.ForeignKeyConstraint(["evidence_id"], ["learning_evidence.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["canonical_node_id"],
            ["education_node_identities.id"],
            ondelete="CASCADE",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_learning_evidence_nodes_node_role",
        "learning_evidence_nodes",
        ["canonical_node_id", "relation_role", "weight"],
    )

    op.create_table(
        "learning_evidence_feedback",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("evidence_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("previous_status", sa.String(length=32), nullable=False),
        sa.Column("new_status", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["evidence_id"], ["learning_evidence.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        **TABLE_OPTIONS,
    )

    op.create_table(
        "student_node_models",
        sa.Column("teaching_class_id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("canonical_node_id", sa.String(length=64), primary_key=True),
        sa.Column("mastery_state", sa.String(length=32), nullable=False),
        sa.Column("direct_summary_json", sa.JSON(), nullable=False),
        sa.Column("risk_summary_json", sa.JSON(), nullable=False),
        sa.Column("open_evidence_count", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "mastery_state IN ('unknown', 'learning', 'mastered', 'needs_review')",
            name="ck_student_node_models_mastery",
        ),
        sa.ForeignKeyConstraint(["teaching_class_id"], ["teaching_classes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["canonical_node_id"],
            ["education_node_identities.id"],
            ondelete="CASCADE",
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_student_node_models_class_user",
        "student_node_models",
        ["teaching_class_id", "user_id", "updated_at"],
    )

    op.create_table(
        "learning_context_summaries",
        sa.Column("teaching_class_id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("scope_type", sa.String(length=16), primary_key=True),
        sa.Column("scope_id", sa.String(length=128), primary_key=True),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("source_watermark", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope_type IN ('node', 'course')",
            name="ck_learning_context_scope",
        ),
        sa.ForeignKeyConstraint(["teaching_class_id"], ["teaching_classes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        **TABLE_OPTIONS,
    )
