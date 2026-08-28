import { apiUrl } from "~/api";

export type OcrRuntimeState = "missing" | "downloading" | "verifying" | "installing" | "self_testing" | "ready" | "error";
export type OcrJobState = "queued" | "starting_engine" | "processing" | "collecting_output" | "done" | "cancelled" | "failed" | "interrupted";

export interface OcrRuntimeStatus {
  state: OcrRuntimeState;
  installable?: boolean;
  version?: string;
  component_version?: string;
  manifest_schema_version?: number;
  install_id?: string;
  downloaded_bytes?: number;
  total_bytes?: number;
  installed_bytes?: number;
  required_disk_bytes?: number;
  available_disk_bytes?: number;
  message?: string;
  error?: string;
  error_code?: string;
  failed_stage?: OcrRuntimeState | null;
  diagnostic?: string;
  retryable?: boolean;
  manifest_sha256?: string;
  installed_manifest_sha256?: string;
  repairable?: boolean;
  download_percent?: number;
  self_test_status?: "passed" | "failed" | null;
}

export type OcrRuntimeDisposition =
  | "unavailable"
  | "retryable_error"
  | "fatal_error"
  | "missing"
  | "installing"
  | "ready";

export function classifyOcrRuntime(status: OcrRuntimeStatus | null | undefined): OcrRuntimeDisposition {
  if (!status) return "missing";
  if (status.installable === false || status.error_code === "ocr_component_unavailable") return "unavailable";
  if (status.state === "error") return status.retryable === true ? "retryable_error" : "fatal_error";
  if (status.state === "ready") return "ready";
  if (status.state === "missing") return "missing";
  return "installing";
}

const OCR_RUNTIME_ERROR_SUMMARIES: Record<string, string> = {
  ocr_download_failed: "OCR 下载连接失败，请检查网络、代理或防火墙后重试。",
  ocr_download_proxy_timeout: "代理 TLS 握手超时，请检查代理设置或网络后重试。",
  ocr_download_timeout: "OCR 下载连接超时，请检查网络、代理或防火墙后重试。",
  ocr_download_incomplete: "OCR 组件下载不完整，可以继续重试下载。",
  ocr_hash_mismatch: "OCR 组件校验失败，可能是网络传输异常，请重试下载。",
  ocr_archive_invalid: "OCR 组件压缩包无法解压，请重试下载。",
  ocr_self_test_failed: "OCR 组件本地校准失败，请重试下载或查看诊断详情。",
  ocr_self_test_timeout: "OCR 组件校准超时，请重试下载或查看诊断详情。",
  ocr_runtime_missing: "OCR 组件安装内容不完整，请重试下载。",
  ocr_license_missing: "OCR 组件许可证文件缺失，请重试下载。",
  ocr_model_manifest_invalid: "OCR 模型清单校验失败，请重试下载。",
  ocr_calibration_missing: "OCR 校准文件缺失或校验失败，请重试下载。",
  insufficient_disk: "可用磁盘空间不足，释放空间后可以重试下载。",
  install_cancelled: "OCR 组件安装已取消，可以重试下载。",
};

export function ocrRuntimeErrorSummary(status: OcrRuntimeStatus): string {
  if (status.error_code === "ocr_component_unavailable" || status.installable === false) {
    return "此版本未发布可安装的 OCR 组件，请安装 MathWeaver 0.1.1；Markdown、TeX 和文本入口仍可用。";
  }
  if (status.error_code === "ocr_download_failed" && /handshake|_ssl\.c:993/i.test(status.diagnostic || status.message || "")) {
    return "代理 TLS 握手超时，请检查代理设置或网络后重试。";
  }
  return OCR_RUNTIME_ERROR_SUMMARIES[status.error_code || ""] || "OCR 组件安装失败，请查看诊断详情后重试。";
}

export function ocrRuntimeDiagnostic(status: OcrRuntimeStatus): string {
  const value = status.diagnostic || status.message || status.error || "暂无更多诊断信息";
  return value
    .replace(/https?:\/\/[^\s]+/gi, "[url]")
    .replace(/[A-Za-z]:[\\/][^\s]+/g, "[path]")
    .slice(0, 500);
}

export interface OcrUploadInfo {
  upload_id: string;
  filename: string;
  size_bytes: number;
  page_count: number;
  eta_seconds: { low: number; high: number } | null;
  sha256: string;
  expires_at: number;
}

export interface OcrJobStatus {
  ocr_job_id: string;
  upload_id: string;
  filename: string;
  size_bytes: number;
  page_count: number;
  status: OcrJobState;
  phase: OcrJobState;
  elapsed_seconds: number;
  eta_seconds: { low: number; high: number } | null;
  retryable: boolean;
  error?: string | null;
  error_code?: string;
}

export interface OcrResult {
  importedText: string;
  filename: string;
  source: "ocr_file";
  ocr_job_id: string;
}

export interface OcrRecoveryResponse {
  jobs: OcrJobStatus[];
}

function errorMessage(body: unknown, fallback: string) {
  if (body && typeof body === "object") {
    const value = body as { message?: unknown; error?: unknown };
    if (typeof value.message === "string" && value.message) return value.message;
    if (typeof value.error === "string" && value.error) return value.error;
  }
  return fallback;
}

async function readResponse<T>(response: Response, fallback: string): Promise<T> {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorMessage(body, fallback));
  return body as T;
}

