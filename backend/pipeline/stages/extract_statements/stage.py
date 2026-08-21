import copy
import re
from pathlib import Path

from ...common.io import read_json, save_stage_json, write_json
from ...common.llm_task import run_multiprocess_task
from ...common.node import (
    merge_node_with_source_envelope,
    normalize_node_type,
    normalize_node_types_in_tree,
)
from ...common.stage_recovery import (
    latest_unresolved_failure_report as _latest_unresolved_failure_report,
    rerun_unresolved_task_report,
    run_recoverable_task,
    write_failure_report,
)
from ...common.tex import build_tex_stage_outputs, is_tex_source_format, read_tex_source
from .templates import (
    correction_prompt03,
    data_template03,
    prompt_template03,
    tex_residual_correction_template,
    tex_residual_data_template,
    tex_residual_prompt_template,
    validation03,
    validation_tex_residual,
)


NAMED_TYPE = (
    r"Theorem|Lemma|Proposition|Corollary|Definition|Claim|Axiom|Property|"
    r"Example|Exercise|Remark|Conjecture|Problem|Observation|Fact|"
    r"定理|引理|命题|推论|定义|断言|公理|性质|例|练习|注|猜想|问题"
)
STAGE_NAME = "extract_statements"
SOURCE_LABEL_PATTERNS = (
    ("named", re.compile(
        rf"^\s*(?P<label>(?:{NAMED_TYPE})\s*"
        r"(?:[A-Z]|[IVXLCDM]+|[A-Za-z]?\d+(?:[.\-][A-Za-z0-9]+)*))"
        r"(?=\s|[.:：]|$)",
        re.IGNORECASE,
    )),
    ("parenthesized", re.compile(
        r"^\s*(?P<label>\((?=[A-Za-z0-9.\-]*\d)[A-Za-z0-9]+(?:[.\-][A-Za-z0-9]+)*\))"
        r"(?=\s|[.:：]|$)"
    )),
    ("dotted", re.compile(
        r"^\s*(?P<label>[A-Za-z0-9]+(?:[.\-][A-Za-z0-9]+)+)"
        r"(?=\s|[.:：]|$)"
    )),
    ("custom", re.compile(r"^\s*(?P<label>\\(?:tag|label)\{[^{}]+\}|[A-Z]\d+)(?=\s|[.:：]|$)")),
)
MARKDOWN_LOGICAL_HEADING_PATTERN = re.compile(
    rf"^\s*#{{1,6}}\s+"
    rf"(?P<type>{NAMED_TYPE})"
    r"\s*(?P<number>[A-Z]?\d+(?:[.\-][A-Za-z0-9]+)*|[IVXLCDM]+|[A-Z])?"
    r"\s*\.?\s*(?P<title>\([^)]*\)|[-–:：].*)?",
    re.IGNORECASE,
)
PROOF_INLINE_PATTERN = re.compile(r"\bProof(?:\s+of)?\s*[\.:]", re.IGNORECASE)


def _clean_raw_text(raw_text):
    if not isinstance(raw_text, str):
        return ""
    value = raw_text.strip()
    if value.startswith('r"""') and value.endswith('"""'):
        value = value[4:-3]
    return value.strip()


def _safe_problem_entry(problem_dict, key):
    if not isinstance(problem_dict, dict):
        return None
    for candidate in (key, str(key)):
        if candidate in problem_dict:
            return problem_dict[candidate]
    try:
        return problem_dict.get(int(str(key)))
    except (TypeError, ValueError):
        return None


def _problem_text(problem_dict, key):
    entry = _safe_problem_entry(problem_dict, key)
    if isinstance(entry, dict):
        return _clean_raw_text(entry.get("pos1", entry.get("text", "")))
    return _clean_raw_text(entry)


def recognize_source_label(raw_text):
    text = _clean_raw_text(raw_text)
    if not text:
        return {"label": "", "source": "none", "family": "", "evidence": []}
    heading_match = MARKDOWN_LOGICAL_HEADING_PATTERN.match(text)
    if heading_match:
        node_type = heading_match.group("type").strip()
        number = (heading_match.group("number") or "").strip().rstrip(".")
        label = f"{node_type} {number}".strip()
        return {
            "label": label,
            "node_type": node_type,
            "source": "markdown_heading_recognizer",
            "family": "markdown_heading",
            "evidence": ["source_block_markdown_heading"],
        }
    if text.startswith("#"):
        return {"label": "", "source": "none", "family": "", "evidence": []}
    for family, pattern in SOURCE_LABEL_PATTERNS:
        match = pattern.match(text)
        if match:
            return {
                "label": match.group("label"),
                "node_type": "",
                "source": "source_start_recognizer",
                "family": family,
                "evidence": ["source_block_initial"],
            }
    return {"label": "", "node_type": "", "source": "none", "family": "", "evidence": []}


