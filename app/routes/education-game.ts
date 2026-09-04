import type {
  CourseGraphSummary,
  EducationAssignment,
  LearningPathStep,
  NodeAssessment,
} from "./education";

export type EducationStudentExperience = "classic" | "map";

export interface EducationGameSettings {
  studentExperience: EducationStudentExperience;
  weeklyXpGoal: number;
  timezone: string;
}

export type EducationAchievementKey =
  | "first_step"
  | "pathfinder"
  | "challenge_clear"
  | "on_time"
  | "steady_learner"
  | "full_route";

export interface EducationAchievement {
  key: EducationAchievementKey;
  title: string;
  description: string;
  unlocked: boolean;
  unlockedAt?: string | null;
}

export interface EducationGameProfile {
  totalXp: number;
  level: number;
  levelXp: number;
  nextLevelXp: number;
  weeklyXp: number;
  weeklyGoal: number;
  activeDaysThisWeek: number;
  consecutiveGoalWeeks: number;
}

export type EducationGameRewardKind = "xp_progress" | "level_up" | "five_level_choice" | "growth_chest" | "weekly_badge" | "stage_milestone" | "currency_chest" | "teacher_gem_award" | "permanent_title";

export interface EducationGrowthReward {
  id: string;
  kind: EducationGameRewardKind;
  rewardType: string;
  status: string;
  level?: number | null;
  stageKey?: string | null;
  payload: Record<string, unknown>;
  createdAt: string;
  claimedAt?: string | null;
  seenAt?: string | null;
}

export type EducationLevelRoadmapState = "completed" | "current" | "upcoming";
export type EducationLevelRoadmapRewardKind = "badge" | "choice" | "growth_chest" | "permanent_title";

export interface EducationLevelRoadmapReward {
  kind: EducationLevelRoadmapRewardKind;
  title: string;
  description: string;
}

export interface EducationLevelRoadmapItem {
  level: number;
  badgeTier: number;
  badgeStars: number;
  state: EducationLevelRoadmapState;
  rewards: EducationLevelRoadmapReward[];
}

export interface EducationCollectible {
  key: string;
  type: "cosmetic" | "title" | "badge" | "challenge_entitlement" | string;
  title: string;
  metadata: Record<string, unknown>;
  equipped: boolean;
  unlockedAt: string;
}

export interface EducationStageProgress {
  stageKey: string;
  goalXp: number;
  currentXp: number;
  completed: boolean;
  milestones: Array<{ percent: number; thresholdXp: number; completed: boolean }>;
}

export interface EducationGrowthProfile {
  badgeTier: number;
  badgeStars: number;
  levelRoadmap: EducationLevelRoadmapItem[];
  unreadLevelUps: EducationGrowthReward[];
  pendingFiveLevelChoices: EducationGrowthReward[];
  growthChests: EducationGrowthReward[];
  permanentTitles: EducationGrowthReward[];
  collectibles: EducationCollectible[];
  weeklyGoal: { weekStart: string; xp: number; goalXp: number; completed: boolean; completedAt?: string | null };
  classXp: { level: number; levelXp: number; levelGoal: number; weeklyGoalCompleters: number };
  stages: EducationStageProgress[];
}

export interface EducationCheckinDay {
  date: string;
  kind: "genuine" | "revived" | null;
  paused: boolean;
  isToday: boolean;
}

export interface EducationCheckinStatus {
  todayCheckedIn: boolean;
  todayKind?: "genuine" | "revived" | null;
  streakDays: number;
  weeklyGenuineDays: number;
  totalGenuineDays: number;
  canReviveYesterday: boolean;
  reviveCards: number;
  weekDays: EducationCheckinDay[];
}

export interface EducationWallet { balance: number; lifetimeGemsEarned: number; }
export interface EducationInventory { reviveCard: number; xpCard: number; activeXpCards: number; }
export interface EducationCurrencyChest {
  id: string;
  kind: "currency_chest";
  chestType: string;
  outcome: Record<string, unknown>;
  openedAt: string;
  seenAt?: string | null;
}

export interface EducationCurrencyChestPresentation {
  title: string;
  rewardLabel: string;
  destinationLabel: string;
  rewardKind: "gems" | "revive_card" | "xp_card" | "unknown";
  jackpot: boolean;
}

