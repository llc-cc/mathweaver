"""Safe, local MatrixFlow verification.

The expression parser below is a small whitelist parser.  It never evaluates
source text as Python and it intentionally returns ``indeterminate`` for
syntax outside the supported scalar grammar.
"""

from __future__ import annotations

import copy
import re
from fractions import Fraction
from typing import Any

from .models import MATRIX_FLOW_VERIFIER_VERSION, diagnostic


class UnsupportedExpression(ValueError):
    pass


class StructuralFlowError(ValueError):
    pass


class _Poly:
    def __init__(self, terms: dict[tuple[str, ...], Fraction] | None = None):
        self.terms = {
            tuple(mon): Fraction(value)
            for mon, value in (terms or {}).items()
            if Fraction(value) != 0
        }

    @classmethod
    def constant(cls, value: Fraction | int | str) -> "_Poly":
        return cls({(): Fraction(value)}) if Fraction(value) else cls()

    @classmethod
    def symbol(cls, value: str) -> "_Poly":
        return cls({(value,): Fraction(1)})

    def is_zero(self) -> bool:
        return not self.terms

    def is_constant(self) -> bool:
        return not self.terms or set(self.terms).issubset({()})

    def constant_value(self) -> Fraction | None:
        if not self.is_constant():
            return None
        return self.terms.get((), Fraction(0))

    def __add__(self, other: "_Poly") -> "_Poly":
        terms = dict(self.terms)
        for monomial, value in other.terms.items():
            terms[monomial] = terms.get(monomial, Fraction(0)) + value
        return _Poly(terms)

    def __neg__(self) -> "_Poly":
        return _Poly({monomial: -value for monomial, value in self.terms.items()})

    def __sub__(self, other: "_Poly") -> "_Poly":
        return self + (-other)

    def __mul__(self, other: "_Poly") -> "_Poly":
        terms: dict[tuple[str, ...], Fraction] = {}
        for left_mon, left_value in self.terms.items():
            for right_mon, right_value in other.terms.items():
                monomial = tuple(sorted(left_mon + right_mon))
                terms[monomial] = terms.get(monomial, Fraction(0)) + left_value * right_value
        return _Poly(terms)

    def divide(self, other: "_Poly") -> "_Poly":
        value = other.constant_value()
        if value is None or value == 0:
            raise UnsupportedExpression("symbolic division is outside the v1 grammar")
        return _Poly({monomial: coefficient / value for monomial, coefficient in self.terms.items()})

    def power(self, exponent: int) -> "_Poly":
        if exponent < 0 or exponent > 8:
            raise UnsupportedExpression("only small non-negative integer powers are supported")
        result = _Poly.constant(1)
        for _ in range(exponent):
            result = result * self
        return result

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Poly) and self.terms == other.terms

    def __repr__(self) -> str:
        if self.is_zero():
            return "0"
        parts = []
        for monomial, coefficient in sorted(self.terms.items()):
            if monomial:
                symbol = "*".join(monomial)
                if coefficient == 1:
                    parts.append(symbol)
                elif coefficient == -1:
                    parts.append(f"-{symbol}")
                else:
                    parts.append(f"{coefficient}*{symbol}")
            else:
                parts.append(str(coefficient))
        return "+".join(parts)


def _read_group(text: str, start: int) -> tuple[str, int]:
    if start >= len(text) or text[start] != "{":
        raise UnsupportedExpression("expected a braced LaTeX group")
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index], index + 1
    raise UnsupportedExpression("unclosed LaTeX group")


def _replace_frac(text: str) -> str:
    result = []
    index = 0
    while index < len(text):
        if text.startswith("\\frac", index):
            cursor = index + len("\\frac")
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            numerator, cursor = _read_group(text, cursor)
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            denominator, cursor = _read_group(text, cursor)
            result.append(f"({_replace_frac(numerator)})/({_replace_frac(denominator)})")
            index = cursor
            continue
        result.append(text[index])
        index += 1
    return "".join(result)


