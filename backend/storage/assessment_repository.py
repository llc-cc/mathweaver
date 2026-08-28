"""测评、提交、评分与 AI 用量的事务仓储。"""

from __future__ import annotations

import uuid
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from storage.database import session_scope
from storage.models import (
    AuditLog,
    ClassMembership,
    EducationAiTask,
    EducationAiUsage,
    EducationAssignment,
    EducationAssignmentSubmission,
    EducationAssessmentAttempt,
    EducationAssessmentNode,
    EducationAssessmentQuestion,
    EducationDiagnostic,
    EducationNodeProgress,
    EducationSubmissionQuestionGrade,
    TeachingClass,
    utc_now,
)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class AssessmentRepository:
    """把冻结、幂等和发布边界落实为短数据库事务。"""

    def __init__(self, session_factory=session_scope) -> None:
        self._session_factory = session_factory

    def claim_ai_task(
        self,
        task_key: str,
        user_id: int,
        task_kind: str,
        scope: str,
        daily_limit: int,
    ) -> dict:
        if daily_limit <= 0:
            return {"claimed": False, "reason": "limit", "remaining": 0}
        try:
            with self._session_factory() as session:
                existing = session.scalar(
                    select(EducationAiTask).where(
                        EducationAiTask.task_key == task_key
                    )
                )
                if existing is not None:
                    return {
                        "claimed": False,
                        "reason": "exists",
                        "id": existing.id,
                        "status": existing.status,
                    }
                today = utc_now().date()
                usage = session.scalar(
                    select(EducationAiUsage)
                    .where(
                        EducationAiUsage.user_id == user_id,
                        EducationAiUsage.usage_day == today,
                    )
                    .with_for_update()
                )
                if usage is not None and usage.request_count >= daily_limit:
                    return {"claimed": False, "reason": "limit", "remaining": 0}
                if usage is None:
                    usage = EducationAiUsage(
                        user_id=user_id,
                        usage_day=today,
                        request_count=0,
                    )
                    session.add(usage)
                usage.request_count += 1
                task = EducationAiTask(
                    id=uuid.uuid4().hex,
                    task_key=task_key,
                    user_id=user_id,
                    task_kind=task_kind,
                    scope=scope,
                    status="running",
                )
                session.add(task)
                session.flush()
                return {
                    "claimed": True,
                    "id": task.id,
                    "remaining": max(0, daily_limit - usage.request_count),
                }
        except IntegrityError:
            # 多 worker 同时认领同一键时只有唯一键胜者计费并执行。
            with self._session_factory() as session:
                existing = session.scalar(
                    select(EducationAiTask).where(
                        EducationAiTask.task_key == task_key
                    )
                )
                return {
                    "claimed": False,
                    "reason": "exists",
                    "id": existing.id if existing else None,
                    "status": existing.status if existing else None,
                }

    def finish_ai_task(self, record_id: str, *, error: str | None = None) -> None:
        with self._session_factory() as session:
            row = session.get(EducationAiTask, record_id)
            if row is None:
                return
            row.status = "failed" if error else "done"
            row.error = error[:1000] if error else None
            row.updated_at = utc_now()

    def get_assessment_node(self, assignment_id: str, node_id: int) -> dict | None:
        with self._session_factory() as session:
            row = session.get(EducationAssessmentNode, (assignment_id, node_id))
            return self._node_dict(row) if row else None

    def get_question(self, assignment_id: str, node_id: int, question_id: str) -> dict | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(EducationAssessmentQuestion).where(
                    EducationAssessmentQuestion.id == question_id,
                    EducationAssessmentQuestion.assignment_id == assignment_id,
                    EducationAssessmentQuestion.node_id == node_id,
                )
            )
            return self._question_dict(row) if row else None

    def list_questions(self, assignment_id: str, node_id: int | None = None) -> list[dict]:
        with self._session_factory() as session:
            filters = [EducationAssessmentQuestion.assignment_id == assignment_id]
            if node_id is not None:
                filters.append(EducationAssessmentQuestion.node_id == node_id)
            rows = session.scalars(
                select(EducationAssessmentQuestion)
                .where(*filters)
                .order_by(
                    EducationAssessmentQuestion.node_id,
                    EducationAssessmentQuestion.sort_order,
                )
            ).all()
            return [self._question_dict(row) for row in rows]

    def unresolved_node_ids(self, assignment_id: str, path_node_ids: list[int]) -> list[int]:
        with self._session_factory() as session:
            nodes = session.scalars(
                select(EducationAssessmentNode).where(
                    EducationAssessmentNode.assignment_id == assignment_id
                )
            ).all()
            by_id = {}
            for node in nodes:
                count = session.scalar(
                    select(func.count())
                    .select_from(EducationAssessmentQuestion)
                    .where(
                        EducationAssessmentQuestion.assignment_id == assignment_id,
                        EducationAssessmentQuestion.node_id == node.node_id,
                    )
                )
                by_id[node.node_id] = (node.status, int(count or 0))
            return [
                node_id
                for node_id in path_node_ids
                if by_id.get(node_id, ("pending", 0))[0]
                not in {"ready", "exempt"}
                or by_id.get(node_id, ("pending", 0))[0] == "ready"
                and by_id.get(node_id, ("pending", 0))[1] < 1
            ]

    def scoring_validation(self, assignment_id: str) -> tuple[bool, dict]:
        questions = self.list_questions(assignment_id)
        invalid = []
        total = 0.0
        for row in questions:
            score = float(row["max_score"] or 0)
            total += score
            if (
                score <= 0
                or not str(row["reference_answer"] or "").strip()
                or not row["expected_points_json"]
            ):
                invalid.append(
                    {
                        "questionId": row["id"],
                        "nodeId": row["node_id"],
                        "reason": "scoring_standard_incomplete",
                    }
                )
        return (
            not invalid and (not questions or abs(total - 100.0) < 0.001),
            {"totalScore": round(total, 1), "invalidQuestions": invalid},
        )

    def update_scoring_standard(
        self,
        assignment_id: str,
        node_id: int,
        question_id: str,
        *,
        reference_answer: str,
        expected_points: list[str],
        max_score: float,
    ) -> bool:
        with self._session_factory() as session:
            assignment = session.get(EducationAssignment, assignment_id)
            question = session.scalar(
                select(EducationAssessmentQuestion)
                .where(
                    EducationAssessmentQuestion.id == question_id,
                    EducationAssessmentQuestion.assignment_id == assignment_id,
                    EducationAssessmentQuestion.node_id == node_id,
                )
                .with_for_update()
            )
            if assignment is None or question is None:
                return False
            if assignment.grades_published_at is not None:
                raise ValueError("frozen")
            if assignment.status == "published":
                finalized = session.scalar(
                    select(EducationAssignmentSubmission.id).where(
                        EducationAssignmentSubmission.assignment_id == assignment_id,
                        EducationAssignmentSubmission.status.in_(("finalized", "released")),
                    )
                )
                changed = (
                    bool(question.reference_answer)
                    and question.reference_answer != reference_answer
                    or bool(question.expected_points_json)
                    and question.expected_points_json != expected_points
                    or question.max_score > 0
                    and abs(question.max_score - max_score) > 0.001
                )
                if finalized is not None or changed:
                    raise ValueError("frozen")
            question.reference_answer = reference_answer
            question.expected_points_json = deepcopy(expected_points)
            question.max_score = max_score
            question.updated_at = utc_now()
            if assignment.status == "published":
                submissions = session.scalars(
                    select(EducationAssignmentSubmission).where(
                        EducationAssignmentSubmission.assignment_id == assignment_id,
                        EducationAssignmentSubmission.status.in_(("submitted", "review_draft")),
                    )
                ).all()
                for submission in submissions:
                    payload = deepcopy(submission.snapshot_json or {})
                    for item in payload.get("questions") or []:
                        if str(item.get("questionId")) == question_id:
                            item.update(
                                {
                                    "referenceAnswer": reference_answer,
                                    "expectedPoints": deepcopy(expected_points),
                                    "maxScore": max_score,
                                }
                            )
                    submission.snapshot_json = payload
                    grade = session.get(
                        EducationSubmissionQuestionGrade,
                        (submission.id, question_id),
                    )
                    if grade:
                        grade.reference_answer = reference_answer
                        grade.expected_points_json = deepcopy(expected_points)
                        grade.max_score = max_score
            return True

    def replace_node_questions(
        self,
        assignment_id: str,
        node_id: int,
        questions: list[dict],
        required_kinds: tuple[str, ...] | list[str],
    ) -> None:
        with self._session_factory() as session:
            assignment = session.scalar(
                select(EducationAssignment)
                .where(EducationAssignment.id == assignment_id)
                .with_for_update()
            )
            if assignment is None or assignment.status != "draft":
                raise ValueError("assessment draft changed")
            existing = session.scalars(
                select(EducationAssessmentQuestion).where(
                    EducationAssessmentQuestion.assignment_id == assignment_id,
                    EducationAssessmentQuestion.node_id == node_id,
                )
            ).all()
            scores = [row.max_score for row in existing]
            for row in existing:
                session.delete(row)
            by_kind = {item["kind"]: item for item in questions}
            for order, kind in enumerate(required_kinds, start=1):
                item = by_kind[kind]
                session.add(
                    EducationAssessmentQuestion(
                        id=uuid.uuid4().hex,
                        assignment_id=assignment_id,
                        node_id=node_id,
                        kind=kind,
                        question=str(item["question"]).strip(),
                        focus=str(item["focus"]).strip(),
                        expected_points_json=deepcopy(item["expectedPoints"]),
                        reference_answer=str(item["referenceAnswer"]).strip(),
                        max_score=(scores[order - 1] if len(scores) == len(required_kinds) else 0),
                        sort_order=order,
                    )
                )
            node = session.get(EducationAssessmentNode, (assignment_id, node_id))
            if node is None:
                raise ValueError("assessment node not found")
            node.status = "ready"
            node.last_error = None
            node.updated_at = utc_now()
            session.flush()
            self._rebalance_scores(session, assignment_id)

    def update_regenerated_question(
        self, assignment_id: str, node_id: int, question_id: str, item: dict
    ) -> bool:
        with self._session_factory() as session:
            assignment = session.get(EducationAssignment, assignment_id)
            question = session.scalar(
                select(EducationAssessmentQuestion)
                .where(
                    EducationAssessmentQuestion.id == question_id,
                    EducationAssessmentQuestion.assignment_id == assignment_id,
                    EducationAssessmentQuestion.node_id == node_id,
                )
                .with_for_update()
            )
            if assignment is None or assignment.status != "draft" or question is None:
                return False
            question.question = str(item["question"]).strip()
            question.focus = str(item["focus"]).strip()
            question.expected_points_json = deepcopy(item["expectedPoints"])
            question.reference_answer = str(item["referenceAnswer"]).strip()
            question.updated_at = utc_now()
            node = session.get(EducationAssessmentNode, (assignment_id, node_id))
            node.status = "ready"
            node.last_error = None
            node.updated_at = utc_now()
            return True

    def delete_question(self, assignment_id: str, node_id: int, question_id: str) -> bool:
        with self._session_factory() as session:
            assignment = session.get(EducationAssignment, assignment_id)
            question = session.scalar(
                select(EducationAssessmentQuestion).where(
                    EducationAssessmentQuestion.id == question_id,
                    EducationAssessmentQuestion.assignment_id == assignment_id,
                    EducationAssessmentQuestion.node_id == node_id,
                )
            )
            if assignment is None or assignment.status != "draft" or question is None:
                return False
            session.delete(question)
            session.flush()
            remaining = session.scalar(
                select(func.count())
                .select_from(EducationAssessmentQuestion)
                .where(
                    EducationAssessmentQuestion.assignment_id == assignment_id,
                    EducationAssessmentQuestion.node_id == node_id,
                )
            )
            node = session.get(EducationAssessmentNode, (assignment_id, node_id))
            node.status = "ready" if remaining else "exempt"
            node.updated_at = utc_now()
            self._rebalance_scores(session, assignment_id)
            return True

    def exempt_node(self, assignment_id: str, node_id: int) -> bool:
        with self._session_factory() as session:
            assignment = session.get(EducationAssignment, assignment_id)
            node = session.get(EducationAssessmentNode, (assignment_id, node_id))
            if assignment is None or assignment.status != "draft" or node is None:
                return False
            for question in session.scalars(
                select(EducationAssessmentQuestion).where(
                    EducationAssessmentQuestion.assignment_id == assignment_id,
                    EducationAssessmentQuestion.node_id == node_id,
                )
            ).all():
                session.delete(question)
            node.status = "exempt"
            node.last_error = None
            node.updated_at = utc_now()
            session.flush()
            self._rebalance_scores(session, assignment_id)
            return True

    def mark_assessment_failed(self, assignment_id: str, node_id: int, error: str) -> None:
        with self._session_factory() as session:
            node = session.get(EducationAssessmentNode, (assignment_id, node_id))
            if node:
                node.status = "failed"
                node.last_error = str(error)[:1000]
                node.updated_at = utc_now()

    def publish_assignment(self, assignment_id: str) -> dict | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(EducationAssignment)
                .where(EducationAssignment.id == assignment_id)
                .with_for_update()
            )
            if row is None or row.status != "draft":
                return None
            row.status = "published"
            row.published_at = utc_now()
            row.updated_at = utc_now()
            return {"published_at": _iso(row.published_at)}

    def upsert_progress(
        self,
        assignment_id: str,
        user_id: int,
        node_id: int,
        state: str,
        mastery_source: str = "self",
        summary: str | None = None,
    ) -> dict:
        with self._session_factory() as session:
            row = session.get(EducationNodeProgress, (assignment_id, user_id, node_id))
            if row is None:
                row = EducationNodeProgress(
                    assignment_id=assignment_id,
                    user_id=user_id,
                    node_id=node_id,
                    state=state,
                    mastery_source=mastery_source,
                )
                session.add(row)
            else:
                row.state = state
                row.mastery_source = mastery_source
            row.diagnostic_summary = summary
            row.updated_at = utc_now()
            session.flush()
            return {
                "nodeId": node_id,
                "state": state,
                "updatedAt": _iso(row.updated_at),
            }

    def get_submission(self, submission_id: str) -> dict | None:
        with self._session_factory() as session:
            row = session.get(EducationAssignmentSubmission, submission_id)
            return self._submission_dict(row) if row else None

    def get_student_submission(self, assignment_id: str, user_id: int) -> dict | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(EducationAssignmentSubmission).where(
                    EducationAssignmentSubmission.assignment_id == assignment_id,
                    EducationAssignmentSubmission.user_id == user_id,
                )
            )
            return self._submission_dict(row) if row else None

    def get_owned_submission(self, submission_id: str, user_id: int) -> dict | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(EducationAssignmentSubmission).where(
                    EducationAssignmentSubmission.id == submission_id,
                    EducationAssignmentSubmission.user_id == user_id,
                )
            )
            return self._submission_dict(row) if row else None

    def start_attempt(
        self, assignment_id: str, user_id: int, node_id: int
    ) -> tuple[dict, bool]:
        try:
            with self._session_factory() as session:
                existing = session.scalar(
                    select(EducationAssessmentAttempt).where(
                        EducationAssessmentAttempt.assignment_id == assignment_id,
                        EducationAssessmentAttempt.user_id == user_id,
                        EducationAssessmentAttempt.node_id == node_id,
                    )
                )
                if existing:
                    return self._attempt_dict(existing), False
                attempt = EducationAssessmentAttempt(
                    id=uuid.uuid4().hex,
                    assignment_id=assignment_id,
                    user_id=user_id,
                    node_id=node_id,
                    status="draft",
                    answers_json={},
                )
                session.add(attempt)
                session.flush()
                return self._attempt_dict(attempt), True
        except IntegrityError:
            with self._session_factory() as session:
                existing = session.scalar(
                    select(EducationAssessmentAttempt).where(
                        EducationAssessmentAttempt.assignment_id == assignment_id,
                        EducationAssessmentAttempt.user_id == user_id,
                        EducationAssessmentAttempt.node_id == node_id,
                    )
                )
                return self._attempt_dict(existing), False

    def get_attempt(self, attempt_id: str, user_id: int | None = None) -> dict | None:
        with self._session_factory() as session:
            filters = [EducationAssessmentAttempt.id == attempt_id]
            if user_id is not None:
                filters.append(EducationAssessmentAttempt.user_id == user_id)
            row = session.scalar(select(EducationAssessmentAttempt).where(*filters))
            return self._attempt_dict(row) if row else None

    def save_attempt_answers(
        self, attempt_id: str, user_id: int, raw_answers: dict[str, str]
    ) -> dict | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(EducationAssessmentAttempt)
                .where(
                    EducationAssessmentAttempt.id == attempt_id,
                    EducationAssessmentAttempt.user_id == user_id,
                )
                .with_for_update()
            )
            if row is None or row.status != "draft":
                return self._attempt_dict(row) if row else None
            answers = deepcopy(row.answers_json or {})
            answers.update(raw_answers)
            row.answers_json = answers
            row.updated_at = utc_now()
            session.flush()
            return self._attempt_dict(row)

    def complete_attempt(
        self, attempt_id: str, user_id: int, raw_answers: dict[str, str]
    ) -> dict | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(EducationAssessmentAttempt)
                .where(
                    EducationAssessmentAttempt.id == attempt_id,
                    EducationAssessmentAttempt.user_id == user_id,
                )
                .with_for_update()
            )
            if row is None:
                return None
            if row.status == "completed":
                return self._attempt_dict(row)
            answers = deepcopy(row.answers_json or {})
            answers.update(raw_answers)
            row.answers_json = answers
            row.status = "completed"
            row.completed_at = utc_now()
            row.updated_at = utc_now()
            progress = session.get(
                EducationNodeProgress,
                (row.assignment_id, user_id, row.node_id),
            )
            if progress is None:
                progress = EducationNodeProgress(
                    assignment_id=row.assignment_id,
                    user_id=user_id,
                    node_id=row.node_id,
                    state="in_progress",
                    mastery_source="self",
                )
                session.add(progress)
            elif progress.state != "mastered":
                progress.state = "in_progress"
                progress.mastery_source = "self"
            progress.updated_at = utc_now()
            session.flush()
            return self._attempt_dict(row)

    def submit_assignment(
        self, assignment_id: str, user_id: int
    ) -> tuple[dict | None, bool, list[str]]:
        try:
            with self._session_factory() as session:
                existing = session.scalar(
                    select(EducationAssignmentSubmission).where(
                        EducationAssignmentSubmission.assignment_id == assignment_id,
                        EducationAssignmentSubmission.user_id == user_id,
                    )
                )
                if existing:
                    return self._submission_dict(existing), False, []
                assignment = session.get(EducationAssignment, assignment_id)
                if (
                    assignment is None
                    or assignment.status != "published"
                    or assignment.grades_published_at is not None
                ):
                    raise ValueError("assignment closed")
                questions = session.scalars(
                    select(EducationAssessmentQuestion)
                    .where(EducationAssessmentQuestion.assignment_id == assignment_id)
                    .order_by(
                        EducationAssessmentQuestion.node_id,
                        EducationAssessmentQuestion.sort_order,
                    )
                ).all()
                attempts = {
                    row.node_id: row
                    for row in session.scalars(
                        select(EducationAssessmentAttempt).where(
                            EducationAssessmentAttempt.assignment_id == assignment_id,
                            EducationAssessmentAttempt.user_id == user_id,
                        )
                    ).all()
                }
                missing = []
                frozen = []
                for question in questions:
                    attempt = attempts.get(question.node_id)
                    answer = str(
                        (attempt.answers_json or {}).get(question.id) if attempt else ""
                    ).strip()
                    if not attempt or attempt.status != "completed" or not answer:
                        missing.append(question.id)
                        continue
                    frozen.append(
                        {
                            "questionId": question.id,
                            "nodeId": question.node_id,
                            "kind": question.kind,
                            "order": question.sort_order,
                            "question": question.question,
                            "focus": question.focus,
                            "expectedPoints": deepcopy(question.expected_points_json or []),
                            "referenceAnswer": question.reference_answer,
                            "maxScore": float(question.max_score or 0),
                            "studentAnswer": answer,
                        }
                    )
                if missing:
                    return None, False, missing
                submission = EducationAssignmentSubmission(
                    id=uuid.uuid4().hex,
                    assignment_id=assignment_id,
                    user_id=user_id,
                    status="submitted",
                    ai_status="not_started",
                    snapshot_json={
                        "assignmentVersion": assignment.version,
                        "questions": frozen,
                    },
                )
                session.add(submission)
                for item in frozen:
                    session.add(
                        EducationSubmissionQuestionGrade(
                            submission_id=submission.id,
                            question_id=item["questionId"],
                            node_id=item["nodeId"],
                            max_score=item["maxScore"],
                            student_answer=item["studentAnswer"],
                            reference_answer=item["referenceAnswer"],
                            expected_points_json=deepcopy(item["expectedPoints"]),
                        )
                    )
                session.flush()
                return self._submission_dict(submission), True, []
        except IntegrityError:
            existing = self.get_student_submission(assignment_id, user_id)
            return existing, False, []

    def list_submission_grades(self, submission_id: str) -> list[dict]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(EducationSubmissionQuestionGrade)
                .where(
                    EducationSubmissionQuestionGrade.submission_id == submission_id
                )
                .order_by(
                    EducationSubmissionQuestionGrade.node_id,
                    EducationSubmissionQuestionGrade.question_id,
                )
            ).all()
            return [self._grade_dict(row) for row in rows]

    def save_teacher_grades(
        self,
        submission_id: str,
        actor_id: int,
        grades: list[dict],
        summary: str,
    ) -> dict:
        with self._session_factory() as session:
            submission = session.scalar(
                select(EducationAssignmentSubmission)
                .where(EducationAssignmentSubmission.id == submission_id)
                .with_for_update()
            )
            if submission is None or submission.status in {"finalized", "released"}:
                raise ValueError("grading finalized")
            stored = {
                row.question_id: row
                for row in session.scalars(
                    select(EducationSubmissionQuestionGrade).where(
                        EducationSubmissionQuestionGrade.submission_id
                        == submission_id
                    )
                ).all()
            }
            for item in grades:
                question_id = str(item.get("questionId") or "")
                if question_id not in stored:
                    raise LookupError("unknown grading question")
                score = item.get("teacherScore")
                if score is not None:
                    score = round(float(score), 1)
                    if score < 0 or score > stored[question_id].max_score:
                        raise ValueError("grading score invalid")
                stored[question_id].teacher_score = score
                stored[question_id].teacher_feedback = str(
                    item.get("teacherFeedback") or ""
                ).strip()
                stored[question_id].updated_at = utc_now()
            submission.status = "review_draft"
            submission.teacher_summary = summary.strip()
            submission.updated_at = utc_now()
            session.add(
                AuditLog(
                    actor_id=actor_id,
                    action="submission.grade",
                    subject_type="education_submission",
                    subject_id=submission_id,
                    details={"question_ids": sorted(stored)},
                )
            )
            session.flush()
            return self._submission_dict(submission)

    def finalize_submission(self, submission_id: str) -> dict:
        with self._session_factory() as session:
            submission = session.scalar(
                select(EducationAssignmentSubmission)
                .where(EducationAssignmentSubmission.id == submission_id)
                .with_for_update()
            )
            if submission is None:
                raise LookupError("submission not found")
            grades = session.scalars(
                select(EducationSubmissionQuestionGrade).where(
                    EducationSubmissionQuestionGrade.submission_id == submission_id
                )
            ).all()
            if any(row.teacher_score is None for row in grades):
                raise ValueError("grading incomplete")
            submission.status = "finalized"
            submission.teacher_total = round(
                sum(float(row.teacher_score or 0) for row in grades), 1
            )
            submission.finalized_at = utc_now()
            submission.updated_at = utc_now()
            session.flush()
            return self._submission_dict(submission)

    def publish_grades(self, assignment_id: str) -> dict:
        with self._session_factory() as session:
            assignment = session.scalar(
                select(EducationAssignment)
                .where(EducationAssignment.id == assignment_id)
                .with_for_update()
            )
            if assignment is None:
                raise LookupError("assignment not found")
            submissions = session.scalars(
                select(EducationAssignmentSubmission).where(
                    EducationAssignmentSubmission.assignment_id == assignment_id
                )
            ).all()
            pending = [row.user_id for row in submissions if row.status != "finalized"]
            if not submissions or pending:
                return {"released": False, "pending": pending, "count": 0}
            now = utc_now()
            for submission in submissions:
                grouped: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
                for grade in session.scalars(
                    select(EducationSubmissionQuestionGrade).where(
                        EducationSubmissionQuestionGrade.submission_id
                        == submission.id
                    )
                ).all():
                    grouped[grade.node_id][0] += float(grade.teacher_score or 0)
                    grouped[grade.node_id][1] += float(grade.max_score or 0)
                for node_id, (score, maximum) in grouped.items():
                    progress = session.get(
                        EducationNodeProgress,
                        (assignment_id, submission.user_id, node_id),
                    )
                    if progress is None:
                        progress = EducationNodeProgress(
                            assignment_id=assignment_id,
                            user_id=submission.user_id,
                            node_id=node_id,
                            state="needs_review",
                            mastery_source="teacher",
                        )
                        session.add(progress)
                    progress.state = (
                        "mastered"
                        if maximum > 0 and score / maximum >= 0.6
                        else "needs_review"
                    )
                    progress.mastery_source = "teacher"
                    progress.diagnostic_summary = submission.teacher_summary
                    progress.updated_at = now
                submission.status = "released"
                submission.released_at = now
                submission.updated_at = now
            assignment.grades_published_at = now
            assignment.updated_at = now
            return {
                "released": True,
                "pending": [],
                "count": len(submissions),
                "published_at": _iso(now),
            }

    def grading_overview(self, assignment_id: str, public_class_id: str) -> list[dict]:
        with self._session_factory() as session:
            teaching_class = session.scalar(
                select(TeachingClass).where(
                    TeachingClass.public_id == public_class_id
                )
            )
            if teaching_class is None:
                return []
            members = session.scalars(
                select(ClassMembership)
                .where(
                    ClassMembership.teaching_class_id == teaching_class.id,
                    ClassMembership.role == "student",
                    ClassMembership.removed_at.is_(None),
                )
                .order_by(
                    ClassMembership.student_number,
                    ClassMembership.student_id,
                )
            ).all()
            result = []
            for member in members:
                submission = session.scalar(
                    select(EducationAssignmentSubmission).where(
                        EducationAssignmentSubmission.assignment_id == assignment_id,
                        EducationAssignmentSubmission.user_id == member.student_id,
                    )
                )
                result.append(
                    {
                        "user_id": member.student_id,
                        "student_name": member.student_name,
                        "student_number": member.student_number,
                        "submission_id": submission.id if submission else None,
                        "status": submission.status if submission else None,
                        "ai_status": submission.ai_status if submission else None,
                        "submitted_at": _iso(submission.submitted_at) if submission else None,
                        "ai_suggested_total": submission.ai_suggested_total if submission else None,
                        "teacher_total": submission.teacher_total if submission else None,
                        "updated_at": _iso(submission.updated_at) if submission else None,
                    }
                )
            return result

    def assignment_overview(self, assignment_id: str, public_class_id: str) -> list[dict]:
        with self._session_factory() as session:
            teaching_class = session.scalar(
                select(TeachingClass).where(
                    TeachingClass.public_id == public_class_id
                )
            )
            if teaching_class is None:
                return []
            members = session.scalars(
                select(ClassMembership)
                .where(
                    ClassMembership.teaching_class_id == teaching_class.id,
                    ClassMembership.role == "student",
                    ClassMembership.removed_at.is_(None),
                )
                .order_by(
                    ClassMembership.student_number,
                    ClassMembership.student_id,
                )
            ).all()
            result = []
            for member in members:
                progress = session.scalars(
                    select(EducationNodeProgress).where(
                        EducationNodeProgress.assignment_id == assignment_id,
                        EducationNodeProgress.user_id == member.student_id,
                    )
                ).all()
                latest_diagnostic = session.scalar(
                    select(EducationDiagnostic)
                    .where(
                        EducationDiagnostic.assignment_id == assignment_id,
                        EducationDiagnostic.user_id == member.student_id,
                        EducationDiagnostic.summary.is_not(None),
                    )
                    .order_by(EducationDiagnostic.updated_at.desc())
                    .limit(1)
                )
                result.append(
                    {
                        "user_id": member.student_id,
                        "student_name": member.student_name,
                        "student_number": member.student_number,
                        "mastered_count": sum(row.state == "mastered" for row in progress),
                        "needs_review_count": sum(row.state == "needs_review" for row in progress),
                        "last_activity": max(
                            (_iso(row.updated_at) for row in progress), default=None
                        ),
                        "diagnostic_summary": (
                            latest_diagnostic.summary if latest_diagnostic else None
                        ),
                    }
                )
            return result

    def submission_member_profile(self, submission_id: str) -> dict | None:
        with self._session_factory() as session:
            submission = session.get(EducationAssignmentSubmission, submission_id)
            if submission is None:
                return None
            assignment = session.get(EducationAssignment, submission.assignment_id)
            membership = session.scalar(
                select(ClassMembership).where(
                    ClassMembership.teaching_class_id
                    == assignment.teaching_class_id,
                    ClassMembership.student_id == submission.user_id,
                )
            )
            return (
                {
                    "student_name": membership.student_name,
                    "student_number": membership.student_number,
                }
                if membership
                else None
            )

    def set_submission_ai_state(
        self,
        submission_id: str,
        *,
        status: str | None = None,
        ai_status: str,
        ai_error: str | None = None,
        ai_total: float | None = None,
    ) -> dict | None:
        with self._session_factory() as session:
            row = session.get(EducationAssignmentSubmission, submission_id)
            if row is None:
                return None
            if status:
                row.status = status
            row.ai_status = ai_status
            row.ai_error = ai_error
            row.ai_suggested_total = ai_total
            row.updated_at = utc_now()
            session.flush()
            return self._submission_dict(row)

    def update_grade_analysis(
        self,
        submission_id: str,
        question_id: str,
        *,
        matrix_report: dict | None = None,
        ai_result: dict | None = None,
        ai_score: float | None = None,
    ) -> None:
        with self._session_factory() as session:
            row = session.get(
                EducationSubmissionQuestionGrade,
                (submission_id, question_id),
            )
            if row is None:
                return
            if matrix_report is not None:
                row.matrix_report_json = deepcopy(matrix_report)
            if ai_result is not None:
                row.ai_result_json = deepcopy(ai_result)
                row.ai_suggested_score = ai_score
            row.updated_at = utc_now()

    @staticmethod
    def _rebalance_scores(session, assignment_id: str) -> None:
        rows = session.scalars(
            select(EducationAssessmentQuestion)
            .where(EducationAssessmentQuestion.assignment_id == assignment_id)
            .order_by(
                EducationAssessmentQuestion.node_id,
                EducationAssessmentQuestion.sort_order,
            )
        ).all()
        if not rows:
            return
        base = round(100.0 / len(rows), 1)
        scores = [base for _ in rows]
        scores[-1] = round(100.0 - sum(scores[:-1]), 1)
        for row, score in zip(rows, scores, strict=True):
            row.max_score = score
            row.updated_at = utc_now()

    @staticmethod
    def _node_dict(row: EducationAssessmentNode) -> dict:
        return {
            "assignment_id": row.assignment_id,
            "node_id": row.node_id,
            "status": row.status,
            "last_error": row.last_error,
            "updated_at": _iso(row.updated_at),
        }

    @staticmethod
    def _question_dict(row: EducationAssessmentQuestion) -> dict:
        return {
            "id": row.id,
            "assignment_id": row.assignment_id,
            "node_id": row.node_id,
            "kind": row.kind,
            "question": row.question,
            "focus": row.focus,
            "expected_points_json": deepcopy(row.expected_points_json or []),
            "reference_answer": row.reference_answer,
            "max_score": float(row.max_score or 0),
            "sort_order": row.sort_order,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }

    @staticmethod
    def _attempt_dict(row: EducationAssessmentAttempt) -> dict:
        return {
            "id": row.id,
            "assignment_id": row.assignment_id,
            "user_id": row.user_id,
            "node_id": row.node_id,
            "status": row.status,
            "answers_json": deepcopy(row.answers_json or {}),
            "started_at": _iso(row.started_at),
            "updated_at": _iso(row.updated_at),
            "completed_at": _iso(row.completed_at),
        }

    @staticmethod
    def _submission_dict(row: EducationAssignmentSubmission) -> dict:
        return {
            "id": row.id,
            "assignment_id": row.assignment_id,
            "user_id": row.user_id,
            "status": row.status,
            "ai_status": row.ai_status,
            "snapshot_json": deepcopy(row.snapshot_json or {}),
            "ai_suggested_total": row.ai_suggested_total,
            "teacher_total": row.teacher_total,
            "teacher_summary": row.teacher_summary,
            "ai_error": row.ai_error,
            "submitted_at": _iso(row.submitted_at),
            "updated_at": _iso(row.updated_at),
            "finalized_at": _iso(row.finalized_at),
            "released_at": _iso(row.released_at),
        }

    @staticmethod
    def _grade_dict(row: EducationSubmissionQuestionGrade) -> dict:
        return {
            "submission_id": row.submission_id,
            "question_id": row.question_id,
            "node_id": row.node_id,
            "max_score": float(row.max_score or 0),
            "student_answer": row.student_answer,
            "reference_answer": row.reference_answer,
            "expected_points_json": deepcopy(row.expected_points_json or []),
            "matrix_report_json": deepcopy(row.matrix_report_json or {}),
            "ai_result_json": deepcopy(row.ai_result_json or {}),
            "ai_suggested_score": row.ai_suggested_score,
            "teacher_score": row.teacher_score,
            "teacher_feedback": row.teacher_feedback,
            "updated_at": _iso(row.updated_at),
        }
