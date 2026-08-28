from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matrix_flow.parser import parse_matrix_flows
from matrix_flow.verifier import verify_flow


def _flow(source: str) -> dict:
    return parse_matrix_flows(source, owner={"global_id": "node-1"})[0]


def test_numeric_row_operation_is_verified_without_sympy_runtime():
    flow = _flow(
        r"\begin{pmatrix}1&2\\3&4\end{pmatrix}"
        r"\xrightarrow{R_2\to R_2-R_1}"
        r"\begin{pmatrix}1&2\\2&2\end{pmatrix}"
    )

    result = verify_flow(flow)

    assert result["verification"]["status"] == "verified"
    assert result["edges"][0]["verification_status"] == "verified"


def test_authored_array_wrapper_with_spaced_fraction_arrow_is_verified():
    flow = _flow(
        r"\left(\begin{array}{cc|c}1&1&3\\2&-1&0\end{array}\right)"
        r"\xrightarrow { R_2\to -\frac{1}{3}R_2 }"
        r"\left(\begin{array}{cc|c}1&1&3\\-\frac{2}{3}&\frac{1}{3}&0\end{array}\right)"
    )

    result = verify_flow(flow)

    assert result["source"].get("recovered") is not True
    assert result["verification"]["status"] == "verified"
    assert result["edges"][0]["verification_status"] == "verified"

def test_missing_label_is_inferred_only_when_one_elementary_operation_fits():
    flow = _flow(
        r"\begin{pmatrix}1&2\\3&4\end{pmatrix}"
        r"\to"
        r"\begin{pmatrix}3&4\\1&2\end{pmatrix}"
    )

    result = verify_flow(flow)

    assert result["verification"]["status"] == "verified"
    assert result["edges"][0]["provenance"] == "inferred"
    assert result["edges"][0]["operations"] == [{"type": "row_swap", "first": 1, "second": 2}]


def test_wrong_target_is_contradicted():
    flow = _flow(
        r"\begin{pmatrix}1&2\\3&4\end{pmatrix}"
        r"\xrightarrow{R_2\to R_2-R_1}"
        r"\begin{pmatrix}1&2\\9&9\end{pmatrix}"
    )

    assert verify_flow(flow)["verification"]["status"] == "contradicted"


def test_unknown_expression_is_indeterminate_not_verified():
    flow = _flow(
        r"\begin{pmatrix}\sqrt{2}&0\\0&1\end{pmatrix}"
        r"\xrightarrow{R_1\to R_1}"
        r"\begin{pmatrix}\sqrt{2}&0\\0&1\end{pmatrix}"
    )

    result = verify_flow(flow)

    assert result["verification"]["status"] == "indeterminate"
    assert result["edges"][0]["verification_status"] == "indeterminate"


def test_augmented_column_operation_is_not_auto_verified():
    flow = _flow(
        r"\begin{array}{c|c}1&2\\3&4\end{array}"
        r"\xrightarrow{C_1\leftrightarrow C_2}"
        r"\begin{array}{c|c}2&1\\4&3\end{array}"
    )

    result = verify_flow(flow)

    assert result["verification"]["status"] == "indeterminate"


def test_cycle_is_structural_invalid():
    flow = {
        "nodes": [
            {"id": "a", "kind": "matrix", "rows": 1, "columns": 1, "cells": [["1"]]},
            {"id": "b", "kind": "matrix", "rows": 1, "columns": 1, "cells": [["1"]]},
        ],
        "edges": [
            {"id": "e1", "from": "a", "to": "b", "operations": []},
            {"id": "e2", "from": "b", "to": "a", "operations": []},
        ],
    }

    assert verify_flow(flow)["verification"]["status"] == "structural_invalid"


def test_strict_named_matrix_is_verified():
    flow = parse_matrix_flows(
        r"A=\begin{pmatrix}1&0\\0&1\end{pmatrix}。",
        owner={"global_id": "node-1"},
    )[0]

    result = verify_flow(flow)

    assert result["role"] == "named_matrix"
    assert result["verification"]["status"] == "verified"


def test_named_matrix_requires_binding_and_matching_state():
    flow = parse_matrix_flows(
        r"A=\begin{pmatrix}1\end{pmatrix}。",
        owner={"global_id": "node-1"},
    )[0]
    missing = {**flow, "bindings": []}
    unknown = {**flow, "bindings": [{**flow["bindings"][0], "state_id": "missing"}]}

    assert verify_flow(missing)["verification"]["status"] == "structural_invalid"
    assert verify_flow(unknown)["verification"]["status"] == "structural_invalid"


def test_tolerant_recovery_is_indeterminate_but_edges_stay_verified():
    flow = parse_matrix_flows(
        r"\left(\begin{array} { c c }1&2\\3&4\end{array}\right)"
        r"→"
        r"\left(\begin{array} { c c }1&2\\2&2\end{array}\right)",
        owner={"global_id": "node-1"},
        source_origin="ocr",
    )[0]

    result = verify_flow(flow)

    assert result["verification"]["status"] == "indeterminate"
    assert result["edges"][0]["verification_status"] == "verified"
    assert any(item["code"] == "ocr_recovery_requires_review" for item in result["verification"]["diagnostics"])


def test_v1_historical_transformation_remains_supported():
    flow = _flow(
        r"\begin{pmatrix}1&2\\3&4\end{pmatrix}"
        r"\to"
        r"\begin{pmatrix}3&4\\1&2\end{pmatrix}"
    )
    flow.pop("role", None)
    flow.pop("bindings", None)
    flow["schema_version"] = 1

    assert verify_flow(flow)["verification"]["status"] == "verified"


def test_tolerant_contradiction_keeps_edge_result_but_not_overall_claim():
    flow = parse_matrix_flows(
        r"\left(\begin{array} { c c }1&2\\3&4\end{array}\right)"
        r"\xrightarrow { R_2\to R_2-R_1 }"
        r"\left(\begin{array} { c c }1&2\\9&9\end{array}\right)",
        owner={"global_id": "node-1"},
        source_origin="ocr",
    )[0]

    result = verify_flow(flow)

    assert result["verification"]["status"] == "indeterminate"
    assert result["edges"][0]["verification_status"] == "contradicted"
