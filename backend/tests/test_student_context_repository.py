"""学生交互、证据、模型、导出与删除仓储测试。"""

from __future__ import annotations

from sqlalchemy import func, select

from storage.database import session_scope
from storage.education_repository import EducationRepository
from storage.models import (
    EducationNodeIdentity,
    EducationNodeOccurrence,
    LearningEvidence,
    LearningEvidenceNode,
    LearningInteraction,
    StudentNodeModel,
    TeachingClass,
)
from storage.student_context_repository import StudentContextRepository

from test_assessment_repository import _domain, _user


def _context_domain():
    teacher_id, student_id, class_id, assignment_id = _domain()
    education = EducationRepository()
    assignment = education.get_assignment(assignment_id)
    snapshot = education.get_snapshot(assignment["snapshot_id"])
    return teacher_id, student_id, class_id, assignment, snapshot


def test_interaction_evidence_and_node_links_commit_atomically(database) -> None:
    _teacher, student_id, _class_id, assignment, snapshot = _context_domain()
    repository = StudentContextRepository()
    packet = repository.build_packet(assignment, snapshot, student_id, 1)

    stored = repository.store_interaction_with_evidence(
        assignment=assignment,
        snapshot=snapshot,
        user_id=student_id,
        node_id=1,
        client_interaction_id="interaction-1",
        action="check",
        user_proof="我的证明",
        assistant_response="检查结果",
        context_packet=packet,
        learning_delta=[{"kind": "understanding", "claim": "理解定义", "confidence": 0.9}],
        classification_status="classified",
    )

    assert stored["contextVersion"] == 1
    assert stored["stateChanges"][0]["kind"] == "understanding"
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(LearningInteraction)) == 1
        assert session.scalar(select(func.count()).select_from(LearningEvidence)) == 1
        assert session.scalar(select(func.count()).select_from(LearningEvidenceNode)) == 1
        assert session.scalar(select(func.count()).select_from(StudentNodeModel)) == 1


def test_feedback_refreshes_only_affected_student_models(database) -> None:
    _teacher, student_id, class_id, assignment, snapshot = _context_domain()
    repository = StudentContextRepository()
    with session_scope() as session:
        teaching_class = session.scalar(
            select(TeachingClass).where(TeachingClass.public_id == class_id)
        )
        unrelated = EducationNodeIdentity(
            id="canonical-unrelated",
            teaching_class_id=teaching_class.id,
            global_id="g-unrelated",
            title="无关节点",
        )
        session.add(unrelated)
        session.add(
            EducationNodeOccurrence(
                snapshot_id=snapshot["id"],
                node_id=2,
                canonical_node_id=unrelated.id,
                global_id=unrelated.global_id,
            )
        )
        session.add(
            StudentNodeModel(
                teaching_class_id=teaching_class.id,
                user_id=student_id,
                canonical_node_id=unrelated.id,
                mastery_state="unknown",
                direct_summary_json={},
                risk_summary_json={},
                open_evidence_count=0,
                version=1,
            )
        )
    packet = repository.build_packet(assignment, snapshot, student_id, 1)
    stored = repository.store_interaction_with_evidence(
        assignment=assignment,
        snapshot=snapshot,
        user_id=student_id,
        node_id=1,
        client_interaction_id="feedback-source",
        action="check",
        user_proof="proof",
        assistant_response="response",
        context_packet=packet,
        learning_delta=[{"kind": "gap", "claim": "gap"}],
        classification_status="classified",
    )

    repository.update_evidence_status(
        stored["stateChanges"][0]["evidenceId"], student_id, "resolved", "已理解"
    )

    with session_scope() as session:
        models = session.scalars(
            select(StudentNodeModel).order_by(StudentNodeModel.canonical_node_id)
        ).all()
        versions = {row.canonical_node_id: row.version for row in models}
    assert versions["canonical-unrelated"] == 1
    assert max(version for key, version in versions.items() if key != "canonical-unrelated") == 2


def test_context_version_changes_after_new_evidence(database) -> None:
    _teacher, student_id, _class_id, assignment, snapshot = _context_domain()
    repository = StudentContextRepository()
    packet = repository.build_packet(assignment, snapshot, student_id, 1)
    repository.store_interaction_with_evidence(
        assignment=assignment,
        snapshot=snapshot,
        user_id=student_id,
        node_id=1,
        client_interaction_id="interaction-1",
        action="hint",
        user_proof="",
        assistant_response="提示",
        context_packet=packet,
        learning_delta=[{"kind": "gap", "claim": "缺少一步"}],
        classification_status="classified",
    )

    refreshed = repository.build_packet(assignment, snapshot, student_id, 1)
    assert packet["contextVersion"] == 0
    assert refreshed["contextVersion"] == 1


def test_export_contains_only_current_students_data(database) -> None:
    _teacher, student_id, class_id, assignment, snapshot = _context_domain()
    repository = StudentContextRepository()
    packet = repository.build_packet(assignment, snapshot, student_id, 1)
    repository.store_interaction_with_evidence(
        assignment=assignment,
        snapshot=snapshot,
        user_id=student_id,
        node_id=1,
        client_interaction_id="interaction-1",
        action="check",
        user_proof="proof",
        assistant_response="response",
        context_packet=packet,
        learning_delta=[{"kind": "gap", "claim": "gap"}],
        classification_status="classified",
    )
    other_student_id = _user("context-other@example.com", "student")
    EducationRepository().join_student(
        class_id, EducationRepository().get_class(class_id)["invite_code"],
        other_student_id, "其他学生", "S002"
    )
    repository.store_interaction_with_evidence(
        assignment=assignment,
        snapshot=snapshot,
        user_id=other_student_id,
        node_id=1,
        client_interaction_id="other-interaction",
        action="check",
        user_proof="other proof",
        assistant_response="other response",
        context_packet=repository.build_packet(assignment, snapshot, other_student_id, 1),
        learning_delta=[{"kind": "gap", "claim": "other gap"}],
        classification_status="classified",
    )
    exported = repository.export_student_context(class_id, student_id)

    assert exported["userId"] == student_id
    assert [item["client_interaction_id"] for item in exported["interactions"]] == [
        "interaction-1"
    ]
    assert all(item["claim"] != "other gap" for item in exported["evidence"])


def test_delete_context_does_not_delete_shared_snapshot_identity(database) -> None:
    _teacher, student_id, class_id, assignment, snapshot = _context_domain()
    repository = StudentContextRepository()
    packet = repository.build_packet(assignment, snapshot, student_id, 1)
    repository.store_interaction_with_evidence(
        assignment=assignment,
        snapshot=snapshot,
        user_id=student_id,
        node_id=1,
        client_interaction_id="interaction-1",
        action="check",
        user_proof="proof",
        assistant_response="response",
        context_packet=packet,
        learning_delta=[{"kind": "gap", "claim": "gap"}],
        classification_status="classified",
    )
    with session_scope() as session:
        identity_count = session.scalar(select(func.count()).select_from(EducationNodeIdentity))

    counts = repository.delete_student_context(class_id, student_id)

    assert counts["deletedInteractions"] == 1
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(EducationNodeIdentity)) == identity_count
        assert session.scalar(select(func.count()).select_from(LearningInteraction)) == 0
        assert session.scalar(select(func.count()).select_from(LearningEvidence)) == 0
        assert session.scalar(select(func.count()).select_from(LearningEvidenceNode)) == 0
