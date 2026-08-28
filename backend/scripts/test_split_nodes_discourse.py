import copy
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.common.node import (
    attach_internal_subnodes,
    build_match_unit_dict,
    build_subnode_specs_from_conclusions,
    get_node_content,
    get_node_formal_content,
    merge_node_with_source_envelope,
)
from pipeline.stages.build_relations.stage import merge_relations_lists
from pipeline.stages.build_relations.stage import create_entity_pairs, extract_explicit_relations
from pipeline.stages.compile_logic_form.stage import build_logic_form_input_dict
from pipeline.stages.compile_logic_form.stage import run as run_compile_logic_form
from pipeline.stages.extract_logic_tuples.stage import build_logic_tuple_input_dict
from pipeline.stages.extract_logic_tuples.stage import logic_tuple_input_quality_issues
from pipeline.stages.extract_logic_tuples.stage import split_statement_with_title_dict
from pipeline.stages.generate_titles.stage import merge_titles_into_statement_dict
from pipeline.stages.split_nodes.stage import (
    _build_split_input_dict,
    apply_discourse_decomposition,
    apply_node_split,
)


def _sealed_statement_dict(statement_dict):
    sealed = copy.deepcopy(statement_dict)
    for wrapper in sealed.values():
        if not isinstance(wrapper, dict):
            continue
        node = wrapper.get("pos1")
        if not isinstance(node, dict) or "_source_envelope" in node:
            continue
        sealed_node, _ = merge_node_with_source_envelope(
            node,
            {},
            stage_name="extract_statements",
            allowed_fields=(),
            seal=True,
            source_metadata={
                "source_text": node.get("content", ""),
                "source_block_key": wrapper.get("_orig_key"),
            },
        )
        wrapper["pos1"] = sealed_node
    return sealed


def test_split_node_output_preserves_parent_and_attaches_complete_children():
    statement_dict = {
        0: {
            "pos1": {
                "node_type": "定理",
                "content": "Assume A. Then (1) P. (2) Q.",
                "proof": "Proof text.",
                "label": "T1",
            },
            "_orig_key": 7,
        }
    }
    split_result = {
        0: {
            "parent_node": {
                "node_type": "定理",
                "content": "Assume A. Then (1) P. (2) Q.",
                "proof": "Proof text.",
                "label": "T1",
            },
            "sub_nodes": [
                {
                    "index": 1,
                    "content": "Assume A. Then P.",
                    "kind": "conclusion",
                    "statement_form": "implication",
                    "source_conclusion": "(1) P",
                    "applicable_context": "A",
                    "applicable_conditions_text": ["A"],
                    "conclusion": "P",
                },
                {
                    "index": 2,
                    "content": "Assume A. Then Q.",
                    "kind": "conclusion",
                    "statement_form": "implication",
                    "source_conclusion": "(2) Q",
                    "applicable_context": "A",
                    "applicable_conditions_text": ["A"],
                    "conclusion": "Q",
                },
            ],
        }
    }

    result = apply_node_split(_sealed_statement_dict(statement_dict), split_result)
    block = result[0]["pos1"]

    assert block["content"] == "Assume A. Then (1) P. (2) Q."
    assert block["remark"]["original_form"] == "Assume A. Then (1) P. (2) Q."
    assert set(block["remark"].keys()) == {"original_form"}
    assert [spec["conclusion"] for spec in block["subnode_specs"]] == ["P", "Q"]
    assert "split_required" not in block
    assert "split_reason" not in block
    assert len(block["sub_nodes"]) == 2
    assert block["sub_nodes"][0]["content"] == "Assume A. Then P."
    assert block["sub_nodes"][1]["applicable_conditions_text"] == ["A"]
    assert result[0]["_orig_key"] == 7


