import sys
import tempfile
import json
import io
import os
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from JoinAgent.LLM_API.llm import SimpleLLM
from JoinAgent.LLM_Parser.llm_parser import LLMParser
from JoinAgent.Multi_Process.multi_process import MultiProcessor
from pipeline.main_agent.extract_statements_repair import ExtractStatementsRepair
from pipeline.common.llm_task import run_multiprocess_task
from pipeline.stages.correct_text.stage import (
    build_structure_preserving_units,
    run as run_correct_text,
    validate_correction_candidate,
)
from pipeline.stages.segment_blocks.stage import (
    assemble_problem_blocks,
    build_boundary_evidence,
    clean_markdown_units,
    classify_boundary_roles,
    extract_problem,
    flatten_units,
    parse_markdown_heading,
    run as run_segment_blocks,
)
from pipeline.stages.segment_blocks import stage as segment_blocks_stage
from pipeline.stages.extract_statements.stage import (
    recognize_source_label,
    reconcile_labels_from_source,
)
from pipeline.stages.clean_nodes.stage import (
    apply_cleaning_decisions,
    build_cleaning_input_dict,
)
from pipeline.stages.clean_nodes.templates import validation_clean_nodes
from pipeline.common.tex import build_tex_stage_outputs
from pipeline.common.node import normalize_node_type, normalize_node_types_in_tree
from pipeline.stages.extract_references import stage as extract_references_stage
from pipeline.stages.build_relations.stage import create_entity_pairs, extract_explicit_relations


def test_simple_llm_uses_explicit_proxy_without_inheriting_system_proxy():
    proxy_environment = {
        "HTTP_PROXY": "http://system-proxy.test:8080",
        "HTTPS_PROXY": "http://system-proxy.test:8080",
        "ALL_PROXY": "socks5://system-proxy.test:1080",
        "PDFPIPELINE_LLM_PROXY": "http://chat-proxy.test:8080",
        "PDFPIPELINE_AUTO_LOCAL_PROXY": "0",
    }
    with patch.dict(os.environ, proxy_environment, clear=True):
        llm = SimpleLLM(model="test", api_url="https://example.test/v1", api_key="key")

    assert llm.session.trust_env is False
    assert llm.session.proxies == {
        "http": "http://chat-proxy.test:8080",
        "https": "http://chat-proxy.test:8080",
    }
    assert llm.proxy == llm.session.proxies

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    llm.session.post = Mock(return_value=response)

    assert llm.ask("hello") == "ok"
    llm.session.post.assert_called_once()
    assert llm.session.post.call_args.kwargs["json"]["temperature"] == 1


def test_simple_llm_auto_detects_local_proxy():
    with patch.dict(os.environ, {"PDFPIPELINE_AUTO_LOCAL_PROXY": "1"}, clear=True), patch(
        "JoinAgent.LLM_API.llm._local_port_is_listening", return_value=True
    ):
        llm = SimpleLLM(model="test", api_url="https://example.test/v1", api_key="key")

    assert llm.session.proxies == {
        "http": "http://127.0.0.1:7897",
        "https": "http://127.0.0.1:7897",
    }


def test_simple_llm_can_explicitly_disable_proxy():
    with patch.dict(
        os.environ,
        {"PDFPIPELINE_LLM_PROXY": "direct", "PDFPIPELINE_AUTO_LOCAL_PROXY": "1"},
        clear=True,
    ), patch("JoinAgent.LLM_API.llm._local_port_is_listening", return_value=True):
        llm = SimpleLLM(model="test", api_url="https://example.test/v1", api_key="key")

    assert llm.session.trust_env is False
    assert llm.session.proxies == {}
    assert llm.proxy is None


def test_simple_llm_debug_logging_is_safe_on_windows_gbk_console():
    llm = SimpleLLM(model="test", api_url="https://example.test/v1", api_key="key")
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    llm.session.post = Mock(return_value=response)
    console_bytes = io.BytesIO()
    console = io.TextIOWrapper(console_bytes, encoding="gbk", errors="strict")

    with patch.dict(os.environ, {"LLM_DEBUG": "1"}), redirect_stdout(console):
        assert llm.ask("hello") == "ok"
    console.flush()
    assert "LLM ok" in console_bytes.getvalue().decode("gbk")


def test_segment_blocks_resolves_string_cut_mark_keys():
    chopped = {
        "0": {
            "pos1": {
                "0": 'r"""Theorem 1."""',
                "1": 'r"""Proof 1."""',
                "2": 'r"""Theorem 2."""',
                "3": 'r"""Proof 2."""',
            }
        }
    }

    problem_dict, mapping_dict = extract_problem(chopped, {"0": [1, 3]})

    assert list(problem_dict) == [0, 1]
    assert problem_dict[0]["pos1"] == "Theorem 1.Proof 1."
    assert problem_dict[1]["pos1"] == "Theorem 2.Proof 2."
    assert list(mapping_dict["0"]) == [0, 1]


