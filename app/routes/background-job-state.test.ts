import { describe, expect, it } from "vitest";
import { isRetriedJobError } from "./home";

describe("background retry page state", () => {
  it("clears the matching homepage error after a sidebar retry starts", () => {
    expect(isRetriedJobError(
      { view: "error", jobId: "failed-job" },
      { id: "failed-job", phase: "running" },
    )).toBe(true);
  });

  it("does not clear another job or an error that has not restarted", () => {
    expect(isRetriedJobError(
      { view: "error", jobId: "other-job" },
      { id: "failed-job", phase: "running" },
    )).toBe(false);
    expect(isRetriedJobError(
      { view: "error", jobId: "failed-job" },
      { id: "failed-job", phase: "error" },
    )).toBe(false);
  });
});
