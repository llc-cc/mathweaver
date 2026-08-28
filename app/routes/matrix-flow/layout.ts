import type {
  ElementaryOperation,
  MatrixFlow,
  MatrixFlowAudience,
  MatrixFlowBinding,
  MatrixFlowField,
  MatrixFlowReference,
  MatrixFlowV2,
  MatrixState,
  MatrixTransform,
} from "./types";

export interface MatrixFlowLayout {
  levels: string[][];
  invalidEdgeIds: string[];
  hasCycle: boolean;
  duplicateNodeIds: string[];
}

export interface MatrixFlowTextSegment {
  type: "text" | "flow";
  text?: string;
  flow?: MatrixFlow;
}

export interface MatrixFlowRenderSegment extends MatrixFlowTextSegment {
  source_span?: FlowSourceSpan;
}

export interface ResolvedMatrixFlowReference {
  flowId: string;
  bindingId: string;
  symbolLatex: string;
  state: MatrixState;
  reference: MatrixFlowReference;
  span: FlowSourceSpan;
}

export interface MatrixFlowImpact {
  sourceCells: Set<string>;
  targetCells: Set<string>;
}

export interface LinearFlowSequence {
  nodes: MatrixState[];
  edges: MatrixTransform[];
}

export interface FlowSourceSpan {
  start: number;
  end: number;
}

export function buildMatrixFlowLayout(flow: MatrixFlow): MatrixFlowLayout {
  const order = new Map<string, number>();
  const duplicateNodeIds: string[] = [];
  flow.nodes.forEach((node, index) => {
    if (order.has(node.id)) duplicateNodeIds.push(node.id);
    else order.set(node.id, index);
  });

  const indegree = new Map(Array.from(order.keys(), id => [id, 0]));
  const outgoing = new Map(Array.from(order.keys(), id => [id, [] as string[]]));
  const invalidEdgeIds: string[] = [];

  for (const edge of flow.edges) {
    if (!order.has(edge.from) || !order.has(edge.to)) {
      invalidEdgeIds.push(edge.id);
      continue;
    }
    outgoing.get(edge.from)!.push(edge.to);
    indegree.set(edge.to, (indegree.get(edge.to) ?? 0) + 1);
  }

  const sortIds = (ids: string[]) => ids.sort((left, right) => (
    (order.get(left) ?? Number.MAX_SAFE_INTEGER) - (order.get(right) ?? Number.MAX_SAFE_INTEGER)
      || left.localeCompare(right)
  ));

  let frontier = sortIds(Array.from(indegree.entries())
    .filter(([, count]) => count === 0)
    .map(([id]) => id));
  const visited = new Set<string>();
  const levels: string[][] = [];

  while (frontier.length > 0) {
    const level = frontier.filter(id => !visited.has(id));
    if (level.length === 0) break;
    levels.push(level);
    const next = new Set<string>();
    for (const id of level) {
      visited.add(id);
      for (const target of outgoing.get(id) ?? []) {
        indegree.set(target, (indegree.get(target) ?? 1) - 1);
        if (indegree.get(target) === 0) next.add(target);
      }
    }
    frontier = sortIds(Array.from(next));
  }

  const remaining = sortIds(Array.from(order.keys()).filter(id => !visited.has(id)));
  if (remaining.length > 0) levels.push(remaining);

  return {
    levels,
    invalidEdgeIds,
    hasCycle: remaining.length > 0,
    duplicateNodeIds,
  };
}

function visibleToAudience(flow: MatrixFlow, audience: MatrixFlowAudience): boolean {
  if (audience === "student") return flow.review?.status === "approved";
  return flow.review?.status !== "dismissed";
}

export function visibleMatrixFlows(
  flows: MatrixFlow[] | undefined,
  field: MatrixFlowField,
  audience: MatrixFlowAudience,
): MatrixFlow[] {
  return (flows ?? []).filter(flow => {
    if (!flow || flow.owner?.field !== field) return false;
    if (flow.schema_version === 2 && flow.role !== "transformation") return false;
    if (flow.schema_version !== 1 && flow.schema_version !== 2) return false;
    return visibleToAudience(flow, audience);
  });
}

function axisName(type: string): "R" | "C" {
  return type.startsWith("col_") ? "C" : "R";
}

