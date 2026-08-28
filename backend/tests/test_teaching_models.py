"""教学与学习域模型约束测试。"""

from __future__ import annotations

from sqlalchemy import JSON
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from storage.models import (
    Base,
    ClassMembership,
    EducationAssignmentSubmission,
    EducationNodeIdentity,
    EducationNodeOccurrence,
    EducationSnapshot,
    History,
    LearningEvidence,
    TeachingClass,
)


TEACHING_TABLES = {
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
}


def test_metadata_contains_all_teaching_domain_tables() -> None:
    assert TEACHING_TABLES <= set(Base.metadata.tables)


def test_teaching_class_has_public_identity_and_archive_state() -> None:
    columns = TeachingClass.__table__.c
    indexes = {index.name: index for index in TeachingClass.__table__.indexes}

    assert columns.public_id.type.length == 64
    assert columns.invite_code.type.length == 32
    assert columns.archived_at.nullable is True
    assert indexes["ix_teaching_classes_public_id"].unique is True
    assert indexes["ix_teaching_classes_invite_code"].unique is True


def test_membership_supports_role_student_profile_and_soft_remove() -> None:
    columns = ClassMembership.__table__.c

    assert columns.role.nullable is False
    assert columns.student_name.nullable is True
    assert columns.student_number.nullable is True
    assert columns.removed_at.nullable is True


def test_snapshot_is_immutable_graph_payload() -> None:
    columns = EducationSnapshot.__table__.c

    assert isinstance(columns.nodes_json.type, JSON)
    assert isinstance(columns.edges_json.type, JSON)
    assert "updated_at" not in columns
    assert next(iter(columns.teaching_class_id.foreign_keys)).ondelete == "CASCADE"


def test_graph_markdown_columns_support_official_dataset_size() -> None:
    history_ddl = str(CreateTable(History.__table__).compile(dialect=mysql.dialect()))
    snapshot_ddl = str(CreateTable(EducationSnapshot.__table__).compile(dialect=mysql.dialect()))

    assert "source_markdown LONGTEXT" in history_ddl
    assert "source_markdown LONGTEXT" in snapshot_ddl


def test_node_identity_is_unique_within_class() -> None:
    unique_columns = {
        tuple(index.columns.keys())
        for index in EducationNodeIdentity.__table__.indexes
        if index.unique
    }

    assert ("teaching_class_id", "global_id") in unique_columns


def test_occurrence_is_unique_within_snapshot_and_node_number() -> None:
    primary_keys = tuple(EducationNodeOccurrence.__table__.primary_key.columns.keys())

    assert primary_keys == ("snapshot_id", "node_id")


def test_submission_and_evidence_foreign_keys_are_student_scoped() -> None:
    submission_columns = EducationAssignmentSubmission.__table__.c
    evidence_columns = LearningEvidence.__table__.c

    assert next(iter(submission_columns.user_id.foreign_keys)).ondelete == "CASCADE"
    assert next(iter(submission_columns.assignment_id.foreign_keys)).ondelete == "CASCADE"
    assert next(iter(evidence_columns.user_id.foreign_keys)).ondelete == "CASCADE"
    assert next(iter(evidence_columns.interaction_id.foreign_keys)).ondelete == "CASCADE"
