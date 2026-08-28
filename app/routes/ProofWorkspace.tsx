import { type ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, BookOpen, CheckCircle2, Download, Eye, FileUp, History, Lightbulb, ListChecks, Loader2, Save, Sigma, Trash2 } from "lucide-react";
import { apiUrl } from "~/api";
import {
  cancelOcrInstall,
  cancelOcrJob,
  classifyOcrRuntime,
  getOcrRuntime,
  getOcrResult,
  installOcrRuntime,
  ocrRuntimeErrorSummary,
  pollOcrJob,
  startOcrJob,
  uploadOcrFile,
  type OcrRuntimeStatus,
  type OcrUploadInfo,
} from "~/ocr";
import { OcrRuntimeErrorPanel } from "~/components/OcrRuntimeErrorPanel";
import { MathText } from "./math";
import type { LatexMacros } from "./math";
import { FormulaComposer } from "./FormulaComposer";
import { FormulaHoverPreview } from "./FormulaHoverPreview";
import { FloatingWorkspaceWindow } from "./FloatingWorkspaceWindow";
import { MatrixFlowText } from "./matrix-flow/MatrixFlowViewer";
import type { MatrixFlowAudience, MatrixFlowLayoutMode } from "./matrix-flow/types";
import {
  buildFormulaTextSegments,
  commitFormula,
  findFormulaAt,
  findFormulaRanges,
  type FormulaMatch,
  type FormulaPresentation,
} from "./formula-input";
import { pointInClientRects } from "./floating-window";
import type { GraphNode, LLMConfig } from "./home";
import { deleteStudentContext, loadStudentContext, loadStudentContextExport, updateStudentContextEvidence } from "./education";
import type { LearningEvidenceItem, StudentContextPreview } from "./education";
import "./proofworkspace.css";

export type ProofAssistAction = "hint" | "check" | "summarize";
type ProofVersionReason = "manual" | "import_text" | "import_ocr" | "assist_hint" | "assist_check" | "assist_summarize";

export interface ProofVersion {
  id: string;
  userProof: string;
  reason: ProofVersionReason;
  createdAt: string;
}

export interface ProofAssistMessage {
  id: string;
  action: ProofAssistAction;
  requestProof: string;
  response: string;
  createdAt: string;
}

export interface ProofImportRecord {
  id: string;
  filename: string;
  source: "text_file" | "ocr_file";
  importedText: string;
  createdAt: string;
}

export interface ProofWorkspaceState {
  nodeId: number;
  userProof: string;
  versions: ProofVersion[];
  aiMessages: ProofAssistMessage[];
  imports?: ProofImportRecord[];
  updatedAt: string;
}

interface ProofWorkspaceProps {
  graphId: string;
  node: GraphNode;
  token?: string;
  llmConfig?: LLMConfig;
  macros?: LatexMacros;
  matrixFlowAudience?: MatrixFlowAudience;
  matrixFlowLayoutMode?: MatrixFlowLayoutMode;
  educationContext?: {
    assignmentId: string;
    classId: string;
    onContextChange?: () => void;
  };
  answerMode?: {
    key: string;
    value: string;
    title?: string;
    subtitle?: string;
    placeholder?: string;
    onChange: (value: string) => void;
    onSave?: (value: string) => void | Promise<void>;
  };
}

