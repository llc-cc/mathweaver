import base64
import gc
import io
import json
import os
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import api_v2
from integrations.neo4j_handler import get_graph_store, reset_graph_store
from scripts.migrate_storage import TARGET_TABLE_ORDER
from storage.database import reset_engine


class FakeProcess:
    def __init__(self):
        self.alive = True

    def is_alive(self):
        return self.alive


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
        "source_origin": "markdown",
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


class PausedHistoryResumeTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get("MATHWEAVER_INTEGRATION_TESTS") != "1":
            self.skipTest("set MATHWEAVER_INTEGRATION_TESTS=1 for Docker MySQL/Neo4j tests")
        database_url = os.environ.get("MATHWEAVER_TEST_DATABASE_URL", "").strip()
        neo4j_uri = os.environ.get("MATHWEAVER_TEST_NEO4J_URI", "").strip()
        neo4j_password_file = os.environ.get("MATHWEAVER_TEST_NEO4J_PASSWORD_FILE", "").strip()
        if not database_url or not neo4j_uri or not neo4j_password_file:
            self.skipTest("test MySQL/Neo4j credentials are not configured")

        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp_dir.name)
        data_key_file = self.root / "data-key.txt"
        data_key_file.write_text(
            base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("="),
            encoding="utf-8",
        )
        self.previous = (
            api_v2._DATA_ROOT,
            api_v2._SOURCE_PDF_ROOT,
            api_v2._EDUCATION_ROOT,
            api_v2._EDUCATION_SNAPSHOT_ROOT,
            api_v2._EDUCATION_ASSIGNMENT_SOURCE_ROOT,
        )
        api_v2._DATA_ROOT = self.root
        api_v2._SOURCE_PDF_ROOT = self.root / "uploads" / "source_pdfs"
        api_v2._EDUCATION_ROOT = self.root / "education"
        api_v2._EDUCATION_SNAPSHOT_ROOT = self.root / "education" / "snapshots"
        api_v2._EDUCATION_ASSIGNMENT_SOURCE_ROOT = self.root / "education" / "assignment_sources"
        self.env = patch.dict(
            os.environ,
            {
                "DATABASE_URL": database_url,
                "NEO4J_URI": neo4j_uri,
                "NEO4J_USER": os.environ.get("MATHWEAVER_TEST_NEO4J_USER", "neo4j"),
                "NEO4J_PASSWORD_FILE": neo4j_password_file,
                "MATHWEAVER_DATA_KEY_FILE": str(data_key_file),
                "MATHGRAPH_DATA_DIR": str(self.root),
            },
            clear=False,
        )
        self.env.start()
        reset_engine()
        reset_graph_store()
        self._clear_storage()
        api_v2._jobs.clear()
        api_v2._job_runtimes.clear()
        api_v2.app.config.update(TESTING=True)
        self.client = api_v2.app.test_client()

    def tearDown(self):
        if not hasattr(self, "env"):
            return
        api_v2._jobs.clear()
        api_v2._job_runtimes.clear()
        try:
            self._clear_storage()
        finally:
            reset_graph_store()
            reset_engine()
            self.env.stop()
            (
                api_v2._DATA_ROOT,
                api_v2._SOURCE_PDF_ROOT,
                api_v2._EDUCATION_ROOT,
                api_v2._EDUCATION_SNAPSHOT_ROOT,
                api_v2._EDUCATION_ASSIGNMENT_SOURCE_ROOT,
            ) = self.previous
            self.temp_dir.cleanup()
            gc.collect()

    def _clear_storage(self):
        with api_v2.connect_database() as connection:
            connection.execute("SET FOREIGN_KEY_CHECKS = 0")
            for table in reversed((*TARGET_TABLE_ORDER, "graph_registry")):
                connection.execute(f"DELETE FROM `{table}`")
            connection.execute("SET FOREIGN_KEY_CHECKS = 1")
        store = get_graph_store()
        with store.driver.session(database=store.database) as session:
            session.run("MATCH (n) DETACH DELETE n").consume()

    def _register(self, email):
        response = self.client.post(
            "/api/v2/auth/register",
            json={"email": email, "password": "password123"},
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["token"]

    @staticmethod
    def _headers(token):
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _json_object(value):
        if isinstance(value, dict):
            return value
        return json.loads(value or "{}")

    def _user_id(self, email):
        with api_v2.connect_database() as connection:
            return int(connection.execute(
                "SELECT id FROM users WHERE email = ?",
                (email,),
            ).fetchone()[0])

    def test_logged_in_job_uses_persistent_artifact_directory(self):
        token = self._register("persistent-job@example.com")
        with patch.object(api_v2, "_start_pipeline_attempt"):
            response = self.client.post(
                "/api/v2/jobs",
                data={
                    "file": (io.BytesIO(b"# Demo"), "demo.md"),
                    **_llm_config(),
                },
                headers=self._headers(token),
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 202, response.get_json())
        job_id = response.get_json()["job_id"]
        job = api_v2._jobs[job_id]
        self.assertEqual(Path(job["_artifact_dir"]), self.root / "jobs" / job_id)
        self.assertIsNotNone(job["_user_id"])

    def test_pause_history_cross_restart_resume_and_permanent_cancel(self):
        owner_token = self._register("history-owner@example.com")
        other_token = self._register("history-other@example.com")
        owner_id = self._user_id("history-owner@example.com")
        job_id = "paused-history-job"
        job, process = _install_paused_job(
            self.root,
            owner_id,
            job_id,
            experimental_logic_ir=True,
        )

        running_cancel = self.client.post(
            f"/api/v2/jobs/{job_id}/cancel",
            headers=self._headers(owner_token),
        )
        self.assertEqual(running_cancel.status_code, 409)

        def terminate(selected):
            self.assertIs(selected, process)
            selected.alive = False

        with patch.object(api_v2, "_terminate_pipeline_process", side_effect=terminate):
            pause = self.client.post(
                f"/api/v2/jobs/{job_id}/pause",
                headers=self._headers(owner_token),
            )
        self.assertEqual(pause.status_code, 200, pause.get_json())
        self.assertEqual(job["status"], "paused")

        history = self.client.get("/api/v2/history", headers=self._headers(owner_token))
        self.assertEqual(history.status_code, 200)
        item = history.get_json()[0]
        self.assertEqual(item["id"], job_id)
        self.assertEqual(item["status"], "paused")
        self.assertEqual(item["stages_done"], ["correct_text"])
        self.assertTrue(item["experimental_logic_ir"])
        self.assertTrue(item["resume_available"])

        repeated_pause = self.client.post(
            f"/api/v2/jobs/{job_id}/pause",
            headers=self._headers(owner_token),
        )
        self.assertEqual(repeated_pause.status_code, 200)
        with api_v2.connect_database() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM history WHERE id = ?",
                (job_id,),
            ).fetchone()[0], 1)
            persisted = {
                "history": [dict(row.items()) for row in connection.execute(
                    "SELECT * FROM history WHERE id = ?", (job_id,)
                ).fetchall()],
                "registry": [dict(row.items()) for row in connection.execute(
                    "SELECT * FROM graph_registry WHERE graph_id = ?", (job_id,)
                ).fetchall()],
            }
        persisted_text = json.dumps(persisted, ensure_ascii=False, default=str)
        self.assertNotIn("top-secret", persisted_text)
        self.assertNotIn("embedding-secret", persisted_text)
        self.assertNotIn(str(self.root / "jobs"), persisted_text)

        api_v2._jobs.clear()
        api_v2._job_runtimes.clear()
        started = []
        with patch.object(
            api_v2,
            "_start_pipeline_attempt",
            side_effect=lambda selected, *, resume: started.append((selected, resume)),
        ):
            resumed = self.client.post(
                f"/api/v2/history/{job_id}/resume",
                json={"llm_config": _llm_config()},
                headers=self._headers(owner_token),
            )
        self.assertEqual(resumed.status_code, 202, resumed.get_json())
        self.assertEqual(resumed.get_json()["job"]["job_id"], job_id)
        self.assertEqual(started, [(job_id, True)])
        self.assertEqual(api_v2._jobs[job_id]["_llm_config"]["api_key"], "top-secret")
        self.assertTrue(api_v2._jobs[job_id]["_experimental_logic_ir"])
        self.assertEqual(api_v2._jobs[job_id]["total_stages"], 16)
        with api_v2.connect_database() as connection:
            self.assertEqual(connection.execute(
                "SELECT status FROM history WHERE id = ?", (job_id,)
            ).fetchone()[0], "running")

        forbidden = self.client.post(
            f"/api/v2/jobs/{job_id}/cancel",
            headers=self._headers(other_token),
        )
        self.assertEqual(forbidden.status_code, 403)

        api_v2._jobs[job_id]["status"] = "paused"
        cancelled = self.client.post(
            f"/api/v2/jobs/{job_id}/cancel",
            headers=self._headers(owner_token),
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.get_json())
        self.assertEqual(cancelled.get_json()["status"], "cancelled")
        self.assertNotIn(job_id, api_v2._jobs)
        self.assertFalse((self.root / "jobs" / job_id).exists())
        with api_v2.connect_database() as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM history WHERE id = ?", (job_id,)
            ).fetchone())
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM graph_registry WHERE graph_id = ?", (job_id,)
            ).fetchone())
        self.assertIsNone(get_graph_store().get_graph(job_id))

    def test_resumed_job_completion_updates_the_same_history_row(self):
        self._register("completion-history@example.com")
        owner_id = self._user_id("completion-history@example.com")
        job_id = "completed-history-job"
        job, _process = _install_paused_job(self.root, owner_id, job_id)
        api_v2._job_runtimes.clear()
        job["status"] = "paused"
        self.assertTrue(api_v2._upsert_job_history(job, "paused"))

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
        with api_v2.connect_database() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS row_count, status, node_count, edge_count FROM history WHERE id = ?",
                (job_id,),
            ).fetchone()
        self.assertEqual((row["row_count"], row["status"], row["node_count"], row["edge_count"]), (1, "done", 2, 1))
        graph = api_v2.load_stored_graph(job_id)
        self.assertEqual(graph["nodes"], [{"id": 1}, {"id": 2}])
        self.assertEqual(graph["edges"], [{"from": 1, "to": 2}])

    def test_stale_running_history_becomes_paused_and_missing_cache_is_disabled(self):
        token = self._register("stale-history@example.com")
        user_id = self._user_id("stale-history@example.com")
        now = datetime.utcnow().isoformat()
        with api_v2.connect_database() as connection:
            connection.execute(
                """INSERT INTO history
                     (id, user_id, filename, node_count, edge_count, source_markdown,
                      source_pdf_json, status, total_stages, stages_done_json, source_format,
                      updated_at, created_at)
                   VALUES (?, ?, ?, 0, 0, ?, ?, 'running', 16, '[]', 'markdown', ?, ?)""",
                (
                    "stale-running",
                    user_id,
                    "input.md",
                    "# Demo",
                    json.dumps({
                        "status": "ready",
                        "available": True,
                        "pdf_path": str(self.root / "uploads" / "input.pdf"),
                    }),
                    now,
                    now,
                ),
            )
        api_v2.persist_graph("stale-running", "history", [], [])
        cache_dir = self.root / "jobs" / "stale-running" / "_stage_cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "manifest.json").write_text("{broken", encoding="utf-8")

        self.assertEqual(api_v2.reconcile_interrupted_history(), 1)
        response = self.client.get("/api/v2/history", headers=self._headers(token))
        self.assertEqual(response.status_code, 200, response.get_json())
        item = response.get_json()[0]
        self.assertEqual(item["status"], "paused")
        self.assertFalse(item["resume_available"])
        with api_v2.connect_database() as connection:
            stored_meta = connection.execute(
                "SELECT source_pdf_json FROM history WHERE id = 'stale-running'"
            ).fetchone()[0]
        stored_meta = self._json_object(stored_meta)
        self.assertNotIn("pdf_path", stored_meta)
        self.assertEqual(stored_meta["pdf_name"], "input.pdf")
        self.assertNotIn(str(self.root / "uploads"), json.dumps(stored_meta))

        resume = self.client.post(
            "/api/v2/history/stale-running/resume",
            json={"llm_config": _llm_config()},
            headers=self._headers(token),
        )
        self.assertEqual(resume.status_code, 410)


if __name__ == "__main__":
    unittest.main()
