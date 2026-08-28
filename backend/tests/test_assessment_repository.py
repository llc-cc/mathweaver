"""测评尝试、提交、评分和 AI 幂等任务仓储测试。"""

from __future__ import annotations

from sqlalchemy import func, select

from storage.assessment_repository import AssessmentRepository
from storage.database import session_scope
from storage.education_repository import EducationRepository
from storage.models import (
    AuditLog,
    EducationAiUsage,
    EducationAssignment,
    EducationAssessmentNode,
    EducationAssessmentQuestion,
    User,
)


def _user(email: str, role: str) -> int:
    with session_scope() as session:
        user = User(
            email=email,
            display_name=email.split("@", 1)[0],
            role=role,
            password_hash="unused",
            initial_password_pending=False,
        )
        session.add(user)
        session.flush()
        return user.id


def _domain() -> tuple[int, int, str, str]:
    teacher_id = _user("assessment-teacher@example.com", "teacher")
    student_id = _user("assessment-student@example.com", "student")
    education = EducationRepository()
    class_row = education.create_class(teacher_id, "测评班")
    education.join_student(class_row["id"], class_row["invite_code"], student_id, "学生", "S001")
    snapshot, _ = education.create_snapshot(
        public_class_id=class_row["id"],
        actor_id=teacher_id,
        source_graph_id="assessment-v1",
        source_history_id=None,
        filename="assessment.md",
        nodes=[{"id": 1, "global_id": "g-1"}],
        edges=[],
        source_markdown="# assessment",
        latex_macros={},
        source_pdf=None,
    )
    assignment = education.create_assignment(
        public_class_id=class_row["id"],
        snapshot_id=snapshot["id"],
        actor_id=teacher_id,
        title="测评",
        target_node_id=1,
        due_at=None,
        path={"steps": [{"nodeId": 1}], "candidateNodeIds": [1]},
    )
    with session_scope() as session:
        node = session.get(EducationAssessmentNode, (assignment["id"], 1))
        node.status = "ready"
        session.add(
            EducationAssessmentQuestion(
                id="q-1",
                assignment_id=assignment["id"],
                node_id=1,
                kind="proof",
                question="证明",
                focus="定义",
                expected_points_json=["定义"],
                reference_answer="标准答案",
                max_score=100,
                sort_order=1,
            )
        )
        stored = session.get(EducationAssignment, assignment["id"])
        stored.status = "published"
    return teacher_id, student_id, class_row["id"], assignment["id"]


def test_complete_attempt_is_idempotent(database) -> None:
    _teacher_id, student_id, _class_id, assignment_id = _domain()
    repository = AssessmentRepository()
    attempt, _created = repository.start_attempt(assignment_id, student_id, 1)

    first = repository.complete_attempt(attempt["id"], student_id, {"q-1": "我的证明"})
    second = repository.complete_attempt(attempt["id"], student_id, {"q-1": "被忽略"})

    assert first["status"] == second["status"] == "completed"
    assert second["answers_json"] == {"q-1": "我的证明"}


def test_submit_assignment_creates_one_submission_per_student(database) -> None:
    _teacher_id, student_id, _class_id, assignment_id = _domain()
    repository = AssessmentRepository()
    attempt, _ = repository.start_attempt(assignment_id, student_id, 1)
    repository.complete_attempt(attempt["id"], student_id, {"q-1": "答案"})

    first, first_created, missing = repository.submit_assignment(assignment_id, student_id)
    second, second_created, second_missing = repository.submit_assignment(assignment_id, student_id)

    assert missing == second_missing == []
    assert first_created is True and second_created is False
    assert first["id"] == second["id"]


def test_grade_transaction_updates_questions_submission_and_audit_log(database) -> None:
    teacher_id, student_id, _class_id, assignment_id = _domain()
    repository = AssessmentRepository()
    attempt, _ = repository.start_attempt(assignment_id, student_id, 1)
    repository.complete_attempt(attempt["id"], student_id, {"q-1": "答案"})
    submission, _created, _missing = repository.submit_assignment(assignment_id, student_id)

    updated = repository.save_teacher_grades(
        submission["id"],
        teacher_id,
        [{"questionId": "q-1", "teacherScore": 88, "teacherFeedback": "清晰"}],
        "继续保持",
    )

    assert updated["status"] == "review_draft"
    assert repository.list_submission_grades(submission["id"])[0]["teacher_score"] == 88
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 1


def test_ai_usage_is_charged_once_for_same_task_key(database) -> None:
    teacher_id = _user("quota-teacher@example.com", "teacher")
    repository = AssessmentRepository()

    first = repository.claim_ai_task("same-task", teacher_id, "assessment", "scope", 5)
    second = repository.claim_ai_task("same-task", teacher_id, "assessment", "scope", 5)

    assert first["claimed"] is True
    assert second["claimed"] is False
    with session_scope() as session:
        assert session.scalar(select(func.sum(EducationAiUsage.request_count))) == 1