interface ImportPreview {
  filename: string;
  source: ProofImportRecord["source"];
  importedText: string;
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

function emptyState(nodeId: number): ProofWorkspaceState {
  return { nodeId, userProof: "", versions: [], aiMessages: [], imports: [], updatedAt: new Date().toISOString() };
}

function localKey(graphId: string, nodeId: number) {
  return `proof_workspace:${graphId}:${nodeId}`;
}

function loadLocalState(graphId: string, nodeId: number) {
  try {
    const raw = localStorage.getItem(localKey(graphId, nodeId));
    return raw ? normalizeState(JSON.parse(raw), nodeId) : emptyState(nodeId);
  } catch {
    return emptyState(nodeId);
  }
}

function normalizeState(value: unknown, nodeId: number): ProofWorkspaceState {
  if (!value || typeof value !== "object") return emptyState(nodeId);
  const raw = value as Partial<ProofWorkspaceState>;
  return {
    nodeId,
    userProof: typeof raw.userProof === "string" ? raw.userProof : "",
    versions: Array.isArray(raw.versions) ? raw.versions : [],
    aiMessages: Array.isArray(raw.aiMessages) ? raw.aiMessages : [],
    imports: Array.isArray(raw.imports) ? raw.imports : [],
    updatedAt: typeof raw.updatedAt === "string" ? raw.updatedAt : new Date().toISOString(),
  };
}

function saveLocalState(graphId: string, nodeId: number, state: ProofWorkspaceState) {
  localStorage.setItem(localKey(graphId, nodeId), JSON.stringify(state));
}

function isAuthFailure(response: Response) {
  return response.status === 401 || response.status === 403;
}

function makeId(prefix: string) {
  const random = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${random}`;
}

function completeLlmConfig(config?: LLMConfig) {
  if (!config?.api_url || !config.model_name || !config.api_key) return undefined;
  return config;
}

const TEXT_IMPORT_EXTS = new Set([".tex", ".md", ".txt"]);
const OCR_IMPORT_EXTS = new Set([".pdf", ".png", ".jpg", ".jpeg", ".webp"]);

function fileExt(filename: string) {
  const dot = filename.lastIndexOf(".");
  return dot >= 0 ? filename.slice(dot).toLowerCase() : "";
}

function appendDraft(current: string, imported: string) {
  if (!current.trim()) return imported;
  return `${current.trimEnd()}\n\n${imported.trimStart()}`;
}

function reasonLabel(reason: ProofVersionReason) {
  if (reason === "import_text") return "导入文本";
  if (reason === "import_ocr") return "导入手稿";
  if (reason === "manual") return "手动保存";
  if (reason === "assist_hint") return "提示前保存";
  if (reason === "assist_check") return "检查前保存";
  return "总结前保存";
}

function actionLabel(action: ProofAssistAction) {
  if (action === "hint") return "提示";
  if (action === "check") return "检查";
  return "总结";
}

function ContextEvidence({ title, item }: { title: string; item: LearningEvidenceItem }) {
  return (
    <div className="proof-ws-context-section">
      <strong>{title}</strong>
      <div className="proof-ws-context-item"><MathText text={item.claim} /></div>
    </div>
  );
}

function ContextEvidenceList({ title, items, tone = "", resolved = false, onUpdate, busyId }: {
  title: string;
  items: LearningEvidenceItem[];
  tone?: "" | "warning" | "risk";
  resolved?: boolean;
  onUpdate?: (item: LearningEvidenceItem, status: "open" | "resolved" | "retracted") => void;
  busyId?: string | null;
}) {
  return (
    <div className={`proof-ws-context-section ${tone}`}>
      <strong>{title}</strong>
      {items.map(item => (
        <div className="proof-ws-context-item" key={item.id}>
          <div><MathText text={item.claim} /></div>
          {item.relationPath && (
            <small>
              {item.relationRole === "prerequisite_risk" ? "可能需要回顾的前置知识" : item.relationRole === "successor_risk" ? "后续知识提醒" : "相关知识"}
              {item.relationPath.edgeLabel ? ` · ${item.relationPath.edgeLabel}` : ""}
              {item.relationPath.edgeDescription ? `：${item.relationPath.edgeDescription}` : ""}
            </small>
          )}
          {onUpdate && (
            <div className="proof-ws-context-actions">
              {resolved ? (
                <button disabled={busyId === item.id} onClick={() => onUpdate(item, "open")}>重新打开</button>
              ) : (
                <>
                  <button disabled={busyId === item.id} onClick={() => onUpdate(item, "retracted")}>不准确</button>
                  <button disabled={busyId === item.id} onClick={() => onUpdate(item, "resolved")}>已解决</button>
                </>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export function ProofWorkspace({ graphId, node, token, llmConfig, macros, matrixFlowAudience = "author", matrixFlowLayoutMode = "vertical", educationContext, answerMode }: ProofWorkspaceProps) {
  const [state, setState] = useState<ProofWorkspaceState>(() => emptyState(node.id));
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [assistLoading, setAssistLoading] = useState<ProofAssistAction | null>(null);
  const [importing, setImporting] = useState(false);
  const [ocrPhase, setOcrPhase] = useState("");
  const [ocrJobId, setOcrJobId] = useState<string | null>(null);
  const [ocrInstallId, setOcrInstallId] = useState<string | null>(null);
  const [ocrRuntime, setOcrRuntime] = useState<OcrRuntimeStatus | null>(null);
  const [ocrUploadPercent, setOcrUploadPercent] = useState<number | null>(null);
  const [ocrSourceFile, setOcrSourceFile] = useState<File | null>(null);
  const [ocrUpload, setOcrUpload] = useState<OcrUploadInfo | null>(null);
  const ocrAbortRef = useRef<AbortController | null>(null);
  const [importPreview, setImportPreview] = useState<ImportPreview | null>(null);
  const [error, setError] = useState("");
  const [showTextbook, setShowTextbook] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [contextPreview, setContextPreview] = useState<StudentContextPreview | null>(null);
  const [contextLoading, setContextLoading] = useState(false);
  const [contextError, setContextError] = useState("");
  const [feedbackBusy, setFeedbackBusy] = useState<string | null>(null);
  const [contextArchiveBusy, setContextArchiveBusy] = useState<"export" | "delete" | null>(null);
  const [formulaOpen, setFormulaOpen] = useState(false);
  const [formulaTarget, setFormulaTarget] = useState<{
    start: number;
    end: number;
    initialValue: string;
    initialPresentation: FormulaPresentation;
    editing: boolean;
  } | null>(null);
  const [showPreview, setShowPreview] = useState(false);
  const [activeFloatingWindow, setActiveFloatingWindow] = useState<"formula" | "preview" | null>(null);
  const [hoveredFormula, setHoveredFormula] = useState<{ formula: FormulaMatch; clientX: number; clientY: number } | null>(null);
  const editorRef = useRef<HTMLTextAreaElement | null>(null);
  const editorMirrorRef = useRef<HTMLDivElement | null>(null);
  const hoverFrameRef = useRef(0);
  const hoverPointerRef = useRef({ x: 0, y: 0 });
  const formulaAnchorRef = useRef<HTMLButtonElement | null>(null);
  const previewAnchorRef = useRef<HTMLButtonElement | null>(null);
  const formulaRanges = useMemo(() => findFormulaRanges(state.userProof), [state.userProof]);
  const formulaTextSegments = useMemo(() => buildFormulaTextSegments(state.userProof), [state.userProof]);
  const textbookProof = asText(node.proof);
  const educationAssignmentId = educationContext?.assignmentId;
  const educationClassId = educationContext?.classId;
  const onEducationContextChange = educationContext?.onContextChange;

  useEffect(() => {
    let cancelled = false;
    setShowHistory(false);
    setFormulaOpen(false);
    setFormulaTarget(null);
    setShowPreview(false);
    setActiveFloatingWindow(null);
    setHoveredFormula(null);
    const load = async () => {
      setLoaded(false);
      setError("");
      if (answerMode) {
        if (!cancelled) {
          setState({ ...emptyState(node.id), userProof: answerMode.value });
          setLoaded(true);
        }
        return;
      }
      if (!token) {
        if (!cancelled) {
          setState(loadLocalState(graphId, node.id));
          setLoaded(true);
        }
        return;
      }
      try {
        const res = await fetch(apiUrl(`/api/v2/proof-workspaces/${encodeURIComponent(graphId)}`), {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (isAuthFailure(res)) {
          if (!cancelled) setState(loadLocalState(graphId, node.id));
          return;
        }
        if (!res.ok) throw new Error("证明工作区加载失败");
        const data = await res.json();
        const found = (data.workspaces || []).find((item: ProofWorkspaceState) => item.nodeId === node.id);
        if (!cancelled) setState(found ? normalizeState(found, node.id) : emptyState(node.id));
      } catch (e) {
        if (!cancelled) {
          setState(emptyState(node.id));
          setError(e instanceof Error ? e.message : "证明工作区加载失败");
        }
      } finally {
        if (!cancelled) setLoaded(true);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [answerMode?.key, graphId, node.id, token]);

  const refreshStudentContext = useCallback(async () => {
    if (!educationAssignmentId || !token || answerMode) {
      setContextPreview(null);
      return;
    }
    setContextLoading(true);
    setContextError("");
    try {
      const data = await loadStudentContext(token, educationAssignmentId, node.id);
      setContextPreview(data.contextPreview || null);
    } catch {
      setContextError("学习情况加载失败");
    } finally {
      setContextLoading(false);
    }
  }, [answerMode, educationAssignmentId, node.id, token]);

  useEffect(() => {
    void refreshStudentContext();
  }, [refreshStudentContext]);

  const persist = useCallback(async (next: ProofWorkspaceState) => {
    const updated = { ...next, updatedAt: new Date().toISOString() };
    setState(updated);
    if (answerMode) {
      answerMode.onChange(updated.userProof);
      return updated;
    }
    if (!token) {
      saveLocalState(graphId, node.id, updated);
      return updated;
    }
    const res = await fetch(apiUrl(`/api/v2/proof-workspaces/${encodeURIComponent(graphId)}/${node.id}`), {
      method: "PUT",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(updated),
    });
    if (isAuthFailure(res)) {
      saveLocalState(graphId, node.id, updated);
      return updated;
    }
    if (!res.ok) throw new Error("保存失败");
    const data = await res.json();
    return data.workspace as ProofWorkspaceState;
  }, [answerMode, graphId, node.id, token]);

  const addVersion = useCallback((reason: ProofVersionReason, base = state) => {
    const now = new Date().toISOString();
    const last = base.versions[base.versions.length - 1];
    if (!base.userProof.trim() || last?.userProof === base.userProof) return base;
    return {
      ...base,
      versions: [
        ...base.versions,
        { id: makeId("version"), userProof: base.userProof, reason, createdAt: now },
      ],
    };
  }, [state]);

  const handleSave = async () => {
    setSaving(true);
    setError("");
    try {
      if (answerMode) {
        answerMode.onChange(state.userProof);
        await answerMode.onSave?.(state.userProof);
        return;
      }
      await persist(addVersion("manual"));
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const runUploadedOcr = async (file: File, uploaded: OcrUploadInfo, controller: AbortController) => {
    let runtime = await getOcrRuntime(controller.signal);
    setOcrRuntime(runtime);
    const disposition = classifyOcrRuntime(runtime);
    if (disposition === "unavailable" || disposition === "fatal_error") {
      throw new Error(ocrRuntimeErrorSummary(runtime));
    }
    if (runtime.state !== "ready") {
      setOcrPhase("安装 OCR 组件");
      runtime = await installOcrRuntime(controller.signal, (status) => {
        setOcrInstallId(status.install_id || null);
        setOcrRuntime(status);
      });
      setOcrRuntime(runtime);
    }
    setOcrPhase("启动 OCR");
    const job = await startOcrJob(uploaded.upload_id, controller.signal);
    setOcrJobId(job.ocr_job_id);
    await pollOcrJob(job.ocr_job_id, (status) => setOcrPhase(status.phase), controller.signal);
    setOcrPhase("整理结果");
    const data = await getOcrResult(job.ocr_job_id, controller.signal);
    setImportPreview({
      filename: data.filename || file.name,
      source: "ocr_file",
      importedText: data.importedText || "",
    });
  };

  const handleImportFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setFormulaOpen(false);
    setFormulaTarget(null);
    setActiveFloatingWindow((current) => current === "formula" ? (showPreview ? "preview" : null) : current);
    setImporting(true);
    const controller = new AbortController();
    ocrAbortRef.current = controller;
    setError("");
    setImportPreview(null);
    setOcrRuntime(null);
    setOcrSourceFile(null);
    setOcrUpload(null);
    try {
      const ext = fileExt(file.name);
      if (TEXT_IMPORT_EXTS.has(ext)) {
        setImportPreview({ filename: file.name, source: "text_file", importedText: await file.text() });
        return;
      }
      if (!OCR_IMPORT_EXTS.has(ext)) {
        throw new Error("仅支持 .tex/.md/.txt/.pdf/.png/.jpg/.jpeg/.webp 文件");
      }
      const maxBytes = ext === ".pdf" ? 100 * 1024 * 1024 : 20 * 1024 * 1024;
      if (file.size > maxBytes) {
        throw new Error(ext === ".pdf" ? "PDF 文件不能超过 100MB" : "图片文件不能超过 20MB");
      }
      setOcrPhase("上传文件");
      const uploaded = await uploadOcrFile(file, setOcrUploadPercent, controller.signal);
      setOcrSourceFile(file);
      setOcrUpload(uploaded);
      await runUploadedOcr(file, uploaded, controller);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导入失败");
    } finally {
      setImporting(false);
      setOcrPhase("");
      setOcrJobId(null);
      setOcrInstallId(null);
      setOcrUploadPercent(null);
      if (ocrAbortRef.current === controller) ocrAbortRef.current = null;
    }
  };

  const retryOcrImport = () => {
    if (!ocrSourceFile || !ocrUpload || importing) return;
    setImporting(true);
    setError("");
    const controller = new AbortController();
    ocrAbortRef.current = controller;
    void runUploadedOcr(ocrSourceFile, ocrUpload, controller)
      .catch((e) => setError(e instanceof Error ? e.message : "导入失败"))
      .finally(() => {
        setImporting(false);
        setOcrPhase("");
        setOcrJobId(null);
        setOcrInstallId(null);
        setOcrUploadPercent(null);
        if (ocrAbortRef.current === controller) ocrAbortRef.current = null;
      });
  };

  const cancelImport = () => {
    if (ocrJobId) void cancelOcrJob(ocrJobId).catch(() => undefined);
    if (ocrInstallId) void cancelOcrInstall(ocrInstallId).catch(() => undefined);
    ocrAbortRef.current?.abort();
    ocrAbortRef.current = null;
    setImporting(false);
    setOcrPhase("");
    setError("OCR 已取消，可重新选择文件重试。");
  };

  const applyImportPreview = async (mode: "append" | "replace") => {
    if (!importPreview?.importedText.trim()) return;
    setSaving(true);
    setError("");
    try {
      const importedAt = new Date().toISOString();
      const record: ProofImportRecord = {
        id: makeId("import"),
        filename: importPreview.filename,
        source: importPreview.source,
        importedText: importPreview.importedText,
        createdAt: importedAt,
      };
      const userProof = mode === "append"
        ? appendDraft(state.userProof, importPreview.importedText)
        : importPreview.importedText;
      const reason: ProofVersionReason = importPreview.source === "ocr_file" ? "import_ocr" : "import_text";
      const next: ProofWorkspaceState = {
        ...state,
        userProof,
        imports: [...(state.imports || []), record],
        versions: [
          ...state.versions,
          { id: makeId("version"), userProof, reason, createdAt: importedAt },
        ],
      };
      await persist(next);
      setImportPreview(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导入保存失败");
    } finally {
      setSaving(false);
    }
  };

  const handleAssist = async (action: ProofAssistAction) => {
    setAssistLoading(action);
    setError("");
    try {
      const snapshotted = addVersion(`assist_${action}` as ProofVersionReason);
      const assistPath = educationAssignmentId
        ? `/api/v2/edu/assignments/${encodeURIComponent(educationAssignmentId)}/proof-assist`
        : "/api/v2/proof-assist";
      const res = await fetch(apiUrl(assistPath), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(educationAssignmentId ? {
          action,
          nodeId: node.id,
          userProof: snapshotted.userProof,
          clientInteractionId: makeId("proof"),
          contextVersion: contextPreview?.contextVersion ?? 0,
        } : {
          action,
          node,
          userProof: snapshotted.userProof,
          llm_config: completeLlmConfig(llmConfig),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.message || data.error || "AI 辅助失败");
      }
      const next: ProofWorkspaceState = {
        ...snapshotted,
        aiMessages: [
          ...snapshotted.aiMessages,
          {
            id: data.interactionId || makeId("assist"),
            action,
            requestProof: snapshotted.userProof,
            response: data.response || "",
            createdAt: new Date().toISOString(),
          },
        ],
      };
      await persist(next);
      if (educationAssignmentId) {
        if (data.contextPreview) setContextPreview(data.contextPreview as StudentContextPreview);
        onEducationContextChange?.();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "AI 辅助失败");
    } finally {
      setAssistLoading(null);
    }
  };

  const openFormulaComposer = () => {
    const editor = editorRef.current;
    const start = editor?.selectionStart ?? state.userProof.length;
    const end = editor?.selectionEnd ?? start;
    const existing = findFormulaAt(state.userProof, start, end);
    setFormulaTarget({
      start,
      end,
      initialValue: existing?.inner || "",
      initialPresentation: existing?.presentation || "inline",
      editing: Boolean(existing),
    });
    setFormulaOpen(true);
    setActiveFloatingWindow("formula");
    setHoveredFormula(null);
    setError("");
  };

  const closeFormulaComposer = () => {
    const target = formulaTarget;
    setFormulaOpen(false);
    setFormulaTarget(null);
    setActiveFloatingWindow((current) => current === "formula" ? (showPreview ? "preview" : null) : current);
    requestAnimationFrame(() => {
      const editor = editorRef.current;
      if (!editor || !target) return;
      editor.focus();
      editor.setSelectionRange(target.start, target.end);
    });
  };

  const commitFormulaFromComposer = (latex: string, presentation: FormulaPresentation) => {
    if (!formulaTarget) return;
    const result = commitFormula(
      state.userProof,
      formulaTarget.start,
      formulaTarget.end,
      latex,
      presentation,
    );
    setState((previous) => ({ ...previous, userProof: result.text }));
    answerMode?.onChange(result.text);
    setFormulaOpen(false);
    setFormulaTarget(null);
    setActiveFloatingWindow((current) => current === "formula" ? (showPreview ? "preview" : null) : current);
    requestAnimationFrame(() => {
      const editor = editorRef.current;
      if (!editor) return;
      editor.focus();
      editor.setSelectionRange(result.selectionStart, result.selectionEnd);
    });
  };

  const closeFullPreview = useCallback(() => {
    setShowPreview(false);
    setActiveFloatingWindow((current) => current === "preview" ? (formulaOpen ? "formula" : null) : current);
    requestAnimationFrame(() => previewAnchorRef.current?.focus());
  }, [formulaOpen]);

  const toggleFullPreview = () => {
    if (showPreview) {
      closeFullPreview();
      return;
    }
    setShowPreview(true);
    setActiveFloatingWindow("preview");
  };

  const hideFormulaTooltip = useCallback(() => {
    if (hoverFrameRef.current) {
      cancelAnimationFrame(hoverFrameRef.current);
      hoverFrameRef.current = 0;
    }
    setHoveredFormula(null);
  }, []);

  const handleEditorMouseMove = (event: React.MouseEvent<HTMLTextAreaElement>) => {
    if (typeof window === "undefined" || !window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;
    hoverPointerRef.current = { x: event.clientX, y: event.clientY };
    if (hoverFrameRef.current) return;
    hoverFrameRef.current = requestAnimationFrame(() => {
      hoverFrameRef.current = 0;
      const mirror = editorMirrorRef.current;
      if (!mirror) return;
      const { x, y } = hoverPointerRef.current;
      const nodes = mirror.querySelectorAll<HTMLElement>("[data-formula-start]");
      for (const node of nodes) {
        const start = Number(node.dataset.formulaStart);
        const formula = formulaRanges.find((item) => item.start === start);
        if (!formula || !formula.inner.trim()) continue;
        if (pointInClientRects(Array.from(node.getClientRects()), x, y)) {
          setHoveredFormula({ formula, clientX: x, clientY: y });
          return;
        }
      }
      setHoveredFormula(null);
    });
  };

  const handleEditorScroll = (event: React.UIEvent<HTMLTextAreaElement>) => {
    const mirror = editorMirrorRef.current;
    if (mirror) {
      mirror.scrollLeft = event.currentTarget.scrollLeft;
      mirror.scrollTop = event.currentTarget.scrollTop;
    }
    hideFormulaTooltip();
  };

  useEffect(() => {
    setHoveredFormula(null);
  }, [state.userProof]);

  useEffect(() => () => {
    if (hoverFrameRef.current) cancelAnimationFrame(hoverFrameRef.current);
  }, []);

  const updateEvidence = async (item: LearningEvidenceItem, status: "open" | "resolved" | "retracted") => {
    if (!token || feedbackBusy) return;
    setFeedbackBusy(item.id);
    setContextError("");
    try {
      await updateStudentContextEvidence(token, item.id, status);
      await refreshStudentContext();
      onEducationContextChange?.();
    } catch {
      setContextError("学习记录更新失败");
    } finally {
      setFeedbackBusy(null);
    }
  };

  const exportCourseContext = async () => {
    if (!token || !educationClassId || contextArchiveBusy) return;
    setContextArchiveBusy("export");
    setContextError("");
    try {
      const payload = await loadStudentContextExport(token, educationClassId);
      const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `mathweaver-student-context-${educationClassId}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch {
      setContextError("学习记录导出失败");
    } finally {
      setContextArchiveBusy(null);
    }
  };

