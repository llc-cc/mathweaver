import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ..common.io import write_json
from .control import (
    AGENT_STATE_DIRNAME,
    DOCUMENT_MEMORY_FILENAME,
    EXPERIMENTAL_LOGIC_IR_STAGES,
    NATURAL_EDGE_CACHE_FILENAME,
    QUALITY_LEDGER_FILENAME,
    REPAIR_LITE_NODE_DICT_FILENAME,
    RUN_REPORT_JSON_FILENAME,
    RUN_REPORT_MD_FILENAME,
    STAGE_ORDER,
    STAGE_SPECS,
    STRUCTURED_EDGE_CACHE_FILENAME,
    AgentRunConfig,
    MainAgentController,
    _agent_state_dir,
    _read_json_optional,
)
from .extract_statements_repair import ExtractStatementsRepair
from ..stages.analysis import stage as analysis_stage
from ..stages.build_relations import stage as build_relations_stage
from ..stages.clean_nodes import stage as clean_nodes_stage
from ..stages.compile_logic_form import stage as compile_logic_form_stage
from ..stages.extract_logic_tuples import stage as extract_logic_tuples_stage
from ..stages.extract_statements import stage as extract_statements_stage
from ..stages.generate_titles import stage as generate_titles_stage
from ..stages.math_disambiguation import stage as math_disambiguation_stage
from ..stages.normalize_predicates import stage as normalize_predicates_stage
from ..stages.repair import stage as repair_stage
from ..stages.split_nodes import stage as split_nodes_stage


QUALITY_FACTS_FILENAME = "quality_facts.json"
AGENT_DECISIONS_FILENAME = "agent_decisions.json"
ORCHESTRATION_STATE_FILENAME = "orchestration_state.json"
REVIEW_PACKET_DIRNAME = "review_packets"
DEFAULT_REVIEW_PACKET_SOURCE_BLOCKS_PER_CHUNK = 8
ADJACENT_CONTEXT_SUMMARY_CHARS = 600
STAGE_RUNS_DIRNAME = "stage_runs"
FAILED_TASK_RERUN_STAGES = {
    "extract_logic_tuples": extract_logic_tuples_stage,
    "extract_statements": extract_statements_stage,
    "split_nodes": split_nodes_stage,
    "generate_titles": generate_titles_stage,
    "math_disambiguation": math_disambiguation_stage,
    "compile_logic_form": compile_logic_form_stage,
    "normalize_predicates": normalize_predicates_stage,
    "analysis": analysis_stage,
    "repair": repair_stage,
    "build_relations": build_relations_stage,
}
STALE_CACHE_DIRNAME = "stale_cache"
PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


