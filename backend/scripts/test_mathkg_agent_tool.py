import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pipeline.common.llm_task import run_multiprocess_task
from pipeline.common.claude_cli_engine import ClaudeCliEngine
from pipeline.context import PipelineContext
from pipeline.main_agent import AgentRunConfig
from pipeline.main_agent.toolkit import AgentTool, active_stage_order, without_proxy_env
from pipeline.stages.extract_statements import stage as extract_statements_stage
from scripts import resume_pipeline_from_stage as resume_script


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def test_late_resume_script_keeps_logic_ir_side_path_opt_in():
    default_args = resume_script._build_parser().parse_args([])
    default_command = resume_script._common_tool_args(
        default_args,
        Path("input.md"),
        Path("output"),
    )
    assert default_args.from_stage == "build_relations"
    assert "--experimental-logic-ir" not in default_command

    experimental_args = resume_script._build_parser().parse_args(
        [
            "--experimental-logic-ir",
            "--from-stage",
            "compile_logic_form",
        ]
    )
    experimental_command = resume_script._common_tool_args(
        experimental_args,
        Path("input.md"),
        Path("output"),
    )
    assert "--experimental-logic-ir" in experimental_command


def _write_fake_claude(path):
    script = r'''
import json
import re
import sys

prompt = sys.stdin.read()
keys = re.findall(r'<source_block key="([^"]+)">', prompt)
if not keys:
    keys = ["0"]
result = {}
for key in keys:
    result[key] = {
        "0": {
            "node_type": "Theorem",
            "content": f"Candidate content for source block {key}.",
            "proof": "",
            "label": f"Theorem {key}",
        }
    }
print(json.dumps({"result": json.dumps(result, ensure_ascii=False)}, ensure_ascii=False))
'''
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(script)


def _write_failing_claude(path):
    script = "import sys\nsys.stderr.write('simulated cli failure')\nraise SystemExit(1)\n"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(script)


class _FakeRepairLLM:
    def __init__(self):
        self.prompts = []

    def ask(self, prompt, temperature=0.7):
        self.prompts.append(prompt)
        return json.dumps(
            {
                "0": {
                    "node_type": "Theorem",
                    "content": "Localized repaired theorem content.",
                    "proof": "",
                    "label": "(1.2)",
                }
            }
        )


class _JsonParser:
    @staticmethod
    def parse_dict(value):
        return json.loads(value)


def _tool_fixture():
    tmp = tempfile.TemporaryDirectory()
    input_path = os.path.join(tmp.name, "input.md")
    output_dir = os.path.join(tmp.name, "out")
    with open(input_path, "w", encoding="utf-8") as handle:
        handle.write("# Demo\n\nTheorem 1. If A then B.")

    context = PipelineContext(
        file_path=input_path,
        output_node_path=os.path.join(output_dir, "nodes.json"),
        output_edge_path=os.path.join(output_dir, "edges.json"),
        llm=object(),
        parser=object(),
        divider=SimpleNamespace(divide=lambda _path: ["Theorem 1. If A then B."]),
    )
    return tmp, context, AgentTool(context, AgentRunConfig(diagnose_only=True))


def test_without_proxy_env_temporarily_clears_and_restores_proxy_variables():
    proxy_keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
    original = {key: os.environ.get(key) for key in proxy_keys}
    try:
        for key in proxy_keys:
            for item in proxy_keys:
                os.environ.pop(item, None)
            os.environ[key] = f"http://proxy.example/{key}"
            with without_proxy_env():
                assert all(os.environ.get(item) is None for item in proxy_keys)
            assert os.environ.get(key) == f"http://proxy.example/{key}"
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_run_stage_clears_proxy_environment_inside_stage_and_restores_it():
    tmp, context, tool = _tool_fixture()
    proxy_keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
    original = {key: os.environ.get(key) for key in proxy_keys}
    observed = {}
    try:
        for key in proxy_keys:
            os.environ.pop(key, None)
        os.environ["HTTP_PROXY"] = "http://proxy.example/HTTP_PROXY"

        def fake_run_stage(stage, state):
            observed["stage"] = stage
            observed["proxies"] = {key: os.environ.get(key) for key in proxy_keys}
            _write_json(os.path.join(context.output_dir, "corrected_text_dict.json"), {"0": {"pos1": {"0": "text"}}})
            return {"chopped_text_dict": {"0": {"pos1": {"0": "text"}}}}

        original_run_stage = tool.controller._run_stage
        tool.controller._run_stage = fake_run_stage
        try:
            result = tool.run_stage("correct_text")
        finally:
            tool.controller._run_stage = original_run_stage

        assert result["command"] == "run-stage"
        assert observed["stage"] == "correct_text"
        assert all(value is None for value in observed["proxies"].values())
        assert os.environ.get("HTTP_PROXY") == "http://proxy.example/HTTP_PROXY"
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        tmp.cleanup()


def test_scan_cache_returns_facts_without_decisions():
    tmp, context, tool = _tool_fixture()
    try:
        _write_json(os.path.join(context.output_dir, "corrected_text_dict.json"), {"0": {"pos1": {"0": "text"}}})
        _write_json(os.path.join(context.output_dir, "problem_dict.json"), {"0": {"pos1": "text"}})

        result = tool.scan_cache()

        assert result["command"] == "scan-cache"
        assert "current_stage" not in result
        assert "retry_plan" not in result
        assert result["stages"][0]["stage"] == "correct_text"
        assert "all_expected_files_exist" in result["stages"][0]
    finally:
        tmp.cleanup()


def test_validate_stage_returns_fact_issues_only():
    tmp, context, tool = _tool_fixture()
    try:
        _write_json(
            os.path.join(context.output_dir, "unsplit_statement_dict.json"),
            {"0": {"pos1": {"node_type": "Theorem", "content": "", "proof": "", "label": ""}}},
        )

        result = tool.validate_stage("extract_statements")

        assert result["command"] == "validate-stage"
        assert "action" not in result
        assert "status" not in result
        assert any(issue["code"] == "empty_content" for issue in result["issues"])
        assert all("retry_scope" not in issue for issue in result["issues"])
    finally:
        tmp.cleanup()


def test_split_nodes_contract_uses_current_outputs_and_restores_state():
    tmp, context, tool = _tool_fixture()
    try:
        node_split = {
            "0": {
                "parent_node": {"node_type": "Theorem", "content": "If A then B.", "proof": "", "label": "T1"},
                "sub_nodes": [],
            }
        }
        statement_without_title = {
            "0": {
                "pos1": {"node_type": "Theorem", "content": "If A then B.", "proof": "", "label": "T1"},
                "_orig_key": "0",
            }
        }
        _write_json(os.path.join(context.output_dir, "unsplit_statement_dict.json"), statement_without_title)
        _write_json(os.path.join(context.output_dir, "node_split_dict.json"), node_split)
        _write_json(os.path.join(context.output_dir, "statement_without_title_dict.json"), statement_without_title)

        scan = tool.scan_cache()
        split_scan = next(item for item in scan["stages"] if item["stage"] == "split_nodes")
        assert split_scan["all_expected_files_exist"] is True
        assert [os.path.basename(item["path"]) for item in split_scan["expected_files"]] == [
            "node_split_dict.json",
            "statement_without_title_dict.json",
        ]

        state = tool.controller.load_state_from_cache()
        assert state["node_split_dict"] == node_split
        assert state["statement_without_title_dict"] == statement_without_title
        assert "discourse_decomposition_dict" not in state

        validation = tool.validate_stage("split_nodes")
        assert not any(issue["code"] == "missing_split_outputs" for issue in validation["issues"])
        assert validation["metrics"] == {
            "split_decision_count": 1,
            "split_node_count": 1,
            "input_node_count": 1,
            "node_split_output_exists": True,
            "statement_without_title_output_exists": True,
        }

        tool.controller._run_stage = lambda _stage, state: state
        run_result = tool.run_stage("split_nodes")
        assert [os.path.basename(item["path"]) for item in run_result["output_files"]] == [
            "node_split_dict.json",
            "statement_without_title_dict.json",
        ]
    finally:
        tmp.cleanup()


