import { useState, useEffect, useLayoutEffect, useRef, useMemo, useCallback } from "react";
import { Network } from "vis-network";
import { DataSet } from "vis-data";
import {
  Search, Settings2, Sun, Moon, Maximize2, Plus, Minus, Crosshair,
  PanelRightClose, PanelLeftClose, PanelLeft, X, Upload, Download, Minimize2,
  BookOpen, ArrowRight, Loader2, History, LayoutGrid, FileText, FileJson, CircleHelp,
} from "lucide-react";
import { MathText, SmartTitle } from "./math";
import type { LatexMacros } from "./math";
import { parseMdBlocks } from "./markdown";
import { HistoryPanel } from "./HistoryPanel";
import { ProofWorkspace } from "./ProofWorkspace";
import { PdfSourceViewer } from "./PdfSourceViewer";
import { nodeTypeLabel } from "./node-type-language";
import { apiUrl } from "~/api";
import { captureAuthRequestIdentity, isAuthRequestIdentityCurrent, protectedFetch } from "./auth";
import type { RestoredJob } from "~/context/jobs";
import type { GraphNode, GraphEdge, GraphResult, NodeLanguage, WorkspaceMode, LLMConfig } from "./home";
import {
  STUDIO_NODE_TYPES, studioStyle, studioColor, studioLabel,
  computeSalience, majorNodeSet, computeDepthsLocal,
  layoutReading, layoutSwimlane, layoutDag,
  buildAnchorIndex, neighborhood, classifyEdge, EDGE_KINDS,
  loadStudioSettings, saveStudioSettings, resolveTheme,
  type StudioSettings, type StudioLayout, type EdgeKind, type Pos,
} from "./studio-graph";
import { nodeStatementText, sourceStatementBlockRange } from "./source-matching";
import "./graphstudio.css";

const LAYOUTS: { key: StudioLayout; label: string }[] = [
  { key: "reading",  label: "阅读顺序" },
  { key: "swimlane", label: "类型泳道" },
  { key: "dag",      label: "依赖层次" },
  { key: "force",    label: "关系网络" },
];

export type GraphExportFormat = "html" | "json";

interface GraphStudioProps {
  workspaceMode: WorkspaceMode;
  result: GraphResult;
  filename: string;
  graphId: string;
  sourceMarkdown?: string;
  nodeLanguage: NodeLanguage;
  token?: string;
  llmConfig?: LLMConfig;
  onLoadHistory?: (result: GraphResult, filename: string, id: string) => void;
  onResumeHistory?: (job: RestoredJob) => void;
  onNodeSelectionChange?: (selected: boolean) => void;
  onReset: () => void;
  onShowApiGuide?: () => void;
  onExport: (format: GraphExportFormat) => void;
  exporting: boolean;
}

type PdfPeek = {
  node: GraphNode;
  page: number;
  searchTerms: string[];
  statementTerms: string[];
  url: string | null;
  status: "compiling" | "ready" | "failed" | "source";
  error?: string | null;
};

type PdfPeekSize = { width: number; height: number };
type PdfPeekResizeEdge = "top" | "right" | "bottom" | "left" | "top-left" | "top-right" | "bottom-left" | "bottom-right";

const SOURCE_STATEMENT_RE = /^(?:definition|theorem|lemma|proposition|corollary|example|exercise|remark|proof)\b|^(?:定义|定理|引理|命题|推论|例|习题|注|证明)/i;
const TEX_STATEMENT_RE = /\\begin\s*\{\s*(?:definition|theorem|lemma|proposition|corollary|example|exercise|remark|proof)\*?\s*\}/i;

function sourceBlockText(block: ReturnType<typeof parseMdBlocks>[number]): string {
  return "text" in block ? block.text.trim() : "";
}

function sourceStatementRange(blocks: ReturnType<typeof parseMdBlocks>, anchorIds: number[], sourceStatement?: string): [number, number] | null {
  if (sourceStatement?.trim()) return sourceStatementBlockRange(blocks, sourceStatement);

  const primary = anchorIds.find(index => blocks[index] && blocks[index].type !== "hr");
  if (primary === undefined) return null;

  let start = primary;
  let foundStatement = false;
  for (let index = primary; index >= 0; index--) {
    const block = blocks[index];
    const text = sourceBlockText(block);
    if (SOURCE_STATEMENT_RE.test(text) || TEX_STATEMENT_RE.test(text)) {
      start = index;
      foundStatement = true;
      break;
    }
    if (index !== primary && /^h[1-4]$/.test(block.type)) break;
  }
  if (!foundStatement) return [primary, primary];

  if (TEX_STATEMENT_RE.test(sourceBlockText(blocks[start]))) return [start, start];

  let end = start;
  for (let index = start + 1; index < blocks.length; index++) {
    const block = blocks[index];
    const text = sourceBlockText(block);
    if (/^h[1-4]$/.test(block.type) || SOURCE_STATEMENT_RE.test(text) || TEX_STATEMENT_RE.test(text)) break;
    end = index;
  }
  return [start, end];
}

function asText(v: unknown): string {
  if (typeof v === "string") return v;
  if (v == null) return "";
  if (Array.isArray(v)) return v.map(asText).filter(Boolean).join(", ");
  if (typeof v === "object") {
    const obj = v as Record<string, unknown>;
    for (const key of ["text", "statement", "content", "title"]) {
      const value = obj[key];
      if (typeof value === "string") return value;
      if (Array.isArray(value)) return value.map(asText).filter(Boolean).join(", ");
    }
  }
  return JSON.stringify(v);
}

// Returns the index at which `s` can be safely cut without leaving an
// unclosed math delimiter ($ / $$ / \( / \[ / \begin{}). Returns the full
// length if `s` is already balanced. Otherwise backs up to just before the
// offending (unclosed) delimiter so we drop the half-formula entirely.
function safeMathCut(s: string): number {
  // Track $-pairs (single & double). $$ counts as one open/close toggle.
  let i = 0;
  let dollarOpen = -1;        // start index of an open $/$$ run, or -1
  let dollarDouble = false;
  let firstUnbalanced = s.length;
  // Stack of open \( \[ and \begin{} starts.
  const envStack: number[] = [];
  while (i < s.length) {
    const c = s[i];
    if (c === "\\") {
      if (s.startsWith("\\(", i) || s.startsWith("\\[", i)) {
        if (dollarOpen < 0) envStack.push(i);
        i += 2; continue;
      }
      if (s.startsWith("\\)", i) || s.startsWith("\\]", i)) {
        if (dollarOpen < 0 && envStack.length) envStack.pop();
        i += 2; continue;
      }
      if (s.startsWith("\\begin", i)) { if (dollarOpen < 0) envStack.push(i); i += 6; continue; }
      if (s.startsWith("\\end", i)) { if (dollarOpen < 0 && envStack.length) envStack.pop(); i += 4; continue; }
      i += 2; continue;       // skip any other escaped char (e.g. \$)
    }
    if (c === "$") {
      const dbl = s[i + 1] === "$";
      if (dollarOpen < 0) { dollarOpen = i; dollarDouble = dbl; }
      else if (dollarDouble === dbl) { dollarOpen = -1; }
      else { /* mismatched run length — treat the open one as the cut point */ }
      i += dbl ? 2 : 1; continue;
    }
    i++;
  }
  if (dollarOpen >= 0) firstUnbalanced = Math.min(firstUnbalanced, dollarOpen);
  if (envStack.length) firstUnbalanced = Math.min(firstUnbalanced, envStack[0]);
  return firstUnbalanced;
}

function previewText(text: string, max = 260): string {
  const compact = text.replace(/\s+/g, " ").trim();
  if (compact.length <= max) return compact;
  let sliced = compact.slice(0, max - 1);
  // If the cut left a dangling/unclosed math delimiter, back up before it.
  const safe = safeMathCut(sliced);
  if (safe < sliced.length) sliced = sliced.slice(0, safe).trimEnd();
  return `${sliced}…`;
}

