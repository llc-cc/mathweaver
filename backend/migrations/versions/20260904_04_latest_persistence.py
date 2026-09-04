"""Bridge the deployed teaching schema to the latest persistence contract.

Revision ID: 20260904_04
Revises: 20260828_03
Create Date: 2026-09-04

The production database already contains the 20260828 teaching schema.  The
new application ships a second, incompatible Alembic root, so applying that
root directly would try to recreate live tables.  This forward-only bridge
keeps every old table as a read-only archive, creates the new MySQL contract,
and copies all relational business records into it.  Graph payloads and user
setting secrets are completed by scripts/migrate_legacy_mysql_storage.py once
Neo4j and the deployment data key are available.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from alembic import op
import sqlalchemy as sa


revision: str = "20260904_04"
down_revision: str | None = "20260828_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ARCHIVE_PREFIX = "legacy_20260828_"
ARCHIVED_TABLES = (
    "users",
    "history",
    "user_settings",
    "proof_workspaces",
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


def _scalar(statement: str, parameters: dict[str, object]) -> object:
    return op.get_bind().execute(sa.text(statement), parameters).scalar()


def _has_table(table: str) -> bool:
    return bool(
        _scalar(
            """SELECT 1 FROM information_schema.tables
                 WHERE table_schema = DATABASE() AND table_name = :table_name""",
            {"table_name": table},
        )
    )


def _has_index(table: str, index: str) -> bool:
    return bool(
        _scalar(
            """SELECT 1 FROM information_schema.statistics
                 WHERE table_schema = DATABASE()
                   AND table_name = :table_name AND index_name = :index_name""",
            {"table_name": table, "index_name": index},
        )
    )


def _has_constraint(table: str, constraint: str) -> bool:
    return bool(
        _scalar(
            """SELECT 1 FROM information_schema.table_constraints
                 WHERE table_schema = DATABASE()
                   AND table_name = :table_name
                   AND constraint_name = :constraint_name""",
            {"table_name": table, "constraint_name": constraint},
        )
    )


def _schema_statements() -> list[str]:
    path = Path(__file__).resolve().parents[2] / "storage" / "mysql_schema.sql"
    text = path.read_text(encoding="utf-8")
    uncommented = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("--")
    )
    return [part.strip() for part in uncommented.split(";") if part.strip()]


def _archive_old_contract() -> None:
    for table in ARCHIVED_TABLES:
        archive = f"{ARCHIVE_PREFIX}{table}"
        source_exists = _has_table(table)
        archive_exists = _has_table(archive)
        if source_exists and archive_exists:
            # This can only happen after a partially completed MySQL DDL run.
            # The source name is already the new contract and must not move.
            continue
        if source_exists:
            op.execute(f"RENAME TABLE `{table}` TO `{archive}`")
        elif not archive_exists:
            raise RuntimeError(f"required production table is missing: {table}")


def _create_latest_contract() -> None:
    create_index = re.compile(
        r"^CREATE INDEX `(?P<index>[^`]+)` ON `(?P<table>[^`]+)`", re.I
    )
    add_constraint = re.compile(
        r"^ALTER TABLE `(?P<table>[^`]+)` ADD CONSTRAINT `(?P<constraint>[^`]+)`",
        re.I,
    )
    for statement in _schema_statements():
        normalized = statement.lstrip()
        index_match = create_index.match(normalized)
        if index_match:
            if not _has_index(index_match.group("table"), index_match.group("index")):
                op.execute(statement)
            continue
        constraint_match = add_constraint.match(normalized)
        if constraint_match:
            if not _has_constraint(
                constraint_match.group("table"), constraint_match.group("constraint")
            ):
                op.execute(statement)
            continue
        if normalized.upper().startswith("CREATE TABLE"):
            statement = re.sub(
                r"^CREATE TABLE\s+", "CREATE TABLE IF NOT EXISTS ", statement, count=1, flags=re.I
            )
        op.execute(statement)


def _upsert(select_sql: str, key_column: str) -> None:
    op.execute(f"{select_sql}\nON DUPLICATE KEY UPDATE `{key_column}` = VALUES(`{key_column}`)")


def _copy_accounts_and_base_data() -> None:
    users = f"`{ARCHIVE_PREFIX}users`"
    history = f"`{ARCHIVE_PREFIX}history`"
    settings = f"`{ARCHIVE_PREFIX}user_settings`"
    workspaces = f"`{ARCHIVE_PREFIX}proof_workspaces`"

    _upsert(
        f"""INSERT INTO users (id, email, password_hash, created_at, can_teach)
            SELECT id,
                   COALESCE(NULLIF(LOWER(TRIM(email)), ''),
                            CONCAT('legacy-user-', id, '@invalid.mathweaver.local')),
                   password_hash,
                   created_at,
                   CASE WHEN role IN ('teacher', 'admin') THEN 1 ELSE 0 END
              FROM {users}""",
        "id",
    )
    _upsert(
        f"""INSERT INTO history
                   (id, user_id, filename, node_count, edge_count, created_at,
                    source_markdown, latex_macros, source_pdf_json, status,
                    stage, stage_label, stage_index, total_stages,
                    stages_done_json, source_format, updated_at,
                    experimental_logic_ir, source_origin)
            SELECT id, user_id, LEFT(filename, 255), node_count, edge_count, created_at,
                   source_markdown,
                   CASE
                     WHEN latex_macros IS NOT NULL AND JSON_VALID(latex_macros)
                       THEN JSON_EXTRACT(latex_macros, '$')
                     ELSE NULL
                   END,
                   source_pdf_json, status, stage, stage_label, stage_index,
                   total_stages, stages_done_json, source_format, updated_at,
                   experimental_logic_ir, source_origin
              FROM {history}""",
        "id",
    )
    _upsert(
        f"""INSERT INTO user_settings
                   (user_id, llm_api_url, llm_model, updated_at,
                    llm_api_key_ciphertext, llm_configs_ciphertext)
            SELECT user_id, llm_api_url, llm_model, updated_at, '', ''
              FROM {settings}""",
        "user_id",
    )
    _upsert(
        f"""INSERT INTO proof_workspaces
                   (user_id, graph_id, node_id, user_proof, versions_json,
                    ai_messages_json, updated_at, imports_json)
            SELECT user_id, graph_id, node_id, user_proof, versions_json,
                   ai_messages_json, updated_at, imports_json
              FROM {workspaces}""",
        "user_id",
    )


def _class_id(alias: str = "c") -> str:
    return (
        f"COALESCE(NULLIF(TRIM({alias}.public_id), ''), "
        f"CONCAT('legacy-class-', {alias}.id))"
    )


def _copy_classes_and_teaching_data() -> None:
    p = ARCHIVE_PREFIX
    class_id = _class_id("c")

    _upsert(
        f"""INSERT INTO education_classes
                   (id, owner_user_id, title, invite_code, archived_at, created_at,
                    student_experience, weekly_xp_goal, timezone)
            SELECT {class_id}, c.teacher_id, c.name, c.invite_code,
                   c.archived_at, c.created_at, 'classic', 60, 'Asia/Shanghai'
              FROM teaching_classes c""",
        "id",
    )
    _upsert(
        f"""INSERT INTO education_memberships
                   (class_id, user_id, role, joined_at, removed_at,
                    student_name, student_number)
            SELECT {class_id}, c.teacher_id, 'teacher', c.created_at, NULL,
                   u.display_name, NULL
              FROM teaching_classes c
              JOIN `{p}users` u ON u.id = c.teacher_id""",
        "class_id",
    )
    _upsert(
        f"""INSERT INTO education_memberships
                   (class_id, user_id, role, joined_at, removed_at,
                    student_name, student_number)
            SELECT {class_id}, m.student_id, m.role, m.created_at, m.removed_at,
                   COALESCE(m.student_name, u.display_name),
                   COALESCE(m.student_number, u.student_no)
              FROM class_memberships m
              JOIN teaching_classes c ON c.id = m.teaching_class_id
              JOIN `{p}users` u ON u.id = m.student_id""",
        "class_id",
    )

    _upsert(
        f"""INSERT INTO education_snapshots
                   (id, class_id, source_graph_id, filename, node_count, edge_count,
                    source_markdown, latex_macros_json, source_pdf_json,
                    created_by, created_at, snapshot_type)
            SELECT s.id, {class_id}, s.source_graph_id, LEFT(s.filename, 255),
                   COALESCE(JSON_LENGTH(s.nodes_json), 0),
                   COALESCE(JSON_LENGTH(s.edges_json), 0),
                   s.source_markdown, s.latex_macros_json, s.source_pdf_json,
                   s.created_by, s.created_at, 'graph'
              FROM `{p}education_snapshots` s
              JOIN teaching_classes c ON c.id = s.teaching_class_id""",
        "id",
    )
    _upsert(
        f"""INSERT INTO education_assignments
                   (id, class_id, snapshot_id, title, target_node_id, due_at,
                    status, base_path_json, summary, version, published_at,
                    created_by, created_at, updated_at, grades_published_at,
                    assignment_type, direct_structure_version)
            SELECT a.id, {class_id}, a.snapshot_id, a.title, a.target_node_id,
                   a.due_at, a.status, a.base_path_json, a.summary, a.version,
                   a.published_at, a.created_by, a.created_at, a.updated_at,
                   a.grades_published_at, 'graph', 0
              FROM `{p}education_assignments` a
              JOIN teaching_classes c ON c.id = a.teaching_class_id""",
        "id",
    )

    copies = (
        ("education_student_paths", "assignment_id, user_id, path_json, updated_at", "assignment_id"),
        ("education_diagnostics", "id, assignment_id, user_id, node_id, question_json, answer, result, summary, created_at, updated_at", "id"),
        ("education_ai_usage", "user_id, usage_day, request_count, updated_at", "user_id"),
        ("education_ai_tasks", "id, LEFT(task_key, 128), user_id, task_kind, scope, status, error, created_at, updated_at", "id"),
        ("education_assessment_nodes", "assignment_id, node_id, status, last_error, updated_at", "assignment_id"),
        ("education_assessment_questions", "id, assignment_id, node_id, kind, question, focus, expected_points_json, sort_order, created_at, updated_at, reference_answer, max_score", "id"),
        ("education_assessment_attempts", "id, assignment_id, user_id, node_id, status, answers_json, started_at, updated_at, completed_at", "id"),
        ("education_node_progress", "assignment_id, user_id, node_id, state, mastery_source, diagnostic_summary, updated_at", "assignment_id"),
        ("education_assignment_submissions", "id, assignment_id, user_id, status, ai_status, snapshot_json, ai_suggested_total, teacher_total, teacher_summary, LEFT(ai_error, 255), submitted_at, updated_at, finalized_at, released_at", "id"),
        ("education_submission_question_grades", "submission_id, question_id, node_id, max_score, student_answer, reference_answer, expected_points_json, matrix_report_json, ai_result_json, ai_suggested_score, teacher_score, teacher_feedback, updated_at", "submission_id"),
    )
    for table, columns, key in copies:
        target_columns = columns.replace("LEFT(task_key, 128)", "task_key").replace(
            "LEFT(ai_error, 255)", "ai_error"
        )
        _upsert(
            f"INSERT INTO `{table}` ({target_columns}) SELECT {columns} FROM `{p}{table}`",
            key,
        )

    _upsert(
        f"""INSERT INTO education_node_identities
                   (id, class_id, global_id, title, created_at)
            SELECT n.id, {class_id}, n.global_id, LEFT(n.title, 255), n.created_at
              FROM `{p}education_node_identities` n
              JOIN teaching_classes c ON c.id = n.teaching_class_id""",
        "id",
    )
    _upsert(
        f"""INSERT INTO education_node_occurrences
                   (snapshot_id, node_id, canonical_node_id, global_id)
            SELECT snapshot_id, node_id, canonical_node_id, global_id
              FROM `{p}education_node_occurrences`""",
        "snapshot_id",
    )
    _upsert(
        f"""INSERT INTO learning_interactions
                   (id, client_interaction_id, user_id, class_id, assignment_id,
                    snapshot_id, canonical_node_id, node_id, action, user_proof,
                    assistant_response, context_version, context_snapshot_json,
                    classification_status, token_estimate, result_json, created_at)
            SELECT i.id, i.client_interaction_id, i.user_id, {class_id},
                   i.assignment_id, i.snapshot_id, i.canonical_node_id, i.node_id,
                   i.action, i.user_proof, i.assistant_response, i.context_version,
                   i.context_snapshot_json, i.classification_status,
                   i.token_estimate, i.result_json, i.created_at
              FROM `{p}learning_interactions` i
              JOIN teaching_classes c ON c.id = i.teaching_class_id""",
        "id",
    )
    _upsert(
        f"""INSERT INTO learning_evidence
                   (id, interaction_id, user_id, class_id, canonical_node_id,
                    kind, claim, status, source_type, confidence, severity,
                    evidence_excerpt, created_at, updated_at)
            SELECT e.id, e.interaction_id, e.user_id, {class_id},
                   e.canonical_node_id, e.kind, e.claim, e.status, e.source_type,
                   e.confidence, e.severity, e.evidence_excerpt,
                   e.created_at, e.updated_at
              FROM `{p}learning_evidence` e
              JOIN teaching_classes c ON c.id = e.teaching_class_id""",
        "id",
    )
    _upsert(
        f"""INSERT INTO learning_evidence_nodes
                   (evidence_id, canonical_node_id, relation_role,
                    relation_path_json, weight)
            SELECT evidence_id, canonical_node_id, relation_role,
                   relation_path_json, weight
              FROM `{p}learning_evidence_nodes`""",
        "evidence_id",
    )
    _upsert(
        f"""INSERT INTO learning_evidence_feedback
                   (id, evidence_id, user_id, action, previous_status,
                    new_status, note, created_at)
            SELECT id, evidence_id, user_id, action, previous_status,
                   new_status, LEFT(note, 255), created_at
              FROM `{p}learning_evidence_feedback`""",
        "id",
    )
    _upsert(
        f"""INSERT INTO student_node_models
                   (class_id, user_id, canonical_node_id, mastery_state,
                    direct_summary_json, risk_summary_json, open_evidence_count,
                    version, updated_at)
            SELECT {class_id}, m.user_id, m.canonical_node_id, m.mastery_state,
                   m.direct_summary_json, m.risk_summary_json,
                   m.open_evidence_count, m.version, m.updated_at
              FROM `{p}student_node_models` m
              JOIN teaching_classes c ON c.id = m.teaching_class_id""",
        "class_id",
    )
    _upsert(
        f"""INSERT INTO learning_context_summaries
                   (class_id, user_id, scope_type, scope_id, summary_json,
                    source_watermark, schema_version, prompt_version,
                    token_count, updated_at)
            SELECT {class_id}, s.user_id, s.scope_type, s.scope_id,
                   s.summary_json, s.source_watermark, s.schema_version,
                   s.prompt_version, s.token_count, s.updated_at
              FROM `{p}learning_context_summaries` s
              JOIN teaching_classes c ON c.id = s.teaching_class_id""",
        "class_id",
    )


def _create_course_graph_order() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS education_course_graph_order (
             class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
             graph_key VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
             sort_order BIGINT NOT NULL,
             updated_by BIGINT NOT NULL,
             updated_at DATETIME(6) NOT NULL,
             PRIMARY KEY (class_id, graph_key),
             CONSTRAINT fk_education_course_graph_order_class_id
               FOREIGN KEY (class_id) REFERENCES education_classes (id) ON DELETE CASCADE,
             CONSTRAINT fk_education_course_graph_order_updated_by
               FOREIGN KEY (updated_by) REFERENCES users (id) ON DELETE RESTRICT
           ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"""
    )
    if not _has_index("education_course_graph_order", "idx_edu_course_graph_order_sort"):
        op.execute(
            "CREATE INDEX idx_edu_course_graph_order_sort "
            "ON education_course_graph_order (class_id, sort_order)"
        )
    _upsert(
        """INSERT INTO education_course_graph_order
                  (class_id, graph_key, sort_order, updated_by, updated_at)
           SELECT class_id, graph_key,
                  ROW_NUMBER() OVER (PARTITION BY class_id ORDER BY created_at, id) - 1,
                  created_by, created_at
             FROM (
                  SELECT ranked.*
                    FROM (
                         SELECT id, class_id, created_by, created_at,
                                CASE
                                  WHEN source_graph_id IS NULL OR TRIM(source_graph_id) = ''
                                    THEN CONCAT('snapshot:', id)
                                  ELSE CONCAT('source:', TRIM(source_graph_id))
                                END AS graph_key,
                                ROW_NUMBER() OVER (
                                  PARTITION BY class_id,
                                    CASE
                                      WHEN source_graph_id IS NULL OR TRIM(source_graph_id) = ''
                                        THEN CONCAT('snapshot:', id)
                                      ELSE CONCAT('source:', TRIM(source_graph_id))
                                    END
                                  ORDER BY created_at, id
                                ) AS graph_rank
                           FROM education_snapshots
                          WHERE snapshot_type = 'graph'
                    ) ranked
                   WHERE graph_rank = 1
             ) deduplicated""",
        "class_id",
    )


def upgrade() -> None:
    _archive_old_contract()
    _create_latest_contract()
    _copy_accounts_and_base_data()
    _copy_classes_and_teaching_data()
    _create_course_graph_order()


def downgrade() -> None:
    raise RuntimeError(
        "20260904_04 is a forward-only production data migration; "
        "restore the pre-migration MySQL backup for rollback"
    )