def test_logic_ir_stages_are_opt_in_and_flag_propagates_to_commands():
    default_order = active_stage_order(AgentRunConfig())
    assert "compile_logic_form" not in default_order
    assert "normalize_predicates" not in default_order

    config = AgentRunConfig(experimental_logic_ir=True)
    experimental_order = active_stage_order(config)
    compile_index = experimental_order.index("compile_logic_form")
    assert experimental_order[compile_index + 1] == "normalize_predicates"
    assert experimental_order[compile_index + 2] == "build_relations"

    tmp, context, tool = _tool_fixture()
    try:
        tool.config.experimental_logic_ir = True
        command = tool._base_command_args("scan-cache")
        assert "--experimental-logic-ir" in command
    finally:
        tmp.cleanup()


def test_clean_nodes_contract_restores_cleaned_unsplit_for_downstream():
    tmp, context, tool = _tool_fixture()
    try:
        raw = {
            "55": {
                "pos1": {"node_type": "exercise", "content": "Prove the above assertion.", "proof": "", "label": "e1.1.17"},
                "source_text": "Prove the above assertion.",
            },
            "56": {
                "pos1": {"node_type": "exercise", "content": "Prove that X is compact.", "proof": "", "label": "e1.1.18"},
                "source_text": "Prove that X is compact.",
            },
        }
        cleaned = {"56": raw["56"]}
        report = {
            "input_node_count": 2,
            "cleaned_node_count": 1,
            "quarantined_node_count": 1,
            "manual_review_count": 0,
            "invalid_chunk_count": 0,
            "missing_decision_count": 0,
        }
        quarantine = {"55": {"wrapper": raw["55"], "decision": {"action": "quarantine"}}}
        _write_json(os.path.join(context.output_dir, "unsplit_statement_dict.json"), raw)
        _write_json(os.path.join(context.output_dir, "unsplit_statement_dict_cleaned.json"), cleaned)
        _write_json(os.path.join(context.output_dir, "node_cleaning_report.json"), report)
        _write_json(os.path.join(context.output_dir, "node_quarantine.json"), quarantine)

        scan = tool.scan_cache()
        clean_scan = next(item for item in scan["stages"] if item["stage"] == "clean_nodes")
        assert clean_scan["all_expected_files_exist"] is True

        state = tool.controller.load_state_from_cache()
        assert state["unsplit_statement_dict_raw"] == raw
        assert state["unsplit_statement_dict"] == cleaned
        assert state["node_quarantine"] == quarantine

        validation = tool.validate_stage("clean_nodes")
        assert validation["metrics"]["raw_node_count"] == 2
        assert validation["metrics"]["cleaned_node_count"] == 1
        assert validation["metrics"]["quarantined_node_count"] == 1
        assert not any(issue["severity"] == "error" for issue in validation["issues"])
    finally:
        tmp.cleanup()


def test_split_nodes_contract_rejects_legacy_or_partial_cache():
    tmp, context, tool = _tool_fixture()
    try:
        statement_without_title = {
            "0": {
                "pos1": {"node_type": "Theorem", "content": "If A then B.", "proof": "", "label": "T1"},
                "_orig_key": "0",
            }
        }
        _write_json(os.path.join(context.output_dir, "unsplit_statement_dict.json"), statement_without_title)
        _write_json(os.path.join(context.output_dir, "discourse_decomposition_dict.json"), {"0": {"legacy": True}})
        _write_json(os.path.join(context.output_dir, "statement_without_title_dict.json"), statement_without_title)

        legacy_scan = tool.scan_cache()
        legacy_split = next(item for item in legacy_scan["stages"] if item["stage"] == "split_nodes")
        assert legacy_split["all_expected_files_exist"] is False
        legacy_validation = tool.validate_stage("split_nodes")
        assert any(issue["code"] == "missing_split_outputs" for issue in legacy_validation["issues"])
        assert legacy_validation["metrics"]["node_split_output_exists"] is False
        assert legacy_validation["metrics"]["statement_without_title_output_exists"] is True

        os.remove(os.path.join(context.output_dir, "statement_without_title_dict.json"))
        _write_json(os.path.join(context.output_dir, "node_split_dict.json"), {"0": {"current": True}})
        partial_validation = tool.validate_stage("split_nodes")
        assert any(issue["code"] == "missing_split_outputs" for issue in partial_validation["issues"])
        assert partial_validation["metrics"]["node_split_output_exists"] is True
        assert partial_validation["metrics"]["statement_without_title_output_exists"] is False
    finally:
        tmp.cleanup()


def test_next_action_advances_from_valid_split_nodes_to_generate_titles():
    tmp, context, tool = _tool_fixture()
    try:
        corrected = {"0": {"pos1": {"0": "Theorem T1. If A then B."}}}
        problem = {"0": {"pos1": "Theorem T1. If A then B."}}
        unsplit = {
            "0": {
                "pos1": {"node_type": "Theorem", "content": "If A then B.", "proof": "", "label": "T1"},
                "_orig_key": "0",
            }
        }
        _write_json(os.path.join(context.output_dir, "corrected_text_dict.json"), corrected)
        _write_json(os.path.join(context.output_dir, "problem_dict.json"), problem)
        _write_json(os.path.join(context.output_dir, "unsplit_statement_dict.json"), unsplit)

        for stage in ("correct_text", "segment_blocks"):
            tool.validate_stage(stage)
            tool.write_agent_decision({"stage": stage, "decision": "continue", "reason": "Test fixture is valid."})
        tool.validate_stage("extract_statements")
        packet = tool.build_review_packet("extract_statements", source_blocks_per_chunk=1)
        tool.write_agent_decision(
            {
                "stage": "extract_statements",
                "decision": "semantic_review_extract_statements",
                "review_scope": "full",
                "reviewed_chunks": [item["path"] for item in packet["manifest"]["chunks"]],
                "blocking_findings": [],
                "manual_review_items": [],
                "candidate_rerun_items": [],
                "candidate_expand_context_items": [],
                "next_action": "continue",
                "reason": "Test fixture preserves the source statement.",
            }
        )

        missing_clean_output = tool.next_action()
        assert missing_clean_output["orchestration_state"] == "needs_stage_run", missing_clean_output
        assert missing_clean_output["next_action"]["stage"] == "clean_nodes", missing_clean_output
        assert missing_clean_output["action_kind"] == "execute_command"
        assert "run-stage" in missing_clean_output["suggested_command"]
        assert "--stage clean_nodes" in missing_clean_output["suggested_command"]
        assert "HTTP_PROXY" not in missing_clean_output["suggested_command"]

        _write_json(os.path.join(context.output_dir, "unsplit_statement_dict_cleaned.json"), unsplit)
        _write_json(
            os.path.join(context.output_dir, "node_cleaning_report.json"),
            {
                "input_node_count": 1,
                "cleaned_node_count": 1,
                "quarantined_node_count": 0,
                "manual_review_count": 0,
            },
        )
        _write_json(os.path.join(context.output_dir, "node_quarantine.json"), {})
        tool.validate_stage("clean_nodes")
        tool.write_agent_decision({"stage": "clean_nodes", "decision": "continue", "reason": "Cleaning outputs are valid."})

        _write_json(os.path.join(context.output_dir, "statement_without_title_dict.json"), unsplit)
        missing_split_output = tool.next_action()
        assert missing_split_output["orchestration_state"] == "needs_stage_run", missing_split_output
        assert missing_split_output["next_action"]["stage"] == "split_nodes", missing_split_output

        _write_json(os.path.join(context.output_dir, "node_split_dict.json"), {"0": {"sub_nodes": []}})
        tool.validate_stage("split_nodes")
        tool.write_agent_decision({"stage": "split_nodes", "decision": "continue", "reason": "Split outputs are valid."})

        result = tool.next_action()
        assert result["orchestration_state"] == "needs_stage_run", result
        assert result["next_action"]["stage"] == "generate_titles", result
        assert result["action_kind"] == "execute_command"
        assert ".codex" in result["suggested_command"]
        assert "--stage generate_titles" in result["suggested_command"]
    finally:
        tmp.cleanup()


