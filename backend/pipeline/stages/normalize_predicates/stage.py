import itertools
import re
from collections import Counter

from ...common.io import save_stage_json
from ...common.llm_task import run_multiprocess_task
from ...common.stage_recovery import (
    latest_unresolved_failure_report as _latest_unresolved_failure_report,
    rerun_unresolved_task_report,
    run_recoverable_task,
    write_failure_report,
)
from ...common.node import attach_fields_to_match_unit, build_match_unit_dict
from ..compile_logic_form.logic_ast_renderer import render_logic_ast_local
from .templates import correction_prompt12, data_template12, prompt_template12, validation12

STAGE_NAME = "normalize_predicates"
SORT_PARENT = {
    "Character": "Function",
    "Function": "Entity",
    "Group": "Entity",
    "Set": "Entity",
}

FIXED_SEMANTIC_KEYS = {
    "DIV_OPERATOR",
    "POWER_OPERATOR",
    "ORDER_OPERATOR",
    "MUL_OPERATOR",
    "ONE_CONSTANT",
    "TWO_CONSTANT",
    "IND_POWER_OPERATOR",
    "RES_POWER_OPERATOR",
    "SUBSET_RELATION",
    "IRR_MEMBERSHIP",
}


def _as_text(value):
    return value if isinstance(value, str) else ""


def _as_list(value):
    return value if isinstance(value, list) else []


def _name_key(value):
    text = _as_text(value)
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"[^A-Za-z0-9]+", "", text).upper()


def _snake_key(value):
    text = _as_text(value)
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").upper()


def _normalize_pred_id(pred_id):
    pred_id = _as_text(pred_id).strip()
    if not pred_id:
        return ""
    pred_id = re.sub(r"_TMP_.*$", "", pred_id, flags=re.IGNORECASE)
    pred_id = re.sub(r"_DUP_\d+$", "", pred_id, flags=re.IGNORECASE)
    pred_id = _snake_key(pred_id)
    return pred_id if pred_id.startswith("P_") else f"P_{pred_id}"


def _sort_ancestors(sort):
    ancestors = []
    current = _as_text(sort)
    while current:
        ancestors.append(current)
        current = SORT_PARENT.get(current, "")
    return ancestors


def _sorts_compatible_global(left_sorts, right_sorts):
    if len(left_sorts) != len(right_sorts):
        return False
    for left_sort, right_sort in zip(left_sorts, right_sorts):
        if left_sort == right_sort:
            continue
        if left_sort == "Entity" or right_sort == "Entity":
            continue
        if right_sort in _sort_ancestors(left_sort) or left_sort in _sort_ancestors(right_sort):
            continue
        return False
    return True


def _entry_blob(entry):
    parts = [
        entry.get("pred_id", ""),
        entry.get("pred_id_stem", ""),
        entry.get("canonical_name", ""),
        entry.get("surface_template", ""),
        entry.get("abstract_surface_template", ""),
        entry.get("gloss", ""),
    ]
    return " ".join(_as_text(part) for part in parts).upper()


def build_semantic_key(entry):
    normalized_pred_id = _normalize_pred_id(entry.get("pred_id"))
    canonical_key = _name_key(entry.get("canonical_name"))
    blob = _entry_blob(entry)

    if normalized_pred_id in {"P_ONE"} or canonical_key == "ONE":
        return "ONE_CONSTANT"
    if normalized_pred_id in {"P_TWO"} or canonical_key == "TWO":
        return "TWO_CONSTANT"
    if normalized_pred_id == "P_DIV" or canonical_key == "DIV":
        return "DIV_OPERATOR"
    if normalized_pred_id == "P_POWER" or canonical_key == "POWER":
        return "POWER_OPERATOR"
    if normalized_pred_id == "P_ORDER" or canonical_key == "ORDER":
        return "ORDER_OPERATOR"
    if normalized_pred_id == "P_MUL" or canonical_key == "MUL":
        return "MUL_OPERATOR"
    if normalized_pred_id.startswith("P_IND_POWER") or canonical_key in {"INDPOWER", "INDPOWERH", "INDPOWERHGROUP"}:
        return "IND_POWER_OPERATOR"
    if normalized_pred_id.startswith("P_RES_POWER") or canonical_key == "RESPOWER":
        return "RES_POWER_OPERATOR"

    if normalized_pred_id in {"P_IRR", "P_IN_IRR"}:
        return "IRR_MEMBERSHIP"
    if normalized_pred_id.startswith("P_IRREDUCIBLE_CHARACTER"):
        return "IRR_MEMBERSHIP"
    if canonical_key in {"IRR", "INIRR", "IRREDUCIBLECHARACTER", "IRREDUCIBLECHARACTERON", "IRREDUCIBLECHARACTEROF"}:
        return "IRR_MEMBERSHIP"

    if normalized_pred_id == "P_SUBSET" or canonical_key == "SUBSET":
        return "SUBSET_RELATION"
    if canonical_key in {"SUBSETKER", "SUBSETOFIRR", "SUBSETDIVZ"} or normalized_pred_id.startswith("P_SUBSET_"):
        return canonical_key or normalized_pred_id.removeprefix("P_")

    if canonical_key in {"NORMALSUBGROUP", "NORMALIN"} or normalized_pred_id in {"P_NORMAL_SUBGROUP", "P_NORMAL_IN"}:
        return "NORMAL_SUBGROUP"
    if canonical_key in {"SUBGROUP", "SUBGROUPOF"} or normalized_pred_id in {"P_SUBGROUP", "P_SUBGROUP_OF"}:
        return "SUBGROUP"

    if canonical_key:
        return canonical_key
    return normalized_pred_id.removeprefix("P_") if normalized_pred_id else "UNKNOWN_PREDICATE"


def _family_key_from_semantic_key(semantic_key):
    if semantic_key in {"SUBSET_RELATION", "SUBSETKER", "SUBSETOFIRR", "SUBSETDIVZ"} or semantic_key.startswith("SUBSET"):
        return "SUBSET_FAMILY"
    if semantic_key in {"IRR_MEMBERSHIP"} or "IRR" in semantic_key:
        return "IRR_FAMILY"
    if semantic_key in {"NORMAL_SUBGROUP", "SUBGROUP"}:
        return "SUBGROUP_FAMILY"
    if semantic_key in {"IND_POWER_OPERATOR", "RES_POWER_OPERATOR"}:
        return "INDUCTION_RESTRICTION_FAMILY"
    return semantic_key


def classify_predicate_entry(entry):
    semantic_key = build_semantic_key(entry)
    if semantic_key in {"SUBSET_RELATION", "IRR_MEMBERSHIP"}:
        return "fixed_relation"
    if semantic_key in FIXED_SEMANTIC_KEYS:
        return "fixed_operator"
    return "domain_predicate"


def normalize_predicate_entry_fields(entry):
    normalized_pred_id = _normalize_pred_id(entry.get("pred_id"))
    canonical_name_key = _name_key(entry.get("canonical_name"))
    semantic_key = build_semantic_key(entry)
    return {
        "normalized_pred_id": normalized_pred_id,
        "canonical_name_key": canonical_name_key,
        "semantic_key": semantic_key,
        "family_key": _family_key_from_semantic_key(semantic_key),
        "entry_role": classify_predicate_entry(entry),
    }


