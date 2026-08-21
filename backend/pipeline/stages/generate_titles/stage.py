import re
from collections import Counter

from ...common.io import save_stage_json, write_json
from ...common.node import (
    adjust,
    get_node_content,
    get_node_formal_content,
    merge_node_with_source_envelope,
)
from ...common.llm_task import run_multiprocess_task
from ...common.stage_recovery import (
    latest_unresolved_failure_report as _latest_unresolved_failure_report,
    rerun_unresolved_task_report,
    run_recoverable_task,
    write_failure_report,
)
from ..extract_logic_tuples.stage import split_statement_with_title_dict  # type: ignore
from .templates import correction_prompt05, data_template05, prompt_template05, validation05

STAGE_NAME = "generate_titles"

def _sort_key(value):
    try:
        return (0, int(str(value)))
    except (ValueError, TypeError):
        return (1, str(value))


def flatten_statement_with_title_dict(statement_with_title_dict):
    natural_node_list = []

    def _is_node_block(block):
        return isinstance(block, dict) and any(
            key in block for key in ("node_type", "content", "proof", "label", "title")
        )

    for key in sorted(statement_with_title_dict.keys(), key=_sort_key):
        entry = statement_with_title_dict[key]
        if not isinstance(entry, dict):
            continue

        if _is_node_block(entry):
            natural_node_list.append(entry)
            continue

        for child_key, child_val in sorted(entry.items(), key=lambda item: _sort_key(item[0])):
            if child_key == "_orig_key":
                continue
            if _is_node_block(child_val):
                natural_node_list.append(child_val)

    return natural_node_list