def test_single_conclusion_node_keeps_virtual_spec_without_expandable_children():
    statement_dict = {
        0: {
            "pos1": {
                "node_type": "定理",
                "content": "Assume A. Then P.",
                "proof": "",
                "label": "T2",
            },
            "_orig_key": 0,
        }
    }
    split_result = {
        0: {
            "parent_node": {},
            "sub_nodes": [
                {
                    "index": 1,
                    "content": "Assume A. Then P.",
                    "kind": "unsplit",
                    "statement_form": "implication",
                    "applicable_conditions_text": ["A"],
                    "conclusion": "P",
                }
            ],
        }
    }

    result = apply_node_split(_sealed_statement_dict(statement_dict), split_result)
    block = result[0]["pos1"]

    assert "sub_nodes" not in block
    assert "split_required" not in block
    assert len(block["subnode_specs"]) == 1
    assert block["subnode_specs"][0]["content"] == "Assume A. Then P."


def test_legacy_entrypoint_ignores_old_discourse_shape():
    statement_dict = {
        0: {
            "pos1": {
                "node_type": "定理",
                "content": "By Proposition 2.1, $D_i = k$ for all i.",
                "proof": "",
                "label": "T3",
            },
            "_orig_key": 0,
        }
    }
    legacy_shape = {0: {}}

    result = apply_discourse_decomposition(
        _sealed_statement_dict(statement_dict),
        legacy_shape,
    )
    remark = result[0]["pos1"]["remark"]

    assert remark == {"original_form": "By Proposition 2.1, $D_i = k$ for all i."}


def test_formal_and_original_content_helpers_support_parent_and_child_shapes():
    parent_node = {
        "node_type": "定理",
        "content": "Assume A. Then P.",
        "remark": {
            "original_form": "Assume A. Then P.",
        },
    }
    child_node = {
        "node_type": "定理",
        "content": "Assume A. Then P.",
        "remark": {
            "original_form": "Assume A. Then P.",
        },
        "conclusion": "P.",
    }

    assert get_node_content(parent_node) == "Assume A. Then P."
    assert get_node_formal_content(parent_node) == "Assume A. Then P."
    assert get_node_content(child_node) == "Assume A. Then P."
    assert get_node_formal_content(child_node) == "P."


def test_title_merge_preserves_split_node_shape():
    statement_dict = {
        0: {
            "pos1": {
                "node_type": "定理",
                "content": "Original theorem.",
                "remark": {
                    "original_form": "Original theorem.",
                },
                "subnode_specs": [{"index": 1, "conclusion": "Original theorem."}],
                "proof": "",
                "label": "T",
            },
            "_orig_key": 0,
        }
    }
    title_result = {0: {"title": {"chinese": "测试定理", "english": "Test Theorem"}}}

    merged = merge_titles_into_statement_dict(
        _sealed_statement_dict(statement_dict),
        title_result,
    )
    block = merged[0]["pos1"]

    assert block["content"] == "Original theorem."
    assert block["title"]["english"] == "Test Theorem"
    assert block["subnode_specs"][0]["conclusion"] == "Original theorem."


def test_logic_tuple_input_uses_split_specs_without_content_field():
    structured_input = {
        0: {
            "pos1": {
                "node_type": "定理",
                "title": {"chinese": "测试", "english": "Test"},
                "content": "Assume A. Then P.",
                "remark": {
                    "original_form": "Assume A. Then P.",
                },
                "subnode_specs": [
                    {
                        "index": 1,
                        "content": "Assume A. Then P.",
                        "applicable_conditions_text": ["A"],
                        "conclusion": "P",
                    }
                ],
                "proof": "",
                "label": "T",
            },
            "_orig_key": 0,
        }
    }

    payload = build_logic_tuple_input_dict(structured_input)[0]["pos1"]

    assert payload["original_form"] == "Assume A. Then P."
    assert payload["subnode_specs"][0]["content"] == "Assume A. Then P."
    assert "formal_statement_core" not in payload
    assert "local_conclusions" not in payload
    assert "content" not in payload


