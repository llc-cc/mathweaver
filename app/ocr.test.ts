import { describe, expect, it } from "vitest";
import {
  classifyOcrRuntime,
  ocrRuntimeDiagnostic,
  ocrRuntimeErrorSummary,
  type OcrRuntimeStatus,
} from "./ocr";

function status(overrides: Partial<OcrRuntimeStatus> = {}): OcrRuntimeStatus {
  return {
    state: "error",
    installable: true,
    retryable: true,
    error_code: "ocr_download_failed",
    ...overrides,
  };
}

describe("OCR runtime state classification", () => {
  it("keeps unavailable manifests distinct from retryable installation errors", () => {
    expect(classifyOcrRuntime(status({ installable: false, error_code: "ocr_component_unavailable", retryable: false }))).toBe("unavailable");
    expect(classifyOcrRuntime(status({ installable: true, error_code: "ocr_download_failed", retryable: true }))).toBe("retryable_error");
    expect(classifyOcrRuntime(status({ installable: true, error_code: "ocr_self_test_failed", retryable: false }))).toBe("fatal_error");
  });

  it("preserves ready, missing, and installing states", () => {
    expect(classifyOcrRuntime(status({ state: "ready", retryable: false }))).toBe("ready");
    expect(classifyOcrRuntime(status({ state: "missing" }))).toBe("missing");
    expect(classifyOcrRuntime(status({ state: "downloading" }))).toBe("installing");
  });

  it("uses a user-safe summary and redacts diagnostic paths and URLs", () => {
    const value = status({ diagnostic: "https://example.test/x?token=secret C:\\Users\\alice\\ocr\\install.log" });
    expect(ocrRuntimeErrorSummary(value)).toContain("网络");
    expect(ocrRuntimeDiagnostic(value)).not.toContain("secret");
    expect(ocrRuntimeDiagnostic(value)).not.toContain("C:\\Users");
    expect(ocrRuntimeErrorSummary(status({ error_code: "ocr_download_proxy_timeout" }))).toContain("代理 TLS 握手超时");
    expect(ocrRuntimeErrorSummary(status({ error_code: "ocr_download_failed", diagnostic: "_ssl.c:993 handshake operation timed out" }))).toContain("代理 TLS 握手超时");
  });
});