def _label_at_source_start(label, source_text):
    label = str(label or "").strip()
    source_text = _clean_raw_text(source_text).lstrip()
    if not label:
        return False
    if source_text.startswith(label):
        remainder = source_text[len(label):len(label) + 1]
        return not remainder or remainder.isspace() or remainder in ".:："
    heading = recognize_source_label(source_text)
    if heading.get("source") == "markdown_heading_recognizer":
        return heading.get("label") == label
    return False


def build_source_label_facts(problem_dict, segment_blocks_report=None):
    report_labels = {}
    if isinstance(segment_blocks_report, dict):
        for block in segment_blocks_report.get("blocks", []):
            if not isinstance(block, dict):
                continue
            label = str(block.get("label_surface", "")).strip()
            if label:
                report_labels[str(block.get("block_id"))] = {
                    "label": label,
                    "node_type": str(block.get("logical_unit_type_hint", "")),
                    "source": "segment_blocks_report",
                    "family": str(block.get("label_family", "")),
                    "evidence": list(block.get("evidence", [])),
                }

    facts = {}
    for key in (problem_dict or {}):
        key_text = str(key)
        fact = report_labels.get(key_text)
        source_text = _problem_text(problem_dict, key)
        if fact is None or not _label_at_source_start(fact.get("label"), source_text):
            fact = recognize_source_label(source_text)
        facts[key_text] = fact
    return facts


def _node_payload(wrapper):
    if not isinstance(wrapper, dict):
        return None
    node = wrapper.get("pos1")
    return node if isinstance(node, dict) else None


def _normalize_for_anchor(text):
    return re.sub(r"\s+", "", text or "")


def _select_parent_node(group, source_text, trusted_label):
    if len(group) == 1:
        return group[0][0], "single_node"

    matching_label = [
        key for key, node in group if str(node.get("label", "")).strip() == trusted_label and trusted_label
    ]
    if len(matching_label) == 1:
        return matching_label[0], "unique_matching_label"

    source_without_label = source_text.lstrip()
    if trusted_label and source_without_label.startswith(trusted_label):
        source_without_label = source_without_label[len(trusted_label):].lstrip(" .:：\n")
    source_head = _normalize_for_anchor(source_without_label[:500])
    matching_content = []
    for key, node in group:
        content_head = _normalize_for_anchor(str(node.get("content", ""))[:120])
        if len(content_head) >= 16 and content_head[:80] in source_head:
            matching_content.append(key)
    if len(matching_content) == 1:
        return matching_content[0], "unique_content_anchor"
    return None, "ambiguous_parent"


def reconcile_labels_from_source(statement_dict, problem_dict, segment_blocks_report=None):
    reconciled = copy.deepcopy(statement_dict)
    facts = build_source_label_facts(problem_dict, segment_blocks_report)
    groups = {}
    for key, wrapper in reconciled.items():
        if not isinstance(wrapper, dict):
            continue
        node = _node_payload(wrapper)
        if node is None:
            continue
        groups.setdefault(str(wrapper.get("_orig_key")), []).append((key, node))

    block_reports = []
    filled_count = 0
    conflict_count = 0
    rejected_count = 0
    ambiguous_count = 0
    preserved_count = 0
    missing_trusted_count = 0
    heading_type_override_count = 0
    proof_assignment_risk_count = 0

    for source_key in (str(key) for key in (problem_dict or {})):
        fact = facts.get(source_key, {"label": "", "node_type": "", "source": "none", "family": "", "evidence": []})
        trusted_label = str(fact.get("label", "")).strip()
        trusted_node_type = str(fact.get("node_type", "")).strip()
        source_text = _problem_text(problem_dict, source_key)
        group = groups.get(source_key, [])
        parent_key, parent_reason = _select_parent_node(group, source_text, trusted_label)
        ambiguous = bool(trusted_label and group and parent_key is None)
        if ambiguous:
            ambiguous_count += 1

        node_reports = []
        for key, node in group:
            original_label = str(node.get("label", "")).strip()
            final_label = original_label
            action = "unchanged"
            is_parent = key == parent_key

            if parent_key is not None and is_parent:
                original_node_type = str(node.get("node_type", "")).strip()
                if trusted_node_type and original_node_type.lower() != trusted_node_type.lower():
                    node["node_type"] = trusted_node_type
                    heading_type_override_count += 1
                if trusted_label:
                    final_label = trusted_label
                    if not original_label:
                        action = "filled_from_source"
                        filled_count += 1
                    elif original_label != trusted_label:
                        action = "replaced_conflicting_label"
                        conflict_count += 1
                    else:
                        action = "preserved_verified_label"
                elif original_label:
                    if _label_at_source_start(original_label, source_text):
                        action = "preserved_source_initial_custom_label"
                    else:
                        final_label = ""
                        action = "cleared_unverified_label"
                        rejected_count += 1
            elif parent_key is not None and original_label:
                final_label = ""
                action = "cleared_non_parent_label"
                rejected_count += 1

            node["label"] = final_label
            content_text = str(node.get("content", "") or "")
            proof_text = str(node.get("proof", "") or "")
            proof_risk = bool(
                proof_text
                and not PROOF_INLINE_PATTERN.search(source_text)
                and len(proof_text) > max(300, len(content_text) * 2)
            )
            if proof_risk:
                proof_assignment_risk_count += 1
            node_reports.append(
                {
                    "node_index": str(key),
                    "is_parent": is_parent,
                    "llm_label": original_label,
                    "final_label": final_label,
                    "action": action,
                    "proof_assignment_risk": proof_risk,
                }
            )

        final_parent_label = ""
        if parent_key is not None:
            parent_node = next((node for key, node in group if key == parent_key), None)
            final_parent_label = str((parent_node or {}).get("label", "")).strip()
        if trusted_label:
            if final_parent_label == trusted_label:
                preserved_count += 1
            else:
                missing_trusted_count += 1

        block_reports.append(
            {
                "source_block_key": source_key,
                "trusted_label": trusted_label,
                "trusted_node_type": trusted_node_type,
                "label_source": fact.get("source", "none"),
                "label_family": fact.get("family", ""),
                "evidence": fact.get("evidence", []),
                "node_count": len(group),
                "parent_node_index": str(parent_key) if parent_key is not None else None,
                "parent_selection": parent_reason,
                "ambiguous_parent": ambiguous,
                "nodes": node_reports,
            }
        )

    labeled_source_count = sum(bool(str(fact.get("label", "")).strip()) for fact in facts.values())
    report = {
        "schema_version": 1,
        "source_block_count": len(problem_dict or {}),
        "numbered_source_block_count": labeled_source_count,
        "preserved_label_count": preserved_count,
        "label_preservation_rate": preserved_count / labeled_source_count if labeled_source_count else 1.0,
        "filled_label_count": filled_count,
        "label_conflict_count": conflict_count,
        "rejected_label_count": rejected_count,
        "ambiguous_parent_label_count": ambiguous_count,
        "missing_trusted_label_count": missing_trusted_count,
        "heading_type_override_count": heading_type_override_count,
        "proof_assignment_risk_count": proof_assignment_risk_count,
        "blocks": block_reports,
    }
    return reconciled, report


