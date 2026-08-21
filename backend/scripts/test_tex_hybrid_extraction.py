from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.common.tex import build_tex_stage_outputs
from pipeline.common.node import SOURCE_ENVELOPE_KEY
from JoinAgent import LLMParser
from pipeline.stages.ensure_coverage import stage as ensure_coverage_stage
from pipeline.stages.extract_statements import stage as extract_stage
from pipeline.stages.segment_blocks import stage as segment_stage


def _context(tmp, source_path):
    return SimpleNamespace(
        file_path=str(source_path),
        output_dir=tmp,
        source_format="tex",
        llm=None,
        parser=None,
        num_threads=1,
        checkpoint=10,
        execution_mode="pipeline",
    )


def _write_source(tmp, source):
    path = Path(tmp) / "input.tex"
    path.write_text(source, encoding="utf-8")
    return path


def _hybrid_source():
    return (
        "% leading comment changes no offsets\n"
        "\\begin{document}\n"
        "\\newtheorem{theorem}{Theorem}\n"
        "\\begin{theorem}\\label{thm:one}Environment theorem.\\end{theorem}\n"
        "\\begin{proof}Environment proof.\\end{proof}\n"
        "\\par{A widget is called regular exactly when $r(w)=1$.}\n"
        "\\end{document}\n"
    )


def _exact_quote_results(_context, tasks, _checkpoint_dir):
    results = {}
    quote = "A widget is called regular exactly when $r(w)=1$."
    for key, payload in tasks.items():
        source = payload["pos1"]
        results[key] = (
            {
                0: {
                    "node_type": "definition",
                    "source_quote": quote,
                    "label": "",
                }
            }
            if quote in source
            else {}
        )
    return results


def test_comment_mask_preserves_offsets_and_residual_excludes_protected_environments():
    source = (
        "% comment before the document\n"
        "\\begin{document}\n"
        "\\newtheorem{mythm}{Theorem}\n"
        "\\begin{mythm}A deterministic theorem.\\end{mythm}\n"
        "\\begin{proof}A proof.\\end{proof}\n"
        "\\begin{figure}Figure prose must be masked.\\end{figure}\n"
        "\\par{An element is called small when $|x|<1$.}\n"
        "\\end{document}\n"
    )
    problem, unsplit, report, model = build_tex_stage_outputs(source, source_file="sample.tex")

    record = model["records"][0]
    statement_start = source.index("\\begin{mythm}")
    statement_end = source.index("\\end{mythm}") + len("\\end{mythm}")
    assert record["statement_source_span"] == {"start": statement_start, "end": statement_end}
    assert source[statement_start:statement_end] in problem[0]["pos1"]
    assert unsplit[0]["pos1"]["content"] == "A deterministic theorem."

    residual_text = "\n".join(block["raw_tex"] for block in report["residual_blocks"])
    assert "An element is called small" in residual_text
    assert "A deterministic theorem" not in residual_text
    assert "A proof" not in residual_text
    assert "Figure prose" not in residual_text
    assert "% comment" not in residual_text
    for block in report["residual_blocks"]:
        span = block["source_span"]
        assert source[span["start"]:span["end"]] == block["raw_tex"]


def test_tex_counter_labels_follow_shared_and_section_scoped_counters():
    source = r"""
\documentclass{article}
\newtheorem{definition}{定义}[section]
\newtheorem{theorem}[definition]{Theorem}
\newtheorem{lemma}[definition]{Lemma}
\begin{document}
\section{One}
\begin{definition}First definition.\end{definition}
\begin{theorem}\label{thm:key}Labeled theorem.\end{theorem}
\begin{lemma}First lemma.\end{lemma}
\section{Two}
\begin{theorem}Second theorem.\end{theorem}
\end{document}
"""

    _, unsplit, report, model = build_tex_stage_outputs(source, source_file="counter.tex")
    nodes = [entry["pos1"] for entry in unsplit.values()]

    assert [node["label"] for node in nodes] == [
        "定义 1.1",
        "thm:key",
        "Lemma 1.3",
        "Theorem 2.1",
    ]
    assert nodes[1]["tex_label_key"] == "thm:key"
    assert model["records"][1]["tex_counter_number"] == "1.2"
    assert report["missing_tex_label_count"] == 3
    assert report["generated_counter_label_count"] == 3
    assert report["fallback_counter_label_count"] == 0
    assert report["unresolved_numbered_label_count"] == 0

    _, second_unsplit, second_report, second_model = build_tex_stage_outputs(
        source,
        source_file="counter.tex",
    )
    assert second_unsplit == unsplit
    assert second_report == report
    assert second_model == model