def _normalize_expression(text: str) -> str:
    value = _replace_frac(str(text or "").strip())
    value = value.replace("\\left", "").replace("\\right", "")
    value = value.replace("\\cdot", "*").replace("\\times", "*")
    value = value.replace("\\,", "").replace("\\!", "")
    value = re.sub(r"\\text\{[^{}]*\}", "", value)
    value = re.sub(r"\^\{([^{}]+)\}", r"^(\1)", value)
    value = re.sub(r"_\{([^{}]+)\}", r"_\1", value)
    value = value.replace("{", "(").replace("}", ")")
    if re.search(r"[A-Za-z][A-Za-z0-9_]*\s*\(", value):
        raise UnsupportedExpression("general functions are outside the v1 scalar grammar")
    macros = {
        "\\alpha": "alpha",
        "\\beta": "beta",
        "\\gamma": "gamma",
        "\\delta": "delta",
        "\\lambda": "lambda",
        "\\mu": "mu",
        "\\pi": "pi",
        "\\theta": "theta",
        "\\cdot": "*",
        "\\times": "*",
    }
    for macro, replacement in macros.items():
        value = value.replace(macro, replacement)
    if "\\" in value:
        raise UnsupportedExpression("unknown LaTeX command")
    return value.strip()


_TOKEN_RE = re.compile(r"\s*(?:(?P<number>\d+(?:\.\d+)?)|(?P<name>[A-Za-z][A-Za-z0-9_]*)|(?P<operator>[+\-*/^()]))")


def _tokens(text: str) -> list[tuple[str, str]]:
    result = []
    position = 0
    while position < len(text):
        match = _TOKEN_RE.match(text, position)
        if not match:
            raise UnsupportedExpression(f"unsupported scalar token near {text[position:position + 12]!r}")
        kind = "number" if match.group("number") else "name" if match.group("name") else match.group("operator")
        result.append((kind, match.group(kind) if kind in {"number", "name"} else match.group("operator")))
        position = match.end()
    result.append(("eof", ""))
    return result


class _ExpressionParser:
    def __init__(self, text: str):
        self.items = _tokens(_normalize_expression(text))
        self.index = 0

    def current(self) -> tuple[str, str]:
        return self.items[self.index]

    def take(self, value: str | None = None) -> tuple[str, str]:
        item = self.current()
        if value is not None and item[1] != value:
            raise UnsupportedExpression(f"expected {value!r}")
        self.index += 1
        return item

    def parse(self) -> _Poly:
        result = self.addition()
        if self.current()[0] != "eof":
            raise UnsupportedExpression("trailing scalar input")
        return result

    def addition(self) -> _Poly:
        result = self.multiplication()
        while self.current()[1] in {"+", "-"}:
            operator = self.take()[1]
            right = self.multiplication()
            result = result + right if operator == "+" else result - right
        return result

    def multiplication(self) -> _Poly:
        result = self.unary()
        while True:
            current = self.current()
            if current[1] in {"*", "/"}:
                operator = self.take()[1]
                right = self.unary()
                result = result * right if operator == "*" else result.divide(right)
                continue
            if current[0] in {"number", "name"} or current[1] == "(":
                result = result * self.unary()
                continue
            break
        return result

    def unary(self) -> _Poly:
        if self.current()[1] == "+":
            self.take("+")
            return self.unary()
        if self.current()[1] == "-":
            self.take("-")
            return -self.unary()
        result = self.primary()
        if self.current()[1] == "^":
            self.take("^")
            exponent = self.take()
            if exponent[0] != "number" or "." in exponent[1]:
                raise UnsupportedExpression("power must be an integer")
            result = result.power(int(exponent[1]))
        return result

    def primary(self) -> _Poly:
        kind, value = self.current()
        if kind == "number":
            self.take()
            return _Poly.constant(Fraction(value))
        if kind == "name":
            self.take()
            return _Poly.symbol(value)
        if value == "(":
            self.take("(")
            result = self.addition()
            self.take(")")
            return result
        raise UnsupportedExpression("expected a scalar expression")


def _parse_scalar(value: Any) -> _Poly:
    if not isinstance(value, str) or not value.strip():
        raise UnsupportedExpression("empty scalar cell")
    return _ExpressionParser(value).parse()


def _matrix(state: dict[str, Any]) -> list[list[_Poly]]:
    rows = state.get("rows")
    columns = state.get("columns")
    cells = state.get("cells")
    if not isinstance(rows, int) or not isinstance(columns, int) or rows <= 0 or columns <= 0:
        raise StructuralFlowError("matrix dimensions must be positive integers")
    if not isinstance(cells, list) or len(cells) != rows or any(not isinstance(row, list) or len(row) != columns for row in cells):
        raise StructuralFlowError("matrix cells are not rectangular")
    return [[_parse_scalar(cell) for cell in row] for row in cells]


def _same_matrix(left: list[list[_Poly]], right: list[list[_Poly]]) -> bool:
    return len(left) == len(right) and all(
        len(a_row) == len(b_row) and all(a == b for a, b in zip(a_row, b_row))
        for a_row, b_row in zip(left, right)
    )


