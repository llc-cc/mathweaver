// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthState } from "~/auth-model";
import { clearAuthAndNotify, saveAuth } from "~/routes/auth";
import { JobsProvider, useJobs } from "./jobs";

vi.mock("~/api", () => ({ apiUrl: (path: string) => path }));

const AUTH: AuthState = {
  token: "jobs-token",
  user: {
    id: 11,
    student_no: "20260011",
    email: null,
    display_name: "学生十一",
    role: "student",
    initial_password_pending: false,
  },
};

const NEXT_AUTH: AuthState = {
  ...AUTH,
  token: "next-jobs-token",
  user: { ...AUTH.user, id: 12, student_no: "20260012" },
};

function Harness() {
  const { jobs, startJob, pauseJob, resumeJob, cancelJob } = useJobs();
  return <>
    <button onClick={() => startJob("job-1", "input.md", "source")}>start</button>
    <button onClick={() => void pauseJob("job-1")}>pause</button>
    <button onClick={() => void resumeJob("job-1")}>resume</button>
    <button onClick={() => void cancelJob("job-1")}>cancel</button>
    <span>{Object.keys(jobs).length}</span>
  </>;
}

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("JobsProvider protected request contract", () => {
  beforeEach(() => {
    localStorage.clear();
    saveAuth(AUTH);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("uses the AuthState bearer token for status and result requests", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ status: "done", stages_done: [], total_stages: 1 }))
      .mockResolvedValueOnce(response({ nodes: [], edges: [] }));
    vi.stubGlobal("fetch", fetchMock);
    render(<JobsProvider><Harness /></JobsProvider>);

    fireEvent.click(screen.getByRole("button", { name: "start" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    for (const [, init] of fetchMock.mock.calls) {
      expect(new Headers(init.headers).get("Authorization")).toBe("Bearer jobs-token");
    }
  });

  it.each([
    ["pause", "paused"],
    ["resume", "running"],
    ["cancel", "cancelled"],
  ])("uses the AuthState bearer token for %s", async (action, status) => {
    const fetchMock = vi.fn().mockResolvedValue(response({ status }));
    vi.stubGlobal("fetch", fetchMock);
    render(<JobsProvider><Harness /></JobsProvider>);

    fireEvent.click(screen.getByRole("button", { name: action }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(new Headers(fetchMock.mock.calls[0][1].headers).get("Authorization")).toBe("Bearer jobs-token");
  });

  it("clears in-memory jobs when any protected request returns 401", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);
    render(<JobsProvider><Harness /></JobsProvider>);

    fireEvent.click(screen.getByRole("button", { name: "start" }));
    expect(await screen.findByText("1")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("0")).toBeTruthy());
  });

  it("clears in-memory jobs on an explicit logout notification", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ status: "running" })));
    render(<JobsProvider><Harness /></JobsProvider>);

    fireEvent.click(screen.getByRole("button", { name: "start" }));
    expect(await screen.findByText("1")).toBeTruthy();

    clearAuthAndNotify();

    await waitFor(() => expect(screen.getByText("0")).toBeTruthy());
  });

  it("ignores a status success from account A after account B logs in", async () => {
    let resolveStatus!: (response: Response) => void;
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => { resolveStatus = resolve; }));
    vi.stubGlobal("fetch", fetchMock);
    render(<JobsProvider><Harness /></JobsProvider>);

    fireEvent.click(screen.getByRole("button", { name: "start" }));
    expect(await screen.findByText("1")).toBeTruthy();
    clearAuthAndNotify();
    saveAuth(NEXT_AUTH);
    await act(async () => {
      resolveStatus(response({ status: "done", stages_done: [], total_stages: 1 }));
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });

    expect(screen.getByText("0")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