def test_book_chapter_and_section_counters_form_the_full_number():
    source = r"""
\documentclass{book}
\newtheorem{theorem}{Theorem}[section]
\begin{document}
\chapter{First}
\section{First section}
\begin{theorem}First theorem.\end{theorem}
\section{Second section}
\begin{theorem}Second theorem.\end{theorem}
\chapter{Second}
\section{First section}
\begin{theorem}Third theorem.\end{theorem}
\end{document}
"""

    _, unsplit, _, model = build_tex_stage_outputs(source, source_file="book.tex")
    nodes = [entry["pos1"] for entry in unsplit.values()]

    assert [node["label"] for node in nodes] == [
        "Theorem 1.1.1",
        "Theorem 1.2.1",
        "Theorem 2.1.1",
    ]
    assert model["records"][0]["tex_counter_within"] == "section"


def test_tex_counter_commands_starred_environments_and_fallbacks():
    source = r"""
\newtheorem{theorem}{Theorem}
\newtheorem*{remark}{Remark}
\numberwithin{theorem}{section}
\section{One}
\setcounter{theorem}{4}
\begin{theorem}Fifth theorem.\end{theorem}
\addtocounter{theorem}{2}
\begin{theorem}Eighth theorem.\end{theorem}
\begin{remark}Unnumbered remark.\end{remark}
\begin{lemma}Fallback lemma one.\end{lemma}
\begin{theorem}Ninth theorem.\end{theorem}
\begin{lemma}Fallback lemma two.\end{lemma}
\section{Two}
\begin{theorem}Reset theorem.\end{theorem}
"""

    _, unsplit, report, model = build_tex_stage_outputs(source, source_file="commands.tex")
    nodes = [entry["pos1"] for entry in unsplit.values()]

    assert [node["label"] for node in nodes] == [
        "Theorem 1.5",
        "Theorem 1.8",
        "",
        "Lemma 1",
        "Theorem 1.9",
        "Lemma 2",
        "Theorem 2.1",
    ]
    assert [record["label_source"] for record in model["records"]] == [
        "tex_counter",
        "tex_counter",
        "unnumbered",
        "tex_counter_fallback",
        "tex_counter",
        "tex_counter_fallback",
        "tex_counter",
    ]
    assert report["unnumbered_environment_count"] == 1
    assert report["fallback_counter_label_count"] == 2
    assert report["unresolved_numbered_label_count"] == 0


def test_common_theorem_declaration_variants_supply_counter_metadata():
    source = r"""
\documentclass{article}
\declaretheorem[name=Theorem,numberwithin=section]{smarttheorem}
\declaretheorem[name=Lemma,sibling=smarttheorem]{smartlemma}
\newtcbtheorem[auto counter,number within=section]{bluebox}{Proposition}{colback=blue!5}{prop}
\newmdtheoremenv{warning}{Remark}
\begin{document}
\section{One}
\begin{smarttheorem}Smart theorem.\end{smarttheorem}
\begin{smartlemma}Shared smart lemma.\end{smartlemma}
\begin{bluebox}{Boxed title}{}Boxed proposition.\end{bluebox}
\begin{warning}Standalone remark.\end{warning}
\end{document}
"""

    _, unsplit, report, model = build_tex_stage_outputs(source, source_file="variants.tex")
    nodes = [entry["pos1"] for entry in unsplit.values()]

    assert [node["label"] for node in nodes] == [
        "Theorem 1.1",
        "Lemma 1.2",
        "Proposition 1.1",
        "Remark 1",
    ]
    assert [record["tex_counter_name"] for record in model["records"]] == [
        "smarttheorem",
        "smarttheorem",
        "bluebox",
        "warning",
    ]
    assert report["generated_counter_label_count"] == 4
    assert report["counter_diagnostic_count"] == 0


