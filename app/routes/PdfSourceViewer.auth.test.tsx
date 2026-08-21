// @vitest-environment jsdom
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PdfSourceViewer } from "./PdfSourceViewer";

const { getDocument } = vi.hoisted(() => ({
  getDocument: vi.fn((_source: unknown) => ({
    promise: Promise.resolve({ numPages: 0 }),
    destroy: vi.fn(),
  })),
}));

vi.mock("pdfjs-dist/build/pdf.mjs", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  Util: { transform: vi.fn() },
  getDocument,
}));

afterEach(() => {
  cleanup();
  getDocument.mockClear();
});

describe("PdfSourceViewer authentication", () => {
  it("passes the bearer token to pdf.js without putting it in the URL", async () => {
    render(<PdfSourceViewer
      url="/api/v2/source-pdf/job-1"
      token="pdf-token"
      page={1}
      sourceStatement=""
      searchTerms={[]}
      statementTerms={[]}
    />);

    await waitFor(() => expect(getDocument).toHaveBeenCalledTimes(1));
    expect(getDocument.mock.calls[0][0]).toMatchObject({
      url: "/api/v2/source-pdf/job-1",
      httpHeaders: { Authorization: "Bearer pdf-token" },
    });
  });
});

