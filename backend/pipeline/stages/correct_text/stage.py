import json
import re

from ...common.io import save_stage_json
from ...common.llm_task import run_multiprocess_task
from ...common.stage_recovery import (
    latest_unresolved_failure_report as _latest_unresolved_failure_report,
    rerun_unresolved_task_report,
    run_recoverable_task,
    write_failure_report,
)
from ...common.tex import is_tex_source_format
from .templates import (
    correction_prompt01,
    data_template01,
    prompt_template01,
    validation01,
)


DEFAULT_BATCH_CHAR_BUDGET = 6000
STAGE_NAME = "correct_text"
DEFAULT_LONG_UNIT_CHARS = 6000
STRUCTURAL_MARKER_PATTERN = re.compile(
    r"(?<!\w)(?=Proof\.\s*)|"
    r"(?<![A-Za-z0-9])(?=\(\d+(?:\.\d+)+\)\s+"
    r"(?:Let|Suppose|Assume|If|For|Given|Theorem|Lemma|Proposition|Corollary|Definition)\b)"
)
DISPLAY_MATH_PATTERN = re.compile(r"(\$\$.*?\$\$|\\\[.*?\\\])", re.DOTALL)


def _append_unit(units, text):
    if not text:
        return
    if text.strip():
        units.append(text)
    elif units:
        units[-1] += text


def _split_long_text(text, max_chars):
    if len(text) <= max_chars:
        return [text]

    units = []
    start = 0
    in_inline_math = False
    safe_boundaries = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "$" and not text.startswith("$$", index):
            in_inline_math = not in_inline_math
        if not in_inline_math and char in ".!?;" and index + 1 < len(text) and text[index + 1].isspace():
            safe_boundaries.append(index + 1)
        if index - start >= max_chars:
            candidates = [boundary for boundary in safe_boundaries if boundary > start]
            if not candidates:
                return [text]
            boundary = candidates[-1]
            units.append(text[start:boundary])
            start = boundary
            safe_boundaries = [item for item in safe_boundaries if item > start]
        index += 1
    if start < len(text):
        units.append(text[start:])
    return units


def _split_non_math_text(text, max_chars):
    if not text:
        return []
    pieces = []
    positions = [match.start() for match in STRUCTURAL_MARKER_PATTERN.finditer(text)]
    positions = sorted({0, *positions, len(text)})
    for start, end in zip(positions, positions[1:]):
        pieces.extend(_split_long_text(text[start:end], max_chars))
    return pieces


def build_structure_preserving_units(text, max_chars=DEFAULT_LONG_UNIT_CHARS):
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    units = []
    paragraphs = re.findall(r".*?(?:\n[ \t]*\n+|\Z)", normalized, re.DOTALL)
    for paragraph in paragraphs:
        if not paragraph:
            continue
        heading_match = re.match(r"^(#{1,6}[^\n]*(?:\n|$))", paragraph)
        if heading_match and heading_match.end() < len(paragraph):
            _append_unit(units, heading_match.group(1))
            paragraph = paragraph[heading_match.end():]
        for part in DISPLAY_MATH_PATTERN.split(paragraph):
            if not part:
                continue
            if DISPLAY_MATH_PATTERN.fullmatch(part):
                _append_unit(units, part)
                continue
            for piece in _split_non_math_text(part, max_chars):
                _append_unit(units, piece)
    return {str(index): unit for index, unit in enumerate(units)}


def _batch_units(units, char_budget=DEFAULT_BATCH_CHAR_BUDGET):
    batches = []
    current = {}
    current_chars = 0
    for unit_id, text in units.items():
        if current and current_chars + len(text) > char_budget:
            batches.append(current)
            current = {}
            current_chars = 0
        current[unit_id] = text
        current_chars += len(text)
    if current:
        batches.append(current)
    return batches


def _context_for_batch(all_units, batch, direction):
    unit_ids = list(all_units)
    batch_ids = list(batch)
    if direction == "previous":
        index = unit_ids.index(batch_ids[0])
        selected = unit_ids[max(0, index - 2):index]
    else:
        index = unit_ids.index(batch_ids[-1]) + 1
        selected = unit_ids[index:index + 2]
    return {unit_id: all_units[unit_id] for unit_id in selected}


