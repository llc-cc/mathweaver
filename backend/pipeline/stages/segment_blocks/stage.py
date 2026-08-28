import copy
import json
import os
import re

from ...common.llm_task import run_multiprocess_task
from ...common.io import save_stage_json
from ...common.stage_recovery import (
    latest_unresolved_failure_report as _latest_unresolved_failure_report,
    rerun_unresolved_task_report,
    run_recoverable_task,
    write_failure_report,
)
from ...common.tex import build_tex_stage_outputs, is_tex_source_format, read_tex_source
from .templates import (
    build_correction_prompt,
    build_prompt,
    correction_prompt02,
    data_template02,
    prompt_template02,
    validation02,
)


STAGE_NAME = "segment_blocks"
LOGICAL_TYPE_PATTERN = re.compile(
    r"^\s*(?P<type>"
    r"theorem|lemma|proposition|corollary|definition|claim|axiom|property|"
    r"example|exercise|remark|conjecture|problem|observation|fact|"
    r"定理|引理|命题|推论|定义|断言|公理|性质|例|练习|注|猜想|问题"
    r")(?=\s|[.:：]|[A-Z0-9IVXLCDM]|$)",
    re.IGNORECASE,
)
NAMED_LABEL_PATTERN = re.compile(
    r"^\s*(?P<label>(?:"
    r"theorem|lemma|proposition|corollary|definition|claim|axiom|property|"
    r"example|exercise|remark|conjecture|problem|observation|fact|"
    r"定理|引理|命题|推论|定义|断言|公理|性质|例|练习|注|猜想|问题"
    r")(?:\s*(?:[A-Z]|[IVXLCDM]+|[A-Za-z]?\d+(?:[.\-][A-Za-z0-9]+)*))?)"
    r"(?=\s*[.:：]|\s+|$)",
    re.IGNORECASE,
)
PROOF_PATTERN = re.compile(r"^\s*(proof|proof\s+of|证明|证)\b[\s.:：]?", re.IGNORECASE)
PROOF_INLINE_PATTERN = re.compile(r"\bProof(?:\s+of)?\s*[\.:]", re.IGNORECASE)
HEADING_PATTERN = re.compile(r"^\s*(#{1,6}\s+|chapter\b|section\b|appendix\b|第.+[章节])", re.IGNORECASE)
SUBPART_PATTERN = re.compile(
    r"^\s*(?:\([a-zivxlcdm0-9]+\)|[a-zivxlcdm]\)|[（][a-zivxlcdm0-9]+[）])\s+",
    re.IGNORECASE,
)
NUMERIC_LABEL_PATTERN = re.compile(
    r"^\s*(?P<label>(?:\([A-Za-z0-9]+(?:[.\-][A-Za-z0-9]+)*\)|"
    r"[A-Za-z0-9]+(?:[.\-][A-Za-z0-9]+)+[.:]?))\s+"
)
CUSTOM_TAG_PATTERN = re.compile(r"^\s*(?P<label>\\(?:tag|label)\{[^{}]+\}|[A-Z]\d+)\s*")
REFERENCE_CUE_PATTERN = re.compile(
    r"\b(by|see|from|using|according\s+to|apply|follows\s+from|"
    r"theorem|lemma|proposition|corollary|definition|reference|参见|由|根据)\b",
    re.IGNORECASE,
)
EMBEDDED_REFERENCE_PATTERN = re.compile(
    r"\b(?:theorem|lemma|proposition|corollary|definition|claim|axiom|property|"
    r"example|exercise|remark|conjecture|problem|observation|fact)\s+"
    r"(?:[A-Z]|[IVXLCDM]+|[A-Za-z]?\d+(?:[.\-][A-Za-z0-9]+)*)\b",
    re.IGNORECASE,
)
MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\([^)]+\)")
UNKNOWN_REFERENCE_PATTERN = re.compile(r"\(\?\?\)|\?\?")
NAVIGATION_LINE_PATTERN = re.compile(r"^\s*#?\s*(?:[A-Za-z][A-Za-z' -]+\s+h\s+){3,}.+", re.IGNORECASE)
MARKDOWN_HEADING_PATTERN = re.compile(r"^\s*(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*$")
MARKDOWN_LOGICAL_HEADING_PATTERN = re.compile(
    r"^\s*#{1,6}\s+"
    r"(?P<type>theorem|lemma|proposition|corollary|definition|claim|axiom|property|"
    r"example|exercise|remark|conjecture|problem|observation|fact)"
    r"\s*(?P<number>[A-Za-z]?\d+(?:[.\-][A-Za-z0-9]+)*|[IVXLCDM]+|[A-Z])?"
    r"\s*\.?\s*(?P<title>\([^)]*\)|[-–:：].*)?\s*$",
    re.IGNORECASE,
)
MARKDOWN_SECTION_HEADING_PATTERN = re.compile(
    r"^\s*#{1,6}\s+(?P<number>\d+(?:\.\d+)*\.?)\s+(?P<title>.+?)\s*$",
    re.IGNORECASE,
)

