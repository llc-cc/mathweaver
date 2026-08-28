import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, ArrowDown, ArrowRight, GitBranch } from "lucide-react";
import { MathText } from "../math";
import type { LatexMacros } from "../math";
import {
  buildMatrixFlowLayout,
  buildMatrixFlowRenderSegments,
  changedCellKeys,
  collectMatrixFlowReferences,
  edgeLabelLatex,
  formatEdgeDescription,
  isRectangularMatrixState,
  linearFlowSequence,
  operationImpact,
} from "./layout";
import type { ResolvedMatrixFlowReference } from "./layout";
import type {
  MatrixFlow,
  MatrixFlowAudience,
  MatrixFlowField,
  MatrixFlowLayoutMode,
  MatrixState,
  MatrixTransform,
} from "./types";
import "./matrix-flow.css";

interface MatrixFlowTextProps {
  text: string;
  flows?: MatrixFlow[];
  field: MatrixFlowField;
  audience: MatrixFlowAudience;
  macros?: LatexMacros;
  className?: string;
  layoutMode?: MatrixFlowLayoutMode;
}

interface MatrixFlowInlineProps {
  flow: MatrixFlow;
  audience: MatrixFlowAudience;
  macros?: LatexMacros;
  layoutMode: MatrixFlowLayoutMode;
}

interface MatrixStateCardProps {
  state: MatrixState;
  index: number;
  macros?: LatexMacros;
  sourceCells?: Set<string>;
  changed?: Set<string>;
  mismatched?: Set<string>;
}

export type MatrixFlowDelimiterKind = "round" | "square" | "brace" | "bar" | "double-bar";