def collect_predicate_entries(logic_form_local_dict, node_dict):
    def _sort_key(value):
        try:
            return (0, int(str(value)))
        except (TypeError, ValueError):
            return (1, str(value))

    def _text(value):
        return value if isinstance(value, str) else ""

    def _list(value):
        return value if isinstance(value, list) else []

    def _node_items(nodes):
        if isinstance(nodes, dict):
            return nodes.items()
        if isinstance(nodes, list):
            return ((str(index), node) for index, node in enumerate(nodes))
        return ()

    def _logic_items(items):
        if isinstance(items, dict):
            return ((key, items[key]) for key in sorted(items.keys(), key=_sort_key))
        if isinstance(items, list):
            return ((str(index), item) for index, item in enumerate(items))
        return ()

    def _node_title(node):
        title = node.get("title") if isinstance(node, dict) else None
        if isinstance(title, dict):
            return _text(title.get("chinese")) or _text(title.get("english"))
        return _text(title)

    def _placeholder_name(variable_type, normalize_type):
        raw_type = _text(variable_type).strip() or _text(normalize_type).strip()
        raw_type = re.sub(r"[_\s]+\d+$", "", raw_type)
        placeholder = re.sub(r"[^A-Za-z0-9]+", "_", raw_type).strip("_").upper()
        if placeholder == "FUNCTION":
            placeholder = "FUNC"
        return placeholder or "VAR"

    def _variable_template_map(node):
        variable_map = {}
        variables = node.get("variables") if isinstance(node, dict) else None
        if not isinstance(variables, list):
            return variable_map

        for variable in variables:
            if not isinstance(variable, dict):
                continue
            normalize_type = _text(variable.get("normalize_type")).strip()
            if not normalize_type:
                continue
            placeholder = "{" + _placeholder_name(variable.get("type"), normalize_type) + "}"
            variable_map[normalize_type] = placeholder

        return variable_map

    def _replace_symbol(text, symbol, placeholder):
        pattern = r"(?<![A-Za-z0-9_])" + re.escape(symbol) + r"(?![A-Za-z0-9_])"
        return re.sub(pattern, placeholder, text)

    def _abstract_template(text, variable_template_map):
        result = _text(text)
        for symbol, placeholder in sorted(variable_template_map.items(), key=lambda item: len(item[0]), reverse=True):
            result = _replace_symbol(result, symbol, placeholder)

        def _replace_numbered_symbol(match):
            prefix = match.group(1)
            prefix = re.sub(r"[^A-Za-z0-9]+", "_", prefix).strip("_").upper()
            if prefix == "FUNCTION":
                prefix = "FUNC"
            return "{" + (prefix or "VAR") + "}"

        result = re.sub(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*)_\d+(?![A-Za-z0-9_])", _replace_numbered_symbol, result)
        return re.sub(r"\s+", " ", result).strip()

    def _pred_id_stem(pred_id):
        pred_id = _text(pred_id).strip()
        if not pred_id:
            return ""
        return re.sub(r"_TMP_.*$", "", pred_id)

    match_units = build_match_unit_dict(node_dict)
    global_id_to_node = {}
    key_to_node = {}
    for node_key, node in _node_items(match_units):
        if not isinstance(node, dict):
            continue
        key_to_node[str(node_key)] = node
        global_id = node.get("global_id")
        if isinstance(global_id, str) and global_id and global_id not in global_id_to_node:
            global_id_to_node[global_id] = node

    predicate_entry_list = []
    for source_key, llm_output in _logic_items(logic_form_local_dict):
        if not isinstance(llm_output, dict):
            continue

        source_global_id = _text(llm_output.get("global_id"))
        source_node = key_to_node.get(str(source_key)) or global_id_to_node.get(source_global_id) or {}
        if not source_global_id and isinstance(source_node, dict):
            source_global_id = _text(source_node.get("global_id"))
        source_sub_index = source_node.get("sub_index") if isinstance(source_node, dict) else None
        variable_template_map = _variable_template_map(source_node)
        predicate_entries = llm_output.get("predicate_entries")
        if not isinstance(predicate_entries, list):
            continue

        for entry_index, predicate_entry in enumerate(predicate_entries):
            if not isinstance(predicate_entry, dict):
                continue

            surface_forms = [
                form for form in _list(predicate_entry.get("surface_forms"))
                if isinstance(form, str)
            ]
            surface_template = _text(predicate_entry.get("surface_template"))
            if not surface_template and surface_forms:
                surface_template = surface_forms[0]

            arg_sorts = [
                _text(sort)
                for sort in _list(predicate_entry.get("arg_sorts"))
            ]
            arity = predicate_entry.get("arity")
            try:
                arity = int(arity)
            except (TypeError, ValueError):
                arity = len(arg_sorts)

            pred_id = _text(predicate_entry.get("pred_id"))
            canonical_name = _text(predicate_entry.get("canonical_name"))

            predicate_entry_list.append(
                {
                    "entry_uid": f"{source_key}:{entry_index}",
                    "source_key": str(source_key),
                    "source_global_id": source_global_id,
                    "source_parent_global_id": _text(source_node.get("parent_global_id")) if isinstance(source_node, dict) else source_global_id,
                    "source_sub_index": source_sub_index,
                    "source_node_type": _text(source_node.get("node_type")) if isinstance(source_node, dict) else "",
                    "source_title": _node_title(source_node) if isinstance(source_node, dict) else "",
                    "pred_id": pred_id,
                    "pred_id_stem": _pred_id_stem(pred_id),
                    "canonical_name": canonical_name,
                    "canonical_key": re.sub(r"[^a-z0-9]+", "", canonical_name.lower()),
                    "surface_forms": surface_forms,
                    "surface_template": surface_template,
                    "abstract_surface_template": _abstract_template(surface_template, variable_template_map),
                    "abstract_surface_forms": [_abstract_template(form, variable_template_map) for form in surface_forms],
                    "arity": arity,
                    "arg_sorts": arg_sorts,
                    "status": _text(predicate_entry.get("status")),
                    "gloss": _text(predicate_entry.get("gloss")),
                    "introduced_by_node": _text(predicate_entry.get("introduced_by_node")),
                    "variable_template_map": variable_template_map,
                }
            )

    for predicate_entry in predicate_entry_list:
        predicate_entry.update(normalize_predicate_entry_fields(predicate_entry))

    return predicate_entry_list


