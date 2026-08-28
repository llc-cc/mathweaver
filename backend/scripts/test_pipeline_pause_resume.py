import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import api_v2


class _HttpError(Exception):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def _heartbeat_worker(path):
    target = Path(path)
    while True:
        target.write_text(str(time.time()), encoding="utf-8")
        time.sleep(0.05)


def _wait_for(path, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if Path(path).exists():
            return True
        time.sleep(0.02)
    return Path(path).exists()


def _job(root, job_id, status):
    source = Path(root) / "input.md"
    source.write_text("# Demo", encoding="utf-8")
    cache = Path(root) / "_stage_cache"
    cache.mkdir()
    (cache / "manifest.json").write_text(
        json.dumps({
            "schema_version": 1,
            "status": status,
            "source": {"sha256": "demo"},
            "plan_sha256": "demo",
        }),
        encoding="utf-8",
    )
    work = Path(root) / "_stage_work"
    work.mkdir()
    (work / "debug.json").write_text("{}", encoding="utf-8")
    return {
        "job_id": job_id,
        "status": status,
        "filename": "input.md",
        "stage": "demo",
        "stage_label": "Demo",
        "stage_index": 0,
        "total_stages": len(api_v2.STAGE_DEFS),
        "stages_done": [],
        "result": None,
        "error": "failed" if status == "error" else None,
        "source_markdown": "# Demo",
        "source_format": "markdown",
        "source_pdf": None,
        "source": "pipeline",
        "_artifact_dir": root,
        "_md_path": str(source),
        "_llm_config": {
            "api_url": "https://example.test/v1",
            "model_name": "test",
            "api_key": "top-secret",
            "embedding_url": "https://example.test/v1",
            "embedding_model": "embedding-test",
            "embedding_api_key": "embedding-secret",
        },
        "_attempt_token": "attempt-1",
        "error_detail": "internal traceback at C:\\private\\worker.py",
    }


def test_pause_terminates_worker_and_cleans_transient_files():
    with tempfile.TemporaryDirectory() as tmp:
        job_id = "pause-process-test"
        heartbeat = Path(tmp) / "heartbeat.txt"
        context = multiprocessing.get_context("spawn")
        process = context.Process(target=_heartbeat_worker, args=(str(heartbeat),))
        process.start()
        assert _wait_for(heartbeat)

        api_v2._jobs[job_id] = _job(tmp, job_id, "running")
        api_v2._job_runtimes[job_id] = {
            "attempt_token": "attempt-1",
            "process": process,
            "pause_requested": False,
        }
        try:
            response = api_v2.app.test_client().post(f"/api/v2/jobs/{job_id}/pause")
            assert response.status_code == 200, response.get_json()
            assert response.get_json()["status"] == "paused"
            assert not process.is_alive()
            assert api_v2._jobs[job_id]["status"] == "paused"
            assert not (Path(tmp) / "_stage_work").exists()
            manifest = json.loads(
                (Path(tmp) / "_stage_cache" / "manifest.json").read_text(encoding="utf-8")
            )
            assert manifest["status"] == "paused"

            status = api_v2.app.test_client().get(f"/api/v2/jobs/{job_id}/status")
            body = status.get_json()
            serialized = json.dumps(body)
            assert status.status_code == 200
            assert "_artifact_dir" not in body
            assert "_llm_config" not in body
            assert "error_detail" not in body
            assert "top-secret" not in serialized
            assert "embedding-secret" not in serialized
        finally:
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            api_v2._jobs.pop(job_id, None)
            api_v2._job_runtimes.pop(job_id, None)


def test_resume_reuses_same_job_for_paused_and_failed_states():
    for original_status in ("paused", "error"):
        with tempfile.TemporaryDirectory() as tmp:
            job_id = f"resume-{original_status}-test"
            api_v2._jobs[job_id] = _job(tmp, job_id, original_status)
            api_v2._job_runtimes.pop(job_id, None)
            captured = []
            try:
                with patch.object(
                    api_v2,
                    "_start_pipeline_attempt",
                    side_effect=lambda selected_job_id, *, resume: captured.append(
                        (selected_job_id, resume)
                    ),
                ):
                    response = api_v2.app.test_client().post(
                        f"/api/v2/jobs/{job_id}/resume"
                    )
                assert response.status_code == 202, response.get_json()
                assert response.get_json()["job_id"] == job_id
                assert api_v2._jobs[job_id]["status"] == "running"
                assert captured == [(job_id, True)]
            finally:
                api_v2._jobs.pop(job_id, None)
                api_v2._job_runtimes.pop(job_id, None)


def test_pausing_one_job_does_not_stop_another_worker():
    with tempfile.TemporaryDirectory() as tmp:
        first_root = Path(tmp) / "first"
        second_root = Path(tmp) / "second"
        first_root.mkdir()
        second_root.mkdir()
        first_heartbeat = first_root / "heartbeat.txt"
        second_heartbeat = second_root / "heartbeat.txt"
        context = multiprocessing.get_context("spawn")
        first_process = context.Process(
            target=_heartbeat_worker,
            args=(str(first_heartbeat),),
        )
        second_process = context.Process(
            target=_heartbeat_worker,
            args=(str(second_heartbeat),),
        )
        first_process.start()
        second_process.start()
        assert _wait_for(first_heartbeat)
        assert _wait_for(second_heartbeat)

        first_id = "isolated-first"
        second_id = "isolated-second"
        api_v2._jobs[first_id] = _job(str(first_root), first_id, "running")
        api_v2._jobs[second_id] = _job(str(second_root), second_id, "running")
        api_v2._job_runtimes[first_id] = {
            "attempt_token": "attempt-1",
            "process": first_process,
            "pause_requested": False,
        }
        api_v2._job_runtimes[second_id] = {
            "attempt_token": "attempt-1",
            "process": second_process,
            "pause_requested": False,
        }
        try:
            response = api_v2.app.test_client().post(
                f"/api/v2/jobs/{first_id}/pause"
            )
            assert response.status_code == 200
            assert not first_process.is_alive()
            assert second_process.is_alive()
            assert api_v2._jobs[second_id]["status"] == "running"
        finally:
            for process in (first_process, second_process):
                if process.is_alive():
                    process.kill()
                process.join(timeout=5)
            for job_id in (first_id, second_id):
                api_v2._jobs.pop(job_id, None)
                api_v2._job_runtimes.pop(job_id, None)


def test_pause_and_resume_reject_invalid_states_and_stale_events():
    with tempfile.TemporaryDirectory() as tmp:
        job_id = "invalid-state-test"
        api_v2._jobs[job_id] = _job(tmp, job_id, "done")
        try:
            pause = api_v2.app.test_client().post(f"/api/v2/jobs/{job_id}/pause")
            resume = api_v2.app.test_client().post(f"/api/v2/jobs/{job_id}/resume")
            assert pause.status_code == 409
            assert resume.status_code == 409

            applied = api_v2._apply_pipeline_event(
                job_id,
                "stale-attempt",
                {"type": "error", "error": "must be ignored"},
            )
            assert applied is False
            assert api_v2._jobs[job_id]["status"] == "done"
        finally:
            api_v2._jobs.pop(job_id, None)
            api_v2._job_runtimes.pop(job_id, None)


def test_job_error_classification_covers_actionable_categories():
    cases = (
        (_HttpError("incorrect API key", 401), "api_config"),
        (_HttpError("rate limit exceeded", 429), "service_limit"),
        (TimeoutError("request timed out"), "network"),
        (ValueError("invalid JSON response format"), "model_response"),
        (ValueError("No content provided"), "document_input"),
        (
            RuntimeError(
                "Stage build_relations did not produce required downstream state "
                "(missing: edge_list). Check the stage cache and failure report."
            ),
            "pipeline_stage",
        ),
        (RuntimeError("unexpected invariant"), "internal"),
    )
    for error, expected_code in cases:
        result = api_v2._classify_job_error(
            error,
            stage="build_relations",
            stage_label="关系图谱",
        )
        assert result["error_code"] == expected_code, (error, result)
        assert result["error_title"]
        assert result["error"]

    stage_result = api_v2._classify_job_error(
        cases[5][0],
        stage="build_relations",
        stage_label="关系图谱",
    )
    assert stage_result["error_title"] == "关系图谱阶段未能完成"


def test_status_is_friendly_and_error_detail_is_lazy_protected_and_redacted():
    with tempfile.TemporaryDirectory() as tmp:
        job_id = "classified-error-test"
        job = _job(tmp, job_id, "error")
        job["stage"] = "build_relations"
        job["stage_label"] = "关系图谱"
        job["error"] = (
            "Stage build_relations did not produce required downstream state "
            "(missing: edge_list); secret=top-secret"
        )
        job["error_detail"] = (
            "Traceback at C:\\private\\worker.py\n"
            "embedding-secret\n"
            + job["error"]
        )
        job["_user_id"] = 7
        api_v2._jobs[job_id] = job
        client = api_v2.app.test_client()
        try:
            status = client.get(f"/api/v2/jobs/{job_id}/status")
            body = status.get_json()
            serialized = json.dumps(body, ensure_ascii=False)
            assert status.status_code == 200
            assert body["error_code"] == "pipeline_stage"
            assert body["error_title"] == "关系图谱阶段未能完成"
            assert "下游" in body["error"] or "后续处理" in body["error"]
            assert "missing: edge_list" not in serialized
            assert "Traceback" not in serialized
            assert "C:\\private" not in serialized
            assert "top-secret" not in serialized
            assert "embedding-secret" not in serialized

            with patch.object(api_v2, "_current_user", return_value={"id": 8}):
                forbidden = client.get(f"/api/v2/jobs/{job_id}/error-detail")
            assert forbidden.status_code == 403

            with patch.object(api_v2, "_current_user", return_value={"id": 7}):
                detail_response = client.get(f"/api/v2/jobs/{job_id}/error-detail")
            detail_body = detail_response.get_json()
            detail_serialized = json.dumps(detail_body, ensure_ascii=False)
            assert detail_response.status_code == 200
            assert "missing: edge_list" in detail_body["message"]
            assert "Traceback" in detail_body["detail"]
            assert "top-secret" not in detail_serialized
            assert "embedding-secret" not in detail_serialized
            assert "***" in detail_serialized

            job["status"] = "running"
            with patch.object(api_v2, "_current_user", return_value={"id": 7}):
                unavailable = client.get(f"/api/v2/jobs/{job_id}/error-detail")
            assert unavailable.status_code == 409

            job["status"] = "error"
            job["error"] = ""
            job["error_detail"] = ""
            with patch.object(api_v2, "_current_user", return_value={"id": 7}):
                empty_detail = client.get(f"/api/v2/jobs/{job_id}/error-detail")
            assert empty_detail.status_code == 409

            missing = client.get("/api/v2/jobs/missing-job/error-detail")
            assert missing.status_code == 404
        finally:
            api_v2._jobs.pop(job_id, None)
            api_v2._job_runtimes.pop(job_id, None)


def run_tests():
    multiprocessing.freeze_support()
    test_pause_terminates_worker_and_cleans_transient_files()
    test_resume_reuses_same_job_for_paused_and_failed_states()
    test_pausing_one_job_does_not_stop_another_worker()
    test_pause_and_resume_reject_invalid_states_and_stale_events()
    test_job_error_classification_covers_actionable_categories()
    test_status_is_friendly_and_error_detail_is_lazy_protected_and_redacted()
    print("pipeline pause/resume tests passed")


if __name__ == "__main__":
    run_tests()
