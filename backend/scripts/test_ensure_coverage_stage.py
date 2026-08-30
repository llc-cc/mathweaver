import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tempfile
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.stages.ensure_coverage import stage as coverage_stage
from JoinAgent import MultiProcessor


SOURCE = (
    "# Theorem 1.1\nFirst statement.\n\n"
    "# Definition 1.2\nSecond statement.\n\n"
    "Remark. This ordinary prose is not a structural candidate.\n"
)

GLOBAL_OPTIMIZATION_SOURCE = (
    "In global optimization, the true global solution of the optimization problem (1.1) is found; the\n"
    "compromise is efficiency. The worst-case complexity of global optimization methods grows\n"
    "exponentially with the problem sizes n and m.\n"
)
GLOBAL_OPTIMIZATION_QUOTE = (
    "In global optimization, the true global solution of the optimization problem (1.1) is found; the\n"
    "compromise is efficiency."
)


def _logical_block(block_id, label, node_type):
    return {
        "block_id": block_id,
        "raw_block_id": block_id,
        "boundary_role": "top_level_logical_unit_start",
        "label_surface": label,
        "logical_unit_type_hint": node_type,
    }


def _state():
    return {
        "corrected_text": SOURCE,
        "problem_dict": {
            0: {"pos1": "# Theorem 1.1\nFirst statement.\n\n"},
            1: {"pos1": "# Definition 1.2\nSecond statement.\n\n"},
        },
        "segment_blocks_report": {
            "blocks": [
                _logical_block(0, "Theorem 1.1", "theorem"),
                _logical_block(1, "Definition 1.2", "definition"),
            ]
        },
        "unsplit_statement_dict": {
            0: {
                "pos1": {
                    "node_type": "theorem",
                    "label": "Theorem 1.1",
                    "content": "First statement.",
                    "proof": "",
                },
                "_orig_key": 0,
                "source_text": "# Theorem 1.1\nFirst statement.\n\n",
            }
        },
    }


def _context(tmp, source_format="markdown"):
    return SimpleNamespace(
        output_dir=tmp,
        source_format=source_format,
        llm=None,
        parser=None,
        num_threads=1,
        checkpoint=10,
    )


def _coverage_processor(tmp, llm):
    return MultiProcessor(
        llm=llm,
        parse_method=lambda value: value,
        data_template=coverage_stage.COVERAGE_DATA_TEMPLATE,
        prompt_template=coverage_stage.TARGETED_PROMPT_TEMPLATE,
        correction_template=coverage_stage.TARGETED_CORRECTION_TEMPLATE,
        validator=coverage_stage.validation_coverage_quote,
        checkpoint_dir=tmp,
    )


def _global_optimization_payload():
    return {
        "pos1": GLOBAL_OPTIMIZATION_SOURCE,
        "target_type": "definition",
        "target_label": "",
        "_candidate_id": "block:3",
        "_origin": "segment_block",
        "_block_id": 3,
        "_source_start": 2052,
        "_source_end": 2052 + len(GLOBAL_OPTIMIZATION_SOURCE),
    }


def test_coverage_correction_prompt_repeats_exact_source_context():
    invalid_answer = {
        "content_quote": "Your previous recovery was invalid.",
        "proof_quote": "",
    }
    with tempfile.TemporaryDirectory() as tmp:
        processor = _coverage_processor(tmp, Mock())
        prompt = processor.generate_correction_prompt(
            invalid_answer,
            **_global_optimization_payload(),
        )

    assert GLOBAL_OPTIMIZATION_SOURCE in prompt
    assert "Target node type: definition" in prompt
    assert "Target source label:" in prompt
    assert repr(invalid_answer) in prompt
    assert coverage_stage.COVERAGE_DATA_TEMPLATE in prompt
    assert "{pos1}" not in prompt
    assert "{target_type}" not in prompt
    assert "{target_label}" not in prompt


def test_coverage_correction_flow_can_quote_the_repeated_source():
    llm = Mock()
    llm.ask.side_effect = [
        {"content_quote": "", "proof_quote": ""},
        {"content_quote": GLOBAL_OPTIMIZATION_QUOTE, "proof_quote": ""},
    ]
    payload = _global_optimization_payload()

    with tempfile.TemporaryDirectory() as tmp:
        result = _coverage_processor(tmp, llm).process_task("block:3", payload, False)

    assert result == {
        "content_quote": GLOBAL_OPTIMIZATION_QUOTE,
        "proof_quote": "",
    }
    assert llm.ask.call_count == 2
    assert GLOBAL_OPTIMIZATION_SOURCE in llm.ask.call_args_list[1].args[0]


