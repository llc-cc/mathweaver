import { describe, expect, it } from "vitest";
import { assessmentAnswersComplete, assessmentGenerationErrorMessage, assessmentOperationCounts, assessmentOperationReducer, assessmentScoringSummary, assignmentGraphResult, educationErrorMessage, gradingAiSuggestionPatch, gradingOverviewActionLabel, gradingStandardIncomplete, groupCourseGraphs, learningCanvasEdges, learningEvidenceKindLabel, learningMasteryStateLabel, replaceNodeAssessment, snapshotGraphResult, studentAssignmentCompletion, unresolvedAssessmentNodeIds } from "./education";
import type { AssessmentOperationQueueState, EducationSnapshot } from "./education";
import type { EducationAssignment, LearningPath } from "./education";


const path: LearningPath = {
  targetNodeId: 3,
  summary: "先修后学",
  candidateNodeIds: [1, 2, 3],
  hasCycles: false,
  steps: [
    { nodeId: 1, order: 1, role: "prerequisite", required: true, rationale: "基础", state: "mastered" },
    { nodeId: 2, order: 2, role: "prerequisite", required: false, rationale: "中间", state: "not_started" },
    { nodeId: 3, order: 3, role: "target", required: true, rationale: "目标", state: "not_started" },
  ],
  edges: [
    { from: 1, to: 2, label: "前置" },
    { from: 2, to: 3, label: "前置" },
  ],
};


