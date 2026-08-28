"""班级、成员、教学图谱快照与作业的事务仓储。"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from storage.database import session_scope
from storage.models import (
    ClassMembership,
    Course,
    EducationAiTask,
    EducationAiUsage,
    EducationAssignment,
    EducationAssignmentSubmission,
    EducationAssessmentAttempt,
    EducationAssessmentNode,
    EducationAssessmentQuestion,
    EducationDiagnostic,
    EducationNodeIdentity,
    EducationNodeOccurrence,
    EducationSnapshot,
    History,
    ProofWorkspace,
    TeachingClass,
    utc_now,
)


SessionFactory = Callable[[], AbstractContextManager[Session]]
DEFAULT_COURSE_CODE = "MATHWEAVER"


class EducationConflictError(RuntimeError):
    """唯一键或资源状态冲突。"""


class StudentNumberConflictError(EducationConflictError):
    """同一班级内学号已经被其他账号使用。"""


class MembershipRemovedError(EducationConflictError):
    """成员已被教师移除，只能由教师显式恢复。"""


class ClassRoleConflictError(EducationConflictError):
    """同一账号不能在同一班级同时承担冲突角色。"""


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("due_at must be an ISO datetime") from exc


class EducationRepository:
    """以公开班级 ID 为边界，内部整数主键不进入 API。"""

    def __init__(self, session_factory: SessionFactory = session_scope) -> None:
        self._session_factory = session_factory

    def create_class(self, owner_user_id: int, title: str) -> dict:
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("class title is required")
        with self._session_factory() as session:
            course = session.scalar(select(Course).where(Course.code == DEFAULT_COURSE_CODE))
            if course is None:
                course = Course(code=DEFAULT_COURSE_CODE, name="MathWeaver")
                session.add(course)
                session.flush()
            teaching_class = TeachingClass(
                public_id=uuid.uuid4().hex,
                course_id=course.id,
                teacher_id=owner_user_id,
                name=normalized_title[:120],
                invite_code=secrets.token_hex(4).upper(),
            )
            session.add(teaching_class)
            session.flush()
            session.add(
                ClassMembership(
                    teaching_class_id=teaching_class.id,
                    student_id=owner_user_id,
                    role="teacher",
                )
            )
            session.flush()
            return self._class_dict(teaching_class)

    def get_class(self, public_id: str) -> dict | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(TeachingClass).where(TeachingClass.public_id == public_id)
            )
            return self._class_dict(row) if row is not None else None

    def list_user_classes(self, user_id: int, role: str) -> list[dict]:
        with self._session_factory() as session:
            rows = session.execute(
                select(TeachingClass, ClassMembership)
                .join(
                    ClassMembership,
                    ClassMembership.teaching_class_id == TeachingClass.id,
                )
                .where(
                    ClassMembership.student_id == user_id,
                    ClassMembership.role == role,
                    ClassMembership.removed_at.is_(None),
                    TeachingClass.archived_at.is_(None),
                )
                .order_by(TeachingClass.created_at.desc())
            ).all()
            result = []
            for teaching_class, membership in rows:
                member_count = session.scalar(
                    select(func.count())
                    .select_from(ClassMembership)
                    .where(
                        ClassMembership.teaching_class_id == teaching_class.id,
                        ClassMembership.removed_at.is_(None),
                    )
                )
                assignment_filters = [
                    EducationAssignment.teaching_class_id == teaching_class.id
                ]
                assignment_filters.append(
                    EducationAssignment.status == "published"
                    if role == "student"
                    else EducationAssignment.status != "archived"
                )
                assignment_count = session.scalar(
                    select(func.count())
                    .select_from(EducationAssignment)
                    .where(*assignment_filters)
                )
                result.append(
                    {
                        **self._class_dict(teaching_class),
                        "role": membership.role,
                        "student_name": membership.student_name,
                        "student_number": membership.student_number,
                        "member_count": int(member_count or 0),
                        "assignment_count": int(assignment_count or 0),
                    }
                )
            return result

    def get_membership(
        self, public_class_id: str, user_id: int, *, include_removed: bool = False
    ) -> dict | None:
        with self._session_factory() as session:
            filters = [
                TeachingClass.public_id == public_class_id,
                ClassMembership.student_id == user_id,
                TeachingClass.archived_at.is_(None),
            ]
            if not include_removed:
                filters.append(ClassMembership.removed_at.is_(None))
            row = session.execute(
                select(TeachingClass, ClassMembership)
                .join(
                    ClassMembership,
                    ClassMembership.teaching_class_id == TeachingClass.id,
                )
                .where(*filters)
            ).first()
            if row is None:
                return None
            return self._membership_dict(*row)

    def join_student(
        self,
        class_or_invite: str,
        invite_code: str,
        user_id: int,
        student_name: str,
        student_number: str,
    ) -> dict:
        try:
            with self._session_factory() as session:
                teaching_class = session.scalar(
                    select(TeachingClass)
                    .where(
                        TeachingClass.archived_at.is_(None),
                        or_(
                            TeachingClass.public_id == class_or_invite,
                            TeachingClass.invite_code == invite_code,
                        ),
                    )
                    .with_for_update()
                )
                if teaching_class is None or teaching_class.invite_code != invite_code:
                    raise LookupError("invalid invite code")
                membership = session.scalar(
                    select(ClassMembership)
                    .where(
                        ClassMembership.teaching_class_id == teaching_class.id,
                        ClassMembership.student_id == user_id,
                    )
                    .with_for_update()
                )
                if membership is not None:
                    if membership.role != "student":
                        raise ClassRoleConflictError
                    if membership.removed_at is not None:
                        raise MembershipRemovedError
                else:
                    membership = ClassMembership(
                        teaching_class_id=teaching_class.id,
                        student_id=user_id,
                        role="student",
                    )
                    session.add(membership)
                membership.student_name = student_name
                membership.student_number = student_number
                session.flush()
                return self._membership_dict(teaching_class, membership)
        except IntegrityError as exc:
            raise StudentNumberConflictError from exc

    def update_student_profile(
        self,
        public_class_id: str,
        user_id: int,
        student_name: str,
        student_number: str,
    ) -> dict | None:
        try:
            with self._session_factory() as session:
                row = session.execute(
                    select(TeachingClass, ClassMembership)
                    .join(
                        ClassMembership,
                        ClassMembership.teaching_class_id == TeachingClass.id,
                    )
                    .where(
                        TeachingClass.public_id == public_class_id,
                        TeachingClass.archived_at.is_(None),
                        ClassMembership.student_id == user_id,
                        ClassMembership.role == "student",
                        ClassMembership.removed_at.is_(None),
                    )
                    .with_for_update()
                ).first()
                if row is None:
                    return None
                teaching_class, membership = row
                membership.student_name = student_name
                membership.student_number = student_number
                session.flush()
                return self._membership_dict(teaching_class, membership)
        except IntegrityError as exc:
            raise StudentNumberConflictError from exc

    def rename_class(self, public_class_id: str, title: str) -> dict | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(TeachingClass)
                .where(TeachingClass.public_id == public_class_id)
                .with_for_update()
            )
            if row is None:
                return None
            row.name = title.strip()[:120]
            session.flush()
            return self._class_dict(row)

    def archive_class(self, public_class_id: str) -> str | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(TeachingClass)
                .where(TeachingClass.public_id == public_class_id)
                .with_for_update()
            )
            if row is None:
                return None
            if row.archived_at is None:
                row.archived_at = utc_now()
            return _iso(row.archived_at)

    def list_class_students(self, public_class_id: str) -> list[dict]:
        with self._session_factory() as session:
            class_id = session.scalar(
                select(TeachingClass.id).where(
                    TeachingClass.public_id == public_class_id
                )
            )
            if class_id is None:
                return []
            rows = session.scalars(
                select(ClassMembership)
                .where(
                    ClassMembership.teaching_class_id == class_id,
                    ClassMembership.role == "student",
                )
                .order_by(
                    ClassMembership.removed_at.is_not(None),
                    ClassMembership.student_number,
                    ClassMembership.student_id,
                )
            ).all()
            return [
                {
                    "user_id": row.student_id,
                    "student_name": row.student_name,
                    "student_number": row.student_number,
                    "joined_at": _iso(row.created_at),
                    "removed_at": _iso(row.removed_at),
                }
                for row in rows
            ]

    def remove_student(self, public_class_id: str, user_id: int) -> str | None:
        with self._session_factory() as session:
            membership = self._locked_student_membership(
                session, public_class_id, user_id
            )
            if membership is None:
                return None
            if membership.removed_at is None:
                membership.removed_at = utc_now()
            return _iso(membership.removed_at)

    def restore_student(self, public_class_id: str, user_id: int) -> bool:
        with self._session_factory() as session:
            membership = self._locked_student_membership(
                session, public_class_id, user_id
            )
            if membership is None:
                return False
            membership.removed_at = None
            return True

    def list_snapshots(self, public_class_id: str) -> list[dict]:
        with self._session_factory() as session:
            rows = session.execute(
                select(EducationSnapshot, TeachingClass)
                .join(
                    TeachingClass,
                    TeachingClass.id == EducationSnapshot.teaching_class_id,
                )
                .where(TeachingClass.public_id == public_class_id)
                .order_by(EducationSnapshot.created_at.desc())
            ).all()
            result = []
            for snapshot, teaching_class in rows:
                count = session.scalar(
                    select(func.count())
                    .select_from(EducationAssignment)
                    .where(EducationAssignment.snapshot_id == snapshot.id)
                )
                item = self._snapshot_dict(snapshot, teaching_class)
                item["bound_assignment_count"] = int(count or 0)
                result.append(item)
            return result

    def get_snapshot(self, snapshot_id: str) -> dict | None:
        with self._session_factory() as session:
            row = session.execute(
                select(EducationSnapshot, TeachingClass)
                .join(
                    TeachingClass,
                    TeachingClass.id == EducationSnapshot.teaching_class_id,
                )
                .where(EducationSnapshot.id == snapshot_id)
            ).first()
            return self._snapshot_dict(*row) if row is not None else None

    def create_snapshot(
        self,
        *,
        public_class_id: str,
        actor_id: int,
        source_graph_id: str | None,
        source_history_id: str | None,
        filename: str,
        nodes: list[dict],
        edges: list[dict],
        source_markdown: str,
        latex_macros: dict,
        source_pdf: dict | None,
    ) -> tuple[dict, bool]:
        with self._session_factory() as session:
            teaching_class = session.scalar(
                select(TeachingClass)
                .where(
                    TeachingClass.public_id == public_class_id,
                    TeachingClass.archived_at.is_(None),
                )
                .with_for_update()
            )
            if teaching_class is None:
                raise LookupError("class not found")
            membership = session.scalar(
                select(ClassMembership).where(
                    ClassMembership.teaching_class_id == teaching_class.id,
                    ClassMembership.student_id == actor_id,
                    ClassMembership.role == "teacher",
                    ClassMembership.removed_at.is_(None),
                )
            )
            if membership is None or teaching_class.teacher_id != actor_id:
                raise PermissionError("forbidden")
            if source_graph_id:
                existing = session.scalar(
                    select(EducationSnapshot)
                    .where(
                        EducationSnapshot.teaching_class_id == teaching_class.id,
                        EducationSnapshot.source_graph_id == source_graph_id,
                    )
                    .order_by(EducationSnapshot.created_at.asc())
                )
                if existing is not None:
                    return self._snapshot_dict(existing, teaching_class), False

            if source_history_id:
                history = session.scalar(
                    select(History).where(
                        History.id == source_history_id,
                        History.user_id == actor_id,
                    )
                )
                if history is not None:
                    # 服务器历史记录是可信源；客户端重复字段只用于无历史导入。
                    nodes = deepcopy(history.nodes_json or [])
                    edges = deepcopy(history.edges_json or [])
                    source_markdown = history.source_markdown or ""
                    filename = history.filename
                    try:
                        import json

                        parsed_macros = json.loads(history.latex_macros or "{}")
                    except (TypeError, ValueError):
                        parsed_macros = {}
                    latex_macros = parsed_macros if isinstance(parsed_macros, dict) else {}
                    source_pdf = deepcopy(history.source_pdf_json)

            self._validate_snapshot_graph(nodes, edges)
            snapshot = EducationSnapshot(
                id=uuid.uuid4().hex,
                teaching_class_id=teaching_class.id,
                source_graph_id=source_graph_id,
                filename=filename[:512] or "教学图谱",
                nodes_json=deepcopy(nodes),
                edges_json=deepcopy(edges),
                source_markdown=source_markdown,
                latex_macros_json=deepcopy(latex_macros),
                source_pdf_json=deepcopy(source_pdf),
                created_by=actor_id,
            )
            session.add(snapshot)
            session.flush()
            for node in nodes:
                node_id = int(node["id"])
                global_id = str(
                    node.get("global_id")
                    or node.get("globalId")
                    or f"source:{source_graph_id or snapshot.id}:node:{node_id}"
                )[:128]
                identity = session.scalar(
                    select(EducationNodeIdentity).where(
                        EducationNodeIdentity.teaching_class_id == teaching_class.id,
                        EducationNodeIdentity.global_id == global_id,
                    )
                )
                if identity is None:
                    identity = EducationNodeIdentity(
                        id=uuid.uuid4().hex,
                        teaching_class_id=teaching_class.id,
                        global_id=global_id,
                        title=str(
                            node.get("title_zh")
                            or node.get("title_en")
                            or node.get("label")
                            or ""
                        )[:512],
                    )
                    session.add(identity)
                    session.flush()
                session.add(
                    EducationNodeOccurrence(
                        snapshot_id=snapshot.id,
                        node_id=node_id,
                        canonical_node_id=identity.id,
                        global_id=global_id,
                    )
                )
            session.flush()
            return self._snapshot_dict(snapshot, teaching_class), True

    def delete_snapshot_group(self, snapshot_id: str) -> dict | None:
        """原子删除同一课程图谱的快照与作业；文件清理由 API 在提交后处理。"""
        with self._session_factory() as session:
            snapshot = session.get(EducationSnapshot, snapshot_id)
            if snapshot is None:
                return None
            if snapshot.source_graph_id:
                snapshot_ids = list(
                    session.scalars(
                        select(EducationSnapshot.id).where(
                            EducationSnapshot.teaching_class_id
                            == snapshot.teaching_class_id,
                            EducationSnapshot.source_graph_id
                            == snapshot.source_graph_id,
                        )
                    ).all()
                )
            else:
                snapshot_ids = [snapshot.id]
            assignment_ids = list(
                session.scalars(
                    select(EducationAssignment.id).where(
                        EducationAssignment.snapshot_id.in_(snapshot_ids)
                    )
                ).all()
            )
            diagnostic_ids = (
                list(
                    session.scalars(
                        select(EducationDiagnostic.id).where(
                            EducationDiagnostic.assignment_id.in_(assignment_ids)
                        )
                    ).all()
                )
                if assignment_ids
                else []
            )
            task_keys = assignment_ids + diagnostic_ids
            if task_keys:
                session.execute(
                    delete(EducationAiTask).where(
                        EducationAiTask.task_key.in_(task_keys)
                    )
                )
            if assignment_ids:
                session.execute(
                    delete(EducationAssignment).where(
                        EducationAssignment.id.in_(assignment_ids)
                    )
                )
            session.execute(
                delete(EducationSnapshot).where(
                    EducationSnapshot.id.in_(snapshot_ids)
                )
            )
            return {
                "snapshot_ids": snapshot_ids,
                "assignment_ids": assignment_ids,
                "diagnostic_ids": diagnostic_ids,
            }

    def list_assignments(self, public_class_id: str, role: str) -> list[dict]:
        with self._session_factory() as session:
            filters = [TeachingClass.public_id == public_class_id]
            filters.append(
                EducationAssignment.status == "published"
                if role == "student"
                else EducationAssignment.status != "archived"
            )
            rows = session.execute(
                select(EducationAssignment, TeachingClass)
                .join(
                    TeachingClass,
                    TeachingClass.id == EducationAssignment.teaching_class_id,
                )
                .where(*filters)
                .order_by(EducationAssignment.updated_at.desc())
            ).all()
            return [self._assignment_dict(*row) for row in rows]

    def get_assignment(self, assignment_id: str) -> dict | None:
        with self._session_factory() as session:
            row = session.execute(
                select(EducationAssignment, TeachingClass)
                .join(
                    TeachingClass,
                    TeachingClass.id == EducationAssignment.teaching_class_id,
                )
                .where(EducationAssignment.id == assignment_id)
            ).first()
            return self._assignment_dict(*row) if row is not None else None

    def create_assignment(
        self,
        *,
        public_class_id: str,
        snapshot_id: str,
        actor_id: int,
        title: str,
        target_node_id: int,
        due_at: str | datetime | None,
        path: dict,
    ) -> dict:
        path_node_ids = self._path_node_ids(path)
        if target_node_id not in path_node_ids:
            raise ValueError("target node must be present in the learning path")
        with self._session_factory() as session:
            row = session.execute(
                select(TeachingClass, EducationSnapshot)
                .join(
                    EducationSnapshot,
                    EducationSnapshot.teaching_class_id == TeachingClass.id,
                )
                .where(
                    TeachingClass.public_id == public_class_id,
                    TeachingClass.teacher_id == actor_id,
                    TeachingClass.archived_at.is_(None),
                    EducationSnapshot.id == snapshot_id,
                )
                .with_for_update()
            ).first()
            if row is None:
                raise LookupError("snapshot not found")
            teaching_class, _snapshot = row
            assignment = EducationAssignment(
                id=uuid.uuid4().hex,
                teaching_class_id=teaching_class.id,
                snapshot_id=snapshot_id,
                title=title.strip()[:160] or f"学习：{target_node_id}",
                target_node_id=target_node_id,
                due_at=_parse_datetime(due_at),
                status="draft",
                base_path_json=deepcopy(path),
                summary=str(path.get("summary") or ""),
                created_by=actor_id,
            )
            session.add(assignment)
            for node_id in path_node_ids:
                session.add(
                    EducationAssessmentNode(
                        assignment_id=assignment.id,
                        node_id=node_id,
                        status="pending",
                    )
                )
            session.flush()
            return self._assignment_dict(assignment, teaching_class)

    def update_assignment(
        self,
        assignment_id: str,
        *,
        title: str,
        due_at: str | datetime | None,
        path: dict | None = None,
        require_status: str | None = None,
    ) -> dict | None:
        with self._session_factory() as session:
            row = session.execute(
                select(EducationAssignment, TeachingClass)
                .join(
                    TeachingClass,
                    TeachingClass.id == EducationAssignment.teaching_class_id,
                )
                .where(EducationAssignment.id == assignment_id)
                .with_for_update()
            ).first()
            if row is None:
                return None
            assignment, teaching_class = row
            if require_status and assignment.status != require_status:
                return None
            assignment.title = title.strip()[:160]
            assignment.due_at = _parse_datetime(due_at)
            if path is not None:
                node_ids = self._path_node_ids(path)
                if assignment.target_node_id not in node_ids:
                    raise ValueError("target node must be present in the learning path")
                existing = set(
                    session.scalars(
                        select(EducationAssessmentNode.node_id).where(
                            EducationAssessmentNode.assignment_id == assignment_id
                        )
                    ).all()
                )
                removed = existing - set(node_ids)
                if removed:
                    session.execute(
                        delete(EducationAssessmentQuestion).where(
                            EducationAssessmentQuestion.assignment_id == assignment_id,
                            EducationAssessmentQuestion.node_id.in_(removed),
                        )
                    )
                    session.execute(
                        delete(EducationAssessmentAttempt).where(
                            EducationAssessmentAttempt.assignment_id == assignment_id,
                            EducationAssessmentAttempt.node_id.in_(removed),
                        )
                    )
                    session.execute(
                        delete(EducationAssessmentNode).where(
                            EducationAssessmentNode.assignment_id == assignment_id,
                            EducationAssessmentNode.node_id.in_(removed),
                        )
                    )
                for node_id in set(node_ids) - existing:
                    session.add(
                        EducationAssessmentNode(
                            assignment_id=assignment_id,
                            node_id=node_id,
                            status="pending",
                        )
                    )
                assignment.base_path_json = deepcopy(path)
                assignment.summary = str(path.get("summary") or "")
            assignment.updated_at = utc_now()
            session.flush()
            return self._assignment_dict(assignment, teaching_class)

    def archive_assignment(self, assignment_id: str) -> bool:
        with self._session_factory() as session:
            row = session.scalar(
                select(EducationAssignment)
                .where(EducationAssignment.id == assignment_id)
                .with_for_update()
            )
            if row is None:
                return False
            row.status = "archived"
            row.updated_at = utc_now()
            return True

    def list_assessments(
        self, assignment_id: str, *, role: str | None, user_id: int | None = None
    ) -> list[dict]:
        with self._session_factory() as session:
            nodes = session.scalars(
                select(EducationAssessmentNode)
                .where(EducationAssessmentNode.assignment_id == assignment_id)
                .order_by(EducationAssessmentNode.node_id)
            ).all()
            questions = session.scalars(
                select(EducationAssessmentQuestion)
                .where(EducationAssessmentQuestion.assignment_id == assignment_id)
                .order_by(
                    EducationAssessmentQuestion.node_id,
                    EducationAssessmentQuestion.sort_order,
                )
            ).all()
            by_node: dict[int, list[EducationAssessmentQuestion]] = {}
            for question in questions:
                by_node.setdefault(question.node_id, []).append(question)
            attempts: dict[int, EducationAssessmentAttempt] = {}
            if role == "student" and user_id is not None:
                attempts = {
                    row.node_id: row
                    for row in session.scalars(
                        select(EducationAssessmentAttempt).where(
                            EducationAssessmentAttempt.assignment_id == assignment_id,
                            EducationAssessmentAttempt.user_id == user_id,
                        )
                    ).all()
                }
            result = []
            for node in nodes:
                item: dict[str, Any] = {
                    "node_id": node.node_id,
                    "status": node.status,
                    "last_error": node.last_error,
                    "updated_at": _iso(node.updated_at),
                    "questions": [],
                }
                if role == "teacher":
                    item["questions"] = [
                        {
                            "id": question.id,
                            "node_id": question.node_id,
                            "kind": question.kind,
                            "sort_order": question.sort_order,
                            "question": question.question,
                            "focus": question.focus,
                            "expected_points_json": deepcopy(
                                question.expected_points_json or []
                            ),
                            "reference_answer": question.reference_answer,
                            "max_score": question.max_score,
                        }
                        for question in by_node.get(node.node_id, [])
                    ]
                elif role == "student":
                    attempt = attempts.get(node.node_id)
                    item["attempt"] = (
                        {
                            "status": attempt.status,
                            "updated_at": _iso(attempt.updated_at),
                            "completed_at": _iso(attempt.completed_at),
                        }
                        if attempt
                        else None
                    )
                result.append(item)
            return result

    def get_progress_map(self, assignment_id: str, user_id: int) -> dict[int, dict]:
        from storage.models import EducationNodeProgress

        with self._session_factory() as session:
            rows = session.scalars(
                select(EducationNodeProgress).where(
                    EducationNodeProgress.assignment_id == assignment_id,
                    EducationNodeProgress.user_id == user_id,
                )
            ).all()
            return {
                row.node_id: {
                    "state": row.state,
                    "masterySource": row.mastery_source,
                    "diagnosticSummary": row.diagnostic_summary,
                    "updatedAt": _iso(row.updated_at),
                }
                for row in rows
            }

    def get_submission_summary(self, assignment_id: str, user_id: int) -> dict | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(EducationAssignmentSubmission).where(
                    EducationAssignmentSubmission.assignment_id == assignment_id,
                    EducationAssignmentSubmission.user_id == user_id,
                )
            )
            if row is None:
                return None
            payload = {
                "id": row.id,
                "status": row.status,
                "submittedAt": _iso(row.submitted_at),
                "updatedAt": _iso(row.updated_at),
                "releasedAt": _iso(row.released_at),
            }
            if row.status == "released":
                payload["teacherTotal"] = float(row.teacher_total or 0)
                payload["teacherSummary"] = row.teacher_summary or ""
            return payload

    def ai_usage_count(self, user_id: int, usage_day: date) -> int:
        with self._session_factory() as session:
            row = session.get(EducationAiUsage, (user_id, usage_day))
            return int(row.request_count or 0) if row is not None else 0

    @staticmethod
    def _class_dict(row: TeachingClass) -> dict:
        return {
            "id": row.public_id,
            "title": row.name,
            "invite_code": row.invite_code,
            "owner_user_id": row.teacher_id,
            "created_at": _iso(row.created_at),
        }

    @classmethod
    def _membership_dict(
        cls, teaching_class: TeachingClass, membership: ClassMembership
    ) -> dict:
        return {
            **cls._class_dict(teaching_class),
            "role": membership.role,
            "student_name": membership.student_name,
            "student_number": membership.student_number,
            "joined_at": _iso(membership.created_at),
            "removed_at": _iso(membership.removed_at),
        }

    @staticmethod
    def _snapshot_dict(
        snapshot: EducationSnapshot, teaching_class: TeachingClass
    ) -> dict:
        return {
            "id": snapshot.id,
            "class_id": teaching_class.public_id,
            "source_graph_id": snapshot.source_graph_id,
            "filename": snapshot.filename,
            "nodes_json": deepcopy(snapshot.nodes_json or []),
            "edges_json": deepcopy(snapshot.edges_json or []),
            "source_markdown": snapshot.source_markdown,
            "latex_macros_json": deepcopy(snapshot.latex_macros_json or {}),
            "source_pdf_json": deepcopy(snapshot.source_pdf_json),
            "created_by": snapshot.created_by,
            "created_at": _iso(snapshot.created_at),
        }

    @staticmethod
    def _assignment_dict(
        assignment: EducationAssignment, teaching_class: TeachingClass
    ) -> dict:
        return {
            "id": assignment.id,
            "class_id": teaching_class.public_id,
            "snapshot_id": assignment.snapshot_id,
            "title": assignment.title,
            "target_node_id": assignment.target_node_id,
            "due_at": _iso(assignment.due_at),
            "status": assignment.status,
            "base_path_json": deepcopy(assignment.base_path_json or {}),
            "summary": assignment.summary,
            "version": assignment.version,
            "published_at": _iso(assignment.published_at),
            "grades_published_at": _iso(assignment.grades_published_at),
            "created_by": assignment.created_by,
            "created_at": _iso(assignment.created_at),
            "updated_at": _iso(assignment.updated_at),
        }

    @staticmethod
    def _path_node_ids(path: dict) -> list[int]:
        result = []
        for step in path.get("steps") or []:
            if not isinstance(step, dict) or not isinstance(step.get("nodeId"), int):
                continue
            if step["nodeId"] not in result:
                result.append(step["nodeId"])
        return result

    @staticmethod
    def _validate_snapshot_graph(nodes: list[dict], edges: list[dict]) -> None:
        if not isinstance(nodes, list) or not nodes or not isinstance(edges, list):
            raise ValueError("nodes and edges are required")
        node_ids = [node.get("id") for node in nodes if isinstance(node, dict)]
        if len(node_ids) != len(nodes) or any(
            not isinstance(node_id, int) for node_id in node_ids
        ):
            raise ValueError("snapshot nodes require integer ids")
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("snapshot node ids must be unique")

    @staticmethod
    def _locked_student_membership(
        session: Session, public_class_id: str, user_id: int
    ) -> ClassMembership | None:
        return session.scalar(
            select(ClassMembership)
            .join(
                TeachingClass,
                TeachingClass.id == ClassMembership.teaching_class_id,
            )
            .where(
                TeachingClass.public_id == public_class_id,
                ClassMembership.student_id == user_id,
                ClassMembership.role == "student",
            )
            .with_for_update()
        )