export default function GraphStudio({
  workspaceMode, result, filename, graphId, sourceMarkdown, nodeLanguage,
  token, llmConfig, onLoadHistory, onResumeHistory, onNodeSelectionChange,
  onReset, onShowApiGuide, onExport, exporting,
}: GraphStudioProps) {
  const [settings, setSettings] = useState<StudioSettings>(() => loadStudioSettings());
  const theme = resolveTheme(settings.theme);
  const lang = nodeLanguage;

  const canvasRef = useRef<HTMLDivElement>(null);
  const netRef = useRef<Network | null>(null);
  const nodesDS = useRef<DataSet<Record<string, unknown>> | null>(null);
  const edgesDS = useRef<DataSet<Record<string, unknown>> | null>(null);
  const readingRef = useRef<HTMLDivElement>(null);
  const railRef = useRef<HTMLDivElement>(null);
  const pdfSourceBodyRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const compactSearchInputRef = useRef<HTMLInputElement>(null);
  const exportMenuRef = useRef<HTMLDivElement>(null);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<number | null>(null);
  const [activeTypes, setActiveTypes] = useState<Set<string>>(
    () => new Set(result.nodes.map(node => node.node_type)),
  );
  const [searchOpen, setSearchOpen] = useState(false);
  const [compactSearchOpen, setCompactSearchOpen] = useState(false);
  const [compactLayoutOpen, setCompactLayoutOpen] = useState(false);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [railOpen, setRailOpen] = useState(true);
  const [panelTab, setPanelTab] = useState<"detail" | "reading">("detail");
  const [panelExpanded, setPanelExpanded] = useState(false);
  const [pdfPeek, setPdfPeek] = useState<PdfPeek | null>(null);
  const [pdfPeekPos, setPdfPeekPos] = useState({ left: 24, top: 24 });
  const [pdfPeekSize, setPdfPeekSize] = useState<PdfPeekSize>({ width: 920, height: 720 });
  const [pdfPeekWidthReady, setPdfPeekWidthReady] = useState(true);
  const [pdfLoadingId, setPdfLoadingId] = useState<number | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [tip, setTip] = useState<{ id: number; x: number; y: number } | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);
  const [tipPos, setTipPos] = useState<{ left: number; top: number } | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const latexMacros = result.latex_macros;

  useEffect(() => {
    onNodeSelectionChange?.(selectedId !== null);
    return () => onNodeSelectionChange?.(false);
  }, [onNodeSelectionChange, selectedId]);

  // Smart-position the hover tooltip so it never overflows the viewport.
  useLayoutEffect(() => {
    if (!tip) { setTipPos(null); return; }
    const el = tipRef.current;
    if (!el) return;
    const M = 8; // viewport margin
    const r = el.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    // Default: to the right / slightly above the cursor.
    let left = tip.x + 14;
    let top = tip.y - 10;
    // Flip to the cursor's left if it would overflow the right edge.
    if (left + r.width > vw - M) left = tip.x - 14 - r.width;
    // Flip upward if it would overflow the bottom edge.
    if (top + r.height > vh - M) top = tip.y - r.height + 10;
    // Clamp within the viewport with a small margin.
    left = Math.max(M, Math.min(left, vw - r.width - M));
    top = Math.max(M, Math.min(top, vh - r.height - M));
    setTipPos({ left, top });
  }, [tip]);

  const [panelWidth, setPanelWidth] = useState(360);
  const resizing = useRef(false);
  const resizeStartX = useRef(0);
  const resizeStartW = useRef(360);

  const getCanvasBounds = useCallback(() => {
    const canvas = canvasRef.current?.getBoundingClientRect();
    if (canvas && canvas.width > 0 && canvas.height > 0) {
      return {
        left: Math.max(8, canvas.left + 8),
        right: Math.min(window.innerWidth - 8, canvas.right - 8),
        top: Math.max(8, canvas.top + 8),
        bottom: Math.min(window.innerHeight - 8, canvas.bottom - 8),
      };
    }
    return { left: 8, right: window.innerWidth - 8, top: 60, bottom: window.innerHeight - 8 };
  }, []);

  const getCanvasPopupFrame = useCallback((preferredWidth: number, preferredHeight: number) => {
    const bounds = getCanvasBounds();
    const width = Math.min(preferredWidth, Math.max(1, bounds.right - bounds.left));
    const height = Math.min(preferredHeight, Math.max(1, bounds.bottom - bounds.top));
    const maxLeft = Math.max(bounds.left, bounds.right - width);
    const maxTop = Math.max(bounds.top, bounds.bottom - height);
    return {
      width,
      height,
      minLeft: bounds.left,
      maxLeft,
      minTop: bounds.top,
      maxTop,
      left: bounds.left + (maxLeft - bounds.left) / 2,
      top: bounds.top + (maxTop - bounds.top) / 2,
    };
  }, [getCanvasBounds]);

  const getWindowPopupFrame = useCallback((width: number, height: number) => {
    const minLeft = 8;
    const minTop = 8;
    return {
      minLeft,
      maxLeft: Math.max(minLeft, window.innerWidth - width - 8),
      minTop,
      maxTop: Math.max(minTop, window.innerHeight - height - 8),
    };
  }, []);

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    resizing.current = true;
    resizeStartX.current = e.clientX;
    resizeStartW.current = panelWidth;
    e.preventDefault();
  }, [panelWidth]);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!resizing.current) return;
      const delta = resizeStartX.current - e.clientX;
      setPanelWidth(Math.max(240, Math.min(640, resizeStartW.current + delta)));
    };
    const onUp = () => { resizing.current = false; };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); };
  }, []);

  // Left rail drag handle — snaps to two states only: hidden (0) or fixed width.
  const openSourcePeek = useCallback((nodeId: number) => {
    const node = result.nodes.find(item => item.id === nodeId);
    if (!node) return;
    const frame = getCanvasPopupFrame(920, 720);
    setPdfPeekWidthReady(true);
    setPdfPeekPos({ left: frame.left, top: frame.top });
    setPdfPeekSize({ width: frame.width, height: frame.height });
    setPdfPeek({ node, page: 1, searchTerms: [], statementTerms: [], url: null, status: "source" });
  }, [getCanvasPopupFrame, result.nodes]);

  const handlePdfPeekDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const card = (e.currentTarget as HTMLElement).closest(".gs-pdf-peek") as HTMLElement | null;
    const cardRect = card?.getBoundingClientRect();
    const startX = e.clientX;
    const startY = e.clientY;
    const startLeft = cardRect?.left ?? pdfPeekPos.left;
    const startTop = cardRect?.top ?? pdfPeekPos.top;
    const cardW = cardRect?.width ?? pdfPeekSize.width;
    const cardH = cardRect?.height ?? pdfPeekSize.height;
    let nextLeft = startLeft;
    let nextTop = startTop;
    let animationFrame = 0;
    document.body.style.cursor = "grabbing";
    document.body.style.userSelect = "none";
    const paint = () => {
      if (card) {
        card.style.left = `${nextLeft}px`;
        card.style.top = `${nextTop}px`;
      }
      animationFrame = 0;
    };
    const onMove = (ev: MouseEvent) => {
      const frame = getWindowPopupFrame(cardW, cardH);
      nextLeft = Math.max(frame.minLeft, Math.min(frame.maxLeft, startLeft + ev.clientX - startX));
      nextTop = Math.max(frame.minTop, Math.min(frame.maxTop, startTop + ev.clientY - startY));
      if (!animationFrame) animationFrame = window.requestAnimationFrame(paint);
    };
    const onUp = () => {
      if (animationFrame) window.cancelAnimationFrame(animationFrame);
      paint();
      setPdfPeekPos({ left: nextLeft, top: nextTop });
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }, [getWindowPopupFrame, pdfPeekPos, pdfPeekSize]);

  const handlePdfPeekResizeStart = useCallback((e: React.MouseEvent, edge: PdfPeekResizeEdge) => {
    e.preventDefault();
    e.stopPropagation();
    const card = (e.currentTarget as HTMLElement).closest(".gs-pdf-peek") as HTMLElement | null;
    const rect = card?.getBoundingClientRect();
    if (!card || !rect) return;

    const startX = e.clientX;
    const startY = e.clientY;
    const startLeft = rect.left;
    const startTop = rect.top;
    const startWidth = rect.width;
    const startHeight = rect.height;
    const startRight = startLeft + startWidth;
    const startBottom = startTop + startHeight;
    const minWidth = Math.min(520, Math.max(1, window.innerWidth - 16));
    const minHeight = Math.min(360, Math.max(1, window.innerHeight - 16));
    let left = startLeft;
    let top = startTop;
    let width = startWidth;
    let height = startHeight;
    let animationFrame = 0;
    const cursor = edge === "top" || edge === "bottom" ? "ns-resize" : edge === "left" || edge === "right" ? "ew-resize" : edge === "top-left" || edge === "bottom-right" ? "nwse-resize" : "nesw-resize";

    document.body.style.cursor = cursor;
    document.body.style.userSelect = "none";
    const paint = () => {
      card.style.left = `${left}px`;
      card.style.top = `${top}px`;
      card.style.width = `${width}px`;
      card.style.height = `${height}px`;
      animationFrame = 0;
    };
    const clamp = (value: number, minimum: number, maximum: number) => Math.max(minimum, Math.min(maximum, value));
    const onMove = (ev: MouseEvent) => {
      const deltaX = ev.clientX - startX;
      const deltaY = ev.clientY - startY;
      if (edge.includes("left")) {
        width = clamp(startWidth - deltaX, minWidth, startRight - 8);
        left = startRight - width;
      } else if (edge.includes("right")) {
        width = clamp(startWidth + deltaX, minWidth, window.innerWidth - startLeft - 8);
        left = startLeft;
      }
      if (edge.includes("top")) {
        height = clamp(startHeight - deltaY, minHeight, startBottom - 8);
        top = startBottom - height;
      } else if (edge.includes("bottom")) {
        height = clamp(startHeight + deltaY, minHeight, window.innerHeight - startTop - 8);
        top = startTop;
      }
      if (!animationFrame) animationFrame = window.requestAnimationFrame(paint);
    };
    const onUp = () => {
      if (animationFrame) window.cancelAnimationFrame(animationFrame);
      paint();
      setPdfPeekPos({ left, top });
      setPdfPeekSize({ width, height });
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }, []);

  const openPdfPeek = useCallback(async (nodeId: number) => {
    const requestIdentity = captureAuthRequestIdentity(token);
    const sourcePdf = result.source_pdf;
    if (!sourcePdf || !graphId) {
      openSourcePeek(nodeId);
      return;
    }
    const node = result.nodes.find(item => item.id === nodeId);
    if (!node) return;
    const status = sourcePdf.status ?? (sourcePdf.available ? "ready" : sourcePdf.error ? "failed" : "compiling");
    const frame = getCanvasPopupFrame(920, 720);
    setPdfPeekWidthReady(status !== "ready");
    setPdfPeekPos({ left: frame.left, top: frame.top });
    setPdfPeekSize({ width: frame.width, height: frame.height });
    if (status === "compiling") {
      setPdfPeek({ node, page: 1, searchTerms: [], statementTerms: [], url: null, status: "compiling" });
      return;
    }
    if (status === "failed" || !sourcePdf.available) {
      setPdfPeek({ node, page: 1, searchTerms: [], statementTerms: [], url: null, status: "failed", error: sourcePdf.error });
      return;
    }
    setPdfLoadingId(nodeId);
    try {
      const res = await protectedFetch(
        apiUrl(`/api/v2/source-pdf/${encodeURIComponent(graphId)}/locate?node_id=${encodeURIComponent(nodeId)}`),
        {},
        token,
      );
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      if (!res.ok) throw new Error("PDF locate failed");
      const loc = await res.json();
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      const page = Math.max(1, Number(loc.page || 1));
      const terms = Array.isArray(loc.search_terms) ? loc.search_terms.filter((t: unknown) => typeof t === "string" && t.trim()) : [];
      const statementTerms = Array.isArray(loc.statement_terms) ? loc.statement_terms.filter((t: unknown) => typeof t === "string" && t.trim()) : [];
      const rawPdfUrl = loc.pdf_url || sourcePdf.pdf_url || `/api/v2/source-pdf/${graphId}`;
      const url = apiUrl(rawPdfUrl);
      setPdfPeek({ node, page, searchTerms: terms, statementTerms, url, status: "ready" });
    } catch {
      setPdfPeekWidthReady(true);
      setPdfPeek({ node, page: 1, searchTerms: [], statementTerms: [], url: null, status: "source" });
    } finally {
      setPdfLoadingId(null);
    }
  }, [getCanvasPopupFrame, graphId, openSourcePeek, result.nodes, result.source_pdf, token]);

  const handlePdfPageSize = useCallback((pageSize: { width: number; height: number }) => {
    const bounds = getCanvasBounds();
    const maxWidth = Math.max(1, bounds.right - bounds.left);
    const width = Math.min(pageSize.width + 2, maxWidth);
    setPdfPeekSize(current => current.width === width ? current : { ...current, width });
    setPdfPeekWidthReady(true);
  }, [getCanvasBounds]);

  const handlePdfLoadError = useCallback(() => setPdfPeekWidthReady(true), []);

  const startRailDrag = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const el = railRef.current;
    if (!el) return;
    const RAIL_W = 232;
    const left = el.getBoundingClientRect().left;
    const prev = el.style.transition;
    el.style.transition = "none";            // follow cursor during drag
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    let finalW = RAIL_W;
    const onMove = (ev: MouseEvent) => {
      finalW = Math.max(0, Math.min(RAIL_W, ev.clientX - left));
      el.style.width = finalW + "px";        // direct DOM — zero re-renders
    };
    const onUp = () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      el.style.transition = prev;            // restore softened transition
      el.style.width = "";                   // hand width back to the class
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      setRailOpen(finalW > RAIL_W / 2);      // snap to nearest of {0, 232}
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }, []);

  const update = useCallback((patch: Partial<StudioSettings>) => {
    setSettings(s => { const n = { ...s, ...patch }; saveStudioSettings(n); return n; });
  }, []);

  // ── Derived ────────────────────────────────────────────────────────────────
  const nodes = result.nodes;
  const edges = result.edges;
  const nodeById = useMemo(() => new Map(nodes.map(n => [n.id, n])), [nodes]);
  const salience = useMemo(() => computeSalience(nodes, edges), [nodes, edges]);
  const depths = useMemo(() => computeDepthsLocal(nodes, edges), [nodes, edges]);
  const majorSet = useMemo(() => majorNodeSet(nodes, salience, settings.density), [nodes, salience, settings.density]);
  const edgeKinds = useMemo(() => edges.map(classifyEdge), [edges]);
  const typeCounts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const n of nodes) c[n.node_type] = (c[n.node_type] ?? 0) + 1;
    return c;
  }, [nodes]);
  const presentTypes = useMemo(
    () => [...STUDIO_NODE_TYPES, ...[...new Set(nodes.map(n => n.node_type))].filter(t => !STUDIO_NODE_TYPES.includes(t))]
      .filter(t => typeCounts[t]), [nodes, typeCounts]);
  const presentKinds = useMemo(() => [...new Set(edgeKinds)], [edgeKinds]);

  // A newly processed, imported, or history-loaded graph should start with all
  // of its actual node types visible. Keep later sidebar choices for this graph.
  const previousGraphId = useRef(graphId);
  useEffect(() => {
    if (previousGraphId.current === graphId) return;
    previousGraphId.current = graphId;
    setActiveTypes(new Set(nodes.map(node => node.node_type)));
  }, [graphId, nodes]);

  const anchor = useMemo(
    () => sourceMarkdown ? buildAnchorIndex(nodes, sourceMarkdown) : null,
    [nodes, sourceMarkdown]);
  const readingBlocks = useMemo(() => sourceMarkdown ? parseMdBlocks(sourceMarkdown) : [], [sourceMarkdown]);
  const pdfSourcePeek = useMemo(() => {
    if (!pdfPeek || pdfPeek.status !== "source" || !anchor) return null;
    const anchorIds = anchor.nodeToBlocks.get(pdfPeek.node.id) ?? [];
    const statementText = nodeStatementText(pdfPeek.node);
    const statementRange = sourceStatementRange(readingBlocks, anchorIds, statementText);
    const hitIds = statementRange
      ? new Set(Array.from({ length: statementRange[1] - statementRange[0] + 1 }, (_, offset) => statementRange[0] + offset))
      : statementText ? new Set<number>() : new Set(anchorIds);
    const blocks = readingBlocks
      .map((block, idx) => ({ idx, block }))
      .filter(item => item.block && item.block.type !== "hr");
    return blocks.length ? { blocks, hitIds, statementRange } : null;
  }, [pdfPeek, anchor, readingBlocks]);
  const pdfPeekFrame = pdfPeek ? pdfPeekSize : null;

  useLayoutEffect(() => {
    const body = pdfSourceBodyRef.current;
    if (!body || !pdfSourcePeek) return;
    const centerHighlight = () => {
      const hit = body.querySelector<HTMLElement>(".gs-source-statement-hit, .gs-source-peek-block.hit");
      if (hit) body.scrollTop = Math.max(0, hit.offsetTop - (body.clientHeight - hit.offsetHeight) / 2);
    };
    centerHighlight();
    const frame = window.requestAnimationFrame(centerHighlight);
    return () => window.cancelAnimationFrame(frame);
  }, [pdfSourcePeek]);

  useEffect(() => {
    if (!pdfPeek) return;
    const onResize = () => {
      const bounds = getCanvasBounds();
      const maxWidth = Math.max(1, bounds.right - bounds.left);
      const maxHeight = Math.max(1, bounds.bottom - bounds.top);
      const minimumHeight = Math.min(360, maxHeight);
      const size = {
        width: Math.min(maxWidth, pdfPeekSize.width),
        height: Math.max(minimumHeight, Math.min(maxHeight, pdfPeekSize.height)),
      };
      if (size.width !== pdfPeekSize.width || size.height !== pdfPeekSize.height) setPdfPeekSize(size);
      const frame = getWindowPopupFrame(size.width, size.height);
      setPdfPeekPos(pos => {
        return {
          left: Math.max(frame.minLeft, Math.min(frame.maxLeft, pos.left)),
          top: Math.max(frame.minTop, Math.min(frame.maxTop, pos.top)),
        };
      });
    };
    window.addEventListener("resize", onResize);
    onResize();
    return () => window.removeEventListener("resize", onResize);
  }, [pdfPeek, pdfPeekSize, getCanvasBounds, getWindowPopupFrame, panelExpanded, panelWidth, railOpen]);

  useEffect(() => {
    if (!pdfPeek || pdfPeek.status !== "compiling" || !graphId) return;
    const requestIdentity = captureAuthRequestIdentity(token);
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failedPolls = 0;
    const pollPdf = async () => {
      try {
        const statusRes = await protectedFetch(
          apiUrl(`/api/v2/jobs/${encodeURIComponent(graphId)}/status`),
          {},
          token,
        );
        if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
        if (statusRes.status === 404) {
          if (!stopped) {
            setPdfPeek(prev => prev ? {
              ...prev,
              status: "failed",
              error: "PDF 编译任务已失效。桌面应用重启后无法恢复正在进行的编译，请重新导入原始 TeX 文件。",
            } : prev);
          }
          return;
        }
        if (!statusRes.ok) throw new Error("PDF status unavailable");
        const jobStatus = await statusRes.json();
        if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
        failedPolls = 0;
        const sourcePdf = jobStatus.source_pdf;
        const status = sourcePdf?.status ?? (sourcePdf?.available ? "ready" : sourcePdf?.error ? "failed" : "compiling");
        if (status === "failed") {
          if (!stopped) setPdfPeek(prev => prev ? { ...prev, status: "failed", error: sourcePdf?.error || "PDF 编译失败" } : prev);
          return;
        }
        if (status === "ready" && sourcePdf?.available) {
          const nodeId = pdfPeek.node.id;
          const locateRes = await protectedFetch(
            apiUrl(`/api/v2/source-pdf/${encodeURIComponent(graphId)}/locate?node_id=${encodeURIComponent(nodeId)}`),
            {},
            token,
          );
          if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
          if (!locateRes.ok) throw new Error("PDF locate failed");
          const loc = await locateRes.json();
          if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
          const page = Math.max(1, Number(loc.page || 1));
          const terms = Array.isArray(loc.search_terms)
            ? loc.search_terms.filter((term: unknown) => typeof term === "string" && term.trim())
            : [];
          const statementTerms = Array.isArray(loc.statement_terms)
            ? loc.statement_terms.filter((term: unknown) => typeof term === "string" && term.trim())
            : [];
          const rawPdfUrl = loc.pdf_url || sourcePdf.pdf_url || `/api/v2/source-pdf/${graphId}`;
          if (!stopped) {
            setPdfPeekWidthReady(false);
            setPdfPeek(prev => prev ? {
              ...prev,
              page,
              searchTerms: terms,
              statementTerms,
              url: apiUrl(rawPdfUrl),
              status: "ready",
              error: null,
            } : prev);
          }
          return;
        }
      } catch {
        failedPolls += 1;
        if (failedPolls >= 3) {
          if (!stopped) {
            setPdfPeek(prev => prev ? {
              ...prev,
              status: "failed",
              error: "无法获取 PDF 编译状态，请检查桌面后端是否仍在运行。",
            } : prev);
          }
          return;
        }
        if (!stopped) timer = setTimeout(pollPdf, 1500);
        return;
      }
      if (!stopped) timer = setTimeout(pollPdf, 1500);
    };
    pollPdf();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [graphId, pdfPeek, token]);

  // Authoritative document order for the reading layout: rank each node by its
  // primary source block (anchor) — the real prose position — then tie-break by
  // node_index_in_doc / id. Unanchored nodes sort last. This guarantees the
  // serpentine grid actually follows the text order.
  const readingOrder = useMemo(() => {
    const blockOf = (n: GraphNode) => {
      const b = anchor?.nodeToBlocks.get(n.id);
      return b && b.length ? b[0] : Number.MAX_SAFE_INTEGER;
    };
    return [...nodes].sort((a, b) => {
      const ba = blockOf(a), bb = blockOf(b);
      if (ba !== bb) return ba - bb;
      return (a.node_index_in_doc ?? a.id) - (b.node_index_in_doc ?? b.id);
    }).map(n => n.id);
  }, [nodes, anchor]);

  const positions = useMemo<Record<number, Pos> | null>(() => {
    if (settings.layout === "reading") return layoutReading(nodes, readingOrder);
    if (settings.layout === "swimlane") return layoutSwimlane(nodes).pos;
    if (settings.layout === "dag") return layoutDag(nodes, depths);
    return null; // force
  }, [settings.layout, nodes, depths, readingOrder]);

  const lanes = useMemo<{ label: string; color: string; x?: number; y?: number }[]>(() => {
    if (settings.layout === "swimlane") return layoutSwimlane(nodes).lanes.map((t, i) => ({ label: nodeTypeLabel(t, lang), color: studioColor(t), x: i * 230 }));
    if (settings.layout === "dag") {
      const maxD = Math.max(0, ...Object.values(depths));
      return Array.from({ length: maxD + 1 }, (_, i) => ({ label: i === 0 ? "基础层" : `前置 ${i} 层`, color: "var(--muted)", y: i * 165 }));
    }
    return [];
  }, [settings.layout, nodes, depths, lang]);

  const focusSet = useMemo(
    () => (selectedId !== null && settings.dimOnFocus) ? neighborhood(selectedId, edges, 1) : null,
    [selectedId, settings.dimOnFocus, edges]);

  // ── Build vis node/edge objects ──────────────────────────────────────────────
  const dark = theme === "dark";
  const fontColor = dark ? "#ece9e3" : "#20201d";
  const buildNode = useCallback((n: GraphNode) => {
    const st = studioStyle(n.node_type);
    const major = majorSet.has(n.id);
    const sal = salience[n.id] ?? 0;
    const visible = activeTypes.has(n.node_type);
    const dim = focusSet && !focusSet.has(n.id);
    const pos = positions?.[n.id];
    const border = dark ? st.borderDark : st.border;
    // major nodes: tinted card; minor nodes: a solid colored dot (legible on both themes)
    const bg = major ? (dark ? st.bgDark : st.bg) : border;
    return {
      id: n.id,
      label: major ? studioLabel(n, lang) : " ",
      shape: major ? st.shape : "dot",
      size: major ? undefined : 5 + sal * 9,
      hidden: !visible,
      opacity: dim ? (dark ? 0.28 : 0.18) : 1,
      borderWidth: 1.5,
      color: {
        background: bg, border,
        highlight: { background: bg, border: dark ? "#7fb0e8" : "#1e5aa8" },
        hover: { background: bg, border },
      },
      font: { color: dim ? "rgba(0,0,0,0)" : fontColor, size: major ? 12.5 + sal * 5 : 1, face: "Inter",
        strokeWidth: dark ? 0 : 3, strokeColor: dark ? "#1c1b19" : "#f1efe9", multi: false },
      ...(pos ? { x: pos.x, y: pos.y, fixed: { x: false, y: false } } : {}),
    } as Record<string, unknown>;
  }, [majorSet, salience, activeTypes, focusSet, positions, lang, dark, fontColor]);

  const buildEdge = useCallback((e: GraphEdge, i: number) => {
    const kind = edgeKinds[i];
    const meta = EDGE_KINDS[kind];
    const inFocus = !focusSet || (focusSet.has(e.from) && focusSet.has(e.to));
    const incident = selectedId !== null && (e.from === selectedId || e.to === selectedId);
    const visible = activeTypes.has(nodeById.get(e.from)?.node_type ?? "") && activeTypes.has(nodeById.get(e.to)?.node_type ?? "");
    // dark uses dimmed same-hue colours, so focused edges can sit at the same
    // opacity as light without glaring; background edges are pushed further down.
    const baseOp = 0.5;
    const edgeColor = dark ? (meta.colorDark ?? meta.color) : meta.color;
    return {
      id: i, from: e.from, to: e.to,
      hidden: !visible,
      label: "",
      color: { color: edgeColor, opacity: incident ? 1 : inFocus ? baseOp : (dark ? 0.1 : 0.12), highlight: edgeColor, inherit: false },
      width: incident ? 2.4 : dark ? 0.9 : 1,
      dashes: meta.dashed ? [4, 4] : false,
      smooth: settings.curvedEdges ? { enabled: true, type: "dynamic", roundness: 0.5 } : false,
      arrows: { to: { enabled: true, scaleFactor: 0.45 } },
    } as Record<string, unknown>;
  }, [edgeKinds, focusSet, selectedId, activeTypes, nodeById, settings.curvedEdges, dark]);

  // ── (Re)create network on structural changes ─────────────────────────────────
  useEffect(() => {
    if (!canvasRef.current) return;
    const dsN = new DataSet(nodes.map(buildNode));
    const dsE = new DataSet(edges.map(buildEdge));
    nodesDS.current = dsN; edgesDS.current = dsE;
    const force = settings.layout === "force";
    const net = new Network(canvasRef.current, { nodes: dsN as never, edges: dsE as never }, {
      autoResize: true,
      physics: force
        ? { enabled: true, solver: "forceAtlas2Based", forceAtlas2Based: { gravitationalConstant: -45, springLength: 130, springConstant: 0.05, avoidOverlap: 0.6 }, stabilization: { iterations: 320 } }
        : { enabled: false },
      layout: { improvedLayout: false },
      interaction: { hover: true, tooltipDelay: 999999, navigationButtons: false, zoomView: true, dragView: true, multiselect: false, hideEdgesOnDrag: nodes.length > 120 },
      nodes: { shapeProperties: { interpolation: false }, margin: { top: 7, bottom: 7, left: 10, right: 10 } as never, widthConstraint: { maximum: 150 } as never },
      edges: { selectionWidth: 0, font: { size: 0 } },
    });
    netRef.current = net;

    const alive = () => netRef.current === net;
    const fitClamped = () => {
      net.fit({ animation: false });
      // Don't zoom out so far that labels become unreadable on dense graphs.
      if (net.getScale() < 0.6) net.moveTo({ scale: 0.6, animation: false });
    };
    if (force) {
      net.once("stabilizationIterationsDone", () => { if (alive()) { net.setOptions({ physics: { enabled: false } }); fitClamped(); } });
      setTimeout(() => { try { if (alive()) { net.setOptions({ physics: { enabled: false } }); fitClamped(); } } catch { /* */ } }, 4500);
    } else {
      setTimeout(() => { if (alive()) fitClamped(); }, 140);
    }

    net.on("click", (p: { nodes: number[]; edges: number[] }) => {
      if (p.nodes.length) { selectNode(p.nodes[0]); }
      else if (p.edges.length) { setSelectedEdge(p.edges[0]); setSelectedId(null); }
      else { setSelectedId(null); setSelectedEdge(null); }
    });
    net.on("hoverNode", (p: { node: number; event: { center?: { x: number; y: number } } }) => {
      const pos = net.canvasToDOM(net.getPositions([p.node])[p.node]);
      const rect = canvasRef.current!.getBoundingClientRect();
      setTip({ id: p.node, x: rect.left + pos.x, y: rect.top + pos.y });
    });
    net.on("blurNode", () => setTip(null));
    net.on("dragStart", () => setTip(null));

    return () => { net.destroy(); netRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result, settings.layout, theme, lang]);

  // ── Light updates (filters / density / focus / edge style) without rebuild ────
  useEffect(() => {
    nodesDS.current?.update(nodes.map(buildNode));
  }, [nodes, buildNode]);
  useEffect(() => {
    edgesDS.current?.update(edges.map(buildEdge));
  }, [edges, buildEdge]);

  // ── Selection → reading panel scroll ─────────────────────────────────────────
  const selectNode = useCallback((id: number) => {
    setSelectedId(id); setSelectedEdge(null);
    netRef.current?.selectNodes([id]);
    if (anchor) {
      const blocks = anchor.nodeToBlocks.get(id);
      if (blocks && blocks.length && readingRef.current) {
        const el = readingRef.current.querySelector(`[data-blk="${blocks[0]}"]`) as HTMLElement | null;
        if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1100); }
      }
    }
  }, [anchor]);

  useEffect(() => {
    if (pdfPeek && selectedId !== pdfPeek.node.id) setPdfPeek(null);
  }, [selectedId, pdfPeek]);

  const focusOnNode = useCallback((id: number) => {
    selectNode(id);
    const net = netRef.current; if (!net) return;
    net.focus(id, { scale: 1.1, animation: { duration: 360, easingFunction: "easeInOutQuad" } });
  }, [selectNode]);

  const openSearch = useCallback(() => {
    const compact = window.matchMedia("(max-width: 1020px)").matches;
    if (compact) {
      setSearchOpen(false);
      setCompactSearchOpen(true);
      requestAnimationFrame(() => compactSearchInputRef.current?.focus());
      return;
    }
    setCompactSearchOpen(false);
    setSearchOpen(true);
    requestAnimationFrame(() => searchInputRef.current?.focus());
  }, []);

  // keyboard: / opens search, esc closes
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "/" && !(e.target as HTMLElement)?.matches("input,textarea")) { e.preventDefault(); openSearch(); }
      if (e.key === "Escape") {
        setSearchOpen(false);
        setCompactSearchOpen(false);
        setCompactLayoutOpen(false);
        setExportMenuOpen(false);
        setShowSettings(false);
      }
    };
    window.addEventListener("keydown", h); return () => window.removeEventListener("keydown", h);
  }, [openSearch]);

  useEffect(() => {
    if (!exportMenuOpen) return;
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (!exportMenuRef.current?.contains(event.target as Node)) {
        setExportMenuOpen(false);
      }
    };
    document.addEventListener("pointerdown", closeOnOutsideClick);
    return () => document.removeEventListener("pointerdown", closeOnOutsideClick);
  }, [exportMenuOpen]);

  useEffect(() => {
    if (exporting) setExportMenuOpen(false);
  }, [exporting]);

  const selected = selectedId !== null ? nodeById.get(selectedId) ?? null : null;
  const resultSourceMode = result.source_mode ?? workspaceMode;
  const hasSourcePdf = !!result.source_pdf && result.source_pdf.status !== "failed";

  // typed deps of the selected node
  const deps = useMemo(() => {
    if (selectedId === null) return [] as { kind: EdgeKind; node: GraphNode; dir: "out" | "in" }[];
    const out: { kind: EdgeKind; node: GraphNode; dir: "out" | "in" }[] = [];
    edges.forEach((e, i) => {
      if (e.from === selectedId && nodeById.get(e.to)) out.push({ kind: edgeKinds[i], node: nodeById.get(e.to)!, dir: "out" });
      else if (e.to === selectedId && nodeById.get(e.from)) out.push({ kind: edgeKinds[i], node: nodeById.get(e.from)!, dir: "in" });
    });
    return out;
  }, [selectedId, edges, edgeKinds, nodeById]);

  const searchResults = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return nodes.slice().sort((a, b) => (salience[b.id] ?? 0) - (salience[a.id] ?? 0)).slice(0, 8);
    return nodes.filter(n =>
      (n.title_zh || "").toLowerCase().includes(q) || (n.title_en || "").toLowerCase().includes(q) ||
      (n.label || "").toLowerCase().includes(q) || (n.content || "").toLowerCase().includes(q))
      .slice(0, 30);
  }, [query, nodes, salience]);
  const titleOf = (n: GraphNode) => lang === "en" ? (n.title_en || n.title_zh) : (n.title_zh || n.title_en);
  const selectSearchResult = (id: number) => {
    focusOnNode(id);
    setSearchOpen(false);
    setCompactSearchOpen(false);
  };
  const handleSearchKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && searchResults[0]) selectSearchResult(searchResults[0].id);
  };
  const renderSearchResults = () => (
    <>
      {searchResults.length === 0 && <div className="gs-search-empty">无匹配概念</div>}
      {searchResults.map(n => (
        <div key={n.id} className="gs-search-item" onClick={() => selectSearchResult(n.id)}>
          <span className="gs-dot" style={{ background: studioColor(n.node_type) }} />
          <span className="gs-si-title"><SmartTitle text={titleOf(n) || `节点${n.id}`} macros={latexMacros} /></span>
          <span className="gs-si-type">{nodeTypeLabel(n.node_type, lang)}</span>
        </div>
      ))}
    </>
  );

  return (
    <div className="gs-root" data-theme={theme}>
      {/* ── Top bar ── */}
      <div className="gs-topbar">
        <button className="gs-iconbtn" title={railOpen ? "收起侧栏" : "展开侧栏"} onClick={() => setRailOpen(v => !v)}>
          {railOpen ? <PanelLeftClose size={17} /> : <PanelLeft size={17} />}
        </button>
        <div className="gs-brand">
          <img className="gs-brand-mark" src="/mathweaver-icon.png" alt="" aria-hidden="true" />
          <span className="gs-title">{filename || "知识图谱"}</span>
          <span className="gs-source-tag">{resultSourceMode === "generate" ? "文档生成" : "文件导入"}</span>
        </div>
        <span className="gs-stats">{nodes.length} 节点 · {edges.length} 关系</span>

        <div className="gs-topbar-spacer" />

        <div className="gs-search gs-search-full">
          <Search size={14} />
          <input
            ref={searchInputRef}
            placeholder="搜索概念  /" value={query}
            onFocus={() => setSearchOpen(true)}
            onChange={e => { setQuery(e.target.value); setSearchOpen(true); }}
            onKeyDown={handleSearchKeyDown}
          />
          {searchOpen && (
            <div className="gs-search-pop" onMouseLeave={() => !query && setSearchOpen(false)}>
              {renderSearchResults()}
            </div>
          )}
        </div>

        <div className="gs-search-compact">
          <button className="gs-iconbtn" title="搜索概念" aria-label="搜索概念" aria-expanded={compactSearchOpen} onClick={openSearch}><Search size={17} /></button>
          {compactSearchOpen && (
            <div className="gs-compact-search-pop" onMouseLeave={() => !query && setCompactSearchOpen(false)}>
              <div className="gs-compact-search-input">
                <Search size={15} />
                <input ref={compactSearchInputRef} placeholder="搜索概念" value={query} onChange={e => setQuery(e.target.value)} onKeyDown={handleSearchKeyDown} />
              </div>
              <div className="gs-compact-search-results">{renderSearchResults()}</div>
            </div>
          )}
        </div>

        <div className="gs-seg gs-layout-full">
          {LAYOUTS.map(l => (
            <button key={l.key} className={settings.layout === l.key ? "active" : ""} onClick={() => update({ layout: l.key })}>{l.label}</button>
          ))}
        </div>

        <div className="gs-layout-compact">
          <button className={`gs-iconbtn ${compactLayoutOpen ? "active" : ""}`} title="选择图谱布局" aria-label="选择图谱布局" aria-expanded={compactLayoutOpen} onClick={() => setCompactLayoutOpen(open => !open)}><LayoutGrid size={17} /></button>
          {compactLayoutOpen && (
            <div className="gs-layout-pop">
              {LAYOUTS.map(l => (
                <button key={l.key} className={settings.layout === l.key ? "active" : ""} onClick={() => { update({ layout: l.key }); setCompactLayoutOpen(false); }}>{l.label}</button>
              ))}
            </div>
          )}
        </div>

        <div className="gs-toolbar-secondary">
          {onShowApiGuide && (
            <button className="gs-iconbtn" title="API 配置指南" onClick={onShowApiGuide}><CircleHelp size={17} /></button>
          )}
          <button className="gs-iconbtn" title="明暗" onClick={() => update({ theme: theme === "dark" ? "light" : "dark" })}>
            {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
          </button>
          <button className={`gs-iconbtn ${showSettings ? "active" : ""}`} title="个性化" onClick={() => setShowSettings(v => !v)}><Settings2 size={17} /></button>
          {token && onLoadHistory && (
            <button className="gs-iconbtn" title="历史记录" onClick={() => setShowHistory(true)}><History size={17} /></button>
          )}
        </div>
        <div className="gs-toolbar-actions">
          <button className="gs-btn gs-btn-ghost gs-action-button gs-reset-action" onClick={onReset} title="重新上传"><Upload size={16} /><span className="gs-action-label">重新上传</span></button>
          <div className="gs-export" ref={exportMenuRef}>
            <button
              className={`gs-btn gs-btn-primary gs-action-button ${exportMenuOpen ? "active" : ""}`}
              onClick={() => setExportMenuOpen(open => !open)}
              disabled={exporting}
              title={exporting ? "正在导出" : "选择导出方式"}
              aria-haspopup="menu"
              aria-expanded={exportMenuOpen}
            >
              {exporting ? <Loader2 size={16} className="gs-spin" /> : <Download size={16} />}<span className="gs-action-label">导出</span>
            </button>
            {exportMenuOpen && (
              <div className="gs-export-menu" role="menu" aria-label="选择导出方式">
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setExportMenuOpen(false);
                    onExport("html");
                  }}
                >
                  <FileText size={18} />
                  <span><strong>导出图谱 HTML</strong><small>交互式图谱 · .html</small></span>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  disabled={resultSourceMode !== "generate"}
                  onClick={() => {
                    setExportMenuOpen(false);
                    onExport("json");
                  }}
                >
                  <FileJson size={18} />
                  <span>
                    <strong>导出处理结果 JSON</strong>
                    <small>{resultSourceMode === "generate" ? "节点、边；阶段缓存可用时一并导出 · .zip" : "仅当前生成任务支持完整缓存导出"}</small>
                  </span>
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Body ── */}
      <div className="gs-body">
        {/* Left rail */}
        <div ref={railRef} className={`gs-rail ${railOpen ? "" : "collapsed"}`}>
          <div className="gs-rail-section">
            <div className="gs-rail-label">节点类型
              <a onClick={() => setActiveTypes(new Set(presentTypes))}>全选</a>
            </div>
            {presentTypes.map(t => (
              <div key={t} className={`gs-filter ${activeTypes.has(t) ? "" : "off"}`} onClick={() => setActiveTypes(s => { const n = new Set(s); n.has(t) ? n.delete(t) : n.add(t); return n; })}>
                <span className="gs-dot" style={{ background: studioColor(t) }} />{nodeTypeLabel(t, lang)}
                <span className="gs-count">{typeCounts[t]}</span>
              </div>
            ))}
          </div>

          <div className="gs-rail-section">
            <div className="gs-control">
              <div className="gs-control-head"><span>信息密度</span><span>{Math.round(settings.density * 100)}%</span></div>
              <div
                className="gs-slider-shell"
                style={{ "--gs-slider-progress": `${((Math.round(settings.density * 100) - 5) / 95) * 100}%` } as React.CSSProperties}
              >
                <input className="gs-slider" type="range" min={5} max={100} step={5}
                  value={Math.round(settings.density * 100)}
                  onChange={e => update({ density: Number(e.target.value) / 100 })} />
              </div>
              <div className="gs-slider-ticks"><span>概览</span><span>全部标注</span></div>
            </div>
          </div>

          {presentKinds.length > 0 && (
            <div className="gs-rail-section">
              <div className="gs-rail-label">依赖类型</div>
              {presentKinds.map(k => (
                <div key={k} className="gs-legend-item">
                  <span className="gs-legend-line" style={{ borderColor: EDGE_KINDS[k].color, borderStyle: EDGE_KINDS[k].dashed ? "dashed" : "solid" }} />
                  {EDGE_KINDS[k].label}
                </div>
              ))}
            </div>
          )}

          {anchor && (
            <div className="gs-rail-section">
              <div className="gs-rail-label">原文召回</div>
              <div className="gs-coverage">
                <div className="gs-coverage-bar"><div className="gs-coverage-fill" style={{ width: `${Math.round(anchor.coverage * 100)}%` }} /></div>
                <div className="gs-coverage-num"><b>{Math.round(anchor.coverage * 100)}%</b> 节点可定位到原文（{nodes.length - anchor.unmatched.length}/{nodes.length}）</div>
              </div>
            </div>
          )}
        </div>

        {/* Left rail drag handle — snaps to hidden / fixed width */}
        <div className="gs-rail-handle" onMouseDown={startRailDrag} title="拖动调整侧栏" />

        {/* Canvas */}
        <div className="gs-canvas-wrap">
          <div ref={canvasRef} className="gs-canvas" />
          {nodes.length === 0 && <div className="gs-canvas-empty"><Crosshair size={28} />暂无节点</div>}

          {/* lane labels */}
          {lanes.length > 0 && (
            <LaneOverlay lanes={lanes} net={netRef} />
          )}

          {/* focus banner */}
          {selected && settings.dimOnFocus && (
            <div className="gs-focus-banner">
              <BookOpen size={14} /><span>聚焦 <b><SmartTitle text={titleOf(selected) || ""} macros={latexMacros} /></b> 的依赖邻域</span>
              <button onClick={() => { setSelectedId(null); netRef.current?.unselectAll(); }}>清除</button>
            </div>
          )}

          {/* zoom cluster */}
          <div className="gs-zoom">
            <button title="放大" onClick={() => { const s = netRef.current?.getScale() ?? 1; netRef.current?.moveTo({ scale: s * 1.25, animation: true }); }}><Plus size={16} /></button>
            <button title="缩小" onClick={() => { const s = netRef.current?.getScale() ?? 1; netRef.current?.moveTo({ scale: s / 1.25, animation: true }); }}><Minus size={16} /></button>
            <hr />
            <button title="适应窗口" onClick={() => netRef.current?.fit({ animation: true })}><Maximize2 size={15} /></button>
          </div>

          {/* hover tooltip */}
          {tip && !panelExpanded && nodeById.get(tip.id) && (
            <div
              ref={tipRef}
              className="gs-tip"
              style={{
                left: tipPos ? tipPos.left : tip.x + 14,
                top: tipPos ? tipPos.top : tip.y - 10,
                visibility: tipPos ? "visible" : "hidden",
              }}
            >
              <div className="gs-tip-type" style={{ color: studioColor(nodeById.get(tip.id)!.node_type) }}>{nodeTypeLabel(nodeById.get(tip.id)!.node_type, lang)}{nodeById.get(tip.id)!.label ? ` · ${nodeById.get(tip.id)!.label}` : ""}</div>
              <div className="gs-tip-title"><SmartTitle text={titleOf(nodeById.get(tip.id)!) || ""} macros={latexMacros} /></div>
              {nodeById.get(tip.id)!.content && (
                <div className="gs-tip-content"><MathText text={previewText(nodeById.get(tip.id)!.content)} macros={latexMacros} /></div>
              )}
            </div>
          )}
        </div>

        {pdfPeek && pdfPeekFrame && (
          <div className="gs-pdf-peek" style={{ left: pdfPeekPos.left, top: pdfPeekPos.top, width: pdfPeekFrame.width, height: pdfPeekFrame.height, visibility: pdfPeek.status === "ready" && !pdfPeekWidthReady ? "hidden" : undefined }}>
            <div className="gs-pdf-peek-head" onMouseDown={handlePdfPeekDragStart}>
              <span><BookOpen size={14} />{pdfPeek.status === "source" ? (/\.tex$/i.test(filename) ? "TeX 原文" : /\.md(?:own)?$/i.test(filename) ? "Markdown 原文" : "原文上下文") : `PDF 原文 · 第 ${pdfPeek.page} 页`}</span>
              <div className="gs-pdf-peek-actions">
                <button className="gs-iconbtn" title="关闭 PDF 原文" onMouseDown={e => e.stopPropagation()} onClick={() => setPdfPeek(null)}><X size={15} /></button>
              </div>
            </div>
            <div className="gs-pdf-peek-node">
              <div><SmartTitle text={titleOf(pdfPeek.node) || `节点 ${pdfPeek.node.id}`} macros={latexMacros} /></div>
            </div>
            {pdfPeek.status === "compiling" && (
              <div className="gs-pdf-state">
                <Loader2 size={24} className="gs-spin" />
                <strong>PDF 编译中</strong>
                <span>编译完成后将自动定位到当前节点。</span>
              </div>
            )}
            {pdfPeek.status === "failed" && (
              <div className="gs-pdf-state error">
                <strong>PDF 编译失败</strong>
                <span>{pdfPeek.error || "无法生成 PDF 原文"}</span>
                <button className="gs-btn gs-btn-ghost" onClick={() => setPdfPeek(current => current ? { ...current, status: "source", error: null } : null)}>查看源码</button>
              </div>
            )}
            {pdfPeek.status === "ready" && pdfPeek.url && (
              <PdfSourceViewer url={pdfPeek.url} token={token} page={pdfPeek.page} sourceStatement={nodeStatementText(pdfPeek.node)} searchTerms={pdfPeek.searchTerms} statementTerms={pdfPeek.statementTerms} onPageSize={handlePdfPageSize} onLoadError={handlePdfLoadError} />
            )}
            {pdfPeek.status === "source" && (
              <div ref={pdfSourceBodyRef} className="gs-pdf-source-body">
                {pdfSourcePeek ? pdfSourcePeek.blocks.map(({ idx, block }) => {
                  const range = pdfSourcePeek.statementRange;
                  if (range && idx > range[0] && idx <= range[1]) return null;
                  const statementBlocks = range && idx === range[0]
                    ? pdfSourcePeek.blocks.filter(item => item.idx >= range[0] && item.idx <= range[1])
                    : [{ idx, block }];
                  const isStatement = !!range && idx === range[0];
                  return (
                    <div key={idx} className={isStatement ? "gs-source-statement-hit" : `gs-source-peek-block ${pdfSourcePeek.hitIds.has(idx) ? "hit" : ""}`}>
                      {statementBlocks.map(item => (
                        <div key={item.idx} className={isStatement ? "gs-source-statement-block" : undefined}>
                          {"text" in item.block && <MathText text={item.block.type === "math-block" ? `$$${item.block.text}$$` : item.block.text} macros={latexMacros} />}
                        </div>
                      ))}
                    </div>
                  );
                }) : <div className="gs-pdf-state"><strong>原始内容不可用</strong></div>}
              </div>
            )}
            {(["top", "right", "bottom", "left", "top-left", "top-right", "bottom-left", "bottom-right"] as PdfPeekResizeEdge[]).map(edge => (
              <div key={edge} className={`gs-pdf-resize-handle ${edge}`} onMouseDown={event => handlePdfPeekResizeStart(event, edge)} />
            ))}
          </div>
        )}

        {/* Right panel */}
        {(selected || selectedEdge !== null || panelTab === "reading") && (
          <>
          {!panelExpanded && <div className="gs-resize" onMouseDown={handleResizeStart} title="拖动调整宽度" />}
          <div className={`gs-panel ${panelExpanded ? "expanded" : ""}`} style={panelExpanded ? undefined : { width: panelWidth }}>
            <div className="gs-panel-head">
              <button className={`gs-panel-tab ${panelTab === "detail" ? "active" : ""}`} onClick={() => setPanelTab("detail")}>详情</button>
              {(sourceMarkdown || hasSourcePdf) && (
                <button
                  className={`gs-panel-tab ${panelTab === "reading" ? "active" : ""}`}
                  onClick={() => {
                    if (selectedId !== null && hasSourcePdf) {
                      setPanelTab("detail");
                      setTip(null);
                      openPdfPeek(selectedId);
                      return;
                    }
                    setPanelTab("reading");
                  }}
                >
                  原文
                </button>
              )}
              <button className="gs-panel-expand gs-iconbtn" title={panelExpanded ? "退出全屏" : "展开详情页"} onClick={() => { setTip(null); setPanelExpanded(v => !v); }}>
                {panelExpanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
              </button>
              <button className="gs-panel-close gs-iconbtn" onClick={() => { setSelectedId(null); setSelectedEdge(null); setPanelTab("detail"); netRef.current?.unselectAll(); }}><X size={16} /></button>
            </div>
            <div className="gs-panel-body" ref={panelTab === "reading" ? readingRef : undefined}>
              {panelTab === "reading"
                ? <ReadingPanel blocks={readingBlocks} anchor={anchor} activeId={selectedId} onPick={selectNode} macros={latexMacros} />
                : selectedEdge !== null
                  ? <EdgeDetail edge={edges[selectedEdge]} kind={edgeKinds[selectedEdge]} nodeById={nodeById} lang={lang} onPick={focusOnNode} macros={latexMacros} />
                  : <NodeDetail
                      node={selected}
                      deps={deps}
                      lang={lang}
                      hasAnchor={!!anchor?.nodeToBlocks.get(selectedId ?? -1)?.length}
                      hasPdf={!!result.source_pdf && result.source_pdf.status !== "failed"}
                      pdfLoading={selectedId !== null && pdfLoadingId === selectedId}
                      onJump={() => { if (selectedId !== null) { setPanelTab("detail"); setTip(null); openPdfPeek(selectedId); } }}
                      onPick={focusOnNode}
                      graphId={graphId}
                      token={token}
                      llmConfig={llmConfig}
                      macros={latexMacros}
                    />}
            </div>
          </div>
          </>
        )}
      </div>

      {/* Settings popover */}
      {showSettings && (
        <div className="gs-pop">
          <div className="gs-pop-title">个性化</div>
          <Row label="主题">
            <div className="gs-pop-seg">
              {(["light", "dark", "auto"] as const).map(m => <button key={m} className={settings.theme === m ? "active" : ""} onClick={() => update({ theme: m })}>{m === "light" ? "浅" : m === "dark" ? "深" : "跟随"}</button>)}
            </div>
          </Row>
          <Row label="聚焦时淡化邻域外节点"><Switch on={settings.dimOnFocus} onClick={() => update({ dimOnFocus: !settings.dimOnFocus })} /></Row>
          <Row label="曲线连接"><Switch on={settings.curvedEdges} onClick={() => update({ curvedEdges: !settings.curvedEdges })} /></Row>
          <div className="gs-pop-title">默认布局</div>
          <div className="gs-pop-seg gs-layout-seg" style={{ margin: "0 10px 8px" }}>
            {LAYOUTS.map(l => <button key={l.key} className={settings.layout === l.key ? "active" : ""} onClick={() => update({ layout: l.key })}>{l.label.slice(0, 2)}</button>)}
          </div>
        </div>
      )}
      <style>{`.gs-spin{animation:gs-spin 1s linear infinite}@keyframes gs-spin{to{transform:rotate(360deg)}}`}</style>

      {showHistory && token && onLoadHistory && (
        <HistoryPanel
          token={token}
          llmConfig={llmConfig}
          onLoad={(r, f, id) => { onLoadHistory(r, f, id); setShowHistory(false); }}
          onResume={(job) => { onResumeHistory?.(job); setShowHistory(false); }}
          onClose={() => setShowHistory(false)}
        />
      )}
    </div>
  );
}

