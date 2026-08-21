import React, { useState, useEffect, useRef, useCallback } from "react";
import { Link, useSearchParams } from "react-router";
import { useJobs, type JobErrorCode, type RestoredJob } from "~/context/jobs";
import { FloatingBadge } from "~/components/FloatingBadge";
import { ApiSetupGuide, type ApiGuideStep } from "~/components/ApiSetupGuide";
import { apiUrl } from "~/api";
import {
  cancelOcrJob,
  cancelOcrInstall,
  classifyOcrRuntime,
  deleteOcrRecovery,
  getOcrRecovery,
  getOcrResult,
  getOcrRuntime,
  installOcrRuntime,
  ocrRuntimeErrorSummary,
  pollOcrJob,
  retryOcrJob,
  startOcrJob,
  uploadOcrFile,
  type OcrJobStatus,
  type OcrRuntimeStatus,
  type OcrUploadInfo,
} from "~/ocr";
import { OcrRuntimeErrorPanel } from "~/components/OcrRuntimeErrorPanel";
import { Network, type Options } from "vis-network";
import { DataSet } from "vis-data";
import "katex/dist/katex.min.css";
import "./mathgraph.css";
import { MathText, SmartTitle } from "./math";
import type { LatexMacros } from "./math";
import { LinkedMarkdownViewer } from "./markdown";
import GraphStudio, { type GraphExportFormat } from "./GraphStudio";
import { ProofWorkspace } from "./ProofWorkspace";
import { nodeTypeLabel } from "./node-type-language";
import {
  MAIN_PIPELINE_STAGE_DEFS as STAGE_DEFS,
  pipelineStageLabel,
} from "../pipeline-stages";
import {
  loadAuth, saveAuth, clearAuthAndNotify, authFetch, protectedFetch, subscribeAuthInvalidated,
  captureAuthRequestIdentity, isAuthRequestIdentityCurrent,
  EMPTY_LLM_CONFIG,
  loadNodeLanguage, saveNodeLanguage,
  loadLlm, saveLlm, loadMd, saveMd, loadSession, saveSession, clearSession, authHeaders,
} from "./auth";
import { isDesktopRuntime } from "../runtime";
import { AuthModal } from "./AuthModal";
import { PasswordChangeModal } from "./PasswordChangeModal";
import { AdminUsersLink } from "./AdminUsers";
import type { AuthState } from "../auth-model";
import { HistoryPanel } from "./HistoryPanel";
import {
  Columns2, LayoutGrid, PanelRightOpen,
  GitBranch, Layers, AlignJustify, BookText,
  History, Upload, Maximize2, Eye, Settings, CircleHelp, Sparkles, ArrowRight,
  LogOut, Loader2, Focus, MessageSquare, FileJson, FileText, FileUp, Download, Home as HomeIcon,
} from "lucide-react";

// ── Types ───────────────────────────────────────────────────────────────────

type View = "upload" | "result" | "error";
export type WorkspaceMode = "generate" | "import";
type ViewMode = "graph" | "hierarchical" | "linear" | "docorder";
type ResultLayout = "md-graph" | "full-graph" | "graph-node";

export interface LLMConfig {
  api_url: string;
  model_name: string;
  api_key: string;
  embedding_url: string;
  embedding_model: string;
  embedding_api_key: string;
}

interface LLMProfile {
  name: string;
  api_url: string;
  model_name: string;
  api_key: string;
  embedding_url: string;
  embedding_model: string;
  embedding_api_key: string;
}

export interface JobStatus {
  job_id: string;
  status: "running" | "paused" | "done" | "error";
  filename: string;
  stage: string | null;
  stage_label: string | null;
  stage_index: number;
  total_stages: number;
  stages_done: string[];
  experimental_logic_ir?: boolean;
  error_code: JobErrorCode | null;
  error_title: string | null;
  error: string | null;
}

export interface GraphNode {
  id: number;
  node_type: string;
  title_zh: string;
  title_en: string;
  label: string;
  content: string;
  statement_form: string;
  subject: unknown[];
  conditions: unknown[];
  conclusions: unknown[];
  proof: unknown;
  node_index_in_doc?: number;
  formalization_guidance?: {
    statement_skeleton?: { kind: string; expected_connectives?: string[]; notes?: string[] };
    semantic_risks?: Array<{ code: string; severity: string; message: string }>;
    concept_hints?: Array<{ name: string; note: string; matched_triggers: string[] }>;
  };
  surface_anchor?: {
    label_text: string;
    title_text: string;
    anchor_terms: string[];
    context_texts: string[];
  };
  locator?: { node_index_in_doc: number };
  source_text?: string;
  source_statement?: string;
  source_span?: { start?: number; end?: number };
  source_file?: string;
  tex_label_key?: string;
  tex_env_name?: string;
}

export interface GraphEdge {
  from: number;
  to: number;
  label: string;
  description: string;
  strength: string;
}

interface SelectedEdge {
  edgeId: number;
  label: string;
  description: string;
  strength: string;
  fromNode: GraphNode | undefined;
  toNode: GraphNode | undefined;
}

export interface GraphResult {
  nodes: GraphNode[];
  edges: GraphEdge[];
  latex_macros?: LatexMacros;
  source_mode?: WorkspaceMode;
  source_pdf?: {
    status?: "compiling" | "ready" | "failed";
    available: boolean;
    error?: string | null;
    pdf_url?: string | null;
    compile_log_url?: string | null;
  } | null;
}

interface ErrorInfo {
  code: JobErrorCode;
  title: string;
  msg: string;
  detail: string;
  partial: GraphResult | null;
  stages: string[];
  totalStages?: number;
}

interface WorkspaceSnapshot {
  view: View;
  jobId: string | null;
  result: GraphResult | null;
  sourceMarkdown?: string;
  errorInfo: ErrorInfo | null;
  filename: string;
}

export interface HistoryItem {
  id: string;
  filename: string;
  node_count: number;
  edge_count: number;
  status: "running" | "paused" | "done" | "error";
  stage: string | null;
  stage_label: string | null;
  stage_index: number;
  total_stages: number;
  experimental_logic_ir?: boolean;
  stages_done: string[];
  resume_available: boolean;
  updated_at: string;
  created_at: string;
}

// ── Constants ───────────────────────────────────────────────────────────────

// White-bg + colored-border node style (auto-formalization aesthetic)
interface NodeStyle { border: string; bg: string; shape: string; }
const NODE_STYLES: Record<string, NodeStyle> = {
  "定义": { border: "#94a3b8", bg: "#fbfcfd", shape: "box" },
  "公理": { border: "#c8b266", bg: "#fffdf5", shape: "diamond" },
  "定理": { border: "#9ad4aa", bg: "#edf8f0", shape: "ellipse" },
  "引理": { border: "#95b8eb", bg: "#f5f8fe", shape: "box" },
  "推论": { border: "#b8a6e0", bg: "#f8f5ff", shape: "box" },
  "性质": { border: "#a6c4c8", bg: "#f3f8f9", shape: "box" },
  "命题": { border: "#c4b28e", bg: "#fdf9f3", shape: "box" },
  "例子": { border: "#b0b8a0", bg: "#f6f8f3", shape: "box" },
};
const NODE_COLORS: Record<string, string> = Object.fromEntries(
  Object.entries(NODE_STYLES).map(([k, v]) => [k, v.border])
);
const ALL_NODE_TYPES = Object.keys(NODE_STYLES);

const VIS_OPTIONS: Options = {
  physics: {
    enabled: true,
    solver: "forceAtlas2Based",
    forceAtlas2Based: {
      gravitationalConstant: -55,
      centralGravity: 0.008,
      springLength: 220,
      springConstant: 0.06,
      damping: 0.42,
      avoidOverlap: 0.6,
    },
    stabilization: { enabled: true, iterations: 300, updateInterval: 20, fit: true },
    minVelocity: 0.75,
    maxVelocity: 30,
  },
  nodes: {
    shape: "box",
    margin: { top: 9, right: 14, bottom: 9, left: 14 },
    borderWidth: 1.5,
    borderWidthSelected: 2.5,
    font: { size: 13, face: "Inter, ui-sans-serif, system-ui", color: "#17202a" },
    shadow: { enabled: true, size: 6, x: 0, y: 2, color: "rgba(23,32,42,0.08)" },
  },
  edges: {
    arrows: { to: { enabled: true, scaleFactor: 0.55 } },
    color: { color: "#b8c4cf", highlight: "#225ea8", hover: "#8a98a6" },
    font: { size: 10, color: "#64717f", strokeWidth: 0, align: "top" },
    smooth: { enabled: true, type: "cubicBezier", roundness: 0.45 },
    width: 1.4,
    selectionWidth: 2.5,
  },
  interaction: {
    hover: true,
    zoomView: true,
    dragView: true,
    selectConnectedEdges: false,
    tooltipDelay: 9999,
  },
  layout: { improvedLayout: false },
};

// Manual fixed-position options for hierarchical mode (no auto-layout → filter doesn't scramble)
const HIERARCHICAL_MANUAL_OPTIONS: Options = {
  ...VIS_OPTIONS,
  physics: { enabled: false },
  layout: { improvedLayout: false, hierarchical: { enabled: false } as never },
};

// Docorder: depth goes top-to-bottom (Y), document order goes left-to-right within each depth (X)
const DOCORDER_OPTIONS: Options = {
  ...VIS_OPTIONS,
  physics: { enabled: false },
  layout: { improvedLayout: false, hierarchical: { enabled: false } as never },
};

// Compute fixed (x,y) for hierarchical view: x = depth * levelSep, y sorted by doc order
function computeManualPositions(
  nodes: GraphNode[],
  depths: Record<number, number>,
  levelSep = 260,
  nodeSep = 120,
): Record<number, { x: number; y: number }> {
  const byDepth = new Map<number, GraphNode[]>();
  for (const node of nodes) {
    const d = depths[node.id] ?? 0;
    const g = byDepth.get(d) ?? [];
    g.push(node);
    byDepth.set(d, g);
  }
  for (const g of byDepth.values()) {
    g.sort((a, b) => (a.node_index_in_doc ?? a.id) - (b.node_index_in_doc ?? b.id));
  }
  const pos: Record<number, { x: number; y: number }> = {};
  for (const [depth, g] of byDepth) {
    const totalH = (g.length - 1) * nodeSep;
    g.forEach((node, i) => {
      pos[node.id] = { x: Number(depth) * levelSep, y: -totalH / 2 + i * nodeSep };
    });
  }
  return pos;
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function nodeColor(type: string) {
  return NODE_COLORS[type] ?? "#c0c0c0";
}

function nodeStyle(type: string): NodeStyle {
  return NODE_STYLES[type] ?? { border: "#c0c0c0", bg: "#fafafa", shape: "box" };
}

// BFS longest-path depth: depth = max hops from any source node (no in-edges)
function computeDepths(nodes: GraphNode[], edges: GraphEdge[]): Record<number, number> {
  const inDeg: Record<number, number> = {};
  const adj: Record<number, number[]> = {};
  nodes.forEach(n => { inDeg[n.id] = 0; adj[n.id] = []; });
  edges.forEach(e => {
    inDeg[e.to] = (inDeg[e.to] ?? 0) + 1;
    (adj[e.from] ??= []).push(e.to);
  });
  const depths: Record<number, number> = {};
  const queue = nodes.filter(n => !inDeg[n.id]).map(n => n.id);
  queue.forEach(id => { depths[id] = 0; });
  while (queue.length > 0) {
    const cur = queue.shift()!;
    for (const next of (adj[cur] ?? [])) {
      const d = depths[cur] + 1;
      if (depths[next] === undefined || depths[next] < d) {
        depths[next] = d;
        queue.push(next);
      }
    }
  }
  const maxD = Math.max(0, ...Object.values(depths));
  nodes.forEach(n => { depths[n.id] ??= maxD + 1; });
  return depths;
}

function asText(val: unknown): string {
  if (typeof val === "string") return val;
  if (val == null) return "";
  if (Array.isArray(val)) return val.map(asText).filter(Boolean).join(", ");
  if (typeof val === "object") {
    const obj = val as Record<string, unknown>;
    for (const key of ["text", "statement", "content", "title"]) {
      const value = obj[key];
      if (typeof value === "string") return value;
      if (Array.isArray(value)) return value.map(asText).filter(Boolean).join(", ");
    }
  }
  return JSON.stringify(val);
}

// For vis-network canvas labels: convert LaTeX to readable Unicode plain text
export function labelText(raw: string): string {
  let t = raw;
  // 1. Strip math delimiters (keep inner content)
  t = t.replace(/\$\$([^$]+?)\$\$/gs, "$1");
  t = t.replace(/\\\[(.+?)\\\]/gs, "$1");
  t = t.replace(/\$([^$\n]+?)\$/g, "$1");
  t = t.replace(/\\\((.+?)\\\)/g, "$1");
  // Preserve common blackboard-bold macros before unknown commands are removed.
  const blackboardSymbols: Record<string, string> = {
    R: "ℝ",
    N: "ℕ",
    Z: "ℤ",
    C: "ℂ",
    Q: "ℚ",
  };
  for (const [command, symbol] of Object.entries(blackboardSymbols)) {
    t = t.replace(new RegExp(`\\\\${command}\\b`, "g"), symbol);
  }
  const superscriptDigits: Record<string, string> = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻",
  };
  t = t.replace(/\^\{?([0-9+-]+)\}?/g, (_, exponent: string) =>
    [...exponent].map(ch => superscriptDigits[ch] || ch).join("")
  );
  // 2. Unwrap common font/text commands: \mathbb{R} → R, \text{foo} → foo, etc.
  const wrappers = ["mathbb","mathbf","mathcal","mathit","mathrm","mathsf","operatorname","text","textbf","textit","overline","underline","widehat","widetilde","vec","hat","bar","tilde","dot","ddot"];
  for (const cmd of wrappers) {
    t = t.replace(new RegExp(`\\\\${cmd}\\{([^}]*)\\}`, "g"), "$1");
  }
  // 3. Greek letters and symbols → Unicode
  const syms: [RegExp, string][] = [
    [/\\infty/g, "∞"], [/\\alpha/g, "α"], [/\\beta/g, "β"], [/\\gamma/g, "γ"],
    [/\\Gamma/g, "Γ"], [/\\delta/g, "δ"], [/\\Delta/g, "Δ"],
    [/\\varepsilon/g, "ε"], [/\\epsilon/g, "ε"], [/\\zeta/g, "ζ"], [/\\eta/g, "η"],
    [/\\theta/g, "θ"], [/\\Theta/g, "Θ"], [/\\iota/g, "ι"], [/\\kappa/g, "κ"],
    [/\\lambda/g, "λ"], [/\\Lambda/g, "Λ"], [/\\mu/g, "μ"], [/\\nu/g, "ν"],
    [/\\xi/g, "ξ"], [/\\Xi/g, "Ξ"], [/\\pi/g, "π"], [/\\Pi/g, "Π"],
    [/\\rho/g, "ρ"], [/\\sigma/g, "σ"], [/\\Sigma/g, "Σ"], [/\\tau/g, "τ"],
    [/\\varphi/g, "φ"], [/\\phi/g, "φ"], [/\\Phi/g, "Φ"],
    [/\\chi/g, "χ"], [/\\psi/g, "ψ"], [/\\Psi/g, "Ψ"], [/\\omega/g, "ω"], [/\\Omega/g, "Ω"],
    [/\\partial/g, "∂"], [/\\nabla/g, "∇"], [/\\forall/g, "∀"], [/\\exists/g, "∃"],
    [/\\in\b/g, "∈"], [/\\notin\b/g, "∉"], [/\\subset\b/g, "⊂"], [/\\subseteq\b/g, "⊆"],
    [/\\cup\b/g, "∪"], [/\\cap\b/g, "∩"], [/\\emptyset/g, "∅"],
    [/\\to\b/g, "→"], [/\\rightarrow/g, "→"], [/\\leftarrow/g, "←"],
    [/\\Rightarrow/g, "⇒"], [/\\Leftarrow/g, "⇐"], [/\\Leftrightarrow/g, "⟺"],
    [/\\leq\b/g, "≤"], [/\\geq\b/g, "≥"], [/\\neq\b/g, "≠"], [/\\approx/g, "≈"],
    [/\\equiv/g, "≡"], [/\\sim\b/g, "∼"], [/\\simeq/g, "≃"],
    [/\\times/g, "×"], [/\\pm/g, "±"], [/\\cdot/g, "·"], [/\\cdots/g, "⋯"], [/\\ldots/g, "…"],
    [/\\int\b/g, "∫"], [/\\sum\b/g, "∑"], [/\\prod\b/g, "∏"],
    [/\\sup\b/g, "sup"], [/\\inf\b/g, "inf"], [/\\lim\b/g, "lim"],
    [/\\min\b/g, "min"], [/\\max\b/g, "max"],
    [/\\left[\(\[\{|]/g, ""], [/\\right[\)\]\}|]/g, ""],
    [/\\[,;!: ]/g, " "],
  ];
  for (const [re, rep] of syms) t = t.replace(re, rep);
  // 4. Remove remaining \cmd sequences (including starred variants like \frac*)
  t = t.replace(/\\[a-zA-Z]+[*]?\s*/g, "");
  // 5. Remove braces
  t = t.replace(/[{}]/g, "");
  // 6. Collapse whitespace
  return t.replace(/\s+/g, " ").trim();
}

function nodeVisLabel(n: GraphNode, lang: NodeLanguage): string {
  const zh = n.title_zh || n.title_en || `节点${n.id}`;
  const en = n.title_en || n.title_zh || `节点${n.id}`;
  if (lang === "zh") return labelText(zh);
  if (lang === "en") return labelText(en);
  // bilingual: "中文\n(English)" — vis-network supports \n in label
  if (n.title_zh && n.title_en && n.title_zh !== n.title_en) {
    return `${labelText(n.title_zh)}\n(${labelText(n.title_en)})`;
  }
  return labelText(zh);
}

function buildVisNodes(nodes: GraphNode[], activeTypes: Set<string>, lang: NodeLanguage = "bilingual", depths?: Record<number, number>) {
  return nodes.map((n) => {
    const style = nodeStyle(n.node_type);
    return {
      id: n.id,
      label: nodeVisLabel(n, lang),
      hidden: !(activeTypes.has(n.node_type) || !ALL_NODE_TYPES.includes(n.node_type)),
      shape: style.shape,
      color: {
        background: style.bg,
        border: style.border,
        highlight: { background: style.bg, border: "#225ea8" },
        hover: { background: style.bg, border: style.border },
      },
      ...(depths !== undefined ? { level: depths[n.id] ?? 0 } : {}),
    };
  });
}