function abortableDelay(milliseconds: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    let timer: number | undefined;
    const onAbort = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      reject(new DOMException("Operation cancelled", "AbortError"));
    };
    timer = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export function uploadOcrFile(
  file: File,
  onProgress?: (percent: number) => void,
  signal?: AbortSignal,
): Promise<OcrUploadInfo> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let settled = false;
    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener("abort", abort);
      callback();
    };
    const abort = () => {
      xhr.abort();
      finish(() => reject(new DOMException("Upload cancelled", "AbortError")));
    };
    xhr.open("POST", apiUrl("/api/v2/ocr/uploads"));
    xhr.responseType = "json";
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onerror = () => finish(() => reject(new Error("OCR upload failed")));
    xhr.onabort = () => finish(() => reject(new DOMException("Upload cancelled", "AbortError")));
    xhr.onload = () => {
      const body = xhr.response ?? {};
      if (xhr.status < 200 || xhr.status >= 300) {
        finish(() => reject(new Error(errorMessage(body, "OCR upload failed"))));
        return;
      }
      finish(() => resolve(body as OcrUploadInfo));
    };
    signal?.addEventListener("abort", abort, { once: true });
    const form = new FormData();
    form.append("file", file, file.name);
    xhr.send(form);
  });
}

export async function getOcrRuntime(signal?: AbortSignal): Promise<OcrRuntimeStatus> {
  const response = await fetch(apiUrl("/api/v2/ocr/runtime"), { signal });
  return readResponse<OcrRuntimeStatus>(response, "Unable to read OCR runtime status");
}

export async function installOcrRuntime(signal?: AbortSignal, onStatus?: (status: OcrRuntimeStatus) => void): Promise<OcrRuntimeStatus> {
  const response = await fetch(apiUrl("/api/v2/ocr/runtime/install"), { method: "POST", signal });
  const initial = await readResponse<OcrRuntimeStatus>(response, "Unable to start OCR runtime installation");
  onStatus?.(initial);
  if (initial.installable === false || initial.error_code === "ocr_component_unavailable") {
    throw new Error(ocrRuntimeErrorSummary(initial));
  }
  if (initial.state === "error") {
    throw new Error(ocrRuntimeErrorSummary(initial));
  }
  if (!initial.install_id) return initial;
  while (true) {
    await abortableDelay(1000, signal);
    const statusResponse = await fetch(apiUrl(`/api/v2/ocr/runtime/install/${encodeURIComponent(initial.install_id)}`), { signal });
    const status = await readResponse<OcrRuntimeStatus>(statusResponse, "Unable to read OCR installation status");
    onStatus?.(status);
    if (status.state === "ready" || status.state === "error") {
      if (status.state === "error") throw new Error(ocrRuntimeErrorSummary(status));
      return status;
    }
  }
}

export async function cancelOcrInstall(installId: string): Promise<OcrRuntimeStatus> {
  const response = await fetch(apiUrl(`/api/v2/ocr/runtime/install/${encodeURIComponent(installId)}/cancel`), { method: "POST" });
  return readResponse<OcrRuntimeStatus>(response, "Unable to cancel OCR installation");
}

export async function startOcrJob(uploadId: string, signal?: AbortSignal): Promise<OcrJobStatus> {
  const response = await fetch(apiUrl("/api/v2/ocr/jobs"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ upload_id: uploadId }),
    signal,
  });
  return readResponse<OcrJobStatus>(response, "Unable to start OCR");
}

export async function getOcrJob(jobId: string, signal?: AbortSignal): Promise<OcrJobStatus> {
  const response = await fetch(apiUrl(`/api/v2/ocr/jobs/${encodeURIComponent(jobId)}`), { signal });
  return readResponse<OcrJobStatus>(response, "Unable to read OCR status");
}

export async function pollOcrJob(
  jobId: string,
  onStatus?: (status: OcrJobStatus) => void,
  signal?: AbortSignal,
): Promise<OcrJobStatus> {
  while (true) {
    const status = await getOcrJob(jobId, signal);
    onStatus?.(status);
    if (["done", "cancelled", "failed", "interrupted"].includes(status.status)) {
      if (status.status !== "done") throw new Error(status.error || "OCR failed");
      return status;
    }
    await abortableDelay(1000, signal);
  }
}

export async function getOcrResult(jobId: string, signal?: AbortSignal): Promise<OcrResult> {
  const response = await fetch(apiUrl(`/api/v2/ocr/jobs/${encodeURIComponent(jobId)}/result`), { signal });
  return readResponse<OcrResult>(response, "Unable to read OCR result");
}

export async function cancelOcrJob(jobId: string): Promise<OcrJobStatus> {
  const response = await fetch(apiUrl(`/api/v2/ocr/jobs/${encodeURIComponent(jobId)}/cancel`), { method: "POST" });
  return readResponse<OcrJobStatus>(response, "Unable to cancel OCR");
}

export async function deleteOcrUpload(uploadId: string): Promise<void> {
  const response = await fetch(apiUrl(`/api/v2/ocr/uploads/${encodeURIComponent(uploadId)}`), { method: "DELETE" });
  await readResponse<{ ok: boolean }>(response, "Unable to remove OCR upload");
}

export async function getOcrRecovery(signal?: AbortSignal): Promise<OcrRecoveryResponse> {
  const response = await fetch(apiUrl("/api/v2/ocr/recovery"), { signal });
  return readResponse<OcrRecoveryResponse>(response, "Unable to read OCR recovery tasks");
}

export async function retryOcrJob(jobId: string, signal?: AbortSignal): Promise<OcrJobStatus> {
  const response = await fetch(apiUrl(`/api/v2/ocr/jobs/${encodeURIComponent(jobId)}/retry`), { method: "POST", signal });
  return readResponse<OcrJobStatus>(response, "Unable to retry OCR");
}

export async function deleteOcrRecovery(jobId: string, signal?: AbortSignal): Promise<void> {
  const response = await fetch(apiUrl(`/api/v2/ocr/recovery/${encodeURIComponent(jobId)}`), { method: "DELETE", signal });
  await readResponse<{ ok: boolean }>(response, "Unable to remove OCR recovery task");
}
