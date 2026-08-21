import copy
import os
from pathlib import Path

from ...common.io import read_json, save_stage_json
from ...common.llm_task import run_multiprocess_task
from ...common.node import get_node_formal_content
from ...common.stage_recovery import (
    latest_unresolved_failure_report as _latest_unresolved_failure_report,
    rerun_unresolved_task_report,
    run_recoverable_task,
    stage_runs_root,
    write_failure_report,
)
from .ambiguity_table import get_ambiguity_table
from .templates import correction_prompt10, data_template10, prompt_template10, validation10


STAGE_NAME = "math_disambiguation"
DEFINITION_BRANCH_STAGE = "math_disambiguation_definition_axiom_dict"
STRUCTURED_BRANCH_STAGE = "math_disambiguation_structured_input_dict"


def _safe_sort_key(value):
    try:
        return (0, int(str(value)))
    except (TypeError, ValueError):
        return (1, str(value))


def _coerce_text(value):
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _extract_node_content(node):
    if not isinstance(node, dict):
        return ""
    return get_node_formal_content(node)


def _extract_wrapped_node(entry):
    if not isinstance(entry, dict):
        return None
    pos1 = entry.get("pos1")
    return pos1 if isinstance(pos1, dict) else None


def _normalize_candidate_meanings(raw_meanings):
    if isinstance(raw_meanings, (list, tuple, set)):
        return [_coerce_text(item).strip() for item in raw_meanings if _coerce_text(item).strip()]
    if isinstance(raw_meanings, str) and raw_meanings.strip():
        return [raw_meanings.strip()]
    return []


def compile_ambiguity_symbols(ambiguity_table=None):
    table = get_ambiguity_table(ambiguity_table)
    if not isinstance(table, dict):
        return []

    compiled = []
    for raw_symbol, raw_meanings in table.items():
        symbol = _coerce_text(raw_symbol).strip()
        if not symbol:
            continue
        compiled.append(
            {
                "symbol": symbol,
                "candidate_meanings": _normalize_candidate_meanings(raw_meanings),
            }
        )

    compiled.sort(key=lambda item: (-len(item["symbol"]), item["symbol"]))
    return compiled


def _spans_overlap(start, end, occupied_spans):
    for occupied_start, occupied_end in occupied_spans:
        if start < occupied_end and occupied_start < end:
            return True
    return False


def _previous_non_whitespace_char(text, index):
    cursor = index - 1
    while cursor >= 0 and text[cursor].isspace():
        cursor -= 1
    return text[cursor] if cursor >= 0 else ""


def _next_non_whitespace_char(text, index):
    cursor = index
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    return text[cursor] if cursor < len(text) else ""


def _is_bar_boundary_char(char):
    if not char:
        return True
    return not (char.isalnum() or char in {"_", "\\"})


def _has_left_bar_boundary(text, start):
    return _is_bar_boundary_char(_previous_non_whitespace_char(text, start))


def _has_right_bar_boundary(text, end):
    return _is_bar_boundary_char(_next_non_whitespace_char(text, end))


def _is_part_of_double_bar(text, index):
    if not (0 <= index < len(text)) or text[index] != "|":
        return False
    return (index > 0 and text[index - 1] == "|") or (index + 1 < len(text) and text[index + 1] == "|")


def _collect_paired_bar_matches(text, *, symbol_key, pair_token, candidate_meanings, occupied_spans):
    matches = []
    token_length = len(pair_token)
    search_from = 0

    while True:
        start = text.find(pair_token, search_from)
        if start == -1:
            break

        open_end = start + token_length
        if _spans_overlap(start, open_end, occupied_spans):
            search_from = start + 1
            continue
        if pair_token == "|" and _is_part_of_double_bar(text, start):
            search_from = start + 1
            continue
        if not _has_left_bar_boundary(text, start):
            search_from = start + 1
            continue

        close_search_from = open_end
        found_match = False
        while True:
            close_start = text.find(pair_token, close_search_from)
            if close_start == -1:
                break

            close_end = close_start + token_length
            if _spans_overlap(close_start, close_end, occupied_spans):
                close_search_from = close_start + 1
                continue
            if pair_token == "|" and _is_part_of_double_bar(text, close_start):
                close_search_from = close_start + 1
                continue

            inner_text = text[open_end:close_start]
            if not inner_text.strip():
                close_search_from = close_start + 1
                continue
            if not _has_right_bar_boundary(text, close_end):
                close_search_from = close_start + 1
                continue
            if _spans_overlap(start, close_end, occupied_spans):
                close_search_from = close_start + 1
                continue

            match = {
                "text": text[start:close_end],
                "start": start,
                "end": close_end,
            }
            matches.append(match)
            occupied_spans.append((start, close_end))
            search_from = close_end
            found_match = True
            break

        if not found_match:
            search_from = start + token_length

    if not matches:
        return None

    return {
        "symbol": symbol_key,
        "candidate_meanings": copy.deepcopy(candidate_meanings),
        "match_count": len(matches),
        "matches": matches,
    }


