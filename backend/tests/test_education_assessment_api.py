"""测评 API 的 MySQL 路径冒烟测试。"""

from __future__ import annotations

from test_assessment_repository import _domain


def test_assignment_assessment_read_uses_repository(database) -> None:
    _teacher_id, _student_id, _class_id, assignment_id = _domain()
    from storage.education_repository import EducationRepository

    assessments = EducationRepository().list_assessments(assignment_id, role="teacher")
    assert assessments[0]["questions"][0]["id"] == "q-1"
