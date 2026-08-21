from __future__ import annotations

import logging
import sys
import types

import pytest
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from storage.database import get_engine, session_scope
from storage.models import Base, LoginSession, User


@pytest.fixture
def client(tmp_path, monkeypatch):
    join_agent = types.ModuleType("JoinAgent")
    for name in ("LLMParser", "SimpleLLM", "TextDivider", "MultiProcessor"):
        setattr(join_agent, name, type(name, (), {}))
    monkeypatch.setitem(sys.modules, "JoinAgent", join_agent)
    import api_v2

    Base.metadata.create_all(get_engine())
    monkeypatch.setattr(api_v2, "_DB_PATH", tmp_path / "legacy.db")
    api_v2._init_db()
    return api_v2.app.test_client()


@pytest.fixture
def create_user():
    sequence = 0

    def create(*, role: str, password: str = "Init-1234") -> User:
        nonlocal sequence
        sequence += 1
        student_no = f"role-{sequence}" if role == "student" else None
        user = User.create_account(
            role=role,
            student_no=student_no,
            email=f"role-{sequence}@example.edu",
            display_name=f"用户{sequence}",
            password_hash=generate_password_hash(password),
        )
        with session_scope() as session:
            session.add(user)
            session.flush()
        return user

    return create


def _login(client, user: User, password: str = "Init-1234") -> str:
    response = client.post(
        "/api/v2/auth/login",
        json={"identifier": user.email, "password": password},
    )
    assert response.status_code == 200
    return response.get_json()["token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("role", ["student", "teacher"])
@pytest.mark.parametrize(
    ("method", "path_suffix", "body"),
    [
        ("post", "reset-password", None),
        ("patch", "status", {"is_active": False}),
    ],
)
def test_non_admin_roles_cannot_call_admin_routes(
    client, create_user, role, method, path_suffix, body
):
    actor = create_user(role=role)
    target = create_user(role="student")
    token = _login(client, actor)

    response = getattr(client, method)(
        f"/api/v2/admin/users/{target.id}/{path_suffix}",
        json=body,
        headers=_bearer(token),
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "forbidden"}


@pytest.mark.parametrize(
    ("method", "path_suffix", "body"),
    [
        ("post", "reset-password", None),
        ("patch", "status", {"is_active": False}),
    ],
)
def test_admin_routes_without_authentication_return_401(
    client, method, path_suffix, body
):
    response = getattr(client, method)(
        f"/api/v2/admin/users/999/{path_suffix}", json=body
    )

    assert response.status_code == 401
    assert response.get_json() == {"error": "not authenticated"}


def test_admin_reset_returns_one_time_password_without_persisting_or_logging_secrets(
    client, create_user, caplog
):
    admin = create_user(role="admin")
    target = create_user(role="student")
    admin_token = _login(client, admin)
    target_token_one = _login(client, target)
    target_token_two = _login(client, target)

    with caplog.at_level(logging.DEBUG):
        response = client.post(
            f"/api/v2/admin/users/{target.id}/reset-password",
            headers=_bearer(admin_token),
        )

    assert response.status_code == 200
    assert set(response.get_json()) == {"temporary_password"}
    temporary_password = response.get_json()["temporary_password"]
    assert 8 <= len(temporary_password) <= 128

    with session_scope() as session:
        stored_user = session.get(User, target.id)
        stored_sessions = session.scalars(
            select(LoginSession).where(LoginSession.user_id == target.id)
        ).all()
    assert stored_user is not None
    assert stored_user.initial_password_pending is True
    assert stored_user.password_hash != temporary_password
    assert check_password_hash(stored_user.password_hash, temporary_password)
    assert all(item.revoked_at is not None for item in stored_sessions)
    assert client.get("/api/v2/auth/me", headers=_bearer(target_token_one)).status_code == 401
    assert client.get("/api/v2/auth/me", headers=_bearer(target_token_two)).status_code == 401

    captured_logs = caplog.text
    for secret in (
        temporary_password,
        stored_user.password_hash,
        admin_token,
        target_token_one,
        target_token_two,
    ):
        assert secret not in captured_logs


@pytest.mark.parametrize("path_suffix", ["reset-password", "status"])
def test_admin_actions_on_unknown_user_return_404(
    client, create_user, path_suffix
):
    admin = create_user(role="admin")
    token = _login(client, admin)

    if path_suffix == "reset-password":
        response = client.post(
            "/api/v2/admin/users/999999/reset-password", headers=_bearer(token)
        )
    else:
        response = client.patch(
            "/api/v2/admin/users/999999/status",
            json={"is_active": False},
            headers=_bearer(token),
        )

    assert response.status_code == 404
    assert response.get_json() == {"error": "user not found"}


def test_admin_can_disable_user_and_revoke_all_target_sessions(client, create_user):
    admin = create_user(role="admin")
    target = create_user(role="student")
    admin_token = _login(client, admin)
    target_token_one = _login(client, target)
    target_token_two = _login(client, target)

    response = client.patch(
        f"/api/v2/admin/users/{target.id}/status",
        json={"is_active": False, "ignored": "value"},
        headers=_bearer(admin_token),
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "user": {
            "id": target.id,
            "student_no": target.student_no,
            "email": target.email,
            "display_name": target.display_name,
            "role": "student",
            "initial_password_pending": True,
        }
    }
    with session_scope() as session:
        stored_user = session.get(User, target.id)
        sessions = session.scalars(
            select(LoginSession).where(LoginSession.user_id == target.id)
        ).all()
    assert stored_user is not None and stored_user.is_active is False
    assert all(item.revoked_at is not None for item in sessions)
    assert client.get("/api/v2/auth/me", headers=_bearer(target_token_one)).status_code == 401
    assert client.get("/api/v2/auth/me", headers=_bearer(target_token_two)).status_code == 401


def test_administrator_cannot_disable_self_but_can_enable_self(client, create_user):
    admin = create_user(role="admin")
    token = _login(client, admin)

    disabled = client.patch(
        f"/api/v2/admin/users/{admin.id}/status",
        json={"is_active": False},
        headers=_bearer(token),
    )
    enabled = client.patch(
        f"/api/v2/admin/users/{admin.id}/status",
        json={"is_active": True},
        headers=_bearer(token),
    )

    assert disabled.status_code == 400
    assert disabled.get_json() == {"error": "cannot disable current administrator"}
    assert enabled.status_code == 200
    assert client.get("/api/v2/auth/me", headers=_bearer(token)).status_code == 200


@pytest.mark.parametrize(
    "payload",
    [[], {}, {"is_active": "false"}, {"is_active": 0}],
)
def test_status_change_rejects_malformed_or_non_boolean_json(
    client, create_user, payload
):
    admin = create_user(role="admin")
    target = create_user(role="student")
    token = _login(client, admin)

    response = client.patch(
        f"/api/v2/admin/users/{target.id}/status",
        json=payload,
        headers=_bearer(token),
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "is_active must be a boolean"}
