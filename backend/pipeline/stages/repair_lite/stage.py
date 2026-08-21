from ...common.io import save_stage_json
from ...common.node import get_node_content, get_node_proof
from ..extract_references.alias import normalize_label
from ..extract_references.resolver import build_label_index


GENERIC_TRIGGER_PHRASES = (
    "由上述受到启发",
    "由上面的讨论可知",
    "由此可见",
)


def _source_text(node):
    parts = []
    content = get_node_content(node)
    if content:
        parts.append(content)
    proof = get_node_proof(node)
    if proof:
        parts.append(proof)
    return "\n".join(parts)


def _init_repair_lite(node):
    node["repair_lite"] = {
        "applied_fixes": [],
        "suppressed_flags": [],
        "forward_repairs": [],
        "notes": [],
    }


def _build_candidate_maps(node_list):
    index = build_label_index(node_list)
    by_type_number_all = {}
    by_alias_all = {}

    for idx, node in enumerate(node_list):
        if not isinstance(node, dict):
            continue
        anchor = node.get("surface_anchor") or {}
        label_text = anchor.get("label_text") or ""
        node_type = anchor.get("node_type") or ""
        aliases = node.get("reference_aliases") or []

        nt, num = None, None
        if label_text:
            nt, num = _extract_type_number(label_text, node_type)
        if nt and num:
            by_type_number_all.setdefault((nt, num), []).append(idx)

        for alias in aliases:
            key = normalize_label(alias)
            if key:
                by_alias_all.setdefault(key, []).append(idx)

    index["by_type_number_all"] = by_type_number_all
    index["by_alias_all"] = by_alias_all
    return index


def _extract_type_number(label_text, node_type_hint):
    from ..extract_references.alias import extract_node_type_and_number

    return extract_node_type_and_number(label_text, node_type_hint)


def _try_repair_forward_reference(node_idx, hit, index, repair_lite):
    if hit.get("match_mode") != "unresolved":
        return False

    nt = hit.get("ref_type")
    num = hit.get("number")
    if nt and num:
        candidates = index["by_type_number_all"].get((nt, num), [])
        forward = [idx for idx in candidates if idx > node_idx]
        if len(candidates) == 1 and len(forward) == 1:
            hit["resolved_index"] = forward[0]
            hit["match_mode"] = "forward_label_exact"
            repair_lite["applied_fixes"].append("forward_reference_repair")
            repair_lite["forward_repairs"].append(
                {
                    "surface": hit.get("surface"),
                    "resolved_index": forward[0],
                    "repair_mode": "forward_label_exact",
                }
            )
            return True

    key = normalize_label(hit.get("surface", ""))
    if key:
        candidates = index["by_alias_all"].get(key, [])
        forward = [idx for idx in candidates if idx > node_idx]
        if len(candidates) == 1 and len(forward) == 1:
            hit["resolved_index"] = forward[0]
            hit["match_mode"] = "forward_label_normalized"
            repair_lite["applied_fixes"].append("forward_reference_repair")
            repair_lite["forward_repairs"].append(
                {
                    "surface": hit.get("surface"),
                    "resolved_index": forward[0],
                    "repair_mode": "forward_label_normalized",
                }
            )
            return True
    return False


def _should_suppress_trigger_without_target(node, signals):
    explicit_hits = signals.get("explicit_targets") or []
    relative_hits = signals.get("relative_references") or []
    formula_hits = signals.get("formula_references") or []
    named_hits = signals.get("named_references") or []

    if explicit_hits or relative_hits or formula_hits or named_hits:
        return False

    triggers = signals.get("reference_triggers") or []
    if not triggers:
        return False

    text = _source_text(node)
    if any(phrase in text for phrase in GENERIC_TRIGGER_PHRASES):
        return True

    trigger_surfaces = {item.get("surface") for item in triggers}
    if trigger_surfaces and trigger_surfaces.issubset({"由", "因此", "于是", "推出", "可知", "得到"}):
        return True
    return False


def _normalize_repair_lite(node):
    repair_lite = node.get("repair_lite") or {}
    for key in ("applied_fixes", "suppressed_flags"):
        if key in repair_lite:
            repair_lite[key] = sorted(set(repair_lite[key]))
    node["repair_lite"] = repair_lite


def _collect_repair_report(node_list):
    report = []
    for idx, node in enumerate(node_list):
        if not isinstance(node, dict):
            continue
        repair_lite = node.get("repair_lite") or {}
        if not any(repair_lite.get(key) for key in ("applied_fixes", "suppressed_flags", "forward_repairs", "notes")):
            continue
        report.append(
            {
                "node_index": idx,
                "label": (node.get("surface_anchor") or {}).get("label_text"),
                "title": (node.get("surface_anchor") or {}).get("title_text"),
                "repair_lite": repair_lite,
                "reference_signals": node.get("reference_signals") or {},
            }
        )
    return report


def run(context, state):
    node_list = state.get("node_list") or []
    if not node_list:
        return state

    index = _build_candidate_maps(node_list)

    for idx, node in enumerate(node_list):
        if not isinstance(node, dict):
            continue

        _init_repair_lite(node)
        repair_lite = node["repair_lite"]
        signals = node.get("reference_signals") or {}

        repaired_forward = False
        for hit in signals.get("explicit_targets") or []:
            if _try_repair_forward_reference(idx, hit, index, repair_lite):
                repaired_forward = True

        if repaired_forward:
            flags = signals.get("repair_flags") or []
            if "unresolved_explicit_reference" in flags:
                remaining_unresolved = any(
                    hit.get("match_mode") == "unresolved" for hit in signals.get("explicit_targets") or []
                )
                if not remaining_unresolved:
                    signals["repair_flags"] = [flag for flag in flags if flag != "unresolved_explicit_reference"]

        flags = signals.get("repair_flags") or []
        if "trigger_without_target" in flags and _should_suppress_trigger_without_target(node, signals):
            signals["repair_flags"] = [flag for flag in flags if flag != "trigger_without_target"]
            repair_lite["applied_fixes"].append("suppress_trigger_without_target")
            repair_lite["suppressed_flags"].append("trigger_without_target")

        _normalize_repair_lite(node)

    state["node_list"] = node_list
    state["node_dict"] = {i: node for i, node in enumerate(node_list)}
    save_stage_json(
        context.output_dir,
        "repair_lite_report.json",
        _collect_repair_report(node_list),
        "Rule-based lightweight repairs after extract_references",
    )
    return state
