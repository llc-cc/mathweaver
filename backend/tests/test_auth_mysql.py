from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import types
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import mysql
from werkzeug.security import check_password_hash, generate_password_hash

from storage.database import get_engine, session_scope
from storage.models import Base, LoginSession, User


INVALID_CREDENTIALS = "学号、邮箱或密码错误"
API_IMPORT_CODE = """
import sys, types
join_agent = types.ModuleType('JoinAgent')
for name in ('LLMParser', 'SimpleLLM', 'TextDivider', 'MultiProcessor'):
    setattr(join_agent, name, type(name, (), {}))
sys.modules['JoinAgent'] = join_agent
import api_v2
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 认证路由不调用 LLM；用轻量占位隔离未安装的可选 dashscope 依赖。
    join_agent = types.ModuleType("JoinAgent")
    for name in ("LLMParser", "SimpleLLM", "TextDivider", "MultiProcessor"):
        setattr(join_agent, name, type(name, (), {}))
    monkeypatch.setitem(sys.modules, "JoinAgent", join_agent)
    import api_v2

    # 首次导入 API 会按显式 URL 重建引擎，随后补建本测试所需表结构。
    Base.metadata.create_all(get_engine())
    # 历史等旧路由仍使用 SQLite；隔离该库，避免认证测试污染开发数据。
    monkeypatch.setattr(api_v2, "_DB_PATH", tmp_path / "legacy.db")
    api_v2._init_db()
    return api_v2.app.test_client()


@pytest.fixture
def create_user():
    def create(
        *,
        student_no: str | None = "0020260001",
        email: str | None = "student@example.edu",
        display_name: str = "张三",
        password: str = "Init-1234",
        is_active: bool = True,
        role: str = "student",
        initial_password_pending: bool = True,
    ) -> User:
        user = User.create_account(
            role=role,
            student_no=student_no,
            email=email,
            display_name=display_name,
            password_hash=generate_password_hash(password),
        )
        user.is_active = is_active
        user.initial_password_pending = initial_password_pending
        with session_scope() as session:
            session.add(user)
            session.flush()
        return user

    return create


def _login(client, identifier="0020260001", password="Init-1234"):
    return client.post(
        "/api/v2/auth/login",
        json={"identifier": identifier, "password": password},
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _user_snapshot(user: User):
    # SQLAlchemy 实体不能浅拷贝；独立值快照才可模拟旧事务已读取的状态。
    return types.SimpleNamespace(
        id=user.id,
        student_no=user.student_no,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        password_hash=user.password_hash,
        initial_password_pending=user.initial_password_pending,
        is_active=user.is_active,
    )


class _RecordingSession:
    def __init__(self, user: User) -> None:
        self.user = user
        self.statements = []

    def scalar(self, statement):
        self.statements.append(statement)
        return self.user


class _LoginResetTransaction:
    def __init__(self, repository: "_LoginResetRaceRepository") -> None:
        self._repository = repository
        self.user = repository.user

    def insert_session(self, token_hash, expires_at):
        self._repository.sessions[token_hash] = False
        self._repository.login_at_write.set()
        self._repository.allow_login_commit.wait(timeout=2)

    def replace_password(
        self,
        password_hash,
        initial_password_pending,
        now,
        new_token_hash=None,
        new_token_expires_at=None,
    ):
        self.user.password_hash = password_hash
        self.user.initial_password_pending = initial_password_pending
        self._repository.sessions = {
            token_hash: True for token_hash in self._repository.sessions
        }
        if new_token_hash is not None:
            self._repository.sessions[new_token_hash] = False
        self._repository.reset_committed.set()


class _LoginResetRaceRepository:
    """同时支持旧接口和期望的新事务接口，用同一测试暴露旧竞态。"""

    def __init__(self, user: User) -> None:
        self.user = user
        self.sessions: dict[str, bool] = {}
        self.user_lock = threading.Lock()
        self.login_at_write = threading.Event()
        self.allow_login_commit = threading.Event()
        self.reset_committed = threading.Event()

    def find_user_by_identifier(self, _identifier):
        snapshot = _user_snapshot(self.user)
        self.login_at_write.set()
        self.reset_committed.wait(timeout=2)
        return snapshot

    def find_user_by_id(self, _user_id):
        return self.user

    def insert_session(self, _user_id, token_hash, _expires_at):
        self.sessions[token_hash] = False

    def update_password_and_sessions(
        self,
        user_id,
        password_hash,
        initial_password_pending,
        now,
        new_token_hash=None,
        new_token_expires_at=None,
    ):
        transaction = _LoginResetTransaction(self)
        transaction.replace_password(
            password_hash,
            initial_password_pending,
            now,
            new_token_hash,
            new_token_expires_at,
        )
        return self.user

    @contextmanager
    def user_transaction_by_identifier(self, _identifier):
        with self.user_lock:
            yield _LoginResetTransaction(self)

    @contextmanager
    def user_transaction_by_id(self, _user_id):
        with self.user_lock:
            yield _LoginResetTransaction(self)


class _ConcurrentChangeTransaction:
    def __init__(self, repository: "_ConcurrentChangeRepository") -> None:
        self._repository = repository
        self.user = repository.user

    def has_active_session(self, token_hash, _now):
        return self._repository.sessions.get(token_hash) is False

    def replace_password(
        self,
        password_hash,
        initial_password_pending,
        now,
        new_token_hash=None,
        new_token_expires_at=None,
    ):
        self.user.password_hash = password_hash
        self.user.initial_password_pending = initial_password_pending
        self._repository.sessions = {
            token_hash: True for token_hash in self._repository.sessions
        }
        if new_token_hash is not None:
            self._repository.sessions[new_token_hash] = False


class _ConcurrentChangeRepository:
    """旧接口用屏障制造双预检，新接口用事务锁模拟数据库行锁。"""

    def __init__(self, user: User, raw_tokens: tuple[str, str]) -> None:
        self.user = user
        self.sessions = {
            hashlib.sha256(token.encode("utf-8")).hexdigest(): False
            for token in raw_tokens
        }
        self.precheck_barrier = threading.Barrier(2)
        self.user_lock = threading.Lock()
        self.state_lock = threading.Lock()

    def find_active_session(self, token_hash, _now):
        with self.state_lock:
            return (
                _user_snapshot(self.user)
                if self.sessions.get(token_hash) is False
                else None
            )

    def find_user_by_id(self, _user_id):
        snapshot = _user_snapshot(self.user)
        self.precheck_barrier.wait(timeout=2)
        return snapshot

    def update_password_and_sessions(
        self,
        user_id,
        password_hash,
        initial_password_pending,
        now,
        new_token_hash=None,
        new_token_expires_at=None,
    ):
        with self.state_lock:
            transaction = _ConcurrentChangeTransaction(self)
            transaction.replace_password(
                password_hash,
                initial_password_pending,
                now,
                new_token_hash,
                new_token_expires_at,
            )
            return _user_snapshot(self.user)

    @contextmanager
    def user_transaction_by_id(self, _user_id):
        with self.user_lock:
            yield _ConcurrentChangeTransaction(self)


@pytest.mark.parametrize(
    ("transaction_name", "argument"),
    [
        ("user_transaction_by_id", 7),
        ("user_transaction_by_identifier", "student@example.edu"),
    ],
)
def test_user_auth_transactions_compile_mysql_for_update(
    transaction_name, argument
):
    from storage.auth_repository import AuthRepository

    user = User.create_account(
        role="student",
        student_no="lock-7",
        email="student@example.edu",
        display_name="锁测试",
        password_hash=generate_password_hash("Init-1234"),
    )
    user.id = 7
    recording_session = _RecordingSession(user)

    @contextmanager
    def session_factory():
        yield recording_session

    repository = AuthRepository(session_factory)

    with getattr(repository, transaction_name)(argument) as transaction:
        assert transaction is not None and transaction.user.id == 7

    assert len(recording_session.statements) == 1
    compiled = str(
        recording_session.statements[0].compile(
            dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "FOR UPDATE" in compiled.upper()


def test_login_and_admin_reset_serialize_on_the_same_user_transaction():
    from services.auth_service import AuthenticatedUser, AuthService

    target = User.create_account(
        role="student",
        student_no="race-login",
        email="race-login@example.edu",
        display_name="竞态用户",
        password_hash=generate_password_hash("Init-1234"),
    )
    target.id = 11
    target.is_active = True
    target.initial_password_pending = True
    repository = _LoginResetRaceRepository(target)
    service = AuthService(repository)
    actor = AuthenticatedUser(
        id=99,
        student_no=None,
        email="admin@example.edu",
        display_name="管理员",
        role="admin",
        initial_password_pending=False,
    )
    outcomes: dict[str, object] = {}

    def run_login() -> None:
        try:
            outcomes["login"] = service.login("race-login", "Init-1234")
        except Exception as exc:
            outcomes["login_error"] = exc

    login_thread = threading.Thread(target=run_login)
    login_thread.start()
    assert repository.login_at_write.wait(timeout=2)
    reset_thread = threading.Thread(
        target=lambda: outcomes.setdefault(
            "reset", service.reset_password(actor, target.id)
        )
    )
    reset_thread.start()
    repository.allow_login_commit.set()
    login_thread.join(timeout=2)
    reset_thread.join(timeout=2)

    assert not login_thread.is_alive() and not reset_thread.is_alive()
    assert set(outcomes) == {"login", "reset"}, {
        key: type(value).__name__ for key, value in outcomes.items()
    }
    assert repository.reset_committed.is_set()
    assert repository.sessions
    assert all(repository.sessions.values())


def test_concurrent_password_changes_cannot_both_commit_from_old_state():
    from services.auth_service import AuthService, PasswordChangeError

    raw_tokens = ("current-token-one", "current-token-two")
    user = User.create_account(
        role="student",
        student_no="race-change",
        email="race-change@example.edu",
        display_name="并发改密用户",
        password_hash=generate_password_hash("Init-1234"),
    )
    user.id = 12
    user.is_active = True
    user.initial_password_pending = True
    repository = _ConcurrentChangeRepository(user, raw_tokens)
    service = AuthService(repository)
    outcomes: list[object] = []
    outcomes_lock = threading.Lock()

    def change(raw_token: str, new_password: str) -> None:
        try:
            outcome = service.change_password(
                user.id, "Init-1234", new_password, raw_token
            )
        except Exception as exc:
            outcome = exc
        with outcomes_lock:
            outcomes.append(outcome)

    threads = [
        threading.Thread(target=change, args=(raw_tokens[0], "Changed-0001")),
        threading.Thread(target=change, args=(raw_tokens[1], "Changed-0002")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    successful = [item for item in outcomes if not isinstance(item, Exception)]
    rejected = [item for item in outcomes if isinstance(item, PasswordChangeError)]
    assert len(successful) == 1, [type(item).__name__ for item in outcomes]
    assert len(rejected) == 1
    assert service.authenticate(successful[0].token) is not None


def test_student_number_login_preserves_leading_zeroes_and_returns_exact_shape(
    client, create_user
):
    user = create_user()

    response = _login(client)

    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload["token"], str) and payload["token"]
    assert payload["user"] == {
        "id": user.id,
        "student_no": "0020260001",
        "email": "student@example.edu",
        "display_name": "张三",
        "role": "student",
        "initial_password_pending": True,
    }


def test_email_login_is_case_insensitive_and_trims_whitespace(client, create_user):
    create_user(email="mixed.case@example.edu")

    response = _login(client, "  Mixed.Case@Example.EDU  ")

    assert response.status_code == 200
    assert response.get_json()["user"]["email"] == "mixed.case@example.edu"


def test_student_number_identifier_is_case_sensitive(create_user):
    from storage.auth_repository import AuthRepository

    user = create_user(student_no="Ab01")
    repository = AuthRepository()

    exact = repository.find_user_by_identifier("Ab01")

    assert exact is not None and exact.id == user.id
    assert repository.find_user_by_identifier("ab01") is None


def test_unknown_account_and_wrong_password_share_the_same_401(client, create_user):
    create_user()

    unknown = _login(client, "0099999999")
    wrong_password = _login(client, password="not-the-password")

    assert unknown.status_code == wrong_password.status_code == 401
    assert unknown.get_json() == wrong_password.get_json() == {"error": INVALID_CREDENTIALS}


def test_inactive_user_uses_the_same_401_message(client, create_user):
    create_user(is_active=False)

    response = _login(client)

    assert response.status_code == 401
    assert response.get_json() == {"error": INVALID_CREDENTIALS}


def test_public_registration_is_disabled_in_web_mode(client):
    response = client.post(
        "/api/v2/auth/register",
        json={"email": "public@example.edu", "password": "password123"},
    )

    assert response.status_code in {404, 405}


def test_login_rejects_non_object_json(client):
    response = client.post("/api/v2/auth/login", json=[])

    assert response.status_code == 400
    assert response.get_json() == {"error": "JSON object required"}


@pytest.mark.parametrize(
    "payload",
    [
        {"identifier": 20260001, "password": "Init-1234"},
        {"identifier": "0020260001", "password": 1234},
    ],
)
def test_login_rejects_non_string_fields(client, payload):
    response = client.post("/api/v2/auth/login", json=payload)

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "identifier and password must be strings"
    }


def test_login_persists_only_the_sha256_token_hash(client, create_user):
    create_user()

    raw_token = _login(client).get_json()["token"]

    with session_scope() as session:
        stored = session.scalar(select(LoginSession))
    assert stored is not None
    assert stored.token_hash == hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    assert raw_token != stored.token_hash


def test_me_accepts_valid_token_and_rejects_missing_and_invalid_tokens(client, create_user):
    user = create_user()
    token = _login(client).get_json()["token"]

    valid = client.get("/api/v2/auth/me", headers=_bearer(token))
    missing = client.get("/api/v2/auth/me")
    invalid = client.get("/api/v2/auth/me", headers=_bearer("invalid-token"))

    assert valid.status_code == 200
    assert valid.get_json()["user"]["id"] == user.id
    assert missing.status_code == invalid.status_code == 401


def test_me_rejects_expired_and_revoked_tokens(client, create_user):
    from storage.auth_repository import AuthRepository

    user = create_user()
    repository = AuthRepository()
    now = datetime.now(timezone.utc)
    expired_raw = "expired-raw-token"
    revoked_raw = "revoked-raw-token"
    expired_hash = hashlib.sha256(expired_raw.encode("utf-8")).hexdigest()
    revoked_hash = hashlib.sha256(revoked_raw.encode("utf-8")).hexdigest()
    repository.insert_session(user.id, expired_hash, now - timedelta(seconds=1))
    repository.insert_session(user.id, revoked_hash, now + timedelta(hours=1))
    repository.revoke_session(revoked_hash, now)

    expired = client.get("/api/v2/auth/me", headers=_bearer(expired_raw))
    revoked = client.get("/api/v2/auth/me", headers=_bearer(revoked_raw))

    assert expired.status_code == revoked.status_code == 401


def test_logout_revokes_current_token_and_is_idempotent(client, create_user):
    create_user()
    token = _login(client).get_json()["token"]

    first = client.post("/api/v2/auth/logout", headers=_bearer(token))
    second = client.post("/api/v2/auth/logout", headers=_bearer(token))
    me = client.get("/api/v2/auth/me", headers=_bearer(token))

    assert first.status_code == second.status_code == 200
    assert first.get_json() == second.get_json() == {"ok": True}
    assert me.status_code == 401
    with session_scope() as session:
        stored = session.scalar(select(LoginSession))
    assert stored is not None and stored.revoked_at is not None


def test_authentication_updates_last_used_at(client, create_user):
    create_user()
    token = _login(client).get_json()["token"]

    client.get("/api/v2/auth/me", headers=_bearer(token))

    with session_scope() as session:
        stored = session.scalar(select(LoginSession))
    assert stored is not None and stored.last_used_at is not None


def test_initial_password_prompt_does_not_block_history(client, create_user):
    create_user(initial_password_pending=True)
    token = _login(client).get_json()["token"]

    response = client.get("/api/v2/history", headers=_bearer(token))

    assert response.status_code == 200
    assert response.get_json() == []


def test_change_password_requires_authentication(client):
    response = client.post(
        "/api/v2/auth/change-password",
        json={"current_password": "Init-1234", "new_password": "Changed-5678"},
    )

    assert response.status_code == 401
    assert response.get_json() == {"error": "not authenticated"}


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        ([], "JSON object required"),
        (
            {"current_password": 1234, "new_password": "Changed-5678"},
            "current_password and new_password must be strings",
        ),
        (
            {"current_password": "Init-1234", "new_password": None},
            "current_password and new_password must be strings",
        ),
    ],
)
def test_change_password_rejects_malformed_body(
    client, create_user, payload, expected_error
):
    create_user()
    token = _login(client).get_json()["token"]

    response = client.post(
        "/api/v2/auth/change-password", json=payload, headers=_bearer(token)
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": expected_error}


def test_change_password_rejects_wrong_current_password_without_detail_leakage(
    client, create_user
):
    create_user()
    token = _login(client).get_json()["token"]

    response = client.post(
        "/api/v2/auth/change-password",
        json={"current_password": "wrong-password", "new_password": "Changed-5678"},
        headers=_bearer(token),
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "密码修改失败"}


@pytest.mark.parametrize("new_password", ["1234567", "x" * 129])
def test_change_password_rejects_lengths_outside_policy(
    client, create_user, new_password
):
    create_user()
    token = _login(client).get_json()["token"]

    response = client.post(
        "/api/v2/auth/change-password",
        json={"current_password": "Init-1234", "new_password": new_password},
        headers=_bearer(token),
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "密码长度必须为 8 至 128 位"}


def test_change_password_replaces_all_sessions_and_clears_initial_prompt(
    client, create_user
):
    user = create_user(initial_password_pending=True)
    current_token = _login(client).get_json()["token"]
    other_token = _login(client).get_json()["token"]

    response = client.post(
        "/api/v2/auth/change-password",
        json={"current_password": "Init-1234", "new_password": "Changed-5678"},
        headers=_bearer(current_token),
    )

    assert response.status_code == 200
    payload = response.get_json()
    fresh_token = payload["token"]
    assert fresh_token not in {current_token, other_token}
    assert payload["user"] == {
        "id": user.id,
        "student_no": "0020260001",
        "email": "student@example.edu",
        "display_name": "张三",
        "role": "student",
        "initial_password_pending": False,
    }
    assert client.get("/api/v2/auth/me", headers=_bearer(current_token)).status_code == 401
    assert client.get("/api/v2/auth/me", headers=_bearer(other_token)).status_code == 401
    assert client.get("/api/v2/auth/me", headers=_bearer(fresh_token)).status_code == 200

    with session_scope() as session:
        stored_user = session.get(User, user.id)
        stored_sessions = session.scalars(
            select(LoginSession).where(LoginSession.user_id == user.id)
        ).all()
    assert stored_user is not None
    assert stored_user.initial_password_pending is False
    assert stored_user.password_hash != "Changed-5678"
    assert check_password_hash(stored_user.password_hash, "Changed-5678")
    assert sum(item.revoked_at is None for item in stored_sessions) == 1
    assert _login(client, password="Init-1234").status_code == 401
    assert _login(client, password="Changed-5678").status_code == 200


def test_revoke_all_user_sessions_can_preserve_one_session(create_user):
    from storage.auth_repository import AuthRepository

    user = create_user()
    repository = AuthRepository()
    now = datetime.now(timezone.utc)
    repository.insert_session(user.id, "keep-hash", now + timedelta(hours=1))
    repository.insert_session(user.id, "revoke-hash", now + timedelta(hours=1))

    revoked_count = repository.revoke_all_user_sessions(
        user.id, now, except_hash="keep-hash"
    )

    with session_scope() as session:
        sessions = {
            item.token_hash: item for item in session.scalars(select(LoginSession)).all()
        }
    assert revoked_count == 1
    assert sessions["keep-hash"].revoked_at is None
    assert sessions["revoke-hash"].revoked_at is not None


def test_session_expiration_uses_configured_ttl(monkeypatch, create_user):
    from services.auth_service import AuthService
    from storage.auth_repository import AuthRepository

    create_user()
    monkeypatch.setenv("MATHWEAVER_SESSION_TTL_SECONDS", "90")
    service = AuthService(AuthRepository())

    service.login("0020260001", "Init-1234")

    with session_scope() as session:
        stored = session.scalar(select(LoginSession))
    assert stored is not None
    assert 89 <= (stored.expires_at - stored.created_at).total_seconds() <= 91


def test_session_expiration_defaults_to_seven_days(create_user):
    from services.auth_service import AuthService
    from storage.auth_repository import AuthRepository

    create_user()

    AuthService(AuthRepository()).login("0020260001", "Init-1234")

    with session_scope() as session:
        stored = session.scalar(select(LoginSession))
    assert stored is not None
    elapsed = (stored.expires_at - stored.created_at).total_seconds()
    assert 604799 <= elapsed <= 604801


@pytest.mark.parametrize("configured_ttl", ["not-an-integer", "0", "-1"])
def test_invalid_ttl_configuration_is_rejected(monkeypatch, configured_ttl):
    from services.auth_service import AuthService
    from storage.auth_repository import AuthRepository

    monkeypatch.setenv("MATHWEAVER_SESSION_TTL_SECONDS", configured_ttl)

    with pytest.raises(ValueError, match="MATHWEAVER_SESSION_TTL_SECONDS"):
        AuthService(AuthRepository())


def test_web_startup_requires_explicit_database_url(tmp_path):
    environment = os.environ.copy()
    environment.pop("MATHWEAVER_DATABASE_URL", None)
    environment.pop("AI4MATH_DESKTOP", None)
    environment["MATHGRAPH_DATA_DIR"] = str(tmp_path)
    backend_dir = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", API_IMPORT_CODE],
        cwd=backend_dir,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode != 0
    assert "MATHWEAVER_DATABASE_URL" in result.stderr


def test_desktop_startup_without_database_url_keeps_legacy_path(tmp_path):
    environment = os.environ.copy()
    environment.pop("MATHWEAVER_DATABASE_URL", None)
    environment["AI4MATH_DESKTOP"] = "1"
    environment["MATHGRAPH_DATA_DIR"] = str(tmp_path)

    result = subprocess.run(
        [sys.executable, "-c", API_IMPORT_CODE],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
