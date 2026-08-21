from __future__ import annotations

import io
import logging
import sys
import threading
import types
from contextlib import contextmanager

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from services.auth_service import AuthenticatedUser, AuthorizationError
from storage.database import get_engine, session_scope
from storage.models import Base, ClassMembership, Course, TeachingClass, User


class _ListResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class _RecordingImportSession:
    """记录真实仓储语句，隔离 SQL 编译检查与数据库运行环境。"""

    def __init__(self) -> None:
        self.course = Course(id=1, code="LOCK-COURSE", name="LOCK-COURSE")
        self.teaching_class = TeachingClass(
            id=2,
            course_id=1,
            teacher_id=9,
            name="LOCK-COURSE",
            term=None,
        )
        self.statements = []

    def scalars(self, statement):
        self.statements.append(statement)
        entity = statement.column_descriptions[0].get("entity")
        return _ListResult([self.course] if entity is Course else [])

    def scalar(self, statement):
        self.statements.append(statement)
        return self.teaching_class

    def add(self, value):
        if isinstance(value, User):
            value.id = 10

    def flush(self):
        return None


class _ConcurrentClassState:
    def __init__(self) -> None:
        self.course = Course(id=1, code="SHARED", name="SHARED")
        self.course_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.unlocked_lookup_barrier = threading.Barrier(2)
        self.next_id = 100
        self.classes: list[TeachingClass] = []
        self.memberships: list[ClassMembership] = []

    def allocate_id(self) -> int:
        with self.state_lock:
            self.next_id += 1
            return self.next_id

    @contextmanager
    def session_factory(self):
        session = _ConcurrentImportSession(self)
        try:
            yield session
            session.commit()
        finally:
            session.close()


