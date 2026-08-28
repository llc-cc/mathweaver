"""正式图谱导入必须原子、幂等且可审计。"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from services import graph_seed_service
from services.graph_seed_service import import_graph_dataset
from storage.database import session_scope
from storage.models import (
    AuditLog,
    ClassMembership,
    Course,
    EducationNodeIdentity,
    EducationNodeOccurrence,
    EducationSnapshot,
    History,
    TeachingClass,
)

from test_assessment_repository import _user


DATASET = "backend/seeds/convex_optimization"


def _counts() -> dict[str, int]:
    models = (
        Course,
        TeachingClass,
        ClassMembership,
        History,
        EducationSnapshot,
        EducationNodeIdentity,
        EducationNodeOccurrence,
        AuditLog,
    )
    with session_scope() as session:
        return {
            model.__tablename__: session.scalar(
                select(func.count()).select_from(model)
            )
            for model in models
        }


def test_import_creates_history_class_snapshot_and_occurrences_atomically(database) -> None:
    _user("seed-teacher@example.com", "teacher")

    result = import_graph_dataset(
        DATASET, "seed-teacher@example.com", "凸优化"
    )

    assert result["nodeCount"] == 90
    assert result["edgeCount"] == 226
    assert result["warningCount"] == 3
    assert result["historyId"] == result["sourceGraphId"]
    assert _counts() == {
        "courses": 1,
        "teaching_classes": 1,
        "class_memberships": 1,
        "history": 1,
        "education_snapshots": 1,
        "education_node_identities": 90,
        "education_node_occurrences": 90,
        "audit_logs": 1,
    }


def test_import_is_idempotent_for_same_dataset_teacher_and_class(database) -> None:
    _user("seed-idempotent@example.com", "teacher")

    first = import_graph_dataset(DATASET, "seed-idempotent@example.com", "凸优化")
    before = _counts()
    second = import_graph_dataset(DATASET, "seed-idempotent@example.com", "凸优化")

    assert second == first
    assert _counts() == before


def test_import_rolls_back_everything_on_invalid_occurrence(
    database, monkeypatch
) -> None:
    _user("seed-rollback@example.com", "teacher")
    monkeypatch.setattr(
        graph_seed_service, "_identity_id", lambda _class_id, _global_id: "duplicate"
    )

    with pytest.raises(ValueError, match="identity conflict"):
        import_graph_dataset(DATASET, "seed-rollback@example.com", "凸优化")

    counts = _counts()
    assert counts["courses"] == 0
    assert counts["teaching_classes"] == 0
    assert counts["history"] == 0
    assert counts["education_snapshots"] == 0
    assert counts["education_node_identities"] == 0
    assert counts["education_node_occurrences"] == 0
    assert counts["audit_logs"] == 0


def test_import_records_audit_log_without_secrets(database) -> None:
    _user("seed-audit@example.com", "teacher")

    result = import_graph_dataset(DATASET, "seed-audit@example.com", "凸优化")

    with session_scope() as session:
        audit = session.scalar(select(AuditLog))
        serialized = str(audit.details).lower()
    assert audit.action == "graph_seed.imported"
    assert audit.subject_id == result["snapshotId"]
    assert all(word not in serialized for word in ("password", "token", "secret", "database_url"))
