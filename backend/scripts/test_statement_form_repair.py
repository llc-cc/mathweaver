import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.stages.compile_logic_form.stage import (
    _is_top_level_equality,
    merge_logic_ast_local,
)
from pipeline.stages.extract_logic_tuples.stage import _repair_statement_form_for_block


def _statement(content, statement_form="other"):
    return {
        "node_type": "theorem",
        "statement_form": statement_form,
        "remark": {"original_form": content},
        "conditions": [],
    }


def _sym(name):
    return {"kind": "sym_ref", "sym_id": name}


def _eq_ast():
    return {"kind": "eq", "left": _sym("X"), "right": _sym("Y")}


def test_explicit_equivalence_markers_use_word_boundaries():
    repaired = _repair_statement_form_for_block(_statement("P iff Q"))
    assert repaired["statement_form"] == "equivalence"
    assert repaired["statement_form_repair"] == "explicit_marker_correction"
    assert repaired["statement_form_repair_evidence"] == "iff"

    for content in ("f is differentiable", "Clifford algebra"):
        original = _statement(content)
        repaired = _repair_statement_form_for_block(original)
        assert repaired["statement_form"] == "other"
        assert "statement_form_repair" not in repaired
        assert original == _statement(content)


def test_only_strong_implication_markers_trigger_repair():
    repaired = _repair_statement_form_for_block(_statement("若 x=0，则 y=1"))
    assert repaired["statement_form"] == "implication"
    assert repaired["statement_form_repair_evidence"] == "若 x=0，则"

    latex = _repair_statement_form_for_block(_statement(r"P \Rightarrow Q", "unknown"))
    assert latex["statement_form"] == "implication"

    for content in ("f: X -> Y", "x_n → x"):
        repaired = _repair_statement_form_for_block(_statement(content, "unknown"))
        assert repaired["statement_form"] == "unknown"
        assert "statement_form_repair" not in repaired


def test_explicit_classifications_are_never_overwritten():
    cases = {
        "implication": "P iff Q",
        "equality": "若 x=0，则 y=1",
        "equivalence": r"P \Rightarrow Q",
        "existence": "P iff Q",
        "characterization": "若 P，则 Q",
    }
    for statement_form, content in cases.items():
        repaired = _repair_statement_form_for_block(_statement(content, statement_form))
        assert repaired["statement_form"] == statement_form
        assert "statement_form_repair" not in repaired


def test_uncertain_forms_remain_uncertain_without_explicit_markers():
    cases = (
        ("other", "存在 x，使得 f(x)=0"),
        ("unknown", "设 x=0，证明 P(x)"),
        ("", "f(x)=0"),
        ("other", "A is compact"),
    )
    for statement_form, content in cases:
        repaired = _repair_statement_form_for_block(_statement(content, statement_form))
        assert repaired["statement_form"] == statement_form
        assert "statement_form_repair" not in repaired


def test_bare_structured_equality_is_high_confidence():
    node = _statement("f(x)=0")
    assert _is_top_level_equality(node, _eq_ast()) is True

    for statement_form in ("implication", "equality", "existence"):
        explicit = _statement("f(x)=0", statement_form)
        assert _is_top_level_equality(explicit, _eq_ast()) is False

    conditioned = _statement("f(x)=0")
    conditioned["conditions"] = [{"id": "c1", "text": "x is real"}]
    assert _is_top_level_equality(conditioned, _eq_ast()) is False

    definition = _statement("f(x)=0")
    definition["node_type"] = "definition"
    assert _is_top_level_equality(definition, _eq_ast()) is False


def test_wrapped_or_logically_nested_equality_is_not_top_level():
    eq_ast = _eq_ast()
    wrappers = (
        {"kind": "forall", "vars": [_sym("X")], "body": eq_ast},
        {"kind": "exists", "vars": [_sym("X")], "body": eq_ast},
        {"kind": "imp", "left": _sym("P"), "right": eq_ast},
        {"kind": "iff", "left": _sym("P"), "right": eq_ast},
        {"kind": "and", "left": eq_ast, "right": _sym("P")},
        {"kind": "not", "arg": eq_ast},
    )
    node = _statement("f(x)=0", "unknown")
    for ast in wrappers:
        assert _is_top_level_equality(node, ast) is False

    nested_logic = {
        "kind": "eq",
        "left": {"kind": "forall", "vars": [_sym("X")], "body": _sym("P")},
        "right": _sym("Y"),
    }
    assert _is_top_level_equality(node, nested_logic) is False


def test_merge_repairs_parent_and_subnode_from_bare_eq_ast():
    node_dict = {
        "0": {
            "global_id": "node-parent",
            "node_type": "theorem",
            "statement_form": "other",
            "conditions": [],
        },
        "1": {
            "global_id": "node-with-child",
            "node_type": "theorem",
            "statement_form": "implication",
            "sub_nodes": [
                {
                    "index": 1,
                    "statement_form": "unknown",
                    "conditions": [],
                }
            ],
        },
    }
    logic_form_local_dict = {
        "0": {"global_id": "node-parent", "logic_ast_local": _eq_ast()},
        "1__sub1": {"global_id": "node-with-child", "logic_ast_local": _eq_ast()},
    }

    merged = merge_logic_ast_local(logic_form_local_dict, node_dict)
    parent = merged["0"]
    child = merged["1"]["sub_nodes"][0]

    for repaired, before in ((parent, "other"), (child, "unknown")):
        assert repaired["statement_form"] == "equality"
        assert repaired["statement_form_before_repair"] == before
        assert repaired["statement_form_repair"] == "structured_logic_correction"
        assert repaired["statement_form_repair_evidence"] == "logic_ast_local.kind=eq"
        assert repaired["logic_ast_local"]["kind"] == "eq"

    assert merged["1"]["statement_form"] == "implication"


def main():
    test_explicit_equivalence_markers_use_word_boundaries()
    test_only_strong_implication_markers_trigger_repair()
    test_explicit_classifications_are_never_overwritten()
    test_uncertain_forms_remain_uncertain_without_explicit_markers()
    test_bare_structured_equality_is_high_confidence()
    test_wrapped_or_logically_nested_equality_is_not_top_level()
    test_merge_repairs_parent_and_subnode_from_bare_eq_ast()
    print("statement form repair tests passed")


if __name__ == "__main__":
    main()
