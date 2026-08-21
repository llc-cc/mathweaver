// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StudioWrapper } from "./home";

vi.mock("./GraphStudio", () => ({
  default: ({ onExport }: { onExport: (format: "html" | "json") => void }) => <>
    <button onClick={() => onExport("html")}>export-html</button>
    <button onClick={() => onExport("json")}>export-json</button>
  </>,
}));
vi.mock("~/api", () => ({ apiUrl: (path: string) => path }));

const RESULT = { nodes: [], edges: [] };

describe("Studio export authentication", () => {
  beforeEach(() => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:export") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it.each([
    ["export-html", "/api/v2/export/job-1"],
    ["export-json", "/api/v2/export/job-1/artifacts"],
  ])("uses the bearer token for %s", async (buttonName, endpoint) => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(new Blob(["archive"]), {
      status: 200,
      headers: { "X-MathGraph-Export-Mode": "full" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    render(<StudioWrapper
      workspaceMode="generate"
      result={RESULT}
      filename="input.md"
      jobId="job-1"
      nodeLanguage="bilingual"
      token="export-token"
      onReset={vi.fn()}
      onShowApiGuide={vi.fn()}
    />);

    fireEvent.click(screen.getByRole("button", { name: buttonName }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(endpoint);
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer export-token");
  });
});

