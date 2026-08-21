// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthModal } from "./AuthModal";

vi.mock("./home", () => ({ Logo: () => <div>MathWeaver</div> }));
vi.mock("~/api", () => ({ apiUrl: (path: string) => path }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AuthModal", () => {
  it("offers one student-number-or-email login field and no registration entry", () => {
    render(<AuthModal onAuth={vi.fn()} onSkip={vi.fn()} />);

    expect(screen.getByPlaceholderText("学号或邮箱")).toBeTruthy();
    expect(screen.queryByText("注册")).toBeNull();
    expect(screen.queryByText("创建账号")).toBeNull();
    expect(screen.queryByText(/暂不登录/)).toBeNull();
  });

  it("posts identifier and password and returns the complete auth state", async () => {
    const auth = {
      token: "new-token",
      user: {
        id: 8,
        student_no: "20260008",
        email: null,
        display_name: "学生八",
        role: "student" as const,
        initial_password_pending: true,
      },
    };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(auth), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const onAuth = vi.fn();
    render(<AuthModal onAuth={onAuth} onSkip={vi.fn()} />);

    fireEvent.change(screen.getByPlaceholderText("学号或邮箱"), { target: { value: "20260008" } });
    fireEvent.change(screen.getByPlaceholderText("密码"), { target: { value: "Init-1234" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    await waitFor(() => expect(onAuth).toHaveBeenCalledWith(auth));
    const [, request] = fetchMock.mock.calls[0];
    expect(JSON.parse(request.body)).toEqual({ identifier: "20260008", password: "Init-1234" });
  });

  it("keeps the legacy Electron email contract and normalizes its response", async () => {
    vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue("MathWeaverDesktop/1.0");
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      token: "desktop-token",
      email: "legacy@example.edu",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const onAuth = vi.fn();
    render(<AuthModal onAuth={onAuth} onSkip={vi.fn()} />);

    expect(screen.getByText(/暂不登录/)).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText("学号或邮箱"), { target: { value: "legacy@example.edu" } });
    fireEvent.change(screen.getByPlaceholderText("密码"), { target: { value: "desktop-pass" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    await waitFor(() => expect(onAuth).toHaveBeenCalledWith({
      token: "desktop-token",
      user: {
        id: 0,
        student_no: null,
        email: "legacy@example.edu",
        display_name: "legacy@example.edu",
        role: "student",
        initial_password_pending: false,
      },
    }));
    const [, request] = fetchMock.mock.calls[0];
    expect(JSON.parse(request.body)).toEqual({ email: "legacy@example.edu", password: "desktop-pass" });
  });
});