function scalar(value: unknown, fallback = "?"): string {
  const text = String(value ?? "").trim();
  return text || fallback;
}

export function formatOperationLatex(operation: ElementaryOperation): string {
  const axis = axisName(operation.type);
  if (operation.type.endsWith("_swap")) {
    return `${axis}_{${scalar(operation.first)}} \\leftrightarrow ${axis}_{${scalar(operation.second)}}`;
  }
  if (operation.type.endsWith("_scale")) {
    const target = scalar(operation.target);
    return `${axis}_{${target}} \\leftarrow ${scalar(operation.factor)}${axis}_{${target}}`;
  }
  if (operation.type.endsWith("_add")) {
    const target = scalar(operation.target);
    const source = scalar(operation.source);
    const coefficient = scalar(operation.coefficient);
    const term = coefficient === "1"
      ? `+ ${axis}_{${source}}`
      : coefficient === "-1"
        ? `- ${axis}_{${source}}`
        : coefficient.startsWith("-")
          ? `- ${coefficient.slice(1)}${axis}_{${source}}`
          : `+ ${coefficient}${axis}_{${source}}`;
    return `${axis}_{${target}} \\leftarrow ${axis}_{${target}} ${term}`;
  }
  return "\\text{未识别的变换}";
}

export function edgeLabelLatex(label: string | undefined, operations: ElementaryOperation[]): string {
  if (label?.trim()) return label.trim();
  if (!operations.length) return "\\text{未标注变换}";
  return operations.map(formatOperationLatex).join("\\;，\\;");
}

function normalizeCoefficient(value: unknown): string {
  return String(value ?? "")
    .trim()
    .replace(/^\+/, "")
    .replace(/^\-\((.*)\)$/, "-$1");
}

function coefficientMath(value: unknown): string {
  return normalizeCoefficient(value) || "?";
}

function operationAxisLabel(type: string): string {
  return type.startsWith("col_") ? "列" : "行";
}

function operationIndex(value: unknown): string {
  return scalar(value);
}

export function formatOperationDescription(operation: ElementaryOperation): string {
  const axis = operationAxisLabel(operation.type);
  const target = operationIndex(operation.target);
  const source = operationIndex(operation.source);

  if (operation.type.endsWith("_swap")) {
    return `交换第 ${operationIndex(operation.first)} ${axis}和第 ${operationIndex(operation.second)} ${axis}`;
  }
  if (operation.type.endsWith("_scale")) {
    return `第 ${target} ${axis}整体乘以 $${coefficientMath(operation.factor)}$`;
  }
  if (operation.type.endsWith("_add")) {
    const coefficient = normalizeCoefficient(operation.coefficient);
    if (coefficient === "1") return `第 ${target} ${axis}加上第 ${source} ${axis}`;
    if (coefficient === "-1") return `第 ${target} ${axis}减去第 ${source} ${axis}`;
    if (coefficient.startsWith("-")) {
      return `第 ${target} ${axis}减去第 ${source} ${axis}的 $${coefficient.slice(1)}$ 倍`;
    }
    return `第 ${target} ${axis}加上第 ${source} ${axis}的 $${coefficientMath(coefficient)}$ 倍`;
  }
  return "执行当前变换标注";
}

export function formatEdgeDescription(edge: MatrixTransform): string {
  if (!edge.operations?.length) return "无法从结构化操作生成说明，请查看原始标注。";
  return edge.operations.map(formatOperationDescription).join("；");
}

export function determinantEffect(flow: MatrixFlow, edge: MatrixTransform): string | null {
  if (flow.nodes.find(node => node.id === edge.from)?.kind !== "determinant") return null;
  const effects = edge.operations.map(operation => {
    if (operation.type.endsWith("_swap")) return "行列式变号";
    if (operation.type.endsWith("_scale")) return `行列式乘以 $${coefficientMath(operation.factor)}$`;
    if (operation.type.endsWith("_add")) return "行列式值不变";
    return null;
  }).filter((value): value is string => Boolean(value));
  return effects.length ? effects.join("；") : null;
}

export function changedCellKeys(source: MatrixState | undefined, target: MatrixState): Set<string> {
  const changed = new Set<string>();
  for (let row = 0; row < target.cells.length; row += 1) {
    for (let column = 0; column < target.cells[row].length; column += 1) {
      if (source?.cells[row]?.[column] !== target.cells[row][column]) {
        changed.add(`${row + 1}:${column + 1}`);
      }
    }
  }
  return changed;
}

