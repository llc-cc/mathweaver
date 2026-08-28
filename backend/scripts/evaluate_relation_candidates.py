"""Evaluate build_relations candidate recall against human-reviewed gold edges.

Gold JSON/JSONL rows use:
{
  "dependent_global_id": "...",
  "support_global_id": "...",
  "relation_kind": "logic|definition",
  "explicit": false,
  "dependent_index": 42,
  "support_index": 3
}

Only non-explicit rows participate in candidate recall because resolved explicit
references bypass the candidate LLM by design.
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def _read_records(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    payload = json.loads(text)
    if isinstance(payload, dict):
        payload = payload.get("edges") or payload.get("candidates") or []
    if not isinstance(payload, list):
        raise ValueError(f"Expected a JSON list in {path}")
    return payload


def _edge_key(row):
    return (
        str(row.get("dependent_global_id") or row.get("出发节点") or ""),
        str(row.get("support_global_id") or row.get("到达节点") or ""),
        str(row.get("relation_kind") or row.get("关系") or "")
        .replace("逻辑依赖", "logic")
        .replace("定义依赖", "definition"),
    )


def _distance(row):
    if row.get("distance") is not None:
        return int(row["distance"])
    dependent = row.get("dependent_index")
    support = row.get("support_index")
    if dependent is None or support is None:
        return None
    return int(dependent) - int(support)


def _recall(gold_keys, candidate_keys):
    if not gold_keys:
        return None
    return len(gold_keys & candidate_keys) / len(gold_keys)


def evaluate_candidates(candidates, gold):
    implicit_gold = [row for row in gold if not row.get("explicit", False)]
    selected = [row for row in candidates if row.get("selected")]
    selected_keys = {_edge_key(row) for row in selected}
    gold_keys = {_edge_key(row) for row in implicit_gold}

    by_kind = {}
    for kind in ("logic", "definition"):
        kind_gold = {_edge_key(row) for row in implicit_gold if _edge_key(row)[2] == kind}
        by_kind[kind] = {
            "gold": len(kind_gold),
            "recalled": len(kind_gold & selected_keys),
            "recall": _recall(kind_gold, selected_keys),
        }

    long_distance_gold = {
        _edge_key(row)
        for row in implicit_gold
        if _edge_key(row)[2] == "logic" and _distance(row) is not None and _distance(row) > 30
    }
    selected_by_dependent = Counter(str(row.get("dependent_global_id") or "") for row in selected)
    selected_definitions_by_dependent = Counter(
        str(row.get("dependent_global_id") or "")
        for row in selected
        if _edge_key(row)[2] == "definition"
    )

    exclusive_channel_hits = Counter()
    for row in selected:
        if _edge_key(row) not in gold_keys:
            continue
        channels = set(row.get("retrieval_channels") or [])
        if len(channels) == 1:
            exclusive_channel_hits.update(channels)

    channel_candidate_counts = Counter()
    for row in candidates:
        channel_candidate_counts.update(set(row.get("retrieval_channels") or []))

    dependent_count = len(selected_by_dependent)
    return {
        "gold_implicit_edges": len(gold_keys),
        "selected_candidates": len(selected),
        "recalled_edges": len(gold_keys & selected_keys),
        "candidate_recall_at_30": _recall(gold_keys, selected_keys),
        "by_kind": by_kind,
        "long_distance_logic": {
            "gold": len(long_distance_gold),
            "recalled": len(long_distance_gold & selected_keys),
            "recall": _recall(long_distance_gold, selected_keys),
        },
        "candidate_budget": {
            "max_per_dependent": max(selected_by_dependent.values(), default=0),
            "average_per_dependent": (
                sum(selected_by_dependent.values()) / dependent_count if dependent_count else 0.0
            ),
            "max_definitions_per_dependent": max(selected_definitions_by_dependent.values(), default=0),
        },
        "channel_candidate_counts": dict(sorted(channel_candidate_counts.items())),
        "exclusive_gold_hits_by_channel": dict(sorted(exclusive_channel_hits.items())),
    }


def enforce_acceptance(report):
    failures = []
    recall = report.get("candidate_recall_at_30")
    long_recall = (report.get("long_distance_logic") or {}).get("recall")
    budget = report.get("candidate_budget") or {}
    if recall is None or recall < 0.95:
        failures.append(f"candidate_recall_at_30={recall!r} < 0.95")
    if long_recall is not None and long_recall < 0.90:
        failures.append(f"long_distance_logic.recall={long_recall!r} < 0.90")
    if budget.get("max_per_dependent", 0) > 30:
        failures.append("max_per_dependent > 30")
    if budget.get("max_definitions_per_dependent", 0) > 10:
        failures.append("max_definitions_per_dependent > 10")
    return failures


def main():
    parser = argparse.ArgumentParser(description="Evaluate relation candidate recall")
    parser.add_argument("--candidates", required=True, help="relation_candidates.json")
    parser.add_argument("--gold", required=True, help="human-reviewed gold JSON or JSONL")
    parser.add_argument("--output", help="optional output report JSON")
    parser.add_argument("--enforce", action="store_true", help="exit non-zero when acceptance thresholds fail")
    args = parser.parse_args()

    report = evaluate_candidates(_read_records(args.candidates), _read_records(args.gold))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    if args.enforce:
        failures = enforce_acceptance(report)
        if failures:
            raise SystemExit("Acceptance failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()

