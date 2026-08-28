"""Deterministic matrix evidence for education grading.

This module only reports evidence. It never assigns or caps a score.
"""

from __future__ import annotations

from typing import Any

from .parser import parse_matrix_flows
from .verifier import compare_matrix_states, verify_flows


_STATUS_ORDER = {
    "not_applicable": 0,
    "verified": 1,
    "indeterminate": 2,
    "structural_invalid": 3,
    "contradicted": 4,
}

_DIAGNOSTIC_MESSAGES = {
    "matrix_mismatch": "矩阵变换后的元素与所写行列操作不一致。",
    "determinant_factor_mismatch": "行列式换行、换列或倍乘后的外部因子不正确。",
    "operation_not_unique": "未写明操作，且无法唯一确定使用了哪一种初等变换。",
    "unsupported_expression": "表达式超出当前数值检查范围，需要教师人工判断。",
    "invalid_operation": "行列操作格式或索引无效。",
    "structural_invalid": "矩阵计算过程的结构不完整或不合法。",
    "flow_too_small": "矩阵计算过程不足以形成可验证的连续步骤。",
    "augmented_column_operation": "增广矩阵的列变换不自动判定，需要教师人工判断。",
    "ocr_recovery_requires_review": "该矩阵由 OCR 容错恢复，需要教师核对识别结果。",
}


def _issue(diagnostic: dict[str, Any], source_excerpt: str) -> dict[str, Any]:
    code = str(diagnostic.get("code") or "matrix_check")
    details = diagnostic.get("details") if isinstance(diagnostic.get("details"), dict) else {}
    return {
        "code": code,
        "message": _DIAGNOSTIC_MESSAGES.get(code, str(diagnostic.get("message") or "矩阵计算需要教师复核。")),
        "edgeId": diagnostic.get("edge_id"),
        "nodeId": diagnostic.get("node_id"),
        "sourceExcerpt": source_excerpt,
        "mismatchedCells": details.get("mismatched_cells") or [],
        "expected": details.get("expected"),
        "actual": details.get("actual"),
        "details": details,
    }


def _flow_status(flows: list[dict[str, Any]]) -> str:
    if not flows:
        return "not_applicable"
    statuses = [str((flow.get("verification") or {}).get("status") or "indeterminate") for flow in flows]
    return max(statuses, key=lambda status: _STATUS_ORDER.get(status, 2))


def _summary(status: str) -> str:
    return {
        "not_applicable": "答案中未识别到可进行确定性检查的矩阵或行列式计算过程。",
        "verified": "已识别的矩阵或行列式计算步骤通过确定性检查。",
        "contradicted": "发现与所写变换或参考结果不一致的矩阵计算步骤。",
        "indeterminate": "已识别矩阵计算，但部分表达式无法可靠自动判断。",
        "structural_invalid": "矩阵计算过程结构不完整，需要教师人工核对。",
    }.get(status, "矩阵计算需要教师人工核对。")


def analyze_matrix_answer(student_answer: str, reference_answer: str) -> dict[str, Any]:
    """Return auditable matrix evidence for one submitted answer."""

    student_flows = verify_flows(parse_matrix_flows(str(student_answer or ""), owner={"global_id": "student-answer"}))
    reference_flows = verify_flows(parse_matrix_flows(str(reference_answer or ""), owner={"global_id": "reference-answer"}))
    issues: list[dict[str, Any]] = []
    for flow in student_flows:
        excerpt = str((flow.get("owner") or {}).get("source_excerpt") or "")
        for diagnostic in (flow.get("verification") or {}).get("diagnostics") or []:
            if isinstance(diagnostic, dict):
                issues.append(_issue(diagnostic, excerpt))

    status = _flow_status(student_flows)
    comparison = None
    if len(student_flows) == 1 and len(reference_flows) == 1:
        student_nodes = student_flows[0].get("nodes") or []
        reference_nodes = reference_flows[0].get("nodes") or []
        if student_nodes and reference_nodes:
            comparison = compare_matrix_states(reference_nodes[-1], student_nodes[-1])
            comparison_status = str(comparison.get("status") or "indeterminate")
            if comparison_status == "contradicted":
                issues.append({
                    "code": "reference_final_matrix_mismatch",
                    "message": "学生计算得到的最终矩阵与参考答案不一致。",
                    "sourceExcerpt": str((student_flows[0].get("owner") or {}).get("source_excerpt") or ""),
                    "mismatchedCells": comparison.get("mismatched_cells") or [],
                    "expected": None,
                    "actual": None,
                    "details": {},
                })
            if _STATUS_ORDER.get(comparison_status, 2) > _STATUS_ORDER.get(status, 0):
                status = comparison_status

    reference_status = _flow_status(reference_flows)
    return {
        "status": status,
        "summary": _summary(status),
        "issues": issues,
        "flowCount": len(student_flows),
        "referenceFlowCount": len(reference_flows),
        "referenceStatus": reference_status,
        "comparison": comparison,
        "flows": student_flows,
    }


__all__ = ["analyze_matrix_answer"]