def test_logic_tuple_input_recovers_original_form_from_content_dict_content_field():
    structured_input = {
        0: {
            "pos1": {
                "node_type": "proposition",
                "title": {"english": "Content Dict"},
                "content": {"content": "Assume A. Then P."},
                "subnode_specs": [{"index": 1, "content": "Assume A. Then P.", "conclusion": "P"}],
                "proof": "",
                "label": "P1",
            },
            "_orig_key": 0,
        }
    }

    payload = build_logic_tuple_input_dict(structured_input)[0]["pos1"]

    assert payload["original_form"] == "Assume A. Then P."
    assert logic_tuple_input_quality_issues(build_logic_tuple_input_dict(structured_input), structured_input) == []


def test_logic_tuple_input_recovers_original_form_from_subnode_specs_when_parent_text_missing():
    structured_input = {
        0: {
            "pos1": {
                "node_type": "lemma",
                "title": {"english": "Spec Fallback"},
                "content": {},
                "subnode_specs": [
                    {"index": 1, "content": "If P, then Q.", "conclusion": "Q"},
                    {"index": 2, "content": "If Q, then P.", "conclusion": "P"},
                ],
                "proof": "",
                "label": "L1",
            },
            "_orig_key": 0,
        }
    }

    payload = build_logic_tuple_input_dict(structured_input)[0]["pos1"]

    assert payload["original_form"] == "If P, then Q.\nIf Q, then P."
    assert [spec["content"] for spec in payload["subnode_specs"]] == ["If P, then Q.", "If Q, then P."]


def test_logic_tuple_input_prefers_subnode_parent_original_form_over_child_join():
    structured_input = {
        0: {
            "pos1": {
                "node_type": "theorem",
                "title": {"english": "Subnode Parent Fallback"},
                "content": {},
                "sub_nodes": [
                    {
                        "index": 1,
                        "content": "If P, then Q.",
                        "remark": {"parent_original_form": "P iff Q.", "original_form": "If P, then Q."},
                    },
                    {
                        "index": 2,
                        "content": "If Q, then P.",
                        "remark": {"parent_original_form": "P iff Q.", "original_form": "If Q, then P."},
                    },
                ],
                "proof": "",
                "label": "T-iff",
            },
            "_orig_key": 0,
        }
    }

    payload = build_logic_tuple_input_dict(structured_input)[0]["pos1"]

    assert payload["original_form"] == "P iff Q."


def test_logic_tuple_input_quality_flags_empty_original_form_with_source_evidence():
    structured_input = {
        0: {
            "pos1": {
                "node_type": "proposition",
                "title": {"english": "Malformed Logic Input"},
                "content": {"content": "Assume A. Then P."},
                "proof": "",
                "label": "P1",
            },
            "_orig_key": 0,
        }
    }
    logic_input = {
        0: {
            "pos1": {
                "node_type": "proposition",
                "title": {"english": "Malformed Logic Input"},
                "original_form": "",
                "subnode_specs": [],
                "proof": "",
                "label": "P1",
            },
            "_orig_key": 0,
        }
    }

    issues = logic_tuple_input_quality_issues(logic_input, structured_input)

    assert issues[0]["task_key"] == "0"
    assert issues[0]["issue_type"] == "empty_original_form_with_source_evidence"


def test_english_definition_and_statement_types_use_shared_type_classification():
    statement_with_title_dict = {
        0: {
            "pos1": {
                "node_type": "definition",
                "content": "A widget is a set with structure.",
                "proof": "",
                "label": "D1",
            },
            "_orig_key": 0,
        },
        1: {
            "pos1": {
                "node_type": "proposition",
                "content": "If A holds, then B holds.",
                "proof": "",
                "label": "P1",
            },
            "_orig_key": 1,
        },
    }

    definition_axiom_dict, structured_input_dict = split_statement_with_title_dict(statement_with_title_dict)

    assert sorted(definition_axiom_dict.keys()) == [0]
    assert sorted(structured_input_dict.keys()) == [1]


