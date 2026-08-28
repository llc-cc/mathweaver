import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline import orchestrator
from pipeline.common.stage_recovery import StageTaskRecoveryError
from pipeline.stages.clean_nodes import stage as clean_nodes_stage
from pipeline.stages.correct_text import stage as correct_text_stage
from pipeline.stages.ensure_coverage import stage as ensure_coverage_stage
from pipeline.stages.segment_blocks import stage as segment_blocks_stage


def _context(tmp, source_path=None):
    return SimpleNamespace(
        output_dir=str(tmp),
        checkpoint_root=str(Path(tmp) / "checkpoint"),
        file_path=str(source_path) if source_path else None,
        source_format="markdown",
        execution_mode="pipeline",
        output_edge_path=None,
        num_threads=2,
        checkpoint=10,
        llm=None,
        parser=None,
    )


class _FakeAdapter:
    STAGE_NAME = "fake_llm_stage"

    def __init__(self, *, persistent=False):
        self.current = None
        self.calls = []
        self.persistent = persistent

    def latest_unresolved_failure_report(self, _context):
        return self.current

    def rerun_failed_tasks(self, _context, state, max_rounds=2):
        self.calls.append((list(self.current["report"]["failed_task_keys"]), max_rounds))
        report = dict(self.current["report"])
        if self.persistent:
            report["attempt_rounds"] = 3
            return state, report
        self.current = None
        state["value"] = "recovered"
        return state, {**report, "status": "resolved", "failed_task_keys": [], "canonical_updated": True}


def test_fixed_executor_recovers_before_completing_stage():
    adapter = _FakeAdapter()
    completed = []

    def run(_context, state):
        adapter.current = {
            "path": "new-report.json",
            "report": {
                "stage": "fake_llm_stage",
                "status": "unresolved",
                "failed_task_keys": ["1"],
                "attempt_rounds": 1,
            },
        }
        state["value"] = "partial"
        return state

    stage = orchestrator.FixedStage(
        "fake_llm_stage",
        "fake",
        run,
        produces=("value",),
        nonempty=("value",),
        recovery_adapter=adapter,
    )
    with tempfile.TemporaryDirectory() as tmp, patch.object(
        orchestrator, "build_fixed_stage_plan", return_value=(stage,)
    ):
        result = orchestrator.execute_fixed_pipeline(
            _context(tmp),
            on_stage_complete=lambda item, *_args: completed.append(item.key),
        )

    assert result["value"] == "recovered"
    assert adapter.calls == [(["1"], 2)]
    assert completed == ["fake_llm_stage"]


def test_fixed_executor_stops_after_unresolved_reruns():
    adapter = _FakeAdapter(persistent=True)
    completed = []

    def run(_context, state):
        adapter.current = {
            "path": "persistent-report.json",
            "report": {
                "stage": "fake_llm_stage",
                "status": "unresolved",
                "expected_task_count": 20,
                "succeeded_task_count": 18,
                "failed_task_count": 2,
                "expected_task_keys": [str(index) for index in range(20)],
                "succeeded_task_keys": [str(index) for index in range(18)],
                "failed_task_keys": ["18", "19"],
                "attempt_rounds": 1,
            },
        }
        state["value"] = "partial"
        return state

    stage = orchestrator.FixedStage(
        "fake_llm_stage",
        "fake",
        run,
        produces=("value",),
        recovery_adapter=adapter,
    )
    with tempfile.TemporaryDirectory() as tmp, patch.object(
        orchestrator, "build_fixed_stage_plan", return_value=(stage,)
    ):
        try:
            orchestrator.execute_fixed_pipeline(
                _context(tmp),
                on_stage_complete=lambda item, *_args: completed.append(item.key),
            )
        except StageTaskRecoveryError as exc:
            assert exc.stage == "fake_llm_stage"
            assert exc.failed_task_keys == ["18", "19"]
            assert exc.attempt_rounds == 3
            assert "persistent-report.json" in str(exc)
        else:
            raise AssertionError("Expected persistent task failure to stop the fixed pipeline")
    assert completed == []