class _ConcurrentImportSession:
    """用共享课程锁模拟 InnoDB：持锁事务提交后，下个事务才可查班级。"""

    def __init__(self, state: _ConcurrentClassState) -> None:
        self._state = state
        self._course_locked = False
        self._staged_classes: list[TeachingClass] = []
        self._staged_memberships: list[ClassMembership] = []

    def scalars(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is Course:
            if statement._for_update_arg is not None:
                self._state.course_lock.acquire()
                self._course_locked = True
            return _ListResult([self._state.course])
        return _ListResult([])

    def scalar(self, _statement):
        with self._state.state_lock:
            existing = self._state.classes[0] if self._state.classes else None
        if not self._course_locked:
            # 无课程行锁时，强制两个事务都在任一提交前完成相同空快照读取。
            self._state.unlocked_lookup_barrier.wait(timeout=2)
        return existing

    def add(self, value):
        if isinstance(value, User):
            value.id = self._state.allocate_id()
        elif isinstance(value, TeachingClass):
            value.id = self._state.allocate_id()
            self._staged_classes.append(value)
        elif isinstance(value, ClassMembership):
            self._staged_memberships.append(value)

    def flush(self):
        return None

    def commit(self):
        with self._state.state_lock:
            self._state.classes.extend(self._staged_classes)
            self._state.memberships.extend(self._staged_memberships)

    def close(self):
        if self._course_locked:
            self._state.course_lock.release()
            self._course_locked = False


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
        user = User.create_account(
            role=role,
            student_no=f"actor-{sequence}" if role == "student" else None,
            email=f"actor-{sequence}@example.edu",
            display_name=f"操作员{sequence}",
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


def _upload(client, token: str | None, content: bytes, filename: str = "students.csv"):
    headers = _bearer(token) if token else {}
    return client.post(
        "/api/v2/admin/users/import",
        data={"file": (io.BytesIO(content), filename)},
        headers=headers,
        content_type="multipart/form-data",
    )


def _student_count() -> int:
    with session_scope() as session:
        return session.scalar(
            select(func.count(User.id)).where(User.role == "student")
        ) or 0


def test_admin_import_accepts_utf8_bom_and_preserves_leading_zero_student_numbers(
    client, create_user
):
    admin = create_user(role="admin")
    token = _login(client, admin)

    response = _upload(
        client,
        token,
        "student_no,display_name,email\n0001,张三,ZHANG@EXAMPLE.EDU\n".encode(
            "utf-8-sig"
        ),
        "STUDENTS.CSV",
    )

    assert response.status_code == 200
    assert response.get_json()["created"] == 1
    with session_scope() as session:
        student = session.scalar(select(User).where(User.student_no == "0001"))
    assert student is not None
    assert student.email == "zhang@example.edu"


def test_generated_password_is_returned_once_but_supplied_password_is_not_echoed(
    client, create_user
):
    admin = create_user(role="admin")
    token = _login(client, admin)
    supplied_password = "Supplied-123"

    response = _upload(
        client,
        token,
        (
            "student_no,display_name,initial_password\n"
            "0002,李四,\n"
            f"0003,王五,{supplied_password}\n"
        ).encode(),
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["created"] == 2
    assert payload["errors"] == []
    assert len(payload["generated_credentials"]) == 1
    generated = payload["generated_credentials"][0]
    assert generated["student_no"] == "0002"
    assert 8 <= len(generated["initial_password"]) <= 128
    assert supplied_password not in response.get_data(as_text=True)

    with session_scope() as session:
        students = {
            item.student_no: item
            for item in session.scalars(
                select(User).where(User.student_no.in_(["0002", "0003"]))
            ).all()
        }
    assert all(item.role == "student" for item in students.values())
    assert all(item.is_active is True for item in students.values())
    assert all(item.initial_password_pending is True for item in students.values())
    assert students["0002"].password_hash != generated["initial_password"]
    assert check_password_hash(
        students["0002"].password_hash, generated["initial_password"]
    )
    assert students["0003"].password_hash != supplied_password
    assert check_password_hash(students["0003"].password_hash, supplied_password)


def test_class_codes_reuse_foundations_without_changing_existing_owner(
    client, create_user
):
    prior_owner = create_user(role="admin")
    importing_admin = create_user(role="admin")
    token = _login(client, importing_admin)
    with session_scope() as session:
        existing_course = Course(code="MATH-A", name="MATH-A")
        session.add(existing_course)
        session.flush()
        existing_class = TeachingClass(
            course_id=existing_course.id,
            teacher_id=prior_owner.id,
            name="MATH-A",
            term=None,
        )
        session.add(existing_class)
        session.flush()
        existing_class_id = existing_class.id

    response = _upload(
        client,
        token,
        (
            "student_no,display_name,class_code\n"
            "0101,甲,MATH-A\n"
            "0102,乙,MATH-B\n"
            "0103,丙,\n"
        ).encode("utf-8"),
    )

    assert response.status_code == 200
    with session_scope() as session:
        courses = {row.code: row for row in session.scalars(select(Course)).all()}
        classes = session.scalars(select(TeachingClass)).all()
        memberships = session.scalars(select(ClassMembership)).all()
        original = session.get(TeachingClass, existing_class_id)
    assert set(courses) == {"MATH-A", "MATH-B"}
    assert original is not None and original.teacher_id == prior_owner.id
    new_class = next(item for item in classes if item.name == "MATH-B")
    assert new_class.teacher_id == importing_admin.id
    assert new_class.term is None
    assert len(classes) == 2
    assert len(memberships) == 2


def test_existing_course_and_class_lookup_compile_to_mysql_for_update():
    from storage.auth_repository import AuthRepository, StudentImportRecord

    recording_session = _RecordingImportSession()

    @contextmanager
    def session_factory():
        yield recording_session

    repository = AuthRepository(session_factory)
    conflicts = repository.import_student_batch(
        [
            StudentImportRecord(
                line=2,
                student_no="lock-student",
                display_name="锁测试",
                email=None,
                class_code="LOCK-COURSE",
                password_hash="hash",
            )
        ],
        actor_id=9,
    )

    assert conflicts == ()
    course_statements = [
        statement
        for statement in recording_session.statements
        if statement.column_descriptions[0].get("entity") is Course
    ]
    assert len(course_statements) == 1
    compiled = str(
        course_statements[0].compile(
            dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "FOR UPDATE" in compiled.upper()
    class_statements = [
        statement
        for statement in recording_session.statements
        if statement.column_descriptions[0].get("entity") is TeachingClass
    ]
    assert len(class_statements) == 1
    compiled_class_query = str(
        class_statements[0].compile(
            dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "FOR UPDATE" in compiled_class_query.upper()


def test_concurrent_imports_reuse_one_provisional_class_for_existing_course():
    from storage.auth_repository import AuthRepository, StudentImportRecord

    state = _ConcurrentClassState()
    repository = AuthRepository(state.session_factory)
    outcomes: list[object] = []
    outcomes_lock = threading.Lock()

    def import_one(student_no: str, actor_id: int) -> None:
        try:
            outcome = repository.import_student_batch(
                [
                    StudentImportRecord(
                        line=2,
                        student_no=student_no,
                        display_name=student_no,
                        email=None,
                        class_code="SHARED",
                        password_hash="hash",
                    )
                ],
                actor_id,
            )
        except Exception as exc:
            outcome = exc
        with outcomes_lock:
            outcomes.append(outcome)

    threads = [
        threading.Thread(target=import_one, args=("concurrent-1", 91)),
        threading.Thread(target=import_one, args=("concurrent-2", 92)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert outcomes == [(), ()]
    assert len(state.classes) == 1
    assert len(state.memberships) == 2
    assert {
        membership.teaching_class_id for membership in state.memberships
    } == {state.classes[0].id}


@pytest.mark.parametrize(
    "email",
    [
        ".alice@example.edu",
        "alice.@example.edu",
        "alice..smith@example.edu",
    ],
)
def test_email_rejects_invalid_local_part_dot_boundaries(email):
    from services.admin_user_service import AdminUserService
    from storage.auth_repository import AuthRepository

    preview = AdminUserService(AuthRepository()).validate_csv(
        io.BytesIO(
            f"student_no,display_name,email\n0901,A,{email}\n".encode()
        )
    )

    assert [(item.line, item.field) for item in preview.errors] == [(2, "email")]


@pytest.mark.parametrize(
    "email",
    [
        "alice.smith+math@example.edu",
        "a_b-c@example-domain.edu.cn",
    ],
)
def test_email_accepts_common_dot_atom_local_parts(email):
    from services.admin_user_service import AdminUserService
    from storage.auth_repository import AuthRepository

    preview = AdminUserService(AuthRepository()).validate_csv(
        io.BytesIO(
            f"student_no,display_name,email\n0901,A,{email}\n".encode()
        )
    )

    assert preview.errors == ()
    assert preview.rows[0].email == email


def test_multiline_duplicate_reports_record_start_after_blank_physical_line():
    from services.admin_user_service import AdminUserService
    from storage.auth_repository import AuthRepository

    preview = AdminUserService(AuthRepository()).validate_csv(
        io.BytesIO(
            (
                "student_no,display_name\n"
                "\n"
                "0201,甲\n"
                '0201,"乙\n'
                '多行"\n'
            ).encode("utf-8")
        )
    )

    duplicate = next(
        item
        for item in preview.errors
        if item.field == "student_no" and "duplicate" in item.message
    )
    assert duplicate.line == 4


def test_file_duplicates_report_later_physical_lines_and_write_nothing(
    client, create_user
):
    admin = create_user(role="admin")
    token = _login(client, admin)

    response = _upload(
        client,
        token,
        (
            "student_no,display_name,email\n"
            "0201,甲,first@example.edu\n"
            "0201,乙,second@example.edu\n"
            "0203,丙,FIRST@EXAMPLE.EDU\n"
        ).encode(),
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["created"] == 0
    assert payload["generated_credentials"] == []
    assert {item["field"]: item["line"] for item in payload["errors"]} == {
        "student_no": 3,
        "email": 4,
    }
    assert _student_count() == 0


def test_existing_database_conflicts_report_source_lines_and_write_nothing(
    client, create_user
):
    admin = create_user(role="admin")
    existing = User.create_account(
        role="student",
        student_no="0301",
        email="existing@example.edu",
        display_name="已存在",
        password_hash=generate_password_hash("Existing-1"),
    )
    with session_scope() as session:
        session.add(existing)
    token = _login(client, admin)

    response = _upload(
        client,
        token,
        (
            "student_no,display_name,email\n"
            "0301,甲,new@example.edu\n"
            "0302,乙,EXISTING@EXAMPLE.EDU\n"
        ).encode(),
    )

    assert response.status_code == 400
    errors = response.get_json()["errors"]
    assert {(item["line"], item["field"]) for item in errors} == {
        (2, "student_no"),
        (3, "email"),
    }
    assert _student_count() == 1


@pytest.mark.parametrize(
    ("content", "field"),
    [
        (b"student_no,display_name,email\n0401,A,not-an-email\n", "email"),
        (b"student_no,email\n0401,a@example.edu\n", "display_name"),
        (b"student_no,display_name\n0401,   \n", "display_name"),
        (("student_no,display_name\n0401," + "A" * 256 + "\n").encode(), "display_name"),
        (b"student_no,display_name,initial_password\n0401,A,short\n", "initial_password"),
        (b"student_no,display_name\n\xff,A\n", "file"),
        (b"student_no,display_name\n\n   ,   \n", "file"),
        (b'student_no,display_name\n0401,"unterminated\n', "file"),
    ],
)
def test_invalid_csv_returns_structured_400_and_writes_nothing(
    client, create_user, content, field
):
    admin = create_user(role="admin")
    token = _login(client, admin)

    response = _upload(client, token, content)

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["created"] == 0
    assert payload["generated_credentials"] == []
    assert any(
        isinstance(item.get("line"), int)
        and item.get("field") == field
        and isinstance(item.get("message"), str)
        and item["message"]
        for item in payload["errors"]
    )
    assert _student_count() == 0


def test_database_failure_after_staged_inserts_rolls_back_entire_batch(
    client, create_user
):
    admin = create_user(role="admin")
    token = _login(client, admin)

    def fail_membership_insert(*_args, **_kwargs):
        raise SQLAlchemyError("simulated database failure")

    event.listen(ClassMembership, "before_insert", fail_membership_insert)
    try:
        response = _upload(
            client,
            token,
            b"student_no,display_name,class_code\n0501,A,ROLLBACK-CLASS\n",
        )
    finally:
        event.remove(ClassMembership, "before_insert", fail_membership_insert)

    assert response.status_code == 400
    assert response.get_json()["created"] == 0
    with session_scope() as session:
        assert session.scalar(select(func.count(User.id)).where(User.role == "student")) == 0
        assert session.scalar(select(func.count(Course.id))) == 0
        assert session.scalar(select(func.count(TeachingClass.id))) == 0
        assert session.scalar(select(func.count(ClassMembership.id))) == 0


def test_missing_file_wrong_extension_and_oversize_upload_return_400(
    client, create_user
):
    admin = create_user(role="admin")
    token = _login(client, admin)

    missing = client.post(
        "/api/v2/admin/users/import",
        data={},
        headers=_bearer(token),
        content_type="multipart/form-data",
    )
    wrong_extension = _upload(client, token, b"student_no,display_name\n1,A\n", "students.txt")
    oversize = _upload(client, token, b"x" * (5 * 1024 * 1024 + 1))

    assert missing.status_code == 400
    assert wrong_extension.status_code == 400
    assert oversize.status_code == 400
    for response in (missing, wrong_extension, oversize):
        assert response.get_json()["created"] == 0
        assert response.get_json()["generated_credentials"] == []
        assert response.get_json()["errors"]


@pytest.mark.parametrize("role", ["student", "teacher"])
def test_non_admin_roles_receive_403_for_import(client, create_user, role):
    actor = create_user(role=role)
    token = _login(client, actor)

    response = _upload(
        client, token, b"student_no,display_name\n0601,A\n"
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "forbidden"}


def test_missing_authentication_receives_401_for_import(client):
    response = _upload(
        client, None, b"student_no,display_name\n0601,A\n"
    )

    assert response.status_code == 401
    assert response.get_json() == {"error": "not authenticated"}


def test_service_rejects_non_admin_actor_without_writing():
    from services.admin_user_service import AdminUserService
    from storage.auth_repository import AuthRepository

    service = AdminUserService(AuthRepository())
    actor = AuthenticatedUser(
        id=1,
        student_no="teacher-1",
        email="teacher@example.edu",
        display_name="Teacher",
        role="teacher",
        initial_password_pending=False,
    )

    with pytest.raises(AuthorizationError):
        service.import_students(
            io.BytesIO(b"student_no,display_name\n0701,A\n"), actor
        )
    assert _student_count() == 0


def test_import_never_logs_passwords_tokens_or_hashes(client, create_user, caplog):
    admin = create_user(role="admin")
    token = _login(client, admin)
    supplied_password = "Private-Password-1"

    with caplog.at_level(logging.DEBUG):
        response = _upload(
            client,
            token,
            (
                "student_no,display_name,initial_password\n"
                "0801,甲,\n"
                f"0802,乙,{supplied_password}\n"
            ).encode(),
        )

    assert response.status_code == 200
    generated = response.get_json()["generated_credentials"][0]["initial_password"]
    with session_scope() as session:
        hashes = session.scalars(
            select(User.password_hash).where(User.student_no.in_(["0801", "0802"]))
        ).all()
    for secret in (token, generated, supplied_password, *hashes):
        assert secret not in caplog.text