def test_global_optimization_quote_is_accepted_but_prompt_echo_is_rejected():
    candidate = coverage_stage._candidate_from_input(
        "block:3",
        _global_optimization_payload(),
    )
    node, diagnostic = coverage_stage._validate_target_result(
        candidate,
        {"content_quote": GLOBAL_OPTIMIZATION_QUOTE, "proof_quote": ""},
    )
    rejected_node, rejected_diagnostic = coverage_stage._validate_target_result(
        candidate,
        {
            "content_quote": "Your previous recovery was invalid.",
            "proof_quote": "",
        },
    )

    assert node["content"] == GLOBAL_OPTIMIZATION_QUOTE
    assert diagnostic["status"] == "accepted"
    assert rejected_node is None
    assert rejected_diagnostic["reason"] == "content_quote_not_unique_contiguous_substring"


def test_structural_missing_item_is_recovered_once():
    def fake_extract(_context, index_dict, _checkpoint_dir):
        return {
            key: {
                "content_quote": "Second statement.",
                "proof_quote": "",
            }
            for key in index_dict
        }

    with tempfile.TemporaryDirectory() as tmp, patch.object(
        coverage_stage, "_run_extract_tasks", fake_extract
    ):
        result = coverage_stage.run(_context(tmp), _state())

    nodes = [wrapper["pos1"] for wrapper in result["unsplit_statement_dict"].values()]
    assert [node["label"] for node in nodes] == ["Theorem 1.1", "Definition 1.2"]
    assert nodes[1]["coverage_recovered"] is True
    report = result["ensure_coverage_report"]
    assert report["schema_version"] == 2
    assert report["mode"] == "markdown_structural_recovery"
    assert report["recovered_candidate_count"] == 1
    assert report["recovered_node_count"] == 1
    assert report["candidate_count"] == 2


def test_covered_blocks_use_source_identity_even_when_label_is_empty():
    state = _state()
    state["unsplit_statement_dict"][0]["pos1"]["label"] = ""
    state["unsplit_statement_dict"][1] = {
        "pos1": {
            "node_type": "definition",
            "label": "Definition 1.2",
            "content": "Second statement.",
            "proof": "",
        },
        "_orig_key": 1,
        "source_text": "# Definition 1.2\nSecond statement.\n\n",
    }
    with tempfile.TemporaryDirectory() as tmp, patch.object(
        coverage_stage, "_run_extract_tasks", return_value={}
    ) as extract:
        result = coverage_stage.run(_context(tmp), state)

        extract.assert_not_called()
        assert result["ensure_coverage_report"]["missing_count"] == 0
        assert Path(tmp, "unsplit_statement_dict_after_coverage.json").exists()


def test_markdown_heading_candidate_stops_at_next_arbitrary_heading():
    source = (
        "# Example 1.1.9\nExample body.\n\n"
        "# Exercise 1.1.14\nExercise body.\n\n"
        "# 1.1.6 A section\nSection prose.\n"
    )
    candidates = coverage_stage.build_structural_candidates(source, {}, {})
    by_label = {candidate["target_label"]: candidate for candidate in candidates}
    example = by_label["Example 1.1.9"]

    assert set(by_label) == {"Example 1.1.9", "Exercise 1.1.14"}
    assert "Exercise 1.1.14" not in example["source_text"]
    assert example["source_end"] == source.index("# Exercise 1.1.14")


def test_shared_heading_recognizer_covers_languages_exercise_and_unnumbered():
    source = (
        "# Exercise 3\nDo it.\n"
        "# Definition\nAn unnumbered definition.\n"
        "# \u5b9a\u4e49\n\u4e2d\u6587\u5b9a\u4e49\u3002\n"
        "# \u5b9a\u7406 4\n\u4e2d\u6587\u5b9a\u7406\u3002\n"
        "# \u4f8b\n\u4e2d\u6587\u4f8b\u5b50\u3002\n"
    )
    candidates = coverage_stage.build_structural_candidates(source, {}, {})
    labels = [candidate["target_label"] for candidate in candidates]

    assert labels == [
        "Exercise 3",
        "Definition",
        "\u5b9a\u4e49",
        "\u5b9a\u7406 4",
        "\u4f8b",
    ]