def test_fixed_executor_continues_with_small_degraded_failure_ratio():
    adapter = _FakeAdapter(persistent=True)
    completed = []

    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "degraded-report.json"
        report = {
            "stage": "fake_llm_stage",
            "status": "unresolved",
            "expected_task_count": 20,
            "succeeded_task_count": 19,
            "failed_task_count": 1,
            "expected_task_keys": [str(index) for index in range(20)],
            "succeeded_task_keys": [str(index) for index in range(19)],
            "failed_task_keys": ["19"],
            "attempt_rounds": 1,
            "canonical_updated": True,
        }
        report_path.write_text(json.dumps(report), encoding="utf-8")

        def run(_context, state):
            adapter.current = {"path": str(report_path), "report": report}
            state["value"] = "partial-canonical-output"
            return state

        stage = orchestrator.FixedStage(
            "fake_llm_stage",
            "fake",
            run,
            produces=("value",),
            nonempty=("value",),
            recovery_adapter=adapter,
        )
        with patch.object(orchestrator, "build_fixed_stage_plan", return_value=(stage,)):
            result = orchestrator.execute_fixed_pipeline(
                _context(tmp),
                on_stage_complete=lambda item, *_args: completed.append(item.key),
            )

        persisted = json.loads(report_path.read_text(encoding="utf-8"))

    assert result["value"] == "partial-canonical-output"
    assert persisted["status"] == "degraded"
    assert persisted["accepted_for_downstream"] is True
    assert persisted["unresolved_task_ratio"] == 0.05
    assert result["degraded_stage_runs"]["fake_llm_stage"]["failed_task_count"] == 1
    assert completed == ["fake_llm_stage"]


def test_fixed_executor_rejects_degraded_report_without_canonical_output():
    adapter = _FakeAdapter(persistent=True)

    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "incomplete-report.json"
        report = {
            "stage": "fake_llm_stage",
            "status": "unresolved",
            "expected_task_count": 20,
            "succeeded_task_count": 19,
            "failed_task_count": 1,
            "expected_task_keys": [str(index) for index in range(20)],
            "succeeded_task_keys": [str(index) for index in range(19)],
            "failed_task_keys": ["19"],
            "attempt_rounds": 1,
            "canonical_updated": False,
        }
        report_path.write_text(json.dumps(report), encoding="utf-8")

        def run(_context, state):
            adapter.current = {"path": str(report_path), "report": report}
            return state

        stage = orchestrator.FixedStage(
            "fake_llm_stage",
            "fake",
            run,
            produces=("value",),
            recovery_adapter=adapter,
        )
        with patch.object(orchestrator, "build_fixed_stage_plan", return_value=(stage,)):
            try:
                orchestrator.execute_fixed_pipeline(_context(tmp))
            except StageTaskRecoveryError as exc:
                assert exc.failed_task_keys == ["19"]
            else:
                raise AssertionError("Incomplete canonical state must not be accepted as degraded")

        persisted = json.loads(report_path.read_text(encoding="utf-8"))

    assert persisted["status"] == "unresolved"
    assert "accepted_for_downstream" not in persisted


def test_fixed_executor_ignores_stale_unresolved_report():
    adapter = _FakeAdapter()
    adapter.current = {
        "path": "stale-report.json",
        "report": {"stage": "fake_llm_stage", "status": "unresolved", "failed_task_keys": ["old"]},
    }
    stage = orchestrator.FixedStage(
        "fake_llm_stage",
        "fake",
        lambda _context, state: {**state, "value": "fresh"},
        produces=("value",),
        recovery_adapter=adapter,
    )
    with tempfile.TemporaryDirectory() as tmp, patch.object(
        orchestrator, "build_fixed_stage_plan", return_value=(stage,)
    ):
        result = orchestrator.execute_fixed_pipeline(_context(tmp))
    assert result["value"] == "fresh"
    assert adapter.calls == []


def test_correct_text_reruns_only_missing_batch():
    calls = []
    source_units = {"0": "Alpha.\n\n", "1": "Beta.\n\n"}
    batches = [{"0": source_units["0"]}, {"1": source_units["1"]}]

    def candidate(tasks):
        return {
            str(key): {"corrected_units": json.loads(task["target_units"]), "warnings": []}
            for key, task in tasks.items()
        }

    def fake_tasks(_context, tasks, _checkpoint_dir):
        calls.append(list(tasks))
        results = candidate(tasks)
        if len(calls) == 1:
            results.pop("1", None)
        return results

    with tempfile.TemporaryDirectory() as tmp:
        source_path = Path(tmp) / "input.md"
        source_path.write_text("Alpha.\n\nBeta.\n\n", encoding="utf-8")
        context = _context(tmp, source_path)
        with patch.object(correct_text_stage, "build_structure_preserving_units", return_value=source_units), patch.object(
            correct_text_stage, "_batch_units", return_value=batches
        ), patch.object(correct_text_stage, "_run_batch_tasks", side_effect=fake_tasks):
            state = correct_text_stage.run(context, {})
            state, report = correct_text_stage.rerun_failed_tasks(context, state, max_rounds=2)

    assert calls == [["0", "1"], ["1"]]
    assert report["status"] == "resolved"
    assert report["attempt_rounds"] == 2
    assert state["correct_text_report"]["fallback_unit_count"] == 0