def repair_missing_labels_from_problem_dict(statement_dict, problem_dict):
    repaired, report = reconcile_labels_from_source(statement_dict, problem_dict)
    return repaired, report["filled_label_count"]


def extract_nonempty_blocks(input_dict):
    def clean_block_text(block):
        if not isinstance(block, dict):
            return block
        cleaned_block = copy.deepcopy(block)
        for field in ("content", "proof"):
            value = cleaned_block.get(field)
            if isinstance(value, str):
                cleaned_block[field] = value.replace("\\\\", "\\").replace("\n", "")
        return cleaned_block

    new_dict = {}
    new_id = 0

    def _safe_int_key(key):
        try:
            return 0, int(str(key))
        except (ValueError, TypeError):
            return 1, str(key)

    for key in sorted(input_dict.keys(), key=_safe_int_key):
        nested = input_dict[key]
        if not nested:
            continue
        if isinstance(nested, dict) and any(
            item in nested for item in ("node_type", "content", "proof", "label", "title")
        ):
            new_dict[new_id] = {"pos1": clean_block_text(nested), "_orig_key": key}
            new_id += 1
            continue
        if isinstance(nested, list):
            for block in nested:
                new_dict[new_id] = {"pos1": clean_block_text(block), "_orig_key": key}
                new_id += 1
            continue
        for _, block in sorted(nested.items(), key=lambda item: _safe_int_key(item[0])):
            new_dict[new_id] = {"pos1": clean_block_text(block), "_orig_key": key}
            new_id += 1
    return new_dict


def _run_extract_statement_tasks(context, index_dict, checkpoint_dir):
    return run_multiprocess_task(
        llm=context.llm,
        parse_method=context.parser.parse_dict,
        data_template=data_template03,
        prompt_template=prompt_template03,
        correction_template=correction_prompt03,
        validator=validation03,
        index_dict=index_dict,
        num_threads=context.num_threads,
        checkpoint=context.checkpoint,
        checkpoint_dir=checkpoint_dir,
        engine=getattr(context, "llm_engine", "api"),
        stage_name=STAGE_NAME,
        output_dir=context.output_dir,
        claude_command=getattr(context, "claude_command", "claude"),
        claude_model=getattr(context, "claude_model", None),
        claude_agent=getattr(context, "claude_agent", None),
        claude_batch_size=getattr(context, "claude_batch_size", 8),
        claude_timeout_seconds=getattr(context, "claude_timeout_seconds", 900),
        claude_max_retries=getattr(context, "claude_max_retries", 1),
    )