  const clearCourseContext = async () => {
    if (!token || !educationClassId || contextArchiveBusy) return;
    if (!window.confirm("确认清除本课程的学习记录吗？提问记录、学习判断和总结都会删除，当前证明草稿不受影响。建议先导出备份。")) return;
    setContextArchiveBusy("delete");
    setContextError("");
    try {
      await deleteStudentContext(token, educationClassId);
      setContextPreview(null);
      onEducationContextChange?.();
    } catch {
      setContextError("学习记录清除失败");
    } finally {
      setContextArchiveBusy(null);
    }
  };

  const latestVersion = useMemo(() => state.versions[state.versions.length - 1], [state.versions]);

  return (
    <div className="proof-ws">
      {!answerMode && textbookProof && (
        <div className="proof-ws-card">
          <button className="proof-ws-rowbtn" onClick={() => setShowTextbook((v) => !v)}>
            <BookOpen size={14} />
            <span>教材证明</span>
            <b>{showTextbook ? "收起" : "查看"}</b>
          </button>
          {showTextbook && (
            <div className="proof-ws-textbook">
              {textbookProof && <MatrixFlowText text={textbookProof} flows={node.matrix_flows} field="proof" audience={matrixFlowAudience} macros={macros} className="proof-ws-textbook-content" layoutMode={matrixFlowLayoutMode} />}
            </div>
          )}
        </div>
      )}

      <div className="proof-ws-head">
        <div>
          <div className="proof-ws-title">{answerMode?.title || "我的草稿"}</div>
          <div className="proof-ws-subtitle">
            {answerMode?.subtitle || (loaded ? (latestVersion ? `上次保存：${new Date(latestVersion.createdAt).toLocaleString("zh-CN")}` : "尚未保存版本") : "正在加载...")}
          </div>
        </div>
        <button className="proof-ws-save" onClick={handleSave} disabled={saving || !loaded}>
          {saving ? <Loader2 size={14} className="proof-ws-spin" /> : <Save size={14} />}
          {answerMode ? "保存答案" : "保存"}
        </button>
      </div>

      <div className="proof-ws-import">
        <div className="proof-ws-import-actions">
          <button
            ref={formulaAnchorRef}
            type="button"
            className={`proof-ws-import-btn proof-ws-formula-toggle ${formulaOpen ? "active" : ""}`}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => {
              if (formulaOpen) {
                closeFormulaComposer();
              } else {
                openFormulaComposer();
              }
            }}
            disabled={importing || !loaded}
          >
            <Sigma size={14} />
            {formulaOpen ? "关闭公式" : "公式输入"}
          </button>
          <label className={`proof-ws-import-btn ${importing || !loaded ? "disabled" : ""}`}>
            {importing ? <Loader2 size={14} className="proof-ws-spin" /> : <FileUp size={14} />}
            {importing ? "处理中" : "导入草稿"}
            <input
              type="file"
              accept=".tex,.md,.txt,.pdf,.png,.jpg,.jpeg,.webp,text/markdown,text/plain,text/x-tex,application/pdf,image/png,image/jpeg,image/webp"
              onChange={handleImportFile}
              disabled={importing || !loaded}
            />
          </label>
          <button
            ref={previewAnchorRef}
            type="button"
            className={`proof-ws-import-btn proof-ws-preview-toggle ${showPreview ? "active" : ""}`}
            onClick={toggleFullPreview}
            aria-expanded={showPreview}
            disabled={!loaded}
          >
            <Eye size={14} />
            {showPreview ? "关闭预览" : "排版预览"}
          </button>
        </div>
        <span>{importing ? `OCR：${ocrPhase || "处理中"}` : "矩阵、行列式建议用公式输入；PDF/图片仍可 OCR。"}</span>
        {importing && <button type="button" className="proof-ws-import-btn proof-ws-import-cancel" onClick={cancelImport}>取消</button>}
      </div>

