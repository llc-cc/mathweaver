from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matrix_flow.parser import (
    extract_matrix_candidates_with_diagnostics,
    parse_matrix_flows,
    parse_matrix_owner,
)


def test_strict_transformation_emits_v2_and_preserves_operation_label():
    source = (
        r"\begin{pmatrix}1&2\\3&4\end{pmatrix}"
        r"\xrightarrow{R_2\to R_2-R_1}"
        r"\begin{pmatrix}1&2\\2&2\end{pmatrix}"
    )

    flows = parse_matrix_flows(source, owner={"global_id": "node-1", "source_block_key": "7"})

    assert len(flows) == 1
    assert flows[0]["schema_version"] == 2
    assert flows[0]["role"] == "transformation"
    assert flows[0]["bindings"] == []
    assert flows[0]["edges"][0]["operations"] == [{
        "type": "row_add",
        "target": 2,
        "source": 1,
        "coefficient": "-(1)",
    }]


def test_authored_array_wrapper_and_arrow_spacing_are_strict():
    source = (
        r"\left(\begin{array}{cc|c}1&1&3\\2&-1&0\end{array}\right)"
        r"\xrightarrow { R_2\to -\frac{1}{3}R_2 }"
        r"\left(\begin{array}{cc|c}1&1&3\\0&\frac{1}{3}&2\end{array}\right)"
    )

    flow = parse_matrix_flows(source, owner={"global_id": "node-1"})[0]

    assert flow["source"].get("recovered") is not True
    assert flow["source"].get("recovery_actions") in (None, [])
    assert flow["nodes"][0]["latex"].startswith(r"\left(")
    assert flow["edges"][0]["label"] == r"R_2\to -\frac{1}{3}R_2"

def test_named_matrix_binds_statement_and_proof_references():
    statement = r"定义 $A=\begin{bmatrix}1&0\\0&1\end{bmatrix}$，随后使用 $A$。"
    proof = r"由 A 可知，亦可写成 \(A\)。"

    parsed = parse_matrix_owner(
        {"statement": statement, "proof": proof},
        owner={"global_id": "node-1", "source_block_key": "7"},
    )

    assert len(parsed["flows"]) == 1
    flow = parsed["flows"][0]
    assert flow["role"] == "named_matrix"
    assert len(flow["nodes"]) == 1
    assert flow["edges"] == []
    binding = flow["bindings"][0]
    assert binding["symbol_latex"] == "A"
    assert binding["definition"]["source_excerpt"] == "A"
    assert {(item["field"], item["source_excerpt"], item["context"]) for item in binding["references"]} == {
        ("statement", "$A$", "math"),
        ("proof", "A", "text"),
        ("proof", r"\(A\)", "math"),
    }


def test_redefinition_shadows_only_later_references():
    statement = (
        r"A=\begin{pmatrix}1\end{pmatrix}，先用 A。"
        r"A=\begin{pmatrix}2\end{pmatrix}，再用 A。"
    )

    flows = parse_matrix_owner({"statement": statement, "proof": ""}, owner={"global_id": "node-1"})["flows"]

    assert len(flows) == 2
    assert [[ref["source_excerpt"] for ref in flow["bindings"][0]["references"]] for flow in flows] == [["A"], ["A"]]


def test_defined_state_inside_transformation_carries_binding():
    source = (
        r"A=\begin{pmatrix}1&0\\0&1\end{pmatrix}"
        r"\to\begin{pmatrix}0&1\\1&0\end{pmatrix}，再看 A。"
    )

    flow = parse_matrix_flows(source, owner={"global_id": "node-1"})[0]

    assert flow["role"] == "transformation"
    assert flow["bindings"][0]["state_id"] == flow["nodes"][0]["id"]
    assert [ref["source_excerpt"] for ref in flow["bindings"][0]["references"]] == ["A"]


def test_ocr_array_wrapper_and_blank_pseudo_row_are_recovered_without_changing_latex():
    source = (
        r"A = { \left( \begin{array} { l l } { 1 } & { −2 } \\   \\ { \dfrac{3}{4} } & { 5 } \end{array} \right) }。"
        r"之后使用 A。"
    )

    flow = parse_matrix_flows(source, owner={"global_id": "node-1"}, source_origin="ocr")[0]
    node = flow["nodes"][0]

    assert flow["role"] == "named_matrix"
    assert flow["source"]["kind"] == "ocr"
    assert flow["source"]["recovered"] is True
    assert node["latex"] == source[node["source_span"]["start"]:node["source_span"]["end"]]
    assert node["latex"].startswith(r"{ \left(")
    assert node["cells"] == [["1", "-2"], [r"\frac{3}{4}", "5"]]
    assert "removed_blank_pseudo_rows" in flow["source"]["recovery_actions"]


