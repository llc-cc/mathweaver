"""Web 学习数据集中持久化与资源归属回归测试。"""

from __future__ import annotations

import io
import base64
import json
import os
import subprocess
import sys
import types
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import event, select
from werkzeug.security import generate_password_hash


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("MATHWEAVER_DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault(
    "MATHWEAVER_CREDENTIAL_KEYS_JSON",
    json.dumps({"test": base64.b64encode(b"t" * 32).decode("ascii")}),
)
os.environ.setdefault("MATHWEAVER_CREDENTIAL_ACTIVE_KEY_ID", "test")
join_agent = types.ModuleType("JoinAgent")
for name in ("LLMParser", "SimpleLLM", "TextDivider", "MultiProcessor"):
    setattr(join_agent, name, type(name, (), {}))
sys.modules.setdefault("JoinAgent", join_agent)

import api_v2
from storage import database as storage_database
from storage.database import configure_database, get_engine, session_scope
from storage.models import Base, History, StorageOutbox, User, UserSettings
from storage.object_storage import ObjectStorageError, StoredVersion


@pytest.fixture(autouse=True)
def isolated_web_storage(tmp_path, monkeypatch):
    """使用文件型 SQLAlchemy 库验证跨连接持久化，并隔离全局任务状态。"""
    previous_engine = storage_database._engine
    previous_factory = storage_database._session_factory
    previous_jobs = dict(api_v2._jobs)
    previous_runtimes = dict(api_v2._job_runtimes)
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'web.db').as_posix()}"
    monkeypatch.setenv("MATHWEAVER_DATABASE_URL", database_url)
    monkeypatch.setenv("MATHGRAPH_DATA_DIR", str(tmp_path / "data"))
    configure_database(database_url)
    Base.metadata.create_all(get_engine())
    api_v2._jobs.clear()
    api_v2._job_runtimes.clear()
    monkeypatch.setattr(api_v2, "_DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(api_v2, "_DB_PATH", tmp_path / "legacy.db")
    monkeypatch.setattr(api_v2, "_SOURCE_PDF_ROOT", tmp_path / "data" / "uploads" / "source_pdfs")
    api_v2._init_db()
    yield database_url
    api_v2._jobs.clear()
    api_v2._jobs.update(previous_jobs)
    api_v2._job_runtimes.clear()
    api_v2._job_runtimes.update(previous_runtimes)
    get_engine().dispose()
    storage_database._engine = previous_engine
    storage_database._session_factory = previous_factory


@pytest.fixture
def users():
    created = []
    for student_no in ("owner-001", "other-002"):
        user = User.create_account(
            role="student",
            student_no=student_no,
            email=f"{student_no}@example.edu",
            display_name=student_no,
            password_hash=generate_password_hash("Init-1234"),
        )
        with session_scope() as session:
            session.add(user)
            session.flush()
            created.append((user.id, student_no))
    return created


@pytest.fixture
def authenticated_clients(users):
    client = api_v2.app.test_client()
    tokens = []
    for _user_id, student_no in users:
        response = client.post(
            "/api/v2/auth/login",
            json={"identifier": student_no, "password": "Init-1234"},
        )
        assert response.status_code == 200
        tokens.append(response.get_json()["token"])
    return client, users, tokens


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class FakeObjectStorage:
    def __init__(self, *, fail_sync: bool = False) -> None:
        self.fail_sync = fail_sync
        self.synced: list[tuple[int, str]] = []
        self.restored: list[tuple[int, str]] = []
        self.deleted: list[tuple[int, str]] = []

    @staticmethod
    def task_prefix(user_id: int, job_id: str) -> str:
        return f"mathweaver/users/{user_id}/jobs/{job_id}/"

    def version_prefix(self, user_id: int, job_id: str, version_id: str) -> str:
        return f"{self.task_prefix(user_id, job_id)}versions/{version_id}/"

    def sync_job(
        self,
        user_id: int,
        job_id: str,
        artifact_root: Path,
        source_pdf_root: Path,
    ) -> str:
        del artifact_root, source_pdf_root
        if self.fail_sync:
            raise ObjectStorageError("OSS object upload failed")
        self.synced.append((user_id, job_id))
        return self.task_prefix(user_id, job_id)

    def restore_job(
        self,
        user_id: int,
        job_id: str,
        artifact_root: Path,
        source_pdf_root: Path,
    ) -> bool:
        self.restored.append((user_id, job_id))
        artifact_root.mkdir(parents=True, exist_ok=True)
        (artifact_root / "nodes.json").write_text('[{"id": 1, "title": "定理"}]', encoding="utf-8")
        (artifact_root / "edges.json").write_text("[]", encoding="utf-8")
        cache = artifact_root / "_stage_cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "manifest.json").write_text('{"schema_version": 1}', encoding="utf-8")
        source_pdf_root.mkdir(parents=True, exist_ok=True)
        (source_pdf_root / "source.pdf").write_bytes(b"%PDF-1.4 restored\n%%EOF")
        (source_pdf_root / "source.tex").write_text("\\begin{document}x\\end{document}", encoding="utf-8")
        (source_pdf_root / "compile.log").write_text("restored log", encoding="utf-8")
        return True

    def restore_version(
        self,
        user_id: int,
        job_id: str,
        version_id: str,
        expected_checksum: str,
        artifact_root: Path,
        source_pdf_root: Path,
    ) -> bool:
        del version_id, expected_checksum
        return self.restore_job(user_id, job_id, artifact_root, source_pdf_root)

    def upload_version(
        self,
        user_id: int,
        job_id: str,
        artifact_root: Path,
        source_pdf_root: Path,
    ) -> StoredVersion:
        del artifact_root, source_pdf_root
        if self.fail_sync:
            raise ObjectStorageError("OSS object upload failed")
        self.synced.append((user_id, job_id))
        version_id = "f" * 32
        prefix = f"{self.task_prefix(user_id, job_id)}versions/{version_id}/"
        return StoredVersion(version_id, prefix, "9" * 64, 2, 10)

    def delete_job(self, user_id: int, job_id: str) -> None:
        self.deleted.append((user_id, job_id))


def _snapshot(
    job_id: str,
    status: str = "running",
    *,
    object_storage_prefix: str | None = None,
    source_pdf: dict | None = None,
):
    try:
        from storage.learning_repository import JobSnapshot
    except ImportError as exc:
        pytest.fail(f"LearningRepository 尚未实现: {exc}")
    return JobSnapshot(
        job_id=job_id,
        filename="lesson.md",
        status=status,
        nodes=[{"id": 1, "title": "定理"}],
        edges=[{"from": 1, "to": 1}],
        source_markdown="# Lesson",
        latex_macros={"RR": "\\mathbb{R}"},
        source_pdf=source_pdf,
        stage="build_graph" if status != "done" else None,
        stage_label="构建图谱" if status != "done" else None,
        stage_index=2,
        total_stages=4,
        stages_done=["parse", "segment"],
        source_format="markdown",
        experimental_logic_ir=False,
        object_storage_prefix=object_storage_prefix,
        created_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
    )


def _repository():
    try:
        from storage.learning_repository import LearningRepository
    except ImportError as exc:
        pytest.fail(f"LearningRepository 尚未实现: {exc}")
    from storage.credential_crypto import CredentialCipher, CredentialKeyring

    return LearningRepository(
        cipher=CredentialCipher(
            CredentialKeyring(keys={"test": b"t" * 32}, active_key_id="test")
        )
    )


def test_settings_persist_across_repository_replacement_and_are_isolated(
    isolated_web_storage, users
):
    first = _repository()
    saved = first.upsert_settings(
        users[0][0], [{"name": "A", "api_key": "secret"}], 0
    )
    config_id = saved["configs"][0]["config_id"]

    configure_database(isolated_web_storage)
    replacement = _repository()
    assert replacement.get_public_settings(users[0][0]) == {
        "configs": [{
            "name": "A",
            "config_id": config_id,
            "has_api_key": True,
            "api_key_masked": "********",
            "has_embedding_api_key": False,
            "embedding_api_key_masked": "",
        }],
        "active_index": 0,
    }
    assert replacement.get_runtime_settings(users[0][0]) == {
        "configs": [{
            "name": "A",
            "config_id": config_id,
            "api_key": "secret",
            "embedding_api_key": "",
        }],
        "active_index": 0,
    }
    assert replacement.get_public_settings(users[1][0]) == {
        "configs": [], "active_index": 0
    }


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"configs": {}},
        {"configs": [], "active_index": True},
        {"configs": [{}], "active_index": 1},
        {"configs": ["bad"], "active_index": 0},
        {"configs": [[]], "active_index": 0},
        {"configs": [None], "active_index": 0},
        {"configs": [False], "active_index": 0},
    ],
)
def test_settings_route_rejects_malformed_json(authenticated_clients, payload):
    client, _users, tokens = authenticated_clients
    response = client.put("/api/v2/settings", json=payload, headers=_headers(tokens[0]))
    assert response.status_code == 400