def test_relation_example_and_exercise_types_enter_logic_tuple_stage():
    node_types = {
        0: "theorem",
        1: "lemma",
        2: "proposition",
        3: "corollary",
        4: "definition",
        5: "axiom",
        6: "remark",
        7: "example",
        8: "exercise",
        9: "例子",
        10: "练习",
        11: "习题",
        12: "示例",
    }
    statement_with_title_dict = {
        key: {
            "pos1": {
                "node_type": node_type,
                "content": f"Source for {node_type}.",
                "proof": "",
                "label": f"N{key}",
            },
            "_orig_key": key,
        }
        for key, node_type in node_types.items()
    }

    passthrough, structured = split_statement_with_title_dict(
        statement_with_title_dict
    )

    assert sorted(structured) == [0, 1, 2, 3, 7, 8, 9, 10, 11, 12]
    assert sorted(passthrough) == [4, 5, 6]
    assert passthrough[6]["node_type"] == "remark"


def test_logic_tuple_input_rejects_non_relation_cache_entry():
    try:
        build_logic_tuple_input_dict(
            {
                0: {
                    "pos1": {
                        "node_type": "remark",
                        "content": "Source remark.",
                        "label": "Remark 1",
                    }
                }
            }
        )
    except ValueError as exc:
        assert "only theorem/relation, example, or exercise" in str(exc)
        assert "Remark 1" in str(exc)
    else:
        raise AssertionError("Expected remark to be rejected by logic tuple input")


def test_example_and_exercise_enter_logic_tuple_and_compile_inputs():
    for node_type in ("example", "exercise"):
        logic_tuple_input = build_logic_tuple_input_dict(
            {
                0: {
                    "pos1": {
                        "node_type": node_type,
                        "content": f"Concrete {node_type}.",
                        "label": node_type,
                    }
                }
            }
        )
        assert logic_tuple_input[0]["pos1"]["node_type"] == node_type

        logic_form_input = build_logic_form_input_dict(
            {
                0: {
                    "node_type": node_type,
                    "global_id": f"{node_type}-id",
                    "statement_form": "other",
                    "remark": {"text_normalized": f"Concrete {node_type}."},
                    "subject": [],
                    "context": [],
                    "variables": [],
                    "conditions": [],
                    "conclusions": [{"id": "q1", "text": "Concrete fact."}],
                }
            }
        )
        assert logic_form_input["0"]["pos1"]["node_type"] == node_type


def test_match_units_include_examples_and_exercises_but_not_remarks():
    units = build_match_unit_dict(
        {
            0: {"node_type": "theorem", "global_id": "t"},
            1: {"node_type": "definition", "global_id": "d"},
            2: {"node_type": "remark", "global_id": "r"},
            3: {"node_type": "example", "global_id": "e"},
            4: {"node_type": "exercise", "global_id": "x"},
        }
    )

    assert sorted(units) == ["0", "1", "3", "4"]


def test_english_statement_types_enter_logic_form_input_and_relation_pairs():
    node_dict = {
        0: {
            "global_id": "n0",
            "node_type": "lemma",
            "title": {"chinese": "Lemma", "english": "Lemma"},
            "remark": {"text_normalized": "A implies B"},
            "statement_form": "implication",
            "subject": [],
            "context": [],
            "variables": [],
            "conditions": [{"id": "c1", "text_normalized": "A"}],
            "conclusions": [{"id": "q1", "text": "B", "text_normalized": "B"}],
            "label": "L1",
        },
        1: {
            "global_id": "n1",
            "node_type": "theorem",
            "title": {"chinese": "Theorem", "english": "Theorem"},
            "remark": {"text_normalized": "Assume B. Then C."},
            "statement_form": "implication",
            "subject": [],
            "context": [],
            "variables": [],
            "conditions": [{"id": "c1", "text_normalized": "B"}],
            "conclusions": [{"id": "q1", "text": "C", "text_normalized": "C"}],
            "label": "T1",
            "reference_signals": {
                "explicit_targets": [
                    {"resolved_index": 0, "surface": "Lemma L1", "match_mode": "label"}
                ]
            },
        },
    }

    logic_input = build_logic_form_input_dict(node_dict)
    proof_pairs, definition_pairs = create_entity_pairs(
        list(node_dict.values()),
        api_key=None,
        api_url=None,
        embedding_model=None,
        use_keyword_filter=True,
    )
    explicit_edges, explicit_pairs = extract_explicit_relations(list(node_dict.values()))

    assert sorted(logic_input.keys()) == ["0", "1"]
    assert len(proof_pairs) == 1
    assert definition_pairs == {}
    assert len(explicit_edges) == 1
    assert explicit_pairs == {(1, 0)}