def test_nested_layout_array_selects_inner_matrix_only():
    source = (
        r"\begin{array}{l}"
        r"D=\left|\begin{array}{cc}1&2\\3&4\end{array}\right|"
        r"\end{array}"
    )

    parsed = parse_matrix_owner({"statement": source, "proof": ""}, owner={"global_id": "node-1"}, source_origin="ocr")

    assert len(parsed["flows"]) == 1
    assert parsed["flows"][0]["nodes"][0]["kind"] == "determinant"
    assert any(item["reason"] == "layout_container_with_inner_matrix" for item in parsed["rejected"])


def test_unicode_ascii_and_stacked_arrows_are_supported():
    arrows = [
        "→",
        "=>",
        r"\xrightarrow { R_2 \to R_2-R_1 }",
        r"\overset{R_2\to R_2-R_1}{\to}",
        r"\stackrel{R_2\to R_2-R_1}{\longrightarrow}",
    ]
    for arrow in arrows:
        source = (
            r"\begin{pmatrix}1&2\\3&4\end{pmatrix}"
            + arrow
            + r"\begin{pmatrix}1&2\\2&2\end{pmatrix}"
        )
        flow = parse_matrix_flows(source, owner={"global_id": "node-1"}, source_origin="ocr")[0]
        assert len(flow["edges"]) == 1


def test_ambiguous_and_unstructured_inputs_do_not_mount():
    inputs = [
        r"A=\binom{1}{2}",
        r"A=(1 2 3 4)",
        r"<table><tr><td>1</td></tr></table>",
        r"![matrix](matrix.png)",
        r"A=\begin{array}{cc}1&2\\3\end{array}",
        r"A=\begin{array}{cc}1&\\3&4\end{array}",
        r"A=\begin{array}{cc}1&2\\3&4",
    ]
    for source in inputs:
        assert parse_matrix_flows(source, owner={"global_id": "node-1"}, source_origin="ocr") == []


def test_natural_language_or_multiple_arrows_do_not_create_transformations():
    matrix = r"\begin{pmatrix}1\end{pmatrix}"
    for gap in ("因此→", "→=>", "="):
        parsed = parse_matrix_owner({"statement": matrix + gap + matrix, "proof": ""}, owner={"global_id": "node-1"})
        assert all(flow["role"] != "transformation" for flow in parsed["flows"])


def test_candidate_spans_are_exact_and_rectangular():
    source = r"前文 A=\left[\begin{array}{cc}1&2\\3&4\end{array}\right] 后文"
    candidates, rejected = extract_matrix_candidates_with_diagnostics(source)

    assert rejected == []
    assert len(candidates) == 1
    candidate = candidates[0]
    assert source[candidate["start"]:candidate["end"]] == candidate["latex"]
    assert all(len(row) == len(candidate["cells"][0]) for row in candidate["cells"])


def test_array_delimiters_and_augmented_divider_are_preserved():
    wrappers = [
        ("(", ")", "matrix"),
        ("[", "]", "matrix"),
        (r"\{", r"\}", "matrix"),
        ("|", "|", "determinant"),
        (r"\Vert", r"\Vert", "determinant"),
    ]
    for left, right, kind in wrappers:
        source = f"A={left}" + r"\begin{array}{cc}1&2\\3&4\end{array}" + right
        flow = parse_matrix_flows(source, owner={"global_id": "node-1"})[0]
        assert flow["nodes"][0]["kind"] == kind
        assert flow["nodes"][0]["latex"] == source[flow["nodes"][0]["source_span"]["start"]:flow["nodes"][0]["source_span"]["end"]]

    augmented = parse_matrix_flows(
        r"A=\left(\begin{array}{cc|c}1&0&2\\0&1&3\end{array}\right)",
        owner={"global_id": "node-1"},
    )[0]["nodes"][0]
    assert augmented["kind"] == "augmented"
    assert augmented["augmented_after_column"] == 2


def test_determinant_definition_symbols_are_not_reinterpreted():
    d_flow = parse_matrix_flows(
        r"D=\begin{vmatrix}1&2\\3&4\end{vmatrix}，使用 D。",
        owner={"global_id": "node-1"},
    )[0]
    det_flow = parse_matrix_flows(
        r"\det(A)=\begin{vmatrix}1&2\\3&4\end{vmatrix}，使用 $\det(A)$。",
        owner={"global_id": "node-1"},
    )[0]

    assert d_flow["bindings"][0]["symbol_latex"] == "D"
    assert det_flow["bindings"][0]["symbol_latex"] == r"\det(A)"
    assert det_flow["bindings"][0]["references"][0]["source_excerpt"] == r"$\det(A)$"
