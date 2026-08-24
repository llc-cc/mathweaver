"""认证业务规则与安全会话令牌管理。"""

from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import timedelta

from werkzeug.security import check_password_hash, generate_password_hash

from storage.auth_repository import AuthRepository
from storage.models import User, utc_now


DEFAULT_SESSION_TTL_SECONDS = 604800
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


@dataclass(frozen=True)
class AuthenticatedUser:
    id: int
    student_no: str | None
    email: str | None
    display_name: str
    role: str
    initial_password_pending: bool


@dataclass(frozen=True)
class LoginResult:
    token: str
    user: AuthenticatedUser


class InvalidCredentialsError(ValueError):
    """账号状态或凭据不允许登录；API 对所有分支返回同一提示。"""


class PasswordChangeError(ValueError):
    """当前凭据或账号状态不允许完成密码修改。"""


class PasswordPolicyError(ValueError):
    """新密码不符合长度策略。"""


class AuthorizationError(PermissionError):
    """调用者角色不允许执行管理动作。"""


class UserNotFoundError(LookupError):
    """管理动作的目标账号不存在。"""


class SelfDisableError(ValueError):
    """管理员尝试停用当前正在使用的自身账号。"""


class AuthService:
    def __init__(self, repository: AuthRepository) -> None:
        self._repository = repository
        raw_ttl = os.environ.get(
            "MATHWEAVER_SESSION_TTL_SECONDS", str(DEFAULT_SESSION_TTL_SECONDS)
        )
        try:
            ttl_seconds = int(raw_ttl)
        except ValueError as exc:
            raise ValueError(
                "MATHWEAVER_SESSION_TTL_SECONDS must be a positive integer"
            ) from exc
        if ttl_seconds <= 0:
            raise ValueError(
                "MATHWEAVER_SESSION_TTL_SECONDS must be a positive integer"
            )
        self._session_ttl = timedelta(seconds=ttl_seconds)

    def login(self, identifier: str, password: str) -> LoginResult:
        with self._repository.user_transaction_by_identifier(identifier) as transaction:
            if transaction is None:
                raise InvalidCredentialsError
            user = transaction.user
            if (
                not user.is_active
                or not check_password_hash(user.password_hash, password)
            ):
                raise InvalidCredentialsError

            raw_token = secrets.token_urlsafe(32)
            now = utc_now()
            transaction.insert_session(
                self._hash_token(raw_token), now + self._session_ttl
            )
            transaction.add_audit(
                actor_id=user.id,
                action="auth.login",
                subject_type="user",
                subject_id=str(user.id),
                details={"result": "success", "reason": "credentials_valid"},
            )
            # 锁定实体不跨出事务；只复制 API 所需的不可变用户快照。
            result = LoginResult(
                token=raw_token,
                user=self._to_authenticated_user(user),
            )
        return result

    def authenticate(self, raw_token: str) -> AuthenticatedUser | None:
        if not raw_token:
            return None
        user = self._repository.find_active_session(
            self._hash_token(raw_token), utc_now()
        )
        return self._to_authenticated_user(user) if user is not None else None

    def logout(self, raw_token: str) -> None:
        if not raw_token:
            return
        self._repository.revoke_session(self._hash_token(raw_token), utc_now())

    def change_password(
        self,
        user_id: int,
        current_password: str,
        new_password: str,
        current_token: str,
    ) -> LoginResult:
        if not MIN_PASSWORD_LENGTH <= len(new_password) <= MAX_PASSWORD_LENGTH:
            raise PasswordPolicyError
        with self._repository.user_transaction_by_id(user_id) as transaction:
            if transaction is None:
                raise PasswordChangeError
            now = utc_now()
            user = transaction.user
            if (
                not user.is_active
                or not transaction.has_active_session(
                    self._hash_token(current_token), now
                )
                or not check_password_hash(user.password_hash, current_password)
            ):
                # 会话、状态和当前密码必须在同一持锁快照中判断，避免旧状态通过预检。
                raise PasswordChangeError

            raw_token = secrets.token_urlsafe(32)
            transaction.replace_password(
                password_hash=generate_password_hash(new_password),
                initial_password_pending=False,
                now=now,
                new_token_hash=self._hash_token(raw_token),
                new_token_expires_at=now + self._session_ttl,
            )
            transaction.add_audit(
                actor_id=user_id,
                action="password.change",
                subject_type="user",
                subject_id=str(user_id),
                details={"sessions_revoked": True},
            )
            result = LoginResult(
                token=raw_token,
                user=self._to_authenticated_user(user),
            )
        return result

    def reset_password(self, actor: AuthenticatedUser, user_id: int) -> str:
        self._require_admin(actor)
        with self._repository.user_transaction_by_id(user_id) as transaction:
            if transaction is None:
                raise UserNotFoundError
            temporary_password = secrets.token_urlsafe(12)
            transaction.replace_password(
                password_hash=generate_password_hash(temporary_password),
                initial_password_pending=True,
                now=utc_now(),
            )
            transaction.add_audit(
                actor_id=actor.id,
                action="admin.password_reset",
                subject_type="user",
                subject_id=str(user_id),
                details={"initial_password_pending": True},
            )
        return temporary_password

    def set_user_active(
        self, actor: AuthenticatedUser, user_id: int, is_active: bool
    ) -> AuthenticatedUser:
        self._require_admin(actor)
        if actor.id == user_id and not is_active:
            # 自停用会立刻切断唯一管理会话，因此在写入前拒绝。
            raise SelfDisableError
        with self._repository.user_transaction_by_id(user_id) as transaction:
            if transaction is None:
                raise UserNotFoundError
            transaction.set_active_status(is_active, utc_now())
            transaction.add_audit(
                actor_id=actor.id,
                action="admin.user_status",
                subject_type="user",
                subject_id=str(user_id),
                details={"is_active": is_active},
            )
            user = self._to_authenticated_user(transaction.user)
        return user

    @staticmethod
    def _require_admin(actor: AuthenticatedUser) -> None:
        if actor.role != "admin":
            raise AuthorizationError

    @staticmethod
    def _hash_token(raw_token: str) -> str:
        # 原始令牌只存在于调用栈和响应中，存储边界始终接收不可逆摘要。
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @staticmethod
    def _to_authenticated_user(user: User) -> AuthenticatedUser:
        return AuthenticatedUser(
            id=user.id,
            student_no=user.student_no,
            email=user.email,
            display_name=user.display_name,
            role=user.role,
            initial_password_pending=user.initial_password_pending,
        )
