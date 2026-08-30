"""Audit TeX coverage and recover structurally identified Markdown statements."""

import copy
import re
import unicodedata
from pathlib import Path

from ...common.io import read_json, save_stage_json, write_json
from ...common.llm_task import run_multiprocess_task
from ...common.node import merge_node_with_source_envelope, normalize_node_type
from ...common.stage_recovery import (
    latest_unresolved_failure_report as _latest_unresolved_failure_report,
    rerun_unresolved_task_report,
    run_recoverable_task,
    write_failure_report,
)
from ...common.tex import is_tex_source_format
from ..extract_statements.stage import _resolve_unique_source_quote, recognize_source_label


STAGE_NAME = "ensure_coverage"
MARKDOWN_HEADING_RE = re.compile(r"(?m)^[ \t]*#{1,6}[ \t]+[^\r\n]*(?:\r?\n|$)")
NUMBER_TOKEN_RE = re.compile(r"\d+(?:[.\-][A-Za-z0-9]+)*")
LITERAL_TAB_ESCAPE_RE = re.compile(r"\\t(?=\s|[$\\]|$)")

COVERAGE_DATA_TEMPLATE = r"""{
  "content_quote": "exact source quote containing the target statement",
  "proof_quote": "exact source quote containing its proof, or an empty string"
}"""

TARGETED_PROMPT_TEMPLATE = r"""
You are recovering exactly one known mathematical logic unit from one exact source
span. Extract only the target unit described below. Do not return neighboring
theorems, definitions, examples, exercises, section headings, or referenced items.

Target node type: {target_type}
Target source label: {target_label}

Return one exact source quote for the target statement and, when present, one exact
source quote for its proof. Do not return node_type, label, title, content, proof,
global_id, or any other complete-node field. Quotes must be contiguous substrings
of the exact source span and must preserve the source language and LaTeX exactly.

Required schema:
{data_template}

Exact source span:
{pos1}
"""

TARGETED_CORRECTION_TEMPLATE = r"""
Your previous recovery was invalid. Return only content_quote and proof_quote.
Do not explain the error, repeat these instructions, or copy text from the schema.

Target node type: {target_type}
Target source label: {target_label}

Copy one non-empty content_quote exactly from the source span below. Include a
proof_quote only when the source span contains a proof; otherwise return an empty
string. Both non-empty quotes must be exact contiguous source substrings and must
preserve the source language and LaTeX exactly.

Required schema:
{data_template}

Invalid previous answer:
{answer}

Exact source span:
{pos1}
"""


def validation_coverage_quote(text):
    if not isinstance(text, dict):
        return False
    content_quote = text.get("content_quote")
    proof_quote = text.get("proof_quote", "")
    return (
        isinstance(content_quote, str)
        and bool(content_quote.strip())
        and isinstance(proof_quote, str)
    )

_TYPE_ALIASES = {
    "theorem": "theorem",
    "\u5b9a\u7406": "theorem",
    "lemma": "lemma",
    "\u5f15\u7406": "lemma",
    "proposition": "proposition",
    "\u547d\u9898": "proposition",
    "corollary": "corollary",
    "\u63a8\u8bba": "corollary",
    "definition": "definition",
    "\u5b9a\u4e49": "definition",
    "claim": "claim",
    "\u65ad\u8a00": "claim",
    "axiom": "axiom",
    "\u516c\u7406": "axiom",
    "property": "property",
    "\u6027\u8d28": "property",
    "example": "example",
    "\u4f8b": "example",
    "\u4f8b\u5b50": "example",
    "exercise": "exercise",
    "\u7ec3\u4e60": "exercise",
    "remark": "remark",
    "\u6ce8": "remark",
    "\u6ce8\u8bb0": "remark",
    "conjecture": "conjecture",
    "\u731c\u60f3": "conjecture",
    "problem": "problem",
    "\u95ee\u9898": "problem",
    "observation": "observation",
    "fact": "fact",
}

_PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "\uff0c": ",",
        "\u3002": ".",
        "\uff1b": ";",
        "\uff1a": ":",
        "\uff01": "!",
        "\uff1f": "?",
        "\uff08": "(",
        "\uff09": ")",
        "\u3010": "[",
        "\u3011": "]",
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
    }
)


def _text(value):
    return value if isinstance(value, str) else ""


def _problem_text(problem_dict, key):
    if not isinstance(problem_dict, dict):
        return ""
    for candidate in (key, str(key)):
        entry = problem_dict.get(candidate)
        if isinstance(entry, dict):
            return _text(entry.get("pos1") or entry.get("text"))
        if isinstance(entry, str):
            return entry
    try:
        entry = problem_dict.get(int(str(key)))
    except (TypeError, ValueError):
        entry = None
    return _text(entry.get("pos1")) if isinstance(entry, dict) else _text(entry)


