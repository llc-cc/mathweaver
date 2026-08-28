import copy

from ...common.io import save_stage_json
from ...common.llm_task import run_multiprocess_task
from ...common.tex import is_tex_source_format
from ...common.node import (
    build_subnode_specs_from_conclusions,
    is_relation_statement_node_type,
    merge_node_with_source_envelope,
    normalize_node_type,
    normalize_node_types_in_tree,
    split_iff_components,
)
from ...common.stage_recovery import (
    latest_unresolved_failure_report as _latest_unresolved_failure_report,
    rerun_unresolved_task_report,
    run_recoverable_task,
    write_failure_report,
)
from .templates import (
    SPLIT_SUBNODE_FIELDS,
    correction_prompt04,
    data_template04,
    prompt_template04,
    validation04,
)

STAGE_NAME = "split_nodes"

def _sort_key(value):
    try:
        return (0, int(str(value)))
    except (TypeError, ValueError):
        return (1, str(value))


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _string_list(value):
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _copy_list(value):
    return copy.deepcopy(value) if isinstance(value, list) else []


def _is_relation_statement_block(block):
    if not isinstance(block, dict):
        return False
    return is_relation_statement_node_type(block.get("node_type"))


def _original_content(block):
    if not isinstance(block, dict):
        return ""
    content = block.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        return _text(content.get("original_form")) or _text(content.get("content"))
    remark = block.get("remark")
    if isinstance(remark, dict):
        return _text(remark.get("original_form"))
    return ""


def _unwrap_split_result(raw):
    if not isinstance(raw, dict):
        return {}
    if isinstance(raw.get("sub_nodes"), list):
        return raw
    for _, value in sorted(raw.items(), key=lambda item: _sort_key(item[0])):
        if not isinstance(value, dict):
            continue
        unwrapped = _unwrap_split_result(value)
        if unwrapped:
            return unwrapped
    return {}


def _get_split_for_key(split_result_dict, key):
    if not isinstance(split_result_dict, dict):
        return {}
    if key in split_result_dict:
        return split_result_dict.get(key) or {}
    str_key = str(key)
    if str_key in split_result_dict:
        return split_result_dict.get(str_key) or {}
    try:
        int_key = int(str_key)
    except (TypeError, ValueError):
        return {}
    return split_result_dict.get(int_key) or {}


def _normalize_subnode(raw_child, parent_block, index, original_form):
    raw_child = raw_child if isinstance(raw_child, dict) else {}
    parent_label = _text(parent_block.get("label"))
    parent_node_type = _text(parent_block.get("node_type"))
    parent_proof = parent_block.get("proof", "")

    conclusion = _text(raw_child.get("conclusion"))
    content = (
        _text(raw_child.get("content"))
        or _text(raw_child.get("statement"))
        or conclusion
        or original_form
    )
    if not conclusion:
        conclusion = content

    label_suffix = _text(raw_child.get("label_suffix"))
    label = ""
    if parent_label and label_suffix:
        label = f"{parent_label}.{label_suffix}"
    else:
        label = parent_label

    return {
        "index": index,
        "node_type": normalize_node_type(parent_node_type),
        "content": content,
        "proof": parent_proof,
        "label": label,
        "parent_label": parent_label,
        "label_suffix": label_suffix,
        "kind": _text(raw_child.get("kind")) or "conclusion",
        "statement_form": _text(raw_child.get("statement_form")),
        "source_conclusion": _text(raw_child.get("source_conclusion")) or conclusion,
        "equivalence_components": _string_list(raw_child.get("equivalence_components")),
        "applicable_context": _text(raw_child.get("applicable_context")),
        "applicable_conditions_text": _string_list(raw_child.get("applicable_conditions_text")),
        "conclusion": conclusion,
    }


