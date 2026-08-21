"""Web 认证所需的用户与会话持久化边界。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from storage.database import session_scope
from storage.models import ClassMembership, Course, LoginSession, TeachingClass, User


SessionFactory = Callable[[], AbstractContextManager[Session]]


@dataclass(frozen=True)
class StudentImportRecord:
    """仓储批次写入所需的已规范化、已哈希学生数据。"""

    line: int
    student_no: str
    display_name: str
    email: str | None
    class_code: str | None
    password_hash: str


@dataclass(frozen=True)
class ImportConflict:
    line: int
    field: str
    message: str


class StudentBatchImportError(RuntimeError):
    """数据库拒绝原子导入；上层仅暴露稳定且不含内部信息的错误。"""


class AuthUserTransaction:
    """封装持锁用户行上的认证写入；实例只在仓储事务上下文内有效。"""

    def __init__(self, session: Session, user: User) -> None:
        self._session = session
        self.user = user

    def has_active_session(self, token_hash: str, now: datetime) -> bool:
        session_id = self._session.scalar(
            select(LoginSession.id).where(
                LoginSession.user_id == self.user.id,
                LoginSession.token_hash == token_hash,
                LoginSession.revoked_at.is_(None),
                LoginSession.expires_at > now,
            )
        )
        return session_id is not None

    def insert_session(self, token_hash: str, expires_at: datetime) -> None:
        self._session.add(
            LoginSession(
                user_id=self.user.id,
                token_hash=token_hash,
                expires_at=expires_at,
            )
        )

    def replace_password(
        self,
        password_hash: str,
        initial_password_pending: bool,
        now: datetime,
        new_token_hash: str | None = None,
        new_token_expires_at: datetime | None = None,
    ) -> None:
        self.user.password_hash = password_hash
        self.user.initial_password_pending = initial_password_pending
        self._revoke_all_sessions(now)
        if new_token_hash is not None and new_token_expires_at is not None:
            # 替代会话与密码变更原子提交，避免成功响应携带已被旧事务覆盖的令牌。
            self.insert_session(new_token_hash, new_token_expires_at)

    def set_active_status(self, is_active: bool, now: datetime) -> None:
        self.user.is_active = is_active
        if not is_active:
            self._revoke_all_sessions(now)

    def _revoke_all_sessions(self, now: datetime) -> None:
        self._session.execute(
            update(LoginSession)
            .where(
                LoginSession.user_id == self.user.id,
                LoginSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )


class AuthRepository:
    """封装认证查询和会话写入，不感知 HTTP 请求。"""

    def __init__(self, session_factory: SessionFactory = session_scope) -> None:
        self._session_factory = session_factory

    def find_user_by_identifier(self, identifier: str) -> User | None:
        condition = self._identifier_condition(identifier)
        if condition is None:
            return None
        with self._session_factory() as session:
            return session.scalar(select(User).where(condition))

    @contextmanager
    def user_transaction_by_identifier(
        self, identifier: str
    ) -> Iterator[AuthUserTransaction | None]:
        condition = self._identifier_condition(identifier)
        if condition is None:
            yield None
            return
        with self._session_factory() as session:
            # 所有账号状态决策都从持锁行读取，跨 worker 也由 MySQL 串行化。
            user = session.scalar(select(User).where(condition).with_for_update())
            yield AuthUserTransaction(session, user) if user is not None else None

    @contextmanager
    def user_transaction_by_id(
        self, user_id: int
    ) -> Iterator[AuthUserTransaction | None]:
        with self._session_factory() as session:
            user = session.scalar(
                select(User).where(User.id == user_id).with_for_update()
            )
            yield AuthUserTransaction(session, user) if user is not None else None

    def insert_session(
        self, user_id: int, token_hash: str, expires_at: datetime
    ) -> None:
        with self._session_factory() as session:
            session.add(
                LoginSession(
                    user_id=user_id,
                    token_hash=token_hash,
                    expires_at=expires_at,
                )
            )

    def find_active_session(self, token_hash: str, now: datetime) -> User | None:
        with self._session_factory() as session:
            row = session.execute(
                select(User, LoginSession)
                .join(LoginSession, LoginSession.user_id == User.id)
                .where(
                    LoginSession.token_hash == token_hash,
                    LoginSession.revoked_at.is_(None),
                    LoginSession.expires_at > now,
                    User.is_active.is_(True),
                )
            ).first()
            if row is None:
                return None
            user, login_session = row
            # 仅成功认证才刷新使用时间，失效令牌不产生持久化副作用。
            login_session.last_used_at = now
            return user

    def revoke_session(self, token_hash: str, now: datetime) -> None:
        with self._session_factory() as session:
            session.execute(
                update(LoginSession)
                .where(
                    LoginSession.token_hash == token_hash,
                    LoginSession.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )

    def revoke_all_user_sessions(
        self,
        user_id: int,
        now: datetime,
        except_hash: str | None = None,
    ) -> int:
        filters = [
            LoginSession.user_id == user_id,
            LoginSession.revoked_at.is_(None),
        ]
        if except_hash is not None:
            filters.append(LoginSession.token_hash != except_hash)
        with self._session_factory() as session:
            result = session.execute(
                update(LoginSession).where(*filters).values(revoked_at=now)
            )
            return int(result.rowcount or 0)

    def import_student_batch(
        self, records: list[StudentImportRecord], actor_id: int
    ) -> tuple[ImportConflict, ...]:
        """先检查冲突，再在同一事务创建账号、班级基础与成员关系。"""
        try:
            with self._session_factory() as session:
                student_numbers = [item.student_no for item in records]
                emails = [item.email for item in records if item.email is not None]
                existing_student_numbers = set(
                    session.scalars(
                        select(User.student_no).where(
                            User.student_no.in_(student_numbers)
                        )
                    ).all()
                )
                existing_emails = set(
                    session.scalars(
                        select(User.email).where(User.email.in_(emails))
                    ).all()
                )
                conflicts: list[ImportConflict] = []
                for item in records:
                    if item.student_no in existing_student_numbers:
                        conflicts.append(
                            ImportConflict(
                                item.line,
                                "student_no",
                                "student number already exists",
                            )
                        )
                    if item.email is not None and item.email in existing_emails:
                        conflicts.append(
                            ImportConflict(item.line, "email", "email already exists")
                        )
                if conflicts:
                    # 任一冲突都在 staging 前返回，保证现有账号永不被覆盖。
                    return tuple(conflicts)

                courses_by_code: dict[str, Course] = {}
                classes_by_code: dict[str, TeachingClass] = {}
                class_codes = {item.class_code for item in records if item.class_code}
                if class_codes:
                    courses_by_code = {
                        course.code: course
                        for course in session.scalars(
                            select(Course)
                            .where(Course.code.in_(class_codes))
                            .order_by(Course.code)
                            .with_for_update()
                        ).all()
                    }

                for item in records:
                    user = User.create_account(
                        role="student",
                        student_no=item.student_no,
                        email=item.email,
                        display_name=item.display_name,
                        password_hash=item.password_hash,
                    )
                    session.add(user)
                    session.flush()
                    if item.class_code is None:
                        continue

                    course = courses_by_code.get(item.class_code)
                    if course is None:
                        course = Course(code=item.class_code, name=item.class_code)
                        session.add(course)
                        session.flush()
                        courses_by_code[item.class_code] = course

                    teaching_class = classes_by_code.get(item.class_code)
                    if teaching_class is None:
                        teaching_class = session.scalar(
                            select(TeachingClass)
                            .where(
                                TeachingClass.course_id == course.id,
                                TeachingClass.name == item.class_code,
                                TeachingClass.term.is_(None),
                            )
                            .with_for_update()
                        )
                        if teaching_class is None:
                            # CSV 没有教师字段，创建时由导入管理员承担临时负责人。
                            teaching_class = TeachingClass(
                                course_id=course.id,
                                teacher_id=actor_id,
                                name=item.class_code,
                                term=None,
                            )
                            session.add(teaching_class)
                            session.flush()
                        classes_by_code[item.class_code] = teaching_class

                    session.add(
                        ClassMembership(
                            teaching_class_id=teaching_class.id,
                            student_id=user.id,
                        )
                    )
                session.flush()
            return ()
        except SQLAlchemyError as exc:
            # session_scope 已完成回滚；这里转换异常以防 API 泄露数据库细节。
            raise StudentBatchImportError from exc

    @staticmethod
    def _identifier_condition(identifier: str):
        normalized = identifier.strip()
        if not normalized:
            return None
        # 是否包含 @ 决定唯一查询分支，避免后续账号数据同时命中两种标识。
        return (
            User.email == normalized.lower()
            if "@" in normalized
            else User.student_no == normalized
        )