def test_settings_route_never_returns_key_and_blank_update_preserves_it(
    authenticated_clients,
):
    client, users, tokens = authenticated_clients
    initial = {
        "configs": [
            {
                "name": "Primary",
                "api_url": "https://api.example/v1",
                "model_name": "chat-model",
                "api_key": "browser-must-not-see",
                "embedding_model": "embed-model",
            }
        ],
        "active_index": 0,
    }
    saved_response = client.put(
        "/api/v2/settings", json=initial, headers=_headers(tokens[0])
    )
    assert saved_response.status_code == 200
    assert saved_response.get_json()["configs"][0]["has_api_key"] is True
    assert "browser-must-not-see" not in saved_response.get_data(as_text=True)

    response = client.get("/api/v2/settings", headers=_headers(tokens[0]))
    payload = response.get_json()

    assert response.status_code == 200
    assert "browser-must-not-see" not in response.get_data(as_text=True)
    assert payload["configs"][0]["has_api_key"] is True
    assert "api_key" not in payload["configs"][0]
    payload["configs"][0]["api_key"] = ""
    assert client.put(
        "/api/v2/settings", json=payload, headers=_headers(tokens[0])
    ).status_code == 200
    runtime = api_v2._learning_repository.get_runtime_settings(users[0][0])
    assert runtime["configs"][0]["api_key"] == "browser-must-not-see"
    with session_scope() as session:
        row = session.get(UserSettings, users[0][0])
        assert row is not None
        assert row.llm_api_key == ""
        assert "browser-must-not-see" not in json.dumps(row.llm_configs_json)
        assert "browser-must-not-see" not in json.dumps(row.llm_secrets_encrypted_json)


@pytest.mark.parametrize("invalid", ["bad", [], None, False])
def test_settings_repository_defensively_rejects_non_object_configs(users, invalid):
    repository = _repository()
    with pytest.raises(ValueError, match="config must be a JSON object"):
        repository.upsert_settings(users[0][0], [invalid], 0)
    assert repository.get_public_settings(users[0][0]) == {
        "configs": [], "active_index": 0
    }