def test_counter_approximations_are_reported_without_losing_labels():
    source = r"""
\newtheorem{theorem}{Theorem}
\renewcommand{\thetheorem}{\Alph{theorem}}
\addtocounter{theorem}{\offset}
\begin{theorem}Approximated theorem.\end{theorem}
"""

    _, unsplit, report, _ = build_tex_stage_outputs(source, source_file="approx.tex")

    assert unsplit[0]["pos1"]["label"] == "Theorem 1"
    reasons = {item["reason"] for item in report["counter_diagnostics"]}
    assert "unsupported_counter_expression" in reasons
    assert "custom_counter_format_approximated" in reasons


def test_explicit_tex_paragraphs_are_separate_residual_failure_domains():
    source = (
        "\\begin{document}\n"
        "\\par{A widget is called regular.}\n"
        "\\par{In summary, earlier results remain valid.}\n"
        "\\end{document}\n"
    )
    problem, _, report, _ = build_tex_stage_outputs(source, source_file="sample.tex")
    residual_keys = [
        key
        for key, value in problem.items()
        if value.get("source_kind") == "tex_residual"
    ]
    assert len(residual_keys) == 2
    assert report["residual_block_count"] == 2
    assert "regular" in problem[residual_keys[0]]["pos1"]
    assert "In summary" in problem[residual_keys[1]]["pos1"]


def test_existing_parser_preserves_raw_triple_quoted_latex():
    answer = r'''{
        0: {
            "node_type": "definition",
            "source_quote": r"""A=\begin{pmatrix}
a&b\\
\end{pmatrix}""",
            "label": ""
        }
    }'''
    parsed = LLMParser().parse_dict(answer)
    assert parsed[0]["source_quote"] == (
        "A=\\begin{pmatrix}\n"
        "a&b\\\\\n"
        "\\end{pmatrix}"
    )


def test_residual_llm_runner_delegates_to_multiprocessor_wrapper():
    parser = LLMParser()
    context = SimpleNamespace(
        llm=object(),
        parser=parser,
        num_threads=3,
        checkpoint=7,
        output_dir="unused",
    )
    tasks = {
        "tex_residual:0:20": {
            "pos1": "A widget is called regular.",
            "source_span": {"start": 0, "end": 27},
        }
    }
    with patch.object(
        extract_stage,
        "run_multiprocess_task",
        return_value={"tex_residual:0:20": {}},
    ) as multiprocess:
        result = extract_stage._run_tex_residual_tasks(
            context,
            tasks,
            "checkpoint",
        )
    assert result == {"tex_residual:0:20": {}}
    kwargs = multiprocess.call_args.kwargs
    assert kwargs["parse_method"] == parser.parse_dict
    assert kwargs["index_dict"] is tasks
    assert kwargs["num_threads"] == 3
    assert kwargs["checkpoint"] == 7