// ── Lane overlay: positions lane tags using current vis transform ────────────
function LaneOverlay({ lanes, net }: { lanes: { label: string; color: string; x?: number; y?: number }[]; net: React.RefObject<Network | null> }) {
  const [, force] = useState(0);
  useEffect(() => {
    const n = net.current; if (!n) return;
    const h = () => force(x => x + 1);
    n.on("afterDrawing", h); h();
    return () => { try { n.off("afterDrawing", h); } catch { /* */ } };
  }, [net]);
  const n = net.current;
  if (!n) return null;
  const frame = (n as unknown as { canvas: { frame: { canvas: { width: number; height: number } } } }).canvas.frame.canvas;
  return (
    <div className="gs-lanes">
      {lanes.map((l, i) => {
        if (l.x !== undefined) {
          // horizontal swimlane: type labels pinned across the top
          let x = 0;
          try { x = n.canvasToDOM({ x: l.x, y: 0 }).x; } catch { return null; }
          if (x < 10 || x > frame.width - 10) return null;
          return <div key={i} className="gs-lane-tag gs-lane-tag-top" style={{ left: x }}>
            <span className="gs-dot" style={{ background: l.color }} />{l.label}
          </div>;
        }
        // vertical lane (dag layers): labels pinned down the left edge
        let y = 0;
        try { y = n.canvasToDOM({ x: 0, y: l.y ?? 0 }).y; } catch { return null; }
        if (y < 30 || y > frame.height - 10) return null;
        return <div key={i} className="gs-lane-tag" style={{ top: y - 11 }}>
          <span className="gs-dot" style={{ background: l.color }} />{l.label}
        </div>;
      })}
    </div>
  );
}