function buildVisEdges(edges: GraphEdge[], visibleIds: Set<number>) {
  return edges.map((e, i) => ({
    id: i,
    from: e.from,
    to: e.to,
    label: e.label,
    title: e.description || undefined,
    hidden: !visibleIds.has(e.from) || !visibleIds.has(e.to),
  }));
}

// Merge duplicate nodes (same title_zh or title_en) into a single canonical node,
// redirecting all edges and removing resulting self-loops / duplicate edges.
function normalizeGraphResultSource(graph: GraphResult): GraphResult {
  const sourceMode = (graph as unknown as { source_mode?: string }).source_mode;
  if (sourceMode === "pipeline") return { ...graph, source_mode: "generate" };
  if (sourceMode === "agent") return { ...graph, source_mode: "import" };
  return graph;
}

function dedupeGraph(graph: GraphResult): GraphResult {
  graph = normalizeGraphResultSource(graph);
  const canonical = new Map<number, number>(); // duplicateId → canonicalId
  const seen = new Map<string, number>();       // titleKey → first nodeId
  for (const n of graph.nodes) {
    const key = (n.title_zh || n.title_en || "").trim().toLowerCase();
    if (!key) continue;
    const existing = seen.get(key);
    if (existing === undefined) { seen.set(key, n.id); }
    else { canonical.set(n.id, existing); }
  }
  if (canonical.size === 0) return graph;
  const resolve = (id: number): number => canonical.get(id) ?? id;
  const nodes = graph.nodes.filter(n => !canonical.has(n.id));
  const edgeKey = (e: GraphEdge) => `${e.from}→${e.to}:${(e.label || "").toLowerCase()}`;
  const seenEdges = new Set<string>();
  const edges = graph.edges
    .map(e => ({ ...e, from: resolve(e.from), to: resolve(e.to) }))
    .filter(e => {
      if (e.from === e.to) return false;
      const k = edgeKey(e);
      if (seenEdges.has(k)) return false;
      seenEdges.add(k); return true;
    });
  return { ...graph, nodes, edges };
}

// Docorder: y = depth * levelSep, x = doc-order within depth
function computeDocOrderPositions(
  nodes: GraphNode[],
  depths: Record<number, number>,
  levelSep = 150,
  nodeSep = 220,
): Record<number, { x: number; y: number }> {
  const byDepth = new Map<number, GraphNode[]>();
  for (const node of nodes) {
    const d = depths[node.id] ?? 0;
    const g = byDepth.get(d) ?? [];
    g.push(node);
    byDepth.set(d, g);
  }
  for (const g of byDepth.values()) {
    g.sort((a, b) => (a.node_index_in_doc ?? a.id) - (b.node_index_in_doc ?? b.id));
  }
  const pos: Record<number, { x: number; y: number }> = {};
  for (const [depth, g] of byDepth) {
    const totalW = (g.length - 1) * nodeSep;
    g.forEach((node, i) => {
      pos[node.id] = { x: -totalW / 2 + i * nodeSep, y: Number(depth) * levelSep };
    });
  }
  return pos;
}

// ── Auth/session types (helpers live in ./auth.ts) ───────────────────────────

export type NodeLanguage = "zh" | "en" | "bilingual";

export interface SavedSession {
  result: GraphResult;
  filename: string;
  jobId: string;
  sourceMarkdown?: string;
}

// ── Logo ─────────────────────────────────────────────────────────────────────

export function Logo() {
  return (
    <div className="mg-logo">
      <img className="mg-logo-mark" src="/mathweaver-icon.png" alt="" aria-hidden="true" />
      <span className="mg-logo-text">绎理</span>
    </div>
  );
}

const WORKSPACE_MODES: Array<{
  mode: WorkspaceMode;
  label: string;
  compactLabel: string;
  title: string;
  Icon: typeof FileText;
}> = [
  { mode: "generate", label: "从文档生成", compactLabel: "生成", title: "查看文档生成任务", Icon: FileText },
  { mode: "import", label: "导入已有图谱", compactLabel: "导入", title: "导入或查看已有图谱", Icon: FileUp },
];

function WorkspaceModeSwitch({
  mode, onChange, isGenerating = false,
}: {
  mode: WorkspaceMode;
  onChange: (mode: WorkspaceMode) => void;
  isGenerating?: boolean;
}) {
  return (
    <div className="mg-workspace-switch" role="tablist" aria-label="图谱创建入口">
      <Link className="mg-home-link" to="/" title="返回首页介绍" aria-label="返回首页介绍">
        <HomeIcon size={14} />
      </Link>
      {WORKSPACE_MODES.map(({ mode: item, label, compactLabel, title, Icon }) => (
        <button
          key={item}
          type="button"
          role="tab"
          aria-selected={mode === item}
          title={title}
          className={mode === item ? "active" : ""}
          onClick={() => onChange(item)}
        >
          {item === "generate" && isGenerating
            ? <Loader2 className="mg-workspace-switch-spinner" size={14} />
            : <Icon size={14} />}
          <span className="mg-workspace-label-full">{label}</span>
          <span className="mg-workspace-label-compact">{compactLabel}</span>
        </button>
      ))}
    </div>
  );
}

// ── Auth Modal ────────────────────────────────────────────────────────────────

// ── History Panel ─────────────────────────────────────────────────────────────

export interface HistoryPanelProps {
  token: string;
  llmConfig?: LLMConfig;
  onLoad: (result: GraphResult, filename: string, id: string) => void;
  onResume: (job: RestoredJob) => void;
  onClose: () => void;
}

// ── Upload Screen ────────────────────────────────────────────────────────────

interface UploadScreenProps {
  onSubmit: (content: string, filename: string, llm: LLMConfig) => Promise<void>;
  llm: LLMConfig;
  onLlmChange: (patch: Partial<LLMConfig>) => void;
  auth: AuthState | null;
  onShowAuth: () => void;
  onShowHistory: () => void;
  onShowSettings: () => void;
  onShowApiGuide: (step: ApiGuideStep) => void;
  configReady: boolean;
  submitButtonRef: React.RefObject<HTMLButtonElement | null>;
  activeFilename?: string | null;
}

function UploadScreen({
  onSubmit, llm, onLlmChange, auth, onShowAuth, onShowHistory, onShowSettings,
  onShowApiGuide, configReady, submitButtonRef, activeFilename,
}: UploadScreenProps) {
  const [file, setFile] = useState<File | null>(null);
  const [textInput, setTextInput] = useState("");
  const [dragging, setDragging] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);
  const [err, setErr] = useState("");
  const [submitPhase, setSubmitPhase] = useState<"uploading" | "installing" | "ocr" | "generate" | null>(null);
  const [ocrUpload, setOcrUpload] = useState<OcrUploadInfo | null>(null);
  const [ocrRuntime, setOcrRuntime] = useState<OcrRuntimeStatus | null>(null);
  const [ocrJob, setOcrJob] = useState<OcrJobStatus | null>(null);
  const [ocrPreview, setOcrPreview] = useState<string | null>(null);
  const [recoveryJobs, setRecoveryJobs] = useState<OcrJobStatus[]>([]);
  const [ocrUploadProgress, setOcrUploadProgress] = useState<number | null>(null);
  const ocrAbortRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadLocked = Boolean(activeFilename);
  const displayedFilename = activeFilename || file?.name || "";
  const ocrDisposition = classifyOcrRuntime(ocrRuntime);
  const ocrRetryableError = ocrDisposition === "retryable_error";
  const ocrBlocked = ocrDisposition === "unavailable" || ocrDisposition === "fatal_error";
  const isLlmComplete = Boolean(
    llm.api_url.trim() && llm.model_name.trim() && llm.api_key.trim() && llm.embedding_model.trim(),
  );

  useEffect(() => () => {
    // 身份失效会重建上传组件；卸载时同步终止仍在进行的 PDF/OCR 请求。
    ocrAbortRef.current?.abort();
    ocrAbortRef.current = null;
  }, []);

  useEffect(() => {
    if (configReady && !isLlmComplete) setConfigOpen(true);
  }, [configReady, isLlmComplete]);

  useEffect(() => {
    let active = true;
    void getOcrRecovery().then((response) => {
      if (active) setRecoveryJobs(response.jobs || []);
    }).catch(() => undefined);
    return () => { active = false; };
  }, []);

  const isSupportedFile = (candidate: File) => /\.(md|txt|tex|pdf)$/i.test(candidate.name);
  const isPdfFile = (candidate: File) => /\.pdf$/i.test(candidate.name);
  const getOcrMarkdownFilename = (sourceFilename: string) => {
    const stem = sourceFilename.replace(/\.[^.]+$/, "") || "ocr-result";
    return `${stem}_ocr.md`;
  };
  const preparePdf = useCallback(async (candidate: File) => {
    if (candidate.size > 100 * 1024 * 1024) {
      setErr("PDF 文件不能超过 100MB");
      return null;
    }
    ocrAbortRef.current?.abort();
    const controller = new AbortController();
    ocrAbortRef.current = controller;
    setOcrUploadProgress(0);
    setOcrJob(null);
    setOcrPreview(null);
    setSubmitPhase("uploading");
    try {
      const uploaded = await uploadOcrFile(candidate, setOcrUploadProgress, controller.signal);
      setOcrUpload(uploaded);
      setOcrRuntime(await getOcrRuntime(controller.signal));
      setSubmitPhase(null);
      return uploaded;
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setErr(error instanceof Error ? error.message : "PDF 预检上传失败");
      }
      setOcrUpload(null);
      setOcrRuntime(null);
      setSubmitPhase(null);
      return null;
    } finally {
      if (ocrAbortRef.current === controller) ocrAbortRef.current = null;
      setOcrUploadProgress(null);
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (submitPhase || uploadLocked) return;
    const f = e.dataTransfer.files?.[0];
    if (f && isSupportedFile(f)) {
      setErr("");
      setFile(f);
      if (isPdfFile(f)) void preparePdf(f);
      else { setOcrUpload(null); setOcrRuntime(null); setOcrJob(null); setOcrPreview(null); }
    } else {
      setErr("请上传 PDF (.pdf)、Markdown (.md)、TeX (.tex) 或纯文本 (.txt) 文件");
    }
  }, [preparePdf, submitPhase, uploadLocked]);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (submitPhase || uploadLocked) return;
    const f = e.target.files?.[0];
    if (!f) return;
    if (!isSupportedFile(f)) {
      setErr("请上传 PDF (.pdf)、Markdown (.md)、TeX (.tex) 或纯文本 (.txt) 文件");
      return;
    }
    setErr("");
    setFile(f);
    if (isPdfFile(f)) void preparePdf(f);
    else { setOcrUpload(null); setOcrRuntime(null); setOcrJob(null); setOcrPreview(null); }
  };

  const handleSubmit = async () => {
    if (submitPhase) return;
    setErr("");
    const hasFile = !!file;
    const hasText = textInput.trim().length > 0;

    if (!hasFile && !hasText) { setErr("请选择文件或输入文本"); return; }
    if (!ocrPreview && !(file && isPdfFile(file)) && (!llm.api_url || !llm.model_name || !llm.api_key)) {
      setErr("LLM 配置尚未完成，可使用新手向导逐步填写。");
      setConfigOpen(true);
      onShowApiGuide("chat");
      return;
    }
    if (!ocrPreview && !(file && isPdfFile(file)) && !llm.embedding_model.trim()) {
      setErr("Embedding 配置尚未完成，可使用新手向导逐步填写。");
      setConfigOpen(true);
      onShowApiGuide("embedding");
      return;
    }

    try {
      let content = textInput;
      let filename = "input.md";

      if (ocrPreview?.trim()) {
        const sourceFilename = file?.name || ocrJob?.filename || "ocr-result.pdf";
        const generatedFilename = getOcrMarkdownFilename(sourceFilename);
        const generatedFile = new File([ocrPreview], generatedFilename, { type: "text/markdown" });
        filename = generatedFile.name;
        content = ocrPreview;
        setFile(generatedFile);
        setTextInput("");
        setOcrPreview(null);
        setOcrUpload(null);
        setOcrRuntime(null);
        setOcrJob(null);
      }
      if (file && !(isPdfFile(file) && ocrPreview?.trim())) {
        filename = file.name;
        if (isPdfFile(file)) {
          let uploaded = ocrUpload;
          if (!uploaded || uploaded.filename !== file.name || uploaded.size_bytes !== file.size) {
            uploaded = await preparePdf(file);
            if (!uploaded) return;
            setErr("PDF 预检完成。请再次点击“开始分析”以启动 OCR。");
            return;
          }
          let runtime = ocrRuntime || await getOcrRuntime();
          setOcrRuntime(runtime);
          const disposition = classifyOcrRuntime(runtime);
          if (disposition === "unavailable" || disposition === "fatal_error") {
            throw new Error(ocrRuntimeErrorSummary(runtime));
          }
          if (runtime.state !== "ready") {
            setSubmitPhase("installing");
            const controller = new AbortController();
            ocrAbortRef.current = controller;
            runtime = await installOcrRuntime(controller.signal, setOcrRuntime);
            setOcrRuntime(runtime);
          }
          setSubmitPhase("ocr");
          const job = await startOcrJob(uploaded.upload_id, ocrAbortRef.current?.signal);
          setOcrJob(job);
          await pollOcrJob(job.ocr_job_id, setOcrJob, ocrAbortRef.current?.signal);
          const data = await getOcrResult(job.ocr_job_id, ocrAbortRef.current?.signal);
          content = data.importedText;
          if (!content.trim()) throw new Error("PDF OCR 未识别出可处理的文本");
          setOcrPreview(content);
          setErr("");
          return;
        } else {
          content = await file.text();
        }
      }

      if (!isLlmComplete) {
        setErr(file && isPdfFile(file)
          ? "PDF OCR 已完成，Markdown 已保留；补充 LLM 与 Embedding 配置后再开始分析。"
          : "LLM 与 Embedding 配置尚未完成，请先补充配置。" );
        setConfigOpen(true);
        onShowApiGuide("chat");
        return;
      }
      setSubmitPhase("generate");
      await onSubmit(content, filename, llm);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "文件处理失败，请重试");
    } finally {
      setSubmitPhase(null);
      ocrAbortRef.current = null;
    }
  };

  const handleRetryOcrInstall = () => {
    if (!ocrRetryableError || submitPhase) return;
    void handleSubmit();
  };

  const handleCancel = async () => {
    const jobId = ocrJob?.ocr_job_id;
    if (jobId && ["queued", "starting_engine", "processing", "collecting_output"].includes(ocrJob.status)) {
      await cancelOcrJob(jobId).catch(() => undefined);
    }
    if (ocrRuntime?.install_id && (ocrRuntime.state === "downloading" || ocrRuntime.state === "verifying" || ocrRuntime.state === "installing" || ocrRuntime.state === "self_testing")) {
      await cancelOcrInstall(ocrRuntime.install_id).catch(() => undefined);
    }
    ocrAbortRef.current?.abort();
    ocrAbortRef.current = null;
    setSubmitPhase(null);
    setOcrJob(null);
    setErr("OCR 已取消，原始上传仍可在 24 小时内重试。");
  };

  const handleRecoveryRetry = async (recovery: OcrJobStatus) => {
    try {
      const job = await retryOcrJob(recovery.ocr_job_id);
      setOcrJob(job);
      await pollOcrJob(job.ocr_job_id, setOcrJob);
      const result = await getOcrResult(job.ocr_job_id);
      setOcrPreview(result.importedText);
      setTextInput(result.importedText);
      setFile(null);
      setErr("已恢复 OCR 结果；请检查预览并补充配置后开始分析。");
      setRecoveryJobs((items) => items.filter((item) => item.ocr_job_id !== recovery.ocr_job_id));
    } catch (error) {
      setErr(error instanceof Error ? error.message : "恢复 OCR 失败");
    }
  };

  const handleSaveOcrPreview = () => {
    if (!ocrPreview) return;
    const sourceFilename = file?.name || ocrUpload?.filename || ocrJob?.filename || "ocr-result.pdf";
    const blob = new Blob([ocrPreview], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = getOcrMarkdownFilename(sourceFilename);
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  };

  const handleCancelOcrPreview = () => {
    setOcrPreview(null);
    setOcrJob(null);
    setOcrUpload(null);
    setOcrRuntime(null);
    setTextInput("");
    setFile(null);
    setErr("");
  };

  return (
    <div className="mg-root">
      <div className="mg-upload-screen">
        {/* Auth bar */}
        <div className="mg-upload-auth-bar">
          {auth ? (
            <>
              <span className="mg-auth-email" style={{ fontSize: 11, color: "var(--muted)", background: "var(--surface-alt)", border: "1px solid var(--line)", borderRadius: 999, padding: "3px 10px" }}>{auth.user.display_name}</span>
              <AdminUsersLink auth={auth} />
              <button className="mg-btn mg-btn-ghost" style={{ fontSize: 11, gap: 5 }} onClick={onShowHistory}><History size={13} />历史</button>
              <button className="mg-btn mg-btn-ghost" style={{ fontSize: 11, gap: 5 }} onClick={() => onShowApiGuide("intro")}><CircleHelp size={13} />帮助</button>
              <button className="mg-btn mg-btn-ghost" style={{ fontSize: 11, gap: 5 }} onClick={onShowSettings} title="LLM 配置与账号设置"><Settings size={13} />设置</button>
            </>
          ) : (
            <>
              <button className="mg-btn mg-btn-ghost" style={{ fontSize: 11, gap: 5 }} onClick={() => onShowApiGuide("intro")}><CircleHelp size={13} />帮助</button>
              <button className="mg-btn mg-btn-ghost" style={{ fontSize: 12, padding: "4px 12px", color: "var(--accent)", borderColor: "var(--accent)" }} onClick={onShowAuth}>
                登录
              </button>
            </>
          )}
        </div>

        <Logo />
        <p className="mg-tagline">将数学文档转化为可交互的知识图谱</p>

        <div className="mg-card">
          {/* Drop Zone */}
          <div
            className={`mg-dropzone${dragging ? " drag-over" : ""}${uploadLocked ? " is-locked" : ""}`}
            onDragOver={(e) => { e.preventDefault(); if (!uploadLocked) setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            onClick={() => { if (!submitPhase && !uploadLocked) fileInputRef.current?.click(); }}
          >
            <div className="mg-dropzone-icon"><Upload size={28} strokeWidth={1.5} /></div>
            {displayedFilename ? (
              <span
                className="mg-file-badge"
                onClick={(e) => e.stopPropagation()}
              >
                📄 {displayedFilename}
                {file && !uploadLocked && (
                  <button disabled={!!submitPhase} onClick={() => { setFile(null); setOcrUpload(null); setOcrRuntime(null); setOcrJob(null); setOcrPreview(null); }}>×</button>
                )}
              </span>
            ) : (
              <>
                <div className="mg-dropzone-primary">拖拽 PDF / Markdown / TeX 文件至此，或点击选择</div>
                <div className="mg-dropzone-sub">.pdf · .md · .tex · .txt</div>
              </>
            )}
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.md,.tex,.txt,application/pdf,text/markdown,text/plain,text/x-tex,application/x-tex"
            onChange={handleFileChange}
            disabled={!!submitPhase || uploadLocked}
            style={{ display: "none" }}
          />

          {/* Text paste */}
          <div className="mg-divider">或直接粘贴文本</div>
          <textarea
            className="mg-textarea"
            placeholder="在此粘贴 Markdown / LaTeX 数学文本…"
            value={textInput}
            onChange={(e) => setTextInput(e.target.value)}
            disabled={!!submitPhase || uploadLocked}
            rows={5}
          />

          {/* LLM Config */}
          {!isLlmComplete && configReady && (
            <button className="mg-api-guide-callout" onClick={() => onShowApiGuide("intro")}>
              <span><Sparkles size={15} />第一次配置？</span>
              <strong>用 3 分钟完成设置 <ArrowRight size={14} /></strong>
            </button>
          )}
          <div style={{ border: "1px solid var(--line)", borderRadius: 9, overflow: "hidden", marginBottom: 4 }}>
            <button
              onClick={() => setConfigOpen((v) => !v)}
              style={{
                width: "100%", display: "flex", alignItems: "center", justifyContent: "space-between",
                padding: "10px 14px", background: "var(--surface-alt)", border: "none",
                margin: 0, cursor: "pointer", fontFamily: "inherit",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Settings size={13} style={{ color: "var(--muted)" }} />
                <span style={{ fontSize: 13, fontWeight: 500, color: "var(--ink)" }}>LLM 配置</span>
                {llm.api_url && llm.model_name && llm.api_key && llm.embedding_model
                  ? <span style={{ fontSize: 11, color: "var(--ok)", fontWeight: 500 }}>✓ 已填写</span>
                  : <span style={{ fontSize: 11, color: "var(--danger)" }}>未填写</span>}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span
                  role="button"
                  tabIndex={0}
                  className="mg-config-guide-link"
                  onClick={(e) => { e.stopPropagation(); onShowApiGuide("provider"); }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      e.stopPropagation();
                      onShowApiGuide("provider");
                    }
                  }}
                >
                  不会填写？
                </span>
                {llm.api_url && (
                  <span
                    role="button"
                    style={{ fontSize: 11, color: "var(--muted)", cursor: "pointer", padding: "1px 6px" }}
                    onClick={(e) => {
                      e.stopPropagation();
                      onLlmChange({
                        api_url: "", model_name: "", api_key: "",
                        embedding_url: "", embedding_model: "", embedding_api_key: "",
                      });
                      setConfigOpen(true);
                    }}
                  >
                    清除
                  </span>
                )}
                <span style={{
                  fontSize: 10, color: "var(--muted)",
                  transform: configOpen ? "rotate(180deg)" : "none",
                  transition: "transform .2s", display: "inline-block",
                }}>▼</span>
              </div>
            </button>
            {configOpen && (
              <div className="mg-motion-accordion" style={{ padding: "14px 14px 10px" }}>
                <div className="mg-config-grid">
                  <div className="mg-field">
                    <label className="mg-label">API URL（Base URL）</label>
                    <input className="mg-input" placeholder="https://api.example.com/v1" value={llm.api_url} onChange={(e) => onLlmChange({ api_url: e.target.value })} />
                  </div>
                  <div className="mg-field">
                    <label className="mg-label">模型名</label>
                    <input className="mg-input" placeholder="chat-model-id" value={llm.model_name} onChange={(e) => onLlmChange({ model_name: e.target.value })} />
                  </div>
                  <div className="mg-field">
                    <label className="mg-label">API Key</label>
                    <input className="mg-input" type="password" placeholder="sk-…" value={llm.api_key} onChange={(e) => onLlmChange({ api_key: e.target.value })} />
                  </div>
                </div>
                <div className="mg-config-grid">
                  <div className="mg-field">
                    <label className="mg-label">Embedding URL（可选）</label>
                    <input className="mg-input" placeholder="默认使用 LLM API URL" value={llm.embedding_url} onChange={(e) => onLlmChange({ embedding_url: e.target.value })} />
                  </div>
                  <div className="mg-field">
                    <label className="mg-label">
                      Embedding 模型
                    </label>
                    <input className="mg-input" placeholder="embedding-model-id" value={llm.embedding_model} onChange={(e) => onLlmChange({ embedding_model: e.target.value })} />
                  </div>
                  <div className="mg-field">
                    <label className="mg-label">Embedding API Key（可选）</label>
                    <input className="mg-input" type="password" placeholder="默认使用 LLM API Key" value={llm.embedding_api_key} onChange={(e) => onLlmChange({ embedding_api_key: e.target.value })} />
                  </div>
                </div>
              </div>
            )}
          </div>

          {err && (
            <p style={{ fontSize: 12, color: "var(--danger)", margin: "8px 0 0" }}>{err}</p>
          )}
          {recoveryJobs.length > 0 && (
            <div style={{ marginTop: 10, padding: "9px 11px", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12 }}>
              <div style={{ color: "var(--muted)", marginBottom: 5 }}>发现可恢复的 OCR 任务</div>
              {recoveryJobs.map((recovery) => (
                <div key={recovery.ocr_job_id} style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 5 }}>
                  <span style={{ flex: 1 }}>{recovery.filename} · {recovery.page_count} 页</span>
                  <button className="mg-btn mg-btn-ghost" onClick={() => void handleRecoveryRetry(recovery)}>重试</button>
                  <button className="mg-btn mg-btn-ghost" onClick={() => void deleteOcrRecovery(recovery.ocr_job_id).then(() => setRecoveryJobs((items) => items.filter((item) => item.ocr_job_id !== recovery.ocr_job_id))).catch(() => undefined)}>清理</button>
                </div>
              ))}
            </div>
          )}

          {!ocrPreview && file && isPdfFile(file) && (ocrUpload || ocrRuntime) && (
            <div style={{ marginTop: 10, padding: "9px 11px", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12, color: "var(--muted)" }}>
              <div>PDF 预检：{ocrUpload ? `${(ocrUpload.size_bytes / 1024 / 1024).toFixed(1)}MB · ${ocrUpload.page_count} 页` : "上传中…"}</div>
              <div>OCR 组件：{ocrRuntime?.state || "检查中"}{ocrRuntime?.total_bytes ? ` · 下载约 ${(ocrRuntime.total_bytes / 1024 / 1024 / 1024).toFixed(1)}GB` : ""}{ocrRuntime?.required_disk_bytes ? ` · 需要约 ${(ocrRuntime.required_disk_bytes / 1024 / 1024 / 1024).toFixed(1)}GB` : ""}{ocrRuntime?.available_disk_bytes ? ` · 可用 ${(ocrRuntime.available_disk_bytes / 1024 / 1024 / 1024).toFixed(1)}GB` : ""}</div>
              {ocrRuntime && (
                <OcrRuntimeErrorPanel
                  status={ocrRuntime}
                  onRetry={handleRetryOcrInstall}
                  retrying={submitPhase === "installing"}
                />
              )}
              <div>OCR powered by MinerU · Windows x64 CPU</div>
              {(ocrJob?.eta_seconds || ocrUpload?.eta_seconds) && <div>预计耗时：{(ocrJob?.eta_seconds || ocrUpload?.eta_seconds)?.low}–{(ocrJob?.eta_seconds || ocrUpload?.eta_seconds)?.high} 秒</div>}
              {ocrUploadProgress !== null && <div>上传进度：{ocrUploadProgress}%</div>}
            </div>
          )}
          {ocrPreview && (
            <div style={{ marginTop: 10, padding: "9px 11px", border: "1px solid var(--line)", borderRadius: 8 }}>
              <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 10, marginBottom: 5 }}>
                <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.5 }}>OCR Markdown 预览</div>
                <button className="mg-btn mg-btn-ghost" style={{ flexShrink: 0, minHeight: 28, padding: "4px 9px", fontSize: 11, gap: 5 }} onClick={handleSaveOcrPreview}>
                  <Download size={13} />保存 OCR 结果
                </button>
              </div>
              <pre style={{ maxHeight: 180, overflow: "auto", whiteSpace: "pre-wrap", margin: 0, fontSize: 11 }}>{ocrPreview}</pre>
            </div>
          )}
          {!ocrPreview && ocrRuntime?.state === "downloading" && <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 6 }}>组件下载进度：{ocrRuntime.download_percent ?? 0}%</div>}
          {!ocrPreview && ocrRuntime?.state === "self_testing" && <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 6 }}>正在执行本地校准测试（首次安装可能需要数分钟）</div>}
          {!ocrPreview && ocrJob && <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 6 }}>OCR 阶段：{ocrJob.phase} · 已用 {ocrJob.elapsed_seconds ?? 0} 秒</div>}

          {ocrPreview ? (
            <div className="mg-ocr-choice-actions">
              <button
                className="mg-btn mg-btn-primary"
                onClick={() => void handleSubmit()}
                disabled={!!submitPhase || uploadLocked}
              >
                提取图谱
              </button>
              <button
                className="mg-btn mg-btn-ghost"
                onClick={handleCancelOcrPreview}
                disabled={!!submitPhase || uploadLocked}
              >
                取消处理
              </button>
            </div>
          ) : (
            <button
              ref={submitButtonRef}
              className="mg-btn mg-btn-primary mg-upload-submit"
              onClick={handleSubmit}
              disabled={!!submitPhase || uploadLocked || (Boolean(ocrUpload) && ocrBlocked)}
              style={{ marginTop: 14, borderRadius: 10, padding: "12px", fontSize: 14, letterSpacing: ".01em" }}
            >
              {uploadLocked ? "正在处理中…" : submitPhase === "uploading" ? "正在上传并预检 PDF…" : submitPhase === "installing" ? "正在安装 OCR 组件…" : submitPhase === "ocr" ? "正在识别 PDF…" : submitPhase === "generate" ? "正在提交分析…" : ocrRetryableError ? "重试下载并开始 OCR" : ocrUpload ? "确认并开始 PDF OCR" : "开始分析"}
            </button>
          )}
          {submitPhase && <button className="mg-btn mg-btn-ghost mg-ocr-cancel-button" style={{ width: "100%", margin: "7px 0 0" }} onClick={() => void handleCancel()}>取消</button>}
        </div>
      </div>
    </div>
  );
}

