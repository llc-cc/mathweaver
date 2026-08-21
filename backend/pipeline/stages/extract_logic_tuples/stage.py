import re

from ...common.io import save_stage_json
from ...common.llm_task import run_multiprocess_task
from ...common.node import (
    adjust,
    attach_internal_subnodes_to_dict,
    get_node_content,
    get_node_label,
    get_node_node_type,
    is_logic_tuple_node_type,
    merge_node_with_source_envelope,
    normalize_node_types_in_tree,
)
from ...common.stage_recovery import (
    latest_unresolved_failure_report as _latest_unresolved_failure_report,
    rerun_unresolved_task_report,
    run_recoverable_task,
    write_failure_report,
)
from ...common.formalization_guards import attach_formalization_guidance
from .templates import (
    DERIVED_LOGIC_FIELDS,
    correction_prompt06,
    data_template06,
    prompt_template06,
    validation06,
)


STAGE_NAME = "extract_logic_tuples"

_REPAIRABLE_STATEMENT_FORMS = frozenset({"", "other", "unknown"})
_EQUIVALENCE_PATTERN = re.compile(
    r"""
    当且仅当
    |充分必要条件
    |等价于
    |\biff\b
    |\bif\s+and\s+only\s+if\b
    |\\iff\b
    |\\Leftrightarrow\b
    |\\Longleftrightarrow\b
    |⇔
    """,
    re.IGNORECASE | re.VERBOSE,
)
_IMPLICATION_PATTERN = re.compile(
    r"""
    如果.+?则
    |若.+?则
    |只要.+?就
    |当.+?时
    |蕴含
    |\bimplies\b
    |\\implies\b
    |\\Rightarrow\b
    |\\Longrightarrow\b
    |⇒
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)


def latest_unresolved_failure_report(context):
    return _latest_unresolved_failure_report(context, STAGE_NAME)


def split_statement_with_title_dict(statement_with_title_dict):
    definition_axiom_dict = {}
    structured_input_dict = {}

    def _sort_key(x):
        try:
            return (0, int(str(x)))
        except (ValueError, TypeError):
            return (1, str(x))

    skipped_count = 0

    for key in sorted(statement_with_title_dict.keys(), key=_sort_key):
        entry = statement_with_title_dict[key]
        if not isinstance(entry, dict):
            skipped_count += 1
            continue

        orig_key = entry.get("_orig_key", key)
        block = None

        if "node_type" in entry:
            block = entry
        else:
            candidates = []
            for child_key, child_val in sorted(entry.items(), key=lambda x: _sort_key(x[0])):
                if child_key == "_orig_key":
                    continue
                if isinstance(child_val, dict) and "node_type" in child_val:
                    candidates.append(child_val)
            if candidates:
                block = candidates[0]

        if not isinstance(block, dict):
            skipped_count += 1
            continue

        node_type = get_node_node_type(block)
        block_copy = dict(block)
        block_copy["_orig_key"] = orig_key

        if is_logic_tuple_node_type(node_type):
            structured_input_dict[key] = {"pos1": block_copy, "_orig_key": orig_key}
        else:
            definition_axiom_dict[key] = block_copy

    if skipped_count:
        print(f"Warning: split_statement_with_title_dict skipped {skipped_count} invalid entries")

    return definition_axiom_dict, structured_input_dict


def _text(value):
    return value if isinstance(value, str) else ""


def _list(value):
    return value if isinstance(value, list) else []


def _discourse_source(block):
    if not isinstance(block, dict):
        return {}
    content = block.get("content")
    if isinstance(content, dict):
        return content
    remark = block.get("remark")
    if isinstance(remark, dict):
        return remark
    return {}


def _first_text(*values):
    for value in values:
        text = _text(value)
        if text.strip():
            return text
    return ""


def _texts_from_specs(specs):
    texts = []
    for spec in _list(specs):
        if not isinstance(spec, dict):
            continue
        text = _first_text(
            spec.get("content"),
            spec.get("source_conclusion"),
            spec.get("conclusion"),
        )
        if text:
            texts.append(text)
    return texts


def _texts_from_subnodes(subnodes):
    parent_original_forms = []
    child_texts = []
    for subnode in _list(subnodes):
        if not isinstance(subnode, dict):
            continue
        remark = subnode.get("remark")
        if isinstance(remark, dict):
            parent_text = _text(remark.get("parent_original_form"))
            if parent_text.strip():
                parent_original_forms.append(parent_text)
            child_text = _first_text(
                remark.get("original_form"),
                remark.get("content"),
                remark.get("text"),
            )
            if child_text.strip():
                child_texts.append(child_text)
        child_text = _first_text(
            subnode.get("content"),
            subnode.get("source_conclusion"),
            subnode.get("conclusion"),
        )
        if child_text.strip():
            child_texts.append(child_text)
    if parent_original_forms:
        return [parent_original_forms[0]]
    return child_texts


def _join_texts(texts):
    unique = []
    seen = set()
    for text in texts:
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(text)
    return "\n".join(unique)


def logic_tuple_original_form_from_block(block):
    if not isinstance(block, dict):
        return ""

    remark = block.get("remark")
    remark = remark if isinstance(remark, dict) else {}
    content = block.get("content")
    content_dict = content if isinstance(content, dict) else {}

    direct_text = _first_text(
        remark.get("original_form"),
        content if isinstance(content, str) else "",
        content_dict.get("original_form"),
        content_dict.get("formal_statement_core"),
        content_dict.get("content"),
        content_dict.get("text"),
        remark.get("content"),
        remark.get("text"),
        get_node_content(block),
    )
    if direct_text:
        return direct_text

    spec_text = _join_texts(_texts_from_specs(block.get("subnode_specs")))
    if spec_text:
        return spec_text

    subnode_text = _join_texts(_texts_from_subnodes(block.get("sub_nodes")))
    if subnode_text:
        return subnode_text

    return ""


def _has_recoverable_text_evidence(block):
    if not isinstance(block, dict):
        return False
    content = block.get("content")
    remark = block.get("remark")
    if isinstance(content, str) and content.strip():
        return True
    if isinstance(content, dict) and any(_text(content.get(field)).strip() for field in ("original_form", "formal_statement_core", "content", "text")):
        return True
    if isinstance(remark, dict) and any(_text(remark.get(field)).strip() for field in ("original_form", "content", "text")):
        return True
    return bool(_texts_from_specs(block.get("subnode_specs")) or _texts_from_subnodes(block.get("sub_nodes")))


def split_payload_from_block(block):
    source = _discourse_source(block)
    original_form = logic_tuple_original_form_from_block(block)
    return {
        "original_form": original_form,
        "source_original_form": _text(block.get("source_original_form")),
        "subnode_specs": _list(block.get("subnode_specs")) or _list(source.get("subnode_specs")),
    }


def logic_tuple_input_quality_issues(logic_tuple_input_dict, structured_input_dict):
    issues = []
    for key, wrapper in (logic_tuple_input_dict or {}).items():
        pos1 = wrapper.get("pos1") if isinstance(wrapper, dict) else {}
        original_form = _text(pos1.get("original_form")) if isinstance(pos1, dict) else ""
        if original_form.strip():
            continue
        source_wrapper = None
        if isinstance(structured_input_dict, dict):
            source_wrapper = structured_input_dict.get(key) or structured_input_dict.get(str(key))
            if source_wrapper is None:
                try:
                    source_wrapper = structured_input_dict.get(int(str(key)))
                except (TypeError, ValueError):
                    source_wrapper = None
        block = source_wrapper.get("pos1") if isinstance(source_wrapper, dict) else None
        if _has_recoverable_text_evidence(block):
            issues.append(
                {
                    "task_key": str(key),
                    "issue_type": "empty_original_form_with_source_evidence",
                    "node_type": pos1.get("node_type", "") if isinstance(pos1, dict) else "",
                    "label": pos1.get("label", "") if isinstance(pos1, dict) else "",
                }
            )
    return issues


def build_logic_tuple_input_dict(structured_input_dict):
    tuple_input = {}
    for key, wrapper in (structured_input_dict or {}).items():
        if not isinstance(wrapper, dict):
            continue
        block = wrapper.get("pos1")
        if not isinstance(block, dict):
            continue
        node_type = get_node_node_type(block)
        if not is_logic_tuple_node_type(node_type):
            raise ValueError(
                "extract_logic_tuples accepts only theorem/relation, example, "
                "or exercise "
                f"nodes; task_key={key!r}, node_type={node_type!r}, "
                f"label={get_node_label(block)!r}. Rerun from generate_titles."
            )
        split_payload = split_payload_from_block(block)
        tuple_input[key] = {
            "pos1": {
                "node_type": node_type,
                "title": block.get("title", {}),
                "original_form": split_payload["original_form"],
                "subnode_specs": split_payload["subnode_specs"],
                "proof": block.get("proof", ""),
                "label": block.get("label", ""),
            },
            "_orig_key": wrapper.get("_orig_key", key),
        }
    return tuple_input


def merge_mixed_node_dict(structured_node_dict, definition_axiom_dict):
    merged_items = []

    def _sort_key(x):
        try:
            return (0, int(str(x)))
        except (ValueError, TypeError):
            return (1, str(x))

    for _, block in structured_node_dict.items():
        if not isinstance(block, dict):
            continue
        reorder_id = block.get("_reorder_id")
        merged_items.append((reorder_id, block))

    for key, block in definition_axiom_dict.items():
        if not isinstance(block, dict):
            continue
        block_copy = dict(block)
        block_copy["_reorder_id"] = block_copy.get("_orig_key", key)
        merged_items.append((block_copy["_reorder_id"], block_copy))

    merged_items.sort(key=lambda item: _sort_key(item[0]))

    node_dict = {}
    for new_id, (_, block) in enumerate(merged_items):
        node_dict[new_id] = block
    return node_dict


def _repair_statement_form_for_block(block):
    if not isinstance(block, dict):
        return block

    repaired = dict(block)
    content = get_node_content(repaired)
    if not isinstance(content, str) or not content.strip():
        return repaired

    statement_form = str(repaired.get("statement_form") or "").strip().lower()
    if statement_form not in _REPAIRABLE_STATEMENT_FORMS:
        return repaired

    equivalence_match = _EQUIVALENCE_PATTERN.search(content)
    implication_match = None if equivalence_match else _IMPLICATION_PATTERN.search(content)
    match = equivalence_match or implication_match
    if match is None:
        return repaired

    repaired["statement_form_before_repair"] = statement_form
    repaired["statement_form"] = "equivalence" if equivalence_match else "implication"
    repaired["statement_form_repair"] = "explicit_marker_correction"
    repaired["statement_form_repair_evidence"] = match.group(0)

    return repaired


def repair_statement_forms(node_dict):
    repaired = {}
    for key, block in node_dict.items():
        repaired[key] = _repair_statement_form_for_block(block)
    return repaired


def _run_logic_tuple_tasks(context, index_dict, checkpoint_dir):
    if not index_dict:
        return {}
    return run_multiprocess_task(
            llm=context.llm,
            parse_method=context.parser.parse_dict,
            data_template=data_template06,
            prompt_template=prompt_template06,
            correction_template=correction_prompt06,
            validator=validation06,
            index_dict=index_dict,
            num_threads=context.num_threads,
            checkpoint=context.checkpoint,
            checkpoint_dir=str(checkpoint_dir),
        )


def _result_for_key(result_dict, key):
    if not isinstance(result_dict, dict):
        return None
    for candidate in (key, str(key)):
        if candidate in result_dict:
            return result_dict[candidate]
    try:
        return result_dict.get(int(str(key)))
    except (TypeError, ValueError):
        return None


def _merge_logic_tuple_results(structured_input_dict, result_dict):
    merged = {}
    for key, wrapper in (structured_input_dict or {}).items():
        if not isinstance(wrapper, dict):
            continue
        source_node = wrapper.get("pos1")
        if not isinstance(source_node, dict):
            continue

        raw_result = _result_for_key(result_dict, key)
        derived = raw_result if isinstance(raw_result, dict) else {}
        node, audit = merge_node_with_source_envelope(
            source_node,
            derived,
            stage_name=STAGE_NAME,
            allowed_fields=DERIVED_LOGIC_FIELDS,
        )
        if raw_result is None:
            statuses = dict(node.get("_derivation_status") or {})
            statuses[STAGE_NAME] = {
                "status": "degraded",
                "reason": "unresolved_model_task",
                "task_key": str(key),
            }
            node["_derivation_status"] = statuses
        audits = list(node.get("_source_merge_audits") or [])
        audits.append(audit)
        node["_source_merge_audits"] = audits
        node["_reorder_id"] = wrapper.get("_orig_key", key)
        merged[key] = node
    return merged


def finalize_complete_statement_dict(context, state, statement_dict):
    structured_input_dict = state["structured_input_dict"]
    structured_node_dict = _merge_logic_tuple_results(
        structured_input_dict,
        statement_dict,
    )
    node_dict = merge_mixed_node_dict(
        structured_node_dict,
        state["definition_axiom_dict"],
    )
    node_dict = repair_statement_forms(node_dict)
    node_dict = attach_internal_subnodes_to_dict(node_dict)
    normalize_node_types_in_tree(node_dict)
    save_stage_json(context.output_dir, "node_dict.json", node_dict, "Node dict")

    node_list = list(node_dict.values())
    print("\n【C-1顺序检查】")
    print(f"node_list长度: {len(node_list)}")
    if node_list:
        print(f"首个节点_reorder_id: {node_list[0].get('_reorder_id', 'N/A')}")
        print(f"末个节点_reorder_id: {node_list[-1].get('_reorder_id', 'N/A')}")
        reorder_ids = [n.get("_reorder_id", -1) for n in node_list]

        def _check_key(value):
            try:
                return (0, int(str(value)))
            except (TypeError, ValueError):
                return (1, str(value))

        is_ascending = all(_check_key(reorder_ids[i]) <= _check_key(reorder_ids[i + 1]) for i in range(len(reorder_ids) - 1))
        print(f"顺序递增检查: {'✅ 通过' if is_ascending else '❌ 失败'}")
    print()

    node_dict = {key: attach_formalization_guidance(adjust(node)) for key, node in node_dict.items()}

    state["statement_dict"] = structured_node_dict
    state["node_dict"] = node_dict
    state["node_list"] = list(node_dict.values())
    return state


def run(context, state):
    structured_input_dict = state["structured_input_dict"]
    logic_tuple_input_dict = build_logic_tuple_input_dict(structured_input_dict)
    save_stage_json(context.output_dir, "logic_tuple_input_dict.json", logic_tuple_input_dict, "Logic tuple input dict")
    input_quality_issues = logic_tuple_input_quality_issues(logic_tuple_input_dict, structured_input_dict)

    if input_quality_issues:
        raise RuntimeError(
            "extract_logic_tuples input contains source nodes with empty original "
            f"text: {input_quality_issues}. Rerun from extract_statements."
        )

    partial, report, run_dir = run_recoverable_task(
        context,
        stage_name=STAGE_NAME,
        input_dict=logic_tuple_input_dict,
        task_runner=lambda index_dict, checkpoint_dir: _run_logic_tuple_tasks(
            context,
            index_dict,
            checkpoint_dir,
        ),
        task_summary=lambda key, payload: {
            "orig_key": (payload or {}).get("_orig_key"),
            "node_type": ((payload or {}).get("pos1") or {}).get("node_type"),
            "label": ((payload or {}).get("pos1") or {}).get("label"),
        },
    )
    state = finalize_complete_statement_dict(context, state, partial)
    report = write_failure_report(
        run_dir,
        run_dir.name,
        STAGE_NAME,
        [str(key) for key in logic_tuple_input_dict],
        partial,
        attempts=report.get("attempt_rounds") or 1,
        canonical_updated=True,
    )
    state["logic_tuple_stage_run"] = report
    return state


def rerun_failed_tasks(context, state, max_rounds=2):
    partial, report, run_dir = rerun_unresolved_task_report(
        context,
        stage_name=STAGE_NAME,
        task_runner=lambda index_dict, checkpoint_dir: _run_logic_tuple_tasks(
            context,
            index_dict,
            checkpoint_dir,
        ),
        max_rounds=max_rounds,
    )
    state = finalize_complete_statement_dict(context, state, partial)
    report = write_failure_report(
        run_dir,
        run_dir.name,
        STAGE_NAME,
        report.get("expected_task_keys") or [],
        partial,
        attempts=report.get("attempt_rounds") or 1,
        canonical_updated=True,
    )
    state["logic_tuple_stage_run"] = report
    return state, report