def test_residual_quote_validation_accepts_exact_multi_and_empty_results():
    source = "A widget is called regular. Every regular widget has rank one."
    payload = {"pos1": source, "source_span": {"start": 100, "end": 100 + len(source)}}
    envelope, diagnostic = extract_stage._validate_tex_residual_result(
        "tex_residual:100:160",
        payload,
        {
            0: {
                "node_type": "definition",
                "source_quote": "A widget is called regular.",
                "label": "",
            },
            1: {
                "node_type": "theorem",
                "source_quote": "Every regular widget has rank one.",
                "label": "",
            },
        },
    )
    assert diagnostic["status"] == "accepted"
    assert [node["source_span"]["start"] for node in envelope["nodes"]] == [100, 128]

    empty, empty_diagnostic = extract_stage._validate_tex_residual_result(
        "tex_residual:100:160",
        payload,
        {},
    )
    assert empty == {
        "status": "completed",
        "source_block_key": "tex_residual:100:160",
        "nodes": [],
    }
    assert empty_diagnostic["reason"] == "empty_valid_result"


def test_residual_label_may_appear_inside_an_exact_quote():
    source = r"\par{\label{def:widget}A widget is called regular.}"
    payload = {"pos1": source, "source_span": {"start": 20, "end": 20 + len(source)}}
    envelope, diagnostic = extract_stage._validate_tex_residual_result(
        "tex_residual:20:72",
        payload,
        {
            0: {
                "node_type": "definition",
                "source_quote": source,
                "label": r"\label{def:widget}",
            }
        },
    )
    assert diagnostic["status"] == "accepted"
    assert envelope["nodes"][0]["label"] == r"\label{def:widget}"


def test_residual_quote_locator_tolerates_only_surface_formatting_differences():
    source = "\t定义 $A\\times B$ 为集合的“积”，\n记作 $A：B$。"
    payload = {"pos1": source, "source_span": {"start": 50, "end": 50 + len(source)}}
    envelope, diagnostic = extract_stage._validate_tex_residual_result(
        "tex_residual:50:82",
        payload,
        {
            0: {
                "node_type": "definition",
                "source_quote": "\t定义 $A\\times B$ 为集合的'积',\n记作 $A:B$.",
                "label": "",
            }
        },
    )
    node = envelope["nodes"][0]
    assert diagnostic["normalized_surface_anchor_count"] == 1
    assert node["source_quote"] == source.strip()
    assert node["source_span"] == {"start": 51, "end": 50 + len(source)}

    rejected, rejected_diagnostic = extract_stage._validate_tex_residual_result(
        "tex_residual:50:82",
        payload,
        {
            0: {
                "node_type": "definition",
                "source_quote": "\t定义 $A+B$ 为集合的'积',\n记作 $A:B$.",
                "label": "",
            }
        },
    )
    assert rejected is None
    assert rejected_diagnostic["status"] == "rejected_anchor"


def test_nonunique_or_invented_quote_rejects_the_entire_residual_task():
    repeated = "A unit is special. A unit is special."
    payload = {"pos1": repeated, "source_span": {"start": 0, "end": len(repeated)}}
    envelope, diagnostic = extract_stage._validate_tex_residual_result(
        "tex_residual:0:37",
        payload,
        {
            0: {
                "node_type": "definition",
                "source_quote": "A unit is special.",
                "label": "",
            }
        },
    )
    assert envelope is None
    assert diagnostic["reason"] == "source_quote_not_unique_contiguous_substring"

    envelope, diagnostic = extract_stage._validate_tex_residual_result(
        "tex_residual:0:37",
        payload,
        {
            0: {
                "node_type": "definition",
                "source_quote": "An invented definition.",
                "label": "",
            }
        },
    )
    assert envelope is None
    assert diagnostic["status"] == "rejected_anchor"


def test_residual_merge_suppresses_same_span_and_text():
    quote = "A widget is called regular."
    node = {
        "node_type": "definition",
        "source_quote": quote,
        "label": "",
        "source_span": {"start": 10, "end": 10 + len(quote)},
    }
    merged, suppressed = extract_stage._merge_tex_statement_nodes(
        {},
        {
            "tex_residual:0:50": {
                "status": "completed",
                "nodes": [node],
            },
            "tex_residual:0:60": {
                "status": "completed",
                "nodes": [dict(node)],
            },
        },
        "sample.tex",
    )
    assert len(merged) == 1
    assert suppressed == 1


