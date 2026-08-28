import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle, ArrowLeft, BookOpen, Brain, Check, CheckCircle2, ChevronLeft, ChevronRight, ClipboardList,
  Copy, FileJson, FileText, GitBranch, GraduationCap, Loader2, Pencil, Plus, Route, Save, School, Send, Settings, Trash2, Upload, Users, X,
} from "lucide-react";
import { apiUrl } from "~/api";
import type { EducationRole, GraphNode, GraphResult } from "./home";
import { MathText } from "./math";
import { loadStudioSettings, resolveTheme } from "./studio-graph";
import "./education.css";


export type LearningStepState = "not_started" | "in_progress" | "mastered" | "needs_review";

export interface LearningPathStep {
  nodeId: number;
  order: number;
  stage?: number;
  role: "prerequisite" | "remedial" | "target";
  required: boolean;
  rationale: string;
  state: LearningStepState;
  cycle?: boolean;
}

export interface LearningPath {
  targetNodeId: number;
  summary: string;
  steps: LearningPathStep[];
  edges: Array<{ from: number; to: number; label?: string; description?: string }>;
  candidateNodeIds: number[];
  hasCycles?: boolean;
  aiEnhanced?: boolean;
}

export type AssessmentStatus = "pending" | "ready" | "failed" | "exempt";
export type AssessmentAttemptStatus = "not_started" | "draft" | "completed";

export interface AssessmentQuestion {
  id: string;
  nodeId: number;
  kind: string;
  order: number;
  question: string;
  focus: string;
  expectedPoints?: string[];
  referenceAnswer?: string;
  maxScore?: number;
  referenceMatrixReport?: MatrixCheckReport;
}

export interface NodeAssessment {
  nodeId: number;
  status: AssessmentStatus;
  questionCount: number;
  updatedAt: string;
  generationError?: string;
  generationErrorCode?: string;
  questions?: AssessmentQuestion[];
  attemptStatus?: AssessmentAttemptStatus;
  attemptUpdatedAt?: string | null;
}

export const ASSESSMENT_REGENERATION_CONCURRENCY = 4;

export type AssessmentOperationKind =
  | "regenerate_node"
  | "regenerate_question"
  | "delete_question"
  | "exempt_node";

export interface AssessmentOperation {
  id: string;
  assignmentId: string;
  nodeId: number;
  kind: AssessmentOperationKind;
  questionId?: string;
  usesAi: boolean;
  status: "queued" | "running";
}

export interface AssessmentOperationQueueState {
  operations: AssessmentOperation[];
}

export type AssessmentOperationAction =
  | { type: "enqueue"; operation: AssessmentOperation }
  | { type: "complete"; operationId: string }
  | { type: "reset" };

function promoteAssessmentOperations(
  operations: AssessmentOperation[],
  maxAiRunning = ASSESSMENT_REGENERATION_CONCURRENCY,
) {
  let aiRunning = operations.filter(operation => operation.status === "running" && operation.usesAi).length;
  return operations.map(operation => {
    if (operation.status === "running") return operation;
    if (operation.usesAi && aiRunning >= maxAiRunning) return operation;
    if (operation.usesAi) aiRunning += 1;
    return { ...operation, status: "running" as const };
  });
}

export function assessmentOperationReducer(
  state: AssessmentOperationQueueState,
  action: AssessmentOperationAction,
): AssessmentOperationQueueState {
  if (action.type === "reset") {
    return { operations: [] };
  }
  if (action.type === "enqueue") {
    if (state.operations.some(operation => operation.nodeId === action.operation.nodeId)) return state;
    return { operations: promoteAssessmentOperations([...state.operations, action.operation]) };
  }
  return {
    operations: promoteAssessmentOperations(
      state.operations.filter(operation => operation.id !== action.operationId),
    ),
  };
}

export function assessmentOperationForNode(
  state: AssessmentOperationQueueState,
  nodeId: number,
) {
  return state.operations.find(operation => operation.nodeId === nodeId);
}

export function assessmentOperationCounts(state: AssessmentOperationQueueState) {
  return state.operations.reduce(
    (counts, operation) => {
      counts[operation.status] += 1;
      return counts;
    },
    { queued: 0, running: 0 },
  );
}

export interface AssessmentAttempt {
  id: string;
  assignmentId: string;
  nodeId: number;
  status: "draft" | "completed";
  answers: Record<string, string>;
  questions: AssessmentQuestion[];
  updatedAt: string;
  completedAt?: string | null;
}

export interface EducationClass {
  id: string;
  title: string;
  inviteCode?: string | null;
  role: "teacher" | "student";
  memberCount?: number;
  assignmentCount?: number;
  studentName?: string | null;
  studentNumber?: string | null;
  profileComplete?: boolean;
  createdAt: string;
}

export interface EducationClassMember {
  userId: number;
  studentName?: string | null;
  studentNumber?: string | null;
  profileComplete?: boolean;
  joinedAt: string;
  status: "active" | "removed";
  removedAt?: string | null;
}

export interface EducationSnapshotSummary {
  id: string;
  classId: string;
  sourceGraphId?: string | null;
  filename: string;
  nodeCount: number;
  edgeCount: number;
  boundAssignmentCount?: number;
  createdAt: string;
}

export interface CourseGraphSummary extends EducationSnapshotSummary {
  snapshotIds: string[];
}

export interface EducationSnapshot extends EducationSnapshotSummary {
  nodes: GraphNode[];
  edges: GraphResult["edges"];
  sourceMarkdown?: string;
  latexMacros?: GraphResult["latex_macros"];
  sourcePdf?: GraphResult["source_pdf"];
}

export interface EducationAssignment {
  id: string;
  classId: string;
  snapshotId: string;
  title: string;
  targetNodeId: number;
  dueAt?: string | null;
  status: "draft" | "published" | "archived";
  summary: string;
  version: number;
  publishedAt?: string | null;
  updatedAt: string;
  role: "teacher" | "student";
  path: LearningPath;
  assessments: NodeAssessment[];
  gradesPublishedAt?: string | null;
  submission?: EducationSubmissionSummary | null;
  snapshot?: EducationSnapshot;
}

export function unresolvedAssessmentNodeIds(assignment: EducationAssignment): number[] {
  return assignment.path.steps
    .filter(step => {
      const assessment = assignment.assessments.find(item => item.nodeId === step.nodeId);
      return !assessment
        || assessment.status === "pending"
        || assessment.status === "failed"
        || (assessment.status === "ready" && assessment.questionCount < 1);
    })
    .map(step => step.nodeId);
}

export function assessmentScoringSummary(assignment: EducationAssignment) {
  const questions = assignment.assessments
    .filter(assessment => assessment.status === "ready")
    .flatMap(assessment => assessment.questions || []);
  const totalScore = Math.round(questions.reduce((total, question) => total + Number(question.maxScore || 0), 0) * 10) / 10;
  const referenceInvalidQuestionIds = questions
    .filter(question => question.referenceMatrixReport?.status === "contradicted" || question.referenceMatrixReport?.status === "structural_invalid")
    .map(question => question.id);
  const invalidQuestionIds = questions
    .filter(question => Number(question.maxScore || 0) <= 0 || !question.referenceAnswer?.trim() || !question.expectedPoints?.some(point => point.trim()) || referenceInvalidQuestionIds.includes(question.id))
    .map(question => question.id);
  const unresolvedNodeIds = unresolvedAssessmentNodeIds(assignment);
  const totalReady = questions.length === 0 || Math.abs(totalScore - 100) < 0.05;
  return { questions, totalScore, invalidQuestionIds, referenceInvalidQuestionIds, unresolvedNodeIds, ready: totalReady && invalidQuestionIds.length === 0 && unresolvedNodeIds.length === 0 };
}

export function studentAssignmentCompletion(assignment: EducationAssignment) {
  const assessmentByNode = new Map(assignment.assessments.map(assessment => [assessment.nodeId, assessment]));
  const requiredSteps = assignment.path.steps.filter(step => assessmentByNode.get(step.nodeId)?.status !== "exempt");
  const completedSteps = requiredSteps.filter(step => {
    const assessment = assessmentByNode.get(step.nodeId);
    return assessment?.status === "ready" && assessment.questionCount > 0 && assessment.attemptStatus === "completed";
  });
  return { completed: completedSteps.length, total: requiredSteps.length, ready: completedSteps.length === requiredSteps.length };
}

export type SubmissionStatus = "submitted" | "review_draft" | "finalized" | "released";
export type SubmissionAiStatus = "not_started" | "running" | "ready" | "failed";

export interface EducationSubmissionSummary {
  id: string;
  status: SubmissionStatus;
  submittedAt: string;
  updatedAt: string;
  releasedAt?: string | null;
  teacherTotal?: number | null;
  teacherSummary?: string;
}

export interface MatrixCheckIssue {
  code: string;
  message: string;
  sourceExcerpt?: string;
  mismatchedCells?: Array<{ row: number; column: number; expected: string; actual: string }>;
  expected?: string | null;
  actual?: string | null;
}

export interface MatrixCheckReport {
  status: "not_applicable" | "verified" | "contradicted" | "indeterminate" | "structural_invalid";
  summary: string;
  issues: MatrixCheckIssue[];
  flowCount: number;
  referenceFlowCount: number;
  referenceStatus?: string;
}

export interface AiQuestionGrade {
  suggestedScore?: number;
  maxScore?: number;
  rationale?: string;
  correctPoints?: string[];
  issues?: string[];
  studentFeedback?: string;
  confidence?: number;
  needsTeacherReview?: boolean;
}

export interface QuestionGrade {
  questionId: string;
  nodeId: number;
  kind: string;
  order: number;
  question: string;
  focus: string;
  studentAnswer: string;
  referenceAnswer: string;
  expectedPoints: string[];
  maxScore: number;
  matrixReport: MatrixCheckReport | Record<string, never>;
  aiResult: AiQuestionGrade;
  aiSuggestedScore?: number | null;
  teacherScore?: number | null;
  teacherFeedback: string;
}

export interface EducationSubmission extends EducationSubmissionSummary {
  assignmentId: string;
  userId: number;
  studentName?: string | null;
  studentNumber?: string | null;
  aiStatus?: SubmissionAiStatus | null;
  aiSuggestedTotal?: number | null;
  teacherTotal?: number | null;
  teacherSummary?: string;
  aiError?: string | null;
  finalizedAt?: string | null;
  grades?: QuestionGrade[];
}

export interface GradingStudent {
  userId: number;
  studentName?: string | null;
  studentNumber?: string | null;
  submissionId?: string | null;
  submissionStatus: SubmissionStatus | "not_submitted";
  aiStatus: SubmissionAiStatus;
  submittedAt?: string | null;
  aiSuggestedTotal?: number | null;
  teacherTotal?: number | null;
  updatedAt?: string | null;
}

export interface GradingOverview {
  assignmentId: string;
  gradesPublishedAt?: string | null;
  canPublish: boolean;
  pendingUserIds: number[];
  students: GradingStudent[];
}

export interface StudentPathSummary {
  userId: number;
  studentName?: string | null;
  studentNumber?: string | null;
  profileComplete?: boolean;
  masteredCount: number;
  needsReviewCount: number;
  completionRate: number;
  lastActivityAt?: string | null;
  diagnosticSummary?: string | null;
}

export type LearningEvidenceStatus = "open" | "confirmed" | "resolved" | "retracted";

export interface LearningEvidenceItem {
  id: string;
  kind: string;
  claim: string;
  status: LearningEvidenceStatus;
  confidence: number;
  severity: "low" | "medium" | "high";
  sourceType?: string;
  excerpt?: string;
  relationRole?: "direct" | "prerequisite_risk" | "successor_risk" | "related";
  relationWeight?: number;
  relationPath?: { fromNodeId?: number; toNodeId?: number; edgeLabel?: string; edgeDescription?: string };
  updatedAt?: string;
}

export function learningEvidenceKindLabel(kind: string): string {
  const labels: Record<string, string> = {
    goal: "学习目标",
    understanding: "已掌握",
    misconception: "理解偏差",
    gap: "知识缺口",
    used_node: "使用的知识",
    hint: "学习提示",
    unresolved_question: "待解决问题",
    strategy: "证明思路",
  };
  return labels[kind] || "学习记录";
}

export interface StudentContextPreview {
  contextVersion: number;
  currentNode?: number | null;
  goal?: LearningEvidenceItem | null;
  understood: LearningEvidenceItem[];
  openGaps: LearningEvidenceItem[];
  usedNodes: LearningEvidenceItem[];
  relatedContext: LearningEvidenceItem[];
  relatedRisks: LearningEvidenceItem[];
  resolvedItems: LearningEvidenceItem[];
  nextStep?: LearningEvidenceItem | null;
  masteryState: "unknown" | "learning" | "mastered" | "needs_review";
  updatedAt?: string | null;
}

export function learningMasteryStateLabel(state: StudentContextPreview["masteryState"]): string {
  const labels: Record<StudentContextPreview["masteryState"], string> = {
    unknown: "尚无记录",
    learning: "学习中",
    mastered: "已掌握",
    needs_review: "待复习",
  };
  return labels[state];
}

export interface StudentNodeContextState {
  nodeId: number;
  title: string;
  masteryState: StudentContextPreview["masteryState"];
  directSummary: Record<string, unknown>;
  riskSummary: { items?: LearningEvidenceItem[] };
  openEvidenceCount: number;
  version: number;
  updatedAt?: string | null;
}

export interface StudentContextOverview {
  contextVersion: number;
  nodeStates: StudentNodeContextState[];
}

export interface TeacherStudentContextSummary {
  student: { userId: number; studentName?: string | null; studentNumber?: string | null };
  summary: StudentContextOverview & {
    courseSummary: Record<string, unknown>;
    courseSummaryUpdatedAt?: string | null;
    evidence: LearningEvidenceItem[];
  };
}

export interface DiagnosticResult {
  result: "mastered" | "needs_review";
  summary: string;
  nextStep?: string;
}

interface ImportedGraphResponse {
  job_id: string;
  courseGraphKey: string;
  filename: string;
  result: GraphResult;
  warnings?: string[];
}

export type PathOrderWarning = { from: number; to: number; message: string };
type ClassManagementDialog = "rename" | "members" | "dissolve" | null;
type AssignmentCreationSource = "snapshot" | null;

async function educationRequest<T>(token: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      Authorization: `Bearer ${token}`,
      ...init?.headers,
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.error || `请求失败（${response.status}）`) as Error & {
      code?: string;
      status?: number;
      classId?: string;
      nodeIds?: number[];
    };
    error.code = body.code;
    error.status = response.status;
    error.classId = body.classId;
    error.nodeIds = Array.isArray(body.nodeIds) ? body.nodeIds.filter((nodeId: unknown): nodeId is number => typeof nodeId === "number") : undefined;
    throw error;
  }
  return body as T;
}