def _run_tex_residual_tasks(context, index_dict, checkpoint_dir):
    return run_multiprocess_task(
        llm=context.llm,
        parse_method=context.parser.parse_dict,
        data_template=tex_residual_data_template,
        prompt_template=tex_residual_prompt_template,
        correction_template=tex_residual_correction_template,
        validator=validation_tex_residual,
        index_dict=index_dict,
        num_threads=context.num_threads,
        checkpoint=context.checkpoint,
        checkpoint_dir=checkpoint_dir,
        engine=getattr(context, "llm_engine", "api"),
        stage_name=STAGE_NAME,
        output_dir=context.output_dir,
        claude_command=getattr(context, "claude_command", "claude"),
        claude_model=getattr(context, "claude_model", None),
        claude_agent=getattr(context, "claude_agent", None),
        claude_batch_size=getattr(context, "claude_batch_size", 8),
        claude_timeout_seconds=getattr(context, "claude_timeout_seconds", 900),
        claude_max_retries=getattr(context, "claude_max_retries", 1),
    )


def _raw_result_for_key(raw_results, key):
    for candidate in (key, str(key)):
        if isinstance(raw_results, dict) and candidate in raw_results:
            return raw_results[candidate]
    return None


def _canonical_residual_type(value):
    value = str(value or "").strip()
    if not value:
        return ""
    fact = recognize_source_label(f"# {value}")
    if fact.get("source") != "markdown_heading_recognizer":
        return ""
    return normalize_node_type(fact.get("node_type"))


SOURCE_ANCHOR_PUNCTUATION = str.maketrans(
    {
        "，": ",",
        "。": ".",
        "；": ";",
        "：": ":",
        "！": "!",
        "？": "?",
        "（": "(",
        "）": ")",
        "“": "'",
        "”": "'",
        "‘": "'",
        "’": "'",
        '"': "'",
    }
)


def _normalized_anchor_with_positions(value):
    normalized = []
    positions = []
    for index, char in enumerate(str(value or "")):
        if char.isspace():
            continue
        normalized.append(char.translate(SOURCE_ANCHOR_PUNCTUATION))
        positions.append(index)
    return "".join(normalized), positions


def _all_substring_positions(text, needle):
    positions = []
    cursor = 0
    while needle:
        position = text.find(needle, cursor)
        if position < 0:
            break
        positions.append(position)
        cursor = position + 1
    return positions


def _resolve_unique_source_quote(source_text, source_quote):
    exact_positions = _all_substring_positions(source_text, source_quote)
    if len(exact_positions) == 1:
        start = exact_positions[0]
        return source_text[start:start + len(source_quote)], start, start + len(source_quote), "exact"

    normalized_source, source_positions = _normalized_anchor_with_positions(source_text)
    normalized_quote, _ = _normalized_anchor_with_positions(source_quote)
    if not normalized_quote:
        return None
    normalized_positions = _all_substring_positions(normalized_source, normalized_quote)
    if len(normalized_positions) != 1:
        return None
    normalized_start = normalized_positions[0]
    normalized_end = normalized_start + len(normalized_quote)
    start = source_positions[normalized_start]
    end = source_positions[normalized_end - 1] + 1
    return source_text[start:end], start, end, "normalized_surface"


def _validate_tex_residual_result(task_key, payload, raw_result):
    if not isinstance(raw_result, dict):
        return None, {
            "status": "rejected_anchor",
            "reason": "invalid_result_shape",
            "returned_node_count": 0,
        }
    source_text = str((payload or {}).get("pos1") or "")
    source_span = (payload or {}).get("source_span")
    if not isinstance(source_span, dict):
        source_span = {}
    source_start = source_span.get("start")
    if not isinstance(source_start, int):
        return None, {
            "status": "rejected_anchor",
            "reason": "missing_source_span",
            "returned_node_count": len(raw_result),
        }

    accepted = []
    anchor_modes = {
        "exact": 0,
        "normalized_surface": 0,
    }
    for raw_node in raw_result.values():
        if not isinstance(raw_node, dict):
            return None, {
                "status": "rejected_anchor",
                "reason": "invalid_node_shape",
                "returned_node_count": len(raw_result),
            }
        node_type = _canonical_residual_type(raw_node.get("node_type"))
        source_quote = str(raw_node.get("source_quote") or "").strip()
        label = str(raw_node.get("label") or "").strip()
        if not node_type:
            return None, {
                "status": "rejected_anchor",
                "reason": "unsupported_node_type",
                "returned_node_count": len(raw_result),
            }
        resolved_quote = (
            _resolve_unique_source_quote(source_text, source_quote)
            if source_quote
            else None
        )
        if resolved_quote is None:
            return None, {
                "status": "rejected_anchor",
                "reason": "source_quote_not_unique_contiguous_substring",
                "returned_node_count": len(raw_result),
                "rejected_source_quote_preview": source_quote[:240],
            }
        anchored_quote, relative_start, relative_end, anchor_mode = resolved_quote
        if label:
            resolved_label = _resolve_unique_source_quote(anchored_quote, label)
            if resolved_label is None:
                return None, {
                    "status": "rejected_anchor",
                    "reason": "label_not_in_source_quote",
                    "returned_node_count": len(raw_result),
                    "rejected_label": label,
                    "rejected_source_quote_preview": anchored_quote[:240],
                }
            label = resolved_label[0]
        anchor_modes[anchor_mode] += 1
        accepted.append(
            {
                "node_type": node_type,
                "source_quote": anchored_quote,
                "label": label,
                "anchor_mode": anchor_mode,
                "source_span": {
                    "start": source_start + relative_start,
                    "end": source_start + relative_end,
                },
            }
        )

    return {
        "status": "completed",
        "source_block_key": str(task_key),
        "nodes": accepted,
    }, {
        "status": "accepted",
        "reason": "empty_valid_result" if not accepted else "all_quotes_uniquely_anchored",
        "returned_node_count": len(raw_result),
        "accepted_node_count": len(accepted),
        "exact_anchor_count": anchor_modes["exact"],
        "normalized_surface_anchor_count": anchor_modes["normalized_surface"],
    }