def find_ambiguity_hits(text, compiled_symbols):
    text = _coerce_text(text)
    if not text or not compiled_symbols:
        return []

    occupied_spans = []
    hits = []
    symbol_lookup = {item["symbol"]: item for item in compiled_symbols}

    for symbol_key, pair_token in (("||...||", "||"), ("|...|", "|")):
        symbol_info = symbol_lookup.get(symbol_key)
        if not symbol_info:
            continue
        paired_hit = _collect_paired_bar_matches(
            text,
            symbol_key=symbol_key,
            pair_token=pair_token,
            candidate_meanings=symbol_info["candidate_meanings"],
            occupied_spans=occupied_spans,
        )
        if paired_hit:
            hits.append(paired_hit)

    for symbol_info in compiled_symbols:
        symbol = symbol_info["symbol"]
        if not symbol or symbol in {"|...|", "||...||"}:
            continue

        matches = []
        search_from = 0
        while True:
            start = text.find(symbol, search_from)
            if start == -1:
                break
            end = start + len(symbol)
            if not _spans_overlap(start, end, occupied_spans):
                matches.append(
                    {
                        "text": symbol,
                        "start": start,
                        "end": end,
                    }
                )
                occupied_spans.append((start, end))
            search_from = start + max(len(symbol), 1)

        if matches:
            matches.sort(key=lambda item: (item["start"], item["end"]))
            hits.append(
                {
                    "symbol": symbol,
                    "candidate_meanings": copy.deepcopy(symbol_info["candidate_meanings"]),
                    "match_count": len(matches),
                    "matches": matches,
                }
            )

    return hits


def scan_node_dict_for_ambiguity(node_dict, *, ambiguity_table, container_name, is_wrapped):
    compiled_symbols = compile_ambiguity_symbols(ambiguity_table)
    ambiguous_node_dict = {}
    if not compiled_symbols:
        return ambiguous_node_dict

    for source_key in sorted((node_dict or {}).keys(), key=_safe_sort_key):
        entry = (node_dict or {}).get(source_key)
        node = _extract_wrapped_node(entry) if is_wrapped else entry
        if not isinstance(node, dict):
            continue

        original_content = _extract_node_content(node)
        hits = find_ambiguity_hits(original_content, compiled_symbols)
        if not hits:
            continue

        ambiguous_node_dict[source_key] = {
            "source_key": source_key,
            "container_name": container_name,
            "node": copy.deepcopy(node),
            "original_content": original_content,
            "symbol_hits": [hit["symbol"] for hit in hits],
            "candidate_meanings": {
                hit["symbol"]: copy.deepcopy(hit.get("candidate_meanings", []))
                for hit in hits
            },
            "ambiguity_hits": copy.deepcopy(hits),
        }

    return ambiguous_node_dict


def _strip_ambiguity_hits(node):
    if not isinstance(node, dict):
        return node
    cleaned_node = copy.deepcopy(node)
    cleaned_node.pop("ambiguity_hits", None)
    return cleaned_node


def build_disambiguation_tasks(ambiguous_node_dict):
    task_dict = {}
    for source_key in sorted((ambiguous_node_dict or {}).keys(), key=_safe_sort_key):
        scan_info = ambiguous_node_dict[source_key]
        node_payload = copy.deepcopy(scan_info["node"])
        if not isinstance(node_payload.get("content"), str):
            node_payload["content"] = scan_info.get("original_content") or ""
        node_payload["ambiguity_hits"] = copy.deepcopy(scan_info.get("ambiguity_hits", []))
        task_dict[source_key] = {
            "pos1": node_payload,
            "source_key": _coerce_text(scan_info.get("source_key")),
        }
    return task_dict