def validate_correction_candidate(target_units, candidate, previous_context=None, next_context=None):
    if not validation01(candidate):
        return {}, {}, ["invalid_response_schema"]
    corrected = candidate["corrected_units"]
    if list(map(str, corrected.keys())) != list(target_units.keys()):
        return {}, {}, ["unit_id_mismatch"]
    return dict(corrected), {}, []


def _build_batch_tasks(source_units, batches):
    tasks = {}
    for batch_id, batch in enumerate(batches):
        tasks[str(batch_id)] = {
            "previous_context": json.dumps(
                _context_for_batch(source_units, batch, "previous"),
                ensure_ascii=False,
                indent=2,
            ),
            "target_units": json.dumps(batch, ensure_ascii=False, indent=2),
            "next_context": json.dumps(
                _context_for_batch(source_units, batch, "next"),
                ensure_ascii=False,
                indent=2,
            ),
        }
    return tasks


def _validated_batch_results(index_dict, result_dict):
    validated = {}
    for key, task in (index_dict or {}).items():
        candidate = (result_dict or {}).get(str(key))
        try:
            target_units = json.loads(task["target_units"])
            previous_context = json.loads(task["previous_context"])
            next_context = json.loads(task["next_context"])
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
        accepted, rejected, batch_issues = validate_correction_candidate(
            target_units,
            candidate,
            previous_context,
            next_context,
        )
        if not batch_issues and not rejected and list(accepted) == list(target_units):
            validated[str(key)] = candidate
    return validated


def _run_batch_tasks(context, index_dict, checkpoint_dir):
    if not index_dict:
        return {}
    result_dict = run_multiprocess_task(
        llm=context.llm,
        parse_method=context.parser.parse_dict,
        data_template=data_template01,
        prompt_template=prompt_template01,
        correction_template=correction_prompt01,
        validator=validation01,
        index_dict=index_dict,
        num_threads=max(1, int(getattr(context, "num_threads", 1) or 1)),
        checkpoint=getattr(context, "checkpoint", 500),
        checkpoint_dir=checkpoint_dir,
    )
    return _validated_batch_results(index_dict, result_dict)


def _wrap_unit(text):
    return f'r"""{text}"""'


def latest_unresolved_failure_report(context):
    return _latest_unresolved_failure_report(context, STAGE_NAME)


def _finalize_outputs(context, state, source_units, batches, results, *, run_dir=None, attempts=1):
    corrected_text_dict = {}
    report_batches = {}
    total_fallback = 0
    total_warnings = 0
    total_changed = 0
    failed_batch_ids = []
    corrected_units = {}
    for batch_id, batch in enumerate(batches):
        key = str(batch_id)
        candidate = results.get(key, {})
        if not candidate:
            failed_batch_ids.append(key)
        previous_context = _context_for_batch(source_units, batch, "previous")
        next_context = _context_for_batch(source_units, batch, "next")
        accepted, rejected, batch_issues = validate_correction_candidate(
            batch,
            candidate,
            previous_context,
            next_context,
        )
        if batch_issues:
            accepted = {}
            rejected = {unit_id: list(batch_issues) for unit_id in batch}
        candidate_warnings = candidate.get("warnings", []) if isinstance(candidate, dict) else []
        total_warnings += len(candidate_warnings)
        output_units = {}
        changes = []
        fallbacks = []
        for unit_id, original in batch.items():
            corrected = accepted.get(unit_id, original)
            output_units[unit_id] = _wrap_unit(corrected)
            corrected_units[unit_id] = corrected
            if unit_id in rejected or unit_id not in accepted:
                fallbacks.append({"unit_id": unit_id, "issues": rejected.get(unit_id, ["missing_output"])})
                total_fallback += 1
            elif corrected != original:
                changes.append({"unit_id": unit_id, "original": original, "corrected": corrected})
                total_changed += 1
        corrected_text_dict[key] = {"pos1": output_units}
        report_batches[key] = {
            "unit_ids": list(batch),
            "changes": changes,
            "warnings": candidate_warnings,
            "fallbacks": fallbacks,
            "errors": batch_issues,
        }

    unit_count = len(source_units)
    report = {
        "schema_version": 1,
        "source_unit_count": unit_count,
        "batch_count": len(batches),
        "changed_unit_count": total_changed,
        "fallback_unit_count": total_fallback,
        "fallback_ratio": total_fallback / unit_count if unit_count else 0,
        "warning_count": total_warnings,
        "failed_batch_ids": failed_batch_ids,
        "numbered_label_preservation_rate": 1.0,
        "batches": report_batches,
    }
    corrected_text = "".join(corrected_units.get(unit_id, source_units[unit_id]) for unit_id in source_units)
    save_stage_json(context.output_dir, "corrected_text_dict.json", corrected_text_dict, "Corrected text dict")
    save_stage_json(context.output_dir, "corrected_text.json", corrected_text, "Corrected text")
    save_stage_json(context.output_dir, "correct_text_report.json", report, "Correct text report")

    failure_report = None
    if run_dir is not None:
        failure_report = write_failure_report(
            run_dir,
            run_dir.name,
            STAGE_NAME,
            [str(index) for index in range(len(batches))],
            results,
            attempts=attempts,
            canonical_updated=True,
        )
        state["correct_text_stage_run"] = failure_report
    state["chopped_text_dict"] = corrected_text_dict
    state["corrected_text"] = corrected_text
    state["correct_text_report"] = report
    return state, failure_report


