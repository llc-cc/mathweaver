import json
import shutil
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.main_agent.control import AgentRunConfig, MainAgentController
from pipeline.main_agent.toolkit import AgentTool
from pipeline.common.node import merge_node_with_source_envelope
from pipeline.stages.extract_logic_tuples import stage as extract_logic_tuples_stage


TMP_ROOT = Path(__file__).resolve().parent / "_tmp_extract_logic_tuples_recovery"


def _temp_dir():
    path = TMP_ROOT / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _context(root):
    final_dir = root / "final"
    cache_dir = final_dir / "_stage_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    input_path = root / "input.md"
    input_path.write_text("test", encoding="utf-8")
    return SimpleNamespace(
        file_path=str(input_path),
        output_dir=str(cache_dir),
        output_node_path=str(final_dir / "TEST_NODE_OUT.json"),
        output_edge_path=str(final_dir / "TEST_EDGE_OUT.json"),
        output_natural_node_path=str(final_dir / "TEST_NODE_NATURAL_OUT.json"),
        checkpoint_root=str(cache_dir / "checkpoint"),
        checkpoint=10,
        num_threads=2,
        llm=object(),
        parser=SimpleNamespace(parse_dict=lambda value: value),
        divider=None,
        enable_analysis=False,
        enable_math_disambiguation=False,
        llm_engine="api",
        execution_mode="agent",
    )


def _source_state():
    def sealed(content, label):
        node, _ = merge_node_with_source_envelope(
            {
                "node_type": "theorem",
                "content": content,
                "proof": "",
                "label": label,
            },
            {},
            stage_name="extract_statements",
            allowed_fields=(),
            seal=True,
            source_metadata={"source_text": content},
        )
        return node

    return {
        "structured_input_dict": {
            "0": {"_orig_key": "0", "pos1": sealed("A", "T0")},
            "1": {"_orig_key": "1", "pos1": sealed("B", "T1")},
        },
        "definition_axiom_dict": {},
    }


def _result(label):
    return {
        "statement_form": "implication",
        "subject": [],
        "context": [],
        "variables": [],
        "conditions": [{"id": "c1", "text": f"condition for {label}"}],
        "conclusions": [{"id": "q1", "text": f"conclusion for {label}"}],
    }


