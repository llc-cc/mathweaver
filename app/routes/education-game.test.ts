import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { loadEducationGameSummary } from "./education";
import type { EducationAssignment, LearningPath } from "./education";
import {
  courseChapterGridPosition,
  deriveAdventureCourseGraphCards,
  deriveAdventureCourseGraphChapters,
  deriveAdventureRegions,
  sortAdventureCourseGraphs,
  deriveAssignmentAdventureState,
  deriveDirectChallengeQuestionNodes,
  deriveLearningStepAdventureState,
  chooseRecommendedAssignment,
  chooseAchievementAtlasGraph,
  chooseAchievementAtlasGraphForCourse,
  chooseRecommendedDirectChallengeNode,
  directChallengeCompletionCount,
  deriveAchievementAtlasEdgeState,
  deriveAchievementAtlasNodeStates,
  describeEducationCurrencyChest,
  educationRewardKey,
  isDirectChallengeReadyToSubmit,
  isRenderableEducationReward,
  parseEducationGameSummary,
  parseEducationShopItems,
  parseEducationRewardReceipt,
  type EducationAchievement,
} from "./education-game";
import { computeDepthsLocal, layoutDag } from "./studio-graph";
import {
  buildAchievementAtlasNetworkLayout,
  buildAchievementAtlasRouteCurve,
  deriveAchievementAtlasFocus,
  deriveAchievementAtlasLandmarkKind,
  deriveAchievementAtlasRouteKind,
} from "./education-atlas-layout";
import type { GraphEdge, GraphNode } from "./home";
import { EducationAchievementAtlas } from "./EducationAchievementAtlas";

const now = new Date("2026-09-01T12:00:00.000Z");

function makePath(): LearningPath {
  return {
    targetNodeId: 3,
    summary: "先修后学",
    candidateNodeIds: [1, 2, 3],
    steps: [
      { nodeId: 1, order: 1, role: "prerequisite", required: true, rationale: "基础", state: "not_started" },
      { nodeId: 2, order: 2, role: "prerequisite", required: false, rationale: "可选", state: "not_started" },
      { nodeId: 3, order: 3, role: "target", required: true, rationale: "目标", state: "not_started" },
    ],
    edges: [{ from: 1, to: 2 }, { from: 2, to: 3 }],
  };
}

function makeAssignment(overrides: Partial<EducationAssignment> = {}): EducationAssignment {
  return {
    id: "a1",
    classId: "c1",
    assignmentType: "graph",
    snapshotId: "s1",
    title: "理解线性无关",
    targetNodeId: 3,
    status: "published",
    summary: "先修后学",
    version: 1,
    updatedAt: "2026-09-01T10:00:00.000Z",
    publishedAt: "2026-09-01T09:00:00.000Z",
    role: "student",
    path: makePath(),
    assessments: [
      { nodeId: 1, status: "ready", questionCount: 1, updatedAt: "2026-09-01T09:00:00.000Z" },
      { nodeId: 2, status: "ready", questionCount: 1, updatedAt: "2026-09-01T09:00:00.000Z" },
      { nodeId: 3, status: "ready", questionCount: 1, updatedAt: "2026-09-01T09:00:00.000Z" },
    ],
    ...overrides,
  };
}

const reward = {
  xpDelta: 10,
  totalXp: 110,
  level: 2,
  levelXp: 10,
  nextLevelXp: 100,
  weeklyXp: 40,
  weeklyGoal: 60,
  unlockedAchievements: [],
  growthEvents: [],
};