export function groupCourseGraphs(snapshots: EducationSnapshotSummary[]): CourseGraphSummary[] {
  const groups = new Map<string, CourseGraphSummary>();
  snapshots.forEach(snapshot => {
    const sourceGraphId = snapshot.sourceGraphId?.trim();
    const key = sourceGraphId ? `source:${sourceGraphId}` : `snapshot:${snapshot.id}`;
    const current = groups.get(key);
    if (!current) {
      groups.set(key, { ...snapshot, snapshotIds: [snapshot.id] });
      return;
    }
    const snapshotIds = current.snapshotIds.includes(snapshot.id)
      ? current.snapshotIds
      : [...current.snapshotIds, snapshot.id];
    const snapshotTime = Date.parse(snapshot.createdAt);
    const currentTime = Date.parse(current.createdAt);
    const snapshotIsEarlier = Number.isNaN(currentTime)
      || (!Number.isNaN(snapshotTime) && snapshotTime < currentTime)
      || (snapshot.createdAt === current.createdAt && snapshot.id < current.id);
    const boundAssignmentCount = (current.boundAssignmentCount ?? 0) + (snapshot.boundAssignmentCount ?? 0);
    groups.set(key, snapshotIsEarlier
      ? { ...snapshot, boundAssignmentCount, snapshotIds }
      : { ...current, boundAssignmentCount, snapshotIds });
  });
  return [...groups.values()];
}

function GraphImportFileField({
  label, hint, file, accept, optional = false, disabled = false, onChange,
}: {
  label: string;
  hint: string;
  file: File | null;
  accept: string;
  optional?: boolean;
  disabled?: boolean;
  onChange: (file: File | null) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <div className="edu-import-file-field">
      <div className="edu-import-file-copy">
        {accept.includes("json") ? <FileJson size={18} /> : <FileText size={18} />}
        <div><strong>{label}{optional && <span>可选</span>}</strong><small>{hint}</small></div>
      </div>
      <button type="button" className="edu-button ghost" disabled={disabled} onClick={() => inputRef.current?.click()}>{file ? "替换文件" : "选择文件"}</button>
      {file && <div className="edu-import-file-selected"><span title={file.name}>{file.name}</span><button type="button" disabled={disabled} aria-label={`移除 ${label}`} onClick={() => onChange(null)}><X size={13} /></button></div>}
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        disabled={disabled}
        onClick={event => { event.currentTarget.value = ""; }}
        onChange={event => onChange(event.target.files?.[0] ?? null)}
      />
    </div>
  );
}

async function waitForImportedSourcePdf(token: string, jobId: string, initial: GraphResult["source_pdf"]) {
  let sourcePdf = initial;
  if (sourcePdf?.status !== "compiling") return { sourcePdf, timedOut: false };
  for (let attempt = 0; attempt < 60; attempt += 1) {
    await new Promise(resolve => window.setTimeout(resolve, 500));
    const response = await fetch(apiUrl(`/api/v2/jobs/${encodeURIComponent(jobId)}/status`), {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) throw new Error("无法获取 TeX 原文编译状态");
    const body = await response.json() as { source_pdf?: GraphResult["source_pdf"] };
    sourcePdf = body.source_pdf ?? sourcePdf;
    if (sourcePdf?.status !== "compiling") return { sourcePdf, timedOut: false };
  }
  return { sourcePdf, timedOut: true };
}

export function educationErrorMessage(cause: unknown) {
  const error = cause as { code?: string; message?: string };
  if (error.message === "title is required") return "请输入任务名称。";
  if (error.message === "title must be at most 160 characters") return "任务名称不能超过 160 个字符。";
  if (error.message === "dueAt must be an ISO date string or null") return "截止时间格式无效。";
  switch (error.code) {
    case "invalid_invite_code": return "邀请码无效或班级已失效，请检查后重试。";
    case "class_role_conflict": return "该账号已经是此班级的教师，不能重复加入。";
    case "class_membership_removed": return "你已被移出该班级，请联系教师恢复加入。";
    case "student_name_required": return "请输入姓名。";
    case "student_name_invalid": return "姓名不能超过 50 个字符。";
    case "student_number_required": return "请输入学号。";
    case "student_number_invalid": return "学号只能包含字母、数字、下划线或连字符，长度不超过 32 位。";
    case "student_number_conflict": return "该学号已被本班其他学生使用，请检查后重试。";
    case "student_profile_required": return "请先完善当前班级的姓名和学号。";
    case "class_title_required": return "请输入班级名称。";
    case "student_not_found": return "找不到这名学生，可能已不在当前班级。";
    case "education_role_forbidden": return "当前教育身份没有执行此操作的权限。";
    case "education_role_required": return "请重新登录并选择学生端或教师端。";
    case "teacher_access_revoked": return "教师账号授权已变更，请退出后重新登录。";
    case "assessment_required": return "请先完成该节点的理解考核，再标记为已掌握。";
    case "assessment_unavailable": return "该节点的考察题暂不可用，请联系教师处理。";
    case "assessment_incomplete": return "请回答全部考察题后再提交。";
    case "assessment_review_required": return "仍有考察题生成失败或尚未处理，暂时不能发布。";
    case "assessment_invalid_result": return "考察题结果未通过结构校验，请重新生成或设为免考。";
    case "assessment_regeneration_failed": return "考察题重新生成失败，原题已保留，请稍后重试。";
    case "assessment_draft_changed": return "草稿已在其他窗口更新或发布，本次结果未写入，请刷新后重试。";
    case "assessment_scoring_required": return "请补齐每题参考答案、评分点和分值，并确保作业总分恰好为 100 分。";
    case "assessment_scoring_frozen": return "该评分标准已冻结；已定稿或已发布成绩的作业不能再修改。";
    case "assessment_score_invalid": return "题目分值必须大于 0，且最多保留一位小数。";
    case "assignment_incomplete": return "请先完成所有非免考节点的全部题目，再提交整份作业。";
    case "assignment_closed": return "该作业已关闭提交。";
    case "assignment_already_submitted": return "作业已经提交，答案已冻结，不能继续修改。";
    case "submission_locked": return "作业已经提交，答案已冻结，不能继续修改。";
    case "grading_score_invalid": return "教师分数必须在 0 到本题满分之间。";
    case "grading_incomplete": return "仍有题目尚未完成教师评分，暂时不能定稿或发布。";
    case "grading_finalized": return "该作业已定稿，不能再修改或重新评价。";
    case "grades_released": return "成绩已经发布。";
    case "education_ai_unconfigured": return "课程 AI 尚未配置，请联系教师或管理员。";
    case "education_ai_limit_reached": return "今天的课程 AI 使用次数已达上限。";
    case "interaction_incomplete": return "上一次请求仍在保存，请稍后重试。";
    default: return error.message || "操作失败，请稍后重试。";
  }
}

export function assessmentGenerationErrorMessage(assessment?: Pick<NodeAssessment, "generationError" | "generationErrorCode">) {
  if (!assessment) return "题目生成失败，请重新生成或设为免考。";
  const legacyCode = assessment.generationError === "education AI is not configured"
    ? "education_ai_unconfigured"
    : assessment.generationError === "education AI daily limit reached"
      ? "education_ai_limit_reached"
      : undefined;
  const code = assessment.generationErrorCode || legacyCode;
  if (code) return educationErrorMessage({ code });
  return assessment.generationError || "题目生成失败，请重新生成或设为免考。";
}

export async function loadEducationAssignment(token: string, assignmentId: string) {
  const body = await educationRequest<{ assignment: EducationAssignment }>(
    token,
    `/api/v2/edu/assignments/${encodeURIComponent(assignmentId)}`,
  );
  return body.assignment;
}

export async function loadEducationSnapshot(token: string, snapshotId: string) {
  const body = await educationRequest<{ snapshot: EducationSnapshot }>(
    token,
    `/api/v2/edu/snapshots/${encodeURIComponent(snapshotId)}`,
  );
  return body.snapshot;
}

export async function saveEducationAssignment(token: string, assignment: EducationAssignment) {
  return educationRequest<{ assignment: EducationAssignment; warnings?: PathOrderWarning[] }>(
    token,
    `/api/v2/edu/assignments/${encodeURIComponent(assignment.id)}`,
    {
      method: "PUT",
      body: JSON.stringify({
        title: assignment.title,
        dueAt: assignment.dueAt,
        summary: assignment.path.summary,
        steps: assignment.path.steps,
      }),
    },
  );
}

export async function updatePublishedEducationAssignment(
  token: string,
  assignmentId: string,
  input: { title: string; dueAt: string | null },
) {
  const body = await educationRequest<{ assignment: EducationAssignment }>(
    token,
    `/api/v2/edu/assignments/${encodeURIComponent(assignmentId)}`,
    { method: "PATCH", body: JSON.stringify(input) },
  );
  return body.assignment;
}

export async function archiveEducationAssignment(token: string, assignmentId: string) {
  await educationRequest<{ ok: true }>(
    token,
    `/api/v2/edu/assignments/${encodeURIComponent(assignmentId)}`,
    { method: "DELETE" },
  );
}

export async function publishEducationAssignment(token: string, assignmentId: string) {
  const body = await educationRequest<{ assignment: EducationAssignment }>(
    token,
    `/api/v2/edu/assignments/${encodeURIComponent(assignmentId)}/publish`,
    { method: "POST" },
  );
  return body.assignment;
}

export function replaceNodeAssessment(assignment: EducationAssignment, assessment: NodeAssessment) {
  const exists = assignment.assessments.some(item => item.nodeId === assessment.nodeId);
  return {
    ...assignment,
    assessments: exists
      ? assignment.assessments.map(item => item.nodeId === assessment.nodeId ? assessment : item)
      : [...assignment.assessments, assessment],
  };
}

export function assessmentAnswersComplete(attempt: AssessmentAttempt) {
  return attempt.questions.length > 0
    && attempt.questions.every(question => Boolean(attempt.answers[question.id]?.trim()));
}

export async function regenerateEducationAssessment(token: string, assignmentId: string, nodeId: number) {
  const body = await educationRequest<{ assessment: NodeAssessment }>(
    token,
    `/api/v2/edu/assignments/${encodeURIComponent(assignmentId)}/assessments/${nodeId}/regenerate`,
    { method: "POST" },
  );
  return body.assessment;
}

export async function regenerateUnresolvedEducationAssessments(token: string, assignmentId: string) {
  return educationRequest<{
    assessments: NodeAssessment[];
    retriedNodeIds: number[];
    readyNodeIds: number[];
    failedNodeIds: number[];
  }>(
    token,
    `/api/v2/edu/assignments/${encodeURIComponent(assignmentId)}/assessments/regenerate-unresolved`,
    { method: "POST" },
  );
}

export async function regenerateEducationAssessmentQuestion(
  token: string,
  assignmentId: string,
  nodeId: number,
  questionId: string,
) {
  const body = await educationRequest<{ assessment: NodeAssessment }>(
    token,
    `/api/v2/edu/assignments/${encodeURIComponent(assignmentId)}/assessments/${nodeId}/questions/${encodeURIComponent(questionId)}/regenerate`,
    { method: "POST" },
  );
  return body.assessment;
}

export async function deleteEducationAssessmentQuestion(
  token: string,
  assignmentId: string,
  nodeId: number,
  questionId: string,
) {
  const body = await educationRequest<{ assessment: NodeAssessment }>(
    token,
    `/api/v2/edu/assignments/${encodeURIComponent(assignmentId)}/assessments/${nodeId}/questions/${encodeURIComponent(questionId)}`,
    { method: "DELETE" },
  );
  return body.assessment;
}

export async function exemptEducationAssessment(token: string, assignmentId: string, nodeId: number) {
  const body = await educationRequest<{ assessment: NodeAssessment }>(
    token,
    `/api/v2/edu/assignments/${encodeURIComponent(assignmentId)}/assessments/${nodeId}`,
    { method: "DELETE" },
  );
  return body.assessment;
}

export async function startEducationAssessmentAttempt(token: string, assignmentId: string, nodeId: number) {
  const body = await educationRequest<{ attempt: AssessmentAttempt }>(
    token,
    `/api/v2/edu/assignments/${encodeURIComponent(assignmentId)}/assessments/${nodeId}/attempts`,
    { method: "POST" },
  );
  return body.attempt;
}

export async function saveEducationAssessmentAttempt(
  token: string,
  attemptId: string,
  answers: Record<string, string>,
) {
  const body = await educationRequest<{ attempt: AssessmentAttempt }>(
    token,
    `/api/v2/edu/assessment-attempts/${encodeURIComponent(attemptId)}`,
    { method: "PATCH", body: JSON.stringify({ answers }) },
  );
  return body.attempt;
}

export async function completeEducationAssessmentAttempt(
  token: string,
  attemptId: string,
  answers: Record<string, string>,
) {
  return educationRequest<{ attempt: AssessmentAttempt; path: LearningPath }>(
    token,
    `/api/v2/edu/assessment-attempts/${encodeURIComponent(attemptId)}/complete`,
    { method: "POST", body: JSON.stringify({ answers }) },
  );
}

export async function updateEducationAssessmentQuestion(
  token: string,
  assignmentId: string,
  nodeId: number,
  questionId: string,
  input: { referenceAnswer: string; expectedPoints: string[]; maxScore: number },
) {
  const body = await educationRequest<{ assessment: NodeAssessment }>(
    token,
    `/api/v2/edu/assignments/${encodeURIComponent(assignmentId)}/assessments/${nodeId}/questions/${encodeURIComponent(questionId)}`,
    { method: "PATCH", body: JSON.stringify(input) },
  );
  return body.assessment;
}

export async function submitEducationAssignment(token: string, assignmentId: string) {
  const body = await educationRequest<{ submission: EducationSubmissionSummary }>(
    token,
    `/api/v2/edu/assignments/${encodeURIComponent(assignmentId)}/submissions`,
    { method: "POST" },
  );
  return body.submission;
}

export async function loadEducationGradingOverview(token: string, assignmentId: string) {
  return educationRequest<GradingOverview>(token, `/api/v2/edu/assignments/${encodeURIComponent(assignmentId)}/grading-overview`);
}

export async function loadEducationSubmission(token: string, submissionId: string) {
  const body = await educationRequest<{ submission: EducationSubmission }>(token, `/api/v2/edu/submissions/${encodeURIComponent(submissionId)}`);
  return body.submission;
}

export async function evaluateEducationSubmission(token: string, submissionId: string) {
  const body = await educationRequest<{ submission: EducationSubmission }>(
    token,
    `/api/v2/edu/submissions/${encodeURIComponent(submissionId)}/evaluate`,
    { method: "POST" },
  );
  return body.submission;
}

export async function saveEducationSubmissionGrade(
  token: string,
  submissionId: string,
  input: { grades: Array<{ questionId: string; teacherScore: number | null; teacherFeedback: string }>; teacherSummary: string },
) {
  const body = await educationRequest<{ submission: EducationSubmission }>(
    token,
    `/api/v2/edu/submissions/${encodeURIComponent(submissionId)}/grade`,
    { method: "PATCH", body: JSON.stringify(input) },
  );
  return body.submission;
}

export async function finalizeEducationSubmission(token: string, submissionId: string) {
  const body = await educationRequest<{ submission: EducationSubmission }>(
    token,
    `/api/v2/edu/submissions/${encodeURIComponent(submissionId)}/finalize`,
    { method: "POST" },
  );
  return body.submission;
}

export async function publishEducationGrades(token: string, assignmentId: string) {
  return educationRequest<{ assignmentId: string; gradesPublishedAt: string; releasedCount: number }>(
    token,
    `/api/v2/edu/assignments/${encodeURIComponent(assignmentId)}/grades/publish`,
    { method: "POST" },
  );
}

export async function updateEducationProgress(
  token: string,
  assignmentId: string,
  nodeId: number,
  state: LearningStepState,
) {
  return educationRequest<{ path: LearningPath }>(
    token,
    `/api/v2/edu/assignments/${encodeURIComponent(assignmentId)}/progress/${nodeId}`,
    { method: "PUT", body: JSON.stringify({ state }) },
  );
}

export async function loadStudentContext(
  token: string,
  assignmentId: string,
  nodeId?: number,
) {
  const query = nodeId === undefined ? "" : `?nodeId=${encodeURIComponent(nodeId)}`;
  return educationRequest<StudentContextOverview & { contextPreview?: StudentContextPreview; tokenEstimate?: number }>(
    token,
    `/api/v2/edu/assignments/${encodeURIComponent(assignmentId)}/student-context${query}`,
  );
}

export async function updateStudentContextEvidence(
  token: string,
  evidenceId: string,
  status: "open" | "resolved" | "retracted",
  note = "",
) {
  return educationRequest<{ evidence: { id: string; status: LearningEvidenceStatus; updatedAt: string } }>(
    token,
    `/api/v2/edu/context/evidence/${encodeURIComponent(evidenceId)}`,
    { method: "PATCH", body: JSON.stringify({ status, note }) },
  );
}

export async function loadStudentContextExport(token: string, classId: string) {
  return educationRequest<Record<string, unknown>>(
    token,
    `/api/v2/edu/classes/${encodeURIComponent(classId)}/student-context/export`,
  );
}

export async function deleteStudentContext(token: string, classId: string) {
  return educationRequest<{ ok: boolean; deletedInteractions: number; deletedEvidence: number; deletedNodeModels: number }>(
    token,
    `/api/v2/edu/classes/${encodeURIComponent(classId)}/student-context`,
    { method: "DELETE", body: JSON.stringify({ confirmClassId: classId }) },
  );
}

export async function loadTeacherStudentContextSummary(
  token: string,
  assignmentId: string,
  studentUserId: number,
) {
  return educationRequest<TeacherStudentContextSummary>(
    token,
    `/api/v2/edu/assignments/${encodeURIComponent(assignmentId)}/students/${studentUserId}/context-summary`,
  );
}

export function assignmentGraphResult(assignment: EducationAssignment): GraphResult | null {
  const snapshot = assignment.snapshot;
  if (!snapshot) return null;
  return snapshotGraphResult(snapshot);
}

export function snapshotGraphResult(snapshot: EducationSnapshot): GraphResult {
  return {
    nodes: snapshot.nodes,
    edges: snapshot.edges,
    latex_macros: snapshot.latexMacros,
    source_pdf: snapshot.sourcePdf,
    source_mode: "import",
  };
}

export function learningCanvasEdges(path: LearningPath, storedEdges: GraphResult["edges"]) {
  const stepIds = new Set(path.steps.map(step => step.nodeId));
  const pathEdges: GraphResult["edges"] = path.edges.filter(edge => stepIds.has(edge.from) && stepIds.has(edge.to)).map(edge => ({
    from: edge.from,
    to: edge.to,
    label: edge.label || "前置",
    description: edge.description || "",
    strength: "",
  }));
  const storedPathEdges = new Set(pathEdges.map(edge => `${edge.to}:${edge.from}`));
  return {
    pathEdgeCount: pathEdges.length,
    edges: [...pathEdges, ...storedEdges.filter(edge => !storedPathEdges.has(`${edge.from}:${edge.to}`))],
  };
}

function nodeTitle(node: GraphNode | undefined, nodeId: number) {
  return node?.title_zh || node?.title_en || node?.label || `节点 ${nodeId}`;
}

function formatDate(value?: string | null) {
  if (!value) return "未设置";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { dateStyle: "medium", timeStyle: "short" });
}

