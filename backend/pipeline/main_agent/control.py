import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..common.io import read_json, write_json
from ..context import PipelineContext
from ..stages.analysis import stage as analysis_stage
from ..stages.build_relations import stage as build_relations_stage
from ..stages.clean_nodes import stage as clean_nodes_stage
from ..stages.compile_logic_form import stage as compile_logic_form_stage
from ..stages.correct_text import stage as correct_text_stage
from ..stages.extract_logic_tuples import stage as extract_logic_tuples_stage
from ..stages.extract_references import stage as extract_references_stage
from ..stages.extract_statements import stage as extract_statements_stage
from ..stages.finalize_output import stage as finalize_output_stage
from ..stages.generate_titles import stage as generate_titles_stage
from ..stages.math_disambiguation import stage as math_disambiguation_stage
from ..stages.normalize_predicates import stage as normalize_predicates_stage
from ..stages.repair import stage as repair_stage
from ..stages.repair_lite import stage as repair_lite_stage
from ..stages.segment_blocks import stage as segment_blocks_stage
from ..stages.split_nodes import stage as split_nodes_stage


AGENT_STATE_DIRNAME = "agent_state"
DOCUMENT_MEMORY_FILENAME = "document_memory.json"
QUALITY_LEDGER_FILENAME = "quality_ledger.json"
RUN_REPORT_JSON_FILENAME = "run_report.json"
RUN_REPORT_MD_FILENAME = "run_report.md"
REPAIR_LITE_NODE_DICT_FILENAME = "node_dict_after_repair_lite.json"
STRUCTURED_EDGE_CACHE_FILENAME = "edge_list_structured.json"
NATURAL_EDGE_CACHE_FILENAME = "edge_list_natural.json"


STAGE_ORDER = [
    "correct_text",
    "segment_blocks",
    "extract_statements",
    "clean_nodes",
    "split_nodes",
    "generate_titles",
    "math_disambiguation",
    "extract_logic_tuples",
    "analysis",
    "repair",
    "extract_references",
    "repair_lite",
    "compile_logic_form",
    "normalize_predicates",
    "build_relations",
    "finalize_output",
]

EXPERIMENTAL_LOGIC_IR_STAGES = (
    "compile_logic_form",
    "normalize_predicates",
)


KEY_STAGE_LABELS = {
    "extract_statements": "node_extraction",
    "clean_nodes": "node_cleaning",
    "generate_titles": "title_generation",
    "split_nodes": "split_nodes",
    "compile_logic_form": "logic_form",
    "normalize_predicates": "predicate_normalization",
    "build_relations": "relation_extraction",
}


EDGE_START_KEYS = ("出发节点", "鍑哄彂鑺傜偣")
EDGE_END_KEYS = ("到达节点", "鍒拌揪鑺傜偣")
EDGE_RELATION_KEYS = ("关系", "鍏崇郴")

LEGACY_LATEX_FREEZE_TOKEN_PATTERN = re.compile(r"@@[A-Z]+::.*?@@")


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _json_path(base_dir, filename):
    return Path(base_dir) / filename