def _mismatches(expected: list[list[_Poly]], actual: list[list[_Poly]]) -> list[dict[str, Any]]:
    result = []
    for row, (expected_row, actual_row) in enumerate(zip(expected, actual), start=1):
        for column, (expected_cell, actual_cell) in enumerate(zip(expected_row, actual_row), start=1):
            if expected_cell != actual_cell:
                result.append({"row": row, "column": column, "expected": repr(expected_cell), "actual": repr(actual_cell)})
    return result[:20]


def compare_matrix_states(expected_state: dict[str, Any], actual_state: dict[str, Any]) -> dict[str, Any]:
    """Conservatively compare two parsed matrix states with the verifier grammar."""

    try:
        expected = _matrix(expected_state)
        actual = _matrix(actual_state)
    except UnsupportedExpression as exc:
        return {"status": "indeterminate", "message": str(exc), "mismatched_cells": []}
    except (StructuralFlowError, KeyError, TypeError, ValueError) as exc:
        return {"status": "structural_invalid", "message": str(exc), "mismatched_cells": []}
    if len(expected) != len(actual) or any(len(a) != len(b) for a, b in zip(expected, actual)):
        return {
            "status": "contradicted",
            "message": "matrix dimensions do not match the reference answer",
            "mismatched_cells": [],
        }
    if _same_matrix(expected, actual):
        return {"status": "verified", "message": "final matrix matches the reference answer", "mismatched_cells": []}
    return {
        "status": "contradicted",
        "message": "final matrix does not match the reference answer",
        "mismatched_cells": _mismatches(expected, actual),
    }


def _copy_matrix(value: list[list[_Poly]]) -> list[list[_Poly]]:
    return [list(row) for row in value]


def _op_axis(operation: dict[str, Any]) -> tuple[str, int, int | None]:
    operation_type = str(operation.get("type") or "")
    if operation_type.endswith("_swap"):
        return operation_type.split("_", 1)[0], int(operation.get("first")), int(operation.get("second"))
    return operation_type.split("_", 1)[0], int(operation.get("target")), None


def _apply_operation(matrix: list[list[_Poly]], operation: dict[str, Any]) -> list[list[_Poly]]:
    if not isinstance(operation, dict):
        raise StructuralFlowError("elementary operation is not an object")
    operation_type = str(operation.get("type") or "")
    axis, target, second = _op_axis(operation)
    limit = len(matrix) if axis == "row" else len(matrix[0]) if matrix else 0
    if target < 1 or target > limit or second is not None and (second < 1 or second > limit):
        raise StructuralFlowError("elementary operation index is out of range")
    result = _copy_matrix(matrix)
    if operation_type in {"row_swap", "col_swap"}:
        if axis == "row":
            result[target - 1], result[second - 1] = result[second - 1], result[target - 1]
        else:
            for row in result:
                row[target - 1], row[second - 1] = row[second - 1], row[target - 1]
        return result

    if operation_type.endswith("_scale"):
        factor = _parse_scalar(operation.get("factor"))
        if factor.is_zero():
            raise StructuralFlowError("a scaling operation must use a non-zero factor")
        if factor.constant_value() is None:
            raise UnsupportedExpression("the verifier cannot prove a symbolic scaling factor is non-zero")
        if axis == "row":
            result[target - 1] = [cell * factor for cell in result[target - 1]]
        else:
            for row in result:
                row[target - 1] = row[target - 1] * factor
        return result

    if operation_type.endswith("_add"):
        source = int(operation.get("source"))
        if source < 1 or source > limit or source == target:
            raise StructuralFlowError("add operation source index is invalid")
        factor = _parse_scalar(operation.get("coefficient"))
        if axis == "row":
            result[target - 1] = [
                left + factor * right
                for left, right in zip(result[target - 1], result[source - 1])
            ]
        else:
            for row in result:
                row[target - 1] = row[target - 1] + factor * row[source - 1]
        return result

    raise StructuralFlowError(f"unsupported elementary operation type: {operation_type}")


def _ratio(numerator: _Poly, denominator: _Poly) -> _Poly | None:
    if denominator.is_zero():
        return None
    try:
        return numerator.divide(denominator)
    except UnsupportedExpression:
        return None


