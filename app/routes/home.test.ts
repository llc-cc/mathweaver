import { describe, expect, it } from "vitest";
import { canUseAutonomousWorkspace } from "./home";

describe("workspace role access", () => {
  it("keeps autonomous graph creation available to teachers and guests", () => {
    expect(canUseAutonomousWorkspace("teacher")).toBe(true);
    expect(canUseAutonomousWorkspace(null)).toBe(true);
    expect(canUseAutonomousWorkspace(undefined)).toBe(true);
  });

  it("blocks autonomous graph creation for students", () => {
    expect(canUseAutonomousWorkspace("student")).toBe(false);
  });
});
