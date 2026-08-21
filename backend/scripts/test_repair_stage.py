from pathlib import Path
import json
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from JoinAgent.Multi_Process.multi_process import MultiProcessor
from pipeline.common.node import (
    attach_internal_subnodes,
    compute_global_id_from_source,
    merge_node_with_source_envelope,
    structured_parent_view,
)
from pipeline.stages.analysis.stage import attach_analysis_back, sync_node_dict_from_list
from pipeline.stages.analysis.templates import validation09
from pipeline.stages.repair import stage as repair_stage
from pipeline.stages.repair.stage import apply_repair_patch, build_repair_input_dict, normalize_repair_result, run as run_repair
from pipeline.stages.repair.templates import (
    correction_prompt13,
    data_template13,
    prompt_template13,
    validation13,
)


def _sample_node():
    node = {
        "node_type": "theorem",
        "label": "Theorem 1",
        "title": {"chinese": "测试定理", "english": "Test theorem"},
        "statement_form": "implication",
        "content": "If A is finite and A holds, then B holds.",
        "source_original_form": "If A is finite and A holds, then B holds.",
        "remark": {
            "original_form": "If A is finite and A holds, then B holds.",
            "text_normalized": "If A is finite and A holds, then B holds.",
        },
        "subject": ["A"],
        "context": ["in a group"],
        "variables": [{"name": "A", "type": "object"}],
        "conditions": [{"id": "c1", "text": "A holds"}],
        "conclusions": [{"id": "r1", "text": "B holds"}],
        "analysis_layer": {"gap_analysis": {"logic_gaps": ["missing constraint"]}},
        "repair_suggestion": {"suggested_conditions": ["A is finite"]},
        "analysis_status": "completed",
    }
    node, _ = merge_node_with_source_envelope(
        node,
        {},
        stage_name="extract_statements",
        allowed_fields=(),
        seal=True,
        source_metadata={"source_text": node["content"]},
    )
    return node


SAMPLE_GLOBAL_ID = _sample_node()["global_id"]


def _seal_source_node(node, source):
    node = dict(node)
    node.setdefault("content", source)
    node.setdefault("source_original_form", source)
    sealed, _ = merge_node_with_source_envelope(
        node,
        {},
        stage_name="extract_statements",
        allowed_fields=(),
        seal=True,
        source_metadata={
            "source_text": node.get("source_text", source),
        },
    )
    return sealed


def _repair_record(field, operation, evidence, *, replaces_ids=None, reason="source omission"):
    return {
        "field": field,
        "operation": operation,
        "replaces_ids": list(replaces_ids or []),
        "source_evidence": evidence,
        "reason": reason,
    }


def _repair_log(*records, skipped=None, risks=None):
    return {
        "applied_repairs": list(records),
        "skipped_suggestions": list(skipped or []),
        "risk_notes": list(risks or []),
    }


def test_build_repair_input_dict_uses_slim_payload():
    task = build_repair_input_dict({0: _sample_node()})[0]
    payload = task["pos1"]

    assert set(task) == {"pos1"}
    assert set(payload) == {"node_ref", "statement", "extraction", "analysis", "structure_snapshot"}
    assert payload["node_ref"]["node_key"] == "0"
    assert payload["node_ref"]["global_id"] == SAMPLE_GLOBAL_ID
    assert "proof" not in payload
    assert "analysis_layer" in payload["analysis"]
    assert payload["structure_snapshot"]["conclusion_count"] == 1
    assert payload["structure_snapshot"]["subnode_count"] == 0
    assert isinstance(payload["extraction"]["subject"], list)
    assert isinstance(payload["extraction"]["context"], list)


def test_repair_prompt_formats_literal_empty_patch():
    task = build_repair_input_dict({0: _sample_node()})[0]
    with tempfile.TemporaryDirectory() as tmpdir:
        processor = MultiProcessor(
            llm=None,
            parse_method=lambda value: value,
            data_template=data_template13,
            prompt_template=prompt_template13,
            correction_template=correction_prompt13,
            validator=validation13,
            checkpoint_dir=tmpdir,
        )
        prompt = processor.generate_prompt(**task)

    assert "没有可靠修复时输出 {}" in prompt
    assert '"field_patch": {}' in prompt