def test_segment_blocks_and_clean_nodes_recover_complete_chunks():
    segment_calls = []
    chopped = {"0": {"pos1": {"0": 'r"""Theorem 1. A statement.\n\n"""'}}}

    def segment_tasks(_context, tasks, _checkpoint_dir):
        segment_calls.append(list(tasks))
        if len(segment_calls) == 1:
            return {}
        output = {}
        for key, task in tasks.items():
            chunk = json.loads(task["unit_packet"])
            output[str(key)] = segment_blocks_stage._fallback_classifications(chunk, "test recovery")
        return output

    clean_calls = []

    def clean_tasks(_context, tasks, _checkpoint_dir):
        clean_calls.append(list(tasks))
        if len(clean_calls) == 1:
            return {}
        return {
            str(chunk_key): {
                str(item["key"]): {
                    "action": "manual_review",
                    "reason": "Valid conservative decision.",
                    "confidence": "low",
                    "evidence": ["review"],
                }
                for item in task["pos1"]["nodes"]
            }
            for chunk_key, task in tasks.items()
        }

    with tempfile.TemporaryDirectory() as tmp:
        context = _context(tmp)
        with patch.object(segment_blocks_stage, "_run_recoverable_boundary_tasks", side_effect=segment_tasks):
            segment_state = segment_blocks_stage.run(context, {"chopped_text_dict": chopped})
            segment_state, segment_report = segment_blocks_stage.rerun_failed_tasks(
                context, segment_state, max_rounds=2
            )
        clean_input = {
            "unsplit_statement_dict": {
                "0": {"pos1": {"node_type": "remark", "content": "Review me."}, "source_text": "Review me."}
            }
        }
        with patch.object(clean_nodes_stage, "_run_cleaning_tasks", side_effect=clean_tasks):
            clean_state = clean_nodes_stage.run(context, clean_input)
            clean_state, clean_report = clean_nodes_stage.rerun_failed_tasks(context, clean_state, max_rounds=2)

    assert segment_calls == [["0"], ["0"]]
    assert segment_report["status"] == "resolved"
    assert not segment_state["segment_blocks_report"]["classification_errors"]
    assert clean_calls == [["0"], ["0"]]
    assert clean_report["status"] == "resolved"
    assert clean_state["node_cleaning_report"]["manual_review_count"] == 1


def test_ensure_coverage_retry_merges_from_base_without_duplicates():
    calls = []

    def fake_extract(_context, tasks, _checkpoint_dir):
        calls.append(list(tasks))
        if len(calls) == 1:
            return {}
        return {
            key: {"content_quote": "Second.", "proof_quote": ""}
            for key in tasks
        }

    source = "# Theorem 1.1\nFirst.\n\n# Definition 1.2\nSecond.\n"
    state = {
        "corrected_text": source,
        "problem_dict": {
            0: {"pos1": "# Theorem 1.1\nFirst.\n\n"},
            1: {"pos1": "# Definition 1.2\nSecond.\n"},
        },
        "segment_blocks_report": {
            "blocks": [
                {
                    "block_id": 0,
                    "boundary_role": "top_level_logical_unit_start",
                    "label_surface": "Theorem 1.1",
                    "logical_unit_type_hint": "theorem",
                },
                {
                    "block_id": 1,
                    "boundary_role": "top_level_logical_unit_start",
                    "label_surface": "Definition 1.2",
                    "logical_unit_type_hint": "definition",
                },
            ]
        },
        "unsplit_statement_dict": {
            0: {
                "pos1": {"node_type": "theorem", "label": "Theorem 1.1", "content": "First.", "proof": ""},
                "_orig_key": 0,
                "source_text": "# Theorem 1.1\nFirst.\n\n",
            }
        },
    }
    with tempfile.TemporaryDirectory() as tmp, patch.object(
        ensure_coverage_stage, "_run_extract_tasks", side_effect=fake_extract
    ):
        context = _context(tmp)
        state = ensure_coverage_stage.run(context, state)
        state, report = ensure_coverage_stage.rerun_failed_tasks(context, state, max_rounds=2)

    labels = [wrapper["pos1"].get("label") for wrapper in state["unsplit_statement_dict"].values()]
    assert calls == [["block:1"], ["block:1"]]
    assert report["status"] == "resolved"
    assert labels == ["Theorem 1.1", "Definition 1.2"]