describe("education graph presentation", () => {
  it("maps learning evidence kinds to student-friendly labels", () => {
    expect(learningEvidenceKindLabel("goal")).toBe("学习目标");
    expect(learningEvidenceKindLabel("understanding")).toBe("已掌握");
    expect(learningEvidenceKindLabel("misconception")).toBe("理解偏差");
    expect(learningEvidenceKindLabel("gap")).toBe("知识缺口");
    expect(learningEvidenceKindLabel("used_node")).toBe("使用的知识");
    expect(learningEvidenceKindLabel("hint")).toBe("学习提示");
    expect(learningEvidenceKindLabel("unresolved_question")).toBe("待解决问题");
    expect(learningEvidenceKindLabel("strategy")).toBe("证明思路");
    expect(learningEvidenceKindLabel("unknown")).toBe("学习记录");
  });

  it("maps every mastery state to a student-friendly label", () => {
    expect(learningMasteryStateLabel("unknown")).toBe("尚无记录");
    expect(learningMasteryStateLabel("learning")).toBe("学习中");
    expect(learningMasteryStateLabel("mastered")).toBe("已掌握");
    expect(learningMasteryStateLabel("needs_review")).toBe("待复习");
  });

  it("groups historical snapshots by source graph without merging same-name revisions", () => {
    const grouped = groupCourseGraphs([
      { id: "new", classId: "c1", sourceGraphId: "chapter-1", filename: "chapter.tex", nodeCount: 3, edgeCount: 2, boundAssignmentCount: 2, createdAt: "2026-08-10T10:00:00" },
      { id: "old", classId: "c1", sourceGraphId: "chapter-1", filename: "chapter.tex", nodeCount: 3, edgeCount: 2, boundAssignmentCount: 1, createdAt: "2026-08-09T10:00:00" },
      { id: "revision", classId: "c1", sourceGraphId: "chapter-2", filename: "chapter.tex", nodeCount: 4, edgeCount: 3, createdAt: "2026-08-11T10:00:00" },
      { id: "legacy-a", classId: "c1", sourceGraphId: null, filename: "legacy.tex", nodeCount: 1, edgeCount: 0, createdAt: "2026-08-08T10:00:00" },
      { id: "legacy-b", classId: "c1", sourceGraphId: null, filename: "legacy.tex", nodeCount: 1, edgeCount: 0, createdAt: "2026-08-07T10:00:00" },
    ]);

    expect(grouped).toHaveLength(4);
    expect(grouped[0].id).toBe("old");
    expect(grouped[0].snapshotIds).toEqual(["new", "old"]);
    expect(grouped[0].boundAssignmentCount).toBe(3);
    expect(grouped.find(graph => graph.id === "revision")?.filename).toBe("chapter.tex");
    expect(grouped.filter(graph => graph.sourceGraphId == null)).toHaveLength(2);
  });

  it("puts prerequisite-first learning edges before dimmed graph edges", () => {
    const result = learningCanvasEdges(path, [
      { from: 2, to: 1, label: "依赖", description: "", strength: "" },
      { from: 3, to: 2, label: "依赖", description: "", strength: "" },
      { from: 3, to: 1, label: "相关", description: "", strength: "" },
    ]);

    expect(result.pathEdgeCount).toBe(2);
    expect(result.edges.slice(0, 2).map(edge => [edge.from, edge.to])).toEqual([[1, 2], [2, 3]]);
    expect(result.edges.map(edge => [edge.from, edge.to])).not.toContainEqual([2, 1]);
    expect(result.edges).toContainEqual(expect.objectContaining({ from: 3, to: 1, label: "相关" }));
  });

  it("does not highlight edges for steps removed from a teacher draft", () => {
    const trimmed = { ...path, steps: path.steps.filter(step => step.nodeId !== 2) };
    const result = learningCanvasEdges(trimmed, [
      { from: 2, to: 1, label: "渚濊禆", description: "", strength: "" },
      { from: 3, to: 2, label: "渚濆悗", description: "", strength: "" },
    ]);

    expect(result.pathEdgeCount).toBe(0);
    expect(result.edges).toHaveLength(2);
  });

  it("restores a frozen snapshot as a GraphStudio result", () => {
    const assignment = {
      id: "a1",
      classId: "c1",
      snapshotId: "s1",
      title: "作业",
      targetNodeId: 3,
      status: "published",
      summary: path.summary,
      version: 1,
      updatedAt: "2026-08-09T00:00:00",
      role: "student",
      path,
      assessments: [],
      snapshot: {
        id: "s1",
        classId: "c1",
        filename: "book.tex",
        nodeCount: 1,
        edgeCount: 0,
        nodes: [{ id: 3, node_type: "定理", title_zh: "目标", title_en: "Target", label: "", content: "", statement_form: "", subject: [], conditions: [], conclusions: [], proof: "" }],
        edges: [],
        sourceMarkdown: "frozen source",
        latexMacros: { RR: "\\mathbb{R}" },
        createdAt: "2026-08-09T00:00:00",
      },
    } satisfies EducationAssignment;

    const graph = assignmentGraphResult(assignment);

    expect(graph?.nodes[0].id).toBe(3);
    expect(graph?.latex_macros).toEqual({ RR: "\\mathbb{R}" });
    expect(graph?.source_mode).toBe("import");
  });

  it("restores a standalone course graph without a learning path", () => {
    const snapshot = {
      id: "s2",
      classId: "c1",
      filename: "chapter-2.tex",
      nodeCount: 2,
      edgeCount: 1,
      createdAt: "2026-08-09T00:00:00",
      nodes: [
        { id: 1, node_type: "瀹氱悊", title_zh: "鍩虹", title_en: "Base", label: "", content: "", statement_form: "", subject: [], conditions: [], conclusions: [], proof: "" },
        { id: 2, node_type: "瀹氱悊", title_zh: "鐩爣", title_en: "Target", label: "", content: "", statement_form: "", subject: [], conditions: [], conclusions: [], proof: "" },
      ],
      edges: [{ from: 2, to: 1, label: "渚濊禆", description: "", strength: "" }],
      sourceMarkdown: "chapter source",
      latexMacros: {},
    } satisfies EducationSnapshot;

    const graph = snapshotGraphResult(snapshot);

    expect(graph.nodes).toHaveLength(2);
    expect(graph.edges).toHaveLength(1);
    expect(graph.source_mode).toBe("import");
  });
});