def _run_validated_tex_residual_tasks(
    context,
    index_dict,
    checkpoint_dir,
    diagnostics=None,
):
    raw_results = _run_tex_residual_tasks(context, index_dict, checkpoint_dir)
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    accepted = {}
    for key, payload in (index_dict or {}).items():
        key = str(key)
        raw_result = _raw_result_for_key(raw_results, key)
        if raw_result is None:
            diagnostics[key] = {
                "status": "failed",
                "reason": "no_model_result",
                "returned_node_count": 0,
            }
            continue
        envelope, diagnostic = _validate_tex_residual_result(
            key,
            payload,
            raw_result,
        )
        previous = diagnostics.get(key)
        attempt_history = (
            list(previous.get("attempts") or [])
            if isinstance(previous, dict)
            else []
        )
        if isinstance(previous, dict) and not attempt_history:
            attempt_history.append(
                {field: value for field, value in previous.items() if field != "attempts"}
            )
        attempt_history.append(dict(diagnostic))
        diagnostics[key] = {**diagnostic, "attempts": attempt_history}
        if envelope is not None:
            accepted[key] = envelope
    return accepted


def _tex_residual_input_dict(problem_dict):
    return {
        str(key): {
            "pos1": str(entry.get("pos1") or ""),
            "source_kind": "tex_residual",
            "source_span": copy.deepcopy(entry.get("source_span") or {}),
        }
        for key, entry in (problem_dict or {}).items()
        if isinstance(entry, dict) and entry.get("source_kind") == "tex_residual"
    }


def _surface_fingerprint(value):
    value = re.sub(r"\\t(?=\s|[$\\]|$)", "", str(value or ""))
    value = re.sub(r"\s+", "", value)
    return value.translate(str.maketrans({"，": ",", "。": ".", "；": ";", "：": ":"}))


def _merge_tex_statement_nodes(
    deterministic_statement_dict,
    residual_results,
    source_file,
):
    wrappers = [
        copy.deepcopy(wrapper)
        for wrapper in (deterministic_statement_dict or {}).values()
        if isinstance(wrapper, dict)
    ]
    seen = set()
    for wrapper in wrappers:
        node = _node_payload(wrapper) or {}
        span = node.get("source_span") if isinstance(node.get("source_span"), dict) else {}
        seen.add(
            (
                span.get("start"),
                span.get("end"),
                _surface_fingerprint(node.get("content")),
            )
        )

    duplicate_suppressed = 0
    for task_key, envelope in (residual_results or {}).items():
        if not isinstance(envelope, dict) or envelope.get("status") != "completed":
            continue
        for item in envelope.get("nodes") or []:
            if not isinstance(item, dict):
                continue
            span = item.get("source_span") if isinstance(item.get("source_span"), dict) else {}
            quote = str(item.get("source_quote") or "")
            fingerprint = (span.get("start"), span.get("end"), _surface_fingerprint(quote))
            if fingerprint in seen:
                duplicate_suppressed += 1
                continue
            seen.add(fingerprint)
            source_block_key = f"tex_residual:{span.get('start')}:{span.get('end')}"
            wrappers.append(
                {
                    "pos1": {
                        "node_type": item.get("node_type", ""),
                        "content": quote,
                        "proof": "",
                        "label": item.get("label", ""),
                        "source_span": copy.deepcopy(span),
                        "source_file": source_file,
                    },
                    "_orig_key": str(task_key),
                    "source_block_key": source_block_key,
                    "source_text": quote,
                }
            )

    def source_order(wrapper):
        node = _node_payload(wrapper) or {}
        span = node.get("source_span") if isinstance(node.get("source_span"), dict) else {}
        start = span.get("start")
        return start if isinstance(start, int) else 10**15

    wrappers.sort(key=lambda wrapper: (source_order(wrapper), str(wrapper.get("source_block_key", ""))))
    ordered = {}
    for index, wrapper in enumerate(wrappers):
        wrapper.setdefault("source_block_key", wrapper.get("_orig_key"))
        wrapper["_orig_key"] = index
        ordered[index] = wrapper
    normalize_node_types_in_tree(ordered)
    return ordered, duplicate_suppressed