def test_analysis_sync_preserves_node_dict_keys():
    node_list = [
        {"global_id": "node-1", "analysis_layer": {}, "repair_suggestion": {}},
        {"global_id": "node-2", "analysis_layer": {}, "repair_suggestion": {}},
    ]

    synced = sync_node_dict_from_list({"a": {}, "b": {}}, node_list)

    assert list(synced.keys()) == ["a", "b"]
    assert synced["a"]["global_id"] == "node-1"
    assert synced["b"]["global_id"] == "node-2"


def test_apply_repair_patch_updates_allowed_fields_only():
    node_dict = {0: _sample_node()}
    repair_result_dict = {
        0: {
            "node_key": "0",
            "node_global_id": SAMPLE_GLOBAL_ID,
            "field_patch": {
                "conditions": [{"id": "c1", "text": "A holds"}, {"id": "c2", "text": "A is finite"}],
                "global_id": "bad-update",
            },
            "repair_log": _repair_log(
                _repair_record("conditions", "append", "A is finite")
            ),
        }
    }

    updated, report = apply_repair_patch(node_dict, repair_result_dict)

    assert updated[0]["global_id"] == SAMPLE_GLOBAL_ID
    assert updated[0]["statement_form"] == "implication"
    assert len(updated[0]["conditions"]) == 2
    assert updated[0]["repair_log"]["applied_repairs"]
    assert report["applied"][0]["applied_fields"] == ["conditions"]


def test_apply_repair_patch_does_not_overwrite_with_empty_values():
    node_dict = {0: _sample_node()}
    repair_result_dict = {
        0: {
            "node_key": "0",
            "node_global_id": SAMPLE_GLOBAL_ID,
            "field_patch": {
                "statement_form": "",
                "conditions": [],
            },
            "repair_log": _repair_log(skipped=["no reliable repair"]),
        }
    }

    updated, _ = apply_repair_patch(node_dict, repair_result_dict)

    assert updated[0]["statement_form"] == "implication"
    assert len(updated[0]["conditions"]) == 1
    assert updated[0]["repair_log"]["skipped_suggestions"] == ["no reliable repair"]


def test_apply_repair_patch_skips_global_id_mismatch():
    node_dict = {0: _sample_node()}
    repair_result_dict = {
        0: {
            "node_key": "0",
            "node_global_id": "other-node",
            "field_patch": {"statement_form": "equality"},
            "repair_log": _repair_log(
                _repair_record("statement_form", "replace", "If A is finite and A holds, then B holds.")
            ),
        }
    }

    updated, report = apply_repair_patch(node_dict, repair_result_dict)

    assert updated[0]["statement_form"] == "implication"
    assert report["skipped"][0]["reason"] == "node_identity_mismatch"


def test_repair_validation_rejects_non_json_values():
    assert not validation13(
        {
            "node_key": "0",
            "node_global_id": SAMPLE_GLOBAL_ID,
            "field_patch": {"conditions": {"A holds", "B holds"}},
            "repair_log": {"applied_repairs": [], "skipped_suggestions": [], "risk_notes": []},
        }
    )


def test_repair_validation_accepts_sparse_evidence_grounded_patch():
    assert validation13(
        {
            "node_key": "0",
            "node_global_id": SAMPLE_GLOBAL_ID,
            "field_patch": {
                "conditions": [
                    {"id": "c1", "text": "A holds"},
                    {"id": "c2", "text": "A is finite"},
                ]
            },
            "repair_log": _repair_log(
                _repair_record("conditions", "append", "A is finite")
            ),
        }
    )


def test_repair_validation_rejects_patch_without_structured_evidence_log():
    assert not validation13(
        {
            "node_key": "0",
            "node_global_id": SAMPLE_GLOBAL_ID,
            "field_patch": {"context": ["in a group", "standard convention"]},
            "repair_log": {
                "applied_repairs": ["add standard convention"],
                "skipped_suggestions": [],
                "risk_notes": [],
            },
        }
    )


def test_normalize_repair_result_converts_sets_to_lists():
    normalized = normalize_repair_result(
        {
            "node_key": "0",
            "node_global_id": SAMPLE_GLOBAL_ID,
            "field_patch": {"conditions": {"A holds", "B holds"}},
            "repair_log": {"applied_repairs": {"add condition"}, "skipped_suggestions": [], "risk_notes": []},
        }
    )

    assert isinstance(normalized["field_patch"]["conditions"], list)
    assert normalized["repair_log"]["applied_repairs"] == ["add condition"]