def test_clean_nodes_quarantines_vacuous_above_assertion_and_keeps_concrete_exercise():
    statement_dict = {
        "55": {
            "pos1": {
                "node_type": "exercise",
                "content": "Prove the above assertion.",
                "proof": "",
                "label": "e1.1.17",
            },
            "_orig_key": 55,
            "source_text": "\\begin{exercise}{}{e1.1.17}\nProve the above assertion.\n\\end{exercise}",
        },
        "56": {
            "pos1": {
                "node_type": "exercise",
                "content": "Prove that every contraction mapping has a unique fixed point.",
                "proof": "",
                "label": "e1.1.18",
            },
            "_orig_key": 56,
            "source_text": "\\begin{exercise}{}{e1.1.18}\nProve that every contraction mapping has a unique fixed point.\n\\end{exercise}",
        },
    }
    input_dict = build_cleaning_input_dict(statement_dict, chunk_size=2)
    assert list(input_dict) == ["0"]
    assert input_dict["0"]["pos1"]["nodes"][0]["key"] == "55"

    decisions = {
        "0": {
            "55": {
                "action": "quarantine",
                "reason": "Only refers to an unspecified above assertion.",
                "confidence": "high",
                "evidence": ["deictic content"],
            },
            "56": {
                "action": "keep",
                "reason": "Concrete mathematical exercise.",
                "confidence": "high",
                "evidence": ["explicit prove that task"],
            },
        }
    }
    assert validation_clean_nodes(decisions["0"]) is True

    cleaned, quarantine, report = apply_cleaning_decisions(statement_dict, decisions, input_dict)

    assert "55" not in cleaned
    assert "56" in cleaned
    assert "55" in quarantine
    assert report["quarantined_node_count"] == 1
    assert report["keep_count"] == 1


def test_clean_nodes_retains_missing_or_invalid_llm_decisions_as_manual_review():
    statement_dict = {
        "0": {
            "pos1": {"node_type": "exercise", "content": "Show the above result.", "proof": "", "label": ""},
            "source_text": "Show the above result.",
        }
    }
    input_dict = build_cleaning_input_dict(statement_dict, chunk_size=1)

    cleaned, quarantine, report = apply_cleaning_decisions(statement_dict, {}, input_dict)

    assert "0" in cleaned
    assert quarantine == {}
    assert report["manual_review_count"] == 1
    assert report["invalid_chunk_count"] == 1


def test_segment_blocks_sorts_json_string_indices_numerically():
    chopped = {
        "0": {
            "pos1": {
                "0": 'r"""zero-"""',
                "1": 'r"""one-"""',
                "2": 'r"""two-"""',
                "10": 'r"""ten-"""',
                "11": 'r"""eleven"""',
            }
        }
    }

    problem_dict, _ = extract_problem(chopped, {"0": [11]})

    assert problem_dict[0]["pos1"] == "zero-one-two-ten-eleven"


def test_segment_blocks_evidence_supports_diverse_labels_without_forcing_boundaries():
    chopped = {
        "0": {
            "pos1": {
                "0": 'r"""Theorem A. First statement.\n\n"""',
                "1": 'r"""By Theorem 2.4, this follows.\n\n"""',
                "2": 'r"""Lemma IV. Second statement.\n\n"""',
                "3": 'r"""T1 Custom statement.\n\n"""',
                "4": 'r"""Definition. Unnumbered statement.\n\n"""',
                "5": 'r"""定理3.2 中文命题。\n\n"""',
            }
        }
    }

    packet = build_boundary_evidence(flatten_units(chopped))

    assert packet[0]["logical_unit_type_hint"].lower() == "theorem"
    assert "unit_initial_logical_type" in packet[2]["rule_evidence"]
    assert packet[3]["label_family_hint"] == "custom_or_symbolic"
    assert packet[4]["logical_unit_type_hint"].lower() == "definition"
    assert packet[5]["label_surface_hint"] == "定理3.2"
    assert all("role" not in item for item in packet)


def test_segment_blocks_parses_markdown_logical_heading_and_section_heading():
    logical = parse_markdown_heading("# Theorem 1.1.2. (Algebraic properties)")
    section = parse_markdown_heading("# 1.1 Metric spaces")

    assert logical["kind"] == "logical"
    assert logical["label"] == "Theorem 1.1.2"
    assert logical["logical_type"].lower() == "theorem"
    assert section["kind"] == "section"


def test_segment_blocks_cleanup_removes_images_and_navigation_lines():
    units = [
        {
            "unit_id": "0",
            "source_batch_key": "0",
            "source_unit_id": "0",
            "text": "# Introduction h Metric spaces h Stokes theorem h Differential forms\n"
            "Statement ![](D:/bad/image.png) remains.\n",
        }
    ]

    cleaned, report = clean_markdown_units(units)

    assert len(cleaned) == 1
    assert "![](" not in cleaned[0]["text"]
    assert "Introduction h Metric" not in cleaned[0]["text"]
    assert {item["reason"] for item in report} >= {"removed_markdown_image", "removed_navigation_or_header_line"}


