import { useEffect, useRef, useState, type CSSProperties } from "react";
import { AlertTriangle, ArrowRight, BookOpen, CalendarDays, CheckCircle2, FileText, Flame, Flag, Gem, Gift, GitBranch, LockKeyhole, Map as MapIcon, Medal, RotateCcw, Route, Sparkles, Star, Trophy, X } from "lucide-react";
import {
  chooseAchievementAtlasGraph,
  chooseRecommendedAssignment,
  deriveAchievementAtlasNodeStates,
  courseChapterGridPosition,
  deriveAdventureCourseGraphChapters,
  deriveAdventureRegions,
  deriveAssignmentAdventureState,
  type AchievementAtlasGraphSelection,
  type AchievementAtlasNodeState,
  type AdventureAssignmentState,
  type AdventureCourseLandmark,
  type EducationGameSummary,
  type EducationLeaderboard,
  type EducationLevelRoadmapRewardKind,
  type EducationLevelRoadmapState,
} from "./education-game";
import type { CourseGraphSummary, EducationAssignment, EducationSnapshot } from "./education";
import { EducationAchievementAtlas } from "./EducationAchievementAtlas";
import { EducationCurrencyChestCelebration } from "./EducationCurrencyChestCelebration";

interface EducationAdventureMapProps {
  assignments: EducationAssignment[];
  courseGraphs: CourseGraphSummary[];
  summary: EducationGameSummary;
  formatDate: (value?: string | null) => string;
  onOpenAssignment: (assignmentId: string) => void;
  onOpenCourseGraph: (snapshotId: string) => void;
  loadCourseGraphSnapshot: (snapshotId: string) => Promise<EducationSnapshot>;
  onGameAction: (path: string, init?: RequestInit) => Promise<unknown>;
  onDialogOpenChange?: (open: boolean) => void;
}
function assignmentStateLabel(state: AdventureAssignmentState) {
  switch (state) {
    case "in_progress": return "进行中";
    case "awaiting_review": return "等待反馈";
    case "settled": return "已结算";
    case "overdue": return "逾期仍可完成";
    default: return "可开始";
  }
}

function assignmentActionLabel(state: AdventureAssignmentState, assignment: EducationAssignment) {
  if (state === "settled") return assignment.assignmentType === "direct" ? "查看结算" : "查看成绩";
  if (state === "awaiting_review") return "查看进度";
  if (state === "in_progress" || state === "overdue") return assignment.assignmentType === "direct" ? "继续挑战" : "继续学习";
  return assignment.assignmentType === "direct" ? "开始挑战" : "开始学习";
}

function assignmentProgress(assignment: EducationAssignment) {
  const items = assignment.path.steps.filter(step => assignment.assessments.find(item => item.nodeId === step.nodeId)?.status !== "exempt");
  const completed = items.filter(step => {
    const assessment = assignment.assessments.find(item => item.nodeId === step.nodeId);
    return assessment?.attemptStatus === "completed" || step.state === "mastered" || step.state === "needs_review";
  }).length;
  return { completed, total: items.length };
}

function AssignmentIcon({ assignment }: { assignment: EducationAssignment }) {
  return assignment.assignmentType === "direct" ? <FileText size={20} /> : <Route size={20} />;
}

function AssignmentStateIcon({ state }: { state: AdventureAssignmentState }) {
  if (state === "settled") return <CheckCircle2 size={15} />;
  if (state === "awaiting_review") return <RotateCcw size={15} />;
  if (state === "overdue") return <AlertTriangle size={15} />;
  return state === "in_progress" ? <BookOpen size={15} /> : <Flag size={15} />;
}

function CourseChapterLandmark({ landmark, landmarkRef }: { landmark: AdventureCourseLandmark; landmarkRef?: (node: HTMLSpanElement | null) => void }) {
  if (landmark === "observatory") return <span ref={landmarkRef} className="edu-adventure-course-landmark observatory"><MapIcon size={24} /><i /><b /></span>;
  if (landmark === "knowledge_gate") return <span ref={landmarkRef} className="edu-adventure-course-landmark knowledge-gate"><GitBranch size={24} /><i /><b /></span>;
  if (landmark === "lighthouse") return <span ref={landmarkRef} className="edu-adventure-course-landmark lighthouse"><Trophy size={22} /><i /><b /></span>;
  return <span ref={landmarkRef} className="edu-adventure-course-landmark academy"><BookOpen size={24} /><i /><b /></span>;
}

