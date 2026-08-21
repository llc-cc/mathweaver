from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.stages.generate_titles.templates import (
    correction_prompt05,
    data_template05,
    prompt_template05,
    validation05,
)


def test_title_prompt_requests_objective_fact_titles() -> None:
    rendered = prompt_template05.format(
        pos1='{"node_type": "Proposition", "content": {"chinese": "示例"}}',
        data_template=data_template05,
    )

    assert "exact mathematical fact" in rendered
    assert "short objective" in rendered
    assert "rather than merely naming the broad topic" in rendered
    assert "bare generic concept" in rendered
    assert "关于……的讨论" in rendered
    assert "Discussion of ..." in rendered


def test_title_prompt_contains_three_node_type_examples() -> None:
    rendered = prompt_template05.format(pos1="{}", data_template=data_template05)

    assert "Example 1 — definition" in rendered
    assert "Example 2 — proposition" in rendered
    assert "Example 3 — concrete representation" in rendered
    assert "Example 4" not in rendered
    assert r"\(n\times m\) 矩阵的定义" in rendered
    assert "可逆矩阵的乘积仍可逆" in rendered
    assert "矩阵乘积可表示为行向量与列向量的标量积" in rendered


def test_title_correction_prompt_preserves_semantic_contract() -> None:
    rendered = correction_prompt05.format(
        data_template=data_template05,
        answer="not-json",
    )

    assert "objective mathematical fact" in rendered
    assert "broad topic-only phrase" in rendered


def test_title_validation_contract_is_unchanged() -> None:
    assert validation05(
        {
            "title": {
                "chinese": "矩阵乘法结合律",
                "english": "Associativity of Matrix Multiplication",
            }
        }
    )
    assert not validation05({"title": {"chinese": "", "english": ""}})


if __name__ == "__main__":
    test_title_prompt_requests_objective_fact_titles()
    test_title_prompt_contains_three_node_type_examples()
    test_title_correction_prompt_preserves_semantic_contract()
    test_title_validation_contract_is_unchanged()
    print("generate_titles prompt contract tests passed")