      {importing && (
        <div className="proof-ws-import-progress" role="status" aria-live="polite" aria-label="正在处理导入文件">
          <div />
          <span>{ocrUploadPercent !== null ? `上传 ${ocrUploadPercent}%` : `OCR ${ocrRuntime?.state || ocrPhase || "处理中"}`}</span>
          {ocrRuntime?.state === "downloading" && <span>组件下载 {ocrRuntime.download_percent ?? 0}%</span>}
        </div>
      )}

      {ocrRuntime && (
        <OcrRuntimeErrorPanel
          status={ocrRuntime}
          onRetry={retryOcrImport}
          retrying={importing && ocrPhase === "安装 OCR 组件"}
          compact
        />
      )}

      {importPreview && (
        <div className="proof-ws-import-preview">
          <div className="proof-ws-import-preview-head">
            <span>{importPreview.source === "ocr_file" ? "手稿识别结果" : "文本导入结果"} · {importPreview.filename}</span>
            <button onClick={() => setImportPreview(null)}>清除</button>
          </div>
          <pre>{importPreview.importedText}</pre>
          <div className="proof-ws-import-preview-actions">
            <button className="primary" onClick={() => applyImportPreview("append")} disabled={saving || !loaded}>
              插入到草稿
            </button>
            <button onClick={() => applyImportPreview("replace")} disabled={saving || !loaded}>
              替换草稿
            </button>
          </div>
        </div>
      )}