export function matrixFlowDelimiterKind(state: Pick<MatrixState, "kind" | "latex">): MatrixFlowDelimiterKind {
  const environment = state.latex.match(/\\begin\{(?<environment>matrix|pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix|array)\}/u)?.groups?.environment;
  if (environment === "bmatrix") return "square";
  if (environment === "Bmatrix") return "brace";
  if (environment === "vmatrix") return "bar";
  if (environment === "Vmatrix") return "double-bar";

  if (/\\(?:left|bigl|Bigl|biggl|Biggl)\s*(?:\\Vert|\\\|)/u.test(state.latex)) return "double-bar";
  if (/\\(?:left|bigl|Bigl|biggl|Biggl)\s*(?:\\vert|\|)/u.test(state.latex)) return "bar";
  if (/\\(?:left|bigl|Bigl|biggl|Biggl)\s*\[/u.test(state.latex)) return "square";
  if (/\\(?:left|bigl|Bigl|biggl|Biggl)\s*\\\{/u.test(state.latex)) return "brace";

  if (state.kind === "determinant") return "bar";
  return "round";
}

function kindLabel(state: MatrixState): string {
  if (state.kind === "determinant") return "行列式";
  if (state.kind === "augmented") return "增广矩阵";
  return "矩阵";
}

function mismatchCellKeys(flow: MatrixFlow, stateId: string): Set<string> {
  const keys = new Set<string>();
  const targetEdgeIds = new Set(flow.edges.filter(edge => edge.to === stateId).map(edge => edge.id));
  for (const diagnostic of flow.verification?.diagnostics ?? []) {
    if (!diagnostic.edge_id || !targetEdgeIds.has(diagnostic.edge_id)) continue;
    const cells = diagnostic.details?.mismatched_cells;
    if (!Array.isArray(cells)) continue;
    for (const cell of cells) {
      if (!cell || typeof cell !== "object") continue;
      const row = Number((cell as Record<string, unknown>).row);
      const column = Number((cell as Record<string, unknown>).column);
      if (Number.isInteger(row) && Number.isInteger(column)) keys.add(`${row}:${column}`);
    }
  }
  return keys;
}

interface MatrixStateMatrixProps {
  state: MatrixState;
  macros?: LatexMacros;
  sourceCells?: Set<string>;
  changed?: Set<string>;
  mismatched?: Set<string>;
}

export function MatrixStateMatrix({
  state,
  macros,
  sourceCells = new Set<string>(),
  changed = new Set<string>(),
  mismatched = new Set<string>(),
}: MatrixStateMatrixProps) {
  return (
    <div className="mf-matrix-shell">
      {state.outer_factor && (
        <span className="mf-outer-factor"><MathText text={`$${state.outer_factor}$`} macros={macros} /></span>
      )}
      <div className={`mf-matrix-frame kind-${state.kind} delimiter-${matrixFlowDelimiterKind(state)}`}>
        <div
          className="mf-matrix-grid"
          role="grid"
          aria-label={`${state.rows} 行 ${state.columns} 列${kindLabel(state)}`}
          style={{ gridTemplateColumns: `repeat(${state.columns}, minmax(26px, auto))` }}
        >
          {state.cells.flatMap((row, rowIndex) => row.map((cell, columnIndex) => {
            const key = `${rowIndex + 1}:${columnIndex + 1}`;
            const divider = state.kind === "augmented" && state.augmented_after_column === columnIndex + 1;
            const sourceImpact = sourceCells.has(key);
            const targetChanged = changed.has(key);
            const mismatch = mismatched.has(key);
            return (
              <span
                key={key}
                role="gridcell"
                className={`mf-cell${divider ? " augmented-divider" : ""}${sourceImpact ? " source-impact" : ""}${targetChanged ? " changed" : ""}${mismatch ? " mismatched" : ""}`}
              >
                <MathText text={`$${cell || "\\phantom{0}"}$`} macros={macros} />
              </span>
            );
          }))}
        </div>
      </div>
    </div>
  );
}

function MatrixStateCard({
  state,
  index,
  macros,
  sourceCells = new Set<string>(),
  changed = new Set<string>(),
  mismatched = new Set<string>(),
}: MatrixStateCardProps) {
  const valid = isRectangularMatrixState(state);
  return (
    <div className={`mf-state${valid ? "" : " is-invalid"}`} data-matrix-node={state.id}>
      <div className="mf-state-head">
        <span>状态 {index + 1}</span>
        {state.kind !== "matrix" && <small>{kindLabel(state)}</small>}
      </div>
      {valid ? (
        <MatrixStateMatrix state={state} macros={macros} sourceCells={sourceCells} changed={changed} mismatched={mismatched} />
      ) : (
        <div className="mf-state-invalid">
          <AlertTriangle size={14} aria-hidden="true" />
          <span>矩阵结构无法安全绘制</span>
          {state.latex && <MathText text={`$$${state.latex}$$`} macros={macros} />}
        </div>
      )}
    </div>
  );
}

function studentVerificationText(flow: MatrixFlow): string | null {
  if (flow.verification?.status === "verified") return "每一步变换已通过数学检查。";
  if (flow.verification?.status === "indeterminate") {
    return flow.review?.reason || "该流程已由教师确认，部分符号条件不在自动检查范围内。";
  }
  return null;
}

export interface MatrixFlowPopoverRect {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

export interface MatrixFlowPopoverPlacement {
  side: "top" | "bottom" | "left" | "right";
  top: number;
  left: number;
}

interface MatrixFlowPopoverPlacementInput {
  vertical: boolean;
  anchor: MatrixFlowPopoverRect;
  matrixRects: MatrixFlowPopoverRect[];
  popover: { width: number; height: number };
  viewport: { width: number; height: number };
  clearance?: number;
  viewportPadding?: number;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), Math.max(minimum, maximum));
}

function hasFiniteRect(rect: MatrixFlowPopoverRect): boolean {
  return [rect.top, rect.right, rect.bottom, rect.left].every(Number.isFinite)
    && rect.right >= rect.left
    && rect.bottom >= rect.top;
}

function intersectsMatrixClearance(
  candidate: MatrixFlowPopoverRect,
  matrix: MatrixFlowPopoverRect,
  clearance: number,
): boolean {
  return candidate.left < matrix.right + clearance
    && candidate.right > matrix.left - clearance
    && candidate.top < matrix.bottom + clearance
    && candidate.bottom > matrix.top - clearance;
}

export function placeMatrixFlowPopover({
  vertical,
  anchor,
  matrixRects,
  popover,
  viewport,
  clearance = 10,
  viewportPadding = 8,
}: MatrixFlowPopoverPlacementInput): MatrixFlowPopoverPlacement | null {
  if (!hasFiniteRect(anchor)
    || !matrixRects.length
    || !matrixRects.every(hasFiniteRect)
    || !Number.isFinite(popover.width)
    || !Number.isFinite(popover.height)
    || popover.width <= 0
    || popover.height <= 0
    || !Number.isFinite(viewport.width)
    || !Number.isFinite(viewport.height)) {
    return null;
  }

  const bounds = matrixRects.reduce<MatrixFlowPopoverRect>((result, rect) => ({
    top: Math.min(result.top, rect.top),
    right: Math.max(result.right, rect.right),
    bottom: Math.max(result.bottom, rect.bottom),
    left: Math.min(result.left, rect.left),
  }), {
    top: matrixRects[0].top,
    right: matrixRects[0].right,
    bottom: matrixRects[0].bottom,
    left: matrixRects[0].left,
  });
  const centerX = anchor.left + (anchor.right - anchor.left) / 2;
  const centerY = anchor.top + (anchor.bottom - anchor.top) / 2;
  const sides: MatrixFlowPopoverPlacement["side"][] = vertical
    ? ["right", "left", "top", "bottom"]
    : ["top", "bottom", "right", "left"];

  for (const side of sides) {
    let top: number;
    let left: number;
    if (side === "top" || side === "bottom") {
      left = clamp(centerX - popover.width / 2, viewportPadding, viewport.width - popover.width - viewportPadding);
      top = side === "top"
        ? bounds.top - clearance - popover.height
        : bounds.bottom + clearance;
    } else {
      top = clamp(centerY - popover.height / 2, viewportPadding, viewport.height - popover.height - viewportPadding);
      left = side === "left"
        ? bounds.left - clearance - popover.width
        : bounds.right + clearance;
    }

    const candidate = {
      top,
      left,
      right: left + popover.width,
      bottom: top + popover.height,
    };
    const insideViewport = candidate.left >= viewportPadding
      && candidate.right <= viewport.width - viewportPadding
      && candidate.top >= viewportPadding
      && candidate.bottom <= viewport.height - viewportPadding;
    if (insideViewport && !matrixRects.some(matrix => intersectsMatrixClearance(candidate, matrix, clearance))) {
      return { side, top, left };
    }
  }

  return null;
}

export type MatrixFlowPopoverPresentation =
  | ({ mode: "floating" } & MatrixFlowPopoverPlacement)
  | {
    mode: "inline";
    transitionWidth: number;
    transitionHeight: number;
  };

export function resolveMatrixFlowPopoverPresentation(
  input: MatrixFlowPopoverPlacementInput,
): MatrixFlowPopoverPresentation {
  const placement = placeMatrixFlowPopover(input);
  if (placement) return { mode: "floating", ...placement };

  const measuredWidth = Number.isFinite(input.popover.width) && input.popover.width > 0
    ? input.popover.width
    : 250;
  const measuredHeight = Number.isFinite(input.popover.height) && input.popover.height > 0
    ? input.popover.height
    : 80;
  return {
    mode: "inline",
    transitionWidth: Math.ceil(measuredWidth) + 20,
    transitionHeight: Math.ceil(measuredHeight) + 54,
  };
}

export interface MatrixReferencePopoverPlacement {
  side: "top" | "bottom";
  top: number;
  left: number;
}

export function placeMatrixReferencePopover(
  anchor: MatrixFlowPopoverRect,
  popover: { width: number; height: number },
  viewport: { width: number; height: number },
  gap = 8,
  viewportPadding = 12,
): MatrixReferencePopoverPlacement | null {
  if (!hasFiniteRect(anchor)
    || !Number.isFinite(popover.width)
    || !Number.isFinite(popover.height)
    || popover.width <= 0
    || popover.height <= 0
    || !Number.isFinite(viewport.width)
    || !Number.isFinite(viewport.height)
    || viewport.width <= 0
    || viewport.height <= 0) {
    return null;
  }

  const topSpace = anchor.top - viewportPadding - gap;
  const bottomSpace = viewport.height - viewportPadding - anchor.bottom - gap;
  const side: MatrixReferencePopoverPlacement["side"] = topSpace >= popover.height || topSpace >= bottomSpace
    ? "top"
    : "bottom";
  const left = clamp(
    anchor.left + (anchor.right - anchor.left - popover.width) / 2,
    viewportPadding,
    viewport.width - popover.width - viewportPadding,
  );
  const preferredTop = side === "top"
    ? anchor.top - gap - popover.height
    : anchor.bottom + gap;
  const top = clamp(preferredTop, viewportPadding, viewport.height - popover.height - viewportPadding);
  return { side, top, left };
}

interface ActiveMatrixReference {
  reference: ResolvedMatrixFlowReference;
  anchor: HTMLElement;
}

function MatrixReferencePopover({
  active,
  macros,
}: {
  active: ActiveMatrixReference;
  macros?: LatexMacros;
}) {
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const [placement, setPlacement] = useState<MatrixReferencePopoverPlacement | null>(null);
  const tooltipId = `mf-reference-preview-${active.reference.flowId}-${active.reference.reference.id}`.replace(/[^A-Za-z0-9_-]/g, "-");

  const updatePosition = useCallback(() => {
    const popover = popoverRef.current;
    if (!popover || !active.anchor.isConnected) return;
    setPlacement(placeMatrixReferencePopover(
      active.anchor.getBoundingClientRect(),
      { width: popover.offsetWidth, height: popover.offsetHeight },
      { width: window.innerWidth, height: window.innerHeight },
    ));
  }, [active]);

  useLayoutEffect(updatePosition, [updatePosition]);
  useEffect(() => {
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [updatePosition]);

  if (typeof document === "undefined") return null;
  const portalRoot = active.anchor.closest(".gs-root") ?? document.body;
  return createPortal(
    <div
      ref={popoverRef}
      id={tooltipId}
      className={`mf-reference-popover side-${placement?.side ?? "top"}`}
      role="tooltip"
      data-mf-reference-popover={active.reference.reference.id}
      style={{
        top: placement?.top ?? 0,
        left: placement?.left ?? 0,
        visibility: placement ? "visible" : "hidden",
      }}
    >
      <MatrixStateMatrix state={active.reference.state} macros={macros} />
    </div>,
    portalRoot,
  );
}

function MatrixReferenceText({
  text,
  start,
  references,
  macros,
  activeReferenceId,
  onActivate,
  onDeactivate,
}: {
  text: string;
  start: number;
  references: ResolvedMatrixFlowReference[];
  macros?: LatexMacros;
  activeReferenceId?: string;
  onActivate: (reference: ResolvedMatrixFlowReference, anchor: HTMLElement) => void;
  onDeactivate: () => void;
}) {
  if (!references.length) return <MathText text={text} macros={macros} />;
  const content = [];
  let cursor = start;
  for (const reference of references) {
    if (reference.span.start > cursor) {
      content.push(
        <MathText
          key={`text:${cursor}`}
          text={text.slice(cursor - start, reference.span.start - start)}
          macros={macros}
        />,
      );
    }
    const tooltipId = `mf-reference-preview-${reference.flowId}-${reference.reference.id}`.replace(/[^A-Za-z0-9_-]/g, "-");
    content.push(
      <span
        key={reference.reference.id}
        className={`mf-matrix-reference${activeReferenceId === reference.reference.id ? " is-active" : ""}`}
        role="button"
        tabIndex={0}
        aria-label={`查看矩阵 ${reference.symbolLatex}`}
        aria-describedby={activeReferenceId === reference.reference.id ? tooltipId : undefined}
        data-mf-matrix-reference={reference.reference.id}
        onMouseEnter={event => onActivate(reference, event.currentTarget)}
        onMouseLeave={onDeactivate}
        onFocus={event => onActivate(reference, event.currentTarget)}
        onBlur={onDeactivate}
        onKeyDown={event => {
          if (event.key !== "Escape") return;
          event.preventDefault();
          onDeactivate();
          event.currentTarget.blur();
        }}
      >
        <MathText text={reference.reference.source_excerpt} macros={macros} />
      </span>,
    );
    cursor = reference.span.end;
  }
  if (cursor < start + text.length) {
    content.push(<MathText key={`text:${cursor}`} text={text.slice(cursor - start)} macros={macros} />);
  }
  return <>{content}</>;
}

export function matrixFlowOperationPopoverContent(edge: MatrixTransform): { description: string; formula: string } {
  return {
    description: formatEdgeDescription(edge),
    formula: edgeLabelLatex(edge.label, edge.operations ?? []),
  };
}

function FlowTransition({
  flow,
  edge,
  index,
  macros,
  vertical,
  active,
  onMouseEnter,
  onMouseLeave,
  onPopoverLeave,
  onFocus,
  onBlur,
  onClick,
  onKeyDown,
}: {
  flow: MatrixFlow;
  edge: MatrixTransform;
  index: number;
  macros?: LatexMacros;
  vertical: boolean;
  active: boolean;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
  onPopoverLeave: () => void;
  onFocus: () => void;
  onBlur: () => void;
  onClick: () => void;
  onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
}) {
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const inlineLockedRef = useRef(false);
  const [presentation, setPresentation] = useState<MatrixFlowPopoverPresentation | null>(null);
  const { description, formula } = matrixFlowOperationPopoverContent(edge);
  const inlinePresentation = presentation?.mode === "inline" ? presentation : null;
  const floatingPresentation = presentation?.mode === "floating" ? presentation : null;
  const transitionStyle = inlinePresentation
    ? {
      minWidth: inlinePresentation.transitionWidth,
      minHeight: vertical ? inlinePresentation.transitionHeight : undefined,
    }
    : undefined;

  useEffect(() => {
    if (!active) {
      inlineLockedRef.current = false;
      setPresentation(null);
      return;
    }

    let frame: number | null = null;
    const updatePosition = () => {
      if (inlineLockedRef.current) return;
      const wrapper = wrapperRef.current;
      const popover = popoverRef.current;
      const root = wrapper?.closest<HTMLElement>(".mf-flow-inline");
      if (!wrapper || !popover || !root) return;
      const nextPresentation = resolveMatrixFlowPopoverPresentation({
        vertical,
        anchor: wrapper.getBoundingClientRect(),
        matrixRects: Array.from(root.querySelectorAll<HTMLElement>(".mf-state")).map(state => state.getBoundingClientRect()),
        popover: popover.getBoundingClientRect(),
        viewport: { width: window.innerWidth, height: window.innerHeight },
      });
      if (nextPresentation.mode === "inline") inlineLockedRef.current = true;
      setPresentation(nextPresentation);
    };
    const scheduleUpdate = () => {
      if (frame !== null) window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        frame = null;
        updatePosition();
      });
    };

    scheduleUpdate();
    if (inlineLockedRef.current) return;
    window.addEventListener("resize", scheduleUpdate);
    window.addEventListener("scroll", scheduleUpdate, true);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(scheduleUpdate);
    const wrapper = wrapperRef.current;
    const popover = popoverRef.current;
    const root = wrapper?.closest<HTMLElement>(".mf-flow-inline");
    if (observer) {
      if (wrapper) observer.observe(wrapper);
      if (popover) observer.observe(popover);
      if (root) {
        observer.observe(root);
        root.querySelectorAll<HTMLElement>(".mf-state").forEach(state => observer.observe(state));
      }
    }
    return () => {
      if (frame !== null) window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", scheduleUpdate);
      window.removeEventListener("scroll", scheduleUpdate, true);
      observer?.disconnect();
    };
  }, [active, vertical]);

  const renderPopover = (inline: boolean) => (
    <div
      ref={popoverRef}
      className={`mf-operation-popover ${inline ? "is-inline" : `side-${floatingPresentation?.side ?? (vertical ? "right" : "bottom")}`}`}
      role="tooltip"
      data-mf-popover={`${flow.id}:${edge.id}`}
      aria-hidden={!inline && !floatingPresentation}
      style={inline ? undefined : {
        top: floatingPresentation?.top ?? 0,
        left: floatingPresentation?.left ?? 0,
        visibility: floatingPresentation ? "visible" : "hidden",
      }}
      onMouseEnter={onMouseEnter}
      onMouseLeave={inline ? undefined : onPopoverLeave}
    >
      <div className="mf-operation-description"><MathText text={description} macros={macros} /></div>
      <div className="mf-operation-formula"><MathText text={`$${formula}$`} macros={macros} /></div>
    </div>
  );

  return (
    <div
      ref={wrapperRef}
      className={`mf-transition${active ? " is-active" : ""}${vertical ? " is-vertical" : ""}${inlinePresentation ? " has-inline-popover" : ""}`}
      style={transitionStyle}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      <button
        type="button"
        className="mf-transition-trigger"
        aria-label={`步骤 ${index + 1}：${description}`}
        aria-expanded={active}
        onFocus={onFocus}
        onBlur={onBlur}
        onClick={onClick}
        onKeyDown={onKeyDown}
      >
        {vertical ? <ArrowDown size={15} aria-hidden="true" /> : <ArrowRight size={15} aria-hidden="true" />}
        <span>步骤 {index + 1}</span>
      </button>
      {active && inlinePresentation && renderPopover(true)}
      {active && !inlinePresentation && typeof document !== "undefined" && createPortal(renderPopover(false), document.body)}
    </div>
  );
}

