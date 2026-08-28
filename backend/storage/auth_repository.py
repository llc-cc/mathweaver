"""Web 认证所需的用户与会话持久化边界。"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from storage.database import session_scope
from storage.models import LoginSession, User


SessionFactory = Callable[[], AbstractContextManager[Session]]


class UserAlreadyExistsError(RuntimeError):
    """数据库唯一键拒绝创建账号；上层不得解析驱动错误字符串。"""


class AuthUserTransaction:
    """封装持锁用户行上的认证写入，实例仅在当前事务内有效。"""

    def __init__(self, session: Session, user: User) -> None:
        self._session = session
        self.user = user

    def insert_session(self, token_hash: str, expires_at: datetime) -> None:
        self._session.add(
            LoginSession(
                user_id=self.user.id,
                token_hash=token_hash,
                expires_at=expires_at,
            )
        )


class AuthRepository:
    """封装账号与会话查询，不感知 Flask 请求和响应。"""

    def __init__(self, session_factory: SessionFactory = session_scope) -> None:
        self._session_factory = session_factory

    def create_email_student_with_session(
        self,
        *,
        email: str,
        display_name: str,
        password_hash: str,
        token_hash: str,
        expires_at: datetime,
    ) -> User:
        """在同一事务创建公开学生账号和首个会话，任一步失败都整体回滚。"""
        try:
            with self._session_factory() as session:
                # 公开注册沿用现有“仅邮箱”契约；CSV 导入仍由模型工厂强制学号。
                user = User(
                    email=email,
                    display_name=display_name,
                    role="student",
                    password_hash=password_hash,
                    initial_password_pending=False,
                )
                session.add(user)
                session.flush()
                session.add(
                    LoginSession(
                        user_id=user.id,
                        token_hash=token_hash,
                        expires_at=expires_at,
                    )
                )
                session.flush()
                return user
        except IntegrityError as exc:
            # 邮箱和令牌摘要均由唯一键兜底，HTTP 层只接收稳定领域异常。
            raise UserAlreadyExistsError from exc

    @contextmanager
    def user_transaction_by_identifier(
        self, identifier: str
    ) -> Iterator[AuthUserTransaction | None]:
        condition = self._identifier_condition(identifier)
        if condition is None:
            yield None
            return
        with self._session_factory() as session:
            user = session.scalar(select(User).where(condition).with_for_update())
            yield AuthUserTransaction(session, user) if user is not None else None

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

    def sync_teacher_accounts(self, accounts: Sequence[dict[str, str]]) -> None:
        """幂等同步白名单教师；管理员账号的权限和密码均保持不变。"""
        with self._session_factory() as session:
            for account in accounts:
                email = str(account.get("email") or "").strip().lower()
                password_hash = str(account.get("password_hash") or "")
                if not email or not password_hash:
                    raise ValueError("teacher account email and password hash are required")
                user = session.scalar(
                    select(User).where(User.email == email).with_for_update()
                )
                if user is None:
                    session.add(
                        User(
                            email=email,
                            display_name=email.split("@", 1)[0],
                            role="teacher",
                            password_hash=password_hash,
                            initial_password_pending=False,
                        )
                    )
                    continue
                if user.role == "admin":
                    # 白名单是教师供应机制，不得反向修改已有最高权限账号。
                    continue
                user.role = "teacher"
                user.password_hash = password_hash
                user.initial_password_pending = False
                user.is_active = True

    @staticmethod
    def _identifier_condition(identifier: str):
        normalized = identifier.strip()
        if not normalized:
            return None
        return (
            User.email == normalized.lower()
            if "@" in normalized
            else User.student_no == normalized
        )
