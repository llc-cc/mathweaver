import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.common.node import (
    adjust,
    compute_global_id_from_source,
    get_node_content,
    get_node_source_original_text,
    merge_node_with_source_envelope,
    normalize_node_fields,
    normalize_source_text_for_id,
)
from pipeline.stages.extract_logic_tuples.stage import (
    _merge_logic_tuple_results,
)
from pipeline.stages.compile_logic_form.stage import normalize_node_dict
from pipeline.stages.finalize_output.stage import run as run_finalize_output
from pipeline.stages.math_disambiguation.stage import _merge_disambiguated_content
from pipeline.stages.split_nodes.stage import _apply_split_to_block


FIXED_POINT_CONCLUSION = r"there exists $\bx \in {\bf D}^{n}$ such that $\bbf(\bx) = \bx$"


def _theorem(source, **overrides):
    node = {
        "node_type": "theorem",
        "source_original_form": source,
        "remark": {"original_form": source},
        "conditions": [{"id": "c1", "text": "a condition"}],
        "conclusions": [{"id": "q1", "text": FIXED_POINT_CONCLUSION}],
        "title": {"english": "A theorem"},
        "label": "thm:1",
        "proof": "A proof.",
    }
    node.update(overrides)
    return node


def _seal(node):
    sealed, _ = merge_node_with_source_envelope(
        node,
        {},
        stage_name="extract_statements",
        allowed_fields=(),
        seal=True,
        source_metadata={"source_text": get_node_source_original_text(node)},
    )
    return sealed


def test_theorems_hash_complete_source_instead_of_shared_conclusion():
    c1_source = r"Every $C^{1}$-map $\bbf: {\bf D}^{n}\to{\bf D}^{n}$ has at least one fixed point."
    continuous_source = r"Every continuous map $\bbf: {\bf D}^{n}\to{\bf D}^{n}$ has at least one fixed point."

    c1_node = normalize_node_fields(_theorem(c1_source, label="t1.1.12"))
    continuous_node = normalize_node_fields(_theorem(continuous_source, label="t1.1.13"))

    assert c1_node["global_id"] != continuous_node["global_id"]
    assert c1_node["conclusions"][0]["text"] == continuous_node["conclusions"][0]["text"]


def test_hash_ignores_derived_fields_and_node_type_within_theorem_family():
    source = "If A, then B."
    first = normalize_node_fields(_theorem(source))
    second = normalize_node_fields(
        _theorem(
            source,
            node_type="lemma",
            title={"english": "Changed"},
            label="lem:99",
            proof="Different proof.",
            conditions=[{"id": "c9", "text": "different extraction"}],
            conclusions=[{"id": "q9", "text": "different extraction"}],
        )
    )
    assert first["global_id"] == second["global_id"]


def test_definition_hash_uses_content_and_supports_content_object():
    text = "A group is a set equipped with an associative binary operation."
    expected = hashlib.md5(normalize_source_text_for_id(text).encode("utf-8")).hexdigest()

    plain = normalize_node_fields({"node_type": "definition", "content": text})
    wrapped = normalize_node_fields(
        {
            "node_type": "axiom",
            "content": {"original_form": text, "formal_statement_core": "derived"},
        }
    )

    assert plain["global_id"] == expected
    assert wrapped["global_id"] == expected
    assert get_node_source_original_text(plain) == text


def test_adjust_does_not_rewrite_definition_source_content():
    source = "  A \\\\ B.  "
    adjusted_plain = adjust({"node_type": "definition", "content": source})
    adjusted_wrapped = adjust(
        {"node_type": "axiom", "content": {"original_form": source, "text_normalized": "  derived  "}}
    )
    assert adjusted_plain["content"] == source
    assert adjusted_wrapped["content"]["original_form"] == source
    assert adjusted_wrapped["content"]["text_normalized"] == "derived"

    example = adjust({"node_type": "example", "content": source})
    assert example["content"] == source


def test_source_hash_only_normalizes_unicode_newlines_and_whitespace():
    first = _theorem("  Caf\u00e9\r\n  implies   B.  ")
    second = _theorem("Cafe\u0301 implies B.")
    assert compute_global_id_from_source(first) == compute_global_id_from_source(second)


def test_missing_authoritative_source_is_rejected():
    try:
        normalize_node_fields({"node_type": "theorem", "content": "Not an allowed fallback"})
    except ValueError as exc:
        assert "authoritative source text is missing" in str(exc)
    else:
        raise AssertionError("Expected missing source_original_form to be rejected")


def test_split_and_logic_tuple_merge_preserve_theorem_source():
    source = "If A, then B."
    split_node = _apply_split_to_block(
        _seal({"node_type": "theorem", "content": source, "proof": "", "label": "thm:source"}),
        {},
    )
    assert split_node["source_original_form"] == source

    merged = _merge_logic_tuple_results(
        {"0": {"pos1": split_node, "_orig_key": 0}},
        {
            "0": {
                "statement_form": "implication",
                "subject": [],
                "context": [],
                "variables": [],
                "conditions": [{"id": "c1", "text": "A"}],
                "conclusions": [{"id": "q1", "text": "B"}],
            }
        },
    )["0"]
    assert merged["source_original_form"] == source