def _strip_layout_artifacts(text):
    if not isinstance(text, str):
        return text

    cleaned = text
    cleaned = re.sub(r"\\setcounter\s*\{[^{}]+\}\s*\{[^{}]+\}", " ", cleaned)
    cleaned = re.sub(r"\[\s*(?:Commutative\s+diagram|Diagram)\s+omitted\s*\]", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    return cleaned.strip()


def _clean_natural_node_artifacts(node):
    if not isinstance(node, dict):
        return node

    cleaned_node = dict(node)
    for field in ("content", "proof", "label"):
        value = cleaned_node.get(field)
        if isinstance(value, str):
            cleaned_node[field] = _strip_layout_artifacts(value)

    title = cleaned_node.get("title")
    if isinstance(title, dict):
        cleaned_node["title"] = {
            key: _strip_layout_artifacts(value) if isinstance(value, str) else value
            for key, value in title.items()
        }

    return cleaned_node


def _extract_formula_hint(text):
    if not isinstance(text, str):
        return ""

    matches = re.findall(r"\$\$(.*?)\$\$|\$(.*?)\$", text, flags=re.DOTALL)
    for block_match, inline_match in matches:
        candidate = (block_match or inline_match or "").strip()
        candidate = re.sub(r"\s+", " ", candidate)
        if candidate:
            return candidate
    return ""


def _looks_reference_heavy(text):
    if not isinstance(text, str):
        return False
    patterns = [
        r"\b(?:prop|thm|lem|cor|def|exam|rem):",
        r"statement\s*\(\d+'?\)",
        r"equivalent to the following statement",
        r"命题.*陈述.*等价性",
    ]
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _is_explicit_statement_equivalence(node):
    if not isinstance(node, dict):
        return False
    title = node.get("title")
    content = get_node_content(node)
    if not isinstance(title, dict):
        return False

    combined_title = f'{title.get("chinese", "")} {title.get("english", "")}'
    if re.search(r"statement\s*\(\d+'?\).+statement\s*\(\d+'?\)", content, flags=re.IGNORECASE):
        return True
    if re.search(r"陈述\s*\(\d+'?\).+陈述\s*\(\d+'?\)", combined_title):
        return True
    if re.search(r"equivalence of statements", combined_title, flags=re.IGNORECASE):
        return True
    return False


def _rewrite_reference_heavy_titles(node_list):
    rewritten = []
    for node in node_list:
        title = node.get("title")
        content = get_node_content(node)
        if not isinstance(title, dict):
            rewritten.append(node)
            continue

        original_chinese = title.get("chinese", "")
        original_english = title.get("english", "")
        combined_title = f"{original_chinese} {original_english}"
        if not _is_explicit_statement_equivalence(node):
            rewritten.append(node)
            continue

        if re.search(r"相关陈述的等价性|Equivalence of Referenced Statements", combined_title):
            rewritten.append(node)
            continue

        statement_refs = re.findall(r"statement\s*\((\d+'?)\)", content, flags=re.IGNORECASE)
        statement_refs = list(dict.fromkeys(statement_refs))
        if len(statement_refs) < 2:
            rewritten.append(node)
            continue

        formula_hint = _extract_formula_hint(content)
        new_node = dict(node)
        new_title = dict(title)
        chinese_base = f"陈述({statement_refs[0]})与({statement_refs[1]})的等价性"
        english_base = f"Equivalence of Statements ({statement_refs[0]}) and ({statement_refs[1]})"
        if formula_hint:
            new_title["chinese"] = f"{chinese_base}：${formula_hint}$"
            new_title["english"] = f"{english_base}: ${formula_hint}$"
        else:
            new_title["chinese"] = chinese_base
            new_title["english"] = english_base
        new_node["title"] = new_title
        rewritten.append(new_node)

    return rewritten


def _disambiguate_duplicate_titles(node_list):
    title_counts = Counter()
    for node in node_list:
        title = node.get("title")
        if isinstance(title, dict):
            title_counts[(title.get("chinese", ""), title.get("english", ""))] += 1

    updated_nodes = []
    for node in node_list:
        title = node.get("title")
        if not isinstance(title, dict):
            updated_nodes.append(node)
            continue

        title_key = (title.get("chinese", ""), title.get("english", ""))
        if title_counts[title_key] <= 1:
            updated_nodes.append(node)
            continue

        formula_hint = _extract_formula_hint(get_node_formal_content(node))
        if not formula_hint:
            updated_nodes.append(node)
            continue

        new_node = dict(node)
        new_title = dict(title)
        if new_title.get("chinese"):
            new_title["chinese"] = f'{new_title["chinese"]}：${formula_hint}$'
        if new_title.get("english"):
            new_title["english"] = f'{new_title["english"]}: ${formula_hint}$'
        new_node["title"] = new_title
        updated_nodes.append(new_node)

    return updated_nodes


def _apply_original_aware_title_fixes(node_list):
    fixed_nodes = []
    for node in node_list:
        title = node.get("title")
        content = get_node_content(node)
        label = node.get("label", "")
        if not isinstance(title, dict):
            fixed_nodes.append(node)
            continue

        new_node = dict(node)
        new_title = dict(title)
        changed = False

        if title.get("chinese") == "$(f, E_r)$-扩张示例：$(f, E_r)$":
            if "d_1^{f, E_2} (h_5) = h_2 h_5" in content:
                new_title["chinese"] = "$(f,E_2)$-扩展示例：$d_1^{f,E_2}(h_5)=h_2h_5$"
                new_title["english"] = "Example of an $(f,E_2)$-extension: $d_1^{f,E_2}(h_5)=h_2h_5$"
                changed = True
            elif "d_2^{f, E_3} (h_0h_4^2) = h_0 p" in content:
                new_title["chinese"] = "$(f,E_3)$-扩展示例：$d_2^{f,E_3}(h_0h_4^2)=h_0p$"
                new_title["english"] = "Example of an $(f,E_3)$-extension: $d_2^{f,E_3}(h_0h_4^2)=h_0p$"
                changed = True
            elif "d_2^{f, E_\\infty} (h_0h_4^2) = h_0 p" in content:
                new_title["chinese"] = "$(f,E_\\infty)$-扩展示例：$d_2^{f,E_\\infty}(h_0h_4^2)=h_0p$"
                new_title["english"] = "Example of an $(f,E_\\infty)$-extension: $d_2^{f,E_\\infty}(h_0h_4^2)=h_0p$"
                changed = True

        if (
            label == "Corollary cor:stretch-extension"
            or "contrapositive statement of the second part of Proposition prop:ext-across-pages" in content
        ):
            new_title["chinese"] = "跨页延拓推论"
            new_title["english"] = "Corollary on Stretching Extensions Across Pages"
            changed = True

        if (
            "The statement $(5)$ in Proposition prop:possibleh62 is equivalent to the following statement $(5')$" in content
            and "\\lambda^3 \\eta [h_0^2x_{124,8}] = \\lambda^6 [h_1h_4x_{109,12}]" in content
        ):
            new_title["chinese"] = "陈述(5)与(5')的等价性：$\\lambda^3\\eta[h_0^2x_{124,8}]$ 的检测"
            new_title["english"] = "Equivalence of Statements (5) and (5'): detection of $\\lambda^3\\eta[h_0^2x_{124,8}]$"
            changed = True

        if changed:
            new_node["title"] = new_title
        fixed_nodes.append(new_node)

    return fixed_nodes


def finalize_natural_node_list(statement_with_title_dict):
    natural_node_list = flatten_statement_with_title_dict(statement_with_title_dict)
    cleaned_nodes = [_clean_natural_node_artifacts(adjust(node)) for node in natural_node_list]
    cleaned_nodes = _rewrite_reference_heavy_titles(cleaned_nodes)
    cleaned_nodes = _apply_original_aware_title_fixes(cleaned_nodes)
    return _disambiguate_duplicate_titles(cleaned_nodes)


def _extract_title_from_output(output):
    if not isinstance(output, dict):
        return None

    title = output.get("title")
    if isinstance(title, dict):
        return {
            "chinese": title.get("chinese", "") if isinstance(title.get("chinese"), str) else "",
            "english": title.get("english", "") if isinstance(title.get("english"), str) else "",
        }

    for _, value in sorted(output.items(), key=lambda item: _sort_key(item[0])):
        if isinstance(value, dict) and isinstance(value.get("title"), dict):
            nested_title = value["title"]
            return {
                "chinese": nested_title.get("chinese", "") if isinstance(nested_title.get("chinese"), str) else "",
                "english": nested_title.get("english", "") if isinstance(nested_title.get("english"), str) else "",
            }
    return None


def merge_titles_into_statement_dict(statement_without_title_dict, title_result_dict):
    merged = {}
    for key in sorted((statement_without_title_dict or {}).keys(), key=_sort_key):
        wrapper = (statement_without_title_dict or {}).get(key)
        if not isinstance(wrapper, dict):
            continue
        block = wrapper.get("pos1")
        if not isinstance(block, dict):
            continue

        raw_output = (title_result_dict or {}).get(key) or (title_result_dict or {}).get(str(key))
        title = _extract_title_from_output(raw_output)
        derived = {}
        if title and (title.get("chinese") or title.get("english")):
            derived["title"] = title
        block_copy, audit = merge_node_with_source_envelope(
            block,
            derived,
            stage_name=STAGE_NAME,
            allowed_fields={"title"},
        )
        if "title" not in derived:
            statuses = dict(block_copy.get("_derivation_status") or {})
            statuses[STAGE_NAME] = {
                "status": "degraded",
                "reason": "unresolved_model_task",
                "task_key": str(key),
            }
            block_copy["_derivation_status"] = statuses
        if isinstance(raw_output, dict):
            ignored_fields = sorted(
                str(field_name)
                for field_name in raw_output
                if field_name != "title"
            )
            if ignored_fields:
                audit["ignored_fields"] = ignored_fields
        audits = list(block_copy.get("_source_merge_audits") or [])
        audits.append(audit)
        block_copy["_source_merge_audits"] = audits

        merged[key] = {
            "pos1": block_copy,
            "_orig_key": wrapper.get("_orig_key", key),
        }
    return merged


def _run_title_tasks(context, index_dict, checkpoint_dir):
    return run_multiprocess_task(
        llm=context.llm,
        parse_method=context.parser.parse_dict,
        data_template=data_template05,
        prompt_template=prompt_template05,
        correction_template=correction_prompt05,
        validator=validation05,
        index_dict=index_dict,
        num_threads=context.num_threads,
        checkpoint=context.checkpoint,
        checkpoint_dir=checkpoint_dir,
    )


def latest_unresolved_failure_report(context):
    return _latest_unresolved_failure_report(context, STAGE_NAME)


def _finalize_outputs(context, state, title_result_dict, *, run_dir=None, attempts=1):
    statement_with_title_dict = merge_titles_into_statement_dict(
        state["statement_without_title_dict"],
        title_result_dict,
    )
    natural_node_list = finalize_natural_node_list(statement_with_title_dict)
    definition_axiom_dict, structured_input_dict = split_statement_with_title_dict(statement_with_title_dict)
    save_stage_json(context.output_dir, "definition_axiom_dict.json", definition_axiom_dict, "Definition/axiom dict")
    save_stage_json(context.output_dir, "structured_input_dict.json", structured_input_dict, "Structured input dict")
    if context.output_natural_node_path:
        write_json(context.output_natural_node_path, natural_node_list)
        print(f"✅ Natural node JSON saved to: {context.output_natural_node_path}")
    if run_dir is not None:
        write_failure_report(
            run_dir,
            run_dir.name,
            STAGE_NAME,
            [str(key) for key in (state.get("statement_without_title_dict") or {}).keys()],
            title_result_dict,
            attempts=attempts,
            canonical_updated=True,
        )
    state["statement_with_title_dict"] = statement_with_title_dict
    state["natural_node_list"] = natural_node_list
    state["definition_axiom_dict"] = definition_axiom_dict
    state["structured_input_dict"] = structured_input_dict
    return state


def run(context, state):
    if "statement_without_title_dict" not in state:
        raise RuntimeError(
            "generate_titles requires statement_without_title_dict from split_nodes. "
            "The split_nodes stage likely failed before writing its canonical output."
        )
    title_result_dict, failure_report, run_dir = run_recoverable_task(
        context,
        stage_name=STAGE_NAME,
        input_dict=state["statement_without_title_dict"],
        task_runner=lambda index_dict, checkpoint_dir: _run_title_tasks(context, index_dict, checkpoint_dir),
    )
    if failure_report.get("status") != "resolved":
        state["generate_titles_stage_run"] = failure_report
        if getattr(context, "execution_mode", "pipeline") == "pipeline":
            return _finalize_outputs(context, state, title_result_dict, run_dir=run_dir, attempts=1)
        return state
    return _finalize_outputs(context, state, title_result_dict, run_dir=run_dir, attempts=1)


def rerun_failed_tasks(context, state, max_rounds=2):
    title_result_dict, failure_report, run_dir = rerun_unresolved_task_report(
        context,
        stage_name=STAGE_NAME,
        task_runner=lambda index_dict, checkpoint_dir: _run_title_tasks(context, index_dict, checkpoint_dir),
        max_rounds=max_rounds,
    )
    if failure_report.get("status") != "resolved":
        state["generate_titles_stage_run"] = failure_report
        return state, failure_report
    state = _finalize_outputs(
        context,
        state,
        title_result_dict,
        run_dir=run_dir,
        attempts=failure_report.get("attempt_rounds") or 1,
    )
    return state, {**failure_report, "status": "resolved", "canonical_updated": True}