// ── Node detail ──────────────────────────────────────────────────────────────
function NodeDetail({ node, deps, lang, hasAnchor, hasPdf, pdfLoading, onJump, onPick, graphId, token, llmConfig, macros }: {
  node: GraphNode | null;
  deps: { kind: EdgeKind; node: GraphNode; dir: "out" | "in" }[];
  lang: NodeLanguage; hasAnchor: boolean; hasPdf?: boolean; pdfLoading?: boolean; onJump: () => void; onPick: (id: number) => void;
  graphId?: string; token?: string; llmConfig?: LLMConfig;
  macros?: LatexMacros;
}) {
  if (!node) return <div className="gs-search-empty" style={{ marginTop: 40 }}>点击节点查看详情</div>;
  const st = studioStyle(node.node_type);
  const title = lang === "en" ? (node.title_en || node.title_zh) : (node.title_zh || node.title_en);
  const sub = lang === "en" ? node.title_zh : node.title_en;
  return (
    <div>
      <span className="gs-badge" style={{ background: st.border }}>
        {nodeTypeLabel(node.node_type, lang)}{node.label ? ` · ${node.label}` : ""}
      </span>
      <div className="gs-d-title"><SmartTitle text={title || `节点 ${node.id}`} macros={macros} /></div>
      {sub && sub !== title && <div className="gs-d-title-sub"><SmartTitle text={sub} macros={macros} /></div>}

      <button className={`gs-d-jump ${hasAnchor || hasPdf ? "" : "disabled"}`} onClick={hasAnchor || hasPdf ? onJump : undefined}>
        {pdfLoading ? <Loader2 size={13} className="gs-spin" /> : <BookOpen size={13} />}
        {pdfLoading ? "定位 PDF..." : hasPdf ? "跳转到 PDF 原文" : hasAnchor ? "跳转到原文" : "原文未定位"}
      </button>

      {node.content && <div className="gs-d-section"><div className="gs-d-label">陈述</div><div className="gs-d-text"><MathText text={node.content} macros={macros} /></div></div>}

      {deps.length > 0 && <>
        <div className="gs-sep" />
        <div className="gs-d-label">依赖关系（{deps.length}）</div>
        {deps.map((d, i) => (
          <div key={i} className="gs-dep" onClick={() => onPick(d.node.id)}>
            <span className="gs-dep-kind" style={{ background: EDGE_KINDS[d.kind].color }}>{EDGE_KINDS[d.kind].label.split(" ")[0]}</span>
            <span className="gs-dep-title"><SmartTitle text={(lang === "en" ? d.node.title_en : d.node.title_zh) || d.node.title_zh || `节点${d.node.id}`} macros={macros} /></span>
            <span className="gs-dep-dir">{d.dir === "out" ? "→ 依赖" : "← 被依赖"}</span>
          </div>
        ))}
      </>}

      {(node.conditions?.length > 0 || node.conclusions?.length > 0) && <>
        <div className="gs-sep" />
        {node.conditions?.length > 0 && <div className="gs-d-section"><div className="gs-d-label">条件</div>{node.conditions.map((c, i) => <span key={i} className="gs-tag"><MathText text={asText(c)} macros={macros} /></span>)}</div>}
        {node.conclusions?.length > 0 && <div className="gs-d-section"><div className="gs-d-label">结论</div>{node.conclusions.map((c, i) => <span key={i} className="gs-tag"><MathText text={asText(c)} macros={macros} /></span>)}</div>}
      </>}

      {graphId && <ProofWorkspace graphId={graphId} node={node} token={token} llmConfig={llmConfig} macros={macros} />}
    </div>
  );
}

