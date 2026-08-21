import io
import gc
import json
import os
from pathlib import Path
import sys
import tempfile
import types
from unittest.mock import patch
from datetime import datetime, timezone

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("MATHWEAVER_DATABASE_URL", "sqlite+pysqlite:///:memory:")
# 本脚本只覆盖任务/历史状态，避免可选 LLM SDK 影响认证夹具。
join_agent = types.ModuleType("JoinAgent")
for name in ("LLMParser", "SimpleLLM", "TextDivider", "MultiProcessor"):
    setattr(join_agent, name, type(name, (), {}))
sys.modules["JoinAgent"] = join_agent

import api_v2
from storage import database as storage_database
from storage.database import configure_database, get_engine, session_scope
from storage.models import Base, User
from storage.learning_repository import JobSnapshot
from werkzeug.security import generate_password_hash


@pytest.fixture(autouse=True)
def isolated_test_databases(tmp_path, monkeypatch):
    """每个脚本测试重建认证库，并在退出时恢复所有 API 全局状态。"""
    previous_engine = storage_database._engine
    previous_session_factory = storage_database._session_factory
    previous_jobs = dict(api_v2._jobs)
    previous_runtimes = dict(api_v2._job_runtimes)
    configure_database("sqlite+pysqlite:///:memory:")
    test_engine = get_engine()
    Base.metadata.create_all(test_engine)
    monkeypatch.setattr(api_v2, "_DB_PATH", tmp_path / "legacy.db")
    api_v2._jobs.clear()
    api_v2._job_runtimes.clear()
    api_v2._init_db()
    try:
        yield
    finally:
        # 异常路径也必须释放引擎和 Flask g 中的旧 SQLite 连接。
        with api_v2.app.app_context():
            api_v2._close_db()
        Base.metadata.drop_all(test_engine)
        test_engine.dispose()
        storage_database._engine = previous_engine
        storage_database._session_factory = previous_session_factory
        api_v2._jobs.clear()
        api_v2._jobs.update(previous_jobs)
        api_v2._job_runtimes.clear()
        api_v2._job_runtimes.update(previous_runtimes)
        gc.collect()


class FakeProcess:
    def __init__(self):
        self.alive = True

    def is_alive(self):
        return self.alive