def test_write_agent_decision_records_claude_decision():
    tmp, _context, tool = _tool_fixture()
    try:
        result = tool.write_agent_decision(
            {
                "stage": "extract_statements",
                "decision": "reuse_cache",
                "reason": "Claude Code judged the cache sufficient for this run.",
            }
        )

        assert result["command"] == "write-agent-decision"
        assert result["decision_count"] == 1
        assert result["record"]["decision_source"] == "claude_code_main_agent"
    finally:
        tmp.cleanup()


def test_build_review_packet_covers_extract_statement_cache_without_decisions():
    tmp, context, tool = _tool_fixture()
    try:
        _write_json(
            os.path.join(context.output_dir, "problem_dict.json"),
            {
                "0": {"pos1": "Theorem 1. If A then B. Proof. Assume A, hence B."},
                "1": {"pos1": "Definition 2. A C-object is an object with property C."},
            },
        )
        _write_json(
            os.path.join(context.output_dir, "unsplit_statement_dict.json"),
            {
                "0": {
                    "pos1": {
                        "node_type": "Theorem",
                        "content": "If A then B.",
                        "proof": "Assume A, hence B.",
                        "label": "Theorem 1",
                    },
                    "_orig_key": "0",
                },
                "1": {
                    "pos1": {
                        "node_type": "Definition",
                        "content": "A C-object is an object with property C.",
                        "proof": "",
                        "label": "Definition 2",
                    },
                    "_orig_key": "1",
                },
                "2": {
                    "pos1": {
                        "node_type": "Remark",
                        "content": "An orphan extracted node.",
                        "proof": "",
                        "label": "",
                    },
                    "_orig_key": "missing",
                },
            },
        )

        result = tool.build_review_packet("extract_statements", source_blocks_per_chunk=1)
        manifest = result["manifest"]
        assert result["command"] == "build-review-packet"
        assert manifest["review_scope"] == "full"
        assert manifest["source_block_count"] == 2
        assert manifest["extracted_node_count"] == 3
        assert manifest["orphan_node_count"] == 1
        assert manifest["coverage"]["source_blocks_included"] == 2
        assert manifest["coverage"]["extracted_nodes_included"] == 3
        assert len(manifest["chunks"]) == 3

        covered_nodes = 0
        covered_source_blocks = 0
        for chunk in manifest["chunks"]:
            with open(chunk["path"], "r", encoding="utf-8") as handle:
                chunk_data = json.load(handle)
            covered_nodes += sum(record["extracted_node_count"] for record in chunk_data["records"])
            covered_source_blocks += sum(1 for record in chunk_data["records"] if record["record_type"] == "source_block")

        assert covered_nodes == 3
        assert covered_source_blocks == 2

        serialized = json.dumps(result, ensure_ascii=False)
        assert "should_continue" not in serialized
        assert "should_rerun" not in serialized
        assert "current_stage" not in serialized
    finally:
        tmp.cleanup()


def test_run_report_includes_review_packet_summary():
    tmp, context, tool = _tool_fixture()
    try:
        _write_json(os.path.join(context.output_dir, "problem_dict.json"), {"0": {"pos1": "Theorem 1. If A then B."}})
        _write_json(
            os.path.join(context.output_dir, "unsplit_statement_dict.json"),
            {
                "0": {
                    "pos1": {"node_type": "Theorem", "content": "If A then B.", "proof": "", "label": "Theorem 1"},
                    "_orig_key": "0",
                }
            },
        )
        tool.build_review_packet("extract_statements", source_blocks_per_chunk=2)
        tool.write_agent_decision(
            {
                "stage": "extract_statements",
                "decision": "semantic_review_extract_statements",
                "review_scope": "full",
                "reviewed_chunks": ["chunk_0001.json"],
                "semantic_findings": [],
                "blocking_findings": [],
                "manual_review_items": [],
                "candidate_rerun_items": [
                    {"source_block_key": "0", "node_index": "0", "issue_type": "manual_check", "evidence": "demo"}
                ],
                "candidate_expand_context_items": [],
                "next_action": "continue",
                "reason": "test semantic review decision",
            }
        )
        report = tool.write_run_report()

        with open(report["run_report_json"], "r", encoding="utf-8") as handle:
            data = json.load(handle)
        assert "review_packets" in data
        assert data["review_packets"]["extract_statements"]["source_block_count"] == 1
        assert len(data["semantic_review_decisions"]) == 1
        assert len(data["reserved_rerun_candidates"]) == 1
    finally:
        tmp.cleanup()


def test_claude_cli_engine_batches_candidates_without_real_claude():
    tmp = tempfile.TemporaryDirectory()
    try:
        fake_claude = os.path.join(tmp.name, "fake_claude.py")
        _write_fake_claude(fake_claude)

        result = run_multiprocess_task(
            llm=None,
            parse_method=json.loads,
            data_template='{"node_type":"...","content":"...","proof":"...","label":"..."}',
            prompt_template="Extract nodes from: {pos1}\n{data_template}",
            correction_template="Fix this answer: {answer}",
            validator=lambda value: isinstance(value, dict),
            index_dict={
                "0": {"pos1": "Theorem 0. If A then B."},
                "1": {"pos1": "Theorem 1. If C then D."},
            },
            num_threads=32,
            checkpoint=1,
            checkpoint_dir=os.path.join(tmp.name, "checkpoint"),
            engine="claude_cli",
            stage_name="extract_statements",
            output_dir=tmp.name,
            claude_command=[sys.executable, fake_claude],
            claude_batch_size=2,
            claude_timeout_seconds=30,
            claude_max_retries=0,
        )

        assert set(result.keys()) == {"0", "1"}
        assert result["0"]["0"]["node_type"] == "Theorem"
        run_root = os.path.join(tmp.name, "agent_state", "subagent_runs", "extract_statements")
        assert os.path.isdir(run_root)
    finally:
        tmp.cleanup()