def test_ordinary_prose_reference_is_not_a_candidate():
    source = "By Theorem 1 and Theorem 2, the result follows.\nDefinition 3 is cited."
    assert coverage_stage.build_structural_candidates(source, {}, {}) == []


def test_complete_node_fields_are_ignored_when_quotes_are_valid():
    def fake_extract(_context, index_dict, _checkpoint_dir):
        return {
            key: {
                "content_quote": "Second statement.",
                "proof_quote": "",
                "node_type": "exercise",
                "label": "forged",
                "content": "forged",
            }
            for key in index_dict
        }

    with tempfile.TemporaryDirectory() as tmp, patch.object(
        coverage_stage, "_run_extract_tasks", fake_extract
    ):
        result = coverage_stage.run(_context(tmp), _state())

    labels = [
        wrapper["pos1"]["label"]
        for wrapper in result["unsplit_statement_dict"].values()
    ]
    assert labels == ["Theorem 1.1", "Definition 1.2"]
    assert result["ensure_coverage_report"]["recovered_node_count"] == 1


def test_invalid_quote_is_source_only_and_retryable():
    def fake_extract(_context, index_dict, _checkpoint_dir):
        return {
            key: {
                "content_quote": "Not present in source.",
                "proof_quote": "",
            }
            for key in index_dict
        }

    with tempfile.TemporaryDirectory() as tmp, patch.object(
        coverage_stage, "_run_extract_tasks", fake_extract
    ):
        result = coverage_stage.run(_context(tmp), _state())

    report = result["ensure_coverage_report"]
    assert len(result["unsplit_statement_dict"]) == 2
    assert report["rejected_ambiguous_count"] == 1
    assert report["recovered_node_count"] == 0
    assert report["source_only_node_count"] == 1
    assert result["ensure_coverage_stage_run"]["status"] == "unresolved"


def test_missing_quote_is_rejected():
    def fake_extract(_context, index_dict, _checkpoint_dir):
        return {
            key: {
                "content_quote": "Missing statement.",
                "proof_quote": "",
            }
            for key in index_dict
        }

    with tempfile.TemporaryDirectory() as tmp, patch.object(
        coverage_stage, "_run_extract_tasks", fake_extract
    ):
        result = coverage_stage.run(_context(tmp), _state())

    report = result["ensure_coverage_report"]
    assert len(result["unsplit_statement_dict"]) == 2
    assert report["rejected_ambiguous_count"] == 1
    assert report["candidates"][1]["reason"] == "content_quote_not_unique_contiguous_substring"


def test_same_source_punctuation_and_spacing_variant_is_suppressed():
    source = "# Definition 1.2\nSecond\uff0c statement\u3002\n"
    state = {
        "corrected_text": source,
        "problem_dict": {},
        "segment_blocks_report": {"blocks": []},
        "unsplit_statement_dict": {
            0: {
                "pos1": {
                    "node_type": "definition",
                    "label": "",
                    "content": "Second,\\t statement.",
                    "proof": "",
                },
                "_orig_key": "legacy",
                "source_text": source,
            }
        },
    }

    def fake_extract(_context, index_dict, _checkpoint_dir):
        return {
            key: {
                "content_quote": "Second, statement.",
                "proof_quote": "",
            }
            for key in index_dict
        }

    with tempfile.TemporaryDirectory() as tmp, patch.object(
        coverage_stage, "_run_extract_tasks", fake_extract
    ):
        result = coverage_stage.run(_context(tmp), state)

    assert len(result["unsplit_statement_dict"]) == 1
    report = result["ensure_coverage_report"]
    assert report["duplicate_suppressed_count"] == 1
    assert report["recovered_candidate_count"] == 0


def test_failed_recovery_is_reported_with_source_only_node():
    with tempfile.TemporaryDirectory() as tmp, patch.object(
        coverage_stage, "_run_extract_tasks", return_value={}
    ):
        result = coverage_stage.run(_context(tmp), _state())
    assert len(result["unsplit_statement_dict"]) == 2
    assert result["ensure_coverage_report"]["failed_count"] == 1
    assert result["ensure_coverage_report"]["source_only_node_count"] == 1
    assert result["ensure_coverage_stage_run"]["status"] == "unresolved"