def _infer_operations(source: list[list[_Poly]], target: list[list[_Poly]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    rows = len(source)
    columns = len(source[0]) if source else 0

    for first in range(rows):
        for second in range(first + 1, rows):
            candidate = _copy_matrix(source)
            candidate[first], candidate[second] = candidate[second], candidate[first]
            if _same_matrix(candidate, target):
                candidates.append({"type": "row_swap", "first": first + 1, "second": second + 1})

    for row in range(rows):
        for column in range(columns):
            factor = _ratio(target[row][column], source[row][column])
            if factor is None or factor.is_zero():
                continue
            candidate = _copy_matrix(source)
            candidate[row] = [cell * factor for cell in candidate[row]]
            if _same_matrix(candidate, target):
                candidates.append({"type": "row_scale", "target": row + 1, "factor": repr(factor)})
                break

    for target_row in range(rows):
        for source_row in range(rows):
            if target_row == source_row:
                continue
            for column in range(columns):
                delta = target[target_row][column] - source[target_row][column]
                factor = _ratio(delta, source[source_row][column])
                if factor is None:
                    continue
                candidate = _copy_matrix(source)
                candidate[target_row] = [
                    left + factor * right
                    for left, right in zip(candidate[target_row], candidate[source_row])
                ]
                if _same_matrix(candidate, target):
                    candidates.append({
                        "type": "row_add",
                        "target": target_row + 1,
                        "source": source_row + 1,
                        "coefficient": repr(factor),
                    })
                    break

    for first in range(columns):
        for second in range(first + 1, columns):
            candidate = _copy_matrix(source)
            for row in candidate:
                row[first], row[second] = row[second], row[first]
            if _same_matrix(candidate, target):
                candidates.append({"type": "col_swap", "first": first + 1, "second": second + 1})

    for column in range(columns):
        for row in range(rows):
            factor = _ratio(target[row][column], source[row][column])
            if factor is None or factor.is_zero():
                continue
            candidate = _copy_matrix(source)
            for candidate_row in candidate:
                candidate_row[column] = candidate_row[column] * factor
            if _same_matrix(candidate, target):
                candidates.append({"type": "col_scale", "target": column + 1, "factor": repr(factor)})
                break

    for target_column in range(columns):
        for source_column in range(columns):
            if target_column == source_column:
                continue
            for row in range(rows):
                delta = target[row][target_column] - source[row][target_column]
                factor = _ratio(delta, source[row][source_column])
                if factor is None:
                    continue
                candidate = _copy_matrix(source)
                for candidate_row in candidate:
                    candidate_row[target_column] = candidate_row[target_column] + factor * candidate_row[source_column]
                if _same_matrix(candidate, target):
                    candidates.append({
                        "type": "col_add",
                        "target": target_column + 1,
                        "source": source_column + 1,
                        "coefficient": repr(factor),
                    })
                    break

    return candidates


def _determinant_factor(operations: list[dict[str, Any]]) -> _Poly:
    result = _Poly.constant(1)
    for operation in operations:
        operation_type = str(operation.get("type") or "")
        if operation_type.endswith("_swap"):
            result = -result
        elif operation_type.endswith("_scale"):
            result = result * _parse_scalar(operation.get("factor"))
    return result


def _topological_cycle(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> bool:
    node_ids = {str(node.get("id")) for node in nodes}
    indegree = {node_id: 0 for node_id in node_ids}
    adjacency = {node_id: [] for node_id in node_ids}
    for edge in edges:
        source = str(edge.get("from") or "")
        target = str(edge.get("to") or "")
        if source not in node_ids or target not in node_ids:
            raise StructuralFlowError("edge endpoint is not a flow node")
        indegree[target] += 1
        adjacency[source].append(target)
    queue = [node_id for node_id, degree in indegree.items() if degree == 0]
    visited = 0
    while queue:
        current = queue.pop()
        visited += 1
        for target in adjacency[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return visited != len(node_ids)


def _valid_span(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("start"), int)
        and isinstance(value.get("end"), int)
        and value["start"] >= 0
        and value["end"] > value["start"]
    )


def _verify_named_matrix(result: dict[str, Any]) -> dict[str, Any]:
    nodes = result.get("nodes")
    edges = result.get("edges")
    bindings = result.get("bindings")
    diagnostics: list[dict[str, Any]] = []
    if not isinstance(nodes, list) or len(nodes) != 1 or not isinstance(edges, list) or edges:
        result["verification"] = {
            "status": "structural_invalid",
            "diagnostics": [diagnostic("named_matrix_shape", "A named_matrix flow requires exactly one state and zero edges.")],
            "verifier_version": MATRIX_FLOW_VERIFIER_VERSION,
        }
        return result
    if not isinstance(bindings, list) or not bindings:
        result["verification"] = {
            "status": "structural_invalid",
            "diagnostics": [diagnostic("named_matrix_binding_missing", "A named_matrix flow requires at least one binding.")],
            "verifier_version": MATRIX_FLOW_VERIFIER_VERSION,
        }
        return result
    try:
        _matrix(nodes[0])
    except UnsupportedExpression as exc:
        result["verification"] = {
            "status": "indeterminate",
            "diagnostics": [diagnostic("unsupported_expression", str(exc))],
            "verifier_version": MATRIX_FLOW_VERIFIER_VERSION,
        }
        return result
    except StructuralFlowError as exc:
        result["verification"] = {
            "status": "structural_invalid",
            "diagnostics": [diagnostic("structural_invalid", str(exc))],
            "verifier_version": MATRIX_FLOW_VERIFIER_VERSION,
        }
        return result
    state_id = str(nodes[0].get("id") or "")
    for binding in bindings:
        if not isinstance(binding, dict) or str(binding.get("state_id") or "") != state_id:
            diagnostics.append(diagnostic("named_matrix_binding_state", "A binding must target the named matrix state."))
            continue
        symbol = str(binding.get("symbol_latex") or "").strip()
        definition = binding.get("definition")
        if not symbol or not isinstance(definition, dict):
            diagnostics.append(diagnostic("named_matrix_binding_invalid", "A binding requires a symbol and definition."))
            continue
        if definition.get("field") not in {"statement", "proof"} or not _valid_span(definition.get("source_span")) or not isinstance(definition.get("source_excerpt"), str) or not definition.get("source_excerpt"):
            diagnostics.append(diagnostic("named_matrix_definition_invalid", "The binding definition span or excerpt is invalid."))
        references = binding.get("references")
        if not isinstance(references, list):
            diagnostics.append(diagnostic("named_matrix_references_invalid", "Binding references must be a list."))
            continue
        for reference in references:
            if (
                not isinstance(reference, dict)
                or reference.get("field") not in {"statement", "proof"}
                or reference.get("context") not in {"math", "text"}
                or not _valid_span(reference.get("source_span"))
                or not isinstance(reference.get("source_excerpt"), str)
                or not reference.get("source_excerpt")
            ):
                diagnostics.append(diagnostic("named_matrix_reference_invalid", "A named matrix reference is malformed."))
                break
    if diagnostics:
        result["verification"] = {
            "status": "structural_invalid",
            "diagnostics": diagnostics,
            "verifier_version": MATRIX_FLOW_VERIFIER_VERSION,
        }
        return result
    recovered = bool((result.get("source") or {}).get("recovered"))
    if recovered:
        diagnostics.append(diagnostic(
            "ocr_recovery_requires_review",
            "The matrix structure was recovered from tolerant OCR syntax and requires review.",
            recovery_actions=(result.get("source") or {}).get("recovery_actions") or [],
        ))
    result["verification"] = {
        "status": "indeterminate" if recovered else "verified",
        "diagnostics": diagnostics,
        "verifier_version": MATRIX_FLOW_VERIFIER_VERSION,
    }
    return result


def verify_flow(flow: dict[str, Any]) -> dict[str, Any]:
    """Verify one flow and return a deep-copied, server-owned result."""

    result = copy.deepcopy(flow or {})
    nodes = result.get("nodes")
    edges = result.get("edges")
    role = result.get("role", "transformation")
    if role == "named_matrix":
        return _verify_named_matrix(result)
    diagnostics: list[dict[str, Any]] = []
    if role != "transformation" or not isinstance(nodes, list) or not isinstance(edges, list) or len(nodes) < 2 or not edges:
        result["verification"] = {"status": "structural_invalid", "diagnostics": [diagnostic("flow_too_small", "MatrixFlow requires at least two nodes and one edge.")]}
        return result

    try:
        if _topological_cycle(nodes, edges):
            raise StructuralFlowError("flow contains a cycle")
        parsed_nodes = {str(node.get("id")): _matrix(node) for node in nodes}
        node_by_id = {str(node.get("id")): node for node in nodes}
    except UnsupportedExpression as exc:
        diagnostics.append(diagnostic("unsupported_expression", str(exc)))
        for edge in edges:
            if isinstance(edge, dict):
                edge["verification_status"] = "indeterminate"
        result["verification"] = {
            "status": "indeterminate",
            "diagnostics": diagnostics,
            "verifier_version": MATRIX_FLOW_VERIFIER_VERSION,
        }
        return result
    except StructuralFlowError as exc:
        diagnostics.append(diagnostic("structural_invalid", str(exc)))
        result["verification"] = {"status": "structural_invalid", "diagnostics": diagnostics}
        return result

    contradicted = False
    indeterminate = False
    for edge in edges:
        edge_id = str(edge.get("id") or "")
        source_id = str(edge.get("from") or "")
        target_id = str(edge.get("to") or "")
        try:
            source = parsed_nodes[source_id]
            target = parsed_nodes[target_id]
            source_node = node_by_id[source_id]
            target_node = node_by_id[target_id]
            if source_node.get("kind") != target_node.get("kind"):
                raise StructuralFlowError("edge changes matrix kind")
            if source_node.get("kind") == "augmented":
                source_divider = source_node.get("augmented_after_column")
                target_divider = target_node.get("augmented_after_column")
                if source_divider != target_divider:
                    raise StructuralFlowError("augmented divider changed across an edge")
            operations = edge.get("operations") or []
            if not operations:
                inferred = _infer_operations(source, target)
                if len(inferred) != 1:
                    indeterminate = True
                    edge["verification_status"] = "indeterminate"
                    diagnostics.append(diagnostic(
                        "operation_not_unique",
                        "The edge has no explicit operation and does not have a unique elementary-transform explanation.",
                        edge_id=edge_id,
                    ))
                    continue
                operations = inferred
                edge["operations"] = operations
                edge["provenance"] = "inferred"
            if source_node.get("kind") == "augmented" and any(str(op.get("type", "")).startswith("col_") for op in operations):
                indeterminate = True
                edge["verification_status"] = "indeterminate"
                diagnostics.append(diagnostic(
                    "augmented_column_operation",
                    "Column operations on augmented matrices are outside MatrixFlow v1 verification.",
                    edge_id=edge_id,
                ))
                continue
            expected = source
            for operation in operations:
                expected = _apply_operation(expected, operation)
            if not _same_matrix(expected, target):
                contradicted = True
                edge["verification_status"] = "contradicted"
                mismatches = _mismatches(expected, target)
                diagnostics.append(diagnostic(
                    "matrix_mismatch",
                    "The target matrix does not match the stated elementary operations.",
                    edge_id=edge_id,
                    mismatched_cells=mismatches,
                ))
                continue

            if source_node.get("kind") == "determinant":
                source_factor = _parse_scalar(source_node.get("outer_factor") or "1")
                target_factor = _parse_scalar(target_node.get("outer_factor") or "1")
                expected_factor = source_factor * _determinant_factor(operations)
                if edge.get("determinant_factor"):
                    expected_factor = source_factor * _parse_scalar(edge["determinant_factor"])
                if expected_factor != target_factor:
                    contradicted = True
                    edge["verification_status"] = "contradicted"
                    diagnostics.append(diagnostic(
                        "determinant_factor_mismatch",
                        "The determinant outer factor does not match row/column swaps and scalings.",
                        edge_id=edge_id,
                        expected=repr(expected_factor),
                        actual=repr(target_factor),
                    ))
                    continue
            edge["verification_status"] = "verified"
        except UnsupportedExpression as exc:
            indeterminate = True
            edge["verification_status"] = "indeterminate"
            diagnostics.append(diagnostic("unsupported_expression", str(exc), edge_id=edge_id))
        except (StructuralFlowError, KeyError, TypeError, ValueError) as exc:
            contradicted = True
            edge["verification_status"] = "contradicted"
            diagnostics.append(diagnostic("invalid_operation", str(exc), edge_id=edge_id))

    recovered = bool((result.get("source") or {}).get("recovered"))
    if recovered:
        indeterminate = True
        diagnostics.append(diagnostic(
            "ocr_recovery_requires_review",
            "The matrix flow was recovered from tolerant OCR syntax and requires review.",
            recovery_actions=(result.get("source") or {}).get("recovery_actions") or [],
        ))
    status = "indeterminate" if recovered else "contradicted" if contradicted else "indeterminate" if indeterminate else "verified"
    result["verification"] = {
        "status": status,
        "diagnostics": diagnostics,
        "verifier_version": MATRIX_FLOW_VERIFIER_VERSION,
    }
    return result


def verify_flows(flows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [verify_flow(flow) for flow in flows or []]


__all__ = ["compare_matrix_states", "verify_flow", "verify_flows"]