def test_iff_three_way_builds_cycle_pair_specs():
    binary_specs = build_subnode_specs_from_conclusions(["P iff Q"])
    assert [spec["conclusion"] for spec in binary_specs] == ["If P, then Q.", "If Q, then P."]
    assert [spec["statement_form"] for spec in binary_specs] == ["implication", "implication"]

    specs = build_subnode_specs_from_conclusions(["P iff Q iff R"])

    assert [spec["kind"] for spec in specs] == ["iff_cycle_pair", "iff_cycle_pair", "iff_cycle_pair"]
    assert [spec["conclusion"] for spec in specs] == ["P iff Q.", "Q iff R.", "R iff P."]
    assert [spec["equivalence_components"] for spec in specs] == [["P", "Q"], ["Q", "R"], ["R", "P"]]


def test_split_nodes_expands_single_equivalence_child():
    statement_dict = {
        0: {
            "pos1": {
                "node_type": "proposition",
                "content": "P iff Q.",
                "proof": "",
                "label": "T-iff",
            },
            "_orig_key": 0,
        }
    }
    split_input, passthrough = _build_split_input_dict(statement_dict)

    assert sorted(split_input.keys()) == [0]
    assert passthrough == {}

    split_result = {
        0: {
            "parent_node": {},
            "sub_nodes": [
                {
                    "index": 1,
                    "content": "P iff Q.",
                    "kind": "unsplit",
                    "statement_form": "equivalence",
                    "source_conclusion": "P iff Q",
                    "conclusion": "P iff Q",
                }
            ],
        }
    }

    result = apply_node_split(_sealed_statement_dict(statement_dict), split_result)
    block = result[0]["pos1"]

    assert block["subnode_count"] == 2
    assert [spec["kind"] for spec in block["subnode_specs"]] == ["iff_direction", "iff_direction"]
    assert [spec["statement_form"] for spec in block["subnode_specs"]] == ["implication", "implication"]
    assert [spec["equivalence_components"] for spec in block["subnode_specs"]] == [["P", "Q"], ["Q", "P"]]
    assert [child["kind"] for child in block["sub_nodes"]] == ["iff_direction", "iff_direction"]


def test_internal_subnodes_copy_conditions_and_use_child_content():
    node = {
        "node_type": "定理",
        "title": {"chinese": "测试", "english": "Test"},
        "content": "Assume A. Then P and Q.",
        "remark": {
            "original_form": "Assume A. Then P and Q.",
        },
        "subnode_specs": [
            {
                "index": 1,
                "content": "Assume A. Then P.",
                "kind": "conclusion",
                "statement_form": "implication",
                "applicable_conditions_text": ["A"],
                "conclusion": "P",
            },
            {
                "index": 2,
                "content": "Assume A. Then Q.",
                "kind": "conclusion",
                "statement_form": "implication",
                "applicable_conditions_text": ["A"],
                "conclusion": "Q",
            },
        ],
        "conditions": [{"id": "c1", "text": "A"}],
        "conclusions": [{"id": "q1", "text": "P"}, {"id": "q2", "text": "Q"}],
        "variables": [{"name": "A", "type": "proposition"}],
        "label": "T",
    }

    enriched = attach_internal_subnodes(node)

    assert "conditions" not in enriched
    assert "conclusions" not in enriched
    assert len(enriched["sub_nodes"]) == 2
    assert enriched["sub_nodes"][0]["content"] == "Assume A. Then P."
    assert enriched["sub_nodes"][0]["remark"]["original_form"] == "Assume A. Then P."
    assert enriched["sub_nodes"][0]["applicable_conditions"] == [{"id": "c1", "text": "A"}]
    assert enriched["sub_nodes"][1]["conclusions"] == [{"id": "q2", "text": "Q"}]

    units = build_match_unit_dict({0: enriched})
    assert sorted(units.keys()) == ["0__sub1", "0__sub2"]
    assert units["0__sub1"]["conclusions"][0]["text"] == "P"


