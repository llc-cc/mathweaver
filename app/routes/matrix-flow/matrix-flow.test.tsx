import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MatrixFlowText, MatrixStateMatrix, matrixFlowDelimiterKind, matrixFlowOperationPopoverContent, placeMatrixFlowPopover, placeMatrixReferencePopover, resolveMatrixFlowPopoverPresentation } from "./MatrixFlowViewer";
import {
  buildMatrixFlowLayout,
  buildMatrixFlowRenderSegments,
  buildMatrixFlowTextSegments,
  changedCellKeys,
  collectMatrixFlowReferences,
  determinantEffect,
  edgeLabelLatex,
  formatOperationDescription,
  formatOperationLatex,
  linearFlowSequence,
  operationImpact,
} from "./layout";
import type { MatrixFlow, MatrixFlowV2, MatrixState, MatrixTransform } from "./types";

const state = (id: string, cells: string[][] = [["1", "0"], ["0", "1"]]): MatrixState => ({
  id,
  kind: "matrix",
  rows: cells.length,
  columns: cells[0]?.length ?? 0,
  cells,
  latex: "",
});

const firstLatex = String.raw`\begin{pmatrix}1&0\\0&1\end{pmatrix}`;
const secondLatex = String.raw`\begin{pmatrix}0&1\\1&0\end{pmatrix}`;
const sourceText = String.raw`前置说明：\[${firstLatex}\xrightarrow{R_1\leftrightarrow R_2}${secondLatex}\]后置说明。`;
const sourceStart = sourceText.indexOf(firstLatex);
const sourceEnd = sourceText.lastIndexOf(secondLatex) + secondLatex.length;

function stateAt(id: string, cells: string[][], latex: string, text = sourceText): MatrixState {
  const start = text.indexOf(latex);
  return { ...state(id, cells), latex, source_span: { start, end: start + latex.length } };
}

const flow = (review: MatrixFlow["review"]["status"] = "approved"): MatrixFlow => ({
  schema_version: 1,
  id: "flow-1",
  owner: {
    global_id: "node-1",
    source_block_key: "1",
    field: "statement",
    source_span: { start: sourceStart, end: sourceEnd },
  },
  source: {
    kind: "ocr",
    document_hash: "not-for-display",
    evidence_ids: ["secret-crop-path"],
  },
  nodes: [
    stateAt("a", [["1", "0"], ["0", "1"]], firstLatex),
    stateAt("b", [["0", "1"], ["1", "0"]], secondLatex),
  ],
  edges: [{
    id: "edge-1",
    from: "a",
    to: "b",
    operations: [{ type: "row_swap", first: 1, second: 2 }],
    provenance: "inferred",
    verification_status: "verified",
  }],
  verification: {
    status: "verified",
    diagnostics: [{
      code: "internal-code",
      message: "可展示的验证说明",
      details: { private_path: "never-render-this-path" },
    }],
  },
  review: { status: review, revision: 1 },
});

const namedMatrixLatex = String.raw`\begin{pmatrix}1&2\\3&4\end{pmatrix}`;
const namedDefinitionExcerpt = String.raw`$A=${namedMatrixLatex}$`;
const namedStatement = String.raw`设 ${namedDefinitionExcerpt}。此后使用 $A$，并把 A 作为记号。组合式 $A^2$、$A^{-1}$、$AB$、$Ax=b$ 不预览。`;
const namedProof = String.raw`由 $A$ 可知结论。`;

function exactSpan(text: string, excerpt: string, from = 0) {
  const start = text.indexOf(excerpt, from);
  if (start < 0) throw new Error(`Missing fixture excerpt: ${excerpt}`);
  return { start, end: start + excerpt.length };
}

function namedMatrixFlow(review: MatrixFlow["review"]["status"] = "approved"): MatrixFlowV2 {
  const definitionSpan = exactSpan(namedStatement, namedDefinitionExcerpt);
  const matrixSpan = exactSpan(namedStatement, namedMatrixLatex);
  const mathReferenceSpan = exactSpan(namedStatement, "$A$", definitionSpan.end);
  const textReferenceSpan = exactSpan(namedStatement, "A", mathReferenceSpan.end);
  const proofReferenceSpan = exactSpan(namedProof, "$A$");
  return {
    schema_version: 2,
    role: "named_matrix",
    id: "named-flow-a",
    owner: {
      global_id: "node-1",
      source_block_key: "1",
      field: "statement",
      source_span: definitionSpan,
      source_excerpt: namedDefinitionExcerpt,
    },
    source: { kind: "markdown", evidence_ids: [] },
    nodes: [{
      id: "named-state-a",
      kind: "matrix",
      rows: 2,
      columns: 2,
      cells: [["1", "2"], ["3", "4"]],
      latex: namedMatrixLatex,
      source_span: matrixSpan,
    }],
    edges: [],
    bindings: [{
      id: "binding-a",
      symbol_latex: "A",
      state_id: "named-state-a",
      definition: {
        field: "statement",
        source_span: definitionSpan,
        source_excerpt: namedDefinitionExcerpt,
      },
      references: [
        { id: "reference-a-math", field: "statement", source_span: mathReferenceSpan, source_excerpt: "$A$", context: "math" },
        { id: "reference-a-text", field: "statement", source_span: textReferenceSpan, source_excerpt: "A", context: "text" },
        { id: "reference-a-proof", field: "proof", source_span: proofReferenceSpan, source_excerpt: "$A$", context: "math" },
      ],
    }],
    verification: { status: "verified", diagnostics: [] },
    review: { status: review, revision: 1 },
  };
}

describe("MatrixFlow layout", () => {
  it("creates stable levels for a branch and merge", () => {
    const value = flow();
    value.nodes = [state("a"), state("b"), state("c"), state("d")];
    value.edges = [
      { ...value.edges[0], id: "ab", from: "a", to: "b" },
      { ...value.edges[0], id: "ac", from: "a", to: "c" },
      { ...value.edges[0], id: "bd", from: "b", to: "d" },
      { ...value.edges[0], id: "cd", from: "c", to: "d" },
    ];

    expect(buildMatrixFlowLayout(value).levels).toEqual([["a"], ["b", "c"], ["d"]]);
  });

  it("keeps cyclic nodes visible and reports the invalid structure", () => {
    const value = flow();
    value.edges.push({ ...value.edges[0], id: "edge-2", from: "b", to: "a" });

    const layout = buildMatrixFlowLayout(value);
    expect(layout.hasCycle).toBe(true);
    expect(layout.levels.flat()).toEqual(["a", "b"]);
  });

  it("recognizes a linear sequence without duplicating states", () => {
    const value = flow();
    expect(linearFlowSequence(value)?.nodes.map(node => node.id)).toEqual(["a", "b"]);
    expect(linearFlowSequence(value)?.edges).toHaveLength(1);
  });
});