def test_tex_hybrid_extraction_merges_environment_and_exact_residual_node():
    with tempfile.TemporaryDirectory() as tmp:
        source = _hybrid_source()
        source_path = _write_source(tmp, source)
        context = _context(tmp, source_path)
        with patch.object(
            extract_stage,
            "_run_tex_residual_tasks",
            side_effect=_exact_quote_results,
        ):
            state = extract_stage.run(context, {"corrected_text": source})

        nodes = [wrapper["pos1"] for wrapper in state["unsplit_statement_dict"].values()]
        assert [node["node_type"] for node in nodes] == ["theorem", "definition"]
        assert all(SOURCE_ENVELOPE_KEY in node for node in nodes)
        assert nodes[0]["content"] == "Environment theorem."
        assert nodes[1]["content"] == "A widget is called regular exactly when $r(w)=1$."
        span = nodes[1]["source_span"]
        assert source[span["start"]:span["end"]] == nodes[1]["content"]
        assert not any(node.get("coverage_recovered") for node in nodes)
        assert state["extract_statements_report"]["environment_node_count"] == 1
        assert state["extract_statements_report"]["residual_node_count"] == 1

        audited = ensure_coverage_stage.run(context, state)
        audit = audited["ensure_coverage_report"]
        assert audit["mode"] == "tex_hybrid_audit"
        assert audit["environment_covered_count"] == 1
        assert audit["residual_completed_task_count"] == audit["residual_task_count"]
        assert audit["residual_node_count"] == 1