def test_equivalence_parent_replaces_stale_unsplit_specs_from_logic_conclusions():
    node = {
        "node_type": "lemma",
        "title": {"chinese": "\u7b49\u4ef7\u6d4b\u8bd5", "english": "Equivalence Test"},
        "statement_form": "equivalence",
        "remark": {
            "original_form": "Assume A. Then P iff Q, and R.",
        },
        "subnode_specs": [
            {
                "index": 1,
                "kind": "unsplit",
                "statement_form": "",
                "source_conclusion": "Assume A. Then P iff Q, and R.",
                "conclusion": "Assume A. Then P iff Q, and R.",
            }
        ],
        "conditions": [{"id": "c1", "text": "A"}],
        "conclusions": [
            {"id": "q1", "text": "P iff Q"},
            {"id": "q2", "text": "R"},
        ],
        "variables": [],
        "label": "T-iff",
    }

    enriched = attach_internal_subnodes(node)

    assert "statement_form" not in enriched
    assert enriched["subnode_count"] == 3
    assert [spec["kind"] for spec in enriched["subnode_specs"]] == [
        "iff_direction",
        "iff_direction",
        "conclusion",
    ]
    assert [spec["statement_form"] for spec in enriched["subnode_specs"][:2]] == [
        "implication",
        "implication",
    ]
    assert enriched["subnode_specs"][0]["equivalence_components"] == ["P", "Q"]
    assert enriched["subnode_specs"][1]["equivalence_components"] == ["Q", "P"]
    assert enriched["sub_nodes"][2]["conclusions"] == [{"id": "q2", "text": "R"}]


def test_split_nodes_preserves_model_subnode_conditions():
    statement_dict = {
        0: {
            "pos1": {
                "node_type": "定理",
                "content": "Assume A. Then P and Q.",
                "proof": "",
                "label": "T",
            },
            "_orig_key": 0,
        }
    }
    split_result = {
        0: {
            "parent_node": {},
            "sub_nodes": [
                {
                    "index": 1,
                    "content": "Assume A. Then P.",
                    "kind": "conclusion",
                    "statement_form": "implication",
                    "source_conclusion": "P",
                    "applicable_context": "A",
                    "applicable_conditions_text": ["A"],
                    "conclusion": "P",
                },
                {
                    "index": 2,
                    "content": "Assume A. Then Q.",
                    "kind": "conclusion",
                    "statement_form": "implication",
                    "source_conclusion": "Q",
                    "applicable_context": "A",
                    "applicable_conditions_text": ["A"],
                    "conclusion": "Q",
                },
            ],
        }
    }

    result = apply_node_split(_sealed_statement_dict(statement_dict), split_result)
    specs = result[0]["pos1"]["subnode_specs"]

    assert len(specs) == 2
    assert specs[0]["applicable_conditions_text"] == ["A"]
    assert specs[0]["content"] == "Assume A. Then P."
    assert specs[1]["conclusion"] == "Q"