function toDatetimeLocal(value?: string | null) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function submissionStatusLabel(status: GradingStudent["submissionStatus"] | SubmissionStatus) {
  return {
    not_submitted: "未提交", submitted: "待评价", review_draft: "批改中", finalized: "已定稿", released: "已发布",
  }[status] || status;
}

function matrixStatusLabel(status?: MatrixCheckReport["status"]) {
  return {
    not_applicable: "无矩阵检查", verified: "数值检查通过", contradicted: "发现计算错误", indeterminate: "需要人工判断", structural_invalid: "结构需要复核",
  }[status || "not_applicable"];
}

export type GradingLaunchPhase = "loading" | "evaluating";

export function gradingStandardIncomplete(grade: Pick<QuestionGrade, "referenceAnswer" | "expectedPoints" | "maxScore">) {
  return !grade.referenceAnswer?.trim() || !grade.expectedPoints?.some(point => point.trim()) || grade.maxScore <= 0;
}

export function gradingAiSuggestionPatch(grade: Pick<QuestionGrade, "aiSuggestedScore" | "aiResult" | "maxScore">) {
  const rawScore = grade.aiSuggestedScore ?? grade.aiResult?.suggestedScore;
  if (rawScore == null || !Number.isFinite(Number(rawScore))) return null;
  const teacherScore = Math.round(Math.min(grade.maxScore, Math.max(0, Number(rawScore))) * 10) / 10;
  const teacherFeedback = grade.aiResult?.studentFeedback?.trim() || grade.aiResult?.rationale?.trim() || "";
  return { teacherScore, teacherFeedback };
}

export function gradingOverviewActionLabel(phase: GradingLaunchPhase | null, status: GradingStudent["submissionStatus"]) {
  if (phase === "loading") return "正在打开…";
  if (phase === "evaluating") return "AI 评价中…";
  if (status === "submitted") return "评价作业";
  if (status === "released") return "查看评分";
  return "继续批改";
}