def test_claude_cli_engine_keeps_large_source_block_as_single_task():
    tmp = tempfile.TemporaryDirectory()
    try:
        fake_claude = os.path.join(tmp.name, "fake_claude.py")
        _write_fake_claude(fake_claude)
        large_text = "Theorem 0. " + ("If A then B. " * 900)

        result = run_multiprocess_task(
            llm=None,
            parse_method=json.loads,
            data_template='{"node_type":"...","content":"...","proof":"...","label":"..."}',
            prompt_template="Extract nodes from: {pos1}\n{data_template}",
            correction_template="Fix this answer: {answer}",
            validator=lambda value: isinstance(value, dict),
            index_dict={"0": {"pos1": large_text}},
            num_threads=32,
            checkpoint=1,
            checkpoint_dir=os.path.join(tmp.name, "checkpoint"),
            engine="claude_cli",
            stage_name="extract_statements",
            output_dir=tmp.name,
            claude_command=[sys.executable, fake_claude],
            claude_batch_size=8,
            claude_timeout_seconds=30,
            claude_max_retries=0,
        )

        assert set(result.keys()) == {"0"}
        assert isinstance(result["0"], dict)
        assert len(result["0"]) == 1
        assert result["0"]["0"]["label"] == "Theorem 0"

        run_root = os.path.join(tmp.name, "agent_state", "subagent_runs", "extract_statements")
        task_packets = []
        for root, _dirs, files in os.walk(run_root):
            assert "split_manifest.json" not in files
            if "task_packet.json" in files:
                task_packets.append(os.path.join(root, "task_packet.json"))
        assert len(task_packets) == 1
        with open(task_packets[0], "r", encoding="utf-8") as handle:
            task_packet = json.load(handle)
        assert task_packet["output_contract"]["top_level_keys"] == ["0"]
        assert len(task_packet["source_blocks"]) == 1
        assert task_packet["source_blocks"][0]["source_block_key"] == "0"
        assert task_packet["source_blocks"][0]["payload"]["pos1"] == large_text
    finally:
        tmp.cleanup()


def test_claude_cli_engine_keeps_existing_path_string_as_one_command():
    tmp = tempfile.TemporaryDirectory()
    try:
        fake_claude = os.path.join(tmp.name, "fake_claude.py")
        _write_fake_claude(fake_claude)
        engine = ClaudeCliEngine(
            stage_name="extract_statements",
            output_dir=tmp.name,
            command=fake_claude,
        )

        assert engine._command_prefix() == [fake_claude]
    finally:
        tmp.cleanup()


def test_extract_statements_stage_can_use_claude_cli_engine_with_existing_output_shape():
    tmp = tempfile.TemporaryDirectory()
    try:
        fake_claude = os.path.join(tmp.name, "fake_claude.py")
        _write_fake_claude(fake_claude)
        output_dir = os.path.join(tmp.name, "out")
        input_path = os.path.join(tmp.name, "input.md")
        with open(input_path, "w", encoding="utf-8") as handle:
            handle.write("Theorem 0. If A then B.")

        context = PipelineContext(
            file_path=input_path,
            output_node_path=os.path.join(output_dir, "nodes.json"),
            output_edge_path=os.path.join(output_dir, "edges.json"),
            llm=object(),
            llm_engine="claude_cli",
            claude_command=[sys.executable, fake_claude],
            claude_batch_size=2,
            claude_timeout_seconds=30,
            claude_max_retries=0,
        )
        state = {"problem_dict": {"0": {"pos1": "Theorem 0. If A then B."}}}

        with contextlib.redirect_stdout(io.StringIO()):
            result_state = extract_statements_stage.run(context, state)

        nodes = result_state["unsplit_statement_dict"]
        assert len(nodes) == 1
        assert nodes[0]["pos1"]["node_type"] == "theorem"
        assert nodes[0]["_orig_key"] == "0"
        output_path = os.path.join(context.output_dir, "unsplit_statement_dict.json")
        assert os.path.exists(output_path)
    finally:
        tmp.cleanup()


def test_claude_cli_engine_rejected_for_unadapted_stage():
    tmp, context, tool = _tool_fixture()
    try:
        context.llm_engine = "claude_cli"
        try:
            tool.run_stage("split_nodes")
        except ValueError as exc:
            assert "only implemented for extract_statements" in str(exc)
        else:
            raise AssertionError("Expected claude_cli to be rejected for unadapted stage")
    finally:
        tmp.cleanup()


def test_repair_context_locates_split_label_without_using_full_problem_block():
    tmp, context, tool = _tool_fixture()
    try:
        _write_json(
            os.path.join(context.output_dir, "corrected_text_dict.json"),
            {
                "0": {
                    "pos1": {
                        "0": 'r"""Unrelated opening."""',
                        "1": 'r"""(1."""',
                        "2": 'r"""2) Let H be normal in G."""',
                        "3": 'r"""Then chi(g)=0."""',
                        "4": 'r"""Proof. Local proof."""',
                        "5": 'r"""Unrelated closing."""',
                    }
                }
            },
        )
        full_block = "FULL_BLOCK_SENTINEL " * 1000
        _write_json(os.path.join(context.output_dir, "problem_dict.json"), {"0": {"pos1": full_block}})
        _write_json(os.path.join(context.output_dir, "unsplit_statement_dict.json"), {})
        result = tool.locate_repair_context(
            {
                "stage": "extract_statements",
                "source_block_key": "0",
                "issue_type": "missing_nodes",
                "anchor_texts": [],
                "expected_labels": ["(1.2)"],
            }
        )
        packet = result["source_packet"]
        assert packet["context_source"] == "corrected_text_dict"
        assert packet["requires_main_agent_confirmation"] is False
        assert len(packet["contexts"]) == 1
        localized = packet["contexts"][0]["localized_source_text"]
        assert "(1.2)" in localized.replace('r"""', "")
        assert "FULL_BLOCK_SENTINEL" not in localized

        prompt_result = tool.build_repair_prompt(result["repair_id"])
        prompt = open(prompt_result["prompts"][0]["path"], encoding="utf-8").read()
        assert "FULL_BLOCK_SENTINEL" not in prompt
        assert "Return the complete repaired node set for this localized context" in prompt
    finally:
        tmp.cleanup()


def test_claude_cli_engine_resolves_default_windows_command_to_claude_cmd():
    if os.name != "nt":
        return
    resolved = shutil.which("claude.cmd")
    if not resolved:
        return
    tmp = tempfile.TemporaryDirectory()
    try:
        engine = ClaudeCliEngine(
            stage_name="extract_statements",
            output_dir=tmp.name,
            command="claude",
        )
        assert os.path.normcase(engine._command_prefix()[0]) == os.path.normcase(resolved)
    finally:
        tmp.cleanup()


