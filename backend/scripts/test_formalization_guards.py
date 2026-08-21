import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.common.formalization_guards import (
    attach_formalization_guidance,
    build_formalizer_context_payload,
    check_formalization_risks,
    extract_role_bindings,
    infer_statement_skeleton,
)


def test_equivalence_skeleton():
    node = {"content": "向量组线性无关的充分必要条件是它的秩等于它所含向量的个数。"}
    skeleton = infer_statement_skeleton(node)
    assert skeleton["kind"] == "equivalence"
    assert "↔" in skeleton["expected_connectives"]


def test_example_skeleton_flags_implication_output():
    node = {"content": "例如, α1=(1,0,0), α2=(0,1,0), 于是 rank{α1,α2}=2。"}
    skeleton = infer_statement_skeleton(node)
    assert skeleton["kind"] == "example_conjunction"
    risks = check_formalization_risks(node, "theorem t : A -> B := by sorry")
    assert any(risk["code"] == "wrong_connector" for risk in risks)


def test_divisibility_role_binding():
    node = {"content": "设 a, b ∈ R，如果存在 c ∈ R 使得 a = bc，则称 b 是 a 的因子。"}
    bindings = extract_role_bindings(node)
    assert any(
        item["type"] == "divisibility"
        and item["roles"].get("divisor") == "b"
        and item["roles"].get("dividend") == "a"
        for item in bindings
    )


def test_concept_downgrade_associated_vs_isunit():
    node = {"node_type": "定义", "content": "若 a = bc 蕴含 b ∼ a 或 b ∼ 1，则 a 为不可约元。"}
    risks = check_formalization_risks(node, "def irreducibleLike := a = b * c -> IsUnit b")
    assert any(risk["code"] == "concept_downgrade" for risk in risks)


def test_vector_rank_api_context_risk():
    node = {"content": "向量组线性无关的充分必要条件是它的秩等于它所含向量的个数。"}
    risks = check_formalization_risks(node, "theorem t : LinearIndependent K v ↔ Matrix.rank M = s := by sorry")
    assert any(risk["code"] == "api_context_mismatch" for risk in risks)


def test_layered_formalizer_payload():
    node = {
        "node_type": "定义",
        "title": {"chinese": "最大公因子的定义"},
        "content": "d 为 a, b 的最大公因子，当且仅当 d|a 且 d|b，且任意公因子 c 都整除 d。",
        "label": "def-gcd",
    }
    payload = build_formalizer_context_payload(node)
    assert payload["primary_statement"].startswith("d 为 a")
    assert payload["auxiliary_context"]["title"] == "最大公因子的定义"
    assert payload["statement_skeleton"]["kind"] == "definition"
    assert any(concept["id"] == "gcd_definition" for concept in payload["concept_hints"])


def test_attach_guidance_keeps_node_fields():
    node = {"node_type": "命题", "content": "获相等的两个向量组不一定等价。"}
    enriched = attach_formalization_guidance(node)
    assert enriched["content"] == node["content"]
    assert "formalization_guidance" in enriched
    assert any(
        risk["code"] == "ocr_suspicious_text"
        for risk in enriched["formalization_guidance"]["semantic_risks"]
    )


def main():
    test_equivalence_skeleton()
    test_example_skeleton_flags_implication_output()
    test_divisibility_role_binding()
    test_concept_downgrade_associated_vs_isunit()
    test_vector_rank_api_context_risk()
    test_layered_formalizer_payload()
    test_attach_guidance_keeps_node_fields()
    print("formalization guard tests passed")


if __name__ == "__main__":
    main()
