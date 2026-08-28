import copy
import re
import unicodedata

from ...common.formalization_guards import attach_formalization_guidance
from ...common.io import save_stage_json
from ...common.llm_task import run_multiprocess_task
from ...common.node import (
    SOURCE_ENVELOPE_KEY,
    SOURCE_ENVELOPE_FIELDS,
    adjust,
    attach_internal_subnodes,
    get_node_label,
    get_node_node_type,
    get_node_source_original_text,
    get_node_title,
    merge_node_with_source_envelope,
    normalize_node_fields,
    structured_parent_view,
)
from ...common.stage_recovery import (
    latest_unresolved_failure_report as _latest_unresolved_failure_report,
    rerun_unresolved_task_report,
    run_recoverable_task,
    write_failure_report,
)
from .templates import correction_prompt13, data_template13, prompt_template13, validation13


STAGE_NAME = "repair"
ALLOWED_PATCH_FIELDS = {
    "statement_form",
    "subject",
    "context",
    "variables",
    "conditions",
    "conclusions",
}
LIST_PATCH_FIELDS = {
    "subject",
    "context",
    "variables",
    "conditions",
    "conclusions",
}
EXPECTED_PATCH_OPERATIONS = {
    "statement_form": "replace",
    "subject": "append",
    "context": "append",
    "variables": "append",
    "conditions": "append",
    "conclusions": "split",
}


def build_repair_input_dict(node_dict):
    def text(value):
        return value if isinstance(value, str) else ""

    def remark_field(node, field_name):
        remark = node.get("remark")
        if isinstance(remark, dict):
            return text(remark.get(field_name))
        return ""

    def has_actionable_value(value):
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, list):
            return any(has_actionable_value(item) for item in value)
        if isinstance(value, dict):
            return any(has_actionable_value(item) for item in value.values())
        return value is not None

    def slim_subnode_specs(node):
        specs = node.get("subnode_specs")
        if not isinstance(specs, list):
            specs = []
        allowed_fields = {
            "index",
            "kind",
            "statement_form",
            "source_conclusion",
            "conclusion",
            "applicable_context",
            "applicable_conditions_text",
            "equivalence_components",
        }
        return [
            {
                field_name: copy.deepcopy(spec[field_name])
                for field_name in allowed_fields
                if field_name in spec
            }
            for spec in specs
            if isinstance(spec, dict)
        ]

    def slim_subnodes(node):
        summaries = []
        for subnode in node.get("sub_nodes") or []:
            if not isinstance(subnode, dict):
                continue
            remark = subnode.get("remark") if isinstance(subnode.get("remark"), dict) else {}
            summaries.append({
                "index": subnode.get("index"),
                "kind": subnode.get("kind"),
                "statement_form": subnode.get("statement_form"),
                "conclusions": copy.deepcopy(subnode.get("conclusions") or []),
                "applicable_conditions_text": copy.deepcopy(
                    remark.get("applicable_conditions_text") or []
                ),
            })
        return summaries

    repair_input_dict = {}
    for key, node in (node_dict or {}).items():
        if not isinstance(node, dict):
            continue
        if node.get("analysis_status") != "completed":
            continue

        node_view = structured_parent_view(node)
        analysis_layer = node_view.get("analysis_layer") or {}
        repair_suggestion = node_view.get("repair_suggestion") or {}
        if not has_actionable_value(repair_suggestion):
            continue

        original_form = remark_field(node_view, "original_form")
        if not original_form:
            original_form = text(get_node_source_original_text(node_view))
        conclusions = copy.deepcopy(node_view.get("conclusions") or [])
        subnodes = slim_subnodes(node)
        payload = {
            "node_ref": {
                "node_key": str(key),
                "global_id": text(node_view.get("global_id")),
                "node_type": get_node_node_type(node_view),
                "label": get_node_label(node_view),
                "title": get_node_title(node_view),
            },
            "statement": {
                "statement_form": text(node_view.get("statement_form")),
                "original_form": original_form,
                "text_normalized": remark_field(node_view, "text_normalized"),
            },
            "extraction": {
                "subject": copy.deepcopy(node_view.get("subject") or []),
                "context": copy.deepcopy(node_view.get("context") or []),
                "variables": copy.deepcopy(node_view.get("variables") or []),
                "conditions": copy.deepcopy(node_view.get("conditions") or []),
                "conclusions": conclusions,
            },
            "analysis": {
                "analysis_layer": copy.deepcopy(analysis_layer),
                "repair_suggestion": copy.deepcopy(repair_suggestion),
            },
            "structure_snapshot": {
                "conclusion_count": len(conclusions),
                "subnode_count": len(subnodes),
                "subnode_specs": slim_subnode_specs(node),
                "subnodes": subnodes,
            },
        }
        repair_input_dict[key] = {"pos1": payload}
    return repair_input_dict


