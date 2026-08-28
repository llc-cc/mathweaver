import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.stages.extract_references import stage


DEFAULT_BOOK_DIR = ROOT / "test_output" / "高等代数-丘维声-上册-第三章节选"
DEFAULT_NODE_CANDIDATES = [
    DEFAULT_BOOK_DIR / "_stage_cache" / "node_dict.json",
    DEFAULT_BOOK_DIR / "TEST_NODE_OUT.json",
]


def _load_node_dict(path: Path):
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    if isinstance(payload, dict):
        return {int(k): v for k, v in payload.items()}
    if isinstance(payload, list):
        return {idx: node for idx, node in enumerate(payload)}
    raise TypeError(f"Unsupported node payload type: {type(payload).__name__}")


def _pick_default_node_path():
    for candidate in DEFAULT_NODE_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Could not find node input. Checked:\n"
        + "\n".join(str(path) for path in DEFAULT_NODE_CANDIDATES)
    )


def _summarize(node_list):
    explicit_resolved = 0
    explicit_unresolved = 0
    relative_resolved = 0
    relative_unresolved = 0
    formula_count = 0
    named_count = 0
    trigger_without_target = 0

    for node in node_list:
        signals = node.get("reference_signals", {})
        explicit_resolved += sum(
            1 for hit in signals.get("explicit_targets", []) if hit.get("match_mode") != "unresolved"
        )
        explicit_unresolved += sum(
            1 for hit in signals.get("explicit_targets", []) if hit.get("match_mode") == "unresolved"
        )
        relative_resolved += sum(
            1 for hit in signals.get("relative_references", []) if hit.get("match_mode") != "unresolved"
        )
        relative_unresolved += sum(
            1 for hit in signals.get("relative_references", []) if hit.get("match_mode") == "unresolved"
        )
        formula_count += len(signals.get("formula_references", []))
        named_count += len(signals.get("named_references", []))
        if "trigger_without_target" in signals.get("repair_flags", []):
            trigger_without_target += 1

    explicit_total = explicit_resolved + explicit_unresolved
    relative_total = relative_resolved + relative_unresolved
    explicit_ratio = explicit_resolved / explicit_total if explicit_total else 0.0
    relative_ratio = relative_resolved / relative_total if relative_total else 0.0

    return {
        "nodes": len(node_list),
        "explicit_resolved": explicit_resolved,
        "explicit_unresolved": explicit_unresolved,
        "explicit_ratio": explicit_ratio,
        "relative_resolved": relative_resolved,
        "relative_unresolved": relative_unresolved,
        "relative_ratio": relative_ratio,
        "formula": formula_count,
        "named": named_count,
        "trigger_without_target": trigger_without_target,
    }


def _print_unresolved_samples(output_dir: Path, limit=10):
    unresolved_path = output_dir / "references_unresolved.json"
    if not unresolved_path.exists():
        print(f"references_unresolved.json not found: {unresolved_path}")
        return

    with unresolved_path.open("r", encoding="utf-8") as f:
        unresolved = json.load(f)

    print(f"unresolved_nodes={len(unresolved)}")
    for item in unresolved[:limit]:
        label = item.get("label") or "<no label>"
        flags = ",".join(item.get("repair_flags", []))
        surfaces = [hit.get("surface") for hit in item.get("explicit_targets", []) if hit.get("match_mode") == "unresolved"]
        rel_surfaces = [
            hit.get("surface") for hit in item.get("relative_references", []) if hit.get("match_mode") == "unresolved"
        ]
        print(
            f"- node_index={item.get('node_index')} label={label} flags={flags} "
            f"explicit_unresolved={surfaces[:3]} relative_unresolved={rel_surfaces[:3]}"
        )


def main():
    node_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else _pick_default_node_path()
    output_dir = node_path.parent if node_path.name == "node_dict.json" else node_path.parent / "_stage_cache"
    output_dir.mkdir(parents=True, exist_ok=True)

    node_dict = _load_node_dict(node_path)
    ctx = SimpleNamespace(output_dir=str(output_dir))
    state = stage.run(ctx, {"node_dict": node_dict})
    summary = _summarize(state["node_list"])

    print(f"node_path={node_path}")
    print(f"output_dir={output_dir}")
    print(f"nodes={summary['nodes']}")
    print(
        "explicit: "
        f"resolved={summary['explicit_resolved']} "
        f"unresolved={summary['explicit_unresolved']} "
        f"ratio={summary['explicit_ratio']:.3f}"
    )
    print(
        "relative: "
        f"resolved={summary['relative_resolved']} "
        f"unresolved={summary['relative_unresolved']} "
        f"ratio={summary['relative_ratio']:.3f}"
    )
    print(
        f"formula={summary['formula']} "
        f"named={summary['named']} "
        f"trigger_without_target={summary['trigger_without_target']}"
    )
    _print_unresolved_samples(output_dir)


if __name__ == "__main__":
    main()