def test_claude_cli_engine_passes_explicit_model_to_cli():
    tmp = tempfile.TemporaryDirectory()
    try:
        engine = ClaudeCliEngine(
            stage_name="extract_statements",
            output_dir=tmp.name,
            command="claude",
            model="deepseek-v4-flash",
        )
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            return SimpleNamespace(stdout='{"result":"{}"}', stderr="", returncode=0)

        original_run = subprocess.run
        subprocess.run = fake_run
        try:
            engine._call_claude("Return JSON")
        finally:
            subprocess.run = original_run

        model_index = captured["command"].index("--model")
        assert captured["command"][model_index + 1] == "deepseek-v4-flash"
    finally:
        tmp.cleanup()


def test_extract_statements_cli_failure_preserves_canonical_cache():
    tmp = tempfile.TemporaryDirectory()
    try:
        failing_claude = os.path.join(tmp.name, "failing_claude.py")
        _write_failing_claude(failing_claude)
        output_dir = os.path.join(tmp.name, "out")
        input_path = os.path.join(tmp.name, "input.md")
        with open(input_path, "w", encoding="utf-8") as handle:
            handle.write("Theorem 0. If A then B.")
        context = PipelineContext(
            file_path=input_path,
            output_node_path=os.path.join(output_dir, "nodes.json"),
            output_edge_path=os.path.join(output_dir, "edges.json"),
            llm=object(),
            llm_engine="claude_cli",
            claude_command=[sys.executable, failing_claude],
            claude_batch_size=1,
            claude_timeout_seconds=30,
            claude_max_retries=0,
        )
        canonical_path = os.path.join(context.output_dir, "unsplit_statement_dict.json")
        canonical = {
            "0": {
                "pos1": {"node_type": "Theorem", "content": "Existing node.", "proof": "", "label": "Theorem 0"},
                "_orig_key": "0",
            }
        }
        _write_json(canonical_path, canonical)

        try:
            extract_statements_stage.run(
                context,
                {"problem_dict": {"0": {"pos1": "Theorem 0. If A then B."}}},
            )
        except RuntimeError as exc:
            assert "protect the canonical cache" in str(exc)
        else:
            raise AssertionError("Expected total Claude CLI failure to reject the stage result")

        with open(canonical_path, encoding="utf-8") as handle:
            assert json.load(handle) == canonical
    finally:
        tmp.cleanup()


def test_write_agent_decision_deduplicates_identical_decision():
    tmp, _context, tool = _tool_fixture()
    try:
        decision = {
            "stage": "extract_statements",
            "decision": "rerun_stage",
            "reason": "Same rerun request.",
        }
        first = tool.write_agent_decision(decision)
        second = tool.write_agent_decision(decision)

        assert first["deduplicated"] is False
        assert second["deduplicated"] is True
        assert second["decision_count"] == 1
        assert second["record"]["decision_id"] == first["record"]["decision_id"]
    finally:
        tmp.cleanup()


def test_write_agent_decision_allows_same_judgment_for_new_state():
    tmp, context, tool = _tool_fixture()
    try:
        state_path = os.path.join(context.output_dir, "agent_state", "orchestration_state.json")
        decision = {
            "stage": "extract_statements",
            "decision": "semantic_review_extract_statements",
            "reason": "Same judgment after a new review packet.",
        }
        _write_json(state_path, {"state_fingerprint": "state-a"})
        first = tool.write_agent_decision(decision)
        _write_json(state_path, {"state_fingerprint": "state-b"})
        second = tool.write_agent_decision(decision)

        assert first["deduplicated"] is False
        assert second["deduplicated"] is False
        assert second["decision_count"] == 2
        assert first["record"]["consumes_state_fingerprint"] == "state-a"
        assert second["record"]["consumes_state_fingerprint"] == "state-b"
    finally:
        tmp.cleanup()


def test_api_repair_run_writes_sidecar_candidate_without_modifying_canonical():
    tmp, context, tool = _tool_fixture()
    try:
        context.llm = _FakeRepairLLM()
        context.parser = _JsonParser()
        _write_json(
            os.path.join(context.output_dir, "corrected_text_dict.json"),
            {"0": {"pos1": {"0": 'r"""(1."""', "1": 'r"""2) Theorem source text."""'}}},
        )
        _write_json(os.path.join(context.output_dir, "problem_dict.json"), {"0": {"pos1": "whole source"}})
        canonical = {
            "0": {
                "pos1": {"node_type": "Theorem", "content": "Old content.", "proof": "", "label": "(1.2)"},
                "_orig_key": "0",
            }
        }
        canonical_path = os.path.join(context.output_dir, "unsplit_statement_dict.json")
        _write_json(canonical_path, canonical)

        result = tool.rerun_extract_statements(
            {
                "stage": "extract_statements",
                "source_block_key": "0",
                "issue_type": "wrong_content",
                "expected_labels": ["(1.2)"],
            }
        )

        with open(canonical_path, encoding="utf-8") as handle:
            assert json.load(handle) == canonical
        with open(result["candidate_path"], encoding="utf-8") as handle:
            candidate = json.load(handle)
        assert len(candidate) == 1
        assert candidate["0"]["_orig_key"] == "0"
        assert candidate["0"]["pos1"]["label"] == "(1.2)"
        assert len(context.llm.prompts) == 1
        assert os.path.exists(result["review_packet_manifest"])
    finally:
        tmp.cleanup()


def test_apply_repair_uses_affected_indices_from_repair_intent():
    tmp, context, tool = _tool_fixture()
    try:
        context.llm = _FakeRepairLLM()
        context.parser = _JsonParser()
        _write_json(
            os.path.join(context.output_dir, "corrected_text_dict.json"),
            {"0": {"pos1": {"0": 'r"""(1."""', "1": 'r"""2) Theorem source text."""'}}},
        )
        _write_json(os.path.join(context.output_dir, "problem_dict.json"), {"0": {"pos1": "whole source"}})
        canonical_path = os.path.join(context.output_dir, "unsplit_statement_dict.json")
        _write_json(
            canonical_path,
            {
                "0": {
                    "pos1": {"node_type": "Theorem", "content": "Before.", "proof": "", "label": "(1.1)"},
                    "_orig_key": "before",
                },
                "1": {
                    "pos1": {"node_type": "Theorem", "content": "Old content.", "proof": "", "label": ""},
                    "_orig_key": "0",
                },
                "2": {
                    "pos1": {"node_type": "Theorem", "content": "After.", "proof": "", "label": "(1.3)"},
                    "_orig_key": "after",
                }
            },
        )
        candidate = tool.rerun_extract_statements(
            {
                "stage": "extract_statements",
                "source_block_key": "0",
                "issue_type": "wrong_content",
                "expected_labels": ["(1.2)"],
                "affected_node_indices": ["1"],
            }
        )

        result = tool.apply_repair(
            candidate["repair_id"],
            {
                "stage": "extract_statements",
                "decision": "apply_repair",
                "repair_id": candidate["repair_id"],
                "approved": True,
            },
        )

        assert result["removed_node_indices"] == ["1"]
        with open(canonical_path, encoding="utf-8") as handle:
            merged = json.load(handle)
        assert len(merged) == 3
        assert merged["0"]["pos1"]["content"] == "Before."
        assert merged["1"]["pos1"]["label"] == "(1.2)"
        assert merged["2"]["pos1"]["content"] == "After."
    finally:
        tmp.cleanup()