function MatrixFlowInline({ flow, audience, macros, layoutMode }: MatrixFlowInlineProps) {
  const rootRef = useRef<HTMLElement | null>(null);
  const [hoveredEdgeId, setHoveredEdgeId] = useState<string | null>(null);
  const [focusedEdgeId, setFocusedEdgeId] = useState<string | null>(null);
  const [clickedEdgeId, setClickedEdgeId] = useState<string | null>(null);
  const closeTimerRef = useRef<number | null>(null);
  const layout = useMemo(() => buildMatrixFlowLayout(flow), [flow]);
  const linear = useMemo(() => linearFlowSequence(flow), [flow]);
  const activeEdgeId = hoveredEdgeId ?? focusedEdgeId ?? clickedEdgeId;
  const activeEdge = flow.edges.find(edge => edge.id === activeEdgeId);
  const nodeById = useMemo(() => new Map(flow.nodes.map(node => [node.id, node])), [flow.nodes]);
  const activeSource = activeEdge ? nodeById.get(activeEdge.from) : undefined;
  const activeTarget = activeEdge ? nodeById.get(activeEdge.to) : undefined;
  const activeImpact = activeEdge && activeSource && activeTarget
    ? operationImpact(activeEdge, activeSource, activeTarget)
    : null;
  const studentText = audience === "student" ? studentVerificationText(flow) : null;
  const structuralWarning = layout.hasCycle || layout.invalidEdgeIds.length > 0 || layout.duplicateNodeIds.length > 0;

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const closeFromOutside = (event: PointerEvent) => {
      const target = event.target as Node;
      const insidePopover = target instanceof Element && target.closest(`[data-mf-popover^="${flow.id}:"]`);
      if (!root.contains(target) && !insidePopover) {
        if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
        setHoveredEdgeId(null);
        setFocusedEdgeId(null);
        setClickedEdgeId(null);
      }
    };
    document.addEventListener("pointerdown", closeFromOutside);
    return () => {
      document.removeEventListener("pointerdown", closeFromOutside);
      if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
    };
  }, [flow.id]);

  const cancelScheduledClose = () => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  };

  const scheduleClose = () => {
    cancelScheduledClose();
    closeTimerRef.current = window.setTimeout(() => {
      setHoveredEdgeId(null);
      closeTimerRef.current = null;
    }, 120);
  };

  const closeOnEscape = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      setHoveredEdgeId(null);
      setFocusedEdgeId(null);
      setClickedEdgeId(null);
    }
  };

  const edgeActive = (edge: MatrixTransform) => edge.id === activeEdgeId;
  const stateProps = (state: MatrixState, index: number) => {
    const targetChanged = activeEdge?.to === state.id ? activeImpact?.targetCells : undefined;
    const sourceCells = activeEdge?.from === state.id ? activeImpact?.sourceCells : undefined;
    return {
      state,
      index,
      macros,
      sourceCells,
      changed: targetChanged,
      mismatched: mismatchCellKeys(flow, state.id),
    };
  };

  const transitionProps = (edge: MatrixTransform, index: number, vertical: boolean) => ({
    flow,
    edge,
    index,
    macros,
    vertical,
    active: edgeActive(edge),
    onMouseEnter: () => {
      cancelScheduledClose();
      setHoveredEdgeId(edge.id);
    },
    onMouseLeave: scheduleClose,
    onPopoverLeave: scheduleClose,
    onFocus: () => setFocusedEdgeId(edge.id),
    onBlur: () => setFocusedEdgeId(null),
    onClick: () => {
      if (clickedEdgeId === edge.id) {
        setClickedEdgeId(null);
        setHoveredEdgeId(null);
        setFocusedEdgeId(null);
      } else {
        setClickedEdgeId(edge.id);
      }
    },
    onKeyDown: closeOnEscape,
  });

  return (
    <figure
      ref={rootRef}
      className={`mf-flow-inline layout-${layoutMode}${linear ? " is-linear" : " is-branching"}`}
      onMouseEnter={cancelScheduledClose}
      onMouseLeave={scheduleClose}
      onKeyDown={closeOnEscape}
    >
      <figcaption className="mf-inline-head">
        <span><GitBranch size={13} aria-hidden="true" />矩阵变换 · {flow.edges.length} 步</span>
      </figcaption>

      <div className="mf-flow-viewport">
        {linear ? (
          <div className="mf-linear-sequence">
            {linear.nodes.map((state, index) => (
              <div className="mf-linear-item" key={state.id}>
                <MatrixStateCard {...stateProps(state, index)} />
                {linear.edges[index] && <FlowTransition {...transitionProps(linear.edges[index], index, layoutMode === "vertical")} />}
              </div>
            ))}
          </div>
        ) : (
          <div className="mf-branch-sequence">
            {layout.levels.map((level, levelIndex) => (
              <div className="mf-branch-level-group" key={`${levelIndex}:${level.join("|")}`}>
                <div className="mf-branch-level">
                  {level.map(id => {
                    const state = nodeById.get(id);
                    if (!state) return null;
                    return <MatrixStateCard key={id} {...stateProps(state, flow.nodes.indexOf(state))} />;
                  })}
                </div>
                {levelIndex < layout.levels.length - 1 && (
                  <div className="mf-branch-transitions">
                    {flow.edges
                      .map((edge, edgeIndex) => ({ edge, edgeIndex }))
                      .filter(({ edge }) => level.includes(edge.from))
                      .map(({ edge, edgeIndex }) => (
                        <FlowTransition key={edge.id} {...transitionProps(edge, edgeIndex, layoutMode === "vertical")} />
                      ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {studentText && <p className="mf-student-note">{studentText}</p>}
      {structuralWarning && (
        <p className="mf-structure-warning"><AlertTriangle size={13} aria-hidden="true" />流程结构不完整，已按可读取顺序展示。</p>
      )}
    </figure>
  );
}

export function MatrixFlowText({ text, flows, field, audience, macros, className = "", layoutMode = "vertical" }: MatrixFlowTextProps) {
  const segments = useMemo(
    () => buildMatrixFlowRenderSegments(text, flows, field, audience),
    [audience, field, flows, text],
  );
  const references = useMemo(
    () => collectMatrixFlowReferences(text, flows, field, audience, segments),
    [audience, field, flows, segments, text],
  );
  const [active, setActive] = useState<ActiveMatrixReference | null>(null);

  useEffect(() => {
    setActive(null);
  }, [audience, field, flows, text]);

  return (
    <div className={`mf-text ${className}`.trim()}>
      {segments.map((segment, index) => {
        if (segment.type === "flow" && segment.flow) {
          return <MatrixFlowInline key={segment.flow.id} flow={segment.flow} audience={audience} macros={macros} layoutMode={layoutMode} />;
        }
        if (!segment.source_span) {
          return <MathText key={`text:${index}`} text={segment.text ?? ""} macros={macros} />;
        }
        const segmentReferences = references.filter(reference => (
          reference.span.start >= segment.source_span!.start && reference.span.end <= segment.source_span!.end
        ));
        return (
          <MatrixReferenceText
            key={`text:${segment.source_span.start}:${segment.source_span.end}`}
            text={segment.text ?? ""}
            start={segment.source_span.start}
            references={segmentReferences}
            macros={macros}
            activeReferenceId={active?.reference.reference.id}
            onActivate={(reference, anchor) => setActive({ reference, anchor })}
            onDeactivate={() => setActive(null)}
          />
        );
      })}
      {active && <MatrixReferencePopover active={active} macros={macros} />}
    </div>
  );
}
