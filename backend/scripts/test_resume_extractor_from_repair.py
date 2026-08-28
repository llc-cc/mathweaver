import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.stages.repair import stage as repair_stage
from scripts import resume_extractor_from_repair as resume


def _node():
    return {
        "global_id": "node-1",
        "node_type": "theorem",
        "title": "Demo",
        "statement_form": "implication",
        "remark": {"original_form": "If A, then B.", "text_normalized": "If A, then B."},
        "subject": ["A"],
        "context": [],
        "variables": [],
        "conditions": [{"id": "c1", "text": "A"}],
        "conclusions": [{"id": "r1", "text": "B"}],
        "analysis_layer": {"gap_analysis": {"logic_gaps": ["missing condition"]}},
        "repair_suggestion": {"suggested_conditions": ["A"]},
        "analysis_status": "completed",
        "repair_log": {"applied_repairs": [], "skipped_suggestions": [], "risk_notes": []},
        "repair_status": "failed",
    }


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def test_load_repair_resume_state_accepts_valid_partial_cache():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = Path(tmpdir) / "_stage_cache"
        node_dict = {"0": _node()}
        repair_input = repair_stage.build_repair_input_dict(node_dict)
        run_dir = cache / "agent_state" / "stage_runs" / "repair" / "run-1"
        input_path = run_dir / "input_dict.json"
        partial_path = run_dir / "partial_result_dict.json"
        report_path = run_dir / "failure_report.json"
        _write(cache / "node_dict_after_repair.json", node_dict)
        _write(cache / "repair_input_dict.json", repair_input)
        _write(cache / "repair_result_dict.json", {})
        _write(input_path, repair_input)
        _write(partial_path, {})
        _write(
            report_path,
            {
                "stage": "repair",
                "status": "unresolved",
                "expected_task_keys": ["0"],
                "succeeded_task_keys": [],
                "failed_task_keys": ["0"],
                "attempt_rounds": 3,
                "input_dict_path": str(input_path),
                "partial_result_dict_path": str(partial_path),
            },
        )

        state, facts = resume.load_repair_resume_state(cache)

        assert list(state["node_dict"]) == ["0"]
        assert facts["task_count"] == 1
        assert facts["succeeded_task_count"] == 0
        assert facts["failed_task_count"] == 1
        assert facts["failed_keys"] == ["0"]
        assert facts["attempt_rounds"] == 3

        valid_result = {
            "node_key": "0",
            "node_global_id": "node-1",
            "field_patch": {},
            "repair_log": {
                "applied_repairs": [],
                "skipped_suggestions": [],
                "risk_notes": [],
            },
        }
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["succeeded_task_keys"] = ["0"]
        report["failed_task_keys"] = []
        _write(report_path, report)
        _write(partial_path, {"0": valid_result})
        try:
            resume.load_repair_resume_state(cache)
        except RuntimeError as exc:
            assert "no unresolved task keys" in str(exc)
        else:
            raise AssertionError("A resolved task partition must not be treated as resumable")

        report["expected_task_keys"] = ["0", "1"]
        report["succeeded_task_keys"] = ["0"]
        report["failed_task_keys"] = ["1"]
        second_node = _node()
        second_node["global_id"] = "node-2"
        node_dict["1"] = second_node
        repair_input = repair_stage.build_repair_input_dict(node_dict)
        _write(cache / "node_dict_after_repair.json", node_dict)
        _write(input_path, repair_input)
        _write(report_path, report)
        state, facts = resume.load_repair_resume_state(cache)
        assert list(state["node_dict"]) == ["0", "1"]
        assert facts["succeeded_task_count"] == 1
        assert facts["failed_keys"] == ["1"]

        report["succeeded_task_keys"] = []
        _write(report_path, report)
        try:
            resume.load_repair_resume_state(cache)
        except RuntimeError as exc:
            assert "partial result keys" in str(exc)
        else:
            raise AssertionError("Partial results must match the report's succeeded keys")


def test_resume_continues_after_resolved_repair():
    state = {"node_dict": {"0": _node()}, "node_list": [_node()]}
    context = SimpleNamespace()
    observed = {}

    def fake_execute(_context, resumed_state, **kwargs):
        observed["state"] = resumed_state
        observed["kwargs"] = kwargs
        return {**resumed_state, "edge_list": []}

    def fake_recover(_context, resumed_state, _adapter, **_kwargs):
        resumed_state["repair_stage_run"] = {"status": "resolved", "canonical_updated": True}
        return resumed_state

    with patch.object(
        resume,
        "recover_failed_stage_tasks",
        side_effect=fake_recover,
    ), patch.object(resume, "execute_fixed_pipeline", side_effect=fake_execute):
        result = resume.resume_repair_and_downstream(context, state)

    assert observed["kwargs"]["start_stage"] == "extract_references"
    assert result["edge_list"] == []


if __name__ == "__main__":
    test_load_repair_resume_state_accepts_valid_partial_cache()
    test_resume_continues_after_resolved_repair()
    print("repair resume tests passed")