interface GraphImportScreenProps {
  onSubmit: (nodesFile: File, edgesFile: File, markdownFile?: File) => Promise<void>;
  auth: AuthState | null;
  onShowAuth: () => void;
  onShowHistory: () => void;
  onShowSettings: () => void;
  onShowApiGuide: (step: ApiGuideStep) => void;
}

function GraphImportFileField({
  label, hint, file, accept, optional, onChange,
}: {
  label: string;
  hint: string;
  file: File | null;
  accept: string;
  optional?: boolean;
  onChange: (file: File | null) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <div className="mg-import-file-field">
      <div className="mg-import-file-copy">
        {accept.includes("json") ? <FileJson size={18} /> : <FileText size={18} />}
        <div>
          <div className="mg-import-file-label">{label}{optional ? <span>可选</span> : null}</div>
          <div className="mg-import-file-hint">{hint}</div>
        </div>
      </div>
      <button className="mg-btn mg-btn-ghost" onClick={() => inputRef.current?.click()}>
        {file ? "替换文件" : "选择文件"}
      </button>
      {file && (
        <div className="mg-import-file-selected">
          <span>{file.name}</span>
          <button title="移除文件" onClick={() => onChange(null)}>×</button>
        </div>
      )}
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        style={{ display: "none" }}
        onChange={(event) => onChange(event.target.files?.[0] ?? null)}
      />
    </div>
  );
}