describe("MatrixFlow matrix delimiters", () => {
  it("uses round brackets by default and preserves explicit source delimiters", () => {
    const matrixState = (latex: string, kind: MatrixState["kind"] = "matrix") => ({
      ...state("delimiter"),
      kind,
      latex,
    });

    expect(matrixFlowDelimiterKind(matrixState(String.raw`\begin{matrix}1&0\\0&1\end{matrix}`))).toBe("round");
    expect(matrixFlowDelimiterKind(matrixState(String.raw`\begin{array}{cc}1&0\\0&1\end{array}`))).toBe("round");
    expect(matrixFlowDelimiterKind(matrixState(String.raw`\begin{pmatrix}1&0\\0&1\end{pmatrix}`))).toBe("round");
    expect(matrixFlowDelimiterKind(matrixState(String.raw`\begin{bmatrix}1&0\\0&1\end{bmatrix}`))).toBe("square");
    expect(matrixFlowDelimiterKind(matrixState(String.raw`\begin{Bmatrix}1&0\\0&1\end{Bmatrix}`))).toBe("brace");
    expect(matrixFlowDelimiterKind(matrixState(String.raw`\begin{vmatrix}1&0\\0&1\end{vmatrix}`))).toBe("bar");
    expect(matrixFlowDelimiterKind(matrixState(String.raw`\begin{Vmatrix}1&0\\0&1\end{Vmatrix}`))).toBe("double-bar");
    expect(matrixFlowDelimiterKind(matrixState("", "determinant"))).toBe("bar");
  });

  it("recognizes explicit delimiters around an array when they are retained", () => {
    expect(matrixFlowDelimiterKind({ kind: "matrix", latex: String.raw`\left[\begin{array}{cc}1&0\\0&1\end{array}\right]` })).toBe("square");
    expect(matrixFlowDelimiterKind({ kind: "matrix", latex: String.raw`\left\{\begin{array}{cc}1&0\\0&1\end{array}\right\}` })).toBe("brace");
    expect(matrixFlowDelimiterKind({ kind: "matrix", latex: String.raw`\left|\begin{array}{cc}1&0\\0&1\end{array}\right|` })).toBe("bar");
    expect(matrixFlowDelimiterKind({ kind: "matrix", latex: String.raw`\left\Vert\begin{array}{cc}1&0\\0&1\end{array}\right\Vert` })).toBe("double-bar");
  });

  it("adds the resolved delimiter class to rendered matrix cards", () => {
    const html = renderToStaticMarkup(
      <MatrixFlowText text={sourceText} flows={[flow()]} field="statement" audience="author" layoutMode="vertical" />,
    );

    expect((html.match(/delimiter-round/g) ?? []).length).toBe(2);
  });
});

describe("MatrixFlow v2 named matrix references", () => {
  it("keeps named zero-edge flows in prose and renders only declared reference triggers", () => {
    const named = namedMatrixFlow();
    const renderSegments = buildMatrixFlowRenderSegments(namedStatement, [named], "statement", "author");
    expect(renderSegments).toHaveLength(1);
    expect(renderSegments[0]).toMatchObject({ type: "text", text: namedStatement, source_span: { start: 0, end: namedStatement.length } });

    const references = collectMatrixFlowReferences(namedStatement, [named], "statement", "author", renderSegments);
    expect(references.map(reference => reference.reference.id)).toEqual(["reference-a-math", "reference-a-text"]);

    const html = renderToStaticMarkup(
      <MatrixFlowText text={namedStatement} flows={[named]} field="statement" audience="author" />,
    );
    expect((html.match(/data-mf-matrix-reference=/g) ?? []).length).toBe(2);
    expect(html).toContain("role=\"button\"");
    expect(html).toContain("tabindex=\"0\"");
    expect(html).not.toContain("矩阵变换 · 0 步");
    expect(html).not.toContain("mf-flow-inline");
  });

  it("shares one named matrix binding between statement and proof fields", () => {
    const named = namedMatrixFlow();
    expect(collectMatrixFlowReferences(namedStatement, [named], "statement", "author")).toHaveLength(2);
    const proofReferences = collectMatrixFlowReferences(namedProof, [named], "proof", "author");
    expect(proofReferences).toHaveLength(1);
    expect(proofReferences[0].state.id).toBe("named-state-a");
  });

  it("keeps v2 transformations inline while preserving v1 behavior", () => {
    const v2Transformation: MatrixFlowV2 = {
      ...flow(),
      schema_version: 2,
      role: "transformation",
      bindings: [],
    };
    expect(buildMatrixFlowTextSegments(sourceText, [flow()], "statement", "author").map(segment => segment.type)).toEqual(["text", "flow", "text"]);
    expect(buildMatrixFlowTextSegments(sourceText, [v2Transformation], "statement", "author").map(segment => segment.type)).toEqual(["text", "flow", "text"]);
  });

  it("rejects composite formulas even when malformed sidecar data marks them as references", () => {
    const named = namedMatrixFlow();
    const composites = ["$A^2$", "$A^{-1}$", "$AB$", "$Ax=b$"];
    named.bindings[0].references = composites.map((excerpt, index) => ({
      id: `composite-${index}`,
      field: "statement" as const,
      source_span: exactSpan(namedStatement, excerpt),
      source_excerpt: excerpt,
      context: "math" as const,
    }));
    expect(collectMatrixFlowReferences(namedStatement, [named], "statement", "author")).toEqual([]);
  });

  it("fails closed for stale spans, unknown states, invalid matrices, overlaps, and hidden review data", () => {
    const stale = namedMatrixFlow();
    stale.bindings[0].references = [{
      ...stale.bindings[0].references[0],
      source_excerpt: "$B$",
    }];
    expect(collectMatrixFlowReferences(namedStatement, [stale], "statement", "author")).toEqual([]);

    const unknown = namedMatrixFlow();
    unknown.bindings[0].state_id = "missing-state";
    expect(collectMatrixFlowReferences(namedStatement, [unknown], "statement", "author")).toEqual([]);

    const invalid = namedMatrixFlow();
    invalid.nodes[0] = { ...invalid.nodes[0], rows: 3 };
    expect(collectMatrixFlowReferences(namedStatement, [invalid], "statement", "author")).toEqual([]);

    const overlapping = namedMatrixFlow();
    overlapping.bindings[0].references = [
      overlapping.bindings[0].references[0],
      { ...overlapping.bindings[0].references[0], id: "reference-a-duplicate" },
    ];
    expect(collectMatrixFlowReferences(namedStatement, [overlapping], "statement", "author")).toEqual([]);

    const definitionBlocked = namedMatrixFlow();
    const mathReference = definitionBlocked.bindings[0].references[0];
    definitionBlocked.bindings[0].definition = {
      field: "statement",
      source_span: { start: 0, end: mathReference.source_span.end },
      source_excerpt: namedStatement.slice(0, mathReference.source_span.end),
    };
    expect(collectMatrixFlowReferences(namedStatement, [definitionBlocked], "statement", "author")).toHaveLength(1);
    expect(collectMatrixFlowReferences(namedStatement, [namedMatrixFlow("pending")], "statement", "student")).toEqual([]);
  });

  it("reuses the matrix body without state-card labels in preview content", () => {
    const html = renderToStaticMarkup(<MatrixStateMatrix state={namedMatrixFlow().nodes[0]} />);
    expect(html).toContain("mf-matrix-shell");
    expect(html).toContain("delimiter-round");
    expect(html).not.toContain("mf-state-head");
    expect(html).not.toContain("状态 1");
    expect(html).not.toContain("A =");
  });
});

