from ...common.io import save_stage_json
from ...common.llm_task import run_multiprocess_task
from ...common.stage_recovery import (
    latest_unresolved_failure_report as _latest_unresolved_failure_report,
    rerun_unresolved_task_report,
    run_recoverable_task,
    write_failure_report,
)
from ...common.node import (
    attach_fields_to_match_unit,
    build_match_unit_dict,
    is_definition_node_type,
    is_logic_tuple_node_type,
    is_relation_statement_node_type,
    merge_node_with_source_envelope,
    normalize_node_fields,
)
from .logic_ast_renderer import render_logic_ast_local
from .templates import correction_prompt11, data_template11, prompt_template11, validation11

STAGE_NAME = "compile_logic_form"

_REPAIRABLE_STATEMENT_FORMS = frozenset({"", "other", "unknown"})
_LOGICAL_STRUCTURE_KINDS = frozenset({"forall", "exists", "imp", "iff", "and", "or", "not"})


def _contains_logical_structure(value):
    if isinstance(value, dict):
        kind = str(value.get("kind") or "").strip().lower()
        if kind in _LOGICAL_STRUCTURE_KINDS:
            return True
        return any(_contains_logical_structure(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_logical_structure(item) for item in value)
    return False


def _is_top_level_equality(node, logic_ast_local):
    if not isinstance(node, dict) or not isinstance(logic_ast_local, dict):
        return False
    if not is_relation_statement_node_type(node.get("node_type")):
        return False

    statement_form = str(node.get("statement_form") or "").strip().lower()
    if statement_form not in _REPAIRABLE_STATEMENT_FORMS:
        return False
    if node.get("conditions"):
        return False
    if str(logic_ast_local.get("kind") or "").strip().lower() != "eq":
        return False
    if "vars" in logic_ast_local or "body" in logic_ast_local:
        return False

    left = logic_ast_local.get("left")
    right = logic_ast_local.get("right")
    if not isinstance(left, dict) or not left or not isinstance(right, dict) or not right:
        return False
    return not _contains_logical_structure(left) and not _contains_logical_structure(right)


def normalize_node_dict(node_dict):
    normalized = {}
    for key, node in (node_dict or {}).items():
        if not isinstance(node, dict):
            continue
        protected, audit = merge_node_with_source_envelope(
            node,
            {},
            stage_name=STAGE_NAME,
            allowed_fields=(),
        )
        audits = list(protected.get("_source_merge_audits") or [])
        audits.append(audit)
        protected["_source_merge_audits"] = audits
        normalized_node = normalize_node_fields(protected)
        normalized[key], _ = merge_node_with_source_envelope(
            normalized_node,
            {},
            stage_name=STAGE_NAME,
            allowed_fields=(),
        )
    return normalized


def build_logic_form_input_dict(node_dict):
    def _sort_key(value):
        try:
            return (0, int(str(value)))
        except (TypeError, ValueError):
            return (1, str(value))

    def _text(value):
        return value if isinstance(value, str) else ""

    def _list(value):
        return value if isinstance(value, list) else []

    def _title_chinese(node):
        title = node.get("title")
        if isinstance(title, dict):
            return _text(title.get("chinese"))
        return _text(title)

    def _node_text_normalized(node):
        remark = node.get("remark")
        if isinstance(remark, dict):
            normalized = _text(remark.get("text_normalized"))
            if normalized:
                return normalized
        content = node.get("content")
        if isinstance(content, dict):
            return _text(content.get("text_normalized"))
        return ""

    def _normalized_list_field(node, field_name):
        value = node.get(field_name)
        if isinstance(value, dict):
            return _list(value.get("text_normalized"))
        return []

    def _variables(node):
        result = []
        variables = node.get("variables")
        if not isinstance(variables, list):
            return result
        for variable in variables:
            if not isinstance(variable, dict):
                continue
            result.append(
                {
                    "type": _text(variable.get("type")),
                    "normalize_type": _text(variable.get("normalize_type")),
                }
            )
        return result

    def _logic_items(node, field_name):
        result = []
        items = node.get(field_name)
        if not isinstance(items, list):
            return result
        for item in items:
            if not isinstance(item, dict):
                continue
            result.append(
                {
                    "id": _text(item.get("id")),
                    "text_normalized": _text(item.get("text_normalized")),
                }
            )
        return result

    logic_form_input_dict = {}

    match_units = build_match_unit_dict(node_dict)
    for key in sorted((match_units or {}).keys(), key=_sort_key):
        node = (match_units or {}).get(key)
        if not isinstance(node, dict):
            continue

        node_type = _text(node.get("node_type")).strip()
        if is_definition_node_type(node_type):
            logic_form_input_dict[key] = {
                "pos1": {
                    "global_id": _text(node.get("global_id")),
                    "parent_global_id": _text(node.get("parent_global_id")) or _text(node.get("global_id")),
                    "sub_index": node.get("sub_index"),
                    "node_type": node_type,
                    "title": _title_chinese(node),
                    "text_normalized": _node_text_normalized(node),
                }
            }
        elif is_logic_tuple_node_type(node_type):
            logic_form_input_dict[key] = {
                "pos1": {
                    "global_id": _text(node.get("global_id")),
                    "parent_global_id": _text(node.get("parent_global_id")) or _text(node.get("global_id")),
                    "sub_index": node.get("sub_index"),
                    "node_type": node_type,
                    "statement_form": _text(node.get("statement_form")),
                    "text_normalized": _node_text_normalized(node),
                    "subject": _normalized_list_field(node, "subject"),
                    "context": _normalized_list_field(node, "context"),
                    "variables": _variables(node),
                    "conditions": _logic_items(node, "conditions"),
                    "conclusions": _logic_items(node, "conclusions"),
                }
            }

    return logic_form_input_dict


def _run_logic_form_tasks(context, index_dict, checkpoint_dir):
    return run_multiprocess_task(
        llm=context.llm,
        parse_method=context.parser.parse_dict,
        data_template=data_template11,
        prompt_template=prompt_template11,
        correction_template=correction_prompt11,
        validator=validation11,
        index_dict=index_dict,
        num_threads=context.num_threads,
        checkpoint=context.checkpoint,
        checkpoint_dir=checkpoint_dir,
    )


def compile_logic_form_local_dict(context, logic_form_input_dict):
    if not logic_form_input_dict:
        return {}

    result_dict, failure_report, _ = run_recoverable_task(
        context,
        stage_name=STAGE_NAME,
        input_dict=logic_form_input_dict,
        task_runner=lambda index_dict, checkpoint_dir: _run_logic_form_tasks(context, index_dict, checkpoint_dir),
    )
    if failure_report.get("status") != "resolved":
        return result_dict
    return result_dict


def latest_unresolved_failure_report(context):
    return _latest_unresolved_failure_report(context, STAGE_NAME)


def merge_logic_ast_local(logic_form_local_dict, node_dict):
    match_units = build_match_unit_dict(node_dict)
    for key, llm_output in (logic_form_local_dict or {}).items():
        if not isinstance(llm_output, dict):
            print(f"Logic form merge error: output for key {key} is not a dict")
            continue

        global_id = llm_output.get("global_id")
        if not isinstance(global_id, str) or not global_id:
            print(f"Logic form merge error: missing global_id for key {key}")
            continue

        logic_ast_local = llm_output.get("logic_ast_local", {})
        fields = {
            "logic_ast_local": logic_ast_local,
            "logic_form_rendered": render_logic_ast_local(logic_ast_local),
        }
        source_unit = match_units.get(str(key))
        if _is_top_level_equality(source_unit, logic_ast_local):
            fields.update(
                {
                    "statement_form_before_repair": str(source_unit.get("statement_form") or "").strip().lower(),
                    "statement_form": "equality",
                    "statement_form_repair": "structured_logic_correction",
                    "statement_form_repair_evidence": "logic_ast_local.kind=eq",
                }
            )
        attached = attach_fields_to_match_unit(
            node_dict,
            key,
            fields,
        )
        if not attached:
            print(f"Logic form merge error: source key not found: {key}")
            continue

        llm_output.setdefault("parent_global_id", global_id)
        _, sub_index = _parse_source_key_for_output(key)
        llm_output.setdefault("sub_index", sub_index if sub_index is not None else 1)

    return node_dict


def _parse_source_key_for_output(source_key):
    source_text = str(source_key)
    if "__sub" not in source_text:
        return source_text, None
    parent_key, _, sub_text = source_text.rpartition("__sub")
    try:
        return parent_key, int(sub_text)
    except ValueError:
        return parent_key, None


def _finalize_outputs(context, state, node_dict, logic_form_input_dict, logic_form_local_dict, *, run_dir=None, attempts=1):
    save_stage_json(context.output_dir, "logic_form_local_dict.json", logic_form_local_dict, "Logic form local dict")
    node_dict = merge_logic_ast_local(logic_form_local_dict, node_dict)
    if run_dir is not None:
        write_failure_report(
            run_dir,
            run_dir.name,
            STAGE_NAME,
            [str(key) for key in (logic_form_input_dict or {}).keys()],
            logic_form_local_dict,
            attempts=attempts,
            canonical_updated=True,
        )
    state["logic_form_input_dict"] = logic_form_input_dict
    state["logic_form_local_dict"] = logic_form_local_dict
    state["node_dict"] = node_dict
    state["node_list"] = list(node_dict.values())
    return state


def run(context, state):
    try:
        node_dict = state["node_dict"]
    except (KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError(
            "compile_logic_form requires node_dict from extract_logic_tuples. "
            "The upstream extract_logic_tuples stage did not produce a usable node_dict."
        ) from exc
    if not isinstance(node_dict, dict) or not node_dict:
        raise RuntimeError(
            "compile_logic_form requires a non-empty node_dict from extract_logic_tuples. "
            "The upstream extract_logic_tuples stage did not produce a usable node_dict."
        )
    node_dict = normalize_node_dict(node_dict)
    save_stage_json(context.output_dir, "node_dict_normalized.json", node_dict, "Node dict after normalization")

    logic_form_input_dict = build_logic_form_input_dict(node_dict)
    save_stage_json(context.output_dir, "logic_form_input_dict.json", logic_form_input_dict, "Logic form input dict")

    if not logic_form_input_dict:
        return _finalize_outputs(context, state, node_dict, logic_form_input_dict, {})

    logic_form_local_dict, failure_report, run_dir = run_recoverable_task(
        context,
        stage_name=STAGE_NAME,
        input_dict=logic_form_input_dict,
        task_runner=lambda index_dict, checkpoint_dir: _run_logic_form_tasks(context, index_dict, checkpoint_dir),
    )
    if failure_report.get("status") != "resolved":
        state["logic_form_input_dict"] = logic_form_input_dict
        state["compile_logic_form_stage_run"] = failure_report
        if getattr(context, "execution_mode", "pipeline") == "pipeline":
            return _finalize_outputs(
                context,
                state,
                node_dict,
                logic_form_input_dict,
                logic_form_local_dict,
                run_dir=run_dir,
                attempts=1,
            )
        return state
    return _finalize_outputs(context, state, node_dict, logic_form_input_dict, logic_form_local_dict, run_dir=run_dir, attempts=1)


def rerun_failed_tasks(context, state, max_rounds=2):
    node_dict = normalize_node_dict(state["node_dict"])
    logic_form_input_dict = build_logic_form_input_dict(node_dict)
    logic_form_local_dict, failure_report, run_dir = rerun_unresolved_task_report(
        context,
        stage_name=STAGE_NAME,
        task_runner=lambda index_dict, checkpoint_dir: _run_logic_form_tasks(context, index_dict, checkpoint_dir),
        max_rounds=max_rounds,
    )
    if failure_report.get("status") != "resolved":
        state["compile_logic_form_stage_run"] = failure_report
        return state, failure_report
    state = _finalize_outputs(
        context,
        state,
        node_dict,
        logic_form_input_dict,
        logic_form_local_dict,
        run_dir=run_dir,
        attempts=failure_report.get("attempt_rounds") or 1,
    )
    return state, {**failure_report, "status": "resolved", "canonical_updated": True}

