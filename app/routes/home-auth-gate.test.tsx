// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";
import type { AuthState } from "../auth-model";
import Home from "./home";
import { loadAuth, protectedFetch, saveAuth } from "./auth";

const { startJobMock } = vi.hoisted(() => ({ startJobMock: vi.fn() }));

vi.mock("~/context/jobs", () => ({
  useJobs: () => ({
    jobs: {},
    latestJobId: null,
    startJob: startJobMock,
    pauseJob: vi.fn(),
    resumeJob: vi.fn(),
    cancelJob: vi.fn(),
    restoreJob: vi.fn(),
    dismissJob: vi.fn(),
  }),
}));
vi.mock("~/components/FloatingBadge", () => ({ FloatingBadge: () => null }));
vi.mock("./GraphStudio", () => ({
  default: ({ filename }: { filename: string }) => <div data-testid="graph-studio">{filename}</div>,
}));

const AUTH: AuthState = {
  token: "home-token",
  user: {
    id: 17,
    student_no: "20260017",
    email: null,
    display_name: "学生十七",
    role: "student",
    initial_password_pending: false,
  },
};

const NEXT_AUTH: AuthState = {
  token: "next-home-token",
  user: {
    id: 18,
    student_no: "20260018",
    email: null,
    display_name: "学生十八",
    role: "student",
    initial_password_pending: false,
  },
};

function renderHome() {
  return render(<MemoryRouter initialEntries={["/workspace"]}><Home /></MemoryRouter>);
}

async function logoutAndLoginAsNextUser() {
  fireEvent.click(screen.getAllByRole("button", { name: /设置/ })[0]);
  fireEvent.click(await screen.findByRole("button", { name: /退出登录/ }));
  fireEvent.change(await screen.findByPlaceholderText("学号或邮箱"), { target: { value: "20260018" } });
  fireEvent.change(screen.getByPlaceholderText("密码"), { target: { value: "password-b" } });
  fireEvent.click(screen.getAllByRole("button", { name: "登录" })[0]);
  expect(await screen.findAllByText(NEXT_AUTH.user.display_name)).not.toHaveLength(0);
}