def test_valid_empty_result_completes_without_recovery():
    source = (
        "\\begin{document}\n"
        "\\newtheorem{theorem}{Theorem}\n"
        "\\begin{theorem}Environment theorem.\\end{theorem}\n"
        "\\par{This paragraph only refers to Theorem 1 and contains no new unit.}\n"
        "\\end{document}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        source_path = _write_source(tmp, source)
        context = _context(tmp, source_path)

        def empty_results(_context, tasks, _checkpoint_dir):
            return {key: {} for key in tasks}

        with patch.object(extract_stage, "_run_tex_residual_tasks", side_effect=empty_results):
            state = extract_stage.run(context, {})

        assert len(state["unsplit_statement_dict"]) == 1
        assert state["extract_statements_stage_run"]["status"] == "resolved"
        assert state["extract_statements_report"]["residual_failed_task_count"] == 0
        assert state["extract_statements_report"]["residual_node_count"] == 0


def test_failed_anchor_retries_from_deterministic_base_without_duplicates():
    with tempfile.TemporaryDirectory() as tmp:
        source = _hybrid_source()
        source_path = _write_source(tmp, source)
        context = _context(tmp, source_path)
        calls = []

        def first_invalid_then_valid(_context, tasks, _checkpoint_dir):
            calls.append(list(tasks))
            if len(calls) == 1:
                return {
                    key: {
                        0: {
                            "node_type": "definition",
                            "source_quote": "Invented quote.",
                            "label": "",
                        }
                    }
                    for key in tasks
                }
            return _exact_quote_results(_context, tasks, _checkpoint_dir)

        with patch.object(
            extract_stage,
            "_run_tex_residual_tasks",
            side_effect=first_invalid_then_valid,
        ):
            state = extract_stage.run(context, {})
            assert len(state["unsplit_statement_dict"]) == 1
            assert state["extract_statements_stage_run"]["status"] == "unresolved"
            state, recovery = extract_stage.rerun_failed_tasks(context, state, max_rounds=2)

        assert recovery["status"] == "resolved"
        assert len(state["unsplit_statement_dict"]) == 2
        assert state["extract_statements_report"]["residual_anchor_rejected_count"] == 1
        assert [
            wrapper["pos1"]["node_type"]
            for wrapper in state["unsplit_statement_dict"].values()
        ] == ["theorem", "definition"]


def test_tex_with_only_natural_language_statements_can_produce_nodes():
    source = (
        "\\begin{document}\n"
        "\\par{A widget is called regular exactly when $r(w)=1$.}\n"
        "\\end{document}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        source_path = _write_source(tmp, source)
        context = _context(tmp, source_path)
        segmented = segment_stage.run(
            context,
            {"chopped_text_dict": {"0": {"pos1": {"0": f'r"""{source}"""'}}}},
        )
        assert segmented["problem_dict"]
        assert segmented["segment_blocks_report"]["environment_block_count"] == 0
        assert segmented["segment_blocks_report"]["residual_block_count"] == 1
        assert segmented["segment_blocks_report"]["blocks"][0]["boundary_role"] == "tex_residual_span"

        with patch.object(
            extract_stage,
            "_run_tex_residual_tasks",
            side_effect=_exact_quote_results,
        ):
            state = extract_stage.run(context, segmented)
        assert len(state["unsplit_statement_dict"]) == 1
        assert state["unsplit_statement_dict"][0]["pos1"]["node_type"] == "definition"


def test_generated_counter_labels_flow_through_segment_and_extract_reports():
    source = (
        "\\newtheorem{theorem}{Theorem}\n"
        "\\newtheorem*{remark}{Remark}\n"
        "\\begin{theorem}Environment theorem.\\end{theorem}\n"
        "\\begin{remark}Environment remark.\\end{remark}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        source_path = _write_source(tmp, source)
        context = _context(tmp, source_path)
        segmented = segment_stage.run(context, {})

        environment_blocks = [
            block
            for block in segmented["segment_blocks_report"]["blocks"]
            if block.get("source_kind") == "tex_environment"
        ]
        assert environment_blocks[0]["label_surface"] == "Theorem 1"
        assert environment_blocks[0]["label_family"] == "tex_counter"
        assert environment_blocks[0]["warnings"] == []
        assert environment_blocks[1]["label_surface"] == ""
        assert environment_blocks[1]["warnings"] == []
        assert segmented["segment_blocks_report"]["warnings"] == []

        state = extract_stage.run(context, segmented)
        nodes = [entry["pos1"] for entry in state["unsplit_statement_dict"].values()]
        assert [node["label"] for node in nodes] == ["Theorem 1", ""]
        assert state["extract_statements_report"]["filled_label_count"] == 1
        assert state["extract_statements_report"]["missing_trusted_label_count"] == 0
        assert state["extract_statements_report"]["unnumbered_environment_count"] == 1


if __name__ == "__main__":
    test_comment_mask_preserves_offsets_and_residual_excludes_protected_environments()
    test_tex_counter_labels_follow_shared_and_section_scoped_counters()
    test_book_chapter_and_section_counters_form_the_full_number()
    test_tex_counter_commands_starred_environments_and_fallbacks()
    test_common_theorem_declaration_variants_supply_counter_metadata()
    test_counter_approximations_are_reported_without_losing_labels()
    test_explicit_tex_paragraphs_are_separate_residual_failure_domains()
    test_existing_parser_preserves_raw_triple_quoted_latex()
    test_residual_llm_runner_delegates_to_multiprocessor_wrapper()
    test_residual_quote_validation_accepts_exact_multi_and_empty_results()
    test_residual_label_may_appear_inside_an_exact_quote()
    test_residual_quote_locator_tolerates_only_surface_formatting_differences()
    test_nonunique_or_invented_quote_rejects_the_entire_residual_task()
    test_residual_merge_suppresses_same_span_and_text()
    test_tex_hybrid_extraction_merges_environment_and_exact_residual_node()
    test_valid_empty_result_completes_without_recovery()
    test_failed_anchor_retries_from_deterministic_base_without_duplicates()
    test_tex_with_only_natural_language_statements_can_produce_nodes()
    test_generated_counter_labels_flow_through_segment_and_extract_reports()
    print("TeX hybrid extraction tests passed")
