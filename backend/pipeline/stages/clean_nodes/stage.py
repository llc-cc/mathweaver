import copy
import re

from ...common.io import save_stage_json
from ...common.llm_task import run_multiprocess_task
from ...common.stage_recovery import (
    latest_unresolved_failure_report as _latest_unresolved_failure_report,
    rerun_unresolved_task_report,
    run_recoverable_task,
    write_failure_report,
)
from .templates import (
    VALID_ACTIONS,
    VALID_CONFIDENCE,
    correction_prompt_clean_nodes,
    data_template_clean_nodes,
    prompt_template_clean_nodes,
    validation_clean_nodes,
)


STAGE_NAME = "clean_nodes"
DEFAULT_CHUNK_SIZE = 8
MATH_SIGNAL_PATTERN = re.compile(
    r"(\\\(|\\\[|\$|\\(?!begin\b|end\b)[a-zA-Z]+|[=<>≤≥∈⊂⊆→↔]|"
    r"\b(for all|there exists|exists|continuous|compact|closed|open|"
    r"homotop|mapping|function|space|subset|metric|group|ring|field|"
    r"prove that|show that|find all|definition of)\b)",
    re.IGNORECASE,
)


def _sort_key(value):
    try:
        return (0, int(str(value)))
    except (TypeError, ValueError):
        return (1, str(value))


def _node_payload(wrapper):
    if not isinstance(wrapper, dict):
        return {}
    node = wrapper.get("pos1")
    return node if isinstance(node, dict) else {}


def _text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = []
        for key in ("text", "text_normalized", "original_form", "content", "conclusion"):
            item = value.get(key)
            if isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    if isinstance(value, list):
        return "\n".join(_text(item) for item in value)
    return str(value)


def _title_text(node):
    title = node.get("title")
    if isinstance(title, dict):
        return " ".join(str(title.get(key, "")) for key in ("chinese", "english", "text"))
    return _text(title)


def _node_source_text(wrapper, node):
    return "\n".join(
        part
        for part in (
            _text(wrapper.get("source_text") if isinstance(wrapper, dict) else ""),
            _text(node.get("source_text")),
            _text(node.get("remark")),
            _text(node.get("content")),
            _text(node.get("proof")),
            _text(node.get("conditions")),
            _text(node.get("conclusions")),
            _title_text(node),
        )
        if part.strip()
    )


def _has_math_signal(wrapper):
    node = _node_payload(wrapper)
    text = _node_source_text(wrapper, node)
    if MATH_SIGNAL_PATTERN.search(text):
        return True
    content = _text(node.get("content"))
    if re.search(r"\b(prove|show|verify)\s+that\b", content, flags=re.IGNORECASE):
        return True
    return False


def _compact(value, limit=1200):
    value = re.sub(r"\s+", " ", _text(value)).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 20].rstrip() + " ...[truncated]"


def build_cleaning_input_dict(statement_dict, chunk_size=DEFAULT_CHUNK_SIZE):
    items = []
    keys = sorted((statement_dict or {}).keys(), key=_sort_key)
    for index, key in enumerate(keys):
        wrapper = statement_dict.get(key)
        node = _node_payload(wrapper)
        items.append(
            {
                "key": str(key),
                "node_type": _compact(node.get("node_type"), 120),
                "label": _compact(node.get("label"), 160),
                "title": _compact(_title_text(node), 240),
                "content": _compact(node.get("content")),
                "proof": _compact(node.get("proof")),
                "conditions": _compact(node.get("conditions")),
                "conclusions": _compact(node.get("conclusions")),
                "source_text": _compact(wrapper.get("source_text") if isinstance(wrapper, dict) else ""),
                "previous_source_summary": _compact(
                    (statement_dict.get(keys[index - 1], {}) or {}).get("source_text", "")
                    if index > 0
                    else ""
                ),
                "next_source_summary": _compact(
                    (statement_dict.get(keys[index + 1], {}) or {}).get("source_text", "")
                    if index + 1 < len(keys)
                    else ""
                ),
            }
        )

    tasks = {}
    for chunk_index, start in enumerate(range(0, len(items), chunk_size)):
        chunk = items[start : start + chunk_size]
        tasks[str(chunk_index)] = {
            "pos1": {
                "chunk_id": str(chunk_index),
                "nodes": chunk,
                "neighbor_context": {
                    "previous": chunk[0].get("previous_source_summary", "") if chunk else "",
                    "next": chunk[-1].get("next_source_summary", "") if chunk else "",
                },
            }
        }
    return tasks