def _make_unsplit_child(parent_block, original_form):
    return {
        "index": 1,
        "node_type": normalize_node_type(_text(parent_block.get("node_type"))),
        "content": original_form,
        "proof": parent_block.get("proof", "") if isinstance(parent_block.get("proof"), str) else "",
        "label": _text(parent_block.get("label")),
        "parent_label": _text(parent_block.get("label")),
        "label_suffix": "",
        "kind": "unsplit",
        "statement_form": _text(parent_block.get("statement_form")),
        "source_conclusion": original_form,
        "equivalence_components": [],
        "applicable_context": "",
        "applicable_conditions_text": [],
        "conclusion": original_form,
    }


def _fallback_subnodes(parent_block, original_form):
    return [_normalize_subnode(_make_unsplit_child(parent_block, original_form), parent_block, 1, original_form)]


def _normalize_subnodes(raw_children, parent_block, original_form):
    children = []
    if isinstance(raw_children, list):
        for raw_child in raw_children:
            child = _normalize_subnode(raw_child, parent_block, len(children) + 1, original_form)
            if child["conclusion"]:
                children.append(child)
    if not children:
        children = _fallback_subnodes(parent_block, original_form)
    children = _expand_single_equivalence_child(children, parent_block, original_form)
    return children


def _expand_single_equivalence_child(children, parent_block, original_form):
    if len(children) != 1:
        return children

    child = children[0]
    candidate = (
        _text(child.get("conclusion"))
        or _text(child.get("source_conclusion"))
        or _text(child.get("content"))
        or original_form
    )
    child_form = _text(child.get("statement_form")).lower()
    child_kind = _text(child.get("kind")).lower()
    if (
        child_form != "equivalence"
        and not child_kind.startswith("iff_")
        and len(split_iff_components(candidate)) < 2
    ):
        return children

    generated_specs = build_subnode_specs_from_conclusions([candidate])
    if len(generated_specs) <= 1:
        return children

    expanded = []
    for spec in generated_specs:
        raw_child = {
            "node_type": child.get("node_type") or parent_block.get("node_type"),
            "content": spec.get("content") or spec.get("conclusion"),
            "proof": child.get("proof"),
            "label": child.get("label"),
            "parent_label": child.get("parent_label"),
            "label_suffix": child.get("label_suffix"),
            "kind": spec.get("kind"),
            "statement_form": spec.get("statement_form"),
            "source_conclusion": spec.get("source_conclusion"),
            "equivalence_components": spec.get("equivalence_components"),
            "applicable_context": child.get("applicable_context"),
            "applicable_conditions_text": child.get("applicable_conditions_text"),
            "conclusion": spec.get("conclusion"),
        }
        expanded.append(_normalize_subnode(raw_child, parent_block, len(expanded) + 1, original_form))
    return expanded


def _subnode_specs_from_children(children):
    specs = []
    for child in children:
        if not isinstance(child, dict):
            continue
        conclusion = _text(child.get("conclusion"))
        if not conclusion:
            continue
        specs.append(
            {
                "index": len(specs) + 1,
                "kind": _text(child.get("kind")) or "conclusion",
                "statement_form": _text(child.get("statement_form")),
                "source_conclusion": _text(child.get("source_conclusion")) or conclusion,
                "equivalence_components": _copy_list(child.get("equivalence_components")),
                "applicable_context": _text(child.get("applicable_context")),
                "applicable_conditions_text": _copy_list(child.get("applicable_conditions_text")),
                "label_suffix": _text(child.get("label_suffix")),
                "content": _text(child.get("content")) or conclusion,
                "conclusion": conclusion,
            }
        )
    return specs