describe("education game response guards", () => {
  it("falls back when the game endpoint is missing or its required fields are incomplete", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}) })));
    await expect(loadEducationGameSummary("token", "c1")).resolves.toBeNull();
    vi.unstubAllGlobals();

    expect(parseEducationGameSummary({ enabled: true, settings: { studentExperience: "map", weeklyXpGoal: 60, timezone: "Asia/Shanghai" }, profile: {}, achievements: [] })).toBeNull();
    expect(parseEducationGameSummary({ enabled: true, profile: null, achievements: [] })).toBeNull();
  });

  it("parses stable system shop item keys without requiring them for custom goods", () => {
    expect(parseEducationShopItems({ items: [
      { id: "revive", kind: "system", itemKey: "revive_card", title: "火花复燃卡", description: "恢复一天", gemPrice: 150, stock: null, active: true },
      { id: "custom", kind: "custom", itemKey: null, title: "课程奖励", description: "奖励", gemPrice: 50, stock: 2, active: true },
    ] })).toHaveLength(2);
    expect(parseEducationShopItems({ items: [{ id: "invalid", kind: "system", itemKey: "unknown", title: "错误", description: "错误", gemPrice: 1, stock: null, active: true }] })).toEqual([]);
  });

  it("describes each persisted currency chest outcome without mixing it with XP growth rewards", () => {
    const baseChest = { id: "chest-1", kind: "currency_chest" as const, chestType: "excellent_assignment", openedAt: "2026-09-01T00:00:00Z", seenAt: null };
    expect(describeEducationCurrencyChest({ ...baseChest, outcome: { kind: "gems", gemDelta: 29 } })).toEqual({
      title: "优秀作业宝石箱",
      rewardLabel: "+29 宝石",
      destinationLabel: "宝石已存入当前课程钱包",
      rewardKind: "gems",
      jackpot: false,
    });
    expect(describeEducationCurrencyChest({ ...baseChest, chestType: "checkin_milestone", outcome: { kind: "item", itemKey: "revive_card", quantity: 1, jackpot: true } })).toMatchObject({
      title: "累计签到宝石箱",
      rewardLabel: "火花复燃卡 ×1",
      rewardKind: "revive_card",
      jackpot: true,
    });
    expect(describeEducationCurrencyChest({ ...baseChest, chestType: "weekly_checkin", outcome: { kind: "item", itemKey: "xp_card", quantity: 2 } })).toMatchObject({
      title: "本周签到宝石箱",
      rewardLabel: "经验卡 ×2",
      destinationLabel: "道具已存入当前课程背包",
      rewardKind: "xp_card",
    });
  });

  it("requires a complete seven-day checkin calendar in the summary", () => {
    const weekDays = Array.from({ length: 7 }, (_, index) => ({ date: `2026-09-${String(index + 1).padStart(2, "0")}`, kind: index === 1 ? "genuine" : null, paused: index === 4, isToday: index === 1 }));
    const base = {
      enabled: true,
      settings: { studentExperience: "map", weeklyXpGoal: 60, timezone: "Asia/Shanghai" },
      profile: { totalXp: 0, level: 1, levelXp: 0, nextLevelXp: 100, weeklyXp: 0, weeklyGoal: 60, activeDaysThisWeek: 0, consecutiveGoalWeeks: 0 },
      achievements: [],
      checkin: { todayCheckedIn: true, todayKind: "genuine", streakDays: 1, weeklyGenuineDays: 1, totalGenuineDays: 1, canReviveYesterday: false, reviveCards: 0, weekDays },
      growth: null,
      wallet: null,
      inventory: null,
      unreadCurrencyRewards: [],
    };
    expect(parseEducationGameSummary(base)).not.toBeNull();
    expect(parseEducationGameSummary({ ...base, checkin: { ...base.checkin, weekDays: weekDays.slice(0, 6) } })).toBeNull();
  });

  it("keeps growth chests structurally separate from gem rewards", () => {
    const base = {
      enabled: true,
      settings: { studentExperience: "map", weeklyXpGoal: 60, timezone: "Asia/Shanghai" },
      profile: { totalXp: 1000, level: 11, levelXp: 0, nextLevelXp: 100, weeklyXp: 10, weeklyGoal: 60, activeDaysThisWeek: 1, consecutiveGoalWeeks: 0 },
      achievements: [],
      checkin: { todayCheckedIn: false, todayKind: null, streakDays: 2, weeklyGenuineDays: 2, totalGenuineDays: 7, canReviveYesterday: false, reviveCards: 0, weekDays: [
        { date: "2026-08-31", kind: null, paused: false, isToday: false },
        { date: "2026-09-01", kind: null, paused: false, isToday: true },
        { date: "2026-09-02", kind: null, paused: false, isToday: false },
        { date: "2026-09-03", kind: null, paused: false, isToday: false },
        { date: "2026-09-04", kind: null, paused: false, isToday: false },
        { date: "2026-09-05", kind: null, paused: false, isToday: false },
        { date: "2026-09-06", kind: null, paused: false, isToday: false },
      ] },
      wallet: { balance: 20, lifetimeGemsEarned: 60 },
      inventory: { reviveCard: 0, xpCard: 0, activeXpCards: 0 },
      unreadCurrencyRewards: [],
      growth: {
        badgeTier: 3, badgeStars: 1, levelRoadmap: [{ level: 11, badgeTier: 3, badgeStars: 1, state: "current", rewards: [{ kind: "badge", title: "第 3 阶 1 星徽章", description: "升级后点亮本级徽章。" }] }], unreadLevelUps: [], pendingFiveLevelChoices: [], permanentTitles: [], collectibles: [],
        weeklyGoal: { weekStart: "2026-08-31", xp: 10, goalXp: 60, completed: false, completedAt: null },
        classXp: { level: 1, levelXp: 0, levelGoal: 500, weeklyGoalCompleters: 0 }, stages: [],
        growthChests: [{ id: "growth-10", kind: "growth_chest", rewardType: "growth_chest", status: "opened", level: 10, stageKey: null, payload: { title: "高级成长箱", containsGems: false }, createdAt: "2026-09-01T00:00:00" }],
      },
    };
    expect(parseEducationGameSummary(base)?.growth?.levelRoadmap[0]).toMatchObject({ level: 11, state: "current", badgeStars: 1 });
    expect(parseEducationGameSummary({ ...base, growth: { ...base.growth, levelRoadmap: [{ ...base.growth.levelRoadmap[0], state: "invalid" }] } })).toBeNull();
    expect(parseEducationGameSummary({ ...base, growth: { ...base.growth, growthChests: [{ ...base.growth.growthChests[0], payload: { gemDelta: 999 } }] } })).toBeNull();
  });

  it("validates weekly goal bounds and reward receipts", () => {
    const base = { enabled: true, settings: { studentExperience: "classic", weeklyXpGoal: 60, timezone: "Asia/Shanghai" }, profile: { totalXp: 0, level: 1, levelXp: 0, nextLevelXp: 100, weeklyXp: 0, weeklyGoal: 60, activeDaysThisWeek: 0, consecutiveGoalWeeks: 0 }, achievements: [] };
    expect(parseEducationGameSummary({ ...base, settings: { ...base.settings, weeklyXpGoal: 10 } })).not.toBeNull();
    expect(parseEducationGameSummary({ ...base, settings: { ...base.settings, weeklyXpGoal: 500 } })).not.toBeNull();
    expect(parseEducationGameSummary({ ...base, settings: { ...base.settings, weeklyXpGoal: 9 } })).toBeNull();
    expect(parseEducationGameSummary({ ...base, settings: { ...base.settings, weeklyXpGoal: 501 } })).toBeNull();
    expect(parseEducationRewardReceipt(reward)).toEqual(reward);
    expect(isRenderableEducationReward(reward)).toBe(true);
    expect(isRenderableEducationReward({ ...reward, xpDelta: 0 })).toBe(false);
    expect(isRenderableEducationReward({ xpDelta: 10 } as never)).toBe(false);
    expect(isRenderableEducationReward(null)).toBe(false);
    expect(educationRewardKey(reward)).toBe(educationRewardKey({ ...reward }));
  });
});