def _empty_stage_run():
    return {
        "status": "resolved",
        "expected_task_count": 0,
        "succeeded_task_count": 0,
        "failed_task_count": 0,
        "expected_task_keys": [],
        "succeeded_task_keys": [],
        "failed_task_keys": [],
        "canonical_updated": False,
    }


def _task_summary(key, payload):
    text = _clean_raw_text(payload.get("pos1", "")) if isinstance(payload, dict) else _clean_raw_text(payload)
    return {"source_chars": len(text), "source_preview": text[:160]}


def latest_unresolved_failure_report(context):
    return _latest_unresolved_failure_report(context, STAGE_NAME)


def _seal_statement_nodes(statement_dict):
    sealed = {}
    for key, wrapper in (statement_dict or {}).items():
        if not isinstance(wrapper, dict):
            continue
        copied_wrapper = copy.deepcopy(wrapper)
        node = _node_payload(copied_wrapper)
        if node is None:
            continue
        source_metadata = {
            "source_text": copied_wrapper.get("source_text", ""),
            "source_block_key": copied_wrapper.get(
                "source_block_key",
                copied_wrapper.get("_orig_key"),
            ),
            "source_span": node.get("source_span"),
            "source_file": node.get("source_file"),
        }
        sealed_node, audit = merge_node_with_source_envelope(
            node,
            {},
            stage_name=STAGE_NAME,
            allowed_fields=(),
            seal=True,
            source_metadata=source_metadata,
        )
        sealed_node["_source_merge_audits"] = [audit]
        copied_wrapper["pos1"] = sealed_node
        sealed[key] = copied_wrapper
    return sealed


def _finalize_outputs(context, state, raw_statement_dict, *, run_dir=None, attempts=1):
    unsplit_statement_dict = extract_nonempty_blocks(raw_statement_dict)

    input_keys = [str(key) for key in (state.get("problem_dict") or {})]
    survived = {
        str(wrapper.get("_orig_key"))
        for wrapper in unsplit_statement_dict.values()
        if isinstance(wrapper, dict) and wrapper.get("_orig_key") is not None
    }
    dropped = [key for key in input_keys if key not in survived]
    if input_keys and dropped:
        print(
            f"Warning: extract_statements input blocks={len(input_keys)}, "
            f"successful={len(input_keys) - len(dropped)}, dropped={dropped}"
        )

    unsplit_statement_dict, report = reconcile_labels_from_source(
        unsplit_statement_dict,
        state.get("problem_dict") or {},
        state.get("segment_blocks_report"),
    )

    problem_dict = state.get("problem_dict") or {}
    for wrapper in unsplit_statement_dict.values():
        if not isinstance(wrapper, dict):
            continue
        wrapper["source_text"] = _problem_text(problem_dict, wrapper.get("_orig_key"))

    normalize_node_types_in_tree(unsplit_statement_dict)
    unsplit_statement_dict = _seal_statement_nodes(unsplit_statement_dict)
    save_stage_json(context.output_dir, "unsplit_statement_dict.json", unsplit_statement_dict, "Unsplit statement dict")
    save_stage_json(context.output_dir, "extract_statements_report.json", report, "Extract statements report")
    if run_dir is not None:
        write_failure_report(
            run_dir,
            run_dir.name,
            STAGE_NAME,
            input_keys,
            raw_statement_dict,
            attempts=attempts,
            canonical_updated=True,
        )
    state["unsplit_statement_dict"] = unsplit_statement_dict
    state["extract_statements_report"] = report
    return state