def _register(client, email):
    user = User.create_account(
        role="student",
        student_no=email.split("@", 1)[0],
        email=email,
        display_name=email,
        password_hash=generate_password_hash("password123"),
    )
    with session_scope() as session:
        session.add(user)
        session.flush()
        user_id = user.id
    response = client.post(
        "/api/v2/auth/login",
        json={"identifier": email, "password": "password123"},
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()["token"], user_id


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _llm_config():
    return {
        "api_url": "https://example.test/v1",
        "model_name": "test-model",
        "api_key": "top-secret",
        "embedding_url": "https://embedding.test/v1",
        "embedding_model": "test-embedding",
        "embedding_api_key": "embedding-secret",
    }


def _install_paused_job(root, user_id, job_id, *, experimental_logic_ir=False):
    artifact_dir = Path(root) / "jobs" / job_id
    cache_dir = artifact_dir / "_stage_cache"
    cache_dir.mkdir(parents=True)
    source = artifact_dir / "input.md"
    source.write_text("# Demo\n\nStatement.", encoding="utf-8")
    (cache_dir / "manifest.json").write_text(
        json.dumps({
            "schema_version": 1,
            "status": "running",
            "completed_stages": ["correct_text"],
            "current_stage": "segment_blocks",
        }),
        encoding="utf-8",
    )
    process = FakeProcess()
    stage_defs = api_v2._pipeline_stage_defs(experimental_logic_ir)
    job = {
        "job_id": job_id,
        "status": "running",
        "filename": "input.md",
        "stage": "segment_blocks",
        "stage_label": "段落分块",
        "stage_index": 1,
        "total_stages": len(stage_defs),
        "stages_done": ["correct_text"],
        "result": None,
        "partial": {"nodes": [{"id": 1}], "edges": []},
        "error": None,
        "source_markdown": source.read_text(encoding="utf-8"),
        "latex_macros": {},
        "source_format": "markdown",
        "source_pdf": None,
        "source": "pipeline",
        "_artifact_dir": str(artifact_dir),
        "_md_path": str(source),
        "_llm_config": _llm_config(),
        "_enable_analysis": True,
        "_experimental_logic_ir": experimental_logic_ir,
        "_stage_defs": stage_defs,
        "_user_id": user_id,
        "_persistent_artifacts": True,
        "_history_persisted": False,
        "_attempt_token": "attempt-1",
        "created_at": "2026-01-01T00:00:00",
    }
    api_v2._jobs[job_id] = job
    api_v2._job_runtimes[job_id] = {
        "attempt_token": "attempt-1",
        "process": process,
        "pause_requested": False,
    }
    return job, process


def test_logged_in_job_uses_persistent_artifact_directory():
    with tempfile.TemporaryDirectory() as tmp:
        original_db = api_v2._DB_PATH
        original_data_root = api_v2._DATA_ROOT
        original_source_root = api_v2._SOURCE_PDF_ROOT
        api_v2._DB_PATH = Path(tmp) / "history.db"
        api_v2._DATA_ROOT = Path(tmp)
        api_v2._SOURCE_PDF_ROOT = Path(tmp) / "uploads" / "source_pdfs"
        api_v2._init_db()
        client = api_v2.app.test_client()
        token, _user_id = _register(client, "persistent-job@example.com")
        try:
            with patch.object(api_v2, "_start_pipeline_attempt"):
                response = client.post(
                    "/api/v2/jobs",
                    data={
                        "file": (io.BytesIO(b"# Demo"), "demo.md"),
                        **_llm_config(),
                    },
                    headers=_headers(token),
                    content_type="multipart/form-data",
                )
            assert response.status_code == 202, response.get_json()
            job_id = response.get_json()["job_id"]
            job = api_v2._jobs[job_id]
            assert Path(job["_artifact_dir"]) == Path(tmp) / "jobs" / job_id
            assert job["_user_id"] is not None
        finally:
            for job_id, job in list(api_v2._jobs.items()):
                if Path(str(job.get("_artifact_dir", ""))).parent == Path(tmp) / "jobs":
                    api_v2._jobs.pop(job_id, None)
            api_v2._DB_PATH = original_db
            api_v2._DATA_ROOT = original_data_root
            api_v2._SOURCE_PDF_ROOT = original_source_root


def test_pause_history_cross_restart_resume_and_permanent_cancel():
    with tempfile.TemporaryDirectory() as tmp:
        original_db = api_v2._DB_PATH
        original_data_root = api_v2._DATA_ROOT
        original_source_root = api_v2._SOURCE_PDF_ROOT
        api_v2._DB_PATH = Path(tmp) / "history.db"
        api_v2._DATA_ROOT = Path(tmp)
        api_v2._SOURCE_PDF_ROOT = Path(tmp) / "uploads" / "source_pdfs"
        api_v2._jobs.clear()
        api_v2._job_runtimes.clear()
        api_v2._init_db()
        client = api_v2.app.test_client()
        owner_token, owner_id = _register(client, "history-owner@example.com")
        other_token, _other_id = _register(client, "history-other@example.com")
        job_id = "paused-history-job"
        job, process = _install_paused_job(
            tmp,
            owner_id,
            job_id,
            experimental_logic_ir=True,
        )
        try:
            running_cancel = client.post(
                f"/api/v2/jobs/{job_id}/cancel",
                headers=_headers(owner_token),
            )
            assert running_cancel.status_code == 409

            def terminate(selected):
                assert selected is process
                selected.alive = False

            with patch.object(api_v2, "_terminate_pipeline_process", side_effect=terminate):
                pause = client.post(
                    f"/api/v2/jobs/{job_id}/pause",
                    headers=_headers(owner_token),
                )
            assert pause.status_code == 200, pause.get_json()
            assert job["status"] == "paused"

            history = client.get("/api/v2/history", headers=_headers(owner_token))
            assert history.status_code == 200
            item = history.get_json()[0]
            assert item["id"] == job_id
            assert item["status"] == "paused"
            assert item["stages_done"] == ["correct_text"]
            assert item["experimental_logic_ir"] is True
            assert item["resume_available"] is True
            repeated_pause = client.post(
                f"/api/v2/jobs/{job_id}/pause",
                headers=_headers(owner_token),
            )
            assert repeated_pause.status_code == 200
            stored = api_v2._learning_repository.get_owned_history(owner_id, job_id)
            assert stored is not None
            database_text = json.dumps(stored, ensure_ascii=False)
            assert "top-secret" not in database_text
            assert "embedding-secret" not in database_text
            assert str(Path(tmp) / "jobs") not in database_text

            api_v2._jobs.clear()
            api_v2._job_runtimes.clear()
            started = []
            with patch.object(
                api_v2,
                "_start_pipeline_attempt",
                side_effect=lambda selected, *, resume: started.append((selected, resume)),
            ):
                resumed = client.post(
                    f"/api/v2/history/{job_id}/resume",
                    json={"llm_config": _llm_config()},
                    headers=_headers(owner_token),
                )
            assert resumed.status_code == 202, resumed.get_json()
            assert resumed.get_json()["job"]["job_id"] == job_id
            assert started == [(job_id, True)]
            assert api_v2._jobs[job_id]["_llm_config"]["api_key"] == "top-secret"
            assert api_v2._jobs[job_id]["_experimental_logic_ir"] is True
            assert api_v2._jobs[job_id]["total_stages"] == 16
            assert api_v2._learning_repository.get_owned_history(owner_id, job_id)["status"] == "running"

            forbidden = client.post(
                f"/api/v2/jobs/{job_id}/cancel",
                headers=_headers(other_token),
            )
            assert forbidden.status_code == 404

            api_v2._jobs[job_id]["status"] = "paused"
            cancelled = client.post(
                f"/api/v2/jobs/{job_id}/cancel",
                headers=_headers(owner_token),
            )
            assert cancelled.status_code == 200, cancelled.get_json()
            assert cancelled.get_json()["status"] == "cancelled"
            assert job_id not in api_v2._jobs
            assert not (Path(tmp) / "jobs" / job_id).exists()
            assert api_v2._learning_repository.get_owned_history(owner_id, job_id) is None
        finally:
            api_v2._jobs.pop(job_id, None)
            api_v2._job_runtimes.pop(job_id, None)
            running_cancel = pause = repeated_pause = history = resumed = forbidden = cancelled = client = None
            gc.collect()
            api_v2._DB_PATH = original_db
            api_v2._DATA_ROOT = original_data_root
            api_v2._SOURCE_PDF_ROOT = original_source_root


def test_resumed_job_completion_updates_the_same_history_row():
    with tempfile.TemporaryDirectory() as tmp:
        original_db = api_v2._DB_PATH
        original_data_root = api_v2._DATA_ROOT
        original_source_root = api_v2._SOURCE_PDF_ROOT
        api_v2._DB_PATH = Path(tmp) / "history.db"
        api_v2._DATA_ROOT = Path(tmp)
        api_v2._SOURCE_PDF_ROOT = Path(tmp) / "uploads" / "source_pdfs"
        api_v2._jobs.clear()
        api_v2._job_runtimes.clear()
        api_v2._init_db()
        client = api_v2.app.test_client()
        token, owner_id = _register(client, "completion-history@example.com")
        job_id = "completed-history-job"
        job, _process = _install_paused_job(tmp, owner_id, job_id)
        api_v2._job_runtimes.clear()
        job["status"] = "paused"
        try:
            assert api_v2._upsert_job_history(job, "paused")
            api_v2._apply_pipeline_event(
                job_id,
                None,
                {
                    "type": "done",
                    "result": {
                        "nodes": [{"id": 1}, {"id": 2}],
                        "edges": [{"from": 1, "to": 2}],
                    },
                },
            )
            row = api_v2._learning_repository.get_owned_history(owner_id, job_id)
            assert (row["status"], row["node_count"], row["edge_count"]) == ("done", 2, 1)
        finally:
            api_v2._cancel_job_record(job_id, owner_id)
            client = None
            gc.collect()
            api_v2._DB_PATH = original_db
            api_v2._DATA_ROOT = original_data_root
            api_v2._SOURCE_PDF_ROOT = original_source_root


def test_persisted_running_history_remains_durable_and_missing_cache_cannot_resume():
    with tempfile.TemporaryDirectory() as tmp:
        original_data_root = api_v2._DATA_ROOT
        original_source_root = api_v2._SOURCE_PDF_ROOT
        api_v2._DATA_ROOT = Path(tmp)
        api_v2._SOURCE_PDF_ROOT = Path(tmp) / "uploads" / "source_pdfs"
        client = api_v2.app.test_client()
        token, user_id = _register(client, "stale-history@example.com")
        assert api_v2._learning_repository.upsert_job_progress(
            user_id,
            JobSnapshot(
                job_id="stale-running",
                filename="input.md",
                status="running",
                nodes=[],
                edges=[],
                source_markdown="# Demo",
                latex_macros={},
                source_pdf={
                    "status": "ready",
                    "available": True,
                    "pdf_path": str(Path(tmp) / "uploads" / "input.pdf"),
                },
                stage=None,
                stage_label=None,
                stage_index=0,
                total_stages=16,
                stages_done=[],
                source_format="markdown",
                experimental_logic_ir=False,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
        )
        cache_dir = Path(tmp) / "jobs" / "stale-running" / "_stage_cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "manifest.json").write_text("{broken", encoding="utf-8")
        try:
            response = client.get("/api/v2/history", headers=_headers(token))
            item = response.get_json()[0]
            assert item["status"] == "running"
            assert item["resume_available"] is False
            stored_meta = api_v2._learning_repository.get_owned_history(
                user_id, "stale-running"
            )["source_pdf"]
            assert "pdf_path" not in stored_meta
            assert stored_meta["pdf_name"] == "input.pdf"
            assert str(Path(tmp) / "uploads") not in json.dumps(stored_meta)
            resume = client.post(
                "/api/v2/history/stale-running/resume",
                json={"llm_config": _llm_config()},
                headers=_headers(token),
            )
            assert resume.status_code == 409
        finally:
            response = resume = client = None
            gc.collect()
            api_v2._DATA_ROOT = original_data_root
            api_v2._SOURCE_PDF_ROOT = original_source_root


if __name__ == "__main__":
    test_logged_in_job_uses_persistent_artifact_directory()
    test_pause_history_cross_restart_resume_and_permanent_cancel()
    test_resumed_job_completion_updates_the_same_history_row()
    test_persisted_running_history_remains_durable_and_missing_cache_cannot_resume()
    print("paused history resume tests passed")