function EdgeDetail({ edge, kind, nodeById, lang, onPick, macros }: {
  edge: GraphEdge; kind: EdgeKind; nodeById: Map<number, GraphNode>; lang: NodeLanguage; onPick: (id: number) => void; macros?: LatexMacros;
}) {
  const from = nodeById.get(edge.from); const to = nodeById.get(edge.to);
  const t = (n?: GraphNode) => n ? ((lang === "en" ? n.title_en : n.title_zh) || n.title_zh || `节点${n.id}`) : "?";
  return (
    <div>
      <span className="gs-badge" style={{ background: EDGE_KINDS[kind].color }}>{EDGE_KINDS[kind].label}</span>
      <div className="gs-d-title" style={{ fontSize: 17 }}><MathText text={edge.label || "关系"} macros={macros} /></div>
      <div className="gs-d-section">
        <div className="gs-dep" onClick={() => from && onPick(from.id)}><span className="gs-dep-title"><SmartTitle text={t(from)} macros={macros} /></span></div>
        <div style={{ display: "flex", justifyContent: "center", color: "var(--muted)", margin: "4px 0" }}><ArrowRight size={16} style={{ transform: "rotate(90deg)" }} /></div>
        <div className="gs-dep" onClick={() => to && onPick(to.id)}><span className="gs-dep-title"><SmartTitle text={t(to)} macros={macros} /></span></div>
      </div>
      {edge.description && <div className="gs-d-section"><div className="gs-d-label">说明</div><div className="gs-d-text"><MathText text={edge.description} macros={macros} /></div></div>}
    </div>
  );
}

