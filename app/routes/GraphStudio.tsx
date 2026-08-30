import { useState, useEffect, useLayoutEffect, useRef, useMemo, useCallback, useReducer } from "react";
import type { Dispatch, SetStateAction } from "react";
import { createPortal } from "react-dom";
import { Network } from "vis-network";
import { DataSet } from "vis-data";
import {
  Search, Settings2, Sun, Moon, Maximize2, Plus, Minus, Crosshair,
  PanelLeftClose, PanelLeft, X, Upload, Download, Minimize2,
  BookOpen, ArrowRight, Loader2, History, LayoutGrid, FileText, FileJson, CircleHelp,
  GraduationCap, Route as RouteIcon, CheckCircle2, AlertTriangle,
  LockKeyhole, Brain, ChevronDown, ChevronLeft, ChevronRight, ArrowUp, ArrowDown, Trash2, Save, RotateCcw, Send, ClipboardCheck,
} from "lucide-react";
import { MathText, SmartTitle } from "./math";
import type { LatexMacros } from "./math";
import { parseMdBlocks } from "./markdown";
import { HistoryPanel } from "./HistoryPanel";
import { ProofWorkspace } from "./ProofWorkspace";
import { MatrixFlowText } from "./matrix-flow/MatrixFlowViewer";
import type { MatrixFlowAudience, MatrixFlowLayoutMode } from "./matrix-flow/types";
import { PdfSourceViewer } from "./PdfSourceViewer";
import { nodeTypeLabel } from "./node-type-language";
import { apiUrl } from "~/api";
import type { RestoredJob } from "~/context/jobs";
import type { GraphNode, GraphEdge, GraphResult, NodeLanguage, WorkspaceMode, LLMConfig } from "./home";
import {
  assessmentAnswersComplete,
  assessmentScoringSummary,
  assessmentGenerationErrorMessage,
  assessmentOperationCounts,
  assessmentOperationForNode,
  assessmentOperationReducer,
  completeEducationAssessmentAttempt,
  deleteEducationAssessmentQuestion,
  exemptEducationAssessment,
  learningCanvasEdges,
  loadStudentContext,
  publishEducationAssignment,
  regenerateEducationAssessment,
  regenerateEducationAssessmentQuestion,
  regenerateUnresolvedEducationAssessments,
  replaceNodeAssessment,
  saveEducationAssessmentAttempt,
  submitEducationAssignment,
  saveEducationAssignment,
  startEducationAssessmentAttempt,
  studentAssignmentCompletion,
  updateEducationProgress,
  updateEducationAssessmentQuestion,
  loadEducationSubmission,
  educationErrorMessage,
  unresolvedAssessmentNodeIds,
} from "./education";
import type { AssessmentAttempt, AssessmentOperation, AssessmentQuestion, EducationAssignment, EducationSubmission, LearningPathStep, MatrixCheckReport, NodeAssessment, QuestionGrade, PathOrderWarning, StudentNodeContextState } from "./education";
import {
  STUDIO_NODE_TYPES, studioStyle, studioColor, studioLabel,
  computeSalience, majorNodeSet, computeDepthsLocal,
  layoutReading, layoutSwimlane, layoutDag,
  buildAnchorIndex, neighborhood, classifyVisibleEdge, ACTIVE_EDGE_KINDS, EDGE_KINDS,
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

const ASSESSMENT_KIND_LABELS: Record<string, string> = {
  weaken_condition: "条件更弱",
  strengthen_or_boundary: "条件更强 / 边界",
  vary_value_or_object: "改变数值或对象",
  proof_detail: "证明细节",
  principle_boundary: "定义原理与边界",
  motivation: "定义动机",
  application: "典型用途",
  distinction_counterexample: "辨析或反例",
  core_meaning: "核心含义",
  condition_change: "条件变化",
  transfer_application: "迁移应用",
  reasoning_detail: "推理细节",
};

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
  learningAssignment?: EducationAssignment | null;
  onOpenEducation?: () => void;
  onImportCourse?: () => void;
  onSetLearningTarget?: (node: GraphNode) => void;
  onLearningAssignmentChange?: Dispatch<SetStateAction<EducationAssignment | null>>;
  onLearningDirtyChange?: (dirty: boolean) => void;
  courseGraphMode?: boolean;
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
type AssessmentFrame = PdfPeekSize & { left: number; top: number };

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
  onReset, onShowApiGuide, onExport, exporting, learningAssignment,
  onOpenEducation, onImportCourse, onSetLearningTarget, onLearningAssignmentChange, onLearningDirtyChange,
  courseGraphMode,
}: GraphStudioProps) {
  const [settings, setSettings] = useState<StudioSettings>(() => loadStudioSettings());
  const theme = resolveTheme(settings.theme);
  const lang = nodeLanguage;

  const canvasRef = useRef<HTMLDivElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
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
  const [activePopup, setActivePopup] = useState<"pdf" | "assessment">("assessment");
  const [pdfPeekWidthReady, setPdfPeekWidthReady] = useState(true);
  const [pdfLoadingId, setPdfLoadingId] = useState<number | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [tip, setTip] = useState<{ id: number; x: number; y: number } | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);
  const [tipPos, setTipPos] = useState<{ left: number; top: number } | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [learningDirty, setLearningDirty] = useState(false);
  const [learningDetailOpen, setLearningDetailOpen] = useState(false);
  const [assessmentNodeId, setAssessmentNodeId] = useState<number | null>(null);
  const [assessmentInitialFrame, setAssessmentInitialFrame] = useState<AssessmentFrame>({ left: 24, top: 24, width: 920, height: 720 });
  const assessmentOpenRef = useRef(false);
  const [studentGradeReport, setStudentGradeReport] = useState<EducationSubmission | null>(null);
  const hasLearningAssignment = Boolean(learningAssignment);
  const latexMacros = result.latex_macros;
  const [studentContextStates, setStudentContextStates] = useState<StudentNodeContextState[]>([]);
  const refreshStudentContextOverview = useCallback(async () => {
    if (!learningAssignment || learningAssignment.role !== "student" || !token) {
      setStudentContextStates([]);
      return;
    }
    try {
      const overview = await loadStudentContext(token, learningAssignment.id);
      setStudentContextStates(overview.nodeStates || []);
    } catch {
      // The proof workspace surfaces actionable context errors.  The graph
      // remains usable if this lightweight overview cannot be refreshed.
    }
  }, [learningAssignment?.id, learningAssignment?.role, token]);

  useEffect(() => {
    void refreshStudentContextOverview();
  }, [refreshStudentContextOverview]);

  const studentContextByNode = useMemo(
    () => new Map(studentContextStates.map(state => [state.nodeId, state])),
    [studentContextStates],
  );

  const closeEducation = useCallback(() => {
    if (learningDirty && !window.confirm("当前作业有未保存修改，确定离开吗？")) return;
    setLearningDirty(false);
    onOpenEducation?.();
  }, [learningDirty, onOpenEducation]);
  useEffect(() => {
    onNodeSelectionChange?.(selectedId !== null);
    return () => onNodeSelectionChange?.(false);
  }, [onNodeSelectionChange, selectedId]);

  useEffect(() => {
    onLearningDirtyChange?.(hasLearningAssignment && learningDirty);
    return () => onLearningDirtyChange?.(false);
  }, [hasLearningAssignment, learningDirty, onLearningDirtyChange]);

  useEffect(() => {
    assessmentOpenRef.current = assessmentNodeId !== null;
  }, [assessmentNodeId]);

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
    setActivePopup("pdf");
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
    const node = result.nodes.find(item => item.id === nodeId);
    if (!node) return;
    // Keep the selected node and the source window in sync. The source window
    // closes when selection changes, so opening a step's source must select
    // that step before setting the peek state.
    setSelectedId(nodeId);
    setSelectedEdge(null);
    netRef.current?.selectNodes([nodeId]);
    const sourcePdf = result.source_pdf;
    if (!sourcePdf || !graphId) {
      openSourcePeek(nodeId);
      return;
    }
    const status = sourcePdf.status ?? (sourcePdf.available ? "ready" : sourcePdf.error ? "failed" : "compiling");
    const frame = getCanvasPopupFrame(920, 720);
    setPdfPeekWidthReady(status !== "ready");
    setPdfPeekPos({ left: frame.left, top: frame.top });
    setPdfPeekSize({ width: frame.width, height: frame.height });
    setActivePopup("pdf");
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
      const locatePath = sourcePdf.locate_url
        ? `${sourcePdf.locate_url}${sourcePdf.locate_url.includes("?") ? "&" : "?"}node_id=${encodeURIComponent(nodeId)}`
        : `/api/v2/source-pdf/${encodeURIComponent(graphId)}/locate?node_id=${encodeURIComponent(nodeId)}`;
      const res = await fetch(apiUrl(locatePath), {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      if (!res.ok) throw new Error("PDF locate failed");
      const loc = await res.json();
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
  const learningStepById = useMemo(
    () => new Map((learningAssignment?.path.steps ?? []).map(step => [step.nodeId, step])),
    [learningAssignment?.path.steps],
  );
  const learningAssessmentById = useMemo(
    () => new Map((learningAssignment?.assessments ?? []).map(assessment => [assessment.nodeId, assessment])),
    [learningAssignment?.assessments],
  );
  const learningCanvas = useMemo(
    () => learningAssignment ? learningCanvasEdges(learningAssignment.path, edges) : null,
    [edges, learningAssignment],
  );
  const canvasEdges = learningCanvas?.edges ?? edges;
  const learningPathEdgeCount = learningCanvas?.pathEdgeCount ?? 0;
  const salience = useMemo(() => computeSalience(nodes, edges), [nodes, edges]);
  const depths = useMemo(() => computeDepthsLocal(nodes, edges), [nodes, edges]);
  const majorSet = useMemo(() => majorNodeSet(nodes, salience, settings.density), [nodes, salience, settings.density]);
  const edgeKinds = useMemo(() => canvasEdges.map(classifyVisibleEdge), [canvasEdges]);
  const typeCounts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const n of nodes) c[n.node_type] = (c[n.node_type] ?? 0) + 1;
    return c;
  }, [nodes]);
  const presentTypes = useMemo(
    () => [...STUDIO_NODE_TYPES, ...[...new Set(nodes.map(n => n.node_type))].filter(t => !STUDIO_NODE_TYPES.includes(t))]
      .filter(t => typeCounts[t]), [nodes, typeCounts]);
  const presentKinds = useMemo(
    () => [...new Set(edgeKinds)].filter(kind => ACTIVE_EDGE_KINDS.includes(kind)),
    [edgeKinds],
  );

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
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failedPolls = 0;
    const pollPdf = async () => {
      try {
        const statusRes = await fetch(apiUrl(`/api/v2/jobs/${encodeURIComponent(graphId)}/status`));
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
        failedPolls = 0;
        const sourcePdf = jobStatus.source_pdf;
        const status = sourcePdf?.status ?? (sourcePdf?.available ? "ready" : sourcePdf?.error ? "failed" : "compiling");
        if (status === "failed") {
          if (!stopped) setPdfPeek(prev => prev ? { ...prev, status: "failed", error: sourcePdf?.error || "PDF 编译失败" } : prev);
          return;
        }
        if (status === "ready" && sourcePdf?.available) {
          const nodeId = pdfPeek.node.id;
          const locateRes = await fetch(apiUrl(`/api/v2/source-pdf/${encodeURIComponent(graphId)}/locate?node_id=${encodeURIComponent(nodeId)}`));
          if (!locateRes.ok) throw new Error("PDF locate failed");
          const loc = await locateRes.json();
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
  }, [graphId, pdfPeek]);

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
    const learningStep = learningStepById.get(n.id);
    const studentContext = studentContextByNode.get(n.id);
    const hasDirectRisk = studentContext?.masteryState === "needs_review";
    const hasRelatedRisk = !hasDirectRisk && Boolean(studentContext?.riskSummary?.items?.length);
    const major = Boolean(learningStep || studentContext) || majorSet.has(n.id);
    const sal = salience[n.id] ?? 0;
    const visible = activeTypes.has(n.node_type);
    const dim = learningAssignment ? !learningStep : Boolean(focusSet && !focusSet.has(n.id));
    const pos = positions?.[n.id];
    const draftOptional = Boolean(
      learningAssignment?.role === "teacher"
      && learningAssignment.status === "draft"
      && learningStep
      && !learningStep.required
      && learningStep.role !== "target",
    );
    const learningBorder = draftOptional
      ? (dark ? "#d6b271" : "#c88422")
      : learningStep?.state === "mastered"
      ? (dark ? "#6fcf97" : "#2f7d56")
      : learningStep?.state === "needs_review" || learningStep?.role === "remedial"
        ? (dark ? "#e3b565" : "#c88422")
        : learningStep ? (dark ? "#7fb0e8" : "#2563aa") : null;
    const contextBorder = hasDirectRisk
      ? (dark ? "#e68178" : "#b94a43")
      : hasRelatedRisk ? (dark ? "#e3b565" : "#c88422") : null;
    const border = contextBorder || learningBorder || (dark ? st.borderDark : st.border);
    // major nodes: tinted card; minor nodes: a solid colored dot (legible on both themes)
    const learningBg = draftOptional
      ? (dark ? "#3c3020" : "#fff3dc")
      : learningStep?.state === "mastered"
      ? (dark ? "#24382e" : "#e8f5ee")
      : learningStep?.state === "needs_review" || learningStep?.role === "remedial"
        ? (dark ? "#3c3020" : "#fff3dc")
        : learningStep ? (dark ? "#253444" : "#eaf2fc") : null;
    const contextBg = hasDirectRisk
      ? (dark ? "#422725" : "#fff0ee")
      : hasRelatedRisk ? (dark ? "#3c3020" : "#fff8e6") : null;
    const bg = contextBg || learningBg || (major ? (dark ? st.bgDark : st.bg) : border);
    return {
      id: n.id,
      label: major ? `${hasDirectRisk ? "⚠ " : hasRelatedRisk ? "◇ " : ""}${learningStep ? `${learningStep.order}. ` : ""}${studioLabel(n, lang)}` : " ",
      shape: major ? st.shape : "dot",
      size: major ? undefined : 5 + sal * 9,
      hidden: !visible,
      opacity: dim ? (learningAssignment ? (dark ? 0.18 : 0.12) : (dark ? 0.28 : 0.18)) : 1,
      borderWidth: hasDirectRisk ? 3 : hasRelatedRisk ? 2.2 : learningStep?.role === "target" ? 4 : learningStep ? 2.2 : 1.5,
      shadow: learningStep?.role === "target" ? { enabled: true, color: dark ? "rgba(127,176,232,.3)" : "rgba(37,99,170,.24)", size: 12, x: 0, y: 0 } : false,
      color: {
        background: bg, border,
        highlight: { background: bg, border: dark ? "#7fb0e8" : "#1e5aa8" },
        hover: { background: bg, border },
      },
      font: { color: dim ? "rgba(0,0,0,0)" : fontColor, size: major ? 12.5 + sal * 5 : 1, face: "Inter",
        strokeWidth: dark ? 0 : 3, strokeColor: dark ? "#1c1b19" : "#f1efe9", multi: false },
      shapeProperties: { borderDashes: hasRelatedRisk ? [5, 3] : false },
      ...(pos ? { x: pos.x, y: pos.y, fixed: { x: false, y: false } } : {}),
    } as Record<string, unknown>;
  }, [majorSet, learningStepById, studentContextByNode, salience, activeTypes, learningAssignment, focusSet, positions, lang, dark, fontColor]);

  const buildEdge = useCallback((e: GraphEdge, i: number) => {
    const kind = edgeKinds[i];
    const meta = EDGE_KINDS[kind];
    const isLearningEdge = Boolean(learningAssignment && i < learningPathEdgeCount);
    const inFocus = learningAssignment ? isLearningEdge : (!focusSet || (focusSet.has(e.from) && focusSet.has(e.to)));
    // In learning mode only the frozen path edges may be emphasized. The
    // ordinary graph focus rule would otherwise brighten every stored edge
    // incident to the selected node, including edges outside the assignment.
    const incident = selectedId !== null
      && (!learningAssignment || isLearningEdge)
      && (e.from === selectedId || e.to === selectedId);
    const visible = activeTypes.has(nodeById.get(e.from)?.node_type ?? "") && activeTypes.has(nodeById.get(e.to)?.node_type ?? "");
    // dark uses dimmed same-hue colours, so focused edges can sit at the same
    // opacity as light without glaring; background edges are pushed further down.
    const baseOp = 0.5;
    const edgeColor = isLearningEdge ? (dark ? "#7fb0e8" : "#2563aa") : (dark ? (meta.colorDark ?? meta.color) : meta.color);
    return {
      id: i, from: e.from, to: e.to,
      hidden: !visible,
      label: "",
      color: { color: edgeColor, opacity: incident || isLearningEdge ? 1 : inFocus ? baseOp : (learningAssignment ? 0.06 : (dark ? 0.1 : 0.12)), highlight: edgeColor, inherit: false },
      width: incident || isLearningEdge ? 2.4 : dark ? 0.9 : 1,
      dashes: isLearningEdge ? false : meta.dashed ? [4, 4] : false,
      smooth: settings.curvedEdges ? { enabled: true, type: "dynamic", roundness: 0.5 } : false,
      arrows: { to: { enabled: true, scaleFactor: 0.45 } },
    } as Record<string, unknown>;
  }, [edgeKinds, focusSet, learningAssignment, learningPathEdgeCount, selectedId, activeTypes, nodeById, settings.curvedEdges, dark]);

  // ── (Re)create network on structural changes ─────────────────────────────────
  useEffect(() => {
    if (!canvasRef.current) return;
    const dsN = new DataSet(nodes.map(buildNode));
    const dsE = new DataSet(canvasEdges.map(buildEdge));
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
      if (p.nodes.length) {
        selectNode(p.nodes[0]);
        if (learningAssignment && assessmentOpenRef.current) {
          setPanelTab("detail");
          setLearningDetailOpen(true);
        }
      }
      else if (p.edges.length && !learningAssignment) { setSelectedEdge(p.edges[0]); setSelectedId(null); }
      else {
        setSelectedId(null);
        setSelectedEdge(null);
        if (learningAssignment) setLearningDetailOpen(false);
      }
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
  }, [result, settings.layout, theme, lang, learningAssignment?.id, canvasEdges.length]);

  // ── Light updates (filters / density / focus / edge style) without rebuild ────
  useEffect(() => {
    nodesDS.current?.update(nodes.map(buildNode));
  }, [nodes, buildNode]);
  useEffect(() => {
    edgesDS.current?.update(canvasEdges.map(buildEdge));
  }, [canvasEdges, buildEdge]);

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

  const openLearningDetail = useCallback((id: number) => {
    setPanelTab("detail");
    setLearningDetailOpen(true);
    focusOnNode(id);
  }, [focusOnNode]);

  const openLearningAssessment = useCallback((id: number) => {
    const frame = getCanvasPopupFrame(920, 720);
    setAssessmentInitialFrame({ left: frame.left, top: frame.top, width: frame.width, height: frame.height });
    setActivePopup("assessment");
    setAssessmentNodeId(id);
  }, [getCanvasPopupFrame]);

  const updateAssessmentAttemptStatus = useCallback((nodeId: number, status: "draft" | "completed") => {
    if (!learningAssignment) return;
    onLearningAssignmentChange?.({
      ...learningAssignment,
      assessments: learningAssignment.assessments.map(assessment => (
        assessment.nodeId === nodeId ? { ...assessment, attemptStatus: status } : assessment
      )),
    });
  }, [learningAssignment, onLearningAssignmentChange]);

  const completeLearningAssessment = useCallback((nodeId: number, path: EducationAssignment["path"]) => {
    if (!learningAssignment) return;
    onLearningAssignmentChange?.({
      ...learningAssignment,
      path,
      assessments: learningAssignment.assessments.map(assessment => (
        assessment.nodeId === nodeId ? { ...assessment, attemptStatus: "completed" } : assessment
      )),
    });
    setAssessmentNodeId(null);
  }, [learningAssignment, onLearningAssignmentChange]);

  const returnToLearningPath = useCallback(() => {
    setLearningDetailOpen(false);
    setPanelTab("detail");
  }, []);

  useEffect(() => {
    setLearningDetailOpen(false);
    setAssessmentNodeId(null);
  }, [learningAssignment?.id]);

  useEffect(() => {
    if (!learningAssignment) return;
    const firstStep = learningAssignment.path.steps.find(step => step.state !== "mastered")
      ?? learningAssignment.path.steps[learningAssignment.path.steps.length - 1];
    if (!firstStep) return;
    const frame = window.requestAnimationFrame(() => focusOnNode(firstStep.nodeId));
    return () => window.cancelAnimationFrame(frame);
  // Focus once when a learning assignment is opened; progress updates keep the learner's current focus.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [learningAssignment?.id]);

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
  const showLearningDetail = Boolean(learningAssignment && learningDetailOpen && selected);
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
    <div ref={rootRef} className="gs-root" data-theme={theme}>
      {/* ── Top bar ── */}
      <div className="gs-topbar">
        <button className="gs-iconbtn gs-rail-toggle" title={railOpen ? "收起侧栏" : "展开侧栏"} onClick={() => setRailOpen(v => !v)}>
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
          <button className="gs-iconbtn" title="明暗" onClick={() => update({ theme: theme === "dark" ? "light" : "dark" })}>
            {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
          </button>
          <button className={`gs-iconbtn ${showSettings ? "active" : ""}`} title="个性化" onClick={() => setShowSettings(v => !v)}><Settings2 size={17} /></button>
          {token && onLoadHistory && (
            <button className="gs-iconbtn" title="历史记录" onClick={() => setShowHistory(true)}><History size={17} /></button>
          )}
        </div>
        <div className="gs-toolbar-actions">
          {learningAssignment && onOpenEducation && !courseGraphMode && (
            <button className="gs-btn gs-btn-ghost gs-action-button gs-learning-action" onClick={closeEducation} title="学习空间">
              <GraduationCap size={16} /><span className="gs-action-label">学习空间</span>
            </button>
          )}
          {courseGraphMode && onOpenEducation && (
            <button className="gs-btn gs-btn-ghost gs-action-button gs-learning-action" onClick={closeEducation} title="学习空间">
              <GraduationCap size={16} /><span className="gs-action-label">学习空间</span>
            </button>
          )}
          {!learningAssignment && onImportCourse && !courseGraphMode && (
            <button className="gs-btn gs-btn-ghost gs-action-button gs-learning-action" onClick={onImportCourse} title="导入课程">
              <GraduationCap size={16} /><span className="gs-action-label">导入课程</span>
            </button>
          )}
          {!learningAssignment && !onImportCourse && onOpenEducation && !courseGraphMode && (
            <button className="gs-btn gs-btn-ghost gs-action-button gs-learning-action" onClick={closeEducation} title="学习路径">
              <RouteIcon size={16} /><span className="gs-action-label">学习路径</span>
            </button>
          )}
          {!learningAssignment && !courseGraphMode && (
            <button className="gs-btn gs-btn-ghost gs-action-button gs-reset-action" onClick={onReset} title="重新上传">
              <Upload size={16} /><span className="gs-action-label">重新上传</span>
            </button>
          )}
          <div className="gs-export" ref={exportMenuRef}>
            <button
              className={`gs-btn gs-btn-ghost gs-action-button ${exportMenuOpen ? "active" : ""}`}
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

        {pdfPeek && pdfPeekFrame && createPortal(
          <div className="gs-pdf-peek" onClick={() => setActivePopup("pdf")} style={{ left: pdfPeekPos.left, top: pdfPeekPos.top, width: pdfPeekFrame.width, height: pdfPeekFrame.height, zIndex: activePopup === "pdf" ? 62 : 61, visibility: pdfPeek.status === "ready" && !pdfPeekWidthReady ? "hidden" : undefined }}>
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
          </div>,
          rootRef.current ?? document.body,
        )}

        {/* Learning path panel */}
        {learningAssignment && !showLearningDetail && (
          <>
            {!panelExpanded && <div className="gs-resize" onMouseDown={handleResizeStart} title="拖动调整宽度" />}
            <div className={`gs-panel gs-learning-panel ${panelExpanded ? "expanded" : ""}`} style={panelExpanded ? undefined : { width: panelWidth }}>
              <div className="gs-panel-head gs-learning-panel-head">
                <span className="gs-learning-panel-title"><GraduationCap size={16} />{learningAssignment.role === "teacher" && learningAssignment.status === "draft" ? "发布前确认" : "学习步骤"}</span>
                <span className="gs-learning-progress-text">{learningAssignment.status === "draft" ? "草稿" : learningAssignment.role === "student" ? `${studentAssignmentCompletion(learningAssignment).completed}/${studentAssignmentCompletion(learningAssignment).total}` : `${learningAssignment.path.steps.filter(step => step.state === "mastered").length}/${learningAssignment.path.steps.length}`}</span>
                <button className="gs-panel-expand gs-iconbtn" title={panelExpanded ? "退出展开" : "展开学习步骤"} onClick={() => { setTip(null); setPanelExpanded(value => !value); }}>
                  {panelExpanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
                </button>
                <button className="gs-panel-close gs-iconbtn" title="返回教育空间" onClick={closeEducation}><X size={16} /></button>
              </div>
              <div className="gs-panel-body">
                <LearningPathPanel
                  assignment={learningAssignment}
                  nodeById={nodeById}
                  activeNodeId={selectedId}
                  token={token}
                  macros={latexMacros}
                  onFocus={focusOnNode}
                  onOpenSource={openPdfPeek}
                  onOpenDetail={openLearningDetail}
                  onStartAssessment={openLearningAssessment}
                  onShowGradeReport={setStudentGradeReport}
                  onChange={onLearningAssignmentChange}
                  onDirtyChange={setLearningDirty}
                />
              </div>
            </div>
          </>
        )}

        {/* Right panel */}
        {(!learningAssignment || showLearningDetail) && (selected || selectedEdge !== null || panelTab === "reading") && (
          <>
          {!panelExpanded && <div className="gs-resize" onMouseDown={handleResizeStart} title="拖动调整宽度" />}
          <div className={`gs-panel ${panelExpanded ? "expanded" : ""}`} style={panelExpanded ? undefined : { width: panelWidth }}>
            <div className="gs-panel-head">
              {showLearningDetail && (
                <button type="button" className="gs-panel-tab gs-learning-back-tab" onClick={returnToLearningPath}>
                  <RouteIcon size={13} />学习路径
                </button>
              )}
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
              <button className="gs-panel-close gs-iconbtn" title={showLearningDetail ? "返回学习路径" : "关闭详情"} onClick={() => {
                if (showLearningDetail) {
                  returnToLearningPath();
                  return;
                }
                setSelectedId(null);
                setSelectedEdge(null);
                setPanelTab("detail");
                netRef.current?.unselectAll();
              }}><X size={16} /></button>
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
                      matrixFlowAudience={learningAssignment?.role === "student" ? "student" : "author"}
                      matrixFlowLayoutMode={panelExpanded ? "horizontal" : "vertical"}
                      onSetLearningTarget={onSetLearningTarget}
                      learningStep={learningAssignment?.role === "student" && selectedId !== null ? learningStepById.get(selectedId) : undefined}
                      learningAssessment={learningAssignment?.role === "student" && selectedId !== null ? learningAssessmentById.get(selectedId) : undefined}
                      learningAssignmentId={learningAssignment?.role === "student" ? learningAssignment.id : undefined}
                      learningClassId={learningAssignment?.role === "student" ? learningAssignment.classId : undefined}
                      learningSubmitted={Boolean(learningAssignment?.submission)}
                      studentContextState={selectedId !== null ? studentContextByNode.get(selectedId) : undefined}
                      onStudentContextChange={refreshStudentContextOverview}
                      onStartAssessment={openLearningAssessment}
                    />}
            </div>
          </div>
          </>
        )}
      </div>

      {learningAssignment && assessmentNodeId !== null && token
        && nodeById.get(assessmentNodeId) && learningAssessmentById.get(assessmentNodeId) && createPortal(
        <AssessmentDialog
          key={`${learningAssignment.id}:${assessmentNodeId}`}
          assignment={learningAssignment}
          node={nodeById.get(assessmentNodeId)!}
          assessment={learningAssessmentById.get(assessmentNodeId)!}
          token={token}
          macros={latexMacros}
          initialFrame={assessmentInitialFrame}
          getWindowPopupFrame={getWindowPopupFrame}
          popupZIndex={activePopup === "assessment" ? 62 : 61}
          onActivate={() => setActivePopup("assessment")}
          onAttemptStarted={(status) => updateAssessmentAttemptStatus(assessmentNodeId, status)}
          onComplete={(path) => completeLearningAssessment(assessmentNodeId, path)}
          onClose={() => setAssessmentNodeId(null)}
        />,
        rootRef.current ?? document.body,
      )}

      {studentGradeReport && createPortal(
        <StudentGradeReportDialog submission={studentGradeReport} macros={latexMacros} onClose={() => setStudentGradeReport(null)} />,
        rootRef.current ?? document.body,
      )}

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
function AssessmentQuestionScoringEditor({ token, assignmentId, nodeId, question, disabled, onSaved }: {
  token?: string;
  assignmentId: string;
  nodeId: number;
  question: AssessmentQuestion;
  disabled: boolean;
  onSaved: (assessment: NodeAssessment) => void;
}) {
  const [referenceAnswer, setReferenceAnswer] = useState(question.referenceAnswer || "");
  const [expectedPoints, setExpectedPoints] = useState((question.expectedPoints || []).join("\n"));
  const [maxScore, setMaxScore] = useState(String(question.maxScore || ""));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setReferenceAnswer(question.referenceAnswer || "");
    setExpectedPoints((question.expectedPoints || []).join("\n"));
    setMaxScore(String(question.maxScore || ""));
    setError("");
  }, [question.id, question.referenceAnswer, question.expectedPoints, question.maxScore]);

  const save = async () => {
    const points = expectedPoints.split(/\r?\n/).map(point => point.trim()).filter(Boolean);
    const score = Math.round(Number(maxScore) * 10) / 10;
    if (!token) { setError("请先登录"); return; }
    if (!referenceAnswer.trim() || points.length === 0 || !Number.isFinite(score) || score <= 0) {
      setError("请填写参考答案、至少一个评分点和大于 0 的分值。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      onSaved(await updateEducationAssessmentQuestion(token, assignmentId, nodeId, question.id, { referenceAnswer: referenceAnswer.trim(), expectedPoints: points, maxScore: score }));
    } catch (cause) {
      setError(educationErrorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  return <div className="gs-assessment-scoring-editor">
    <label><span>参考答案</span><textarea value={referenceAnswer} disabled={disabled || busy} onChange={event => setReferenceAnswer(event.target.value)} placeholder="填写教师审核后的参考答案" /></label>
    {question.referenceMatrixReport?.status === "contradicted" || question.referenceMatrixReport?.status === "structural_invalid" ? <div className="gs-reference-matrix-alert blocking"><AlertTriangle size={13} /><span><strong>参考答案存在确定性矩阵错误，发布已阻止</strong><small>{question.referenceMatrixReport.summary} 修改后请保存评分标准重新检查。</small></span></div> : question.referenceMatrixReport?.status === "indeterminate" ? <div className="gs-reference-matrix-alert warning"><AlertTriangle size={13} /><span><strong>参考答案的矩阵过程需要人工复核</strong><small>{question.referenceMatrixReport.summary} 不会仅因此判定错误。</small></span></div> : null}
    <label><span>评分点 <small>每行一项</small></span><textarea value={expectedPoints} disabled={disabled || busy} onChange={event => setExpectedPoints(event.target.value)} placeholder="关键结论&#10;关键推导步骤" /></label>
    <div className="gs-assessment-score-row"><label><span>本题满分</span><input type="number" min="0.1" max="100" step="0.1" value={maxScore} disabled={disabled || busy} onChange={event => setMaxScore(event.target.value)} /></label><button type="button" className="gs-btn gs-btn-ghost" disabled={disabled || busy} onClick={() => void save()}>{busy ? <Loader2 size={12} className="gs-spin" /> : <Save size={12} />}保存评分标准</button></div>
    {error && <div className="gs-learning-inline-error">{error}</div>}
  </div>;
}

function LearningPathPanel({ assignment, nodeById, activeNodeId, token, macros, onFocus, onOpenSource, onOpenDetail, onStartAssessment, onShowGradeReport, onChange, onDirtyChange }: {
  assignment: EducationAssignment;
  nodeById: Map<number, GraphNode>;
  activeNodeId: number | null;
  token?: string;
  macros?: LatexMacros;
  onFocus: (nodeId: number) => void;
  onOpenSource: (nodeId: number) => void;
  onOpenDetail: (nodeId: number) => void;
  onStartAssessment: (nodeId: number) => void;
  onShowGradeReport: (submission: EducationSubmission) => void;
  onChange?: Dispatch<SetStateAction<EducationAssignment | null>>;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const [expandedWhy, setExpandedWhy] = useState<Set<number>>(new Set());
  const [progressBusyNode, setProgressBusyNode] = useState<number | null>(null);
  const [progressErrorNode, setProgressErrorNode] = useState<number | null>(null);
  const [progressError, setProgressError] = useState("");
  const [expandedAssessments, setExpandedAssessments] = useState<Set<number>>(new Set());
  const [assessmentOperations, dispatchAssessmentOperation] = useReducer(assessmentOperationReducer, { operations: [] });
  const [assessmentErrors, setAssessmentErrors] = useState<Record<number, string>>({});
  const startedAssessmentOperations = useRef(new Set<string>());
  const [error, setError] = useState("");
  const [draftBusy, setDraftBusy] = useState<"save" | "publish" | null>(null);
  const [draftWarnings, setDraftWarnings] = useState<PathOrderWarning[]>([]);
  const [draftDirty, setDraftDirty] = useState(false);
  const [draftFeedback, setDraftFeedback] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const [batchAssessmentBusy, setBatchAssessmentBusy] = useState(false);
  const [submissionBusy, setSubmissionBusy] = useState<"submit" | "report" | null>(null);
  const [submissionError, setSubmissionError] = useState("");
  const isDraft = assignment.role === "teacher" && assignment.status === "draft";
  const mastered = assignment.path.steps.filter(step => step.state === "mastered").length;
  const studentCompletion = studentAssignmentCompletion(assignment);
  const scoringSummary = assessmentScoringSummary(assignment);
  const progressCompleted = assignment.role === "student" ? studentCompletion.completed : mastered;
  const progressTotal = assignment.role === "student" ? studentCompletion.total : assignment.path.steps.length;
  const unresolvedNodeIds = unresolvedAssessmentNodeIds(assignment);
  const unresolvedAssessments = unresolvedNodeIds.length;
  const assessmentCounts = assessmentOperationCounts(assessmentOperations);
  const assessmentOperationsActive = assessmentCounts.queued + assessmentCounts.running > 0;
  const assessmentBusy = assessmentOperationsActive || batchAssessmentBusy;
  const assignmentRef = useRef(assignment);

  useEffect(() => {
    dispatchAssessmentOperation({ type: "reset" });
    setAssessmentErrors({});
    setSubmissionError("");
    startedAssessmentOperations.current.clear();
  }, [assignment.id]);

  useEffect(() => {
    if (progressBusyNode === null) assignmentRef.current = assignment;
  }, [assignment, progressBusyNode]);

  useEffect(() => {
    onDirtyChange?.(isDraft && draftDirty);
    return () => onDirtyChange?.(false);
  }, [draftDirty, isDraft, onDirtyChange]);

  const updateDraft = (next: EducationAssignment) => {
    onChange?.(next);
    setDraftDirty(true);
  };

  const updateDraftStep = (nodeId: number, patch: Partial<LearningPathStep>) => {
    if (assessmentBusy) return;
    updateDraft({
      ...assignment,
      path: {
        ...assignment.path,
        steps: assignment.path.steps.map(step => step.nodeId === nodeId ? { ...step, ...patch } : step),
      },
    });
  };

  const moveDraftStep = (index: number, offset: number) => {
    if (assessmentBusy) return;
    const targetIndex = index + offset;
    if (!isDraft || targetIndex < 0 || targetIndex >= assignment.path.steps.length) return;
    if (assignment.path.steps[index].role === "target" || assignment.path.steps[targetIndex].role === "target") return;
    const steps = [...assignment.path.steps];
    [steps[index], steps[targetIndex]] = [steps[targetIndex], steps[index]];
    updateDraft({ ...assignment, path: { ...assignment.path, steps: steps.map((step, order) => ({ ...step, order: order + 1 })) } });
  };

  const removeDraftStep = (step: LearningPathStep) => {
    if (!isDraft || assessmentBusy || step.required || step.role === "target") return;
    updateDraft({
      ...assignment,
      path: {
        ...assignment.path,
        steps: assignment.path.steps.filter(item => item.nodeId !== step.nodeId).map((item, order) => ({ ...item, order: order + 1 })),
      },
    });
  };

  const saveDraft = async () => {
    if (!token || !isDraft || draftBusy || assessmentBusy) return;
    setDraftBusy("save"); setError(""); setDraftFeedback(null);
    try {
      const body = await saveEducationAssignment(token, assignment);
      onChange?.(body.assignment);
      setDraftWarnings(body.warnings ?? []);
      setDraftDirty(false);
      setDraftFeedback({ kind: "success", text: "草稿已保存" });
    } catch (cause) {
      setDraftFeedback({ kind: "error", text: educationErrorMessage(cause) });
    }
    finally { setDraftBusy(null); }
  };

  const publishDraft = async () => {
    if (!token || !isDraft || draftBusy || assessmentBusy) return;
    if (unresolvedNodeIds.length > 0) {
      const firstNodeId = unresolvedNodeIds[0];
      setExpandedAssessments(current => new Set(current).add(firstNodeId));
      onFocus(firstNodeId);
      window.requestAnimationFrame(() => document.getElementById(`gs-assessment-step-${firstNodeId}`)?.scrollIntoView({ behavior: "smooth", block: "center" }));
      setDraftFeedback({ kind: "error", text: `还有 ${unresolvedNodeIds.length} 个节点的考察题未处理，请重新生成或设为免考。` });
      return;
    }
    if (!scoringSummary.ready) {
      const invalidQuestion = scoringSummary.questions.find(question => scoringSummary.invalidQuestionIds.includes(question.id));
      if (invalidQuestion) {
        setExpandedAssessments(current => new Set(current).add(invalidQuestion.nodeId));
        onFocus(invalidQuestion.nodeId);
      }
      setDraftFeedback({ kind: "error", text: scoringSummary.invalidQuestionIds.length > 0 ? "请补齐每题参考答案、评分点和有效分值。" : `当前总分为 ${scoringSummary.totalScore.toFixed(1)}，请调整为 100 分。` });
      return;
    }
    setDraftBusy("publish"); setError(""); setDraftFeedback(null);
    try {
      const saved = await saveEducationAssignment(token, assignment);
      onChange?.(saved.assignment);
      setDraftWarnings(saved.warnings ?? []);
      setDraftDirty(false);
      const published = await publishEducationAssignment(token, saved.assignment.id);
      onChange?.(published);
      setDraftFeedback({ kind: "success", text: "任务已发布" });
    } catch (cause) {
      const error = cause as { nodeIds?: number[] };
      const nodeIds = error.nodeIds?.length ? error.nodeIds : unresolvedAssessmentNodeIds(assignment);
      if (nodeIds.length > 0) {
        const firstNodeId = nodeIds[0];
        setExpandedAssessments(current => new Set(current).add(firstNodeId));
        onFocus(firstNodeId);
        window.requestAnimationFrame(() => document.getElementById(`gs-assessment-step-${firstNodeId}`)?.scrollIntoView({ behavior: "smooth", block: "center" }));
      }
      setDraftFeedback({ kind: "error", text: educationErrorMessage(cause) });
    }
    finally { setDraftBusy(null); }
  };

  const regenerateUnresolvedAssessments = async () => {
    if (!token || batchAssessmentBusy || assessmentOperationsActive || unresolvedNodeIds.length === 0) return;
    setBatchAssessmentBusy(true);
    setDraftFeedback(null);
    try {
      const body = await regenerateUnresolvedEducationAssessments(token, assignment.id);
      onChange?.(current => current && current.id === assignment.id
        ? { ...current, assessments: body.assessments }
        : current);
      if (body.failedNodeIds.length > 0) {
        const firstNodeId = body.failedNodeIds[0];
        setExpandedAssessments(current => new Set(current).add(firstNodeId));
        onFocus(firstNodeId);
        window.requestAnimationFrame(() => document.getElementById(`gs-assessment-step-${firstNodeId}`)?.scrollIntoView({ behavior: "smooth", block: "center" }));
        setDraftFeedback({ kind: "error", text: `已重试 ${body.retriedNodeIds.length} 个节点，仍有 ${body.failedNodeIds.length} 个节点需要处理。` });
      } else {
        setDraftFeedback({ kind: "success", text: `已重新生成 ${body.readyNodeIds.length} 个节点的考察题。` });
      }
    } catch (cause) {
      setDraftFeedback({ kind: "error", text: educationErrorMessage(cause) });
    } finally {
      setBatchAssessmentBusy(false);
    }
  };

  const submitAssignment = async () => {
    if (!token || assignment.role !== "student" || assignment.submission || submissionBusy || !studentCompletion.ready) return;
    setSubmissionBusy("submit");
    setSubmissionError("");
    try {
      const submission = await submitEducationAssignment(token, assignment.id);
      onChange?.(current => current && current.id === assignment.id ? { ...current, submission } : current);
    } catch (cause) {
      setSubmissionError(educationErrorMessage(cause));
    } finally {
      setSubmissionBusy(null);
    }
  };

  const viewGradeReport = async () => {
    if (!token || assignment.submission?.status !== "released" || submissionBusy) return;
    setSubmissionBusy("report");
    setSubmissionError("");
    try {
      onShowGradeReport(await loadEducationSubmission(token, assignment.submission.id));
    } catch (cause) {
      setSubmissionError(educationErrorMessage(cause));
    } finally {
      setSubmissionBusy(null);
    }
  };

  const updateProgress = async (step: LearningPathStep, state: LearningPathStep["state"]) => {
    if (progressBusyNode !== null) return;
    const previous = assignmentRef.current;
    const optimistic = {
      ...previous,
      path: {
        ...previous.path,
        steps: previous.path.steps.map(item => item.nodeId === step.nodeId ? { ...item, state } : item),
      },
    };
    assignmentRef.current = optimistic;
    onChange?.(optimistic);
    setProgressBusyNode(step.nodeId);
    setProgressErrorNode(null);
    setProgressError("");
    try {
      if (!token) throw new Error("请先登录");
      const body = await updateEducationProgress(token, assignment.id, step.nodeId, state);
      const next = { ...assignmentRef.current, path: body.path };
      assignmentRef.current = next;
      onChange?.(next);
    } catch (cause) {
      assignmentRef.current = previous;
      onChange?.(previous);
      setProgressErrorNode(step.nodeId);
      setProgressError(cause instanceof Error ? cause.message : "保存失败，请重试");
    } finally { setProgressBusyNode(null); }
  };

  const applyAssessment = useCallback((assignmentId: string, assessment: NodeAssessment) => {
    onChange?.(current => current && current.id === assignmentId
      ? replaceNodeAssessment(current, assessment)
      : current);
  }, [onChange]);

  const executeAssessmentOperation = useCallback(async (operation: AssessmentOperation) => {
    if (!token) throw new Error("请先登录");
    if (operation.kind === "regenerate_node") {
      return regenerateEducationAssessment(token, operation.assignmentId, operation.nodeId);
    }
    if (operation.kind === "regenerate_question") {
      if (!operation.questionId) throw new Error("考察题编号缺失");
      return regenerateEducationAssessmentQuestion(token, operation.assignmentId, operation.nodeId, operation.questionId);
    }
    if (operation.kind === "delete_question") {
      if (!operation.questionId) throw new Error("考察题编号缺失");
      return deleteEducationAssessmentQuestion(token, operation.assignmentId, operation.nodeId, operation.questionId);
    }
    return exemptEducationAssessment(token, operation.assignmentId, operation.nodeId);
  }, [token]);

  useEffect(() => {
    for (const operation of assessmentOperations.operations) {
      if (operation.status !== "running" || startedAssessmentOperations.current.has(operation.id)) continue;
      startedAssessmentOperations.current.add(operation.id);
      void executeAssessmentOperation(operation)
        .then(next => {
          if (operation.assignmentId !== assignment.id) return;
          applyAssessment(operation.assignmentId, next);
          setAssessmentErrors(current => {
            if (!(operation.nodeId in current)) return current;
            const updated = { ...current };
            delete updated[operation.nodeId];
            return updated;
          });
        })
        .catch(cause => {
          if (operation.assignmentId !== assignment.id) return;
          setAssessmentErrors(current => ({
            ...current,
            [operation.nodeId]: educationErrorMessage(cause),
          }));
        })
        .finally(() => {
          dispatchAssessmentOperation({ type: "complete", operationId: operation.id });
        });
    }
  }, [applyAssessment, assignment.id, assessmentOperations.operations, executeAssessmentOperation]);

  const enqueueAssessmentOperation = (nodeId: number, kind: AssessmentOperation["kind"], questionId?: string) => {
    if (!token || assessmentOperationForNode(assessmentOperations, nodeId)) return;
    setAssessmentErrors(current => {
      if (!(nodeId in current)) return current;
      const updated = { ...current };
      delete updated[nodeId];
      return updated;
    });
    dispatchAssessmentOperation({
      type: "enqueue",
      operation: {
        id: `${assignment.id}:${nodeId}:${kind}:${questionId || ""}:${Date.now()}:${Math.random()}`,
        assignmentId: assignment.id,
        nodeId,
        kind,
        questionId,
        usesAi: kind === "regenerate_node" || kind === "regenerate_question",
        status: "queued",
      },
    });
  };

  const regenerateAssessment = (nodeId: number) => {
    enqueueAssessmentOperation(nodeId, "regenerate_node");
  };

  const regenerateQuestion = (nodeId: number, questionId: string) => {
    enqueueAssessmentOperation(nodeId, "regenerate_question", questionId);
  };

  const deleteQuestion = (assessment: NodeAssessment, questionId: string) => {
    if (assessment.questionCount === 1 && !window.confirm("删除最后一道题后，该节点将设为免考。确认继续吗？")) return;
    enqueueAssessmentOperation(assessment.nodeId, "delete_question", questionId);
  };

  const exemptAssessment = (nodeId: number) => {
    if (!window.confirm("确认删除该节点全部考察题并设为免考吗？")) return;
    enqueueAssessmentOperation(nodeId, "exempt_node");
  };

  return (
    <div className="gs-learning-path">
      <div className="gs-learning-summary">
         <div className="gs-learning-summary-top"><span>{assignment.role === "teacher" ? "班级基础路径" : "整份作业完成进度"}</span><b>{progressCompleted}/{progressTotal}</b></div>
         <div className="gs-learning-progress"><span style={{ width: `${(progressCompleted / Math.max(1, progressTotal)) * 100}%` }} /></div>
         <p>{assignment.path.summary}</p>
         {assignment.path.hasCycles && <div className="gs-learning-cycle"><AlertTriangle size={13} />部分节点互相依赖，已放入同一学习阶段。</div>}
       </div>
      {error && <div className="gs-learning-error">{error}</div>}
      {isDraft && draftWarnings.length > 0 && <div className="gs-learning-cycle"><AlertTriangle size={13} /><span>发现 {draftWarnings.length} 处依赖顺序冲突，请确认前置节点的教学顺序。</span></div>}
      <div className="gs-learning-steps">
        {assignment.path.steps.map(step => {
          const node = nodeById.get(step.nodeId);
          const title = node?.title_zh || node?.title_en || node?.label || `节点 ${step.nodeId}`;
          const assessment = assignment.assessments.find(item => item.nodeId === step.nodeId);
          const assessmentOperation = assessmentOperationForNode(assessmentOperations, step.nodeId);
          const assessmentOperationBusy = Boolean(assessmentOperation);
          const assessmentOperationQueued = assessmentOperation?.status === "queued";
          const assessmentOperationError = assessmentErrors[step.nodeId];
          const collapsed = !step.required && step.state === "mastered" && progressBusyNode !== step.nodeId;
          const whyOpen = expandedWhy.has(step.nodeId);
          const assessmentOpen = expandedAssessments.has(step.nodeId);
          if (isDraft) {
            return (
              <article id={`gs-assessment-step-${step.nodeId}`} key={step.nodeId} className={`gs-learning-step draft ${step.role === "target" ? "target" : ""} ${activeNodeId === step.nodeId ? "active" : ""}`}>
                <button className="gs-learning-step-main" onClick={() => onFocus(step.nodeId)}>
                  <span className="gs-learning-step-index"><span>{step.order}</span></span>
                  <span className="gs-learning-step-copy"><strong><SmartTitle text={title} macros={macros} /></strong><small>{step.role === "target" ? "最终目标" : step.required ? "必修前置" : "建议前置"}</small></span>
                </button>
                <div className="gs-learning-step-interactions">
                <div className="gs-learning-draft-actions">
                  <button type="button" className="gs-learning-open-button" onClick={() => onOpenSource(step.nodeId)}>
                    <BookOpen size={14} /><span className="gs-learning-open-label">查看原文</span>
                  </button>
                  <button
                    type="button"
                    className={`gs-learning-required-toggle ${step.required ? "active" : ""}`}
                    aria-pressed={step.required}
                    disabled={assessmentBusy || step.role === "target"}
                    onClick={() => updateDraftStep(step.nodeId, { required: !step.required })}
                  >
                    <LockKeyhole size={14} />必修
                  </button>
                  <div className="gs-learning-step-tools">
                    <button type="button" title="上移" disabled={assessmentBusy || step.role === "target" || step.order === 1} onClick={() => moveDraftStep(step.order - 1, -1)}><ArrowUp size={14} /></button>
                    <button type="button" title="下移" disabled={assessmentBusy || step.role === "target" || step.order === assignment.path.steps.length - 1 || assignment.path.steps[step.order]?.role === "target"} onClick={() => moveDraftStep(step.order - 1, 1)}><ArrowDown size={14} /></button>
                    <button type="button" title="删除" disabled={assessmentBusy || step.required || step.role === "target"} onClick={() => removeDraftStep(step)}><Trash2 size={14} /></button>
                  </div>
                </div>
                <div className="gs-assessment-review">
                  <button
                    type="button"
                    className="gs-assessment-review-toggle"
                    onClick={() => setExpandedAssessments(current => {
                      const next = new Set(current);
                      next.has(step.nodeId) ? next.delete(step.nodeId) : next.add(step.nodeId);
                      return next;
                    })}
                  >
                    <Brain size={13} />
                    <span>考察题</span>
                    <b className={assessmentOperationBusy ? "pending" : assessment?.status || "pending"}>
                      {assessmentOperationQueued ? "排队中" : assessmentOperationBusy ? "生成中" : assessment?.status === "exempt" ? "免考" : assessment?.status === "failed" ? "生成失败" : assessment?.status === "ready" ? `${assessment.questionCount} 道` : "生成中"}
                    </b>
                    <ChevronDown size={13} className={assessmentOpen ? "open" : ""} />
                  </button>
                  {assessmentOpen && <div className="gs-assessment-review-body">
                    <div className="gs-assessment-review-actions">
                      <button type="button" disabled={assessmentOperationBusy} onClick={() => regenerateAssessment(step.nodeId)}>
                        {assessmentOperation?.kind === "regenerate_node" && !assessmentOperationQueued ? <Loader2 size={12} className="gs-spin" /> : <RotateCcw size={12} />}{assessmentOperation?.kind === "regenerate_node" && assessmentOperationQueued ? "排队中" : "重新生成 4 题"}
                      </button>
                      <button type="button" disabled={assessmentOperationBusy} onClick={() => exemptAssessment(step.nodeId)}>{assessmentOperation?.kind === "exempt_node" ? <Loader2 size={12} className="gs-spin" /> : <Trash2 size={12} />}设为免考</button>
                    </div>
                    {(assessmentOperationError || assessment?.status === "failed") && <div className="gs-assessment-generation-error"><AlertTriangle size={13} />{assessmentOperationError || assessmentGenerationErrorMessage(assessment)}</div>}
                    {assessment?.status === "exempt" && <div className="gs-assessment-exempt-note">该节点已免考，不纳入学生提交与评分。</div>}
                    {(assessment?.questions || []).map(question => <article key={question.id} className="gs-assessment-question-card">
                      <div className="gs-assessment-question-head"><span>{question.order}</span><b>{ASSESSMENT_KIND_LABELS[question.kind] || question.kind}</b><div>
                        <button type="button" title="重新生成此题" disabled={assessmentOperationBusy} onClick={() => regenerateQuestion(step.nodeId, question.id)}>{assessmentOperation?.kind === "regenerate_question" && !assessmentOperationQueued ? <Loader2 size={12} className="gs-spin" /> : <RotateCcw size={12} />}</button>
                        <button type="button" title="删除此题" disabled={assessmentOperationBusy} onClick={() => { if (assessment) deleteQuestion(assessment, question.id); }}>{assessmentOperation?.kind === "delete_question" ? <Loader2 size={12} className="gs-spin" /> : <Trash2 size={12} />}</button>
                      </div></div>
                      <div className="gs-assessment-question-text"><MathText text={question.question} macros={macros} /></div>
                      <small>检查重点：{question.focus}</small>
                      <AssessmentQuestionScoringEditor token={token} assignmentId={assignment.id} nodeId={step.nodeId} question={question} disabled={assessmentOperationBusy} onSaved={next => applyAssessment(assignment.id, next)} />
                    </article>)}
                  </div>}
                </div>
                </div>
              </article>
            );
          }
          return (
            <article key={step.nodeId} className={`gs-learning-step ${step.state} ${step.role === "target" ? "target" : ""} ${activeNodeId === step.nodeId ? "active" : ""} ${collapsed ? "collapsed" : ""}`}>
              <button className="gs-learning-step-main" onClick={() => onFocus(step.nodeId)}>
                <span className="gs-learning-step-index">{step.state === "mastered" ? <CheckCircle2 size={17} /> : step.state === "needs_review" ? <AlertTriangle size={16} /> : <span>{step.order}</span>}</span>
                <span className="gs-learning-step-copy"><strong><SmartTitle text={title} macros={macros} /></strong><small>{step.role === "target" ? "最终目标" : step.role === "remedial" ? "补弱节点" : step.required ? "必修前置" : "建议前置"}</small></span>
                {step.required && <LockKeyhole size={12} className="gs-learning-lock" />}
                {collapsed && <ChevronDown size={14} />}
              </button>
              {!collapsed && <div className="gs-learning-step-actions">
                <button onClick={() => assignment.role === "student" ? onOpenDetail(step.nodeId) : onOpenSource(step.nodeId)}>
                  <BookOpen size={12} />{assignment.role === "student" ? "学习" : "查看原文"}
                </button>
                 {assignment.role === "student" && (assessment?.status === "exempt" ? <span className="gs-learning-node-complete"><CheckCircle2 size={12} />本节点免考</span> : assessment?.attemptStatus === "completed" && assignment.submission ? <span className="gs-learning-node-complete"><CheckCircle2 size={12} />已完成本节点</span> : <button onClick={() => onStartAssessment(step.nodeId)} disabled={Boolean(assignment.submission) || progressBusyNode !== null || !assessment || assessment.status === "pending" || assessment.status === "failed"}>{<><CheckCircle2 size={12} />{!assessment ? "考核暂不可用" : assessment.attemptStatus === "completed" ? "修改作答" : assessment.attemptStatus === "draft" ? "继续作答" : "开始作答"}</>}</button>)}
                 <button onClick={() => setExpandedWhy(current => { const next = new Set(current); next.has(step.nodeId) ? next.delete(step.nodeId) : next.add(step.nodeId); return next; })}><CircleHelp size={12} />为什么需要它</button>
               </div>}
               {progressErrorNode === step.nodeId && <div className="gs-learning-inline-error">{progressError || "保存失败，请重试"}</div>}
              {whyOpen && <div className="gs-learning-rationale">{step.rationale || "该节点位于目标节点的真实前置依赖子图中。"}</div>}
            </article>
          );
        })}
      </div>
      {isDraft ? (
        <div className="gs-learning-draft-footer">
          <span className={draftFeedback?.kind === "error" || draftDirty || unresolvedAssessments || assessmentBusy || !scoringSummary.ready ? "dirty" : "saved"}>{batchAssessmentBusy ? "正在批量重新生成未处理考察题…" : assessmentOperationsActive ? `考察题处理中：运行 ${assessmentCounts.running} 个，排队 ${assessmentCounts.queued} 个` : draftDirty ? "有未保存修改" : unresolvedAssessments ? `还有 ${unresolvedAssessments} 个节点的考察题未处理` : scoringSummary.questions.length === 0 ? "全部节点免考" : `评分标准：${scoringSummary.totalScore.toFixed(1)} / 100 分`}</span>
          {!unresolvedAssessments && scoringSummary.invalidQuestionIds.length > 0 && <div className="gs-learning-draft-feedback error">{scoringSummary.referenceInvalidQuestionIds.length > 0 ? `有 ${scoringSummary.referenceInvalidQuestionIds.length} 道参考答案存在确定性矩阵错误；请修正并保存后再发布。` : `还有 ${scoringSummary.invalidQuestionIds.length} 道题缺少完整参考答案、评分点或有效分值。`}</div>}
          {!unresolvedAssessments && scoringSummary.invalidQuestionIds.length === 0 && scoringSummary.questions.length > 0 && Math.abs(scoringSummary.totalScore - 100) >= 0.05 && <div className="gs-learning-draft-feedback error">当前总分为 {scoringSummary.totalScore.toFixed(1)}，发布前必须调整为 100 分。</div>}
          {draftFeedback && <div className={`gs-learning-draft-feedback ${draftFeedback.kind}`}>{draftFeedback.text}</div>}
          {unresolvedAssessments > 0 && <div className="gs-learning-draft-recovery"><button type="button" className="gs-btn gs-btn-ghost" disabled={Boolean(draftBusy) || assessmentBusy} onClick={() => void regenerateUnresolvedAssessments()}>{batchAssessmentBusy ? <Loader2 size={13} className="gs-spin" /> : <RotateCcw size={13} />}一键重新生成失败项</button></div>}
          <div>
            <button type="button" className="gs-btn gs-btn-ghost" disabled={Boolean(draftBusy) || assessmentBusy} onClick={() => void saveDraft()}>{draftBusy === "save" ? <Loader2 size={13} className="gs-spin" /> : <Save size={13} />}保存草稿</button>
            <button type="button" className="gs-btn gs-btn-primary" disabled={Boolean(draftBusy) || assessmentBusy || !scoringSummary.ready} onClick={() => void publishDraft()}>{draftBusy === "publish" ? <Loader2 size={13} className="gs-spin" /> : <CheckCircle2 size={13} />}确认发布</button>
          </div>
        </div>
      ) : assignment.role === "student" ? (
        <div className="gs-assignment-submit-footer">
          {assignment.submission?.status === "released" ? <><div><ClipboardCheck size={16} /><span><strong>成绩已发布</strong><small>最终总分 {assignment.submission.teacherTotal?.toFixed(1) ?? "—"} / 100</small></span></div><button type="button" className="gs-btn gs-btn-primary" disabled={submissionBusy === "report"} onClick={() => void viewGradeReport()}>{submissionBusy === "report" ? <Loader2 size={13} className="gs-spin" /> : <ClipboardCheck size={13} />}查看成绩</button></> : assignment.submission ? <div className="waiting"><CheckCircle2 size={16} /><span><strong>已提交，等待教师批改</strong><small>提交后答案已冻结，成绩统一发布前不会显示评分信息。</small></span></div> : <><div><span><strong>整份作业 {studentCompletion.completed}/{studentCompletion.total}</strong><small>{studentCompletion.ready ? "所有非免考节点已完成，可以提交。" : "完成所有非免考节点后可提交整份作业。"}</small></span></div><button type="button" className="gs-btn gs-btn-primary" disabled={!studentCompletion.ready || submissionBusy === "submit"} onClick={() => void submitAssignment()}>{submissionBusy === "submit" ? <Loader2 size={13} className="gs-spin" /> : <Send size={13} />}提交作业</button></>}
          {submissionError && <div className="gs-learning-inline-error">{submissionError}</div>}
        </div>
      ) : null}
      <div className="gs-learning-legend"><span><i className="required" />必修</span><span><i className="review" />补弱/复习</span><span><i className="mastered" />已掌握</span><span><i className="target" />学习目标</span></div>
    </div>
  );
}

function StudentGradeReportDialog({ submission, macros, onClose }: { submission: EducationSubmission; macros?: LatexMacros; onClose: () => void }) {
  const grades = submission.grades || [];
  const nodeScores = [...grades.reduce((map, grade) => {
    const current = map.get(grade.nodeId) || { score: 0, maximum: 0 };
    current.score += grade.teacherScore || 0;
    current.maximum += grade.maxScore || 0;
    map.set(grade.nodeId, current);
    return map;
  }, new Map<number, { score: number; maximum: number }>()).entries()];
  const reportFor = (grade: QuestionGrade) => {
    const report = grade?.matrixReport as MatrixCheckReport | undefined;
    return report?.status ? report : { status: "not_applicable" as const, summary: "该题未检测到可核验的矩阵或行列式过程。", issues: [], flowCount: 0, referenceFlowCount: 0 };
  };
  return <div className="gs-grade-report-backdrop" onClick={onClose}>
    <section className="gs-grade-report-dialog" role="dialog" aria-modal="true" aria-label="作业成绩报告" onClick={event => event.stopPropagation()}>
      <header><div><span>教师已发布</span><h2>作业成绩报告</h2><small>{submission.studentName || "我的作业"} · 提交时间 {submission.submittedAt ? new Date(submission.submittedAt).toLocaleString("zh-CN") : "—"}</small></div><div className="gs-grade-report-total"><strong>{submission.teacherTotal?.toFixed(1) ?? "—"}</strong><span>/ 100</span></div><button type="button" className="gs-iconbtn" onClick={onClose} aria-label="关闭成绩报告"><X size={17} /></button></header>
      <div className="gs-grade-report-summary">
        <section><h3>教师整体评语</h3><p>{submission.teacherSummary || "教师未填写整体评语。"}</p></section>
        <section><h3>节点得分率</h3><div>{nodeScores.map(([nodeId, score]) => <span key={nodeId}><b>节点 {nodeId}</b><strong>{score.maximum > 0 ? Math.round(score.score / score.maximum * 100) : 0}%</strong><small>{score.score.toFixed(1)} / {score.maximum.toFixed(1)}</small></span>)}</div></section>
      </div>
      <main>
        {grades.map((grade, index) => {
          const matrix = reportFor(grade);
          return <article key={grade.questionId} className="gs-grade-report-question">
            <div className="gs-grade-report-question-head"><span>{index + 1}</span><div><b>节点 {grade.nodeId} · 第 {grade.order} 题</b><small>{ASSESSMENT_KIND_LABELS[grade.kind] || grade.kind}</small></div><strong>{grade.teacherScore?.toFixed(1) ?? "—"} / {grade.maxScore.toFixed(1)}</strong></div>
            <section><h4>题目</h4><MathText text={grade.question} macros={macros} /></section>
            <section><h4>我的答案</h4><MathText text={grade.studentAnswer} macros={macros} /></section>
            <div className="gs-grade-report-columns"><section><h4>教师评语</h4><p>{grade.teacherFeedback || "教师未填写逐题评语。"}</p></section><section><h4>参考答案与评分点</h4><MathText text={grade.referenceAnswer} macros={macros} />{grade.expectedPoints.length > 0 && <ul>{grade.expectedPoints.map((point, pointIndex) => <li key={pointIndex}><MathText text={point} macros={macros} /></li>)}</ul>}</section></div>
            <div className="gs-grade-report-columns"><section className={`matrix ${matrix.status}`}><h4>矩阵数值检查 · {matrixStatusLabelForStudent(matrix.status)}</h4><p>{matrix.summary}</p>{matrix.issues.map((issue, issueIndex) => <div className="gs-grade-report-issue" key={issueIndex}><AlertTriangle size={13} /><span>{issue.message}{issue.sourceExcerpt && <small>原文：{issue.sourceExcerpt}</small>}{issue.mismatchedCells?.map((cell, cellIndex) => <small key={cellIndex}>第 {cell.row} 行第 {cell.column} 列：期望 {cell.expected}，实际 {cell.actual}</small>)}</span></div>)}</section><section><h4>AI 结构化评分建议</h4>{grade.aiResult?.rationale ? <><p><b>建议分：{grade.aiSuggestedScore?.toFixed(1) ?? grade.aiResult.suggestedScore ?? "—"} / {grade.maxScore.toFixed(1)}</b></p><p>{grade.aiResult.rationale}</p>{grade.aiResult.correctPoints?.length ? <ul>{grade.aiResult.correctPoints.map((item, itemIndex) => <li key={itemIndex}>{item}</li>)}</ul> : null}{grade.aiResult.issues?.length ? <ul className="issues">{grade.aiResult.issues.map((item, itemIndex) => <li key={itemIndex}>{item}</li>)}</ul> : null}{grade.aiResult.studentFeedback && <p>{grade.aiResult.studentFeedback}</p>}{grade.aiResult.needsTeacherReview && <small>该建议要求教师重点复核。</small>}</> : <p>本题没有可展示的 AI 建议；最终分数以教师评分为准。</p>}</section></div>
          </article>;
        })}
      </main>
      <footer><span>报告仅展示结构化评分结果和可审计检查证据，不包含内部 Prompt 或模型隐藏推理。</span><button type="button" className="gs-btn gs-btn-primary" onClick={onClose}>返回学习路径</button></footer>
    </section>
  </div>;
}

function matrixStatusLabelForStudent(status: MatrixCheckReport["status"]) {
  if (status === "verified") return "计算过程通过";
  if (status === "contradicted") return "发现明确计算错误";
  if (status === "indeterminate") return "需要人工判断";
  if (status === "structural_invalid") return "表达结构无法可靠解析";
  return "不适用";
}

function AssessmentDialog({ assignment, node, assessment, token, macros, initialFrame, getWindowPopupFrame, popupZIndex, onActivate, onAttemptStarted, onComplete, onClose }: {
  assignment: EducationAssignment;
  node: GraphNode;
  assessment: NodeAssessment;
  token: string;
  macros?: LatexMacros;
  initialFrame: AssessmentFrame;
  getWindowPopupFrame: (width: number, height: number) => { minLeft: number; maxLeft: number; minTop: number; maxTop: number };
  popupZIndex: number;
  onActivate: () => void;
  onAttemptStarted: (status: "draft" | "completed") => void;
  onComplete: (path: EducationAssignment["path"]) => void;
  onClose: () => void;
}) {
  const [position, setPosition] = useState({ left: initialFrame.left, top: initialFrame.top });
  const [size, setSize] = useState<PdfPeekSize>({ width: initialFrame.width, height: initialFrame.height });
  const [attempt, setAttempt] = useState<AssessmentAttempt | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [completing, setCompleting] = useState(false);
  const [error, setError] = useState("");
  const dirtyRef = useRef(false);
  const latestAnswersRef = useRef<Record<string, string>>({});

  const handleDragStart = useCallback((e: React.MouseEvent) => {
    if (window.innerWidth <= 720) return;
    e.preventDefault();
    const card = (e.currentTarget as HTMLElement).closest(".gs-assessment-dialog") as HTMLElement | null;
    const rect = card?.getBoundingClientRect();
    const startX = e.clientX;
    const startY = e.clientY;
    const startLeft = rect?.left ?? position.left;
    const startTop = rect?.top ?? position.top;
    const cardWidth = rect?.width ?? size.width;
    const cardHeight = rect?.height ?? size.height;
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
      const frame = getWindowPopupFrame(cardWidth, cardHeight);
      nextLeft = Math.max(frame.minLeft, Math.min(frame.maxLeft, startLeft + ev.clientX - startX));
      nextTop = Math.max(frame.minTop, Math.min(frame.maxTop, startTop + ev.clientY - startY));
      if (!animationFrame) animationFrame = window.requestAnimationFrame(paint);
    };
    const onUp = () => {
      if (animationFrame) window.cancelAnimationFrame(animationFrame);
      paint();
      setPosition({ left: nextLeft, top: nextTop });
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }, [getWindowPopupFrame, position.left, position.top, size.height, size.width]);

  const handleResizeStart = useCallback((e: React.MouseEvent, edge: PdfPeekResizeEdge) => {
    if (window.innerWidth <= 720) return;
    e.preventDefault();
    e.stopPropagation();
    const card = (e.currentTarget as HTMLElement).closest(".gs-assessment-dialog") as HTMLElement | null;
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
      }
      if (edge.includes("top")) {
        height = clamp(startHeight - deltaY, minHeight, startBottom - 8);
        top = startBottom - height;
      } else if (edge.includes("bottom")) {
        height = clamp(startHeight + deltaY, minHeight, window.innerHeight - startTop - 8);
      }
      if (!animationFrame) animationFrame = window.requestAnimationFrame(paint);
    };
    const onUp = () => {
      if (animationFrame) window.cancelAnimationFrame(animationFrame);
      paint();
      setPosition({ left, top });
      setSize({ width, height });
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }, []);

  useEffect(() => {
    const constrain = () => {
      if (window.innerWidth <= 720) return;
      const width = Math.min(size.width, Math.max(1, window.innerWidth - 16));
      const height = Math.min(size.height, Math.max(1, window.innerHeight - 16));
      const frame = getWindowPopupFrame(width, height);
      setSize(current => current.width === width && current.height === height ? current : { width, height });
      setPosition(current => ({
        left: Math.max(frame.minLeft, Math.min(frame.maxLeft, current.left)),
        top: Math.max(frame.minTop, Math.min(frame.maxTop, current.top)),
      }));
    };
    window.addEventListener("resize", constrain);
    constrain();
    return () => window.removeEventListener("resize", constrain);
  }, [getWindowPopupFrame, size.height, size.width]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError("");
    void startEducationAssessmentAttempt(token, assignment.id, node.id)
      .then(next => {
        if (cancelled) return;
        setAttempt(next);
        latestAnswersRef.current = next.answers;
        const firstUnanswered = next.questions.findIndex(question => !next.answers[question.id]?.trim());
        setActiveIndex(firstUnanswered >= 0 ? firstUnanswered : 0);
        onAttemptStarted(next.status);
      })
      .catch(cause => { if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  // Start once per assignment/node; the callback updates the parent assignment object.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assignment.id, node.id, token]);

  const persistAnswers = useCallback(async (answers: Record<string, string>) => {
    if (!attempt || attempt.status === "completed") return attempt;
    setSaving(true); setError("");
    try {
      const saved = await saveEducationAssessmentAttempt(token, attempt.id, answers);
      if (latestAnswersRef.current === answers) {
        dirtyRef.current = false;
        setAttempt(saved);
        latestAnswersRef.current = saved.answers;
      } else {
        setAttempt(current => current ? { ...current, updatedAt: saved.updatedAt } : current);
      }
      return saved;
    } catch (cause) {
      dirtyRef.current = true;
      setError(cause instanceof Error ? cause.message : String(cause));
      throw cause;
    } finally {
      setSaving(false);
    }
  }, [attempt, token]);

  useEffect(() => {
    if (!attempt || !dirtyRef.current) return;
    const answers = attempt.answers;
    const timer = window.setTimeout(() => { void persistAnswers(answers).catch(() => undefined); }, 800);
    return () => window.clearTimeout(timer);
  }, [attempt?.answers, persistAnswers]);

  const updateAnswer = (questionId: string, value: string) => {
    dirtyRef.current = true;
    setAttempt(current => {
      if (!current) return current;
      const answers = { ...current.answers, [questionId]: value };
      latestAnswersRef.current = answers;
      return { ...current, answers };
    });
  };

  const moveTo = async (index: number) => {
    if (!attempt || saving) return;
    try {
      if (dirtyRef.current) await persistAnswers(attempt.answers);
      setActiveIndex(index);
    } catch { /* keep the current question visible */ }
  };

  const closeAndSave = async () => {
    if (!attempt || !dirtyRef.current) { onClose(); return; }
    try {
      await persistAnswers(attempt.answers);
      onClose();
    } catch { /* keep the dialog open so the answer is not silently lost */ }
  };

  const complete = async () => {
    if (!attempt || completing || !assessmentAnswersComplete(attempt)) return;
    setCompleting(true); setError("");
    try {
      const result = await completeEducationAssessmentAttempt(token, attempt.id, attempt.answers);
      dirtyRef.current = false;
      setAttempt(result.attempt);
      onComplete(result.path);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setCompleting(false); }
  };

  const question = attempt?.questions[activeIndex];
  const title = node.title_zh || node.title_en || node.label || `节点 ${node.id}`;
  return <div className="gs-assessment-dialog" role="dialog" aria-label={`${title} 理解考核`} onClick={onActivate} style={{ left: position.left, top: position.top, width: size.width, height: size.height, zIndex: popupZIndex }}>
      <header onMouseDown={handleDragStart}>
        <div><Brain size={18} /><span><strong>理解考核</strong><small>{title}</small></span></div>
        <button type="button" className="gs-iconbtn" title="关闭理解考核" aria-label="关闭理解考核" onMouseDown={e => e.stopPropagation()} onClick={() => void closeAndSave()} disabled={saving || completing}><X size={16} /></button>
      </header>
      {loading ? <div className="gs-assessment-loading"><Loader2 size={22} className="gs-spin" />正在加载已审核考察题…</div> : error && !attempt ? <div className="gs-assessment-loading error"><AlertTriangle size={20} />{error}<button onClick={onClose}>返回学习</button></div> : attempt && question ? <>
        <div className="gs-assessment-progress-head"><span>第 {activeIndex + 1} / {attempt.questions.length} 题</span><b>{saving ? "正在保存…" : dirtyRef.current ? "答案待保存" : "答案已保存"}</b></div>
        <div className="gs-assessment-progress-bar"><span style={{ width: `${((activeIndex + 1) / attempt.questions.length) * 100}%` }} /></div>
        <main>
          <div className="gs-assessment-prompt">
            <span>衍生问题 · {ASSESSMENT_KIND_LABELS[question.kind] || question.kind}</span>
            <h3><MathText text={question.question} macros={macros} /></h3>
            {question.focus && <small>检查重点：{question.focus}</small>}
          </div>
          <ProofWorkspace
            key={question.id}
            graphId={`assessment:${assignment.id}:${attempt.id}`}
            node={node}
            token={token}
            macros={macros}
            answerMode={{
              key: question.id,
              value: attempt.answers[question.id] || "",
              title: "我的作答",
              subtitle: "可直接输入，也可上传 PDF 或图片手稿进行 OCR",
              placeholder: "写下你的回答或证明思路，可以使用 Markdown 与 LaTeX 记号。",
              onChange: value => updateAnswer(question.id, value),
              onSave: async value => { await persistAnswers({ ...attempt.answers, [question.id]: value }); },
            }}
          />
        </main>
        {error && <div className="gs-assessment-dialog-error">{error}</div>}
        <footer>
          <button type="button" className="gs-btn gs-btn-ghost" onClick={() => void closeAndSave()} disabled={saving || completing}>返回学习</button>
          <div>
            <button type="button" disabled={activeIndex === 0 || saving || completing} onClick={() => void moveTo(activeIndex - 1)}><ChevronLeft size={14} />上一题</button>
            {activeIndex < attempt.questions.length - 1
              ? <button type="button" className="primary" disabled={saving || completing} onClick={() => void moveTo(activeIndex + 1)}>下一题<ChevronRight size={14} /></button>
              : <button type="button" className="primary" disabled={!assessmentAnswersComplete(attempt) || saving || completing} onClick={() => void complete()}>{completing ? <Loader2 size={14} className="gs-spin" /> : <CheckCircle2 size={14} />}完成本节点</button>}
          </div>
        </footer>
      </> : null}
      {["top", "right", "bottom", "left", "top-left", "top-right", "bottom-left", "bottom-right"].map(edge => <span key={edge} className={`gs-assessment-resize-handle ${edge}`} onMouseDown={e => handleResizeStart(e, edge as PdfPeekResizeEdge)} />)}
  </div>;
}

function NodeDetail({ node, deps, lang, hasAnchor, hasPdf, pdfLoading, onJump, onPick, graphId, token, llmConfig, macros, matrixFlowAudience, matrixFlowLayoutMode, onSetLearningTarget, learningStep, learningAssessment, learningAssignmentId, learningClassId, learningSubmitted, studentContextState, onStudentContextChange, onStartAssessment }: {
  node: GraphNode | null;
  deps: { kind: EdgeKind; node: GraphNode; dir: "out" | "in" }[];
  lang: NodeLanguage; hasAnchor: boolean; hasPdf?: boolean; pdfLoading?: boolean; onJump: () => void; onPick: (id: number) => void;
  graphId?: string; token?: string; llmConfig?: LLMConfig;
  macros?: LatexMacros;
  matrixFlowAudience: MatrixFlowAudience;
  matrixFlowLayoutMode: MatrixFlowLayoutMode;
  onSetLearningTarget?: (node: GraphNode) => void;
  learningStep?: LearningPathStep;
  learningAssessment?: NodeAssessment;
  learningAssignmentId?: string;
  learningClassId?: string;
  learningSubmitted?: boolean;
  studentContextState?: StudentNodeContextState;
  onStudentContextChange?: () => void;
  onStartAssessment?: (nodeId: number) => void;
}) {
  if (!node) return <div className="gs-search-empty" style={{ marginTop: 40 }}>点击节点查看详情</div>;
  const st = studioStyle(node.node_type);
  const title = lang === "en" ? (node.title_en || node.title_zh) : (node.title_zh || node.title_en);
  const sub = lang === "en" ? node.title_zh : node.title_en;
  const statementText = nodeStatementText(node);
  return (
    <div>
      <span className="gs-badge" style={{ background: st.border }}>
        {nodeTypeLabel(node.node_type, lang)}{node.label ? ` · ${node.label}` : ""}
      </span>
      <div className="gs-d-title"><SmartTitle text={title || `节点 ${node.id}`} macros={macros} /></div>
      {sub && sub !== title && <div className="gs-d-title-sub"><SmartTitle text={sub} macros={macros} /></div>}

      <div className="gs-d-actions">
        {onSetLearningTarget && (
          <button className="gs-learning-target" onClick={() => onSetLearningTarget(node)}>
            <GraduationCap size={14} />设为学习目标
          </button>
        )}
        {learningStep && learningAssessment && (
          learningStep.state === "mastered"
            ? <span className="gs-node-assessment-action completed"><CheckCircle2 size={13} />已掌握</span>
            : learningAssessment.status === "exempt"
              ? <span className="gs-node-assessment-action completed"><CheckCircle2 size={13} />本节点免考</span>
              : learningAssessment.attemptStatus === "completed" && learningSubmitted
                ? <span className="gs-node-assessment-action completed"><CheckCircle2 size={13} />已完成本节点</span>
                : <button className="gs-node-assessment-action" onClick={() => onStartAssessment?.(node.id)} disabled={learningAssessment.status !== "ready" || learningSubmitted}><Brain size={13} />{learningAssessment.attemptStatus === "completed" ? "修改作答" : learningAssessment.attemptStatus === "draft" ? "继续作答" : "开始作答"}</button>
        )}
        <button className={`gs-d-jump ${hasAnchor || hasPdf ? "" : "disabled"}`} onClick={hasAnchor || hasPdf ? onJump : undefined}>
          {pdfLoading ? <Loader2 size={13} className="gs-spin" /> : <BookOpen size={13} />}
          {pdfLoading ? "定位 PDF..." : hasPdf ? "跳转到 PDF 原文" : hasAnchor ? "跳转到原文" : "原文未定位"}
        </button>
      </div>

      {studentContextState && (studentContextState.masteryState === "needs_review" || studentContextState.riskSummary?.items?.length) && (
        <div className={`gs-node-context-status ${studentContextState.masteryState === "needs_review" ? "direct" : "risk"}`}>
          <AlertTriangle size={14} />
          <span>
            <strong>{studentContextState.masteryState === "needs_review" ? "这里还有需要确认的地方" : "相关知识可能需要复习"}</strong>
            <small>{studentContextState.masteryState === "needs_review" ? "根据你在这里的回答整理。" : "这不是已确认错误，可在下方学习情况中标记不准确。"}</small>
          </span>
        </div>
      )}

      {statementText && <div className="gs-d-section"><div className="gs-d-label">陈述</div><MatrixFlowText text={statementText} flows={node.matrix_flows} field="statement" audience={matrixFlowAudience} macros={macros} className="gs-d-text" layoutMode={matrixFlowLayoutMode} /></div>}

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

      {graphId && <ProofWorkspace
        graphId={graphId}
        node={node}
        token={token}
        llmConfig={llmConfig}
        macros={macros}
        matrixFlowAudience={matrixFlowAudience}
        matrixFlowLayoutMode={matrixFlowLayoutMode}
        educationContext={learningAssignmentId && learningClassId ? { assignmentId: learningAssignmentId, classId: learningClassId, onContextChange: onStudentContextChange } : undefined}
      />}
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
      <div className="gs-d-title" style={{ fontSize: 17 }}><MathText text={EDGE_KINDS[kind].label} macros={macros} /></div>
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
