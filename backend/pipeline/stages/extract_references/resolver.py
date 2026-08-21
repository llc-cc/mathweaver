from .alias import extract_node_type_and_number, normalize_label


def build_label_index(node_list):
    by_type_number = {}
    by_type_number_all = {}
    by_alias = {}
    by_alias_all = {}
    by_title = {}
    for idx, node in enumerate(node_list):
        if not isinstance(node, dict):
            continue
        anchor = node.get("surface_anchor") or {}
        aliases = node.get("reference_aliases") or []
        label_text = anchor.get("label_text") or ""
        tex_label_key = node.get("tex_label_key") or label_text
        title_text = anchor.get("title_text") or ""
        node_type = anchor.get("node_type") or ""

        nt, num = extract_node_type_and_number(label_text, node_type)
        if nt and num:
            by_type_number.setdefault((nt, num), idx)
            by_type_number_all.setdefault((nt, num), []).append(idx)

        for alias in aliases:
            key = normalize_label(alias)
            if key:
                by_alias.setdefault(key, idx)
                by_alias_all.setdefault(key, []).append(idx)
        if tex_label_key:
            key = normalize_label(tex_label_key)
            if key:
                by_alias.setdefault(key, idx)
                by_alias_all.setdefault(key, []).append(idx)

        if title_text and len(title_text.strip()) >= 4:
            by_title.setdefault(title_text.strip(), []).append(idx)

    return {
        "by_type_number": by_type_number,
        "by_type_number_all": by_type_number_all,
        "by_alias": by_alias,
        "by_alias_all": by_alias_all,
        "by_title": by_title,
    }


def _pick_nearest_previous(candidates, node_idx):
    candidates = sorted(set(candidates))
    previous = [idx for idx in candidates if idx < node_idx]
    if not previous:
        return None
    return max(previous)


def resolve_explicit_targets(node_idx, explicit_hits, index):
    results = []
    for hit in explicit_hits:
        nt = hit.get("node_type")
        num = hit.get("number")
        label_key = hit.get("label_key")
        target_idx = None
        match_mode = None
        candidate_indices = []

        if label_key:
            key = normalize_label(label_key)
            candidate_indices = sorted(set(index["by_alias_all"].get(key, [])))
            target_idx = _pick_nearest_previous(candidate_indices, node_idx)
            if target_idx is not None:
                match_mode = "tex_label"

        if target_idx is None and nt and num:
            candidate_indices = sorted(set(index["by_type_number_all"].get((nt, num), [])))
            target_idx = _pick_nearest_previous(candidate_indices, node_idx)
            if target_idx is not None:
                match_mode = "label_exact"

        if target_idx is None:
            key = normalize_label(hit.get("surface", ""))
            candidate_indices = sorted(set(index["by_alias_all"].get(key, [])))
            target_idx = _pick_nearest_previous(candidate_indices, node_idx)
            if target_idx is not None:
                match_mode = "label_normalized"

        if target_idx is None:
            result = {
                "surface": hit.get("surface"),
                "ref_type": nt,
                "number": num,
                "label_key": label_key,
                "match_mode": "unresolved",
                "resolved_index": None,
            }
            if len(candidate_indices) > 1:
                result["candidate_indices"] = candidate_indices
                result["ambiguity_hint"] = "duplicate_label_or_alias"
            elif len(candidate_indices) == 1 and candidate_indices[0] >= node_idx:
                result["candidate_indices"] = candidate_indices
                result["ambiguity_hint"] = "only_forward_candidate"
            results.append(result)
            continue

        results.append({
            "surface": hit.get("surface"),
            "ref_type": nt,
            "number": num,
            "label_key": label_key,
            "match_mode": match_mode,
            "resolved_index": target_idx,
            "candidate_indices": candidate_indices if len(candidate_indices) > 1 else None,
        })
    return results


def resolve_relative_references(node_idx, relative_hits, node_list, max_distance=30):
    results = []
    for hit in relative_hits:
        target_type = hit.get("node_type")
        target_idx = None
        lower = max(0, node_idx - max_distance)
        for i in range(node_idx - 1, lower - 1, -1):
            candidate = node_list[i]
            if not isinstance(candidate, dict):
                continue
            anchor = candidate.get("surface_anchor") or {}
            if anchor.get("node_type") == target_type:
                target_idx = i
                break
        results.append({
            "surface": hit.get("surface"),
            "ref_type": target_type,
            "match_mode": "relative_nearest_previous" if target_idx is not None else "unresolved",
            "resolved_index": target_idx,
        })
    return results


def resolve_title_fallback(node_idx, unresolved_explicit, index, node_list):
    for hit in unresolved_explicit:
        if hit.get("match_mode") != "unresolved":
            continue
        surface = hit.get("surface") or ""
        for title, indices in index["by_title"].items():
            if len(indices) != 1:
                continue
            if title in surface or surface in title:
                cand = indices[0]
                if cand < node_idx:
                    hit["resolved_index"] = cand
                    hit["match_mode"] = "title_exact"
                    break
    return unresolved_explicit