def test_checkpoint_save_converts_sets_to_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        processor = MultiProcessor(
            llm=None,
            parse_method=lambda value: value,
            data_template="",
            prompt_template="",
            correction_template="",
            validator=lambda value: True,
            checkpoint_dir=tmpdir,
        )
        processor.save_checkpoint({"0": {"values": {"B", "A"}}})
        loaded = processor.load_checkpoint()

    assert loaded["0"]["values"] == ["A", "B"]


def _valid_analysis_result():
    return {
        "analysis_layer": {
            "boundary_analysis": {"context_condition_notes": [], "condition_conclusion_notes": []},
            "gap_analysis": {"logic_gaps": [], "missing_constraints": []},
            "definition_analysis": {"definitions_referenced": []},
            "structural_analysis": {"special_form_notes": []},
        },
        "repair_suggestion": {
            "suggested_conditions": [],
            "suggested_definitions": [],
            "repair_notes": [],
        },
    }


def test_analysis_marks_partial_string_key_results_per_node():
    nodes = [
        _seal_source_node(
            {"node_type": "remark", "label": "A"},
            "Source remark A.",
        ),
        _seal_source_node(
            {"node_type": "remark", "label": "B"},
            "Source remark B.",
        ),
    ]
    updated = attach_analysis_back(nodes, {"0": _valid_analysis_result()})

    assert validation09(_valid_analysis_result()) is True
    assert updated[0]["analysis_status"] == "completed"
    assert updated[1]["analysis_status"] == "failed"


def test_repair_splits_one_subnode_and_preserves_source_global_id():
    source = "If A holds, then B holds, D holds, and C holds."
    parent = {
        "node_type": "theorem",
        "label": "Theorem 1.1",
        "title": {"chinese": " Source title ", "english": " Source title "},
        "source_text": "Source block text.",
        "source_original_form": source,
        "remark": {"original_form": source},
        "statement_form": "implication",
        "subject": ["A"],
        "context": ["in a structure"],
        "variables": [{"name": "A", "type": "object"}],
        "conditions": [{"id": "c1", "text": "A holds"}],
        "conclusions": [
            {"id": "q1", "text": "B holds and D holds"},
            {"id": "q2", "text": "C holds"},
        ],
    }
    parent = attach_internal_subnodes(_seal_source_node(parent, source))
    original_global_id = parent["global_id"]
    original_title = parent["title"].copy()
    original_source_text = parent["source_text"]

    updated, report = apply_repair_patch(
        {0: parent},
        {
            "0": {
                "node_key": "0",
                "node_global_id": original_global_id,
                "field_patch": {
                    "conclusions": [
                        {"id": "q1a", "text": "B holds"},
                        {"id": "q1b", "text": "D holds"},
                        {"id": "q2", "text": "C holds"},
                    ]
                },
                "repair_log": _repair_log(
                    _repair_record(
                        "conclusions",
                        "split",
                        "B holds, D holds, and C holds",
                        replaces_ids=["q1"],
                    )
                ),
            }
        },
        repair_input_keys=[0],
    )

    assert updated[0]["global_id"] == original_global_id
    assert updated[0]["label"] == "Theorem 1.1"
    assert updated[0]["title"] == original_title
    assert updated[0]["source_text"] == original_source_text
    assert updated[0]["source_original_form"] == source
    assert updated[0]["repair_status"] == "applied", report
    assert [item["conclusions"][0]["text"] for item in updated[0]["sub_nodes"]] == [
        "B holds",
        "D holds",
        "C holds",
    ]
    assert updated[0]["subnode_count"] == 3
    assert updated[0]["sub_nodes"][0]["conclusions"][0]["text_normalized"]


def test_repair_rejects_partial_conclusion_replacement_atomically():
    parent = attach_internal_subnodes({
        "global_id": "node-split",
        "node_type": "theorem",
        "statement_form": "implication",
        "remark": {"original_form": "If A, then B, D, and C."},
        "subject": ["A"],
        "context": [],
        "variables": [],
        "conditions": [{"id": "c1", "text": "A"}],
        "conclusions": [
            {"id": "q1", "text": "B and D"},
            {"id": "q2", "text": "C"},
        ],
    })
    original = json.loads(json.dumps(parent, ensure_ascii=False))
    updated, report = apply_repair_patch(
        {0: parent},
        {0: {
            "node_key": "0",
            "node_global_id": "node-split",
            "field_patch": {
                "conclusions": [
                    {"id": "q1a", "text": "B"},
                    {"id": "q1b", "text": "D"},
                ]
            },
            "repair_log": _repair_log(
                _repair_record("conclusions", "split", "B, D, and C", replaces_ids=["q1"])
            ),
        }},
    )

    assert updated[0]["repair_status"] == "rejected_guard"
    assert updated[0]["sub_nodes"] == original["sub_nodes"]
    assert report["skipped"][0]["reason"] == "guard_rejected"