def test_segment_blocks_fallback_starts_blocks_for_markdown_logical_headings_and_skips_sections():
    chopped = {
        "0": {
            "pos1": {
                "0": 'r"""# 1.1 Metric spaces\n\n"""',
                "1": 'r"""# Theorem 1.1.1\n\nStatement.\n\n"""',
                "2": 'r"""Proof. Done.\n\n"""',
                "3": 'r"""# Exercise 1.1.1\n\nProblem.\n\n"""',
            }
        }
    }
    packet = build_boundary_evidence(flatten_units(chopped))
    classifications = {
        "units": {
            item["unit_id"]: {
                "role": "top_level_logical_unit_start"
                if "markdown_logical_heading" in item["rule_evidence"]
                else ("section_context" if "markdown_section_heading" in item["rule_evidence"] else "proof_start_or_continuation"),
                "label_surface": item["label_surface_hint"],
                "label_family": item["label_family_hint"],
                "logical_unit_type_hint": item["logical_unit_type_hint"],
                "evidence": item["rule_evidence"],
                "reason": "test",
            }
            for item in packet
        },
        "warnings": [],
    }

    problem_dict, _, report = assemble_problem_blocks(flatten_units(chopped), classifications)

    assert len(problem_dict) == 2
    assert list(problem_dict) == [0, 1]
    assert "# 1.1 Metric spaces" not in problem_dict[0]["pos1"]
    assert "Proof. Done." in problem_dict[0]["pos1"]
    assert report["blocks"][0]["block_quality_flags"] == ["section_context_block"]


def test_segment_blocks_does_not_attach_definition_context_to_exercise():
    chopped = {
        "0": {
            "pos1": {
                "0": 'r"""# Exercise 1.1.1\n\nShow the claim.\n\n"""',
                "1": 'r"""A distance-preserving bijection is called an isometry.\n\n"""',
                "2": 'r"""# Theorem 1.1.1\n\nStatement.\n\n"""',
            }
        }
    }
    units = flatten_units(chopped)
    packet = build_boundary_evidence(units)
    classifications = {
        "units": {
            item["unit_id"]: {
                "role": "top_level_logical_unit_start"
                if "markdown_logical_heading" in item["rule_evidence"]
                else "ordinary_continuation",
                "label_surface": item["label_surface_hint"],
                "label_family": item["label_family_hint"],
                "logical_unit_type_hint": item["logical_unit_type_hint"],
                "evidence": item["rule_evidence"],
                "reason": "test",
            }
            for item in packet
        },
        "warnings": [],
    }

    problem_dict, _, report = assemble_problem_blocks(units, classifications)

    assert len(problem_dict) == 2
    assert "isometry" not in problem_dict[0]["pos1"]
    assert "# Theorem 1.1.1" in problem_dict[1]["pos1"]
    assert any("context_after_exercise" in block["evidence"] for block in report["blocks"])


def test_segment_blocks_assembly_keeps_proof_and_subparts_with_parent():
    chopped = {
        "0": {
            "pos1": {
                "0": 'r"""Theorem A. First statement.\n\n"""',
                "1": 'r"""(a) First part.\n\n"""',
                "2": 'r"""Proof. Complete proof.\n\n"""',
                "3": 'r"""By Theorem A, an explanatory reference follows.\n\n"""',
                "4": 'r"""无编号但独立的命题。\n\n"""',
            }
        }
    }
    units = flatten_units(chopped)
    roles = [
        "top_level_logical_unit_start",
        "subpart_or_item",
        "proof_start_or_continuation",
        "reference_only",
        "top_level_logical_unit_start",
    ]
    classification = {
        "units": {
            unit["unit_id"]: {
                "role": role,
                "label_surface": "",
                "label_family": "",
                "logical_unit_type_hint": "",
                "evidence": [],
                "reason": "",
            }
            for unit, role in zip(units, roles)
        },
        "warnings": [],
    }

    problem_dict, _, report = assemble_problem_blocks(units, classification)

    assert len(problem_dict) == 2
    assert "Proof. Complete proof." in problem_dict[0]["pos1"]
    assert "By Theorem A" in problem_dict[0]["pos1"]
    assert problem_dict[1]["pos1"].startswith("无编号")
    assert report["all_units_consumed_once"] is True
    assert len(report["unit_assignments"]) == len(units)


class _BoundaryRoleLLM:
    def ask(self, prompt, temperature=0.2):
        marker = "Ordered units and rule-generated evidence:\n"
        packet = json.loads(prompt.split(marker, 1)[1])
        roles = {
            "0": "top_level_logical_unit_start",
            "1": "proof_start_or_continuation",
            "2": "top_level_logical_unit_start",
        }
        return json.dumps(
            {
                "units": {
                    item["unit_id"]: {
                        "role": roles[item["unit_id"]],
                        "label_surface": item["label_surface_hint"],
                        "label_family": item["label_family_hint"],
                        "logical_unit_type_hint": item["logical_unit_type_hint"],
                        "evidence": [],
                        "reason": "test classification",
                    }
                    for item in packet
                },
                "warnings": [],
            },
            ensure_ascii=False,
        )