def test_repair_run_reuses_identical_intent_and_engine():
    tmp, context, tool = _tool_fixture()
    try:
        context.llm = _FakeRepairLLM()
        context.parser = _JsonParser()
        _write_json(
            os.path.join(context.output_dir, "corrected_text_dict.json"),
            {"0": {"pos1": {"0": 'r"""(1."""', "1": 'r"""2) Theorem source text."""'}}},
        )
        _write_json(os.path.join(context.output_dir, "problem_dict.json"), {"0": {"pos1": "whole source"}})
        _write_json(os.path.join(context.output_dir, "unsplit_statement_dict.json"), {})
        intent = {
            "stage": "extract_statements",
            "source_block_key": "0",
            "issue_type": "missing_nodes",
            "expected_labels": ["(1.2)"],
        }

        first = tool.rerun_extract_statements(intent)
        second = tool.rerun_extract_statements(intent)

        assert second["reused_existing"] is True
        assert second["repair_id"] == first["repair_id"]
        assert len(context.llm.prompts) == 1

        forced = tool.rerun_extract_statements({**intent, "force_new_attempt": True})
        assert forced["reused_existing"] is False
        assert forced["repair_id"] != first["repair_id"]
        assert len(context.llm.prompts) == 2
    finally:
        tmp.cleanup()


def test_next_action_blocking_review_requires_user_confirmation_and_guards_repeat():
    tmp, context, tool = _tool_fixture()
    try:
        _write_json(os.path.join(context.output_dir, "corrected_text_dict.json"), {"0": {"pos1": {"0": "source"}}})
        _write_json(os.path.join(context.output_dir, "problem_dict.json"), {"0": {"pos1": "Theorem 1. Source."}})
        _write_json(
            os.path.join(context.output_dir, "unsplit_statement_dict.json"),
            {
                "0": {
                    "pos1": {"node_type": "Theorem", "content": "Source.", "proof": "", "label": "Theorem 1"},
                    "_orig_key": "0",
                }
            },
        )
        tool.validate_stage("extract_statements")
        tool.build_review_packet("extract_statements", source_blocks_per_chunk=1)
        tool.write_agent_decision(
            {
                "stage": "extract_statements",
                "decision": "semantic_review_extract_statements",
                "review_scope": "full",
                "reviewed_chunks": ["chunk_0001.json"],
                "blocking_findings": ["Missing proof boundary."],
                "candidate_rerun_items": [{"source_block_key": "0", "reason": "repairable"}],
                "candidate_expand_context_items": [],
                "next_action": "pause",
                "reason": "Blocked.",
            }
        )
        first = tool.next_action()
        second = tool.next_action()

        assert first["orchestration_state"] == "blocking_review_needs_user_confirmation", first
        assert first["structural_status"] == "valid"
        assert first["semantic_status"] == "blocked"
        assert first["next_action"]["action"] == "request_user_confirmation_for_repair"
        assert first["next_action"]["required_decision"] == "repair_intent_or_manual_pause"
        assert first["next_action"]["blocking_findings"] == ["Missing proof boundary."]
        assert first["next_action"]["candidate_rerun_items"] == [{"source_block_key": "0", "reason": "repairable"}]
        assert first["action_kind"] == "ask_user"
        assert first["suggested_command"] is None
        assert "Missing proof boundary" in first["user_confirmation_prompt"]
        assert first["repeat_guard"]["triggered"] is False
        assert second["orchestration_state"] == first["orchestration_state"]
        assert second["next_action"] is None
        assert second["action_kind"] == "stop"
        assert second["suggested_command"] is None
        assert second["repeat_guard"]["triggered"] is True
    finally:
        tmp.cleanup()


def test_next_action_requires_validation_and_agent_judgment_before_next_stage():
    tmp, context, tool = _tool_fixture()
    try:
        _write_json(os.path.join(context.output_dir, "corrected_text_dict.json"), {"0": {"pos1": {"0": "source"}}})

        needs_validation = tool.next_action()
        assert needs_validation["orchestration_state"] == "needs_stage_structural_validation"
        assert needs_validation["next_action"]["stage"] == "correct_text"
        assert needs_validation["action_kind"] == "execute_command"
        assert "validate-stage" in needs_validation["suggested_command"]
        assert "--stage correct_text" in needs_validation["suggested_command"]
        assert "HTTP_PROXY" not in needs_validation["suggested_command"]

        tool.validate_stage("correct_text")
        needs_judgment = tool.next_action()
        assert needs_judgment["orchestration_state"] == "stage_quality_needs_agent_judgment"
        assert needs_judgment["next_action"]["required_decision"].startswith("reuse_cache")
        assert needs_judgment["action_kind"] == "agent_decision"
        assert needs_judgment["suggested_command"] is None

        tool.write_agent_decision(
            {
                "stage": "correct_text",
                "decision": "continue",
                "reason": "Corrected text is complete enough for segmentation.",
            }
        )
        next_stage = tool.next_action()
        assert next_stage["orchestration_state"] == "needs_stage_run"
        assert next_stage["next_action"]["stage"] == "segment_blocks"
    finally:
        tmp.cleanup()


def test_next_action_advances_repair_candidate_state_machine():
    tmp, context, tool = _tool_fixture()
    try:
        context.llm = _FakeRepairLLM()
        context.parser = _JsonParser()
        _write_json(
            os.path.join(context.output_dir, "corrected_text_dict.json"),
            {"0": {"pos1": {"0": 'r"""(1."""', "1": 'r"""2) Theorem source text."""'}}},
        )
        _write_json(os.path.join(context.output_dir, "problem_dict.json"), {"0": {"pos1": "whole source"}})
        _write_json(
            os.path.join(context.output_dir, "unsplit_statement_dict.json"),
            {
                "0": {
                    "pos1": {"node_type": "Theorem", "content": "Old.", "proof": "", "label": "(1.2)"},
                    "_orig_key": "0",
                }
            },
        )
        tool.validate_stage("extract_statements")
        tool.build_review_packet("extract_statements", source_blocks_per_chunk=1)
        tool.write_agent_decision(
            {
                "stage": "extract_statements",
                "decision": "semantic_review_extract_statements",
                "review_scope": "full",
                "reviewed_chunks": ["chunk_0001.json"],
                "blocking_findings": ["Wrong content."],
                "candidate_rerun_items": [{"source_block_key": "0", "reason": "wrong content"}],
                "candidate_expand_context_items": [],
                "next_action": "rerun_stage",
                "reason": "Blocked.",
            }
        )
        intent = {
            "stage": "extract_statements",
            "source_block_key": "0",
            "issue_type": "wrong_content",
            "evidence": "Old content is incomplete.",
            "expected_labels": ["(1.2)"],
        }
        tool.write_agent_decision(
            {
                "stage": "extract_statements",
                "decision": "repair_intent",
                "user_confirmed": True,
                "repair_intent": intent,
                "reason": "Repair the localized node.",
            }
        )

        before_candidate = tool.next_action()
        assert before_candidate["orchestration_state"] == "repair_intent_needs_candidate", before_candidate
        repair = tool.rerun_extract_statements(intent)
        after_candidate = tool.next_action()
        assert after_candidate["orchestration_state"] == "candidate_generated_needs_review"
        assert after_candidate["next_action"]["repair_id"] == repair["repair_id"]

        time.sleep(0.01)
        tool.write_agent_decision(
            {
                "stage": "extract_statements",
                "decision": "candidate_review_extract_statements",
                "repair_id": repair["repair_id"],
                "approved": True,
                "reason": "Candidate preserves the complete theorem.",
            }
        )
        after_review = tool.next_action()
        assert after_review["orchestration_state"] == "candidate_reviewed_needs_apply"
        assert after_review["next_action"]["command"] == "apply-repair"
    finally:
        tmp.cleanup()