export function operationImpact(
  edge: MatrixTransform,
  source: MatrixState | undefined,
  target: MatrixState,
): MatrixFlowImpact {
  const sourceCells = new Set<string>();
  const targetCells = changedCellKeys(source, target);
  if (!source) return { sourceCells, targetCells };
  for (const operation of edge.operations ?? []) {
    const axis = axisName(operation.type);
    const indexes = operation.type.endsWith("_swap")
      ? [operation.first, operation.second]
      : operation.type.endsWith("_add")
        ? [operation.target, operation.source]
        : [operation.target];
    for (const value of indexes) {
      const index = Number(value);
      if (!Number.isInteger(index) || index < 1) continue;
      if (axis === "R") {
        for (let column = 1; column <= source.columns; column += 1) sourceCells.add(`${index}:${column}`);
      } else {
        for (let row = 1; row <= source.rows; row += 1) sourceCells.add(`${row}:${index}`);
      }
    }
  }
  return { sourceCells, targetCells };
}

function validStoredSpan(span?: FlowSourceSpan | null): span is FlowSourceSpan {
  return Boolean(
    span
    && Number.isInteger(span.start)
    && Number.isInteger(span.end)
    && span.start >= 0
    && span.end > span.start
  );
}

export function isRectangularMatrixState(state: MatrixState): boolean {
  return Number.isInteger(state.rows)
    && Number.isInteger(state.columns)
    && state.rows > 0
    && state.columns > 0
    && Array.isArray(state.cells)
    && state.cells.length === state.rows
    && state.cells.every(row => Array.isArray(row) && row.length === state.columns);
}

function validSpan(text: string, span?: FlowSourceSpan | null): span is FlowSourceSpan {
  return validStoredSpan(span) && span.end <= text.length;
}

interface AnchoredState {
  state: MatrixState;
  span: FlowSourceSpan;
}

interface LayoutShellStart {
  start: number;
  display: boolean;
  environment: boolean;
  layoutGroup: "small" | null;
}

interface LayoutShellEnd {
  end: number;
  display: boolean;
  environment: boolean;
  layoutGroup: "small" | null;
  preservedText: string;
}

interface FlowReplacement {
  flow: MatrixFlow;
  span: FlowSourceSpan;
  preservedText: string;
}

const ARROW_START_RE = /\\(?:xrightarrow|overset|stackrel|longrightarrow|rightarrow|to|Rightarrow)|[→⇒⟶⟹]|=>|->/gu;
const SIMPLE_ARROW_RE = /^(?:\\(?:longrightarrow|rightarrow|to|Rightarrow)|[→⇒⟶⟹]|=>|->)$/u;

function uniqueLiteralSpan(text: string, literal: string, minimumStart: number): FlowSourceSpan | null {
  if (!literal) return null;
  const first = text.indexOf(literal, minimumStart);
  if (first < 0 || text.indexOf(literal, first + literal.length) >= 0) return null;
  return { start: first, end: first + literal.length };
}

function stateSpans(text: string, flow: MatrixFlow): AnchoredState[] | null {
  const anchored: AnchoredState[] = [];
  let minimumStart = 0;
  for (const state of flow.nodes) {
    const provided = state.source_span;
    const exactProvided = validSpan(text, provided) && text.slice(provided.start, provided.end) === state.latex;
    const span = exactProvided ? provided : uniqueLiteralSpan(text, state.latex, minimumStart);
    if (!span || span.start < minimumStart) return null;
    anchored.push({ state, span });
    minimumStart = span.end;
  }
  return anchored;
}

function isInvisibleLayout(text: string): boolean {
  let remaining = text;
  let previous = "";
  while (remaining !== previous) {
    previous = remaining;
    remaining = remaining
      .replace(/\s+/gu, "")
      .replace(/&(?:\{\})?/gu, "")
      .replace(/\\\\(?:\[[^\]]*\])?/gu, "")
      .replace(/\\(?:quad|qquad|enspace|displaystyle|textstyle|,|;|!|:)/gu, "")
      .replace(/\\hspace\{[^{}]*\}/gu, "")
      .replace(/\{\}/gu, "");
  }
  return remaining.length === 0;
}

