import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { DirectChallengeSubmitConfirm } from "./DirectChallengeMap";

describe("direct challenge submit confirmation", () => {
  it("renders an in-app confirmation dialog instead of a native browser prompt", () => {
    const markup = renderToStaticMarkup(
      <DirectChallengeSubmitConfirm questionCount={3} onCancel={vi.fn()} onConfirm={vi.fn()} />,
    );

    expect(markup).toContain('role="alertdialog"');
    expect(markup).toContain('aria-modal="true"');
    expect(markup).toContain("确认提交作业？");
    expect(markup).toContain("已完成全部 3 道题");
    expect(markup).toContain("取消");
    expect(markup).toContain("确认提交");
  });
});
