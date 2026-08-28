import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_relation_candidates import enforce_acceptance, evaluate_candidates


def test_candidate_evaluation_reports_recall_distance_budget_and_channels():
    candidates = [
        {
            "dependent_global_id": "B",
            "support_global_id": "A",
            "relation_kind": "logic",
            "dependent_index": 40,
            "support_index": 0,
            "selected": True,
            "retrieval_channels": ["bm25f", "predicate_symbol"],
        },
        {
            "dependent_global_id": "B",
            "support_global_id": "D",
            "relation_kind": "definition",
            "dependent_index": 40,
            "support_index": 2,
            "selected": True,
            "retrieval_channels": ["exact_alias"],
        },
    ]
    gold = [
        {
            "dependent_global_id": "B",
            "support_global_id": "A",
            "relation_kind": "logic",
            "explicit": False,
            "dependent_index": 40,
            "support_index": 0,
        },
        {
            "dependent_global_id": "B",
            "support_global_id": "D",
            "relation_kind": "definition",
            "explicit": False,
            "dependent_index": 40,
            "support_index": 2,
        },
        {
            "dependent_global_id": "B",
            "support_global_id": "X",
            "relation_kind": "logic",
            "explicit": True,
            "dependent_index": 40,
            "support_index": 1,
        },
    ]

    report = evaluate_candidates(candidates, gold)

    assert report["candidate_recall_at_30"] == 1.0
    assert report["long_distance_logic"]["recall"] == 1.0
    assert report["candidate_budget"]["max_per_dependent"] == 2
    assert report["exclusive_gold_hits_by_channel"] == {"exact_alias": 1}
    assert enforce_acceptance(report) == []


def test_candidate_evaluation_enforcement_detects_missed_gold():
    report = evaluate_candidates(
        [],
        [
            {
                "dependent_global_id": "B",
                "support_global_id": "A",
                "relation_kind": "logic",
                "explicit": False,
                "dependent_index": 40,
                "support_index": 0,
            }
        ],
    )

    failures = enforce_acceptance(report)

    assert any("candidate_recall_at_30" in failure for failure in failures)
    assert any("long_distance_logic" in failure for failure in failures)


if __name__ == "__main__":
    test_candidate_evaluation_reports_recall_distance_budget_and_channels()
    test_candidate_evaluation_enforcement_detects_missed_gold()
    print("relation candidate evaluation tests passed")