def restore_definition_axiom_dict(definition_axiom_dict, ambiguous_node_dict, llm_result_dict):
    restored_dict = copy.deepcopy(definition_axiom_dict or {})
    for source_key in sorted((ambiguous_node_dict or {}).keys(), key=_safe_sort_key):
        llm_node = (llm_result_dict or {}).get(source_key)
        if isinstance(llm_node, dict):
            original_node = restored_dict.get(source_key)
            if isinstance(original_node, dict):
                restored_dict[source_key] = _merge_disambiguated_content(original_node, llm_node)
    return restored_dict


def restore_structured_input_dict(structured_input_dict, ambiguous_node_dict, llm_result_dict):
    restored_dict = copy.deepcopy(structured_input_dict or {})
    for source_key in sorted((ambiguous_node_dict or {}).keys(), key=_safe_sort_key):
        entry = restored_dict.get(source_key)
        llm_node = (llm_result_dict or {}).get(source_key)
        if not isinstance(entry, dict) or not isinstance(llm_node, dict):
            continue
        original_node = entry.get("pos1")
        if isinstance(original_node, dict):
            entry["pos1"] = _merge_disambiguated_content(original_node, llm_node)
        restored_dict[source_key] = entry
    return restored_dict


def _merge_disambiguated_content(original_node, llm_node):
    merged = _strip_ambiguity_hits(original_node)
    content_after = llm_node.get("content")
    if isinstance(content_after, str) and content_after.strip():
        # Preserve authoritative source fields and store the LLM rewrite as a
        # derived view. get_node_content() opts into this view by default.
        merged["disambiguated_content"] = content_after

    proof_after = llm_node.get("proof")
    if isinstance(proof_after, str):
        merged["proof"] = proof_after
    return merged


def _run_disambiguation_branch(
    node_dict,
    *,
    llm,
    parser,
    ambiguity_table,
    num_threads,
    checkpoint,
    checkpoint_dir,
    debug_output_dir,
    container_name,
    is_wrapped,
):
    ambiguous_node_dict = scan_node_dict_for_ambiguity(
        node_dict,
        ambiguity_table=ambiguity_table,
        container_name=container_name,
        is_wrapped=is_wrapped,
    )
    if debug_output_dir:
        save_stage_json(
            debug_output_dir,
            f"{container_name}_ambiguous_node_dict.json",
            ambiguous_node_dict,
            f"Ambiguous node dict for {container_name}",
        )

    if not ambiguous_node_dict:
        return copy.deepcopy(node_dict or {})

    task_dict = build_disambiguation_tasks(ambiguous_node_dict)
    if debug_output_dir:
        save_stage_json(
            debug_output_dir,
            f"{container_name}_task_dict.json",
            task_dict,
            f"Disambiguation task dict for {container_name}",
        )

    result_dict = run_multiprocess_task(
        llm=llm,
        parse_method=parser.parse_dict,
        data_template=data_template10,
        prompt_template=prompt_template10,
        correction_template=correction_prompt10,
        validator=validation10,
        index_dict=task_dict,
        num_threads=num_threads,
        checkpoint=checkpoint,
        checkpoint_dir=checkpoint_dir,
    )
    if debug_output_dir:
        save_stage_json(
            debug_output_dir,
            f"{container_name}_result_dict.json",
            result_dict,
            f"Disambiguation result dict for {container_name}",
        )

    if is_wrapped:
        return restore_structured_input_dict(node_dict, ambiguous_node_dict, result_dict)
    return restore_definition_axiom_dict(node_dict, ambiguous_node_dict, result_dict)


def run_math_disambiguation_layer(
    definition_axiom_dict,
    structured_input_dict,
    *,
    llm,
    parser,
    ambiguity_table,
    num_threads,
    checkpoint,
    checkpoint_dir=None,
    debug_output_dir=None,
):
    definition_checkpoint_dir = os.path.join(checkpoint_dir, "definition_axiom_dict") if checkpoint_dir else None
    structured_checkpoint_dir = os.path.join(checkpoint_dir, "structured_input_dict") if checkpoint_dir else None

    definition_result = _run_disambiguation_branch(
        definition_axiom_dict,
        llm=llm,
        parser=parser,
        ambiguity_table=ambiguity_table,
        num_threads=num_threads,
        checkpoint=checkpoint,
        checkpoint_dir=definition_checkpoint_dir,
        debug_output_dir=debug_output_dir,
        container_name="definition_axiom_dict",
        is_wrapped=False,
    )
    structured_result = _run_disambiguation_branch(
        structured_input_dict,
        llm=llm,
        parser=parser,
        ambiguity_table=ambiguity_table,
        num_threads=num_threads,
        checkpoint=checkpoint,
        checkpoint_dir=structured_checkpoint_dir,
        debug_output_dir=debug_output_dir,
        container_name="structured_input_dict",
        is_wrapped=True,
    )

    return definition_result, structured_result


