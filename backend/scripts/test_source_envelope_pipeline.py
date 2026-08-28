import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.common.node import (  # noqa: E402
    SOURCE_ENVELOPE_KEY,
    attach_internal_subnodes,
    merge_node_with_source_envelope,
)
from pipeline.stages.ensure_coverage.stage import (  # noqa: E402
    COVERAGE_DATA_TEMPLATE,
    _validate_target_result,
)
from pipeline.stages.extract_logic_tuples.stage import (  # noqa: E402
    _merge_logic_tuple_results,
)
from pipeline.stages.extract_logic_tuples.templates import (  # noqa: E402
    DERIVED_LOGIC_FIELDS,
    data_template06,
)
from pipeline.stages.extract_statements.stage import _seal_statement_nodes  # noqa: E402
from pipeline.stages.finalize_output.stage import run as finalize_output  # noqa: E402
from pipeline.stages.generate_titles.templates import (  # noqa: E402
    data_template05,
    validation05,
)
from pipeline.stages.split_nodes.stage import _apply_split_to_block  # noqa: E402
from pipeline.stages.split_nodes.templates import (  # noqa: E402
    SPLIT_SUBNODE_FIELDS,
    data_template04,
)


def _sealed_node(content="If A, then B.", **overrides):
    node = {
        "node_type": "theorem",
        "content": content,
        "proof": "Original proof.",
        "label": "Theorem 1",
        "title": {"english": "Original title"},
    }
    node.update(overrides)
    sealed, _ = merge_node_with_source_envelope(
        node,
        {},
        stage_name="extract_statements",
        allowed_fields=(),
        seal=True,
        source_metadata={
            "source_text": content,
            "source_span": {"start": 0, "end": len(content)},
            "source_block_key": "0",
        },
    )
    return sealed