def _apply_split_to_block(block, split_result, task_key=None):
    block_copy = copy.deepcopy(block)
    block_copy["node_type"] = normalize_node_type(block_copy.get("node_type"))
    original_form = _original_content(block_copy)
    raw_split_result = _unwrap_split_result(split_result)
    children = _normalize_subnodes(raw_split_result.get("sub_nodes"), block_copy, original_form)
    if len(children) == 1:
        child = copy.deepcopy(children[0])
        child["index"] = 1
        child["kind"] = "unsplit"
        child["label_suffix"] = ""
        child["content"] = _text(child.get("content")) or original_form
        child["source_conclusion"] = _text(child.get("source_conclusion")) or _text(child.get("conclusion")) or original_form
        child["conclusion"] = _text(child.get("conclusion")) or child["content"] or original_form
        children = [child]
    subnode_specs = _subnode_specs_from_children(children)

    remark = block_copy.get("remark")
    if not isinstance(remark, dict):
        remark = {}
    for stale_key in (
        "formal_statement_core",
        "global_context",
        "local_conclusions",
        "narrative_prefix",
        "proof_or_derivation_hint",
        "excluded_from_formalization",
    ):
        remark.pop(stale_key, None)
    remark["original_form"] = original_form
    derived = {
        "remark": remark,
        "subnode_specs": subnode_specs,
    }
    if len(children) > 1:
        derived["sub_nodes"] = children
        derived["subnode_display"] = "expandable"
        derived["subnode_count"] = len(children)

    merged, audit = merge_node_with_source_envelope(
        block_copy,
        derived,
        stage_name=STAGE_NAME,
        allowed_fields={
            "remark",
            "subnode_specs",
            "sub_nodes",
            "subnode_display",
            "subnode_count",
        },
    )
    if _is_relation_statement_block(block_copy) and not raw_split_result:
        statuses = dict(merged.get("_derivation_status") or {})
        statuses[STAGE_NAME] = {
            "status": "degraded",
            "reason": "unresolved_model_task",
            "task_key": str(task_key) if task_key is not None else None,
        }
        merged["_derivation_status"] = statuses
    ignored_fields = {
        str(field_name)
        for field_name in raw_split_result
        if field_name != "sub_nodes"
    }
    for child in raw_split_result.get("sub_nodes") or []:
        if not isinstance(child, dict):
            continue
        ignored_fields.update(
            str(field_name)
            for field_name in child
            if field_name not in SPLIT_SUBNODE_FIELDS
        )
    if ignored_fields:
        audit["ignored_fields"] = sorted(ignored_fields)
    audits = list(merged.get("_source_merge_audits") or [])
    audits.append(audit)
    merged["_source_merge_audits"] = audits
    merged.pop("split_required", None)
    merged.pop("split_reason", None)
    return merged


def apply_node_split(statement_dict, split_result_dict):
    result = {}
    new_id = 0

    for key in sorted((statement_dict or {}).keys(), key=_sort_key):
        wrapper = (statement_dict or {}).get(key)
        if not isinstance(wrapper, dict):
            continue
        block = wrapper.get("pos1")
        if not isinstance(block, dict):
            continue

        result[new_id] = {
            "pos1": _apply_split_to_block(
                block,
                _get_split_for_key(split_result_dict, key),
                task_key=key,
            ),
            "_orig_key": wrapper.get("_orig_key", key),
        }
        new_id += 1

    return result


def apply_discourse_decomposition(statement_dict, decomposition_dict):
    return apply_node_split(statement_dict, decomposition_dict)


def _make_passthrough_split_result(block):
    original_form = _original_content(block)
    return {
        "sub_nodes": [_make_unsplit_child(block, original_form)],
    }


def _build_split_input_dict(statement_dict):
    split_input = {}
    passthrough_results = {}

    for key in sorted((statement_dict or {}).keys(), key=_sort_key):
        wrapper = (statement_dict or {}).get(key)
        if not isinstance(wrapper, dict):
            continue
        block = wrapper.get("pos1")
        if not isinstance(block, dict):
            continue
        if _is_relation_statement_block(block):
            split_input[key] = wrapper
        else:
            passthrough_results[key] = _make_passthrough_split_result(block)

    return split_input, passthrough_results


def _run_split_tasks(context, index_dict, checkpoint_dir):
    return run_multiprocess_task(
        llm=context.llm,
        parse_method=context.parser.parse_dict,
        data_template=data_template04,
        prompt_template=prompt_template04,
        correction_template=correction_prompt04,
        validator=validation04,
        index_dict=index_dict,
        num_threads=context.num_threads,
        checkpoint=context.checkpoint,
        checkpoint_dir=checkpoint_dir,
    )