def _tex_state():
    return {
        "corrected_text": (
            "\\begin{theorem}First.\\end{theorem}\n"
            "\u7531\u5b9a\u7406 1 \u548c\u5b9a\u7406 2 \u53ef\u5f97\u7ed3\u8bba\u3002\n"
            "\\begin{theorem}Second.\\end{theorem}\n"
        ),
        "problem_dict": {
            0: {"pos1": "\\begin{theorem}First.\\end{theorem}"},
            1: {"pos1": "\\begin{theorem}Second.\\end{theorem}"},
        },
        "tex_extract_statements_report": {
            "blocks": [
                {
                    "source_block_key": "0",
                    "node_type": "theorem",
                    "label": "",
                    "source_span": {"start": 0, "end": 37},
                },
                {
                    "source_block_key": "1",
                    "node_type": "theorem",
                    "label": "",
                    "source_span": {"start": 58, "end": 96},
                },
            ]
        },
        "unsplit_statement_dict": {
            0: {
                "pos1": {
                    "node_type": "theorem",
                    "label": "",
                    "content": "First.",
                    "proof": "",
                },
                "_orig_key": 0,
                "source_text": "\\begin{theorem}First.\\end{theorem}",
            },
            1: {
                "pos1": {
                    "node_type": "theorem",
                    "label": "",
                    "content": "Second.",
                    "proof": "",
                },
                "_orig_key": 1,
                "source_text": "\\begin{theorem}Second.\\end{theorem}",
            },
        },
    }


def test_tex_is_audit_only_and_never_calls_llm_for_references():
    with tempfile.TemporaryDirectory() as tmp, patch.object(
        coverage_stage, "_run_extract_tasks", return_value={}
    ) as extract:
        result = coverage_stage.run(_context(tmp, "tex"), _tex_state())

    extract.assert_not_called()
    assert len(result["unsplit_statement_dict"]) == 2
    report = result["ensure_coverage_report"]
    assert report["mode"] == "tex_hybrid_audit"
    assert report["candidate_count"] == 2
    assert report["missing_count"] == 0
    assert not any(
        wrapper["pos1"].get("coverage_recovered")
        for wrapper in result["unsplit_statement_dict"].values()
    )


def test_tex_missing_deterministic_block_reports_and_raises():
    state = _tex_state()
    state["unsplit_statement_dict"].pop(1)
    with tempfile.TemporaryDirectory() as tmp, patch.object(
        coverage_stage, "_run_extract_tasks", return_value={}
    ) as extract:
        try:
            coverage_stage.run(_context(tmp, "tex"), state)
        except RuntimeError as exc:
            assert "extract_statements" in str(exc)
            assert "['1']" in str(exc)
        else:
            raise AssertionError("Expected deterministic TeX coverage audit to fail")

        extract.assert_not_called()
        report = json.loads(Path(tmp, "ensure_coverage_report.json").read_text(encoding="utf-8"))
        assert report["failed_count"] == 1
        assert report["candidates"][1]["status"] == "missing_deterministic_tex_block"
        assert Path(tmp, "unsplit_statement_dict_after_coverage.json").exists()


if __name__ == "__main__":
    test_coverage_correction_prompt_repeats_exact_source_context()
    test_coverage_correction_flow_can_quote_the_repeated_source()
    test_global_optimization_quote_is_accepted_but_prompt_echo_is_rejected()
    test_structural_missing_item_is_recovered_once()
    test_covered_blocks_use_source_identity_even_when_label_is_empty()
    test_markdown_heading_candidate_stops_at_next_arbitrary_heading()
    test_shared_heading_recognizer_covers_languages_exercise_and_unnumbered()
    test_ordinary_prose_reference_is_not_a_candidate()
    test_complete_node_fields_are_ignored_when_quotes_are_valid()
    test_invalid_quote_is_source_only_and_retryable()
    test_missing_quote_is_rejected()
    test_same_source_punctuation_and_spacing_variant_is_suppressed()
    test_failed_recovery_is_reported_with_source_only_node()
    test_tex_is_audit_only_and_never_calls_llm_for_references()
    test_tex_missing_deterministic_block_reports_and_raises()
    print("ensure coverage stage tests passed")