describe("education action feedback", () => {
  it("maps role and invite errors to actionable Chinese feedback", () => {
    expect(educationErrorMessage({ code: "invalid_invite_code" })).toContain("邀请码无效");
    expect(educationErrorMessage({ code: "class_role_conflict" })).toContain("已经是此班级的教师");
    expect(educationErrorMessage({ code: "class_membership_removed" })).toContain("被移出");
    expect(educationErrorMessage({ code: "student_not_found" })).toContain("找不到");
    expect(educationErrorMessage({ code: "student_name_required" })).toContain("姓名");
    expect(educationErrorMessage({ code: "student_number_conflict" })).toContain("学号");
    expect(educationErrorMessage({ code: "student_profile_required" })).toContain("完善");
    expect(educationErrorMessage({ code: "education_role_forbidden" })).toContain("没有执行此操作的权限");
    expect(educationErrorMessage({ code: "assessment_draft_changed" })).toContain("其他窗口");
    expect(educationErrorMessage({ code: "assessment_invalid_result" })).toContain("结构校验");
  });

  it("identifies missing, pending, failed, and empty assessment nodes", () => {
    const assignment = {
      id: "a1",
      classId: "c1",
      snapshotId: "s1",
      title: "task",
      targetNodeId: 3,
      status: "draft" as const,
      summary: path.summary,
      version: 1,
      updatedAt: "2026-08-11T00:00:00",
      role: "teacher" as const,
      path,
      assessments: [
        { nodeId: 1, status: "ready" as const, questionCount: 4, updatedAt: "now", questions: [] },
        { nodeId: 2, status: "failed" as const, questionCount: 0, updatedAt: "now", questions: [] },
      ],
    } satisfies EducationAssignment;

    expect(unresolvedAssessmentNodeIds(assignment)).toEqual([2, 3]);
  });

  it("localizes current and legacy assessment configuration failures", () => {
    expect(assessmentGenerationErrorMessage({ generationErrorCode: "education_ai_unconfigured", generationError: "internal detail" })).toContain("尚未配置");
    expect(assessmentGenerationErrorMessage({ generationError: "education AI is not configured" })).toContain("尚未配置");
    expect(assessmentGenerationErrorMessage({ generationError: "provider failed" })).toBe("provider failed");
  });
});