function balancedGroup(text: string, start: number): FlowSourceSpan | null {
  if (text[start] !== "{") return null;
  let depth = 0;
  let escaped = false;
  for (let index = start; index < text.length; index += 1) {
    const character = text[index];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (character === "\\") {
      escaped = true;
      continue;
    }
    if (character === "{") depth += 1;
    else if (character === "}") {
      depth -= 1;
      if (depth === 0) return { start, end: index + 1 };
    }
  }
  return null;
}

function skipWhitespace(text: string, start: number): number {
  let cursor = start;
  while (cursor < text.length && /\s/u.test(text[cursor])) cursor += 1;
  return cursor;
}

function arrowSpans(gap: string): FlowSourceSpan[] {
  const spans: FlowSourceSpan[] = [];
  ARROW_START_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = ARROW_START_RE.exec(gap)) !== null) {
    const start = match.index;
    let end = ARROW_START_RE.lastIndex;
    const command = match[0];
    if (command === "\\xrightarrow") {
      const group = balancedGroup(gap, skipWhitespace(gap, end));
      if (!group) continue;
      end = group.end;
    } else if (command === "\\overset" || command === "\\stackrel") {
      const label = balancedGroup(gap, skipWhitespace(gap, end));
      if (!label) continue;
      const arrow = balancedGroup(gap, skipWhitespace(gap, label.end));
      if (!arrow || !SIMPLE_ARROW_RE.test(gap.slice(arrow.start + 1, arrow.end - 1).trim())) continue;
      end = arrow.end;
    }
    spans.push({ start, end });
    ARROW_START_RE.lastIndex = end;
  }
  return spans;
}

function arrowInGap(gap: string): FlowSourceSpan | null {
  const matches = arrowSpans(gap);
  if (matches.length !== 1) return null;
  const { start, end } = matches[0];
  return isInvisibleLayout(gap.slice(0, start)) && isInvisibleLayout(gap.slice(end))
    ? { start, end }
    : null;
}

