import { describe, expect, it } from "vitest";
import { createDirectQuestionDrafts, directQuestionDraftsForAssignment, equalDirectQuestionScores, normalizeDirectQuestionDraftScores, rebalanceDirectQuestionDrafts, splitDirectQuestions } from "./education-direct";

describe("direct assignment question import", () => {
  it("splits numbered Chinese questions deterministically", () => {
    expect(splitDirectQuestions("第 1 题\n求 x。\n第 2 题\n证明结论。"))
      .toEqual(["第 1 题\n求 x。", "第 2 题\n证明结论。"]);
  });

  it("accepts punctuation after Chinese question markers", () => {
    expect(splitDirectQuestions("第1题：求 x。\n第2题：证明结论。"))
      .toEqual(["第1题：求 x。", "第2题：证明结论。"]);
  });

  it("supports numeric, bracketed, and heading blocks", () => {
    expect(splitDirectQuestions("1. 计算\n答案要求\n(2) 证明\n过程\n# 3 讨论\n说明"))
      .toEqual(["1. 计算\n答案要求", "(2) 证明\n过程", "# 3 讨论\n说明"]);
  });

  it("falls back to one question when no reliable boundary exists", () => {
    expect(splitDirectQuestions("请完成下面的证明。\n需要写出完整过程。"))
      .toEqual(["请完成下面的证明。\n需要写出完整过程。"]);
    expect(splitDirectQuestions("")).toEqual([]);
  });

  it("allocates scores to exactly 100", () => {
    for (const count of [1, 2, 3, 6, 7]) {
      const scores = equalDirectQuestionScores(count);
      expect(scores).toHaveLength(count);
      expect(scores.reduce((sum, score) => sum + score, 0)).toBeCloseTo(100, 6);
    }
  });

  it("projects sequential direct assignments with stable node and question ids", () => {
    const projected = directQuestionDraftsForAssignment({
      directStructureVersion: 1,
      targetNodeId: 12,
      path: { steps: [{ nodeId: 12, order: 2 }, { nodeId: 11, order: 1 }] },
      assessments: [
        { nodeId: 11, questions: [{ id: "q-11", nodeId: 11, order: 1, question: "第一题", kind: "proof", focus: "定义", referenceAnswer: "答一", expectedPoints: ["点一"], maxScore: 40 }] },
        { nodeId: 12, questions: [{ id: "q-12", nodeId: 12, order: 1, question: "第二题", kind: "calculation", focus: "计算", referenceAnswer: "答二", expectedPoints: ["点二"], maxScore: 60 }] },
      ],
    });
    expect(projected.map(item => ({ nodeId: item.nodeId, id: item.id, order: item.order, question: item.question }))).toEqual([
      { nodeId: 11, id: "q-11", order: 1, question: "第一题" },
      { nodeId: 12, id: "q-12", order: 2, question: "第二题" },
    ]);
  });

  it("flattens legacy single-container questions in stored order", () => {
    const projected = directQuestionDraftsForAssignment({
      directStructureVersion: 0,
      targetNodeId: 99,
      path: { steps: [{ nodeId: 99, order: 1 }] },
      assessments: [{
        nodeId: 99,
        questions: [
          { id: "legacy-1", nodeId: 99, order: 1, question: "第一题", focus: "", kind: "direct", referenceAnswer: "", expectedPoints: [], maxScore: 50 },
          { id: "legacy-2", nodeId: 99, order: 2, question: "第二题", focus: "", kind: "direct", referenceAnswer: "", expectedPoints: [], maxScore: 50 },
        ],
      }],
    });
    expect(projected.map(item => item.id)).toEqual(["legacy-1", "legacy-2"]);
  });

  it("preserves score proportions while projecting drafts to 100", () => {
    const drafts = createDirectQuestionDrafts("第 1 题\n甲\n第 2 题\n乙");
    const normalized = normalizeDirectQuestionDraftScores([
      { ...drafts[0], maxScore: 1 },
      { ...drafts[1], maxScore: 3 },
    ]);
    expect(normalized.map(item => item.maxScore)).toEqual([25, 75]);
  });

  it("renumbers and rebalances edited questions", () => {
    const drafts = createDirectQuestionDrafts("第 1 题\n甲\n第 2 题\n乙");
    const edited = rebalanceDirectQuestionDrafts([
      { ...drafts[1], question: "乙" },
      { ...drafts[0], question: "甲" },
      { ...drafts[0], id: "new", question: "丙" },
    ]);
    expect(edited.map(item => item.order)).toEqual([1, 2, 3]);
    expect(edited.reduce((sum, item) => sum + item.maxScore, 0)).toBeCloseTo(100, 6);
  });
});
