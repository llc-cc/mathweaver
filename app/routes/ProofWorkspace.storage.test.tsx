// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthState } from "../auth-model";
import type { GraphNode } from "./home";
import { saveAuth } from "./auth";
import { ProofWorkspace } from "./ProofWorkspace";

vi.mock("~/api", () => ({ apiUrl: (path: string) => path }));

const AUTH: AuthState = {
  token: "proof-token",
  user: {
    id: 21,
    student_no: "20260021",
    email: null,
    display_name: "证明学生",
    role: "student",
    initial_password_pending: false,
  },
};

const NODE: GraphNode = {
  id: 1,
  node_type: "theorem",
  title_zh: "测试定理",
  title_en: "Test theorem",
  label: "Theorem 1",
  content: "statement",
  statement_form: "",
  subject: [],
  conditions: [],
  conclusions: [],
  proof: null,
};

const PROOF_KEY = "proof_workspace:graph-a:1";
const LEGACY_PROOF = {
  nodeId: 1,
  userProof: "account A proof draft",
  versions: [],
  aiMessages: [],
  imports: [],
  updatedAt: "2026-08-21T00:00:00.000Z",
};

describe("ProofWorkspace storage isolation", () => {
  beforeEach(() => localStorage.clear());

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("does not load or save legacy local proof drafts in Web mode", async () => {
    localStorage.setItem(PROOF_KEY, JSON.stringify(LEGACY_PROOF));
    render(<ProofWorkspace graphId="graph-a" node={NODE} />);

    const editor = await screen.findByPlaceholderText("在这里写下你的证明思路，可以使用 LaTeX 记号。");
    await waitFor(() => expect(editor.hasAttribute("disabled")).toBe(false));
    expect((editor as HTMLTextAreaElement).value).toBe("");
    fireEvent.change(editor, { target: { value: "account B draft" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(localStorage.getItem(PROOF_KEY)).toBeNull());
  });

  it("does not fall back to a local proof when a Web protected request is forbidden", async () => {
    saveAuth(AUTH);
    localStorage.setItem(PROOF_KEY, JSON.stringify(LEGACY_PROOF));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: "forbidden" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    })));

    render(<ProofWorkspace graphId="graph-a" node={NODE} token={AUTH.token} />);

    const editor = await screen.findByPlaceholderText("在这里写下你的证明思路，可以使用 LaTeX 记号。");
    await waitFor(() => expect(editor.hasAttribute("disabled")).toBe(false));
    expect((editor as HTMLTextAreaElement).value).toBe("");
    expect(localStorage.getItem(PROOF_KEY)).toBeNull();
  });

  it("keeps Electron offline proof drafts readable and writable", async () => {
    vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue("MathWeaverDesktop/1.0");
    localStorage.setItem(PROOF_KEY, JSON.stringify(LEGACY_PROOF));
    render(<ProofWorkspace graphId="graph-a" node={NODE} />);

    const editor = await screen.findByPlaceholderText("在这里写下你的证明思路，可以使用 LaTeX 记号。");
    await waitFor(() => expect((editor as HTMLTextAreaElement).value).toBe("account A proof draft"));
    fireEvent.change(editor, { target: { value: "updated desktop proof" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(localStorage.getItem(PROOF_KEY)).toContain("updated desktop proof"));
  });
});

