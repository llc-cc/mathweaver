"""MySQL 认证仓储、服务和兼容 Web 契约测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from services.auth_service import (
    AuthService,
    DuplicateEmailError,
    InvalidCredentialsError,
)
from storage.auth_repository import AuthRepository
from storage.database import session_scope
from storage.models import LoginSession, User


def test_register_login_and_logout_store_only_token_hash(database) -> None:
    service = AuthService(AuthRepository())

    registration = service.register_student(" Student@Example.com ", "secret1")
    current = service.authenticate(registration.token)

    assert registration.user.email == "student@example.com"
    assert current is not None
    assert current.role == "student"
    with session_scope() as session:
        stored = session.scalar(select(LoginSession))
        assert stored is not None
        assert stored.token_hash == hashlib.sha256(
            registration.token.encode("utf-8")
        ).hexdigest()
        assert registration.token != stored.token_hash

    service.logout(registration.token)
    assert service.authenticate(registration.token) is None


def test_duplicate_registration_has_stable_domain_error(database) -> None:
    service = AuthService(AuthRepository())
    service.register_student("student@example.com", "secret1")

    with pytest.raises(DuplicateEmailError):
        service.register_student("STUDENT@example.com", "secret2")


def test_teacher_role_requires_a_teacher_account(database) -> None:
    service = AuthService(AuthRepository())
    service.register_student("student@example.com", "secret1")

    with pytest.raises(InvalidCredentialsError):
        service.login("student@example.com", "secret1", education_role="teacher")


def test_teacher_whitelist_sync_never_downgrades_admin(database) -> None:
    repository = AuthRepository()
    original_hash = generate_password_hash("admin-secret")
    with session_scope() as session:
        session.add(
            User(
                email="admin@example.com",
                display_name="Administrator",
                role="admin",
                password_hash=original_hash,
                initial_password_pending=False,
            )
        )

    repository.sync_teacher_accounts(
        [
            {
                "email": "admin@example.com",
                "password_hash": generate_password_hash("whitelist-secret"),
            }
        ]
    )

    with session_scope() as session:
        admin = session.scalar(select(User).where(User.email == "admin@example.com"))
        assert admin is not None
        assert admin.role == "admin"
        assert admin.password_hash == original_hash


def test_web_auth_never_calls_sqlite(database, monkeypatch: pytest.MonkeyPatch) -> None:
    # 教学域 SQLite 会在 Task 5-8 继续迁移；本用例只禁止认证路由触碰它。
    monkeypatch.setenv("MATHGRAPH_DATA_DIR", str(Path(database.url.database).parent))
    import api_v2

    monkeypatch.setattr(
        api_v2,
        "_get_db",
        lambda: (_ for _ in ()).throw(AssertionError("Web auth called SQLite")),
    )
    client = api_v2.app.test_client()

    registered = client.post(
        "/api/v2/auth/register",
        json={"email": "web@example.com", "password": "secret1"},
    )
    assert registered.status_code == 201
    payload = registered.get_json()
    token = payload["token"]
    assert payload["educationRole"] == "student"
    assert payload["canTeach"] is False

    current = client.get(
        "/api/v2/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert current.status_code == 200
    assert current.get_json()["email"] == "web@example.com"

    logged_out = client.post(
        "/api/v2/auth/logout", headers={"Authorization": f"Bearer {token}"}
    )
    assert logged_out.status_code == 200
    assert client.get(
        "/api/v2/auth/me", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 401