def test_logic_tuple_merge_restores_non_relation_source_content():
    for node_type in ("exercise", "example", "remark"):
        source = f"Source text for {node_type}."
        split_node = _apply_split_to_block(
            _seal({"node_type": node_type, "content": source, "proof": "", "label": node_type}),
            {},
        )
        merged = _merge_logic_tuple_results(
            {"0": {"pos1": split_node, "_orig_key": 0}},
            {
                "0": {
                    "statement_form": "other",
                    "subject": [],
                    "context": [],
                    "variables": [],
                    "conditions": [],
                    "conclusions": [{"id": "q1", "text": "Derived"}],
                }
            },
        )["0"]

        assert merged["content"] == source
        assert normalize_node_fields(merged)["global_id"] == hashlib.md5(
            normalize_source_text_for_id(source).encode("utf-8")
        ).hexdigest()


def test_compile_normalization_rejects_legacy_non_relation_cache():
    source = "Show that every contraction has a unique fixed point."
    try:
        normalize_node_dict(
            {
                "0": {
                    "node_type": "exercise",
                    "label": "Exercise 1",
                    "remark": {"original_form": source},
                    "conclusions": [{"id": "q1", "text": "Derived conclusion"}],
                }
            }
        )
    except ValueError as exc:
        assert "Rerun from extract_statements" in str(exc)
    else:
        raise AssertionError("Expected a legacy node without source envelope to be rejected")


def test_compile_normalization_rejects_legacy_relation_cache():
    try:
        normalize_node_dict(
            {
                "0": {
                    "node_type": "theorem",
                    "label": "Theorem 1",
                    "remark": {"original_form": "If A, then B."},
                }
            }
        )
    except ValueError as exc:
        assert "Rerun from extract_statements" in str(exc)
    else:
        raise AssertionError("Expected a theorem without source envelope to be rejected")


def test_disambiguation_preserves_authoritative_source_fields():
    definition = {"node_type": "definition", "content": r"A \subset B"}
    restored_definition = _merge_disambiguated_content(definition, {"content": "Subset(A,B)"})
    assert restored_definition["content"] == definition["content"]
    assert restored_definition["disambiguated_content"] == "Subset(A,B)"
    assert get_node_content(restored_definition) == "Subset(A,B)"
    assert get_node_content(restored_definition, prefer_disambiguated=False) == definition["content"]

    theorem = _theorem(r"x \subset H", content=r"x \subset H")
    restored_theorem = _merge_disambiguated_content(theorem, {"content": "Subset(x,H)"})
    assert restored_theorem["source_original_form"] == theorem["source_original_form"]
    assert compute_global_id_from_source(restored_theorem) == compute_global_id_from_source(theorem)


def test_finalize_deduplicates_identical_sources_and_validates_edges():
    theorem_a = normalize_node_fields(_seal(_theorem("If A, then B.", label="thm:first")))
    theorem_b = normalize_node_fields(_seal(_theorem("If A,\nthen   B.", label="thm:duplicate")))
    definition = normalize_node_fields(
        _seal({"node_type": "definition", "content": "A is a premise.", "label": "def:a"})
    )
    edge = {
        "出发节点": theorem_a["global_id"],
        "到达节点": definition["global_id"],
        "关系": "逻辑依赖",
        "理由": "test",
    }

    with tempfile.TemporaryDirectory() as temp_dir:
        context = SimpleNamespace(
            output_dir=temp_dir,
            output_node_path=None,
            output_edge_path=None,
        )
        state = run_finalize_output(
            context,
            {"node_list": [theorem_a, theorem_b, definition], "edge_list": [edge]},
        )
        report = json.loads(
            (Path(temp_dir) / "global_id_dedup_report.json").read_text(encoding="utf-8")
        )

    assert len(state["node_list"]) == 2
    assert state["node_list"][0]["label"] == "thm:first"
    assert report["duplicate_node_count"] == 1
    assert report["duplicates"][0]["dropped_label"] == "thm:duplicate"


if __name__ == "__main__":
    test_theorems_hash_complete_source_instead_of_shared_conclusion()
    test_hash_ignores_derived_fields_and_node_type_within_theorem_family()
    test_definition_hash_uses_content_and_supports_content_object()
    test_adjust_does_not_rewrite_definition_source_content()
    test_source_hash_only_normalizes_unicode_newlines_and_whitespace()
    test_missing_authoritative_source_is_rejected()
    test_split_and_logic_tuple_merge_preserve_theorem_source()
    test_logic_tuple_merge_restores_non_relation_source_content()
    test_compile_normalization_rejects_legacy_non_relation_cache()
    test_compile_normalization_rejects_legacy_relation_cache()
    test_disambiguation_preserves_authoritative_source_fields()
    test_finalize_deduplicates_identical_sources_and_validates_edges()
    print("global_id source-text tests passed")
