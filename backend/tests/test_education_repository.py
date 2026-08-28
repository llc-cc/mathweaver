"""班级、成员、不可变图谱快照和作业仓储测试。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from storage.database import session_scope
from storage.education_repository import EducationRepository
from storage.learning_repository import JobSnapshot, LearningRepository
from storage.models import EducationAssignment, TeachingClass, User


def _user(email: str, role: str) -> int:
    with session_scope() as session:
        row = User(
            email=email,
            display_name=email.split("@", 1)[0],
            role=role,
            password_hash="not-used",
            initial_password_pending=False,
        )
        session.add(row)
        session.flush()
        return row.id


def test_class_public_id_is_stable_and_internal_id_is_not_exposed(database) -> None:
    teacher_id = _user("teacher@example.com", "teacher")
    repository = EducationRepository()

    created = repository.create_class(teacher_id, "凸优化 2026")
    loaded = repository.get_class(created["id"])

    assert loaded == created
    assert set(created) == {
        "id",
        "title",
        "invite_code",
        "owner_user_id",
        "created_at",
    }
    with session_scope() as session:
        internal = session.scalar(
            select(TeachingClass).where(TeachingClass.public_id == created["id"])
        )
        assert internal is not None
        assert str(internal.id) != created["id"]


def test_join_code_is_unique_and_teacher_can_restore_removed_member(database) -> None:
    teacher_id = _user("teacher@example.com", "teacher")
    student_id = _user("student@example.com", "student")
    repository = EducationRepository()
    first = repository.create_class(teacher_id, "一班")
    second = repository.create_class(teacher_id, "二班")
    assert first["invite_code"] != second["invite_code"]

    repository.join_student(
        first["id"],
        first["invite_code"],
        student_id,
        "张三",
        "S001",
    )
    assert repository.remove_student(first["id"], student_id) is not None
    assert repository.get_membership(first["id"], student_id, include_removed=True)["removed_at"]

    repository.restore_student(first["id"], student_id)
    assert repository.get_membership(first["id"], student_id)["removed_at"] is None


def test_snapshot_creation_copies_history_as_immutable_graph(database) -> None:
    teacher_id = _user("teacher@example.com", "teacher")
    repository = EducationRepository()
    class_row = repository.create_class(teacher_id, "图谱班")
    LearningRepository().upsert_job_progress(
        teacher_id,
        JobSnapshot(
            job_id="history-graph",
            filename="official.md",
            status="done",
            nodes=[{"id": 1, "global_id": "g-1", "title_zh": "集合"}],
            edges=[],
            source_markdown="# 官方图谱",
            latex_macros={},
            source_pdf=None,
            stage="complete",
            stage_label="完成",
            stage_index=1,
            total_stages=1,
            stages_done=["complete"],
            source_format="markdown",
            source_origin="official_graph",
            experimental_logic_ir=False,
            created_at=datetime.now(timezone.utc),
        ),
    )

    snapshot, created = repository.create_snapshot(
        public_class_id=class_row["id"],
        actor_id=teacher_id,
        source_graph_id="official-v1",
        source_history_id="history-graph",
        filename="client-name.md",
        nodes=[{"id": 999}],
        edges=[{"source": 999, "target": 1}],
        source_markdown="client payload",
        latex_macros={},
        source_pdf=None,
    )

    assert created is True
    assert snapshot["nodes_json"] == [{"id": 1, "global_id": "g-1", "title_zh": "集合"}]
    assert snapshot["source_markdown"] == "# 官方图谱"
    assert snapshot["filename"] == "official.md"


def test_assignment_write_rolls_back_when_path_validation_fails(database) -> None:
    teacher_id = _user("teacher@example.com", "teacher")
    repository = EducationRepository()
    class_row = repository.create_class(teacher_id, "事务班")
    snapshot, _created = repository.create_snapshot(
        public_class_id=class_row["id"],
        actor_id=teacher_id,
        source_graph_id="direct-v1",
        source_history_id=None,
        filename="direct.md",
        nodes=[{"id": 1, "global_id": "g-1"}],
        edges=[],
        source_markdown="# direct",
        latex_macros={},
        source_pdf=None,
    )

    with pytest.raises(ValueError, match="target node"):
        repository.create_assignment(
            public_class_id=class_row["id"],
            snapshot_id=snapshot["id"],
            actor_id=teacher_id,
            title="无效作业",
            target_node_id=1,
            due_at=None,
            path={"steps": []},
        )

    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(EducationAssignment)) == 0
