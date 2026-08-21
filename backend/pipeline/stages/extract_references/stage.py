from ...common.io import save_stage_json
from ...common.node import get_node_content, get_node_label, get_node_node_type, get_node_proof, get_node_title
from .alias import expand_aliases, extract_anchor_terms
from .resolver import build_label_index, resolve_explicit_targets, resolve_relative_references, resolve_title_fallback
from .rules import find_explicit_refs, find_formula_refs, find_named_refs, find_relative_node_refs, find_tex_refs, find_triggers


def _source_text(node):
    parts = []
    content = get_node_content(node)
    if content:
        parts.append(content)
    proof = get_node_proof(node)
    if proof:
        parts.append(proof)
    return "\n".join(parts)


def _init_node_reference_fields(node, idx):
    context_texts = node.get("context") if isinstance(node.get("context"), list) else []
    anchor = {
        "label_text": get_node_label(node) or "",
        "title_text": get_node_title(node) or "",
        "node_type": get_node_node_type(node) or "",
        "context_texts": context_texts,
        "anchor_terms": extract_anchor_terms(get_node_title(node) or "", context_texts),
    }
    node["locator"] = {
        "node_index_in_doc": idx,
        "reorder_id": node.get("_reorder_id"),
    }
    node["surface_anchor"] = anchor
    node["reference_aliases"] = expand_aliases(
        anchor["label_text"], anchor["title_text"], anchor["node_type"], context_texts=anchor["context_texts"]
    )
    node["reference_signals"] = {
        "explicit_targets": [],
        "relative_references": [],
        "formula_references": [],
        "named_references": [],
        "reference_triggers": [],
        "repair_flags": [],
    }


def _compute_signals(node_list, max_relative_distance=30):
    index = build_label_index(node_list)

    for idx, node in enumerate(node_list):
        if not isinstance(node, dict):
            continue
        text = _source_text(node)
        if not text:
            continue

        explicit_hits = find_explicit_refs(text)
        explicit_hits.extend(find_tex_refs(text))
        formula_hits = find_formula_refs(text)
        relative_hits = find_relative_node_refs(text)
        named_hits = find_named_refs(text)
        triggers = find_triggers(text)

        explicit_resolved = resolve_explicit_targets(idx, explicit_hits, index)
        explicit_resolved = resolve_title_fallback(idx, explicit_resolved, index, node_list)
        relative_resolved = resolve_relative_references(
            idx, relative_hits, node_list, max_distance=max_relative_distance
        )

        signals = node["reference_signals"]
        signals["explicit_targets"] = explicit_resolved
        signals["relative_references"] = relative_resolved
        signals["formula_references"] = formula_hits
        signals["named_references"] = named_hits
        signals["reference_triggers"] = triggers

        flags = []
        if any(h["match_mode"] == "unresolved" for h in explicit_resolved):
            flags.append("unresolved_explicit_reference")
        if any(h["match_mode"] == "unresolved" for h in relative_resolved):
            flags.append("relative_reference_no_anchor")
        if named_hits:
            flags.append("named_reference_recorded")
        if formula_hits:
            flags.append("formula_reference_recorded")
        has_target = (
            any(h.get("resolved_index") is not None for h in explicit_resolved)
            or any(h.get("resolved_index") is not None for h in relative_resolved)
            or bool(named_hits)
            or bool(formula_hits)
        )
        if triggers and not has_target:
            flags.append("trigger_without_target")
        signals["repair_flags"] = flags


def _collect_unresolved(node_list):
    unresolved = []
    for idx, node in enumerate(node_list):
        if not isinstance(node, dict):
            continue
        signals = node.get("reference_signals") or {}
        flags = signals.get("repair_flags") or []
        if not flags:
            continue
        unresolved.append({
            "node_index": idx,
            "reorder_id": (node.get("locator") or {}).get("reorder_id"),
            "label": (node.get("surface_anchor") or {}).get("label_text"),
            "repair_flags": flags,
            "explicit_targets": signals.get("explicit_targets", []),
            "relative_references": signals.get("relative_references", []),
            "formula_references": signals.get("formula_references", []),
            "named_references": signals.get("named_references", []),
            "reference_triggers": signals.get("reference_triggers", []),
        })
    return unresolved


def run(context, state):
    node_dict = state.get("node_dict") or {}
    node_list = list(node_dict.values()) if node_dict else list(state.get("node_list") or [])
    if not node_list:
        return state

    for idx, node in enumerate(node_list):
        if isinstance(node, dict):
            _init_node_reference_fields(node, idx)

    _compute_signals(node_list)

    new_dict = {i: node for i, node in enumerate(node_list)}
    state["node_dict"] = new_dict
    state["node_list"] = node_list

    save_stage_json(
        context.output_dir,
        "references_dict.json",
        new_dict,
        "References signals per node",
    )
    save_stage_json(
        context.output_dir,
        "references_unresolved.json",
        _collect_unresolved(node_list),
        "Unresolved references for repair_lite",
    )
    return state
