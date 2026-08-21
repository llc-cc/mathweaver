// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthState } from "../auth-model";
import { PasswordChangeModal } from "./PasswordChangeModal";

vi.mock("~/api", () => ({ apiUrl: (path: string) => path }));

const AUTH: AuthState = {
  token: "old-token",
  user: {
    id: 9,
    student_no: "20260009",
    email: null,
    display_name: "学生九",
    role: "student",
    initial_password_pending: true,
  },
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("PasswordChangeModal", () => {
  it("allows the initial-password hint to be dismissed without changing auth", () => {
    const onClose = vi.fn();
    const onAuth = vi.fn();
    render(<PasswordChangeModal auth={AUTH} requiredHint onAuth={onAuth} onClose={onClose} />);

    expect(screen.getByText("当前仍在使用初始密码，建议尽快修改。")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "暂不修改" }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onAuth).not.toHaveBeenCalled();
  });

  it("replaces token and user after a successful password change", async () => {
    const nextAuth: AuthState = {
      token: "replacement-token",
      user: { ...AUTH.user, initial_password_pending: false },
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(nextAuth), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })));
    const onAuth = vi.fn();
    render(<PasswordChangeModal auth={AUTH} requiredHint onAuth={onAuth} onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "现在修改" }));
    fireEvent.change(screen.getByLabelText("当前密码"), { target: { value: "Init-1234" } });
    fireEvent.change(screen.getByLabelText("新密码"), { target: { value: "Changed-5678" } });
    fireEvent.click(screen.getByRole("button", { name: "确认修改" }));

    await waitFor(() => expect(onAuth).toHaveBeenCalledWith(nextAuth));
  });
});