class SourceEnvelopeTests(unittest.TestCase):
    def test_model_templates_only_request_derived_fields(self):
        self.assertEqual(
            set(json.loads(COVERAGE_DATA_TEMPLATE)),
            {"content_quote", "proof_quote"},
        )
        split_template = json.loads(data_template04)
        self.assertEqual(set(split_template), {"sub_nodes"})
        self.assertEqual(
            set(split_template["sub_nodes"][0]),
            set(SPLIT_SUBNODE_FIELDS),
        )
        self.assertEqual(
            set(json.loads(data_template06)),
            set(DERIVED_LOGIC_FIELDS),
        )
        self.assertEqual(set(json.loads(data_template05)), {"title"})
        self.assertFalse(validation05({"content": "forged complete node"}))
        self.assertTrue(
            validation05(
                {"title": {"chinese": "", "english": "Short title"}}
            )
        )

    def test_markdown_extraction_seals_every_statement_node(self):
        sealed = _seal_statement_nodes(
            {
                0: {
                    "pos1": {
                        "node_type": "remark",
                        "content": "Source remark.",
                        "proof": "",
                        "label": "",
                    },
                    "_orig_key": 0,
                    "source_text": "Source remark.",
                    "source_block_key": "0",
                }
            }
        )
        self.assertIn(SOURCE_ENVELOPE_KEY, sealed[0]["pos1"])
        self.assertEqual(
            sealed[0]["pos1"][SOURCE_ENVELOPE_KEY]["source_text"],
            "Source remark.",
        )

    def test_merge_applies_only_allowlisted_derived_fields(self):
        source = _sealed_node()
        merged, audit = merge_node_with_source_envelope(
            source,
            {
                "statement_form": "implication",
                "content": "forged",
                "proof": "forged",
                "label": "forged",
                "node_type": "remark",
                "title": {"english": "forged"},
                "global_id": "forged",
            },
            stage_name="extract_logic_tuples",
            allowed_fields={"statement_form"},
        )

        self.assertEqual(merged["statement_form"], "implication")
        self.assertEqual(merged["content"], source["content"])
        self.assertEqual(merged["proof"], source["proof"])
        self.assertEqual(merged["label"], source["label"])
        self.assertEqual(merged["node_type"], source["node_type"])
        self.assertEqual(merged["title"], source["title"])
        self.assertEqual(merged["global_id"], source["global_id"])
        self.assertEqual(
            set(audit["ignored_fields"]),
            {"content", "global_id", "label", "node_type", "proof", "title"},
        )

    def test_merge_requires_new_cache_schema(self):
        with self.assertRaisesRegex(ValueError, "Rerun from extract_statements"):
            merge_node_with_source_envelope(
                {"node_type": "remark", "content": "Source remark."},
                {},
                stage_name="split_nodes",
                allowed_fields=(),
            )

    def test_merge_rejects_tampered_envelope(self):
        source = _sealed_node()
        source[SOURCE_ENVELOPE_KEY]["proof"] = "tampered"
        with self.assertRaisesRegex(ValueError, "integrity check failed"):
            merge_node_with_source_envelope(
                source,
                {},
                stage_name="split_nodes",
                allowed_fields=(),
            )

    def test_merge_deep_copies_derived_fields(self):
        source = _sealed_node()
        derived = {"conditions": [{"id": "c1", "text": "A"}]}
        merged, _ = merge_node_with_source_envelope(
            source,
            derived,
            stage_name="extract_logic_tuples",
            allowed_fields={"conditions"},
        )
        derived["conditions"][0]["text"] = "tampered"
        self.assertEqual(merged["conditions"][0]["text"], "A")

    def test_split_ignores_parent_identity_fields(self):
        source = _sealed_node()
        result = _apply_split_to_block(
            source,
            {
                "parent_node": {"content": "forged"},
                "sub_nodes": [
                    {
                        "index": 1,
                        "content": "If A, then B.",
                        "conclusion": "B",
                        "kind": "unsplit",
                        "statement_form": "implication",
                        "source_conclusion": "B",
                        "applicable_context": "",
                        "applicable_conditions_text": ["A"],
                        "equivalence_components": [],
                        "label_suffix": "",
                        "node_type": "remark",
                        "proof": "forged",
                        "label": "forged",
                        "unexpected": "forged",
                    }
                ],
            },
        )
        self.assertEqual(result["content"], source["content"])
        self.assertEqual(result["proof"], source["proof"])
        self.assertEqual(result["label"], source["label"])
        self.assertEqual(result["node_type"], source["node_type"])
        self.assertIn("parent_node", result["_source_merge_audits"][-1]["ignored_fields"])
        self.assertIn("unexpected", result["_source_merge_audits"][-1]["ignored_fields"])

    def test_materialized_subnodes_inherit_parent_identity(self):
        source = _sealed_node()
        split = _apply_split_to_block(
            source,
            {
                "sub_nodes": [
                    {
                        "index": 1,
                        "content": "If A, then B.",
                        "conclusion": "B",
                        "kind": "conclusion",
                        "statement_form": "implication",
                        "source_conclusion": "B",
                        "applicable_context": "",
                        "applicable_conditions_text": ["A"],
                        "equivalence_components": [],
                        "label_suffix": "a",
                    },
                    {
                        "index": 2,
                        "content": "If B, then C.",
                        "conclusion": "C",
                        "kind": "conclusion",
                        "statement_form": "implication",
                        "source_conclusion": "C",
                        "applicable_context": "",
                        "applicable_conditions_text": ["B"],
                        "equivalence_components": [],
                        "label_suffix": "b",
                    },
                ],
            },
        )
        materialized = attach_internal_subnodes(split)

        self.assertEqual(len(materialized["sub_nodes"]), 2)
        self.assertEqual(materialized["sub_nodes"][0]["node_type"], source["node_type"])
        self.assertEqual(materialized["sub_nodes"][0]["proof"], source["proof"])
        self.assertEqual(materialized["sub_nodes"][0]["label"], "Theorem 1.a")
        self.assertEqual(materialized["sub_nodes"][0]["parent_label"], source["label"])

    def test_logic_tuple_partial_result_preserves_missing_source_node(self):
        first = _sealed_node("If A, then B.", label="T1")
        second = _sealed_node("If C, then D.", label="T2")
        structured = {
            "0": {"pos1": first, "_orig_key": 0},
            "1": {"pos1": second, "_orig_key": 1},
        }
        merged = _merge_logic_tuple_results(
            structured,
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
        )
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged["1"]["content"], "If C, then D.")
        self.assertEqual(
            merged["1"]["_derivation_status"]["extract_logic_tuples"]["status"],
            "degraded",
        )

    def test_coverage_builds_node_from_exact_quotes(self):
        candidate = {
            "candidate_id": "block:1",
            "origin": "segment_block",
            "block_id": 1,
            "source_text": "# Definition 1\nA group is a monoid with inverses.\nProof. Immediate.",
            "source_start": 100,
            "source_end": 167,
            "target_type": "definition",
            "target_label": "Definition 1",
        }
        node, diagnostic = _validate_target_result(
            candidate,
            {
                "content_quote": "A group is a monoid with inverses.",
                "proof_quote": "Proof. Immediate.",
                "label": "forged",
            },
        )
        self.assertEqual(diagnostic["status"], "accepted")
        self.assertEqual(node["content"], "A group is a monoid with inverses.")
        self.assertEqual(node["label"], "Definition 1")
        self.assertIn("label", node["_source_merge_audits"][0]["ignored_fields"])

    def test_finalize_removes_internal_fields_and_reports_degradation(self):
        node = _sealed_node(
            "A group is a set with an operation.",
            node_type="definition",
            proof="",
        )
        node["_derivation_status"] = {
            "extract_logic_tuples": {
                "status": "degraded",
                "task_key": "0",
                "reason": "unresolved_model_task",
            }
        }
        node["_source_merge_audits"] = [
            {
                "stage": "extract_logic_tuples",
                "ignored_fields": ["content"],
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            state = finalize_output(
                SimpleNamespace(
                    output_dir=temp_dir,
                    output_node_path=None,
                    output_edge_path=None,
                ),
                {
                    "node_list": [copy.deepcopy(node)],
                    "edge_list": [],
                    "degraded_stage_runs": {
                        "extract_logic_tuples": {"failed_task_count": 1}
                    },
                },
            )
        final_node = state["node_list"][0]
        self.assertNotIn(SOURCE_ENVELOPE_KEY, final_node)
        self.assertNotIn("_derivation_status", final_node)
        self.assertEqual(state["quality_summary"]["status"], "degraded")
        self.assertEqual(
            state["quality_summary"]["ignored_protected_field_count"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