def build_predicate_candidate_pairs(predicate_entry_list):
    def _tokens(value):
        text = _as_text(value).lower()
        return set(re.findall(r"\{[^}]+\}|[a-z0-9]+|[\u4e00-\u9fff]+", text))

    def _jaccard(left, right):
        if not left or not right:
            return 0.0
        return len(left & right) / len(left | right)

    def _signature_compatible(left, right):
        if left.get("arity") != right.get("arity"):
            return False
        return _sorts_compatible_global(left.get("arg_sorts") or [], right.get("arg_sorts") or [])

    def _minimal_entry(entry):
        keys = [
            "entry_uid",
            "source_key",
            "source_global_id",
            "source_parent_global_id",
            "source_sub_index",
            "source_node_type",
            "pred_id",
            "pred_id_stem",
            "normalized_pred_id",
            "canonical_name",
            "canonical_name_key",
            "semantic_key",
            "family_key",
            "entry_role",
            "surface_template",
            "abstract_surface_template",
            "arity",
            "arg_sorts",
            "status",
            "gloss",
        ]
        return {key: entry.get(key) for key in keys}

    def _domain_synonym_rule(left, right):
        pair = {left.get("semantic_key"), right.get("semantic_key")}
        if pair == {"NORMAL_SUBGROUP", "SUBGROUP"}:
            return "subgroup_family_candidate"
        if pair <= {"SUBSETKER", "SUBSETOFIRR", "SUBSETDIVZ"} and len(pair) > 1:
            return "subset_family_candidate"
        return ""

    def _match_reasons(left, right):
        reasons = ["signature_compatible"]
        left_pred_id = _as_text(left.get("pred_id"))
        right_pred_id = _as_text(right.get("pred_id"))
        left_stem = _as_text(left.get("pred_id_stem"))
        right_stem = _as_text(right.get("pred_id_stem"))
        left_normalized = _as_text(left.get("normalized_pred_id"))
        right_normalized = _as_text(right.get("normalized_pred_id"))
        left_canonical = _as_text(left.get("canonical_name_key"))
        right_canonical = _as_text(right.get("canonical_name_key"))
        left_semantic = _as_text(left.get("semantic_key"))
        right_semantic = _as_text(right.get("semantic_key"))
        left_family = _as_text(left.get("family_key"))
        right_family = _as_text(right.get("family_key"))
        left_template = re.sub(r"\s+", " ", _as_text(left.get("abstract_surface_template")).lower()).strip()
        right_template = re.sub(r"\s+", " ", _as_text(right.get("abstract_surface_template")).lower()).strip()

        if left_pred_id and left_pred_id == right_pred_id:
            reasons.append("same_pred_id")
        if left_stem and left_stem == right_stem:
            reasons.append("same_pred_id_stem")
        if left_normalized and left_normalized == right_normalized:
            reasons.append("same_normalized_pred_id")
        if left_canonical and left_canonical == right_canonical:
            reasons.append("same_canonical_name")
        if left_semantic and left_semantic == right_semantic:
            reasons.append("same_semantic_key")
        if left_family and left_family == right_family and left_semantic != right_semantic:
            reasons.append("same_family_key_with_compatible_signature")
        if left_template and left_template == right_template:
            reasons.append("same_abstract_surface_template")

        domain_rule = _domain_synonym_rule(left, right)
        if domain_rule:
            reasons.append(domain_rule)

        template_similarity = _jaccard(_tokens(left_template), _tokens(right_template))
        name_gloss_overlap = _jaccard(
            _tokens(left.get("canonical_name")) | _tokens(left.get("gloss")),
            _tokens(right.get("canonical_name")) | _tokens(right.get("gloss")),
        )
        if template_similarity >= 0.72 and name_gloss_overlap > 0:
            reasons.append("similar_template_with_name_or_gloss_overlap")

        return reasons, template_similarity, name_gloss_overlap

    def _score(reasons, template_similarity, name_gloss_overlap):
        score = 0.20
        weights = {
            "same_pred_id": 0.30,
            "same_pred_id_stem": 0.20,
            "same_normalized_pred_id": 0.25,
            "same_canonical_name": 0.20,
            "same_semantic_key": 0.35,
            "same_family_key_with_compatible_signature": 0.16,
            "same_abstract_surface_template": 0.25,
            "similar_template_with_name_or_gloss_overlap": 0.12,
            "subgroup_family_candidate": 0.10,
            "subset_family_candidate": 0.10,
        }
        for reason, weight in weights.items():
            if reason in reasons:
                score += weight
        score += min(template_similarity, 1.0) * 0.08
        score += min(name_gloss_overlap, 1.0) * 0.04
        return round(min(score, 1.0), 4)

    predicate_candidate_pairs = []
    entries = [
        entry for entry in (predicate_entry_list or [])
        if isinstance(entry, dict) and entry.get("entry_role") == "domain_predicate"
    ]
    for left, right in itertools.combinations(entries, 2):
        if not _signature_compatible(left, right):
            continue

        reasons, template_similarity, name_gloss_overlap = _match_reasons(left, right)
        if len(reasons) <= 1:
            continue

        pair_number = len(predicate_candidate_pairs) + 1
        predicate_candidate_pairs.append(
            {
                "pair_id": f"predicate_pair_{pair_number:06d}",
                "left_entry_uid": left.get("entry_uid", ""),
                "right_entry_uid": right.get("entry_uid", ""),
                "left_pred_id": left.get("pred_id", ""),
                "right_pred_id": right.get("pred_id", ""),
                "signature": {
                    "arity": left.get("arity"),
                    "arg_sorts_left": left.get("arg_sorts", []),
                    "arg_sorts_right": right.get("arg_sorts", []),
                },
                "match_reasons": reasons,
                "match_score": _score(reasons, template_similarity, name_gloss_overlap),
                "template_similarity": round(template_similarity, 4),
                "name_gloss_overlap": round(name_gloss_overlap, 4),
                "left": _minimal_entry(left),
                "right": _minimal_entry(right),
            }
        )

    return predicate_candidate_pairs

def build_predicate_candidate_groups(predicate_candidate_pairs, predicate_entry_list):
    def _uid_sort_key(uid):
        parts = str(uid).split(":")
        result = []
        for part in parts:
            try:
                result.append((0, int(part)))
            except ValueError:
                result.append((1, part))
        return result

    def _unique_text(values):
        return sorted({value for value in values if isinstance(value, str) and value})

    def _unique_signatures(entries):
        signatures = []
        seen = set()
        for entry in entries:
            signature = tuple(entry.get("arg_sorts") or [])
            if signature in seen:
                continue
            seen.add(signature)
            signatures.append(list(signature))
        return sorted(signatures, key=lambda item: (len(item), item))

    def _minimal_entry(entry):
        keys = [
            "entry_uid",
            "source_key",
            "source_global_id",
            "source_node_type",
            "pred_id",
            "pred_id_stem",
            "normalized_pred_id",
            "canonical_name",
            "canonical_name_key",
            "semantic_key",
            "family_key",
            "entry_role",
            "surface_template",
            "abstract_surface_template",
            "arity",
            "arg_sorts",
            "status",
            "gloss",
        ]
        return {key: entry.get(key) for key in keys}

    def _minimal_pair(pair):
        keys = [
            "pair_id",
            "left_entry_uid",
            "right_entry_uid",
            "left_pred_id",
            "right_pred_id",
            "signature",
            "match_reasons",
            "match_score",
            "template_similarity",
            "name_gloss_overlap",
        ]
        return {key: pair.get(key) for key in keys}

    def _score(pair):
        try:
            return float(pair.get("match_score", 0.0))
        except (TypeError, ValueError):
            return 0.0

    parent = {}

    def find(uid):
        parent.setdefault(uid, uid)
        if parent[uid] != uid:
            parent[uid] = find(parent[uid])
        return parent[uid]

    def union(left_uid, right_uid):
        left_root = find(left_uid)
        right_root = find(right_uid)
        if left_root != right_root:
            parent[right_root] = left_root

    entry_by_uid = {
        entry.get("entry_uid"): entry
        for entry in (predicate_entry_list or [])
        if isinstance(entry, dict) and entry.get("entry_uid") and entry.get("entry_role") == "domain_predicate"
    }

    valid_pairs = []
    for pair in predicate_candidate_pairs or []:
        if not isinstance(pair, dict):
            continue
        left_uid = pair.get("left_entry_uid")
        right_uid = pair.get("right_entry_uid")
        if left_uid not in entry_by_uid or right_uid not in entry_by_uid:
            continue
        union(left_uid, right_uid)
        valid_pairs.append(pair)

    components = {}
    for uid in parent:
        components.setdefault(find(uid), []).append(uid)

    groups = []
    for entry_uids in components.values():
        entry_uids = sorted(set(entry_uids), key=_uid_sort_key)
        if len(entry_uids) < 2:
            continue

        uid_set = set(entry_uids)
        supporting_pairs = [
            pair for pair in valid_pairs
            if pair.get("left_entry_uid") in uid_set and pair.get("right_entry_uid") in uid_set
        ]
        supporting_pairs = sorted(
            supporting_pairs,
            key=lambda pair: (
                str(pair.get("pair_id", "")),
                _uid_sort_key(pair.get("left_entry_uid", "")),
                _uid_sort_key(pair.get("right_entry_uid", "")),
            ),
        )
        entries = [entry_by_uid[uid] for uid in entry_uids]
        scores = [_score(pair) for pair in supporting_pairs]

        groups.append(
            {
                "candidate_group_id": "",
                "entry_uids": entry_uids,
                "size": len(entry_uids),
                "pred_ids": _unique_text(entry.get("pred_id") for entry in entries),
                "normalized_pred_ids": _unique_text(entry.get("normalized_pred_id") for entry in entries),
                "canonical_names": _unique_text(entry.get("canonical_name") for entry in entries),
                "semantic_key_summary": _unique_text(entry.get("semantic_key") for entry in entries),
                "family_key_summary": _unique_text(entry.get("family_key") for entry in entries),
                "arg_sorts_signatures": _unique_signatures(entries),
                "source_keys": _unique_text(entry.get("source_key") for entry in entries),
                "source_global_ids": _unique_text(entry.get("source_global_id") for entry in entries),
                "source_parent_global_ids": _unique_text(entry.get("source_parent_global_id") for entry in entries),
                "source_node_types": _unique_text(entry.get("source_node_type") for entry in entries),
                "supporting_pair_ids": _unique_text(pair.get("pair_id") for pair in supporting_pairs),
                "max_pair_score": round(max(scores), 4) if scores else 0.0,
                "avg_pair_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
                "entries": [_minimal_entry(entry) for entry in entries],
                "supporting_pairs": [_minimal_pair(pair) for pair in supporting_pairs],
            }
        )

    groups.sort(
        key=lambda group: (
            -group["size"],
            -group["max_pair_score"],
            _uid_sort_key(group["entry_uids"][0]),
        )
    )
    for index, group in enumerate(groups, 1):
        group["candidate_group_id"] = f"predicate_candidate_group_{index:06d}"

    return groups

