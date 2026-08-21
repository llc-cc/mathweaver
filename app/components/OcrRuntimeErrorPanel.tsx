import {
  classifyOcrRuntime,
  ocrRuntimeDiagnostic,
  ocrRuntimeErrorSummary,
  type OcrRuntimeStatus,
} from "~/ocr";

interface OcrRuntimeErrorPanelProps {
  status: OcrRuntimeStatus;
  onRetry?: () => void;
  retrying?: boolean;
  compact?: boolean;
}

export function OcrRuntimeErrorPanel({
  status,
  onRetry,
  retrying = false,
  compact = false,
}: OcrRuntimeErrorPanelProps) {
  const disposition = classifyOcrRuntime(status);
  if (!["unavailable", "retryable_error", "fatal_error"].includes(disposition)) return null;

  const retryable = disposition === "retryable_error" && Boolean(onRetry);
  const summary = ocrRuntimeErrorSummary(status);
  const downloaded = Number(status.downloaded_bytes || 0);
  const total = Number(status.total_bytes || 0);

  return (
    <div
      role="alert"
      style={{
        marginTop: compact ? 6 : 10,
        padding: compact ? "8px 10px" : "10px 12px",
        border: "1px solid var(--danger-line)",
        borderRadius: 8,
        background: "var(--danger-light)",
        color: "var(--danger)",
      }}
    >
      <div style={{ fontWeight: 600 }}>{summary}</div>
      {retryable && (
        <button
          type="button"
          className="mg-btn mg-btn-ghost"
          onClick={onRetry}
          disabled={retrying}
          style={{ marginTop: 7, minHeight: 28, padding: "4px 10px", color: "var(--danger)" }}
        >
          {retrying ? "正在重试下载…" : "重试下载"}
        </button>
      )}
      <details style={{ marginTop: 7, color: "var(--muted)" }}>
        <summary style={{ cursor: "pointer" }}>查看错误详情</summary>
        <div style={{ marginTop: 5, lineHeight: 1.6, wordBreak: "break-word" }}>
          <div>错误码：{status.error_code || status.error || "unknown"}</div>
          {status.failed_stage && <div>失败阶段：{status.failed_stage}</div>}
          {status.component_version && <div>组件版本：{status.component_version}</div>}
          {total > 0 && <div>下载进度：{downloaded} / {total} bytes</div>}
          <div>诊断：{ocrRuntimeDiagnostic(status)}</div>
        </div>
      </details>
    </div>
  );
}