def _normalize_decision(raw):
    if not isinstance(raw, dict):
        return {
            "action": "manual_review",
            "reason": "Missing or invalid LLM decision.",
            "confidence": "low",
            "evidence": ["invalid_decision_shape"],
        }
    action = str(raw.get("action", "")).strip()
    if action not in VALID_ACTIONS:
        action = "manual_review"
    confidence = str(raw.get("confidence", "low")).strip() or "low"
    if confidence not in VALID_CONFIDENCE:
        confidence = "low"
    evidence = raw.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = [str(evidence)]
    return {
        "action": action,
        "reason": str(raw.get("reason", "") or "No reason provided.").strip(),
        "confidence": confidence,
        "evidence": [str(item) for item in evidence],
    }


def apply_cleaning_decisions(statement_dict, decision_dict, input_dict):
    cleaned = {}
    quarantine = {}
    report_items = []
    missing_decision_count = 0
    invalid_chunk_count = 0
    quarantined_count = 0
    manual_review_count = 0
    keep_count = 0
    downgraded_count = 0

    decisions_by_key = {}
    expected_by_chunk = {}
    for chunk_key, task in (input_dict or {}).items():
        packet = (task or {}).get("pos1", {})
        expected = [str(item.get("key")) for item in packet.get("nodes", []) if isinstance(item, dict)]
        expected_by_chunk[str(chunk_key)] = expected
        chunk_result = (decision_dict or {}).get(chunk_key)
        if not isinstance(chunk_result, dict):
            invalid_chunk_count += 1
            for key in expected:
                decisions_by_key[key] = {
                    "action": "manual_review",
                    "reason": "LLM chunk output was missing or invalid; node retained for safety.",
                    "confidence": "low",
                    "evidence": ["invalid_or_missing_chunk"],
                }
            continue
        for key in expected:
            if key not in chunk_result:
                missing_decision_count += 1
                decisions_by_key[key] = {
                    "action": "manual_review",
                    "reason": "LLM omitted this node key; node retained for safety.",
                    "confidence": "low",
                    "evidence": ["missing_key_in_chunk_decision"],
                }
            else:
                decisions_by_key[key] = _normalize_decision(chunk_result.get(key))

    for key in sorted((statement_dict or {}).keys(), key=_sort_key):
        key_text = str(key)
        wrapper = copy.deepcopy(statement_dict.get(key))
        decision = decisions_by_key.get(
            key_text,
            {
                "action": "manual_review",
                "reason": "No cleaning decision was found; node retained for safety.",
                "confidence": "low",
                "evidence": ["missing_decision"],
            },
        )
        action = decision["action"]
        if action == "quarantine" and _has_math_signal(wrapper):
            action = "manual_review"
            decision = {
                **decision,
                "action": action,
                "reason": f"{decision['reason']} Downgraded from quarantine because deterministic math signals were present.",
                "evidence": list(decision.get("evidence", [])) + ["python_math_signal_guard"],
            }
            downgraded_count += 1

        item = {
            "key": key_text,
            "action": action,
            "reason": decision.get("reason", ""),
            "confidence": decision.get("confidence", "low"),
            "evidence": decision.get("evidence", []),
        }
        report_items.append(item)

        if action == "quarantine":
            quarantine[key_text] = {
                "wrapper": wrapper,
                "decision": item,
            }
            quarantined_count += 1
        else:
            cleaned[key] = wrapper
            if action == "manual_review":
                manual_review_count += 1
            else:
                keep_count += 1

    report = {
        "schema_version": 1,
        "input_node_count": len(statement_dict or {}),
        "cleaned_node_count": len(cleaned),
        "quarantined_node_count": quarantined_count,
        "manual_review_count": manual_review_count,
        "keep_count": keep_count,
        "missing_decision_count": missing_decision_count,
        "invalid_chunk_count": invalid_chunk_count,
        "downgraded_quarantine_count": downgraded_count,
        "chunk_count": len(input_dict or {}),
        "expected_keys_by_chunk": expected_by_chunk,
        "items": report_items,
    }
    return cleaned, quarantine, report


def _validated_cleaning_results(input_dict, result_dict):
    validated = {}
    for chunk_key, task in (input_dict or {}).items():
        packet = (task or {}).get("pos1", {})
        expected = [str(item.get("key")) for item in packet.get("nodes", []) if isinstance(item, dict)]
        raw_result = (result_dict or {}).get(str(chunk_key))
        if not validation_clean_nodes(raw_result):
            continue
        normalized = {str(key): value for key, value in raw_result.items()}
        if set(normalized) == set(expected):
            validated[str(chunk_key)] = normalized
    return validated


