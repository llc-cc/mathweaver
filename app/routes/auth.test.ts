import { beforeEach, describe, expect, it } from "vitest";
import { clearAuth, loadAuth, saveAuth } from "./auth";


class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>();
  get length() { return this.values.size; }
  clear() { this.values.clear(); }
  getItem(key: string) { return this.values.get(key) ?? null; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  removeItem(key: string) { this.values.delete(key); }
  setItem(key: string, value: string) { this.values.set(key, String(value)); }
}


describe("auth mysql contract", () => {
  beforeEach(() => {
    const localStorage = new MemoryStorage();
    Object.assign(globalThis, { window: globalThis, localStorage });
  });

  it("persists the server canTeach capability independently from the selected role", () => {
    saveAuth("token", "Teacher@Example.com", "student", true);

    expect(loadAuth()).toEqual({
      token: "token",
      email: "Teacher@Example.com",
      educationRole: "student",
      canTeach: true,
    });

    saveAuth("token", "Teacher@Example.com", "student", false);
    expect(loadAuth()?.canTeach).toBe(false);
  });

  it("clears capability and bearer state together", () => {
    saveAuth("token", "teacher@example.com", "teacher", true);
    clearAuth();

    expect(loadAuth()).toBeNull();
  });
});