function samePaths(left: string[], right: string[]) {
  return left.length === right.length && left.every((path, index) => path === right[index]);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function asLeaderboard(value: unknown): EducationLeaderboard | null {
  if (!isRecord(value) || (value.kind !== "xp" && value.kind !== "gems") || !Array.isArray(value.entries)) return null;
  const entries = value.entries.map(item => isRecord(item) && typeof item.rank === "number" && typeof item.displayName === "string" && typeof item.score === "number" && typeof item.isSelf === "boolean" ? { rank: item.rank, displayName: item.displayName, score: item.score, isSelf: item.isSelf } : null);
  return entries.some(item => item === null) ? null : { kind: value.kind, entries: entries as EducationLeaderboard["entries"] };
}

function growthOptions(value: Record<string, unknown>): Array<{ key: string; title: string; description: string }> {
  return Array.isArray(value.options) ? value.options.flatMap(option => isRecord(option) && typeof option.key === "string" && typeof option.title === "string" && typeof option.description === "string" ? [{ key: option.key, title: option.title, description: option.description }] : []) : [];
}

function LevelRoadmapRewardIcon({ kind }: { kind: EducationLevelRoadmapRewardKind }) {
  if (kind === "badge") return <Star size={14} />;
  if (kind === "choice" || kind === "growth_chest") return <Gift size={14} />;
  return <Trophy size={14} />;
}

function LevelRoadmapStateIcon({ state }: { state: EducationLevelRoadmapState }) {
  if (state === "completed") return <CheckCircle2 size={13} />;
  if (state === "current") return <Sparkles size={13} />;
  return <LockKeyhole size={13} />;
}

function levelRoadmapStateLabel(state: EducationLevelRoadmapState) {
  if (state === "completed") return "已点亮";
  if (state === "current") return "当前等级";
  return "待解锁";
}

function achievementAtlasPreview(
  selection: AchievementAtlasGraphSelection | null,
  assignments: EducationAssignment[],
) {
  if (!selection) return { mastered: 0, needsReview: 0, states: [] as AchievementAtlasNodeState[] };
  const nodeIds: number[] = [];
  assignments.forEach(assignment => {
    if (assignment.assignmentType === "direct" || assignment.status !== "published" || assignment.snapshotId !== selection.snapshotId) return;
    assignment.path.steps.forEach(step => { if (!nodeIds.includes(step.nodeId)) nodeIds.push(step.nodeId); });
  });
  const states = deriveAchievementAtlasNodeStates(nodeIds.map(id => ({ id })), assignments, selection.snapshotId);
  const values = nodeIds.map(id => states[id]);
  const previewStates = [...values].sort((left, right) => {
    const rank = (state: AchievementAtlasNodeState) => state === "mastered" ? 0 : state === "needs_review" ? 1 : 2;
    return rank(left) - rank(right);
  }).slice(0, 5);
  while (previewStates.length < Math.min(5, selection.graph.nodeCount)) previewStates.push("unlearned");
  return {
    mastered: values.filter(state => state === "mastered").length,
    needsReview: values.filter(state => state === "needs_review").length,
    states: previewStates,
  };
}

export function EducationAdventureMap({ assignments, courseGraphs, summary, formatDate, onOpenAssignment, onOpenCourseGraph, loadCourseGraphSnapshot, onGameAction, onDialogOpenChange }: EducationAdventureMapProps) {
  const regions = deriveAdventureRegions(assignments, courseGraphs);
  const recommended = chooseRecommendedAssignment(assignments);
  const courseGraphChapters = deriveAdventureCourseGraphChapters(courseGraphs, assignments, recommended);
  const courseChapterKey = courseGraphChapters.map(chapter => chapter.graph.id).join("|");
  const recommendedRegion = recommended ? regions.find(region => region.assignments.some(item => item.id === recommended.id)) : undefined;
  const [atlasOpen, setAtlasOpen] = useState(false);
  const [growthOpen, setGrowthOpen] = useState(false);
  const [leaderboardOpen, setLeaderboardOpen] = useState(false);
  const knownLevelUpIdsRef = useRef<Set<string> | null>(null);
  const [leaderboardKind, setLeaderboardKind] = useState<"xp" | "gems">("xp");
  const [leaderboard, setLeaderboard] = useState<EducationLeaderboard | null>(null);
  const [gameBusy, setGameBusy] = useState("");
  const [gameMessage, setGameMessage] = useState("");
  const [courseMapColumns, setCourseMapColumns] = useState(4);
  const [courseMapPaths, setCourseMapPaths] = useState<string[]>([]);
  const [courseMapBounds, setCourseMapBounds] = useState({ width: 1, height: 1 });
  const courseMapRef = useRef<HTMLDivElement>(null);
  const courseLandmarkRefs = useRef(new Map<string, HTMLSpanElement>());
  const atlasSelection = chooseAchievementAtlasGraph(courseGraphs, assignments);
  const atlasPreview = achievementAtlasPreview(atlasSelection, assignments);
  const profile = summary.profile;
  const growth = summary.growth;
  const levelRoadmap = growth?.levelRoadmap || [];
  const latestLevelUp = growth?.unreadLevelUps.length ? growth.unreadLevelUps[growth.unreadLevelUps.length - 1] : null;
  const latestLevelNode = latestLevelUp ? levelRoadmap.find(item => item.level === latestLevelUp.level) : null;
  const unlockedGrowthCollectibles = growth?.collectibles.filter(item => item.type === "cosmetic" || item.type === "title") || [];
  const stageByKey = new Map((growth?.stages || []).map(stage => [stage.stageKey, stage]));
  const checkin = summary.checkin;
  const weeklyProgress = profile && profile.weeklyGoal > 0 ? Math.min(100, Math.round((profile.weeklyXp / profile.weeklyGoal) * 100)) : 0;
  const levelProgress = profile && profile.nextLevelXp > 0 ? Math.min(100, Math.round((profile.levelXp / profile.nextLevelXp) * 100)) : 0;
  const unlocked = summary.achievements.filter(item => item.unlocked);
  const needsReviewCount = assignments.reduce((count, assignment) => count + assignment.path.steps.filter(step => step.state === "needs_review").length, 0);

  useEffect(() => {
    const container = courseMapRef.current;
    if (!container || !courseGraphChapters.length) {
      setCourseMapPaths([]);
      return;
    }
    let frame = 0;
    const updateLayout = () => {
      const nextColumns = window.innerWidth < 480 ? 1 : window.innerWidth < 720 ? 2 : 4;
      setCourseMapColumns(previous => previous === nextColumns ? previous : nextColumns);
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        const current = courseMapRef.current;
        if (!current) return;
        const containerRect = current.getBoundingClientRect();
        const chapterIds = courseChapterKey ? courseChapterKey.split("|") : [];
        const points = chapterIds.map(id => {
          const landmark = courseLandmarkRefs.current.get(id);
          if (!landmark) return null;
          const rect = landmark.getBoundingClientRect();
          return { x: rect.left - containerRect.left + rect.width / 2, y: rect.top - containerRect.top + rect.height / 2 };
        });
        const nextPaths: string[] = [];
        for (let index = 1; index < points.length; index += 1) {
          const start = points[index - 1];
          const end = points[index];
          if (!start || !end) continue;
          const previousPosition = courseChapterGridPosition(index - 1, nextColumns);
          const nextPosition = courseChapterGridPosition(index, nextColumns);
          if (previousPosition.row === nextPosition.row) {
            const bend = Math.min(32, Math.max(16, Math.abs(end.x - start.x) * 0.18));
            nextPaths.push(`M ${start.x} ${start.y} C ${start.x + (end.x - start.x) * 0.32} ${start.y - bend}, ${start.x + (end.x - start.x) * 0.68} ${end.y - bend}, ${end.x} ${end.y}`);
          } else {
            const turn = start.x >= containerRect.width / 2 ? 46 : -46;
            nextPaths.push(`M ${start.x} ${start.y} C ${start.x + turn} ${start.y + 30}, ${end.x + turn} ${end.y - 30}, ${end.x} ${end.y}`);
          }
        }
        setCourseMapPaths(previous => samePaths(previous, nextPaths) ? previous : nextPaths);
        setCourseMapBounds(previous => (
          previous.width === Math.max(1, Math.round(containerRect.width)) && previous.height === Math.max(1, Math.round(containerRect.height))
            ? previous
            : { width: Math.max(1, Math.round(containerRect.width)), height: Math.max(1, Math.round(containerRect.height)) }
        ));
      });
    };
    updateLayout();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(updateLayout);
    observer?.observe(container);
    window.addEventListener("resize", updateLayout);
    return () => {
      window.cancelAnimationFrame(frame);
      observer?.disconnect();
      window.removeEventListener("resize", updateLayout);
    };
  }, [courseChapterKey, courseGraphChapters.length, courseMapColumns]);

  useEffect(() => {
    if (!growth) return;
    const unreadIds = growth.unreadLevelUps.map(item => item.id);
    if (knownLevelUpIdsRef.current === null) {
      knownLevelUpIdsRef.current = new Set(unreadIds);
      return;
    }
    const knownIds = knownLevelUpIdsRef.current;
    const hasNewLevelUp = unreadIds.some(id => !knownIds.has(id));
    unreadIds.forEach(id => knownIds.add(id));
    if (hasNewLevelUp) setGrowthOpen(true);
  }, [growth]);

  const anyDialogOpen = atlasOpen || growthOpen || leaderboardOpen;

  useEffect(() => {
    onDialogOpenChange?.(anyDialogOpen);
  }, [anyDialogOpen, onDialogOpenChange]);

  useEffect(() => () => {
    onDialogOpenChange?.(false);
  }, [onDialogOpenChange]);

  const runGameAction = async (label: string, path: string, init?: RequestInit) => {
    setGameBusy(label);
    setGameMessage("");
    try {
      return await onGameAction(path, init);
    } catch (cause) {
      setGameMessage(cause instanceof Error ? cause.message : "操作未完成，请稍后再试。");
      return null;
    } finally {
      setGameBusy("");
    }
  };

  const markGameNotificationsSeen = async (growthIds: string[] = [], chestIds: string[] = []) => {
    if (!growthIds.length && !chestIds.length) return true;
    const result = await runGameAction("game-notifications", "/game-notifications/seen", { method: "POST", body: JSON.stringify({ growthIds, chestIds }) });
    return result !== null;
  };

  const closeGrowth = async () => {
    setGrowthOpen(false);
    const growthIds = [
      ...(growth?.unreadLevelUps || []).map(reward => reward.id),
      ...(growth?.growthChests || []).filter(reward => !reward.seenAt).map(reward => reward.id),
      ...(growth?.permanentTitles || []).filter(reward => !reward.seenAt).map(reward => reward.id),
    ];
    await markGameNotificationsSeen(Array.from(new Set(growthIds)));
  };


  const openLeaderboard = async (kind: "xp" | "gems") => {
    setLeaderboardKind(kind);
    setLeaderboardOpen(true);
    const result = await runGameAction("leaderboard", "/leaderboards?kind=" + kind);
    const parsed = asLeaderboard(result);
    if (parsed) setLeaderboard(parsed);
  };

  const claimChoice = async (rewardId: string, optionKey: string) => {
    await runGameAction("growth-choice", "/growth-rewards/" + encodeURIComponent(rewardId) + "/claim", { method: "POST", body: JSON.stringify({ optionKey }) });
  };

  return (
    <section className="edu-adventure-layout" aria-label="课程探索地图">
      <div className="edu-adventure-main">
        {gameMessage && <div className="edu-adventure-game-message" role="status">{gameMessage}</div>}
        {recommended && (
          <section className="edu-adventure-hero">
            <div className="edu-adventure-hero-copy">
              <span className="edu-kicker">{recommendedRegion?.kind === "challenge" ? "作业挑战区" : `课程阶段 · ${recommendedRegion?.title || "当前进度"}`}</span>
              <h2>{recommended.title}</h2>
              <p>{recommended.assignmentType === "direct" ? "完成这次题目挑战，继续积累你的学习进度。" : "沿着课程图谱的前置关系，继续确认自己的理解。"}</p>
            </div>
            <button type="button" className="edu-button primary" onClick={() => onOpenAssignment(recommended.id)}>{assignmentActionLabel(deriveAssignmentAdventureState(recommended), recommended)}<ArrowRight size={14} /></button>
          </section>
        )}

        <section className="edu-adventure-graph-library" aria-labelledby="edu-adventure-graph-library-title">
          <header className="edu-adventure-graph-library-head">
            <div className="edu-adventure-region-title">
              <span className="edu-adventure-region-icon"><GitBranch size={17} /></span>
              <div>
                <span className="edu-kicker">课程资源</span>
                <h2 id="edu-adventure-graph-library-title">课程图谱 <span>{courseGraphChapters.length}</span></h2>
                <small>教师加入班级后即可浏览课程节点、关系和教材原文</small>
              </div>
            </div>
            {courseGraphChapters.length > 0 && <div className="edu-adventure-graph-library-progress">
              <span><Flag size={15} /></span>
              <div><strong>课程进行中</strong><small>新章节发布后路线将在这里继续延伸</small></div>
            </div>}
          </header>
          {courseGraphChapters.length ? (
            <div
              ref={courseMapRef}
              className={`edu-adventure-course-map columns-${courseMapColumns}`}
              style={{ "--edu-course-map-columns": courseMapColumns } as CSSProperties}
            >
              <svg className="edu-adventure-course-map-paths" viewBox={`0 0 ${courseMapBounds.width} ${courseMapBounds.height}`} preserveAspectRatio="none" aria-hidden="true">
                {courseMapPaths.map((path, index) => <path key={`${index}:${path}`} d={path} />)}
              </svg>
              {courseGraphChapters.map((chapter, index) => {
                const position = courseChapterGridPosition(index, courseMapColumns);
                const stageKey = chapter.graph.sourceGraphId ? `source:${chapter.graph.sourceGraphId}` : `snapshot:${chapter.graph.id}`;
                const stage = stageByKey.get(stageKey);
                return (
                  <button
                    type="button"
                    className={`edu-adventure-course-chapter ${chapter.landmark}${chapter.isRecommended ? " recommended" : ""}`}
                    key={chapter.graph.id}
                    style={{ gridColumn: position.column + 1, gridRow: position.row + 1 }}
                    onClick={() => onOpenCourseGraph(chapter.graph.id)}
                    aria-current={chapter.isRecommended ? "step" : undefined}
                    aria-label={`查看第 ${chapter.chapter} 章课程图谱：${chapter.graph.filename}`}
                  >
                    {chapter.isRecommended && <span className="edu-adventure-course-current">当前章节</span>}
                    <CourseChapterLandmark landmark={chapter.landmark} landmarkRef={node => {
                      if (node) courseLandmarkRefs.current.set(chapter.graph.id, node);
                      else courseLandmarkRefs.current.delete(chapter.graph.id);
                    }} />
                    <span className="edu-adventure-course-chapter-copy">
                      <small>第 {chapter.chapter} 章</small>
                      <strong>{chapter.graph.filename}</strong>
                      <span>{chapter.graph.nodeCount} 个节点 · {chapter.graph.edgeCount} 条关系</span>
                    </span>
                    <span className={`edu-adventure-course-chapter-status ${chapter.visibleAssignmentCount ? "has-assignments" : "browse-only"}`}>
                      {chapter.visibleAssignmentCount ? `包含 ${chapter.visibleAssignmentCount} 个学习任务` : "可自由浏览"}
                    </span>
                    {stage && <span className="edu-adventure-course-stage" aria-label={`阶段成长路线：${stage.currentXp} / ${stage.goalXp} XP`}><small>成长路线 {stage.currentXp} / {stage.goalXp} XP</small><i><em style={{ width: `${Math.min(100, Math.round(stage.currentXp / Math.max(1, stage.goalXp) * 100))}%` }} /></i><ol>{stage.milestones.map(node => <li className={node.completed ? "done" : ""} key={node.percent}>{node.percent}%</li>)}</ol></span>}
                  </button>
                );
              })}
            </div>
          ) : (
            <p className="edu-adventure-graph-library-empty">{regions.length ? "教师尚未加入课程图谱。" : "教师尚未提供课程图谱或学习任务。"}</p>
          )}
        </section>

        {regions.map(region => (
          <section className="edu-adventure-region" key={region.id}>
            <header className="edu-adventure-region-head">
              <div className="edu-adventure-region-title"><span className="edu-adventure-region-icon"><Route size={17} /></span><div><span className="edu-kicker">{region.kind === "challenge" ? "挑战区域" : "课程阶段"}</span><h2>{region.title}</h2><small>{region.subtitle}</small></div></div>
              {region.courseGraph && <button type="button" className="edu-button ghost" onClick={() => onOpenCourseGraph(region.courseGraph!.id)}><BookOpen size={14} />查看图谱</button>}
            </header>
            <div className="edu-adventure-path">
              {region.assignments.map((assignment, index) => {
                const state = deriveAssignmentAdventureState(assignment);
                const progress = assignmentProgress(assignment);
                const isRecommended = recommended?.id === assignment.id;
                const isLockedChallenge = assignment.assignmentType === "direct" && assignment.growthChallenge?.locked;
                const unlockLabel = assignment.growthChallenge?.requiredLevel ? `等级 ${assignment.growthChallenge.requiredLevel} 解锁` : assignment.growthChallenge?.requiredStageMilestone ? `完成阶段 ${assignment.growthChallenge.requiredStageMilestone}% 解锁` : "成长条件解锁";
                return (
                  <div className={`edu-adventure-stop-wrap ${index % 2 ? "offset" : ""}`} key={assignment.id}>
                    {index > 0 && <span className="edu-adventure-connector" aria-hidden="true" />}
                    <div className={`edu-adventure-stop ${state}${isRecommended ? " recommended" : ""}${isLockedChallenge ? " growth-locked" : ""}`}>
                      {isRecommended && !isLockedChallenge && <span id={`edu-adventure-recommendation-${assignment.id}`} className="edu-adventure-recommendation">推荐下一步</span>}
                      <button type="button" className="edu-adventure-stop-button" disabled={isLockedChallenge} onClick={() => onOpenAssignment(assignment.id)} aria-current={isRecommended && !isLockedChallenge ? "step" : undefined} aria-describedby={isRecommended && !isLockedChallenge ? `edu-adventure-recommendation-${assignment.id}` : undefined}>
                        <span className="edu-adventure-stop-node">{isLockedChallenge ? <LockKeyhole size={20} /> : <AssignmentIcon assignment={assignment} />}</span>
                        <span className="edu-adventure-stop-copy"><strong>{assignment.title}</strong><small>{isLockedChallenge ? `成长挑战 · ${unlockLabel}` : `${assignment.assignmentType === "direct" ? "题目挑战" : "图谱学习任务"} · ${progress.completed}/${progress.total} 已完成`}</small></span>
                        <span className={`edu-adventure-stop-state ${isLockedChallenge ? "locked" : state}`}>{isLockedChallenge ? <LockKeyhole size={15} /> : <AssignmentStateIcon state={state} />}{isLockedChallenge ? "暂未解锁" : assignmentStateLabel(state)}</span>
                      </button>
                      <div className="edu-adventure-stop-meta"><span>{assignment.dueAt ? <><CalendarDays size={12} />截止 {formatDate(assignment.dueAt)}</> : "未设置截止时间"}</span><span>{isLockedChallenge ? unlockLabel : assignmentActionLabel(state, assignment)}</span></div>
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        ))}
        {!regions.length && courseGraphChapters.length > 0 && <div className="edu-empty compact"><Route size={25} /><span>暂无待完成任务，可以先浏览上方课程图谱。</span></div>}
      </div>

      <aside className="edu-adventure-rail">
        <section className="edu-card edu-adventure-card edu-adventure-weekly-card">
          <div className="edu-card-title"><Flame size={18} /><div><strong>本周学习目标</strong><small>{checkin?.weeklyGenuineDays ?? profile?.activeDaysThisWeek ?? 0} 个有效学习日</small></div></div>
          <div className="edu-adventure-stat-row"><strong>{profile?.weeklyXp ?? 0}</strong><span>/ {profile?.weeklyGoal ?? summary.settings.weeklyXpGoal} XP</span></div>
          <div className="edu-adventure-progress-bar"><span style={{ width: String(weeklyProgress) + "%" }} /></div>
          <small className="edu-adventure-progress-note">{weeklyProgress >= 100 ? "本周目标已达成，已推进班级经验条" : "还差 " + Math.max(0, (profile?.weeklyGoal ?? summary.settings.weeklyXpGoal) - (profile?.weeklyXp ?? 0)) + " XP 达标"}</small>
          {growth && <div className="edu-class-xp-mini"><span>班级等级 {growth.classXp.level}</span><b>{growth.classXp.levelXp} / {growth.classXp.levelGoal} XP</b><i><em style={{ width: String(Math.min(100, Math.round(growth.classXp.levelXp / Math.max(1, growth.classXp.levelGoal) * 100))) + "%" }} /></i><small>本周 {growth.classXp.weeklyGoalCompleters} 人达标</small></div>}
        </section>

        <section className="edu-card edu-adventure-card edu-growth-card">
          <button
            type="button"
            className="edu-growth-card-button"
            onClick={() => setGrowthOpen(true)}
            aria-label={profile ? `查看等级进阶路线，当前等级 ${profile.level}，本级进度 ${profile.levelXp} / ${profile.nextLevelXp} XP` : "查看等级进阶路线，奖励数据同步中"}
          >
            <div className="edu-growth-card-head">
              <span className="edu-growth-card-icon" aria-hidden="true"><Trophy size={19} /></span>
              <div className="edu-growth-card-heading">
                <strong>探索等级</strong>
                {growth ? <span className="edu-growth-tier-badge">第 {growth.badgeTier} 阶 · {growth.badgeStars} 星徽章</span> : <small>等级数据同步中</small>}
              </div>
            </div>
            <div className="edu-growth-level-summary">
              <div className="edu-growth-level-primary"><small>当前等级</small><strong><span>Lv.</span>{profile?.level ?? "—"}</strong></div>
              <div className="edu-growth-total-xp"><small>累计经验</small><strong>{profile ? profile.totalXp.toLocaleString("zh-CN") : "—"}<span> XP</span></strong></div>
            </div>
            <div className="edu-growth-progress-block">
              <div className="edu-growth-progress-meta"><span>本级进度</span><strong>{profile ? profile.levelXp.toLocaleString("zh-CN") + " / " + profile.nextLevelXp.toLocaleString("zh-CN") + " XP" : "奖励数据同步中"}</strong></div>
              <div className="edu-adventure-progress-bar level edu-growth-progress-bar"><span style={{ width: String(levelProgress) + "%" }} /></div>
            </div>
            <span className={`edu-growth-card-action${growth?.pendingFiveLevelChoices.length ? " pending" : ""}`}>
              <span>{growth?.pendingFiveLevelChoices.length ? <><Gift size={13} />有 {growth.pendingFiveLevelChoices.length} 个成长选择待领取</> : "查看等级进阶路线"}</span>
              <ArrowRight size={14} aria-hidden="true" />
            </span>
          </button>
        </section>

        <section className="edu-card edu-adventure-card edu-leaderboard-card">
          <div className="edu-card-title"><Medal size={18} /><div><strong>课程排行榜</strong><small>成长与货币分开统计</small></div></div>
          <div className="edu-leaderboard-actions"><button type="button" onClick={() => void openLeaderboard("xp")}><Trophy size={14} />经验榜</button><button type="button" onClick={() => void openLeaderboard("gems")}><Gem size={14} />宝石榜</button></div>
        </section>

        <section className="edu-card edu-adventure-card edu-atlas-card">
          <div className="edu-card-title"><MapIcon size={18} /><div><strong>成果图鉴</strong><small>{unlocked.length} / {summary.achievements.length} 项探索成就</small></div></div>
          <div className="edu-atlas-card-preview" aria-hidden="true">
            <svg viewBox="0 0 220 62" preserveAspectRatio="none"><path d="M10 45 C48 8, 78 53, 112 25 S174 8, 210 37" /></svg>
            <div className="edu-atlas-card-dots">
              {atlasPreview.states.map((state, index) => <span className={state} style={{ left: `${12 + index * 20}%`, top: `${index % 2 ? 48 : 22}%` }} key={`${state}:${index}`} />)}
            </div>
          </div>
          {atlasSelection ? <div className="edu-atlas-card-metrics"><span><b>{atlasPreview.mastered}</b> 已点亮</span><span className="needs-review"><b>{atlasPreview.needsReview}</b> 待复习</span></div> : <small className="edu-atlas-card-empty">教师加入课程图谱后，这里会生成你的成果地图。</small>}
          <button type="button" className="edu-button ghost edu-adventure-card-button" disabled={!atlasSelection} onClick={() => setAtlasOpen(true)}>打开成果地图<ArrowRight size={13} /></button>
        </section>

        {needsReviewCount > 0 && <section className="edu-card edu-adventure-card edu-adventure-review-card"><div className="edu-card-title"><AlertTriangle size={18} /><div><strong>待复习提醒</strong><small>{needsReviewCount} 个节点需要回看</small></div></div><p>回到相关课程，重新查看原文和自己的理解记录。</p></section>}
      </aside>
      {atlasOpen && <EducationAchievementAtlas
        assignments={assignments}
        courseGraphs={courseGraphs}
        initialSelection={atlasSelection}
        achievements={summary.achievements}
        loadCourseGraphSnapshot={loadCourseGraphSnapshot}
        onOpenCourseGraph={onOpenCourseGraph}
        onClose={() => setAtlasOpen(false)}
      />}
      {growthOpen && <div className="edu-game-dialog-backdrop" role="presentation" onMouseDown={() => void closeGrowth()}><section className="edu-game-dialog edu-growth-dialog" role="dialog" aria-modal="true" aria-label="等级进阶路线" onMouseDown={event => event.stopPropagation()}>
        <header><div><span className="edu-kicker">等级进阶路线</span><h2>探索等级 {profile?.level ?? "—"}</h2><p>完成学习任务获得 XP，逐级点亮徽章并解锁成长奖励。</p></div><button type="button" aria-label="关闭等级进阶路线" onClick={() => void closeGrowth()}><X size={18} /></button></header>
        {latestLevelUp && <div className="edu-level-up-banner"><Sparkles size={21} /><div><span>等级提升</span><strong>{growth && growth.unreadLevelUps.length > 1 ? `连续提升 ${growth.unreadLevelUps.length} 级，已到达等级 ${latestLevelUp.level}` : `恭喜升至等级 ${latestLevelUp.level}`}</strong><small>第 {latestLevelNode?.badgeTier ?? growth?.badgeTier ?? "—"} 阶 {latestLevelNode?.badgeStars ?? growth?.badgeStars ?? "—"} 星徽章已点亮，新的等级路线已经开启。</small></div></div>}
        <div className="edu-level-roadmap-overview">
          <div className="edu-level-current-badge"><span><Trophy size={22} /></span><div><small>当前徽章</small><strong>{growth ? `第 ${growth.badgeTier} 阶 · ${growth.badgeStars} 星` : "成长数据同步中"}</strong></div></div>
          <div className="edu-level-current-progress"><div><span>当前升级进度</span><strong>{profile ? `${profile.levelXp.toLocaleString("zh-CN")} / ${profile.nextLevelXp.toLocaleString("zh-CN")} XP` : "同步中"}</strong></div><div className="edu-adventure-progress-bar level"><span style={{ width: String(levelProgress) + "%" }} /></div><small>{profile ? `再获得 ${Math.max(0, profile.nextLevelXp - profile.levelXp)} XP 升至等级 ${profile.level + 1}` : "正在同步等级进度"}</small></div>
        </div>
        <section className="edu-level-roadmap-section">
          <header><div><h3>等级路线</h3><p>每一级都会点亮新的徽章，里程碑等级还会解锁额外奖励。</p></div>{levelRoadmap.length > 0 && <span>等级 {levelRoadmap[0].level}—{levelRoadmap[levelRoadmap.length - 1].level}</span>}</header>
          {levelRoadmap.length ? <ol className="edu-level-roadmap-list">{levelRoadmap.map(item => {
            const pendingChoice = growth?.pendingFiveLevelChoices.find(reward => reward.level === item.level);
            return <li className={item.state} key={item.level}>
              <span className="edu-level-roadmap-badge" aria-hidden="true"><Medal size={20} /><span>{Array.from({ length: item.badgeStars }, (_, index) => <Star size={8} fill="currentColor" key={index} />)}</span></span>
              <article className="edu-level-roadmap-card">
                <header><div><small>等级 {item.level}</small><strong>第 {item.badgeTier} 阶 · {item.badgeStars} 星徽章</strong></div><span className={`edu-level-roadmap-state ${item.state}`}><LevelRoadmapStateIcon state={item.state} />{levelRoadmapStateLabel(item.state)}</span></header>
                <div className="edu-level-roadmap-rewards">{item.rewards.map(reward => <span className={reward.kind} key={reward.kind}><LevelRoadmapRewardIcon kind={reward.kind} /><span><b>{reward.title}</b><small>{reward.description}</small></span></span>)}</div>
                {pendingChoice && <div className="edu-level-roadmap-choice"><strong>选择本级成长奖励</strong><small>可以稍后领取，选择后会加入你的课程奖励栏。</small><div className="edu-growth-choice-options">{growthOptions(pendingChoice.payload).map(option => <button type="button" disabled={Boolean(gameBusy)} key={option.key} onClick={() => void claimChoice(pendingChoice.id, option.key)}><b>{option.title}</b><small>{option.description}</small></button>)}</div></div>}
              </article>
            </li>;
          })}</ol> : <div className="edu-level-roadmap-empty"><Medal size={20} /><span>等级路线数据同步中，请稍后再试。</span></div>}
        </section>
        {unlockedGrowthCollectibles.length > 0 && <section className="edu-growth-unlocked"><div><h3>已解锁奖励</h3><small>选择一个课程外观或称号进行展示。</small></div><div className="edu-collectibles">{unlockedGrowthCollectibles.map(item => <button type="button" key={item.key} className={item.equipped ? "equipped" : ""} disabled={Boolean(gameBusy)} onClick={() => void runGameAction("equip", "/collectibles/equip", { method: "POST", body: JSON.stringify({ collectibleKey: item.key }) })}><b>{item.title}</b><small>{item.equipped ? "已装备" : "点击装备"}</small></button>)}</div></section>}
      </section></div>}
      <EducationCurrencyChestCelebration chests={summary.unreadCurrencyRewards} onAcknowledge={chestId => markGameNotificationsSeen([], [chestId])} />
      {leaderboardOpen && <div className="edu-game-dialog-backdrop" role="presentation" onMouseDown={() => setLeaderboardOpen(false)}><section className="edu-game-dialog edu-leaderboard-dialog" role="dialog" aria-modal="true" aria-label="课程排行榜" onMouseDown={event => event.stopPropagation()}>
        <header><div><span className="edu-kicker">课程公开展示</span><h2>课程排行榜</h2><p>{leaderboardKind === "xp" ? "经验榜按课程累计 XP 排名。" : "宝石榜按课程累计获得宝石排名。"}</p></div><button type="button" aria-label="关闭排行榜" onClick={() => setLeaderboardOpen(false)}><X size={18} /></button></header>
        <div className="edu-leaderboard-tabs"><button type="button" className={leaderboardKind === "xp" ? "active" : ""} onClick={() => void openLeaderboard("xp")}><Trophy size={15} />经验榜</button><button type="button" className={leaderboardKind === "gems" ? "active" : ""} onClick={() => void openLeaderboard("gems")}><Gem size={15} />宝石榜</button></div>
        <ol className="edu-leaderboard-list">{leaderboard?.entries.map(entry => <li className={entry.isSelf ? "self" : ""} key={entry.rank + entry.displayName}><b>{entry.rank}</b><span>{entry.displayName}{entry.isSelf ? "（你）" : ""}</span><strong>{entry.score}{leaderboardKind === "xp" ? " XP" : " 宝石"}</strong></li>)}</ol>
      </section></div>}
    </section>
  );
}

