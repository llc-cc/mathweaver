export type MatrixKind = "matrix" | "determinant" | "augmented";

export type MatrixFlowVerificationStatus =
  | "verified"
  | "indeterminate"
  | "contradicted"
  | "structural_invalid";

export type MatrixFlowReviewStatus = "pending" | "approved" | "dismissed";
export type MatrixFlowAudience = "author" | "student";
export type MatrixFlowField = "statement" | "proof";
export type MatrixFlowLayoutMode = "vertical" | "horizontal";

export interface MatrixFlowSourceSpan {
  start: number;
  end: number;
}

export interface MatrixFlowDiagnostic {
  code: string;
  message: string;
  edge_id?: string;
  node_id?: string;
  details?: Record<string, unknown>;
}

export interface MatrixState {
  id: string;
  kind: MatrixKind;
  rows: number;
  columns: number;
  cells: string[][];
  latex: string;
  augmented_after_column?: number;
  outer_factor?: string;
  source_span?: MatrixFlowSourceSpan;
}

export type ElementaryOperationType =
  | "row_swap"
  | "col_swap"
  | "row_scale"
  | "col_scale"
  | "row_add"
  | "col_add";

export interface ElementaryOperation {
  type: ElementaryOperationType | string;
  first?: number;
  second?: number;
  target?: number;
  source?: number;
  factor?: string;
  coefficient?: string;
}

export interface MatrixTransform {
  id: string;
  from: string;
  to: string;
  operations: ElementaryOperation[];
  label?: string;
  provenance: "observed" | "inferred" | "teacher";
  determinant_factor?: string;
  verification_status: MatrixFlowVerificationStatus;
}

export interface MatrixFlowReference {
  id: string;
  field: MatrixFlowField;
  source_span: MatrixFlowSourceSpan;
  source_excerpt: string;
  context: "math" | "text";
}

export interface MatrixFlowBinding {
  id: string;
  symbol_latex: string;
  state_id: string;
  definition: {
    field: MatrixFlowField;
    source_span: MatrixFlowSourceSpan;
    source_excerpt: string;
  };
  references: MatrixFlowReference[];
}

interface MatrixFlowBase {
  id: string;
  owner: {
    global_id: string;
    source_block_key: string;
    field: MatrixFlowField;
    source_span: MatrixFlowSourceSpan;
    source_excerpt?: string;
  };
  source: {
    kind: "markdown" | "ocr" | "student";
    document_hash?: string;
    evidence_ids: string[];
  };
  nodes: MatrixState[];
  edges: MatrixTransform[];
  verification: {
    status: MatrixFlowVerificationStatus;
    diagnostics: MatrixFlowDiagnostic[];
  };
  review: {
    status: MatrixFlowReviewStatus;
    reason?: string;
    revision: number;
  };
}

export interface MatrixFlowV1 extends MatrixFlowBase {
  schema_version: 1;
}

export interface MatrixFlowV2 extends MatrixFlowBase {
  schema_version: 2;
  role: "transformation" | "named_matrix";
  bindings: MatrixFlowBinding[];
}

export type MatrixFlow = MatrixFlowV1 | MatrixFlowV2;