      {formulaOpen && formulaTarget && (
        <FormulaComposer
          key={`${formulaTarget.start}:${formulaTarget.end}:${formulaTarget.initialValue}`}
          anchorElement={formulaAnchorRef.current}
          initialValue={formulaTarget.initialValue}
          initialPresentation={formulaTarget.initialPresentation}
          editing={formulaTarget.editing}
          macros={macros}
          active={activeFloatingWindow === "formula"}
          onActivate={() => setActiveFloatingWindow("formula")}
          onCommit={commitFormulaFromComposer}
          onCancel={closeFormulaComposer}
        />
      )}

      <div className="proof-ws-editor-shell">
        <div ref={editorMirrorRef} className="proof-ws-editor proof-ws-editor-mirror" aria-hidden="true">
          {formulaTextSegments.map((segment) => segment.formula ? (
            <span key={`${segment.start}-${segment.end}`} data-formula-start={segment.start}>{segment.text}</span>
          ) : segment.text)}
        </div>
        <textarea
          ref={editorRef}
          className="proof-ws-editor"
          value={state.userProof}
          onChange={(event) => {
            hideFormulaTooltip();
            const value = event.target.value;
            setState((prev) => ({ ...prev, userProof: value }));
            answerMode?.onChange(value);
          }}
          onMouseMove={handleEditorMouseMove}
          onMouseLeave={hideFormulaTooltip}
          onScroll={handleEditorScroll}
          onBlur={hideFormulaTooltip}
          onCompositionStart={hideFormulaTooltip}
          placeholder={answerMode?.placeholder || "写下你的证明思路或疑问"}
          disabled={!loaded}
        />
      </div>