describe("education adventure assignment organization", () => {
  it("lays saved chapters out as a four-column S path while keeping source order for focus", () => {
    const graphs = Array.from({ length: 9 }, (_, index) => ({
      id: `s${index + 1}`,
      classId: "c1",
      filename: `第 ${index + 1} 章`,
      nodeCount: 2,
      edgeCount: 1,
      courseOrder: index,
      createdAt: `2026-08-${String(index + 1).padStart(2, "0")}T00:00:00.000Z`,
      snapshotIds: [`s${index + 1}`],
    }));
    const chapters = deriveAdventureCourseGraphChapters(graphs, [makeAssignment({ id: "recommended", snapshotId: "s5" })]);
    expect(chapters.map(chapter => chapter.chapter)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]);
    expect(chapters.find(chapter => chapter.graph.id === "s5")?.isRecommended).toBe(true);
    expect(chapters.filter(chapter => chapter.visibleAssignmentCount === 0)).toHaveLength(8);
    expect(chapters.map((chapter, index) => ({ chapter: chapter.chapter, ...courseChapterGridPosition(index, 4) }))).toEqual([
      { chapter: 1, row: 0, column: 0 }, { chapter: 2, row: 0, column: 1 }, { chapter: 3, row: 0, column: 2 }, { chapter: 4, row: 0, column: 3 },
      { chapter: 5, row: 1, column: 3 }, { chapter: 6, row: 1, column: 2 }, { chapter: 7, row: 1, column: 1 }, { chapter: 8, row: 1, column: 0 },
      { chapter: 9, row: 2, column: 0 },
    ]);
    expect(courseChapterGridPosition(2, 2)).toEqual({ row: 1, column: 1 });
    expect(courseChapterGridPosition(2, 1)).toEqual({ row: 2, column: 0 });
  });

  it("uses saved course order before the legacy first-joined fallback", () => {
    const graphs = [
      { id: "late", classId: "c1", filename: "晚", nodeCount: 1, edgeCount: 0, courseOrder: 2, createdAt: "2026-08-01T00:00:00.000Z", snapshotIds: ["late"] },
      { id: "middle", classId: "c1", filename: "中", nodeCount: 1, edgeCount: 0, courseOrder: 1, createdAt: "2026-08-04T00:00:00.000Z", snapshotIds: ["middle"] },
      { id: "legacy", classId: "c1", filename: "旧", nodeCount: 1, edgeCount: 0, createdAt: "2026-07-01T00:00:00.000Z", snapshotIds: ["legacy"] },
      { id: "first", classId: "c1", filename: "先", nodeCount: 1, edgeCount: 0, courseOrder: 0, createdAt: "2026-08-03T00:00:00.000Z", snapshotIds: ["first"] },
    ];
    expect(sortAdventureCourseGraphs(graphs).map(graph => graph.id)).toEqual(["first", "middle", "late", "legacy"]);
  });

  it("keeps every course graph visible and counts only published student assignments", () => {
    const graphs = [
      { id: "s1", classId: "c1", filename: "线性代数", nodeCount: 3, edgeCount: 2, boundAssignmentCount: 9, createdAt: "2026-08-01T00:00:00.000Z", snapshotIds: ["s1", "s1-v2"] },
      { id: "s2", classId: "c1", filename: "概率论", nodeCount: 4, edgeCount: 3, boundAssignmentCount: 6, createdAt: "2026-08-02T00:00:00.000Z", snapshotIds: ["s2"] },
    ];
    const cards = deriveAdventureCourseGraphCards(graphs, [
      makeAssignment({ id: "visible-1", snapshotId: "s1" }),
      makeAssignment({ id: "visible-2", snapshotId: "s1-v2" }),
      makeAssignment({ id: "draft", snapshotId: "s1", status: "draft" }),
      makeAssignment({ id: "archived", snapshotId: "s2", status: "archived" }),
    ]);
    expect(cards.map(card => card.graph.id)).toEqual(["s1", "s2"]);
    expect(cards.map(card => card.visibleAssignmentCount)).toEqual([2, 0]);
  });

  it("partitions graph, unmatched, and direct assignments and sorts due dates first", () => {
    const assignments = [
      makeAssignment({ id: "no-due", title: "无截止时间", dueAt: null, publishedAt: "2026-09-01T12:00:00.000Z" }),
      makeAssignment({ id: "due", title: "有截止时间", dueAt: "2026-09-02T12:00:00.000Z" }),
      makeAssignment({ id: "direct", assignmentType: "direct", snapshotId: "direct-snapshot", title: "题目挑战" }),
      makeAssignment({ id: "other", snapshotId: "unmatched", title: "其他任务" }),
    ];
    const regions = deriveAdventureRegions(assignments, [{ id: "s1", classId: "c1", filename: "线性代数", nodeCount: 3, edgeCount: 2, createdAt: "2026-08-01T00:00:00.000Z", snapshotIds: ["s1"] }]);
    expect(regions.map(region => region.kind)).toEqual(["course", "other", "challenge"]);
    expect(regions[0].assignments.map(item => item.id)).toEqual(["due", "no-due"]);
    expect(regions[1].assignments[0].id).toBe("other");
    expect(regions[2].assignments[0].id).toBe("direct");
  });

  it("maps all five assignment states and recommends actionable work in priority order", () => {
    const settled = makeAssignment({ id: "settled", submission: { id: "sub-1", status: "released", submittedAt: "2026-08-31T00:00:00.000Z", updatedAt: "2026-08-31T00:00:00.000Z" } });
    const waiting = makeAssignment({ id: "waiting", submission: { id: "sub-2", status: "submitted", submittedAt: "2026-08-31T00:00:00.000Z", updatedAt: "2026-08-31T00:00:00.000Z" } });
    const overdue = makeAssignment({ id: "overdue", dueAt: "2026-08-30T00:00:00.000Z" });
    const inProgress = makeAssignment({ id: "progress", assessments: [{ nodeId: 1, status: "ready", questionCount: 1, updatedAt: "now", attemptStatus: "draft" }, { nodeId: 2, status: "ready", questionCount: 1, updatedAt: "now" }, { nodeId: 3, status: "ready", questionCount: 1, updatedAt: "now" }] });
    const available = makeAssignment({ id: "available" });
    expect(deriveAssignmentAdventureState(settled, now)).toBe("settled");
    expect(deriveAssignmentAdventureState(waiting, now)).toBe("awaiting_review");
    expect(deriveAssignmentAdventureState(overdue, now)).toBe("overdue");
    expect(deriveAssignmentAdventureState(inProgress, now)).toBe("in_progress");
    expect(deriveAssignmentAdventureState(available, now)).toBe("available");
    expect(chooseRecommendedAssignment([available, overdue, inProgress], now)?.id).toBe("progress");
  });
});