def test_repair_preserves_local_subnode_conditions_when_adding_global_condition():
    source = "If A holds and G holds, then B and C hold."
    parent = attach_internal_subnodes(_seal_source_node({
        "node_type": "theorem",
        "statement_form": "implication",
        "remark": {"original_form": source},
        "subject": ["A"],
        "context": [],
        "variables": [],
        "conditions": [{"id": "c1", "text": "A holds"}],
        "conclusions": [
            {"id": "q1", "text": "B holds"},
            {"id": "q2", "text": "C holds"},
        ],
    }, source))
    parent["sub_nodes"][1]["remark"]["applicable_conditions_text"] = ["local C condition"]
    parent["subnode_specs"][1]["applicable_conditions_text"] = ["local C condition"]
    parent["analysis_status"] = "completed"
    parent["analysis_layer"] = {}
    parent["repair_suggestion"] = {"suggested_conditions": ["G holds"]}

    snapshot = build_repair_input_dict({0: parent})[0]["pos1"]["structure_snapshot"]
    assert snapshot["subnode_count"] == 2
    assert snapshot["subnode_specs"][1]["applicable_conditions_text"] == ["local C condition"]
    assert snapshot["subnodes"][1]["applicable_conditions_text"] == ["local C condition"]

    updated, report = apply_repair_patch(
        {0: parent},
        {0: {
            "node_key": "0",
            "node_global_id": parent["global_id"],
            "field_patch": {
                "conditions": [
                    {"id": "c1", "text": "A holds"},
                    {"id": "c2", "text": "G holds"},
                ]
            },
            "repair_log": _repair_log(
                _repair_record("conditions", "append", "G holds")
            ),
        }},
    )

    assert updated[0]["repair_status"] == "applied", report
    assert updated[0]["subnode_count"] == 2
    assert "local C condition" in updated[0]["sub_nodes"][1]["remark"]["applicable_conditions_text"]
    assert "G holds" in updated[0]["sub_nodes"][1]["remark"]["applicable_conditions_text"]


def test_repair_rejects_unfaithful_mathematical_additions():
    cases = [
        (
            "Exercise using an equivalence class [x].",
            "context",
            ["given equivalence class", "[x] is the singleton set {x}"],
            "Exercise using an equivalence class [x].",
        ),
        (
            "A semi-metric is symmetric and satisfies the triangle inequality.",
            "conditions",
            [
                {"id": "c1", "text": "d is symmetric"},
                {"id": "c2", "text": "d(x, x) = 0"},
            ],
            "A semi-metric is symmetric and satisfies the triangle inequality.",
        ),
        (
            "Let gamma be a regular closed curve in R^2.",
            "context",
            ["gamma is a regular closed curve", "gamma maps into R^2\\{0}"],
            "Let gamma be a regular closed curve in R^2.",
        ),
    ]
    for index, (source, field_name, patched_value, evidence) in enumerate(cases):
        node = {
            "global_id": f"bad-{index}",
            "node_type": "theorem",
            "statement_form": "assertion",
            "remark": {"original_form": source},
            "subject": [],
            "context": patched_value[:1] if field_name == "context" else [],
            "variables": [],
            "conditions": patched_value[:1] if field_name == "conditions" else [],
            "conclusions": [{"id": "q1", "text": "claim"}],
        }
        updated, _ = apply_repair_patch(
            {0: node},
            {0: {
                "node_key": "0",
                "node_global_id": f"bad-{index}",
                "field_patch": {field_name: patched_value},
                "repair_log": _repair_log(
                    _repair_record(field_name, "append", evidence)
                ),
            }},
        )
        assert updated[0]["repair_status"] == "rejected_guard"
        assert structured_parent_view(updated[0]).get(field_name) == structured_parent_view(node).get(field_name)


