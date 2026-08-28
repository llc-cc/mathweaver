from __future__ import annotations

import copy
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.common.node import merge_node_with_source_envelope
from pipeline.stages.generate_titles.stage import (
    _build_title_task_input_dict,
    extract_source_title_hint,
    merge_titles_into_statement_dict,
    normalize_title_for_node,
    title_kind_requirement,
)
from pipeline.stages.generate_titles.templates import (
    correction_prompt05,
    data_template05,
    prompt_template05,
    title_text_within_limits,
    validation05,
)


def _render_prompt(node=None, hint="", requirement="No additional node-kind marker is required."):
    return prompt_template05.format(
        pos1=node or {},
        source_title_hint=hint,
        title_kind_requirement=requirement,
        data_template=data_template05,
    )


def test_title_prompt_requests_type_aware_noun_phrases() -> None:
    rendered = _render_prompt(
        {"node_type": "example", "content": "Gaussian elimination example."},
        requirement=title_kind_requirement("example"),
    )

    assert "standalone noun phrase" in rendered
    assert "Source-provided title hint" in rendered
    assert "what knowledge it exemplifies" in rendered
    assert "what knowledge or ability it practises" in rendered
    assert "must visibly" in rendered
    assert "24 visible Chinese characters" in rendered
    assert "14 English words" in rendered
    assert "关于……的讨论" in rendered


def test_title_prompt_contains_type_specific_examples() -> None:
    rendered = _render_prompt()

    assert "可逆矩阵的定义" in rendered
    assert "行列式的换行变号性质" in rendered
    assert "高斯消元法的示例" in rendered
    assert "三阶行列式的倍加法计算示例" in rendered
    assert "初等行变换保解性的证明习题" in rendered
    assert "关于可逆矩阵定义的注释" in rendered
    assert "可逆矩阵的乘积仍可逆" not in rendered


def test_title_correction_prompt_preserves_full_context() -> None:
    rendered = correction_prompt05.format(
        data_template=data_template05,
        pos1={"node_type": "remark", "content": "注：左右逆等价。"},
        source_title_hint="关于左右逆等价",
        title_kind_requirement=title_kind_requirement("remark"),
        answer="not-json",
    )

    assert "Input node" in rendered
    assert "Source-provided title hint" in rendered
    assert "Required visible node-kind wording" in rendered
    assert "remarks/notes" in rendered
    assert "reveal an exercise answer" in rendered


def test_title_validation_accepts_compact_headings() -> None:
    accepted = (
        ("矩阵乘法的计算示例", "Example of Matrix Multiplication"),
        ("线性相关性的反例", "Counterexample to Linear Dependence"),
        ("行列式展开的计算习题", "Exercise on Determinant Expansion"),
        ("可逆矩阵判定的证明习题", "Proof Exercise on Invertibility"),
        ("关于逆矩阵唯一性的注释", "Remark on Uniqueness of the Inverse"),
        ("矩阵乘法结合律", "Associativity of Matrix Multiplication"),
    )
    for chinese, english in accepted:
        assert validation05({"title": {"chinese": chinese, "english": english}})


def test_title_validation_rejects_long_or_declarative_titles() -> None:
    assert not validation05(
        {
            "title": {
                "chinese": "交换行列式的两行相当于乘以-1",
                "english": "Swapping Two Rows Is Equivalent to Multiplying the Determinant by Minus One",
            }
        }
    )
    assert not validation05(
        {
            "title": {
                "chinese": "若矩阵可逆则它的行列式不等于零",
                "english": "If a Matrix Is Invertible Then Its Determinant Is Nonzero",
            }
        }
    )
    assert not validation05(
        {
            "title": {
                "chinese": "这是一个超过二十四个可见字符并且继续复述全部数学条件与结论的标题",
                "english": "A Deliberately Overlong Mathematical Heading Containing Far More Than Fourteen Separate English Words for Validation",
            }
        }
    )
    assert not validation05({"title": {"chinese": "", "english": ""}})


def test_latex_counts_as_one_title_unit() -> None:
    assert title_text_within_limits(
        r"矩阵恒等式 $A^{-1}(AB)B^{-1}=I$",
        "chinese",
    )
    assert title_text_within_limits(
        r"Identity for $A^{-1}(AB)B^{-1}=I$",
        "english",
    )


def test_source_title_hint_extraction() -> None:
    assert extract_source_title_hint(
        {"label": "", "content": "（秩定理）设矩阵 $A$ 满足……"}
    ) == "秩定理"
    assert extract_source_title_hint(
        {"label": "", "content": "(Urysohn's lemma) Let $A$ and $B$ be closed."}
    ) == "Urysohn's lemma"
    assert extract_source_title_hint(
        {"label": "", "content": "例 2.3（高斯消元法的应用）求解方程组。"}
    ) == "高斯消元法的应用"
    assert extract_source_title_hint(
        {"label": "", "content": "注：关于行列式符号的约定，以下均采用……"}
    ) == "关于行列式符号的约定"


def test_source_title_hint_rejects_numbering_and_formulas() -> None:
    assert extract_source_title_hint(
        {"label": "(1.3)", "content": "(1.3) Let $A$ be invertible."}
    ) == ""
    assert extract_source_title_hint(
        {"label": "", "content": "(a) Prove the first assertion."}
    ) == ""
    assert extract_source_title_hint(
        {"label": "Theorem 3.2", "content": "Let $A$ be invertible."}
    ) == ""
    assert extract_source_title_hint(
        {"label": "", "content": "($A=B$) is used below."}
    ) == ""