describe("MatrixFlow operation presentation", () => {
  it("formats every supported elementary-operation shape", () => {
    expect(formatOperationLatex({ type: "row_swap", first: 1, second: 2 })).toContain("leftrightarrow");
    expect(formatOperationLatex({ type: "col_scale", target: 2, factor: "3" })).toBe("C_{2} \\leftarrow 3C_{2}");
    expect(formatOperationLatex({ type: "row_add", target: 2, source: 1, coefficient: "-1" })).toBe("R_{2} \\leftarrow R_{2} - R_{1}");
    expect(edgeLabelLatex(undefined, [])).toContain("未标注变换");
  });

  it("creates reader-friendly descriptions for row and column operations", () => {
    expect(formatOperationDescription({ type: "row_swap", first: 2, second: 3 })).toBe("交换第 2 行和第 3 行");
    expect(formatOperationDescription({ type: "col_scale", target: 2, factor: "3" })).toContain("第 2 列整体乘以");
    expect(formatOperationDescription({ type: "row_add", target: 2, source: 1, coefficient: "-(2)" })).toContain("减去第 1 行的 $2$ 倍");
    expect(formatOperationDescription({ type: "col_add", target: 3, source: 1, coefficient: "1/2" })).toContain("加上第 1 列的 $1/2$ 倍");
  });

  it("reports determinant effects and affected cells", () => {
    const determinant: MatrixFlow = {
      ...flow(),
      nodes: [
        { ...state("a"), kind: "determinant" },
        { ...state("b", [["0", "1"], ["1", "0"]]), kind: "determinant" },
      ],
    };
    const edge = determinant.edges[0];
    expect(determinantEffect(determinant, edge)).toBe("行列式变号");
    expect(Array.from(operationImpact(edge, determinant.nodes[0], determinant.nodes[1]).sourceCells)).toEqual(["1:1", "1:2", "2:1", "2:2"]);
    expect(Array.from(changedCellKeys(determinant.nodes[0], determinant.nodes[1]))).toEqual(["1:1", "1:2", "2:1", "2:2"]);
  });

  it("keeps popovers limited to the concrete operation and formula", () => {
    const content = matrixFlowOperationPopoverContent(flow().edges[0]);
    expect(content.description).toBe("交换第 1 行和第 2 行");
    expect(content.formula).toContain("leftrightarrow");
    expect(Object.keys(content)).toEqual(["description", "formula"]);
    expect(Object.values(content).join(" ")).not.toContain("根据前后矩阵推断");
    expect(Object.values(content).join(" ")).not.toContain("行列式值不变");
  });

  it("places popovers outside every matrix safety boundary", () => {
    const rect = (left: number, top: number, width: number, height: number) => ({
      top,
      left,
      right: left + width,
      bottom: top + height,
    });
    const overlapsWithClearance = (
      placement: { top: number; left: number },
      popover: { width: number; height: number },
      matrix: ReturnType<typeof rect>,
      clearance = 10,
    ) => {
      const right = placement.left + popover.width;
      const bottom = placement.top + popover.height;
      return placement.left < matrix.right + clearance
        && right > matrix.left - clearance
        && placement.top < matrix.bottom + clearance
        && bottom > matrix.top - clearance;
    };
    const cases = [
      {
        name: "horizontal row",
        vertical: false,
        anchor: rect(290, 200, 60, 40),
        matrices: [rect(80, 180, 200, 120), rect(380, 180, 200, 120)],
        popover: { width: 250, height: 80 },
        viewport: { width: 1000, height: 700 },
        side: "top",
      },
      {
        name: "branched row",
        vertical: false,
        anchor: rect(240, 200, 60, 40),
        matrices: [rect(60, 180, 150, 100), rect(350, 140, 150, 100), rect(350, 280, 150, 100)],
        popover: { width: 250, height: 80 },
        viewport: { width: 1000, height: 700 },
        side: "top",
      },
      {
        name: "vertical column",
        vertical: true,
        anchor: rect(560, 240, 60, 40),
        matrices: [rect(500, 100, 180, 120), rect(500, 300, 180, 120)],
        popover: { width: 250, height: 78 },
        viewport: { width: 1280, height: 720 },
        side: "right",
      },
      {
        name: "narrow viewport",
        vertical: true,
        anchor: rect(130, 270, 60, 40),
        matrices: [rect(80, 160, 160, 100), rect(80, 320, 160, 100)],
        popover: { width: 240, height: 72 },
        viewport: { width: 320, height: 640 },
        side: "top",
      },
      {
        name: "top viewport edge",
        vertical: false,
        anchor: rect(290, 70, 60, 40),
        matrices: [rect(80, 16, 200, 120), rect(380, 16, 200, 120)],
        popover: { width: 250, height: 80 },
        viewport: { width: 1000, height: 700 },
        side: "bottom",
      },
    ] as const;

    for (const testCase of cases) {
      const input = { ...testCase, matrixRects: [...testCase.matrices] };
      const placement = placeMatrixFlowPopover(input);
      const presentation = resolveMatrixFlowPopoverPresentation(input);
      expect(placement, testCase.name).not.toBeNull();
      expect(placement?.side, testCase.name).toBe(testCase.side);
      expect(presentation.mode, testCase.name).toBe("floating");
      expect(testCase.matrices.some(matrix => placement && overlapsWithClearance(placement, testCase.popover, matrix))).toBe(false);
    }
  });

  it("places a floating popover with browser DOMRect-like inputs", () => {
    const domRectLike = (left: number, top: number, width: number, height: number) => Object.defineProperties({}, {
      top: { value: top },
      right: { value: left + width },
      bottom: { value: top + height },
      left: { value: left },
    }) as { top: number; right: number; bottom: number; left: number };
    const input = {
      vertical: false,
      anchor: domRectLike(530.5625, 542.71875, 92.8203125, 33.21875),
      matrixRects: [
        domRectLike(355, 493.5, 175.5625, 131.65625),
        domRectLike(623.3828125, 493.5, 175.5625, 131.65625),
        domRectLike(892.9453125, 493.5, 175.5625, 131.65625),
      ],
      popover: { width: 250, height: 59.390625 },
      viewport: { width: 1280, height: 720 },
    };

    expect(Object.keys(input.matrixRects[0])).toEqual([]);
    expect(resolveMatrixFlowPopoverPresentation(input)).toMatchObject({
      mode: "floating",
      side: "top",
    });
  });

  it("expands the active transition when the screenshot viewport has no safe floating position", () => {
    const rect = (left: number, top: number, width: number, height: number) => ({
      top,
      left,
      right: left + width,
      bottom: top + height,
    });
    const input = {
      vertical: false,
      anchor: rect(340, 130, 147, 65),
      matrixRects: [
        rect(5, 30, 315, 263),
        rect(507, 30, 315, 263),
      ],
      popover: { width: 250, height: 80 },
      viewport: { width: 840, height: 307 },
    };

    expect(placeMatrixFlowPopover(input)).toBeNull();
    expect(resolveMatrixFlowPopoverPresentation(input)).toEqual({
      mode: "inline",
      transitionWidth: 270,
      transitionHeight: 134,
    });
  });

  it("positions named-matrix previews above, below, and inside narrow viewports", () => {
    expect(placeMatrixReferencePopover(
      { top: 180, right: 120, bottom: 200, left: 100 },
      { width: 100, height: 60 },
      { width: 400, height: 300 },
    )).toEqual({ side: "top", top: 112, left: 60 });

    expect(placeMatrixReferencePopover(
      { top: 8, right: 120, bottom: 28, left: 100 },
      { width: 100, height: 60 },
      { width: 400, height: 300 },
    )).toEqual({ side: "bottom", top: 36, left: 60 });

    const narrow = placeMatrixReferencePopover(
      { top: 120, right: 160, bottom: 140, left: 140 },
      { width: 280, height: 80 },
      { width: 300, height: 240 },
    );
    expect(narrow).toEqual({ side: "top", top: 32, left: 12 });
    expect(placeMatrixReferencePopover(
      { top: 120, right: 160, bottom: 140, left: 140 },
      { width: 0, height: 80 },
      { width: 300, height: 240 },
    )).toBeNull();
  });

});

