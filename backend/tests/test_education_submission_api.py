"""提交访问控制回归测试。"""

from __future__ import annotations

from storage.assessment_repository import AssessmentRepository
from storage.education_repository import EducationRepository

from test_assessment_repository import _domain, _user


def test_cross_class_submission_access_is_rejected(database) -> None:
    _teacher_id, student_id, _class_id, assignment_id = _domain()
    repository = AssessmentRepository()
    attempt, _ = repository.start_attempt(assignment_id, student_id, 1)
    repository.complete_attempt(attempt["id"], student_id, {"q-1": "答案"})
    submission, _created, _missing = repository.submit_assignment(assignment_id, student_id)

    outsider_id = _user("outsider@example.com", "student")
    assert repository.get_owned_submission(submission["id"], outsider_id) is None
    assert repository.get_owned_submission(submission["id"], student_id) is not None