def _branch_stage_name(container_name):
    if container_name == "definition_axiom_dict":
        return DEFINITION_BRANCH_STAGE
    if container_name == "structured_input_dict":
        return STRUCTURED_BRANCH_STAGE
    raise ValueError(f"Unknown math_disambiguation container: {container_name}")


def _run_disambiguation_tasks(context, index_dict, checkpoint_dir):
    return run_multiprocess_task(
        llm=context.llm,
        parse_method=context.parser.parse_dict,
        data_template=data_template10,
        prompt_template=prompt_template10,
        correction_template=correction_prompt10,
        validator=validation10,
        index_dict=index_dict,
        num_threads=context.num_threads,
        checkpoint=context.checkpoint,
        checkpoint_dir=checkpoint_dir,
    )


def _build_branch_task_dict(context, node_dict, *, container_name, is_wrapped, debug_output_dir=None):
    ambiguous_node_dict = scan_node_dict_for_ambiguity(
        node_dict,
        ambiguity_table=getattr(context, "ambiguity_table", None),
        container_name=container_name,
        is_wrapped=is_wrapped,
    )
    if debug_output_dir:
        save_stage_json(
            debug_output_dir,
            f"{container_name}_ambiguous_node_dict.json",
            ambiguous_node_dict,
            f"Ambiguous node dict for {container_name}",
        )
    task_dict = build_disambiguation_tasks(ambiguous_node_dict)
    if debug_output_dir:
        save_stage_json(
            debug_output_dir,
            f"{container_name}_task_dict.json",
            task_dict,
            f"Disambiguation task dict for {container_name}",
        )
    return ambiguous_node_dict, task_dict


def _restore_branch(node_dict, ambiguous_node_dict, result_dict, *, is_wrapped):
    if not ambiguous_node_dict:
        return copy.deepcopy(node_dict or {})
    if is_wrapped:
        return restore_structured_input_dict(node_dict, ambiguous_node_dict, result_dict)
    return restore_definition_axiom_dict(node_dict, ambiguous_node_dict, result_dict)


def _latest_resolved_partial_result(context, branch_stage_name):
    reports = []
    for report_path in stage_runs_root(context, branch_stage_name).glob("*/failure_report.json"):
        try:
            report = read_json(str(report_path))
        except Exception:
            continue
        if isinstance(report, dict) and report.get("status") == "resolved":
            reports.append((report_path.stat().st_mtime, report))
    if not reports:
        return None, None
    _, report = max(reports, key=lambda item: item[0])
    partial_path = report.get("partial_result_dict_path")
    if not partial_path:
        return None, report
    path = Path(partial_path)
    if not path.exists():
        return None, report
    return read_json(str(path)), report


def latest_unresolved_failure_report(context):
    latest = None
    for branch_stage in (DEFINITION_BRANCH_STAGE, STRUCTURED_BRANCH_STAGE):
        unresolved = _latest_unresolved_failure_report(context, branch_stage)
        if not unresolved:
            continue
        report_path = Path(unresolved["path"])
        item = (report_path.stat().st_mtime, branch_stage, unresolved)
        if latest is None or item[0] > latest[0]:
            latest = item
    if latest is None:
        return None
    _, branch_stage, unresolved = latest
    return {
        **unresolved,
        "branch_stage": branch_stage,
        "report": {
            **unresolved["report"],
            "stage": STAGE_NAME,
            "branch_stage": branch_stage,
        },
    }


def _run_branch(context, node_dict, *, container_name, is_wrapped, debug_output_dir):
    branch_stage = _branch_stage_name(container_name)
    ambiguous_node_dict, task_dict = _build_branch_task_dict(
        context,
        node_dict,
        container_name=container_name,
        is_wrapped=is_wrapped,
        debug_output_dir=debug_output_dir,
    )
    if not task_dict:
        return _restore_branch(node_dict, ambiguous_node_dict, {}, is_wrapped=is_wrapped), None, None, task_dict, {}
    result_dict, failure_report, run_dir = run_recoverable_task(
        context,
        stage_name=branch_stage,
        input_dict=task_dict,
        task_runner=lambda index_dict, checkpoint_dir: _run_disambiguation_tasks(context, index_dict, checkpoint_dir),
    )
    if debug_output_dir:
        save_stage_json(
            debug_output_dir,
            f"{container_name}_result_dict.json",
            result_dict,
            f"Disambiguation result dict for {container_name}",
        )
    if failure_report.get("status") != "resolved":
        return None, failure_report, run_dir, task_dict, result_dict
    return _restore_branch(node_dict, ambiguous_node_dict, result_dict, is_wrapped=is_wrapped), failure_report, run_dir, task_dict, result_dict