def test_non_theorem_titles_receive_visible_kind_markers() -> None:
    example = normalize_title_for_node(
        {"node_type": "example", "content": "An example of matrix multiplication."},
        {"chinese": "矩阵乘法", "english": "Matrix Multiplication"},
    )
    assert example == {
        "chinese": "矩阵乘法的示例",
        "english": "Example of Matrix Multiplication",
    }

    exercise = normalize_title_for_node(
        {"node_type": "exercise", "content": "Compute a determinant."},
        {"chinese": "行列式展开的计算", "english": "Determinant Expansion"},
    )
    assert exercise == {
        "chinese": "行列式展开的计算习题",
        "english": "Exercise on Determinant Expansion",
    }

    remark = normalize_title_for_node(
        {"node_type": "remark", "content": "A remark about inverse uniqueness."},
        {"chinese": "逆矩阵唯一性", "english": "Uniqueness of the Inverse"},
    )
    assert remark == {
        "chinese": "关于逆矩阵唯一性的注释",
        "english": "Remark on Uniqueness of the Inverse",
    }

    problem = normalize_title_for_node(
        {"node_type": "problem", "content": "Study solvability."},
        {"chinese": "线性方程组可解性", "english": "Solvability of Linear Systems"},
    )
    assert problem == {
        "chinese": "线性方程组可解性问题",
        "english": "Problem on Solvability of Linear Systems",
    }


def test_specific_kind_markers_are_not_duplicated() -> None:
    counterexample = normalize_title_for_node(
        {"node_type": "example", "content": "A counterexample."},
        {"chinese": "线性相关性的反例", "english": "Counterexample to Linear Dependence"},
    )
    assert counterexample["chinese"] == "线性相关性的反例"
    assert counterexample["english"] == "Counterexample to Linear Dependence"

    proof_exercise = normalize_title_for_node(
        {"node_type": "exercise", "content": "Prove the claim."},
        {"chinese": "可逆矩阵判定的证明习题", "english": "Proof Exercise on Invertibility"},
    )
    assert proof_exercise["chinese"] == "可逆矩阵判定的证明习题"
    assert proof_exercise["english"] == "Proof Exercise on Invertibility"


def test_source_hint_core_is_preserved_with_fallback() -> None:
    node = {"node_type": "example", "content": "（矩阵乘法）下面计算 $AB$。"}
    normalized = normalize_title_for_node(
        node,
        {"chinese": "两个矩阵相乘", "english": "Example of Multiplying Matrices"},
    )
    assert normalized["chinese"] == "矩阵乘法的示例"
    assert normalized["english"] == "Example of Multiplying Matrices"

    preserved = normalize_title_for_node(
        node,
        {"chinese": "矩阵乘法的计算示例", "english": "Example of Matrix Multiplication"},
    )
    assert preserved["chinese"] == "矩阵乘法的计算示例"


def test_title_task_hints_are_temporary() -> None:
    source = {
        3: {
            "pos1": {
                "node_type": "example",
                "content": "（矩阵乘法）下面计算 $AB$。",
                "proof": "",
                "label": "Example 2.3",
            },
            "_orig_key": 3,
        }
    }
    original = copy.deepcopy(source)
    enriched = _build_title_task_input_dict(source)

    assert source == original
    assert enriched[3]["source_title_hint"] == "矩阵乘法"
    assert "示例" in enriched[3]["title_kind_requirement"]
    assert "source_title_hint" not in enriched[3]["pos1"]


def test_title_merge_preserves_source_envelope_and_global_id() -> None:
    source_node = {
        "node_type": "example",
        "content": "（矩阵乘法）下面计算 $AB$。",
        "proof": "",
        "label": "Example 2.3",
    }
    sealed_node, _ = merge_node_with_source_envelope(
        source_node,
        {},
        stage_name="extract_statements",
        allowed_fields=(),
        seal=True,
        source_metadata={"source_text": source_node["content"], "source_block_key": 3},
    )
    source_envelope = copy.deepcopy(sealed_node["_source_envelope"])
    global_id = sealed_node["global_id"]

    merged = merge_titles_into_statement_dict(
        {3: {"pos1": sealed_node, "_orig_key": 3}},
        {"3": {"title": {"chinese": "矩阵乘法", "english": "Matrix Multiplication"}}},
    )
    result = merged[3]["pos1"]

    assert result["title"] == {
        "chinese": "矩阵乘法的示例",
        "english": "Example of Matrix Multiplication",
    }
    assert result["global_id"] == global_id
    assert result["_source_envelope"] == source_envelope
    assert result["content"] == source_node["content"]
    assert "source_title_hint" not in result


if __name__ == "__main__":
    test_title_prompt_requests_type_aware_noun_phrases()
    test_title_prompt_contains_type_specific_examples()
    test_title_correction_prompt_preserves_full_context()
    test_title_validation_accepts_compact_headings()
    test_title_validation_rejects_long_or_declarative_titles()
    test_latex_counts_as_one_title_unit()
    test_source_title_hint_extraction()
    test_source_title_hint_rejects_numbering_and_formulas()
    test_non_theorem_titles_receive_visible_kind_markers()
    test_specific_kind_markers_are_not_duplicated()
    test_source_hint_core_is_preserved_with_fallback()
    test_title_task_hints_are_temporary()
    test_title_merge_preserves_source_envelope_and_global_id()
    print("generate_titles prompt and strategy tests passed")