describe("education assessments", () => {
  it("runs four AI operations, queues the fifth, and keeps non-AI operations outside the AI cap", () => {
    let state: AssessmentOperationQueueState = { operations: [] };
    for (let nodeId = 1; nodeId <= 5; nodeId += 1) {
      state = assessmentOperationReducer(state, {
        type: "enqueue",
        operation: {
          id: `node-${nodeId}`,
          assignmentId: "a1",
          nodeId,
          kind: "regenerate_node",
          usesAi: true,
          status: "queued",
        },
      });
    }
    expect(assessmentOperationCounts(state)).toEqual({ running: 4, queued: 1 });
    state = assessmentOperationReducer(state, { type: "complete", operationId: "node-1" });
    expect(assessmentOperationCounts(state)).toEqual({ running: 4, queued: 0 });
    expect(state.operations.find(operation => operation.id === "node-5")?.status).toBe("running");

    state = assessmentOperationReducer(state, {
      type: "enqueue",
      operation: {
        id: "duplicate-node-2",
        assignmentId: "a1",
        nodeId: 2,
        kind: "delete_question",
        questionId: "q2",
        usesAi: false,
        status: "queued",
      },
    });
    expect(state.operations.filter(operation => operation.nodeId === 2)).toHaveLength(1);
    state = assessmentOperationReducer(state, {
      type: "enqueue",
      operation: {
        id: "delete-node-6",
        assignmentId: "a1",
        nodeId: 6,
        kind: "delete_question",
        questionId: "q6",
        usesAi: false,
        status: "queued",
      },
    });
    expect(state.operations.find(operation => operation.id === "delete-node-6")?.status).toBe("running");
  });

  it("requires one non-empty answer for every current question", () => {
    const attempt = {
      id: "attempt-1",
      assignmentId: "a1",
      nodeId: 1,
      status: "draft" as const,
      answers: { q1: "proof idea", q2: "  " },
      questions: [
        { id: "q1", nodeId: 1, kind: "core_meaning", order: 1, question: "Q1", focus: "F1" },
        { id: "q2", nodeId: 1, kind: "condition_change", order: 2, question: "Q2", focus: "F2" },
      ],
      updatedAt: "2026-08-11T00:00:00",
    };

    expect(assessmentAnswersComplete(attempt)).toBe(false);
    expect(assessmentAnswersComplete({ ...attempt, answers: { q1: "proof idea", q2: "OCR text" } })).toBe(true);
    expect(assessmentAnswersComplete({ ...attempt, questions: [] })).toBe(false);
  });

  it("replaces one reviewed node without disturbing path or other assessments", () => {
    const assignment = {
      id: "a1",
      classId: "c1",
      snapshotId: "s1",
      title: "task",
      targetNodeId: 3,
      status: "draft" as const,
      summary: path.summary,
      version: 1,
      updatedAt: "2026-08-11T00:00:00",
      role: "teacher" as const,
      path,
      assessments: [
        { nodeId: 1, status: "failed" as const, questionCount: 0, updatedAt: "old", questions: [], generationError: "failed" },
        { nodeId: 2, status: "exempt" as const, questionCount: 0, updatedAt: "old", questions: [], generationError: "" },
      ],
    } satisfies EducationAssignment;
    const replacement = {
      nodeId: 1,
      status: "ready" as const,
      questionCount: 1,
      updatedAt: "new",
      generationError: "",
      questions: [{ id: "q1", nodeId: 1, kind: "core_meaning", order: 1, question: "Q", focus: "F", expectedPoints: ["P"] }],
    };

    const updated = replaceNodeAssessment(assignment, replacement);

    expect(updated.path).toBe(path);
    expect(updated.assessments).toHaveLength(2);
    expect(updated.assessments[0]).toEqual(replacement);
    expect(updated.assessments[1]).toBe(assignment.assessments[1]);

    const secondReplacement = {
      nodeId: 2,
      status: "ready" as const,
      questionCount: 1,
      updatedAt: "newer",
      questions: [{ id: "q2", nodeId: 2, kind: "core_meaning", order: 1, question: "Q2", focus: "F2" }],
    };
    const reverseCompleted = replaceNodeAssessment(updated, secondReplacement);
    expect(reverseCompleted.assessments[0]).toEqual(replacement);
    expect(reverseCompleted.assessments[1]).toEqual(secondReplacement);
  });
});