def test_segment_blocks_stage_writes_problem_dict_and_audit_report():
    chopped = {
        "0": {
            "pos1": {
                "0": 'r"""Theorem A. Statement.\n\n"""',
                "1": 'r"""Proof. Done.\n\n"""',
                "2": 'r"""Claim. Another statement.\n\n"""',
            }
        }
    }
    with tempfile.TemporaryDirectory() as tmp:
        context = SimpleNamespace(output_dir=tmp, llm=_BoundaryRoleLLM(), parser=LLMParser())
        state = run_segment_blocks(context, {"chopped_text_dict": chopped})

        assert len(state["problem_dict"]) == 2
        assert "Proof. Done." in state["problem_dict"][0]["pos1"]
        report = json.loads((Path(tmp) / "segment_blocks_report.json").read_text(encoding="utf-8"))
        assert report["all_units_consumed_once"] is True
        assert report["blocks"][0]["label_surface"] == "Theorem A"


def _classification_for(packet, roles):
    return {
        "units": {
            item["unit_id"]: {
                "role": roles.get(item["unit_id"], "ordinary_continuation"),
                "label_surface": item["label_surface_hint"],
                "label_family": item["label_family_hint"],
                "logical_unit_type_hint": item["logical_unit_type_hint"],
                "evidence": [],
                "reason": "test classification",
            }
            for item in packet
        },
        "warnings": [],
    }


def test_segment_blocks_parallel_chunk_merge_keeps_core_only_and_order():
    chopped = {
        "0": {
            "pos1": {
                "0": 'r"""Theorem A. Statement.\n\n"""',
                "1": 'r"""Proof. Done.\n\n"""',
                "2": 'r"""Claim. Another statement.\n\n"""',
            }
        }
    }
    packet = build_boundary_evidence(flatten_units(chopped))
    chunks = [
        {"context_start": 0, "core_start": 0, "end": 1, "items": packet[0:2]},
        {"context_start": 0, "core_start": 1, "end": 3, "items": packet[0:3]},
    ]
    result_by_key = {
        "0": _classification_for(
            packet[0:2],
            {"0": "top_level_logical_unit_start", "1": "ordinary_continuation"},
        ),
        "1": _classification_for(
            packet[0:3],
            {
                "0": "ordinary_continuation",
                "1": "proof_start_or_continuation",
                "2": "top_level_logical_unit_start",
            },
        ),
    }

    with patch.object(segment_blocks_stage, "_chunk_unit_packet", return_value=chunks):
        with patch.object(segment_blocks_stage, "_run_boundary_chunk_tasks", return_value=result_by_key) as runner:
            classified, answers, errors = classify_boundary_roles(
                Mock(),
                LLMParser(),
                packet,
                num_threads=8,
                checkpoint=3,
                checkpoint_dir="checkpoint-test",
            )

    runner.assert_called_once()
    assert classified["units"]["0"]["role"] == "top_level_logical_unit_start"
    assert classified["units"]["1"]["role"] == "proof_start_or_continuation"
    assert classified["units"]["2"]["role"] == "top_level_logical_unit_start"
    assert len(answers) == 2
    assert errors == []


def test_segment_blocks_parallel_chunk_missing_result_falls_back_locally():
    chopped = {
        "0": {
            "pos1": {
                "0": 'r"""Theorem A. Statement.\n\n"""',
                "1": 'r"""Proof. Done.\n\n"""',
                "2": 'r"""Claim. Another statement.\n\n"""',
            }
        }
    }
    packet = build_boundary_evidence(flatten_units(chopped))
    chunks = [
        {"context_start": 0, "core_start": 0, "end": 1, "items": packet[0:1]},
        {"context_start": 1, "core_start": 1, "end": 3, "items": packet[1:3]},
    ]
    result_by_key = {
        "0": _classification_for(packet[0:1], {"0": "top_level_logical_unit_start"}),
    }

    with patch.object(segment_blocks_stage, "_chunk_unit_packet", return_value=chunks):
        with patch.object(segment_blocks_stage, "_run_boundary_chunk_tasks", return_value=result_by_key):
            classified, answers, errors = classify_boundary_roles(
                Mock(),
                LLMParser(),
                packet,
                num_threads=4,
                checkpoint=3,
                checkpoint_dir="checkpoint-test",
            )

    assert classified["units"]["0"]["role"] == "top_level_logical_unit_start"
    assert classified["units"]["1"]["role"] == "proof_start_or_continuation"
    assert classified["units"]["2"]["role"] == "top_level_logical_unit_start"
    assert len(answers) == 1
    assert errors == ["classification_task_missing_or_coverage_invalid"]


def test_multiprocess_correction_prompt_receives_task_context():
    with tempfile.TemporaryDirectory() as tmp:
        processor = MultiProcessor(
            llm=Mock(),
            parse_method=lambda value: value,
            data_template="{}",
            prompt_template="{unit_packet}",
            correction_template="source={unit_packet}; answer={answer}; schema={data_template}",
            validator=lambda value: True,
            checkpoint_dir=tmp,
        )

        prompt = processor.generate_correction_prompt("bad-json", unit_packet="ORIGINAL_UNITS")

    assert "source=ORIGINAL_UNITS" in prompt
    assert "answer=bad-json" in prompt