// ── Reading panel (linked source text) ───────────────────────────────────────
function ReadingPanel({ blocks, anchor, activeId, onPick, macros }: {
  blocks: ReturnType<typeof parseMdBlocks>;
  anchor: ReturnType<typeof buildAnchorIndex> | null;
  activeId: number | null; onPick: (id: number) => void;
  macros?: LatexMacros;
}) {
  if (!blocks.length) return <div className="gs-search-empty" style={{ marginTop: 40 }}>原始文档未保存</div>;
  return (
    <div className="gs-reading">
      {blocks.map((b, i) => {
        const nids = anchor?.blockToNodes.get(i) ?? [];
        const isSrc = nids.length > 0;
        const active = activeId !== null && nids.includes(activeId);
        const cls = `gs-blk ${isSrc ? "src" : ""} ${active ? "active" : ""}`;
        const onClick = isSrc ? () => onPick(nids[0]) : undefined;
        const common = { "data-blk": i, className: cls, onClick } as Record<string, unknown>;
        if (b.type === "h1") return <h1 key={i} {...common}><MathText text={b.text} macros={macros} /></h1>;
        if (b.type === "h2") return <h2 key={i} {...common}><MathText text={b.text} macros={macros} /></h2>;
        if (b.type === "h3") return <h3 key={i} {...common}><MathText text={b.text} macros={macros} /></h3>;
        if (b.type === "h4") return <h3 key={i} {...common} style={{ color: "var(--muted)" }}><MathText text={b.text} macros={macros} /></h3>;
        if (b.type === "math-block") return <div key={i} {...common} style={{ overflowX: "auto", margin: "10px 0" }}><MathText text={`$$${b.text}$$`} macros={macros} /></div>;
        if (b.type === "hr") return <hr key={i} style={{ border: "none", borderTop: "1px solid var(--line)", margin: "16px 0" }} />;
        return <p key={i} {...common}><MathText text={b.text} macros={macros} /></p>;
      })}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="gs-pop-row"><span>{label}</span>{children}</div>;
}
function Switch({ on, onClick }: { on: boolean; onClick: () => void }) {
  return <button className={`gs-switch ${on ? "on" : ""}`} onClick={onClick} aria-pressed={on} />;
}