def normalize_repair_result(result):
    def text(value):
        return value if isinstance(value, str) else ""

    def json_safe(value):
        if isinstance(value, dict):
            return {str(k): json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [json_safe(item) for item in value]
        if isinstance(value, set):
            return [json_safe(item) for item in sorted(value, key=repr)]
        return value

    def empty_repair_log():
        return {
            "applied_repairs": [],
            "skipped_suggestions": [],
            "risk_notes": [],
        }

    if not isinstance(result, dict):
        return {
            "node_key": "",
            "node_global_id": "",
            "field_patch": {},
            "repair_log": empty_repair_log(),
        }

    field_patch = result.get("field_patch")
    if not isinstance(field_patch, dict):
        field_patch = {}
    field_patch = {
        field_name: json_safe(value)
        for field_name, value in field_patch.items()
        if field_name in ALLOWED_PATCH_FIELDS
    }

    repair_log = result.get("repair_log")
    if not isinstance(repair_log, dict):
        repair_log = {}

    normalized_log = empty_repair_log()
    for key in normalized_log:
        value = repair_log.get(key)
        if isinstance(value, (list, tuple, set)):
            normalized_log[key] = [json_safe(item) for item in value]
        else:
            normalized_log[key] = []

    return {
        "node_key": text(result.get("node_key")),
        "node_global_id": text(result.get("node_global_id")),
        "field_patch": field_patch,
        "repair_log": normalized_log,
    }


def _source_statement_text(node):
    remark = node.get("remark") if isinstance(node, dict) else None
    if isinstance(remark, dict):
        original_form = remark.get("original_form")
        if isinstance(original_form, str) and original_form.strip():
            return original_form
    source_text = get_node_source_original_text(node)
    return source_text if isinstance(source_text, str) else ""


def _normalized_text(value):
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", value).strip()


def _compact_math_text(value):
    value = _normalized_text(value)
    value = value.replace("\\left", "").replace("\\right", "")
    return re.sub(r"[\s$`]", "", value)


def _evidence_is_source_excerpt(evidence, source_text):
    normalized_evidence = _normalized_text(evidence)
    normalized_source = _normalized_text(source_text)
    return bool(normalized_evidence and normalized_evidence in normalized_source)


def _value_is_supported_by_evidence(value, evidence, field_name):
    evidence_compact = _compact_math_text(evidence)
    if not evidence_compact:
        return False

    if isinstance(value, str):
        fragments = [value]
    elif isinstance(value, dict):
        if field_name == "variables":
            fragments = [value.get("name"), value.get("type")]
        else:
            fragments = [value.get("text")]
    else:
        return False

    fragments = [fragment for fragment in fragments if isinstance(fragment, str) and fragment.strip()]
    if not fragments:
        return False
    return all(_compact_math_text(fragment) in evidence_compact for fragment in fragments)


def _statement_form_supported(statement_form, evidence):
    compact = _compact_math_text(evidence).lower()
    form = _normalized_text(statement_form).lower()
    markers = {
        "equality": ("=",),
        "equivalence": ("ifandonlyif", "iff", "equivalent", "⇔", "⟺"),
        "characterization": ("ifandonlyif", "iff", "equivalent", "characterizedby"),
        "existence": ("thereexists", "thereis", "exists", "∃"),
        "implication": ("if", "then"),
    }
    required = markers.get(form)
    if not required:
        return False
    if form == "implication":
        return all(marker in compact for marker in required)
    return any(marker in compact for marker in required)


def _conclusion_id(item):
    if not isinstance(item, dict):
        return ""
    value = item.get("id")
    return value.strip() if isinstance(value, str) else ""


def _repair_records_by_field(repair_log):
    records = repair_log.get("applied_repairs") if isinstance(repair_log, dict) else None
    if not isinstance(records, list):
        return {}, ["repair_log.applied_repairs must be a list"]

    by_field = {}
    errors = []
    for record in records:
        if not isinstance(record, dict):
            errors.append("every applied repair must be an object")
            continue
        field_name = record.get("field")
        if field_name not in ALLOWED_PATCH_FIELDS:
            errors.append(f"invalid applied repair field: {field_name!r}")
            continue
        if field_name in by_field:
            errors.append(f"duplicate applied repair record for {field_name}")
            continue
        by_field[field_name] = record
    return by_field, errors


def _validate_append_patch(field_name, old_value, new_value, evidence):
    if not isinstance(old_value, list):
        return [f"{field_name} is not a list in the current extraction"]
    if not isinstance(new_value, list) or not new_value:
        return [f"{field_name} patch must be a non-empty full list"]
    if len(new_value) <= len(old_value):
        return [f"{field_name} append patch must add at least one item"]
    if new_value[:len(old_value)] != old_value:
        return [f"{field_name} patch did not preserve the existing list verbatim"]
    additions = new_value[len(old_value):]
    if any(not _value_is_supported_by_evidence(item, evidence, field_name) for item in additions):
        return [f"{field_name} addition is not directly supported by source_evidence"]
    return []


def _validate_conclusion_patch(old_value, new_value, record):
    errors = []
    if not isinstance(old_value, list) or not old_value:
        return ["current conclusions must be a non-empty list"]
    if not isinstance(new_value, list) or not new_value:
        return ["conclusions patch must be a non-empty full list"]
    if len(new_value) <= len(old_value):
        errors.append("conclusions split must increase the conclusion count")

    old_ids = [_conclusion_id(item) for item in old_value]
    new_ids = [_conclusion_id(item) for item in new_value]
    if any(not item_id for item_id in old_ids + new_ids):
        errors.append("every conclusion must have a non-empty string id")
    if len(set(old_ids)) != len(old_ids) or len(set(new_ids)) != len(new_ids):
        errors.append("conclusion ids must be unique")

    replaces_ids = record.get("replaces_ids")
    if not isinstance(replaces_ids, list) or len(replaces_ids) != 1:
        errors.append("conclusions split must declare exactly one replaces_ids entry")
        return errors
    replaced_id = replaces_ids[0]
    if not isinstance(replaced_id, str) or replaced_id not in old_ids:
        errors.append("replaces_ids does not identify an existing conclusion")
        return errors

    old_by_id = {item_id: item for item_id, item in zip(old_ids, old_value)}
    new_by_id = {item_id: item for item_id, item in zip(new_ids, new_value)}
    for item_id, old_item in old_by_id.items():
        if item_id == replaced_id:
            if item_id in new_by_id:
                errors.append("the replaced conclusion id must not remain in the split output")
            continue
        if item_id not in new_by_id or new_by_id[item_id] != old_item:
            errors.append(f"unchanged conclusion {item_id} was removed or modified")

    new_split_items = [
        item for item_id, item in zip(new_ids, new_value)
        if item_id not in old_by_id
    ]
    if len(new_split_items) < 2:
        errors.append("a split must replace one conclusion with at least two new conclusions")
    evidence = record.get("source_evidence")
    if any(
        not _value_is_supported_by_evidence(item, evidence, "conclusions")
        for item in new_split_items
    ):
        errors.append("a split conclusion is not directly supported by source_evidence")

    replaced_index = old_ids.index(replaced_id)
    expected_prefix = old_ids[:replaced_index]
    expected_suffix = old_ids[replaced_index + 1:]
    if new_ids[:replaced_index] != expected_prefix:
        errors.append("conclusions before the split position were reordered")
    if expected_suffix and new_ids[-len(expected_suffix):] != expected_suffix:
        errors.append("conclusions after the split position were reordered")
    split_ids = new_ids[replaced_index:len(new_ids) - len(expected_suffix) if expected_suffix else None]
    if len(split_ids) < 2 or any(item_id in old_by_id for item_id in split_ids):
        errors.append("new split conclusions must occupy the replaced conclusion position")
    return errors


def _validate_repair_patch(node, field_patch, repair_log):
    errors = []
    node_view = structured_parent_view(node)
    source_text = _source_statement_text(node)
    records_by_field, record_errors = _repair_records_by_field(repair_log)
    errors.extend(record_errors)

    if set(records_by_field) != set(field_patch):
        errors.append("field_patch and applied_repairs fields must match exactly")

    for field_name, value in field_patch.items():
        if field_name not in ALLOWED_PATCH_FIELDS:
            errors.append(f"field_patch contains unsupported field {field_name}")
            continue
        if field_name == "statement_form":
            if not isinstance(value, str) or not value.strip():
                errors.append("statement_form patch must be a non-empty string")
        elif not isinstance(value, list) or not value:
            errors.append(f"{field_name} patch must be a non-empty list")

        record = records_by_field.get(field_name)
        if not isinstance(record, dict):
            continue
        if record.get("operation") != EXPECTED_PATCH_OPERATIONS[field_name]:
            errors.append(f"invalid operation for {field_name}")
        evidence = record.get("source_evidence")
        if not isinstance(evidence, str) or not _evidence_is_source_excerpt(evidence, source_text):
            errors.append(f"{field_name} source_evidence is not an exact source excerpt")
            continue
        if not isinstance(record.get("reason"), str) or not record["reason"].strip():
            errors.append(f"{field_name} repair reason is missing")

        if field_name in LIST_PATCH_FIELDS - {"conclusions"}:
            if record.get("replaces_ids") not in ([], None):
                errors.append(f"{field_name} append must not declare replaces_ids")
            errors.extend(
                _validate_append_patch(field_name, node_view.get(field_name), value, evidence)
            )
        elif field_name == "conclusions":
            errors.extend(
                _validate_conclusion_patch(node_view.get("conclusions"), value, record)
            )
        else:
            if record.get("replaces_ids") not in ([], None):
                errors.append("statement_form replace must not declare replaces_ids")
            if isinstance(node.get("sub_nodes"), list) and len(node["sub_nodes"]) > 1:
                errors.append("statement_form repair is unsafe for a multi-subnode node")
            if isinstance(value, str) and not _statement_form_supported(value, evidence):
                errors.append("statement_form is not directly supported by source_evidence")
    return errors


def _append_values(existing, additions):
    values = copy.deepcopy(existing) if isinstance(existing, list) else []
    for addition in additions:
        if addition not in values:
            values.append(copy.deepcopy(addition))
    return values


def _condition_texts(conditions):
    return [
        item.get("text")
        for item in conditions
        if isinstance(item, dict) and isinstance(item.get("text"), str) and item.get("text").strip()
    ]


def _apply_append_patch(node, field_name, full_value, old_value):
    additions = full_value[len(old_value):]
    subnodes = node.get("sub_nodes")
    if not isinstance(subnodes, list) or not subnodes:
        node[field_name] = copy.deepcopy(full_value)
        return node

    if isinstance(node.get(field_name), list):
        node[field_name] = _append_values(node[field_name], additions)
    for subnode in subnodes:
        if not isinstance(subnode, dict):
            continue
        subnode[field_name] = _append_values(subnode.get(field_name), additions)
        if field_name == "conditions":
            subnode["applicable_conditions"] = _append_values(
                subnode.get("applicable_conditions"), additions
            )
            remark = subnode.get("remark")
            if isinstance(remark, dict):
                remark["applicable_conditions_text"] = _append_values(
                    remark.get("applicable_conditions_text"),
                    _condition_texts(additions),
                )

    if field_name == "conditions":
        for spec in node.get("subnode_specs") or []:
            if isinstance(spec, dict):
                spec["applicable_conditions_text"] = _append_values(
                    spec.get("applicable_conditions_text"),
                    _condition_texts(additions),
                )
    return node


def _set_split_subnode_conclusion(subnode, conclusion, index, source_conclusion):
    subnode = copy.deepcopy(subnode)
    conclusion_text = conclusion.get("text") if isinstance(conclusion, dict) else ""
    subnode["index"] = index
    subnode["kind"] = "split_conclusion"
    subnode["source_conclusion"] = source_conclusion
    subnode["content"] = conclusion_text
    subnode["conclusions"] = [copy.deepcopy(conclusion)]
    remark = subnode.get("remark")
    if isinstance(remark, dict):
        remark["original_form"] = conclusion_text
    return subnode


def _set_split_spec_conclusion(spec, conclusion, index, source_conclusion):
    spec = copy.deepcopy(spec) if isinstance(spec, dict) else {}
    conclusion_text = conclusion.get("text") if isinstance(conclusion, dict) else ""
    spec["index"] = index
    spec["kind"] = "split_conclusion"
    spec["source_conclusion"] = source_conclusion
    spec["content"] = conclusion_text
    spec["conclusion"] = conclusion_text
    spec["equivalence_components"] = []
    return spec


def _apply_conclusion_split(node, new_conclusions, replaced_id):
    node_view = structured_parent_view(node)
    old_conclusions = node_view.get("conclusions") or []
    old_ids = [_conclusion_id(item) for item in old_conclusions]
    old_by_id = {item_id: item for item_id, item in zip(old_ids, old_conclusions)}
    new_ids = [_conclusion_id(item) for item in new_conclusions]
    split_items = [item for item_id, item in zip(new_ids, new_conclusions) if item_id not in old_by_id]
    replaced_conclusion = old_by_id[replaced_id]
    replaced_text = replaced_conclusion.get("text", "")

    subnodes = node.get("sub_nodes")
    if not isinstance(subnodes, list) or not subnodes:
        node["conclusions"] = copy.deepcopy(new_conclusions)
        return attach_internal_subnodes(node)

    subnode_by_id = {}
    for subnode in subnodes:
        if not isinstance(subnode, dict):
            continue
        conclusions = subnode.get("conclusions")
        if isinstance(conclusions, list) and len(conclusions) == 1:
            conclusion_id = _conclusion_id(conclusions[0])
            if conclusion_id:
                subnode_by_id[conclusion_id] = subnode
    if any(item_id not in subnode_by_id for item_id in old_ids):
        raise ValueError("cannot map every existing conclusion to its subnode")

    specs = node.get("subnode_specs")
    if not isinstance(specs, list) or len(specs) != len(old_conclusions):
        raise ValueError("cannot preserve subnode_specs during conclusion split")
    spec_by_id = {item_id: specs[index] for index, item_id in enumerate(old_ids)}
    source_subnode = subnode_by_id[replaced_id]
    source_spec = spec_by_id[replaced_id]

    rebuilt_subnodes = []
    rebuilt_specs = []
    for index, (item_id, conclusion) in enumerate(zip(new_ids, new_conclusions), start=1):
        if item_id in old_by_id:
            subnode = copy.deepcopy(subnode_by_id[item_id])
            spec = copy.deepcopy(spec_by_id[item_id])
            subnode["index"] = index
            spec["index"] = index
        else:
            if conclusion not in split_items:
                raise ValueError("unexpected conclusion while rebuilding split subnodes")
            subnode = _set_split_subnode_conclusion(
                source_subnode,
                conclusion,
                index,
                replaced_text,
            )
            spec = _set_split_spec_conclusion(
                source_spec,
                conclusion,
                index,
                replaced_text,
            )
        rebuilt_subnodes.append(subnode)
        rebuilt_specs.append(spec)

    node["sub_nodes"] = rebuilt_subnodes
    node["subnode_specs"] = rebuilt_specs
    node["subnode_count"] = len(rebuilt_subnodes)
    node["subnode_display"] = "expandable"
    for source_field in ("remark", "content"):
        source = node.get(source_field)
        if isinstance(source, dict) and isinstance(source.get("subnode_specs"), list):
            source["subnode_specs"] = copy.deepcopy(rebuilt_specs)
    return node


def _finalize_after_repair(node):
    protected, _ = merge_node_with_source_envelope(
        node,
        {},
        stage_name=STAGE_NAME,
        allowed_fields=(),
    )
    rebuilt = adjust(protected)
    if get_node_source_original_text(rebuilt):
        rebuilt = normalize_node_fields(rebuilt)
        rebuilt = attach_formalization_guidance(rebuilt)
    derived = {
        field_name: value
        for field_name, value in rebuilt.items()
        if field_name not in SOURCE_ENVELOPE_FIELDS
        and field_name not in {SOURCE_ENVELOPE_KEY, "title"}
    }
    rebuilt, audit = merge_node_with_source_envelope(
        protected,
        derived,
        stage_name=STAGE_NAME,
        allowed_fields=set(derived),
    )
    audits = list(rebuilt.get("_source_merge_audits") or [])
    audits.append(audit)
    rebuilt["_source_merge_audits"] = audits
    return rebuilt


def _subnode_count(node):
    subnodes = node.get("sub_nodes") if isinstance(node, dict) else None
    return len(subnodes) if isinstance(subnodes, list) else 0


def _subnode_conclusion_id(subnode):
    conclusions = subnode.get("conclusions") if isinstance(subnode, dict) else None
    if not isinstance(conclusions, list) or len(conclusions) != 1:
        return ""
    return _conclusion_id(conclusions[0])


def _contains_all(existing, required):
    if not isinstance(required, list):
        return True
    if not isinstance(existing, list):
        return not required
    def matches(candidate, expected):
        if isinstance(candidate, dict) and isinstance(expected, dict):
            for field_name in ("id", "text"):
                if field_name in expected and candidate.get(field_name) != expected.get(field_name):
                    return False
            return True
        return candidate == expected

    return all(
        any(matches(candidate, expected) for candidate in existing)
        for expected in required
    )


def _local_scope_preserved(original, candidate, replaced_id="", split_ids=None):
    original_subnodes = original.get("sub_nodes") if isinstance(original, dict) else None
    candidate_subnodes = candidate.get("sub_nodes") if isinstance(candidate, dict) else None
    if not isinstance(original_subnodes, list) or not original_subnodes:
        return True
    if not isinstance(candidate_subnodes, list):
        return False

    split_ids = split_ids or []
    candidate_by_id = {
        _subnode_conclusion_id(subnode): subnode
        for subnode in candidate_subnodes
        if _subnode_conclusion_id(subnode)
    }
    for original_subnode in original_subnodes:
        original_id = _subnode_conclusion_id(original_subnode)
        target_ids = split_ids if original_id == replaced_id else [original_id]
        if not target_ids or any(target_id not in candidate_by_id for target_id in target_ids):
            return False
        original_remark = original_subnode.get("remark")
        original_local_texts = (
            original_remark.get("applicable_conditions_text")
            if isinstance(original_remark, dict)
            else []
        )
        for target_id in target_ids:
            target = candidate_by_id[target_id]
            target_remark = target.get("remark")
            target_local_texts = (
                target_remark.get("applicable_conditions_text")
                if isinstance(target_remark, dict)
                else []
            )
            if not _contains_all(target_local_texts, original_local_texts):
                return False
            if not _contains_all(target.get("conditions"), original_subnode.get("conditions")):
                return False
            if not _contains_all(
                target.get("applicable_conditions"),
                original_subnode.get("applicable_conditions"),
            ):
                return False
    return True


def _apply_validated_patch(node, field_patch, repair_log):
    original_view = structured_parent_view(node)
    candidate = copy.deepcopy(node)
    records_by_field, _ = _repair_records_by_field(repair_log)

    replaced_id = ""
    split_ids = []
    if "conclusions" in field_patch:
        conclusion_record = records_by_field["conclusions"]
        replaced_id = conclusion_record["replaces_ids"][0]
        old_ids = {
            _conclusion_id(item)
            for item in original_view.get("conclusions") or []
        }
        split_ids = [
            _conclusion_id(item)
            for item in field_patch["conclusions"]
            if _conclusion_id(item) not in old_ids
        ]
        candidate = _apply_conclusion_split(
            candidate,
            field_patch["conclusions"],
            replaced_id,
        )

    for field_name in ("subject", "context", "variables", "conditions"):
        if field_name not in field_patch:
            continue
        old_value = original_view.get(field_name)
        candidate = _apply_append_patch(
            candidate,
            field_name,
            field_patch[field_name],
            old_value,
        )

    if "statement_form" in field_patch:
        candidate["statement_form"] = field_patch["statement_form"]

    candidate["repair_log"] = copy.deepcopy(repair_log)
    candidate = _finalize_after_repair(candidate)

    original_conclusion_count = len(original_view.get("conclusions") or [])
    candidate_conclusion_count = len(structured_parent_view(candidate).get("conclusions") or [])
    if candidate_conclusion_count < original_conclusion_count:
        raise ValueError("repair reduced the conclusion count")
    if _subnode_count(candidate) < _subnode_count(node):
        raise ValueError("repair reduced the subnode count")
    if not _local_scope_preserved(node, candidate, replaced_id, split_ids):
        raise ValueError("repair changed or removed an existing subnode local condition scope")
    return candidate


def apply_repair_patch(node_dict, repair_result_dict, repair_input_keys=None):
    def text(value):
        return value if isinstance(value, str) else ""

    def has_actionable_value(value):
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, list):
            return any(has_actionable_value(item) for item in value)
        if isinstance(value, dict):
            return any(has_actionable_value(item) for item in value.values())
        return value is not None

    def empty_repair_log():
        return {
            "applied_repairs": [],
            "skipped_suggestions": [],
            "risk_notes": [],
        }

    new_dict = copy.deepcopy(node_dict or {})
    expected_input_keys = {str(key) for key in (repair_input_keys or [])}
    report = {
        "applied": [],
        "skipped": [],
    }

    for key, node in new_dict.items():
        if isinstance(node, dict):
            node["repair_log"] = empty_repair_log()
            if node.get("analysis_status") == "failed":
                node["repair_status"] = "skipped_analysis_failed"
            elif str(key) in expected_input_keys:
                node["repair_status"] = "failed"
                statuses = dict(node.get("_derivation_status") or {})
                statuses[STAGE_NAME] = {
                    "status": "degraded",
                    "reason": "unresolved_model_task",
                    "task_key": str(key),
                }
                node["_derivation_status"] = statuses
            else:
                node["repair_status"] = "not_needed"

    for key, raw_result in (repair_result_dict or {}).items():
        node_key = key
        node = new_dict.get(node_key)
        if node is None:
            try:
                fallback_key = int(str(key))
            except (TypeError, ValueError):
                fallback_key = key
            node_key = fallback_key
            node = new_dict.get(node_key)
        if not isinstance(node, dict):
            report["skipped"].append({
                "key": key,
                "reason": "node_not_found",
            })
            continue

        statuses = dict(node.get("_derivation_status") or {})
        statuses.pop(STAGE_NAME, None)
        if statuses:
            node["_derivation_status"] = statuses
        else:
            node.pop("_derivation_status", None)
        result = normalize_repair_result(raw_result)
        expected_node_key = str(node_key)
        actual_node_key = result["node_key"]
        expected_global_id = text(node.get("global_id"))
        actual_global_id = result["node_global_id"]
        if actual_node_key != expected_node_key or actual_global_id != expected_global_id:
            report["skipped"].append({
                "key": key,
                "reason": "node_identity_mismatch",
                "expected_node_key": expected_node_key,
                "actual_node_key": actual_node_key,
                "expected_global_id": expected_global_id,
                "actual_global_id": actual_global_id,
            })
            node["repair_status"] = "failed"
            continue

        field_patch = {
            field_name: value
            for field_name, value in result["field_patch"].items()
            if has_actionable_value(value)
        }
        applied_fields = list(field_patch)
        if not applied_fields:
            node["repair_log"] = result["repair_log"]
            node["repair_status"] = "not_needed"
            report["applied"].append({
                "key": node_key,
                "global_id": expected_global_id,
                "applied_fields": [],
                "repair_log": result["repair_log"],
            })
            continue

        guard_errors = _validate_repair_patch(node, field_patch, result["repair_log"])
        if guard_errors:
            repair_log = copy.deepcopy(result["repair_log"])
            repair_log["risk_notes"] = list(repair_log.get("risk_notes") or []) + [{
                "guard_rejections": guard_errors,
            }]
            node["repair_log"] = repair_log
            node["repair_status"] = "rejected_guard"
            report["skipped"].append({
                "key": node_key,
                "global_id": expected_global_id,
                "reason": "guard_rejected",
                "errors": guard_errors,
            })
            continue

        try:
            candidate = _apply_validated_patch(node, field_patch, result["repair_log"])
        except (KeyError, TypeError, ValueError) as exc:
            repair_log = copy.deepcopy(result["repair_log"])
            repair_log["risk_notes"] = list(repair_log.get("risk_notes") or []) + [{
                "guard_rejections": [str(exc)],
            }]
            node["repair_log"] = repair_log
            node["repair_status"] = "rejected_guard"
            report["skipped"].append({
                "key": node_key,
                "global_id": expected_global_id,
                "reason": "guard_rejected",
                "errors": [str(exc)],
            })
            continue

        candidate["repair_status"] = "applied"
        new_dict[node_key] = candidate
        report["applied"].append({
            "key": node_key,
            "global_id": expected_global_id,
            "applied_fields": applied_fields,
            "repair_log": result["repair_log"],
        })

    return new_dict, report