def _node_payload(wrapper):
    if not isinstance(wrapper, dict):
        return None
    payload = wrapper.get("pos1")
    return payload if isinstance(payload, dict) else None


def _canonical_type(value):
    normalized = unicodedata.normalize("NFKC", _text(value)).strip().lower()
    return _TYPE_ALIASES.get(normalized, "")


def _trusted_logical_type(value):
    value = _text(value).strip()
    if not value:
        return ""
    fact = recognize_source_label(f"# {value}")
    if fact.get("source") != "markdown_heading_recognizer":
        return ""
    return _text(fact.get("node_type")).strip()


def _normalize_label(value):
    value = unicodedata.normalize("NFKC", _text(value)).strip().lower()
    value = re.sub(r"^\s*#{1,6}\s*", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.rstrip(" .:;,\uff1a\uff1b\uff0c\u3002")


def _label_identity(label, node_type=""):
    label = _normalize_label(label)
    canonical_type = _canonical_type(node_type)
    number_match = NUMBER_TOKEN_RE.search(label)
    if not canonical_type and number_match:
        canonical_type = _canonical_type(label[:number_match.start()].strip())
    if number_match:
        number = number_match.group(0).replace("-", ".").lower()
        return f"{canonical_type}:{number}" if canonical_type else f"label:{label}"
    return f"{canonical_type}:unnumbered" if canonical_type else f"label:{label}"


def _surface_fingerprint(value, *, strip_heading=True):
    value = unicodedata.normalize("NFKC", _text(value))
    value = value.replace("\\n\\n", "\n\n")
    value = LITERAL_TAB_ESCAPE_RE.sub("", value)
    value = value.translate(_PUNCTUATION_TRANSLATION)
    if strip_heading:
        value = MARKDOWN_HEADING_RE.sub("", value, count=1)
    return re.sub(r"\s+", "", value)


def _find_source_span(corrected_text, source_text, search_start=0):
    if not source_text:
        return -1, -1
    start = corrected_text.find(source_text, max(0, search_start))
    if start < 0:
        start = corrected_text.find(source_text)
    if start < 0:
        stripped = source_text.strip()
        start = corrected_text.find(stripped, max(0, search_start)) if stripped else -1
        if start < 0 and stripped:
            start = corrected_text.find(stripped)
        if start >= 0:
            return start, start + len(stripped)
    return (start, start + len(source_text)) if start >= 0 else (-1, -1)


def _heading_fact(heading_text):
    fact = recognize_source_label(heading_text)
    if fact.get("source") != "markdown_heading_recognizer":
        return None
    node_type = _trusted_logical_type(fact.get("node_type"))
    if not node_type:
        return None
    return {
        "target_label": _text(fact.get("label")).strip(),
        "target_type": node_type,
    }


def _segment_candidates(corrected_text, problem_dict, segment_report):
    candidates = []
    search_cursor = 0
    for block in (segment_report or {}).get("blocks", []):
        if not isinstance(block, dict):
            continue
        block_id = block.get("block_id")
        target_type = _trusted_logical_type(block.get("logical_unit_type_hint"))
        if (
            block_id is None
            or block.get("boundary_role") != "top_level_logical_unit_start"
            or not target_type
        ):
            continue
        source_text = _problem_text(problem_dict, block_id)
        if not source_text:
            continue
        source_start, source_end = _find_source_span(corrected_text, source_text, search_cursor)
        if source_start >= 0:
            search_cursor = source_end
        source_fact = _heading_fact(source_text)
        target_label = (
            source_fact["target_label"]
            if source_fact
            else _text(block.get("label_surface")).strip()
        )
        if source_fact:
            target_type = source_fact["target_type"]
        candidates.append(
            {
                "candidate_id": f"block:{block_id}",
                "origin": "segment_block",
                "block_id": block_id,
                "source_text": source_text,
                "source_start": source_start,
                "source_end": source_end,
                "heading_start": source_start if source_fact else None,
                "target_label": target_label,
                "target_type": target_type,
            }
        )
    return candidates


def _heading_aligns_with_segment(heading, segment_candidate):
    heading_start = heading["source_start"]
    segment_start = segment_candidate.get("source_start", -1)
    segment_end = segment_candidate.get("source_end", -1)
    same_type = (
        _canonical_type(heading["target_type"])
        == _canonical_type(segment_candidate["target_type"])
    )
    same_label = (
        _label_identity(heading["target_label"], heading["target_type"])
        == _label_identity(
            segment_candidate["target_label"],
            segment_candidate["target_type"],
        )
    )
    if segment_start >= 0 and segment_end >= segment_start:
        return segment_start <= heading_start < segment_end and same_type and same_label
    return (
        same_type
        and same_label
        and _surface_fingerprint(segment_candidate.get("source_text"), strip_heading=False).startswith(
            _surface_fingerprint(heading.get("heading_text"), strip_heading=False)
        )
    )


def build_structural_candidates(corrected_text, problem_dict, segment_report):
    """Build only segment-confirmed blocks and explicit Markdown heading candidates."""
    corrected_text = _text(corrected_text)
    candidates = _segment_candidates(corrected_text, problem_dict, segment_report)
    headings = list(MARKDOWN_HEADING_RE.finditer(corrected_text))

    for index, match in enumerate(headings):
        fact = _heading_fact(match.group(0))
        if fact is None:
            continue
        source_end = headings[index + 1].start() if index + 1 < len(headings) else len(corrected_text)
        heading = {
            "candidate_id": f"heading:{match.start()}",
            "origin": "markdown_heading",
            "block_id": None,
            "source_text": corrected_text[match.start():source_end],
            "source_start": match.start(),
            "source_end": source_end,
            "heading_start": match.start(),
            "heading_text": match.group(0).rstrip("\r\n"),
            **fact,
        }
        if any(_heading_aligns_with_segment(heading, candidate) for candidate in candidates):
            continue
        candidates.append(heading)

    candidates.sort(
        key=lambda item: (
            item["source_start"] if item.get("source_start", -1) >= 0 else 10**15,
            item["candidate_id"],
        )
    )
    return candidates


def build_numbered_candidates(corrected_text, problem_dict, segment_report):
    """Backward-compatible alias for the structural candidate builder."""
    return build_structural_candidates(corrected_text, problem_dict, segment_report)


def _wrapper_heading_keys(wrapper, corrected_text):
    node = _node_payload(wrapper) or {}
    node_label = _label_identity(node.get("label"), node.get("node_type"))
    source_text = _text((wrapper or {}).get("source_text"))
    if not source_text or not node_label:
        return set()
    source_start, _ = _find_source_span(corrected_text, source_text)
    if source_start < 0:
        return set()
    keys = set()
    for match in MARKDOWN_HEADING_RE.finditer(source_text):
        fact = _heading_fact(match.group(0))
        if fact is None:
            continue
        fact_label = _label_identity(fact["target_label"], fact["target_type"])
        if fact_label == node_label:
            keys.add((source_start + match.start(), fact_label))
    return keys


def find_missing_candidates(candidates, statement_dict, corrected_text=""):
    covered_origins = set()
    covered_heading_keys = set()
    for wrapper in (statement_dict or {}).values():
        if not isinstance(wrapper, dict):
            continue
        source_block_key = wrapper.get("source_block_key", wrapper.get("_orig_key"))
        if source_block_key is not None:
            covered_origins.add(str(source_block_key))
        covered_heading_keys.update(_wrapper_heading_keys(wrapper, corrected_text))

    missing = []
    covered = []
    for candidate in candidates:
        block_covered = (
            candidate.get("origin") == "segment_block"
            and str(candidate.get("block_id")) in covered_origins
        )
        heading_key = (
            candidate.get("heading_start"),
            _label_identity(candidate.get("target_label"), candidate.get("target_type")),
        )
        heading_covered = (
            candidate.get("origin") == "markdown_heading"
            and heading_key in covered_heading_keys
        )
        (covered if block_covered or heading_covered else missing).append(candidate)
    return missing, covered


def _candidate_input(candidate):
    return {
        "pos1": candidate["source_text"],
        "target_type": candidate["target_type"],
        "target_label": candidate["target_label"],
        "_candidate_id": candidate["candidate_id"],
        "_origin": candidate["origin"],
        "_block_id": candidate.get("block_id"),
        "_source_start": candidate.get("source_start"),
        "_source_end": candidate.get("source_end"),
    }


def _candidate_from_input(key, payload):
    payload = payload if isinstance(payload, dict) else {}
    return {
        "candidate_id": _text(payload.get("_candidate_id")) or str(key),
        "origin": _text(payload.get("_origin")),
        "block_id": payload.get("_block_id"),
        "source_text": _text(payload.get("pos1")),
        "source_start": payload.get("_source_start", -1),
        "source_end": payload.get("_source_end", -1),
        "target_label": _text(payload.get("target_label")),
        "target_type": _text(payload.get("target_type")),
    }


def _raw_result_for_key(raw_results, key):
    for candidate in (key, str(key)):
        if isinstance(raw_results, dict) and candidate in raw_results:
            return raw_results[candidate]
    return None


def _unwrap_quote_result(raw_result):
    if validation_coverage_quote(raw_result):
        return raw_result
    if not isinstance(raw_result, dict):
        return None
    matches = [
        value
        for value in raw_result.values()
        if validation_coverage_quote(value)
    ]
    return matches[0] if len(matches) == 1 else None


def _validate_target_result(candidate, raw_result):
    quote_result = _unwrap_quote_result(raw_result)
    if quote_result is None:
        return None, {
            "status": "rejected_ambiguous",
            "reason": "invalid_quote_result_shape",
            "returned_node_count": 0,
        }

    source_text = _text(candidate.get("source_text"))
    resolved_content = _resolve_unique_source_quote(
        source_text,
        _text(quote_result.get("content_quote")).strip(),
    )
    if resolved_content is None:
        return None, {
            "status": "rejected_ambiguous",
            "reason": "content_quote_not_unique_contiguous_substring",
            "returned_node_count": 1,
        }

    proof_quote = _text(quote_result.get("proof_quote")).strip()
    resolved_proof = (
        _resolve_unique_source_quote(source_text, proof_quote)
        if proof_quote
        else None
    )
    if proof_quote and resolved_proof is None:
        return None, {
            "status": "rejected_ambiguous",
            "reason": "proof_quote_not_unique_contiguous_substring",
            "returned_node_count": 1,
        }

    content, relative_start, relative_end, anchor_mode = resolved_content
    source_start = candidate.get("source_start")
    absolute_span = (
        {
            "start": source_start + relative_start,
            "end": source_start + relative_end,
        }
        if isinstance(source_start, int) and source_start >= 0
        else {}
    )
    node = {
        "node_type": normalize_node_type(candidate.get("target_type")),
        "content": content,
        "proof": resolved_proof[0] if resolved_proof is not None else "",
        "label": _text(candidate.get("target_label")).strip(),
        **({"source_span": absolute_span} if absolute_span else {}),
    }
    source_block_key = (
        candidate.get("block_id")
        if candidate.get("origin") == "segment_block"
        else candidate.get("candidate_id")
    )
    node, audit = merge_node_with_source_envelope(
        node,
        {},
        stage_name=STAGE_NAME,
        allowed_fields=(),
        seal=True,
        source_metadata={
            "source_text": source_text,
            "source_block_key": source_block_key,
            "source_span": absolute_span,
        },
    )
    ignored_fields = sorted(
        str(field_name)
        for field_name in quote_result
        if field_name not in {"content_quote", "proof_quote"}
    )
    if ignored_fields:
        audit["ignored_fields"] = ignored_fields
    node["_source_merge_audits"] = [audit]
    return node, {
        "status": "accepted",
        "reason": "quotes_uniquely_anchored",
        "returned_node_count": 1,
        "content_anchor_mode": anchor_mode,
        "ignored_fields": ignored_fields,
    }


def _run_extract_tasks(context, index_dict, checkpoint_dir):
    return run_multiprocess_task(
        llm=context.llm,
        parse_method=context.parser.parse_dict,
        data_template=COVERAGE_DATA_TEMPLATE,
        prompt_template=TARGETED_PROMPT_TEMPLATE,
        correction_template=TARGETED_CORRECTION_TEMPLATE,
        validator=validation_coverage_quote,
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


def _run_validated_extract_tasks(context, index_dict, checkpoint_dir, diagnostics=None):
    raw_results = _run_extract_tasks(context, index_dict, checkpoint_dir)
    accepted = {}
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
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
        candidate = _candidate_from_input(key, payload)
        node, diagnostic = _validate_target_result(candidate, raw_result)
        diagnostics[key] = diagnostic
        if node is not None:
            accepted[key] = node
    return accepted


def _source_only_recovery(candidate, diagnostic):
    source_start = candidate.get("source_start")
    source_end = candidate.get("source_end")
    if (
        not isinstance(source_start, int)
        or source_start < 0
        or not isinstance(source_end, int)
        or source_end <= source_start
    ):
        raise RuntimeError(
            "ensure_coverage cannot preserve a failed recovery candidate without "
            f"a trusted source span: {candidate.get('candidate_id')}"
        )

    source_text = _text(candidate.get("source_text"))
    node = {
        "node_type": normalize_node_type(candidate.get("target_type")),
        "content": source_text,
        "proof": "",
        "label": _text(candidate.get("target_label")).strip(),
        "source_span": {"start": source_start, "end": source_end},
        "_derivation_status": {
            STAGE_NAME: {
                "status": "degraded",
                "reason": _text((diagnostic or {}).get("reason")) or "unresolved_model_task",
                "task_key": str(candidate.get("candidate_id")),
            }
        },
    }
    source_block_key = (
        candidate.get("block_id")
        if candidate.get("origin") == "segment_block"
        else candidate.get("candidate_id")
    )
    node, audit = merge_node_with_source_envelope(
        node,
        {},
        stage_name=STAGE_NAME,
        allowed_fields=(),
        seal=True,
        source_metadata={
            "source_text": source_text,
            "source_span": {"start": source_start, "end": source_end},
            "source_block_key": source_block_key,
        },
    )
    node["_source_merge_audits"] = [audit]
    return node


def _recovery_wrappers(raw_results, missing, diagnostics):
    wrappers = {}
    source_only_ids = set()
    for candidate in missing:
        candidate_id = str(candidate["candidate_id"])
        node = _raw_result_for_key(raw_results, candidate_id)
        if not isinstance(node, dict):
            node = _source_only_recovery(candidate, diagnostics.get(candidate_id))
            source_only_ids.add(candidate_id)
        wrappers[candidate_id] = {
            "pos1": copy.deepcopy(node),
            "_orig_key": candidate_id,
        }
    return wrappers, source_only_ids


def _wrapper_source_span(wrapper, corrected_text):
    if isinstance(wrapper, dict) and isinstance(wrapper.get("_coverage_source_start"), int):
        start = wrapper["_coverage_source_start"]
        return start, start + len(_text(wrapper.get("source_text")))
    return _find_source_span(corrected_text, _text((wrapper or {}).get("source_text")))


def _source_position(wrapper, corrected_text, fallback):
    start, _ = _wrapper_source_span(wrapper, corrected_text)
    return start if start >= 0 else 10**15 + fallback


def _same_heading_and_position(wrapper, candidate, corrected_text):
    candidate_heading = candidate.get("heading_start")
    if not isinstance(candidate_heading, int) or candidate_heading < 0:
        return False
    target_identity = _label_identity(
        candidate.get("target_label"),
        candidate.get("target_type"),
    )
    return (candidate_heading, target_identity) in _wrapper_heading_keys(wrapper, corrected_text)


def _same_source_fingerprint(wrapper, recovered_node, candidate, corrected_text):
    existing_node = _node_payload(wrapper)
    if existing_node is None:
        return False
    existing = _surface_fingerprint(existing_node.get("content"))
    recovered = _surface_fingerprint(recovered_node.get("content"))
    if not existing or existing != recovered:
        return False
    existing_start, existing_end = _wrapper_source_span(wrapper, corrected_text)
    candidate_start = candidate.get("source_start", -1)
    candidate_end = candidate.get("source_end", -1)
    return (
        existing_start >= 0
        and candidate_start >= 0
        and existing_start < candidate_end
        and candidate_start < existing_end
    )


def _is_duplicate_recovery(existing_wrappers, recovered_node, candidate, corrected_text):
    candidate_block_key = (
        str(candidate.get("block_id"))
        if candidate.get("origin") == "segment_block"
        else None
    )
    for wrapper in existing_wrappers:
        source_block_key = wrapper.get("source_block_key", wrapper.get("_orig_key"))
        if candidate_block_key is not None and str(source_block_key) == candidate_block_key:
            return True
        if _same_heading_and_position(wrapper, candidate, corrected_text):
            return True
        if _same_source_fingerprint(wrapper, recovered_node, candidate, corrected_text):
            return True
    return False


def merge_recovered_statements(
    statement_dict,
    recovered_dict,
    candidates,
    corrected_text,
    *,
    source_only_ids=None,
):
    candidate_by_id = {candidate["candidate_id"]: candidate for candidate in candidates}
    source_only_ids = {str(key) for key in (source_only_ids or ())}
    merged = [
        copy.deepcopy(wrapper)
        for wrapper in (statement_dict or {}).values()
        if isinstance(wrapper, dict)
    ]
    recovered_candidate_ids = set()
    duplicate_suppressed_ids = set()
    processed_candidate_ids = set()

    for wrapper in (recovered_dict or {}).values():
        if not isinstance(wrapper, dict):
            continue
        candidate_id = str(wrapper.get("_orig_key"))
        candidate = candidate_by_id.get(candidate_id)
        node = _node_payload(wrapper)
        if (
            candidate is None
            or node is None
            or candidate_id in processed_candidate_ids
        ):
            continue
        processed_candidate_ids.add(candidate_id)
        if _is_duplicate_recovery(merged, node, candidate, corrected_text):
            duplicate_suppressed_ids.add(candidate_id)
            continue

        node["coverage_recovered"] = True
        if candidate_id in source_only_ids:
            node["coverage_source_only"] = True
        wrapper["coverage_candidate_id"] = candidate_id
        wrapper["source_block_key"] = (
            candidate["block_id"]
            if candidate.get("origin") == "segment_block"
            else candidate_id
        )
        wrapper["source_text"] = candidate["source_text"]
        wrapper["_coverage_source_start"] = candidate.get("source_start", -1)
        recovered_candidate_ids.add(candidate_id)
        merged.append(copy.deepcopy(wrapper))

    if not recovered_candidate_ids:
        return (
            copy.deepcopy(statement_dict or {}),
            recovered_candidate_ids,
            duplicate_suppressed_ids,
        )

    decorated = [
        (_source_position(wrapper, corrected_text, index), index, wrapper)
        for index, wrapper in enumerate(merged)
    ]
    decorated.sort(key=lambda item: (item[0], item[1]))
    ordered = {}
    for new_key, (_, _, wrapper) in enumerate(decorated):
        wrapper.setdefault("source_block_key", wrapper.get("_orig_key"))
        wrapper["_orig_key"] = new_key
        wrapper.pop("_coverage_source_start", None)
        ordered[new_key] = wrapper
    return ordered, recovered_candidate_ids, duplicate_suppressed_ids


def _candidate_summary(candidate, status, diagnostic=None):
    diagnostic = diagnostic if isinstance(diagnostic, dict) else {}
    return {
        "candidate_id": candidate["candidate_id"],
        "origin": candidate["origin"],
        "block_id": candidate.get("block_id"),
        "source_start": candidate.get("source_start"),
        "source_end": candidate.get("source_end"),
        "target_type": candidate.get("target_type", ""),
        "target_label": candidate.get("target_label", ""),
        "status": status,
        **({"reason": diagnostic.get("reason")} if diagnostic.get("reason") else {}),
    }


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


def _base_report(mode, candidates, covered, missing):
    return {
        "schema_version": 2,
        "mode": mode,
        "candidate_count": len(candidates),
        "covered_count": len(covered),
        "missing_count": len(missing),
        "attempted_count": 0,
        "recovered_candidate_count": 0,
        "recovered_node_count": 0,
        "source_only_node_count": 0,
        "duplicate_suppressed_count": 0,
        "rejected_ambiguous_count": 0,
        "failed_count": 0,
        "candidates": [],
    }


def _save_outputs(context, state, statement_dict, report):
    state["unsplit_statement_dict"] = statement_dict
    state["ensure_coverage_report"] = report
    save_stage_json(
        context.output_dir,
        "unsplit_statement_dict_after_coverage.json",
        statement_dict,
        "Statements after coverage",
    )
    save_stage_json(
        context.output_dir,
        "ensure_coverage_report.json",
        report,
        "Ensure coverage report",
    )


def _tex_audit_candidates(problem_dict, tex_report):
    candidates = []
    for block in (tex_report or {}).get("blocks", []):
        if not isinstance(block, dict):
            continue
        key = block.get("source_block_key")
        if key is None:
            continue
        source_text = _problem_text(problem_dict, key)
        source_span = block.get("source_span") if isinstance(block.get("source_span"), dict) else {}
        candidates.append(
            {
                "candidate_id": f"block:{key}",
                "origin": "tex_block",
                "block_id": key,
                "source_text": source_text,
                "source_start": source_span.get("start", -1),
                "source_end": source_span.get("end", -1),
                "target_label": _text(block.get("label")),
                "target_type": _text(block.get("node_type")),
            }
        )
    return candidates


def _run_tex_audit(context, state):
    statement_dict = state.get("unsplit_statement_dict") or {}
    candidates = _tex_audit_candidates(
        state.get("problem_dict") or {},
        state.get("tex_extract_statements_report")
        or state.get("extract_statements_report")
        or {},
    )
    covered_origins = {
        str(wrapper.get("source_block_key", wrapper.get("_orig_key")))
        for wrapper in statement_dict.values()
        if isinstance(wrapper, dict)
        and wrapper.get("source_block_key", wrapper.get("_orig_key")) is not None
    }
    covered = [
        candidate
        for candidate in candidates
        if str(candidate["block_id"]) in covered_origins
    ]
    missing = [
        candidate
        for candidate in candidates
        if str(candidate["block_id"]) not in covered_origins
    ]
    tex_report = (
        state.get("tex_extract_statements_report")
        or state.get("extract_statements_report")
        or {}
    )
    residual_candidates = []
    residual_failed = []
    residual_completed = []
    for block in tex_report.get("residual_blocks", []):
        if not isinstance(block, dict):
            continue
        candidate = {
            "candidate_id": str(block.get("block_id")),
            "origin": "tex_residual",
            "block_id": block.get("block_id"),
            "source_start": (block.get("source_span") or {}).get("start"),
            "source_end": (block.get("source_span") or {}).get("end"),
            "target_type": "",
            "target_label": "",
            "status": block.get("status", "failed"),
            "extracted_node_count": int(block.get("extracted_node_count") or 0),
        }
        residual_candidates.append(candidate)
        if candidate["status"] in {"completed_empty", "completed_with_nodes"}:
            residual_completed.append(candidate)
        else:
            residual_failed.append(candidate)

    all_candidates = [*candidates, *residual_candidates]
    report = _base_report(
        "tex_hybrid_audit",
        all_candidates,
        [*covered, *residual_completed],
        [*missing, *residual_failed],
    )
    report.update(
        {
            "environment_candidate_count": len(candidates),
            "environment_covered_count": len(covered),
            "environment_missing_count": len(missing),
            "residual_task_count": len(residual_candidates),
            "residual_completed_task_count": len(residual_completed),
            "residual_failed_task_count": len(residual_failed),
            "residual_node_count": int(tex_report.get("residual_node_count") or 0),
            "residual_anchor_rejected_count": int(
                tex_report.get("residual_anchor_rejected_count") or 0
            ),
            "residual_normalized_surface_anchor_count": int(
                tex_report.get("residual_normalized_surface_anchor_count") or 0
            ),
            "duplicate_suppressed_count": int(
                tex_report.get("residual_duplicate_suppressed_count") or 0
            ),
            "failed_count": len(missing) + len(residual_failed),
        }
    )
    report["candidates"] = [
        _candidate_summary(
            candidate,
            "covered" if candidate in covered else "missing_deterministic_tex_block",
        )
        for candidate in candidates
    ]
    report["candidates"].extend(
        {
            "candidate_id": candidate["candidate_id"],
            "origin": candidate["origin"],
            "block_id": candidate["block_id"],
            "source_start": candidate["source_start"],
            "source_end": candidate["source_end"],
            "target_type": "",
            "target_label": "",
            "status": candidate["status"],
            "extracted_node_count": candidate["extracted_node_count"],
        }
        for candidate in residual_candidates
    )
    state["ensure_coverage_stage_run"] = _empty_stage_run()
    save_stage_json(context.output_dir, "coverage_candidates.json", all_candidates, "Coverage candidates")
    save_stage_json(context.output_dir, "coverage_missing_input_dict.json", {}, "Missing coverage inputs")
    _save_outputs(context, state, statement_dict, report)
    if missing:
        missing_keys = [str(candidate["block_id"]) for candidate in missing]
        raise RuntimeError(
            "ensure_coverage TeX audit found deterministic source block(s) missing "
            f"from extract_statements: {missing_keys}. Fix or rerun extract_statements; "
            "probabilistic coverage recovery is disabled for TeX."
        )
    if residual_failed:
        failed_keys = [candidate["candidate_id"] for candidate in residual_failed]
        raise RuntimeError(
            "ensure_coverage TeX hybrid audit found residual extraction task(s) "
            f"without a completed validated result: {failed_keys}. Rerun or repair "
            "extract_statements; ensure_coverage does not perform a third extraction pass."
        )
    return state


def latest_unresolved_failure_report(context):
    return _latest_unresolved_failure_report(context, STAGE_NAME)


def _finalize_outputs(
    context,
    state,
    base_statement_dict,
    raw_results,
    *,
    diagnostics=None,
    run_dir=None,
    attempts=1,
):
    corrected_text = state["corrected_text"]
    candidates = build_structural_candidates(
        corrected_text,
        state.get("problem_dict") or {},
        state.get("segment_blocks_report") or {},
    )
    missing, covered = find_missing_candidates(candidates, base_statement_dict, corrected_text)
    missing_input = {
        candidate["candidate_id"]: _candidate_input(candidate)
        for candidate in missing
        if candidate.get("source_text")
    }
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    recovered, source_only_ids = _recovery_wrappers(
        raw_results or {},
        missing,
        diagnostics,
    )
    ordered, recovered_ids, duplicate_ids = merge_recovered_statements(
        base_statement_dict,
        recovered,
        missing,
        corrected_text,
        source_only_ids=source_only_ids,
    )
    source_only_recovered_ids = recovered_ids & source_only_ids
    model_recovered_ids = recovered_ids - source_only_ids
    rejected_ids = {
        candidate["candidate_id"]
        for candidate in missing
        if candidate["candidate_id"] in source_only_recovered_ids
        and candidate["candidate_id"] not in duplicate_ids
        and diagnostics.get(candidate["candidate_id"], {}).get("status") == "rejected_ambiguous"
    }
    failed_ids = {
        candidate["candidate_id"]
        for candidate in missing
        if candidate["candidate_id"] in source_only_recovered_ids
        and candidate["candidate_id"] not in duplicate_ids
        and candidate["candidate_id"] not in rejected_ids
    }
    report = _base_report("markdown_structural_recovery", candidates, covered, missing)
    report.update(
        {
            "attempted_count": len(missing_input),
            "recovered_candidate_count": len(model_recovered_ids),
            "recovered_node_count": len(model_recovered_ids),
            "source_only_node_count": len(source_only_recovered_ids),
            "duplicate_suppressed_count": len(duplicate_ids),
            "rejected_ambiguous_count": len(rejected_ids),
            "failed_count": len(failed_ids),
            "candidates": [
                _candidate_summary(
                    candidate,
                    (
                        "covered"
                        if candidate in covered
                        else "recovered"
                        if candidate["candidate_id"] in model_recovered_ids
                        else "source_only_degraded"
                        if candidate["candidate_id"] in source_only_recovered_ids
                        else "duplicate_suppressed"
                        if candidate["candidate_id"] in duplicate_ids
                        else "rejected_ambiguous"
                        if candidate["candidate_id"] in rejected_ids
                        else "failed"
                    ),
                    diagnostics.get(candidate["candidate_id"]),
                )
                for candidate in candidates
            ],
        }
    )
    failure_report = None
    if run_dir is not None:
        failure_report = write_failure_report(
            Path(run_dir),
            Path(run_dir).name,
            STAGE_NAME,
            list(missing_input),
            raw_results or {},
            attempts=attempts,
            canonical_updated=bool(recovered_ids),
        )
        state["ensure_coverage_stage_run"] = failure_report
    else:
        state["ensure_coverage_stage_run"] = _empty_stage_run()
    _save_outputs(context, state, ordered, report)
    return state, failure_report


def run(context, state):
    corrected_text = state.get("corrected_text")
    if not isinstance(corrected_text, str) or not corrected_text:
        raise RuntimeError("ensure_coverage requires non-empty corrected_text from correct_text.")
    statement_dict = state.get("unsplit_statement_dict")
    if not isinstance(statement_dict, dict):
        raise RuntimeError("ensure_coverage requires unsplit_statement_dict from extract_statements.")

    if is_tex_source_format(context):
        return _run_tex_audit(context, state)

    candidates = build_structural_candidates(
        corrected_text,
        state.get("problem_dict") or {},
        state.get("segment_blocks_report") or {},
    )
    missing, _ = find_missing_candidates(candidates, statement_dict, corrected_text)
    missing_input = {
        candidate["candidate_id"]: _candidate_input(candidate)
        for candidate in missing
        if candidate.get("source_text")
    }
    save_stage_json(context.output_dir, "coverage_candidates.json", candidates, "Coverage candidates")
    save_stage_json(
        context.output_dir,
        "coverage_missing_input_dict.json",
        missing_input,
        "Missing coverage inputs",
    )

    base_statement_dict = copy.deepcopy(statement_dict)
    if not missing_input:
        state, _ = _finalize_outputs(
            context,
            state,
            base_statement_dict,
            {},
        )
        return state

    diagnostics = {}
    raw_results, failure_report, run_dir = run_recoverable_task(
        context,
        stage_name=STAGE_NAME,
        input_dict=missing_input,
        task_runner=lambda index_dict, checkpoint_dir: _run_validated_extract_tasks(
            context,
            index_dict,
            checkpoint_dir,
            diagnostics,
        ),
    )
    write_json(str(Path(run_dir) / "base_statement_dict.json"), base_statement_dict)
    state, _ = _finalize_outputs(
        context,
        state,
        base_statement_dict,
        raw_results,
        diagnostics=diagnostics,
        run_dir=run_dir,
        attempts=failure_report.get("attempt_rounds") or 1,
    )
    return state


def rerun_failed_tasks(context, state, max_rounds=2):
    diagnostics = {}
    raw_results, failure_report, run_dir = rerun_unresolved_task_report(
        context,
        stage_name=STAGE_NAME,
        task_runner=lambda index_dict, checkpoint_dir: _run_validated_extract_tasks(
            context,
            index_dict,
            checkpoint_dir,
            diagnostics,
        ),
        max_rounds=max_rounds,
    )
    base_path = Path(run_dir) / "base_statement_dict.json"
    base_statement_dict = (
        read_json(str(base_path))
        if base_path.exists()
        else copy.deepcopy(state.get("unsplit_statement_dict") or {})
    )
    state, final_report = _finalize_outputs(
        context,
        state,
        base_statement_dict,
        raw_results,
        diagnostics=diagnostics,
        run_dir=run_dir,
        attempts=failure_report.get("attempt_rounds") or 1,
    )
    if failure_report.get("status") != "resolved":
        state["ensure_coverage_stage_run"] = final_report or failure_report
        return state, final_report or failure_report
    return state, {
        **(final_report or failure_report),
        "status": "resolved",
        "canonical_updated": bool(
            state.get("ensure_coverage_report", {}).get("recovered_node_count")
        ),
    }


__all__ = [
    "build_numbered_candidates",
    "build_structural_candidates",
    "find_missing_candidates",
    "merge_recovered_statements",
    "latest_unresolved_failure_report",
    "rerun_failed_tasks",
    "run",
]