function factorStart(text: string, start: number, state: MatrixState): number {
  const factor = state.outer_factor?.trim();
  if (!factor) return start;
  const before = text.slice(0, start);
  const suffix = before.match(/\s*(?:\\cdot|\*)?\s*$/u)?.[0] ?? "";
  const factorStartIndex = before.length - suffix.length - factor.length;
  if (factorStartIndex < 0 || before.slice(factorStartIndex, factorStartIndex + factor.length) !== factor) return start;
  const boundary = factorStartIndex === 0 ? "" : before[factorStartIndex - 1];
  return !boundary || /[\s$([{=:+\-*/]/u.test(boundary) ? factorStartIndex : start;
}

function consumeOpeningShell(text: string, start: number): LayoutShellStart {
  let cursor = start;
  let display = false;
  let environment = false;
  let layoutGroup: "small" | null = null;
  let changed = true;
  while (changed) {
    changed = false;
    if (display) break;
    const before = text.slice(0, cursor);
    const whitespace = before.match(/\s+$/u)?.[0];
    if (whitespace) {
      const beforeWhitespace = before.slice(0, -whitespace.length);
      if (!/(?:&(?:\{\})?|\\begin\{(?:aligned|array)\}(?:\[[^\]]*\])?(?:\{[^{}]*\})?|\\\[|\\\(|\$\$|\$)$/u.test(beforeWhitespace)) break;
      cursor -= whitespace.length;
      changed = true;
      continue;
    }
    const align = before.match(/&(?:\{\})?$/u)?.[0];
    if (align) {
      cursor -= align.length;
      changed = true;
      continue;
    }
    const environmentMatch = before.match(/\\begin\{(?:aligned|array)\}(?:\[[^\]]*\])?(?:\{[^{}]*\})?$/u)?.[0];
    if (environmentMatch) {
      cursor -= environmentMatch.length;
      environment = true;
      changed = true;
      continue;
    }
    const delimiter = before.match(/(?:\\\[|\\\(|\$\$|\$)$/u)?.[0];
    if (delimiter) {
      cursor -= delimiter.length;
      display = true;
      changed = true;
    }
  }
  if (display || environment) {
    const layoutMatch = text.slice(0, cursor).match(/\{\\small\s*$/u)?.[0];
    if (layoutMatch) {
      cursor -= layoutMatch.length;
      layoutGroup = "small";
    }
  }
  return { start: cursor, display, environment, layoutGroup };
}

function consumeClosingShell(text: string, end: number): LayoutShellEnd {
  let cursor = end;
  let display = false;
  let environment = false;
  let layoutGroup: "small" | null = null;
  let preservedText = "";
  const leadingWhitespace = text.slice(cursor).match(/^\s+/u)?.[0] ?? "";
  cursor += leadingWhitespace.length;
  const punctuation = text.slice(cursor).match(/^[,.;:，。；：]/u)?.[0] ?? "";
  if (punctuation) cursor += punctuation.length;
  const afterPunctuationWhitespace = text.slice(cursor).match(/^\s+/u)?.[0] ?? "";
  cursor += afterPunctuationWhitespace.length;
  const environmentMatch = text.slice(cursor).match(/^\\end\{(?:aligned|array)\}/u)?.[0];
  if (environmentMatch) {
    cursor += environmentMatch.length;
    environment = true;
    const whitespace = text.slice(cursor).match(/^\s+/u)?.[0] ?? "";
    cursor += whitespace.length;
  }
  const delimiter = text.slice(cursor).match(/^(?:\\\]|\\\)|\$\$|\$)/u)?.[0];
  if (delimiter) {
    cursor += delimiter.length;
    display = true;
  }
  if (display || environment) {
    const layoutWhitespace = text.slice(cursor).match(/^\s*/u)?.[0] ?? "";
    const layoutEnd = cursor + layoutWhitespace.length;
    if (text[layoutEnd] === "}") {
      cursor = layoutEnd + 1;
      layoutGroup = "small";
    }
  }
  if (punctuation && (environment || display)) preservedText = `${leadingWhitespace}${punctuation}${afterPunctuationWhitespace}`;
  if (!environment && !display) return { end, display: false, environment: false, layoutGroup: null, preservedText: "" };
  return { end: cursor, display, environment, layoutGroup, preservedText };
}

function flowReplacement(text: string, flow: MatrixFlow): FlowReplacement | null {
  const states = stateSpans(text, flow);
  if (!states || states.length < 2) return null;
  const providedOwner = flow.owner?.source_span;
  const excerpt = flow.owner?.source_excerpt;
  let owner: FlowSourceSpan | null | undefined;
  if (excerpt) {
    owner = validSpan(text, providedOwner) && text.slice(providedOwner.start, providedOwner.end) === excerpt
      ? providedOwner
      : uniqueLiteralSpan(text, excerpt, 0);
    if (!validSpan(text, owner)) return null;
  } else {
    if (!validStoredSpan(providedOwner)) return null;
    owner = providedOwner;
  }
  // Source spans are authored against the canonical raw node field.  Never
  // apply them to a transformed string: an offset mismatch could otherwise
  // remove a matrix prefix or leave its closing environment behind.
  if (excerpt && text.slice(owner.start, owner.end) !== excerpt) return null;
  // Legacy records predate source_excerpt.  Their only safe fallback is an
  // exact owner interval bounded by the first and last verified matrix.  If
  // all persisted matrix spans share one offset, relocate that old interval
  // by the same offset after verifying its original boundaries.
  if (!excerpt && (
    owner.start !== states[0].span.start || owner.end !== states[states.length - 1].span.end
  )) {
    const providedStates = flow.nodes.map(state => state.source_span);
    if (providedStates.some(span => !validStoredSpan(span))) return null;
    const offsets = states.map((anchored, index) => ({
      start: anchored.span.start - (providedStates[index] as FlowSourceSpan).start,
      end: anchored.span.end - (providedStates[index] as FlowSourceSpan).end,
    }));
    const firstOffset = offsets[0];
    if (!firstOffset || offsets.some(offset => offset.start !== firstOffset.start || offset.end !== firstOffset.end)) return null;
    const legacyStart = (providedStates[0] as FlowSourceSpan).start;
    const legacyEnd = (providedStates[providedStates.length - 1] as FlowSourceSpan).end;
    if (owner.start !== legacyStart || owner.end !== legacyEnd || firstOffset.start !== firstOffset.end) return null;
    const relocatedOwner = {
      start: owner.start + firstOffset.start,
      end: owner.end + firstOffset.start,
    };
    if (relocatedOwner.start !== states[0].span.start || relocatedOwner.end !== states[states.length - 1].span.end) return null;
    if (!validSpan(text, relocatedOwner)) return null;
    owner = relocatedOwner;
  }

  if (owner.start > states[0].span.start || owner.end < states[states.length - 1].span.end) return null;

  for (let index = 0; index < states.length - 1; index += 1) {
    const targetStart = factorStart(text, states[index + 1].span.start, states[index + 1].state);
    if (targetStart < states[index].span.end) return null;
    const gap = text.slice(states[index].span.end, targetStart);
    if (!arrowInGap(gap)) return null;
  }

  const matrixStart = factorStart(text, states[0].span.start, states[0].state);
  const opening = consumeOpeningShell(text, matrixStart);
  const closing = consumeClosingShell(text, states[states.length - 1].span.end);
  if (
    opening.display !== closing.display
    || opening.environment !== closing.environment
    || opening.layoutGroup !== closing.layoutGroup
  ) return null;
  return {
    flow,
    span: { start: opening.start, end: closing.end },
    preservedText: closing.preservedText,
  };
}

function nonOverlappingReplacements(text: string, flows: MatrixFlow[]): FlowReplacement[] {
  const candidates = flows
    .map(flow => flowReplacement(text, flow))
    .filter((value): value is FlowReplacement => Boolean(value))
    .sort((left, right) => left.span.start - right.span.start || left.span.end - right.span.end);
  return candidates.filter((candidate, index) => candidates.every((other, otherIndex) => (
    index === otherIndex
      || candidate.span.end <= other.span.start
      || other.span.end <= candidate.span.start
  )));
}

export function buildMatrixFlowRenderSegments(
  text: string,
  flows: MatrixFlow[] | undefined,
  field: MatrixFlowField,
  audience: MatrixFlowAudience,
): MatrixFlowRenderSegment[] {
  const visible = visibleMatrixFlows(flows, field, audience);
  const anchored = nonOverlappingReplacements(text, visible);
  if (!anchored.length) return [{ type: "text", text, source_span: { start: 0, end: text.length } }];

  const segments: MatrixFlowRenderSegment[] = [];
  let cursor = 0;
  for (const item of anchored) {
    if (item.span.start < cursor) continue;
    if (item.span.start > cursor) {
      segments.push({
        type: "text",
        text: text.slice(cursor, item.span.start),
        source_span: { start: cursor, end: item.span.start },
      });
    }
    segments.push({ type: "flow", flow: item.flow, source_span: item.span });
    if (item.preservedText) segments.push({ type: "text", text: item.preservedText });
    cursor = item.span.end;
  }
  if (cursor < text.length) {
    segments.push({ type: "text", text: text.slice(cursor), source_span: { start: cursor, end: text.length } });
  }
  return segments;
}

export function buildMatrixFlowTextSegments(
  text: string,
  flows: MatrixFlow[] | undefined,
  field: MatrixFlowField,
  audience: MatrixFlowAudience,
): MatrixFlowTextSegment[] {
  return buildMatrixFlowRenderSegments(text, flows, field, audience).map(({ type, text: segmentText, flow }) => ({
    type,
    ...(segmentText !== undefined ? { text: segmentText } : {}),
    ...(flow ? { flow } : {}),
  }));
}

function spansOverlap(left: FlowSourceSpan, right: FlowSourceSpan): boolean {
  return left.start < right.end && right.start < left.end;
}

function exactSpan(text: string, span: FlowSourceSpan | undefined, excerpt: string): span is FlowSourceSpan {
  return validSpan(text, span) && Boolean(excerpt) && text.slice(span.start, span.end) === excerpt;
}

function standaloneMathSymbol(excerpt: string, symbolLatex: string): boolean {
  const trimmed = excerpt.trim();
  let inner: string | null = null;
  if (trimmed.startsWith("$$") && trimmed.endsWith("$$") && trimmed.length >= 4) inner = trimmed.slice(2, -2);
  else if (trimmed.startsWith("\\[") && trimmed.endsWith("\\]") && trimmed.length >= 4) inner = trimmed.slice(2, -2);
  else if (trimmed.startsWith("\\(") && trimmed.endsWith("\\)") && trimmed.length >= 4) inner = trimmed.slice(2, -2);
  else if (trimmed.startsWith("$") && trimmed.endsWith("$") && trimmed.length >= 2) inner = trimmed.slice(1, -1);
  return inner !== null && inner.trim() === symbolLatex.trim();
}

function validReferenceExcerpt(reference: MatrixFlowReference, binding: MatrixFlowBinding): boolean {
  if (!binding.symbol_latex.trim()) return false;
  if (reference.context === "math") return standaloneMathSymbol(reference.source_excerpt, binding.symbol_latex);
  return reference.context === "text" && reference.source_excerpt === binding.symbol_latex;
}

export function collectMatrixFlowReferences(
  text: string,
  flows: MatrixFlow[] | undefined,
  field: MatrixFlowField,
  audience: MatrixFlowAudience,
  renderSegments: MatrixFlowRenderSegment[] = buildMatrixFlowRenderSegments(text, flows, field, audience),
): ResolvedMatrixFlowReference[] {
  const visibleV2 = (flows ?? []).filter((flow): flow is MatrixFlowV2 => (
    flow?.schema_version === 2 && visibleToAudience(flow, audience)
  ));
  const blockedSpans = renderSegments
    .filter(segment => segment.type === "flow" && segment.source_span)
    .map(segment => segment.source_span as FlowSourceSpan);

  for (const flow of visibleV2) {
    for (const state of flow.nodes) {
      if (flow.owner.field === field && exactSpan(text, state.source_span, state.latex)) {
        blockedSpans.push(state.source_span);
      }
    }
    for (const binding of flow.bindings ?? []) {
      const definition = binding.definition;
      if (definition?.field === field && exactSpan(text, definition.source_span, definition.source_excerpt)) {
        blockedSpans.push(definition.source_span);
      }
    }
  }

  const candidates: ResolvedMatrixFlowReference[] = [];
  for (const flow of visibleV2) {
    const stateById = new Map(flow.nodes.map(state => [state.id, state]));
    for (const binding of flow.bindings ?? []) {
      const state = stateById.get(binding.state_id);
      if (!state || !isRectangularMatrixState(state)) continue;
      for (const reference of binding.references ?? []) {
        if (reference.field !== field) continue;
        if (!exactSpan(text, reference.source_span, reference.source_excerpt)) continue;
        if (!validReferenceExcerpt(reference, binding)) continue;
        if (blockedSpans.some(span => spansOverlap(span, reference.source_span))) continue;
        candidates.push({
          flowId: flow.id,
          bindingId: binding.id,
          symbolLatex: binding.symbol_latex,
          state,
          reference,
          span: reference.source_span,
        });
      }
    }
  }

  candidates.sort((left, right) => left.span.start - right.span.start || left.span.end - right.span.end);
  return candidates.filter((candidate, index) => candidates.every((other, otherIndex) => (
    index === otherIndex || !spansOverlap(candidate.span, other.span)
  )));
}

export function linearFlowSequence(flow: MatrixFlow): LinearFlowSequence | null {
  if (flow.nodes.length < 2 || flow.edges.length !== flow.nodes.length - 1) return null;
  const incoming = new Map<string, MatrixTransform[]>();
  const outgoing = new Map<string, MatrixTransform[]>();
  for (const node of flow.nodes) {
    incoming.set(node.id, []);
    outgoing.set(node.id, []);
  }
  for (const edge of flow.edges) {
    incoming.get(edge.to)?.push(edge);
    outgoing.get(edge.from)?.push(edge);
  }
  const start = flow.nodes.find(node => (incoming.get(node.id)?.length ?? 0) === 0);
  if (!start || flow.nodes.some(node => (incoming.get(node.id)?.length ?? 0) > 1 || (outgoing.get(node.id)?.length ?? 0) > 1)) return null;

  const nodes: MatrixState[] = [];
  const edges: MatrixTransform[] = [];
  const seen = new Set<string>();
  let current: MatrixState | undefined = start;
  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    nodes.push(current);
    const nextEdge: MatrixTransform | undefined = outgoing.get(current.id)?.[0];
    if (!nextEdge) break;
    edges.push(nextEdge);
    const nextNode: MatrixState | undefined = flow.nodes.find(node => node.id === nextEdge.to);
    current = nextNode;
  }
  return nodes.length === flow.nodes.length && edges.length === flow.edges.length
    ? { nodes, edges }
    : null;
}