def _run_cleaning_tasks(context, input_dict, checkpoint_dir):
    result_dict = run_multiprocess_task(
        llm=context.llm,
        parse_method=context.parser.parse_dict,
        data_template=data_template_clean_nodes,
        prompt_template=prompt_template_clean_nodes,
        correction_template=correction_prompt_clean_nodes,
        validator=validation_clean_nodes,
        index_dict=input_dict,
        num_threads=context.num_threads,
        checkpoint=context.checkpoint,
        checkpoint_dir=checkpoint_dir,
    )
    return _validated_cleaning_results(input_dict, result_dict)


def latest_unresolved_failure_report(context):
    return _latest_unresolved_failure_report(context, STAGE_NAME)


def _finalize_outputs(context, state, raw_statement_dict, input_dict, decision_dict, *, run_dir=None, attempts=1):
    cleaned, quarantine, report = apply_cleaning_decisions(raw_statement_dict, decision_dict, input_dict)
    save_stage_json(context.output_dir, "unsplit_statement_dict_raw.json", raw_statement_dict, "Raw unsplit statement dict")
    save_stage_json(context.output_dir, "node_cleaning_decision_dict.json", decision_dict, "Node cleaning decision dict")
    save_stage_json(context.output_dir, "unsplit_statement_dict_cleaned.json", cleaned, "Cleaned unsplit statement dict")
    save_stage_json(context.output_dir, "node_quarantine.json", quarantine, "Node quarantine")
    save_stage_json(context.output_dir, "node_cleaning_report.json", report, "Node cleaning report")
    failure_report = None
    if run_dir is not None:
        failure_report = write_failure_report(
            run_dir,
            run_dir.name,
            STAGE_NAME,
            [str(key) for key in input_dict],
            decision_dict,
            attempts=attempts,
            canonical_updated=True,
        )
        state["clean_nodes_stage_run"] = failure_report
    state["unsplit_statement_dict_raw"] = raw_statement_dict
    state["unsplit_statement_dict"] = cleaned
    state["node_cleaning_decision_dict"] = decision_dict
    state["node_quarantine"] = quarantine
    state["node_cleaning_report"] = report
    return state, failure_report


def run(context, state):
    statement_dict = state.get("unsplit_statement_dict")
    if not isinstance(statement_dict, dict) or not statement_dict:
        raise RuntimeError("clean_nodes requires unsplit_statement_dict from extract_statements.")

    raw_statement_dict = copy.deepcopy(statement_dict)
    input_dict = build_cleaning_input_dict(raw_statement_dict)
    if input_dict:
        decision_dict, failure_report, run_dir = run_recoverable_task(
            context,
            stage_name=STAGE_NAME,
            input_dict=input_dict,
            task_runner=lambda selected, checkpoint_dir: _run_cleaning_tasks(
                context,
                selected,
                checkpoint_dir,
            ),
        )
    else:
        decision_dict, failure_report, run_dir = {}, {"attempt_rounds": 0}, None
    state, _ = _finalize_outputs(
        context,
        state,
        raw_statement_dict,
        input_dict,
        decision_dict,
        run_dir=run_dir,
        attempts=failure_report.get("attempt_rounds") or 1,
    )
    return state


def rerun_failed_tasks(context, state, max_rounds=2):
    raw_statement_dict = copy.deepcopy(
        state.get("unsplit_statement_dict_raw") or state.get("unsplit_statement_dict") or {}
    )
    input_dict = build_cleaning_input_dict(raw_statement_dict)
    decision_dict, failure_report, run_dir = rerun_unresolved_task_report(
        context,
        stage_name=STAGE_NAME,
        task_runner=lambda selected, checkpoint_dir: _run_cleaning_tasks(
            context,
            selected,
            checkpoint_dir,
        ),
        max_rounds=max_rounds,
    )
    if failure_report.get("status") != "resolved":
        state["clean_nodes_stage_run"] = failure_report
        return state, failure_report
    state, final_report = _finalize_outputs(
        context,
        state,
        raw_statement_dict,
        input_dict,
        decision_dict,
        run_dir=run_dir,
        attempts=failure_report.get("attempt_rounds") or 1,
    )
    return state, final_report