def _write_branch_resolved(run_dir, branch_stage, task_dict, result_dict, attempts):
    if run_dir is None:
        return
    write_failure_report(
        run_dir,
        run_dir.name,
        branch_stage,
        [str(key) for key in task_dict.keys()],
        result_dict,
        attempts=attempts,
        canonical_updated=True,
    )


def run(context, state):
    if not getattr(context, "enable_math_disambiguation", True):
        return state

    debug_output_dir = os.path.join(context.output_dir, "math_disambiguation_debug")
    definition_axiom_dict, definition_report, definition_run_dir, definition_tasks, definition_result = _run_branch(
        context,
        state.get("definition_axiom_dict", {}),
        container_name="definition_axiom_dict",
        is_wrapped=False,
        debug_output_dir=debug_output_dir,
    )
    if definition_report and definition_report.get("status") != "resolved":
        state["math_disambiguation_stage_run"] = {
            **definition_report,
            "stage": STAGE_NAME,
            "branch_stage": DEFINITION_BRANCH_STAGE,
        }
        if getattr(context, "execution_mode", "pipeline") != "pipeline":
            return state
        definition_axiom_dict = _restore_branch(
            state.get("definition_axiom_dict", {}),
            _build_branch_task_dict(
                context,
                state.get("definition_axiom_dict", {}),
                container_name="definition_axiom_dict",
                is_wrapped=False,
                debug_output_dir=None,
            )[0],
            definition_result,
            is_wrapped=False,
        )

    structured_input_dict, structured_report, structured_run_dir, structured_tasks, structured_result = _run_branch(
        context,
        state.get("structured_input_dict", {}),
        container_name="structured_input_dict",
        is_wrapped=True,
        debug_output_dir=debug_output_dir,
    )
    if structured_report and structured_report.get("status") != "resolved":
        state["math_disambiguation_stage_run"] = {
            **structured_report,
            "stage": STAGE_NAME,
            "branch_stage": STRUCTURED_BRANCH_STAGE,
        }
        if getattr(context, "execution_mode", "pipeline") != "pipeline":
            return state
        structured_input_dict = _restore_branch(
            state.get("structured_input_dict", {}),
            _build_branch_task_dict(
                context,
                state.get("structured_input_dict", {}),
                container_name="structured_input_dict",
                is_wrapped=True,
                debug_output_dir=None,
            )[0],
            structured_result,
            is_wrapped=True,
        )

    save_stage_json(
        context.output_dir,
        "definition_axiom_dict_disambiguated.json",
        definition_axiom_dict,
        "Definition/axiom dict after disambiguation",
    )
    save_stage_json(
        context.output_dir,
        "structured_input_dict_disambiguated.json",
        structured_input_dict,
        "Structured input dict after disambiguation",
    )

    _write_branch_resolved(
        definition_run_dir,
        DEFINITION_BRANCH_STAGE,
        definition_tasks,
        definition_result,
        (definition_report or {}).get("attempt_rounds") or 1,
    )
    _write_branch_resolved(
        structured_run_dir,
        STRUCTURED_BRANCH_STAGE,
        structured_tasks,
        structured_result,
        (structured_report or {}).get("attempt_rounds") or 1,
    )

    state["definition_axiom_dict"] = definition_axiom_dict
    state["structured_input_dict"] = structured_input_dict
    return state


