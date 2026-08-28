import copy
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.stages.build_relations.stage import _attach_global_ids, extract_explicit_relations
from pipeline.stages.extract_references import stage as extract_stage
from pipeline.stages.repair_lite import stage as repair_stage


DEFAULT_BOOK_DIR = ROOT / "test_output" / "高等代数-丘维声-上册-第三章节选"
DEFAULT_NODE_PATH = DEFAULT_BOOK_DIR / "_stage_cache" / "node_dict.json"


def _load_node_dict(path: Path):
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, dict):
        return {int(k): v for k, v in payload.items()}
    if isinstance(payload, list):
        return {idx: node for idx, node in enumerate(payload)}
    raise TypeError(f"Unsupported payload type: {type(payload).__name__}")


def _count_explicit_unresolved(node_list):
    return sum(
        1
        for node in node_list
        for hit in (node.get("reference_signals", {}).get("explicit_targets", []))
        if hit.get("match_mode") == "unresolved"
    )


def _count_flag(node_list, flag):
    return sum(1 for node in node_list if flag in (node.get("reference_signals", {}).get("repair_flags", [])))


def _duplicate_labels(node_list):
    counts = Counter()
    rows = defaultdict(list)
    for idx, node in enumerate(node_list):
        label = (node.get("label") or "").strip()
        if not label:
            continue
        counts[label] += 1
        rows[label].append((idx, node.get("title")))
    return {label: items for label, items in rows.items() if counts[label] > 1}


def main():
    node_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_NODE_PATH
    output_dir = node_path.parent
    node_dict = _load_node_dict(node_path)
    ctx = SimpleNamespace(output_dir=str(output_dir))

    state = extract_stage.run(ctx, {"node_dict": node_dict})
    pre_nodes = copy.deepcopy(state["node_list"])
    pre_relations, _ = extract_explicit_relations(_attach_global_ids(pre_nodes), text_mode="natural")

    state = repair_stage.run(ctx, state)
    post_nodes = state["node_list"]
    post_relations, _ = extract_explicit_relations(_attach_global_ids(post_nodes), text_mode="natural")

    forward_hits = []
    for idx, node in enumerate(post_nodes):
        for hit in node.get("reference_signals", {}).get("explicit_targets", []):
            if str(hit.get("match_mode", "")).startswith("forward_"):
                forward_hits.append((idx, hit.get("surface"), hit.get("resolved_index"), hit.get("match_mode")))

    print(f"node_path={node_path}")
    print(f"pre_unresolved_explicit={_count_explicit_unresolved(pre_nodes)}")
    print(f"post_unresolved_explicit={_count_explicit_unresolved(post_nodes)}")
    print(f"pre_trigger_without_target={_count_flag(pre_nodes, 'trigger_without_target')}")
    print(f"post_trigger_without_target={_count_flag(post_nodes, 'trigger_without_target')}")
    print(f"pre_explicit_relations={len(pre_relations)}")
    print(f"post_explicit_relations={len(post_relations)}")
    print(f"added_explicit_relations={len(post_relations) - len(pre_relations)}")
    print("duplicate_labels=")
    for label, items in sorted(_duplicate_labels(post_nodes).items()):
        print(label, items)
    print("forward_repairs=")
    for item in forward_hits:
        print(item)
    print("duplicate_resolution_examples=")
    for idx, node in enumerate(post_nodes):
        for hit in node.get("reference_signals", {}).get("explicit_targets", []):
            if hit.get("candidate_indices"):
                print(idx, hit.get("surface"), hit.get("resolved_index"), hit.get("candidate_indices"))


if __name__ == "__main__":
    main()