def build_predicate_cluster_input_dict(predicate_candidate_groups):
    cluster_input_dict = {}
    for index, group in enumerate(predicate_candidate_groups or [], 1):
        if not isinstance(group, dict):
            continue
        group_id = group.get("candidate_group_id") or f"predicate_candidate_group_{index:06d}"
        cluster_input_dict[str(group_id)] = {"pos1": group}
    return cluster_input_dict


def _run_predicate_cluster_tasks(context, index_dict, checkpoint_dir):
    return run_multiprocess_task(
        llm=context.llm,
        parse_method=context.parser.parse_dict,
        data_template=data_template12,
        prompt_template=prompt_template12,
        correction_template=correction_prompt12,
        validator=validation12,
        index_dict=index_dict,
        num_threads=context.num_threads,
        checkpoint=context.checkpoint,
        checkpoint_dir=checkpoint_dir,
    )


def cluster_predicate_candidate_groups(context, predicate_cluster_input_dict):
    if not predicate_cluster_input_dict:
        return {}

    result_dict, failure_report, _ = run_recoverable_task(
        context,
        stage_name=STAGE_NAME,
        input_dict=predicate_cluster_input_dict,
        task_runner=lambda index_dict, checkpoint_dir: _run_predicate_cluster_tasks(context, index_dict, checkpoint_dir),
    )
    if failure_report.get("status") != "resolved":
        return result_dict
    return result_dict


def latest_unresolved_failure_report(context):
    return _latest_unresolved_failure_report(context, STAGE_NAME)


def normalize_cluster_decisions(predicate_cluster_decisions, predicate_candidate_groups):
    def _decision_items(decisions):
        if isinstance(decisions, dict):
            return decisions.items()
        if isinstance(decisions, list):
            return ((str(index), item) for index, item in enumerate(decisions))
        return ()

    def _uid_sort_key(uid):
        parts = str(uid).split(":")
        result = []
        for part in parts:
            try:
                result.append((0, int(part)))
            except ValueError:
                result.append((1, part))
        return result

    raw_by_group_id = {}
    for fallback_group_id, raw_decision in _decision_items(predicate_cluster_decisions):
        if not isinstance(raw_decision, dict):
            continue
        group_id = _as_text(raw_decision.get("candidate_group_id")) or str(fallback_group_id)
        raw_by_group_id[group_id] = raw_decision

    normalized_decisions = {}
    report = []
    for group in predicate_candidate_groups or []:
        if not isinstance(group, dict):
            continue
        group_id = group.get("candidate_group_id")
        valid_uids = [uid for uid in group.get("entry_uids", []) if isinstance(uid, str)]
        valid_uid_set = set(valid_uids)
        raw_decision = raw_by_group_id.get(group_id, {})
        seen = set()
        duplicate_uids = []
        unknown_uids = []
        normalized_clusters = []

        for cluster_index, cluster in enumerate(_as_list(raw_decision.get("clusters")), 1):
            if not isinstance(cluster, dict):
                continue
            member_uids = []
            for uid in _as_list(cluster.get("member_entry_uids")):
                if not isinstance(uid, str):
                    continue
                if uid not in valid_uid_set:
                    unknown_uids.append(uid)
                    continue
                if uid in seen:
                    duplicate_uids.append(uid)
                    continue
                seen.add(uid)
                member_uids.append(uid)
            member_uids = sorted(member_uids, key=_uid_sort_key)
            if len(member_uids) < 2:
                for uid in member_uids:
                    seen.discard(uid)
                continue
            normalized_cluster = dict(cluster)
            normalized_cluster["cluster_id"] = _as_text(cluster.get("cluster_id")) or f"cluster_{cluster_index:06d}"
            normalized_cluster["member_entry_uids"] = member_uids
            normalized_clusters.append(normalized_cluster)

        singleton_uids = []
        for uid in _as_list(raw_decision.get("singleton_entry_uids")):
            if not isinstance(uid, str):
                continue
            if uid not in valid_uid_set:
                unknown_uids.append(uid)
                continue
            if uid in seen:
                duplicate_uids.append(uid)
                continue
            seen.add(uid)
            singleton_uids.append(uid)

        missing_uids = [uid for uid in valid_uids if uid not in seen]
        singleton_uids.extend(missing_uids)
        singleton_uids = sorted(set(singleton_uids), key=_uid_sort_key)

        normalized_decisions[group_id] = {
            "candidate_group_id": group_id,
            "clusters": normalized_clusters,
            "singleton_entry_uids": singleton_uids,
            "notes": _as_text(raw_decision.get("notes")),
        }
        if duplicate_uids or unknown_uids or missing_uids or group_id not in raw_by_group_id:
            report.append(
                {
                    "candidate_group_id": group_id,
                    "missing_decision": group_id not in raw_by_group_id,
                    "duplicate_entry_uids": sorted(set(duplicate_uids), key=_uid_sort_key),
                    "unknown_entry_uids": sorted(set(unknown_uids), key=_uid_sort_key),
                    "auto_singleton_entry_uids": sorted(set(missing_uids), key=_uid_sort_key),
                }
            )

    return normalized_decisions, report