def _run_repair_tasks(context, index_dict, checkpoint_dir):
    return run_multiprocess_task(
        llm=context.llm,
        parse_method=context.parser.parse_dict,
        data_template=data_template13,
        prompt_template=prompt_template13,
        correction_template=correction_prompt13,
        validator=validation13,
        index_dict=index_dict,
        num_threads=context.num_threads,
        checkpoint=context.checkpoint,
        checkpoint_dir=checkpoint_dir,
    )


def latest_unresolved_failure_report(context):
    return _latest_unresolved_failure_report(context, STAGE_NAME)


def _finalize_outputs(
    context,
    state,
    node_dict,
    repair_input_dict,
    repair_result_dict,
    run_dir=None,
    attempts=1,
    failure_report=None,
):
    save_stage_json(context.output_dir, "repair_result_dict.json", repair_result_dict, "Repair result dict")

    node_dict, repair_patch_report = apply_repair_patch(
        node_dict,
        repair_result_dict,
        repair_input_keys=repair_input_dict.keys(),
    )
    save_stage_json(context.output_dir, "repair_patch_report.json", repair_patch_report, "Repair patch report")
    save_stage_json(context.output_dir, "node_dict_after_repair.json", node_dict, "Node dict after repair")

    if run_dir is not None:
        failure_report = write_failure_report(
            run_dir,
            run_dir.name,
            STAGE_NAME,
            [str(key) for key in repair_input_dict.keys()],
            repair_result_dict,
            attempts=attempts,
            canonical_updated=True,
        )

    state["repair_input_dict"] = repair_input_dict
    state["repair_result_dict"] = repair_result_dict
    state["repair_patch_report"] = repair_patch_report
    if not isinstance(failure_report, dict) or "status" not in failure_report:
        failure_report = {
            "status": "resolved",
            "expected_task_count": 0,
            "succeeded_task_count": 0,
            "failed_task_count": 0,
            "expected_task_keys": [],
            "succeeded_task_keys": [],
            "failed_task_keys": [],
            "canonical_updated": False,
        }
    state["repair_stage_run"] = failure_report
    state["node_dict"] = node_dict
    state["node_list"] = list(node_dict.values())
    return state