def run(context, state):
    with open(context.file_path, "r", encoding="utf-8") as source_file:
        source_text = source_file.read()

    if is_tex_source_format(context):
        corrected_text_dict = {"0": {"pos1": {"0": _wrap_unit(source_text)}}}
        report = {
            "schema_version": 1,
            "source_format": "tex",
            "source_unit_count": 1,
            "batch_count": 1,
            "changed_unit_count": 0,
            "fallback_unit_count": 0,
            "fallback_ratio": 0,
            "warning_count": 0,
            "failed_batch_ids": [],
            "numbered_label_preservation_rate": 1.0,
            "batches": {"0": {"unit_ids": ["0"], "changes": [], "warnings": [], "fallbacks": [], "errors": []}},
        }
        save_stage_json(context.output_dir, "corrected_text_dict.json", corrected_text_dict, "Corrected text dict")
        save_stage_json(context.output_dir, "corrected_text.json", source_text, "Corrected text")
        save_stage_json(context.output_dir, "correct_text_report.json", report, "Correct text report")
        state["chopped_text_dict"] = corrected_text_dict
        state["corrected_text"] = source_text
        state["correct_text_report"] = report
        return state

    source_units = build_structure_preserving_units(source_text)
    batches = _batch_units(source_units)

    max_workers = max(1, min(context.num_threads, len(batches))) if batches else 1
    print(
        f"[correct_text] Processing {len(source_units)} unit(s) in "
        f"{len(batches)} batch(es) with {max_workers} worker(s)",
        flush=True,
    )
    tasks = _build_batch_tasks(source_units, batches)
    results, failure_report, run_dir = run_recoverable_task(
        context,
        stage_name=STAGE_NAME,
        input_dict=tasks,
        task_runner=lambda index_dict, checkpoint_dir: _run_batch_tasks(context, index_dict, checkpoint_dir),
    )
    state, _ = _finalize_outputs(
        context,
        state,
        source_units,
        batches,
        results,
        run_dir=run_dir,
        attempts=failure_report.get("attempt_rounds") or 1,
    )
    return state


def rerun_failed_tasks(context, state, max_rounds=2):
    with open(context.file_path, "r", encoding="utf-8") as source_file:
        source_text = source_file.read()
    source_units = build_structure_preserving_units(source_text)
    batches = _batch_units(source_units)
    results, failure_report, run_dir = rerun_unresolved_task_report(
        context,
        stage_name=STAGE_NAME,
        task_runner=lambda index_dict, checkpoint_dir: _run_batch_tasks(context, index_dict, checkpoint_dir),
        max_rounds=max_rounds,
    )
    if failure_report.get("status") != "resolved":
        state["correct_text_stage_run"] = failure_report
        return state, failure_report
    state, final_report = _finalize_outputs(
        context,
        state,
        source_units,
        batches,
        results,
        run_dir=run_dir,
        attempts=failure_report.get("attempt_rounds") or 1,
    )
    return state, final_report