def test_partial_run_preserves_canonical_and_reruns_only_failed_key():
    root = _temp_dir()
    try:
        context = _context(root)
        old_canonical = {"old": {"node_type": "theorem", "label": "old"}}
        canonical_path = Path(context.output_dir) / "node_dict.json"
        _write(canonical_path, old_canonical)

        with patch.object(extract_logic_tuples_stage, "run_multiprocess_task", return_value={"0": _result("T0")}):
            state = extract_logic_tuples_stage.run(context, _source_state())

        assert len(json.loads(canonical_path.read_text(encoding="utf-8"))) == 2
        assert state["logic_tuple_stage_run"]["status"] == "unresolved"
        assert state["logic_tuple_stage_run"]["failed_task_keys"] == ["1"]

        called_keys = []

        def fake_rerun(**kwargs):
            called_keys.append(list(kwargs["index_dict"].keys()))
            return {"1": _result("T1")}

        with patch.object(extract_logic_tuples_stage, "run_multiprocess_task", side_effect=fake_rerun):
            state, report = extract_logic_tuples_stage.rerun_failed_tasks(context, _source_state(), max_rounds=2)

        assert called_keys == [["1"]]
        assert report["status"] == "resolved"
        assert report["canonical_updated"] is True
        assert len(json.loads(canonical_path.read_text(encoding="utf-8"))) == 2
        assert len(state["node_dict"]) == 2
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_complete_run_writes_canonical_directly():
    root = _temp_dir()
    try:
        context = _context(root)
        complete = {"0": _result("T0"), "1": _result("T1")}
        with patch.object(extract_logic_tuples_stage, "run_multiprocess_task", return_value=complete):
            state = extract_logic_tuples_stage.run(context, _source_state())
        assert state["logic_tuple_stage_run"]["status"] == "resolved"
        assert state["logic_tuple_stage_run"]["canonical_updated"] is True
        assert len(json.loads((Path(context.output_dir) / "node_dict.json").read_text(encoding="utf-8"))) == 2
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_pipeline_mode_partial_run_writes_partial_canonical():
    root = _temp_dir()
    try:
        context = _context(root)
        context.execution_mode = "pipeline"
        canonical_path = Path(context.output_dir) / "node_dict.json"

        with patch.object(extract_logic_tuples_stage, "run_multiprocess_task", return_value={"0": _result("T0")}):
            state = extract_logic_tuples_stage.run(context, _source_state())

        assert state["logic_tuple_stage_run"]["status"] == "unresolved"
        assert state["logic_tuple_stage_run"]["canonical_updated"] is True
        assert state["logic_tuple_stage_run"]["failed_task_keys"] == ["1"]
        assert len(json.loads(canonical_path.read_text(encoding="utf-8"))) == 2
        assert len(state["node_dict"]) == 2
        assert state["node_dict"][1]["_derivation_status"]["extract_logic_tuples"]["status"] == "degraded"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_pipeline_mode_invalid_logic_tuple_input_stops_before_downstream():
    root = _temp_dir()
    try:
        context = _context(root)
        context.execution_mode = "pipeline"
        with patch.object(
            extract_logic_tuples_stage,
            "logic_tuple_input_quality_issues",
            return_value=[{"task_key": "0", "issue": "empty_original_form"}],
        ):
            try:
                extract_logic_tuples_stage.run(context, _source_state())
            except RuntimeError as exc:
                assert "empty original text" in str(exc)
            else:
                raise AssertionError("Expected invalid pipeline input to stop at extract_logic_tuples")

        assert not (Path(context.output_dir) / "node_dict.json").exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_pipeline_mode_empty_logic_tuple_result_preserves_source_nodes():
    root = _temp_dir()
    try:
        context = _context(root)
        context.execution_mode = "pipeline"
        with patch.object(extract_logic_tuples_stage, "run_multiprocess_task", return_value={}):
            state = extract_logic_tuples_stage.run(context, _source_state())

        assert (Path(context.output_dir) / "node_dict.json").exists()
        assert len(state["node_dict"]) == 2
        assert state["logic_tuple_stage_run"]["status"] == "unresolved"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_unresolved_report_has_priority_and_downstream_cache_moves_to_stale():
    root = _temp_dir()
    try:
        context = _context(root)
        with patch.object(extract_logic_tuples_stage, "run_multiprocess_task", return_value={"0": _result("T0")}):
            extract_logic_tuples_stage.run(context, _source_state())
        assert (Path(context.output_dir) / "node_dict.json").exists()

        tool = AgentTool(context, AgentRunConfig(enable_analysis=False, enable_math_disambiguation=False))
        validation = tool.validate_stage("extract_logic_tuples")
        assert any(issue["code"] == "incomplete_logic_tuple_tasks" for issue in validation["issues"])
        action = tool.next_action()
        assert action["orchestration_state"] == "failed_stage_tasks_need_rerun"
        assert action["pending_action"]["command"] == "rerun-failed-tasks"

        cache = Path(context.output_dir)
        _write(cache / "references_dict.json", {"stale": {}})
        _write(cache / "node_dict_normalized.json", {"stale": {}})
        _write(Path(context.output_edge_path), [{"stale": True}])
        _write(cache / "agent_state" / "quality_facts.json", {"stages": {"extract_references": {}, "compile_logic_form": {}}})

        invalidation = tool._invalidate_downstream_cache("extract_logic_tuples", "test")
        assert invalidation["moved_count"] >= 3
        assert not (cache / "references_dict.json").exists()
        assert not (cache / "node_dict_normalized.json").exists()
        assert not Path(context.output_edge_path).exists()
        assert Path(invalidation["manifest_path"]).exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_failed_rerun_uses_two_rounds_and_keeps_canonical_unchanged():
    root = _temp_dir()
    try:
        context = _context(root)
        canonical_path = Path(context.output_dir) / "node_dict.json"
        old_canonical = {"old": {"node_type": "theorem", "label": "old"}}
        _write(canonical_path, old_canonical)
        with patch.object(extract_logic_tuples_stage, "run_multiprocess_task", return_value={"0": _result("T0")}):
            extract_logic_tuples_stage.run(context, _source_state())

        with patch.object(extract_logic_tuples_stage, "run_multiprocess_task", return_value={}) as rerun:
            _, report = extract_logic_tuples_stage.rerun_failed_tasks(context, _source_state(), max_rounds=2)

        assert rerun.call_count == 2
        assert report["status"] == "unresolved"
        assert report["failed_task_keys"] == ["1"]
        assert len(json.loads(canonical_path.read_text(encoding="utf-8"))) == 2
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_tool_rerun_resolves_partial_run_and_invalidates_downstream():
    root = _temp_dir()
    try:
        context = _context(root)
        cache = Path(context.output_dir)
        source_state = _source_state()
        _write(cache / "structured_input_dict.json", source_state["structured_input_dict"])
        _write(cache / "definition_axiom_dict.json", source_state["definition_axiom_dict"])
        with patch.object(extract_logic_tuples_stage, "run_multiprocess_task", return_value={"0": _result("T0")}):
            extract_logic_tuples_stage.run(context, source_state)
        _write(cache / "references_dict.json", {"stale": {}})

        tool = AgentTool(context, AgentRunConfig(enable_analysis=False, enable_math_disambiguation=False))
        with patch.object(extract_logic_tuples_stage, "run_multiprocess_task", return_value={"1": _result("T1")}):
            result = tool.rerun_failed_tasks("extract_logic_tuples")

        assert result["failure_report"]["status"] == "resolved"
        assert result["downstream_invalidation"]["moved_count"] >= 1
        assert not (cache / "references_dict.json").exists()
        assert (cache / "node_dict.json").exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stage_bounded_state_does_not_load_downstream_node_dict():
    root = _temp_dir()
    try:
        context = _context(root)
        cache = Path(context.output_dir)
        _write(cache / "node_dict.json", {"canonical": {"node_type": "theorem"}})
        _write(cache / "references_dict.json", {"references": {"node_type": "theorem"}})
        _write(cache / "node_dict_after_predicate_normalization.json", {"predicate": {"node_type": "theorem"}})
        controller = MainAgentController(
            context,
            AgentRunConfig(enable_analysis=False, enable_math_disambiguation=False),
        )

        logic_state = controller.load_state_for_stage("extract_logic_tuples")
        reference_state = controller.load_state_for_stage("extract_references")
        assert "node_dict" not in logic_state
        assert list(reference_state["node_dict"].keys()) == ["canonical"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    test_partial_run_preserves_canonical_and_reruns_only_failed_key()
    test_complete_run_writes_canonical_directly()
    test_pipeline_mode_partial_run_writes_partial_canonical()
    test_pipeline_mode_invalid_logic_tuple_input_stops_before_downstream()
    test_pipeline_mode_empty_logic_tuple_result_preserves_source_nodes()
    test_unresolved_report_has_priority_and_downstream_cache_moves_to_stale()
    test_failed_rerun_uses_two_rounds_and_keeps_canonical_unchanged()
    test_tool_rerun_resolves_partial_run_and_invalidates_downstream()
    test_stage_bounded_state_does_not_load_downstream_node_dict()
    print("extract_logic_tuples recovery tests passed")