def test_next_action_repair_intent_without_user_confirmation_stays_blocked():
    tmp, context, tool = _tool_fixture()
    try:
        _write_json(os.path.join(context.output_dir, "corrected_text_dict.json"), {"0": {"pos1": {"0": "source"}}})
        _write_json(os.path.join(context.output_dir, "problem_dict.json"), {"0": {"pos1": "Theorem 1. Source."}})
        _write_json(
            os.path.join(context.output_dir, "unsplit_statement_dict.json"),
            {
                "0": {
                    "pos1": {"node_type": "Theorem", "content": "Source.", "proof": "", "label": "Theorem 1"},
                    "_orig_key": "0",
                }
            },
        )
        tool.validate_stage("extract_statements")
        tool.build_review_packet("extract_statements", source_blocks_per_chunk=1)
        tool.write_agent_decision(
            {
                "stage": "extract_statements",
                "decision": "semantic_review_extract_statements",
                "review_scope": "full",
                "reviewed_chunks": ["chunk_0001.json"],
                "blocking_findings": ["Missing theorem."],
                "candidate_rerun_items": [{"source_block_key": "0"}],
                "candidate_expand_context_items": [],
                "next_action": "pause",
                "reason": "Blocked.",
            }
        )
        tool.write_agent_decision(
            {
                "stage": "extract_statements",
                "decision": "repair_intent",
                "repair_intent": {
                    "stage": "extract_statements",
                    "source_block_key": "0",
                    "issue_type": "missing_nodes",
                    "expected_labels": ["Theorem 1"],
                },
                "reason": "Repair is proposed but not confirmed.",
            }
        )

        action = tool.next_action()
        assert action["orchestration_state"] == "blocking_review_needs_user_confirmation"
        assert action["next_action"]["command"] == "write-agent-decision"
        assert action["next_action"]["required_decision"] == "repair_intent_or_manual_pause"
        assert action["evidence_refs"]["unconfirmed_repair_intent"]
    finally:
        tmp.cleanup()


def test_next_action_user_declined_repair_allows_report():
    tmp, context, tool = _tool_fixture()
    try:
        _write_json(os.path.join(context.output_dir, "corrected_text_dict.json"), {"0": {"pos1": {"0": "source"}}})
        _write_json(os.path.join(context.output_dir, "problem_dict.json"), {"0": {"pos1": "Theorem 1. Source."}})
        _write_json(
            os.path.join(context.output_dir, "unsplit_statement_dict.json"),
            {
                "0": {
                    "pos1": {"node_type": "Theorem", "content": "Source.", "proof": "", "label": "Theorem 1"},
                    "_orig_key": "0",
                }
            },
        )
        tool.validate_stage("extract_statements")
        tool.build_review_packet("extract_statements", source_blocks_per_chunk=1)
        tool.write_agent_decision(
            {
                "stage": "extract_statements",
                "decision": "semantic_review_extract_statements",
                "review_scope": "full",
                "reviewed_chunks": ["chunk_0001.json"],
                "blocking_findings": ["Missing theorem."],
                "candidate_rerun_items": [{"source_block_key": "0"}],
                "candidate_expand_context_items": [],
                "next_action": "pause",
                "reason": "Blocked.",
            }
        )
        time.sleep(0.01)
        tool.write_agent_decision(
            {
                "stage": "extract_statements",
                "decision": "pause",
                "reason": "User declined repair after blocking semantic review.",
            }
        )

        action = tool.next_action()
        assert action["orchestration_state"] == "user_declined_repair_needs_report"
        assert action["next_action"]["command"] == "write-run-report"
        assert action["action_kind"] == "execute_command"
        assert "write-run-report" in action["suggested_command"]
    finally:
        tmp.cleanup()


def test_skill_instructions_forbid_self_recursion_and_task_control_flow():
    for host in (".codex", ".claude"):
        skill_dir = os.path.join(ROOT, host, "skills", "mathkg-process")
        text = open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8").read()
        state_machine = open(
            os.path.join(skill_dir, "references", "state-machine.md"),
            encoding="utf-8",
        ).read()
        review = open(
            os.path.join(skill_dir, "references", "extract-statements-review.md"),
            encoding="utf-8",
        ).read()
        repair = open(
            os.path.join(skill_dir, "references", "repair-loop.md"),
            encoding="utf-8",
        ).read()
        commands = open(
            os.path.join(skill_dir, "references", "commands.md"),
            encoding="utf-8",
        ).read()
        assert 'Skill("mathkg-process")' in text
        assert "Never use TaskCreate, TaskUpdate" in text
        assert "Proxy bypass is built into stage execution tools" in text
        assert "execute exactly the returned `suggested_command`" in text
        assert "Do not emit progress text that lacks a concrete stage/action" in text
        assert "Do not use todo updates as pipeline progress" in text
        assert "repeat_guard.triggered" in text
        assert "`next-action`" in text
        assert "references/state-machine.md" in text
        assert "references/extract-statements-review.md" in text
        assert "references/repair-loop.md" in text
        assert "references/commands.md" in text
        assert "Default: `--llm-engine api`" in text
        assert "`--llm-engine claude_cli`" in text
        assert "First-version rule" not in text
        assert "blocking_review_needs_user_confirmation" in state_machine
        assert "Prefer the returned `suggested_command`" in state_machine
        assert "If `action_kind` is `ask_user`" in state_machine
        assert "`user_confirmed: true`" in state_machine
        assert "Calling `rerun-extract-statements` before confirmation" in state_machine
        assert "Read every chunk in the manifest. Do not sample." in review
        assert "candidate_rerun_items" in review
        assert "Python tools execute: context location" in repair
        assert "--llm-engine api" in commands
        assert "--llm-engine claude_cli" in commands
        assert "Stage tools clear proxy environment variables internally" in commands
        assert "$env:HTTP_PROXY=''" in commands


