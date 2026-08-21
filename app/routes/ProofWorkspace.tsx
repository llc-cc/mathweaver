import { type ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BookOpen, CheckCircle2, FileUp, History, Lightbulb, ListChecks, Loader2, Save } from "lucide-react";
import { apiUrl } from "~/api";
import {
  captureAuthRequestIdentity,
  clearWebSensitiveStorage,
  isAuthRequestIdentityCurrent,
  protectedFetch,
} from "./auth";
import { isDesktopRuntime } from "../runtime";
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
import type { GraphNode, LLMConfig } from "./home";
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
  if (!isDesktopRuntime()) {
    clearWebSensitiveStorage();
    return emptyState(nodeId);
  }
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
  if (!isDesktopRuntime()) {
    clearWebSensitiveStorage();
    return;
  }
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

export function ProofWorkspace({ graphId, node, token, llmConfig, macros }: ProofWorkspaceProps) {
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
  const textbookProof = asText(node.proof);

  useEffect(() => () => {
    ocrAbortRef.current?.abort();
    ocrAbortRef.current = null;
  }, []);

  useEffect(() => {
    let cancelled = false;
    setShowHistory(false);
    const load = async () => {
      const requestIdentity = captureAuthRequestIdentity(token);
      setLoaded(false);
      setError("");
      if (!token) {
        if (!cancelled) {
          setState(loadLocalState(graphId, node.id));
          setLoaded(true);
        }
        return;
      }
      try {
        const res = await protectedFetch(apiUrl(`/api/v2/proof-workspaces/${encodeURIComponent(graphId)}`), {
          headers: { Authorization: `Bearer ${token}` },
        }, token);
        if (isAuthFailure(res)) {
          if (!cancelled) setState(loadLocalState(graphId, node.id));
          return;
        }
        if (!res.ok) throw new Error("证明工作区加载失败");
        const data = await res.json();
        if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
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
  }, [graphId, node.id, token]);

  const persist = useCallback(async (next: ProofWorkspaceState) => {
    const requestIdentity = captureAuthRequestIdentity(token);
    const updated = { ...next, updatedAt: new Date().toISOString() };
    setState(updated);
    if (!token) {
      saveLocalState(graphId, node.id, updated);
      return updated;
    }
    const res = await protectedFetch(apiUrl(`/api/v2/proof-workspaces/${encodeURIComponent(graphId)}/${node.id}`), {
      method: "PUT",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(updated),
    }, token);
    if (!isAuthRequestIdentityCurrent(requestIdentity)) return updated;
    if (isAuthFailure(res)) {
      saveLocalState(graphId, node.id, updated);
      return updated;
    }
    if (!res.ok) throw new Error("保存失败");
    const data = await res.json();
    if (!isAuthRequestIdentityCurrent(requestIdentity)) return updated;
    return data.workspace as ProofWorkspaceState;
  }, [graphId, node.id, token]);

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
    const requestIdentity = captureAuthRequestIdentity(token);
    setAssistLoading(action);
    setError("");
    try {
      const snapshotted = addVersion(`assist_${action}` as ProofVersionReason);
      const res = await protectedFetch(apiUrl("/api/v2/proof-assist"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          action,
          node,
          userProof: snapshotted.userProof,
          llm_config: completeLlmConfig(llmConfig),
        }),
      }, token);
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      const data = await res.json().catch(() => ({}));
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      if (!res.ok) {
        throw new Error(data.message || data.error || "AI 辅助失败");
      }
      const next: ProofWorkspaceState = {
        ...snapshotted,
        aiMessages: [
          ...snapshotted.aiMessages,
          {
            id: makeId("assist"),
            action,
            requestProof: snapshotted.userProof,
            response: data.response || "",
            createdAt: new Date().toISOString(),
          },
        ],
      };
      await persist(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : "AI 辅助失败");
    } finally {
      setAssistLoading(null);
    }
  };

  const latestVersion = useMemo(() => state.versions[state.versions.length - 1], [state.versions]);

  return (
    <div className="proof-ws">
      {textbookProof && (
        <div className="proof-ws-card">
          <button className="proof-ws-rowbtn" onClick={() => setShowTextbook((v) => !v)}>
            <BookOpen size={14} />
            <span>教材证明</span>
            <b>{showTextbook ? "收起" : "查看"}</b>
          </button>
          {showTextbook && (
            <div className="proof-ws-textbook">
              <MathText text={textbookProof} macros={macros} />
            </div>
          )}
        </div>
      )}

      <div className="proof-ws-head">
        <div>
          <div className="proof-ws-title">我的证明</div>
          <div className="proof-ws-subtitle">
            {loaded ? (latestVersion ? `上次保存：${new Date(latestVersion.createdAt).toLocaleString("zh-CN")}` : "尚未保存版本") : "正在加载..."}
          </div>
        </div>
        <button className="proof-ws-save" onClick={handleSave} disabled={saving || !loaded}>
          {saving ? <Loader2 size={14} className="proof-ws-spin" /> : <Save size={14} />}
          保存
        </button>
      </div>

      <div className="proof-ws-import">
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
        <span>{importing ? `OCR：${ocrPhase || "处理中"}` : "支持文本文件直接读取，PDF/图片自动识别。"}</span>
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

      <textarea
        className="proof-ws-editor"
        value={state.userProof}
        onChange={(event) => setState((prev) => ({ ...prev, userProof: event.target.value }))}
        placeholder="在这里写下你的证明思路，可以使用 LaTeX 记号。"
        disabled={!loaded}
      />

      <div className="proof-ws-actions">
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
      </div>

      {error && <div className="proof-ws-error">{error}</div>}

      {showHistory && (
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