def build_global_predicate_registry(predicate_cluster_decisions, predicate_entry_list):
    def _text(value):
        return value if isinstance(value, str) else ""

    def _list(value):
        return value if isinstance(value, list) else []

    def _uid_sort_key(uid):
        parts = str(uid).split(":")
        result = []
        for part in parts:
            try:
                result.append((0, int(part)))
            except ValueError:
                result.append((1, part))
        return result

    def _unique_text(values):
        result = []
        seen = set()
        for value in values:
            if not isinstance(value, str) or not value or value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    def _most_common(values, default=None):
        normalized = []
        for value in values:
            if isinstance(value, list):
                normalized.append(tuple(value))
            elif value not in (None, ""):
                normalized.append(value)
        if not normalized:
            return default
        value = Counter(normalized).most_common(1)[0][0]
        return list(value) if isinstance(value, tuple) else value

    def _snake_from_name(name):
        text = _text(name).strip()
        if not text:
            return "P_NORMALIZED"
        text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
        text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
        text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").upper()
        if not text:
            return "P_NORMALIZED"
        return text if text.startswith("P_") else f"P_{text}"

    def _safe_registry_key(value, fallback_name, index):
        key = _text(value).strip()
        if not key:
            key = _snake_from_name(fallback_name)
        key = re.sub(r"[^A-Za-z0-9_]+", "_", key).strip("_").upper()
        key = re.sub(r"_TMP_.*$", "", key, flags=re.IGNORECASE)
        key = re.sub(r"_DUP_\d+$", "", key, flags=re.IGNORECASE)
        if not key:
            key = f"P_NORMALIZED_{index:06d}"
        if not key.startswith("P_"):
            key = f"P_{key}"
        return key

    def _unique_registry_key(base_key, registry):
        key = base_key
        suffix = 2
        while key in registry:
            key = f"{base_key}_ALT_{suffix:03d}"
            suffix += 1
        return key

    def _iter_outputs(outputs):
        if isinstance(outputs, dict):
            return outputs.items()
        if isinstance(outputs, list):
            return ((str(index), item) for index, item in enumerate(outputs))
        return ()

    def _iter_cluster_records(outputs):
        for fallback_group_id, output in _iter_outputs(outputs):
            if not isinstance(output, dict):
                continue
            candidate_group_id = _text(output.get("candidate_group_id")) or str(fallback_group_id)
            for cluster_index, cluster in enumerate(_list(output.get("clusters")), 1):
                if not isinstance(cluster, dict):
                    continue
                member_entry_uids = [
                    uid for uid in _list(cluster.get("member_entry_uids"))
                    if isinstance(uid, str) and uid
                ]
                if not member_entry_uids:
                    continue
                yield {
                    "candidate_group_id": candidate_group_id,
                    "cluster_index": cluster_index,
                    "cluster_id": _text(cluster.get("cluster_id")) or f"cluster_{cluster_index:06d}",
                    "member_entry_uids": member_entry_uids,
                    "canonical_pred_id": _text(cluster.get("canonical_pred_id")),
                    "canonical_name": _text(cluster.get("canonical_name")),
                    "arity": cluster.get("arity"),
                    "arg_sorts": _list(cluster.get("arg_sorts")),
                    "aliases": [alias for alias in _list(cluster.get("aliases")) if isinstance(alias, str)],
                    "reason": _text(cluster.get("reason")),
                }

    def _best_pred_id(entries, cluster=None):
        cluster = cluster or {}
        if _text(cluster.get("canonical_pred_id")):
            return _text(cluster.get("canonical_pred_id"))
        if len(entries) == 1:
            pred_id = _text(entries[0].get("pred_id"))
            if pred_id:
                return pred_id

        preferred_status = {"builtin", "defined_here"}
        for entry in entries:
            pred_id = _text(entry.get("pred_id"))
            if pred_id and "_TMP_" not in pred_id and entry.get("status") in preferred_status:
                return pred_id
        for entry in entries:
            pred_id = _text(entry.get("pred_id"))
            if pred_id and "_TMP_" not in pred_id:
                return pred_id
        for entry in entries:
            pred_id_stem = _text(entry.get("pred_id_stem"))
            if pred_id_stem:
                return pred_id_stem
        for entry in entries:
            pred_id = _text(entry.get("pred_id"))
            if pred_id:
                return pred_id
        return ""

    def _registry_record(cluster, entries, registry_index, source):
        canonical_name = _text(cluster.get("canonical_name")) or _most_common(
            (entry.get("canonical_name") for entry in entries),
            "",
        )
        canonical_pred_id = _best_pred_id(entries, cluster)
        arity = cluster.get("arity")
        try:
            arity = int(arity)
        except (TypeError, ValueError):
            arity = _most_common((entry.get("arity") for entry in entries), 0)

        arg_sorts = cluster.get("arg_sorts") if isinstance(cluster.get("arg_sorts"), list) else []
        if not arg_sorts:
            arg_sorts = _most_common((entry.get("arg_sorts") for entry in entries), [])

        aliases = []
        aliases.extend(alias for alias in _list(cluster.get("aliases")) if isinstance(alias, str))
        aliases.extend(_text(entry.get("abstract_surface_template")) for entry in entries)
        for entry in entries:
            aliases.extend(form for form in _list(entry.get("abstract_surface_forms")) if isinstance(form, str))
        if not any(aliases):
            aliases.extend(_text(entry.get("surface_template")) for entry in entries)

        member_predicates = []
        for entry in entries:
            pred_id = _text(entry.get("pred_id"))
            if not pred_id:
                continue
            member_predicates.append(
                {
                    "entry_uid": entry.get("entry_uid"),
                    "source_key": entry.get("source_key"),
                    "source_global_id": entry.get("source_global_id"),
                    "source_parent_global_id": entry.get("source_parent_global_id"),
                    "source_sub_index": entry.get("source_sub_index"),
                    "pred_id": pred_id,
                }
            )

        return {
            "canonical_pred_id": canonical_pred_id,
            "canonical_name": canonical_name,
            "arity": arity,
            "arg_sorts": arg_sorts,
            "aliases": _unique_text(aliases),
            "member_entry_uids": sorted({entry.get("entry_uid") for entry in entries if entry.get("entry_uid")}, key=_uid_sort_key),
            "member_pred_ids": _unique_text(entry.get("pred_id") for entry in entries),
            "member_pred_id_stems": _unique_text(entry.get("pred_id_stem") for entry in entries),
            "member_predicates": member_predicates,
            "semantic_keys": _unique_text(entry.get("semantic_key") for entry in entries),
            "family_keys": _unique_text(entry.get("family_key") for entry in entries),
            "primary_semantic_key": _most_common((entry.get("semantic_key") for entry in entries), ""),
            "source_keys": _unique_text(entry.get("source_key") for entry in entries),
            "source_global_ids": _unique_text(entry.get("source_global_id") for entry in entries),
            "source_parent_global_ids": _unique_text(entry.get("source_parent_global_id") for entry in entries),
            "source_node_types": _unique_text(entry.get("source_node_type") for entry in entries),
            "statuses": _unique_text(entry.get("status") for entry in entries),
            "registry_source": source,
            "candidate_group_id": _text(cluster.get("candidate_group_id")),
            "cluster_id": _text(cluster.get("cluster_id")),
            "cluster_index": cluster.get("cluster_index"),
            "reason": _text(cluster.get("reason")),
            "registry_index": registry_index,
        }

    entries = [
        entry for entry in (predicate_entry_list or [])
        if isinstance(entry, dict) and entry.get("entry_role") == "domain_predicate"
    ]
    entry_by_uid = {entry.get("entry_uid"): entry for entry in entries if entry.get("entry_uid")}

    registry = {}
    used_entry_uids = set()
    registry_index = 1

    for cluster in _iter_cluster_records(predicate_cluster_decisions):
        member_uids = [
            uid for uid in cluster["member_entry_uids"]
            if uid in entry_by_uid and uid not in used_entry_uids
        ]
        if not member_uids:
            continue
        cluster_entries = [entry_by_uid[uid] for uid in sorted(set(member_uids), key=_uid_sort_key)]
        record = _registry_record(cluster, cluster_entries, registry_index, "llm_cluster")
        base_key = _safe_registry_key(record["canonical_pred_id"], record["canonical_name"], registry_index)
        registry_key = _unique_registry_key(base_key, registry)
        record["canonical_pred_id"] = registry_key
        registry[registry_key] = record
        used_entry_uids.update(member_uids)
        registry_index += 1

    for entry in sorted(entries, key=lambda item: _uid_sort_key(item.get("entry_uid", ""))):
        entry_uid = entry.get("entry_uid")
        if not entry_uid or entry_uid in used_entry_uids:
            continue
        record = _registry_record({}, [entry], registry_index, "singleton")
        base_key = _safe_registry_key(record["canonical_pred_id"], record["canonical_name"], registry_index)
        registry_key = _unique_registry_key(base_key, registry)
        record["canonical_pred_id"] = registry_key
        registry[registry_key] = record
        registry_index += 1

    return registry