def latest_unresolved_failure_report(context):
    return _latest_unresolved_failure_report(context, STAGE_NAME)


def _finalize_outputs(context, state, llm_split_dict, passthrough_results, *, expected_keys=None, run_dir=None, attempts=1):
    node_split_dict = {}
    node_split_dict.update(passthrough_results)
    node_split_dict.update(llm_split_dict)
    normalize_node_types_in_tree(node_split_dict)
    statement_without_title_dict = apply_node_split(
        state["unsplit_statement_dict"],
        node_split_dict,
    )
    normalize_node_types_in_tree(statement_without_title_dict)
    save_stage_json(
        context.output_dir,
        "node_split_dict.json",
        node_split_dict,
        "Node split dict",
    )
    save_stage_json(
        context.output_dir,
        "statement_without_title_dict.json",
        statement_without_title_dict,
        "Statement without title dict",
    )
    if run_dir is not None:
        write_failure_report(
            run_dir,
            run_dir.name,
            STAGE_NAME,
            [str(key) for key in (expected_keys or llm_split_dict.keys())],
            llm_split_dict,
            attempts=attempts,
            canonical_updated=True,
        )
    state["node_split_dict"] = node_split_dict
    state["statement_without_title_dict"] = statement_without_title_dict
    return state


def run(context, state):
    split_input_dict, passthrough_results = _build_split_input_dict(state["unsplit_statement_dict"])
    if is_tex_source_format(context):
        tex_passthrough = {
            key: _make_passthrough_split_result(wrapper.get("pos1"))
            for key, wrapper in (state.get("unsplit_statement_dict") or {}).items()
            if isinstance(wrapper, dict) and isinstance(wrapper.get("pos1"), dict)
        }
        return _finalize_outputs(context, state, {}, tex_passthrough)

    llm_split_dict = {}
    if split_input_dict:
        llm_split_dict, failure_report, run_dir = run_recoverable_task(
            context,
            stage_name=STAGE_NAME,
            input_dict=split_input_dict,
            task_runner=lambda index_dict, checkpoint_dir: _run_split_tasks(context, index_dict, checkpoint_dir),
        )
        if failure_report.get("status") != "resolved":
            state["split_nodes_stage_run"] = failure_report
            if getattr(context, "execution_mode", "pipeline") == "pipeline":
                return _finalize_outputs(
                    context,
                    state,
                    llm_split_dict,
                    passthrough_results,
                    expected_keys=split_input_dict.keys(),
                    run_dir=run_dir,
                    attempts=1,
                )
            return state
        return _finalize_outputs(
            context,
            state,
            llm_split_dict,
            passthrough_results,
            expected_keys=split_input_dict.keys(),
            run_dir=run_dir,
            attempts=1,
        )
    return _finalize_outputs(context, state, llm_split_dict, passthrough_results)


def rerun_failed_tasks(context, state, max_rounds=2):
    split_input_dict, passthrough_results = _build_split_input_dict(state["unsplit_statement_dict"])
    llm_split_dict, failure_report, run_dir = rerun_unresolved_task_report(
        context,
        stage_name=STAGE_NAME,
        task_runner=lambda index_dict, checkpoint_dir: _run_split_tasks(context, index_dict, checkpoint_dir),
        max_rounds=max_rounds,
    )
    if failure_report.get("status") != "resolved":
        state["split_nodes_stage_run"] = failure_report
        return state, failure_report
    state = _finalize_outputs(
        context,
        state,
        llm_split_dict,
        passthrough_results,
        expected_keys=split_input_dict.keys(),
        run_dir=run_dir,
        attempts=failure_report.get("attempt_rounds") or 1,
    )
    return state, {**failure_report, "status": "resolved", "canonical_updated": True}