def _finalize_tex_outputs(
    context,
    state,
    deterministic_statement_dict,
    residual_results,
    *,
    diagnostics=None,
    run_dir=None,
    attempts=1,
):
    problem_dict = state.get("problem_dict") or {}
    tex_report = state.get("tex_extract_statements_report") or {}
    document_model = state.get("tex_document_model") or {}
    residual_input = _tex_residual_input_dict(problem_dict)
    residual_results = {
        str(key): value for key, value in (residual_results or {}).items()
    }
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    statement_dict, duplicate_suppressed = _merge_tex_statement_nodes(
        deterministic_statement_dict,
        residual_results,
        context.file_path,
    )
    statement_dict = _seal_statement_nodes(statement_dict)

    expected_keys = list(residual_input)
    completed_keys = [key for key in expected_keys if key in residual_results]
    failed_keys = [key for key in expected_keys if key not in residual_results]
    residual_node_count = sum(
        len((residual_results.get(key) or {}).get("nodes") or [])
        for key in completed_keys
    ) - duplicate_suppressed
    residual_node_count = max(0, residual_node_count)
    rejected_count = sum(
        attempt.get("status") == "rejected_anchor"
        for diagnostic in diagnostics.values()
        if isinstance(diagnostic, dict)
        for attempt in (
            diagnostic.get("attempts")
            if isinstance(diagnostic.get("attempts"), list)
            else [diagnostic]
        )
        if isinstance(attempt, dict)
    )
    normalized_anchor_count = sum(
        item.get("anchor_mode") == "normalized_surface"
        for envelope in residual_results.values()
        if isinstance(envelope, dict)
        for item in (envelope.get("nodes") or [])
        if isinstance(item, dict)
    )

    residual_blocks = []
    for block in tex_report.get("residual_blocks", []):
        if not isinstance(block, dict):
            continue
        block_id = str(block.get("block_id"))
        envelope = residual_results.get(block_id)
        node_count = len((envelope or {}).get("nodes") or []) if isinstance(envelope, dict) else 0
        diagnostic = diagnostics.get(block_id) if isinstance(diagnostics.get(block_id), dict) else {}
        residual_blocks.append(
            {
                **block,
                "status": (
                    "completed_with_nodes"
                    if node_count
                    else "completed_empty"
                    if block_id in residual_results
                    else "failed"
                ),
                "extracted_node_count": node_count,
                **(
                    {"failure_reason": diagnostic.get("reason")}
                    if diagnostic.get("status") in {"failed", "rejected_anchor"}
                    and diagnostic.get("reason")
                    else {}
                ),
            }
        )

    numbered_environment_count = sum(
        bool(block.get("numbered"))
        for block in tex_report.get("blocks", [])
        if isinstance(block, dict)
    )
    labeled_environment_count = sum(
        bool(block.get("numbered")) and bool(str(block.get("label", "")).strip())
        for block in tex_report.get("blocks", [])
        if isinstance(block, dict)
    )
    generated_label_count = int(tex_report.get("generated_counter_label_count") or 0)
    unresolved_label_count = int(tex_report.get("unresolved_numbered_label_count") or 0)
    report = {
        **tex_report,
        "schema_version": 2,
        "node_count": len(statement_dict),
        "environment_node_count": len(deterministic_statement_dict or {}),
        "residual_node_count": residual_node_count,
        "residual_task_count": len(expected_keys),
        "residual_completed_task_count": len(completed_keys),
        "residual_failed_task_count": len(failed_keys),
        "residual_anchor_rejected_count": rejected_count,
        "residual_normalized_surface_anchor_count": normalized_anchor_count,
        "residual_duplicate_suppressed_count": duplicate_suppressed,
        "residual_blocks": residual_blocks,
        "numbered_source_block_count": numbered_environment_count,
        "preserved_label_count": labeled_environment_count,
        "label_preservation_rate": (
            labeled_environment_count / numbered_environment_count
            if numbered_environment_count
            else 1.0
        ),
        "filled_label_count": generated_label_count,
        "label_conflict_count": 0,
        "rejected_label_count": 0,
        "ambiguous_parent_label_count": 0,
        "missing_trusted_label_count": unresolved_label_count,
    }

    failure_report = None
    if run_dir is not None:
        failure_report = write_failure_report(
            Path(run_dir),
            Path(run_dir).name,
            STAGE_NAME,
            expected_keys,
            residual_results,
            attempts=attempts,
            canonical_updated=bool(residual_node_count),
        )
        state["extract_statements_stage_run"] = failure_report
    else:
        state["extract_statements_stage_run"] = _empty_stage_run()

    save_stage_json(context.output_dir, "problem_dict.json", problem_dict, "Problem dict")
    save_stage_json(context.output_dir, "unsplit_statement_dict.json", statement_dict, "Unsplit statement dict")
    save_stage_json(context.output_dir, "extract_statements_report.json", report, "Extract statements report")
    save_stage_json(context.output_dir, "tex_extract_statements_report.json", report, "TeX extract statements report")
    save_stage_json(context.output_dir, "tex_document_model.json", document_model, "TeX document model")
    save_stage_json(
        context.output_dir,
        "tex_residual_input_dict.json",
        residual_input,
        "TeX residual input dict",
    )
    save_stage_json(
        context.output_dir,
        "tex_residual_validated_result.json",
        residual_results,
        "Validated TeX residual extraction result",
    )
    save_stage_json(
        context.output_dir,
        "tex_residual_extraction_diagnostics.json",
        diagnostics,
        "TeX residual extraction diagnostics",
    )
    if run_dir is not None:
        write_json(
            str(Path(run_dir) / "tex_residual_extraction_diagnostics.json"),
            diagnostics,
        )

    state["unsplit_statement_dict"] = statement_dict
    state["extract_statements_report"] = report
    state["tex_extract_statements_report"] = report
    state["tex_document_model"] = document_model
    return state, failure_report


