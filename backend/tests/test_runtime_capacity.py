"""Capacity leases must protect expensive work across backend instances."""

import time

import api_v2

from runtime_capacity import ProcessCapacityPool


class _NoCapacityPool:
    def try_acquire(self):
        return None


def test_capacity_is_shared_and_released(tmp_path) -> None:
    first_pool = ProcessCapacityPool(tmp_path, "ai", 2)
    second_pool = ProcessCapacityPool(tmp_path, "ai", 2)

    first = first_pool.try_acquire()
    second = second_pool.try_acquire()
    assert first is not None
    assert second is not None
    assert {first.slot, second.slot} == {0, 1}
    assert second_pool.try_acquire() is None

    first.release()
    replacement = second_pool.try_acquire()
    assert replacement is not None
    assert replacement.slot == first.slot

    # Cleanup is intentionally idempotent because monitor and cancellation
    # paths can race while a child process exits.
    first.release()
    replacement.release()
    second.release()


def test_pipeline_submission_returns_retryable_busy_response(
    monkeypatch, tmp_path,
) -> None:
    before = set(api_v2._jobs)
    monkeypatch.setattr(api_v2, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(api_v2, "_PIPELINE_CAPACITY_POOL", _NoCapacityPool())

    response = api_v2.app.test_client().post(
        "/api/v2/jobs",
        json={
            "text": "# Demo",
            "filename": "demo.md",
            "api_url": "https://example.test/v1",
            "model_name": "test-model",
            "api_key": "test-key",
            "embedding_model": "test-embedding",
        },
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == str(api_v2._CAPACITY_RETRY_AFTER)
    assert response.get_json()["code"] == "pipeline_capacity_busy"
    assert set(api_v2._jobs) == before


def test_education_capacity_rejects_before_claiming_quota(monkeypatch) -> None:
    monkeypatch.setattr(api_v2, "_AI_CAPACITY_POOL", _NoCapacityPool())
    monkeypatch.setattr(
        api_v2,
        "_education_llm_config",
        lambda _user_id: {
            "api_url": "https://example.test/v1",
            "model_name": "test-model",
            "api_key": "test-key",
        },
    )
    claimed = []
    monkeypatch.setattr(
        api_v2,
        "_education_consume_ai_quota",
        lambda *_args, **_kwargs: claimed.append(True),
    )

    try:
        api_v2._education_ai_tasks(
            user_id=1,
            task_id="busy-task",
            task_kind="proof_assist",
            tasks={"busy-task": {}},
            scope="test",
        )
    except api_v2.EducationAIError as error:
        assert error.code == "education_ai_busy"
        assert error.status == 429
    else:
        raise AssertionError("busy education AI request was not rejected")
    assert claimed == []


def test_terminal_job_registry_is_pruned_without_removing_persisted_artifacts(
    monkeypatch,
) -> None:
    job_id = "old-persisted-job"
    monkeypatch.setattr(api_v2, "_JOB_MEMORY_TTL_SECONDS", 60)
    api_v2._jobs[job_id] = {
        "job_id": job_id,
        "status": "done",
        "_finished_at": time.time() - 61,
        "_persistent_artifacts": True,
    }
    removed_artifacts = []
    monkeypatch.setattr(
        api_v2,
        "_remove_job_artifacts",
        lambda *_args: removed_artifacts.append(True),
    )

    try:
        assert api_v2._prune_terminal_jobs() == 1
        assert job_id not in api_v2._jobs
        assert removed_artifacts == []
    finally:
        api_v2._jobs.pop(job_id, None)