PROOF_CAPABLE_TYPES = {
    "theorem",
    "lemma",
    "proposition",
    "corollary",
    "claim",
    "axiom",
    "property",
    "conjecture",
    "problem",
    "fact",
}
CONTEXT_AFTER_EXERCISE_PATTERN = re.compile(
    r"\b("
    r"A\s+\w+.*\bis\s+(called|defined)|"
    r"An\s+\w+.*\bis\s+(called|defined)|"
    r"The\s+\w+.*\bis\s+(called|defined)|"
    r"In\s+general\b|"
    r"Recall\s+from\b|"
    r"We\s+can\s+(define|introduce)|"
    r"is\s+called\s+an?\b|"
    r"is\s+said\s+to\s+be\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)


def _unwrap_fragment(value):
    value = str(value)
    if value.startswith('r"""') and value.endswith('"""'):
        return value[4:-3]
    return value


def _numeric_sort_key(value):
    try:
        return 0, int(value)
    except (TypeError, ValueError):
        return 1, str(value)


def flatten_units(chopped_dict):
    units = []
    for source_key in sorted(chopped_dict, key=_numeric_sort_key):
        wrapper = chopped_dict[source_key]
        pos1 = wrapper.get("pos1", {}) if isinstance(wrapper, dict) else {}
        for source_unit_id in sorted(pos1, key=_numeric_sort_key):
            units.append(
                {
                    "unit_id": str(len(units)),
                    "source_batch_key": str(source_key),
                    "source_unit_id": str(source_unit_id),
                    "text": _unwrap_fragment(pos1[source_unit_id]),
                }
            )
    return units


def _strip_markdown_noise_from_unit(unit):
    text = unit.get("text", "")
    removed_images = MARKDOWN_IMAGE_PATTERN.findall(text)
    cleaned = MARKDOWN_IMAGE_PATTERN.sub("", text)
    removed_navigation_lines = []
    kept_lines = []
    for line in cleaned.splitlines(keepends=True):
        if NAVIGATION_LINE_PATTERN.match(line):
            removed_navigation_lines.append(line.strip())
            continue
        kept_lines.append(line)
    cleaned = "".join(kept_lines)
    issues = []
    if removed_images:
        issues.append({"unit_id": unit["unit_id"], "reason": "removed_markdown_image", "items": removed_images})
    if removed_navigation_lines:
        issues.append(
            {
                "unit_id": unit["unit_id"],
                "reason": "removed_navigation_or_header_line",
                "items": removed_navigation_lines,
            }
        )
    cleaned_unit = dict(unit)
    cleaned_unit["text"] = cleaned
    if cleaned != text:
        cleaned_unit["raw_text_before_md_cleanup"] = text
    return cleaned_unit, issues


def clean_markdown_units(units):
    cleaned_units = []
    cleanup_report = []
    for unit in units:
        cleaned, issues = _strip_markdown_noise_from_unit(unit)
        cleanup_report.extend(issues)
        if cleaned.get("text", "").strip():
            cleaned["unit_id"] = str(len(cleaned_units))
            cleaned_units.append(cleaned)
        elif issues:
            cleanup_report.append({"unit_id": unit["unit_id"], "reason": "dropped_empty_unit_after_cleanup"})
    return cleaned_units, cleanup_report


def parse_markdown_heading(text):
    stripped = ""
    for line in str(text or "").splitlines():
        if line.strip():
            stripped = line.strip()
            break
    heading_match = MARKDOWN_HEADING_PATTERN.match(stripped)
    if not heading_match:
        return None
    title = heading_match.group("title").strip()
    logical_match = MARKDOWN_LOGICAL_HEADING_PATTERN.match(stripped)
    if logical_match:
        logical_type = logical_match.group("type").strip()
        number = (logical_match.group("number") or "").strip().rstrip(".")
        title_suffix = (logical_match.group("title") or "").strip()
        label = f"{logical_type} {number}".strip()
        return {
            "kind": "logical",
            "label": label,
            "logical_type": logical_type,
            "title": title_suffix.strip(" -–:："),
            "evidence": ["markdown_logical_heading"],
        }
    section_match = MARKDOWN_SECTION_HEADING_PATTERN.match(stripped)
    if section_match:
        return {
            "kind": "section",
            "label": "",
            "logical_type": "",
            "title": title,
            "evidence": ["markdown_section_heading"],
        }
    return {
        "kind": "section",
        "label": "",
        "logical_type": "",
        "title": title,
        "evidence": ["markdown_generic_heading"],
    }


def _label_evidence(text):
    stripped = text.strip()
    evidence = []
    label_surface = ""
    label_family = ""
    logical_type_hint = ""

    heading_fact = parse_markdown_heading(text)
    if heading_fact:
        evidence.extend(heading_fact["evidence"])
        if heading_fact["kind"] == "logical":
            label_surface = heading_fact["label"]
            label_family = "markdown_heading"
            logical_type_hint = heading_fact["logical_type"]
            evidence.append("unit_initial_logical_type")
        return label_surface, label_family, logical_type_hint, evidence

    type_match = LOGICAL_TYPE_PATTERN.match(stripped)
    if type_match:
        logical_type_hint = type_match.group("type")
        named_label_match = NAMED_LABEL_PATTERN.match(stripped)
        label_surface = named_label_match.group("label") if named_label_match else logical_type_hint
        label_family = "named"
        evidence.append("unit_initial_logical_type")

    numeric_match = NUMERIC_LABEL_PATTERN.match(stripped)
    if numeric_match:
        label_surface = numeric_match.group("label")
        label_family = "numeric_or_alphanumeric"
        evidence.append("unit_initial_label_like")

    custom_match = CUSTOM_TAG_PATTERN.match(stripped)
    if custom_match:
        label_surface = custom_match.group("label")
        label_family = "custom_or_symbolic"
        evidence.append("unit_initial_custom_tag")

    return label_surface, label_family, logical_type_hint, evidence


def build_boundary_evidence(units):
    packet = []
    for index, unit in enumerate(units):
        text = unit["text"]
        stripped = text.strip()
        label_surface, label_family, logical_type_hint, evidence = _label_evidence(text)
        heading_fact = parse_markdown_heading(text)

        if index == 0:
            evidence.append("document_initial")
        if HEADING_PATTERN.match(stripped):
            evidence.append("heading_like")
        if PROOF_PATTERN.match(stripped):
            evidence.append("proof_like")
        if SUBPART_PATTERN.match(stripped):
            evidence.append("subpart_like")
        embedded_reference = EMBEDDED_REFERENCE_PATTERN.search(stripped)
        if embedded_reference and embedded_reference.start() > 0:
            prefix = stripped[: embedded_reference.start()]
            if REFERENCE_CUE_PATTERN.search(prefix):
                evidence.append("embedded_label_reference")
        if "\n\n" in text:
            evidence.append("paragraph_boundary")

        packet.append(
            {
                **unit,
                "label_surface_hint": label_surface,
                "label_family_hint": label_family,
                "logical_unit_type_hint": logical_type_hint,
                "markdown_heading_kind": (heading_fact or {}).get("kind", ""),
                "markdown_heading_title": (heading_fact or {}).get("title", ""),
                "rule_evidence": evidence,
            }
        )
    return packet


def _fallback_classifications(unit_packet, reason):
    warnings = []
    classifications = {}
    for item in unit_packet:
        unit_id = item["unit_id"]
        evidence = item["rule_evidence"]
        role = "ordinary_continuation"
        if "markdown_logical_heading" in evidence or "unit_initial_logical_type" in evidence:
            role = "top_level_logical_unit_start"
        elif "markdown_section_heading" in evidence or "markdown_generic_heading" in evidence:
            role = "section_context"
        elif "heading_like" in evidence:
            role = "heading_or_section_start"
        elif "proof_like" in evidence:
            role = "proof_start_or_continuation"
        elif "subpart_like" in evidence:
            role = "subpart_or_item"
        classifications[unit_id] = {
            "role": role,
            "label_surface": item["label_surface_hint"],
            "label_family": item["label_family_hint"],
            "logical_unit_type_hint": item["logical_unit_type_hint"],
            "evidence": evidence,
            "reason": "Conservative deterministic fallback; no semantic boundary inferred.",
            "decision_source": "deterministic_fallback",
        }
        warnings.append({"unit_id": unit_id, "reason": reason})
    return {"units": classifications, "warnings": warnings}


def _normalize_classifications(parsed, unit_packet):
    normalized = {"units": {}, "warnings": parsed.get("warnings", [])}
    for item in unit_packet:
        unit_id = item["unit_id"]
        classification = dict(parsed["units"][unit_id])
        model_label = classification.get("label_surface", "").strip()
        if model_label and model_label not in item["text"]:
            model_label = ""
            normalized["warnings"].append(
                {"unit_id": unit_id, "reason": "model_label_surface_not_found_in_source"}
            )
        classification["label_surface"] = model_label or item["label_surface_hint"]
        classification["label_family"] = (
            classification.get("label_family", "").strip() or item["label_family_hint"]
        )
        classification["logical_unit_type_hint"] = (
            classification.get("logical_unit_type_hint", "").strip()
            or item["logical_unit_type_hint"]
        )
        classification["evidence"] = list(
            dict.fromkeys(item["rule_evidence"] + classification.get("evidence", []))
        )
        if "markdown_logical_heading" in item["rule_evidence"]:
            classification["role"] = "top_level_logical_unit_start"
        elif "markdown_section_heading" in item["rule_evidence"] or "markdown_generic_heading" in item["rule_evidence"]:
            classification["role"] = "section_context"
        classification["decision_source"] = "rules_and_llm"
        normalized["units"][unit_id] = classification
    return normalized


def _classification_prompt_char_limit():
    raw_value = os.getenv("SEGMENT_BLOCKS_PROMPT_CHAR_LIMIT", "60000")
    try:
        return max(10000, int(raw_value))
    except (TypeError, ValueError):
        return 60000


def _classification_overlap_chars():
    raw_value = os.getenv("SEGMENT_BLOCKS_OVERLAP_CHARS", "6000")
    try:
        return max(0, int(raw_value))
    except (TypeError, ValueError):
        return 6000


def _classification_overlap_units():
    raw_value = os.getenv("SEGMENT_BLOCKS_OVERLAP_UNITS", "8")
    try:
        return max(0, int(raw_value))
    except (TypeError, ValueError):
        return 8


def _math_context_balanced(text):
    if text.count("$$") % 2:
        return False
    if text.count(r"\[") != text.count(r"\]"):
        return False
    stack = []
    for match in re.finditer(r"\\(begin|end)\{([^{}]+)\}", text):
        kind, env_name = match.groups()
        if kind == "begin":
            stack.append(env_name)
        elif not stack or stack.pop() != env_name:
            return False
    return not stack


def _safe_boundary_after(unit_packet, start, end):
    if end <= start or end > len(unit_packet):
        return False
    text = "".join(item["text"] for item in unit_packet[start:end])
    if not _math_context_balanced(text):
        return False
    previous = unit_packet[end - 1]["text"]
    newline_boundary = previous.rstrip(" \t").endswith("\n")
    stripped = previous.rstrip()
    return newline_boundary or stripped.endswith((".", "!", "?", ";", ":", "。", "！", "？", "；", "："))


def _fit_window_end(unit_packet, start, max_prompt_chars):
    end = start
    for candidate_end in range(start + 1, len(unit_packet) + 1):
        if len(build_prompt(unit_packet[start:candidate_end])) > max_prompt_chars:
            break
        end = candidate_end
    if end == start:
        return min(start + 1, len(unit_packet))

    for candidate_end in range(end, start, -1):
        if _safe_boundary_after(unit_packet, start, candidate_end):
            return candidate_end
    return end


def _overlap_start(unit_packet, core_start, overlap_chars, overlap_units):
    if core_start <= 0:
        return 0
    start = core_start
    char_count = 0
    min_start_by_units = max(0, core_start - overlap_units)
    while start > 0 and (char_count < overlap_chars or start > min_start_by_units):
        start -= 1
        char_count += len(unit_packet[start]["text"])
    return start


def _chunk_unit_packet(unit_packet, max_prompt_chars):
    chunks = []
    core_start = 0
    overlap_chars = _classification_overlap_chars()
    overlap_units = _classification_overlap_units()
    while core_start < len(unit_packet):
        context_start = _overlap_start(unit_packet, core_start, overlap_chars, overlap_units)
        end = _fit_window_end(unit_packet, context_start, max_prompt_chars)
        while end <= core_start and context_start < core_start:
            context_start += 1
            end = _fit_window_end(unit_packet, context_start, max_prompt_chars)
        if end <= core_start:
            end = min(core_start + 1, len(unit_packet))
            context_start = core_start
        chunks.append(
            {
                "context_start": context_start,
                "core_start": core_start,
                "end": end,
                "items": unit_packet[context_start:end],
            }
        )
        core_start = end
    return chunks


def _classify_boundary_chunk(llm, parser, unit_packet):
    if not unit_packet:
        return {"units": {}, "warnings": []}, [], []
    expected_ids = [item["unit_id"] for item in unit_packet]
    answers = []
    prompt = build_prompt(unit_packet)
    for attempt in range(2):
        try:
            raw_answer = llm.ask(prompt)
            answers.append(raw_answer)
            parsed = parser.parse_dict(raw_answer)
            if validation02(parsed, expected_ids):
                return _normalize_classifications(parsed, unit_packet), answers, []
            error = "classification_schema_or_coverage_invalid"
        except Exception as exc:
            error = str(exc)
        prompt = build_correction_prompt(unit_packet, answers[-1] if answers else error)
    return _fallback_classifications(unit_packet, error), answers, [error]


def _boundary_checkpoint_dir(output_dir):
    if output_dir:
        return os.path.join(str(output_dir), "checkpoint", "segment_blocks_boundary")
    return os.path.join("checkpoint", "segment_blocks_boundary")


def _build_boundary_chunk_tasks(unit_packet, chunks):
    tasks = {}
    metadata = {}
    for zero_index, chunk_info in enumerate(chunks):
        chunk = chunk_info["items"]
        context_start = chunk_info["context_start"]
        core_start = chunk_info["core_start"]
        end = chunk_info["end"]
        key = str(zero_index)
        emitted_ids = {item["unit_id"] for item in unit_packet[core_start:end]}
        tasks[key] = {
            "unit_packet": json.dumps(chunk, ensure_ascii=False, indent=2),
        }
        metadata[key] = {
            "chunk": chunk,
            "context_start": context_start,
            "core_start": core_start,
            "end": end,
            "emitted_ids": emitted_ids,
            "expected_ids": [item["unit_id"] for item in chunk],
        }
    return tasks, metadata


def _run_boundary_chunk_tasks(llm, parser, tasks, *, num_threads, checkpoint, checkpoint_dir):
    if not tasks:
        return {}
    return run_multiprocess_task(
        llm=llm,
        parse_method=parser.parse_dict,
        data_template=data_template02,
        prompt_template=prompt_template02,
        correction_template=correction_prompt02,
        validator=validation02,
        index_dict=tasks,
        num_threads=max(1, int(num_threads or 1)),
        checkpoint=checkpoint or 10,
        checkpoint_dir=checkpoint_dir,
    )


def _validated_boundary_results(tasks, result_dict):
    validated = {}
    for key, task in (tasks or {}).items():
        try:
            chunk = json.loads(task["unit_packet"])
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
        expected_ids = [item.get("unit_id") for item in chunk if isinstance(item, dict)]
        parsed = (result_dict or {}).get(str(key))
        if validation02(parsed, expected_ids):
            validated[str(key)] = parsed
    return validated


def _run_recoverable_boundary_tasks(context, tasks, checkpoint_dir):
    result_dict = _run_boundary_chunk_tasks(
        context.llm,
        context.parser,
        tasks,
        num_threads=getattr(context, "num_threads", 1),
        checkpoint=getattr(context, "checkpoint", 10),
        checkpoint_dir=checkpoint_dir,
    )
    return _validated_boundary_results(tasks, result_dict)


def _merge_boundary_task_results(unit_packet, chunks, result_dict):
    _, metadata = _build_boundary_chunk_tasks(unit_packet, chunks)
    merged = {"units": {}, "warnings": []}
    all_answers = []
    all_errors = []
    for zero_index in range(len(chunks)):
        key = str(zero_index)
        info = metadata[key]
        chunk = info["chunk"]
        context_start = info["context_start"]
        core_start = info["core_start"]
        end = info["end"]
        emitted_ids = info["emitted_ids"]
        expected_ids = info["expected_ids"]
        prompt_chars = len(build_prompt(chunk))
        print(
            f"[segment_blocks] Boundary chunk {zero_index + 1}/{len(chunks)}: "
            f"context_units={len(chunk)}, core_units={end - core_start}, "
            f"context_range=[{context_start}, {end}), core_range=[{core_start}, {end}), "
            f"prompt_chars={prompt_chars}",
            flush=True,
        )
        parsed = result_dict.get(key)
        errors = []
        if parsed is not None:
            all_answers.append(parsed)
        if validation02(parsed, expected_ids):
            classified = _normalize_classifications(parsed, chunk)
        else:
            error = "classification_task_missing_or_coverage_invalid"
            classified = _fallback_classifications(chunk, error)
            errors = [error]
        for unit_id, classification in classified.get("units", {}).items():
            if unit_id in emitted_ids:
                merged["units"][unit_id] = classification
        merged["warnings"].extend(
            warning
            for warning in classified.get("warnings", [])
            if str(warning.get("unit_id")) in emitted_ids
        )
        all_errors.extend(errors)
    return merged, all_answers, all_errors


def classify_boundary_roles(
    llm,
    parser,
    unit_packet,
    *,
    num_threads=1,
    checkpoint=10,
    checkpoint_dir=None,
):
    if not unit_packet:
        return {"units": {}, "warnings": []}, [], []

    chunks = _chunk_unit_packet(unit_packet, _classification_prompt_char_limit())
    tasks, metadata = _build_boundary_chunk_tasks(unit_packet, chunks)
    print(
        f"[segment_blocks] Classifying {len(unit_packet)} units in {len(chunks)} "
        f"sliding-window chunk(s) with {max(1, int(num_threads or 1))} thread(s)",
        flush=True,
    )

    result_dict = _run_boundary_chunk_tasks(
        llm,
        parser,
        tasks,
        num_threads=num_threads,
        checkpoint=checkpoint,
        checkpoint_dir=checkpoint_dir,
    )

    result_dict = _validated_boundary_results(tasks, result_dict)
    return _merge_boundary_task_results(unit_packet, chunks, result_dict)


def _block_quality_flags(text, block_units, classifications):
    flags = []
    if MARKDOWN_IMAGE_PATTERN.search(text):
        flags.append("contains_image")
    if len(re.findall(r"(?m)^\s*#{1,6}\s+", text or "")) > 1:
        flags.append("contains_multiple_headings")
    if UNKNOWN_REFERENCE_PATTERN.search(text or ""):
        flags.append("contains_unknown_refs")
    first_role = classifications[block_units[0]["unit_id"]].get("role") if block_units else ""
    if first_role == "section_context":
        flags.append("section_context_block")
    proof_match = PROOF_INLINE_PATTERN.search(text or "") or PROOF_PATTERN.search(text or "")
    if proof_match:
        before = text[: proof_match.start()].strip()
        after = text[proof_match.end():].strip()
        first_type = (classifications[block_units[0]["unit_id"]].get("logical_unit_type_hint") or "").lower()
        if first_type and first_type not in PROOF_CAPABLE_TYPES:
            flags.append("proof_attached_to_non_proof_capable_type")
        if before and after and len(after) > max(300, len(before) * 2):
            flags.append("proof_longer_than_content")
    if len(text or "") > 2500:
        flags.append("very_long_block")
    return flags


def _quality_summary(block_reports):
    summary = {}
    for block in block_reports:
        for flag in block.get("block_quality_flags", []):
            summary[flag] = summary.get(flag, 0) + 1
    return summary


def _block_first_type(block_units, classifications):
    if not block_units:
        return ""
    return (classifications[block_units[0]["unit_id"]].get("logical_unit_type_hint") or "").strip().lower()


def _looks_like_context_after_exercise(unit_text):
    text = str(unit_text or "").strip()
    if not text:
        return False
    if MARKDOWN_HEADING_PATTERN.match(text) or SUBPART_PATTERN.match(text) or PROOF_PATTERN.match(text):
        return False
    return bool(CONTEXT_AFTER_EXERCISE_PATTERN.search(text[:1200]))


def assemble_problem_blocks(units, classification_result):
    classifications = classification_result["units"]
    report_warnings = list(classification_result.get("warnings", []))
    blocks = []
    current = []

    for unit in units:
        classification = classifications[unit["unit_id"]]
        role = classification["role"]
        if (
            current
            and _block_first_type(current, classifications) == "exercise"
            and role == "ordinary_continuation"
            and _looks_like_context_after_exercise(unit["text"])
        ):
            blocks.append(current)
            current = []
            classification["role"] = "section_context"
            classification["evidence"] = list(
                dict.fromkeys(classification.get("evidence", []) + ["context_after_exercise"])
            )
            role = "section_context"
        starts_block = role in {"top_level_logical_unit_start", "heading_or_section_start", "section_context"}
        if starts_block and current:
            blocks.append(current)
            current = []
        current.append(unit)
    if current:
        blocks.append(current)

    problem_dict = {}
    mapping_dict = {}
    block_reports = []
    unit_assignments = {}
    problem_block_id = 0
    for raw_block_id, block_units in enumerate(blocks):
        text = "".join(unit["text"] for unit in block_units)
        first = block_units[0]
        first_classification = classifications[first["unit_id"]]
        quality_flags = _block_quality_flags(text, block_units, classifications)
        is_section_context = first_classification["role"] == "section_context"
        output_block_id = None if is_section_context else problem_block_id
        if not is_section_context:
            problem_dict[problem_block_id] = {"pos1": text}
            problem_block_id += 1
        block_reports.append(
            {
                "block_id": output_block_id,
                "raw_block_id": raw_block_id,
                "start_unit_id": first["unit_id"],
                "end_unit_id": block_units[-1]["unit_id"],
                "boundary_role": first_classification["role"],
                "label_surface": first_classification.get("label_surface", ""),
                "label_family": first_classification.get("label_family", ""),
                "logical_unit_type_hint": first_classification.get("logical_unit_type_hint", ""),
                "decision_source": first_classification.get("decision_source", "rules_and_llm"),
                "evidence": first_classification.get("evidence", []),
                "warnings": [
                    warning
                    for warning in report_warnings
                    if str(warning.get("unit_id")) in {unit["unit_id"] for unit in block_units}
                ],
                "block_quality_flags": quality_flags,
                "unit_ids": [unit["unit_id"] for unit in block_units],
            }
        )
        for unit in block_units:
            unit_assignments[unit["unit_id"]] = {
                "block_id": output_block_id,
                "raw_block_id": raw_block_id,
                "role": classifications[unit["unit_id"]]["role"],
                "source_batch_key": unit["source_batch_key"],
            }
            if output_block_id is not None:
                mapping_dict.setdefault(unit["source_batch_key"], {})[output_block_id] = text

    report = {
        "schema_version": 1,
        "source_unit_count": len(units),
        "problem_block_count": len(problem_dict),
        "all_units_consumed_once": len(unit_assignments) == len(units),
        "warnings": report_warnings,
        "blocks": block_reports,
        "unit_assignments": unit_assignments,
        "quality_summary": _quality_summary(block_reports),
    }
    return problem_dict, mapping_dict, report


def extract_problem(chopped_dict, marker_dict):
    """Legacy cut-marker assembler retained for callers that still use it directly."""
    chopped_dict_1 = copy.deepcopy(chopped_dict)
    for key, value in marker_dict.items():
        source_key = key if key in chopped_dict else str(key)
        if source_key not in chopped_dict:
            continue
        for cut_mark in value:
            try:
                numeric_cut_mark = int(cut_mark)
            except (ValueError, TypeError):
                continue
            pos1 = chopped_dict[source_key]["pos1"]
            resolved_cut_mark = numeric_cut_mark if numeric_cut_mark in pos1 else str(numeric_cut_mark)
            if resolved_cut_mark in pos1:
                chopped_dict_1[source_key]["pos1"][resolved_cut_mark] = (
                    _unwrap_fragment(pos1[resolved_cut_mark]) + "###cut mark###"
                )

    concatenated_string = ""
    segment_owner = []
    for key in sorted(chopped_dict_1, key=_numeric_sort_key):
        for sub_key in sorted(chopped_dict_1[key].get("pos1", {}), key=_numeric_sort_key):
            text = _unwrap_fragment(chopped_dict_1[key]["pos1"][sub_key])
            concatenated_string += text
            if "###cut mark###" in text:
                segment_owner.append(key)
    split_list = [value for value in concatenated_string.split("###cut mark###") if value]
    split_dict = {index: {"pos1": value} for index, value in enumerate(split_list)}
    mapping_dict = {key: {} for key in sorted(chopped_dict, key=_numeric_sort_key)}
    for global_id, big_key in enumerate(segment_owner):
        mapping_dict[big_key][global_id] = split_dict[global_id]["pos1"]
    if len(split_list) > len(segment_owner) and segment_owner:
        last_global_id = len(split_list) - 1
        mapping_dict[segment_owner[-1]][last_global_id] = split_dict[last_global_id]["pos1"]
    return split_dict, mapping_dict


def latest_unresolved_failure_report(context):
    return _latest_unresolved_failure_report(context, STAGE_NAME)


def _finalize_boundary_outputs(
    context,
    state,
    units,
    md_cleanup_report,
    unit_packet,
    result_dict,
    *,
    run_dir=None,
    attempts=1,
):
    chunks = _chunk_unit_packet(unit_packet, _classification_prompt_char_limit())
    classifications, raw_answers, classification_errors = _merge_boundary_task_results(
        unit_packet,
        chunks,
        result_dict,
    )
    problem_dict, mapping_dict, report = assemble_problem_blocks(units, classifications)
    report["classification_errors"] = classification_errors
    report["llm_attempt_count"] = len(raw_answers)
    report["md_cleanup_report"] = md_cleanup_report
    save_stage_json(context.output_dir, "problem_dict.json", problem_dict, "Problem dict")
    save_stage_json(
        context.output_dir,
        "segment_blocks_md_cleanup_report.json",
        md_cleanup_report,
        "Segment blocks MD cleanup report",
    )
    save_stage_json(context.output_dir, "segment_blocks_report.json", report, "Segment blocks report")
    failure_report = None
    if run_dir is not None:
        failure_report = write_failure_report(
            run_dir,
            run_dir.name,
            STAGE_NAME,
            [str(index) for index in range(len(chunks))],
            result_dict,
            attempts=attempts,
            canonical_updated=True,
        )
        state["segment_blocks_stage_run"] = failure_report
    state["problem_dict"] = problem_dict
    state["mapping_dict"] = mapping_dict
    state["segment_blocks_report"] = report
    return state, failure_report


def run(context, state):
    if is_tex_source_format(context):
        source_text = read_tex_source(context.file_path)
        problem_dict, _, tex_report, document_model = build_tex_stage_outputs(
            source_text,
            source_file=context.file_path,
        )
        environment_blocks = [
            {
                "block_id": int(block["source_block_key"]),
                "start_unit_id": block["source_block_key"],
                "end_unit_id": block["source_block_key"],
                "boundary_role": "top_level_logical_unit_start",
                "label_surface": block.get("label", ""),
                "label_family": (
                    "tex_counter"
                    if block.get("label_source") in {"tex_counter", "tex_counter_fallback"}
                    else "tex_label"
                    if block.get("label_source") == "tex_label_key"
                    else ""
                ),
                "logical_unit_type_hint": block.get("node_type", ""),
                "decision_source": "tex_environment_parser",
                "evidence": ["tex_theorem_environment"],
                "warnings": (
                    [{"reason": "unresolved_tex_counter_label"}]
                    if block.get("numbered") and not block.get("label")
                    else []
                ),
                "unit_ids": [block["source_block_key"]],
                "source_kind": "tex_environment",
                "source_span": block.get("source_span", {}),
            }
            for block in tex_report.get("blocks", [])
        ]
        residual_blocks = [
            {
                "block_id": block["block_id"],
                "start_unit_id": block["block_id"],
                "end_unit_id": block["block_id"],
                "boundary_role": "tex_residual_span",
                "label_surface": "",
                "label_family": "",
                "logical_unit_type_hint": "",
                "decision_source": "tex_residual_builder",
                "evidence": ["outside_protected_tex_environments"],
                "warnings": [],
                "unit_ids": [block["block_id"]],
                "source_kind": "tex_residual",
                "source_span": block.get("source_span", {}),
                "split_reason": block.get("split_reason", ""),
            }
            for block in tex_report.get("residual_blocks", [])
        ]
        all_blocks = sorted(
            [*environment_blocks, *residual_blocks],
            key=lambda block: (
                (block.get("source_span") or {}).get("start", 10**15),
                str(block.get("block_id")),
            ),
        )
        report = {
            "schema_version": 2,
            "source_format": "tex",
            "source_unit_count": tex_report["source_block_count"],
            "problem_block_count": len(problem_dict),
            "environment_block_count": len(environment_blocks),
            "residual_block_count": len(residual_blocks),
            "all_units_consumed_once": True,
            "warnings": [
                {
                    "source_block_key": block["source_block_key"],
                    "reason": "unresolved_tex_counter_label",
                }
                for block in tex_report.get("blocks", [])
                if block.get("numbered") and not block.get("label")
            ],
            "blocks": all_blocks,
            "unit_assignments": {
                str(block["block_id"]): {
                    "block_id": block["block_id"],
                    "role": block["boundary_role"],
                    "source_batch_key": "0",
                }
                for block in all_blocks
            },
            "classification_errors": [],
            "llm_attempt_count": 0,
        }
        save_stage_json(context.output_dir, "problem_dict.json", problem_dict, "Problem dict")
        save_stage_json(context.output_dir, "segment_blocks_report.json", report, "Segment blocks report")
        save_stage_json(context.output_dir, "tex_document_model.json", document_model, "TeX document model")
        state["problem_dict"] = problem_dict
        state["mapping_dict"] = {"0": {key: value["pos1"] for key, value in problem_dict.items()}}
        state["segment_blocks_report"] = report
        state["tex_document_model"] = document_model
        return state

    raw_units = flatten_units(state["chopped_text_dict"])
    units, md_cleanup_report = clean_markdown_units(raw_units)
    print(
        f"[segment_blocks] Flattened {len(raw_units)} corrected text unit(s); "
        f"{len(units)} remain after MD cleanup",
        flush=True,
    )
    unit_packet = build_boundary_evidence(units)
    chunks = _chunk_unit_packet(unit_packet, _classification_prompt_char_limit())
    tasks, _ = _build_boundary_chunk_tasks(unit_packet, chunks)
    result_dict, failure_report, run_dir = run_recoverable_task(
        context,
        stage_name=STAGE_NAME,
        input_dict=tasks,
        task_runner=lambda index_dict, checkpoint_dir: _run_recoverable_boundary_tasks(
            context,
            index_dict,
            checkpoint_dir,
        ),
    )
    state, _ = _finalize_boundary_outputs(
        context,
        state,
        units,
        md_cleanup_report,
        unit_packet,
        result_dict,
        run_dir=run_dir,
        attempts=failure_report.get("attempt_rounds") or 1,
    )
    return state


def rerun_failed_tasks(context, state, max_rounds=2):
    raw_units = flatten_units(state["chopped_text_dict"])
    units, md_cleanup_report = clean_markdown_units(raw_units)
    unit_packet = build_boundary_evidence(units)
    result_dict, failure_report, run_dir = rerun_unresolved_task_report(
        context,
        stage_name=STAGE_NAME,
        task_runner=lambda index_dict, checkpoint_dir: _run_recoverable_boundary_tasks(
            context,
            index_dict,
            checkpoint_dir,
        ),
        max_rounds=max_rounds,
    )
    if failure_report.get("status") != "resolved":
        state["segment_blocks_stage_run"] = failure_report
        return state, failure_report
    state, final_report = _finalize_boundary_outputs(
        context,
        state,
        units,
        md_cleanup_report,
        unit_packet,
        result_dict,
        run_dir=run_dir,
        attempts=failure_report.get("attempt_rounds") or 1,
    )
    return state, final_report