def test_apply_repair_requires_approval_and_replaces_covered_label_only():
    tmp, context, tool = _tool_fixture()
    try:
        context.llm = _FakeRepairLLM()
        context.parser = _JsonParser()
        _write_json(
            os.path.join(context.output_dir, "corrected_text_dict.json"),
            {"0": {"pos1": {"0": 'r"""(1."""', "1": 'r"""2) Theorem source text."""'}}},
        )
        _write_json(os.path.join(context.output_dir, "problem_dict.json"), {"0": {"pos1": "whole source"}})
        _write_json(
            os.path.join(context.output_dir, "unsplit_statement_dict.json"),
            {
                "0": {
                    "pos1": {"node_type": "Theorem", "content": "Old content.", "proof": "", "label": "(1.2)"},
                    "_orig_key": "0",
                },
                "1": {
                    "pos1": {"node_type": "Theorem", "content": "Keep content.", "proof": "", "label": "(1.3)"},
                    "_orig_key": "0",
                },
            },
        )
        repair = tool.rerun_extract_statements(
            {
                "stage": "extract_statements",
                "source_block_key": "0",
                "issue_type": "wrong_content",
                "expected_labels": ["(1.2)"],
            }
        )
        try:
            tool.apply_repair(repair["repair_id"], {"repair_id": repair["repair_id"], "approved": False})
        except ValueError as exc:
            assert "approved=true" in str(exc)
        else:
            raise AssertionError("Expected unapproved repair to be rejected")

        applied = tool.apply_repair(
            repair["repair_id"],
            {
                "stage": "extract_statements",
                "decision": "apply_repair",
                "repair_id": repair["repair_id"],
                "approved": True,
                "reason": "candidate reviewed",
            },
        )
        assert applied["removed_node_indices"] == ["0"]
        with open(applied["canonical_path"], encoding="utf-8") as handle:
            merged = json.load(handle)
        labels = [wrapper["pos1"]["label"] for wrapper in merged.values()]
        assert labels.count("(1.2)") == 1
        assert "(1.3)" in labels
        assert os.path.exists(applied["backup_path"])
    finally:
        tmp.cleanup()


def test_api_and_claude_cli_repair_runs_share_prompt_contract():
    tmp, context, tool = _tool_fixture()
    try:
        fake_claude = os.path.join(tmp.name, "fake_claude.py")
        _write_fake_claude(fake_claude)
        context.llm = _FakeRepairLLM()
        context.parser = _JsonParser()
        _write_json(
            os.path.join(context.output_dir, "corrected_text_dict.json"),
            {"0": {"pos1": {"0": 'r"""(1."""', "1": 'r"""2) Theorem source text."""'}}},
        )
        _write_json(os.path.join(context.output_dir, "problem_dict.json"), {"0": {"pos1": "whole source"}})
        canonical_path = os.path.join(context.output_dir, "unsplit_statement_dict.json")
        _write_json(canonical_path, {})
        intent = {
            "stage": "extract_statements",
            "source_block_key": "0",
            "issue_type": "missing_nodes",
            "expected_labels": ["(1.2)"],
        }

        api_result = tool.rerun_extract_statements(intent)
        api_prompt = open(
            os.path.join(api_result["repair_dir"], "repair_prompt_0001.md"),
            encoding="utf-8",
        ).read()

        context.llm_engine = "claude_cli"
        context.claude_command = [sys.executable, fake_claude]
        context.claude_timeout_seconds = 30
        context.claude_max_retries = 0
        cli_result = tool.rerun_extract_statements(intent)
        cli_prompt = open(
            os.path.join(cli_result["repair_dir"], "repair_prompt_0001.md"),
            encoding="utf-8",
        ).read()

        assert api_prompt == cli_prompt
        assert cli_result["engine"] == "claude_cli"
        assert os.path.exists(
            os.path.join(cli_result["repair_dir"], "engine_runs", "context_0001", "run_meta.json")
        )
        with open(canonical_path, encoding="utf-8") as handle:
            assert json.load(handle) == {}
    finally:
        tmp.cleanup()


def test_repair_run_refuses_automatic_full_problem_block_fallback():
    tmp, context, tool = _tool_fixture()
    try:
        context.llm = _FakeRepairLLM()
        context.parser = _JsonParser()
        _write_json(os.path.join(context.output_dir, "corrected_text_dict.json"), {"0": {"pos1": {"0": "unrelated"}}})
        _write_json(os.path.join(context.output_dir, "problem_dict.json"), {"0": {"pos1": "full problem block"}})
        _write_json(os.path.join(context.output_dir, "unsplit_statement_dict.json"), {})
        try:
            tool.rerun_extract_statements(
                {
                    "stage": "extract_statements",
                    "source_block_key": "0",
                    "issue_type": "missing_nodes",
                    "anchor_texts": ["anchor not found"],
                }
            )
        except ValueError as exc:
            assert "allow_full_problem_block_fallback=true" in str(exc)
        else:
            raise AssertionError("Expected repair run to refuse automatic full-block fallback")
        assert context.llm.prompts == []
        repair_root = os.path.join(
            context.output_dir,
            "agent_state",
            "repair_candidates",
            "extract_statements",
        )
        repair_dirs = [os.path.join(repair_root, name) for name in os.listdir(repair_root)]
        assert len(repair_dirs) == 1
        assert not os.path.exists(os.path.join(repair_dirs[0], "repair_prompt.md"))
    finally:
        tmp.cleanup()


if __name__ == "__main__":
    test_late_resume_script_keeps_logic_ir_side_path_opt_in()
    test_without_proxy_env_temporarily_clears_and_restores_proxy_variables()
    test_run_stage_clears_proxy_environment_inside_stage_and_restores_it()
    test_scan_cache_returns_facts_without_decisions()
    test_logic_ir_stages_are_opt_in_and_flag_propagates_to_commands()
    test_validate_stage_returns_fact_issues_only()
    test_split_nodes_contract_uses_current_outputs_and_restores_state()
    test_clean_nodes_contract_restores_cleaned_unsplit_for_downstream()
    test_split_nodes_contract_rejects_legacy_or_partial_cache()
    test_next_action_advances_from_valid_split_nodes_to_generate_titles()
    test_write_agent_decision_records_claude_decision()
    test_write_agent_decision_deduplicates_identical_decision()
    test_write_agent_decision_allows_same_judgment_for_new_state()
    test_build_review_packet_covers_extract_statement_cache_without_decisions()
    test_run_report_includes_review_packet_summary()
    test_claude_cli_engine_batches_candidates_without_real_claude()
    test_claude_cli_engine_keeps_large_source_block_as_single_task()
    test_claude_cli_engine_keeps_existing_path_string_as_one_command()
    test_claude_cli_engine_resolves_default_windows_command_to_claude_cmd()
    test_claude_cli_engine_passes_explicit_model_to_cli()
    test_extract_statements_cli_failure_preserves_canonical_cache()
    test_extract_statements_stage_can_use_claude_cli_engine_with_existing_output_shape()
    test_claude_cli_engine_rejected_for_unadapted_stage()
    test_repair_context_locates_split_label_without_using_full_problem_block()
    test_api_repair_run_writes_sidecar_candidate_without_modifying_canonical()
    test_repair_run_reuses_identical_intent_and_engine()
    test_next_action_blocking_review_requires_user_confirmation_and_guards_repeat()
    test_next_action_requires_validation_and_agent_judgment_before_next_stage()
    test_next_action_advances_repair_candidate_state_machine()
    test_next_action_repair_intent_without_user_confirmation_stays_blocked()
    test_next_action_user_declined_repair_allows_report()
    test_skill_instructions_forbid_self_recursion_and_task_control_flow()
    test_apply_repair_requires_approval_and_replaces_covered_label_only()
    test_api_and_claude_cli_repair_runs_share_prompt_contract()
    test_repair_run_refuses_automatic_full_problem_block_fallback()
    print("mathkg agent tool tests passed")