export interface EducationLeaderboardEntry { rank: number; displayName: string; score: number; isSelf: boolean; }
export interface EducationLeaderboard { kind: "xp" | "gems"; entries: EducationLeaderboardEntry[]; }
export interface EducationShopItem { id: string; kind: string; itemKey?: "revive_card" | "xp_card" | null; title: string; description: string; gemPrice: number; stock: number | null; active: boolean; }
export function describeEducationCurrencyChest(chest: EducationCurrencyChest): EducationCurrencyChestPresentation {
  const title = chest.chestType === "excellent_assignment"
    ? "优秀作业宝石箱"
    : chest.chestType === "checkin_milestone"
      ? "累计签到宝石箱"
      : "本周签到宝石箱";
  const jackpot = chest.outcome.jackpot === true;
  if (chest.outcome.kind === "gems" && isFiniteNumber(chest.outcome.gemDelta)) {
    return {
      title,
      rewardLabel: `+${chest.outcome.gemDelta} 宝石`,
      destinationLabel: "宝石已存入当前课程钱包",
      rewardKind: "gems",
      jackpot,
    };
  }
  const quantity = isFiniteNumber(chest.outcome.quantity) && chest.outcome.quantity > 0
    ? Math.floor(chest.outcome.quantity)
    : 1;
  if (chest.outcome.kind === "item" && chest.outcome.itemKey === "revive_card") {
    return {
      title,
      rewardLabel: `火花复燃卡 ×${quantity}`,
      destinationLabel: "道具已存入当前课程背包",
      rewardKind: "revive_card",
      jackpot,
    };
  }
  if (chest.outcome.kind === "item" && chest.outcome.itemKey === "xp_card") {
    return {
      title,
      rewardLabel: `经验卡 ×${quantity}`,
      destinationLabel: "道具已存入当前课程背包",
      rewardKind: "xp_card",
      jackpot,
    };
  }
  return {
    title,
    rewardLabel: "课程奖励已到账",
    destinationLabel: "奖励已存入当前课程账户",
    rewardKind: "unknown",
    jackpot,
  };
}

export interface EducationGameSummary {
  enabled: boolean;
  settings: EducationGameSettings;
  profile: EducationGameProfile | null;
  achievements: EducationAchievement[];
  growth: EducationGrowthProfile | null;
  checkin: EducationCheckinStatus | null;
  wallet: EducationWallet | null;
  inventory: EducationInventory | null;
  unreadCurrencyRewards: EducationCurrencyChest[];
}

export interface EducationRewardReceipt {
  xpDelta: number;
  totalXp: number;
  level: number;
  levelXp: number;
  nextLevelXp: number;
  weeklyXp: number;
  weeklyGoal: number;
  unlockedAchievements: EducationAchievement[];
  growthEvents?: Array<Record<string, unknown>>;
}

export function parseEducationRewardReceipt(value: unknown): EducationRewardReceipt | null {
  if (!isRecord(value)) return null;
  const numbers = [value.xpDelta, value.totalXp, value.level, value.levelXp, value.nextLevelXp, value.weeklyXp, value.weeklyGoal];
  if (!numbers.every(isFiniteNumber) || !Array.isArray(value.unlockedAchievements)) return null;
  const unlockedAchievements = value.unlockedAchievements.map(parseAchievement);
  if (unlockedAchievements.some(item => item === null)) return null;
  const growthEvents = value.growthEvents === undefined ? [] : value.growthEvents;
  if (!Array.isArray(growthEvents) || growthEvents.some(item => !isRecord(item))) return null;
  return {
    xpDelta: value.xpDelta as number,
    totalXp: value.totalXp as number,
    level: value.level as number,
    levelXp: value.levelXp as number,
    nextLevelXp: value.nextLevelXp as number,
    weeklyXp: value.weeklyXp as number,
    weeklyGoal: value.weeklyGoal as number,
    unlockedAchievements: unlockedAchievements as EducationAchievement[],
    growthEvents: growthEvents as Array<Record<string, unknown>>,
  };
}

export function isRenderableEducationReward(reward: EducationRewardReceipt | null | undefined): reward is EducationRewardReceipt {
  const parsed = parseEducationRewardReceipt(reward);
  return Boolean(parsed && parsed.xpDelta > 0);
}

export function educationRewardKey(reward: EducationRewardReceipt): string {
  return [reward.xpDelta, reward.totalXp, reward.level, reward.levelXp, reward.weeklyXp, reward.weeklyGoal, reward.unlockedAchievements.map(item => item.key).join(",")].join("|");
}

export type AdventureAssignmentState =
  | "available"
  | "in_progress"
  | "awaiting_review"
  | "settled"
  | "overdue";

export type AdventureLearningStepState =
  | "locked"
  | "available"
  | "draft"
  | "awaiting_review"
  | "mastered"
  | "needs_review"
  | "exempt";

export interface AdventureLearningStep {
  state: AdventureLearningStepState;
  blockedBy?: LearningPathStep;
}

export interface AdventureRegion {
  id: string;
  title: string;
  subtitle: string;
  kind: "course" | "other" | "challenge";
  courseGraph?: CourseGraphSummary;
  assignments: EducationAssignment[];
}