def merge_registry_duplicates(global_predicate_registry):
    def _merge_unique(left, right):
        result = list(left or [])
        seen = set()
        normalized = []
        for item in result:
            marker = repr(item)
            if marker in seen:
                continue
            seen.add(marker)
            normalized.append(item)
        for item in right or []:
            marker = repr(item)
            if marker in seen:
                continue
            seen.add(marker)
            normalized.append(item)
        return normalized

    def _signature(record):
        return (
            record.get("primary_semantic_key") or "",
            record.get("arity"),
            tuple(record.get("arg_sorts") or []),
        )

    merged_registry = {}
    signature_to_key = {}
    report = []
    for registry_key, record in (global_predicate_registry or {}).items():
        if not isinstance(record, dict):
            continue
        signature = _signature(record)
        existing_key = signature_to_key.get(signature)
        if existing_key and existing_key in merged_registry:
            target = merged_registry[existing_key]
            for list_key in [
                "aliases",
                "member_entry_uids",
                "member_pred_ids",
                "member_pred_id_stems",
                "member_predicates",
                "semantic_keys",
                "family_keys",
                "source_keys",
                "source_global_ids",
                "source_parent_global_ids",
                "source_node_types",
                "statuses",
            ]:
                target[list_key] = _merge_unique(target.get(list_key, []), record.get(list_key, []))
            report.append(
                {
                    "merged_from": registry_key,
                    "merged_into": existing_key,
                    "signature": {
                        "semantic_key": signature[0],
                        "arity": signature[1],
                        "arg_sorts": list(signature[2]),
                    },
                }
            )
            continue

        clean_key = re.sub(r"_TMP_.*$", "", registry_key, flags=re.IGNORECASE)
        clean_key = re.sub(r"_DUP_\d+$", "", clean_key, flags=re.IGNORECASE)
        if clean_key != registry_key and clean_key not in merged_registry:
            registry_key = clean_key
            record = dict(record)
            record["canonical_pred_id"] = clean_key
        signature_to_key[signature] = registry_key
        merged_registry[registry_key] = record

    final_registry = {}
    for registry_key, record in merged_registry.items():
        final_key = re.sub(r"_TMP_.*$", "", registry_key, flags=re.IGNORECASE)
        final_key = re.sub(r"_DUP_(\d+)$", r"_ALT_\1", final_key, flags=re.IGNORECASE)
        if not final_key:
            final_key = registry_key
        base_final_key = final_key
        suffix = 2
        while final_key in final_registry:
            final_key = f"{base_final_key}_ALT_{suffix:03d}"
            suffix += 1
        if final_key != registry_key:
            record = dict(record)
            record["canonical_pred_id"] = final_key
            report.append(
                {
                    "renamed_from": registry_key,
                    "renamed_to": final_key,
                    "reason": "remove_tmp_or_dup_suffix",
                }
            )
        final_registry[final_key] = record

    return final_registry, report

def build_fixed_operator_rewrite_map(predicate_entry_list):
    fixed_operator_rewrite_map = {}
    fixed_operator_misuse_report = []
    for entry in predicate_entry_list or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("entry_role") not in {"fixed_operator", "fixed_relation"}:
            continue
        source_global_id = entry.get("source_global_id")
        source_key = entry.get("source_key")
        pred_id = entry.get("pred_id")
        if not source_key or not pred_id:
            continue
        rewrite_key = f"{source_key}::{pred_id}"
        fixed_operator_rewrite_map[rewrite_key] = {
            "entry_uid": entry.get("entry_uid"),
            "source_key": source_key,
            "source_global_id": source_global_id,
            "source_parent_global_id": entry.get("source_parent_global_id"),
            "source_sub_index": entry.get("source_sub_index"),
            "pred_id": pred_id,
            "semantic_key": entry.get("semantic_key"),
            "family_key": entry.get("family_key"),
            "entry_role": entry.get("entry_role"),
            "arity": entry.get("arity"),
            "arg_sorts": entry.get("arg_sorts", []),
            "surface_template": entry.get("surface_template", ""),
        }
        if entry.get("arity") == 0 and entry.get("semantic_key") not in {"ONE_CONSTANT", "TWO_CONSTANT"}:
            fixed_operator_misuse_report.append(
                {
                    "entry_uid": entry.get("entry_uid"),
                    "source_key": source_key,
                    "source_global_id": source_global_id,
                    "pred_id": pred_id,
                    "semantic_key": entry.get("semantic_key"),
                    "reason": "fixed operator entry has arity 0; AST rewrite requires actual arguments",
                }
            )
    return fixed_operator_rewrite_map, fixed_operator_misuse_report

def build_predicate_rewrite_map(global_predicate_registry):
    predicate_rewrite_map = {}
    for registry_key, registry_entry in (global_predicate_registry or {}).items():
        if not isinstance(registry_entry, dict):
            continue
        canonical_pred_id = registry_entry.get("canonical_pred_id") or registry_key
        for member in registry_entry.get("member_predicates") or []:
            if not isinstance(member, dict):
                continue
            source_key = member.get("source_key")
            pred_id = member.get("pred_id")
            if not source_key or not pred_id:
                continue
            predicate_rewrite_map[f"{source_key}::{pred_id}"] = canonical_pred_id
    return predicate_rewrite_map