describe("direct challenge map state", () => {
  const items = [
    { key: "1:0", nodeId: 1, questionIndex: 0, order: 1 },
    { key: "2:0", nodeId: 2, questionIndex: 0, order: 2 },
    { key: "3:0", nodeId: 3, questionIndex: 0, order: 3 },
  ];

  it("prefers live attempt state over assignment snapshots and counts completion", () => {
    const nodes = deriveDirectChallengeQuestionNodes(
      items,
      { "1": { status: "completed" }, "2": { status: "draft" } },
      [{ nodeId: 1, attemptStatus: "draft" }, { nodeId: 2, attemptStatus: "completed" }, { nodeId: 3, attemptStatus: "not_started" }],
    );
    expect(nodes.map(node => node.state)).toEqual(["completed", "draft", "not_started"]);
    expect(directChallengeCompletionCount(nodes)).toBe(1);
    expect(chooseRecommendedDirectChallengeNode(nodes)?.key).toBe("2:0");
    expect(isDirectChallengeReadyToSubmit(nodes)).toBe(false);
  });

  it("enables the finish node only after every question is completed", () => {
    const nodes = deriveDirectChallengeQuestionNodes(
      items,
      { "1": { status: "completed" }, "2": { status: "completed" }, "3": { status: "completed" } },
      [],
    );
    expect(directChallengeCompletionCount(nodes)).toBe(3);
    expect(chooseRecommendedDirectChallengeNode(nodes)).toBeNull();
    expect(isDirectChallengeReadyToSubmit(nodes)).toBe(true);
  });
});