def test_multiprocess_retries_transient_proxy_errors_with_backoff():
    llm = Mock()
    llm.ask.side_effect = [
        RuntimeError("ProxyError: RemoteDisconnected without response"),
        ConnectionResetError(10054, "connection reset by peer"),
        {"ok": True},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        processor = MultiProcessor(
            llm=llm,
            parse_method=lambda value: value,
            data_template="{}",
            prompt_template="{payload}",
            correction_template="{answer}",
            validator=lambda value: value == {"ok": True},
            checkpoint_dir=tmp,
        )

        with patch("JoinAgent.Multi_Process.multi_process.time.sleep") as sleep:
            result = processor.process_task("0", {"payload": "demo"}, False)

    assert result == {"ok": True}
    assert llm.ask.call_count == 3
    assert sleep.call_count == 2


def test_extract_statements_recognizes_general_source_labels_only_at_block_start():
    cases = {
        "# Theorem 1.1.2. (Algebraic properties)\n\nStatement.": "Theorem 1.1.2",
        "(1.2) Statement.": "(1.2)",
        "(A.3) Statement.": "(A.3)",
        "2.4.1 Statement.": "2.4.1",
        "Theorem 2.4. Statement.": "Theorem 2.4",
        "Lemma IV. Statement.": "Lemma IV",
        "定理3.2 命题。": "定理3.2",
        "T1 Custom statement.": "T1",
        "\\tag{A} Custom statement.": "\\tag{A}",
        "(a) Internal subpart.": "",
        "By Theorem 2.4, the result follows.": "",
        "Unlabeled proposition.": "",
    }
    for source, expected in cases.items():
        assert recognize_source_label(source)["label"] == expected


def test_extract_statements_reconciles_labels_without_changing_other_fields():
    problems = {
        "0": {"pos1": "(1.2) Parent statement."},
        "1": {"pos1": "Theorem A. Named statement."},
        "2": {"pos1": "Unlabeled statement. By Lemma 3.1, done."},
    }
    statements = {
        0: {"pos1": {"node_type": "定理", "content": "Parent statement.", "proof": "Proof.", "label": ""}, "_orig_key": "0"},
        1: {"pos1": {"node_type": "定理", "content": "Named statement.", "proof": "", "label": "Lemma 9"}, "_orig_key": "1"},
        2: {"pos1": {"node_type": "定理", "content": "Unlabeled statement.", "proof": "", "label": "Lemma 3.1"}, "_orig_key": "2"},
    }

    reconciled, report = reconcile_labels_from_source(statements, problems)

    assert reconciled[0]["pos1"]["label"] == "(1.2)"
    assert reconciled[1]["pos1"]["label"] == "Theorem A"
    assert reconciled[2]["pos1"]["label"] == ""
    assert reconciled[0]["pos1"]["content"] == statements[0]["pos1"]["content"]
    assert reconciled[0]["pos1"]["proof"] == statements[0]["pos1"]["proof"]
    assert report["label_preservation_rate"] == 1.0
    assert report["filled_label_count"] == 1
    assert report["label_conflict_count"] == 1

    custom = {
        0: {
            "pos1": {"node_type": "定理", "content": "Custom statement.", "proof": "", "label": "[custom-x]"},
            "_orig_key": "0",
        }
    }
    custom_result, _ = reconcile_labels_from_source(custom, {"0": {"pos1": "[custom-x] Custom statement."}})
    assert custom_result[0]["pos1"]["label"] == "[custom-x]"


def test_extract_statements_reconciles_markdown_heading_label_and_type():
    problems = {"0": {"pos1": "# Theorem 1.1.2. (Algebraic properties)\n\nStatement."}}
    statements = {
        0: {
            "pos1": {"node_type": "exercise", "content": "Statement.", "proof": "", "label": ""},
            "_orig_key": "0",
        }
    }
    segment_report = {
        "blocks": [
            {
                "block_id": 0,
                "label_surface": "Theorem 1.1.2",
                "label_family": "markdown_heading",
                "logical_unit_type_hint": "Theorem",
                "evidence": ["markdown_logical_heading"],
            }
        ]
    }

    reconciled, report = reconcile_labels_from_source(statements, problems, segment_report)

    assert reconciled[0]["pos1"]["label"] == "Theorem 1.1.2"
    normalize_node_types_in_tree(reconciled)
    assert reconciled[0]["pos1"]["node_type"] == "theorem"
    assert report["filled_label_count"] == 1
    assert report["heading_type_override_count"] == 1


def test_node_type_normalization_lowercases_ascii_and_preserves_chinese():
    tree = {
        "0": {
            "pos1": {
                "node_type": "Lemma",
                "sub_nodes": [
                    {"node_type": "Theorem"},
                    {"node_type": "定理"},
                ],
            }
        }
    }

    normalize_node_types_in_tree(tree)

    assert normalize_node_type(" Definition ") == "definition"
    assert normalize_node_type("引理") == "引理"
    assert tree["0"]["pos1"]["node_type"] == "lemma"
    assert tree["0"]["pos1"]["sub_nodes"][0]["node_type"] == "theorem"
    assert tree["0"]["pos1"]["sub_nodes"][1]["node_type"] == "定理"


def test_tex_extracts_default_exercise_environment():
    source = r"""
\begin{exercise}
Show that every open ball is open.
\end{exercise}
"""

    _, unsplit, report, _ = build_tex_stage_outputs(source, source_file="sample.tex")

    assert len(unsplit) == 1
    node = unsplit[0]["pos1"]
    assert node["node_type"] == "exercise"
    assert "open ball" in node["content"]
    assert report["theorem_envs"]["exercise"] == "exercise"


def test_tex_discovers_common_custom_theorem_environments():
    source = r"""
\newtheorem{mythm}{Theorem}
\declaretheorem[name=Lemma]{smartlemma}
\newtcbtheorem{bluebox}{Proposition}{colback=blue!5}{prop}
\newmdtheoremenv{warning}{Remark}
\newenvironment{exercisebox}{\begin{exercise}}{\end{exercise}}

\begin{mythm}\label{thm:custom}Custom theorem.\end{mythm}
\begin{smartlemma}\label{lem:smart}Custom lemma.\end{smartlemma}
\begin{bluebox}{Boxed title}{prop:box}Custom proposition.\end{bluebox}
\begin{warning}\label{rem:warning}Custom remark.\end{warning}
\begin{exercisebox}Custom exercise.\end{exercisebox}
"""

    _, unsplit, report, _ = build_tex_stage_outputs(source, source_file="custom.tex")
    nodes = [entry["pos1"] for entry in unsplit.values()]

    assert [node["node_type"] for node in nodes] == ["theorem", "lemma", "proposition", "remark", "exercise"]
    assert [node["label"] for node in nodes[:4]] == ["thm:custom", "lem:smart", "prop:box", "rem:warning"]
    assert nodes[4]["label"] == "Exercise 1"
    assert nodes[4]["tex_label_key"] == ""
    assert report["fallback_counter_label_count"] == 1
    assert report["theorem_envs"]["mythm"] == "theorem"
    assert report["theorem_envs"]["smartlemma"] == "lemma"
    assert report["theorem_envs"]["bluebox"] == "proposition"
    assert report["theorem_envs"]["exercisebox"] == "exercise"


def test_extract_statements_does_not_copy_parent_label_to_ambiguous_multiple_nodes():
    problems = {"0": {"pos1": "(1.2) Parent statement with two extracted fragments."}}
    statements = {
        0: {"pos1": {"node_type": "定理", "content": "First unrelated fragment.", "proof": "", "label": ""}, "_orig_key": "0"},
        1: {"pos1": {"node_type": "定理", "content": "Second unrelated fragment.", "proof": "", "label": ""}, "_orig_key": "0"},
    }

    reconciled, report = reconcile_labels_from_source(statements, problems)

    assert reconciled == statements
    assert report["ambiguous_parent_label_count"] == 1
    assert report["missing_trusted_label_count"] == 1


def test_repair_candidate_deduplicates_same_label_using_more_complete_node():
    with tempfile.TemporaryDirectory() as tmp:
        repair = ExtractStatementsRepair(SimpleNamespace(output_dir=tmp))
        candidate = repair._normalize_candidate(
            {
                "context_1": {
                    "0": {"node_type": "theorem", "content": "Statement.", "proof": "", "label": "(1.7)"}
                },
                "context_2": {
                    "0": {
                        "node_type": "theorem",
                        "content": "Statement.",
                        "proof": "Complete proof.",
                        "label": "(1.7)",
                    }
                },
            },
            "7",
        )

        assert len(candidate) == 1
        assert candidate[0]["pos1"]["proof"] == "Complete proof."


def test_correct_text_units_preserve_math_labels_references_and_display_math():
    source = (
        "# Heading\n\n"
        "(1.3) Let $G$ be finite. By Theorem 6.32, the claim follows.\n\n"
        "$$\na.b=c\n$$\n\n"
        "Proof. Done."
    )

    units = build_structure_preserving_units(source, max_chars=70)

    assert "".join(units.values()) == source
    assert any("(1.3)" in value for value in units.values())
    assert any("Theorem 6.32" in value for value in units.values())
    assert any(value.startswith("$$") and value.rstrip().endswith("$$") for value in units.values())
    assert all("(1." not in value or "(1.3)" in value for value in units.values())


def test_correct_text_validation_accepts_semantic_rewrites():
    target = {
        "0": "(1.3) Let $\\chi$ be a character.",
        "1": "Proof. Theorem 6.32 applies.",
    }
    candidate = {
        "corrected_units": {
            "0": "Renumbered text with $chi$ and no original LaTeX command.",
            "1": "A fully rewritten paragraph without the original reference.",
        },
        "warnings": [],
    }

    accepted, rejected, batch_issues = validate_correction_candidate(target, candidate)

    assert not batch_issues
    assert accepted == candidate["corrected_units"]
    assert rejected == {}


def test_correct_text_validation_rejects_invalid_structure():
    target = {"0": "Alpha.", "1": "Beta."}

    invalid_candidates = [
        {},
        {"corrected_units": {}, "warnings": []},
        {"corrected_units": {"0": "Alpha."}, "warnings": []},
        {"corrected_units": {"1": "Beta.", "0": "Alpha."}, "warnings": []},
        {"corrected_units": {"0": "Alpha.", "1": None}, "warnings": []},
        {"corrected_units": {"0": "Alpha.", "1": "   "}, "warnings": []},
        {"corrected_units": {"0": "Alpha.", "1": "Beta."}, "warnings": {}},
    ]

    for candidate in invalid_candidates:
        accepted, rejected, batch_issues = validate_correction_candidate(target, candidate)
        assert accepted == {}
        assert rejected == {}
        assert batch_issues in (["invalid_response_schema"], ["unit_id_mismatch"])


class _EchoCorrectionLLM:
    def ask(self, prompt, temperature=0.2):
        marker = "Original target units:\n" if "Original target units:\n" in prompt else "Target units:\n"
        payload = prompt.split(marker, 1)[1].split("\n\nNext context:", 1)[0]
        target_units = json.loads(payload)
        return json.dumps({"corrected_units": target_units, "warnings": []}, ensure_ascii=False)


def test_correct_text_stage_writes_compatible_units_and_report():
    source = (
        "# Heading\n\n"
        "(1.3) Let $\\chi \\subseteq X$ be a character and "
        "$\\operatorname{Hom}(A,B)$ be nonempty.\n\n"
        "$$\n"
        "\\begin{aligned}\n"
        "a&=b \\\\\n"
        "c&=d\n"
        "\\end{aligned}\n"
        "$$\n\n"
        "Proof. Done."
    )
    with tempfile.TemporaryDirectory() as tmp:
        source_path = Path(tmp) / "input.md"
        source_path.write_text(source, encoding="utf-8")
        context = SimpleNamespace(
            file_path=str(source_path),
            output_dir=tmp,
            num_threads=2,
            llm=_EchoCorrectionLLM(),
            parser=LLMParser(),
        )

        with patch(
            "pipeline.stages.correct_text.stage.run_multiprocess_task",
            wraps=run_multiprocess_task,
        ) as multiprocess:
            state = run_correct_text(context, {})
        corrected = state["chopped_text_dict"]
        report = state["correct_text_report"]

        assert multiprocess.call_count == 1
        assert multiprocess.call_args.kwargs["num_threads"] == 2
        assert corrected
        assert all(isinstance(wrapper["pos1"], dict) for wrapper in corrected.values())
        reconstructed = "".join(
            value[4:-3]
            for wrapper in corrected.values()
            for value in wrapper["pos1"].values()
        )
        assert reconstructed == source
        assert state["corrected_text"] == source
        assert "@@" not in reconstructed
        assert "\\chi" in reconstructed
        assert "\\subseteq" in reconstructed
        assert "\\operatorname{Hom}" in reconstructed
        assert "\\begin{aligned}\n" in reconstructed
        assert "\n\\end{aligned}" in reconstructed
        assert report["fallback_unit_count"] == 0
        assert report["failed_batch_ids"] == []
        assert "protected_token_preservation_rate" not in report


def test_correct_text_tex_stage_exposes_source_as_corrected_text():
    source = (
        "\\newtheorem{theorem}{Theorem}\n"
        "\\begin{theorem}\\label{thm:one}\n"
        "If $A$, then $B$.\n"
        "\\end{theorem}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        source_path = Path(tmp) / "input.tex"
        source_path.write_text(source, encoding="utf-8")
        context = SimpleNamespace(
            file_path=str(source_path),
            output_dir=tmp,
            source_format="tex",
        )

        state = run_correct_text(context, {})

        assert state["corrected_text"] == source
        assert state["correct_text_report"]["source_format"] == "tex"
        assert state["chopped_text_dict"]["0"]["pos1"]["0"] == f'r"""{source}"""'


def test_tex_statement_extraction_outputs_standard_unsplit_shape():
    source = r"""
\newtheorem{theorem}{Theorem}
\newtheorem{lemma}{Lemma}
\begin{theorem}[Main result]\label{thm:main}
If $A$, then $B$.
\end{theorem}
\begin{proof}
Use \ref{lem:key}.
\end{proof}
\begin{lemma}\label{lem:key}
Assume $A$.
\end{lemma}
\begin{definition}
A widget is good.
\end{definition}
"""

    problem_dict, unsplit, report, model = build_tex_stage_outputs(source, source_file="sample.tex")

    assert len(problem_dict) == 3
    assert unsplit[0]["pos1"]["node_type"] == "theorem"
    assert unsplit[0]["pos1"]["label"] == "thm:main"
    assert "Use \\ref{lem:key}." in unsplit[0]["pos1"]["proof"]
    assert unsplit[0]["pos1"]["title"]["english"] == "Main result"
    assert unsplit[1]["pos1"]["node_type"] == "lemma"
    assert unsplit[2]["pos1"]["node_type"] == "definition"
    assert unsplit[2]["pos1"]["label"] == "Definition 1"
    assert unsplit[2]["pos1"]["tex_label_key"] == ""
    assert report["missing_tex_label_count"] == 1
    assert report["generated_counter_label_count"] == 1
    assert model["records"][0]["env_name"] == "theorem"


def test_tex_references_resolve_to_existing_reference_signals():
    node_list = [
        {
            "global_id": "lemma-node",
            "node_type": "lemma",
            "content": "Assume $A$.",
            "proof": "",
            "label": "lem:key",
            "tex_label_key": "lem:key",
        },
        {
            "global_id": "theorem-node",
            "node_type": "theorem",
            "content": "If $A$, then $B$.",
            "proof": r"Use \ref{lem:key} and \cref{missing:eq}.",
            "label": "thm:main",
            "tex_label_key": "thm:main",
        },
    ]

    context = SimpleNamespace(output_dir=tempfile.mkdtemp())
    state = extract_references_stage.run(context, {"node_list": node_list})

    signals = state["node_list"][1]["reference_signals"]
    assert signals["explicit_targets"][0]["resolved_index"] == 0
    assert signals["explicit_targets"][0]["match_mode"] == "tex_label"
    assert signals["explicit_targets"][1]["label_key"] == "missing:eq"
    assert signals["explicit_targets"][1]["match_mode"] == "unresolved"


def test_explicit_pairs_are_excluded_from_llm_relation_candidates():
    node_list = [
        {
            "global_id": "lemma-node",
            "node_type": "lemma",
            "content": "Assume $A$.",
            "label": "lem:key",
        },
        {
            "global_id": "theorem-node",
            "node_type": "theorem",
            "content": "If $A$, then $B$.",
            "label": "thm:main",
            "reference_signals": {
                "explicit_targets": [{"resolved_index": 0, "surface": r"\ref{lem:key}", "match_mode": "tex_label"}],
                "relative_references": [],
            },
        },
    ]

    explicit_edges, explicit_pairs = extract_explicit_relations(node_list)
    proof_pairs, definition_pairs = create_entity_pairs(
        node_list,
        api_key=None,
        api_url=None,
        embedding_model=None,
        use_keyword_filter=True,
        exclude_pairs=explicit_pairs,
    )

    assert explicit_pairs == {(1, 0)}
    assert len(explicit_edges) == 1
    assert proof_pairs == {}
    assert definition_pairs == {}


if __name__ == "__main__":
    test_simple_llm_ignores_all_proxy_configuration()
    test_simple_llm_debug_logging_is_safe_on_windows_gbk_console()
    test_segment_blocks_resolves_string_cut_mark_keys()
    test_clean_nodes_quarantines_vacuous_above_assertion_and_keeps_concrete_exercise()
    test_clean_nodes_retains_missing_or_invalid_llm_decisions_as_manual_review()
    test_segment_blocks_sorts_json_string_indices_numerically()
    test_segment_blocks_evidence_supports_diverse_labels_without_forcing_boundaries()
    test_segment_blocks_parses_markdown_logical_heading_and_section_heading()
    test_segment_blocks_cleanup_removes_images_and_navigation_lines()
    test_segment_blocks_fallback_starts_blocks_for_markdown_logical_headings_and_skips_sections()
    test_segment_blocks_does_not_attach_definition_context_to_exercise()
    test_segment_blocks_assembly_keeps_proof_and_subparts_with_parent()
    test_segment_blocks_stage_writes_problem_dict_and_audit_report()
    test_segment_blocks_parallel_chunk_merge_keeps_core_only_and_order()
    test_segment_blocks_parallel_chunk_missing_result_falls_back_locally()
    test_multiprocess_correction_prompt_receives_task_context()
    test_multiprocess_retries_transient_proxy_errors_with_backoff()
    test_extract_statements_recognizes_general_source_labels_only_at_block_start()
    test_extract_statements_reconciles_labels_without_changing_other_fields()
    test_extract_statements_reconciles_markdown_heading_label_and_type()
    test_node_type_normalization_lowercases_ascii_and_preserves_chinese()
    test_tex_extracts_default_exercise_environment()
    test_tex_discovers_common_custom_theorem_environments()
    test_extract_statements_does_not_copy_parent_label_to_ambiguous_multiple_nodes()
    test_repair_candidate_deduplicates_same_label_using_more_complete_node()
    test_correct_text_units_preserve_math_labels_references_and_display_math()
    test_correct_text_validation_accepts_semantic_rewrites()
    test_correct_text_validation_rejects_invalid_structure()
    test_correct_text_stage_writes_compatible_units_and_report()
    test_correct_text_tex_stage_exposes_source_as_corrected_text()
    test_tex_statement_extraction_outputs_standard_unsplit_shape()
    test_tex_references_resolve_to_existing_reference_signals()
    test_explicit_pairs_are_excluded_from_llm_relation_candidates()
    print("stage execution default tests passed")
