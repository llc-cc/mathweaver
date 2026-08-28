"""认证业务规则与安全会话令牌管理。"""

from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import timedelta

from werkzeug.security import check_password_hash, generate_password_hash

from storage.auth_repository import AuthRepository, UserAlreadyExistsError
from storage.models import User, utc_now


DEFAULT_SESSION_TTL_SECONDS = 604800
MIN_PASSWORD_LENGTH = 6
MAX_PASSWORD_LENGTH = 128


@dataclass(frozen=True)
class AuthenticatedUser:
    id: int
    student_no: str | None
    email: str | None
    display_name: str
    role: str
    initial_password_pending: bool

    @property
    def can_teach(self) -> bool:
        return self.role in {"teacher", "admin"}

    @property
    def education_role(self) -> str:
        return "teacher" if self.can_teach else "student"

    def __getitem__(self, key: str):
        """兼容尚未迁移的教学路由对 sqlite.Row 的字段访问方式。"""
        aliases = {
            "can_teach": self.can_teach,
            "education_role": self.education_role,
        }
        if key in aliases:
            return aliases[key]
        if key in {
            "id",
            "student_no",
            "email",
            "display_name",
            "role",
            "initial_password_pending",
        }:
            return getattr(self, key)
        raise KeyError(key)


@dataclass(frozen=True)
class LoginResult:
    token: str
    user: AuthenticatedUser
    education_role: str


class InvalidCredentialsError(ValueError):
    """账号状态、角色或凭据不允许登录。"""


class PasswordPolicyError(ValueError):
    """公开注册密码不符合兼容长度策略。"""


class DuplicateEmailError(ValueError):
    """公开注册邮箱已经存在。"""


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
            raise ValueError("MATHWEAVER_SESSION_TTL_SECONDS must be a positive integer")
        self._session_ttl = timedelta(seconds=ttl_seconds)

    def register_student(self, email: str, password: str) -> LoginResult:
        normalized_email = email.strip().lower()
        self._validate_password(password)
        raw_token = secrets.token_urlsafe(32)
        now = utc_now()
        try:
            user = self._repository.create_email_student_with_session(
                email=normalized_email,
                display_name=normalized_email.split("@", 1)[0],
                password_hash=generate_password_hash(password),
                token_hash=self._hash_token(raw_token),
                expires_at=now + self._session_ttl,
            )
        except UserAlreadyExistsError as exc:
            raise DuplicateEmailError from exc
        return LoginResult(raw_token, self._to_authenticated_user(user), "student")

    def login(
        self, identifier: str, password: str, education_role: str = "student"
    ) -> LoginResult:
        if education_role not in {"student", "teacher"}:
            raise InvalidCredentialsError
        with self._repository.user_transaction_by_identifier(identifier) as transaction:
            if transaction is None:
                raise InvalidCredentialsError
            user = transaction.user
            if (
                not user.is_active
                or not check_password_hash(user.password_hash, password)
                or education_role == "teacher"
                and user.role not in {"teacher", "admin"}
            ):
                raise InvalidCredentialsError
            raw_token = secrets.token_urlsafe(32)
            now = utc_now()
            transaction.insert_session(
                self._hash_token(raw_token), now + self._session_ttl
            )
            result = LoginResult(
                raw_token,
                self._to_authenticated_user(user),
                education_role,
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
        if raw_token:
            self._repository.revoke_session(self._hash_token(raw_token), utc_now())

    @staticmethod
    def _validate_password(password: str) -> None:
        if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            raise PasswordPolicyError

    @staticmethod
    def _hash_token(raw_token: str) -> str:
        # 原始 Bearer 令牌只返回客户端，持久化边界始终接收不可逆摘要。
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