describe("education adventure learning steps", () => {
  it("soft-locks only behind incomplete required prerequisites", () => {
    const assignment = makeAssignment();
    const target = assignment.path.steps[2];
    const optional = assignment.path.steps[1];
    expect(deriveLearningStepAdventureState(assignment, target)).toEqual(expect.objectContaining({ state: "locked", blockedBy: expect.objectContaining({ nodeId: 1 }) }));
    expect(deriveLearningStepAdventureState(assignment, optional).state).toBe("available");

    const unlocked = makeAssignment({ path: { ...assignment.path, steps: assignment.path.steps.map(step => step.nodeId === 1 ? { ...step, state: "mastered" as const } : step) } });
    expect(deriveLearningStepAdventureState(unlocked, unlocked.path.steps[2]).state).toBe("available");
  });

  it("preserves draft, awaiting review, mastered, needs review, and exempt states", () => {
    const base = makeAssignment();
    const draft = makeAssignment({ path: { ...base.path, steps: base.path.steps.map(step => step.nodeId === 1 ? { ...step, state: "in_progress" as const } : step) } });
    const awaiting = makeAssignment({ assessments: base.assessments.map(item => item.nodeId === 1 ? { ...item, attemptStatus: "completed" as const } : item) });
    const mastered = makeAssignment({ path: { ...base.path, steps: base.path.steps.map(step => step.nodeId === 1 ? { ...step, state: "mastered" as const } : step) } });
    const needsReview = makeAssignment({ path: { ...base.path, steps: base.path.steps.map(step => step.nodeId === 1 ? { ...step, state: "needs_review" as const } : step) } });
    const exempt = makeAssignment({ assessments: base.assessments.map(item => item.nodeId === 1 ? { ...item, status: "exempt" as const } : item) });
    expect(deriveLearningStepAdventureState(draft, draft.path.steps[0]).state).toBe("draft");
    expect(deriveLearningStepAdventureState(awaiting, awaiting.path.steps[0]).state).toBe("awaiting_review");
    expect(deriveLearningStepAdventureState(mastered, mastered.path.steps[0]).state).toBe("mastered");
    expect(deriveLearningStepAdventureState(needsReview, needsReview.path.steps[0]).state).toBe("needs_review");
    expect(deriveLearningStepAdventureState(exempt, exempt.path.steps[0]).state).toBe("exempt");
  });
});

