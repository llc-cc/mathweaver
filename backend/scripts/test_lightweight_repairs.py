import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.common.node import normalize_text_with_variables
from pipeline.stages.extract_logic_tuples.stage import repair_statement_forms
from pipeline.stages.extract_statements.stage import repair_missing_labels_from_problem_dict
from pipeline.stages.split_nodes.stage import enrich_split_blocks


def test_label_repair_real(base_dir: Path):
    unsplit = json.loads((base_dir / "unsplit_statement_dict.json").read_text())
    problem = json.loads((base_dir / "problem_dict.json").read_text())
    repaired, count = repair_missing_labels_from_problem_dict(unsplit, problem)
    print(f"label_repair_count={count}")
    for key in ["7", "8"]:
        block = repaired[key]["pos1"]
        print(f"label_repair key={key} label={block.get('label')}")


def test_split_enrichment_synthetic():
    sample = {
        "0": {
            "pos1": {
                "node_type": "命题",
                "content": "设 G 是可解群，π 是素数集合。(a) H 是 Hall π-子群。",
                "proof": "",
                "label": "Proposition 1.5",
            },
            "_orig_key": 100,
        },
        "1": {
            "pos1": {
                "node_type": "命题",
                "content": "设 G 是可解群，π 是素数集合。(b) K 是 Hall π-子群。",
                "proof": "",
                "label": "Proposition 1.5",
            },
            "_orig_key": 100,
        },
    }
    enriched = enrich_split_blocks(sample)
    for key in ["0", "1"]:
        block = enriched[key]["pos1"]
        print(
            "split_enrich",
            key,
            f"label={block.get('label')}",
            f"label_suffix={block.get('label_suffix')}",
            f"shared_context={block.get('shared_context')}",
            f"local_content={block.get('local_content')}",
        )


def test_statement_form_repair_synthetic():
    sample = {
        0: {
            "node_type": "性质",
            "statement_form": "implication",
            "remark": {"original_form": "A 当且仅当 B"},
            "conditions": [],
            "conclusions": [],
        },
        1: {
            "node_type": "性质",
            "statement_form": "implication",
            "remark": {"original_form": "f(x) = g(x)"},
            "conditions": [],
            "conclusions": [],
        },
    }
    repaired = repair_statement_forms(sample)
    for key in sorted(repaired):
        print(
            "statement_form_repair",
            key,
            f"before={sample[key]['statement_form']}",
            f"after={repaired[key].get('statement_form')}",
        )


def test_normalize_whitelist_synthetic():
    text = "Ind_H^G \\chi = Hom(V,W)"
    normalized = normalize_text_with_variables(
        [{"name": "H", "normalize_type": "GROUP_1"}, {"name": "G", "normalize_type": "GROUP_2"}],
        text,
    )
    print(f"normalize_whitelist={normalized}")


def main():
    base_dir = ROOT / "test_output" / "高等代数-丘维声-上册-第三章节选" / "_stage_cache"
    test_label_repair_real(base_dir)
    test_split_enrichment_synthetic()
    test_statement_form_repair_synthetic()
    test_normalize_whitelist_synthetic()


if __name__ == "__main__":
    main()
