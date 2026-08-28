import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .io import atomic_write_json, json_sha256, read_json
from .llm_task import active_reload_scope
from .pipeline_cache import encode_cache_value


DEFAULT_MAX_DEGRADED_FAILURE_RATIO = 0.05


class StageTaskRecoveryError(RuntimeError):
    """Raised when a fixed-pipeline stage still has unresolved LLM tasks."""

    def __init__(self, stage, report, report_path):
        self.report = dict(report or {})
        self.stage = str(stage or self.report.get("stage") or "unknown")
        self.report_path = str(report_path or self.report.get("run_dir") or "")
        self.failed_task_keys = [str(key) for key in self.report.get("failed_task_keys") or []]
        self.attempt_rounds = int(self.report.get("attempt_rounds") or 0)
        message = (
            f"Stage {self.stage} still has {len(self.failed_task_keys)} unresolved LLM task(s) "
            f"after {self.attempt_rounds} attempt round(s): {self.failed_task_keys}. "
            f"Failure report: {self.report_path}"
        )
        super().__init__(message)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def string_key_dict(value):
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def stage_runs_root(context, stage_name):
    if getattr(context, "cache_policy", "legacy") == "minimal":
        fixed_stage = getattr(context, "current_stage_key", None) or stage_name
        path = Path(context.checkpoint_root) / fixed_stage / "tasks" / stage_name
    else:
        path = Path(context.output_dir) / "agent_state" / "stage_runs" / stage_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_stage_run(context, stage_name):
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:10]}"
    run_dir = stage_runs_root(context, stage_name) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_id, run_dir


def write_input_manifest(run_dir, run_id, stage_name, input_dict, *, task_summary=None):
    tasks = []
    task_summary = task_summary or (lambda key, payload: {})
    for key, payload in (input_dict or {}).items():
        summary = task_summary(key, payload)
        if not isinstance(summary, dict):
            summary = {}
        tasks.append(
            {
                "task_key": str(key),
                "original_position": str(key),
                **summary,
            }
        )
    manifest = {
        "schema_version": 1,
        "stage": stage_name,
        "run_id": run_id,
        "created_at": utc_now(),
        "task_count": len(tasks),
        "task_keys": [item["task_key"] for item in tasks],
        "tasks": tasks,
        "input_sha256": json_sha256(encode_cache_value(input_dict or {})),
    }
    atomic_write_json(str(run_dir / "input_manifest.json"), manifest)
    atomic_write_json(str(run_dir / "input_dict.json"), input_dict or {})
    return manifest


def latest_matching_stage_run(context, stage_name, input_dict):
    expected_sha256 = json_sha256(encode_cache_value(input_dict or {}))
    matches = []
    for manifest_path in stage_runs_root(context, stage_name).glob("*/input_manifest.json"):
        try:
            manifest = read_json(str(manifest_path))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict) or manifest.get("input_sha256") != expected_sha256:
            continue
        matches.append((manifest_path.stat().st_mtime, manifest_path.parent, manifest))
    if not matches:
        return None
    _, run_dir, manifest = max(matches, key=lambda item: item[0])
    report_path = run_dir / "failure_report.json"
    report = None
    if report_path.exists():
        try:
            candidate = read_json(str(report_path))
            report = candidate if isinstance(candidate, dict) else None
        except (OSError, json.JSONDecodeError):
            report = None
    return {"run_dir": run_dir, "manifest": manifest, "report": report}


def write_failure_report(
    run_dir,
    run_id,
    stage_name,
    expected_keys,
    partial_result_dict,
    *,
    attempts,
    canonical_updated,
    result_filename="partial_result_dict.json",
):
    partial = string_key_dict(partial_result_dict)
    expected_keys = [str(key) for key in expected_keys]
    succeeded_keys = [key for key in expected_keys if key in partial]
    failed_keys = [key for key in expected_keys if key not in partial]
    report = {
        "schema_version": 1,
        "stage": stage_name,
        "run_id": run_id,
        "updated_at": utc_now(),
        "status": "resolved" if not failed_keys else "unresolved",
        "expected_task_count": len(expected_keys),
        "succeeded_task_count": len(succeeded_keys),
        "failed_task_count": len(failed_keys),
        "expected_task_keys": expected_keys,
        "succeeded_task_keys": succeeded_keys,
        "failed_task_keys": failed_keys,
        "attempt_rounds": attempts,
        "canonical_updated": canonical_updated,
        "run_dir": str(run_dir),
        "input_manifest_path": str(run_dir / "input_manifest.json"),
        "input_dict_path": str(run_dir / "input_dict.json"),
        "partial_result_dict_path": str(run_dir / result_filename),
    }
    atomic_write_json(str(run_dir / "failure_report.json"), report)
    return report