describe("education achievement atlas", () => {
  const graphs = [
    { id: "s1", classId: "c1", filename: "线性代数", nodeCount: 4, edgeCount: 3, createdAt: "2026-08-01T00:00:00.000Z", snapshotIds: ["s1", "s1-v2"] },
    { id: "s2", classId: "c1", filename: "概率论", nodeCount: 3, edgeCount: 2, createdAt: "2026-08-02T00:00:00.000Z", snapshotIds: ["s2"] },
  ];

  it("chooses the recommended graph assignment and falls back to the first graph", () => {
    const direct = makeAssignment({ id: "direct", assignmentType: "direct", snapshotId: "direct-snapshot", assessments: [{ nodeId: 1, status: "ready", questionCount: 1, updatedAt: "now", attemptStatus: "draft" }] });
    const graphTask = makeAssignment({ id: "graph", snapshotId: "s2", assessments: [{ nodeId: 1, status: "ready", questionCount: 1, updatedAt: "now", attemptStatus: "draft" }] });
    expect(chooseAchievementAtlasGraph(graphs, [direct, graphTask], now)).toEqual(expect.objectContaining({ graph: graphs[1], snapshotId: "s2", assignmentId: "graph" }));
    expect(chooseAchievementAtlasGraph(graphs, [], now)).toEqual(expect.objectContaining({ graph: graphs[0], snapshotId: "s1" }));
    expect(chooseAchievementAtlasGraph([], [], now)).toBeNull();
  });

  it("uses the course's recommended snapshot, then its most recently updated assignment snapshot", () => {
    const available = makeAssignment({ id: "available", snapshotId: "s1-v2" });
    expect(chooseAchievementAtlasGraphForCourse(graphs[0], [available], now)).toEqual(expect.objectContaining({ snapshotId: "s1-v2", assignmentId: "available" }));

    const older = makeAssignment({ id: "older", snapshotId: "s1", updatedAt: "2026-08-30T10:00:00.000Z", submission: { id: "sub-1", status: "released", submittedAt: "2026-08-30T10:00:00.000Z", updatedAt: "2026-08-30T10:00:00.000Z" } });
    const newer = makeAssignment({ id: "newer", snapshotId: "s1-v2", updatedAt: "2026-08-31T10:00:00.000Z", submission: { id: "sub-2", status: "released", submittedAt: "2026-08-31T10:00:00.000Z", updatedAt: "2026-08-31T10:00:00.000Z" } });
    expect(chooseAchievementAtlasGraphForCourse(graphs[0], [older, newer], now)).toEqual(expect.objectContaining({ snapshotId: "s1-v2", assignmentId: "newer" }));
  });

  it("derives three node states from only the exact immutable snapshot", () => {
    const base = makeAssignment();
    const primary = makeAssignment({
      id: "primary",
      snapshotId: "s1",
      path: {
        ...base.path,
        steps: base.path.steps.map(step => step.nodeId === 1
          ? { ...step, state: "mastered" as const }
          : step.nodeId === 2
            ? { ...step, state: "needs_review" as const }
            : { ...step, state: "in_progress" as const }),
      },
    });
    const conflicting = makeAssignment({
      id: "conflicting",
      snapshotId: "s1",
      path: { ...base.path, steps: base.path.steps.map(step => step.nodeId === 2 ? { ...step, state: "mastered" as const } : step) },
    });
    const otherSnapshot = makeAssignment({
      id: "other-snapshot",
      snapshotId: "s2",
      path: { ...base.path, steps: [{ ...base.path.steps[2], nodeId: 4, state: "needs_review" as const }] },
    });
    const exempt = makeAssignment({
      id: "exempt",
      snapshotId: "s1",
      path: { ...base.path, steps: [{ ...base.path.steps[0], nodeId: 4, state: "mastered" as const }] },
      assessments: [{ nodeId: 4, status: "exempt", questionCount: 0, updatedAt: "now" }],
    });
    const states = deriveAchievementAtlasNodeStates([{ id: 1 }, { id: 2 }, { id: 3 }, { id: 4 }], [primary, conflicting, otherSnapshot, exempt], "s1");
    expect(states).toEqual({ 1: "mastered", 2: "needs_review", 3: "unlearned", 4: "unlearned" });
    expect(deriveAchievementAtlasEdgeState({ from: 1, to: 2 }, states)).toBe("needs_review");
    expect(deriveAchievementAtlasEdgeState({ from: 1, to: 1 }, states)).toBe("mastered");
    expect(deriveAchievementAtlasEdgeState({ from: 1, to: 3 }, states)).toBe("unlearned");
  });

  it("keeps empty, cyclic, and isolated graph layout deterministic", () => {
    expect(layoutDag([], computeDepthsLocal([], []))).toEqual({});
    const nodes = [{ id: 1, node_index_in_doc: 1 }, { id: 2, node_index_in_doc: 2 }, { id: 3, node_index_in_doc: 3 }] as Parameters<typeof computeDepthsLocal>[0];
    const edges = [{ from: 1, to: 2 }, { from: 2, to: 1 }] as Parameters<typeof computeDepthsLocal>[1];
    const first = layoutDag(nodes, computeDepthsLocal(nodes, edges));
    const second = layoutDag(nodes, computeDepthsLocal(nodes, edges));
    expect(first).toEqual(second);
    expect(Object.keys(first)).toHaveLength(3);
  });
});

