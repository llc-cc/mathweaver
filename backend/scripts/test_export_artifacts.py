"""Regression checks for HTML and processing-artifact exports."""

from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import api_v2  # noqa: E402


class ExportArtifactsTest(unittest.TestCase):
    def setUp(self) -> None:
        api_v2.app.config.update(TESTING=True)
        self.client = api_v2.app.test_client()
        self.job_ids: list[str] = []

    def tearDown(self) -> None:
        for job_id in self.job_ids:
            api_v2._jobs.pop(job_id, None)

    def add_job(self, job_id: str, **overrides) -> dict:
        job = {
            "job_id": job_id,
            "status": "done",
            "filename": "sample.pdf",
            "source": "pipeline",
            "result": {
                "nodes": [{"id": 1, "node_type": "定义", "title_zh": "测试节点"}],
                "edges": [],
                "latex_macros": {},
            },
        }
        job.update(overrides)
        api_v2._jobs[job_id] = job
        self.job_ids.append(job_id)
        return job

    def test_artifact_zip_contains_only_whitelisted_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nodes_bytes = json.dumps([{"id": 1}], ensure_ascii=False).encode("utf-8")
            edges_bytes = json.dumps([{"from": 1, "to": 2}], ensure_ascii=False).encode("utf-8")
            cache_bytes = b'{"stage": "raw", "source_path": "C:/temp/input.pdf"}'

            (root / "nodes.json").write_bytes(nodes_bytes)
            (root / "edges.json").write_bytes(edges_bytes)
            nested_cache = root / "_stage_cache" / "checkpoint"
            nested_cache.mkdir(parents=True)
            (nested_cache / "checkpoint_0.json").write_bytes(cache_bytes)
            (root / "source.pdf").write_bytes(b"must not be exported")
            (root / "unrelated.json").write_text("{}", encoding="utf-8")

            self.add_job("artifact-success", _artifact_dir=temp_dir)
            response = self.client.post("/api/v2/export/artifact-success/artifacts")
            self.addCleanup(response.close)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "application/zip")
            self.assertEqual(
                response.headers.get("X-MathGraph-Export-Mode"),
                "complete",
            )
            self.assertIn(
                "sample_processing_result.zip",
                response.headers.get("Content-Disposition", ""),
            )

            with zipfile.ZipFile(io.BytesIO(response.data)) as bundle:
                names = set(bundle.namelist())
                self.assertIn("sample/nodes.json", names)
                self.assertIn("sample/edges.json", names)
                self.assertIn("sample/_stage_cache/", names)
                self.assertIn("sample/_stage_cache/checkpoint/", names)
                self.assertIn("sample/_stage_cache/checkpoint/checkpoint_0.json", names)
                self.assertNotIn("sample/source.pdf", names)
                self.assertNotIn("sample/unrelated.json", names)
                self.assertEqual(bundle.read("sample/nodes.json"), nodes_bytes)
                self.assertEqual(bundle.read("sample/edges.json"), edges_bytes)
                self.assertEqual(
                    bundle.read("sample/_stage_cache/checkpoint/checkpoint_0.json"),
                    cache_bytes,
                )

    def test_artifact_export_rejects_jobs_without_any_exportable_result(self) -> None:
        missing_response = self.client.post("/api/v2/export/does-not-exist/artifacts")
        self.assertEqual(missing_response.status_code, 404)
        self.assertEqual(missing_response.get_json()["error"], "Job not found")

        self.add_job("running-job", status="running")
        running_response = self.client.post("/api/v2/export/running-job/artifacts")
        self.assertEqual(running_response.status_code, 409)
        self.assertEqual(running_response.get_json()["error"], "Job is not complete")

        self.add_job("import-job", source="agent")
        import_response = self.client.post("/api/v2/export/import-job/artifacts")
        self.assertEqual(import_response.status_code, 409)
        self.assertIn("cache is unavailable", import_response.get_json()["error"])

        self.add_job(
            "no-result-job",
            _artifact_dir="C:/missing/task-output",
            result=None,
        )
        no_result_response = self.client.post(
            "/api/v2/export/no-result-job/artifacts"
        )
        self.assertEqual(no_result_response.status_code, 409)
        self.assertIn("unavailable", no_result_response.get_json()["error"])

    def test_missing_job_or_cache_degrades_to_nodes_and_edges_zip(self) -> None:
        fallback_nodes = [{"id": 9, "title": "fallback"}]
        fallback_edges = [{"from": 9, "to": 10}]
        missing_job_response = self.client.post(
            "/api/v2/export/restarted-job/artifacts",
            json={
                "filename": "fallback.tex",
                "nodes": fallback_nodes,
                "edges": fallback_edges,
            },
        )
        self.addCleanup(missing_job_response.close)
        self.assertEqual(missing_job_response.status_code, 200)
        self.assertEqual(
            missing_job_response.headers.get("X-MathGraph-Export-Mode"),
            "nodes-edges-only",
        )
        self.assertEqual(
            missing_job_response.headers.get("X-MathGraph-Export-Warning"),
            "processing-cache-missing",
        )
        self.assertIn(
            "fallback_nodes_edges.zip",
            missing_job_response.headers.get("Content-Disposition", ""),
        )
        with zipfile.ZipFile(io.BytesIO(missing_job_response.data)) as bundle:
            self.assertEqual(
                set(bundle.namelist()),
                {"fallback/nodes.json", "fallback/edges.json"},
            )
            self.assertEqual(
                json.loads(bundle.read("fallback/nodes.json")),
                fallback_nodes,
            )
            self.assertEqual(
                json.loads(bundle.read("fallback/edges.json")),
                fallback_edges,
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nodes_bytes = b'[{"id":1}]'
            edges_bytes = b"[]"
            (root / "nodes.json").write_bytes(nodes_bytes)
            (root / "edges.json").write_bytes(edges_bytes)
            self.add_job("missing-cache-job", _artifact_dir=temp_dir)
            degraded_response = self.client.post(
                "/api/v2/export/missing-cache-job/artifacts"
            )
            self.addCleanup(degraded_response.close)
            self.assertEqual(degraded_response.status_code, 200)
            self.assertEqual(
                degraded_response.headers.get("X-MathGraph-Export-Mode"),
                "nodes-edges-only",
            )
            with zipfile.ZipFile(io.BytesIO(degraded_response.data)) as bundle:
                self.assertEqual(
                    set(bundle.namelist()),
                    {"sample/nodes.json", "sample/edges.json"},
                )
                self.assertEqual(bundle.read("sample/nodes.json"), nodes_bytes)
                self.assertEqual(bundle.read("sample/edges.json"), edges_bytes)

        with tempfile.TemporaryDirectory() as cleaned_dir:
            missing_dir = cleaned_dir
        self.add_job("cleaned-job", _artifact_dir=missing_dir)
        cleaned_response = self.client.post("/api/v2/export/cleaned-job/artifacts")
        self.addCleanup(cleaned_response.close)
        self.assertEqual(cleaned_response.status_code, 200)
        self.assertEqual(
            cleaned_response.headers.get("X-MathGraph-Export-Mode"),
            "nodes-edges-only",
        )

    def test_status_hides_internal_artifact_directory(self) -> None:
        self.add_job("status-job", _artifact_dir="C:/private/task-output")
        response = self.client.get("/api/v2/jobs/status-job/status")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_artifact_dir", response.get_json())
        self.assertNotIn("C:/private/task-output", response.get_data(as_text=True))

    def test_existing_html_export_is_unchanged(self) -> None:
        self.add_job("html-job")
        response = self.client.post("/api/v2/export/html-job")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")
        self.assertIn("sample_mathgraph.html", response.headers["Content-Disposition"])
        self.assertIn("<!DOCTYPE html>", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