STAGE_EXTRA_ARTIFACTS = {
    "correct_text": ("correct_text_report.json",),
    "segment_blocks": ("segment_blocks_report.json",),
    "extract_statements": ("extract_statements_report.json",),
    "clean_nodes": (
        "unsplit_statement_dict_raw.json",
        "node_cleaning_decision_dict.json",
        "node_cleaning_report.json",
        "node_quarantine.json",
    ),
    "analysis": ("analysis_debug",),
    "repair": ("repair_input_dict.json", "repair_result_dict.json", "repair_patch_report.json"),
    "repair_lite": (),
    "compile_logic_form": (),
    "normalize_predicates": (
        "predicate_entry_list.json",
        "fixed_operator_rewrite_map.json",
        "predicate_candidate_pairs.json",
        "predicate_candidate_groups.json",
        "predicate_cluster_input_dict.json",
        "predicate_cluster_decisions_raw.json",
        "predicate_cluster_decisions.json",
        "fixed_operator_misuse_report.json",
        "predicate_cluster_decision_normalization_report.json",
        "registry_duplicate_merge_report.json",
        "predicate_rewrite_map.json",
    ),
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def without_proxy_env():
    original = {key: os.environ.get(key) for key in PROXY_ENV_KEYS}
    try:
        for key in PROXY_ENV_KEYS:
            os.environ.pop(key, None)
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _stable_fingerprint(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _timestamp(value):
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _decision_payload(decision):
    return {
        key: value
        for key, value in decision.items()
        if key not in {"recorded_at", "decision_source", "decision_id", "decision_fingerprint"}
    }


def active_stage_order(config):
    stages = []
    for stage in STAGE_ORDER:
        if (
            stage in EXPERIMENTAL_LOGIC_IR_STAGES
            and not config.experimental_logic_ir
        ):
            continue
        if stage == "math_disambiguation" and not config.enable_math_disambiguation:
            continue
        if stage in {"analysis", "repair"} and not config.enable_analysis:
            continue
        stages.append(stage)
    return stages


def agent_state_paths(context):
    state_dir = _agent_state_dir(context)
    return {
        "agent_state_dir": str(state_dir),
        "document_memory": str(state_dir / DOCUMENT_MEMORY_FILENAME),
        "quality_facts": str(state_dir / QUALITY_FACTS_FILENAME),
        "agent_decisions": str(state_dir / AGENT_DECISIONS_FILENAME),
        "orchestration_state": str(state_dir / ORCHESTRATION_STATE_FILENAME),
        "review_packets": str(state_dir / REVIEW_PACKET_DIRNAME),
        "repair_candidates": str(state_dir / "repair_candidates"),
        "stage_runs": str(state_dir / STAGE_RUNS_DIRNAME),
        "stale_cache": str(state_dir / STALE_CACHE_DIRNAME),
        "quality_ledger_legacy": str(state_dir / QUALITY_LEDGER_FILENAME),
        "run_report_json": str(state_dir / RUN_REPORT_JSON_FILENAME),
        "run_report_md": str(state_dir / RUN_REPORT_MD_FILENAME),
    }


def file_fact(path):
    path = Path(path)
    fact = {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": 0,
        "modified_at": None,
        "json_valid": None,
        "json_type": None,
        "item_count": None,
    }
    if not path.exists():
        return fact
    try:
        stat = path.stat()
        fact["size_bytes"] = stat.st_size
        fact["modified_at"] = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
    except OSError:
        return fact
    if path.suffix.lower() == ".json":
        try:
            data = _read_json_optional(path, None)
            fact["json_valid"] = data is not None
            fact["json_type"] = type(data).__name__ if data is not None else None
            if isinstance(data, (dict, list)):
                fact["item_count"] = len(data)
        except Exception:
            fact["json_valid"] = False
    return fact


def _safe_sort_key(value):
    try:
        return (0, int(str(value)))
    except (TypeError, ValueError):
        return (1, str(value))


def _string_key(value):
    return None if value is None else str(value)


def _text_or_json(value):
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _problem_block_text(entry):
    if isinstance(entry, dict) and "pos1" in entry:
        return _text_or_json(entry.get("pos1"))
    return _text_or_json(entry)


def _summary_text(value, limit=ADJACENT_CONTEXT_SUMMARY_CHARS):
    text = " ".join(_text_or_json(value).split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _node_block(wrapper):
    if not isinstance(wrapper, dict):
        return None
    if any(key in wrapper for key in ("node_type", "content", "proof", "label", "title", "remark")):
        return wrapper
    block = wrapper.get("pos1")
    return block if isinstance(block, dict) else None


def _review_node_payload(node_key, wrapper, matched_source_key):
    node = _node_block(wrapper)
    if node is None:
        node = {}
    orig_key = wrapper.get("_orig_key") if isinstance(wrapper, dict) else None
    return {
        "node_index": _string_key(node_key),
        "source_block_key": matched_source_key,
        "raw_source_block_key": _string_key(orig_key),
        "node_type": node.get("node_type"),
        "content": node.get("content"),
        "proof": node.get("proof"),
        "label": node.get("label"),
    }


class AgentTool:
    """Fact and execution tools for Claude Code to use as the main agent."""

    def __init__(self, context, config=None):
        self.context = context
        self.config = config or AgentRunConfig(
            enable_analysis=context.enable_analysis,
            enable_math_disambiguation=context.enable_math_disambiguation,
        )
        self.controller = MainAgentController(context, self.config)
        self.extract_statements_repair = ExtractStatementsRepair(context)

    def scan_cache(self):
        stages = []
        for stage in active_stage_order(self.config):
            files = self._stage_files(stage)
            file_facts = [file_fact(path) for path in files]
            stages.append(
                {
                    "stage": stage,
                    "expected_files": file_facts,
                    "all_expected_files_exist": all(item["exists"] for item in file_facts) if file_facts else None,
                    "metrics": self._stage_cache_metrics(stage),
                }
            )
        document_memory = self._build_document_memory_fact(stages)
        self._write_json(DOCUMENT_MEMORY_FILENAME, document_memory)
        return {
            "command": "scan-cache",
            "generated_at": utc_now(),
            "input_path": self.context.file_path,
            "stage_cache_dir": self.context.output_dir,
            "stage_order": active_stage_order(self.config),
            "stages": stages,
            "agent_state_paths": agent_state_paths(self.context),
        }

    def validate_stage(self, stage):
        self._require_stage(stage)
        state = self.controller.load_state_from_cache()
        raw = self.controller._check_stage(stage, state, source="tool")
        facts = {
            "command": "validate-stage",
            "generated_at": utc_now(),
            "input_path": self.context.file_path,
            "stage_cache_dir": self.context.output_dir,
            "stage": stage,
            "expected_files": [file_fact(path) for path in self._stage_files(stage)],
            "metrics": raw.get("metrics", {}),
            "issues": [self._fact_issue(issue) for issue in raw.get("issues", [])],
        }
        self._upsert_quality_fact(stage, facts)
        return facts

    def build_review_packet(self, stage, source_blocks_per_chunk=DEFAULT_REVIEW_PACKET_SOURCE_BLOCKS_PER_CHUNK):
        self._require_stage(stage)
        if stage != "extract_statements":
            raise ValueError("Review packet generation is only implemented for extract_statements")
        if source_blocks_per_chunk < 1:
            raise ValueError("source_blocks_per_chunk must be >= 1")
        return self._build_extract_statements_review_packet(source_blocks_per_chunk)

    def run_stage(self, stage):
        self._require_stage(stage)
        if getattr(self.context, "llm_engine", "api") == "claude_cli" and stage != "extract_statements":
            raise ValueError("llm_engine=claude_cli is only implemented for extract_statements in this version")
        started_at = time.time()
        state = self.controller.load_state_for_stage(stage)
        with contextlib.redirect_stdout(sys.stderr):
            with without_proxy_env():
                state = self.controller._run_stage(stage, state)
                self.controller._persist_runtime_sidecars(stage, state)
        validation = self.controller._check_stage(stage, state, source="run")
        invalidation = None
        if not any(issue.get("severity") == "error" for issue in validation.get("issues", [])):
            invalidation = self._invalidate_downstream_cache(stage, reason="stage_completed_successfully")
        duration = round(time.time() - started_at, 3)
        result = {
            "command": "run-stage",
            "generated_at": utc_now(),
            "input_path": self.context.file_path,
            "stage_cache_dir": self.context.output_dir,
            "stage": stage,
            "duration_seconds": duration,
            "output_files": [file_fact(path) for path in self._stage_files(stage)],
            "validation": validation,
            "downstream_invalidation": invalidation,
            "note": "Stage internals, prompts, and LLM API calls were executed by the existing pipeline implementation.",
        }
        self._append_execution_fact(result)
        return result

    def rerun_failed_tasks(self, stage):
        self._require_stage(stage)
        adapter = FAILED_TASK_RERUN_STAGES.get(stage)
        if adapter is None or not hasattr(adapter, "rerun_failed_tasks"):
            raise ValueError(f"rerun-failed-tasks is not implemented for stage: {stage}")
        started_at = time.time()
        state = self.controller.load_state_for_stage(stage)
        with contextlib.redirect_stdout(sys.stderr):
            with without_proxy_env():
                state, failure_report = adapter.rerun_failed_tasks(
                    self.context,
                    state,
                    max_rounds=2,
                )
        invalidation = None
        if failure_report.get("status") == "resolved" and failure_report.get("canonical_updated") is True:
            invalidation = self._invalidate_downstream_cache(stage, reason="failed_tasks_resolved")
        result = {
            "command": "rerun-failed-tasks",
            "generated_at": utc_now(),
            "input_path": self.context.file_path,
            "stage_cache_dir": self.context.output_dir,
            "stage": stage,
            "duration_seconds": round(time.time() - started_at, 3),
            "failure_report": failure_report,
            "output_files": [file_fact(path) for path in self._stage_files(stage)],
            "downstream_invalidation": invalidation,
        }
        self._append_execution_fact(result)
        return result

    def locate_repair_context(self, repair_intent):
        return self.extract_statements_repair.locate_context(repair_intent)

    def build_repair_prompt(self, repair_id):
        return self.extract_statements_repair.build_prompt(repair_id)

    def rerun_extract_statements(self, repair_intent):
        with without_proxy_env():
            return self.extract_statements_repair.rerun(repair_intent)

    def build_candidate_review_packet(self, repair_id):
        return self.extract_statements_repair.build_candidate_review_packet(repair_id)

    def apply_repair(self, repair_id, decision):
        return self.extract_statements_repair.apply(repair_id, decision)

    def load_agent_state(self):
        paths = agent_state_paths(self.context)
        return {
            "command": "load-agent-state",
            "generated_at": utc_now(),
            "input_path": self.context.file_path,
            "stage_cache_dir": self.context.output_dir,
            "state": {
                "document_memory": _read_json_optional(paths["document_memory"], {}),
                "quality_facts": _read_json_optional(paths["quality_facts"], {}),
                "agent_decisions": _read_json_optional(paths["agent_decisions"], []),
                "orchestration_state": _read_json_optional(paths["orchestration_state"], {}),
                "review_packets": self._load_review_packet_manifests(),
                "repair_candidates": self.extract_statements_repair.list_candidates(),
                "run_report": _read_json_optional(paths["run_report_json"], {}),
            },
            "agent_state_paths": paths,
        }

    def next_action(self):
        """Return one unconsumed orchestration transition derived from agent decisions and artifacts."""
        assessment = self._orchestration_assessment()
        fingerprint = _stable_fingerprint(
            {
                "state": assessment["state"],
                "frontier_stage": assessment.get("frontier_stage"),
                "pending_action": assessment.get("pending_action"),
                "evidence_refs": assessment.get("evidence_refs"),
            }
        )
        path = _agent_state_dir(self.context) / ORCHESTRATION_STATE_FILENAME
        previous = _read_json_optional(path, {})
        if not isinstance(previous, dict):
            previous = {}
        repeated = previous.get("last_issued_fingerprint") == fingerprint
        state_record = {
            "schema_version": 1,
            "updated_at": utc_now(),
            "state": assessment["state"],
            "frontier_stage": assessment.get("frontier_stage"),
            "state_fingerprint": fingerprint,
            "last_issued_fingerprint": fingerprint,
            "last_issued_action": assessment.get("pending_action"),
            "last_issued_at": previous.get("last_issued_at") if repeated else utc_now(),
            "repeat_guard_triggered": repeated,
        }
        write_json(str(path), state_record)
        pending_action = None if repeated else assessment.get("pending_action")
        action_delivery = self._action_delivery(pending_action, repeated)
        return {
            "command": "next-action",
            "generated_at": utc_now(),
            "orchestration_state": assessment["state"],
            "frontier_stage": assessment.get("frontier_stage"),
            "state_fingerprint": fingerprint,
            "structural_status": assessment.get("structural_status"),
            "semantic_status": assessment.get("semantic_status"),
            "pending_action": assessment.get("pending_action"),
            "next_action": pending_action,
            "action_kind": action_delivery["action_kind"],
            "suggested_command": action_delivery.get("suggested_command"),
            "suggested_command_args": action_delivery.get("suggested_command_args"),
            "user_confirmation_prompt": action_delivery.get("user_confirmation_prompt"),
            "repeat_guard": {
                "triggered": repeated,
                "instruction": (
                    "Stop this invocation and report no progress. Do not repeat scan-cache, load-agent-state, "
                    "validate-stage, create tasks, or relaunch mathkg-process until a decision or artifact changes."
                    if repeated
                    else "Execute only next_action, then call next-action once to observe the new state."
                ),
            },
            "evidence_refs": assessment.get("evidence_refs", {}),
            "written_path": str(path),
        }

    def _action_delivery(self, pending_action, repeated):
        if repeated or not isinstance(pending_action, dict):
            return {"action_kind": "stop"}
        if pending_action.get("action") == "request_user_confirmation_for_repair":
            findings = pending_action.get("blocking_findings") or []
            candidates = pending_action.get("candidate_rerun_items") or []
            return {
                "action_kind": "ask_user",
                "user_confirmation_prompt": (
                    "extract_statements semantic review found blocking issues. "
                    f"Blocking findings: {json.dumps(findings, ensure_ascii=False)}. "
                    f"Proposed repair candidates: {json.dumps(candidates, ensure_ascii=False)}. "
                    "Ask the user whether to write a user_confirmed repair_intent or pause."
                ),
            }
        command_args = self._suggested_command_args(pending_action)
        if command_args is None:
            return {"action_kind": "agent_decision"}
        return {
            "action_kind": "execute_command",
            "suggested_command_args": command_args,
            "suggested_command": subprocess.list2cmdline(command_args),
        }

    def _tool_entry(self):
        return getattr(self.context, "agent_tool_entry", None) or ".codex/skills/mathkg-process/scripts/mathkg_agent_tool.py"

    def _base_command_args(self, subcommand):
        args = ["python", self._tool_entry(), subcommand, self.context.file_path]
        if getattr(self.context, "output_node_path", None):
            args.extend(["--output-node", self.context.output_node_path])
        if getattr(self.context, "output_edge_path", None):
            args.extend(["--output-edge", self.context.output_edge_path])
        if getattr(self.context, "output_natural_node_path", None):
            args.extend(["--output-natural-node", self.context.output_natural_node_path])
        if getattr(self.context, "api_url", None):
            args.extend(["--api-url", self.context.api_url])
        if getattr(self.context, "model_name", None):
            args.extend(["--model-name", self.context.model_name])
        args.extend(["--num-threads", str(getattr(self.context, "num_threads", 32))])
        args.extend(["--checkpoint", str(getattr(self.context, "checkpoint", 500))])
        if self.config.enable_analysis:
            args.append("--enable-analysis")
        if not self.config.enable_math_disambiguation:
            args.append("--disable-math-disambiguation")
        if self.config.experimental_logic_ir:
            args.append("--experimental-logic-ir")
        args.extend(["--edge-output-mode", self.config.edge_output_mode or "structured"])
        args.extend(["--relation-prompt-profile", self.config.relation_prompt_profile or "graph"])
        if getattr(self.context, "source_format", "auto") != "auto":
            args.extend(["--source-format", str(self.context.source_format)])
        if getattr(self.context, "llm_engine", "api") != "api":
            args.extend(["--llm-engine", str(self.context.llm_engine)])
        if getattr(self.context, "claude_command", "claude") != "claude":
            command = self.context.claude_command
            if isinstance(command, (list, tuple)):
                command = " ".join(str(item) for item in command)
            args.extend(["--claude-command", str(command)])
        if getattr(self.context, "claude_model", None):
            args.extend(["--claude-model", str(self.context.claude_model)])
        if getattr(self.context, "claude_agent", None):
            args.extend(["--claude-agent", str(self.context.claude_agent)])
        if getattr(self.context, "claude_batch_size", 8) != 8:
            args.extend(["--claude-batch-size", str(self.context.claude_batch_size)])
        if getattr(self.context, "claude_timeout_seconds", 900) != 900:
            args.extend(["--claude-timeout-seconds", str(self.context.claude_timeout_seconds)])
        if getattr(self.context, "claude_max_retries", 1) != 1:
            args.extend(["--claude-max-retries", str(self.context.claude_max_retries)])
        return args

    def _suggested_command_args(self, pending_action):
        command = pending_action.get("command")
        stage = pending_action.get("stage")
        if command in {"run-stage", "validate-stage", "build-review-packet", "rerun-failed-tasks"}:
            args = self._base_command_args(command)
            if stage:
                args.extend(["--stage", str(stage)])
            return args
        if command == "build-candidate-review-packet":
            args = self._base_command_args(command)
            args.extend(["--repair-id", str(pending_action.get("repair_id"))])
            return args
        if command == "apply-repair":
            args = self._base_command_args(command)
            args.extend(["--repair-id", str(pending_action.get("repair_id"))])
            args.extend(["--decision", json.dumps(pending_action.get("decision") or {}, ensure_ascii=False)])
            return args
        if command == "rerun-extract-statements":
            args = self._base_command_args(command)
            args.extend(["--repair-intent", json.dumps(pending_action.get("repair_intent") or {}, ensure_ascii=False)])
            return args
        if command == "write-run-report":
            return self._base_command_args(command)
        return None

    def write_agent_decision(self, decision):
        if not isinstance(decision, dict):
            raise ValueError("decision must be a JSON object")
        decision = dict(decision)
        stage = decision.get("stage")
        if stage is not None:
            self._require_stage(str(stage))
        if "consumes_state_fingerprint" not in decision:
            orchestration = _read_json_optional(
                _agent_state_dir(self.context) / ORCHESTRATION_STATE_FILENAME,
                {},
            )
            if isinstance(orchestration, dict) and orchestration.get("state_fingerprint"):
                decision["consumes_state_fingerprint"] = orchestration["state_fingerprint"]
        payload = _decision_payload(decision)
        fingerprint = _stable_fingerprint(payload)
        record = {
            "recorded_at": utc_now(),
            "decision_source": "claude_code_main_agent",
            "decision_id": f"decision_{uuid.uuid4().hex}",
            "decision_fingerprint": fingerprint,
            **decision,
        }
        path = _agent_state_dir(self.context) / AGENT_DECISIONS_FILENAME
        decisions = _read_json_optional(path, [])
        if not isinstance(decisions, list):
            decisions = []
        existing = next(
            (
                item
                for item in reversed(decisions)
                if isinstance(item, dict)
                and (
                    item.get("decision_fingerprint") == fingerprint
                    or _stable_fingerprint(_decision_payload(item)) == fingerprint
                )
            ),
            None,
        )
        if existing is not None:
            return {
                "command": "write-agent-decision",
                "generated_at": utc_now(),
                "decision_count": len(decisions),
                "written_path": str(path),
                "deduplicated": True,
                "record": existing,
            }
        decisions.append(record)
        write_json(str(path), decisions)
        return {
            "command": "write-agent-decision",
            "generated_at": utc_now(),
            "decision_count": len(decisions),
            "written_path": str(path),
            "deduplicated": False,
            "record": record,
        }

    def write_run_report(self):
        paths = agent_state_paths(self.context)
        document_memory = _read_json_optional(paths["document_memory"], {})
        quality_facts = _read_json_optional(paths["quality_facts"], {})
        decisions = _read_json_optional(paths["agent_decisions"], [])
        if not isinstance(decisions, list):
            decisions = []
        semantic_reviews = self._semantic_review_decisions(decisions)
        report = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "input_path": self.context.file_path,
            "stage_cache_dir": self.context.output_dir,
            "document_memory": document_memory,
            "quality_facts_summary": self._quality_summary(quality_facts),
            "orchestration_state": _read_json_optional(paths["orchestration_state"], {}),
            "review_packets": self._load_review_packet_manifests(),
            "repair_candidates": self.extract_statements_repair.list_candidates(),
            "semantic_review_decisions": semantic_reviews,
            "reserved_rerun_candidates": self._reserved_rerun_candidates(semantic_reviews),
            "agent_decisions": decisions,
            "note": "Decisions in this report are Claude Code main-agent records; Python tools supplied facts and executed requested actions.",
        }
        write_json(paths["run_report_json"], report)
        Path(paths["run_report_md"]).write_text(
            self._render_report_markdown(report),
            encoding="utf-8",
        )
        return {
            "command": "write-run-report",
            "generated_at": utc_now(),
            "run_report_json": paths["run_report_json"],
            "run_report_md": paths["run_report_md"],
            "decision_count": len(decisions),
        }

    def _orchestration_assessment(self):
        paths = agent_state_paths(self.context)
        decisions = _read_json_optional(paths["agent_decisions"], [])
        if not isinstance(decisions, list):
            decisions = []
        decisions = [item for item in decisions if isinstance(item, dict)]
        quality_facts = _read_json_optional(paths["quality_facts"], {})
        quality_stages = quality_facts.get("stages", {}) if isinstance(quality_facts, dict) else {}
        if not isinstance(quality_stages, dict):
            quality_stages = {}
        review_packets = self._load_review_packet_manifests()
        execution_facts = _read_json_optional(_agent_state_dir(self.context) / "execution_facts.json", [])
        if not isinstance(execution_facts, list):
            execution_facts = []
        candidates = self.extract_statements_repair.list_candidates()
        stage_order = active_stage_order(self.config)

        def latest_stage_decision(stage, *names):
            return next(
                (
                    item
                    for item in reversed(decisions)
                    if item.get("stage") == stage and item.get("decision") in names
                ),
                None,
            )

        def latest_decision(*names):
            return latest_stage_decision("extract_statements", *names)

        def decision_ref(item):
            if not item:
                return None
            return item.get("decision_id") or item.get("decision_fingerprint") or _stable_fingerprint(_decision_payload(item))

        def decision_position(item):
            if item is None:
                return -1
            return next((index for index, candidate in reversed(list(enumerate(decisions))) if candidate is item), -1)

        def result(state, pending_action, structural_status=None, semantic_status=None, **evidence):
            return {
                "state": state,
                "frontier_stage": pending_action.get("stage") if isinstance(pending_action, dict) else "extract_statements",
                "pending_action": pending_action,
                "structural_status": structural_status,
                "semantic_status": semantic_status,
                "evidence_refs": evidence,
            }

        def general_stage_gate(stage):
            facts = [file_fact(path) for path in self._stage_files(stage)]
            if facts and not all(item["exists"] for item in facts):
                return result(
                    "needs_stage_run",
                    {"action": "run_stage", "command": "run-stage", "stage": stage},
                    structural_status="missing_output",
                    semantic_status="not_reached",
                    missing_files=[item["path"] for item in facts if not item["exists"]],
                )
            quality = quality_stages.get(stage, {})
            quality_expected = quality.get("expected_files", []) if isinstance(quality, dict) else []
            quality_by_name = {
                Path(str(item.get("path", ""))).name: item
                for item in quality_expected
                if isinstance(item, dict)
            }
            quality_matches = bool(facts) and all(
                isinstance(quality_by_name.get(Path(item["path"]).name), dict)
                and quality_by_name[Path(item["path"]).name].get("modified_at") == item.get("modified_at")
                and quality_by_name[Path(item["path"]).name].get("size_bytes") == item.get("size_bytes")
                for item in facts
            )
            latest_output_time = max((_timestamp(item.get("modified_at")) for item in facts), default=0.0)
            quality_time = _timestamp(quality.get("generated_at")) if isinstance(quality, dict) else 0.0
            stage_index = stage_order.index(stage)
            downstream_facts = [
                file_fact(path)
                for later_stage in stage_order[stage_index + 1 :]
                for path in self._stage_files(later_stage)
            ]
            downstream_progress_exists = any(item["exists"] for item in downstream_facts)
            latest_downstream_time = max(
                (_timestamp(item.get("modified_at")) for item in downstream_facts if item["exists"]),
                default=0.0,
            )
            latest_stage_execution = next(
                (
                    item
                    for item in reversed(execution_facts)
                    if isinstance(item, dict) and item.get("stage") == stage
                ),
                None,
            )
            downstream_consumes_stage = downstream_progress_exists and (
                latest_stage_execution is None
                or latest_downstream_time >= _timestamp(latest_stage_execution.get("generated_at"))
            )
            if not quality and downstream_consumes_stage:
                return None
            if not isinstance(quality, dict) or (not quality_matches and quality_time < latest_output_time):
                return result(
                    "needs_stage_structural_validation",
                    {"action": "validate_stage", "command": "validate-stage", "stage": stage},
                    structural_status="unknown_or_stale",
                    semantic_status="needs_agent_judgment_after_validation",
                    latest_output_modified_at=max((item.get("modified_at") or "" for item in facts), default=None),
                    latest_quality_fact_at=quality.get("generated_at") if isinstance(quality, dict) else None,
                )
            issues = quality.get("issues") or []
            structural_status = "valid" if not issues else "issues_present"
            judgment = latest_stage_decision(
                stage,
                "reuse_cache",
                "continue",
                "rerun_stage",
                "expand_context_rerun",
                "pause",
                "manual_review",
            )
            judgment_is_current = judgment and _timestamp(judgment.get("recorded_at")) >= quality_time
            if not judgment_is_current and not issues and downstream_consumes_stage:
                return None
            if not judgment_is_current:
                return result(
                    "stage_quality_needs_agent_judgment",
                    {
                        "action": "judge_stage_quality",
                        "command": "write-agent-decision",
                        "stage": stage,
                        "required_decision": "reuse_cache|continue|rerun_stage|pause|manual_review",
                        "structural_issues": issues,
                    },
                    structural_status=structural_status,
                    semantic_status="needs_agent_judgment",
                    latest_quality_fact_at=quality.get("generated_at"),
                )
            if judgment.get("decision") in {"pause", "manual_review"}:
                return result(
                    "paused_after_stage_quality_judgment",
                    {"action": "pause_and_report", "command": "write-run-report", "stage": stage},
                    structural_status=structural_status,
                    semantic_status="paused_by_agent",
                    stage_judgment=decision_ref(judgment),
                )
            if judgment.get("decision") in {"rerun_stage", "expand_context_rerun"}:
                latest_execution = next(
                    (
                        item
                        for item in reversed(execution_facts)
                        if isinstance(item, dict) and item.get("stage") == stage
                    ),
                    None,
                )
                if not latest_execution or _timestamp(latest_execution.get("generated_at")) < _timestamp(
                    judgment.get("recorded_at")
                ):
                    return result(
                        "stage_judgment_needs_rerun",
                        {"action": "run_stage", "command": "run-stage", "stage": stage},
                        structural_status=structural_status,
                        semantic_status="rerun_requested_by_agent",
                        stage_judgment=decision_ref(judgment),
                    )
            return None

        for failed_stage, adapter in FAILED_TASK_RERUN_STAGES.items():
            latest = None
            if hasattr(adapter, "latest_unresolved_failure_report"):
                latest = adapter.latest_unresolved_failure_report(self.context)
            if latest is None:
                continue
            failure_report = latest["report"]
            return result(
                "failed_stage_tasks_need_rerun",
                {
                    "action": "rerun_failed_tasks",
                    "command": "rerun-failed-tasks",
                    "stage": failed_stage,
                    "failed_task_keys": failure_report.get("failed_task_keys") or [],
                },
                structural_status="incomplete_stage_tasks",
                semantic_status="not_reached",
                failure_report_path=latest["path"],
                failed_task_keys=failure_report.get("failed_task_keys") or [],
            )

        extract_index = stage_order.index("extract_statements")
        for stage in stage_order[: extract_index + 1]:
            if stage == "extract_statements":
                facts = [file_fact(path) for path in self._stage_files(stage)]
                if facts and not all(item["exists"] for item in facts):
                    return result(
                        "needs_stage_run",
                        {"action": "run_stage", "command": "run-stage", "stage": stage},
                        structural_status="missing_output",
                        semantic_status="not_reached",
                        missing_files=[item["path"] for item in facts if not item["exists"]],
                    )
                continue
            gate = general_stage_gate(stage)
            if gate is not None:
                return gate

        extract_fact = file_fact(Path(self.context.output_dir) / "unsplit_statement_dict.json")
        quality = quality_stages.get("extract_statements", {})
        quality_time = _timestamp(quality.get("generated_at")) if isinstance(quality, dict) else 0.0
        extract_time = _timestamp(extract_fact.get("modified_at"))
        quality_extract_fact = next(
            (
                item
                for item in quality.get("expected_files", [])
                if isinstance(item, dict) and Path(item.get("path", "")).name == "unsplit_statement_dict.json"
            ),
            None,
        ) if isinstance(quality, dict) else None
        quality_matches_output = (
            isinstance(quality_extract_fact, dict)
            and quality_extract_fact.get("modified_at") == extract_fact.get("modified_at")
            and quality_extract_fact.get("size_bytes") == extract_fact.get("size_bytes")
        )
        if not isinstance(quality, dict) or (not quality_matches_output and quality_time < extract_time):
            return result(
                "needs_extract_statements_structural_validation",
                {"action": "validate_stage", "command": "validate-stage", "stage": "extract_statements"},
                structural_status="unknown_or_stale",
                semantic_status="not_current",
                extract_output_modified_at=extract_fact.get("modified_at"),
                latest_quality_fact_at=quality.get("generated_at") if isinstance(quality, dict) else None,
            )

        issues = quality.get("issues") or []
        structural_status = "valid" if not issues else "issues_present"
        manifest = review_packets.get("extract_statements")
        manifest_time = _timestamp(manifest.get("generated_at")) if isinstance(manifest, dict) else 0.0
        manifest_extract_fact = (
            (manifest.get("source_files") or {}).get("unsplit_statement_dict")
            if isinstance(manifest, dict)
            else None
        )
        manifest_matches_output = (
            isinstance(manifest_extract_fact, dict)
            and manifest_extract_fact.get("modified_at") == extract_fact.get("modified_at")
            and manifest_extract_fact.get("size_bytes") == extract_fact.get("size_bytes")
        )
        if not isinstance(manifest, dict) or (not manifest_matches_output and manifest_time < extract_time):
            return result(
                "needs_extract_statements_review_packet",
                {
                    "action": "build_review_packet",
                    "command": "build-review-packet",
                    "stage": "extract_statements",
                },
                structural_status=structural_status,
                semantic_status="not_current",
                extract_output_modified_at=extract_fact.get("modified_at"),
                latest_review_packet_at=manifest.get("generated_at") if isinstance(manifest, dict) else None,
            )

        semantic_review = latest_decision("semantic_review_extract_statements")
        semantic_time = _timestamp(semantic_review.get("recorded_at")) if semantic_review else 0.0
        if semantic_time < manifest_time:
            return result(
                "needs_extract_statements_semantic_review",
                {
                    "action": "perform_semantic_review",
                    "command": "write-agent-decision",
                    "stage": "extract_statements",
                    "required_decision": "semantic_review_extract_statements",
                    "review_manifest_path": manifest.get("manifest_path"),
                    "reviewed_chunks": [item.get("path") for item in manifest.get("chunks", [])],
                },
                structural_status=structural_status,
                semantic_status="needs_agent_judgment",
                review_packet_generated_at=manifest.get("generated_at"),
                latest_semantic_review=decision_ref(semantic_review),
            )

        blocking = bool(semantic_review.get("blocking_findings"))
        has_rerun_candidates = bool(
            semantic_review.get("candidate_rerun_items") or semantic_review.get("candidate_expand_context_items")
        )
        semantic_status = "blocked" if blocking else "accepted"
        rerun_decision = latest_decision("rerun_stage", "expand_context_rerun")
        repair_intent_decision = latest_decision("repair_intent")
        pause_decision = latest_decision("pause")
        semantic_position = decision_position(semantic_review)
        rerun_after_review = decision_position(rerun_decision) > semantic_position
        repair_intent_after_review = decision_position(repair_intent_decision) > semantic_position
        pause_after_review = decision_position(pause_decision) > semantic_position

        if blocking or has_rerun_candidates:
            if pause_after_review and decision_position(pause_decision) > decision_position(repair_intent_decision):
                return result(
                    "user_declined_repair_needs_report",
                    {
                        "action": "pause_and_report",
                        "command": "write-run-report",
                        "stage": "extract_statements",
                    },
                    structural_status=structural_status,
                    semantic_status=semantic_status,
                    semantic_review=decision_ref(semantic_review),
                    pause_decision=decision_ref(pause_decision),
                )
            repair_intent = (
                repair_intent_decision.get("repair_intent")
                if repair_intent_after_review and isinstance(repair_intent_decision.get("repair_intent"), dict)
                else None
            )
            user_confirmed = repair_intent_after_review and repair_intent_decision.get("user_confirmed") is True
            if repair_intent is None or not user_confirmed:
                return result(
                    "blocking_review_needs_user_confirmation",
                    {
                        "action": "request_user_confirmation_for_repair",
                        "command": "write-agent-decision",
                        "stage": "extract_statements",
                        "required_decision": "repair_intent_or_manual_pause",
                        "repair_intent_must_include": {"user_confirmed": True},
                        "blocking_findings": semantic_review.get("blocking_findings") or [],
                        "candidate_rerun_items": semantic_review.get("candidate_rerun_items") or [],
                        "candidate_expand_context_items": semantic_review.get("candidate_expand_context_items") or [],
                    },
                    structural_status=structural_status,
                    semantic_status=semantic_status,
                    semantic_review=decision_ref(semantic_review),
                    rerun_request=decision_ref(rerun_decision),
                    unconfirmed_repair_intent=decision_ref(repair_intent_decision) if repair_intent_after_review else None,
                )

            intent_fingerprint = self.extract_statements_repair.intent_fingerprint(repair_intent)
            matching = [
                item
                for item in candidates
                if (item.get("report") or {}).get("intent_fingerprint") == intent_fingerprint
            ]
            if repair_intent.get("force_new_attempt") is True:
                repair_intent_time = _timestamp(repair_intent_decision.get("recorded_at"))
                matching = [
                    item
                    for item in matching
                    if _timestamp((item.get("report") or {}).get("updated_at")) >= repair_intent_time
                ]
            candidate = matching[-1] if matching else None
            report = candidate.get("report", {}) if candidate else {}
            repair_id = candidate.get("repair_id") if candidate else None
            status = report.get("status")
            if status in {None, "context_located"}:
                return result(
                    "repair_intent_needs_candidate",
                    {
                        "action": "rerun_extract_statements",
                        "command": "rerun-extract-statements",
                        "stage": "extract_statements",
                        "repair_intent": repair_intent,
                    },
                    structural_status=structural_status,
                    semantic_status=semantic_status,
                    repair_intent_decision=decision_ref(repair_intent_decision),
                    intent_fingerprint=intent_fingerprint,
                    repair_id=repair_id,
                )
            if status == "candidate_generated":
                candidate_review = next(
                    (
                        item
                        for item in reversed(decisions)
                        if item.get("stage") == "extract_statements"
                        and str(item.get("repair_id", "")) == str(repair_id)
                        and item.get("decision") in {"candidate_review_extract_statements", "apply_repair", "reject_repair"}
                        and _timestamp(item.get("recorded_at")) >= _timestamp(report.get("updated_at"))
                    ),
                    None,
                )
                if candidate_review is None:
                    return result(
                        "candidate_generated_needs_review",
                        {
                            "action": "review_repair_candidate",
                            "command": "write-agent-decision",
                            "stage": "extract_statements",
                            "required_decision": "candidate_review_extract_statements",
                            "repair_id": repair_id,
                            "review_manifest_path": str(
                                Path(candidate["repair_dir"]) / "candidate_review_packet" / "manifest.json"
                            ),
                        },
                        structural_status=structural_status,
                        semantic_status="candidate_needs_agent_judgment",
                        repair_id=repair_id,
                        candidate_status=status,
                    )
                approved = candidate_review.get("approved") is True or candidate_review.get("decision") == "apply_repair"
                if approved:
                    return result(
                        "candidate_reviewed_needs_apply",
                        {
                            "action": "apply_repair",
                            "command": "apply-repair",
                            "stage": "extract_statements",
                            "repair_id": repair_id,
                            "decision": candidate_review,
                        },
                        structural_status=structural_status,
                        semantic_status="candidate_approved",
                        repair_id=repair_id,
                        candidate_review=decision_ref(candidate_review),
                    )
                return result(
                    "candidate_rejected_needs_new_repair_intent",
                    {
                        "action": "pause_and_write_new_repair_intent",
                        "command": "write-agent-decision",
                        "stage": "extract_statements",
                        "required_decision": "repair_intent_or_pause",
                        "repair_id": repair_id,
                    },
                    structural_status=structural_status,
                    semantic_status="candidate_rejected",
                    repair_id=repair_id,
                    candidate_review=decision_ref(candidate_review),
                )
            if status == "applied":
                return result(
                    "post_apply_needs_structural_validation",
                    {"action": "validate_stage", "command": "validate-stage", "stage": "extract_statements"},
                    structural_status="unknown_or_stale",
                    semantic_status="post_apply_review_required",
                    repair_id=repair_id,
                    candidate_status=status,
                )

        if rerun_after_review:
            latest_execution = next(
                (
                    item
                    for item in reversed(execution_facts)
                    if isinstance(item, dict) and item.get("stage") == "extract_statements"
                ),
                None,
            )
            if not latest_execution or _timestamp(latest_execution.get("generated_at")) < _timestamp(
                rerun_decision.get("recorded_at")
            ):
                return result(
                    "needs_extract_statements_stage_rerun",
                    {"action": "run_stage", "command": "run-stage", "stage": "extract_statements"},
                    structural_status=structural_status,
                    semantic_status=semantic_status,
                    rerun_request=decision_ref(rerun_decision),
                )

        for stage in stage_order[extract_index + 1 :]:
            gate = general_stage_gate(stage)
            if gate is not None:
                gate["evidence_refs"].setdefault("extract_statements_semantic_review", decision_ref(semantic_review))
                return gate

        return result(
            "pipeline_artifacts_complete_needs_final_judgment",
            {"action": "write_run_report", "command": "write-run-report", "stage": "finalize_output"},
            structural_status=structural_status,
            semantic_status=semantic_status,
            semantic_review=decision_ref(semantic_review),
        )

    def _require_stage(self, stage):
        if stage not in STAGE_ORDER:
            raise ValueError(f"Unknown stage: {stage}")
        if stage not in active_stage_order(self.config):
            raise ValueError(f"Stage is disabled for this run: {stage}")

    def _stage_files(self, stage):
        cache_dir = Path(self.context.output_dir)
        state_dir = _agent_state_dir(self.context)
        if stage == "build_relations":
            paths = [
                state_dir / STRUCTURED_EDGE_CACHE_FILENAME,
                state_dir / NATURAL_EDGE_CACHE_FILENAME,
            ]
            if self.context.output_edge_path:
                paths.append(Path(self.context.output_edge_path))
            return [str(path) for path in paths]
        if stage == "finalize_output":
            paths = []
            if self.context.output_node_path:
                paths.append(Path(self.context.output_node_path))
            if self.context.output_edge_path:
                paths.append(Path(self.context.output_edge_path))
            return [str(path) for path in paths]
        return [str(cache_dir / filename) for filename in STAGE_SPECS[stage].cache_files]

    def _registered_stage_artifacts(self, stage):
        cache_dir = Path(self.context.output_dir)
        state_dir = _agent_state_dir(self.context)
        paths = [Path(path) for path in self._stage_files(stage)]
        paths.extend(cache_dir / name for name in STAGE_EXTRA_ARTIFACTS.get(stage, ()))
        if stage == "extract_statements":
            paths.append(state_dir / REVIEW_PACKET_DIRNAME / "extract_statements")
        if stage == "extract_logic_tuples":
            paths.extend(
                [
                    cache_dir / "logic_tuple_input_dict.json",
                    state_dir / STAGE_RUNS_DIRNAME / "extract_logic_tuples",
                ]
            )
        if stage == "repair_lite":
            paths.append(state_dir / REPAIR_LITE_NODE_DICT_FILENAME)
        if stage == "build_relations":
            paths.extend(
                [
                    state_dir / STRUCTURED_EDGE_CACHE_FILENAME,
                    state_dir / NATURAL_EDGE_CACHE_FILENAME,
                ]
            )
        if stage == "finalize_output":
            paths.extend(
                [
                    state_dir / RUN_REPORT_JSON_FILENAME,
                    state_dir / RUN_REPORT_MD_FILENAME,
                    state_dir / DOCUMENT_MEMORY_FILENAME,
                    state_dir / ORCHESTRATION_STATE_FILENAME,
                ]
            )
        unique = []
        seen = set()
        for path in paths:
            resolved = str(path.resolve())
            if resolved not in seen:
                unique.append(path)
                seen.add(resolved)
        return unique

    def _invalidate_downstream_cache(self, triggering_stage, reason):
        # Invalidate optional side-path artifacts too. They may be re-enabled in
        # a later experimental run and must never survive an upstream rewrite.
        stage_order = STAGE_ORDER
        trigger_index = stage_order.index(triggering_stage)
        downstream_stages = stage_order[trigger_index + 1 :]
        if not downstream_stages:
            return None

        state_dir = _agent_state_dir(self.context)
        invalidation_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:10]}"
        stale_dir = state_dir / STALE_CACHE_DIRNAME / invalidation_id
        moved = []
        missing = []
        failed = []

        for stage in downstream_stages:
            for source in self._registered_stage_artifacts(stage):
                if not source.exists():
                    missing.append({"stage": stage, "path": str(source)})
                    continue
                try:
                    try:
                        relative = source.relative_to(Path(self.context.output_dir))
                        destination = stale_dir / "stage_cache" / relative
                    except ValueError:
                        destination = stale_dir / "external_outputs" / source.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(source), str(destination))
                    moved.append(
                        {
                            "stage": stage,
                            "source": str(source),
                            "destination": str(destination),
                        }
                    )
                except OSError as exc:
                    failed.append({"stage": stage, "path": str(source), "error": str(exc)})

        quality_path = state_dir / QUALITY_FACTS_FILENAME
        quality = _read_json_optional(quality_path, {})
        removed_quality_stages = []
        if isinstance(quality, dict) and isinstance(quality.get("stages"), dict):
            for stage in downstream_stages:
                if stage in quality["stages"]:
                    removed_quality_stages.append(stage)
                    quality["stages"].pop(stage, None)
            quality["updated_at"] = utc_now()
            write_json(str(quality_path), quality)

        manifest = {
            "schema_version": 1,
            "invalidation_id": invalidation_id,
            "created_at": utc_now(),
            "triggering_stage": triggering_stage,
            "reason": reason,
            "updated_upstream_files": [file_fact(path) for path in self._stage_files(triggering_stage)],
            "downstream_stages": downstream_stages,
            "moved": moved,
            "missing": missing,
            "failed": failed,
            "removed_quality_stages": removed_quality_stages,
        }
        stale_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = stale_dir / "manifest.json"
        write_json(str(manifest_path), manifest)
        return {
            "invalidation_id": invalidation_id,
            "manifest_path": str(manifest_path),
            "moved_count": len(moved),
            "failed_count": len(failed),
            "downstream_stages": downstream_stages,
        }

    def _stage_cache_metrics(self, stage):
        metrics = {}
        for path in self._stage_files(stage):
            fact = file_fact(path)
            basename = Path(path).name
            if fact["item_count"] is not None:
                metrics[f"{basename}:item_count"] = fact["item_count"]
        adapter = FAILED_TASK_RERUN_STAGES.get(stage)
        if adapter is not None and hasattr(adapter, "latest_unresolved_failure_report"):
            unresolved = adapter.latest_unresolved_failure_report(self.context)
            metrics["unresolved_task_count"] = (
                len(unresolved["report"].get("failed_task_keys") or []) if unresolved else 0
            )
            metrics["unresolved_failure_report"] = unresolved["path"] if unresolved else None
        return metrics

    def _build_extract_statements_review_packet(self, source_blocks_per_chunk):
        cache_dir = Path(self.context.output_dir)
        problem_dict = _read_json_optional(cache_dir / "problem_dict.json", {})
        unsplit = _read_json_optional(cache_dir / "unsplit_statement_dict.json", {})
        if not isinstance(problem_dict, dict):
            problem_dict = {}
        if not isinstance(unsplit, dict):
            unsplit = {}

        source_keys = sorted(problem_dict.keys(), key=_safe_sort_key)
        source_key_lookup = {_string_key(key): key for key in source_keys}
        nodes_by_source = {key: [] for key in source_key_lookup}
        orphan_nodes_by_key = {}
        extracted_node_count = 0

        for node_key in sorted(unsplit.keys(), key=_safe_sort_key):
            wrapper = unsplit.get(node_key)
            if not isinstance(wrapper, dict):
                wrapper = {}
            raw_source_key = _string_key(wrapper.get("_orig_key"))
            matched_source_key = raw_source_key if raw_source_key in source_key_lookup else None
            payload = _review_node_payload(node_key, wrapper, matched_source_key)
            extracted_node_count += 1
            if matched_source_key is None:
                orphan_nodes_by_key.setdefault(raw_source_key or "__missing_orig_key__", []).append(payload)
            else:
                nodes_by_source.setdefault(matched_source_key, []).append(payload)

        records = []
        for index, key in enumerate(source_keys):
            source_key = _string_key(key)
            previous_key = source_keys[index - 1] if index > 0 else None
            next_key = source_keys[index + 1] if index + 1 < len(source_keys) else None
            previous_context = None
            next_context = None
            if previous_key is not None:
                previous_context = {
                    "source_block_key": _string_key(previous_key),
                    "text_summary": _summary_text(_problem_block_text(problem_dict.get(previous_key))),
                }
            if next_key is not None:
                next_context = {
                    "source_block_key": _string_key(next_key),
                    "text_summary": _summary_text(_problem_block_text(problem_dict.get(next_key))),
                }
            extracted_nodes = nodes_by_source.get(source_key, [])
            records.append(
                {
                    "record_type": "source_block",
                    "source_block_index": index,
                    "source_block_key": source_key,
                    "source_text": _problem_block_text(problem_dict.get(key)),
                    "previous_context": previous_context,
                    "next_context": next_context,
                    "extracted_node_count": len(extracted_nodes),
                    "extracted_nodes": extracted_nodes,
                }
            )

        for raw_source_key in sorted(orphan_nodes_by_key.keys(), key=_safe_sort_key):
            extracted_nodes = orphan_nodes_by_key[raw_source_key]
            records.append(
                {
                    "record_type": "orphan_nodes",
                    "source_block_key": None,
                    "raw_source_block_key": None if raw_source_key == "__missing_orig_key__" else raw_source_key,
                    "source_text": "",
                    "previous_context": None,
                    "next_context": None,
                    "extracted_node_count": len(extracted_nodes),
                    "extracted_nodes": extracted_nodes,
                }
            )

        packet_dir = _agent_state_dir(self.context) / REVIEW_PACKET_DIRNAME / "extract_statements"
        packet_dir.mkdir(parents=True, exist_ok=True)
        for stale_chunk in packet_dir.glob("chunk_*.json"):
            stale_chunk.unlink()

        chunks = []
        if records:
            chunk_count = (len(records) + source_blocks_per_chunk - 1) // source_blocks_per_chunk
        else:
            chunk_count = 0
        for chunk_index in range(chunk_count):
            start = chunk_index * source_blocks_per_chunk
            end = min(start + source_blocks_per_chunk, len(records))
            chunk_records = records[start:end]
            chunk_path = packet_dir / f"chunk_{chunk_index + 1:04d}.json"
            chunk_data = {
                "schema_version": 1,
                "packet_type": "extract_statements_semantic_review",
                "stage": "extract_statements",
                "generated_at": utc_now(),
                "chunk_index": chunk_index,
                "chunk_number": chunk_index + 1,
                "chunk_count": chunk_count,
                "record_count": len(chunk_records),
                "records": chunk_records,
            }
            write_json(str(chunk_path), chunk_data)
            chunks.append(
                {
                    "chunk_index": chunk_index,
                    "chunk_number": chunk_index + 1,
                    "path": str(chunk_path),
                    "record_count": len(chunk_records),
                    "source_block_keys": [
                        record.get("source_block_key")
                        for record in chunk_records
                        if record.get("record_type") == "source_block"
                    ],
                    "node_count": sum(record.get("extracted_node_count", 0) for record in chunk_records),
                }
            )

        manifest = {
            "schema_version": 1,
            "packet_type": "extract_statements_semantic_review",
            "generated_at": utc_now(),
            "input_path": self.context.file_path,
            "stage_cache_dir": self.context.output_dir,
            "stage": "extract_statements",
            "review_scope": "full",
            "packet_dir": str(packet_dir),
            "manifest_path": str(packet_dir / "manifest.json"),
            "source_blocks_per_chunk": source_blocks_per_chunk,
            "source_block_count": len(source_keys),
            "extracted_node_count": extracted_node_count,
            "orphan_node_count": sum(len(nodes) for nodes in orphan_nodes_by_key.values()),
            "coverage": {
                "source_blocks_included": len(source_keys),
                "extracted_nodes_included": extracted_node_count,
                "orphan_node_groups": len(orphan_nodes_by_key),
            },
            "source_files": {
                "problem_dict": file_fact(cache_dir / "problem_dict.json"),
                "unsplit_statement_dict": file_fact(cache_dir / "unsplit_statement_dict.json"),
            },
            "chunks": chunks,
        }
        write_json(manifest["manifest_path"], manifest)
        return {
            "command": "build-review-packet",
            "generated_at": utc_now(),
            "stage": "extract_statements",
            "manifest": manifest,
        }

    def _load_review_packet_manifests(self):
        packet_root = _agent_state_dir(self.context) / REVIEW_PACKET_DIRNAME
        manifests = {}
        if not packet_root.exists():
            return manifests
        for manifest_path in packet_root.glob("*/manifest.json"):
            manifest = _read_json_optional(manifest_path, None)
            if isinstance(manifest, dict):
                stage = manifest.get("stage") or manifest_path.parent.name
                manifests[str(stage)] = manifest
        return manifests

    def _build_document_memory_fact(self, stages):
        return {
            "schema_version": 1,
            "generated_at": utc_now(),
            "input_path": self.context.file_path,
            "stage_cache_dir": self.context.output_dir,
            "stage_order": active_stage_order(self.config),
            "stage_file_facts": [
                {
                    "stage": item["stage"],
                    "all_expected_files_exist": item["all_expected_files_exist"],
                    "metrics": item["metrics"],
                }
                for item in stages
            ],
        }

    def _upsert_quality_fact(self, stage, facts):
        path = _agent_state_dir(self.context) / QUALITY_FACTS_FILENAME
        data = _read_json_optional(path, {})
        if not isinstance(data, dict):
            data = {}
        data.setdefault("schema_version", 1)
        data["updated_at"] = utc_now()
        data.setdefault("stages", {})
        data["stages"][stage] = facts
        write_json(str(path), data)

    def _append_execution_fact(self, fact):
        path = _agent_state_dir(self.context) / "execution_facts.json"
        data = _read_json_optional(path, [])
        if not isinstance(data, list):
            data = []
        data.append(fact)
        write_json(str(path), data)

    def _write_json(self, filename, data):
        path = _agent_state_dir(self.context) / filename
        write_json(str(path), data)
        return str(path)

    def _quality_summary(self, quality_facts):
        stages = quality_facts.get("stages") if isinstance(quality_facts, dict) else {}
        if not isinstance(stages, dict):
            return {}
        return {
            stage: {
                "issue_count": len((fact or {}).get("issues", [])) if isinstance(fact, dict) else 0,
                "metrics": (fact or {}).get("metrics", {}) if isinstance(fact, dict) else {},
            }
            for stage, fact in stages.items()
        }

    def _semantic_review_decisions(self, decisions):
        return [
            decision
            for decision in decisions
            if isinstance(decision, dict) and decision.get("decision") == "semantic_review_extract_statements"
        ]

    def _reserved_rerun_candidates(self, semantic_reviews):
        candidates = []
        for review in semantic_reviews:
            candidates.extend(review.get("candidate_rerun_items") or [])
            candidates.extend(review.get("candidate_expand_context_items") or [])
        return candidates

    def _fact_issue(self, issue):
        return {
            "stage": issue.get("stage"),
            "severity": issue.get("severity"),
            "code": issue.get("code"),
            "message": issue.get("message"),
            "item_ref": issue.get("item_ref"),
        }

    def _render_report_markdown(self, report):
        lines = [
            "# MathKG Claude Code Main Agent Report",
            "",
            f"- Input: `{report.get('input_path')}`",
            f"- Stage cache: `{report.get('stage_cache_dir')}`",
            f"- Generated at: `{report.get('generated_at')}`",
            "",
            "## Tool Facts",
            "",
        ]
        orchestration = report.get("orchestration_state") or {}
        if orchestration:
            lines.extend(
                [
                    f"- Orchestration state: `{orchestration.get('state', '')}`",
                    f"- State fingerprint: `{orchestration.get('state_fingerprint', '')}`",
                    f"- Repeat guard: `{orchestration.get('repeat_guard_triggered', False)}`",
                    "",
                ]
            )
        summary = report.get("quality_facts_summary") or {}
        if summary:
            lines.extend(["| Stage | Issues | Metrics |", "| --- | ---: | --- |"])
            for stage, item in summary.items():
                metrics = ", ".join(f"{key}={value}" for key, value in (item.get("metrics") or {}).items())
                lines.append(f"| {stage} | {item.get('issue_count', 0)} | {metrics} |")
        else:
            lines.append("No stage quality facts recorded.")

        packets = report.get("review_packets") or {}
        lines.extend(["", "## Review Packets", ""])
        if packets:
            lines.extend(["| Stage | Scope | Source blocks | Nodes | Chunks | Manifest |", "| --- | --- | ---: | ---: | ---: | --- |"])
            for stage, packet in packets.items():
                lines.append(
                    f"| {stage} | {packet.get('review_scope', '')} | "
                    f"{packet.get('source_block_count', 0)} | "
                    f"{packet.get('extracted_node_count', 0)} | "
                    f"{len(packet.get('chunks') or [])} | "
                    f"`{packet.get('manifest_path', '')}` |"
                )
        else:
            lines.append("No review packets recorded.")

        semantic_reviews = report.get("semantic_review_decisions") or []
        lines.extend(["", "## Semantic Review Judgments", ""])
        if semantic_reviews:
            for review in semantic_reviews:
                lines.append(
                    f"- stage=`{review.get('stage', '')}` scope=`{review.get('review_scope', '')}` "
                    f"next_action=`{review.get('next_action', '')}` "
                    f"blocking={len(review.get('blocking_findings') or [])} "
                    f"manual_review={len(review.get('manual_review_items') or [])}"
                )
        else:
            lines.append("No semantic review decisions recorded.")

        reserved = report.get("reserved_rerun_candidates") or []
        lines.extend(["", "## Reserved Rerun Candidates", ""])
        if reserved:
            for item in reserved:
                lines.append(f"- `{json.dumps(item, ensure_ascii=False)}`")
        else:
            lines.append("No reserved rerun candidates recorded.")

        repair_candidates = report.get("repair_candidates") or []
        lines.extend(["", "## Repair Candidates", ""])
        if repair_candidates:
            for item in repair_candidates:
                repair_report = item.get("report") or {}
                lines.append(
                    f"- repair_id=`{item.get('repair_id', '')}` "
                    f"status=`{repair_report.get('status', '')}` "
                    f"engine=`{repair_report.get('engine', '')}`"
                )
        else:
            lines.append("No repair candidates recorded.")

        lines.extend(["", "## Claude Code Decisions", ""])
        decisions = report.get("agent_decisions") or []
        if decisions:
            for decision in decisions:
                lines.append(
                    f"- `{decision.get('stage', '')}` decision=`{decision.get('decision', '')}` "
                    f"reason=`{decision.get('reason', '')}`"
                )
        else:
            lines.append("No Claude Code decisions recorded.")
        return "\n".join(lines) + "\n"