describe("assignment grading helpers", () => {
  const gradingAssignment = {
    id: "grading-a1", classId: "c1", snapshotId: "s1", title: "矩阵作业", targetNodeId: 2, status: "draft" as const, summary: "", version: 1, updatedAt: "now", role: "teacher" as const, path,
    assessments: [
      { nodeId: 1, status: "ready" as const, questionCount: 2, updatedAt: "now", questions: [
        { id: "q1", nodeId: 1, kind: "core_meaning", order: 1, question: "Q1", focus: "F1", referenceAnswer: "A1", expectedPoints: ["P1"], maxScore: 25 },
        { id: "q2", nodeId: 1, kind: "core_meaning", order: 2, question: "Q2", focus: "F2", referenceAnswer: "A2", expectedPoints: ["P2"], maxScore: 25 },
      ] },
      { nodeId: 2, status: "ready" as const, questionCount: 2, updatedAt: "now", questions: [
        { id: "q3", nodeId: 2, kind: "core_meaning", order: 1, question: "Q3", focus: "F3", referenceAnswer: "A3", expectedPoints: ["P3"], maxScore: 25 },
        { id: "q4", nodeId: 2, kind: "core_meaning", order: 2, question: "Q4", focus: "F4", referenceAnswer: "A4", expectedPoints: ["P4"], maxScore: 25 },
      ] },
      { nodeId: 3, status: "exempt" as const, questionCount: 0, updatedAt: "now", questions: [] },
    ],
  } satisfies EducationAssignment;

  it("requires complete scoring standards totaling exactly 100", () => {
    expect(assessmentScoringSummary(gradingAssignment)).toMatchObject({ totalScore: 100, invalidQuestionIds: [], unresolvedNodeIds: [], ready: true });
    const changed = { ...gradingAssignment, assessments: gradingAssignment.assessments.map(item => item.nodeId !== 2 ? item : { ...item, questions: item.questions?.map(question => question.id === "q4" ? { ...question, maxScore: 20, referenceAnswer: "" } : question) }) };
    expect(assessmentScoringSummary(changed)).toMatchObject({ totalScore: 95, invalidQuestionIds: ["q4"], ready: false });
    const referenceInvalid = { ...gradingAssignment, assessments: gradingAssignment.assessments.map(item => item.nodeId !== 1 ? item : { ...item, questions: item.questions?.map(question => question.id === "q1" ? { ...question, referenceMatrixReport: { status: "contradicted" as const, summary: "wrong", issues: [], flowCount: 1, referenceFlowCount: 1 } } : question) }) };
    expect(assessmentScoringSummary(referenceInvalid)).toMatchObject({ referenceInvalidQuestionIds: ["q1"], invalidQuestionIds: ["q1"], ready: false });
  });

  it("makes grading launch and missing-standard states explicit", () => {
    expect(gradingOverviewActionLabel("loading", "submitted")).toBe("正在打开…");
    expect(gradingOverviewActionLabel("evaluating", "submitted")).toBe("AI 评价中…");
    expect(gradingOverviewActionLabel(null, "submitted")).toBe("评价作业");
    expect(gradingStandardIncomplete({ referenceAnswer: "", expectedPoints: ["步骤"], maxScore: 10 })).toBe(true);
    expect(gradingStandardIncomplete({ referenceAnswer: "答案", expectedPoints: ["步骤"], maxScore: 10 })).toBe(false);
  });

  it("adopts the AI score and student-facing feedback into the teacher draft", () => {
    expect(gradingAiSuggestionPatch({ maxScore: 6.2, aiSuggestedScore: 4, aiResult: { suggestedScore: 3.5, rationale: "评分依据", studentFeedback: "请补充定义。" } })).toEqual({ teacherScore: 4, teacherFeedback: "请补充定义。" });
    expect(gradingAiSuggestionPatch({ maxScore: 6.2, aiSuggestedScore: 9, aiResult: { rationale: "使用评分依据作为回退评语" } })).toEqual({ teacherScore: 6.2, teacherFeedback: "使用评分依据作为回退评语" });
    expect(gradingAiSuggestionPatch({ maxScore: 6.2, aiSuggestedScore: null, aiResult: {} })).toBeNull();
  });

  it("counts only non-exempt nodes for whole-assignment submission", () => {
    const student = { ...gradingAssignment, role: "student" as const, status: "published" as const, assessments: gradingAssignment.assessments.map(item => item.status === "ready" ? { nodeId: item.nodeId, status: item.status, questionCount: item.questionCount, updatedAt: item.updatedAt, attemptStatus: item.nodeId === 1 ? "completed" as const : "draft" as const } : item) };
    expect(studentAssignmentCompletion(student)).toEqual({ completed: 1, total: 2, ready: false });
    const completed = { ...student, assessments: student.assessments.map(item => item.status === "ready" ? { ...item, attemptStatus: "completed" as const } : item) };
    expect(studentAssignmentCompletion(completed)).toEqual({ completed: 2, total: 2, ready: true });
  });

  it("localizes submission and grading workflow errors", () => {
    expect(educationErrorMessage(Object.assign(new Error("x"), { code: "assignment_incomplete" }))).toContain("完成所有非免考节点");
    expect(educationErrorMessage(Object.assign(new Error("x"), { code: "assessment_scoring_required" }))).toContain("总分恰好为 100");
    expect(educationErrorMessage(Object.assign(new Error("x"), { code: "grading_incomplete" }))).toContain("尚未完成教师评分");
  });
});