describe("achievement atlas achievement gallery", () => {
  it("renders every achievement and preserves unlocked states", () => {
    const keys: EducationAchievement["key"][] = ["first_step", "pathfinder", "challenge_clear", "on_time", "steady_learner", "full_route"];
    const achievements: EducationAchievement[] = keys.map((key, index) => ({
      key,
      title: `Achievement ${index + 1}`,
      description: `Description ${index + 1}`,
      unlocked: index < 3,
      unlockedAt: index < 3 ? "2026-09-03T00:00:00.000Z" : null,
    }));
    const markup = renderToStaticMarkup(createElement(EducationAchievementAtlas, {
      courseGraphs: [],
      assignments: [],
      initialSelection: null,
      achievements,
      loadCourseGraphSnapshot: async () => { throw new Error("not called"); },
      onOpenCourseGraph: () => {},
      onClose: () => {},
    }));
    achievements.forEach(achievement => expect(markup).toContain(achievement.title));
    expect(markup.match(/edu-atlas-achievement unlocked/g)).toHaveLength(3);
    expect(markup.match(/edu-atlas-achievement locked/g)).toHaveLength(3);
  });
});

describe("achievement atlas free network layout", () => {
  const nodes = [
    { id: 1, node_type: "definition", node_index_in_doc: 1 },
    { id: 2, node_type: "definition", node_index_in_doc: 2 },
    { id: 3, node_type: "theorem", node_index_in_doc: 3 },
    { id: 4, node_type: "example", node_index_in_doc: 4 },
    { id: 5, node_type: "remark", node_index_in_doc: 5 },
    { id: 6, node_type: "remark", node_index_in_doc: 6 },
  ] as GraphNode[];
  const edges = [
    { from: 1, to: 2, label: "定义依赖", description: "", strength: "" },
    { from: 1, to: 3, label: "逻辑依赖", description: "", strength: "" },
    { from: 1, to: 4, label: "逻辑依赖", description: "", strength: "" },
    { from: 1, to: 5, label: "逻辑依赖", description: "", strength: "" },
    { from: 3, to: 5, label: "逻辑依赖", description: "", strength: "" },
  ] as GraphEdge[];

  it("creates a deterministic free network independent of input order", () => {
    const first = buildAchievementAtlasNetworkLayout(nodes, edges);
    const second = buildAchievementAtlasNetworkLayout([...nodes].reverse(), [...edges].reverse());
    expect(second).toEqual(first);
    expect(first.width).toBeGreaterThanOrEqual(920);
    expect(first.height).toBeGreaterThanOrEqual(620);
    Object.values(first.positions).forEach(position => {
      expect(Number.isFinite(position.x)).toBe(true);
      expect(Number.isFinite(position.y)).toBe(true);
    });
  });

  it("uses stable landmarks, decorative route kinds, and direct-neighbor focus", () => {
    const layout = buildAchievementAtlasNetworkLayout(nodes, edges);
    expect(layout.landmarkKinds).toEqual(expect.objectContaining({
      1: "lighthouse",
      2: "monument",
      3: "highland",
      4: "camp",
      6: "reef",
    }));
    expect(deriveAchievementAtlasLandmarkKind({ node_type: "definition" } as GraphNode, 0)).toBe("reef");
    expect(deriveAchievementAtlasRouteKind(layout.edges[0].key)).toBe(layout.edges[0].routeKind);
    expect(["trail", "stream", "meadow"]).toContain(layout.edges[0].routeKind);
    const focus = deriveAchievementAtlasFocus(1, layout);
    expect([...focus.nodeIds].sort((left, right) => left - right)).toEqual([1, 2, 3, 4, 5]);
    expect(focus.edgeKeys.size).toBe(4);
  });

  it("keeps landmarks separated and routes finite for cycles and isolated nodes", () => {
    const layout = buildAchievementAtlasNetworkLayout(nodes, [...edges, { from: 5, to: 1, label: "逻辑依赖", description: "", strength: "" }]);
    const visible = Object.values(layout.positions);
    for (let left = 0; left < visible.length; left += 1) {
      for (let right = left + 1; right < visible.length; right += 1) {
        expect(Math.hypot(visible[left].x - visible[right].x, visible[left].y - visible[right].y)).toBeGreaterThan(190);
      }
    }
    const route = buildAchievementAtlasRouteCurve(layout.positions[1], layout.positions[2], layout.edges[0].key);
    expect(route.path).not.toContain("NaN");
    expect(Number.isFinite(route.arrow.angle)).toBe(true);
  });

  it("keeps empty graphs stable and filters routes whose endpoints are absent", () => {
    expect(buildAchievementAtlasNetworkLayout([], [])).toEqual({
      width: 820, height: 560, positions: {}, landmarkKinds: {}, degreeByNode: {}, neighborIdsByNode: {}, edges: [],
    });
    const layout = buildAchievementAtlasNetworkLayout(
      nodes.slice(0, 2),
      [...edges, { from: 1, to: 99, label: "invalid", description: "", strength: "" }],
    );
    expect(layout.edges).toHaveLength(1);
    expect(layout.neighborIdsByNode[1]).toEqual([2]);
  });
});