def run(context, state):
    if is_tex_source_format(context):
        source_text = read_tex_source(context.file_path)
        problem_dict, deterministic_statement_dict, tex_report, document_model = build_tex_stage_outputs(
            source_text,
            source_file=context.file_path,
        )
        normalize_node_types_in_tree(deterministic_statement_dict)
        state["problem_dict"] = problem_dict
        state["tex_extract_statements_report"] = tex_report
        state["tex_document_model"] = document_model
        residual_input = _tex_residual_input_dict(problem_dict)
        if not residual_input:
            state, _ = _finalize_tex_outputs(
                context,
                state,
                deterministic_statement_dict,
                {},
            )
            return state

        diagnostics = {}
        residual_results, failure_report, run_dir = run_recoverable_task(
            context,
            stage_name=STAGE_NAME,
            input_dict=residual_input,
            task_runner=lambda index_dict, checkpoint_dir: _run_validated_tex_residual_tasks(
                context,
                index_dict,
                checkpoint_dir,
                diagnostics,
            ),
            task_summary=_task_summary,
        )
        write_json(
            str(Path(run_dir) / "tex_deterministic_statement_dict.json"),
            deterministic_statement_dict,
        )
        state, _ = _finalize_tex_outputs(
            context,
            state,
            deterministic_statement_dict,
            residual_results,
            diagnostics=diagnostics,
            run_dir=run_dir,
            attempts=failure_report.get("attempt_rounds") or 1,
        )
        return state

    raw_statement_dict, failure_report, run_dir = run_recoverable_task(
        context,
        stage_name=STAGE_NAME,
        input_dict=state["problem_dict"],
        task_runner=lambda index_dict, checkpoint_dir: _run_extract_statement_tasks(context, index_dict, checkpoint_dir),
        task_summary=_task_summary,
    )
    if failure_report.get("status") != "resolved":
        if failure_report.get("succeeded_task_count") == 0:
            raise RuntimeError(
                "extract_statements produced no valid task results; aborting to protect the canonical cache. "
                f"Failure report: {failure_report.get('run_dir')}"
            )
        state["extract_statements_stage_run"] = failure_report
        if getattr(context, "execution_mode", "pipeline") == "pipeline":
            return _finalize_outputs(context, state, raw_statement_dict, run_dir=run_dir, attempts=1)
        return state
    return _finalize_outputs(context, state, raw_statement_dict, run_dir=run_dir, attempts=1)


def rerun_failed_tasks(context, state, max_rounds=2):
    if is_tex_source_format(context):
        unresolved = latest_unresolved_failure_report(context)
        diagnostics_path = (
            Path(unresolved["run_dir"]) / "tex_residual_extraction_diagnostics.json"
            if isinstance(unresolved, dict) and unresolved.get("run_dir")
            else Path(context.output_dir) / "tex_residual_extraction_diagnostics.json"
        )
        diagnostics = (
            read_json(str(diagnostics_path))
            if diagnostics_path.exists()
            else {}
        )
        residual_results, failure_report, run_dir = rerun_unresolved_task_report(
            context,
            stage_name=STAGE_NAME,
            task_runner=lambda index_dict, checkpoint_dir: _run_validated_tex_residual_tasks(
                context,
                index_dict,
                checkpoint_dir,
                diagnostics,
            ),
            max_rounds=max_rounds,
        )
        base_path = Path(run_dir) / "tex_deterministic_statement_dict.json"
        deterministic_statement_dict = (
            read_json(str(base_path))
            if base_path.exists()
            else {
                key: copy.deepcopy(wrapper)
                for key, wrapper in (state.get("unsplit_statement_dict") or {}).items()
                if isinstance(wrapper, dict)
                and str(wrapper.get("source_block_key", "")).isdigit()
            }
        )
        state, final_report = _finalize_tex_outputs(
            context,
            state,
            deterministic_statement_dict,
            residual_results,
            diagnostics=diagnostics,
            run_dir=run_dir,
            attempts=failure_report.get("attempt_rounds") or 1,
        )
        if failure_report.get("status") != "resolved":
            return state, final_report or failure_report
        return state, {
            **(final_report or failure_report),
            "status": "resolved",
            "canonical_updated": bool(
                state.get("extract_statements_report", {}).get("residual_node_count")
            ),
        }

    raw_statement_dict, failure_report, run_dir = rerun_unresolved_task_report(
        context,
        stage_name=STAGE_NAME,
        task_runner=lambda index_dict, checkpoint_dir: _run_extract_statement_tasks(context, index_dict, checkpoint_dir),
        max_rounds=max_rounds,
    )
    if failure_report.get("status") != "resolved":
        state["extract_statements_stage_run"] = failure_report
        return state, failure_report
    state = _finalize_outputs(
        context,
        state,
        raw_statement_dict,
        run_dir=run_dir,
        attempts=failure_report.get("attempt_rounds") or 1,
    )
    return state, {
        **failure_report,
        "status": "resolved",
        "canonical_updated": True,
    }