def test_existing_cache_replay_never_reduces_subnodes():
    cache_dir = PROJECT_ROOT / "test_output" / "elegantbook-en-section-1.1_md" / "_stage_cache"
    node_path = cache_dir / "node_dict.json"
    result_path = cache_dir / "repair_result_dict.json"
    if not node_path.exists() or not result_path.exists():
        return

    with node_path.open("r", encoding="utf-8") as handle:
        nodes = json.load(handle)
    with result_path.open("r", encoding="utf-8") as handle:
        results = json.load(handle)

    updated, report = apply_repair_patch(nodes, results, repair_input_keys=results.keys())
    before_count = sum(len(node.get("sub_nodes") or []) for node in nodes.values())
    after_count = sum(len(node.get("sub_nodes") or []) for node in updated.values())

    assert before_count == 65
    assert after_count >= before_count
    assert any(item.get("reason") == "guard_rejected" for item in report["skipped"])
    for key, result in results.items():
        patch = result.get("field_patch") if isinstance(result, dict) else None
        if not isinstance(patch, dict) or not any(bool(value) for value in patch.values()):
            continue
        before_view = structured_parent_view(nodes[key])
        after_view = structured_parent_view(updated[key])
        for field_name in ("statement_form", "subject", "context", "variables", "conditions", "conclusions"):
            assert after_view.get(field_name) == before_view.get(field_name)


def test_repair_without_actionable_suggestions_reports_resolved():
    node = _sample_node()
    node["analysis_layer"] = {}
    node["repair_suggestion"] = {}
    node["analysis_status"] = "completed"
    with tempfile.TemporaryDirectory() as tmpdir:
        state = run_repair(
            SimpleNamespace(output_dir=tmpdir),
            {"node_dict": {0: node}, "node_list": [node]},
        )

    assert state["repair_stage_run"]["status"] == "resolved"
    assert state["node_dict"][0]["repair_status"] == "not_needed"


def test_repair_rerun_publishes_partial_results_before_degraded_continue():
    first = _sample_node()
    second = _sample_node()
    second["global_id"] = "node-2"
    node_dict = {0: first, 1: second}
    partial = {
        "0": {
            "node_key": "0",
            "node_global_id": SAMPLE_GLOBAL_ID,
            "field_patch": {},
            "repair_log": _repair_log(),
        }
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        run_dir = Path(tmpdir) / "run"
        run_dir.mkdir()
        unresolved_report = {
            "stage": "repair",
            "status": "unresolved",
            "expected_task_count": 2,
            "succeeded_task_count": 1,
            "failed_task_count": 1,
            "expected_task_keys": ["0", "1"],
            "succeeded_task_keys": ["0"],
            "failed_task_keys": ["1"],
            "attempt_rounds": 3,
            "canonical_updated": False,
        }
        with patch.object(
            repair_stage,
            "rerun_unresolved_task_report",
            return_value=(partial, unresolved_report, run_dir),
        ):
            state, report = repair_stage.rerun_failed_tasks(
                SimpleNamespace(output_dir=tmpdir),
                {"node_dict": node_dict, "node_list": list(node_dict.values())},
            )

    assert report["status"] == "unresolved"
    assert report["canonical_updated"] is True
    assert state["repair_result_dict"] == partial
    assert state["node_dict"][0]["repair_status"] == "not_needed"
    assert state["node_dict"][1]["repair_status"] == "failed"


if __name__ == "__main__":
    test_build_repair_input_dict_uses_slim_payload()
    test_repair_prompt_formats_literal_empty_patch()
    test_analysis_sync_preserves_node_dict_keys()
    test_apply_repair_patch_updates_allowed_fields_only()
    test_apply_repair_patch_does_not_overwrite_with_empty_values()
    test_apply_repair_patch_skips_global_id_mismatch()
    test_repair_validation_rejects_non_json_values()
    test_repair_validation_accepts_sparse_evidence_grounded_patch()
    test_repair_validation_rejects_patch_without_structured_evidence_log()
    test_normalize_repair_result_converts_sets_to_lists()
    test_checkpoint_save_converts_sets_to_json()
    test_analysis_marks_partial_string_key_results_per_node()
    test_repair_splits_one_subnode_and_preserves_source_global_id()
    test_repair_rejects_partial_conclusion_replacement_atomically()
    test_repair_preserves_local_subnode_conditions_when_adding_global_condition()
    test_repair_rejects_unfaithful_mathematical_additions()
    test_existing_cache_replay_never_reduces_subnodes()
    test_repair_without_actionable_suggestions_reports_resolved()
    test_repair_rerun_publishes_partial_results_before_degraded_continue()
    print("repair stage tests passed")