def rerun_failed_tasks(context, state, max_rounds=2):
    unresolved = latest_unresolved_failure_report(context)
    if unresolved is None:
        raise RuntimeError(f"No unresolved failure report for stage: {STAGE_NAME}")
    branch_stage = unresolved["branch_stage"]
    debug_output_dir = os.path.join(context.output_dir, "math_disambiguation_debug")

    definition_axiom_dict = state.get("definition_axiom_dict", {})
    structured_input_dict = state.get("structured_input_dict", {})

    definition_ambiguous, definition_tasks = _build_branch_task_dict(
        context,
        definition_axiom_dict,
        container_name="definition_axiom_dict",
        is_wrapped=False,
        debug_output_dir=debug_output_dir,
    )
    structured_ambiguous, structured_tasks = _build_branch_task_dict(
        context,
        structured_input_dict,
        container_name="structured_input_dict",
        is_wrapped=True,
        debug_output_dir=debug_output_dir,
    )

    rerun_result, rerun_report, rerun_dir = rerun_unresolved_task_report(
        context,
        stage_name=branch_stage,
        task_runner=lambda index_dict, checkpoint_dir: _run_disambiguation_tasks(context, index_dict, checkpoint_dir),
        max_rounds=max_rounds,
    )
    if rerun_report.get("status") != "resolved":
        state["math_disambiguation_stage_run"] = {
            **rerun_report,
            "stage": STAGE_NAME,
            "branch_stage": branch_stage,
        }
        return state, state["math_disambiguation_stage_run"]

    if branch_stage == DEFINITION_BRANCH_STAGE:
        definition_result = rerun_result
        definition_report = rerun_report
        definition_run_dir = rerun_dir
        structured_result, structured_report = _latest_resolved_partial_result(context, STRUCTURED_BRANCH_STAGE)
        structured_run_dir = None
        if structured_tasks and structured_result is None:
            structured_result, structured_report, structured_run_dir = run_recoverable_task(
                context,
                stage_name=STRUCTURED_BRANCH_STAGE,
                input_dict=structured_tasks,
                task_runner=lambda index_dict, checkpoint_dir: _run_disambiguation_tasks(context, index_dict, checkpoint_dir),
            )
            if structured_report.get("status") != "resolved":
                state["math_disambiguation_stage_run"] = {
                    **structured_report,
                    "stage": STAGE_NAME,
                    "branch_stage": STRUCTURED_BRANCH_STAGE,
                }
                return state, state["math_disambiguation_stage_run"]
    else:
        structured_result = rerun_result
        structured_report = rerun_report
        structured_run_dir = rerun_dir
        definition_result, definition_report = _latest_resolved_partial_result(context, DEFINITION_BRANCH_STAGE)
        definition_run_dir = None
        if definition_tasks and definition_result is None:
            definition_result, definition_report, definition_run_dir = run_recoverable_task(
                context,
                stage_name=DEFINITION_BRANCH_STAGE,
                input_dict=definition_tasks,
                task_runner=lambda index_dict, checkpoint_dir: _run_disambiguation_tasks(context, index_dict, checkpoint_dir),
            )
            if definition_report.get("status") != "resolved":
                state["math_disambiguation_stage_run"] = {
                    **definition_report,
                    "stage": STAGE_NAME,
                    "branch_stage": DEFINITION_BRANCH_STAGE,
                }
                return state, state["math_disambiguation_stage_run"]

    definition_result = definition_result or {}
    structured_result = structured_result or {}
    definition_axiom_dict = _restore_branch(
        definition_axiom_dict,
        definition_ambiguous,
        definition_result,
        is_wrapped=False,
    )
    structured_input_dict = _restore_branch(
        structured_input_dict,
        structured_ambiguous,
        structured_result,
        is_wrapped=True,
    )

    save_stage_json(
        context.output_dir,
        "definition_axiom_dict_disambiguated.json",
        definition_axiom_dict,
        "Definition/axiom dict after disambiguation",
    )
    save_stage_json(
        context.output_dir,
        "structured_input_dict_disambiguated.json",
        structured_input_dict,
        "Structured input dict after disambiguation",
    )
    _write_branch_resolved(
        definition_run_dir,
        DEFINITION_BRANCH_STAGE,
        definition_tasks,
        definition_result,
        (definition_report or {}).get("attempt_rounds") or 1,
    )
    _write_branch_resolved(
        structured_run_dir,
        STRUCTURED_BRANCH_STAGE,
        structured_tasks,
        structured_result,
        (structured_report or {}).get("attempt_rounds") or 1,
    )

    state["definition_axiom_dict"] = definition_axiom_dict
    state["structured_input_dict"] = structured_input_dict
    return state, {
        **rerun_report,
        "stage": STAGE_NAME,
        "branch_stage": branch_stage,
        "status": "resolved",
        "canonical_updated": True,
    }


__all__ = [
    "build_disambiguation_tasks",
    "compile_ambiguity_symbols",
    "find_ambiguity_hits",
    "latest_unresolved_failure_report",
    "rerun_failed_tasks",
    "restore_definition_axiom_dict",
    "restore_structured_input_dict",
    "run_math_disambiguation_layer",
    "scan_node_dict_for_ambiguity",
]