def _agent_state_dir(context):
    path = Path(context.output_dir) / AGENT_STATE_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_json_optional(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def _write_agent_json(context, filename, data):
    path = _agent_state_dir(context) / filename
    write_json(str(path), data)
    return str(path)


def _sort_key(value):
    try:
        return (0, int(str(value)))
    except (TypeError, ValueError):
        return (1, str(value))


def _len_mapping(value):
    return len(value) if isinstance(value, dict) else 0


def _len_sequence(value):
    return len(value) if isinstance(value, (list, tuple)) else 0


def _node_block_from_wrapper(value):
    if not isinstance(value, dict):
        return None
    if any(key in value for key in ("node_type", "content", "proof", "label", "title", "remark")):
        return value
    block = value.get("pos1")
    return block if isinstance(block, dict) else None


def _iter_wrapped_nodes(value):
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item
        return
    if not isinstance(value, dict):
        return
    for key in sorted(value.keys(), key=_sort_key):
        block = _node_block_from_wrapper(value.get(key))
        if isinstance(block, dict):
            yield block


def _text(value):
    return value if isinstance(value, str) else ""


def _contains_legacy_latex_freeze_token(value):
    if isinstance(value, str):
        return LEGACY_LATEX_FREEZE_TOKEN_PATTERN.search(value) is not None
    if isinstance(value, dict):
        return any(_contains_legacy_latex_freeze_token(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_legacy_latex_freeze_token(item) for item in value)
    return False


def _title_text(node):
    title = node.get("title") if isinstance(node, dict) else None
    if isinstance(title, dict):
        return (title.get("chinese") or title.get("english") or "").strip()
    return title.strip() if isinstance(title, str) else ""


def _content_text(node):
    if not isinstance(node, dict):
        return ""
    content = node.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        return (
            content.get("formal_statement_core")
            or content.get("original_form")
            or content.get("text_normalized")
            or ""
        ).strip()
    remark = node.get("remark")
    if isinstance(remark, dict):
        return (
            remark.get("formal_statement_core")
            or remark.get("original_form")
            or remark.get("text_normalized")
            or ""
        ).strip()
    return ""


def _issue(stage, severity, code, message, *, item_ref=None, retry_scope="stage"):
    return {
        "stage": stage,
        "severity": severity,
        "code": code,
        "message": message,
        "item_ref": item_ref,
        "retry_scope": retry_scope,
    }


def _has_errors(issues):
    return any(issue.get("severity") == "error" for issue in issues)


def _stage_result(stage, status, source, issues=None, metrics=None, action="continue"):
    issues = issues or []
    return {
        "stage": stage,
        "label": KEY_STAGE_LABELS.get(stage, stage),
        "status": status,
        "source": source,
        "issues": issues,
        "metrics": metrics or {},
        "action": "stop" if _has_errors(issues) else action,
        "checked_at": _utc_now(),
    }


@dataclass
class AgentRunConfig:
    edge_output_mode: str = "structured"
    relation_prompt_profile: str = "graph"
    enable_analysis: bool = False
    enable_math_disambiguation: bool = True
    experimental_logic_ir: bool = False
    force_stage: str | None = None
    force_from: str | None = None
    stop_after: str | None = None
    diagnose_only: bool = False
    max_stage_retries: int = 1
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class StageSpec:
    name: str
    cache_files: tuple[str, ...]
    optional: bool = False


STAGE_SPECS = {
    "correct_text": StageSpec("correct_text", ("corrected_text_dict.json",)),
    "segment_blocks": StageSpec("segment_blocks", ("problem_dict.json",)),
    "extract_statements": StageSpec("extract_statements", ("unsplit_statement_dict.json",)),
    "clean_nodes": StageSpec(
        "clean_nodes",
        (
            "unsplit_statement_dict_cleaned.json",
            "node_cleaning_report.json",
            "node_quarantine.json",
        ),
    ),
    "split_nodes": StageSpec(
        "split_nodes",
        ("node_split_dict.json", "statement_without_title_dict.json"),
    ),
    "generate_titles": StageSpec(
        "generate_titles",
        ("definition_axiom_dict.json", "structured_input_dict.json"),
    ),
    "math_disambiguation": StageSpec(
        "math_disambiguation",
        ("definition_axiom_dict_disambiguated.json", "structured_input_dict_disambiguated.json"),
        optional=True,
    ),
    "extract_logic_tuples": StageSpec("extract_logic_tuples", ("node_dict.json",)),
    "analysis": StageSpec("analysis", ("analysis_debug/analysis_result.json",), optional=True),
    "repair": StageSpec("repair", ("node_dict_after_repair.json",), optional=True),
    "extract_references": StageSpec("extract_references", ("references_dict.json", "references_unresolved.json")),
    "repair_lite": StageSpec("repair_lite", ("repair_lite_report.json",)),
    "compile_logic_form": StageSpec(
        "compile_logic_form",
        ("node_dict_normalized.json", "logic_form_input_dict.json", "logic_form_local_dict.json"),
    ),
    "normalize_predicates": StageSpec(
        "normalize_predicates",
        ("node_dict_after_predicate_normalization.json", "global_predicate_registry.json"),
    ),
    "build_relations": StageSpec("build_relations", ()),
    "finalize_output": StageSpec("finalize_output", ()),
}


def build_default_output_paths(input_path, output_dir=None):
    input_path = os.path.abspath(input_path)
    stem = os.path.splitext(os.path.basename(input_path))[0]
    if output_dir:
        final_dir = os.path.abspath(output_dir)
    else:
        final_dir = os.path.join(os.path.dirname(input_path), f"{stem}_agent_output")
    os.makedirs(final_dir, exist_ok=True)
    return {
        "output_dir": final_dir,
        "node": os.path.join(final_dir, "TEST_NODE_OUT.json"),
        "edge": os.path.join(final_dir, "TEST_EDGE_OUT.json"),
        "natural_node": os.path.join(final_dir, "TEST_NODE_NATURAL_OUT.json"),
    }


def natural_edge_path(edge_path):
    if not edge_path:
        return None
    edge_abs = os.path.abspath(edge_path)
    edge_dir = os.path.dirname(edge_abs)
    edge_name = os.path.basename(edge_abs)
    edge_stem, edge_ext = os.path.splitext(edge_name)
    return os.path.join(edge_dir, f"{edge_stem}_natural{edge_ext or '.json'}")


class MainAgentController:
    """External control plane around the existing stage implementation."""

    def __init__(self, context: PipelineContext, config: AgentRunConfig | None = None):
        self.context = context
        self.config = config or AgentRunConfig(
            enable_analysis=context.enable_analysis,
            enable_math_disambiguation=context.enable_math_disambiguation,
        )
        self.cache_dir = Path(context.output_dir)
        self.agent_state_dir = _agent_state_dir(context)

    def process(self):
        raise RuntimeError(
            "The automatic Python main-agent loop is disabled. "
            "Use the MathKG skill script mathkg_agent_tool.py so Claude Code can make the main-agent decisions."
        )

    def diagnose(self):
        raise RuntimeError(
            "The automatic Python diagnosis loop is disabled. "
            "Use the MathKG skill script mathkg_agent_tool.py scan-cache and validate-stage instead."
        )

    def scan_cache(self, active_stages=None):
        active_stages = active_stages or self._active_stages()
        state = self.load_state_from_cache()
        results = []
        for stage_name in active_stages:
            if self._is_forced(stage_name):
                results.append(
                    _stage_result(
                        stage_name,
                        "forced",
                        "cache",
                        [
                            _issue(
                                stage_name,
                                "warning",
                                "forced_rerun",
                                "Stage is selected for explicit rerun.",
                                retry_scope="stage",
                            )
                        ],
                        action="rerun",
                    )
                )
                continue
            results.append(self._check_stage(stage_name, state, source="cache"))
        return results

    def load_state_from_cache(self):
        cache = self.cache_dir
        state = {}

        chopped_text_dict = _read_json_optional(cache / "corrected_text_dict.json", {})
        if isinstance(chopped_text_dict, dict):
            state["chopped_text_dict"] = chopped_text_dict

        correct_text_report = _read_json_optional(cache / "correct_text_report.json", {})
        if isinstance(correct_text_report, dict):
            state["correct_text_report"] = correct_text_report

        problem_dict = _read_json_optional(cache / "problem_dict.json", {})
        if isinstance(problem_dict, dict):
            state["problem_dict"] = problem_dict

        segment_blocks_report = _read_json_optional(cache / "segment_blocks_report.json", {})
        if isinstance(segment_blocks_report, dict):
            state["segment_blocks_report"] = segment_blocks_report

        raw_unsplit = _read_json_optional(cache / "unsplit_statement_dict.json", {})
        if isinstance(raw_unsplit, dict):
            state["unsplit_statement_dict_raw"] = raw_unsplit

        unsplit = _read_json_optional(cache / "unsplit_statement_dict_cleaned.json", None)
        if not isinstance(unsplit, dict):
            unsplit = raw_unsplit
        if isinstance(unsplit, dict):
            state["unsplit_statement_dict"] = unsplit

        node_cleaning_report = _read_json_optional(cache / "node_cleaning_report.json", {})
        if isinstance(node_cleaning_report, dict):
            state["node_cleaning_report"] = node_cleaning_report

        node_quarantine = _read_json_optional(cache / "node_quarantine.json", {})
        if isinstance(node_quarantine, dict):
            state["node_quarantine"] = node_quarantine

        extract_statements_report = _read_json_optional(cache / "extract_statements_report.json", {})
        if isinstance(extract_statements_report, dict):
            state["extract_statements_report"] = extract_statements_report

        node_split = _read_json_optional(cache / "node_split_dict.json", {})
        if isinstance(node_split, dict):
            state["node_split_dict"] = node_split

        statement_without_title = _read_json_optional(cache / "statement_without_title_dict.json", {})
        if isinstance(statement_without_title, dict):
            state["statement_without_title_dict"] = statement_without_title

        if self.config.enable_math_disambiguation:
            definition_axiom = _read_json_optional(cache / "definition_axiom_dict_disambiguated.json", None)
            structured_input = _read_json_optional(cache / "structured_input_dict_disambiguated.json", None)
        else:
            definition_axiom = None
            structured_input = None
        if definition_axiom is None:
            definition_axiom = _read_json_optional(cache / "definition_axiom_dict.json", {})
        if structured_input is None:
            structured_input = _read_json_optional(cache / "structured_input_dict.json", {})
        if isinstance(definition_axiom, dict):
            state["definition_axiom_dict"] = definition_axiom
        if isinstance(structured_input, dict):
            state["structured_input_dict"] = structured_input

        node_dict = _read_json_optional(cache / "node_dict.json", {})
        if isinstance(node_dict, dict):
            state["node_dict"] = node_dict
            state["node_list"] = list(node_dict.values())

        if self.config.enable_analysis and "node_list" in state:
            analysis_result = _read_json_optional(cache / "analysis_debug" / "analysis_result.json", None)
            if isinstance(analysis_result, dict):
                state["node_list"] = analysis_stage.attach_analysis_back(state["node_list"], analysis_result)
                state["node_dict"] = analysis_stage.sync_node_dict_from_list(state.get("node_dict"), state["node_list"])

        repaired_node_dict = _read_json_optional(cache / "node_dict_after_repair.json", None)
        if isinstance(repaired_node_dict, dict):
            state["node_dict"] = repaired_node_dict
            state["node_list"] = list(repaired_node_dict.values())

        references_dict = _read_json_optional(cache / "references_dict.json", None)
        if isinstance(references_dict, dict):
            state["node_dict"] = references_dict
            state["node_list"] = list(references_dict.values())

        repair_lite_node_dict = _read_json_optional(self.agent_state_dir / REPAIR_LITE_NODE_DICT_FILENAME, None)
        if isinstance(repair_lite_node_dict, dict):
            state["node_dict"] = repair_lite_node_dict
            state["node_list"] = list(repair_lite_node_dict.values())

        if self.config.experimental_logic_ir:
            logic_form_local = _read_json_optional(cache / "logic_form_local_dict.json", {})
            if isinstance(logic_form_local, dict):
                state["logic_form_local_dict"] = logic_form_local

            logic_form_input = _read_json_optional(cache / "logic_form_input_dict.json", {})
            if isinstance(logic_form_input, dict):
                state["logic_form_input_dict"] = logic_form_input

            node_dict_normalized = _read_json_optional(cache / "node_dict_normalized.json", None)
            if isinstance(node_dict_normalized, dict):
                state["node_dict"] = compile_logic_form_stage.merge_logic_ast_local(
                    logic_form_local if isinstance(logic_form_local, dict) else {},
                    node_dict_normalized,
                )
                state["node_list"] = list(state["node_dict"].values())

            predicate_node_dict = _read_json_optional(
                cache / "node_dict_after_predicate_normalization.json",
                None,
            )
            if isinstance(predicate_node_dict, dict):
                state["node_dict"] = predicate_node_dict
                state["node_list"] = list(predicate_node_dict.values())

            for filename, state_key in (
                ("predicate_entry_list.json", "predicate_entry_list"),
                ("global_predicate_registry.json", "global_predicate_registry"),
                ("fixed_operator_misuse_report.json", "fixed_operator_misuse_report"),
                (
                    "predicate_cluster_decision_normalization_report.json",
                    "predicate_cluster_decision_normalization_report",
                ),
                ("registry_duplicate_merge_report.json", "registry_duplicate_merge_report"),
            ):
                value = _read_json_optional(cache / filename, None)
                if value is not None:
                    state[state_key] = value

        edge_list = self._load_cached_edges()
        if edge_list is not None:
            state["edge_list"] = edge_list
        return state

    def load_state_for_stage(self, stage_name):
        """Restore only the upstream state that the requested stage may consume."""
        if stage_name not in STAGE_ORDER:
            raise ValueError(f"Unknown stage: {stage_name}")
        active_stages = self._active_stages()
        if stage_name not in active_stages:
            raise ValueError(f"Stage is disabled for this run: {stage_name}")
        state = self.load_state_from_cache()
        stage_index = active_stages.index(stage_name)

        if stage_name == "clean_nodes":
            raw_unsplit = _read_json_optional(self.cache_dir / "unsplit_statement_dict.json", {})
            if isinstance(raw_unsplit, dict):
                state["unsplit_statement_dict"] = raw_unsplit

        late_state_keys = {
            "logic_form_local_dict": "compile_logic_form",
            "logic_form_input_dict": "compile_logic_form",
            "predicate_entry_list": "normalize_predicates",
            "global_predicate_registry": "normalize_predicates",
            "fixed_operator_misuse_report": "normalize_predicates",
            "predicate_cluster_decision_normalization_report": "normalize_predicates",
            "registry_duplicate_merge_report": "normalize_predicates",
            "edge_list": "build_relations",
        }
        for state_key, producer_stage in late_state_keys.items():
            if (
                producer_stage not in active_stages
                or stage_index <= active_stages.index(producer_stage)
            ):
                state.pop(state_key, None)

        node_source = None
        if stage_index > active_stages.index("extract_logic_tuples"):
            node_source = _read_json_optional(self.cache_dir / "node_dict.json", None)
        if (
            self.config.enable_analysis
            and stage_index > active_stages.index("analysis")
            and isinstance(node_source, dict)
        ):
            analysis_result = _read_json_optional(self.cache_dir / "analysis_debug" / "analysis_result.json", None)
            if isinstance(analysis_result, dict):
                node_list = analysis_stage.attach_analysis_back(list(node_source.values()), analysis_result)
                node_source = analysis_stage.sync_node_dict_from_list(node_source, node_list)
        if self.config.enable_analysis and stage_index > active_stages.index("repair"):
            repaired = _read_json_optional(self.cache_dir / "node_dict_after_repair.json", None)
            if isinstance(repaired, dict):
                node_source = repaired
        if stage_index > active_stages.index("extract_references"):
            references = _read_json_optional(self.cache_dir / "references_dict.json", None)
            if isinstance(references, dict):
                node_source = references
        if stage_index > active_stages.index("repair_lite"):
            repair_lite = _read_json_optional(self.agent_state_dir / REPAIR_LITE_NODE_DICT_FILENAME, None)
            if isinstance(repair_lite, dict):
                node_source = repair_lite
        if (
            self.config.experimental_logic_ir
            and stage_index > active_stages.index("compile_logic_form")
        ):
            normalized = _read_json_optional(self.cache_dir / "node_dict_normalized.json", None)
            logic_local = _read_json_optional(self.cache_dir / "logic_form_local_dict.json", {})
            if isinstance(normalized, dict):
                node_source = compile_logic_form_stage.merge_logic_ast_local(
                    logic_local if isinstance(logic_local, dict) else {},
                    normalized,
                )
        if (
            self.config.experimental_logic_ir
            and stage_index > active_stages.index("normalize_predicates")
        ):
            predicate = _read_json_optional(self.cache_dir / "node_dict_after_predicate_normalization.json", None)
            if isinstance(predicate, dict):
                node_source = predicate

        if isinstance(node_source, dict):
            state["node_dict"] = node_source
            state["node_list"] = list(node_source.values())
        else:
            state.pop("node_dict", None)
            state.pop("node_list", None)
        return state

    def _load_cached_edges(self):
        structured = _read_json_optional(self.agent_state_dir / STRUCTURED_EDGE_CACHE_FILENAME, None)
        natural = _read_json_optional(self.agent_state_dir / NATURAL_EDGE_CACHE_FILENAME, None)
        mode = (self.config.edge_output_mode or "structured").lower()
        if mode == "natural" and natural is not None:
            return natural
        if structured is not None:
            return structured
        if self.context.output_edge_path:
            edge = _read_json_optional(self.context.output_edge_path, None)
            if edge is not None:
                return edge
        return None

    def _active_stages(self):
        stages = []
        for stage_name in STAGE_ORDER:
            if (
                stage_name in EXPERIMENTAL_LOGIC_IR_STAGES
                and not self.config.experimental_logic_ir
            ):
                continue
            if stage_name == "math_disambiguation" and not self.config.enable_math_disambiguation:
                continue
            if stage_name in {"analysis", "repair"} and not self.config.enable_analysis:
                continue
            stages.append(stage_name)
        return stages

    def _select_start_stage(self, preflight, active_stages):
        if self.config.force_from:
            return self.config.force_from
        if self.config.force_stage:
            return self.config.force_stage
        for result in preflight:
            if result["stage"] not in active_stages:
                continue
            if result["status"] not in {"cached_valid", "skipped"}:
                return result["stage"]
            if _has_errors(result.get("issues", [])):
                return result["stage"]
        return None

    def _stop_index(self, active_stages):
        if self.config.stop_after and self.config.stop_after in active_stages:
            return active_stages.index(self.config.stop_after)
        return len(active_stages) - 1

    def _is_forced(self, stage_name):
        if self.config.force_stage == stage_name:
            return True
        active_stages = self._active_stages()
        if self.config.force_from and self.config.force_from in active_stages:
            return active_stages.index(stage_name) >= active_stages.index(self.config.force_from)
        return False

    def _run_stage_with_retries(self, stage_name, state):
        attempts = 0
        last_result = None
        while attempts <= max(0, self.config.max_stage_retries):
            attempts += 1
            state = self._run_stage(stage_name, state)
            result = self._check_stage(stage_name, state, source="run")
            result["attempts"] = attempts
            if not _has_errors(result.get("issues", [])):
                return {"state": state, "result": result}
            last_result = result
        return {"state": state, "result": last_result}

    def _run_stage(self, stage_name, state):
        runners = {
            "correct_text": lambda s: correct_text_stage.run(self.context, s),
            "segment_blocks": lambda s: segment_blocks_stage.run(self.context, s),
            "extract_statements": lambda s: extract_statements_stage.run(self.context, s),
            "clean_nodes": lambda s: clean_nodes_stage.run(self.context, s),
            "split_nodes": lambda s: split_nodes_stage.run(self.context, s),
            "generate_titles": lambda s: generate_titles_stage.run(self.context, s),
            "math_disambiguation": lambda s: math_disambiguation_stage.run(self.context, s),
            "extract_logic_tuples": lambda s: extract_logic_tuples_stage.run(self.context, s),
            "analysis": lambda s: analysis_stage.run(self.context, s),
            "repair": lambda s: repair_stage.run(self.context, s),
            "extract_references": lambda s: extract_references_stage.run(self.context, s),
            "repair_lite": lambda s: repair_lite_stage.run(self.context, s),
            "compile_logic_form": lambda s: compile_logic_form_stage.run(self.context, s),
            "normalize_predicates": lambda s: normalize_predicates_stage.run(self.context, s),
            "build_relations": self._run_relation_stage,
            "finalize_output": lambda s: finalize_output_stage.run(self.context, s),
        }
        if stage_name not in runners:
            raise ValueError(f"Unknown stage: {stage_name}")
        return runners[stage_name](state)

    def _run_relation_stage(self, state):
        mode = (self.config.edge_output_mode or "structured").strip().lower()
        if mode not in {"structured", "natural", "both"}:
            raise ValueError("edge_output_mode only supports structured / natural / both")

        profile = (self.config.relation_prompt_profile or "graph").strip().lower()
        if profile not in {"graph", "formalization"}:
            raise ValueError("relation_prompt_profile only supports graph / formalization")

        relation_input_state = dict(state)
        if mode == "structured":
            state = build_relations_stage.run(
                self.context,
                dict(relation_input_state),
                relation_mode="structured",
                relation_prompt_profile=profile,
            )
            _write_agent_json(self.context, STRUCTURED_EDGE_CACHE_FILENAME, state.get("edge_list", []))
            return state

        if mode == "natural":
            state = build_relations_stage.run(
                self.context,
                dict(relation_input_state),
                relation_mode="natural",
                relation_prompt_profile=profile,
            )
            _write_agent_json(self.context, NATURAL_EDGE_CACHE_FILENAME, state.get("edge_list", []))
            return state

        structured_state = build_relations_stage.run(
            self.context,
            dict(relation_input_state),
            relation_mode="structured",
            relation_prompt_profile=profile,
        )
        natural_state = build_relations_stage.run(
            self.context,
            dict(relation_input_state),
            relation_mode="natural",
            relation_prompt_profile=profile,
        )
        structured_edges = structured_state.get("edge_list", [])
        natural_edges = natural_state.get("edge_list", [])
        state = structured_state
        state["edge_list_structured"] = structured_edges
        state["edge_list_natural"] = natural_edges
        _write_agent_json(self.context, STRUCTURED_EDGE_CACHE_FILENAME, structured_edges)
        _write_agent_json(self.context, NATURAL_EDGE_CACHE_FILENAME, natural_edges)
        natural_path = natural_edge_path(self.context.output_edge_path)
        if natural_path:
            write_json(natural_path, natural_edges)
            print(f"Edge JSON (natural mode) saved to: {natural_path}")
        return state

    def _persist_runtime_sidecars(self, stage_name, state):
        if stage_name == "repair_lite" and isinstance(state.get("node_dict"), dict):
            _write_agent_json(self.context, REPAIR_LITE_NODE_DICT_FILENAME, state["node_dict"])

    def _check_stage(self, stage_name, state, source):
        checker = getattr(self, f"_check_{stage_name}", None)
        if checker is None:
            return self._check_generic(stage_name, state, source)
        return checker(state, source)

    def _cache_files_exist(self, stage_name):
        spec = STAGE_SPECS[stage_name]
        if stage_name == "build_relations":
            return self._relation_cache_exists()
        if stage_name == "finalize_output":
            return self._final_outputs_exist()
        return all((self.cache_dir / filename).exists() for filename in spec.cache_files)

    def _check_generic(self, stage_name, state, source):
        issues = []
        if not self._cache_files_exist(stage_name):
            issues.append(_issue(stage_name, "error", "missing_output", "Expected stage output is missing."))
            return _stage_result(stage_name, "missing", source, issues)
        return _stage_result(stage_name, "cached_valid" if source == "cache" else "passed", source)

    def _check_correct_text(self, state, source):
        issues = []
        chopped = state.get("chopped_text_dict") or _read_json_optional(self.cache_dir / "corrected_text_dict.json", {})
        report = state.get("correct_text_report") or _read_json_optional(self.cache_dir / "correct_text_report.json", {})
        if not isinstance(chopped, dict) or not chopped:
            issues.append(_issue("correct_text", "error", "empty_corrected_text", "Corrected text dict is empty."))
        elif _contains_legacy_latex_freeze_token(chopped):
            issues.append(
                _issue(
                    "correct_text",
                    "warning",
                    "legacy_latex_freeze_token_present",
                    "Corrected text still contains legacy @@CATEGORY::command@@ LaTeX freeze tokens; rerun from correct_text before trusting downstream cache.",
                    retry_scope="stage",
                )
            )
        if isinstance(report, dict) and report:
            fallback_ratio = report.get("fallback_ratio", 0)
            if isinstance(fallback_ratio, (int, float)) and fallback_ratio > 0.30:
                issues.append(
                    _issue(
                        "correct_text",
                        "error",
                        "high_fallback_ratio",
                        f"Correct-text fallback ratio is {fallback_ratio:.1%}.",
                        retry_scope="stage",
                    )
                )
            for metric, code in (
                ("numbered_label_preservation_rate", "numbered_label_loss"),
            ):
                value = report.get(metric, 1)
                if isinstance(value, (int, float)) and value < 1:
                    issues.append(
                        _issue(
                            "correct_text",
                            "error",
                            code,
                            f"{metric} is {value:.1%}.",
                            retry_scope="stage",
                        )
                    )
            if report.get("warning_count"):
                issues.append(
                    _issue(
                        "correct_text",
                        "warning",
                        "correction_warnings",
                        f"Correct-text reported {report['warning_count']} unresolved warning(s).",
                    )
                )
        return _stage_result(
            "correct_text",
            "cached_valid" if source == "cache" and not issues else ("failed" if _has_errors(issues) else "passed"),
            source,
            issues,
            {
                "corrected_blocks": _len_mapping(chopped),
                "fallback_ratio": report.get("fallback_ratio") if isinstance(report, dict) else None,
                "warning_count": report.get("warning_count") if isinstance(report, dict) else None,
            },
        )

    def _check_segment_blocks(self, state, source):
        issues = []
        problem_dict = state.get("problem_dict") or _read_json_optional(self.cache_dir / "problem_dict.json", {})
        report = state.get("segment_blocks_report") or _read_json_optional(
            self.cache_dir / "segment_blocks_report.json", {}
        )
        if not isinstance(problem_dict, dict) or not problem_dict:
            issues.append(_issue("segment_blocks", "error", "empty_problem_dict", "Problem dict is empty."))
        if isinstance(report, dict) and report.get("all_units_consumed_once") is False:
            issues.append(
                _issue(
                    "segment_blocks",
                    "error",
                    "incomplete_unit_assignment",
                    "Not every corrected text unit was assigned to exactly one problem block.",
                )
            )
        if isinstance(report, dict) and report.get("classification_errors"):
            issues.append(
                _issue(
                    "segment_blocks",
                    "error",
                    "boundary_classification_failed",
                    "Semantic boundary classification failed; conservative fallback blocks require review.",
                )
            )
        return _stage_result(
            "segment_blocks",
            "cached_valid" if source == "cache" and not issues else ("failed" if _has_errors(issues) else "passed"),
            source,
            issues,
            {
                "problem_blocks": _len_mapping(problem_dict),
                "source_units": report.get("source_unit_count") if isinstance(report, dict) else None,
                "warning_count": len(report.get("warnings", [])) if isinstance(report, dict) else None,
                "all_units_consumed_once": (
                    report.get("all_units_consumed_once") if isinstance(report, dict) else None
                ),
            },
        )

    def _check_extract_statements(self, state, source):
        stage = "extract_statements"
        issues = []
        if not self._cache_files_exist(stage):
            issues.append(_issue(stage, "error", "missing_unsplit_statement_dict", "Unsplit statement output is missing."))
        unsplit = state.get("unsplit_statement_dict_raw") or _read_json_optional(self.cache_dir / "unsplit_statement_dict.json", {})
        report = state.get("extract_statements_report") or _read_json_optional(
            self.cache_dir / "extract_statements_report.json", {}
        )
        nodes = list(_iter_wrapped_nodes(unsplit))
        if not nodes:
            issues.append(_issue(stage, "error", "empty_nodes", "No extracted statement nodes were found."))
        empty_content = []
        missing_type = []
        proof_only = []
        for index, node in enumerate(nodes):
            if not _text(node.get("node_type")).strip():
                missing_type.append(index)
            if not _content_text(node):
                empty_content.append(index)
            if not _content_text(node) and _text(node.get("proof")).strip():
                proof_only.append(index)
        for index in empty_content[:10]:
            issues.append(_issue(stage, "error", "empty_content", "Extracted node has empty content.", item_ref=index, retry_scope="local"))
        for index in missing_type[:10]:
            issues.append(_issue(stage, "error", "missing_node_type", "Extracted node has no node_type.", item_ref=index, retry_scope="local"))
        for index in proof_only[:10]:
            issues.append(_issue(stage, "warning", "proof_without_content", "Node has proof text but no content.", item_ref=index, retry_scope="local"))
        if isinstance(report, dict) and report.get("missing_trusted_label_count"):
            issues.append(
                _issue(
                    stage,
                    "error",
                    "missing_trusted_labels",
                    f"{report['missing_trusted_label_count']} source-grounded label(s) remain missing.",
                    retry_scope="local",
                )
            )
        if isinstance(report, dict) and report.get("ambiguous_parent_label_count"):
            issues.append(
                _issue(
                    stage,
                    "error",
                    "ambiguous_parent_labels",
                    f"{report['ambiguous_parent_label_count']} labeled source block(s) have ambiguous parent nodes.",
                    retry_scope="local",
                )
            )
        return _stage_result(
            stage,
            "cached_valid" if source == "cache" and not _has_errors(issues) else ("failed" if _has_errors(issues) else "passed"),
            source,
            issues,
            {
                "node_count": len(nodes),
                "empty_content_count": len(empty_content),
                "missing_type_count": len(missing_type),
                "numbered_source_block_count": report.get("numbered_source_block_count") if isinstance(report, dict) else None,
                "preserved_label_count": report.get("preserved_label_count") if isinstance(report, dict) else None,
                "label_preservation_rate": report.get("label_preservation_rate") if isinstance(report, dict) else None,
                "label_conflict_count": report.get("label_conflict_count") if isinstance(report, dict) else None,
                "ambiguous_parent_label_count": report.get("ambiguous_parent_label_count") if isinstance(report, dict) else None,
            },
        )

    def _check_clean_nodes(self, state, source):
        stage = "clean_nodes"
        issues = []
        if not self._cache_files_exist(stage):
            issues.append(_issue(stage, "error", "missing_clean_nodes_outputs", "Clean node outputs are missing."))
        cleaned = state.get("unsplit_statement_dict") or _read_json_optional(
            self.cache_dir / "unsplit_statement_dict_cleaned.json",
            {},
        )
        raw = state.get("unsplit_statement_dict_raw") or _read_json_optional(
            self.cache_dir / "unsplit_statement_dict.json",
            {},
        )
        report = state.get("node_cleaning_report") or _read_json_optional(
            self.cache_dir / "node_cleaning_report.json",
            {},
        )
        quarantine = state.get("node_quarantine") or _read_json_optional(
            self.cache_dir / "node_quarantine.json",
            {},
        )
        if not isinstance(cleaned, dict) or not cleaned:
            issues.append(_issue(stage, "error", "empty_cleaned_nodes", "Cleaned unsplit node dict is empty."))
        if isinstance(raw, dict) and isinstance(cleaned, dict) and len(cleaned) > len(raw):
            issues.append(_issue(stage, "error", "cleaned_node_count_grew", "Cleaned node count exceeds raw node count."))
        if isinstance(report, dict) and report.get("invalid_chunk_count"):
            issues.append(
                _issue(
                    stage,
                    "warning",
                    "invalid_cleaning_chunks",
                    f"{report['invalid_chunk_count']} cleaning chunk(s) fell back to manual_review.",
                    retry_scope="stage",
                )
            )
        if isinstance(report, dict) and report.get("missing_decision_count"):
            issues.append(
                _issue(
                    stage,
                    "warning",
                    "missing_cleaning_decisions",
                    f"{report['missing_decision_count']} node(s) were retained because cleaning decisions were missing.",
                    retry_scope="stage",
                )
            )
        return _stage_result(
            stage,
            "cached_valid" if source == "cache" and not _has_errors(issues) else ("failed" if _has_errors(issues) else "passed"),
            source,
            issues,
            {
                "raw_node_count": _len_mapping(raw),
                "cleaned_node_count": _len_mapping(cleaned),
                "quarantined_node_count": _len_mapping(quarantine),
                "manual_review_count": report.get("manual_review_count") if isinstance(report, dict) else None,
                "invalid_chunk_count": report.get("invalid_chunk_count") if isinstance(report, dict) else None,
                "missing_decision_count": report.get("missing_decision_count") if isinstance(report, dict) else None,
            },
        )

    def _check_split_nodes(self, state, source):
        stage = "split_nodes"
        issues = []
        node_split_path = self.cache_dir / "node_split_dict.json"
        statement_path = self.cache_dir / "statement_without_title_dict.json"
        node_split_exists = node_split_path.exists()
        statement_exists = statement_path.exists()
        if not node_split_exists or not statement_exists:
            issues.append(_issue(stage, "error", "missing_split_outputs", "Split stage outputs are missing."))
        node_split = state.get("node_split_dict") or _read_json_optional(node_split_path, {})
        output = state.get("statement_without_title_dict") or _read_json_optional(self.cache_dir / "statement_without_title_dict.json", {})
        input_nodes = state.get("unsplit_statement_dict") or _read_json_optional(self.cache_dir / "unsplit_statement_dict.json", {})
        nodes = list(_iter_wrapped_nodes(output))
        input_count = _len_mapping(input_nodes)
        if not nodes:
            issues.append(_issue(stage, "error", "empty_split_output", "Split stage output is empty."))
        if input_count and len(nodes) < max(1, input_count // 2):
            issues.append(
                _issue(
                    stage,
                    "error",
                    "split_count_drop",
                    f"Split output count {len(nodes)} is unexpectedly below input count {input_count}.",
                    retry_scope="stage",
                )
            )
        missing_formal_core = [
            index
            for index, node in enumerate(nodes)
            if not _content_text(node)
        ]
        for index in missing_formal_core[:10]:
            issues.append(_issue(stage, "warning", "missing_formal_core", "Split node has no visible formal content.", item_ref=index))
        return _stage_result(
            stage,
            "cached_valid" if source == "cache" and not _has_errors(issues) else ("failed" if _has_errors(issues) else "passed"),
            source,
            issues,
            {
                "split_decision_count": _len_mapping(node_split),
                "split_node_count": len(nodes),
                "input_node_count": input_count,
                "node_split_output_exists": node_split_exists,
                "statement_without_title_output_exists": statement_exists,
            },
        )

    def _check_generate_titles(self, state, source):
        stage = "generate_titles"
        issues = []
        if not self._cache_files_exist(stage):
            issues.append(_issue(stage, "error", "missing_title_outputs", "Title stage outputs are missing."))
        definition_axiom = state.get("definition_axiom_dict") or _read_json_optional(self.cache_dir / "definition_axiom_dict.json", {})
        structured_input = state.get("structured_input_dict") or _read_json_optional(self.cache_dir / "structured_input_dict.json", {})
        nodes = list(_iter_wrapped_nodes(definition_axiom)) + list(_iter_wrapped_nodes(structured_input))
        if not nodes:
            issues.append(_issue(stage, "error", "empty_title_outputs", "Title stage did not produce downstream node buckets."))
        titled = [node for node in nodes if _title_text(node)]
        coverage = (len(titled) / len(nodes)) if nodes else 0.0
        if nodes and coverage < 0.35:
            issues.append(
                _issue(
                    stage,
                    "warning",
                    "low_title_coverage",
                    f"Only {coverage:.1%} of nodes have titles.",
                    retry_scope="stage",
                )
            )
        copied = []
        for index, node in enumerate(nodes):
            title = _title_text(node)
            content = _content_text(node)
            if title and content and len(title) > 80 and title[:80] in content:
                copied.append(index)
        for index in copied[:10]:
            issues.append(_issue(stage, "warning", "title_copies_statement", "Title appears to copy statement text.", item_ref=index))
        return _stage_result(
            stage,
            "cached_valid" if source == "cache" and not _has_errors(issues) else ("failed" if _has_errors(issues) else "passed"),
            source,
            issues,
            {"node_count": len(nodes), "title_coverage": round(coverage, 4), "copied_title_count": len(copied)},
        )

    def _check_math_disambiguation(self, state, source):
        stage = "math_disambiguation"
        definition_axiom = state.get("definition_axiom_dict") or {}
        structured_input = state.get("structured_input_dict") or {}
        if not self._cache_files_exist(stage):
            return _stage_result(
                stage,
                "missing",
                source,
                [_issue(stage, "error", "missing_disambiguation_output", "Math disambiguation outputs are missing.")],
            )
        return _stage_result(
            stage,
            "cached_valid" if source == "cache" else "passed",
            source,
            metrics={
                "definition_count": _len_mapping(definition_axiom),
                "structured_count": _len_mapping(structured_input),
            },
        )

    def _check_extract_logic_tuples(self, state, source):
        stage = "extract_logic_tuples"
        issues = []
        unresolved = extract_logic_tuples_stage.latest_unresolved_failure_report(self.context)
        if unresolved is not None:
            failed_keys = unresolved["report"].get("failed_task_keys") or []
            issues.append(
                _issue(
                    stage,
                    "error",
                    "incomplete_logic_tuple_tasks",
                    f"{len(failed_keys)} logic-tuple tasks remain unresolved.",
                    item_ref=failed_keys,
                    retry_scope="local",
                )
            )
        if not self._cache_files_exist(stage):
            issues.append(_issue(stage, "error", "missing_node_dict", "Node dict output is missing."))
        node_dict = state.get("node_dict") or _read_json_optional(self.cache_dir / "node_dict.json", {})
        nodes = list(_iter_wrapped_nodes(node_dict))
        if not nodes:
            issues.append(_issue(stage, "error", "empty_node_dict", "Logic tuple stage produced no nodes."))
        missing_statement_form = [
            index for index, node in enumerate(nodes)
            if _text(node.get("node_type")).strip() and not _text(node.get("statement_form")).strip()
        ]
        if len(missing_statement_form) > max(5, len(nodes) // 2):
            issues.append(
                _issue(
                    stage,
                    "warning",
                    "many_missing_statement_forms",
                    "Many nodes have no statement_form.",
                    retry_scope="stage",
                )
            )
        return _stage_result(
            stage,
            "cached_valid" if source == "cache" and not _has_errors(issues) else ("failed" if _has_errors(issues) else "passed"),
            source,
            issues,
            {
                "node_count": len(nodes),
                "missing_statement_form_count": len(missing_statement_form),
                "unresolved_task_count": len(unresolved["report"].get("failed_task_keys") or []) if unresolved else 0,
            },
        )

    def _check_analysis(self, state, source):
        stage = "analysis"
        if not self._cache_files_exist(stage):
            return _stage_result(stage, "missing", source, [_issue(stage, "error", "missing_analysis_result", "Analysis result is missing.")])
        result = _read_json_optional(self.cache_dir / "analysis_debug" / "analysis_result.json", {})
        return _stage_result(stage, "cached_valid" if source == "cache" else "passed", source, metrics={"analysis_count": _len_mapping(result)})

    def _check_repair(self, state, source):
        stage = "repair"
        if not self._cache_files_exist(stage):
            return _stage_result(stage, "missing", source, [_issue(stage, "error", "missing_repair_output", "Repair output is missing.")])
        report = _read_json_optional(self.cache_dir / "repair_patch_report.json", {})
        applied = _len_sequence(report.get("applied")) if isinstance(report, dict) else 0
        skipped = _len_sequence(report.get("skipped")) if isinstance(report, dict) else 0
        return _stage_result(stage, "cached_valid" if source == "cache" else "passed", source, metrics={"applied_repairs": applied, "skipped_repairs": skipped})

    def _check_extract_references(self, state, source):
        stage = "extract_references"
        issues = []
        references = state.get("node_dict") or _read_json_optional(self.cache_dir / "references_dict.json", {})
        unresolved = _read_json_optional(self.cache_dir / "references_unresolved.json", [])
        if not isinstance(references, dict) or not references:
            issues.append(_issue(stage, "error", "empty_references", "References dict is empty."))
        if isinstance(unresolved, list) and unresolved:
            issues.append(
                _issue(
                    stage,
                    "warning",
                    "unresolved_references",
                    f"{len(unresolved)} nodes have unresolved reference signals.",
                    retry_scope="manual",
                )
            )
        return _stage_result(
            stage,
            "cached_valid" if source == "cache" and not _has_errors(issues) else ("failed" if _has_errors(issues) else "passed"),
            source,
            issues,
            {"node_count": _len_mapping(references), "unresolved_count": _len_sequence(unresolved)},
        )

    def _check_repair_lite(self, state, source):
        stage = "repair_lite"
        if not self._cache_files_exist(stage):
            return _stage_result(stage, "missing", source, [_issue(stage, "error", "missing_repair_lite_report", "Repair lite report is missing.")])
        report = _read_json_optional(self.cache_dir / "repair_lite_report.json", [])
        return _stage_result(stage, "cached_valid" if source == "cache" else "passed", source, metrics={"repair_lite_items": _len_sequence(report)})

    def _check_compile_logic_form(self, state, source):
        stage = "compile_logic_form"
        issues = []
        logic_input = state.get("logic_form_input_dict") or _read_json_optional(self.cache_dir / "logic_form_input_dict.json", {})
        logic_output = state.get("logic_form_local_dict") or _read_json_optional(self.cache_dir / "logic_form_local_dict.json", {})
        nodes = state.get("node_list") or []
        if not self._cache_files_exist(stage):
            issues.append(_issue(stage, "error", "missing_logic_form_output", "Logic form outputs are missing."))
        if isinstance(logic_input, dict) and logic_input and not logic_output:
            issues.append(_issue(stage, "error", "empty_logic_form_result", "Logic form input is non-empty but result is empty."))
        render_errors = [
            index for index, node in enumerate(nodes)
            if isinstance(node, dict) and isinstance(node.get("logic_form_rendered"), str)
            and node["logic_form_rendered"].startswith("(RenderError")
        ]
        for index in render_errors[:10]:
            issues.append(_issue(stage, "warning", "logic_ast_render_error", "Logic AST did not render cleanly.", item_ref=index, retry_scope="local"))
        return _stage_result(
            stage,
            "cached_valid" if source == "cache" and not _has_errors(issues) else ("failed" if _has_errors(issues) else "passed"),
            source,
            issues,
            {
                "logic_input_count": _len_mapping(logic_input),
                "logic_result_count": _len_mapping(logic_output),
                "render_error_count": len(render_errors),
            },
        )

    def _check_normalize_predicates(self, state, source):
        stage = "normalize_predicates"
        issues = []
        registry = state.get("global_predicate_registry") or _read_json_optional(self.cache_dir / "global_predicate_registry.json", {})
        misuse = state.get("fixed_operator_misuse_report") or _read_json_optional(self.cache_dir / "fixed_operator_misuse_report.json", [])
        if not self._cache_files_exist(stage):
            issues.append(_issue(stage, "error", "missing_predicate_outputs", "Predicate normalization outputs are missing."))
        if isinstance(misuse, list) and misuse:
            issues.append(
                _issue(
                    stage,
                    "warning",
                    "fixed_operator_misuse",
                    f"{len(misuse)} fixed-operator rewrite issues were reported.",
                    retry_scope="manual",
                )
            )
        return _stage_result(
            stage,
            "cached_valid" if source == "cache" and not _has_errors(issues) else ("failed" if _has_errors(issues) else "passed"),
            source,
            issues,
            {"registry_size": _len_mapping(registry), "fixed_operator_misuse_count": _len_sequence(misuse)},
        )

    def _check_build_relations(self, state, source):
        stage = "build_relations"
        issues = []
        edge_list = state.get("edge_list") or self._load_cached_edges()
        node_list = state.get("node_list") or []
        if edge_list is None:
            issues.append(_issue(stage, "error", "missing_edge_list", "Relation edge list is missing."))
            edge_list = []
        if not isinstance(edge_list, list):
            issues.append(_issue(stage, "error", "invalid_edge_list", "Relation output is not a list."))
            edge_list = []
        node_ids = {node.get("global_id") for node in node_list if isinstance(node, dict) and node.get("global_id")}
        invalid_edges = []
        duplicate_edges = 0
        seen = set()
        for index, edge in enumerate(edge_list):
            if not isinstance(edge, dict):
                invalid_edges.append(index)
                continue
            start = _first_present(edge, EDGE_START_KEYS)
            end = _first_present(edge, EDGE_END_KEYS)
            relation = _first_present(edge, EDGE_RELATION_KEYS)
            key = (start, end, relation)
            if key in seen:
                duplicate_edges += 1
            seen.add(key)
            if not start or not end or (node_ids and (start not in node_ids or end not in node_ids)):
                invalid_edges.append(index)
        for index in invalid_edges[:10]:
            issues.append(_issue(stage, "error", "invalid_edge_reference", "Relation edge references an invalid node.", item_ref=index, retry_scope="stage"))
        if duplicate_edges:
            issues.append(_issue(stage, "warning", "duplicate_edges", f"{duplicate_edges} duplicate relation edges were detected."))
        if len(node_list) > 2 and not edge_list:
            issues.append(_issue(stage, "warning", "empty_relation_graph", "No relation edges were produced for a non-trivial node list."))
        return _stage_result(
            stage,
            "cached_valid" if source == "cache" and not _has_errors(issues) else ("failed" if _has_errors(issues) else "passed"),
            source,
            issues,
            {"edge_count": len(edge_list), "invalid_edge_count": len(invalid_edges), "duplicate_edge_count": duplicate_edges},
        )

    def _check_finalize_output(self, state, source):
        stage = "finalize_output"
        issues = []
        if self.context.output_node_path and not os.path.exists(self.context.output_node_path):
            issues.append(_issue(stage, "error", "missing_node_output", "Final node output file is missing."))
        if self.context.output_edge_path and not os.path.exists(self.context.output_edge_path):
            issues.append(_issue(stage, "error", "missing_edge_output", "Final edge output file is missing."))
        return _stage_result(
            stage,
            "cached_valid" if source == "cache" and not _has_errors(issues) else ("failed" if _has_errors(issues) else "passed"),
            source,
            issues,
        )

    def _relation_cache_exists(self):
        if (self.agent_state_dir / STRUCTURED_EDGE_CACHE_FILENAME).exists():
            return True
        if (self.agent_state_dir / NATURAL_EDGE_CACHE_FILENAME).exists():
            return True
        return bool(self.context.output_edge_path and os.path.exists(self.context.output_edge_path))

    def _final_outputs_exist(self):
        node_ok = (not self.context.output_node_path) or os.path.exists(self.context.output_node_path)
        edge_ok = (not self.context.output_edge_path) or os.path.exists(self.context.output_edge_path)
        return node_ok and edge_ok

    def _build_ledger(self, preflight, start_stage, executed):
        executed_by_stage = {item["stage"]: item for item in executed}
        stages = []
        for item in preflight:
            stage = item["stage"]
            if stage in executed_by_stage:
                stages.append(executed_by_stage[stage])
            else:
                stages.append(item)
        for item in executed:
            if item["stage"] not in {stage["stage"] for stage in stages}:
                stages.append(item)

        manual_review = []
        retry_plan = []
        for stage in stages:
            for issue in stage.get("issues", []):
                if issue.get("severity") == "error":
                    retry_plan.append(
                        {
                            "stage": issue.get("stage"),
                            "item_ref": issue.get("item_ref"),
                            "scope": issue.get("retry_scope") or "stage",
                            "reason": issue.get("code"),
                        }
                    )
                if issue.get("retry_scope") == "manual" or issue.get("severity") == "warning":
                    manual_review.append(
                        {
                            "stage": issue.get("stage"),
                            "item_ref": issue.get("item_ref"),
                            "reason": issue.get("code"),
                            "message": issue.get("message"),
                        }
                    )

        return {
            "schema_version": 1,
            "run_id": self.config.run_id,
            "input_path": self.context.file_path,
            "stage_cache_dir": str(self.cache_dir),
            "updated_at": _utc_now(),
            "start_stage": start_stage,
            "current_stage": self._current_stage_from_results(stages),
            "last_completed_stage": self._last_completed_stage(stages),
            "stages": stages,
            "retry_plan": retry_plan,
            "manual_review": manual_review,
        }

    def _current_stage_from_results(self, stages):
        for stage in stages:
            if stage.get("status") in {"missing", "failed", "forced"} or stage.get("action") == "stop":
                return stage.get("stage")
        return None

    def _last_completed_stage(self, stages):
        completed = None
        for stage in stages:
            if stage.get("status") in {"cached_valid", "passed"}:
                completed = stage.get("stage")
            elif stage.get("status") in {"missing", "failed", "forced"}:
                break
        return completed

    def _write_reports(self, state, ledger, completed):
        document_memory = self._build_document_memory(state, ledger, completed)
        run_report = self._build_run_report(document_memory, ledger, completed)
        _write_agent_json(self.context, DOCUMENT_MEMORY_FILENAME, document_memory)
        _write_agent_json(self.context, QUALITY_LEDGER_FILENAME, ledger)
        _write_agent_json(self.context, RUN_REPORT_JSON_FILENAME, run_report)
        md_path = self.agent_state_dir / RUN_REPORT_MD_FILENAME
        md_path.write_text(self._render_run_report_markdown(run_report, ledger), encoding="utf-8")
        return {
            "document_memory": document_memory,
            "quality_ledger": ledger,
            "run_report": run_report,
            "run_report_path": str(md_path),
        }

    def _build_document_memory(self, state, ledger, completed):
        node_list = state.get("node_list") or []
        node_types = {}
        for node in node_list:
            if not isinstance(node, dict):
                continue
            node_type = _text(node.get("node_type")).strip() or "unknown"
            node_types[node_type] = node_types.get(node_type, 0) + 1

        return {
            "schema_version": 1,
            "input_path": self.context.file_path,
            "stage_cache_dir": str(self.cache_dir),
            "updated_at": _utc_now(),
            "completed": completed,
            "current_stage": ledger.get("current_stage"),
            "last_completed_stage": ledger.get("last_completed_stage"),
            "stage_summaries": [
                {
                    "stage": stage.get("stage"),
                    "status": stage.get("status"),
                    "metrics": stage.get("metrics", {}),
                    "issue_count": len(stage.get("issues", [])),
                }
                for stage in ledger.get("stages", [])
            ],
            "counts": {
                "problem_blocks": _len_mapping(state.get("problem_dict")),
                "unsplit_nodes": _len_mapping(state.get("unsplit_statement_dict")),
                "final_nodes": len(node_list),
                "edges": _len_sequence(state.get("edge_list")),
            },
            "node_type_counts": node_types,
            "manual_review_count": len(ledger.get("manual_review", [])),
            "retry_plan_count": len(ledger.get("retry_plan", [])),
        }

    def _build_run_report(self, document_memory, ledger, completed):
        reused = [
            stage.get("stage")
            for stage in ledger.get("stages", [])
            if stage.get("status") == "cached_valid"
        ]
        executed = [
            stage.get("stage")
            for stage in ledger.get("stages", [])
            if stage.get("source") == "run"
        ]
        failed = [
            stage.get("stage")
            for stage in ledger.get("stages", [])
            if stage.get("status") == "failed" or _has_errors(stage.get("issues", []))
        ]
        return {
            "schema_version": 1,
            "run_id": self.config.run_id,
            "completed": completed,
            "input_path": self.context.file_path,
            "stage_cache_dir": str(self.cache_dir),
            "node_output_path": self.context.output_node_path,
            "edge_output_path": self.context.output_edge_path,
            "started_from_stage": ledger.get("start_stage"),
            "current_stage": ledger.get("current_stage"),
            "last_completed_stage": ledger.get("last_completed_stage"),
            "reused_cache_stages": reused,
            "executed_stages": executed,
            "failed_stages": failed,
            "counts": document_memory.get("counts", {}),
            "manual_review": ledger.get("manual_review", []),
            "retry_plan": ledger.get("retry_plan", []),
        }

    def _render_run_report_markdown(self, report, ledger):
        lines = [
            "# MathKG Main Agent Run Report",
            "",
            f"- Completed: {report.get('completed')}",
            f"- Input: `{report.get('input_path')}`",
            f"- Stage cache: `{report.get('stage_cache_dir')}`",
            f"- Started from stage: `{report.get('started_from_stage')}`",
            f"- Current stage: `{report.get('current_stage')}`",
            f"- Last completed stage: `{report.get('last_completed_stage')}`",
            "",
            "## Stage Gates",
            "",
            "| Stage | Status | Source | Issues | Key metrics |",
            "| --- | --- | --- | ---: | --- |",
        ]
        for stage in ledger.get("stages", []):
            metrics = ", ".join(f"{key}={value}" for key, value in (stage.get("metrics") or {}).items())
            lines.append(
                f"| {stage.get('stage')} | {stage.get('status')} | {stage.get('source')} | "
                f"{len(stage.get('issues', []))} | {metrics} |"
            )

        if report.get("retry_plan"):
            lines.extend(["", "## Retry Plan", ""])
            for item in report["retry_plan"]:
                lines.append(
                    f"- `{item.get('stage')}` scope=`{item.get('scope')}` "
                    f"item=`{item.get('item_ref')}` reason=`{item.get('reason')}`"
                )

        if report.get("manual_review"):
            lines.extend(["", "## Manual Review", ""])
            for item in report["manual_review"][:50]:
                lines.append(
                    f"- `{item.get('stage')}` item=`{item.get('item_ref')}` "
                    f"reason=`{item.get('reason')}`: {item.get('message')}"
                )
        return "\n".join(lines) + "\n"


def _first_present(mapping, keys):
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _ledger_has_errors(ledger):
    for stage in ledger.get("stages", []):
        if _has_errors(stage.get("issues", [])):
            return True
    return False