def test_settings_blank_update_preserves_key_by_config_id_after_reorder(users):
    repository = _repository()
    saved = repository.upsert_settings(
        users[0][0],
        [
            {"name": "A", "api_key": "key-a"},
            {"name": "B", "api_key": "key-b"},
        ],
        0,
    )

    repository.upsert_settings(
        users[0][0],
        [
            {**saved["configs"][1], "api_key": ""},
            {**saved["configs"][0], "api_key": ""},
        ],
        0,
    )

    runtime = repository.get_runtime_settings(users[0][0])["configs"]
    assert [config["api_key"] for config in runtime] == ["key-b", "key-a"]


def test_settings_requires_explicit_clear_and_rejects_foreign_config_id(users):
    repository = _repository()
    saved = repository.upsert_settings(
        users[0][0], [{"name": "A", "api_key": "key-a"}], 0
    )
    config = saved["configs"][0]

    cleared = repository.upsert_settings(
        users[0][0], [{**config, "clear_api_key": True}], 0
    )
    assert cleared["configs"][0]["has_api_key"] is False

    with pytest.raises(ValueError, match="config_id is not owned by this user"):
        repository.upsert_settings(
            users[0][0],
            [{"config_id": "foreign", "name": "X", "api_key": "key-x"}],
            0,
        )


def test_proof_workspaces_persist_and_are_isolated(authenticated_clients):
    client, _users, tokens = authenticated_clients
    saved = client.put(
        "/api/v2/proof-workspaces/graph-a/7",
        json={"userProof": "proof", "versions": [{"v": 1}], "aiMessages": [], "imports": []},
        headers=_headers(tokens[0]),
    )
    assert saved.status_code == 200
    api_v2._jobs.clear()
    owner = client.get("/api/v2/proof-workspaces/graph-a", headers=_headers(tokens[0]))
    other = client.get("/api/v2/proof-workspaces/graph-a", headers=_headers(tokens[1]))
    assert owner.get_json()["workspaces"][0]["userProof"] == "proof"
    assert other.get_json() == {"workspaces": []}


def test_repository_snapshots_survive_memory_clear_and_enforce_owner(users):
    repository = _repository()
    owner_id, other_id = users[0][0], users[1][0]
    for status in ("running", "paused", "error", "done"):
        assert repository.upsert_job_progress(owner_id, _snapshot(f"job-{status}", status)) is True
    api_v2._jobs.clear()

    assert {row["status"] for row in repository.list_history(owner_id)} == {
        "done", "error", "paused", "running"
    }
    assert repository.get_owned_history(owner_id, "job-done")["nodes"] == [{"id": 1, "title": "定理"}]
    assert repository.get_owned_history(other_id, "job-done") is None
    assert repository.upsert_job_progress(other_id, _snapshot("job-done", "error")) is False
    assert repository.get_owned_history(owner_id, "job-done")["status"] == "done"


def test_history_round_trips_object_storage_prefix(users):
    repository = _repository()
    owner_id = users[0][0]
    prefix = f"mathweaver/users/{owner_id}/jobs/job-oss/"

    assert repository.upsert_job_progress(
        owner_id,
        _snapshot("job-oss", object_storage_prefix=prefix),
    ) is True

    assert repository.get_owned_history(owner_id, "job-oss")["object_storage_prefix"] == prefix


def test_version_switch_and_old_cleanup_outbox_commit_together(users):
    repository = _repository()
    owner_id = users[0][0]
    first = StoredVersion("a" * 32, "prefix-a/", "1" * 64, 2, 10)
    second = StoredVersion("b" * 32, "prefix-b/", "2" * 64, 3, 20)

    assert repository.commit_storage_version(owner_id, _snapshot("job-version"), first)
    assert repository.commit_storage_version(owner_id, _snapshot("job-version"), second)

    row = repository.get_owned_history(owner_id, "job-version")
    assert row["storage_version"] == second.version_id
    assert row["storage_checksum"] == second.manifest_checksum
    with session_scope() as session:
        operations = session.scalars(select(StorageOutbox)).all()
    assert [(item.operation, item.version_id) for item in operations] == [
        ("delete_version", first.version_id)
    ]


def test_soft_delete_hides_history_and_enqueues_cleanup(users):
    repository = _repository()
    owner_id = users[0][0]
    stored = StoredVersion("c" * 32, "prefix-c/", "3" * 64, 1, 5)
    assert repository.commit_storage_version(owner_id, _snapshot("job-delete"), stored)

    assert repository.soft_delete_history(owner_id, "job-delete") is True

    assert repository.get_owned_history(owner_id, "job-delete") is None
    assert repository.list_history(owner_id) == []
    with session_scope() as session:
        operations = session.scalars(
            select(StorageOutbox).where(StorageOutbox.history_id == "job-delete")
        ).all()
    assert {item.operation for item in operations} == {
        "delete_job_versions",
        "delete_local_cache",
    }


def test_list_history_uses_summary_projection_without_large_json(users):
    repository = _repository()
    owner_id = users[0][0]
    assert repository.upsert_job_progress(owner_id, _snapshot("job-summary", "done"))
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lower())

    event.listen(get_engine(), "before_cursor_execute", capture)
    try:
        rows = repository.list_history(owner_id)
    finally:
        event.remove(get_engine(), "before_cursor_execute", capture)

    assert rows[0]["id"] == "job-summary"
    select_sql = next(item for item in statements if item.lstrip().startswith("select"))
    assert "nodes_json" not in select_sql
    assert "edges_json" not in select_sql
    assert "source_pdf_json" not in select_sql


