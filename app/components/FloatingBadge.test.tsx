import React from "react";
import { describe, expect, it, vi } from "vitest";
import { JobDetailPanel } from "./FloatingBadge";
import type { BackgroundJob } from "../context/jobs";

function findButton(node: React.ReactNode, label: string): React.ReactElement<{ onClick: () => void }> | null {
  for (const child of React.Children.toArray(node)) {
    if (!React.isValidElement(child)) continue;
    const props = child.props as { children?: React.ReactNode; onClick?: () => void };
    if (child.type === "button" && props.children === label && props.onClick) {
      return child as React.ReactElement<{ onClick: () => void }>;
    }
    const nested = findButton(props.children, label);
    if (nested) return nested;
  }
  return null;
}

describe("failed background job actions", () => {
  it("clears the failed job when Close is clicked", () => {
    const job: BackgroundJob = {
      id: "failed-job",
      filename: "input.md",
      phase: "error",
      stage: "ensure_coverage",
      stageLabel: "遗漏知识补全",
      stagesDone: ["correct_text"],
      totalStages: 14,
      pct: 14,
      errorCode: "model_response",
      errorTitle: "模型返回内容无法解析",
      errorMsg: "请重试",
      result: null,
      sourceMarkdown: "source",
      pendingAction: null,
    };
    const onDismiss = vi.fn();
    const tree = JobDetailPanel({
      job,
      onViewResult: vi.fn(),
      onDismiss,
      onPause: vi.fn(),
      onResume: vi.fn(),
      onCancel: vi.fn(),
    });

    const closeButton = findButton(tree, "关闭");
    expect(closeButton).not.toBeNull();
    closeButton?.props.onClick();
    expect(onDismiss).toHaveBeenCalledWith("failed-job");
  });
});