def rewrite_node_logic_ast_predicates(node_dict, predicate_rewrite_map, fixed_operator_rewrite_map=None):
    fixed_operator_rewrite_map = fixed_operator_rewrite_map or {}
    fixed_operator_misuse_report = []

    def _misuse(source_global_id, pred_id, rule, args, reason):
        fixed_operator_misuse_report.append(
            {
                "source_global_id": source_global_id,
                "pred_id": pred_id,
                "semantic_key": (rule or {}).get("semantic_key"),
                "args_count": len(args) if isinstance(args, list) else None,
                "reason": reason,
            }
        )

    def _fixed_app(name, args):
        return {"kind": "app", "fn": {"kind": "sym_ref", "sym_id": name}, "args": args}

    def _convert_fixed_pred(node, source_global_id, rule, parent_kind):
        pred_id = node.get("pred_id")
        args = node.get("args")
        if not isinstance(args, list):
            _misuse(source_global_id, pred_id, rule, [], "fixed predicate node is missing list field 'args'")
            return node

        semantic_key = rule.get("semantic_key")
        if semantic_key == "IRR_MEMBERSHIP":
            if len(args) >= 2:
                return {"kind": "in", "element": args[0], "set": _fixed_app("Irr", [args[1]])}
            _misuse(source_global_id, pred_id, rule, args, "Irr membership rewrite requires character and group arguments")
            return node
        if semantic_key == "SUBSET_RELATION":
            if len(args) >= 2:
                return {"kind": "subset", "left": args[0], "right": args[1]}
            _misuse(source_global_id, pred_id, rule, args, "Subset rewrite requires two arguments")
            return node
        if semantic_key == "POWER_OPERATOR":
            if len(args) >= 2:
                return {"kind": "power", "base": args[0], "exponent": args[1]}
            _misuse(source_global_id, pred_id, rule, args, "Power rewrite requires base and exponent arguments")
            return node
        if semantic_key == "DIV_OPERATOR":
            if len(args) >= 2:
                return {"kind": "div", "left": args[0], "right": args[1]}
            _misuse(source_global_id, pred_id, rule, args, "Div rewrite requires two arguments")
            return node
        if semantic_key == "ORDER_OPERATOR":
            if len(args) >= 1:
                return {"kind": "order", "arg": args[0]}
            _misuse(source_global_id, pred_id, rule, args, "Order rewrite requires one argument")
            return node
        if semantic_key == "MUL_OPERATOR":
            if len(args) >= 2:
                return {"kind": "mul", "args": args}
            _misuse(source_global_id, pred_id, rule, args, "Mul rewrite requires at least two arguments")
            return node
        if semantic_key == "ONE_CONSTANT":
            if not args:
                return {"kind": "int", "value": 1}
            _misuse(source_global_id, pred_id, rule, args, "One constant rewrite expects zero arguments")
            return node
        if semantic_key == "TWO_CONSTANT":
            if not args:
                return {"kind": "int", "value": 2}
            _misuse(source_global_id, pred_id, rule, args, "Two constant rewrite expects zero arguments")
            return node
        if semantic_key == "IND_POWER_OPERATOR":
            if len(args) >= 3 and parent_kind in {"eq", "app", "power", "mul", "sum", "in"}:
                return _fixed_app("IndPower", args)
            _misuse(source_global_id, pred_id, rule, args, "IndPower is a term operator; predicate position or missing args is unsafe to rewrite")
            return node
        if semantic_key == "RES_POWER_OPERATOR":
            if len(args) >= 3 and parent_kind in {"eq", "app", "power", "mul", "sum", "in"}:
                return _fixed_app("ResPower", args)
            _misuse(source_global_id, pred_id, rule, args, "ResPower is a term operator; predicate position or missing args is unsafe to rewrite")
            return node
        return node

    def _rewrite_ast(node, source_key, source_global_id, parent_kind=""):
        if isinstance(node, dict):
            if node.get("kind") == "pred":
                pred_id = node.get("pred_id")
                rewrite_key = f"{source_key}::{pred_id}"
                if pred_id and rewrite_key in fixed_operator_rewrite_map:
                    return _convert_fixed_pred(node, source_global_id, fixed_operator_rewrite_map[rewrite_key], parent_kind)
                if pred_id and rewrite_key in predicate_rewrite_map:
                    node["pred_id"] = predicate_rewrite_map[rewrite_key]
            current_kind = node.get("kind", "")
            for key, value in list(node.items()):
                if isinstance(value, (dict, list)):
                    node[key] = _rewrite_ast(value, source_key, source_global_id, current_kind)
            return node
        if isinstance(node, list):
            return [
                _rewrite_ast(item, source_key, source_global_id, parent_kind) if isinstance(item, (dict, list)) else item
                for item in node
            ]
        return node

    match_units = build_match_unit_dict(node_dict)
    for source_key, unit in match_units.items():
        if not isinstance(unit, dict):
            continue
        source_global_id = unit.get("global_id")
        logic_ast_local = unit.get("logic_ast_local")
        if not source_global_id or not isinstance(logic_ast_local, dict):
            continue
        rewritten_ast = _rewrite_ast(logic_ast_local, source_key, source_global_id)
        attach_fields_to_match_unit(
            node_dict,
            source_key,
            {
                "logic_ast_local": rewritten_ast,
                "logic_form_rendered": render_logic_ast_local(rewritten_ast),
            },
        )

    return node_dict, fixed_operator_misuse_report

def run(context, state):
    node_dict = state["node_dict"]

    predicate_entry_list = collect_predicate_entries(state["logic_form_local_dict"], node_dict)
    save_stage_json(context.output_dir, "predicate_entry_list.json", predicate_entry_list, "Predicate entry list")

    fixed_operator_rewrite_map, initial_fixed_operator_misuse_report = build_fixed_operator_rewrite_map(
        predicate_entry_list
    )
    save_stage_json(
        context.output_dir,
        "fixed_operator_rewrite_map.json",
        fixed_operator_rewrite_map,
        "Fixed operator rewrite map",
    )

    predicate_candidate_pairs = build_predicate_candidate_pairs(predicate_entry_list)
    save_stage_json(
        context.output_dir,
        "predicate_candidate_pairs.json",
        predicate_candidate_pairs,
        "Predicate candidate pairs",
    )

    predicate_candidate_groups = build_predicate_candidate_groups(predicate_candidate_pairs, predicate_entry_list)
    save_stage_json(
        context.output_dir,
        "predicate_candidate_groups.json",
        predicate_candidate_groups,
        "Predicate candidate groups",
    )

    predicate_cluster_input_dict = build_predicate_cluster_input_dict(predicate_candidate_groups)
    save_stage_json(
        context.output_dir,
        "predicate_cluster_input_dict.json",
        predicate_cluster_input_dict,
        "Predicate cluster input dict",
    )

    if predicate_cluster_input_dict:
        raw_predicate_cluster_decisions, failure_report, run_dir = run_recoverable_task(
            context,
            stage_name=STAGE_NAME,
            input_dict=predicate_cluster_input_dict,
            task_runner=lambda index_dict, checkpoint_dir: _run_predicate_cluster_tasks(context, index_dict, checkpoint_dir),
        )
        if failure_report.get("status") != "resolved":
            state["normalize_predicates_stage_run"] = failure_report
            if getattr(context, "execution_mode", "pipeline") != "pipeline":
                return state
    else:
        raw_predicate_cluster_decisions = {}
        run_dir = None
        failure_report = {"attempt_rounds": 0}
    save_stage_json(
        context.output_dir,
        "predicate_cluster_decisions_raw.json",
        raw_predicate_cluster_decisions,
        "Raw predicate cluster decisions",
    )

    predicate_cluster_decisions, predicate_cluster_decision_normalization_report = normalize_cluster_decisions(
        raw_predicate_cluster_decisions,
        predicate_candidate_groups,
    )
    save_stage_json(
        context.output_dir,
        "predicate_cluster_decisions.json",
        predicate_cluster_decisions,
        "Predicate cluster decisions",
    )
    save_stage_json(
        context.output_dir,
        "predicate_cluster_decision_normalization_report.json",
        predicate_cluster_decision_normalization_report,
        "Predicate cluster decision normalization report",
    )

    global_predicate_registry = build_global_predicate_registry(
        predicate_cluster_decisions,
        predicate_entry_list,
    )
    global_predicate_registry, registry_duplicate_merge_report = merge_registry_duplicates(
        global_predicate_registry
    )
    save_stage_json(
        context.output_dir,
        "global_predicate_registry.json",
        global_predicate_registry,
        "Global predicate registry",
    )
    save_stage_json(
        context.output_dir,
        "registry_duplicate_merge_report.json",
        registry_duplicate_merge_report,
        "Registry duplicate merge report",
    )

    predicate_rewrite_map = build_predicate_rewrite_map(global_predicate_registry)
    save_stage_json(
        context.output_dir,
        "predicate_rewrite_map.json",
        predicate_rewrite_map,
        "Predicate rewrite map",
    )

    node_dict, rewrite_fixed_operator_misuse_report = rewrite_node_logic_ast_predicates(
        node_dict,
        predicate_rewrite_map,
        fixed_operator_rewrite_map,
    )
    fixed_operator_misuse_report = initial_fixed_operator_misuse_report + rewrite_fixed_operator_misuse_report
    save_stage_json(
        context.output_dir,
        "fixed_operator_misuse_report.json",
        fixed_operator_misuse_report,
        "Fixed operator misuse report",
    )
    save_stage_json(
        context.output_dir,
        "node_dict_after_predicate_normalization.json",
        node_dict,
        "Node dict after predicate normalization",
    )
    if run_dir is not None:
        write_failure_report(
            run_dir,
            run_dir.name,
            STAGE_NAME,
            [str(key) for key in predicate_cluster_input_dict.keys()],
            raw_predicate_cluster_decisions,
            attempts=failure_report.get("attempt_rounds") or 1,
            canonical_updated=True,
        )

    state["predicate_entry_list"] = predicate_entry_list
    state["fixed_operator_rewrite_map"] = fixed_operator_rewrite_map
    state["fixed_operator_misuse_report"] = fixed_operator_misuse_report
    state["predicate_candidate_pairs"] = predicate_candidate_pairs
    state["predicate_candidate_groups"] = predicate_candidate_groups
    state["predicate_cluster_input_dict"] = predicate_cluster_input_dict
    state["raw_predicate_cluster_decisions"] = raw_predicate_cluster_decisions
    state["predicate_cluster_decisions"] = predicate_cluster_decisions
    state["predicate_cluster_decision_normalization_report"] = predicate_cluster_decision_normalization_report
    state["global_predicate_registry"] = global_predicate_registry
    state["registry_duplicate_merge_report"] = registry_duplicate_merge_report
    state["predicate_rewrite_map"] = predicate_rewrite_map
    state["node_dict"] = node_dict
    state["node_list"] = list(node_dict.values()) if isinstance(node_dict, dict) else list(node_dict or [])
    return state


