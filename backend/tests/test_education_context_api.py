"""学生上下文 API 使用仓储的冒烟测试。"""

from storage.education_repository import EducationRepository
from storage.student_context_repository import StudentContextRepository

from test_assessment_repository import _domain


def test_student_context_overview_is_student_scoped(database) -> None:
    _teacher, student_id, _class_id, assignment_id = _domain()
    education = EducationRepository()
    assignment = education.get_assignment(assignment_id)
    snapshot = education.get_snapshot(assignment["snapshot_id"])

    overview = StudentContextRepository().build_overview(
        assignment, snapshot, student_id
    )

    assert overview == {"contextVersion": 0, "nodeStates": []}