      {hoveredFormula && (
        <FormulaHoverPreview
          anchorElement={editorRef.current}
          formula={hoveredFormula.formula}
          clientX={hoveredFormula.clientX}
          clientY={hoveredFormula.clientY}
          macros={macros}
        />
      )}

      {showPreview && (
        <FloatingWorkspaceWindow
          anchorElement={previewAnchorRef.current}
          title="排版预览"
          subtitle="按最终显示样式检查整段文字与公式，拖动此处可移动浮窗"
          ariaLabel="草稿排版预览"
          className="proof-ws-full-preview"
          preferredWidth={520}
          maxHeight={600}
          splitGraphHeight
          placement="bottom-right"
          active={activeFloatingWindow === "preview"}
          onActivate={() => setActiveFloatingWindow("preview")}
          onClose={closeFullPreview}
        >
          <div className={`proof-ws-full-preview-body ${state.userProof.trim() ? "" : "empty"}`}>
            {state.userProof.trim() ? (
              <MathText className="proof-ws-full-preview-content" text={state.userProof} macros={macros} />
            ) : (
              <div className="proof-ws-full-preview-empty">
                <Eye size={24} aria-hidden="true" />
                <strong>暂无可预览内容</strong>
                <span>输入文字或公式后，这里会实时显示排版效果。</span>
              </div>
            )}
          </div>
        </FloatingWorkspaceWindow>
      )}