def run(context, state):
    node_dict = state.get("node_dict") or {i: node for i, node in enumerate(state.get("node_list") or [])}
    if not node_dict:
        return state

    repair_input_dict = build_repair_input_dict(node_dict)
    save_stage_json(context.output_dir, "repair_input_dict.json", repair_input_dict, "Repair input dict")

    if repair_input_dict:
        repair_result_dict, failure_report, run_dir = run_recoverable_task(
            context,
            stage_name=STAGE_NAME,
            input_dict=repair_input_dict,
            task_runner=lambda index_dict, checkpoint_dir: _run_repair_tasks(context, index_dict, checkpoint_dir),
        )
        if failure_report.get("status") != "resolved":
            state["repair_stage_run"] = failure_report
            if getattr(context, "execution_mode", "pipeline") == "pipeline":
                return _finalize_outputs(
                    context,
                    state,
                    node_dict,
                    repair_input_dict,
                    repair_result_dict,
                    run_dir=run_dir,
                    attempts=1,
                    failure_report=failure_report,
                )
            return state
    else:
        repair_result_dict = {}
        run_dir = None
        failure_report = {"attempt_rounds": 0}

    return _finalize_outputs(
        context,
        state,
        node_dict,
        repair_input_dict,
        repair_result_dict,
        run_dir=run_dir,
        attempts=failure_report.get("attempt_rounds") or 1,
        failure_report=failure_report,
    )


def rerun_failed_tasks(context, state, max_rounds=2):
    node_dict = state.get("node_dict") or {i: node for i, node in enumerate(state.get("node_list") or [])}
    repair_input_dict = build_repair_input_dict(node_dict)
    repair_result_dict, failure_report, run_dir = rerun_unresolved_task_report(
        context,
        stage_name=STAGE_NAME,
        task_runner=lambda index_dict, checkpoint_dir: _run_repair_tasks(context, index_dict, checkpoint_dir),
        max_rounds=max_rounds,
    )
    state = _finalize_outputs(
        context,
        state,
        node_dict,
        repair_input_dict,
        repair_result_dict,
        run_dir=run_dir,
        attempts=failure_report.get("attempt_rounds") or 1,
        failure_report=failure_report,
    )
    return state, state["repair_stage_run"]


__all__ = [
    "apply_repair_patch",
    "build_repair_input_dict",
    "latest_unresolved_failure_report",
    "normalize_repair_result",
    "rerun_failed_tasks",
    "run",
]