function GraphImportScreen({ onSubmit, auth, onShowAuth, onShowHistory, onShowSettings, onShowApiGuide }: GraphImportScreenProps) {
  const [nodesFile, setNodesFile] = useState<File | null>(null);
  const [edgesFile, setEdgesFile] = useState<File | null>(null);
  const [markdownFile, setMarkdownFile] = useState<File | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    if (!nodesFile || !edgesFile) {
      setErr("请选择 Node JSON 和 Edge JSON 文件");
      return;
    }
    setErr("");
    setLoading(true);
    try {
      await onSubmit(nodesFile, edgesFile, markdownFile ?? undefined);
    } catch (error) {
      setErr(String(error));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mg-root">
      <div className="mg-upload-screen">
        <div className="mg-upload-auth-bar">
          {auth ? (
            <>
              <span className="mg-auth-email" style={{ fontSize: 11, color: "var(--muted)", background: "var(--surface-alt)", border: "1px solid var(--line)", borderRadius: 999, padding: "3px 10px" }}>{auth.user.display_name}</span>
              <AdminUsersLink auth={auth} />
              <button className="mg-btn mg-btn-ghost" style={{ fontSize: 11, gap: 5 }} onClick={onShowHistory}><History size={13} />历史</button>
              <button className="mg-btn mg-btn-ghost" style={{ fontSize: 11, gap: 5 }} onClick={() => onShowApiGuide("intro")}><CircleHelp size={13} />帮助</button>
              <button className="mg-btn mg-btn-ghost" style={{ fontSize: 11, gap: 5 }} onClick={onShowSettings} title="LLM 配置与账号设置"><Settings size={13} />设置</button>
            </>
          ) : (
            <>
              <button className="mg-btn mg-btn-ghost" style={{ fontSize: 11, gap: 5 }} onClick={() => onShowApiGuide("intro")}><CircleHelp size={13} />帮助</button>
              <button className="mg-btn mg-btn-ghost" style={{ fontSize: 12, padding: "4px 12px", color: "var(--accent)", borderColor: "var(--accent)" }} onClick={onShowAuth}>
                登录
              </button>
            </>
          )}
        </div>
        <Logo />
        <p className="mg-tagline">导入已有图谱文件，直接进入图谱工作区</p>
        <div className="mg-card mg-import-card">
          <GraphImportFileField label="Node JSON" hint="上传已有图谱的节点文件" file={nodesFile} accept=".json,application/json" onChange={setNodesFile} />
          <GraphImportFileField label="Edge JSON" hint="上传已有图谱的关系文件" file={edgesFile} accept=".json,application/json" onChange={setEdgesFile} />
          <GraphImportFileField label="原始 Markdown / TeX" hint="用于启用 MD + 图谱布局、双向跳转和 TeX 宏渲染" file={markdownFile} accept=".md,.tex,.txt,text/markdown,text/plain,text/x-tex,application/x-tex" optional onChange={setMarkdownFile} />
          {err && <p style={{ fontSize: 12, color: "var(--danger)", margin: "12px 0 0" }}>{err}</p>}
          <button className="mg-btn mg-btn-primary mg-upload-submit" onClick={submit} disabled={loading} style={{ marginTop: 18 }}>
            {loading ? <><Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />正在导入...</> : "导入图谱"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Processing Screen ────────────────────────────────────────────────────────

export interface ProcessingScreenProps {
  status: JobStatus | null;
  filename: string;
}

// ── Node Detail Drawer ────────────────────────────────────────────────────────

interface DrawerProps {
  node: GraphNode | null;
  onClose: () => void;
  graphId: string;
  token?: string;
  llmConfig?: LLMConfig;
  nodeLanguage?: NodeLanguage;
  macros?: LatexMacros;
}

function Drawer({ node, onClose, graphId, token, llmConfig, nodeLanguage = "bilingual", macros }: DrawerProps) {
  const [copied, setCopied] = useState(false);

  const copy = (text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  if (!node) return <div className="mg-drawer" />;

  const color = nodeColor(node.node_type);
  const fg = node.formalization_guidance;
  const skeleton = fg?.statement_skeleton;
  const risks = fg?.semantic_risks ?? [];
  const concepts = fg?.concept_hints ?? [];

  const hasFormalization = !!(skeleton || risks.length || concepts.length);

  return (
    <div className="mg-drawer open">
      <div className="mg-drawer-inner" style={{ position: "relative" }}>
        <button className="mg-drawer-close" onClick={onClose}>×</button>

        {/* Title block */}
        <span className="mg-node-badge" style={{ background: color }}>
          {nodeTypeLabel(node.node_type, nodeLanguage)}
        </span>
        {node.statement_form && (
          <span className="mg-form-badge" style={{ marginLeft: 6 }}>
            {node.statement_form}
          </span>
        )}
        <div className="mg-node-title">
          <SmartTitle macros={macros} text={
            nodeLanguage === "en"
              ? (node.title_en || node.title_zh || `节点 ${node.id}`)
              : (node.title_zh || node.title_en || `节点 ${node.id}`)
          } />
        </div>
        {nodeLanguage !== "zh" && node.title_en && node.title_en !== node.title_zh && (
          <div className="mg-node-title-en"><SmartTitle text={node.title_en} macros={macros} /></div>
        )}
        {nodeLanguage === "en" && node.title_zh && node.title_zh !== node.title_en && (
          <div className="mg-node-title-en" style={{ color: "var(--muted)", fontSize: 12 }}><SmartTitle text={node.title_zh} macros={macros} /></div>
        )}

        {/* Content */}
        {node.content && (
          <div className="mg-detail-section">
            <div className="mg-detail-section-label">原文</div>
            <div className="mg-detail-text"><MathText text={node.content} macros={macros} /></div>
          </div>
        )}

        {/* Logical structure */}
        {(node.subject?.length > 0 ||
          node.conditions?.length > 0 ||
          node.conclusions?.length > 0) && (
          <>
            <div className="mg-sep" />
            <div className="mg-detail-section-label" style={{ marginBottom: 10 }}>逻辑结构</div>
            {node.subject?.length > 0 && (
              <div className="mg-detail-section">
                <div className="mg-detail-section-label">主语</div>
                {node.subject.map((s, i) => (
                  <span key={i} className="mg-tag"><MathText text={asText(s)} macros={macros} /></span>
                ))}
              </div>
            )}
            {node.conditions?.length > 0 && (
              <div className="mg-detail-section">
                <div className="mg-detail-section-label">条件</div>
                {node.conditions.map((c, i) => (
                  <span key={i} className="mg-tag"><MathText text={asText(c)} macros={macros} /></span>
                ))}
              </div>
            )}
            {node.conclusions?.length > 0 && (
              <div className="mg-detail-section">
                <div className="mg-detail-section-label">结论</div>
                {node.conclusions.map((c, i) => (
                  <span key={i} className="mg-tag"><MathText text={asText(c)} macros={macros} /></span>
                ))}
              </div>
            )}
          </>
        )}

        <ProofWorkspace graphId={graphId} node={node} token={token} llmConfig={llmConfig} macros={macros} />

        {/* Formalization guidance */}
        {hasFormalization && (
          <>
            <div className="mg-sep" />
            <div className="mg-detail-section-label" style={{ marginBottom: 10 }}>形式化指导</div>

            {skeleton && (
              <div className="mg-detail-section">
                <div className="mg-detail-section-label">命题骨架</div>
                <div className="mg-skeleton-kind">
                  {skeleton.kind}
                </div>
                {skeleton.expected_connectives?.map((c, i) => (
                  <span key={i} className="mg-tag">{c}</span>
                ))}
                {skeleton.notes?.map((n, i) => (
                  <p key={i} style={{ fontSize: 11, color: "var(--muted)", marginTop: 4, lineHeight: 1.5 }}>{n}</p>
                ))}
              </div>
            )}

            {concepts.length > 0 && (
              <div className="mg-detail-section">
                <div className="mg-detail-section-label">概念提示</div>
                {concepts.map((c, i) => (
                  <div key={i} className="mg-risk" style={{ borderColor: "var(--accent-mid)", background: "var(--accent-light)", color: "var(--accent)" }}>
                    <strong>{c.name}</strong>：{c.note}
                  </div>
                ))}
              </div>
            )}

            {risks.length > 0 && (
              <div className="mg-detail-section">
                <div className="mg-detail-section-label">语义风险 ({risks.length})</div>
                {risks.map((r, i) => (
                  <div key={i} className={`mg-risk${r.severity === "high" ? " mg-risk-high" : ""}`}>
                    {r.severity === "high" ? "⚠ " : "ℹ "}{r.message}
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ── Edge Info Drawer ──────────────────────────────────────────────────────────

interface EdgeInfoDrawerProps {
  edge: SelectedEdge | null;
  onClose: () => void;
  macros?: LatexMacros;
}

function EdgeInfoDrawer({ edge, onClose, macros }: EdgeInfoDrawerProps) {
  if (!edge) return <div className="mg-drawer" />;

  const strengthLabel: Record<string, string> = { strong: "强", medium: "中", weak: "弱" };
  const strengthColor: Record<string, string> = { strong: "var(--ok)", medium: "var(--accent)", weak: "var(--warn)" };
  const sc = strengthColor[edge.strength] ?? "var(--muted)";

  return (
    <div className="mg-drawer open">
      <div className="mg-drawer-inner" style={{ position: "relative" }}>
        <button className="mg-drawer-close" onClick={onClose}>×</button>

        <span className="mg-node-badge" style={{ background: "var(--muted)" }}>关系</span>
        <div className="mg-node-title" style={{ marginTop: 8 }}>
          <MathText text={edge.label || "（无名称）"} macros={macros} />
        </div>

        {edge.strength && (
          <div style={{ fontSize: 11, color: sc, fontWeight: 600, marginBottom: 12 }}>
            强度：{strengthLabel[edge.strength] ?? edge.strength}
          </div>
        )}

        <div className="mg-sep" />

        <div className="mg-detail-section">
          <div className="mg-detail-section-label">连接节点</div>
          <div style={{ fontSize: 13, lineHeight: 2, display: "flex", alignItems: "baseline", flexWrap: "wrap", gap: 4 }}>
            <span style={{ background: "var(--accent-light)", color: "var(--accent)", borderRadius: "var(--radius-sm)", padding: "2px 8px", fontWeight: 500 }}>
              <SmartTitle text={edge.fromNode?.title_zh || edge.fromNode?.title_en || `节点${edge.fromNode?.id ?? "?"}`} macros={macros} />
            </span>
            <span style={{ color: "var(--muted)", fontSize: 16 }}>→</span>
            <span style={{ background: "var(--accent-light)", color: "var(--accent)", borderRadius: "var(--radius-sm)", padding: "2px 8px", fontWeight: 500 }}>
              <SmartTitle text={edge.toNode?.title_zh || edge.toNode?.title_en || `节点${edge.toNode?.id ?? "?"}`} macros={macros} />
            </span>
          </div>
        </div>

        {edge.description && (
          <div className="mg-detail-section">
            <div className="mg-detail-section-label">关系解释</div>
            <div className="mg-detail-text"><MathText text={edge.description} macros={macros} /></div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Text-link tooltip ─────────────────────────────────────────────────────────

// Strip LaTeX for plain-text tooltip preview
function stripForTooltip(text: string): string {
  return text
    .replace(/\$\$[\s\S]*?\$\$/g, "…")
    .replace(/\$[^$\n]*?\$/g, "…")
    .replace(/\\\[[\s\S]*?\\\]/g, "…")
    .replace(/\\\([\s\S]*?\\\)/g, "…")
    .replace(/\\[a-zA-Z]+\s*(\{[^}]*\}|\([^)]*\))*/g, "")
    .replace(/[{}_\\]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

interface TooltipState { nodeId: number; rect: DOMRect; }

function TextNodeTooltip({
  state, nodes, onClose, onFocusGraph, nodeLanguage = "bilingual", macros,
}: {
  state: TooltipState;
  nodes: GraphNode[];
  onClose: () => void;
  onFocusGraph: (nodeId: number) => void;
  nodeLanguage?: NodeLanguage;
  macros?: LatexMacros;
}) {
  const node = nodes.find(n => n.id === state.nodeId);
  if (!node) return null;

  const CARD_H = 160;
  const top = state.rect.top > CARD_H + 12
    ? state.rect.top - CARD_H - 8
    : state.rect.bottom + 8;
  const left = Math.max(8, Math.min(state.rect.left, window.innerWidth - 296));

  return (
    <div className="mg-text-tooltip" style={{ top, left }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
        <span className={`mg-chip-${chipColor(node.node_type)}`} style={{ fontSize: 11 }}>{nodeTypeLabel(node.node_type, nodeLanguage)}</span>
        <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--muted)", fontSize: 16, lineHeight: 1, padding: 0 }}>×</button>
      </div>
      <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6, color: "var(--ink)" }}>
        <MathText text={node.title_zh || node.title_en || node.label} macros={macros} />
      </div>
      <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.6, overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical" }}>
        {stripForTooltip(node.content || "").slice(0, 160) || "—"}
      </div>
      <button
        onClick={() => { onFocusGraph(node.id); onClose(); }}
        style={{ marginTop: 10, fontSize: 11, color: "var(--accent)", background: "none", border: "none", cursor: "pointer", padding: 0, textDecoration: "underline" }}
      >
        在图谱中查看 →
      </button>
    </div>
  );
}

function chipColor(type: string): string {
  const m: Record<string, string> = {
    "定义": "gray", "公理": "amber", "定理": "green",
    "引理": "blue", "推论": "blue", "性质": "gray",
    "命题": "amber", "例子": "gray",
  };
  return m[type] ?? "gray";
}

// ── Result Screen ─────────────────────────────────────────────────────────────

interface ResultScreenProps {
  workspaceMode: WorkspaceMode;
  result: GraphResult;
  filename: string;
  jobId: string;
  sourceMarkdown?: string;
  onReset: () => void;
  auth: AuthState | null;
  llmConfig: LLMConfig;
  onShowHistory: () => void;
  onShowAuth: () => void;
  onShowSettings: () => void;
  onShowApiGuide: () => void;
  onShowStudio?: () => void;
  onSourceMarkdownRecover?: (md: string) => void;
  nodeLanguage: NodeLanguage;
  onNodeLanguageChange: (lang: NodeLanguage) => void;
}

function ResultScreen({ workspaceMode, result, filename, jobId, sourceMarkdown, onReset, auth, llmConfig, onShowHistory, onShowAuth, onShowSettings, onShowApiGuide, onShowStudio, onSourceMarkdownRecover, nodeLanguage, onNodeLanguageChange }: ResultScreenProps) {
  const latexMacros = result.latex_macros;
  const resultSourceMode = result.source_mode ?? workspaceMode;
  const graphRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<Network | null>(null);
  const nodesDatasetRef = useRef<DataSet<ReturnType<typeof buildVisNodes>[number]> | null>(null);
  const edgesDatasetRef = useRef<DataSet<ReturnType<typeof buildVisEdges>[number]> | null>(null);
  const savedPositionsRef = useRef<Record<number, { x: number; y: number }>>({});
  const lastResultRef = useRef<GraphResult | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<SelectedEdge | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("graph");
  const [resultLayout, setResultLayout] = useState<ResultLayout>("full-graph");
  const heightLabelRef = useRef<HTMLSpanElement>(null);
  const sliderRef = useRef<HTMLInputElement>(null);
  const [activeTypes, setActiveTypes] = useState<Set<string>>(
    () => new Set(ALL_NODE_TYPES)
  );
  const [exporting, setExporting] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [leftPanelWidth, setLeftPanelWidth] = useState(300);
  const [rightPanelWidth, setRightPanelWidth] = useState(360);
  const [rightPanelCollapsed, setRightPanelCollapsed] = useState(false);
  const leftPanelRef = useRef<HTMLDivElement>(null);
  const rightPanelRef = useRef<HTMLDivElement>(null);
  const fullGraphDrawerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!sourceMarkdown && resultLayout === "md-graph") setResultLayout("full-graph");
  }, [sourceMarkdown, resultLayout]);
  // Auto-recover sourceMarkdown from backend if not in localStorage
  useEffect(() => {
    if (sourceMarkdown || !auth || !jobId) return;
    let cancelled = false;
    const requestIdentity = captureAuthRequestIdentity(auth.token);
    protectedFetch(apiUrl(`/api/v2/history/${jobId}/markdown`), {}, auth.token)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!cancelled && isAuthRequestIdentityCurrent(requestIdentity) && data?.markdown) {
          saveMd(jobId, data.markdown);
          onSourceMarkdownRecover?.(data.markdown);
        }
      })
      .catch(() => {/* silent fail */});
    return () => { cancelled = true; };
  // Only run once on mount
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  // Text ↔ Graph bidirectional link
  const [textLinkMode, setTextLinkMode] = useState<"focus" | "tooltip">("focus");
  const [activeNodeId, setActiveNodeId] = useState<number | null>(null);
  const [tooltipState, setTooltipState] = useState<TooltipState | null>(null);
  const textPanelRef = useRef<HTMLDivElement | null>(null);

  const toggleRightPanel = () => {
    const panelEl = rightPanelRef.current ?? fullGraphDrawerRef.current;
    const nextCollapsed = !rightPanelCollapsed;
    setRightPanelCollapsed(nextCollapsed);
    if (panelEl) {
      panelEl.style.width = nextCollapsed ? "0px" : rightPanelWidth + "px";
      panelEl.style.transition = "width .25s ease";
    }
  };

  // Tooltip close on outside click / Escape
  useEffect(() => {
    if (!tooltipState) return;
    const onDown = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest(".mg-text-tooltip")) setTooltipState(null);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setTooltipState(null); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [tooltipState]);

  // Focus graph on a node (used by Mode A and tooltip "在图谱中查看")
  const focusGraphNode = useCallback((nodeId: number) => {
    networkRef.current?.focus(nodeId, { scale: 1.3, animation: { duration: 500, easingFunction: "easeInOutQuad" } });
    networkRef.current?.selectNodes([nodeId]);
    setSelectedNode(result.nodes.find(n => n.id === nodeId) ?? null);
    setSelectedEdge(null);
    setActiveNodeId(nodeId);
  }, [result.nodes]);

  // Called when user clicks a highlighted term in the text
  const handleTextAnchorClick = useCallback((nodeId: number, anchorEl: HTMLElement) => {
    if (textLinkMode === "focus") {
      focusGraphNode(nodeId);
    } else {
      setTooltipState({ nodeId, rect: anchorEl.getBoundingClientRect() });
    }
  }, [textLinkMode, focusGraphNode]);

  const startResize = (e: React.MouseEvent, side: "left" | "right") => {
    e.preventDefault();
    const startX = e.clientX;
    const panelEl = side === "left"
      ? leftPanelRef.current
      : (rightPanelRef.current ?? fullGraphDrawerRef.current);
    if (!panelEl) return;
    // Expand panel if collapsed before drag
    if (side === "right" && rightPanelCollapsed) {
      setRightPanelCollapsed(false);
    }
    // Disable CSS transition during drag so width updates are instant
    const prevTransition = panelEl.style.transition;
    panelEl.style.transition = "none";

    const startW = panelEl.offsetWidth || rightPanelWidth;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const clamp = (v: number) =>
      side === "left" ? Math.max(160, Math.min(700, v)) : Math.max(200, Math.min(700, v));

    const onMove = (ev: MouseEvent) => {
      const delta = ev.clientX - startX;
      const next = clamp(side === "left" ? startW + delta : startW - delta);
      panelEl.style.width = next + "px"; // direct DOM — zero React re-renders during drag
    };
    const onUp = (ev: MouseEvent) => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      panelEl.style.transition = prevTransition; // restore transition
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      const finalW = clamp(side === "left" ? startW + ev.clientX - startX : startW - (ev.clientX - startX));
      // Sync to React state once on release so layout persists across re-renders
      side === "left" ? setLeftPanelWidth(finalW) : setRightPanelWidth(finalW);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };

  // Auto-save to history when result first loads and has a backend job ID.
  useEffect(() => {
    if (!auth || !jobId || saved) return;
    const autoSave = async () => {
      setSaving(true);
      try {
        const res = await protectedFetch(apiUrl("/api/v2/history"), {
          method: "POST",
          headers: authHeaders(auth.token),
          body: JSON.stringify({ job_id: jobId }),
        }, auth.token);
        if (res.ok) setSaved(true);
      } finally {
        setSaving(false);
      }
    };
    autoSave();
  // Only run once when result loads
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth, jobId]);

  // Count nodes per type
  const typeCounts: Record<string, number> = {};
  result.nodes.forEach((n) => {
    typeCounts[n.node_type] = (typeCounts[n.node_type] ?? 0) + 1;
  });
  const presentTypes = ALL_NODE_TYPES.filter((t) => typeCounts[t]);
  const otherTypes = result.nodes
    .map((n) => n.node_type)
    .filter((t) => !ALL_NODE_TYPES.includes(t))
    .filter((v, i, a) => a.indexOf(v) === i);

  const toggleType = (type: string) => {
    setActiveTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  };

  // ── Effect 1: Rebuild network only when result or viewMode changes ───────────
  useEffect(() => {
    if (!graphRef.current) return;

    // Clear saved positions when a new result loads (different file)
    if (lastResultRef.current !== result) {
      savedPositionsRef.current = {};
      lastResultRef.current = result;
    }

    const isLinear       = viewMode === "linear";
    const isHierarchical = viewMode === "hierarchical";
    const isDocOrder     = viewMode === "docorder";
    const isForce        = viewMode === "graph";
    const hasSaved = isForce && Object.keys(savedPositionsRef.current).length > 0;

    const depths = (isHierarchical || isDocOrder) ? computeDepths(result.nodes, result.edges) : undefined;
    const manualPos = isHierarchical && depths ? computeManualPositions(result.nodes, depths) : undefined;
    const docOrderPos = isDocOrder && depths ? computeDocOrderPositions(result.nodes, depths) : undefined;
    // buildVisNodes WITHOUT depths → no `level` attr (level conflicts with manual positions)
    const visNodes = buildVisNodes(result.nodes, activeTypes, nodeLanguage);
    const visibleIds = new Set(visNodes.filter(n => !n.hidden).map(n => n.id));
    const visEdges = buildVisEdges(result.edges, visibleIds);

    const finalNodes = isLinear
      ? visNodes.map((n, i) => ({
          ...n,
          x: i % 2 === 0 ? -180 : 180,
          y: i * 200,
          fixed: { x: true, y: true },
        }))
      : isHierarchical && manualPos
        ? visNodes.map(n => {
            const p = manualPos[n.id];
            return p ? { ...n, x: p.x, y: p.y } : n;
          })
        : isDocOrder && docOrderPos
          ? visNodes.map(n => {
              const p = docOrderPos[n.id];
              return p ? { ...n, x: p.x, y: p.y } : n;
            })
          : isForce && hasSaved
            ? visNodes.map(n => {
                const p = savedPositionsRef.current[n.id];
                return p ? { ...n, x: p.x, y: p.y } : n;
              })
            : visNodes;

    const ds_nodes = new DataSet(finalNodes);
    const ds_edges = new DataSet(visEdges);
    nodesDatasetRef.current = ds_nodes as never;
    edgesDatasetRef.current = ds_edges as never;

    if (networkRef.current) networkRef.current.destroy();

    const netOptions: Options = isLinear
      ? { ...VIS_OPTIONS, physics: { enabled: false } }
      : isHierarchical
        ? HIERARCHICAL_MANUAL_OPTIONS
        : isDocOrder
          ? DOCORDER_OPTIONS
          : hasSaved
          ? { ...VIS_OPTIONS, physics: { enabled: false } }
          : {
              ...VIS_OPTIONS,
              physics: {
                ...VIS_OPTIONS.physics,
                forceAtlas2Based: {
                  gravitationalConstant: -60,
                  centralGravity: 0.008,
                  springLength: 220,
                  springConstant: 0.05,
                  damping: 0.45,
                  avoidOverlap: 0.95,
                },
              },
            };

    const net = new Network(
      graphRef.current,
      { nodes: ds_nodes, edges: ds_edges },
      netOptions
    );

    let physicsKillTimer: ReturnType<typeof setTimeout> | null = null;

    if (isForce) {
      if (!hasSaved) {
        const freezeAndSave = () => {
          net.setOptions({ physics: { enabled: false } });
          savedPositionsRef.current = net.getPositions() as Record<number, { x: number; y: number }>;
        };
        net.once("stabilizationIterationsDone", freezeAndSave);
        physicsKillTimer = setTimeout(freezeAndSave, 5000);
      } else {
        setTimeout(() => net.fit(), 80);
      }
    } else if (isHierarchical || isDocOrder) {
      setTimeout(() => net.fit(), 150);
    } else {
      setTimeout(() => net.moveTo({ position: { x: 0, y: 150 }, scale: 0.85, animation: false }), 80);
    }

    net.on("click", (params) => {
      if (params.nodes.length > 0) {
        const nodeId = params.nodes[0] as number;
        setSelectedNode(result.nodes.find((n) => n.id === nodeId) ?? null);
        setSelectedEdge(null);
        setActiveNodeId(nodeId);
        // Graph → Text: scroll text panel to the paragraph that mentions this node
        if (textPanelRef.current) {
          const para = textPanelRef.current.querySelector(
            `[data-node-ids~="${nodeId}"]`
          ) as HTMLElement | null;
          if (para) {
            para.scrollIntoView({ behavior: "smooth", block: "center" });
            para.classList.add("mg-paragraph-flash");
            setTimeout(() => para.classList.remove("mg-paragraph-flash"), 1200);
          }
        }
      } else if (params.edges.length > 0) {
        const edgeId = params.edges[0] as number;
        const edge = result.edges[edgeId];
        if (edge) {
          setSelectedEdge({
            edgeId,
            label: edge.label,
            description: edge.description,
            strength: edge.strength,
            fromNode: result.nodes.find((n) => n.id === edge.from),
            toNode: result.nodes.find((n) => n.id === edge.to),
          });
          setSelectedNode(null);
        }
      } else {
        setSelectedNode(null);
        setSelectedEdge(null);
        setActiveNodeId(null);
      }
    });

    networkRef.current = net;

    return () => {
      if (physicsKillTimer) clearTimeout(physicsKillTimer);
      net.destroy();
      networkRef.current = null;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result, viewMode, nodeLanguage]); // NOT activeTypes — filter is handled in Effect 2

  // ── Effect 2: Update node/edge visibility without touching the Network ────────
  useEffect(() => {
    const ds_nodes = nodesDatasetRef.current;
    const ds_edges = edgesDatasetRef.current;
    if (!ds_nodes || !ds_edges) return;

    const nodeUpdates = result.nodes.map(n => ({
      id: n.id,
      hidden: !(activeTypes.has(n.node_type) || !ALL_NODE_TYPES.includes(n.node_type)),
    }));
    ds_nodes.update(nodeUpdates as never);

    const visibleIds = new Set(nodeUpdates.filter(u => !u.hidden).map(u => u.id));
    const edgeUpdates = result.edges.map((e, i) => ({
      id: i,
      hidden: !visibleIds.has(e.from) || !visibleIds.has(e.to),
    }));
    ds_edges.update(edgeUpdates as never);
  }, [activeTypes, result]);

  // Sync slider + label to the actual CSS-determined canvas height after each layout
  useEffect(() => {
    const timer = setTimeout(() => {
      if (!graphRef.current) return;
      const h = graphRef.current.clientHeight;
      if (heightLabelRef.current) heightLabelRef.current.textContent = String(h);
      if (sliderRef.current) sliderRef.current.value = String(Math.min(1200, Math.max(200, h)));
    }, 160);
    return () => clearTimeout(timer);
  }, [result, viewMode]);

  // Re-fit graph when layout changes (container size changes)
  useEffect(() => {
    const t = setTimeout(() => networkRef.current?.fit(), 120);
    return () => clearTimeout(t);
  }, [resultLayout]);

  const handleExport = async () => {
    const requestIdentity = captureAuthRequestIdentity(auth?.token);
    setExporting(true);
    try {
      const res = await protectedFetch(apiUrl(`/api/v2/export/${jobId}`), { method: "POST" }, auth?.token);
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      if (!res.ok) throw new Error("Export failed");
      const blob = await res.blob();
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${filename.replace(/\.\w+$/, "")}_mathweaver.html`;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setExporting(false);
    }
  };

  const presentAll = [...presentTypes, ...otherTypes];

  return (
    <div className="mg-root">
      <div className="mg-result-screen">
        {/* Header */}
        <header className="mg-header">
          <div className={`mg-header-left ${workspaceMode === "import" ? "mg-header-left-import" : ""}`}>
            {(workspaceMode === "generate" || sourceMarkdown !== undefined) && (
              <span className="mg-header-title">{filename}</span>
            )}
            <span className="mg-result-source">{resultSourceMode === "generate" ? "文档生成" : "文件导入"}</span>
          </div>

          {/* Layout switcher — center */}
          <div className="mg-result-layout-switcher mg-responsive-layout-switcher">
            {(
              [
                { key: "md-graph",   Icon: Columns2,       label: "MD + 图谱"  },
                { key: "full-graph", Icon: LayoutGrid,     label: "全图谱"      },
                { key: "graph-node", Icon: PanelRightOpen, label: "图谱 + 节点" },
              ] as { key: ResultLayout; Icon: React.FC<{size?: number}>; label: string }[]
            ).map(({ key, Icon, label }) => {
              const active = resultLayout === key;
              const disabled = key === "md-graph" && !sourceMarkdown;
              return (
                <button className="mg-result-layout-button" key={key} title={disabled ? "上传原始 Markdown 后可启用 MD + 图谱" : label} disabled={disabled} onClick={() => setResultLayout(key)} style={{
                  display: "flex", flexDirection: "column", alignItems: "center", gap: 2,
                  padding: "5px 12px", borderRadius: 6, border: "none",
                  background: active ? "var(--surface)" : "transparent",
                  color: active ? "var(--accent)" : "var(--muted)",
                  boxShadow: active ? "var(--shadow-sm)" : "none",
                  transition: "all .15s",
                  opacity: disabled ? .35 : 1,
                  cursor: disabled ? "not-allowed" : "pointer",
                }}>
                  <Icon size={14} />
                  <span style={{ fontSize: 9, fontWeight: 500, letterSpacing: ".03em" }}>{label}</span>
                </button>
              );
            })}
          </div>

          <div className="mg-header-actions">
            <button className="mg-btn mg-btn-ghost mg-result-action-button mg-result-icon-button" style={{ fontSize: 11, gap: 5 }} onClick={onShowApiGuide} title="API 配置指南">
              <CircleHelp size={13} />
            </button>
            {onShowStudio && (
              <button className="mg-btn mg-btn-ghost mg-result-action-button" style={{ fontSize: 11, gap: 5, color: "var(--accent)", borderColor: "var(--accent-mid)" }} onClick={onShowStudio} title="切换到新版 Studio 视图">
                ✨ 新版
              </button>
            )}
            {/* View mode toggle */}
            <div className="mg-view-mode-switcher mg-responsive-view-switcher">
              {(
                [
                  { mode: "graph" as ViewMode,       Icon: GitBranch,    label: "力图" },
                  { mode: "hierarchical" as ViewMode, Icon: Layers,       label: "层次" },
                  { mode: "docorder" as ViewMode,     Icon: BookText,     label: "文档序" },
                ]
              ).map(({ mode, Icon, label }) => {
                const active = viewMode === mode;
                return (
                  <button className="mg-view-mode-button" key={mode} title={label} onClick={() => setViewMode(mode)} style={{
                    display: "flex", alignItems: "center", gap: 4,
                    padding: "4px 9px", fontSize: 11, borderRadius: 6, border: "none",
                    cursor: "pointer", fontWeight: active ? 600 : 400,
                    background: active ? "var(--surface)" : "transparent",
                    color: active ? "var(--ink)" : "var(--muted)",
                    boxShadow: active ? "var(--shadow-sm)" : "none",
                    transition: "all .15s",
                  }}>
                    <Icon size={12} /><span>{label}</span>
                  </button>
                );
              })}
            </div>
            {auth ? (
              <>
                <button className="mg-btn mg-btn-ghost mg-result-action-button mg-responsive-history" style={{ fontSize: 11, gap: 5 }} onClick={onShowHistory}>
                  <History size={13} />历史{saving ? "…" : saved ? " ✓" : ""}
                </button>
                <button className="mg-btn mg-btn-ghost mg-result-action-button mg-result-icon-button" style={{ fontSize: 11, gap: 5 }} onClick={onShowSettings} title="设置">
                  <Settings size={13} />
                </button>
              </>
            ) : (
              <button className="mg-btn mg-btn-ghost mg-result-action-button" style={{ fontSize: 11, color: "var(--accent)", gap: 5 }} onClick={onShowAuth}>
                登录保存
              </button>
            )}
            <button className="mg-btn mg-btn-ghost mg-result-action-button mg-result-collapsible-action" style={{ gap: 5 }} onClick={onReset} title="重新上传">
              <Upload size={13} /><span>重新上传</span>
            </button>
            <button className="mg-btn mg-btn-primary mg-result-action-button mg-result-collapsible-action" style={{ width: "auto", padding: "6px 14px", fontSize: 12 }}
              onClick={handleExport} disabled={exporting} title={exporting ? "正在生成 HTML" : "导出 HTML"}>
              {exporting
                ? <><Loader2 size={13} style={{ animation: "spin 1s linear infinite" }} /><span>生成中…</span></>
                : <><Download size={13} /><span>导出 HTML</span></>}
            </button>
          </div>
        </header>

        {/* Body — graph div is ALWAYS mounted so vis-network survives layout switches */}
        <div className="mg-body" style={{ overflow: "hidden" }}>

          {/* ── Left panel ── */}
          {resultLayout === "md-graph" && (
            <div ref={leftPanelRef} style={{ width: leftPanelWidth, flexShrink: 0, overflow: "auto", background: "var(--bg)", display: "flex", flexDirection: "column" }}>
              {/* Mode toggle strip */}
              {sourceMarkdown && (
                <div style={{ display: "flex", alignItems: "center", gap: 4, padding: "10px 16px 6px", borderBottom: "1px solid var(--line)", flexShrink: 0 }}>
                  <span style={{ fontSize: 10, color: "var(--muted)", fontWeight: 600, letterSpacing: ".05em", marginRight: 4 }}>点击模式</span>
                  {([
                    { mode: "focus" as const, Icon: Focus, label: "聚焦图谱" },
                    { mode: "tooltip" as const, Icon: MessageSquare, label: "悬浮卡片" },
                  ]).map(({ mode, Icon, label }) => {
                    const active = textLinkMode === mode;
                    return (
                      <button key={mode} title={label} onClick={() => setTextLinkMode(mode)} style={{
                        display: "flex", alignItems: "center", gap: 4,
                        padding: "3px 8px", borderRadius: 5, border: "none", cursor: "pointer",
                        background: active ? "var(--accent-light)" : "transparent",
                        color: active ? "var(--accent)" : "var(--muted)",
                        fontSize: 11, fontWeight: active ? 600 : 400,
                        transition: "all .12s",
                      }}>
                        <Icon size={11} />{label}
                      </button>
                    );
                  })}
                </div>
              )}
              <div style={{ padding: "16px 28px", flex: 1, overflow: "auto" }}>
                <div style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: ".07em", color: "var(--text-muted)", marginBottom: 16 }}>原始文档</div>
                {sourceMarkdown
                  ? <LinkedMarkdownViewer
                      markdown={sourceMarkdown}
                      nodes={result.nodes}
                      onNodeClick={handleTextAnchorClick}
                      activeNodeId={activeNodeId}
                      panelRef={textPanelRef}
                      macros={latexMacros}
                    />
                  : (
                    <div style={{ color: "var(--text-muted)", fontSize: 13, textAlign: "center", paddingTop: 60, lineHeight: 1.8 }}>
                      <div style={{ fontSize: 28, marginBottom: 12 }}>◎</div>
                      原始文档未保存<br />
                      <span style={{ fontSize: 12 }}>重新上传文件后即可在此查看<br />Markdown 渲染结果</span>
                    </div>
                  )
                }
              </div>
            </div>
          )}

          {resultLayout === "full-graph" && (
            <aside ref={leftPanelRef} className="mg-sidebar" style={{ width: leftPanelWidth, flexShrink: 0 }}>
              <div className="mg-sidebar-section">
                <div className="mg-sidebar-label">节点类型</div>
                {presentAll.map((type) => (
                  <div key={type} className={`mg-filter-item${activeTypes.has(type) ? "" : " off"}`} onClick={() => toggleType(type)}>
                    <span className="mg-filter-dot" style={{ background: nodeColor(type) }} />
                    {nodeTypeLabel(type, nodeLanguage)}
                    <span className="mg-filter-count">{typeCounts[type] ?? 0}</span>
                  </div>
                ))}
                {presentAll.length === 0 && <div style={{ fontSize: 12, color: "var(--text-muted)" }}>暂无节点</div>}
              </div>
              <div className="mg-sidebar-section">
                <div className="mg-sidebar-label">操作</div>
                <button className="mg-btn mg-btn-ghost" style={{ width: "100%", justifyContent: "flex-start", fontSize: 12, gap: 6 }} onClick={() => networkRef.current?.fit()}><Maximize2 size={13} />适应窗口</button>
                <button className="mg-btn mg-btn-ghost" style={{ width: "100%", justifyContent: "flex-start", fontSize: 12, marginTop: 6, gap: 6 }} onClick={() => setActiveTypes(new Set(ALL_NODE_TYPES))}><Eye size={13} />显示全部</button>
              </div>
            </aside>
          )}

          {/* ── Resize handle: left ── */}
          {(resultLayout === "md-graph" || resultLayout === "full-graph") && (
            <div
              onMouseDown={(e) => startResize(e, "left")}
              style={{
                width: 6, flexShrink: 0, cursor: "col-resize",
                background: "var(--border)", position: "relative",
                transition: "background .15s",
              }}
              onMouseEnter={e => (e.currentTarget.style.background = "var(--accent)")}
              onMouseLeave={e => (e.currentTarget.style.background = "var(--border)")}
            >
              <div style={{
                position: "absolute", top: "50%", left: "50%",
                transform: "translate(-50%,-50%)",
                display: "flex", flexDirection: "column", gap: 3, pointerEvents: "none",
              }}>
                {[0,1,2].map(i => (
                  <div key={i} style={{ width: 2, height: 2, borderRadius: "50%", background: "var(--muted)" }} />
                ))}
              </div>
            </div>
          )}

          {/* ── Graph — always in DOM ── */}
          <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", position: "relative" }}>
            <div className="mg-graph" style={{ flex: 1, position: "relative" }}>
              {result.nodes.length === 0 && (
                <div className="mg-empty"><div className="mg-empty-icon" style={{ fontSize: 36 }}>◈</div>暂无节点数据</div>
              )}
              {/* Lane strip — horizontal top bar for hierarchical, vertical left bar for docorder */}
              {viewMode === "hierarchical" && (() => {
                const depths = computeDepths(result.nodes, result.edges);
                const maxDepth = Math.max(0, ...Object.values(depths));
                const LANE_W = 260;
                const typeLabels: Record<number, string[]> = {};
                result.nodes.forEach(n => {
                  const d = depths[n.id] ?? 0;
                  if (d === 0) (typeLabels[0] ??= []).push(nodeTypeLabel(n.node_type, nodeLanguage));
                });
                const depth0Types = [...new Set(typeLabels[0] ?? [])];
                return (
                  <div className="mg-lane-strip">
                    {Array.from({ length: maxDepth + 1 }, (_, i) => (
                      <div key={i} className="mg-lane-cell" style={{ width: LANE_W }}>
                        <span className="mg-lane-cell-depth">{i}</span>
                        {i === 0 && depth0Types.length > 0
                          ? <span style={{ opacity: .7, fontSize: 9 }}>{depth0Types.slice(0,2).join("·")}</span>
                          : <span style={{ opacity: .5, fontSize: 9 }}>前置 {i} 层</span>
                        }
                      </div>
                    ))}
                  </div>
                );
              })()}
              {viewMode === "docorder" && (() => {
                const depths = computeDepths(result.nodes, result.edges);
                const maxDepth = Math.max(0, ...Object.values(depths));
                const LANE_H = 150; // matches computeDocOrderPositions levelSep
                return (
                  <div style={{
                    position: "absolute", left: 0, top: 0, bottom: 0,
                    display: "flex", flexDirection: "column",
                    zIndex: 5, pointerEvents: "none",
                    borderRight: "1px solid var(--line)",
                    background: "rgba(247,248,250,0.85)",
                    width: 52,
                  }}>
                    {Array.from({ length: maxDepth + 1 }, (_, i) => (
                      <div key={i} style={{
                        height: LANE_H, flexShrink: 0, padding: "4px 6px",
                        borderBottom: "1px solid var(--line)",
                        display: "flex", flexDirection: "column", justifyContent: "center",
                      }}>
                        <span style={{ fontSize: 12, fontWeight: 700, color: "var(--accent)" }}>{i}</span>
                        <span style={{ fontSize: 8, color: "var(--muted)", lineHeight: 1.3 }}>
                          {i === 0 ? "基础层" : `前置${i}层`}
                        </span>
                      </div>
                    ))}
                  </div>
                );
              })()}
              <div ref={graphRef} className="mg-graph-container" style={viewMode === "hierarchical" ? { paddingTop: 28 } : viewMode === "docorder" ? { paddingLeft: 52 } : {}} />
            </div>

          </div>

          {/* ── Resize handle: right ── */}
          {(resultLayout === "full-graph" || resultLayout === "graph-node" || (resultLayout === "md-graph" && !!(selectedNode || selectedEdge))) && (
            <div
              onMouseDown={(e) => startResize(e, "right")}
              style={{
                width: 6, flexShrink: 0, cursor: "col-resize",
                background: "var(--line)", position: "relative",
                transition: "background .15s",
              }}
              onMouseEnter={e => (e.currentTarget.style.background = "var(--accent)")}
              onMouseLeave={e => (e.currentTarget.style.background = "var(--line)")}
            >
              <div style={{
                position: "absolute", top: "50%", left: "50%",
                transform: "translate(-50%,-50%)",
                display: "flex", flexDirection: "column", gap: 3, pointerEvents: "none",
              }}>
                {[0,1,2].map(i => (
                  <div key={i} style={{ width: 2, height: 2, borderRadius: "50%", background: "var(--muted)" }} />
                ))}
              </div>
            </div>
          )}

          {/* ── Right panel ── */}
          {resultLayout === "md-graph" && (selectedNode || selectedEdge) && (
            <div ref={rightPanelRef} style={{
              width: rightPanelCollapsed ? 0 : rightPanelWidth,
              flexShrink: 0,
              overflow: "hidden",
              background: "var(--surface)",
              transition: "width .25s ease",
              display: "flex",
              flexDirection: "column",
            }}>
              {selectedEdge
                ? <EdgeInfoDrawer edge={selectedEdge} onClose={() => setSelectedEdge(null)} macros={latexMacros} />
                : <Drawer node={selectedNode} onClose={() => setSelectedNode(null)} graphId={jobId} token={auth?.token} llmConfig={llmConfig} nodeLanguage={nodeLanguage} macros={latexMacros} />
              }
            </div>
          )}

          {resultLayout === "full-graph" && (
            <div
              ref={fullGraphDrawerRef}
              style={{
                width: rightPanelCollapsed ? 0 : ((selectedNode || selectedEdge) ? rightPanelWidth : 0),
                flexShrink: 0,
                overflow: "hidden",
                transition: "width .25s ease",
                display: "flex",
                flexDirection: "column",
              }}
            >
              {selectedEdge
                ? <EdgeInfoDrawer edge={selectedEdge} onClose={() => setSelectedEdge(null)} macros={latexMacros} />
                : <Drawer node={selectedNode} onClose={() => setSelectedNode(null)} graphId={jobId} token={auth?.token} llmConfig={llmConfig} nodeLanguage={nodeLanguage} macros={latexMacros} />
              }
            </div>
          )}

          {resultLayout === "graph-node" && (
            <div ref={rightPanelRef} style={{
              width: rightPanelCollapsed ? 0 : rightPanelWidth,
              flexShrink: 0,
              borderLeft: rightPanelCollapsed ? "none" : "1px solid var(--line)",
              overflow: "hidden",
              background: "var(--surface)",
              transition: "width .25s ease",
              display: "flex",
              flexDirection: "column",
            }}>
              {selectedEdge
                ? <EdgeInfoDrawer edge={selectedEdge} onClose={() => setSelectedEdge(null)} macros={latexMacros} />
                : selectedNode
                  ? <Drawer node={selectedNode} onClose={() => setSelectedNode(null)} graphId={jobId} token={auth?.token} llmConfig={llmConfig} nodeLanguage={nodeLanguage} macros={latexMacros} />
                  : (
                    <div style={{ padding: 28, color: "var(--text-muted)", fontSize: 13, textAlign: "center", marginTop: 60 }}>
                      <div style={{ fontSize: 28, marginBottom: 12 }}>◈</div>
                      点击图谱中的节点<br />查看详细信息
                    </div>
                  )
              }
            </div>
          )}
        </div>

        {/* Text-node tooltip */}
        {tooltipState && resultLayout === "md-graph" && (
          <TextNodeTooltip
            state={tooltipState}
            nodes={result.nodes}
            onClose={() => setTooltipState(null)}
            onFocusGraph={focusGraphNode}
            nodeLanguage={nodeLanguage}
            macros={latexMacros}
          />
        )}

        {/* Height slider — fixed, shifts left when right panel is open */}
        <div style={{
          position: "fixed",
          right: (() => {
            if (rightPanelCollapsed) return 10;
            if (resultLayout === "graph-node") return rightPanelWidth + 10;
            if ((resultLayout === "md-graph" || resultLayout === "full-graph") && (selectedNode || selectedEdge)) return rightPanelWidth + 10;
            return 10;
          })(),
          top: "50%",
          transform: "translateY(-50%)",
          zIndex: 200,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 6,
          background: "rgba(255,255,255,0.94)",
          border: "1px solid var(--line)",
          borderRadius: "var(--radius)",
          padding: "10px 6px",
          boxShadow: "var(--shadow-sm)",
          userSelect: "none",
        }}>
          <span style={{ fontSize: 9, color: "var(--muted)", letterSpacing: ".04em" }}>高度</span>
          <div style={{ position: "relative", height: 160, width: 24 }}>
            <input
              ref={sliderRef}
              type="range"
              min={200}
              max={1200}
              step={10}
              defaultValue={600}
              onChange={(e) => {
                const h = Number(e.target.value);
                // Direct DOM update — no React re-render, no flicker
                if (graphRef.current) graphRef.current.style.height = h + "px";
                if (heightLabelRef.current) heightLabelRef.current.textContent = String(h);
                networkRef.current?.redraw();
              }}
              style={{
                position: "absolute",
                width: 160,
                margin: 0,
                left: "50%",
                top: "50%",
                transformOrigin: "center",
                transform: "translateX(-50%) translateY(-50%) rotate(-90deg)",
                cursor: "pointer",
                accentColor: "var(--accent)",
              }}
            />
          </div>
          <span ref={heightLabelRef} style={{ fontSize: 11, fontWeight: 600, color: "var(--ink)" }}>—</span>
          <span style={{ fontSize: 9, color: "var(--muted)" }}>px</span>
        </div>
      </div>
    </div>
  );
}

// ── Studio Wrapper (new experience) ───────────────────────────────────────────
// Thin shell that owns export state and renders the redesigned GraphStudio.
export function StudioWrapper({ workspaceMode, result, filename, jobId, sourceMarkdown, nodeLanguage, token, llmConfig, onLoadHistory, onResumeHistory, onNodeSelectionChange, onReset, onShowApiGuide }: {
  workspaceMode: WorkspaceMode; result: GraphResult; filename: string; jobId: string;
  sourceMarkdown?: string; nodeLanguage: NodeLanguage;
  token?: string; llmConfig?: LLMConfig; onLoadHistory?: (result: GraphResult, filename: string, id: string) => void;
  onResumeHistory?: (job: RestoredJob) => void;
  onNodeSelectionChange?: (selected: boolean) => void;
  onReset: () => void;
  onShowApiGuide: () => void;
}) {
  const [exporting, setExporting] = useState(false);
  const handleExport = async (format: GraphExportFormat) => {
    if (!jobId) { alert("当前结果没有关联的任务 ID，无法导出。请重新处理该文档后再导出。"); return; }
    setExporting(true);
    const requestIdentity = captureAuthRequestIdentity(token);
    try {
      const endpoint = format === "html"
        ? `/api/v2/export/${jobId}`
        : `/api/v2/export/${jobId}/artifacts`;
      const res = await protectedFetch(apiUrl(endpoint), {
        method: "POST",
        ...(format === "json" ? {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            filename,
            nodes: result.nodes,
            edges: result.edges,
          }),
        } : {}),
      }, token);
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      if (!res.ok) {
        let reason = "";
        try { reason = (await res.json())?.error || ""; } catch { /* response had no JSON body */ }
        throw new Error(reason || `导出请求失败（HTTP ${res.status}）`);
      }
      const exportMode = res.headers.get("X-MathGraph-Export-Mode");
      const blob = await res.blob();
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const stem = filename.replace(/\.\w+$/, "");
      a.href = url;
      a.download = format === "html"
        ? `${stem}_mathweaver.html`
        : exportMode === "nodes-edges-only"
          ? `${stem}_nodes_edges.zip`
          : `${stem}_processing_result.zip`;
      a.click();
      URL.revokeObjectURL(url);
      if (format === "json" && exportMode === "nodes-edges-only") {
        alert("后端阶段缓存已丢失。\n\n已为你导出仅包含 nodes.json 和 edges.json 的压缩包。");
      }
    } catch (e) {
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      const detail = e instanceof Error ? e.message : String(e);
      const hint = format === "json"
        ? "阶段缓存丢失时会自动降级导出 nodes.json 与 edges.json；若仍失败，请确认当前结果包含有效节点和边。"
        : "导出依赖后端内存中的任务结果。若后端重启过，请重新处理该文档后再导出。";
      alert(`导出失败：${detail}\n\n提示：${hint}`);
    } finally { setExporting(false); }
  };
  return (
    <GraphStudio
      workspaceMode={workspaceMode} result={result} filename={filename} graphId={jobId} sourceMarkdown={sourceMarkdown}
      nodeLanguage={nodeLanguage} token={token} llmConfig={llmConfig} onLoadHistory={onLoadHistory}
      onResumeHistory={onResumeHistory}
      onNodeSelectionChange={onNodeSelectionChange}
      onReset={onReset}
      onShowApiGuide={onShowApiGuide}
      onExport={handleExport} exporting={exporting}
    />
  );
}

// ── Error Screen ──────────────────────────────────────────────────────────────

interface ErrorScreenProps {
  errorTitle: string;
  error: string;
  errorDetail: string;
  partial: GraphResult | null;
  filename: string;
  jobId: string;
  authToken?: string;
  stagesDone: string[];
  totalStages: number;
  onReset: () => void;
  onViewPartial: () => void;
  onRetry: () => Promise<boolean>;
  onShowApiGuide: () => void;
}

function ErrorScreen({
  errorTitle, error, errorDetail, partial, filename, jobId, authToken, stagesDone, totalStages,
  onReset, onViewPartial, onRetry, onShowApiGuide,
}: ErrorScreenProps) {
  const [retrying, setRetrying] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailLoadError, setDetailLoadError] = useState("");
  const [detailData, setDetailData] = useState<{ message: string; detail: string } | null>(
    errorDetail.trim() ? { message: "", detail: errorDetail } : null,
  );
  const detailTriggerRef = useRef<HTMLButtonElement>(null);
  const detailDialogRef = useRef<HTMLDivElement>(null);
  const hasPartial = partial && partial.nodes.length > 0;
  const canViewDetail = Boolean(jobId || errorDetail.trim());

  useEffect(() => {
    setDetailOpen(false);
    setDetailLoading(false);
    setDetailLoadError("");
    setDetailData(errorDetail.trim() ? { message: "", detail: errorDetail } : null);
  }, [errorDetail, jobId]);

  const closeDetail = useCallback(() => {
    setDetailOpen(false);
    window.requestAnimationFrame(() => detailTriggerRef.current?.focus());
  }, []);

  const loadErrorDetail = useCallback(async (force = false) => {
    if (detailLoading || (detailData && !force)) return;
    setDetailLoading(true);
    setDetailLoadError("");
    try {
      if (errorDetail.trim()) {
        setDetailData({ message: "", detail: errorDetail });
        return;
      }
      if (!jobId) throw new Error("当前任务没有可用的完整错误信息");
      const response = await protectedFetch(
        apiUrl(`/api/v2/jobs/${encodeURIComponent(jobId)}/error-detail`),
        {},
        authToken,
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(body.error || "完整错误信息加载失败");
      }
      const message = typeof body.message === "string" ? body.message : "";
      const detail = typeof body.detail === "string" ? body.detail : "";
      if (!message && !detail) throw new Error("后端未返回可用的错误详情");
      setDetailData({ message, detail });
    } catch (loadError) {
      setDetailLoadError(
        loadError instanceof Error ? loadError.message : "完整错误信息加载失败，请稍后重试",
      );
    } finally {
      setDetailLoading(false);
    }
  }, [authToken, detailData, detailLoading, errorDetail, jobId]);

  const openDetail = () => {
    setDetailOpen(true);
    void loadErrorDetail();
  };

  useEffect(() => {
    if (!detailOpen) return;
    window.requestAnimationFrame(() => detailDialogRef.current?.focus());
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeDetail();
        return;
      }
      if (event.key !== "Tab") return;
      const dialog = detailDialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      )).filter((element) => element.offsetParent !== null);
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog)) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [closeDetail, detailOpen]);

  return (
    <div className="mg-root">
      <div className="mg-processing-screen">
        <div style={{
          background: "var(--bg-surface)",
          border: "1px solid #FCA5A5",
          borderRadius: "var(--radius-lg)",
          boxShadow: "var(--shadow-md)",
          padding: "44px 52px",
          width: "100%",
          maxWidth: 640,
          position: "relative",
        }}>
          <button
            className="mg-btn mg-btn-ghost"
            onClick={onShowApiGuide}
            style={{ position: "absolute", top: 14, right: 14, fontSize: 11, gap: 5 }}
          >
            <CircleHelp size={13} />
            API 配置指南
          </button>
          <div style={{ fontSize: 28, marginBottom: 8 }}>⚠</div>
          <div style={{ fontSize: 17, fontWeight: 600, marginBottom: 4 }}>处理失败</div>
          <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 24 }}>{filename}</div>

          <div style={{ background: "var(--danger-light)", border: "1px solid var(--danger-line)", borderRadius: "var(--radius-sm)", padding: "12px 14px", color: "var(--danger)", marginBottom: 20, lineHeight: 1.6 }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 3 }}>{errorTitle}</div>
            <div style={{ fontSize: 12 }}>{error}</div>
          </div>

          {stagesDone.length > 0 && (
            <div style={{ marginBottom: 20 }}>
              <div style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: ".07em", color: "var(--text-muted)", marginBottom: 8 }}>
                已完成的阶段 ({stagesDone.length}/{totalStages})
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {stagesDone.map((k) => (
                  <span key={k} style={{ background: "var(--ok-light)", border: "1px solid var(--ok-line)", borderRadius: 3, padding: "2px 8px", fontSize: 11, color: "var(--ok)" }}>
                    ✓ {pipelineStageLabel(k)}
                  </span>
                ))}
              </div>
            </div>
          )}

          {hasPartial && (
            <div style={{ background: "var(--accent-light)", border: "1px solid #C5D5EE", borderRadius: "var(--radius-sm)", padding: "12px 14px", fontSize: 13, color: "var(--accent)", marginBottom: 20 }}>
              虽然流程未完整走完，但已解析出 <strong>{partial!.nodes.length}</strong> 个节点。
              可以查看这些中间产物。
            </div>
          )}

          {canViewDetail && (
            <button
              ref={detailTriggerRef}
              style={{ fontSize: 11, color: "var(--text-muted)", background: "none", border: "none", cursor: "pointer", padding: 0, marginBottom: 20, textDecoration: "underline" }}
              onClick={openDetail}
            >
              查看完整错误信息
            </button>
          )}

          <div style={{ display: "flex", gap: 10 }}>
            <button className="mg-btn mg-btn-ghost mg-error-action-button" onClick={onReset} style={{ flex: 1 }}>
              ← 重新上传
            </button>
            <button
              className="mg-btn mg-btn-primary mg-error-action-button"
              disabled={retrying}
              onClick={async () => {
                setRetrying(true);
                try {
                  await onRetry();
                } finally {
                  setRetrying(false);
                }
              }}
              style={{ flex: 1 }}
            >
              {retrying ? "恢复中…" : "重试"}
            </button>
            {hasPartial && (
              <button className="mg-btn mg-btn-primary" onClick={onViewPartial} style={{ flex: 1 }}>
                查看已解析节点 ({partial!.nodes.length})
              </button>
            )}
          </div>
        </div>
      </div>
      {detailOpen && (
        <div
          className="mg-motion-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeDetail();
          }}
          style={{
            position: "fixed", inset: 0, zIndex: 1400, padding: 16,
            display: "flex", alignItems: "center", justifyContent: "center",
            background: "rgba(0,0,0,.48)",
          }}
        >
          <div
            ref={detailDialogRef}
            className="mg-motion-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="error-detail-title"
            tabIndex={-1}
            style={{
              width: "min(760px, calc(100vw - 32px))",
              maxHeight: "min(82vh, 720px)",
              display: "flex",
              flexDirection: "column",
              background: "var(--surface)",
              border: "1px solid var(--line)",
              borderRadius: "var(--radius-lg)",
              boxShadow: "var(--shadow-lg)",
              overflow: "hidden",
              outline: "none",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, padding: "18px 20px", borderBottom: "1px solid var(--line)" }}>
              <div>
                <div id="error-detail-title" style={{ fontSize: 16, fontWeight: 700, color: "var(--ink)" }}>完整错误信息</div>
                <div style={{ marginTop: 3, fontSize: 11, color: "var(--muted)" }}>以下为已脱敏的后端原始报错，仅用于问题定位。</div>
              </div>
              <button
                type="button"
                aria-label="关闭完整错误信息"
                onClick={closeDetail}
                style={{ border: 0, background: "transparent", color: "var(--muted)", cursor: "pointer", fontSize: 22, lineHeight: 1 }}
              >
                ×
              </button>
            </div>
            <div style={{ flex: "1 1 auto", minHeight: 0, padding: 20, overflow: "auto" }}>
              {detailLoading && (
                <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--muted)", fontSize: 13 }}>
                  <Loader2 size={15} className="api-guide-spin" />
                  正在读取后端错误信息…
                </div>
              )}
              {!detailLoading && detailLoadError && (
                <div style={{ padding: 14, border: "1px solid var(--danger-line)", borderRadius: "var(--radius-sm)", background: "var(--danger-light)", color: "var(--danger)", fontSize: 12 }}>
                  <div>{detailLoadError}</div>
                  <button
                    type="button"
                    className="mg-btn mg-btn-ghost"
                    onClick={() => void loadErrorDetail(true)}
                    style={{ marginTop: 10, fontSize: 11 }}
                  >
                    重新加载
                  </button>
                </div>
              )}
              {!detailLoading && !detailLoadError && detailData && (
                <div style={{ display: "grid", gap: 14 }}>
                  {detailData.message && (
                    <section>
                      <div style={{ marginBottom: 6, color: "var(--muted)", fontSize: 11, fontWeight: 700 }}>异常信息</div>
                      <pre style={{ margin: 0, padding: 14, overflowX: "auto", whiteSpace: "pre-wrap", overflowWrap: "anywhere", borderRadius: 5, background: "var(--bg-code)", color: "#E5E5E7", fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.6 }}>
                        {detailData.message}
                      </pre>
                    </section>
                  )}
                  {detailData.detail && (
                    <section>
                      <div style={{ marginBottom: 6, color: "var(--muted)", fontSize: 11, fontWeight: 700 }}>Traceback</div>
                      <pre style={{ margin: 0, padding: 14, overflowX: "auto", whiteSpace: "pre-wrap", overflowWrap: "anywhere", borderRadius: 5, background: "var(--bg-code)", color: "#E5E5E7", fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.6 }}>
                        {detailData.detail}
                      </pre>
                    </section>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Settings Modal ────────────────────────────────────────────────────────────

interface SettingsModalProps {
  profiles: LLMProfile[];
  activeIndex: number;
  auth: AuthState;
  onSave: (profiles: LLMProfile[], activeIndex: number) => void;
  onClose: () => void;
  onLogout: () => void;
  onChangePassword: () => void;
  nodeLanguage: NodeLanguage;
  onNodeLanguageChange: (lang: NodeLanguage) => void;
  onShowApiGuide: (step: ApiGuideStep) => void;
}

const EMPTY_PROFILE: LLMProfile = {
  name: "", api_url: "", model_name: "", api_key: "",
  embedding_url: "", embedding_model: "", embedding_api_key: "",
};

function SettingsModal({
  profiles, activeIndex, auth, onSave, onClose, onLogout, onChangePassword, nodeLanguage,
  onNodeLanguageChange, onShowApiGuide,
}: SettingsModalProps) {
  const [drafts, setDrafts] = useState<LLMProfile[]>(profiles.length > 0 ? profiles.map(p => ({ ...p })) : [{ ...EMPTY_PROFILE, name: "默认配置" }]);
  const [activeIdx, setActiveIdx] = useState(Math.min(activeIndex, Math.max(0, profiles.length - 1)));
  const [editingIdx, setEditingIdx] = useState<number | null>(profiles.length === 0 ? 0 : null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState("");
  const [closing, setClosing] = useState(false);

  const closeSoftly = () => {
    if (closing) return;
    setClosing(true);
    window.setTimeout(onClose, 140);
  };

  const handleSave = async () => {
    const missingEmbeddingModel = drafts.findIndex(p => !p.embedding_model.trim());
    if (missingEmbeddingModel >= 0) {
      const profileName = drafts[missingEmbeddingModel].name || `配置 ${missingEmbeddingModel + 1}`;
      setErr(`请为“${profileName}”填写 Embedding 模型`);
      setEditingIdx(missingEmbeddingModel);
      return;
    }
    const requestIdentity = captureAuthRequestIdentity(auth.token);
    setSaving(true); setErr(""); setSaved(false);
    try {
      const res = await protectedFetch(apiUrl("/api/v2/settings"), {
        method: "PUT",
        headers: authHeaders(auth.token),
        body: JSON.stringify({ configs: drafts, active_index: activeIdx }),
      }, auth.token);
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      if (!res.ok) { setErr("保存失败，请重试"); return; }
      onSave(drafts, activeIdx);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      if (isAuthRequestIdentityCurrent(requestIdentity)) setErr("无法连接到后端");
    }
    finally { setSaving(false); }
  };

  const addProfile = () => {
    const next = [...drafts, { ...EMPTY_PROFILE, name: `配置 ${drafts.length + 1}` }];
    setDrafts(next);
    setEditingIdx(next.length - 1);
  };

  const deleteProfile = (i: number) => {
    const next = drafts.filter((_, idx) => idx !== i);
    setDrafts(next);
    const newActive = Math.min(activeIdx, Math.max(0, next.length - 1));
    setActiveIdx(newActive);
    setEditingIdx(null);
  };

  const updateDraft = (i: number, patch: Partial<LLMProfile>) => {
    setDrafts(prev => prev.map((p, idx) => idx === i ? { ...p, ...patch } : p));
  };

  const openApiGuide = () => {
    const dirty = JSON.stringify(drafts) !== JSON.stringify(profiles);
    if (dirty && !window.confirm("当前账号设置中有未保存的修改。打开向导将放弃这些修改，是否继续？")) return;
    onClose();
    onShowApiGuide("provider");
  };

  return (
    <div className={`mg-motion-backdrop ${closing ? "closing" : ""}`} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1200 }}
      onClick={closeSoftly}>
      <div className={`mg-motion-dialog mg-settings-dialog ${closing ? "closing" : ""}`} style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--radius-lg)", padding: 4, width: 526, maxWidth: "calc(100vw - 24px)", maxHeight: "85vh", overflow: "hidden", boxShadow: "var(--shadow-lg)" }}
        onClick={e => e.stopPropagation()}>
        <div className="mg-settings-dialog-scroll" style={{ width: "100%", maxHeight: "calc(85vh - 10px)", overflow: "auto", borderRadius: "calc(var(--radius-lg) - 4px)", padding: "24px 28px" }}>

        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
          <div style={{ fontSize: 17, fontWeight: 600, fontFamily: "var(--font-display)", color: "var(--ink)" }}>账号设置</div>
          <button style={{ background: "none", border: "none", fontSize: 20, color: "var(--muted)", cursor: "pointer", fontFamily: "inherit" }} onClick={closeSoftly}>×</button>
        </div>
        <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 20, padding: "7px 12px", background: "var(--bg)", borderRadius: "var(--radius-sm)" }}>
          {auth.user.display_name} · {auth.user.student_no ?? auth.user.email ?? "未设置登录标识"}
        </div>

        {/* Node language */}
        <div style={{ marginBottom: 22 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: "var(--muted)", textTransform: "uppercase", letterSpacing: ".06em", marginBottom: 10 }}>节点语言</div>
          <div style={{ display: "flex", background: "var(--surface-alt)", borderRadius: 9, padding: 3, gap: 2 }}>
            {([ ["zh", "中文"], ["bilingual", "双语"], ["en", "English"] ] as [NodeLanguage, string][]).map(([lang, label]) => {
              const active = nodeLanguage === lang;
              return (
                <button
                  key={lang}
                  onClick={() => { onNodeLanguageChange(lang); saveNodeLanguage(lang); }}
                  style={{
                    flex: 1, padding: "7px 0", borderRadius: 6, fontSize: 13,
                    fontWeight: active ? 600 : 400,
                    border: "none",
                    background: active ? "var(--surface)" : "transparent",
                    color: active ? "var(--accent)" : "var(--muted)",
                    boxShadow: active ? "0 1px 4px rgba(23,32,42,0.10)" : "none",
                    cursor: "pointer", transition: "all .15s",
                    textAlign: "center", display: "block",
                  }}
                >{label}</button>
              );
            })}
          </div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 6 }}>控制节点类型、节点标签与详情面板标题语言，不影响公式和原文</div>
        </div>

        {/* Profile list */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: "var(--ink)" }}>LLM 配置</div>
            <button type="button" className="mg-settings-guide-link" onClick={openApiGuide}>不会填写？打开配置向导</button>
          </div>
          <button
            style={{ background: "none", border: "1px solid var(--accent)", color: "var(--accent)", borderRadius: 6, padding: "3px 10px", fontSize: 11, cursor: "pointer", fontWeight: 500 }}
            onClick={addProfile}
          >+ 添加</button>
        </div>

        {drafts.length === 0 && (
          <div style={{ fontSize: 13, color: "var(--text-muted)", textAlign: "center", padding: "20px 0" }}>暂无配置，点击添加</div>
        )}

        {drafts.map((p, i) => {
          const isActive = i === activeIdx;
          const isEditing = editingIdx === i;
          return (
            <div key={i} style={{ border: `1.5px solid ${isActive ? "var(--accent)" : "var(--line)"}`, borderRadius: "var(--radius)", marginBottom: 8, overflow: "hidden" }}>
              {/* Row header */}
              <div
                style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 12px", cursor: "pointer", background: isActive ? "var(--accent-light)" : "var(--bg)" }}
                onClick={() => { setActiveIdx(i); setEditingIdx(isEditing ? null : i); }}
              >
                <span style={{ width: 10, height: 10, borderRadius: "50%", flexShrink: 0, border: `2px solid ${isActive ? "var(--accent)" : "var(--line-strong)"}`, background: isActive ? "var(--accent)" : "transparent" }} />
                <span style={{ flex: 1, fontSize: 13, fontWeight: isActive ? 600 : 400, color: isActive ? "var(--accent)" : "var(--ink)" }}>
                  {p.name || `配置 ${i + 1}`}
                  {isActive && <span style={{ marginLeft: 8, fontSize: 10, background: "var(--accent)", color: "#fff", borderRadius: 4, padding: "1px 6px" }}>使用中</span>}
                </span>
                <span style={{ fontSize: 11, color: "var(--muted)" }}>{isEditing ? "▲" : "▼"}</span>
                <button
                  style={{ background: "none", border: "none", color: "var(--danger)", fontSize: 12, cursor: "pointer", padding: "0 4px", opacity: 0.7 }}
                  onClick={e => { e.stopPropagation(); deleteProfile(i); }}
                  title="删除此配置"
                >✕</button>
              </div>

              {/* Expanded edit form */}
              {isEditing && (
                <div className="mg-motion-accordion" style={{ padding: "12px 14px 14px", borderTop: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 9 }}>
                  <div className="mg-field">
                    <label className="mg-label">名称</label>
                    <input className="mg-input" placeholder="DeepSeek / OpenAI / …"
                      value={p.name} onChange={e => updateDraft(i, { name: e.target.value })} />
                  </div>
                  <div className="mg-field">
                    <label className="mg-label">API URL（Base URL）</label>
                    <input className="mg-input" placeholder="https://api.example.com/v1"
                      value={p.api_url} onChange={e => updateDraft(i, { api_url: e.target.value })} />
                  </div>
                  <div className="mg-field">
                    <label className="mg-label">模型名</label>
                    <input className="mg-input" placeholder="chat-model-id"
                      value={p.model_name} onChange={e => updateDraft(i, { model_name: e.target.value })} />
                  </div>
                  <div className="mg-field">
                    <label className="mg-label">API Key</label>
                    <input className="mg-input" type="password" placeholder="sk-…"
                      value={p.api_key} onChange={e => updateDraft(i, { api_key: e.target.value })} />
                  </div>
                  <div className="mg-field">
                    <label className="mg-label">Embedding URL（可选）</label>
                    <input className="mg-input" placeholder="默认使用 LLM API URL"
                      value={p.embedding_url} onChange={e => updateDraft(i, { embedding_url: e.target.value })} />
                  </div>
                  <div className="mg-field">
                    <label className="mg-label">Embedding 模型</label>
                    <input className="mg-input" placeholder="embedding-model-id"
                      value={p.embedding_model} onChange={e => updateDraft(i, { embedding_model: e.target.value })} />
                  </div>
                  <div className="mg-field">
                    <label className="mg-label">Embedding API Key（可选）</label>
                    <input className="mg-input" type="password" placeholder="默认使用 LLM API Key"
                      value={p.embedding_api_key} onChange={e => updateDraft(i, { embedding_api_key: e.target.value })} />
                  </div>
                </div>
              )}
            </div>
          );
        })}

        {err && <p style={{ fontSize: 12, color: "var(--danger)", margin: "8px 0" }}>{err}</p>}

        <button className="mg-btn mg-btn-primary" style={{ width: "100%", marginTop: 16, marginBottom: 10 }}
          onClick={handleSave} disabled={saving}>
          {saving ? "保存中…" : saved ? "✓ 已保存" : "保存所有配置"}
        </button>
        <button style={{ display: "flex", alignItems: "center", justifyContent: "center", width: "100%", background: "none", border: "none", color: "var(--accent)", fontSize: 12, cursor: "pointer", padding: "6px 0" }}
          onClick={onChangePassword}>修改密码</button>
        <button style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 6, width: "100%", background: "none", border: "none", color: "var(--muted)", fontSize: 12, cursor: "pointer", padding: "6px 0" }}
          onClick={onLogout}><LogOut size={13} />退出登录</button>
        </div>
      </div>
    </div>
  );
}

// ── Root Component ────────────────────────────────────────────────────────────

export default function Home() {
  const { jobs, latestJobId, startJob, resumeJob } = useJobs();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedGuideStep = searchParams.get("step");
  const guideStep: ApiGuideStep = (
    ["intro", "provider", "chat", "embedding", "test"].includes(requestedGuideStep || "")
      ? requestedGuideStep
      : "intro"
  ) as ApiGuideStep;
  const showApiGuide = searchParams.get("guide") === "api-setup";

  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>("generate");
  const [view, setView] = useState<View>("upload");
  const [jobId, setJobId] = useState<string | null>(null);
  const [result, setResult] = useState<GraphResult | null>(null);
  const [sourceMarkdown, setSourceMarkdown] = useState<string | undefined>();
  const [errorInfo, setErrorInfo] = useState<ErrorInfo | null>(null);
  const [filename, setFilename] = useState("input.md");
  const [generateUploadEpoch, setGenerateUploadEpoch] = useState(0);
  const [importUploadEpoch, setImportUploadEpoch] = useState(0);
  const activeWorkspaceModeRef = useRef<WorkspaceMode>("generate");
  const studioHistorySaveKeys = useRef(new Set<string>());
  const uploadSubmitButtonRef = useRef<HTMLButtonElement>(null);
  const workspaceStates = useRef<Record<WorkspaceMode, WorkspaceSnapshot>>({
    generate: { view: "upload", jobId: null, result: null, errorInfo: null, filename: "input.md" },
    import: { view: "upload", jobId: null, result: null, errorInfo: null, filename: "graph-import" },
  });

  // LLM config — active config used for processing
  const [llm, setLlm] = useState<LLMConfig>({
    api_url: "", model_name: "", api_key: "",
    embedding_url: "", embedding_model: "", embedding_api_key: "",
  });
  const updateLlm = (patch: Partial<LLMConfig>) => {
    setLlm(prev => { const next = { ...prev, ...patch }; saveLlm(next); return next; });
  };
  // Multi-profile state (managed in settings modal, synced to account)
  const [llmProfiles, setLlmProfiles] = useState<LLMProfile[]>([]);
  const [llmActiveIdx, setLlmActiveIdx] = useState(0);
  const [configReady, setConfigReady] = useState(false);

  // Auth
  const [auth, setAuth] = useState<AuthState | null>(null);
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showPasswordChange, setShowPasswordChange] = useState(false);
  const [passwordChangeRequiredHint, setPasswordChangeRequiredHint] = useState(false);
  const [nodeLanguage, setNodeLanguage] = useState<NodeLanguage>(() => loadNodeLanguage());
  const [studioNodeSelected, setStudioNodeSelected] = useState(false);
  const clearUserMemory = useCallback(() => {
    // Web 账号退出/切换时，不能让图谱、原文或模型密钥留给下一个登录账号。
    setLlm({ ...EMPTY_LLM_CONFIG });
    setLlmProfiles([]);
    setLlmActiveIdx(0);
    setResult(null);
    setSourceMarkdown(undefined);
    setJobId(null);
    setErrorInfo(null);
    setFilename("input.md");
    setGenerateUploadEpoch((value) => value + 1);
    setImportUploadEpoch((value) => value + 1);
    studioHistorySaveKeys.current.clear();
    setWorkspaceMode("generate");
    activeWorkspaceModeRef.current = "generate";
    workspaceStates.current = {
      generate: { view: "upload", jobId: null, result: null, errorInfo: null, filename: "input.md" },
      import: { view: "upload", jobId: null, result: null, errorInfo: null, filename: "graph-import" },
    };
    setView("upload");
  }, []);
  const setGuideLocation = useCallback((step: ApiGuideStep, replace = true) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("guide", "api-setup");
      next.set("step", step);
      return next;
    }, { replace });
  }, [setSearchParams]);
  const closeApiGuide = useCallback(() => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("guide");
      next.delete("step");
      return next;
    }, { replace: true });
  }, [setSearchParams]);
  // Results always open in Studio; the classic implementation remains internal only.
  const [experience, setExperience] = useState<"studio" | "classic">("studio");
  const switchExperience = useCallback((e: "studio" | "classic") => {
    setExperience(e);
    try { localStorage.setItem("mathgraph.experience", e); } catch { /* quota */ }
  }, []);

  const handleNodeLanguageChange = useCallback((lang: NodeLanguage) => {
    setNodeLanguage(lang);
    saveNodeLanguage(lang);
  }, []);

  // Fetch LLM profiles from account after login
  const fetchAccountSettings = async (token: string) => {
    const requestIdentity = captureAuthRequestIdentity(token);
    try {
      const res = await authFetch(apiUrl("/api/v2/settings"), {}, token);
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      if (res.status === 401) {
        setAuth(null);
        return;
      }
      if (!res.ok) return;
      const s = await res.json();
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      const profiles: LLMProfile[] = (Array.isArray(s.configs) ? s.configs : []).map((profile: Partial<LLMProfile>) => ({
        name: profile.name ?? "",
        api_url: profile.api_url ?? "",
        model_name: profile.model_name ?? "",
        api_key: profile.api_key ?? "",
        embedding_url: profile.embedding_url ?? "",
        embedding_model: profile.embedding_model ?? "",
        embedding_api_key: profile.embedding_api_key ?? "",
      }));
      const idx: number = s.active_index ?? 0;
      setLlmProfiles(profiles);
      setLlmActiveIdx(idx);
      if (profiles.length > 0) {
        const active = profiles[Math.min(idx, profiles.length - 1)];
        const cfg: LLMConfig = {
          api_url: active.api_url, model_name: active.model_name, api_key: active.api_key,
          embedding_url: active.embedding_url, embedding_model: active.embedding_model,
          embedding_api_key: active.embedding_api_key,
        };
        setLlm(cfg);
        saveLlm(cfg);
      } else {
        setLlm({ ...EMPTY_LLM_CONFIG });
        saveLlm({ ...EMPTY_LLM_CONFIG });
      }
    } catch { /* ignore */ }
  };

  // On mount: hydrate browser-only local settings/auth after SSR has matched.
  useEffect(() => {
    setLlm(loadLlm());
    const a = loadAuth();
    if (!a) {
      if (!isDesktopRuntime()) setShowAuthModal(true);
      setConfigReady(true);
      return;
    }
    setAuth(a);
    if (a.user.initial_password_pending) {
      setPasswordChangeRequiredHint(true);
      setShowPasswordChange(true);
    }
    void fetchAccountSettings(a.token).finally(() => setConfigReady(true));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => subscribeAuthInvalidated(() => {
    // 会话过期时同步清理 React 内存和敏感浮层，Web 端立即回到登录入口。
    setAuth(null);
    setShowHistory(false);
    setShowSettings(false);
    setShowPasswordChange(false);
    setPasswordChangeRequiredHint(false);
    clearUserMemory();
    if (!isDesktopRuntime()) setShowAuthModal(true);
  }), [clearUserMemory]);

  // The classic result screen saves history itself. Studio bypasses that screen,
  // so persist every logged-in Studio result here for both workspace entries.
  useEffect(() => {
    if (experience !== "studio" || view !== "result" || !auth || !jobId || !result) return;
    const saveKey = `${auth.token}:${jobId}`;
    if (studioHistorySaveKeys.current.has(saveKey)) return;
    studioHistorySaveKeys.current.add(saveKey);

    void protectedFetch(apiUrl("/api/v2/history"), {
      method: "POST",
      headers: authHeaders(auth.token),
      body: JSON.stringify({ job_id: jobId }),
    }, auth.token).catch(() => {
      // Keep the result usable even if the background history save fails.
    });
  }, [auth, experience, jobId, result, view]);

  // Dev-only fixture loader: /workspace?fixture=NAME loads /fixtures/NAME.json
  // ({ result, sourceMarkdown, filename }) straight into the result view, so the
  // redesigned Studio can be exercised on real graphs without auth/upload.
  useEffect(() => {
    const isLocal = typeof window !== "undefined" && /^(localhost|127\.0\.0\.1)$/.test(window.location.hostname);
    if (!isLocal) return;
    const name = new URLSearchParams(window.location.search).get("fixture");
    if (!name || !/^[\w-]+$/.test(name)) return;
    fetch(`/fixtures/${name}.json`).then(r => r.ok ? r.json() : null).then((d) => {
      if (!d?.result) return;
      setResult(dedupeGraph(d.result as GraphResult));
      setSourceMarkdown(d.sourceMarkdown || undefined);
      setFilename(d.filename || `${name}.md`);
      setJobId(d.jobId || "fixture");
      setView("result");
    }).catch(() => { /* no fixture */ });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const applySnapshot = useCallback((snapshot: WorkspaceSnapshot) => {
    setView(snapshot.view);
    setJobId(snapshot.jobId);
    setResult(snapshot.result ? dedupeGraph(snapshot.result) : null);
    setSourceMarkdown(snapshot.sourceMarkdown);
    setErrorInfo(snapshot.errorInfo);
    setFilename(snapshot.filename);
  }, []);

  const captureSnapshot = useCallback((): WorkspaceSnapshot => ({
    view, jobId, result, sourceMarkdown, errorInfo, filename,
  }), [view, jobId, result, sourceMarkdown, errorInfo, filename]);

  const switchWorkspaceMode = useCallback((nextMode: WorkspaceMode) => {
    if (nextMode === workspaceMode) return;
    workspaceStates.current[workspaceMode] = captureSnapshot();
    activeWorkspaceModeRef.current = nextMode;
    setWorkspaceMode(nextMode);
    applySnapshot(workspaceStates.current[nextMode]);
  }, [workspaceMode, captureSnapshot, applySnapshot]);

  // Restore the most recent result for both modes.
  useEffect(() => {
    (["generate", "import"] as WorkspaceMode[]).forEach((sessionMode) => {
      const saved = loadSession(sessionMode);
      if (!saved) return;
      workspaceStates.current[sessionMode] = {
        view: "result",
        jobId: saved.jobId,
        result: saved.result,
        sourceMarkdown: saved.sourceMarkdown,
        errorInfo: null,
        filename: saved.filename,
      };
    });
    if (workspaceStates.current.generate.view === "result") {
      applySnapshot(workspaceStates.current.generate);
    }
  }, [applySnapshot]);

  useEffect(() => {
    workspaceStates.current[workspaceMode] = captureSnapshot();
  }, [workspaceMode, captureSnapshot]);

  // React to background job state changes (done / error)
  useEffect(() => {
    if (!latestJobId) return;
    const job = jobs[latestJobId];
    if (!job) return;

    if (job.phase === "done" && job.result) {
      const graph = dedupeGraph({ ...(job.result as GraphResult), source_mode: "generate" });
      const snapshot: WorkspaceSnapshot = {
        view: "result", jobId: job.id, result: graph,
        sourceMarkdown: job.sourceMarkdown || undefined,
        errorInfo: null, filename: job.filename,
      };
      workspaceStates.current.generate = snapshot;
      saveSession("generate", { result: graph, filename: job.filename, jobId: job.id, sourceMarkdown: job.sourceMarkdown });
      if (activeWorkspaceModeRef.current !== "generate") return;
      setResult(graph);
      setFilename(job.filename);
      setJobId(job.id);
      setSourceMarkdown(job.sourceMarkdown || undefined);
      setView("result");
    }

    if (job.phase === "error") {
      const nextError = {
        code: job.errorCode ?? "internal",
        title: job.errorTitle ?? "处理过程中出现异常",
        msg: job.errorMsg ?? "未知错误",
        detail: "",
        partial: null,
        stages: job.stagesDone,
        totalStages: job.totalStages,
      };
      workspaceStates.current.generate = {
        ...workspaceStates.current.generate,
        view: "error",
        jobId: job.id,
        filename: job.filename,
        errorInfo: nextError,
      };
      if (activeWorkspaceModeRef.current !== "generate") return;
      setJobId(job.id);
      setFilename(job.filename);
      setErrorInfo(nextError);
      setView("error");
    }
  }, [jobs, latestJobId]);

  const handleSubmit = async (content: string, fname: string, cfg: LLMConfig) => {
    if (!auth && !isDesktopRuntime()) {
      setShowAuthModal(true);
      return;
    }
    const llm = cfg;
    const requestIdentity = captureAuthRequestIdentity(auth?.token);
    setErrorInfo(null);

    const formData = new FormData();
    const blob = new Blob([content], { type: "text/markdown" });
    formData.append("file", blob, fname);
    formData.append("api_url", llm.api_url);
    formData.append("model_name", llm.model_name);
    formData.append("api_key", llm.api_key);
    formData.append("embedding_url", llm.embedding_url);
    formData.append("embedding_model", llm.embedding_model);
    formData.append("embedding_api_key", llm.embedding_api_key);

    try {
      const res = await protectedFetch(apiUrl("/api/v2/jobs"), {
        method: "POST",
        body: formData,
      }, auth?.token);
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const nextError: ErrorInfo = {
          code: res.status === 400 ? "document_input" : "internal",
          title: res.status === 400 ? "文档内容无法处理" : "文档提交失败",
          msg: res.status === 400
            ? "请确认文件内容和 API 配置完整后重新提交。"
            : "后端暂时无法接收该任务，请稍后重试。",
          detail: body.error ?? body.message ?? "",
          partial: null,
          stages: [],
        };
        if (activeWorkspaceModeRef.current !== "generate") {
          workspaceStates.current.generate = {
            ...workspaceStates.current.generate,
            view: "error",
            errorInfo: nextError,
          };
          return;
        }
        setErrorInfo(nextError);
        setView("error");
        return;
      }
      const { job_id } = await res.json();
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      // Hand off to background context — view stays where it is
      startJob(job_id, fname, content);
    } catch (e) {
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      const nextError: ErrorInfo = {
        code: "network",
        title: "无法连接后端服务",
        msg: "请确认后端服务已经启动，并检查本机网络后重试。",
        detail: String(e),
        partial: null,
        stages: [],
      };
      if (activeWorkspaceModeRef.current !== "generate") {
        workspaceStates.current.generate = {
          ...workspaceStates.current.generate,
          view: "error",
          errorInfo: nextError,
        };
        return;
      }
      setErrorInfo(nextError);
      setView("error");
    }
  };

  const handleGraphImportSubmit = async (nodesFile: File, edgesFile: File, markdownFile?: File) => {
    if (!auth && !isDesktopRuntime()) {
      setShowAuthModal(true);
      throw new Error("请先登录后再导入图谱");
    }
    const formData = new FormData();
    const requestIdentity = captureAuthRequestIdentity(auth?.token);
    formData.append("nodes_file", nodesFile);
    formData.append("edges_file", edgesFile);
    if (markdownFile) formData.append("markdown_file", markdownFile);
    const res = await protectedFetch(
      apiUrl("/api/v2/agent-import"),
      { method: "POST", body: formData },
      auth?.token,
    );
    if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
    const body = await res.json().catch(() => ({}));
    if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
    if (!res.ok) throw new Error(body.error ?? "图谱导入失败");
    const markdown = markdownFile ? await markdownFile.text() : undefined;
    if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
    const importedResult = body.result as GraphResult;
    importedResult.source_mode = "import";
    const snapshot: WorkspaceSnapshot = {
      view: "result",
      jobId: body.job_id,
      result: importedResult,
      sourceMarkdown: markdown,
      errorInfo: null,
      filename: body.filename,
    };
    workspaceStates.current.import = snapshot;
    saveSession("import", { result: snapshot.result!, filename: snapshot.filename, jobId: snapshot.jobId!, sourceMarkdown: markdown });
    if (activeWorkspaceModeRef.current === "import") applySnapshot(snapshot);
  };

  const handleAuth = (nextAuth: AuthState) => {
    if (auth && auth.user.id !== nextAuth.user.id) clearUserMemory();
    saveAuth(nextAuth);
    setAuth(nextAuth);
    setShowAuthModal(false);
    if (nextAuth.user.initial_password_pending) {
      setPasswordChangeRequiredHint(true);
      setShowPasswordChange(true);
    }
    fetchAccountSettings(nextAuth.token);
  };

  const handleLogout = async () => {
    if (auth) {
      await protectedFetch(apiUrl("/api/v2/auth/logout"), {
        method: "POST",
        headers: authHeaders(auth.token),
      }, auth.token).catch(() => {});
    }
    clearAuthAndNotify();
    setAuth(null);
    clearUserMemory();
    setShowHistory(false);
    setShowSettings(false);
    setShowPasswordChange(false);
  };

  const handleGuideComplete = async (config: LLMConfig, profileName: string) => {
    if (auth) {
      const requestIdentity = captureAuthRequestIdentity(auth.token);
      const activeIdx = llmProfiles.length
        ? Math.min(llmActiveIdx, llmProfiles.length - 1)
        : 0;
      const nextProfiles: LLMProfile[] = llmProfiles.length
        ? llmProfiles.map((profile, index) => (
            index === activeIdx
              ? { ...config, name: profile.name || profileName }
              : profile
          ))
        : [{ ...config, name: profileName }];
      const response = await protectedFetch(apiUrl("/api/v2/settings"), {
        method: "PUT",
        headers: authHeaders(auth.token),
        body: JSON.stringify({ configs: nextProfiles, active_index: activeIdx }),
      }, auth.token);
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      if (!response.ok) throw new Error("账号配置保存失败，请重试。");
      setLlmProfiles(nextProfiles);
      setLlmActiveIdx(activeIdx);
    }
    setLlm(config);
    saveLlm(config);
    closeApiGuide();
    window.setTimeout(() => uploadSubmitButtonRef.current?.focus(), 0);
  };

  const handleHistoryLoad = (histResult: GraphResult, histFilename: string, histId: string) => {
    const targetMode = activeWorkspaceModeRef.current;
    const md = loadMd(histId);
    const restoredResult = { ...histResult, source_mode: histResult.source_mode ?? targetMode };
    const snapshot: WorkspaceSnapshot = {
      view: "result", jobId: histId, result: restoredResult, sourceMarkdown: md,
      errorInfo: null, filename: histFilename,
    };
    workspaceStates.current[targetMode] = snapshot;
    applySnapshot(snapshot);
    saveSession(targetMode, { result: restoredResult, filename: histFilename, jobId: histId, sourceMarkdown: md });
  };

  const handleHistoryResume = (job: RestoredJob) => {
    const snapshot: WorkspaceSnapshot = {
      view: "upload",
      jobId: job.job_id,
      result: null,
      sourceMarkdown: job.source_markdown || undefined,
      errorInfo: null,
      filename: job.filename,
    };
    workspaceStates.current.generate = snapshot;
    activeWorkspaceModeRef.current = "generate";
    setWorkspaceMode("generate");
    applySnapshot(snapshot);
  };

  const resetView = () => {
    setStudioNodeSelected(false);
    clearSession(workspaceMode);
    if (workspaceMode === "generate") setGenerateUploadEpoch((value) => value + 1);
    else setImportUploadEpoch((value) => value + 1);
    const snapshot: WorkspaceSnapshot = {
      view: "upload", jobId: null, result: null, sourceMarkdown: undefined,
      errorInfo: null, filename: workspaceMode === "generate" ? "input.md" : "graph-import",
    };
    workspaceStates.current[workspaceMode] = snapshot;
    applySnapshot(snapshot);
  };

  const activeUploadJob = latestJobId ? jobs[latestJobId] : undefined;
  const activeUploadFilename = activeUploadJob?.phase === "running" ? activeUploadJob.filename : null;
  const isGenerating = Object.values(jobs).some((job) => job.phase === "running");

  return (
    <>
      <WorkspaceModeSwitch
        mode={workspaceMode}
        onChange={switchWorkspaceMode}
        isGenerating={isGenerating}
      />
      {showApiGuide && (
        <ApiSetupGuide
          config={llm}
          step={guideStep}
          signedIn={Boolean(auth)}
          onStepChange={(step) => setGuideLocation(step)}
          onClose={closeApiGuide}
          onComplete={handleGuideComplete}
        />
      )}
      {/* Auth modal — shown on demand or on first visit */}
      {showAuthModal && (
        <AuthModal
          onAuth={handleAuth}
          onSkip={() => {
            if (isDesktopRuntime()) setShowAuthModal(false);
          }}
        />
      )}

      {showPasswordChange && auth && (
        <PasswordChangeModal
          auth={auth}
          requiredHint={passwordChangeRequiredHint}
          onAuth={handleAuth}
          onClose={() => setShowPasswordChange(false)}
        />
      )}

      {/* History panel */}
      {showHistory && auth && (
        <HistoryPanel
          token={auth.token}
          llmConfig={llm}
          onLoad={handleHistoryLoad}
          onResume={handleHistoryResume}
          onClose={() => setShowHistory(false)}
        />
      )}

      {/* Settings modal */}
      {showSettings && auth && (
        <SettingsModal
          profiles={llmProfiles}
          activeIndex={llmActiveIdx}
          auth={auth}
          onSave={(profiles, idx) => {
            setLlmProfiles(profiles);
            setLlmActiveIdx(idx);
            if (profiles.length > 0) {
              const active = profiles[Math.min(idx, profiles.length - 1)];
              const cfg: LLMConfig = {
                api_url: active.api_url, model_name: active.model_name, api_key: active.api_key,
                embedding_url: active.embedding_url, embedding_model: active.embedding_model,
                embedding_api_key: active.embedding_api_key,
              };
              setLlm(cfg); saveLlm(cfg);
            }
          }}
          onClose={() => setShowSettings(false)}
          onLogout={() => { handleLogout(); setShowSettings(false); }}
          onChangePassword={() => {
            setShowSettings(false);
            setPasswordChangeRequiredHint(false);
            setShowPasswordChange(true);
          }}
          nodeLanguage={nodeLanguage}
          onNodeLanguageChange={handleNodeLanguageChange}
          onShowApiGuide={setGuideLocation}
        />
      )}

      {/* Floating background job badge */}
      <FloatingBadge
        hidden={view === "result" && experience === "studio" && studioNodeSelected}
        onViewResult={(jid) => {
          const job = jobs[jid];
          if (job?.result) {
            setStudioNodeSelected(false);
            workspaceStates.current[workspaceMode] = captureSnapshot();
            activeWorkspaceModeRef.current = "generate";
            setWorkspaceMode("generate");
            const graph = dedupeGraph({ ...(job.result as GraphResult), source_mode: "generate" });
            setResult(graph);
            setFilename(job.filename);
            setJobId(jid);
            setSourceMarkdown(job.sourceMarkdown || undefined);
            setView("result");
            workspaceStates.current.generate = {
              view: "result", jobId: jid, result: graph, sourceMarkdown: job.sourceMarkdown,
              errorInfo: null, filename: job.filename,
            };
            saveSession("generate", { result: graph, filename: job.filename, jobId: jid, sourceMarkdown: job.sourceMarkdown });
          }
        }}
      />

      <div style={{ display: workspaceMode === "generate" && view === "upload" ? "contents" : "none" }}>
        <UploadScreen
          key={`generate-upload-${generateUploadEpoch}`}
          onSubmit={handleSubmit}
          llm={llm}
          onLlmChange={updateLlm}
          auth={auth}
          onShowAuth={() => setShowAuthModal(true)}
          onShowHistory={() => setShowHistory(true)}
          onShowSettings={() => setShowSettings(true)}
          onShowApiGuide={setGuideLocation}
          configReady={configReady}
          submitButtonRef={uploadSubmitButtonRef}
          activeFilename={activeUploadFilename}
        />
      </div>
      <div style={{ display: workspaceMode === "import" && view === "upload" ? "contents" : "none" }}>
        <GraphImportScreen
          key={`import-upload-${importUploadEpoch}`}
          onSubmit={handleGraphImportSubmit}
          auth={auth}
          onShowAuth={() => setShowAuthModal(true)}
          onShowHistory={() => setShowHistory(true)}
          onShowSettings={() => setShowSettings(true)}
          onShowApiGuide={setGuideLocation}
        />
      </div>
      {view === "result" && result && experience === "studio" && (
        <StudioWrapper
          workspaceMode={workspaceMode}
          result={result}
          filename={filename}
          jobId={jobId!}
          sourceMarkdown={sourceMarkdown}
          nodeLanguage={nodeLanguage}
            token={auth?.token}
            llmConfig={llm}
            onLoadHistory={auth ? handleHistoryLoad : undefined}
            onResumeHistory={handleHistoryResume}
            onNodeSelectionChange={setStudioNodeSelected}
          onReset={resetView}
          onShowApiGuide={() => setGuideLocation("intro")}
        />
      )}
      {view === "result" && result && experience === "classic" && (
        <ResultScreen
          workspaceMode={workspaceMode}
          result={result}
          filename={filename}
          jobId={jobId!}
          sourceMarkdown={sourceMarkdown}
          onReset={resetView}
          auth={auth}
          llmConfig={llm}
          onShowHistory={() => setShowHistory(true)}
          onShowAuth={() => setShowAuthModal(true)}
          onShowSettings={() => setShowSettings(true)}
          onShowApiGuide={() => setGuideLocation("intro")}
          onShowStudio={() => switchExperience("studio")}
          nodeLanguage={nodeLanguage}
          onNodeLanguageChange={handleNodeLanguageChange}
          onSourceMarkdownRecover={(md) => {
            const targetMode = workspaceMode;
            workspaceStates.current[targetMode] = { ...workspaceStates.current[targetMode], sourceMarkdown: md };
            if (activeWorkspaceModeRef.current === targetMode) setSourceMarkdown(md);
            if (jobId) saveSession(targetMode, { result: result!, filename, jobId, sourceMarkdown: md });
          }}
        />
      )}
      {view === "error" && errorInfo && (
        <ErrorScreen
          errorTitle={errorInfo.title}
          error={errorInfo.msg}
          errorDetail={errorInfo.detail}
          partial={errorInfo.partial}
          filename={filename}
          jobId={jobId ?? ""}
          authToken={auth?.token}
          stagesDone={errorInfo.stages}
          totalStages={errorInfo.totalStages ?? STAGE_DEFS.length}
          onReset={resetView}
          onViewPartial={() => {
            if (errorInfo.partial) { setResult(dedupeGraph(errorInfo.partial)); setView("result"); }
          }}
          onRetry={async () => {
            const resumableJobId = jobId ?? latestJobId;
            if (!resumableJobId) return false;
            const resumed = await resumeJob(resumableJobId);
            if (resumed) {
              setErrorInfo(null);
              setView("upload");
            }
            return resumed;
          }}
          onShowApiGuide={() => setGuideLocation("intro")}
        />
      )}
    </>
  );
}