      {educationAssignmentId && !answerMode && (
        <div className="proof-ws-context">
          <div className="proof-ws-context-head">
            <span><BookOpen size={15} />学习情况</span>
            <small>{contextLoading ? "正在更新…" : contextPreview ? "已更新" : "暂无记录"}</small>
          </div>
          {contextPreview && (
            <div className="proof-ws-context-body">
              {contextPreview.goal && <ContextEvidence title="当前目标" item={contextPreview.goal} />}
              {contextPreview.understood.length > 0 && <ContextEvidenceList title="已掌握内容" items={contextPreview.understood} />}
              {contextPreview.usedNodes.length > 0 && <ContextEvidenceList title="已使用知识" items={contextPreview.usedNodes} />}
              {contextPreview.relatedContext.length > 0 && <ContextEvidenceList title="相关知识中的已掌握内容" items={contextPreview.relatedContext} />}
              {contextPreview.openGaps.length > 0 && (
                <ContextEvidenceList title="还需解决" items={contextPreview.openGaps} tone="warning" onUpdate={updateEvidence} busyId={feedbackBusy} />
              )}
              {contextPreview.relatedRisks.length > 0 && (
                <ContextEvidenceList title="可能需要复习的相关知识" items={contextPreview.relatedRisks} tone="risk" onUpdate={updateEvidence} busyId={feedbackBusy} />
              )}
              {contextPreview.nextStep && <ContextEvidence title="下一步建议" item={contextPreview.nextStep} />}
              {contextPreview.resolvedItems.length > 0 && (
                <ContextEvidenceList title="已处理记录" items={contextPreview.resolvedItems} resolved onUpdate={updateEvidence} busyId={feedbackBusy} />
              )}
              {!contextPreview.goal && contextPreview.understood.length === 0 && contextPreview.relatedContext.length === 0 && contextPreview.openGaps.length === 0 && contextPreview.relatedRisks.length === 0 && (
                <div className="proof-ws-context-empty">完成一次提示、检查或总结后，这里会整理你的学习目标、已掌握内容和待解决问题。</div>
              )}
            </div>
          )}
          {contextError && <div className="proof-ws-context-error"><AlertTriangle size={13} />{contextError}</div>}
          {educationClassId && (
            <div className="proof-ws-context-data-actions">
              <span>学习记录会帮助你在后续提问时接着学习。你可以随时导出或清除本课程的记录。</span>
              <div>
                <button type="button" disabled={contextArchiveBusy !== null} onClick={() => void exportCourseContext()}>{contextArchiveBusy === "export" ? <Loader2 className="proof-ws-spin" size={12} /> : <Download size={12} />}导出学习记录</button>
                <button type="button" className="danger" disabled={contextArchiveBusy !== null} onClick={() => void clearCourseContext()}>{contextArchiveBusy === "delete" ? <Loader2 className="proof-ws-spin" size={12} /> : <Trash2 size={12} />}清除学习记录</button>
              </div>
            </div>
          )}
        </div>
      )}