def test_persisted_status_and_result_are_available_after_jobs_clear(authenticated_clients):
    client, users, tokens = authenticated_clients
    repository = _repository()
    repository.upsert_job_progress(users[0][0], _snapshot("persisted-running", "running"))
    repository.upsert_job_progress(users[0][0], _snapshot("persisted-done", "done"))
    api_v2._jobs.clear()

    status = client.get("/api/v2/jobs/persisted-running/status", headers=_headers(tokens[0]))
    result = client.get("/api/v2/jobs/persisted-done/result", headers=_headers(tokens[0]))
    detail = client.get("/api/v2/history/persisted-running", headers=_headers(tokens[0]))
    assert status.status_code == 200 and status.get_json()["status"] == "running"
    assert result.status_code == 200 and result.get_json()["nodes"][0]["id"] == 1
    assert detail.status_code == 409


def _install_owned_done_job(tmp_path: Path, owner_id: int) -> str:
    job_id = "owned-job"
    artifact_dir = api_v2._persistent_job_dir(job_id)
    cache_dir = artifact_dir / "_stage_cache"
    cache_dir.mkdir(parents=True)
    (artifact_dir / "nodes.json").write_text("[]", encoding="utf-8")
    (artifact_dir / "edges.json").write_text("[]", encoding="utf-8")
    (cache_dir / "manifest.json").write_text('{"schema_version": 1}', encoding="utf-8")
    source_dir = api_v2._source_pdf_dir(job_id)
    source_dir.mkdir(parents=True)
    pdf_path = source_dir / "source.pdf"
    log_path = source_dir / "compile.log"
    pdf_path.write_bytes(b"%PDF-1.4")
    log_path.write_text("compile", encoding="utf-8")
    api_v2._jobs[job_id] = {
        "job_id": job_id,
        "status": "done",
        "filename": "lesson.md",
        "result": {"nodes": [{"id": 1, "content": "x"}], "edges": [], "latex_macros": {}},
        "partial": None,
        "source_pdf": {
            "status": "ready", "available": True, "pdf_path": str(pdf_path),
            "log_path": str(log_path), "source_path": str(source_dir / "source.tex"),
        },
        "source": "pipeline",
        "latex_macros": {},
        "_artifact_dir": str(artifact_dir),
        "_user_id": owner_id,
        "_history_persisted": True,
        "stage_index": 0,
        "total_stages": 0,
        "stages_done": [],
        "created_at": "2026-08-21T00:00:00+00:00",
    }
    return job_id


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v2/jobs/owned-job/status"),
        ("get", "/api/v2/jobs/owned-job/error-detail"),
        ("get", "/api/v2/jobs/owned-job/result"),
        ("get", "/api/v2/source-pdf/owned-job"),
        ("get", "/api/v2/source-pdf/owned-job/compile-log"),
        ("get", "/api/v2/source-pdf/owned-job/locate?node_id=1"),
        ("post", "/api/v2/export/owned-job"),
        ("post", "/api/v2/export/owned-job/artifacts"),
        ("post", "/api/v2/jobs/owned-job/pause"),
        ("post", "/api/v2/jobs/owned-job/cancel"),
        ("post", "/api/v2/jobs/owned-job/resume"),
    ],
)
def test_second_user_cannot_access_any_job_resource(
    authenticated_clients, tmp_path, method, path
):
    client, users, tokens = authenticated_clients
    _install_owned_done_job(tmp_path, users[0][0])
    response = getattr(client, method)(path, headers=_headers(tokens[1]))
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v2/jobs/owned-job/status"),
        ("get", "/api/v2/jobs/owned-job/error-detail"),
        ("get", "/api/v2/jobs/owned-job/result"),
        ("get", "/api/v2/source-pdf/owned-job"),
        ("get", "/api/v2/source-pdf/owned-job/compile-log"),
        ("get", "/api/v2/source-pdf/owned-job/locate?node_id=1"),
        ("post", "/api/v2/export/owned-job"),
        ("post", "/api/v2/export/owned-job/artifacts"),
        ("post", "/api/v2/jobs/owned-job/pause"),
        ("post", "/api/v2/jobs/owned-job/cancel"),
        ("post", "/api/v2/jobs/owned-job/resume"),
    ],
)
def test_web_job_resources_require_authentication(authenticated_clients, tmp_path, method, path):
    client, users, _tokens = authenticated_clients
    _install_owned_done_job(tmp_path, users[0][0])
    assert getattr(client, method)(path).status_code == 401


def test_anonymous_web_processing_creates_no_job_or_artifact(authenticated_clients):
    client, users, _tokens = authenticated_clients
    before = set(api_v2._jobs)
    with patch.object(api_v2, "_start_pipeline_attempt"):
        response = client.post(
            "/api/v2/jobs",
            json={
                "text": "# demo", "api_url": "https://example.test/v1",
                "model_name": "m", "api_key": "secret", "embedding_model": "e",
            },
        )
    assert response.status_code == 401
    assert set(api_v2._jobs) == before
    assert not (api_v2._DATA_ROOT / "jobs").exists()
    assert _repository().list_history(users[0][0]) == []