function GradingDialog({ token, submission, theme, evaluating = false, onUpdated, onClose }: {
  token: string;
  theme: "light" | "dark";
  submission: EducationSubmission;
  evaluating?: boolean;
  onUpdated: (submission: EducationSubmission) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(submission);
  const [activeIndex, setActiveIndex] = useState(0);
  const [busy, setBusy] = useState<"evaluate" | "save" | "finalize" | "standard" | null>(null);
  const [standardReference, setStandardReference] = useState("");
  const [standardPoints, setStandardPoints] = useState("");
  const [standardMaxScore, setStandardMaxScore] = useState("");
  const [error, setError] = useState("");
  const evaluationLockRef = useRef(false);
  const saveLockRef = useRef(false);
  useEffect(() => { setDraft(submission); }, [submission]);
  useEffect(() => { setActiveIndex(0); }, [submission.id]);
  const grades = draft.grades || [];
  const active = grades[activeIndex];
  const incompleteStandardCount = grades.filter(gradingStandardIncomplete).length;
  const scoringIncomplete = incompleteStandardCount > 0;
  useEffect(() => {
    setStandardReference(active?.referenceAnswer || "");
    setStandardPoints((active?.expectedPoints || []).join("\n"));
    setStandardMaxScore(active ? String(active.maxScore || "") : "");
  }, [active?.questionId, active?.referenceAnswer, active?.expectedPoints, active?.maxScore]);
  const teacherTotal = grades.reduce((total, grade) => total + (grade.teacherScore ?? 0), 0);
  const complete = grades.every(grade => grade.teacherScore !== null && grade.teacherScore !== undefined && grade.teacherScore >= 0 && grade.teacherScore <= grade.maxScore);
  const readOnly = draft.status === "finalized" || draft.status === "released";
  const isEvaluating = evaluating || busy === "evaluate";
  const aiSuggestion = active ? gradingAiSuggestionPatch(active) : null;
  const gradeGroups = [...grades.reduce((groups, grade, index) => {
    const current = groups.get(grade.nodeId) || [];
    current.push({ grade, index });
    groups.set(grade.nodeId, current);
    return groups;
  }, new Map<number, Array<{ grade: QuestionGrade; index: number }>>()).entries()];
  const updateGrade = (questionId: string, patch: Partial<QuestionGrade>) => {
    setDraft(current => ({ ...current, grades: (current.grades || []).map(grade => grade.questionId === questionId ? { ...grade, ...patch } : grade) }));
  };
  const adoptAiSuggestion = () => {
    if (!active || !aiSuggestion || readOnly || isEvaluating || busy !== null) return;
    updateGrade(active.questionId, aiSuggestion);
  };
  const payload = () => ({
    grades: grades.map(grade => ({ questionId: grade.questionId, teacherScore: grade.teacherScore ?? null, teacherFeedback: grade.teacherFeedback || "" })),
    teacherSummary: draft.teacherSummary || "",
  });
  const saveScoringStandard = async () => {
    if (!active || readOnly) return;
    const points = standardPoints.split(/\r?\n/).map(point => point.trim()).filter(Boolean);
    const maxScore = Math.round(Number(standardMaxScore) * 10) / 10;
    if (!standardReference.trim() || !points.length || !Number.isFinite(maxScore) || maxScore <= 0) { setError("请补齐参考答案、评分点和有效分值。"); return; }
    setBusy("standard"); setError("");
    try {
      await updateEducationAssessmentQuestion(token, draft.assignmentId, active.nodeId, active.questionId, { referenceAnswer: standardReference.trim(), expectedPoints: points, maxScore });
      const next = await loadEducationSubmission(token, draft.id);
      setDraft(next); onUpdated(next);
    } catch (cause) { setError(educationErrorMessage(cause)); }
    finally { setBusy(null); }
  };
  const evaluate = async () => {
    if (evaluationLockRef.current || evaluating) return;
    evaluationLockRef.current = true;
    setBusy("evaluate"); setError("");
    try {
      const saved = await saveEducationSubmissionGrade(token, draft.id, payload());
      const next = await evaluateEducationSubmission(token, saved.id);
      setDraft(next); onUpdated(next);
    }
    catch (cause) { setError(educationErrorMessage(cause)); }
    finally { evaluationLockRef.current = false; setBusy(null); }
  };
  const save = async () => {
    if (saveLockRef.current || isEvaluating || busy !== null || readOnly) return false;
    saveLockRef.current = true;
    setBusy("save"); setError("");
    try {
      const next = await saveEducationSubmissionGrade(token, draft.id, payload());
      setDraft(next); onUpdated(next);
      return true;
    }
    catch (cause) { setError(educationErrorMessage(cause)); return false; }
    finally { saveLockRef.current = false; setBusy(null); }
  };
  const moveToQuestion = async (index: number) => {
    if (index < 0 || index >= grades.length || index === activeIndex || isEvaluating || busy !== null) return;
    if (readOnly) { setActiveIndex(index); return; }
    if (await save()) setActiveIndex(index);
  };
  const finalize = async () => {
    if (!complete) return;
    setBusy("finalize"); setError("");
    try {
      const saved = await saveEducationSubmissionGrade(token, draft.id, payload());
      const next = await finalizeEducationSubmission(token, saved.id);
      setDraft(next); onUpdated(next);
    } catch (cause) { setError(educationErrorMessage(cause)); }
    finally { setBusy(null); }
  };
  return createPortal(<div className="edu-root edu-modal-backdrop edu-grading-backdrop" data-theme={theme} onClick={isEvaluating ? undefined : onClose}>
    <section className="edu-grading-dialog" role="dialog" aria-modal="true" aria-label="作业评分" aria-busy={isEvaluating} onClick={event => event.stopPropagation()}>
      <header className="edu-grading-head">
        <div><span className="edu-kicker">教师批改</span><h2>{draft.studentName || "学生作业"}</h2><small>学号：{draft.studentNumber || "待补全"} · {submissionStatusLabel(draft.status)}</small></div>
        <div className="edu-grading-head-score"><span>教师总分</span><strong>{teacherTotal.toFixed(1)}</strong><small>/ 100</small></div>
        <button className="edu-icon-button" disabled={isEvaluating} onClick={onClose} aria-label="关闭评分"><X size={18} /></button>
      </header>
      <div className="edu-grading-body">
      {isEvaluating ? <div className="edu-grading-running" role="status" aria-live="polite"><Loader2 className="edu-spin" size={18} /><div><strong>AI 评价进行中</strong><small>正在检查矩阵计算过程并生成逐题评分建议，完成后将自动回填结果，请勿重复操作。</small></div></div> : scoringIncomplete ? <div className="edu-grading-blocked" role="alert"><AlertTriangle size={18} /><div><strong>AI 评价尚未开始</strong><small>该历史作业有 {incompleteStandardCount} 道题缺少参考答案、评分点或有效分值。请按左侧题目逐题补齐并保存，再点击 AI 评价。</small></div></div> : null}
      <div className="edu-grading-layout">
        <nav className="edu-grading-nav">
          <strong>按知识节点分组</strong>
          {gradeGroups.map(([nodeId, items]) => <section className="edu-grading-nav-group" key={nodeId}><h3>节点 {nodeId}</h3>{items.map(({ grade, index }) => {
            const standardIncomplete = gradingStandardIncomplete(grade);
            return <button key={grade.questionId} className={index === activeIndex ? "active" : ""} disabled={isEvaluating || busy !== null} onClick={() => void moveToQuestion(index)}>
            <span>{grade.order || index + 1}</span><div><b>第 {grade.order || index + 1} 题</b><small>{standardIncomplete ? "评分标准待补充" : grade.teacherScore == null ? "待评分" : `${grade.teacherScore}/${grade.maxScore}`}</small></div>
          </button>})}</section>)}
        </nav>
        <main className="edu-grading-content">
          {active ? <>
            <section className="edu-grading-question"><span>题目 · 满分 {active.maxScore}</span><h3><MathText text={active.question} /></h3>{active.focus && <small>检查重点：{active.focus}</small>}</section>
            <section className="edu-grading-block"><h4>学生答案</h4><div className="edu-grading-math"><MathText text={active.studentAnswer} /></div></section>
            <section className="edu-grading-block reference"><h4>参考答案与评分点</h4>{!active.referenceAnswer?.trim() || !active.expectedPoints?.some(point => point.trim()) || active.maxScore <= 0 ? <div className="edu-grading-standard-editor"><div className="edu-modal-error"><AlertTriangle size={14} />历史作业缺少完整评分标准，请补齐后再发起 AI 评价。</div><label><span>参考答案</span><textarea value={standardReference} onChange={event => setStandardReference(event.target.value)} placeholder="补充教师审核后的参考答案" /></label><label><span>评分点（每行一项）</span><textarea value={standardPoints} onChange={event => setStandardPoints(event.target.value)} placeholder="关键结论&#10;关键步骤" /></label><label><span>本题满分</span><input type="number" min={0.1} max={100} step={0.1} value={standardMaxScore} onChange={event => setStandardMaxScore(event.target.value)} /></label><button className="edu-button secondary" disabled={busy !== null} onClick={() => void saveScoringStandard()}>{busy === "standard" ? <Loader2 className="edu-spin" size={14} /> : <Save size={14} />}保存并更新待批改快照</button></div> : <><div className="edu-grading-math"><MathText text={active.referenceAnswer} /></div>{active.expectedPoints.length > 0 && <ul>{active.expectedPoints.map((point, index) => <li key={index}><MathText text={point} /></li>)}</ul>}</>}</section>
            <section className={`edu-grading-block matrix ${(active.matrixReport as MatrixCheckReport).status || "not_applicable"}`}><h4><span>{matrixStatusLabel((active.matrixReport as MatrixCheckReport).status)}</span></h4><p>{(active.matrixReport as MatrixCheckReport).summary || "尚未运行数值检查。"}</p>{((active.matrixReport as MatrixCheckReport).issues || []).map((issue, index) => <div className="edu-matrix-issue" key={index}><AlertTriangle size={14} /><span>{issue.message}{issue.sourceExcerpt ? <small>原文：{issue.sourceExcerpt}</small> : null}{issue.expected != null || issue.actual != null ? <small>期望：{issue.expected ?? "—"}；实际：{issue.actual ?? "—"}</small> : null}{issue.mismatchedCells?.length ? <small>{issue.mismatchedCells.map(cell => `第${cell.row}行第${cell.column}列：应为 ${cell.expected}，实际为 ${cell.actual}`).join("；")}</small> : null}</span></div>)}</section>
            <section className="edu-grading-block ai"><div className="edu-ai-head"><h4><Brain size={15} />AI 评分建议</h4>{aiSuggestion && <button className="edu-button secondary" disabled={readOnly || isEvaluating || busy !== null} onClick={adoptAiSuggestion}><Check size={13} />采纳建议</button>}</div>{active.aiResult?.rationale ? <><div className="edu-ai-score">建议 {active.aiSuggestedScore ?? active.aiResult.suggestedScore} / {active.maxScore}</div><p>{active.aiResult.rationale}</p>{active.aiResult.correctPoints?.length ? <ul>{active.aiResult.correctPoints.map((item, index) => <li key={index}>{item}</li>)}</ul> : null}{active.aiResult.issues?.length ? <ul className="issues">{active.aiResult.issues.map((item, index) => <li key={index}>{item}</li>)}</ul> : null}{active.aiResult.studentFeedback && <p><b>给学生的反馈：</b>{active.aiResult.studentFeedback}</p>}<small>置信度：{active.aiResult.confidence == null ? "未提供" : `${Math.round(active.aiResult.confidence * 100)}%`}{active.aiResult.needsTeacherReview ? " · 建议重点复核" : ""}</small></> : <p>教师点击“AI 评价”后生成建议；未配置模型时仍可手工评分。</p>}</section>
          </> : <div className="edu-empty compact">该提交没有需要评分的题目。</div>}
        </main>
        <aside className="edu-grading-score-panel">
          {active && <><label><span>本题得分 <b>/ {active.maxScore}</b></span><input type="number" min={0} max={active.maxScore} step="0.1" value={active.teacherScore ?? ""} disabled={readOnly || isEvaluating} onChange={event => updateGrade(active.questionId, { teacherScore: event.target.value === "" ? null : Math.round(Number(event.target.value) * 10) / 10 })} /></label><label><span>教师逐题评语</span><textarea value={active.teacherFeedback || ""} disabled={readOnly || isEvaluating} onChange={event => updateGrade(active.questionId, { teacherFeedback: event.target.value })} placeholder="补充或修正 AI 建议" /></label></>}
          <label className="edu-grading-summary"><span>整体评语</span><textarea value={draft.teacherSummary || ""} disabled={readOnly || isEvaluating} onChange={event => setDraft(current => ({ ...current, teacherSummary: event.target.value }))} placeholder="成绩发布后展示给学生" /></label>
          <div className="edu-grading-total"><span>建议总分 <b>{draft.aiSuggestedTotal ?? "—"}</b></span><span>教师总分 <strong>{teacherTotal.toFixed(1)}</strong></span></div>
        </aside>
      </div>
      {(error || draft.aiError) && <div className="edu-modal-error edu-grading-error">{error || `AI 评价失败：${draft.aiError}。数值检查结果已保留，可继续手工评分。`}</div>}
      </div>
      <footer className="edu-grading-footer"><button className="edu-button ghost" disabled={isEvaluating || busy !== null} onClick={onClose}>返回列表</button><div className="edu-grading-pager"><button className="edu-button ghost" disabled={isEvaluating || busy !== null || activeIndex === 0} onClick={() => void moveToQuestion(activeIndex - 1)}><ChevronLeft size={14} />上一题</button><button className="edu-button ghost" disabled={isEvaluating || busy !== null || activeIndex >= grades.length - 1} onClick={() => void moveToQuestion(activeIndex + 1)}>保存并下一题<ChevronRight size={14} /></button></div><div className="edu-grading-actions"><button className="edu-button secondary" disabled={isEvaluating || busy !== null || scoringIncomplete || draft.status === "finalized" || draft.status === "released"} onClick={() => void evaluate()}>{isEvaluating ? <Loader2 className="edu-spin" size={14} /> : <Brain size={14} />}{isEvaluating ? "AI 评价中…" : scoringIncomplete ? "请先补齐评分标准" : draft.aiStatus === "ready" || draft.aiStatus === "failed" ? "重新 AI 评价" : "AI 评价"}</button><button className="edu-button ghost" disabled={isEvaluating || busy !== null || draft.status === "finalized" || draft.status === "released"} onClick={() => void save()}>{busy === "save" ? <Loader2 className="edu-spin" size={14} /> : <Save size={14} />}保存全部草稿</button><button className="edu-button primary" disabled={isEvaluating || busy !== null || !complete || draft.status === "finalized" || draft.status === "released"} onClick={() => void finalize()}>{busy === "finalize" ? <Loader2 className="edu-spin" size={14} /> : <CheckCircle2 size={14} />}确认评分</button></div></footer>
    </section>
  </div>, document.body);
}

interface EducationHubProps {
  token: string;
  educationRole: EducationRole;
  targetNode?: GraphNode | null;
  initialCourseGraphId?: string | null;
  initialClassId?: string | null;
  resumeTarget?: { kind: "assignment" | "courseGraph"; id: string } | null;
  onOpenAssignment: (assignmentId: string) => void;
  onOpenCourseGraph: (snapshotId: string) => void;
  onOpenCreate: () => void;
  onReauthenticate: () => void;
  initialNotice?: string | null;
  onNoticeConsumed?: () => void;
}

export function EducationHub({
  token, educationRole, targetNode, initialCourseGraphId, initialClassId, resumeTarget,
  onOpenAssignment, onOpenCourseGraph, onOpenCreate, onReauthenticate, initialNotice, onNoticeConsumed,
}: EducationHubProps) {
  const [classes, setClasses] = useState<EducationClass[]>([]);
  const [selectedClassId, setSelectedClassId] = useState<string | null>(null);
  const [assignments, setAssignments] = useState<EducationAssignment[]>([]);
  const [courseGraphs, setCourseGraphs] = useState<CourseGraphSummary[]>([]);
  const [selectedSnapshotId, setSelectedSnapshotId] = useState(initialCourseGraphId || "");
  const [selectedSnapshot, setSelectedSnapshot] = useState<EducationSnapshot | null>(null);
  const [assignmentCreateOpen, setAssignmentCreateOpen] = useState(educationRole === "teacher" && Boolean(targetNode && initialCourseGraphId));
  const [assignmentSource, setAssignmentSource] = useState<AssignmentCreationSource>(
    educationRole === "teacher" && targetNode && initialCourseGraphId ? "snapshot" : null,
  );
  const [assignmentClassId, setAssignmentClassId] = useState(initialClassId || "");
  const [assignmentError, setAssignmentError] = useState("");
  const [overview, setOverview] = useState<StudentPathSummary[] | null>(null);
  const [gradingOverview, setGradingOverview] = useState<GradingOverview | null>(null);
  const [gradingSubmission, setGradingSubmission] = useState<EducationSubmission | null>(null);
  const [gradingError, setGradingError] = useState("");
  const [gradingLaunchPhase, setGradingLaunchPhase] = useState<{ submissionId: string; phase: GradingLaunchPhase } | null>(null);
  const gradingLaunchLockRef = useRef(new Set<string>());
  const [overviewAssignmentId, setOverviewAssignmentId] = useState<string | null>(null);
  const [studentContextSummary, setStudentContextSummary] = useState<TeacherStudentContextSummary | null>(null);
  const [studentContextSummaryLoading, setStudentContextSummaryLoading] = useState(false);
  const [studentContextSummaryError, setStudentContextSummaryError] = useState("");
  const [classTitle, setClassTitle] = useState("");
  const [renameTitle, setRenameTitle] = useState("");
  const [dissolveTitle, setDissolveTitle] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [studentName, setStudentName] = useState("");
  const [studentNumber, setStudentNumber] = useState("");
  const [joinStudentName, setJoinStudentName] = useState("");
  const [joinStudentNumber, setJoinStudentNumber] = useState("");
  const [targetId, setTargetId] = useState<number | null>(targetNode?.id ?? null);
  const [assignmentTitle, setAssignmentTitle] = useState(targetNode ? `学习：${nodeTitle(targetNode, targetNode.id)}` : "");
  const [dueAt, setDueAt] = useState("");
  const [loading, setLoading] = useState(true);
  const [courseGraphsLoading, setCourseGraphsLoading] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [managementDialog, setManagementDialog] = useState<ClassManagementDialog>(null);
  const [settingsError, setSettingsError] = useState("");
  const [members, setMembers] = useState<EducationClassMember[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);
  const [success, setSuccess] = useState("");
  const initialNoticeTimeoutRef = useRef<number | null>(null);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [editingAssignment, setEditingAssignment] = useState<EducationAssignment | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editDueAt, setEditDueAt] = useState("");
  const [editError, setEditError] = useState("");
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importNodesFile, setImportNodesFile] = useState<File | null>(null);
  const [importEdgesFile, setImportEdgesFile] = useState<File | null>(null);
  const [importSourceFile, setImportSourceFile] = useState<File | null>(null);
  const [importError, setImportError] = useState("");
  const [importPhase, setImportPhase] = useState("");
  const [deletingCourseGraph, setDeletingCourseGraph] = useState<CourseGraphSummary | null>(null);
  const [deleteCourseGraphError, setDeleteCourseGraphError] = useState("");

  const selectedClass = classes.find(item => item.id === selectedClassId) ?? null;
  const studentProfileRequired = educationRole === "student" && selectedClass?.profileComplete === false;
  const activeStudentCount = Math.max(0, (selectedClass?.memberCount ?? 1) - 1);
  const courseGraphById = new Map(courseGraphs.flatMap(graph => graph.snapshotIds.map(snapshotId => [snapshotId, graph] as const)));
  const assignmentNodes = selectedSnapshot?.nodes ?? [];
  const educationModalOpen = Boolean(
    assignmentCreateOpen || studentContextSummaryLoading || studentContextSummary || studentContextSummaryError || gradingSubmission
      || createOpen || settingsOpen || managementDialog || editingAssignment || deleteConfirm || importOpen || deletingCourseGraph,
  );
  const selectedAssignmentTarget = assignmentNodes.find(node => node.id === targetId) ?? null;
  const assignmentSourceLabel = selectedSnapshot?.filename || courseGraphById.get(selectedSnapshotId)?.filename || "正在加载课程图谱…";
  const refreshClasses = useCallback(async () => {
    const body = await educationRequest<{ classes: EducationClass[] }>(token, "/api/v2/edu/classes");
    setClasses(body.classes);
    setSelectedClassId(current => current && body.classes.some(item => item.id === current)
      ? current
      : initialClassId && body.classes.some(item => item.id === initialClassId)
        ? initialClassId
        : body.classes[0]?.id ?? null);
  }, [initialClassId, token]);

  useEffect(() => {
    setTheme(resolveTheme(loadStudioSettings().theme));
  }, []);

  useEffect(() => {
    setLoading(true);
    refreshClasses().catch(cause => setError(educationErrorMessage(cause))).finally(() => setLoading(false));
  }, [refreshClasses]);

  useEffect(() => {
    if (!selectedClassId) {
      setAssignments([]);
      setCourseGraphs([]);
      setSelectedSnapshotId("");
      return;
    }
    if (educationRole === "student" && selectedClass?.profileComplete === false) {
      setAssignments([]);
      setCourseGraphs([]);
      return;
    }
    setError("");
    setCourseGraphsLoading(true);
    Promise.all([
      educationRequest<{ assignments: EducationAssignment[] }>(
        token,
        `/api/v2/edu/classes/${encodeURIComponent(selectedClassId)}/assignments`,
      ),
      educationRequest<{ snapshots: EducationSnapshotSummary[] }>(
        token,
        `/api/v2/edu/classes/${encodeURIComponent(selectedClassId)}/snapshots`,
      ),
    ]).then(([assignmentBody, snapshotBody]) => {
      const groupedSnapshots = groupCourseGraphs(snapshotBody.snapshots);
      setAssignments(assignmentBody.assignments);
      setCourseGraphs(groupedSnapshots);
    }).catch(cause => setError(educationErrorMessage(cause))).finally(() => setCourseGraphsLoading(false));
  }, [educationRole, selectedClass?.profileComplete, selectedClassId, token]);

  useEffect(() => {
    if (!assignmentSource || !selectedSnapshotId) {
      setSelectedSnapshot(null);
      setTargetId(null);
      return;
    }
    let cancelled = false;
    setCourseGraphsLoading(true);
    loadEducationSnapshot(token, selectedSnapshotId)
      .then(snapshot => {
        if (cancelled) return;
        setSelectedSnapshot(snapshot);
        setAssignmentClassId(snapshot.classId);
        setTargetId(current => current !== null && snapshot.nodes.some(node => node.id === current) ? current : null);
      })
      .catch(cause => { if (!cancelled) setAssignmentError(educationErrorMessage(cause)); })
      .finally(() => { if (!cancelled) setCourseGraphsLoading(false); });
    return () => { cancelled = true; };
  }, [assignmentSource, selectedSnapshotId, token]);

  useEffect(() => {
    if (educationRole !== "student" || !selectedClassId || selectedClass?.profileComplete !== false) return;
    setStudentName(selectedClass.studentName || "");
    setStudentNumber(selectedClass.studentNumber || "");
    setSettingsError("");
    setSettingsOpen(true);
  }, [educationRole, selectedClass?.profileComplete, selectedClassId]);

  useEffect(() => {
    if (educationRole !== "teacher" || !targetNode) return;
    if (!initialCourseGraphId) return;
    setAssignmentSource("snapshot");
    setAssignmentClassId(initialClassId || "");
    setSelectedSnapshotId(initialCourseGraphId || "");
    setSelectedSnapshot(null);
    setTargetId(targetNode.id);
    setAssignmentTitle(`学习：${nodeTitle(targetNode, targetNode.id)}`);
    setDueAt("");
    setAssignmentError("");
    setAssignmentCreateOpen(true);
  }, [educationRole, initialClassId, initialCourseGraphId, targetNode]);

  useEffect(() => {
    if (targetId === null) return;
    const node = assignmentNodes.find(item => item.id === targetId);
    if (node && !assignmentTitle.trim()) setAssignmentTitle(`学习：${nodeTitle(node, node.id)}`);
  }, [assignmentNodes, assignmentTitle, targetId]);

  useEffect(() => {
    if (!initialNotice) return;
    setSuccess(initialNotice);
    onNoticeConsumed?.();
    if (initialNoticeTimeoutRef.current !== null) window.clearTimeout(initialNoticeTimeoutRef.current);
    initialNoticeTimeoutRef.current = window.setTimeout(() => {
      setSuccess(current => current === initialNotice ? "" : current);
      initialNoticeTimeoutRef.current = null;
    }, 3000);
  }, [initialNotice, onNoticeConsumed]);

  useEffect(() => () => {
    if (initialNoticeTimeoutRef.current !== null) window.clearTimeout(initialNoticeTimeoutRef.current);
  }, []);

  useEffect(() => {
    if (!settingsOpen || managementDialog !== "members" || educationRole !== "teacher" || !selectedClass) return;
    setRenameTitle(selectedClass.title);
    setDissolveTitle("");
    setMembersLoading(true);
    educationRequest<{ members: EducationClassMember[] }>(
      token,
      `/api/v2/edu/classes/${encodeURIComponent(selectedClass.id)}/members`,
    ).then(body => setMembers(body.members))
      .catch(cause => setSettingsError(educationErrorMessage(cause)))
      .finally(() => setMembersLoading(false));
  }, [settingsOpen, managementDialog, educationRole, selectedClass, token]);

  const openCreateClass = () => {
    setClassTitle("");
    setSettingsError("");
    setCreateOpen(true);
  };

  const openClassSettings = () => {
    if (educationRole === "teacher" && !selectedClass) return;
    setSettingsError("");
    setRenameTitle(selectedClass?.title || "");
    setDissolveTitle("");
    setStudentName(selectedClass?.studentName || "");
    setStudentNumber(selectedClass?.studentNumber || "");
    setJoinStudentName(selectedClass?.studentName || "");
    setJoinStudentNumber(selectedClass?.studentNumber || "");
    setManagementDialog(null);
    setSettingsOpen(true);
  };

  const closeManagementDialog = () => {
    setManagementDialog(null);
    setSettingsError("");
  };

  const closeClassSettings = () => {
    setSettingsOpen(false);
    setManagementDialog(null);
    setSettingsError("");
    setRenameTitle("");
    setDissolveTitle("");
    setMembers([]);
  };

  const openAssignmentCreatorFromCourseGraph = (graph: CourseGraphSummary) => {
    if (!selectedClass || selectedClass.role !== "teacher") return;
    setAssignmentSource("snapshot");
    setAssignmentClassId(selectedClass.id);
    setSelectedSnapshotId(graph.id);
    setSelectedSnapshot(null);
    setTargetId(null);
    setAssignmentTitle("");
    setDueAt("");
    setAssignmentError("");
    setAssignmentCreateOpen(true);
  };

  const closeAssignmentCreator = () => {
    if (busy === "assignment") return;
    setAssignmentCreateOpen(false);
    setAssignmentSource(null);
    setAssignmentClassId("");
    setSelectedSnapshotId("");
    setSelectedSnapshot(null);
    setTargetId(null);
    setAssignmentTitle("");
    setDueAt("");
    setAssignmentError("");
  };

  const createClass = async () => {
    if (!classTitle.trim()) return;
    setBusy("class"); setSettingsError(""); setError("");
    try {
      const body = await educationRequest<{ class: EducationClass }>(token, "/api/v2/edu/classes", {
        method: "POST", body: JSON.stringify({ title: classTitle }),
      });
      setClassTitle("");
      await refreshClasses();
      setSelectedClassId(body.class.id);
      setCreateOpen(false);
      setSuccess("班级已创建");
      window.setTimeout(() => setSuccess(""), 2400);
    } catch (cause) { setSettingsError(educationErrorMessage(cause)); }
    finally { setBusy(""); }
  };

  const joinClass = async () => {
    const code = inviteCode.trim().toUpperCase();
    if (!code || !joinStudentName.trim() || !joinStudentNumber.trim()) return;
    setBusy("join"); setSettingsError(""); setError("");
    try {
      const body = await educationRequest<{ class: EducationClass }>(
        token,
        `/api/v2/edu/classes/${encodeURIComponent(code)}/join`,
        {
          method: "POST",
          body: JSON.stringify({
            inviteCode: code,
            studentName: joinStudentName,
            studentNumber: joinStudentNumber,
          }),
        },
      );
      setInviteCode("");
      setJoinStudentName("");
      setJoinStudentNumber("");
      await refreshClasses();
      setSelectedClassId(body.class.id);
      setSettingsOpen(false);
      setSuccess("已加入班级");
      window.setTimeout(() => setSuccess(""), 2400);
    } catch (cause) { setSettingsError(educationErrorMessage(cause)); }
    finally { setBusy(""); }
  };

  const saveStudentProfile = async () => {
    if (!selectedClass || selectedClass.role !== "student") return;
    if (!studentName.trim() || !studentNumber.trim()) return;
    setBusy("profile"); setSettingsError(""); setError("");
    try {
      const body = await educationRequest<{ class: EducationClass }>(
        token,
        `/api/v2/edu/classes/${encodeURIComponent(selectedClass.id)}/membership`,
        {
          method: "PUT",
          body: JSON.stringify({ studentName, studentNumber }),
        },
      );
      setClasses(items => items.map(item => item.id === selectedClass.id
        ? { ...item, ...body.class, profileComplete: true }
        : item));
      await refreshClasses();
      closeClassSettings();
      setSuccess("班级资料已保存");
      window.setTimeout(() => setSuccess(""), 2400);
      if (resumeTarget?.kind === "assignment") onOpenAssignment(resumeTarget.id);
      if (resumeTarget?.kind === "courseGraph") onOpenCourseGraph(resumeTarget.id);
    } catch (cause) { setSettingsError(educationErrorMessage(cause)); }
    finally { setBusy(""); }
  };

  const renameClass = async () => {
    if (!selectedClass || !renameTitle.trim()) return;
    setBusy("rename"); setSettingsError("");
    try {
      const body = await educationRequest<{ class: { id: string; title: string } }>(
        token,
        `/api/v2/edu/classes/${encodeURIComponent(selectedClass.id)}`,
        { method: "PATCH", body: JSON.stringify({ title: renameTitle.trim() }) },
      );
      setClasses(items => items.map(item => item.id === selectedClass.id ? { ...item, title: body.class.title } : item));
      setRenameTitle(body.class.title);
      setManagementDialog(null);
      setSuccess("班级名称已更新");
      window.setTimeout(() => setSuccess(""), 2400);
    } catch (cause) { setSettingsError(educationErrorMessage(cause)); }
    finally { setBusy(""); }
  };

  const removeMember = async (member: EducationClassMember) => {
    if (!selectedClass || member.status === "removed") return;
    const displayName = member.studentName || "资料待补全";
    const displayNumber = member.studentNumber ? `（${member.studentNumber}）` : "";
    if (!window.confirm(`确定将 ${displayName}${displayNumber} 移出“${selectedClass.title}”吗？`)) return;
    setBusy(`remove-${member.userId}`); setSettingsError("");
    try {
      await educationRequest(token, `/api/v2/edu/classes/${encodeURIComponent(selectedClass.id)}/members/${member.userId}`, { method: "DELETE" });
      setMembers(items => items.map(item => item.userId === member.userId ? { ...item, status: "removed", removedAt: new Date().toISOString() } : item));
      await refreshClasses();
      setSuccess("学生已移出班级");
      window.setTimeout(() => setSuccess(""), 2400);
    } catch (cause) { setSettingsError(educationErrorMessage(cause)); }
    finally { setBusy(""); }
  };

  const restoreMember = async (member: EducationClassMember) => {
    if (!selectedClass || member.status !== "removed") return;
    setBusy(`restore-${member.userId}`); setSettingsError("");
    try {
      await educationRequest(token, `/api/v2/edu/classes/${encodeURIComponent(selectedClass.id)}/members/${member.userId}/restore`, { method: "POST" });
      setMembers(items => items.map(item => item.userId === member.userId ? { ...item, status: "active", removedAt: null } : item));
      await refreshClasses();
      setSuccess("学生已恢复加入");
      window.setTimeout(() => setSuccess(""), 2400);
    } catch (cause) { setSettingsError(educationErrorMessage(cause)); }
    finally { setBusy(""); }
  };

  const dissolveClass = async () => {
    if (!selectedClass || dissolveTitle !== selectedClass.title) return;
    if (!window.confirm(`确定解散“${selectedClass.title}”吗？解散后班级将立即停止使用。`)) return;
    setBusy("dissolve"); setSettingsError("");
    try {
      await educationRequest(token, `/api/v2/edu/classes/${encodeURIComponent(selectedClass.id)}`, { method: "DELETE" });
      closeClassSettings();
      setSelectedClassId(null);
      await refreshClasses();
      setSuccess("班级已解散");
      window.setTimeout(() => setSuccess(""), 2400);
    } catch (cause) { setSettingsError(educationErrorMessage(cause)); }
    finally { setBusy(""); }
  };

  const openGraphImport = () => {
    setImportNodesFile(null);
    setImportEdgesFile(null);
    setImportSourceFile(null);
    setImportError("");
    setImportPhase("");
    setImportOpen(true);
  };

  const closeGraphImport = () => {
    if (busy === "course-import") return;
    setImportOpen(false);
    setImportError("");
    setImportPhase("");
  };

  const importCourseGraph = async () => {
    if (!selectedClass || selectedClass.role !== "teacher") return;
    if (!importNodesFile || !importEdgesFile) {
      setImportError("请选择 Node JSON 和 Edge JSON 文件。");
      return;
    }
    if (!importNodesFile.name.toLowerCase().endsWith(".json") || !importEdgesFile.name.toLowerCase().endsWith(".json")) {
      setImportError("节点和关系文件必须是 JSON 文件。");
      return;
    }
    if (importSourceFile && !/\.(md|txt|tex)$/i.test(importSourceFile.name)) {
      setImportError("原文文件必须是 Markdown、TXT 或 TeX 文件。");
      return;
    }

    setBusy("course-import");
    setImportError("");
    setImportPhase("正在校验并导入图谱…");
    try {
      const formData = new FormData();
      formData.append("nodes_file", importNodesFile);
      formData.append("edges_file", importEdgesFile);
      if (importSourceFile) formData.append("markdown_file", importSourceFile);
      const importResponse = await fetch(apiUrl("/api/v2/agent-import"), {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      });
      const imported = await importResponse.json().catch(() => ({})) as Partial<ImportedGraphResponse> & { error?: string };
      if (!importResponse.ok || !imported.result || !imported.job_id || !imported.courseGraphKey) {
        throw new Error(imported.error || "图谱导入失败");
      }

      const warnings = [...(imported.warnings || [])];
      let historySaved = false;
      try {
        await educationRequest(token, "/api/v2/history", {
          method: "POST",
          body: JSON.stringify({ job_id: imported.job_id }),
        });
        historySaved = true;
      } catch {
        warnings.push("导入历史未保存，PDF 原文无法复制到课程图谱。");
      }

      let pdfTimedOut = false;
      if (imported.result.source_pdf?.status === "compiling") {
        setImportPhase("图谱已解析，正在准备 PDF 原文…");
        try {
          const pdfResult = await waitForImportedSourcePdf(token, imported.job_id, imported.result.source_pdf);
          pdfTimedOut = pdfResult.timedOut;
          if (pdfResult.timedOut) warnings.push("PDF 编译仍在进行，本次课程图谱暂不包含 PDF。");
          if (pdfResult.sourcePdf?.status === "failed") warnings.push("TeX 原文 PDF 编译失败，图谱仍已保留。");
        } catch (cause) {
          pdfTimedOut = true;
          warnings.push(cause instanceof Error ? cause.message : "无法获取 PDF 编译状态。");
        }
      }

      setImportPhase("正在加入当前班级…");
      const sourceMarkdown = importSourceFile ? await importSourceFile.text() : "";
      const snapshotBody = await educationRequest<{ snapshot: EducationSnapshot; created?: boolean }>(
        token,
        `/api/v2/edu/classes/${encodeURIComponent(selectedClass.id)}/snapshots`,
        {
          method: "POST",
          body: JSON.stringify({
            sourceGraphId: imported.courseGraphKey,
            sourceJobId: historySaved && imported.result.source_pdf && !pdfTimedOut ? imported.job_id : undefined,
            filename: imported.filename || importSourceFile?.name || "导入已有图谱",
            nodes: imported.result.nodes,
            edges: imported.result.edges,
            sourceMarkdown,
            latexMacros: imported.result.latex_macros || {},
          }),
        },
      );
      const snapshots = await educationRequest<{ snapshots: EducationSnapshotSummary[] }>(
        token,
        `/api/v2/edu/classes/${encodeURIComponent(selectedClass.id)}/snapshots`,
      );
      const groupedSnapshots = groupCourseGraphs(snapshots.snapshots);
      setCourseGraphs(groupedSnapshots);
      setImportOpen(false);
      setImportNodesFile(null);
      setImportEdgesFile(null);
      setImportSourceFile(null);
      setImportPhase("");
      const baseMessage = snapshotBody.created === false ? "该课程图谱已存在" : "课程图谱已导入并加入班级";
      setSuccess(warnings.length ? `${baseMessage}。${warnings.join("；")}` : baseMessage);
      window.setTimeout(() => setSuccess(""), 3600);
    } catch (cause) {
      setImportError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
      setImportPhase("");
    }
  };

  const createAssignment = async () => {
    if (!assignmentSource || targetId === null) return;
    const classId = selectedSnapshot?.classId || assignmentClassId;
    const assignmentClass = classes.find(item => item.id === classId && item.role === "teacher");
    if (!assignmentClass) {
      setAssignmentError("请选择要布置任务的班级。");
      return;
    }
    if (!selectedSnapshot || !selectedSnapshotId) {
      setAssignmentError("课程图谱尚未加载完成，请稍后重试。");
      return;
    }
    setBusy("assignment");
    setAssignmentError("");
    setError("");
    try {
      const assignmentBody = await educationRequest<{ assignment: EducationAssignment; warnings?: PathOrderWarning[] }>(
        token,
        `/api/v2/edu/classes/${encodeURIComponent(assignmentClass.id)}/assignments`,
        {
          method: "POST",
          body: JSON.stringify({
            snapshotId: selectedSnapshotId,
            targetNodeId: targetId,
            title: assignmentTitle.trim() || `学习：${nodeTitle(selectedAssignmentTarget ?? undefined, targetId)}`,
            dueAt: dueAt ? new Date(dueAt).toISOString() : null,
          }),
        },
      );
      setSelectedClassId(assignmentClass.id);
      setAssignments(items => [assignmentBody.assignment, ...items]);
      setAssignmentCreateOpen(false);
      setAssignmentSource(null);
      setAssignmentClassId("");
      setSelectedSnapshotId("");
      setSelectedSnapshot(null);
      setTargetId(null);
      setAssignmentTitle("");
      setDueAt("");
      onOpenAssignment(assignmentBody.assignment.id);
    } catch (cause) { setAssignmentError(educationErrorMessage(cause)); }
    finally { setBusy(""); }
  };

  const deleteCourseGraph = async () => {
    if (!selectedClass || selectedClass.role !== "teacher" || !deletingCourseGraph) return;
    setBusy("course-graph-delete");
    setDeleteCourseGraphError("");
    try {
      const body = await educationRequest<{
        deletedSnapshotIds: string[];
        deletedAssignmentCount: number;
        cleanupWarnings?: string[];
      }>(token, `/api/v2/edu/snapshots/${encodeURIComponent(deletingCourseGraph.id)}`, { method: "DELETE" });
      const [assignmentBody, snapshotBody] = await Promise.all([
        educationRequest<{ assignments: EducationAssignment[] }>(token, `/api/v2/edu/classes/${encodeURIComponent(selectedClass.id)}/assignments`),
        educationRequest<{ snapshots: EducationSnapshotSummary[] }>(token, `/api/v2/edu/classes/${encodeURIComponent(selectedClass.id)}/snapshots`),
      ]);
      setAssignments(assignmentBody.assignments);
      setCourseGraphs(groupCourseGraphs(snapshotBody.snapshots));
      if (body.deletedSnapshotIds.includes(selectedSnapshotId)) {
        setSelectedSnapshotId("");
        setSelectedSnapshot(null);
        setTargetId(null);
        setAssignmentSource(null);
        setAssignmentCreateOpen(false);
      }
      setDeletingCourseGraph(null);
      const warning = body.cleanupWarnings?.length ? "，部分缓存目录未能清理" : "";
      setSuccess(`课程图谱已删除，同时删除 ${body.deletedAssignmentCount} 个关联任务${warning}`);
      window.setTimeout(() => setSuccess(""), 3200);
      await refreshClasses();
    } catch (cause) {
      setDeleteCourseGraphError(educationErrorMessage(cause));
    } finally {
      setBusy("");
    }
  };

  const loadOverview = async (assignmentId: string) => {
    setBusy(`overview-${assignmentId}`); setError("");
    setOverviewAssignmentId(null);
    closeStudentContextSummary();
    try {
      const [body, grading] = await Promise.all([
        educationRequest<{ students: StudentPathSummary[] }>(token, `/api/v2/edu/assignments/${encodeURIComponent(assignmentId)}/overview`),
        loadEducationGradingOverview(token, assignmentId),
      ]);
      setOverview(body.students);
      setGradingOverview(grading);
      setOverviewAssignmentId(assignmentId);
      setStudentContextSummary(null);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(""); }
  };

  const openGradingSubmission = async (submissionId: string, evaluateFirst: boolean) => {
    if (gradingLaunchLockRef.current.has(submissionId)) return;
    gradingLaunchLockRef.current.add(submissionId);
    setBusy(`grading-${submissionId}`); setGradingLaunchPhase({ submissionId, phase: "loading" }); setGradingError("");
    try {
      let submission = await loadEducationSubmission(token, submissionId);
      setGradingSubmission(submission);
      const scoringIncomplete = (submission.grades || []).some(gradingStandardIncomplete);
      if (evaluateFirst && !scoringIncomplete && submission.status !== "finalized" && submission.status !== "released" && submission.aiStatus !== "ready") {
        setGradingLaunchPhase({ submissionId, phase: "evaluating" });
        try { submission = await evaluateEducationSubmission(token, submissionId); setGradingSubmission(submission); }
        catch (cause) {
          setGradingError(educationErrorMessage(cause));
          submission = await loadEducationSubmission(token, submissionId);
          setGradingSubmission(submission);
        }
      }
    } catch (cause) { setError(educationErrorMessage(cause)); }
    finally {
      gradingLaunchLockRef.current.delete(submissionId);
      setGradingLaunchPhase(current => current?.submissionId === submissionId ? null : current);
      setBusy(current => current === `grading-${submissionId}` ? "" : current);
    }
  };

  const handleGradingUpdated = (submission: EducationSubmission) => {
    setGradingSubmission(submission);
    setGradingOverview(current => current ? { ...current, students: current.students.map(student => student.submissionId === submission.id ? { ...student, submissionStatus: submission.status, aiStatus: submission.aiStatus || student.aiStatus, aiSuggestedTotal: submission.aiSuggestedTotal, teacherTotal: submission.teacherTotal, updatedAt: submission.updatedAt } : student) } : current);
  };

  const releaseGrades = async () => {
    if (!overviewAssignmentId || !gradingOverview?.canPublish) return;
    setBusy("grades-publish"); setError("");
    try {
      await publishEducationGrades(token, overviewAssignmentId);
      await loadOverview(overviewAssignmentId);
      setSuccess("成绩已统一发布，学习路径掌握状态已更新");
    } catch (cause) { setError(educationErrorMessage(cause)); }
    finally { setBusy(""); }
  };

  const openStudentContextSummary = async (studentUserId: number) => {
    if (!overviewAssignmentId) return;
    setStudentContextSummary(null);
    setStudentContextSummaryError("");
    setStudentContextSummaryLoading(true);
    try {
      setStudentContextSummary(await loadTeacherStudentContextSummary(token, overviewAssignmentId, studentUserId));
    } catch (cause) {
      setStudentContextSummaryError(educationErrorMessage(cause));
    } finally {
      setStudentContextSummaryLoading(false);
    }
  };

  const closeStudentContextSummary = () => {
    setStudentContextSummary(null);
    setStudentContextSummaryError("");
    setStudentContextSummaryLoading(false);
  };

  const openAssignmentEditor = (assignment: EducationAssignment) => {
    setEditingAssignment(assignment);
    setEditTitle(assignment.title);
    setEditDueAt(toDatetimeLocal(assignment.dueAt));
    setEditError("");
    setDeleteConfirm(false);
  };

  const closeAssignmentEditor = () => {
    setEditingAssignment(null);
    setEditTitle("");
    setEditDueAt("");
    setEditError("");
    setDeleteConfirm(false);
  };

  const updatePublishedAssignment = async () => {
    if (!editingAssignment) return;
    const title = editTitle.trim();
    if (!title) { setEditError("请输入任务名称"); return; }
    if (title.length > 160) { setEditError("任务名称不能超过 160 个字符"); return; }
    let dueAt: string | null = null;
    if (editDueAt) {
      const date = new Date(editDueAt);
      if (Number.isNaN(date.getTime())) { setEditError("请输入有效的截止时间"); return; }
      dueAt = date.toISOString();
    }
    setBusy("assignment-update"); setEditError("");
    try {
      const assignment = await updatePublishedEducationAssignment(token, editingAssignment.id, { title, dueAt });
      setAssignments(items => items.map(item => item.id === assignment.id ? { ...item, ...assignment } : item));
      closeAssignmentEditor();
      setSuccess("任务信息已更新");
      window.setTimeout(() => setSuccess(""), 2400);
    } catch (cause) { setEditError(educationErrorMessage(cause)); }
    finally { setBusy(""); }
  };

  const archivePublishedAssignment = async () => {
    if (!editingAssignment) return;
    setBusy("assignment-delete"); setEditError("");
    try {
      await archiveEducationAssignment(token, editingAssignment.id);
      setAssignments(items => items.filter(item => item.id !== editingAssignment.id));
      setOverview(null);
      setOverviewAssignmentId(null);
      closeStudentContextSummary();
      closeAssignmentEditor();
      await refreshClasses();
      setSuccess("任务已删除");
      window.setTimeout(() => setSuccess(""), 2400);
    } catch (cause) { setEditError(educationErrorMessage(cause)); }
    finally { setBusy(""); }
  };

  if (loading) return <div className="edu-root"><div className="edu-loading"><Loader2 className="edu-spin" />正在打开教育空间…</div></div>;

  return (
    <div className={`edu-root${educationModalOpen ? " edu-modal-open" : ""}`} data-theme={theme}>
      <header className="edu-header">
        <div className="edu-heading">
          <div className="edu-brand" aria-label="绎理">
            <img className="edu-brand-mark" src="/mathweaver-icon.png" alt="" aria-hidden="true" />
            <span className="edu-brand-name">绎理</span>
          </div>
          <span className={`edu-role-badge ${educationRole}`}>
            <span className="edu-role-dot" />
            {educationRole === "teacher" ? "教师端" : "学生端"}
          </span>
        </div>
        <div className="edu-header-actions">
          {educationRole === "teacher" && (
            <button
              type="button"
              className="edu-button ghost"
              title="创建班级"
              aria-label="创建班级"
              onClick={openCreateClass}
            >
              <Plus size={15} /><span className="edu-header-button-label">创建班级</span>
            </button>
          )}
          <button
            type="button"
            className="edu-button ghost"
            title="班级设置"
            aria-label="班级设置"
            disabled={educationRole === "teacher" && !selectedClass}
            onClick={openClassSettings}
          >
            <Settings size={15} /><span className="edu-header-button-label">班级设置</span>
          </button>
        </div>
      </header>

      <div className="edu-shell">
        <aside className="edu-sidebar">
          <div className="edu-sidebar-title"><School size={15} />我的班级</div>
          <div className="edu-class-list">
            {classes.map(item => (
              <button key={item.id} className={`edu-class-item ${selectedClassId === item.id ? "active" : ""}`} onClick={() => { setSelectedClassId(item.id); setSuccess(""); setOverview(null); setOverviewAssignmentId(null); closeStudentContextSummary(); }}>
                <span>{item.title}</span><small>{item.assignmentCount ?? 0} 个作业</small>
              </button>
            ))}
            {!classes.length && <p className="edu-muted">{educationRole === "teacher" ? "还没有班级，请点击右上角创建。" : "还没有班级，请在班级设置中加入。"}</p>}
          </div>
        </aside>

        <main className="edu-main">
          {error && <div className="edu-error">{error}</div>}
          {success && <div className="edu-success"><Check size={15} />{success}</div>}
          {!selectedClass && <div className="edu-empty"><GraduationCap size={34} /><strong>{targetNode ? `已选择学习目标：${nodeTitle(targetNode, targetNode.id)}` : "选择一个班级开始"}</strong><span>{targetNode ? "创建或选择你担任教师的班级，即可生成学习路径。" : educationRole === "teacher" ? "教师可以从当前图谱发布学习路径。" : "学生可以查看已发布作业并开始学习。"}</span><button className="edu-button secondary" onClick={educationRole === "teacher" ? openCreateClass : openClassSettings}>{educationRole === "teacher" ? <><Plus size={14} />创建班级</> : <><Settings size={14} />打开班级设置</>}</button></div>}

          {selectedClass && (
            <>
              <section className="edu-class-head">
                <div><span className="edu-kicker">{selectedClass.role === "teacher" ? "教师视图" : "学生视图"}</span><h1>{selectedClass.title}</h1></div>
              </section>

              <section className="edu-section edu-course-graphs-section">
                <div className="edu-section-heading-row">
                  <div className="edu-section-title"><GitBranch size={17} /><strong>课程图谱</strong><span>{courseGraphs.length}</span></div>
                  {selectedClass.role === "teacher" && (
                    <div className="edu-course-graph-actions">
                      <button className="edu-button ghost" onClick={onOpenCreate}><GitBranch size={14} />自主建图</button>
                      <button className="edu-button ghost edu-import-open" onClick={openGraphImport}><Upload size={14} />导入图谱</button>
                    </div>
                  )}
                </div>
                {courseGraphsLoading && !courseGraphs.length ? (
                  <div className="edu-course-graph-loading"><Loader2 className="edu-spin" size={15} />正在加载课程图谱…</div>
                ) : courseGraphs.length ? (
                  <div className="edu-course-graph-grid">
                    {courseGraphs.map(graph => {
                      const relatedAssignments = assignments.filter(item => graph.snapshotIds.includes(item.snapshotId));
                      const boundAssignmentCount = graph.boundAssignmentCount ?? relatedAssignments.length;
                      return (
                        <article className={`edu-course-graph-card ${graph.snapshotIds.includes(selectedSnapshotId) ? "selected" : ""}`} key={graph.id}>
                          <div className="edu-course-graph-icon"><GitBranch size={18} /></div>
                          <div className="edu-course-graph-copy"><strong>{graph.filename}</strong><small>{graph.nodeCount} 个节点 · {graph.edgeCount} 条关系 · {boundAssignmentCount} 个关联任务</small><small>加入时间：{formatDate(graph.createdAt)}</small></div>
                          <div className="edu-row-actions">
                            <button className="edu-button ghost" onClick={() => onOpenCourseGraph(graph.id)}><BookOpen size={14} />查看图谱</button>
                            {selectedClass.role === "teacher" && <button className="edu-button secondary" onClick={() => openAssignmentCreatorFromCourseGraph(graph)}><Route size={14} />布置任务</button>}
                            {selectedClass.role === "teacher" && <button className="edu-button ghost edu-course-delete" title="删除图谱" aria-label={`删除课程图谱 ${graph.filename}`} onClick={() => { setDeleteCourseGraphError(""); setDeletingCourseGraph(graph); }}><Trash2 size={14} /></button>}
                          </div>
                        </article>
                      );
                    })}
                  </div>
                ) : (
                  <p className="edu-course-graph-empty">{selectedClass.role === "teacher" ? "暂无课程图谱，可先自主建图，或直接导入已有图谱。" : "教师还没有提供课程图谱。"}</p>
                )}
              </section>

              {selectedClass.role === "student" && selectedClass.profileComplete === false ? (
                <section className="edu-card edu-profile-required">
                  <div className="edu-card-title"><Users size={18} /><div><strong>请先完善班级资料</strong><small>教师需要姓名和学号来识别学习进度</small></div></div>
                  <p>完成资料后即可查看该班级的学习任务。</p>
                  <button className="edu-button secondary" onClick={openClassSettings}><Pencil size={14} />完善姓名和学号</button>
                </section>
              ) : (
                <section className="edu-section">
                  <div className="edu-section-title"><ClipboardList size={17} /><strong>{selectedClass.role === "teacher" ? "班级作业" : "我的学习任务"}</strong><span>{assignments.length}</span></div>
                  <div className="edu-assignment-grid">
                    {assignments.map(item => (
                      <article className="edu-assignment" key={item.id}>
                        <div className={`edu-status ${item.status}`}>{item.status === "draft" ? "草稿" : "已发布"}</div>
                        <h3>{item.title}</h3><p>{item.summary || `${item.path.steps.length} 个学习步骤`}</p>
                        <p className="edu-assignment-source">课程图谱：{courseGraphById.get(item.snapshotId)?.filename || "已绑定课程图谱"}</p>
                        <div className="edu-assignment-meta"><span>{item.path.steps.length} 步</span><span>截止 {formatDate(item.dueAt)}</span></div>
                        <div className="edu-row-actions">
                          <button className="edu-button primary" onClick={() => onOpenAssignment(item.id)}><BookOpen size={14} />{item.role === "teacher" && item.status === "draft" ? "编辑并发布" : item.role === "student" ? "开始学习" : "查看路径"}</button>
                          {item.role === "student" && <button className="edu-button ghost" onClick={() => onOpenCourseGraph(item.snapshotId)}><GitBranch size={14} />课程图谱</button>}
                          {item.role === "teacher" && item.status === "published" && <button className="edu-button ghost" disabled={busy === `overview-${item.id}`} onClick={() => loadOverview(item.id)}><ClipboardList size={14} />批改作业</button>}
                          {item.role === "teacher" && item.status === "published" && <button className="edu-button ghost" onClick={() => openAssignmentEditor(item)}><Pencil size={14} />编辑</button>}
                        </div>
                      </article>
                    ))}
                    {!assignments.length && <div className="edu-empty compact"><ClipboardList size={25} /><span>还没有作业</span></div>}
                  </div>
                </section>
              )}

              {overview && gradingOverview && <section className="edu-card edu-grading-overview-card">
                <div className="edu-card-title edu-grading-overview-title"><Users size={18} /><div><strong>作业批改</strong><small>AI 评价由教师发起，最终分数需逐题确认后统一发布</small></div><button className="edu-button primary" disabled={!gradingOverview.canPublish || busy === "grades-publish"} onClick={() => void releaseGrades()}>{busy === "grades-publish" ? <Loader2 className="edu-spin" size={14} /> : <Send size={14} />}发布成绩</button></div>
                {gradingOverview.gradesPublishedAt && <div className="edu-grade-release-note"><CheckCircle2 size={15} />成绩已于 {formatDate(gradingOverview.gradesPublishedAt)} 发布</div>}
                <div className="edu-table edu-grading-table">
                  <div className="edu-table-row head"><span>学生</span><span>提交状态</span><span>AI 评价</span><span>建议分</span><span>教师分</span><span>操作</span></div>
                  {gradingOverview.students.map(student => {
                    const progress = overview.find(item => item.userId === student.userId);
                    const launchPhase = student.submissionId && gradingLaunchPhase?.submissionId === student.submissionId ? gradingLaunchPhase.phase : null;
                    const gradingActionLabel = gradingOverviewActionLabel(launchPhase, student.submissionStatus);
                    return <div className="edu-table-row" key={student.userId}>
                      <span><b>{student.studentName || "资料待补全"}</b><small>学号：{student.studentNumber || "待补全"}</small><small>{progress?.diagnosticSummary || "暂无诊断摘要"}</small></span>
                      <span><b className={`edu-grade-status ${student.submissionStatus}`}>{submissionStatusLabel(student.submissionStatus)}</b><small>{formatDate(student.submittedAt)}</small></span>
                      <span>{student.aiStatus === "running" ? "评价中" : student.aiStatus === "ready" ? "已生成" : student.aiStatus === "failed" ? "失败，可手工评分" : "未发起"}</span>
                      <span>{student.aiSuggestedTotal == null ? "—" : student.aiSuggestedTotal.toFixed(1)}</span>
                      <span>{student.teacherTotal == null ? "—" : student.teacherTotal.toFixed(1)}</span>
                      <span className="edu-grading-row-actions">{student.submissionId ? <button className="edu-button secondary" disabled={launchPhase !== null} aria-busy={launchPhase !== null} onClick={() => void openGradingSubmission(student.submissionId!, student.aiStatus === "not_started" || student.aiStatus === "failed")}>{launchPhase ? <Loader2 className="edu-spin" size={14} /> : <Brain size={14} />}{gradingActionLabel}</button> : <small>等待学生提交</small>}<button className="edu-button ghost edu-context-summary-button" disabled={!overviewAssignmentId || studentContextSummaryLoading} onClick={() => void openStudentContextSummary(student.userId)}><ClipboardList size={14} />掌握情况</button></span>
                    </div>;
                  })}
                </div>
                {!gradingOverview.canPublish && gradingOverview.students.some(student => student.submissionId) && !gradingOverview.gradesPublishedAt && <small className="edu-grading-publish-hint">所有已提交作业完成教师定稿后，才可统一发布成绩。</small>}
              </section>}
            </>
          )}

        </main>
      </div>
      {gradingSubmission && <GradingDialog token={token} submission={gradingSubmission} theme={theme} evaluating={gradingLaunchPhase?.submissionId === gradingSubmission.id && gradingLaunchPhase.phase === "evaluating"} onUpdated={handleGradingUpdated} onClose={() => { setGradingSubmission(null); setGradingError(""); if (overviewAssignmentId) void loadOverview(overviewAssignmentId); }} />}
      {gradingError && !gradingSubmission && <div className="edu-modal-error">{gradingError}</div>}
            {assignmentCreateOpen && assignmentSource && typeof document !== "undefined" && createPortal(
        <div className="edu-root edu-modal-backdrop edu-assignment-create-backdrop" data-theme={theme} onClick={closeAssignmentCreator}>
          <section className="edu-settings-modal edu-assignment-create-modal" role="dialog" aria-modal="true" aria-labelledby="edu-assignment-create-title" onClick={event => event.stopPropagation()}>
            <div className="edu-settings-head">
              <div><span className="edu-kicker">学习路径</span><h2 id="edu-assignment-create-title">布置学习任务</h2></div>
              <button className="edu-icon-button" disabled={busy === "assignment"} onClick={closeAssignmentCreator} aria-label="关闭"><X size={17} /></button>
            </div>
            <p className="edu-settings-copy">选择目标节点并填写任务信息，系统将同步生成学习路径及每个节点的考察题，生成后可逐题检查。</p>
            <div className="edu-assignment-create-source"><GitBranch size={17} /><div><small>课程图谱</small><strong>{assignmentSourceLabel}</strong></div></div>
            <div className="edu-assignment-create-form">
              <label className="edu-settings-field"><span>学习目标</span><select autoFocus value={targetId ?? ""} disabled={busy === "assignment" || !selectedSnapshot || courseGraphsLoading} onChange={event => setTargetId(Number(event.target.value))}><option value="" disabled>{courseGraphsLoading ? "正在加载节点…" : "选择目标节点"}</option>{assignmentNodes.map(node => <option key={node.id} value={node.id}>{nodeTitle(node, node.id)}</option>)}</select></label>
              <label className="edu-settings-field"><span>任务标题</span><input value={assignmentTitle} maxLength={160} disabled={busy === "assignment"} onChange={event => setAssignmentTitle(event.target.value)} placeholder="例如：掌握基扩张定理" /></label>
              <label className="edu-settings-field"><span>截止时间</span><input type="datetime-local" value={dueAt} disabled={busy === "assignment"} onChange={event => setDueAt(event.target.value)} /></label>
            </div>
            {assignmentError && <div className="edu-modal-error">{assignmentError}</div>}
            <button className="edu-button primary edu-settings-submit" disabled={!assignmentClassId || targetId === null || !assignmentNodes.length || busy === "assignment" || !selectedSnapshot} onClick={() => void createAssignment()}>{busy === "assignment" ? <><Loader2 className="edu-spin" size={15} />正在生成学习路径及考察题…</> : <><Route size={15} />生成学习路径及考察题</>}</button>
          </section>
        </div>,
        document.body,
      )}
      {(studentContextSummaryLoading || studentContextSummary || studentContextSummaryError) && (
        <div className="edu-modal-backdrop" onClick={closeStudentContextSummary}>
          <section className="edu-settings-modal edu-student-context-modal" role="dialog" aria-modal="true" aria-labelledby="edu-student-context-title" onClick={event => event.stopPropagation()}>
            <div className="edu-settings-head">
              <div><span className="edu-kicker">学习诊断</span><h2 id="edu-student-context-title">学生掌握情况</h2></div>
              <button className="edu-icon-button" onClick={closeStudentContextSummary} aria-label="关闭"><X size={17} /></button>
            </div>
            {studentContextSummaryLoading ? (
              <div className="edu-context-summary-loading"><Loader2 className="edu-spin" size={16} />正在加载掌握情况…</div>
            ) : studentContextSummary ? (
              <>
                <div className="edu-context-student">
                  <ClipboardList size={18} />
                  <div><strong>{studentContextSummary.student.studentName || "资料待补全"}</strong><small>学号：{studentContextSummary.student.studentNumber || "待补全"}{studentContextSummary.summary.courseSummaryUpdatedAt ? ` · 最近更新 ${formatDate(studentContextSummary.summary.courseSummaryUpdatedAt)}` : ""}</small></div>
                </div>
                <div className="edu-context-metrics">
                  <div><strong>{studentContextSummary.summary.nodeStates.filter(item => item.masteryState === "needs_review").length}</strong><span>需重点复习</span></div>
                  <div><strong>{studentContextSummary.summary.nodeStates.filter(item => (item.riskSummary.items?.length ?? 0) > 0).length}</strong><span>相关知识提醒</span></div>
                  <div><strong>{studentContextSummary.summary.evidence.length}</strong><span>学习判断依据</span></div>
                </div>
                <section className="edu-context-summary-section">
                  <h3>知识掌握情况</h3>
                  {studentContextSummary.summary.nodeStates.length ? <div className="edu-context-node-list">{studentContextSummary.summary.nodeStates.map(item => <div className={`edu-context-node-state ${item.masteryState}`} key={item.nodeId}><div><strong>{item.title}</strong><small>{item.openEvidenceCount > 0 ? `${item.openEvidenceCount} 个待解决问题` : "暂无待解决问题"}</small></div><span>{learningMasteryStateLabel(item.masteryState)}</span></div>)}</div> : <p className="edu-muted">尚未形成知识掌握情况。</p>}
                </section>
                <section className="edu-context-summary-section">
                  <h3>判断依据</h3>
                  <p>仅展示用于判断的学习片段，不显示完整对话。</p>
                  {studentContextSummary.summary.evidence.length ? <div className="edu-context-evidence-list">{studentContextSummary.summary.evidence.map(item => <article key={item.id}><div><span>{learningEvidenceKindLabel(item.kind)}</span><small>{Math.round(item.confidence * 100)}% 可信度 · {item.status === "confirmed" ? "已确认" : "未解决"}</small></div><strong>{item.claim}</strong>{item.excerpt && <p>{item.excerpt}</p>}<code>记录编号：{item.id}</code></article>)}</div> : <p className="edu-muted">暂无开放或已确认的学习记录。</p>}
                </section>
              </>
            ) : null}
            {studentContextSummaryError && <div className="edu-modal-error">{studentContextSummaryError}</div>}
          </section>
        </div>
      )}
      {deletingCourseGraph && (
        <div className="edu-modal-backdrop" onClick={() => { if (busy !== "course-graph-delete") setDeletingCourseGraph(null); }}>
          <section className="edu-settings-modal edu-confirm-modal" role="alertdialog" aria-modal="true" aria-labelledby="edu-delete-course-graph-title" onClick={event => event.stopPropagation()}>
            <div className="edu-settings-head">
              <div><span className="edu-kicker">不可恢复</span><h2 id="edu-delete-course-graph-title">删除课程图谱</h2></div>
              <button className="edu-icon-button" disabled={busy === "course-graph-delete"} onClick={() => setDeletingCourseGraph(null)} aria-label="关闭"><X size={17} /></button>
            </div>
            <p className="edu-settings-copy">确定删除“{deletingCourseGraph.filename}”吗？</p>
            {(deletingCourseGraph.boundAssignmentCount ?? 0) > 0 ? (
              <div className="edu-delete-warning">
                该图谱关联 {deletingCourseGraph.boundAssignmentCount} 个任务。确认后，同源历史图谱、草稿、已发布或已归档任务，以及学生进度、个性化路径和诊断记录都会一并删除。
              </div>
            ) : (
              <div className="edu-delete-warning">确认后将永久删除该课程图谱及同源历史快照。</div>
            )}
            <p className="edu-settings-copy">自主建图的原始结果和账户历史不会被删除，之后仍可重新导入。</p>
            {deleteCourseGraphError && <div className="edu-modal-error">{deleteCourseGraphError}</div>}
            <div className="edu-row-actions edu-confirm-actions">
              <button className="edu-button ghost" disabled={busy === "course-graph-delete"} onClick={() => setDeletingCourseGraph(null)}>取消</button>
              <button className="edu-button danger" disabled={busy === "course-graph-delete"} onClick={() => void deleteCourseGraph()}>
                {busy === "course-graph-delete" ? <><Loader2 className="edu-spin" size={15} />正在删除…</> : <><Trash2 size={15} />确认删除</>}
              </button>
            </div>
          </section>
        </div>
      )}
      {createOpen && (
        <div className="edu-modal-backdrop" onClick={() => setCreateOpen(false)}>
          <section className="edu-settings-modal edu-create-modal" role="dialog" aria-modal="true" aria-labelledby="edu-create-title" onClick={event => event.stopPropagation()}>
            <div className="edu-settings-head"><div><span className="edu-kicker">教育空间</span><h2 id="edu-create-title">创建班级</h2></div><button className="edu-icon-button" onClick={() => setCreateOpen(false)} aria-label="关闭"><X size={17} /></button></div>
            <p className="edu-settings-copy">创建后会生成专属邀请码，你可以分享给学生加入。</p>
            <label className="edu-settings-field"><span>班级名称</span><input autoFocus value={classTitle} onChange={event => setClassTitle(event.target.value)} placeholder="例如：线性代数 2026" onKeyDown={event => { if (event.key === "Enter") void createClass(); }} /></label>
            {settingsError && <div className="edu-modal-error">{settingsError}</div>}
            <button className="edu-button primary edu-settings-submit" disabled={!classTitle.trim() || busy === "class"} onClick={() => void createClass()}>{busy === "class" ? <><Loader2 className="edu-spin" size={15} />正在创建…</> : <><Plus size={15} />创建班级</>}</button>
          </section>
        </div>
      )}
      {importOpen && (
        <div className="edu-modal-backdrop" onClick={closeGraphImport}>
          <section className="edu-settings-modal edu-import-modal" role="dialog" aria-modal="true" aria-labelledby="edu-import-title" onClick={event => event.stopPropagation()}>
            <div className="edu-settings-head">
              <div><span className="edu-kicker">课程图谱</span><h2 id="edu-import-title">导入已有图谱</h2></div>
              <button className="edu-icon-button" disabled={busy === "course-import"} onClick={closeGraphImport} aria-label="关闭"><X size={17} /></button>
            </div>
            <p className="edu-settings-copy">导入节点、关系和可选原文，完成后直接加入当前班级。</p>
            <div className="edu-import-file-list">
              <GraphImportFileField label="Node JSON" hint="已有图谱的节点文件" file={importNodesFile} accept=".json,application/json" disabled={busy === "course-import"} onChange={setImportNodesFile} />
              <GraphImportFileField label="Edge JSON" hint="已有图谱的关系文件" file={importEdgesFile} accept=".json,application/json" disabled={busy === "course-import"} onChange={setImportEdgesFile} />
              <GraphImportFileField label="原始 Markdown / TeX" hint="用于原文定位、TeX 宏渲染和 PDF 查看" file={importSourceFile} accept=".md,.tex,.txt,text/markdown,text/plain,text/x-tex,application/x-tex" optional disabled={busy === "course-import"} onChange={setImportSourceFile} />
            </div>
            {importError && <div className="edu-modal-error">{importError}</div>}
            {importPhase && <div className="edu-import-phase"><Loader2 className="edu-spin" size={14} />{importPhase}</div>}
            <button className="edu-button primary edu-settings-submit" disabled={!importNodesFile || !importEdgesFile || busy === "course-import"} onClick={() => void importCourseGraph()}>
              {busy === "course-import" ? <><Loader2 className="edu-spin" size={15} />正在导入…</> : <><Upload size={15} />导入并加入班级</>}
            </button>
          </section>
        </div>
      )}
      {settingsOpen && (
        <div className="edu-modal-backdrop" onClick={closeClassSettings}>
          <section className={`edu-settings-modal ${educationRole === "teacher" ? "edu-settings-menu-modal" : ""}`} role="dialog" aria-modal="true" aria-labelledby="edu-settings-title" onClick={event => event.stopPropagation()}>
            <div className="edu-settings-head"><div><span className="edu-kicker">教育空间</span><h2 id="edu-settings-title">{educationRole === "teacher" ? "班级设置" : selectedClass ? "班级设置" : "加入班级"}</h2></div><button className="edu-icon-button" onClick={closeClassSettings} aria-label="关闭"><X size={17} /></button></div>
            {educationRole === "teacher" ? (
              <>
                <p className="edu-settings-copy">选择要进行的班级管理操作。</p>
                {selectedClass?.inviteCode && <div className="edu-settings-invite"><div><small>班级邀请码</small><b>{selectedClass.inviteCode}</b></div><button className="edu-button ghost" onClick={() => { void navigator.clipboard.writeText(selectedClass.inviteCode!); setCopied(true); window.setTimeout(() => setCopied(false), 1500); }}><Copy size={14} />{copied ? "已复制" : "复制"}</button></div>}
                <div className="edu-settings-menu-list">
                  <button className="edu-settings-menu-item" onClick={() => { setSettingsError(""); setManagementDialog("rename"); }}><span className="edu-settings-menu-icon"><Pencil size={16} /></span><span><strong>修改班级名称</strong><small>{selectedClass?.title}</small></span><ChevronRight size={17} /></button>
                  <button className="edu-settings-menu-item" onClick={() => { setSettingsError(""); setManagementDialog("members"); }}><span className="edu-settings-menu-icon"><Users size={17} /></span><span><strong>查看学生</strong><small>{activeStudentCount} 位学生已加入</small></span><ChevronRight size={17} /></button>
                  <button className="edu-settings-menu-item danger" onClick={() => { setSettingsError(""); setDissolveTitle(""); setManagementDialog("dissolve"); }}><span className="edu-settings-menu-icon"><Settings size={16} /></span><span><strong>解散班级</strong><small>停止邀请码和班级资源使用</small></span><ChevronRight size={17} /></button>
                </div>
                {settingsError && <div className="edu-modal-error">{settingsError}</div>}
                <div className="edu-settings-account"><div><small>当前登录</small><strong>教师端</strong></div><button className="edu-button ghost" onClick={() => { closeClassSettings(); onReauthenticate(); }}>退出登录</button></div>
              </>
            ) : (
              <>
                {selectedClass && <div className="edu-student-profile-form">
                  <div className="edu-settings-section-head"><strong>我的班级资料</strong><span>{selectedClass.profileComplete === false ? "请先补全" : "可随时修改"}</span></div>
                  <label className="edu-settings-field"><span>姓名</span><input autoFocus value={studentName} onChange={event => setStudentName(event.target.value)} placeholder="请输入姓名" /></label>
                  <label className="edu-settings-field"><span>学号</span><input value={studentNumber} onChange={event => setStudentNumber(event.target.value.toUpperCase())} placeholder="例如：20260001" /></label>
                  {studentProfileRequired && settingsError && <div className="edu-modal-error">{settingsError}</div>}
                  <button className="edu-button primary edu-settings-submit" disabled={!studentName.trim() || !studentNumber.trim() || busy === "profile"} onClick={() => void saveStudentProfile()}>{busy === "profile" ? <><Loader2 className="edu-spin" size={15} />正在保存…</> : <><Check size={15} />保存班级资料</>}</button>
                </div>}
                {!studentProfileRequired && <>
                  <div className="edu-settings-section">
                    <div className="edu-settings-section-head"><strong>加入其他班级</strong><span>需要重新填写身份信息</span></div>
                    <label className="edu-settings-field"><span>姓名</span><input autoFocus={!selectedClass} value={joinStudentName} onChange={event => setJoinStudentName(event.target.value)} placeholder="请输入姓名" onKeyDown={event => { if (event.key === "Enter") void joinClass(); }} /></label>
                    <label className="edu-settings-field"><span>学号</span><input value={joinStudentNumber} onChange={event => setJoinStudentNumber(event.target.value.toUpperCase())} placeholder="例如：20260001" onKeyDown={event => { if (event.key === "Enter") void joinClass(); }} /></label>
                    <label className="edu-settings-field"><span>班级邀请码</span><input value={inviteCode} onChange={event => setInviteCode(event.target.value.toUpperCase())} placeholder="例如：A1B2C3D4" onKeyDown={event => { if (event.key === "Enter") void joinClass(); }} /></label>
                  </div>
                  {settingsError && <div className="edu-modal-error">{settingsError}</div>}
                  <button className="edu-button primary edu-settings-submit" disabled={!inviteCode.trim() || !joinStudentName.trim() || !joinStudentNumber.trim() || busy === "join"} onClick={() => void joinClass()}>{busy === "join" ? <><Loader2 className="edu-spin" size={15} />正在加入…</> : <><Users size={15} />加入班级</>}</button>
                  <div className="edu-settings-account"><div><small>当前登录</small><strong>学生端</strong></div><button className="edu-button ghost" onClick={() => { closeClassSettings(); onReauthenticate(); }}>退出登录</button></div>
                </>}
              </>
            )}
          </section>
        </div>
      )}
      {settingsOpen && educationRole === "teacher" && managementDialog && (
        <div className="edu-modal-backdrop edu-secondary-modal-backdrop" onClick={closeManagementDialog}>
          <section className="edu-settings-modal edu-secondary-modal" role="dialog" aria-modal="true" aria-labelledby="edu-secondary-title" onClick={event => event.stopPropagation()}>
            <div className="edu-secondary-head"><button className="edu-secondary-back" onClick={closeManagementDialog}><ArrowLeft size={15} />班级设置</button><button className="edu-icon-button" onClick={closeManagementDialog} aria-label="关闭"><X size={17} /></button></div>
            {managementDialog === "rename" && (
              <>
                <div className="edu-secondary-title"><span className="edu-settings-menu-icon"><Pencil size={17} /></span><div><span className="edu-kicker">班级管理</span><h2 id="edu-secondary-title">修改班级名称</h2></div></div>
                <p className="edu-settings-copy">修改后会立即同步给班级成员。</p>
                <label className="edu-settings-field"><span>班级名称</span><input autoFocus value={renameTitle} onChange={event => setRenameTitle(event.target.value)} onKeyDown={event => { if (event.key === "Enter") void renameClass(); }} /></label>
                {settingsError && <div className="edu-modal-error">{settingsError}</div>}
                <button className="edu-button primary edu-settings-submit" disabled={!renameTitle.trim() || busy === "rename"} onClick={() => void renameClass()}>{busy === "rename" ? <><Loader2 className="edu-spin" size={15} />正在保存…</> : "保存名称"}</button>
              </>
            )}
            {managementDialog === "members" && (
              <>
                <div className="edu-secondary-title"><span className="edu-settings-menu-icon"><Users size={18} /></span><div><span className="edu-kicker">班级管理</span><h2 id="edu-secondary-title">查看学生</h2></div></div>
                <p className="edu-settings-copy">查看当前班级成员，并管理被移出的学生。</p>
                {membersLoading ? <div className="edu-members-loading"><Loader2 className="edu-spin" size={15} />正在加载学生名单…</div> : members.length ? <div className="edu-member-list">{members.map(member => <div className={`edu-member-row ${member.status}`} key={member.userId}><div><b>{member.studentName || "资料待补全"}</b><small>{member.studentNumber ? `学号：${member.studentNumber}` : "学号待补全"} · {member.status === "active" ? `加入于 ${formatDate(member.joinedAt)}` : `已移出 · ${formatDate(member.removedAt)}`}</small></div>{member.status === "active" ? <button className="edu-button ghost edu-member-action" disabled={busy === `remove-${member.userId}`} onClick={() => void removeMember(member)}>{busy === `remove-${member.userId}` ? <Loader2 className="edu-spin" size={13} /> : "移出"}</button> : <button className="edu-button secondary edu-member-action" disabled={busy === `restore-${member.userId}`} onClick={() => void restoreMember(member)}>{busy === `restore-${member.userId}` ? <Loader2 className="edu-spin" size={13} /> : "允许重新加入"}</button>}</div>)}</div> : <div className="edu-members-empty">还没有学生加入这个班级。</div>}
                {settingsError && <div className="edu-modal-error">{settingsError}</div>}
              </>
            )}
            {managementDialog === "dissolve" && (
              <>
                <div className="edu-secondary-title danger"><span className="edu-settings-menu-icon"><Settings size={17} /></span><div><span className="edu-kicker">危险操作</span><h2 id="edu-secondary-title">解散班级</h2></div></div>
                <div className="edu-danger-copy">解散后邀请码和班级资源将立即停止使用，历史数据会保留。</div>
                <label className="edu-settings-field"><span>输入“{selectedClass?.title}”确认</span><input autoFocus value={dissolveTitle} onChange={event => setDissolveTitle(event.target.value)} placeholder={selectedClass?.title || "班级名称"} /></label>
                {settingsError && <div className="edu-modal-error">{settingsError}</div>}
                <button className="edu-button danger edu-settings-submit" disabled={!selectedClass || dissolveTitle !== selectedClass.title || busy === "dissolve"} onClick={() => void dissolveClass()}>{busy === "dissolve" ? <><Loader2 className="edu-spin" size={14} />正在解散…</> : "解散班级"}</button>
              </>
            )}
          </section>
        </div>
      )}
      {editingAssignment && (
        <div className="edu-modal-backdrop" onClick={closeAssignmentEditor}>
          <section className="edu-settings-modal edu-assignment-edit-modal" role="dialog" aria-modal="true" aria-labelledby="edu-assignment-edit-title" onClick={event => event.stopPropagation()}>
            <div className="edu-settings-head">
              <div><span className="edu-kicker">任务管理</span><h2 id="edu-assignment-edit-title">编辑任务</h2></div>
              <button className="edu-icon-button" onClick={closeAssignmentEditor} aria-label="关闭"><X size={17} /></button>
            </div>
            <p className="edu-settings-copy">修改已发布任务的名称和截止时间，学习路径与学生记录不会改变。</p>
            <label className="edu-settings-field"><span>任务名称</span><input autoFocus value={editTitle} maxLength={160} onChange={event => setEditTitle(event.target.value)} onKeyDown={event => { if (event.key === "Enter") void updatePublishedAssignment(); }} /></label>
            <label className="edu-settings-field edu-assignment-edit-due"><span>截止时间</span><input type="datetime-local" value={editDueAt} onChange={event => setEditDueAt(event.target.value)} /><small>留空表示不设置截止时间。</small></label>
            {editError && <div className="edu-modal-error">{editError}</div>}
            <div className="edu-assignment-edit-actions">
              <button className="edu-button primary" disabled={!editTitle.trim() || busy === "assignment-update" || busy === "assignment-delete"} onClick={() => void updatePublishedAssignment()}>{busy === "assignment-update" ? <><Loader2 className="edu-spin" size={15} />正在保存…</> : "保存修改"}</button>
              <button className="edu-button danger" disabled={busy === "assignment-update" || busy === "assignment-delete"} onClick={() => setDeleteConfirm(true)}>删除任务</button>
            </div>
          </section>
        </div>
      )}
      {editingAssignment && deleteConfirm && (
        <div className="edu-modal-backdrop edu-assignment-confirm-backdrop" onClick={() => setDeleteConfirm(false)}>
          <section className="edu-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="edu-assignment-confirm-title" onClick={event => event.stopPropagation()}>
            <h2 id="edu-assignment-confirm-title">确认删除任务？</h2>
            <p>“{editingAssignment.title}”将从师生列表隐藏，已有学习进度和诊断记录会保留。</p>
            <div className="edu-confirm-actions">
              <button className="edu-button ghost" disabled={busy === "assignment-delete"} onClick={() => setDeleteConfirm(false)}>取消</button>
              <button className="edu-button danger" disabled={busy === "assignment-delete"} onClick={() => void archivePublishedAssignment()}>{busy === "assignment-delete" ? <Loader2 className="edu-spin" size={14} /> : null}确认删除</button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