      {!answerMode && <div className="proof-ws-actions">
        <button onClick={() => handleAssist("hint")} disabled={!!assistLoading || !loaded}>
          {assistLoading === "hint" ? <Loader2 size={14} className="proof-ws-spin" /> : <Lightbulb size={14} />}
          提示
        </button>
        <button onClick={() => handleAssist("check")} disabled={!!assistLoading || !loaded}>
          {assistLoading === "check" ? <Loader2 size={14} className="proof-ws-spin" /> : <CheckCircle2 size={14} />}
          检查
        </button>
        <button onClick={() => handleAssist("summarize")} disabled={!!assistLoading || !loaded}>
          {assistLoading === "summarize" ? <Loader2 size={14} className="proof-ws-spin" /> : <ListChecks size={14} />}
          总结
        </button>
        <button
          onClick={() => setShowHistory((visible) => !visible)}
          disabled={!loaded}
          aria-expanded={showHistory}
        >
          <History size={14} />
          历史
        </button>
      </div>}

      {error && <div className="proof-ws-error">{error}</div>}

      {!answerMode && showHistory && (
        <>
          {state.aiMessages.length > 0 && (
            <div className="proof-ws-ai">
              {state.aiMessages.slice().reverse().map((message) => (
                <div key={message.id} className="proof-ws-ai-item">
                  <div className="proof-ws-ai-head">
                    <span>{actionLabel(message.action)}</span>
                    <time>{new Date(message.createdAt).toLocaleString("zh-CN")}</time>
                  </div>
                  <div className="proof-ws-ai-body"><MathText text={message.response} macros={macros} /></div>
                </div>
              ))}
            </div>
          )}

          {state.versions.length > 0 && (
            <div className="proof-ws-versions">
              {state.versions.slice().reverse().map((version) => (
                <div key={version.id} className="proof-ws-version">
                  <div className="proof-ws-version-head">
                    <span>{reasonLabel(version.reason)}</span>
                    <time>{new Date(version.createdAt).toLocaleString("zh-CN")}</time>
                  </div>
                  <pre>{version.userProof}</pre>
                </div>
              ))}
            </div>
          )}

          {state.aiMessages.length === 0 && state.versions.length === 0 && (
            <div className="proof-ws-empty">暂无历史记录</div>
          )}
        </>
      )}
    </div>
  );
}
