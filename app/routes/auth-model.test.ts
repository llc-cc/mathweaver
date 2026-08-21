// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthState } from "../auth-model";
import {
  authFetch,
  clearAuth,
  loadAuth,
  loadLlm,
  loadMd,
  loadSession,
  protectedFetch,
  saveAuth,
  saveLlm,
  saveMd,
  saveSession,
  subscribeAuthInvalidated,
} from "./auth";

const AUTH: AuthState = {
  token: "session-token",
  user: {
    id: 7,
    student_no: "20260007",
    email: "student@example.edu",
    display_name: "测试学生",
    role: "student",
    initial_password_pending: true,
  },
};

const NEXT_AUTH: AuthState = {
  token: "next-session-token",
  user: {
    id: 8,
    student_no: "20260008",
    email: null,
    display_name: "另一名学生",
    role: "student",
    initial_password_pending: false,
  },
};

describe("auth storage", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("persists and loads the complete authentication state", () => {
    saveAuth(AUTH);

    expect(loadAuth()).toEqual(AUTH);
    expect(JSON.parse(localStorage.getItem("mg_auth") ?? "null")).toEqual(AUTH);
  });

  it("clears legacy credentials and requires a fresh login", () => {
    localStorage.setItem("mg_token", "legacy-token");
    localStorage.setItem("mg_email", "legacy@example.edu");

    expect(loadAuth()).toBeNull();
    expect(localStorage.getItem("mg_token")).toBeNull();
    expect(localStorage.getItem("mg_email")).toBeNull();
  });

  it("clears the complete and legacy authentication state on logout", () => {
    saveAuth(AUTH);
    localStorage.setItem("mg_token", "legacy-token");
    localStorage.setItem("mg_email", "legacy@example.edu");

    clearAuth();

    expect(localStorage.getItem("mg_auth")).toBeNull();
    expect(localStorage.getItem("mg_token")).toBeNull();
    expect(localStorage.getItem("mg_email")).toBeNull();
  });

  it("clears local authentication when an authenticated request returns 401", async () => {
    saveAuth(AUTH);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));

    const response = await authFetch("/protected", {}, AUTH.token);

    expect(response.status).toBe(401);
    expect(loadAuth()).toBeNull();
  });

  it("notifies React owners when a protected request invalidates the session", async () => {
    saveAuth(AUTH);
    const invalidated = vi.fn();
    const unsubscribe = subscribeAuthInvalidated(invalidated);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));

    await protectedFetch("/protected");

    expect(invalidated).toHaveBeenCalledTimes(1);
    expect(loadAuth()).toBeNull();
    unsubscribe();
  });

  it("loads the current AuthState token for protected requests", async () => {
    saveAuth(AUTH);
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await protectedFetch("/protected", { headers: { Accept: "application/json" } });

    const [, request] = fetchMock.mock.calls[0];
    const headers = new Headers(request.headers);
    expect(headers.get("Authorization")).toBe("Bearer session-token");
    expect(headers.get("Accept")).toBe("application/json");
  });

  it("does not expose account A Web caches or API keys after logout and account B login", () => {
    saveAuth(AUTH);
    saveLlm({
      api_url: "https://a.example/v1",
      model_name: "a-model",
      api_key: "account-a-secret",
      embedding_url: "https://a.example/embed",
      embedding_model: "a-embed",
      embedding_api_key: "account-a-embed-secret",
    });
    saveMd("job-a", "account A source markdown");
    saveSession("generate", {
      result: { nodes: [], edges: [] },
      filename: "account-a.md",
      jobId: "job-a",
      sourceMarkdown: "account A source markdown",
    });
    localStorage.setItem("proof_workspace:job-a:1", JSON.stringify({ userProof: "account A proof" }));

    clearAuth();
    saveAuth(NEXT_AUTH);

    expect(loadLlm().api_key).toBe("");
    expect(loadLlm().embedding_api_key).toBe("");
    expect(loadMd("job-a")).toBeUndefined();
    expect(loadSession("generate")).toBeNull();
    expect(localStorage.getItem("proof_workspace:job-a:1")).toBeNull();
    expect(Object.values(localStorage).join(" ")).not.toContain("account-a-secret");
    expect(Object.values(localStorage).join(" ")).not.toContain("account A proof");
  });

  it("keeps Electron offline caches available after local logout", () => {
    vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue("MathWeaverDesktop/1.0");
    saveAuth(AUTH);
    saveLlm({
      api_url: "http://localhost/v1",
      model_name: "desktop-model",
      api_key: "desktop-key",
      embedding_url: "",
      embedding_model: "desktop-embed",
      embedding_api_key: "",
    });
    saveMd("desktop-job", "desktop source");
    saveSession("generate", {
      result: { nodes: [], edges: [] },
      filename: "desktop.md",
      jobId: "desktop-job",
      sourceMarkdown: "desktop source",
    });

    clearAuth();

    expect(loadLlm().api_key).toBe("desktop-key");
    expect(loadMd("desktop-job")).toBe("desktop source");
    expect(loadSession("generate")?.filename).toBe("desktop.md");
  });

  it("does not invalidate a new session when an explicit old-token request returns 401 late", async () => {
    let resolveRequest!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { resolveRequest = resolve; })));
    saveAuth(AUTH);
    const invalidated = vi.fn();
    const unsubscribe = subscribeAuthInvalidated(invalidated);

    const pending = protectedFetch("/protected", {}, AUTH.token);
    saveAuth(NEXT_AUTH);
    resolveRequest(new Response(null, { status: 401 }));
    await pending;

    expect(loadAuth()).toEqual(NEXT_AUTH);
    expect(invalidated).not.toHaveBeenCalled();
    unsubscribe();
  });

  it("does not invalidate a new session when a default-token request returns 401 late", async () => {
    let resolveRequest!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { resolveRequest = resolve; })));
    saveAuth(AUTH);
    const invalidated = vi.fn();
    const unsubscribe = subscribeAuthInvalidated(invalidated);

    const pending = protectedFetch("/protected");
    saveAuth(NEXT_AUTH);
    resolveRequest(new Response(null, { status: 401 }));
    await pending;

    expect(loadAuth()).toEqual(NEXT_AUTH);
    expect(invalidated).not.toHaveBeenCalled();
    unsubscribe();
  });
});