def test_anonymous_agent_import_is_rejected(authenticated_clients):
    client, _users, _tokens = authenticated_clients
    response = client.post(
        "/api/v2/agent-import",
        data={
            "nodes_file": (io.BytesIO(json.dumps([{"id": 1}]).encode()), "nodes.json"),
            "edges_file": (io.BytesIO(b"[]"), "edges.json"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 401


def test_source_pdf_metadata_is_sanitized_and_traversal_cannot_escape(users, tmp_path):
    repository = _repository()
    snapshot = _snapshot("safe-pdf", "done")
    unsafe = {
        **snapshot.__dict__,
        "source_pdf": {
            "status": "ready", "available": True,
            "pdf_path": str(tmp_path / "outside.pdf"),
            "log_name": "../../outside.log", "source_name": "..\\outside.tex",
        },
    }
    snapshot = type(snapshot)(**unsafe)
    assert repository.upsert_job_progress(users[0][0], snapshot) is True
    stored = repository.get_owned_history(users[0][0], "safe-pdf")["source_pdf"]
    assert "pdf_path" not in stored
    assert stored["pdf_name"] == "outside.pdf"
    assert stored["log_name"] == "outside.log"
    assert stored["source_name"] == "outside.tex"


def test_web_learning_routes_never_use_legacy_sqlite(authenticated_clients, monkeypatch):
    client, users, tokens = authenticated_clients
    repository = _repository()
    repository.upsert_job_progress(users[0][0], _snapshot("centralized", "done"))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Web learning routes must not open legacy SQLite")

    monkeypatch.setattr(api_v2, "_get_db", forbidden)
    monkeypatch.setattr(api_v2.sqlite3, "connect", forbidden)
    assert client.get("/api/v2/settings", headers=_headers(tokens[0])).status_code == 200
    assert client.get("/api/v2/history", headers=_headers(tokens[0])).status_code == 200
    assert client.get("/api/v2/history/centralized", headers=_headers(tokens[0])).status_code == 200
    assert client.get("/api/v2/proof-workspaces/graph", headers=_headers(tokens[0])).status_code == 200


def test_job_creation_surfaces_progress_failure_without_marking_persisted(
    authenticated_clients, monkeypatch
):
    client, _users, tokens = authenticated_clients
    monkeypatch.setattr(api_v2._learning_repository, "upsert_job_progress", lambda *_args: False)
    with patch.object(api_v2, "_start_pipeline_attempt"):
        response = client.post(
            "/api/v2/jobs",
            json={
                "text": "# demo", "api_url": "https://example.test/v1",
                "model_name": "m", "api_key": "secret", "embedding_model": "e",
            },
            headers=_headers(tokens[0]),
        )
    assert response.status_code == 500
    assert all(not job.get("_history_persisted") for job in api_v2._jobs.values())


def test_authenticated_job_uses_stored_key_when_browser_sends_blank(
    authenticated_clients,
):
    client, users, tokens = authenticated_clients
    api_v2._learning_repository.upsert_settings(
        users[0][0],
        [{
            "name": "Stored",
            "api_url": "https://stored.example/v1",
            "model_name": "stored-model",
            "api_key": "stored-secret",
            "embedding_url": "https://stored.example/embed",
            "embedding_model": "stored-embedding",
            "embedding_api_key": "stored-embedding-secret",
        }],
        0,
    )

    with patch.object(api_v2, "_start_pipeline_attempt"):
        response = client.post(
            "/api/v2/jobs",
            json={
                "text": "# demo",
                "api_url": "https://stored.example/v1",
                "model_name": "stored-model",
                "api_key": "",
                "embedding_model": "stored-embedding",
            },
            headers=_headers(tokens[0]),
        )

    assert response.status_code == 202
    job = api_v2._jobs[response.get_json()["job_id"]]
    assert job["_llm_config"]["api_key"] == "stored-secret"
    assert job["_llm_config"]["embedding_url"] == "https://stored.example/embed"
    assert job["_llm_config"]["embedding_api_key"] == "stored-embedding-secret"


def _install_persisted_live_job(owner_id: int, tmp_path: Path, status: str = "running") -> dict:
    job_id = f"persist-failure-{status}"
    artifact_dir = api_v2._persistent_job_dir(job_id)
    cache_dir = artifact_dir / "_stage_cache"
    cache_dir.mkdir(parents=True)
    source = artifact_dir / "input.md"
    source.write_text("# Demo", encoding="utf-8")
    (cache_dir / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "status": status, "completed_stages": []}),
        encoding="utf-8",
    )
    job = {
        "job_id": job_id,
        "status": status,
        "filename": "input.md",
        "stage": "parse",
        "stage_label": "解析",
        "stage_index": 0,
        "total_stages": 2,
        "stages_done": [],
        "result": None,
        "partial": {"nodes": [], "edges": []},
        "error": None,
        "source_markdown": "# Demo",
        "latex_macros": {},
        "source_format": "markdown",
        "source_pdf": None,
        "source": "pipeline",
        "_artifact_dir": str(artifact_dir),
        "_md_path": str(source),
        "_llm_config": {
            "api_url": "https://example.test/v1",
            "model_name": "m",
            "api_key": "top-secret",
            "embedding_model": "e",
        },
        "_experimental_logic_ir": False,
        "_user_id": owner_id,
        "_history_persisted": True,
        "created_at": "2026-08-21T00:00:00+00:00",
    }
    api_v2._jobs[job_id] = job
    return job


@pytest.mark.parametrize(
    "event",
    [
        {"type": "done", "result": {"nodes": [{"id": 1}], "edges": []}},
        {"type": "error", "error": "provider failed", "error_detail": "top-secret trace"},
        {"type": "stage_start", "stage": "parse", "stage_label": "解析", "stage_index": 1},
    ],
    ids=["done", "error", "progress"],
)
def test_async_persistence_failure_is_exposed_without_false_success(
    authenticated_clients, tmp_path, monkeypatch, event
):
    client, users, tokens = authenticated_clients
    job = _install_persisted_live_job(users[0][0], tmp_path)
    monkeypatch.setattr(api_v2, "_upsert_job_history", lambda *_args, **_kwargs: False)

    api_v2._apply_pipeline_event(job["job_id"], None, event)
    response = client.get(
        f"/api/v2/jobs/{job['job_id']}/status", headers=_headers(tokens[0])
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "error"
    assert payload["persistence_error"] is True
    assert payload["error_code"] == "persistence_error"
    assert "top-secret" not in json.dumps(payload)
    assert job.get("result") is None


def test_web_job_verifies_new_oss_version_before_database_pointer_switch(users, tmp_path, monkeypatch):
    owner_id = users[0][0]
    job = _install_persisted_live_job(owner_id, tmp_path)
    storage = FakeObjectStorage()
    order: list[str] = []
    original_upsert = api_v2._upsert_job_history

    def tracked_upsert(*args, **kwargs):
        assert storage.synced == [(owner_id, job["job_id"])]
        order.append("database")
        return original_upsert(*args, **kwargs)

    monkeypatch.setattr(api_v2, "_object_storage", storage)
    monkeypatch.setattr(api_v2, "_upsert_job_history", tracked_upsert)

    assert api_v2._persist_job_with_files(job, "running") is True
    row = _repository().get_owned_history(owner_id, job["job_id"])

    assert order == ["database"]
    assert row["object_storage_prefix"].startswith(
        f"{storage.task_prefix(owner_id, job['job_id'])}versions/"
    )
    assert row["storage_version"] == "f" * 32


def test_oss_failure_never_reports_persistence_success(users, tmp_path, monkeypatch):
    job = _install_persisted_live_job(users[0][0], tmp_path)
    monkeypatch.setattr(api_v2, "_object_storage", FakeObjectStorage(fail_sync=True))

    assert api_v2._persist_job_state(job, "done") is False
    assert job["status"] == "error"
    assert job["error_code"] == "persistence_error"


def test_resume_persistence_failure_restores_memory_and_never_starts_worker(
    authenticated_clients, tmp_path, monkeypatch
):
    client, users, tokens = authenticated_clients
    job = _install_persisted_live_job(users[0][0], tmp_path, status="paused")
    before = {
        "status": job["status"],
        "stage": job["stage"],
        "stage_label": job["stage_label"],
        "stage_index": job["stage_index"],
        "stages_done": list(job["stages_done"]),
        "result": job["result"],
    }
    started = []
    monkeypatch.setattr(api_v2, "_upsert_job_history", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        api_v2, "_start_pipeline_attempt", lambda *args, **kwargs: started.append((args, kwargs))
    )

    response = client.post(
        f"/api/v2/jobs/{job['job_id']}/resume", headers=_headers(tokens[0])
    )

    assert response.status_code == 503
    assert response.get_json() == {"error": "Task progress persistence failed"}
    assert started == []
    assert {key: job[key] for key in before} == before
    assert job["_persistence_error"] is True


def test_persisted_done_artifact_export_uses_database_json_and_ignores_client_injection(
    authenticated_clients
):
    client, users, tokens = authenticated_clients
    owner_id = users[0][0]
    repository = _repository()
    assert repository.upsert_job_progress(owner_id, _snapshot("restart-export", "done"))
    api_v2._jobs.clear()

    response = client.post(
        "/api/v2/export/restart-export/artifacts",
        json={"nodes": [{"id": "injected"}], "edges": [], "filename": "evil"},
        headers=_headers(tokens[0]),
    )
    forbidden = client.post(
        "/api/v2/export/restart-export/artifacts",
        json={"nodes": [], "edges": []},
        headers=_headers(tokens[1]),
    )

    assert response.status_code == 200
    assert response.headers["X-MathGraph-Export-Mode"] == "nodes-edges-only"
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        nodes_name = next(name for name in archive.namelist() if name.endswith("/nodes.json"))
        nodes = json.loads(archive.read(nodes_name))
    assert nodes == [{"id": 1, "title": "定理"}]
    assert forbidden.status_code == 404


def test_restart_export_restores_owned_artifacts_from_oss(
    authenticated_clients, monkeypatch
):
    client, users, tokens = authenticated_clients
    owner_id = users[0][0]
    storage = FakeObjectStorage()
    prefix = storage.task_prefix(owner_id, "restart-oss-export")
    assert _repository().upsert_job_progress(
        owner_id,
        _snapshot("restart-oss-export", "done", object_storage_prefix=prefix),
    )
    api_v2._jobs.clear()
    monkeypatch.setattr(api_v2, "_object_storage", storage)

    response = client.post(
        "/api/v2/export/restart-oss-export/artifacts",
        headers=_headers(tokens[0]),
    )
    forbidden = client.post(
        "/api/v2/export/restart-oss-export/artifacts",
        headers=_headers(tokens[1]),
    )

    assert response.status_code == 200
    assert response.headers["X-MathGraph-Export-Mode"] == "complete"
    assert storage.restored == [(owner_id, "restart-oss-export")]
    assert forbidden.status_code == 404


def test_source_pdf_and_log_restore_only_after_owner_check(
    authenticated_clients, monkeypatch
):
    client, users, tokens = authenticated_clients
    owner_id = users[0][0]
    storage = FakeObjectStorage()
    job_id = "restart-source-pdf"
    prefix = storage.task_prefix(owner_id, job_id)
    source_pdf = {
        "status": "ready",
        "available": True,
        "error": None,
        "pdf_name": "source.pdf",
        "source_name": "source.tex",
        "log_name": "compile.log",
        "pdf_url": f"/api/v2/source-pdf/{job_id}",
        "compile_log_url": f"/api/v2/source-pdf/{job_id}/compile-log",
    }
    assert _repository().upsert_job_progress(
        owner_id,
        _snapshot(
            job_id,
            "done",
            object_storage_prefix=prefix,
            source_pdf=source_pdf,
        ),
    )
    api_v2._jobs.clear()
    monkeypatch.setattr(api_v2, "_object_storage", storage)

    pdf = client.get(f"/api/v2/source-pdf/{job_id}", headers=_headers(tokens[0]))
    log = client.get(
        f"/api/v2/source-pdf/{job_id}/compile-log",
        headers=_headers(tokens[0]),
    )
    forbidden = client.get(f"/api/v2/source-pdf/{job_id}", headers=_headers(tokens[1]))

    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF-")
    assert log.status_code == 200 and b"restored log" in log.data
    assert storage.restored == [(owner_id, job_id)]
    assert forbidden.status_code == 404


def test_history_resume_restores_cache_before_availability_check(
    authenticated_clients, monkeypatch
):
    client, users, tokens = authenticated_clients
    owner_id = users[0][0]
    storage = FakeObjectStorage()
    job_id = "restart-paused-job"
    prefix = storage.task_prefix(owner_id, job_id)
    assert _repository().upsert_job_progress(
        owner_id,
        _snapshot(job_id, "paused", object_storage_prefix=prefix),
    )
    api_v2._jobs.clear()
    monkeypatch.setattr(api_v2, "_object_storage", storage)

    response = client.post(
        f"/api/v2/history/{job_id}/resume",
        json={},
        headers=_headers(tokens[0]),
    )
    forbidden = client.post(
        f"/api/v2/history/{job_id}/resume",
        json={},
        headers=_headers(tokens[1]),
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Complete LLM and embedding configuration is required"
    assert storage.restored == [(owner_id, job_id)]
    assert forbidden.status_code == 404


def test_history_delete_soft_deletes_before_async_object_cleanup(authenticated_clients, monkeypatch):
    client, users, tokens = authenticated_clients
    owner_id = users[0][0]
    storage = FakeObjectStorage()
    assert _repository().upsert_job_progress(
        owner_id,
        _snapshot(
            "delete-oss-job",
            "done",
            object_storage_prefix=storage.task_prefix(owner_id, "delete-oss-job"),
        ),
    )
    monkeypatch.setattr(api_v2, "_object_storage", storage)

    response = client.delete(
        "/api/v2/history/delete-oss-job",
        headers=_headers(tokens[0]),
    )

    assert response.status_code == 200
    assert response.get_json()["cleanup_status"] == "pending"
    assert storage.deleted == []
    assert _repository().get_owned_history(owner_id, "delete-oss-job") is None


def test_explicit_desktop_mode_restores_sqlite_source_pdf_and_artifact_fallback(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AI4MATH_DESKTOP", "1")
    monkeypatch.setattr(api_v2, "_desktop_legacy_auth", True)
    monkeypatch.setattr(api_v2, "_DB_PATH", tmp_path / "desktop.db")
    monkeypatch.setattr(api_v2, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(api_v2, "_SOURCE_PDF_ROOT", tmp_path / "uploads" / "source_pdfs")
    api_v2._jobs.clear()
    api_v2._init_db()
    source_dir = api_v2._source_pdf_dir("desktop-persisted")
    source_dir.mkdir(parents=True)
    (source_dir / "source.pdf").write_bytes(b"%PDF-1.4 desktop")
    outside_pdf = tmp_path / "outside.pdf"
    outside_log = tmp_path / "outside.log"
    outside_pdf.write_bytes(b"%PDF-1.4 outside-secret")
    outside_log.write_text("outside-secret-log", encoding="utf-8")
    with api_v2.app.app_context():
        db = api_v2._get_db()
        for job_id, source_meta in (
            (
                "desktop-persisted",
                {"status": "ready", "available": True, "pdf_name": "source.pdf"},
            ),
            (
                "desktop-outside-pdf",
                {"status": "ready", "available": True, "pdf_path": str(outside_pdf)},
            ),
            (
                "desktop-outside-log",
                {"status": "failed", "available": False, "log_path": str(outside_log)},
            ),
        ):
            db.execute(
                """INSERT INTO history
                   (id, user_id, filename, nodes_json, edges_json, source_pdf_json,
                    status, stages_done_json, source_format, created_at)
                   VALUES (?, 1, 'source.tex', '[]', '[]', ?, 'done', '[]', 'tex', ?)""",
                (job_id, json.dumps(source_meta), "2026-08-21T00:00:00"),
            )
        db.commit()

    client = api_v2.app.test_client()
    pdf = client.get("/api/v2/source-pdf/desktop-persisted")
    outside_pdf_response = client.get("/api/v2/source-pdf/desktop-outside-pdf")
    outside_log_response = client.get(
        "/api/v2/source-pdf/desktop-outside-log/compile-log"
    )
    fallback = client.post(
        "/api/v2/export/desktop-missing/artifacts",
        json={"filename": "legacy", "nodes": [{"id": 9}], "edges": []},
    )

    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF-1.4")
    assert outside_pdf_response.status_code == 404
    assert outside_log_response.status_code == 404
    assert fallback.status_code == 200
    assert fallback.headers["X-MathGraph-Export-Mode"] == "nodes-edges-only"


def test_source_pdf_job_root_rejects_dot_segments_and_accepts_safe_direct_child(tmp_path, monkeypatch):
    monkeypatch.setattr(api_v2, "_SOURCE_PDF_ROOT", tmp_path / "source-pdfs")
    safe = api_v2._SOURCE_PDF_ROOT / "safe-job"
    safe.mkdir(parents=True)
    resolver = getattr(
        api_v2,
        "_controlled_source_pdf_job_root",
        lambda job_id: api_v2._source_pdf_dir(job_id).resolve(),
    )

    assert resolver(".") is None
    assert resolver("..") is None
    assert resolver("safe-job") == safe.resolve()


def test_desktop_job_directory_junction_cannot_escape_source_pdf_root(tmp_path, monkeypatch):
    monkeypatch.setenv("AI4MATH_DESKTOP", "1")
    monkeypatch.setattr(api_v2, "_desktop_legacy_auth", True)
    monkeypatch.setattr(api_v2, "_DB_PATH", tmp_path / "desktop-junction.db")
    monkeypatch.setattr(api_v2, "_DATA_ROOT", tmp_path / "data")
    controlled_root = tmp_path / "data" / "uploads" / "source_pdfs"
    monkeypatch.setattr(api_v2, "_SOURCE_PDF_ROOT", controlled_root)
    api_v2._jobs.clear()
    api_v2._init_db()

    external = tmp_path / "external"
    external.mkdir()
    (external / "source.pdf").write_bytes(b"%PDF-1.4 junction-outside")
    (external / "compile.log").write_text("junction-outside-log", encoding="utf-8")
    (external / "source.tex").write_text("\\section{junction-outside-source}", encoding="utf-8")
    controlled_root.mkdir(parents=True)
    junction = controlled_root / "junction-job"
    created = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(external)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if created.returncode != 0:
        # 受限 Windows 环境无法创建 junction 时，用路径替身复现“词法根在内、resolve 后在外”。
        original_source_dir = api_v2._source_pdf_dir

        class RedirectedJobRoot:
            def resolve(self):
                return external.resolve()

            def __truediv__(self, name):
                return external / name

        monkeypatch.setattr(
            api_v2,
            "_source_pdf_dir",
            lambda job_id: RedirectedJobRoot() if job_id == "junction-job" else original_source_dir(job_id),
        )

    with api_v2.app.app_context():
        db = api_v2._get_db()
        db.execute(
            """INSERT INTO history
               (id, user_id, filename, nodes_json, edges_json, source_pdf_json,
                status, stages_done_json, source_format, created_at)
               VALUES (?, 1, 'source.tex', ?, '[]', ?, 'done', '[]', 'tex', ?)""",
            (
                "junction-job",
                json.dumps([{"id": 1, "content": "node"}]),
                json.dumps({
                    "status": "ready",
                    "available": True,
                    "pdf_name": "source.pdf",
                    "log_name": "compile.log",
                    "source_name": "source.tex",
                }),
                "2026-08-21T00:00:00",
            ),
        )
        db.commit()

    client = api_v2.app.test_client()
    pdf = client.get("/api/v2/source-pdf/junction-job")
    log = client.get("/api/v2/source-pdf/junction-job/compile-log")
    locator = client.get("/api/v2/source-pdf/junction-job/locate?node_id=1")

    assert pdf.status_code == 404
    assert log.status_code == 404
    assert locator.status_code == 404
    if junction.exists():
        junction.rmdir()


def test_web_job_junction_to_sibling_job_cannot_cross_resource_ownership(
    authenticated_clients, tmp_path, monkeypatch
):
    client, users, tokens = authenticated_clients
    controlled_root = tmp_path / "data" / "uploads" / "source_pdfs"
    monkeypatch.setattr(api_v2, "_SOURCE_PDF_ROOT", controlled_root)
    repository = _repository()
    source_meta = {
        "status": "ready",
        "available": True,
        "pdf_name": "source.pdf",
        "log_name": "compile.log",
        "source_name": "source.tex",
    }
    for owner_id, job_id in ((users[0][0], "job-a"), (users[1][0], "job-b")):
        snapshot = _snapshot(job_id, "done")
        assert repository.upsert_job_progress(
            owner_id, type(snapshot)(**{**snapshot.__dict__, "source_pdf": source_meta})
        )
    api_v2._jobs.clear()

    job_b = controlled_root / "job-b"
    job_b.mkdir(parents=True)
    (job_b / "source.pdf").write_bytes(b"%PDF-1.4 user-b-private")
    (job_b / "compile.log").write_text("user-b-private-log", encoding="utf-8")
    (job_b / "source.tex").write_text("\\section{USER B PRIVATE TITLE}", encoding="utf-8")
    job_a = controlled_root / "job-a"
    created = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(job_a), str(job_b)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if created.returncode != 0:
        original_resolve = Path.resolve
        lexical_a = job_a.absolute()
        resolved_b = job_b.resolve()

        def redirected_resolve(path, *args, **kwargs):
            if path.absolute() == lexical_a:
                return resolved_b
            return original_resolve(path, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", redirected_resolve)

    try:
        a_headers = _headers(tokens[0])
        b_headers = _headers(tokens[1])
        a_pdf = client.get("/api/v2/source-pdf/job-a", headers=a_headers)
        a_log = client.get("/api/v2/source-pdf/job-a/compile-log", headers=a_headers)
        a_locator = client.get(
            "/api/v2/source-pdf/job-a/locate?node_id=1", headers=a_headers
        )
        b_pdf = client.get("/api/v2/source-pdf/job-b", headers=b_headers)
        b_log = client.get("/api/v2/source-pdf/job-b/compile-log", headers=b_headers)
        b_locator = client.get(
            "/api/v2/source-pdf/job-b/locate?node_id=1", headers=b_headers
        )

        assert [a_pdf.status_code, a_log.status_code, a_locator.status_code] == [404, 404, 404]
        assert [b_pdf.status_code, b_log.status_code, b_locator.status_code] == [200, 200, 200]
        assert b_pdf.data.startswith(b"%PDF-1.4 user-b-private")
        assert b"user-b-private-log" in b_log.data
    finally:
        if job_a.exists():
            job_a.rmdir()


def test_history_model_does_not_store_llm_secrets(users):
    repository = _repository()
    assert repository.upsert_job_progress(users[0][0], _snapshot("no-secret", "running"))
    with session_scope() as session:
        row = session.get(History, "no-secret")
        serialized = json.dumps({
            "nodes": row.nodes_json,
            "edges": row.edges_json,
            "source_pdf": row.source_pdf_json,
        })
    assert "api_key" not in serialized