def rerun_failed_tasks(context, state, max_rounds=2):
    node_dict = state["node_dict"]

    predicate_entry_list = collect_predicate_entries(state["logic_form_local_dict"], node_dict)
    save_stage_json(context.output_dir, "predicate_entry_list.json", predicate_entry_list, "Predicate entry list")

    fixed_operator_rewrite_map, initial_fixed_operator_misuse_report = build_fixed_operator_rewrite_map(
        predicate_entry_list
    )
    save_stage_json(
        context.output_dir,
        "fixed_operator_rewrite_map.json",
        fixed_operator_rewrite_map,
        "Fixed operator rewrite map",
    )

    predicate_candidate_pairs = build_predicate_candidate_pairs(predicate_entry_list)
    save_stage_json(
        context.output_dir,
        "predicate_candidate_pairs.json",
        predicate_candidate_pairs,
        "Predicate candidate pairs",
    )

    predicate_candidate_groups = build_predicate_candidate_groups(predicate_candidate_pairs, predicate_entry_list)
    save_stage_json(
        context.output_dir,
        "predicate_candidate_groups.json",
        predicate_candidate_groups,
        "Predicate candidate groups",
    )

    predicate_cluster_input_dict = build_predicate_cluster_input_dict(predicate_candidate_groups)
    save_stage_json(
        context.output_dir,
        "predicate_cluster_input_dict.json",
        predicate_cluster_input_dict,
        "Predicate cluster input dict",
    )

    raw_predicate_cluster_decisions, failure_report, run_dir = rerun_unresolved_task_report(
        context,
        stage_name=STAGE_NAME,
        task_runner=lambda index_dict, checkpoint_dir: _run_predicate_cluster_tasks(context, index_dict, checkpoint_dir),
        max_rounds=max_rounds,
    )
    if failure_report.get("status") != "resolved":
        state["normalize_predicates_stage_run"] = failure_report
        return state, failure_report

    save_stage_json(
        context.output_dir,
        "predicate_cluster_decisions_raw.json",
        raw_predicate_cluster_decisions,
        "Raw predicate cluster decisions",
    )

    predicate_cluster_decisions, predicate_cluster_decision_normalization_report = normalize_cluster_decisions(
        raw_predicate_cluster_decisions,
        predicate_candidate_groups,
    )
    save_stage_json(
        context.output_dir,
        "predicate_cluster_decisions.json",
        predicate_cluster_decisions,
        "Predicate cluster decisions",
    )
    save_stage_json(
        context.output_dir,
        "predicate_cluster_decision_normalization_report.json",
        predicate_cluster_decision_normalization_report,
        "Predicate cluster decision normalization report",
    )

    global_predicate_registry = build_global_predicate_registry(
        predicate_cluster_decisions,
        predicate_entry_list,
    )
    global_predicate_registry, registry_duplicate_merge_report = merge_registry_duplicates(
        global_predicate_registry
    )
    save_stage_json(
        context.output_dir,
        "global_predicate_registry.json",
        global_predicate_registry,
        "Global predicate registry",
    )
    save_stage_json(
        context.output_dir,
        "registry_duplicate_merge_report.json",
        registry_duplicate_merge_report,
        "Registry duplicate merge report",
    )

    predicate_rewrite_map = build_predicate_rewrite_map(global_predicate_registry)
    save_stage_json(
        context.output_dir,
        "predicate_rewrite_map.json",
        predicate_rewrite_map,
        "Predicate rewrite map",
    )

    node_dict, rewrite_fixed_operator_misuse_report = rewrite_node_logic_ast_predicates(
        node_dict,
        predicate_rewrite_map,
        fixed_operator_rewrite_map,
    )
    fixed_operator_misuse_report = initial_fixed_operator_misuse_report + rewrite_fixed_operator_misuse_report
    save_stage_json(
        context.output_dir,
        "fixed_operator_misuse_report.json",
        fixed_operator_misuse_report,
        "Fixed operator misuse report",
    )
    save_stage_json(
        context.output_dir,
        "node_dict_after_predicate_normalization.json",
        node_dict,
        "Node dict after predicate normalization",
    )
    write_failure_report(
        run_dir,
        run_dir.name,
        STAGE_NAME,
        [str(key) for key in predicate_cluster_input_dict.keys()],
        raw_predicate_cluster_decisions,
        attempts=failure_report.get("attempt_rounds") or 1,
        canonical_updated=True,
    )

    state["predicate_entry_list"] = predicate_entry_list
    state["fixed_operator_rewrite_map"] = fixed_operator_rewrite_map
    state["fixed_operator_misuse_report"] = fixed_operator_misuse_report
    state["predicate_candidate_pairs"] = predicate_candidate_pairs
    state["predicate_candidate_groups"] = predicate_candidate_groups
    state["predicate_cluster_input_dict"] = predicate_cluster_input_dict
    state["raw_predicate_cluster_decisions"] = raw_predicate_cluster_decisions
    state["predicate_cluster_decisions"] = predicate_cluster_decisions
    state["predicate_cluster_decision_normalization_report"] = predicate_cluster_decision_normalization_report
    state["global_predicate_registry"] = global_predicate_registry
    state["registry_duplicate_merge_report"] = registry_duplicate_merge_report
    state["predicate_rewrite_map"] = predicate_rewrite_map
    state["node_dict"] = node_dict
    state["node_list"] = list(node_dict.values()) if isinstance(node_dict, dict) else list(node_dict or [])
    return state, {**failure_report, "status": "resolved", "canonical_updated": True}





