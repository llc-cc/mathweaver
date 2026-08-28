from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matrix_flow.grading import analyze_matrix_answer


REFERENCE = (
    r"\begin{pmatrix}1&2\\3&4\end{pmatrix}"
    r"\xrightarrow{R_2\to R_2-R_1}"
    r"\begin{pmatrix}1&2\\2&2\end{pmatrix}"
)


def test_verified_row_operation_matches_reference_final_matrix():
    report = analyze_matrix_answer(REFERENCE, REFERENCE)

    assert report["status"] == "verified"
    assert report["issues"] == []
    assert report["comparison"]["status"] == "verified"


def test_wrong_cells_are_reported_with_expected_and_actual_values():
    student = (
        r"\begin{pmatrix}1&2\\3&4\end{pmatrix}"
        r"\xrightarrow{R_2\to R_2-R_1}"
        r"\begin{pmatrix}1&2\\9&9\end{pmatrix}"
    )

    report = analyze_matrix_answer(student, REFERENCE)

    assert report["status"] == "contradicted"
    mismatch = next(issue for issue in report["issues"] if issue["code"] == "matrix_mismatch")
    assert mismatch["mismatchedCells"] == [
        {"row": 2, "column": 1, "expected": "2", "actual": "9"},
        {"row": 2, "column": 2, "expected": "2", "actual": "9"},
    ]
    assert any(issue["code"] == "reference_final_matrix_mismatch" for issue in report["issues"])


def test_determinant_row_swap_requires_negative_outer_factor():
    student = (
        r"\begin{vmatrix}1&2\\3&4\end{vmatrix}"
        r"\xrightarrow{R_1\leftrightarrow R_2}"
        r"\begin{vmatrix}3&4\\1&2\end{vmatrix}"
    )

    report = analyze_matrix_answer(student, "")

    assert report["status"] == "contradicted"
    issue = next(issue for issue in report["issues"] if issue["code"] == "determinant_factor_mismatch")
    assert issue["expected"] == "-1"
    assert issue["actual"] == "1"


def test_unsupported_expression_is_indeterminate_not_incorrect():
    student = (
        r"\begin{pmatrix}\sqrt{2}&0\\0&1\end{pmatrix}"
        r"\xrightarrow{R_1\to R_1}"
        r"\begin{pmatrix}\sqrt{2}&0\\0&1\end{pmatrix}"
    )

    report = analyze_matrix_answer(student, "")

    assert report["status"] == "indeterminate"
    assert any(issue["code"] == "unsupported_expression" for issue in report["issues"])


def test_plain_text_answer_is_not_applicable():
    report = analyze_matrix_answer("这是一个文字证明。", "标准答案。")

    assert report["status"] == "not_applicable"
    assert report["flowCount"] == 0
    assert report["issues"] == []