export interface AdventureCourseGraphCard {
  graph: CourseGraphSummary;
  visibleAssignmentCount: number;
}

export type AdventureCourseLandmark = "academy" | "observatory" | "knowledge_gate" | "lighthouse";

export interface AdventureCourseGraphChapter extends AdventureCourseGraphCard {
  chapter: number;
  landmark: AdventureCourseLandmark;
  isRecommended: boolean;
}

export interface AdventureCourseGridPosition {
  row: number;
  column: number;
}

export type AchievementAtlasNodeState = "mastered" | "needs_review" | "unlearned";
export type AchievementAtlasEdgeState = AchievementAtlasNodeState;

export interface AchievementAtlasGraphSelection {
  graph: CourseGraphSummary;
  snapshotId: string;
  assignmentId?: string;
}

export type DirectChallengeQuestionState = "not_started" | "draft" | "completed";

export interface DirectChallengeQuestionSource {
  key: string;
  nodeId: number;
  questionIndex: number;
  order: number;
}

export interface DirectChallengeQuestionNode extends DirectChallengeQuestionSource {
  state: DirectChallengeQuestionState;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function parseAchievement(value: unknown): EducationAchievement | null {
  if (!isRecord(value)) return null;
  if (typeof value.key !== "string" || typeof value.title !== "string" || typeof value.description !== "string") return null;
  if (typeof value.unlocked !== "boolean") return null;
  if (value.unlockedAt !== undefined && value.unlockedAt !== null && typeof value.unlockedAt !== "string") return null;
  return {
    key: value.key as EducationAchievementKey,
    title: value.title,
    description: value.description,
    unlocked: value.unlocked,
    unlockedAt: value.unlockedAt as string | null | undefined,
  };
}

function parseSettings(value: unknown): EducationGameSettings | null {
  if (!isRecord(value)) return null;
  if (value.studentExperience !== "classic" && value.studentExperience !== "map") return null;
  if (!isFiniteNumber(value.weeklyXpGoal) || !Number.isInteger(value.weeklyXpGoal) || value.weeklyXpGoal < 10 || value.weeklyXpGoal > 500) return null;
  if (typeof value.timezone !== "string" || !value.timezone.trim()) return null;
  return {
    studentExperience: value.studentExperience,
    weeklyXpGoal: value.weeklyXpGoal,
    timezone: value.timezone,
  };
}

function parseProfile(value: unknown): EducationGameProfile | null | undefined {
  if (value === null) return null;
  if (!isRecord(value)) return undefined;
  const totalXp = value.totalXp;
  const level = value.level;
  const levelXp = value.levelXp;
  const nextLevelXp = value.nextLevelXp;
  const weeklyXp = value.weeklyXp;
  const weeklyGoal = value.weeklyGoal;
  const activeDaysThisWeek = value.activeDaysThisWeek;
  const consecutiveGoalWeeks = value.consecutiveGoalWeeks;
  if (!isFiniteNumber(totalXp) || !isFiniteNumber(level) || !isFiniteNumber(levelXp) || !isFiniteNumber(nextLevelXp) || !isFiniteNumber(weeklyXp) || !isFiniteNumber(weeklyGoal) || !isFiniteNumber(activeDaysThisWeek) || !isFiniteNumber(consecutiveGoalWeeks)) return undefined;
  return { totalXp, level, levelXp, nextLevelXp, weeklyXp, weeklyGoal, activeDaysThisWeek, consecutiveGoalWeeks };
}


function parseGrowthReward(value: unknown): EducationGrowthReward | null {
  if (!isRecord(value) || typeof value.id !== "string" || typeof value.kind !== "string" || typeof value.rewardType !== "string" || typeof value.status !== "string" || !isRecord(value.payload) || typeof value.createdAt !== "string") return null;
  if (value.kind === "growth_chest" && "gemDelta" in value.payload) return null;
  if (value.level !== undefined && value.level !== null && !isFiniteNumber(value.level)) return null;
  if (value.stageKey !== undefined && value.stageKey !== null && typeof value.stageKey !== "string") return null;
  return { id: value.id, kind: value.kind as EducationGameRewardKind, rewardType: value.rewardType, status: value.status, level: value.level as number | null | undefined, stageKey: value.stageKey as string | null | undefined, payload: value.payload, createdAt: value.createdAt, claimedAt: value.claimedAt as string | null | undefined, seenAt: value.seenAt as string | null | undefined };
}

function parseLevelRoadmap(value: unknown): EducationLevelRoadmapItem[] | null {
  if (value === undefined) return [];
  if (!Array.isArray(value)) return null;
  const rewardKinds = new Set<EducationLevelRoadmapRewardKind>(["badge", "choice", "growth_chest", "permanent_title"]);
  const items = value.map(item => {
    if (!isRecord(item) || !isFiniteNumber(item.level) || !Number.isInteger(item.level) || !isFiniteNumber(item.badgeTier) || !Number.isInteger(item.badgeTier) || !isFiniteNumber(item.badgeStars) || !Number.isInteger(item.badgeStars) || item.level < 1 || item.badgeTier < 1 || item.badgeStars < 1 || item.badgeStars > 5 || (item.state !== "completed" && item.state !== "current" && item.state !== "upcoming") || !Array.isArray(item.rewards)) return null;
    const rewards = item.rewards.map(reward => {
      if (!isRecord(reward) || typeof reward.kind !== "string" || !rewardKinds.has(reward.kind as EducationLevelRoadmapRewardKind) || typeof reward.title !== "string" || typeof reward.description !== "string") return null;
      return { kind: reward.kind as EducationLevelRoadmapRewardKind, title: reward.title, description: reward.description };
    });
    if (rewards.some(reward => reward === null)) return null;
    return { level: item.level, badgeTier: item.badgeTier, badgeStars: item.badgeStars, state: item.state, rewards: rewards as EducationLevelRoadmapReward[] } as EducationLevelRoadmapItem;
  });
  return items.some(item => item === null) ? null : items as EducationLevelRoadmapItem[];
}

function parseGrowth(value: unknown): EducationGrowthProfile | null | undefined {
  if (value === null) return null;
  if (!isRecord(value) || !isFiniteNumber(value.badgeTier) || !isFiniteNumber(value.badgeStars) || !Array.isArray(value.unreadLevelUps) || !Array.isArray(value.pendingFiveLevelChoices) || !Array.isArray(value.growthChests) || !Array.isArray(value.permanentTitles) || !Array.isArray(value.collectibles) || !Array.isArray(value.stages) || !isRecord(value.weeklyGoal) || !isRecord(value.classXp)) return undefined;
  const levelRoadmap = parseLevelRoadmap(value.levelRoadmap);
  if (levelRoadmap === null) return undefined;
  const parseRewards = (items: unknown[]) => items.map(parseGrowthReward);
  const unreadLevelUps = parseRewards(value.unreadLevelUps);
  const pendingFiveLevelChoices = parseRewards(value.pendingFiveLevelChoices);
  const growthChests = parseRewards(value.growthChests);
  const permanentTitles = parseRewards(value.permanentTitles);
  if ([...unreadLevelUps, ...pendingFiveLevelChoices, ...growthChests, ...permanentTitles].some(item => item === null)) return undefined;
  const collectibles = value.collectibles.map(item => {
    if (!isRecord(item) || typeof item.key !== "string" || typeof item.type !== "string" || typeof item.title !== "string" || !isRecord(item.metadata) || typeof item.equipped !== "boolean" || typeof item.unlockedAt !== "string") return null;
    return { key: item.key, type: item.type, title: item.title, metadata: item.metadata, equipped: item.equipped, unlockedAt: item.unlockedAt } as EducationCollectible;
  });
  const stages = value.stages.map(item => {
    if (!isRecord(item) || typeof item.stageKey !== "string" || !isFiniteNumber(item.goalXp) || !isFiniteNumber(item.currentXp) || typeof item.completed !== "boolean" || !Array.isArray(item.milestones)) return null;
    const milestones = item.milestones.map(node => isRecord(node) && isFiniteNumber(node.percent) && isFiniteNumber(node.thresholdXp) && typeof node.completed === "boolean" ? { percent: node.percent, thresholdXp: node.thresholdXp, completed: node.completed } : null);
    return milestones.some(node => node === null) ? null : { stageKey: item.stageKey, goalXp: item.goalXp, currentXp: item.currentXp, completed: item.completed, milestones: milestones as EducationStageProgress["milestones"] };
  });
  const weekly = value.weeklyGoal;
  const classXp = value.classXp;
  if (collectibles.some(item => item === null) || stages.some(item => item === null) || typeof weekly.weekStart !== "string" || !isFiniteNumber(weekly.xp) || !isFiniteNumber(weekly.goalXp) || typeof weekly.completed !== "boolean" || !isFiniteNumber(classXp.level) || !isFiniteNumber(classXp.levelXp) || !isFiniteNumber(classXp.levelGoal) || !isFiniteNumber(classXp.weeklyGoalCompleters)) return undefined;
  return { badgeTier: value.badgeTier, badgeStars: value.badgeStars, levelRoadmap, unreadLevelUps: unreadLevelUps as EducationGrowthReward[], pendingFiveLevelChoices: pendingFiveLevelChoices as EducationGrowthReward[], growthChests: growthChests as EducationGrowthReward[], permanentTitles: permanentTitles as EducationGrowthReward[], collectibles: collectibles as EducationCollectible[], weeklyGoal: { weekStart: weekly.weekStart, xp: weekly.xp, goalXp: weekly.goalXp, completed: weekly.completed, completedAt: weekly.completedAt as string | null | undefined }, classXp: { level: classXp.level, levelXp: classXp.levelXp, levelGoal: classXp.levelGoal, weeklyGoalCompleters: classXp.weeklyGoalCompleters }, stages: stages as EducationStageProgress[] };
}

function parseCheckin(value: unknown): EducationCheckinStatus | null | undefined {
  if (value === null) return null;
  if (!isRecord(value) || typeof value.todayCheckedIn !== "boolean" || !isFiniteNumber(value.streakDays) || !isFiniteNumber(value.weeklyGenuineDays) || !isFiniteNumber(value.totalGenuineDays) || typeof value.canReviveYesterday !== "boolean" || !isFiniteNumber(value.reviveCards) || !Array.isArray(value.weekDays)) return undefined;
  if (value.todayKind !== undefined && value.todayKind !== null && value.todayKind !== "genuine" && value.todayKind !== "revived") return undefined;
  const weekDays = value.weekDays.map(day => {
    if (!isRecord(day) || typeof day.date !== "string" || typeof day.paused !== "boolean" || typeof day.isToday !== "boolean" || (day.kind !== null && day.kind !== "genuine" && day.kind !== "revived")) return null;
    return { date: day.date, kind: day.kind as EducationCheckinDay["kind"], paused: day.paused, isToday: day.isToday };
  });
  if (weekDays.length !== 7 || weekDays.some(day => day === null)) return undefined;
  return { todayCheckedIn: value.todayCheckedIn, todayKind: value.todayKind as EducationCheckinStatus["todayKind"], streakDays: value.streakDays, weeklyGenuineDays: value.weeklyGenuineDays, totalGenuineDays: value.totalGenuineDays, canReviveYesterday: value.canReviveYesterday, reviveCards: value.reviveCards, weekDays: weekDays as EducationCheckinDay[] };
}

function parseWallet(value: unknown): EducationWallet | null | undefined {
  if (value === null) return null;
  return isRecord(value) && isFiniteNumber(value.balance) && isFiniteNumber(value.lifetimeGemsEarned) ? { balance: value.balance, lifetimeGemsEarned: value.lifetimeGemsEarned } : undefined;
}

function parseInventory(value: unknown): EducationInventory | null | undefined {
  if (value === null) return null;
  return isRecord(value) && isFiniteNumber(value.reviveCard) && isFiniteNumber(value.xpCard) && isFiniteNumber(value.activeXpCards) ? { reviveCard: value.reviveCard, xpCard: value.xpCard, activeXpCards: value.activeXpCards } : undefined;
}

function parseCurrencyChest(value: unknown): EducationCurrencyChest | null {
  if (!isRecord(value) || value.kind !== "currency_chest" || typeof value.id !== "string" || typeof value.chestType !== "string" || !isRecord(value.outcome) || typeof value.openedAt !== "string") return null;
  return { id: value.id, kind: "currency_chest", chestType: value.chestType, outcome: value.outcome, openedAt: value.openedAt, seenAt: value.seenAt as string | null | undefined };
}

export function parseEducationShopItems(value: unknown): EducationShopItem[] {
  if (!isRecord(value) || !Array.isArray(value.items)) return [];
  return value.items.flatMap(item => {
    if (!isRecord(item) || typeof item.id !== "string" || typeof item.kind !== "string" || typeof item.title !== "string" || typeof item.description !== "string" || !isFiniteNumber(item.gemPrice) || (typeof item.stock !== "number" && item.stock !== null) || typeof item.active !== "boolean") return [];
    const itemKey = item.itemKey;
    if (itemKey !== undefined && itemKey !== null && itemKey !== "revive_card" && itemKey !== "xp_card") return [];
    return [{ id: item.id, kind: item.kind, itemKey: itemKey as EducationShopItem["itemKey"], title: item.title, description: item.description, gemPrice: item.gemPrice, stock: item.stock, active: item.active }];
  });
}

export function parseEducationGameSettings(value: unknown): EducationGameSettings | null {
  if (!isRecord(value)) return null;
  return parseSettings(value.settings);
}

export function parseEducationGameSummary(value: unknown): EducationGameSummary | null {
  if (!isRecord(value) || typeof value.enabled !== "boolean") return null;
  const settings = parseSettings(value.settings);
  const profile = parseProfile(value.profile);
  if (!settings || profile === undefined || !Array.isArray(value.achievements)) return null;
  const achievements = value.achievements.map(parseAchievement);
  const growth = value.growth === undefined ? null : parseGrowth(value.growth);
  const checkin = value.checkin === undefined ? null : parseCheckin(value.checkin);
  const wallet = value.wallet === undefined ? null : parseWallet(value.wallet);
  const inventory = value.inventory === undefined ? null : parseInventory(value.inventory);
  const unreadCurrencyRewards = value.unreadCurrencyRewards === undefined ? [] : value.unreadCurrencyRewards;
  if (achievements.some(item => item === null) || growth === undefined || checkin === undefined || wallet === undefined || inventory === undefined || !Array.isArray(unreadCurrencyRewards)) return null;
  const chests = unreadCurrencyRewards.map(parseCurrencyChest);
  if (chests.some(item => item === null)) return null;
  return { enabled: value.enabled, settings, profile, achievements: achievements as EducationAchievement[], growth, checkin, wallet, inventory, unreadCurrencyRewards: chests as EducationCurrencyChest[] };
}

function hasCompletedAssessment(assessment: NodeAssessment | undefined, step: LearningPathStep): boolean {
  return step.state === "mastered" || step.state === "needs_review" || assessment?.attemptStatus === "completed";
}

export function deriveLearningStepAdventureState(
  assignment: EducationAssignment,
  step: LearningPathStep,
): AdventureLearningStep {
  const assessment = assignment.assessments.find(item => item.nodeId === step.nodeId);
  if (step.state === "mastered") return { state: "mastered" };
  if (step.state === "needs_review") return { state: "needs_review" };
  if (assessment?.status === "exempt") return { state: "exempt" };
  if (assessment?.attemptStatus === "completed") return { state: "awaiting_review" };
  if (assessment?.attemptStatus === "draft" || step.state === "in_progress") return { state: "draft" };
  if (!step.required) return { state: "available" };

  const blockedBy = assignment.path.steps
    .filter(candidate => candidate.order < step.order && candidate.required)
    .find(candidate => {
      const candidateAssessment = assignment.assessments.find(item => item.nodeId === candidate.nodeId);
      return candidateAssessment?.status !== "exempt" && !hasCompletedAssessment(candidateAssessment, candidate);
    });
  if (blockedBy) return { state: "locked", blockedBy };
  return { state: "available" };
}

function assignmentHasProgress(assignment: EducationAssignment): boolean {
  return assignment.assessments.some(assessment => assessment.attemptStatus === "draft" || assessment.attemptStatus === "completed")
    || assignment.path.steps.some(step => step.state !== "not_started");
}

export function deriveAssignmentAdventureState(
  assignment: EducationAssignment,
  now = new Date(),
): AdventureAssignmentState {
  if (assignment.submission?.status === "released") return "settled";
  if (assignment.submission) return "awaiting_review";
  if (assignment.dueAt && new Date(assignment.dueAt).getTime() < now.getTime()) return "overdue";
  if (assignmentHasProgress(assignment)) return "in_progress";
  return "available";
}

function timestamp(value?: string | null): number | null {
  const time = value ? new Date(value).getTime() : NaN;
  return Number.isFinite(time) ? time : null;
}

function compareAssignments(left: EducationAssignment, right: EducationAssignment): number {
  const leftDue = timestamp(left.dueAt);
  const rightDue = timestamp(right.dueAt);
  if (leftDue === null && rightDue !== null) return 1;
  if (leftDue !== null && rightDue === null) return -1;
  if (leftDue !== null && rightDue !== null && leftDue !== rightDue) return leftDue - rightDue;

  const leftPublished = timestamp(left.publishedAt ?? left.updatedAt);
  const rightPublished = timestamp(right.publishedAt ?? right.updatedAt);
  if (leftPublished === null && rightPublished !== null) return 1;
  if (leftPublished !== null && rightPublished === null) return -1;
  if (leftPublished !== null && rightPublished !== null && leftPublished !== rightPublished) return leftPublished - rightPublished;
  return left.id.localeCompare(right.id);
}

export function deriveAdventureRegions(
  assignments: EducationAssignment[],
  courseGraphs: CourseGraphSummary[],
): AdventureRegion[] {
  const visibleAssignments = assignments.filter(assignment => assignment.status !== "archived");
  const regions: AdventureRegion[] = [];
  const matchedIds = new Set<string>();

  courseGraphs.forEach(graph => {
    const related = visibleAssignments.filter(assignment => graph.snapshotIds.includes(assignment.snapshotId)).sort(compareAssignments);
    related.forEach(assignment => matchedIds.add(assignment.id));
    if (related.length > 0) {
      regions.push({
        id: `course:${graph.id}`,
        title: graph.filename,
        subtitle: `${graph.nodeCount} 个节点 · ${related.length} 个学习任务`,
        kind: "course",
        courseGraph: graph,
        assignments: related,
      });
    }
  });

  const unmatched = visibleAssignments.filter(assignment => !matchedIds.has(assignment.id) && assignment.assignmentType !== "direct").sort(compareAssignments);
  if (unmatched.length > 0) {
    regions.push({
      id: "other-course",
      title: "其他课程任务",
      subtitle: `${unmatched.length} 个学习任务`,
      kind: "other",
      assignments: unmatched,
    });
  }

  const challenges = visibleAssignments.filter(assignment => assignment.assignmentType === "direct").sort(compareAssignments);
  if (challenges.length > 0) {
    regions.push({
      id: "challenges",
      title: "作业挑战区",
      subtitle: `${challenges.length} 个题目挑战`,
      kind: "challenge",
      assignments: challenges,
    });
  }
  return regions;
}

function courseGraphTimestamp(value: string): number | null {
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : null;
}

export function sortAdventureCourseGraphs(courseGraphs: CourseGraphSummary[]): CourseGraphSummary[] {
  return [...courseGraphs].sort((left, right) => {
    const leftOrder = typeof left.courseOrder === "number" && Number.isInteger(left.courseOrder) && left.courseOrder >= 0 ? left.courseOrder : null;
    const rightOrder = typeof right.courseOrder === "number" && Number.isInteger(right.courseOrder) && right.courseOrder >= 0 ? right.courseOrder : null;
    if (leftOrder !== null && rightOrder !== null && leftOrder !== rightOrder) return leftOrder - rightOrder;
    if (leftOrder !== null && rightOrder === null) return -1;
    if (leftOrder === null && rightOrder !== null) return 1;
    const leftTime = courseGraphTimestamp(left.createdAt);
    const rightTime = courseGraphTimestamp(right.createdAt);
    if (leftTime !== null && rightTime !== null && leftTime !== rightTime) return leftTime - rightTime;
    if (leftTime !== null && rightTime === null) return -1;
    if (leftTime === null && rightTime !== null) return 1;
    return left.id.localeCompare(right.id);
  });
}

export function courseChapterGridPosition(index: number, columns: number): AdventureCourseGridPosition {
  const normalizedColumns = Math.max(1, Math.floor(columns));
  const row = Math.floor(Math.max(0, index) / normalizedColumns);
  const positionInRow = Math.max(0, index) % normalizedColumns;
  return { row, column: row % 2 === 0 ? positionInRow : normalizedColumns - positionInRow - 1 };
}

export function deriveAdventureCourseGraphCards(
  courseGraphs: CourseGraphSummary[],
  assignments: EducationAssignment[],
): AdventureCourseGraphCard[] {
  const visibleAssignments = assignments.filter(assignment => assignment.status === "published");
  return sortAdventureCourseGraphs(courseGraphs).map(graph => ({
    graph,
    visibleAssignmentCount: visibleAssignments.filter(assignment => graph.snapshotIds.includes(assignment.snapshotId)).length,
  }));
}

export function deriveAdventureCourseGraphChapters(
  courseGraphs: CourseGraphSummary[],
  assignments: EducationAssignment[],
  recommendedAssignment: EducationAssignment | null = chooseRecommendedAssignment(assignments),
): AdventureCourseGraphChapter[] {
  const landmarks: AdventureCourseLandmark[] = ["academy", "observatory", "knowledge_gate", "lighthouse"];
  return deriveAdventureCourseGraphCards(courseGraphs, assignments).map((card, index) => ({
    ...card,
    chapter: index + 1,
    landmark: landmarks[index % landmarks.length],
    isRecommended: Boolean(recommendedAssignment && card.graph.snapshotIds.includes(recommendedAssignment.snapshotId)),
  }));
}

export function deriveDirectChallengeQuestionNodes(
  items: DirectChallengeQuestionSource[],
  attempts: Record<string, { status?: DirectChallengeQuestionState } | undefined>,
  assessments: Array<Pick<NodeAssessment, "nodeId" | "attemptStatus">>,
): DirectChallengeQuestionNode[] {
  return items.map(item => {
    const attemptStatus = attempts[String(item.nodeId)]?.status;
    const assessmentStatus = assessments.find(assessment => assessment.nodeId === item.nodeId)?.attemptStatus;
    const state = attemptStatus || assessmentStatus || "not_started";
    return { ...item, state: state === "completed" || state === "draft" ? state : "not_started" };
  });
}

export function chooseRecommendedDirectChallengeNode(nodes: DirectChallengeQuestionNode[]): DirectChallengeQuestionNode | null {
  return nodes.find(node => node.state === "draft") || nodes.find(node => node.state === "not_started") || null;
}

export function directChallengeCompletionCount(nodes: DirectChallengeQuestionNode[]): number {
  return nodes.filter(node => node.state === "completed").length;
}

export function isDirectChallengeReadyToSubmit(nodes: DirectChallengeQuestionNode[]): boolean {
  return nodes.length > 0 && nodes.every(node => node.state === "completed");
}

export function chooseRecommendedAssignment(
  assignments: EducationAssignment[],
  now = new Date(),
): EducationAssignment | null {
  const candidates = assignments
    .map(assignment => ({ assignment, state: deriveAssignmentAdventureState(assignment, now) }))
    .filter(item => item.state === "in_progress" || item.state === "available" || item.state === "overdue")
    .sort((left, right) => {
      const rank = (state: AdventureAssignmentState) => state === "in_progress" ? 0 : state === "available" ? 1 : 2;
      const stateDelta = rank(left.state) - rank(right.state);
      return stateDelta || compareAssignments(left.assignment, right.assignment);
    });
  return candidates[0]?.assignment || null;
}

function achievementAtlasAssignments(
  assignments: EducationAssignment[],
  snapshotIds?: string[],
): EducationAssignment[] {
  return assignments.filter(assignment => (
    assignment.assignmentType !== "direct"
    && assignment.status === "published"
    && (!snapshotIds || snapshotIds.includes(assignment.snapshotId))
  ));
}

export function chooseAchievementAtlasGraphForCourse(
  graph: CourseGraphSummary,
  assignments: EducationAssignment[],
  now = new Date(),
): AchievementAtlasGraphSelection {
  const related = achievementAtlasAssignments(assignments, graph.snapshotIds);
  const recommended = chooseRecommendedAssignment(related, now);
  const fallback = [...related].sort((left, right) => {
    const updatedDelta = (timestamp(right.updatedAt) ?? 0) - (timestamp(left.updatedAt) ?? 0);
    return updatedDelta || right.id.localeCompare(left.id);
  })[0];
  const assignment = recommended || fallback;
  return {
    graph,
    snapshotId: assignment?.snapshotId || graph.id,
    assignmentId: assignment?.id,
  };
}

export function chooseAchievementAtlasGraph(
  courseGraphs: CourseGraphSummary[],
  assignments: EducationAssignment[],
  now = new Date(),
): AchievementAtlasGraphSelection | null {
  if (!courseGraphs.length) return null;
  const recommended = chooseRecommendedAssignment(achievementAtlasAssignments(assignments), now);
  const recommendedGraph = recommended
    ? courseGraphs.find(graph => graph.snapshotIds.includes(recommended.snapshotId))
    : undefined;
  if (recommended && recommendedGraph) {
    return { graph: recommendedGraph, snapshotId: recommended.snapshotId, assignmentId: recommended.id };
  }
  return chooseAchievementAtlasGraphForCourse(courseGraphs[0], assignments, now);
}

export function deriveAchievementAtlasNodeStates(
  nodes: Array<{ id: number }>,
  assignments: EducationAssignment[],
  snapshotId: string,
): Record<number, AchievementAtlasNodeState> {
  const nodeIds = new Set(nodes.map(node => node.id));
  const states: Record<number, AchievementAtlasNodeState> = {};
  nodes.forEach(node => { states[node.id] = "unlearned"; });

  achievementAtlasAssignments(assignments, [snapshotId]).forEach(assignment => {
    const assessmentByNode = new Map(assignment.assessments.map(assessment => [assessment.nodeId, assessment]));
    assignment.path.steps.forEach(step => {
      if (!nodeIds.has(step.nodeId) || assessmentByNode.get(step.nodeId)?.status === "exempt") return;
      if (step.state === "needs_review") {
        states[step.nodeId] = "needs_review";
      } else if (step.state === "mastered" && states[step.nodeId] !== "needs_review") {
        states[step.nodeId] = "mastered";
      }
    });
  });
  return states;
}

export function deriveAchievementAtlasEdgeState(
  edge: { from: number; to: number },
  nodeStates: Record<number, AchievementAtlasNodeState>,
): AchievementAtlasEdgeState {
  const from = nodeStates[edge.from] || "unlearned";
  const to = nodeStates[edge.to] || "unlearned";
  if (from === "needs_review" || to === "needs_review") return "needs_review";
  if (from === "mastered" && to === "mastered") return "mastered";
  return "unlearned";
}

export function assignmentNeedsReview(assignment: EducationAssignment): boolean {
  return assignment.path.steps.some(step => step.state === "needs_review");
}