def latest_unresolved_failure_report(context, stage_name):
    reports = []
    for report_path in stage_runs_root(context, stage_name).glob("*/failure_report.json"):
        try:
            report = read_json(str(report_path))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(report, dict):
            reports.append((report_path.stat().st_mtime, report_path, report))
    if not reports:
        return None
    _, report_path, report = max(reports, key=lambda item: item[0])
    if report.get("status") != "unresolved":
        return None
    return {"path": str(report_path), "run_dir": str(report_path.parent), "report": report}


def run_recoverable_task(
    context,
    *,
    stage_name,
    input_dict,
    task_runner,
    task_summary=None,
    partial_filename="partial_result_dict.json",
):
    reusable = None
    if getattr(context, "resume_task_checkpoints", False):
        reusable = latest_matching_stage_run(context, stage_name, input_dict)

    if reusable is not None:
        run_dir = reusable["run_dir"]
        manifest = reusable["manifest"]
        run_id = manifest.get("run_id") or run_dir.name
        previous_report = reusable.get("report") or {}
        partial_path = run_dir / partial_filename
        try:
            previous_partial = (
                string_key_dict(read_json(str(partial_path)))
                if partial_path.exists()
                else {}
            )
        except (OSError, json.JSONDecodeError):
            previous_partial = {}
        if (
            previous_report.get("status") == "resolved"
            and partial_path.exists()
            and set(previous_report.get("expected_task_keys") or []) == set(manifest["task_keys"])
        ):
            return previous_partial, previous_report, run_dir
        with active_reload_scope(True):
            result_dict = task_runner(input_dict, run_dir / "checkpoint")
        partial = {**previous_partial, **string_key_dict(result_dict)}
        attempts = int(previous_report.get("attempt_rounds") or 0) + 1
    else:
        run_id, run_dir = new_stage_run(context, stage_name)
        manifest = write_input_manifest(
            run_dir,
            run_id,
            stage_name,
            input_dict,
            task_summary=task_summary,
        )
        with active_reload_scope(False):
            result_dict = task_runner(input_dict, run_dir / "checkpoint")
        partial = string_key_dict(result_dict)
        attempts = 1

    atomic_write_json(str(run_dir / partial_filename), partial)
    report = write_failure_report(
        run_dir,
        run_id,
        stage_name,
        manifest["task_keys"],
        partial,
        attempts=attempts,
        canonical_updated=False,
        result_filename=partial_filename,
    )
    return partial, report, run_dir


def rerun_unresolved_task_report(
    context,
    *,
    stage_name,
    task_runner,
    max_rounds=2,
    partial_filename="partial_result_dict.json",
):
    unresolved = latest_unresolved_failure_report(context, stage_name)
    if unresolved is None:
        raise RuntimeError(f"No unresolved failure report for stage: {stage_name}")
    report = unresolved["report"]
    run_dir = Path(unresolved["run_dir"])
    input_dict = read_json(report["input_dict_path"])
    partial_path = run_dir / partial_filename
    partial = string_key_dict(read_json(str(partial_path)) if partial_path.exists() else {})

    expected_keys = [str(key) for key in report.get("expected_task_keys") or []]
    failed_keys = [key for key in expected_keys if key not in partial]
    attempts = int(report.get("attempt_rounds") or 1)

    for round_number in range(1, max_rounds + 1):
        if not failed_keys:
            break
        selected = {key: input_dict[key] for key in failed_keys if key in input_dict}
        if not selected:
            break
        attempts += 1
        new_results = task_runner(selected, run_dir / f"rerun_checkpoint_{round_number}")
        partial.update(string_key_dict(new_results))
        atomic_write_json(str(partial_path), partial)
        failed_keys = [key for key in expected_keys if key not in partial]

    report = write_failure_report(
        run_dir,
        report.get("run_id") or run_dir.name,
        stage_name,
        expected_keys,
        partial,
        attempts=attempts,
        canonical_updated=False,
        result_filename=partial_filename,
    )
    return partial, report, run_dir


def unresolved_report_path(adapter, context):
    """Return the adapter's current unresolved report path, if any."""
    if (
        adapter is None
        or not hasattr(adapter, "latest_unresolved_failure_report")
        or not getattr(context, "output_dir", None)
    ):
        return None
    latest = adapter.latest_unresolved_failure_report(context)
    if not isinstance(latest, dict):
        return None
    path = latest.get("path")
    return str(path) if path else None