def test_relation_both_mode_recovers_each_branch_with_matching_mode():
    current = {"value": None}
    rerun_modes = []

    def fake_run(_context, state, relation_mode, relation_prompt_profile):
        assert relation_prompt_profile == "graph"
        current["value"] = {
            "path": f"{relation_mode}.json",
            "report": {
                "stage": "build_relations",
                "status": "unresolved",
                "failed_task_keys": [relation_mode],
                "attempt_rounds": 1,
            },
        }
        return state

    def fake_latest(_context):
        return current["value"]

    def fake_rerun(_context, state, max_rounds, relation_mode, relation_prompt_profile):
        assert max_rounds == 2
        assert relation_prompt_profile == "graph"
        rerun_modes.append(relation_mode)
        current["value"] = None
        state["edge_list"] = [relation_mode]
        return state, {"status": "resolved", "failed_task_keys": [], "canonical_updated": True}

    with tempfile.TemporaryDirectory() as tmp, patch.object(
        orchestrator.build_relations_stage, "run", side_effect=fake_run
    ), patch.object(
        orchestrator.build_relations_stage, "latest_unresolved_failure_report", side_effect=fake_latest
    ), patch.object(
        orchestrator.build_relations_stage, "rerun_failed_tasks", side_effect=fake_rerun
    ):
        result = orchestrator._relation_runner("both", "graph")(
            _context(tmp), {"node_dict": {}, "node_list": []}
        )

    assert rerun_modes == ["structured", "natural"]
    assert result["edge_list"] == ["structured"]
    assert result["edge_list_structured"] == ["structured"]
    assert result["edge_list_natural"] == ["natural"]


def test_relation_resume_recovers_report_updated_at_same_path():
    current = {
        "value": {
            "path": "same-report.json",
            "report": {
                "stage": "build_relations",
                "status": "unresolved",
                "failed_task_keys": ["0"],
                "updated_at": "2026-07-30T08:00:00+00:00",
            },
        }
    }
    rerun_calls = []

    def fake_run(_context, state, relation_mode, relation_prompt_profile):
        assert relation_mode == "structured"
        assert relation_prompt_profile == "graph"
        current["value"] = {
            "path": "same-report.json",
            "report": {
                "stage": "build_relations",
                "status": "unresolved",
                "failed_task_keys": ["0"],
                "updated_at": "2026-07-30T08:01:00+00:00",
            },
        }
        return state

    def fake_rerun(_context, state, max_rounds, relation_mode, relation_prompt_profile):
        rerun_calls.append((max_rounds, relation_mode, relation_prompt_profile))
        current["value"] = None
        state["edge_list"] = []
        return state, {
            "status": "resolved",
            "failed_task_keys": [],
            "canonical_updated": True,
        }

    with tempfile.TemporaryDirectory() as tmp, patch.object(
        orchestrator.build_relations_stage, "run", side_effect=fake_run
    ), patch.object(
        orchestrator.build_relations_stage,
        "latest_unresolved_failure_report",
        side_effect=lambda _context: current["value"],
    ), patch.object(
        orchestrator.build_relations_stage,
        "rerun_failed_tasks",
        side_effect=fake_rerun,
    ):
        context = _context(tmp)
        context.resume_task_checkpoints = True
        result = orchestrator._relation_runner("structured", "graph")(
            context,
            {"node_dict": {}, "node_list": []},
        )

    assert rerun_calls == [(2, "structured", "graph")]
    assert result["edge_list"] == []


if __name__ == "__main__":
    test_fixed_executor_recovers_before_completing_stage()
    test_fixed_executor_stops_after_unresolved_reruns()
    test_fixed_executor_continues_with_small_degraded_failure_ratio()
    test_fixed_executor_rejects_degraded_report_without_canonical_output()
    test_fixed_executor_ignores_stale_unresolved_report()
    test_correct_text_reruns_only_missing_batch()
    test_segment_blocks_and_clean_nodes_recover_complete_chunks()
    test_ensure_coverage_retry_merges_from_base_without_duplicates()
    test_relation_both_mode_recovers_each_branch_with_matching_mode()
    test_relation_resume_recovers_report_updated_at_same_path()
    print("fixed stage task recovery tests passed")