def test_non_relation_nodes_skip_llm_and_match_unsplit_output_shape():
    statement_dict = {
        0: {
            "pos1": {
                "node_type": "习题",
                "content": "Show that P.",
                "proof": "",
                "label": "E1",
            },
            "_orig_key": 0,
        },
        1: {
            "pos1": {
                "node_type": "定理",
                "content": "Assume A. Then P.",
                "proof": "",
                "label": "T1",
            },
            "_orig_key": 1,
        },
    }
    split_input, passthrough = _build_split_input_dict(statement_dict)

    assert sorted(split_input.keys()) == [1]
    assert sorted(passthrough.keys()) == [0]

    llm_unsplit_result = {
        1: {
            "parent_node": {},
            "sub_nodes": [
                {
                    "index": 1,
                    "content": "Assume A. Then P.",
                    "kind": "conclusion",
                    "statement_form": "implication",
                    "applicable_conditions_text": ["A"],
                    "conclusion": "P",
                }
            ],
        }
    }
    merged = {}
    merged.update(passthrough)
    merged.update(llm_unsplit_result)

    result = apply_node_split(_sealed_statement_dict(statement_dict), merged)
    exercise_block = result[0]["pos1"]
    theorem_block = result[1]["pos1"]

    for block in (exercise_block, theorem_block):
        assert "sub_nodes" not in block
        assert "subnode_display" not in block
        assert "subnode_count" not in block
        assert len(block["subnode_specs"]) == 1
        assert block["subnode_specs"][0]["kind"] == "unsplit"


def test_relation_merge_combines_child_matches_per_parent_pair():
    relations = [
        {
            "出发节点": "B",
            "到达节点": "A",
            "关系": "逻辑依赖",
            "理由": "match 1",
            "child_matches": [{"source_sub_index": 1, "target_sub_index": 1}],
        },
        {
            "出发节点": "B",
            "到达节点": "A",
            "关系": "逻辑依赖",
            "理由": "match 2",
            "child_matches": [{"source_sub_index": 2, "target_sub_index": 1}],
        },
    ]

    merged = merge_relations_lists(relations, [])

    assert len(merged) == 1
    assert "match 1" in merged[0]["理由"]
    assert "match 2" in merged[0]["理由"]
    assert len(merged[0]["child_matches"]) == 2


def test_compile_logic_form_reports_missing_node_dict_clearly():
    try:
        run_compile_logic_form(SimpleNamespace(output_dir="unused"), {})
    except RuntimeError as exc:
        assert "requires node_dict from extract_logic_tuples" in str(exc)
    else:
        raise AssertionError("Expected compile_logic_form to reject missing node_dict")


if __name__ == "__main__":
    test_split_node_output_preserves_parent_and_attaches_complete_children()
    test_single_conclusion_node_keeps_virtual_spec_without_expandable_children()
    test_legacy_entrypoint_ignores_old_discourse_shape()
    test_formal_and_original_content_helpers_support_parent_and_child_shapes()
    test_title_merge_preserves_split_node_shape()
    test_logic_tuple_input_uses_split_specs_without_content_field()
    test_logic_tuple_input_recovers_original_form_from_content_dict_content_field()
    test_logic_tuple_input_recovers_original_form_from_subnode_specs_when_parent_text_missing()
    test_logic_tuple_input_prefers_subnode_parent_original_form_over_child_join()
    test_logic_tuple_input_quality_flags_empty_original_form_with_source_evidence()
    test_english_definition_and_statement_types_use_shared_type_classification()
    test_relation_example_and_exercise_types_enter_logic_tuple_stage()
    test_logic_tuple_input_rejects_non_relation_cache_entry()
    test_example_and_exercise_enter_logic_tuple_and_compile_inputs()
    test_match_units_include_examples_and_exercises_but_not_remarks()
    test_english_statement_types_enter_logic_form_input_and_relation_pairs()
    test_iff_three_way_builds_cycle_pair_specs()
    test_split_nodes_expands_single_equivalence_child()
    test_internal_subnodes_copy_conditions_and_use_child_content()
    test_equivalence_parent_replaces_stale_unsplit_specs_from_logic_conclusions()
    test_split_nodes_preserves_model_subnode_conditions()
    test_non_relation_nodes_skip_llm_and_match_unsplit_output_shape()
    test_relation_merge_combines_child_matches_per_parent_pair()
    test_compile_logic_form_reports_missing_node_dict_clearly()
    print("split_nodes tests passed")
