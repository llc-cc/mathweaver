"""Versioned MatrixFlow data structures.

The public representation intentionally stays JSON-shaped.  The fixed graph
pipeline and the education API both pass dictionaries around, so keeping this
boundary explicit avoids leaking Python-only objects into cached artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


MATRIX_FLOW_SCHEMA_VERSION = 2
MATRIX_FLOW_PARSER_VERSION = "4"
MATRIX_FLOW_VERIFIER_VERSION = "2"

MatrixKind = Literal["matrix", "determinant", "augmented"]
VerificationStatus = Literal[
    "verified",
    "indeterminate",
    "contradicted",
    "structural_invalid",
]
ReviewStatus = Literal["pending", "approved", "dismissed"]
MatrixFlowRole = Literal["transformation", "named_matrix"]


@dataclass
class VerificationDiagnostic:
    code: str
    message: str
    edge_id: str | None = None
    node_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            **({"edge_id": self.edge_id} if self.edge_id else {}),
            **({"node_id": self.node_id} if self.node_id else {}),
            **({"details": self.details} if self.details else {}),
        }


@dataclass
class MatrixState:
    id: str
    kind: MatrixKind
    rows: int
    columns: int
    cells: list[list[str]]
    latex: str
    augmented_after_column: int | None = None
    outer_factor: str | None = None
    source_span: dict[str, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "rows": self.rows,
            "columns": self.columns,
            "cells": self.cells,
            "latex": self.latex,
        }
        if self.augmented_after_column is not None:
            result["augmented_after_column"] = self.augmented_after_column
        if self.outer_factor:
            result["outer_factor"] = self.outer_factor
        if self.source_span:
            result["source_span"] = self.source_span
        return result


@dataclass
class MatrixTransform:
    id: str
    from_id: str
    to_id: str
    operations: list[dict[str, Any]] = field(default_factory=list)
    label: str | None = None
    provenance: Literal["observed", "inferred", "teacher"] = "observed"
    determinant_factor: str | None = None
    verification_status: VerificationStatus = "indeterminate"

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "from": self.from_id,
            "to": self.to_id,
            "operations": self.operations,
            "provenance": self.provenance,
            "verification_status": self.verification_status,
        }
        if self.label:
            result["label"] = self.label
        if self.determinant_factor:
            result["determinant_factor"] = self.determinant_factor
        return result


@dataclass
class MatrixReference:
    id: str
    field: Literal["statement", "proof"]
    source_span: dict[str, int]
    source_excerpt: str
    context: Literal["math", "text"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "field": self.field,
            "source_span": self.source_span,
            "source_excerpt": self.source_excerpt,
            "context": self.context,
        }


@dataclass
class MatrixBinding:
    id: str
    symbol_latex: str
    state_id: str
    definition: dict[str, Any]
    references: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "symbol_latex": self.symbol_latex,
            "state_id": self.state_id,
            "definition": self.definition,
            "references": self.references,
        }


@dataclass
class MatrixFlow:
    id: str
    owner: dict[str, Any]
    source: dict[str, Any]
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    role: MatrixFlowRole = "transformation"
    bindings: list[dict[str, Any]] = field(default_factory=list)
    verification_status: VerificationStatus = "indeterminate"
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    review_status: ReviewStatus = "pending"
    review_reason: str | None = None
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        verification: dict[str, Any] = {
            "status": self.verification_status,
            "diagnostics": self.diagnostics,
        }
        review: dict[str, Any] = {
            "status": self.review_status,
            "revision": self.revision,
        }
        if self.review_reason:
            review["reason"] = self.review_reason
        return {
            "schema_version": MATRIX_FLOW_SCHEMA_VERSION,
            "id": self.id,
            "role": self.role,
            "owner": self.owner,
            "source": self.source,
            "nodes": self.nodes,
            "edges": self.edges,
            "bindings": self.bindings,
            "verification": verification,
            "review": review,
        }


def diagnostic(
    code: str,
    message: str,
    *,
    edge_id: str | None = None,
    node_id: str | None = None,
    **details: Any,
) -> dict[str, Any]:
    """Build a compact JSON diagnostic without empty optional fields."""

    return VerificationDiagnostic(
        code=code,
        message=message,
        edge_id=edge_id,
        node_id=node_id,
        details=details,
    ).to_dict()
