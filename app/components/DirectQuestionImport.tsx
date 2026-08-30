import { useRef, useState } from "react";
import { FileText, Loader2, Type, Upload, X } from "lucide-react";
import {
  cancelOcrInstall,
  cancelOcrJob,
  classifyOcrRuntime,
  getOcrResult,
  getOcrRuntime,
  installOcrRuntime,
  ocrRuntimeErrorSummary,
  pollOcrJob,
  startOcrJob,
  uploadOcrFile,
  type OcrJobStatus,
  type OcrRuntimeStatus,
} from "~/ocr";
import { OcrRuntimeErrorPanel } from "./OcrRuntimeErrorPanel";

export type DirectImportOrigin = "paste" | "document" | "ocr";
type ImportMode = "paste" | "document" | "pdf";

interface DirectQuestionImportProps {
  onImported: (text: string, file: File | null, origin: DirectImportOrigin) => void;
  onCancel: () => void;
  cancelLabel?: string;
}

export function DirectQuestionImport({ onImported, onCancel, cancelLabel = "关闭导入" }: DirectQuestionImportProps) {
  const [mode, setMode] = useState<ImportMode>("paste");
  const [text, setText] = useState("");
  const [documentFile, setDocumentFile] = useState<File | null>(null);
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [phase, setPhase] = useState<"reading" | "uploading" | "installing" | "ocr" | null>(null);
  const [runtime, setRuntime] = useState<OcrRuntimeStatus | null>(null);
  const [job, setJob] = useState<OcrJobStatus | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const isOcrFile = (candidate: File) => /\.(pdf|png|jpg|jpeg|webp)$/i.test(candidate.name);
  const isDocumentFile = (candidate: File) => /\.(md|tex|txt)$/i.test(candidate.name);

  const importText = (value: string, origin: DirectImportOrigin, sourceFile: File | null = null) => {
    const normalized = value.trim();
    if (!normalized) {
      setError("没有识别到可导入的题目文本。");
      return;
    }
    onImported(normalized, sourceFile, origin);
  };

  const readDocument = async () => {
    if (!documentFile || !isDocumentFile(documentFile)) {
      setError("请选择 Markdown、TeX 或纯文本文件。");
      return;
    }
    setPhase("reading");
    setError("");
    try {
      importText(await documentFile.text(), "document", documentFile);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "文档读取失败");
    } finally {
      setPhase(null);
    }
  };

  const runOcr = async (candidate = pdfFile) => {
    if (!candidate || !isOcrFile(candidate)) {
      setError("请选择 PDF 或图片文件。");
      return;
    }
    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;
    setError("");
    setJob(null);
    setRuntime(null);
    setProgress(0);
    setPhase("uploading");
    try {
      const uploaded = await uploadOcrFile(candidate, setProgress, controller.signal);
      let nextRuntime = await getOcrRuntime(controller.signal);
      setRuntime(nextRuntime);
      const disposition = classifyOcrRuntime(nextRuntime);
      if (disposition === "unavailable" || disposition === "fatal_error") {
        throw new Error(ocrRuntimeErrorSummary(nextRuntime));
      }
      if (nextRuntime.state !== "ready") {
        setPhase("installing");
        nextRuntime = await installOcrRuntime(controller.signal, setRuntime);
        setRuntime(nextRuntime);
      }
      setPhase("ocr");
      const nextJob = await startOcrJob(uploaded.upload_id, controller.signal);
      setJob(nextJob);
      await pollOcrJob(nextJob.ocr_job_id, setJob, controller.signal);
      const result = await getOcrResult(nextJob.ocr_job_id, controller.signal);
      importText(result.importedText, "ocr", candidate);
    } catch (cause) {
      if (!(cause instanceof DOMException && cause.name === "AbortError")) {
        setError(cause instanceof Error ? cause.message : "识别失败，请重试");
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setPhase(null);
      setProgress(null);
    }
  };

  const cancel = async () => {
    const currentJob = job;
    if (currentJob && ["queued", "starting_engine", "processing", "collecting_output"].includes(currentJob.status)) {
      await cancelOcrJob(currentJob.ocr_job_id).catch(() => undefined);
    }
    if (runtime?.install_id && ["downloading", "verifying", "installing", "self_testing"].includes(runtime.state)) {
      await cancelOcrInstall(runtime.install_id).catch(() => undefined);
    }
    abortRef.current?.abort();
    abortRef.current = null;
    setPhase(null);
    setJob(null);
    setError("题目识别已取消。");
  };

  const chooseMode = (nextMode: ImportMode) => {
    if (phase) return;
    setMode(nextMode);
    setError("");
  };

  return (
    <section className="edu-direct-import" aria-label="导入题目">
      <div className="edu-direct-import-tabs" role="tablist" aria-label="题目导入方式">
        <button type="button" className={mode === "paste" ? "active" : ""} onClick={() => chooseMode("paste")}><Type size={14} />粘贴文本</button>
        <button type="button" className={mode === "document" ? "active" : ""} onClick={() => chooseMode("document")}><FileText size={14} />上传文档</button>
        <button type="button" className={mode === "pdf" ? "active" : ""} onClick={() => chooseMode("pdf")}><Upload size={14} />上传 PDF / 图片</button>
      </div>
      {mode === "paste" && <>
        <textarea className="edu-direct-import-textarea" rows={8} value={text} onChange={event => setText(event.target.value)} placeholder="粘贴题目文本、Markdown 或 LaTeX…" disabled={Boolean(phase)} />
        <button type="button" className="edu-button primary" disabled={Boolean(phase) || !text.trim()} onClick={() => importText(text, "paste")}>导入文本</button>
      </>}
      {mode === "document" && <>
        <label className="edu-direct-file-picker">
          <Upload size={18} /><span>{documentFile?.name || "选择 Markdown / TeX / TXT 文件"}</span>
          <input type="file" accept=".md,.tex,.txt,text/markdown,text/plain,text/x-tex" onChange={event => { const next = event.target.files?.[0] || null; setDocumentFile(next); setError(next && !isDocumentFile(next) ? "文档格式不支持。" : ""); }} />
        </label>
        <button type="button" className="edu-button primary" disabled={Boolean(phase) || !documentFile || !isDocumentFile(documentFile)} onClick={() => void readDocument()}>{phase === "reading" ? <><Loader2 className="edu-spin" size={14} />正在读取…</> : "导入文档"}</button>
      </>}
      {mode === "pdf" && <>
        <label className="edu-direct-file-picker">
          <Upload size={18} /><span>{pdfFile?.name || "选择 PDF 或 PNG / JPG / WEBP 图片"}</span>
          <input type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,application/pdf,image/png,image/jpeg,image/webp" onChange={event => {
            const next = event.target.files?.[0] || null;
            setPdfFile(next);
            setError(next && !isOcrFile(next) ? "仅支持 PDF、PNG、JPG、JPEG 或 WEBP。" : "");
            if (next && isOcrFile(next)) void runOcr(next);
          }} />
        </label>
        <p className="edu-direct-import-hint">选择文件后会自动识别题目，无需单独启动 OCR。</p>
        {runtime && <div className="edu-direct-import-status"><div>识别组件：{runtime.state}</div><OcrRuntimeErrorPanel status={runtime} onRetry={() => void runOcr()} retrying={phase === "installing"} /></div>}
        {progress !== null && <div className="edu-direct-import-status">上传进度：{progress}%</div>}
        {job && <div className="edu-direct-import-status">识别阶段：{job.phase} · 已用 {job.elapsed_seconds ?? 0} 秒</div>}
        {phase && <button type="button" className="edu-button ghost" onClick={() => void cancel()}>取消识别</button>}
      </>}
      {error && <div className="edu-modal-error">{error}</div>}
      <button type="button" className="edu-button ghost" onClick={onCancel} disabled={Boolean(phase)}><X size={14} />{cancelLabel}</button>
    </section>
  );
}