describe("Home Web authentication gate", () => {
  beforeEach(() => {
    localStorage.clear();
    startJobMock.mockReset();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      configs: [],
      active_index: 0,
    }), { status: 200, headers: { "Content-Type": "application/json" } })));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("opens the non-skippable login modal on the first unauthenticated Web visit", async () => {
    renderHome();

    expect(await screen.findByPlaceholderText("学号或邮箱")).toBeTruthy();
    expect(screen.queryByText(/暂不登录/)).toBeNull();
  });

  it("clears in-memory authentication and reopens login after any 401 notification", async () => {
    saveAuth(AUTH);
    renderHome();
    expect(await screen.findAllByText("学生十七")).not.toHaveLength(0);

    vi.mocked(fetch).mockResolvedValueOnce(new Response(null, { status: 401 }));
    await protectedFetch("/protected", {}, AUTH.token);

    await waitFor(() => expect(loadAuth()).toBeNull());
    expect(await screen.findByPlaceholderText("学号或邮箱")).toBeTruthy();
    expect(screen.queryByText("学生十七")).toBeNull();
  });

  it("uses the AuthState bearer token when creating a pipeline job", async () => {
    saveAuth(AUTH);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/v2/settings")) return new Response(JSON.stringify({
        configs: [{
          name: "默认配置",
          api_url: "https://llm.example/v1",
          model_name: "model",
          api_key: "key",
          embedding_url: "https://embed.example/v1",
          embedding_model: "embedding",
          embedding_api_key: "embed-key",
        }],
        active_index: 0,
      }), { status: 200, headers: { "Content-Type": "application/json" } });
      if (url.includes("/api/v2/ocr/recovery")) return new Response(JSON.stringify({ jobs: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
      if (url.endsWith("/api/v2/jobs")) return new Response(JSON.stringify({ job_id: "job-created" }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      });
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderHome();

    const textarea = await screen.findByRole("textbox", { name: "" });
    // 页面还有模型字段；正文输入框是唯一 textarea。
    const sourceInput = textarea.tagName === "TEXTAREA"
      ? textarea
      : document.querySelector("textarea")!;
    sourceInput.dispatchEvent(new Event("focus"));
    sourceInput.textContent = "# Definition\nA group is ...";
    sourceInput.dispatchEvent(new Event("input", { bubbles: true }));
    fireEvent.change(sourceInput, { target: { value: "# Definition\nA group is ..." } });
    fireEvent.click(await screen.findByRole("button", { name: "开始分析" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/v2/jobs"))).toBe(true));
    const [, init] = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/v2/jobs"))!;
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer home-token");
  });

  it("uses the AuthState bearer token for agent-import", async () => {
    saveAuth(AUTH);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/v2/settings")) return new Response(JSON.stringify({ configs: [], active_index: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
      if (url.includes("/api/v2/ocr/recovery")) return new Response(JSON.stringify({ jobs: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
      if (url.endsWith("/api/v2/agent-import")) return new Promise<Response>(() => undefined);
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const view = renderHome();

    fireEvent.click(await screen.findByRole("tab", { name: /导入已有图谱/ }));
    const inputs = view.container.querySelectorAll<HTMLInputElement>('.mg-import-card input[type="file"]');
    fireEvent.change(inputs[0], { target: { files: [new File(["[]"], "nodes.json", { type: "application/json" })] } });
    fireEvent.change(inputs[1], { target: { files: [new File(["[]"], "edges.json", { type: "application/json" })] } });
    fireEvent.click(screen.getByRole("button", { name: "导入图谱" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/v2/agent-import"))).toBe(true));
    const [, init] = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/v2/agent-import"))!;
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer home-token");
  });

  it("does not retain account A API keys in memory after account B logs in", async () => {
    saveAuth(AUTH);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/v2/ocr/recovery")) return new Response(JSON.stringify({ jobs: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
      if (url.endsWith("/api/v2/settings")) {
        const token = new Headers(init?.headers).get("Authorization");
        const configs = token === "Bearer home-token" ? [{
          name: "A 配置",
          api_url: "https://a.example/v1",
          model_name: "a-model",
          api_key: "account-a-ui-secret",
          embedding_url: "https://a.example/embed",
          embedding_model: "a-embed",
          embedding_api_key: "account-a-ui-embed-secret",
        }] : [];
        return new Response(JSON.stringify({ configs, active_index: 0 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.endsWith("/api/v2/auth/logout")) return new Response(null, { status: 204 });
      if (url.endsWith("/api/v2/auth/login")) return new Response(JSON.stringify(NEXT_AUTH), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderHome();

    expect(await screen.findAllByText("学生十七")).not.toHaveLength(0);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => (
      String(url).endsWith("/api/v2/settings")
      && new Headers(init?.headers).get("Authorization") === "Bearer home-token"
    ))).toBe(true));
    fireEvent.click(screen.getAllByRole("button", { name: /设置/ })[0]);
    fireEvent.click(await screen.findByRole("button", { name: /退出登录/ }));
    fireEvent.click((await screen.findAllByRole("button", { name: "登录" }))[0]);
    fireEvent.change(screen.getByPlaceholderText("学号或邮箱"), { target: { value: "20260018" } });
    fireEvent.change(screen.getByPlaceholderText("密码"), { target: { value: "password-b" } });
    fireEvent.click(screen.getAllByRole("button", { name: "登录" })[0]);

    expect(await screen.findAllByText("学生十八")).not.toHaveLength(0);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => (
      String(url).endsWith("/api/v2/settings")
      && new Headers(init?.headers).get("Authorization") === "Bearer next-home-token"
    ))).toBe(true));
    // 空配置会自动展开；仅在仍折叠时点击，避免测试把已展开面板反向关闭。
    if (!screen.queryByPlaceholderText("sk-…")) {
      fireEvent.click(screen.getByText("LLM 配置").closest("button")!);
    }

    expect((await screen.findByPlaceholderText("sk-…") as HTMLInputElement).value).toBe("");
    expect((screen.getByPlaceholderText("默认使用 LLM API Key") as HTMLInputElement).value).toBe("");
    expect(Object.values(localStorage).join(" ")).not.toContain("account-a-ui-secret");
  });

  it("remounts both upload screens so account B cannot see account A drafts or files", async () => {
    saveAuth(AUTH);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v2/settings")) return new Response(JSON.stringify({ configs: [], active_index: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
      if (url.includes("/api/v2/ocr/recovery")) return new Response(JSON.stringify({ jobs: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
      if (url.endsWith("/api/v2/auth/logout")) return new Response(null, { status: 204 });
      if (url.endsWith("/api/v2/auth/login")) return new Response(JSON.stringify(NEXT_AUTH), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
      return new Response(null, { status: 404 });
    }));
    const view = renderHome();

    expect(await screen.findAllByText(AUTH.user.display_name)).not.toHaveLength(0);
    const sourceInput = view.container.querySelector<HTMLTextAreaElement>("textarea")!;
    fireEvent.change(sourceInput, { target: { value: "account A private draft" } });
    const generateFile = view.container.querySelector<HTMLInputElement>('.mg-upload-screen input[type="file"]')!;
    fireEvent.change(generateFile, {
      target: { files: [new File(["private"], "account-a-private.md", { type: "text/markdown" })] },
    });

    fireEvent.click(screen.getByRole("tab", { name: /导入已有图谱/ }));
    const importFiles = view.container.querySelectorAll<HTMLInputElement>('.mg-import-card input[type="file"]');
    fireEvent.change(importFiles[0], { target: { files: [new File(["[]"], "account-a-nodes.json")] } });
    fireEvent.change(importFiles[1], { target: { files: [new File(["[]"], "account-a-edges.json")] } });
    fireEvent.change(importFiles[2], { target: { files: [new File(["private"], "account-a-source.md")] } });
    expect(screen.getByText("account-a-source.md")).toBeTruthy();

    fireEvent.click(screen.getAllByRole("button", { name: /设置/ })[0]);
    fireEvent.click(await screen.findByRole("button", { name: /退出登录/ }));
    fireEvent.change(await screen.findByPlaceholderText("学号或邮箱"), { target: { value: "20260018" } });
    fireEvent.change(screen.getByPlaceholderText("密码"), { target: { value: "password-b" } });
    fireEvent.click(screen.getAllByRole("button", { name: "登录" })[0]);

    expect(await screen.findAllByText(NEXT_AUTH.user.display_name)).not.toHaveLength(0);
    expect((view.container.querySelector("textarea") as HTMLTextAreaElement).value).toBe("");
    expect(screen.queryByText("account-a-private.md")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: /导入已有图谱/ }));
    expect(screen.queryByText("account-a-nodes.json")).toBeNull();
    expect(screen.queryByText("account-a-edges.json")).toBeNull();
    expect(screen.queryByText("account-a-source.md")).toBeNull();
  });

  it("aborts an in-flight OCR upload when authentication is cleared", async () => {
    saveAuth(AUTH);
    const xhrAbort = vi.fn();
    vi.stubGlobal("XMLHttpRequest", class {
      upload: { onprogress?: (event: ProgressEvent) => void } = {};
      responseType = "";
      response: unknown = null;
      status = 0;
      onerror: (() => void) | null = null;
      onabort: (() => void) | null = null;
      onload: (() => void) | null = null;
      open() {}
      send() {}
      abort() { xhrAbort(); }
    });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v2/settings")) return new Response(JSON.stringify({ configs: [], active_index: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
      if (url.includes("/api/v2/ocr/recovery")) return new Response(JSON.stringify({ jobs: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
      if (url.endsWith("/api/v2/auth/logout")) return new Response(null, { status: 204 });
      return new Response(null, { status: 404 });
    }));
    const view = renderHome();

    expect(await screen.findAllByText(AUTH.user.display_name)).not.toHaveLength(0);
    const generateFile = view.container.querySelector<HTMLInputElement>('.mg-upload-screen input[type="file"]')!;
    fireEvent.change(generateFile, {
      target: { files: [new File(["pdf"], "account-a-private.pdf", { type: "application/pdf" })] },
    });
    fireEvent.click(screen.getAllByRole("button", { name: /设置/ })[0]);
    fireEvent.click(await screen.findByRole("button", { name: /退出登录/ }));

    await waitFor(() => expect(xhrAbort).toHaveBeenCalledTimes(1));
  });

  it("ignores account A settings that resolve after account B has logged in", async () => {
    saveAuth(AUTH);
    let resolveASettings!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const token = new Headers(init?.headers).get("Authorization");
      if (url.endsWith("/api/v2/settings") && token === "Bearer home-token") {
        return new Promise<Response>((resolve) => { resolveASettings = resolve; });
      }
      if (url.endsWith("/api/v2/settings")) return new Response(JSON.stringify({ configs: [], active_index: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
      if (url.includes("/api/v2/ocr/recovery")) return new Response(JSON.stringify({ jobs: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
      if (url.endsWith("/api/v2/auth/logout")) return new Response(null, { status: 204 });
      if (url.endsWith("/api/v2/auth/login")) return new Response(JSON.stringify(NEXT_AUTH), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
      return new Response(null, { status: 404 });
    }));
    renderHome();

    expect(await screen.findAllByText(AUTH.user.display_name)).not.toHaveLength(0);
    await logoutAndLoginAsNextUser();
    await act(async () => {
      resolveASettings(new Response(JSON.stringify({
        configs: [{
          name: "late A",
          api_url: "https://a.example/v1",
          model_name: "a-model",
          api_key: "late-account-a-secret",
          embedding_url: "",
          embedding_model: "a-embed",
          embedding_api_key: "",
        }],
        active_index: 0,
      }), { status: 200, headers: { "Content-Type": "application/json" } }));
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });

    if (!screen.queryByPlaceholderText("sk-…")) {
      fireEvent.click(screen.getByText("LLM 配置").closest("button")!);
    }
    await waitFor(() => expect((screen.getByPlaceholderText("sk-…") as HTMLInputElement).value).toBe(""));
  });

  it("ignores account A job and import successes that resolve after account B login", async () => {
    saveAuth(AUTH);
    let resolveJob!: (response: Response) => void;
    let resolveImport!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v2/settings")) return new Response(JSON.stringify({
        configs: [{
          name: "ready",
          api_url: "https://llm.example/v1",
          model_name: "model",
          api_key: "key",
          embedding_url: "",
          embedding_model: "embed",
          embedding_api_key: "",
        }],
        active_index: 0,
      }), { status: 200, headers: { "Content-Type": "application/json" } });
      if (url.includes("/api/v2/ocr/recovery")) return new Response(JSON.stringify({ jobs: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
      if (url.endsWith("/api/v2/jobs")) return new Promise<Response>((resolve) => { resolveJob = resolve; });
      if (url.endsWith("/api/v2/agent-import")) return new Promise<Response>((resolve) => { resolveImport = resolve; });
      if (url.endsWith("/api/v2/auth/logout")) return new Response(null, { status: 204 });
      if (url.endsWith("/api/v2/auth/login")) return new Response(JSON.stringify(NEXT_AUTH), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
      return new Response(null, { status: 404 });
    }));
    const view = renderHome();

    expect(await screen.findAllByText(AUTH.user.display_name)).not.toHaveLength(0);
    const sourceInput = view.container.querySelector<HTMLTextAreaElement>("textarea")!;
    fireEvent.change(sourceInput, { target: { value: "account A source" } });
    fireEvent.click(screen.getByRole("button", { name: "开始分析" }));
    await waitFor(() => expect(resolveJob).toBeTypeOf("function"));

    fireEvent.click(screen.getByRole("tab", { name: /导入已有图谱/ }));
    const importFiles = view.container.querySelectorAll<HTMLInputElement>('.mg-import-card input[type="file"]');
    fireEvent.change(importFiles[0], { target: { files: [new File(["[]"], "a-nodes.json")] } });
    fireEvent.change(importFiles[1], { target: { files: [new File(["[]"], "a-edges.json")] } });
    fireEvent.click(screen.getByRole("button", { name: "导入图谱" }));
    await waitFor(() => expect(resolveImport).toBeTypeOf("function"));

    await logoutAndLoginAsNextUser();
    await act(async () => {
      resolveJob(new Response(JSON.stringify({ job_id: "late-a-job" }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      }));
      resolveImport(new Response(JSON.stringify({
        job_id: "late-a-import",
        filename: "account-a-imported",
        result: { nodes: [], edges: [] },
      }), { status: 200, headers: { "Content-Type": "application/json" } }));
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });

    await waitFor(() => expect(startJobMock).not.toHaveBeenCalled());
    fireEvent.click(screen.getByRole("tab", { name: /导入已有图谱/ }));
    expect(view.container.querySelector(".mg-import-card")).toBeTruthy();
    expect(screen.queryByText("account-a-imported")).toBeNull();
  });
});