def unresolved_task_ratio(report):
    if not isinstance(report, dict):
        return None
    expected_count = int(
        report.get("expected_task_count")
        or len(report.get("expected_task_keys") or [])
        or 0
    )
    failed_count = int(
        report.get("failed_task_count")
        or len(report.get("failed_task_keys") or [])
        or 0
    )
    if expected_count <= 0 or failed_count <= 0 or failed_count > expected_count:
        return None
    return failed_count / expected_count


def can_continue_with_degraded_stage(
    report,
    max_failure_ratio=DEFAULT_MAX_DEGRADED_FAILURE_RATIO,
):
    ratio = unresolved_task_ratio(report)
    return (
        ratio is not None
        and ratio <= max_failure_ratio
        and report.get("canonical_updated") is True
    )


def mark_stage_report_degraded(
    report_path,
    report,
    *,
    max_failure_ratio=DEFAULT_MAX_DEGRADED_FAILURE_RATIO,
):
    ratio = unresolved_task_ratio(report)
    if ratio is None:
        raise ValueError("Cannot mark a report degraded without valid expected and failed task counts.")
    degraded = {
        **dict(report),
        "status": "degraded",
        "accepted_for_downstream": True,
        "unresolved_task_ratio": ratio,
        "max_degraded_failure_ratio": max_failure_ratio,
        "updated_at": utc_now(),
    }
    atomic_write_json(str(report_path), degraded)
    return degraded


def recover_failed_stage_tasks(
    context,
    state,
    adapter,
    *,
    baseline_report_path=None,
    baseline_report_updated_at=None,
    max_rounds=2,
    max_failure_ratio=DEFAULT_MAX_DEGRADED_FAILURE_RATIO,
    rerun_kwargs=None,
):
    """Resolve reports created by the current stage invocation.

    Reports that already existed before the stage started are ignored.  Some stages
    (notably relation construction) can create more than one task report, so recovery
    continues until no new unresolved report remains.
    """
    if (
        adapter is None
        or not hasattr(adapter, "rerun_failed_tasks")
        or not getattr(context, "output_dir", None)
    ):
        return state

    baseline_report_path = str(baseline_report_path) if baseline_report_path else None
    rerun_kwargs = dict(rerun_kwargs or {})
    recovered_paths = set()

    for _ in range(32):
        latest = adapter.latest_unresolved_failure_report(context)
        if not isinstance(latest, dict):
            return state
        report_path = str(latest.get("path") or "")
        report = latest.get("report") if isinstance(latest.get("report"), dict) else {}
        unchanged_baseline = (
            report_path == baseline_report_path
            and (
                baseline_report_updated_at is None
                or report.get("updated_at") == baseline_report_updated_at
            )
        )
        if not report_path or unchanged_baseline or report_path in recovered_paths:
            return state

        stage = report.get("stage") or getattr(adapter, "STAGE_NAME", None)
        state, updated_report = adapter.rerun_failed_tasks(
            context,
            state,
            max_rounds=max_rounds,
            **rerun_kwargs,
        )
        if not isinstance(updated_report, dict) or updated_report.get("status") != "resolved":
            next_latest = adapter.latest_unresolved_failure_report(context)
            next_path = str(next_latest.get("path") or "") if isinstance(next_latest, dict) else ""
            if next_path and next_path != report_path and next_path != baseline_report_path:
                recovered_paths.add(report_path)
                continue
            effective_report = updated_report if isinstance(updated_report, dict) else report
            effective_path = next_path or report_path
            if can_continue_with_degraded_stage(effective_report, max_failure_ratio):
                degraded_report = mark_stage_report_degraded(
                    effective_path,
                    effective_report,
                    max_failure_ratio=max_failure_ratio,
                )
                degraded_runs = state.setdefault("degraded_stage_runs", {})
                degraded_runs[str(stage or "unknown")] = degraded_report
                stage_run_key = f"{stage}_stage_run" if stage else None
                if stage_run_key and stage_run_key in state:
                    state[stage_run_key] = degraded_report
                print(
                    f"[stage_recovery] Continuing after stage {stage} with "
                    f"{degraded_report.get('failed_task_count')} unresolved task(s) "
                    f"({degraded_report['unresolved_task_ratio']:.2%} <= {max_failure_ratio:.2%}).",
                    flush=True,
                )
                return state
            raise StageTaskRecoveryError(
                stage,
                updated_report or report,
                next_path or report_path,
            )
        recovered_paths.add(report_path)

    raise RuntimeError("Too many distinct unresolved stage-task reports during automatic recovery.")