describe("MatrixFlow inline source replacement", () => {
  it("splits prose around a flow and consumes display delimiters once", () => {
    const segments = buildMatrixFlowTextSegments(sourceText, [flow()], "statement", "author");
    expect(segments.map(segment => segment.type)).toEqual(["text", "flow", "text"]);
    expect(segments[0].text).toBe("前置说明：");
    expect(segments[2].text).toBe("后置说明。");
  });

  it("consumes delimiters around a factored matrix formula", () => {
    const factoredText = String.raw`前置 $$-1${firstLatex}\to${secondLatex}$$ 后置`;
    const factoredFlow = {
      ...flow(),
      owner: {
        ...flow().owner,
        source_span: {
          start: factoredText.indexOf(firstLatex),
          end: factoredText.lastIndexOf(secondLatex) + secondLatex.length,
        },
      },
      nodes: [
        { ...stateAt("a", [["1", "0"], ["0", "1"]], firstLatex, factoredText), outer_factor: "-1" },
        stateAt("b", [["0", "1"], ["1", "0"]], secondLatex, factoredText),
      ],
    };
    const segments = buildMatrixFlowTextSegments(factoredText, [factoredFlow], "statement", "author");
    expect(segments.map(segment => segment.type)).toEqual(["text", "flow", "text"]);
    expect(segments[0].text).toBe("前置 ");
    expect(segments[2].text).toBe(" 后置");
  });

  it("keeps the original source when a flow is hidden or cannot be anchored", () => {
    expect(buildMatrixFlowTextSegments(sourceText, [flow("pending")], "statement", "student")).toEqual([{ type: "text", text: sourceText }]);
    const invalid = { ...flow(), owner: { ...flow().owner, source_span: { start: -1, end: 2 } } };
    expect(buildMatrixFlowTextSegments(sourceText, [invalid], "statement", "author")).toEqual([{ type: "text", text: sourceText }]);
  });

  it("keeps the source intact when an owner excerpt no longer matches its span", () => {
    const stale = {
      ...flow(),
      owner: { ...flow().owner, source_excerpt: "a different canonical field" },
    };
    expect(buildMatrixFlowTextSegments(sourceText, [stale], "statement", "author")).toEqual([{ type: "text", text: sourceText }]);
  });

  it("requires an old flow owner interval to match the verified endpoint matrices", () => {
    const staleLegacy = {
      ...flow(),
      owner: { ...flow().owner, source_span: { start: sourceStart - 1, end: sourceEnd } },
    };
    expect(buildMatrixFlowTextSegments(sourceText, [staleLegacy], "statement", "author")).toEqual([{ type: "text", text: sourceText }]);
  });

  it("does not inline overlapping source spans", () => {
    const overlapping = { ...flow(), id: "flow-2" };
    expect(buildMatrixFlowTextSegments(sourceText, [flow(), overlapping], "statement", "author")).toEqual([{ type: "text", text: sourceText }]);
  });

  it("preserves prose and references, but falls back when prose is between matrices", () => {
    const proseText = String.raw`前言\[${firstLatex}\to 因此由定理~\ref{thm:rank} 可得 ${secondLatex}\]结语。`;
    const proseFlow: MatrixFlow = {
      ...flow(),
      owner: { ...flow().owner, source_span: { start: proseText.indexOf(firstLatex), end: proseText.lastIndexOf(secondLatex) + secondLatex.length } },
      nodes: [
        stateAt("a", [["1", "0"], ["0", "1"]], firstLatex, proseText),
        stateAt("b", [["0", "1"], ["1", "0"]], secondLatex, proseText),
      ],
    };
    expect(buildMatrixFlowTextSegments(proseText, [proseFlow], "statement", "author")).toEqual([{ type: "text", text: proseText }]);
  });

  it("uses valid per-matrix spans for repeated matrices and consumes aligned shells only", () => {
    const repeatedText = String.raw`开始\[\begin{aligned}&${firstLatex}\Rightarrow${firstLatex}.\end{aligned}\]结束`;
    const first = repeatedText.indexOf(firstLatex);
    const second = repeatedText.indexOf(firstLatex, first + firstLatex.length);
    const repeated: MatrixFlow = {
      ...flow(),
      owner: { ...flow().owner, source_span: { start: first, end: second + firstLatex.length } },
      nodes: [
        { ...state("a"), latex: firstLatex, source_span: { start: first, end: first + firstLatex.length } },
        { ...state("b"), latex: firstLatex, source_span: { start: second, end: second + firstLatex.length } },
      ],
    };
    const segments = buildMatrixFlowTextSegments(repeatedText, [repeated], "statement", "author");
    expect(segments.map(segment => segment.type)).toEqual(["text", "flow", "text", "text"]);
    expect(segments[0].text).toBe("开始");
    expect(segments[2].text).toBe(".");
    expect(segments[3].text).toBe("结束");
  });

  it("uses a unique exact string match when display normalization invalidates a state offset", () => {
    const normalized = {
      ...flow(),
      nodes: flow().nodes.map(node => ({ ...node, source_span: { start: 0, end: 1 } })),
    };
    expect(buildMatrixFlowTextSegments(sourceText, [normalized], "statement", "author").map(segment => segment.type)).toEqual(["text", "flow", "text"]);
  });

  it("relocates a canonical owner excerpt when surrounding TeX changed its offsets", () => {
    const shifted = {
      ...flow(),
      owner: {
        ...flow().owner,
        source_span: { start: sourceStart + 6, end: sourceEnd + 6 },
        source_excerpt: sourceText.slice(sourceStart, sourceEnd),
      },
    };

    expect(buildMatrixFlowTextSegments(sourceText, [shifted], "statement", "author").map(segment => segment.type)).toEqual(["text", "flow", "text"]);
  });

  it("recognizes every backend-compatible arrow without consuming surrounding prose", () => {
    const arrows = [String.raw`\to`, String.raw`\rightarrow`, String.raw`\longrightarrow`, String.raw`\Rightarrow`, String.raw`\xrightarrow{R_2\to R_2-R_1}`, "->", "=>"];
    const matrices = Array.from({ length: arrows.length + 1 }, (_, index) => String.raw`\begin{pmatrix}${index + 1}\end{pmatrix}`);
    const body = matrices.map((matrix, index) => `${matrix}${arrows[index] ?? ""}`).join("");
    const allArrowsText = String.raw`引言\[${body}\]结语`;
    const nodes = matrices.map((latex, index) => {
      const start = allArrowsText.indexOf(latex);
      return { ...state(`s-${index}`, [[String(index + 1)]]), latex, source_span: { start, end: start + latex.length } };
    });
    const allArrows: MatrixFlow = {
      ...flow(),
      owner: { ...flow().owner, source_span: { start: nodes[0].source_span!.start, end: nodes[nodes.length - 1].source_span!.end } },
      nodes,
      edges: nodes.slice(1).map((node, index) => ({ ...flow().edges[0], id: `arrow-${index}`, from: nodes[index].id, to: node.id })),
    };
    expect(buildMatrixFlowTextSegments(allArrowsText, [allArrows], "statement", "author").map(segment => segment.type)).toEqual(["text", "flow", "text"]);
  });

  it("renders the five-state Gaussian fixture inside aligned layout", () => {
    const matrices = [
      String.raw`\begin{array}{ccc|c}1&1&1&6\\2&-1&1&3\\1&2&-1&3\end{array}`,
      String.raw`\begin{array}{ccc|c}1&1&1&6\\0&-3&-1&-9\\1&2&-1&3\end{array}`,
      String.raw`\begin{array}{ccc|c}1&1&1&6\\0&-3&-1&-9\\0&1&-2&-3\end{array}`,
      String.raw`\begin{array}{ccc|c}1&1&1&6\\0&1&-2&-3\\0&-3&-1&-9\end{array}`,
      String.raw`\begin{array}{ccc|c}1&1&1&6\\0&1&-2&-3\\0&0&-7&-18\end{array}`,
    ];
    const gaussianText = String.raw`的增广矩阵按下列顺序化为阶梯形：\[
\begin{aligned}
 &${matrices[0]}
 \rightarrow
 ${matrices[1]}
 \xrightarrow{R_3\to R_3-R_1}
 ${matrices[2]}
 \\[1em]
 &{}
 \xrightarrow{R_2\leftrightarrow R_3}
 ${matrices[3]}
 \xrightarrow{R_3\to R_3+3R_2}
 ${matrices[4]}.
\end{aligned}
\]由引理~\ref{lem:row-solution}，每一步都保持解集。`;
    const nodes = matrices.map((latex, index) => {
      const start = gaussianText.indexOf(latex);
      return {
        ...state(`g-${index}`, latex.split(String.raw`\\`).map(row => row.replace(/^.*?\}/, "").replace(/\\end\{array\}$/, "").split("&"))),
        kind: "augmented" as const,
        latex,
        source_span: { start, end: start + latex.length },
        augmented_after_column: 3,
      };
    });
    const gaussianFlow: MatrixFlow = {
      ...flow(),
      id: "gaussian-flow",
      owner: {
        ...flow().owner,
        source_span: { start: nodes[0].source_span.start, end: nodes[4].source_span.end },
        source_excerpt: gaussianText.slice(nodes[0].source_span.start, nodes[4].source_span.end),
      },
      nodes,
      edges: nodes.slice(1).map((node, index) => ({
        ...flow().edges[0],
        id: `g-edge-${index}`,
        from: nodes[index].id,
        to: node.id,
      })),
    };

    const segments = buildMatrixFlowTextSegments(gaussianText, [gaussianFlow], "statement", "author");
    expect(segments.map(segment => segment.type)).toEqual(["text", "flow", "text", "text"]);
    expect(segments[0].text).toBe("的增广矩阵按下列顺序化为阶梯形：");
    expect(segments[2].text).toBe(".\n");
    expect(segments[3].text).toContain(String.raw`由引理~\ref{lem:row-solution}`);

    const html = renderToStaticMarkup(
      <MatrixFlowText text={gaussianText} flows={[gaussianFlow]} field="statement" audience="author" layoutMode="horizontal" />,
    );
    expect((html.match(/data-matrix-node=/g) ?? []).length).toBe(5);
    expect(html).toContain("矩阵变换 · 4 步");
    expect(html).not.toContain(String.raw`\begin{aligned}`);

    const legacyGaussianFlow: MatrixFlow = {
      ...gaussianFlow,
      owner: {
        ...gaussianFlow.owner,
        source_excerpt: undefined,
        source_span: {
          start: gaussianFlow.owner.source_span.start + 28,
          end: gaussianFlow.owner.source_span.end + 28,
        },
      },
      nodes: gaussianFlow.nodes.map(node => ({
        ...node,
        source_span: {
          start: node.source_span!.start + 28,
          end: node.source_span!.end + 28,
        },
      })),
    };
    const legacySegments = buildMatrixFlowTextSegments(gaussianText, [legacyGaussianFlow], "statement", "author");
    expect(legacySegments.map(segment => segment.type)).toEqual(["text", "flow", "text", "text"]);
    const legacyHtml = renderToStaticMarkup(
      <MatrixFlowText text={gaussianText} flows={[legacyGaussianFlow]} field="statement" audience="author" layoutMode="vertical" />,
    );
    expect((legacyHtml.match(/data-matrix-node=/g) ?? []).length).toBe(5);
    expect((legacyHtml.match(/mf-transition-trigger/g) ?? []).length).toBe(4);

    const inconsistentOffset = {
      ...legacyGaussianFlow,
      nodes: legacyGaussianFlow.nodes.map((node, index) => index === 2
        ? { ...node, source_span: { start: node.source_span!.start + 1, end: node.source_span!.end + 1 } }
        : node),
    };
    expect(buildMatrixFlowTextSegments(gaussianText, [inconsistentOffset], "statement", "author")).toEqual([{ type: "text", text: gaussianText }]);

    const mismatchedOwner = {
      ...legacyGaussianFlow,
      owner: {
        ...legacyGaussianFlow.owner,
        source_span: {
          start: legacyGaussianFlow.owner.source_span.start,
          end: legacyGaussianFlow.owner.source_span.end + 1,
        },
      },
    };
    expect(buildMatrixFlowTextSegments(gaussianText, [mismatchedOwner], "statement", "author")).toEqual([{ type: "text", text: gaussianText }]);
  });

  it("relocates the legacy determinant fixture when its stored end exceeds the current statement", () => {
    const determinantText = String.raw`只使用不改变行列式值的行倍加和列倍加，可以把行列式逐步化为上三角形式：

\[
 \begin{vmatrix}
 1&2&0\\
 0&3&1\\
 2&1&4
 \end{vmatrix}
 \xrightarrow{C_2\to C_2-2C_1}
 \begin{vmatrix}
 1&0&0\\
 0&3&1\\
 2&-3&4
 \end{vmatrix}
 \xrightarrow{R_3\to R_3-2R_1}
 \begin{vmatrix}
 1&0&0\\
 0&3&1\\
 0&-3&4
 \end{vmatrix}
 \Rightarrow
 \begin{vmatrix}
 1&0&0\\
 0&3&1\\
 0&0&5
 \end{vmatrix}.
\]
最后一条无标签箭头可唯一推断为 $R_3\to R_3+R_2$。因此原行列式等于 $1\cdot3\cdot5=15$。`;
    const determinants = [
      String.raw`\begin{vmatrix}
 1&2&0\\
 0&3&1\\
 2&1&4
 \end{vmatrix}`,
      String.raw`\begin{vmatrix}
 1&0&0\\
 0&3&1\\
 2&-3&4
 \end{vmatrix}`,
      String.raw`\begin{vmatrix}
 1&0&0\\
 0&3&1\\
 0&-3&4
 \end{vmatrix}`,
      String.raw`\begin{vmatrix}
 1&0&0\\
 0&3&1\\
 0&0&5
 \end{vmatrix}`,
    ];
    const cells = [
      [["1", "2", "0"], ["0", "3", "1"], ["2", "1", "4"]],
      [["1", "0", "0"], ["0", "3", "1"], ["2", "-3", "4"]],
      [["1", "0", "0"], ["0", "3", "1"], ["0", "-3", "4"]],
      [["1", "0", "0"], ["0", "3", "1"], ["0", "0", "5"]],
    ];
    const currentSpans = determinants.map(latex => {
      const start = determinantText.indexOf(latex);
      return { start, end: start + latex.length };
    });
    const legacyOffset = 85;
    const nodes: MatrixState[] = determinants.map((latex, index) => ({
      ...state(`d-${index}`, cells[index]),
      kind: "determinant",
      latex,
      source_span: {
        start: currentSpans[index].start + legacyOffset,
        end: currentSpans[index].end + legacyOffset,
      },
    }));
    const determinantFlow: MatrixFlow = {
      ...flow(),
      id: "legacy-determinant-flow",
      owner: {
        ...flow().owner,
        source_span: {
          start: currentSpans[0].start + legacyOffset,
          end: currentSpans[3].end + legacyOffset,
        },
      },
      nodes,
      edges: [
        {
          ...flow().edges[0],
          id: "det-edge-1",
          from: nodes[0].id,
          to: nodes[1].id,
          label: String.raw`C_2\to C_2-2C_1`,
          provenance: "observed",
          operations: [{ type: "col_add", target: 2, source: 1, coefficient: "-(2)" }],
        },
        {
          ...flow().edges[0],
          id: "det-edge-2",
          from: nodes[1].id,
          to: nodes[2].id,
          label: String.raw`R_3\to R_3-2R_1`,
          provenance: "observed",
          operations: [{ type: "row_add", target: 3, source: 1, coefficient: "-(2)" }],
        },
        {
          ...flow().edges[0],
          id: "det-edge-3",
          from: nodes[2].id,
          to: nodes[3].id,
          provenance: "inferred",
          operations: [{ type: "row_add", target: 3, source: 2, coefficient: "1" }],
        },
      ],
    };

    expect(determinantText).toHaveLength(410);
    expect(determinantFlow.owner.source_span).toEqual({ start: 126, end: 429 });
    const segments = buildMatrixFlowTextSegments(determinantText, [determinantFlow], "statement", "author");
    expect(segments.map(segment => segment.type)).toEqual(["text", "flow", "text", "text"]);
    expect(segments[0].text).toBe("只使用不改变行列式值的行倍加和列倍加，可以把行列式逐步化为上三角形式：\n\n");
    expect(segments[2].text).toBe(".\n");
    expect(segments[3].text).toContain(String.raw`因此原行列式等于 $1\cdot3\cdot5=15$`);

    const html = renderToStaticMarkup(
      <MatrixFlowText text={determinantText} flows={[determinantFlow]} field="statement" audience="author" layoutMode="vertical" />,
    );
    expect((html.match(/kind-determinant/g) ?? []).length).toBe(4);
    expect((html.match(/delimiter-bar/g) ?? []).length).toBe(4);
    expect((html.match(/mf-transition-trigger/g) ?? []).length).toBe(3);
    expect(html).toContain("矩阵变换 · 3 步");
    expect(html).not.toContain(String.raw`\begin{vmatrix}`);
  });

  it("rejects unsafe legacy determinant relocation variants", () => {
    const first = String.raw`\begin{vmatrix}1&2\\3&4\end{vmatrix}`;
    const second = String.raw`\begin{vmatrix}1&0\\0&4\end{vmatrix}`;
    const determinantText = String.raw`前文\[${first}\Rightarrow${second}.\]后文`;
    const firstStart = determinantText.indexOf(first);
    const secondStart = determinantText.indexOf(second);
    const offset = 50;
    const legacy: MatrixFlow = {
      ...flow(),
      id: "legacy-determinant-rejections",
      owner: {
        ...flow().owner,
        source_span: { start: firstStart + offset, end: secondStart + second.length + offset },
      },
      nodes: [
        { ...state("d-a", [["1", "2"], ["3", "4"]]), kind: "determinant", latex: first, source_span: { start: firstStart + offset, end: firstStart + first.length + offset } },
        { ...state("d-b", [["1", "0"], ["0", "4"]]), kind: "determinant", latex: second, source_span: { start: secondStart + offset, end: secondStart + second.length + offset } },
      ],
    };
    const fallback = [{ type: "text", text: determinantText }];

    expect(buildMatrixFlowTextSegments(determinantText, [{
      ...legacy,
      owner: { ...legacy.owner, source_span: { start: -1, end: legacy.owner.source_span.end } },
    }], "statement", "author")).toEqual(fallback);
    expect(buildMatrixFlowTextSegments(determinantText, [{
      ...legacy,
      nodes: legacy.nodes.map((node, index) => index === 0 ? { ...node, source_span: { start: 8, end: 8 } } : node),
    }], "statement", "author")).toEqual(fallback);
    expect(buildMatrixFlowTextSegments(determinantText, [{
      ...legacy,
      nodes: legacy.nodes.map((node, index) => index === 1
        ? { ...node, source_span: { start: node.source_span!.start + 1, end: node.source_span!.end + 1 } }
        : node),
    }], "statement", "author")).toEqual(fallback);
    expect(buildMatrixFlowTextSegments(determinantText, [{
      ...legacy,
      owner: { ...legacy.owner, source_span: { start: legacy.owner.source_span.start, end: legacy.owner.source_span.end + 1 } },
    }], "statement", "author")).toEqual(fallback);

    const proseText = String.raw`前文\[${first}\Rightarrow 因此 ${second}.\]后文`;
    expect(buildMatrixFlowTextSegments(proseText, [legacy], "statement", "author")).toEqual([{ type: "text", text: proseText }]);
    expect(buildMatrixFlowTextSegments(determinantText, [{
      ...legacy,
      owner: { ...legacy.owner, source_excerpt: String.raw`${first}\Rightarrow 因此 ${second}` },
    }], "statement", "author")).toEqual(fallback);
  });

  it("renders one inline timeline and keeps private evidence out of the markup", () => {
    const html = renderToStaticMarkup(
      <MatrixFlowText text={sourceText} flows={[flow("pending")]} field="statement" audience="author" />,
    );

    expect(html).toContain("矩阵变换 · 1 步");
    expect(html).toContain("mf-transition-trigger");
    expect(html).toContain("aria-expanded=\"false\"");
    expect(html).toContain("mf-state");
    expect(html).not.toContain("mf-status");
    expect(html).not.toContain("mf-review-state");
    expect(html).not.toContain("已核验");
    expect(html).not.toContain("待审核");
    expect(html).not.toContain("is-source");
    expect(html).not.toContain("is-target");
    expect(html).not.toContain("secret-crop-path");
    expect(html).not.toContain("never-render-this-path");
    expect(html).not.toContain("mf-diagnostics");
    expect(html).not.toContain("查看验证说明");
    expect(html).not.toContain("可展示的验证说明");
    expect((html.match(/data-matrix-node=/g) ?? []).length).toBe(2);
  });

  it("uses explicit vertical and horizontal layout modes instead of viewport rules", () => {
    const vertical = renderToStaticMarkup(<MatrixFlowText text={sourceText} flows={[flow()]} field="statement" audience="author" layoutMode="vertical" />);
    const horizontal = renderToStaticMarkup(<MatrixFlowText text={sourceText} flows={[flow()]} field="statement" audience="author" layoutMode="horizontal" />);
    expect(vertical).toContain("layout-vertical");
    expect(horizontal).toContain("layout-horizontal");
    expect(horizontal).toContain("mf-flow-viewport");
    expect(horizontal).not.toContain("data-state-count");
  });
});
describe("MatrixFlow production payload regressions", () => {
  function productionFlow(
    id: string,
    text: string,
    inputs: Array<{
      latex: string;
      cells: string[][];
      kind?: MatrixState["kind"];
      outerFactor?: string;
      augmentedAfterColumn?: number;
    }>,
    labels: string[],
  ): MatrixFlowV2 {
    let cursor = 0;
    const nodes = inputs.map((input, index) => {
      const source_span = exactSpan(text, input.latex, cursor);
      cursor = source_span.end;
      return {
        id: `${id}-state-${index}`,
        kind: input.kind ?? "matrix",
        rows: input.cells.length,
        columns: input.cells[0]?.length ?? 0,
        cells: input.cells,
        latex: input.latex,
        source_span,
        ...(input.outerFactor ? { outer_factor: input.outerFactor } : {}),
        ...(input.augmentedAfterColumn ? { augmented_after_column: input.augmentedAfterColumn } : {}),
      } satisfies MatrixState;
    });
    const ownerSpan = {
      start: nodes[0].source_span!.start,
      end: nodes[nodes.length - 1].source_span!.end,
    };
    return {
      schema_version: 2,
      role: "transformation",
      id,
      owner: {
        global_id: id,
        source_block_key: id,
        field: "statement",
        source_span: ownerSpan,
        source_excerpt: text.slice(ownerSpan.start, ownerSpan.end),
      },
      source: { kind: "markdown", evidence_ids: [] },
      nodes,
      edges: labels.map((label, index) => ({
        id: `${id}-edge-${index}`,
        from: nodes[index].id,
        to: nodes[index + 1].id,
        label,
        operations: [],
        provenance: "observed",
        verification_status: "verified",
      })),
      bindings: [],
      verification: { status: "verified", diagnostics: [] },
      review: { status: "pending", revision: 0 },
    };
  }

  const augmented = (latex: string, cells: string[][], divider: number) => ({
    latex,
    cells,
    kind: "augmented" as const,
    augmentedAfterColumn: divider,
  });

  it("renders the three production flows that previously fell back to raw KaTeX", () => {
    const gaussianMatrices = [
      String.raw`\left(\begin{array}{ccc|c}1&2&-1&2\\2&5&1&7\\-1&-1&2&-1\end{array}\right)`,
      String.raw`\left(\begin{array}{ccc|c}1&2&-1&2\\0&1&3&3\\-1&-1&2&-1\end{array}\right)`,
      String.raw`\left(\begin{array}{ccc|c}1&2&-1&2\\0&1&3&3\\0&1&1&1\end{array}\right)`,
      String.raw`\left(\begin{array}{ccc|c}1&2&-1&2\\0&1&3&3\\0&0&-2&-2\end{array}\right)`,
      String.raw`\left(\begin{array}{ccc|c}1&2&-1&2\\0&1&3&3\\0&0&1&1\end{array}\right)`,
    ];
    const gaussianLabels = [
      String.raw`R_2\to R_2-2R_1`,
      String.raw`R_3\to R_3+R_1`,
      String.raw`R_3\to R_3-R_2`,
      String.raw`R_3\to -\frac{1}{2}R_3`,
    ];
    const gaussianText = "考虑增广矩阵。连续消元过程为\n\\[\n\\begin{aligned}\n &"
      + gaussianMatrices[0] + "\n \\xrightarrow{" + gaussianLabels[0] + "}\n " + gaussianMatrices[1]
      + "\n \\\\\n &\\xrightarrow{" + gaussianLabels[1] + "}\n " + gaussianMatrices[2]
      + "\n \\xrightarrow{" + gaussianLabels[2] + "}\n " + gaussianMatrices[3]
      + "\n \\\\\n &\\xrightarrow { " + gaussianLabels[3] + " }\n " + gaussianMatrices[4]
      + ".\n\\end{aligned}\n\\]\n最后一行给出结果。";
    const gaussian = productionFlow(
      "production-gaussian",
      gaussianText,
      gaussianMatrices.map((latex, index) => augmented(latex, [
        ["1", "2", "-1", "2"],
        index === 0 ? ["2", "5", "1", "7"] : ["0", "1", "3", "3"],
        index < 2 ? ["-1", "-1", "2", "-1"] : index === 2 ? ["0", "1", "1", "1"] : index === 3 ? ["0", "0", "-2", "-2"] : ["0", "0", "1", "1"],
      ], 3)),
      gaussianLabels,
    );

    const backMatrices = [
      String.raw`\left(\begin{array}{cc|c}1&1&3\\2&-1&0\end{array}\right)`,
      String.raw`\left(\begin{array}{cc|c}1&1&3\\0&-3&-6\end{array}\right)`,
      String.raw`\left(\begin{array}{cc|c}1&1&3\\0&1&2\end{array}\right)`,
      String.raw`\left(\begin{array}{cc|c}1&0&1\\0&1&2\end{array}\right)`,
    ];
    const backLabels = [
      String.raw`R_2\to R_2-2R_1`,
      String.raw`R_2\to -\frac{1}{3}R_2`,
      String.raw`R_1\to R_1-R_2`,
    ];
    const backText = "由定理可将求解过程写成\n{\\small\n\\[\n "
      + backMatrices[0] + "\n \\xrightarrow{" + backLabels[0] + "}\n " + backMatrices[1]
      + "\n \\xrightarrow{" + backLabels[1] + "}\n " + backMatrices[2]
      + "\n \\xrightarrow{" + backLabels[2] + "}\n " + backMatrices[3]
      + ".\n\\]\n}\n因此方程组有唯一解。";
    const back = productionFlow(
      "production-back-substitution",
      backText,
      [
        augmented(backMatrices[0], [["1", "1", "3"], ["2", "-1", "0"]], 2),
        augmented(backMatrices[1], [["1", "1", "3"], ["0", "-3", "-6"]], 2),
        augmented(backMatrices[2], [["1", "1", "3"], ["0", "1", "2"]], 2),
        augmented(backMatrices[3], [["1", "0", "1"], ["0", "1", "2"]], 2),
      ],
      backLabels,
    );

    const determinants = [
      String.raw`\begin{vmatrix}2&1\\5&3\end{vmatrix}`,
      String.raw`\begin{vmatrix}5&3\\2&1\end{vmatrix}`,
    ];
    const determinantLabel = String.raw`R_1\leftrightarrow R_2`;
    const determinantText = "由性质可知，\n\\[\n 1" + determinants[0]
      + "\n \\xrightarrow{" + determinantLabel + "}\n -1" + determinants[1]
      + ".\n\\]\n前后表达式表示同一个数。";
    const determinant = productionFlow(
      "production-determinant-factor",
      determinantText,
      [
        { latex: determinants[0], cells: [["2", "1"], ["5", "3"]], kind: "determinant", outerFactor: "1" },
        { latex: determinants[1], cells: [["5", "3"], ["2", "1"]], kind: "determinant", outerFactor: "-1" },
      ],
      [determinantLabel],
    );

    for (const [text, value, states, edges] of [
      [gaussianText, gaussian, 5, 4],
      [backText, back, 4, 3],
      [determinantText, determinant, 2, 1],
    ] as const) {
      expect(buildMatrixFlowTextSegments(text, [value], "statement", "author").map(segment => segment.type))
        .toEqual(["text", "flow", "text", "text"]);
      const html = renderToStaticMarkup(
        <MatrixFlowText text={text} flows={[value]} field="statement" audience="author" />,
      );
      expect((html.match(/data-matrix-node=/g) ?? []).length).toBe(states);
      expect((html.match(/mf-transition-trigger/g) ?? []).length).toBe(edges);
      expect(html).not.toContain(String.raw`\begin{array}`);
      expect(html).not.toContain(String.raw`\begin{vmatrix}`);
    }

    const backSegments = buildMatrixFlowTextSegments(backText, [back], "statement", "author");
    expect(backSegments.map(segment => segment.type)).toEqual(["text", "flow", "text", "text"]);
    expect(backSegments[0].text).toBe("由定理可将求解过程写成\n");
    expect(backSegments[2].text).toBe(".\n");
    expect(backSegments[3].text).toBe("\n因此方程组有唯一解。");
    expect(backSegments.filter(segment => segment.type === "text").map(segment => segment.text).join(""))
      .not.toContain(String.raw`{\small`);
  });

  it("rejects incomplete or content-bearing small layout groups", () => {
    const first = String.raw`\begin{pmatrix}1&0\\0&1\end{pmatrix}`;
    const second = String.raw`\begin{pmatrix}0&1\\1&0\end{pmatrix}`;
    const body = first + String.raw`\to` + second;
    const cases = [
      "前文{\\small\n\\[" + body + "\\]后文",
      "前文\\[" + body + "\\]\n}\n后文",
      "前文{\\small 注释\n\\[" + body + "\\]\n}\n后文",
    ];

    for (const [index, text] of cases.entries()) {
      const value = productionFlow(
        `rejected-small-shell-${index}`,
        text,
        [{ latex: first, cells: [["1", "0"], ["0", "1"]] }, { latex: second, cells: [["0", "1"], ["1", "0"]] }],
        [String.raw`\to`],
      );
      expect(buildMatrixFlowTextSegments(text, [value], "statement", "author"))
        .toEqual([{ type: "text", text }]);
    }
  });

  it("supports all backend arrow forms while retaining strict rejection", () => {
    const first = String.raw`\begin{pmatrix}1&0\\0&1\end{pmatrix}`;
    const second = String.raw`\begin{pmatrix}0&1\\1&0\end{pmatrix}`;
    const supported = [
      String.raw`\xrightarrow{R_1\to -\frac{1}{2}R_1}`,
      String.raw`\overset{R_1\leftrightarrow R_2}{\to}`,
      String.raw`\stackrel{R_1\leftrightarrow R_2}{\longrightarrow}`,
      "→",
      "=>",
    ];
    for (const [index, arrow] of supported.entries()) {
      const text = "前文\\[" + first + arrow + second + "\\]后文";
      const value = productionFlow(
        `supported-arrow-${index}`,
        text,
        [{ latex: first, cells: [["1", "0"], ["0", "1"]] }, { latex: second, cells: [["0", "1"], ["1", "0"]] }],
        [arrow],
      );
      expect(buildMatrixFlowTextSegments(text, [value], "statement", "author").map(segment => segment.type))
        .toEqual(["text", "flow", "text"]);
    }

    for (const gap of [
      String.raw`\xrightarrow{R_1\to -\frac{1}{2}R_1`,
      String.raw`\to\Rightarrow`,
      String.raw`\to 因此 `,
    ]) {
      const text = "前文\\[" + first + gap + second + "\\]后文";
      const value = productionFlow(
        `rejected-arrow-${gap.length}`,
        text,
        [{ latex: first, cells: [["1", "0"], ["0", "1"]] }, { latex: second, cells: [["0", "1"], ["1", "0"]] }],
        [gap],
      );
      expect(buildMatrixFlowTextSegments(text, [value], "statement", "author"))
        .toEqual([{ type: "text", text }]);
    }
  });

  it("rejects an external factor that does not match the source", () => {
    const first = String.raw`\begin{vmatrix}1&0\\0&1\end{vmatrix}`;
    const second = String.raw`\begin{vmatrix}0&1\\1&0\end{vmatrix}`;
    const text = "前文\\[1" + first + String.raw`\to` + "-1" + second + "\\]后文";
    const value = productionFlow(
      "mismatched-factor",
      text,
      [
        { latex: first, cells: [["1", "0"], ["0", "1"]], kind: "determinant", outerFactor: "1" },
        { latex: second, cells: [["0", "1"], ["1", "0"]], kind: "determinant", outerFactor: "2" },
      ],
      [String.raw`\to`],
    );

    expect(buildMatrixFlowTextSegments(text, [value], "statement", "author"))
      .toEqual([{ type: "text", text }]);
  });
});