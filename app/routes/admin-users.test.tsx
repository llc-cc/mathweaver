// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router";
import { renderToString } from "react-dom/server";
import type { AuthState } from "../auth-model";
import AdminUsers, { AdminUsersLink, credentialsCsv } from "./AdminUsers";
import { protectedFetch, saveAuth } from "./auth";

const ADMIN: AuthState = {
  token: "admin-token",
  user: {
    id: 1,
    student_no: null,
    email: "admin@example.edu",
    display_name: "管理员",
    role: "admin",
    initial_password_pending: false,
  },
};

const STUDENT: AuthState = {
  token: "student-token",
  user: {
    id: 2,
    student_no: "20260002",
    email: null,
    display_name: "学生",
    role: "student",
    initial_password_pending: false,
  },
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/admin/users"]}>
      <Routes>
        <Route path="/admin/users" element={<AdminUsers />} />
        <Route path="/workspace" element={<div>工作区</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("AdminUsers", () => {
  it("keeps the SSR first render hydration-safe before browser auth is available", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

    const html = renderToString(
      <MemoryRouter initialEntries={["/admin/users"]}>
        <AdminUsers />
      </MemoryRouter>,
    );

    expect(html).toBe("");
    expect(consoleError.mock.calls.flat().join(" ")).not.toContain("Navigate must not be used on the initial render");
  });

  it("redirects non-administrators to the workspace", async () => {
    saveAuth(STUDENT);
    renderPage();

    expect(await screen.findByText("工作区")).toBeTruthy();
  });

  it("shows the user-management entry only to administrators", () => {
    const { rerender } = render(<MemoryRouter><AdminUsersLink auth={STUDENT} /></MemoryRouter>);
    expect(screen.queryByText("用户管理")).toBeNull();

    rerender(<MemoryRouter><AdminUsersLink auth={ADMIN} /></MemoryRouter>);
    expect(screen.getByText("用户管理")).toBeTruthy();
  });

  it("uploads CSV files and displays validation error line numbers", async () => {
    saveAuth(ADMIN);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      created: 0,
      generated_credentials: [],
      errors: [{ line: 3, field: "student_no", message: "学号重复" }],
    }, 400)));
    renderPage();

    const file = new File(["student_no,display_name\n001,甲"], "students.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("学生 CSV"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "导入学生" }));

    expect(await screen.findByText(/第 3 行/)).toBeTruthy();
    expect(screen.getByText(/学号重复/)).toBeTruthy();
  });

  it("keeps generated credentials in memory and revokes the download URL on leave", async () => {
    saveAuth(ADMIN);
    const createObjectURL = vi.fn().mockReturnValue("blob:credentials");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      created: 1,
      generated_credentials: [{ student_no: "0001", initial_password: "Temp-1234" }],
      errors: [],
    })));
    const view = renderPage();

    fireEvent.change(screen.getByLabelText("学生 CSV"), {
      target: { files: [new File(["student_no,display_name\n0001,甲"], "students.csv", { type: "text/csv" })] },
    });
    fireEvent.click(screen.getByRole("button", { name: "导入学生" }));

    expect(await screen.findByText("Temp-1234")).toBeTruthy();
    expect(screen.getByRole("link", { name: "下载一次性凭据" }).getAttribute("href")).toBe("blob:credentials");
    expect(Object.values(localStorage).join(" ")).not.toContain("Temp-1234");

    view.unmount();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:credentials");
  });

  it("requires confirmation before resetting a password and shows it only in the current UI", async () => {
    saveAuth(ADMIN);
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ temporary_password: "Reset-5678" }));
    vi.stubGlobal("fetch", fetchMock);
    const confirmMock = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    renderPage();

    fireEvent.change(screen.getByLabelText("用户 ID"), { target: { value: "42" } });
    fireEvent.click(screen.getByRole("button", { name: "重置密码" }));
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "重置密码" }));
    expect(await screen.findByText("Reset-5678")).toBeTruthy();
    expect(confirmMock).toHaveBeenCalledTimes(2);
    expect(Object.values(localStorage).join(" ")).not.toContain("Reset-5678");
  });

  it("updates account status through the existing API", async () => {
    saveAuth(ADMIN);
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ user: { id: 42, is_active: false } }));
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    fireEvent.change(screen.getByLabelText("用户 ID"), { target: { value: "42" } });
    fireEvent.click(screen.getByRole("button", { name: "停用账号" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, request] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/v2/admin/users/42/status");
    expect(request.method).toBe("PATCH");
    expect(JSON.parse(request.body)).toEqual({ is_active: false });
  });

  it("shows a permission error for import 403 instead of treating it as a network failure", async () => {
    saveAuth(ADMIN);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ error: "forbidden" }, 403)));
    renderPage();

    fireEvent.change(screen.getByLabelText("学生 CSV"), {
      target: { files: [new File(["student_no\n001"], "students.csv", { type: "text/csv" })] },
    });
    fireEvent.click(screen.getByRole("button", { name: "导入学生" }));

    expect((await screen.findByRole("alert")).textContent).toContain("权限不足");
    expect(screen.queryByText("无法连接到后端")).toBeNull();
  });

  it("rejects a malformed successful import response without reading missing arrays", async () => {
    saveAuth(ADMIN);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ created: 1 })));
    renderPage();

    fireEvent.change(screen.getByLabelText("学生 CSV"), {
      target: { files: [new File(["student_no\n001"], "students.csv", { type: "text/csv" })] },
    });
    fireEvent.click(screen.getByRole("button", { name: "导入学生" }));

    expect((await screen.findByRole("alert")).textContent).toContain("导入响应格式错误");
    expect(screen.queryByText("无法连接到后端")).toBeNull();
  });

  it("rejects malformed credential rows before building a download", async () => {
    saveAuth(ADMIN);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      created: 1,
      generated_credentials: [{}],
      errors: [],
    })));
    renderPage();

    fireEvent.change(screen.getByLabelText("学生 CSV"), {
      target: { files: [new File(["student_no\n001"], "students.csv", { type: "text/csv" })] },
    });
    fireEvent.click(screen.getByRole("button", { name: "导入学生" }));

    expect((await screen.findByRole("alert")).textContent).toContain("导入响应格式错误");
  });

  it("neutralizes spreadsheet formulas in every downloaded CSV cell", () => {
    const csv = credentialsCsv([
      { student_no: "=HYPERLINK(\"https://evil\")", initial_password: "+SUM(1,1)" },
      { student_no: "0001", initial_password: "@cmd" },
      { student_no: "-2", initial_password: "\t=cmd" },
      { student_no: "0002", initial_password: "\tcmd" },
    ]);

    expect(csv).toContain("\"'=HYPERLINK(\"\"https://evil\"\")\"");
    expect(csv).toContain("\"'+SUM(1,1)\"");
    expect(csv).toContain("\"0001\"");
    expect(csv).toContain("\"'@cmd\"");
    expect(csv).toContain("\"'-2\"");
    expect(csv).toContain("\"'\t=cmd\"");
    expect(csv).toContain("\"'\tcmd\"");
  });

  it("shows a clear permission error for password-reset 403", async () => {
    saveAuth(ADMIN);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ error: "forbidden" }, 403)));
    renderPage();

    fireEvent.change(screen.getByLabelText("用户 ID"), { target: { value: "42" } });
    fireEvent.click(screen.getByRole("button", { name: "重置密码" }));

    expect((await screen.findByRole("alert")).textContent).toContain("权限不足");
  });

  it("clears authentication and leaves the admin page after a 401", async () => {
    saveAuth(ADMIN);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ error: "unauthorized" }, 401)));
    renderPage();

    fireEvent.change(screen.getByLabelText("学生 CSV"), {
      target: { files: [new File(["student_no\n001"], "students.csv", { type: "text/csv" })] },
    });
    fireEvent.click(screen.getByRole("button", { name: "导入学生" }));

    expect(await screen.findByText("工作区")).toBeTruthy();
    expect(localStorage.getItem("mg_auth")).toBeNull();
  });

  it("clears one-time credentials and revokes their Blob URL on an external auth invalidation", async () => {
    saveAuth(ADMIN);
    const createObjectURL = vi.fn().mockReturnValue("blob:external-invalidation");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({
        created: 1,
        generated_credentials: [{ student_no: "0009", initial_password: "Import-Secret" }],
        errors: [],
      }))
      .mockResolvedValueOnce(jsonResponse({ temporary_password: "Reset-Secret" }))
      .mockResolvedValueOnce(jsonResponse({ error: "unauthorized" }, 401));
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    fireEvent.change(screen.getByLabelText("学生 CSV"), {
      target: { files: [new File(["student_no\n0009"], "students.csv", { type: "text/csv" })] },
    });
    fireEvent.click(screen.getByRole("button", { name: "导入学生" }));
    expect(await screen.findByText("Import-Secret")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("用户 ID"), { target: { value: "9" } });
    fireEvent.click(screen.getByRole("button", { name: "重置密码" }));
    expect(await screen.findByText("Reset-Secret")).toBeTruthy();

    await act(async () => { await protectedFetch("/another-protected-resource", {}, ADMIN.token); });

    expect(await screen.findByText("工作区")).toBeTruthy();
    expect(screen.queryByText("Import-Secret")).toBeNull();
    expect(screen.queryByText("Reset-Secret")).toBeNull();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:external-invalidation");
  });
});

