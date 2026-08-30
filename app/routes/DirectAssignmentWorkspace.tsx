import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Brain,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  FileText,
  FileUp,
  Image as ImageIcon,
  Loader2,
  Pencil,
  Plus,
  Save,
  Send,
  Sigma,
  Trash2,
  X,
} from "lucide-react";
import { OcrRuntimeErrorPanel } from "~/components/OcrRuntimeErrorPanel";
import {
  cancelOcrInstall,
  cancelOcrJob,
  classifyOcrRuntime,
  getOcrResult,
  getOcrRuntime,
  installOcrRuntime,
  ocrRuntimeErrorSummary,
  pollOcrJob,
  startOcrJob,
  uploadOcrFile,
  type OcrRuntimeStatus,
  type OcrUploadInfo,
} from "~/ocr";
import { ProofWorkspace } from "./ProofWorkspace";
import { FormulaComposer } from "./FormulaComposer";
import { commitFormula, findFormulaAt, type FormulaPresentation } from "./formula-input";
import { MathText } from "./math";
import { buildDirectImageMarkdown, DirectQuestionContent, DirectQuestionEditor, insertDirectTextAtSelection, type DirectQuestionEditorHandle } from "./direct-question-content";
import type { DirectImportOrigin } from "~/components/DirectQuestionImport";
import {
  assessmentAnswersComplete,
  completeEducationAssessmentAttempt,
  createDirectEducationAssignment,
  educationErrorMessage,
  evaluateEducationSubmission,
  generateDirectQuestionStandard,
  loadEducationGradingOverview,
  loadEducationSubmission,
  publishEducationAssignment,
  publishEducationGrades,
  removeEducationAssignment,
  saveEducationAssessmentAttempt,
  saveDirectEducationAssignmentMetadata,
  saveEducationSubmissionGrade,
  startEducationAssessmentAttempt,
  submitEducationAssignment,
  updateDirectEducationQuestions,
  updatePublishedEducationAssignment,
} from "./education";
import type {
  AssessmentAttempt,
  EducationAssignment,
  EducationClass,
  EducationSubmission,
  GradingOverview,
  MatrixCheckReport,
  QuestionGrade,
} from "./education";
import type { GraphNode, LLMConfig } from "./home";
import {
  directQuestionDraftsForAssignment,
  equalDirectQuestionScores,
  normalizeDirectQuestionDraftScores,
  rebalanceDirectQuestionDrafts,
  type DirectQuestionDraft,
} from "../education-direct";
import "./direct-assignment.css";

export type DirectAssignmentWorkspaceMode = "create" | "review" | "readOnly" | "answer" | "result" | "grading";

export interface DirectAssignmentWorkspaceProps {
  mode: DirectAssignmentWorkspaceMode;
  token: string;
  theme: "light" | "dark";
  classInfo?: EducationClass | null;
  classId?: string | null;
  assignment?: EducationAssignment | null;
  llmConfig?: LLMConfig;
  onBack: () => void;
  onCreated?: (assignment: EducationAssignment) => void;
  onChanged?: (assignment: EducationAssignment) => void;
  onRemoved?: (assignmentId: string) => void;
  onDirtyChange?: (dirty: boolean) => void;
}

type DirectQuestionItem = {
  key: string;
  nodeId: number;
  questionIndex: number;
  order: number;
  status: "not_started" | "draft" | "completed";
};

type DirectAttemptMap = Record<string, AssessmentAttempt>;

function toLocalDateTime(value?: string | null) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function toIsoDateTime(value: string) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function directQuestionLabel(order: number) {
  return `第 ${order} 题`;
}

type DirectEditorField = "question" | "referenceAnswer";
type DirectEditorSelection = { start: number; end: number };
type DirectEditorTarget = DirectEditorSelection & { questionId: string; field: DirectEditorField };

const DIRECT_TEXT_IMPORT_EXTS = new Set([".tex", ".md", ".txt"]);
const DIRECT_IMAGE_IMPORT_EXTS = new Set([".png", ".jpg", ".jpeg", ".webp"]);

function directImportFileExt(filename: string) {
  const index = filename.lastIndexOf(".");
  return index >= 0 ? filename.slice(index).toLowerCase() : "";
}

function directEditorFieldLabel(field: DirectEditorField) {
  return field === "referenceAnswer" ? "参考答案" : "题目内容";
}

function emptyDirectQuestionDraft(order = 1): DirectQuestionDraft {
  return {
    id: `direct-question-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    order,
    question: "",
    focus: "",
    kind: `第 ${order} 题`,
    referenceAnswer: "",
    expectedPoints: [],
    maxScore: 100,
  };
}

function directReviewDrafts(assignment: EducationAssignment, editable: boolean) {
  const projected = directQuestionDraftsForAssignment(assignment);
  return projected.length || !editable ? projected : [emptyDirectQuestionDraft()];
}

function syntheticDirectNode(question: string, nodeId: number, order: number): GraphNode {
  return {
    id: nodeId,
    node_type: "exercise",
    title_zh: directQuestionLabel(order),
    title_en: `Question ${order}`,
    label: directQuestionLabel(order),
    content: question,
    statement_form: question,
    subject: [],
    conditions: [],
    conclusions: [],
    proof: null,
  };
}

function directScoringSummary(questions: DirectQuestionDraft[]) {
  const invalid = questions.filter(question => !question.question.trim() || !question.referenceAnswer.trim() || !Number.isFinite(question.maxScore) || question.maxScore <= 0);
  const total = Math.round(questions.reduce((sum, question) => sum + (Number(question.maxScore) || 0), 0) * 10) / 10;
  return { invalidCount: invalid.length, total, ready: questions.length > 0 && invalid.length === 0 && Math.abs(total - 100) < 0.05 };
}

function assignmentQuestionItems(assignment: EducationAssignment): DirectQuestionItem[] {
  const steps = assignment.path.steps.slice().sort((left, right) => left.order - right.order);
  let order = 0;
  return steps.flatMap(step => {
    const count = assignment.assessments.find(item => item.nodeId === step.nodeId)?.questionCount || 0;
    return Array.from({ length: count }, (_, questionIndex) => ({
      key: `${step.nodeId}:${questionIndex}`,
      nodeId: step.nodeId,
      questionIndex,
      order: ++order,
      status: (assignment.assessments.find(item => item.nodeId === step.nodeId)?.attemptStatus || "not_started") as DirectQuestionItem["status"],
    }));
  });
}

function submissionStatusLabel(status: string) {
  return ({
    not_submitted: "未提交",
    submitted: "待评价",
    review_draft: "批改中",
    finalized: "已定稿",
    released: "已发布",
  } as Record<string, string>)[status] || status;
}

function gradingStandardIncomplete(grade: Pick<QuestionGrade, "referenceAnswer" | "maxScore">) {
  return !grade.referenceAnswer.trim() || grade.maxScore <= 0;
}

function matrixStatusLabel(status?: MatrixCheckReport["status"]) {
  return ({
    verified: "计算过程通过",
    contradicted: "发现明确计算错误",
    indeterminate: "需要人工判断",
    structural_invalid: "表达结构无法可靠解析",
    not_applicable: "无矩阵检查",
  } as Record<string, string>)[status || "not_applicable"] || "无矩阵检查";
}

export function DirectAssignmentWorkspace(props: DirectAssignmentWorkspaceProps) {
  if (props.mode === "create") return <DirectAssignmentCreate {...props} />;
  if (props.mode === "grading") return <DirectAssignmentGrading {...props} />;
  if (props.mode === "answer" || props.mode === "result") return <DirectAssignmentStudent {...props} />;
  return <DirectAssignmentReview {...props} mode={props.mode} />;
}

function DirectAssignmentShell({ children, theme, title, subtitle, eyebrow = "题目作业", showTitle = true, showSubtitle = true, centerTitle = false, leftEyebrow = false, onBack, actions, className = "" }: {
  children: ReactNode;
  theme: "light" | "dark";
  title: string;
  subtitle?: string;
  eyebrow?: string;
  showTitle?: boolean;
  showSubtitle?: boolean;
  centerTitle?: boolean;
  leftEyebrow?: boolean;
  onBack: () => void;
  actions?: React.ReactNode;
  className?: string;
}) {
  return <div className={`direct-workspace edu-root ${className}`} data-theme={theme}>
    <header className="direct-workspace-header">
      {centerTitle && leftEyebrow ? <div className="direct-workspace-header-left"><button type="button" className="edu-button ghost" onClick={onBack}><ChevronLeft size={15} />返回班级作业</button><span className="edu-kicker">{eyebrow}</span></div> : <button type="button" className="edu-button ghost" onClick={onBack}><ChevronLeft size={15} />返回班级作业</button>}
      <div className={`direct-workspace-heading${centerTitle ? " direct-centered-heading" : ""}`}>{(!centerTitle || !leftEyebrow) && <span className="edu-kicker">{eyebrow}</span>}{centerTitle ? <div className="direct-centered-title-row">{showTitle && <h1>{title}</h1>}{showSubtitle && subtitle && <small>{subtitle}</small>}</div> : <>{showTitle && <h1>{title}</h1>}{showSubtitle && subtitle && <small>{subtitle}</small>}</>}</div>
      <div className="direct-workspace-actions">{actions}</div>
    </header>
    {children}
  </div>;
}

function DirectAssignmentCreate({ token, theme, classInfo, classId, onBack, onCreated, onDirtyChange }: DirectAssignmentWorkspaceProps) {
  const [title, setTitle] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { onDirtyChange?.(Boolean(title.trim() || dueAt)); }, [dueAt, onDirtyChange, title]);

  const save = async () => {
    if (!classId) return;
    if (!title.trim()) { setError("请输入作业名称。"); return; }
    setBusy(true); setError("");
    try {
      const assignment = await createDirectEducationAssignment(token, classId, {
        title: title.trim(),
        dueAt: toIsoDateTime(dueAt),
        questions: [],
        sourceText: "",
        sourceFile: null,
        sourceOrigin: "paste",
      });
      onDirtyChange?.(false);
      onCreated?.(assignment);
    } catch (cause) {
      setError(educationErrorMessage(cause));
    } finally { setBusy(false); }
  };

  const content = <div className="edu-root edu-modal-backdrop direct-create-backdrop" data-theme={theme} onClick={busy ? undefined : onBack}>
    <section className="edu-settings-modal direct-create-modal" role="dialog" aria-modal="true" aria-labelledby="direct-create-title" onClick={event => event.stopPropagation()}>
      <div className="edu-settings-head">
        <div><span className="edu-kicker">教师端</span><h2 id="direct-create-title">创建题目作业</h2></div>
        <button type="button" className="edu-icon-button" disabled={busy} onClick={onBack} aria-label="关闭"><X size={17} /></button>
      </div>
      <p className="edu-settings-copy">{classInfo?.title ? `为“${classInfo.title}”填写作业信息，保存后进入题目编辑页导入题目。` : "填写作业信息，保存后进入题目编辑页导入题目。"}</p>
      <div className="direct-create-fields">
        <label className="edu-settings-field"><span>作业名称</span><input autoFocus value={title} maxLength={160} onChange={event => setTitle(event.target.value)} placeholder="例如：第三章课后练习" disabled={busy} /></label>
        <label className="edu-settings-field"><span>截止时间</span><input type="datetime-local" value={dueAt} onChange={event => setDueAt(event.target.value)} disabled={busy} /></label>
      </div>
      {error && <div className="edu-modal-error">{error}</div>}
      <div className="direct-create-footer"><button type="button" className="edu-button ghost" onClick={onBack} disabled={busy}>取消</button><button type="button" className="edu-button primary" disabled={busy || !title.trim()} onClick={() => void save()}>{busy ? <><Loader2 className="edu-spin" size={14} />正在保存…</> : <><Save size={14} />保存并导入题目</>}</button></div>
    </section>
  </div>;

  return typeof document === "undefined" ? null : createPortal(content, document.body);
}

function DirectAssignmentReview(props: DirectAssignmentWorkspaceProps & { mode: "review" | "readOnly" }) {
  if (!props.assignment) return <DirectAssignmentShell theme={props.theme} title="题目作业" onBack={props.onBack}><div className="direct-state"><AlertTriangle size={20} />作业数据不可用。</div></DirectAssignmentShell>;
  return <DirectAssignmentReviewContent {...props} assignment={props.assignment} />;
}

function DirectAssignmentReviewContent({ token, theme, assignment, mode, onBack, onChanged, onRemoved, onDirtyChange }: DirectAssignmentWorkspaceProps & { mode: "review" | "readOnly"; assignment: EducationAssignment }) {
  const editable = mode === "review" && (assignment.status === "draft" || assignment.status === "published") && assignment.role === "teacher";
  const [drafts, setDrafts] = useState<DirectQuestionDraft[]>(() => directReviewDrafts(assignment, editable));
  const [activeId, setActiveId] = useState(() => directQuestionDraftsForAssignment(assignment)[0]?.id || "");
  const [title, setTitle] = useState(assignment.title);
  const [dueAt, setDueAt] = useState(toLocalDateTime(assignment.dueAt));
  const [metadataEditing, setMetadataEditing] = useState(false);
  const [pendingSourceText, setPendingSourceText] = useState("");
  const [pendingSourceFile, setPendingSourceFile] = useState<File | null>(null);
  const [pendingSourceOrigin, setPendingSourceOrigin] = useState<DirectImportOrigin>("paste");
  const [busy, setBusy] = useState<"save" | "publish" | "metadata" | "remove" | null>(null);
  const [generatingQuestionIds, setGeneratingQuestionIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [managementConfirm, setManagementConfirm] = useState<"archive" | "delete" | null>(null);
  const [metadataDirty, setMetadataDirty] = useState(false);
  const [questionsDirty, setQuestionsDirty] = useState(false);
  const [activeEditorField, setActiveEditorField] = useState<DirectEditorField>("question");
  const [importing, setImporting] = useState(false);
  const [ocrPhase, setOcrPhase] = useState("");
  const [ocrJobId, setOcrJobId] = useState<string | null>(null);
  const [ocrInstallId, setOcrInstallId] = useState<string | null>(null);
  const [ocrRuntime, setOcrRuntime] = useState<OcrRuntimeStatus | null>(null);
  const [ocrUploadPercent, setOcrUploadPercent] = useState<number | null>(null);
  const [ocrSourceFile, setOcrSourceFile] = useState<File | null>(null);
  const [ocrUpload, setOcrUpload] = useState<OcrUploadInfo | null>(null);
  const [ocrImportTarget, setOcrImportTarget] = useState<DirectEditorTarget | null>(null);
  const [formulaOpen, setFormulaOpen] = useState(false);
  const [formulaTarget, setFormulaTarget] = useState<(DirectEditorTarget & {
    initialValue: string;
    initialPresentation: FormulaPresentation;
    editing: boolean;
  }) | null>(null);
  const feedbackTimer = useRef<number | null>(null);
  const errorTimer = useRef<number | null>(null);
  const questionEditorRef = useRef<DirectQuestionEditorHandle | null>(null);
  const answerEditorRef = useRef<DirectQuestionEditorHandle | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const formulaAnchorRef = useRef<HTMLButtonElement | null>(null);
  const preparedImportTargetRef = useRef<DirectEditorTarget | null>(null);
  const editorSelectionRef = useRef<Record<DirectEditorField, DirectEditorSelection>>({
    question: { start: 0, end: 0 },
    referenceAnswer: { start: 0, end: 0 },
  });
  const activeQuestionIdRef = useRef("");
  const generationAssignmentIdRef = useRef(assignment.id);
  const draftsRef = useRef(drafts);
  const ocrAbortRef = useRef<AbortController | null>(null);

  const showFeedback = useCallback((message: string) => {
    if (feedbackTimer.current !== null) window.clearTimeout(feedbackTimer.current);
    setFeedback(message);
    feedbackTimer.current = window.setTimeout(() => {
      feedbackTimer.current = null;
      setFeedback("");
    }, 3000);
  }, []);

  useEffect(() => () => {
    if (feedbackTimer.current !== null) window.clearTimeout(feedbackTimer.current);
    if (errorTimer.current !== null) window.clearTimeout(errorTimer.current);
    ocrAbortRef.current?.abort();
  }, []);
  useEffect(() => {
    if (errorTimer.current !== null) window.clearTimeout(errorTimer.current);
    if (!error) return;
    errorTimer.current = window.setTimeout(() => {
      errorTimer.current = null;
      setError("");
    }, 3000);
    return () => {
      if (errorTimer.current !== null) window.clearTimeout(errorTimer.current);
    };
  }, [error]);

  useEffect(() => {
    const next = directReviewDrafts(assignment, editable);
    ocrAbortRef.current?.abort();
    ocrAbortRef.current = null;
    setDrafts(next);
    setActiveId(current => next.some(item => item.id === current) ? current : next[0]?.id || "");
    setTitle(assignment.title);
    setDueAt(toLocalDateTime(assignment.dueAt));
    setPendingSourceText("");
    setPendingSourceFile(null);
    setPendingSourceOrigin("paste");
    setMetadataDirty(false);
    setQuestionsDirty(false);
    setImporting(false);
    setOcrPhase("");
    setOcrJobId(null);
    setOcrInstallId(null);
    setOcrRuntime(null);
    setOcrUploadPercent(null);
    setOcrSourceFile(null);
    setOcrUpload(null);
    setOcrImportTarget(null);
    setFormulaOpen(false);
    setFormulaTarget(null);
    setManagementConfirm(null);
    setGeneratingQuestionIds(new Set());
  }, [assignment, editable]);

  useEffect(() => { onDirtyChange?.(metadataDirty || questionsDirty); }, [metadataDirty, onDirtyChange, questionsDirty]);
  const active = drafts.find(item => item.id === activeId) || drafts[0] || null;
  const scoring = useMemo(() => directScoringSummary(drafts), [drafts]);
  activeQuestionIdRef.current = active?.id || "";
  generationAssignmentIdRef.current = assignment.id;
  draftsRef.current = drafts;

  useEffect(() => {
    editorSelectionRef.current = {
      question: { start: active?.question.length || 0, end: active?.question.length || 0 },
      referenceAnswer: { start: active?.referenceAnswer.length || 0, end: active?.referenceAnswer.length || 0 },
    };
    setActiveEditorField("question");
    setFormulaOpen(false);
    setFormulaTarget(null);
  }, [active?.id]);

  const editorRefFor = (field: DirectEditorField) => field === "question" ? questionEditorRef : answerEditorRef;
  const rememberEditorSelection = (field: DirectEditorField, selection: DirectEditorSelection) => {
    setActiveEditorField(field);
    editorSelectionRef.current[field] = selection;
  };
  const captureEditorTarget = (): DirectEditorTarget | null => {
    if (!active) return null;
    const editor = editorRefFor(activeEditorField).current;
    const saved = editorSelectionRef.current[activeEditorField];
    const selection = editor ? editor.getSelectionRange() : saved;
    return {
      questionId: active.id,
      field: activeEditorField,
      start: selection.start,
      end: selection.end,
    };
  };
  const updateQuestion = (id: string, patch: Partial<DirectQuestionDraft>) => {
    if (!editable) return;
    setDrafts(current => current.map(item => item.id === id ? { ...item, ...patch } : item));
    setQuestionsDirty(true);
    setFeedback("");
  };
  const appendGeneratedText = (existing: string, generated: string, separator: string) => {
    const next = generated.trim();
    if (!next) return existing;
    return existing.trim() ? `${existing.trimEnd()}${separator}${next}` : next;
  };
  const generateStandard = async (questionId: string) => {
    if (!editable || busy !== null || importing || generatingQuestionIds.has(questionId)) return;
    const targetDraft = draftsRef.current.find(item => item.id === questionId);
    const question = targetDraft?.question.trim() || "";
    if (!question) {
      setError("请先填写题目内容，再生成答案和评分点。");
      return;
    }
    const requestAssignmentId = assignment.id;
    setGeneratingQuestionIds(current => new Set(current).add(questionId));
    setError("");
    setFeedback("");
    try {
      const generated = await generateDirectQuestionStandard(token, question);
      if (generationAssignmentIdRef.current !== requestAssignmentId) return;
      setDrafts(current => current.map(item => item.id === questionId ? {
        ...item,
        referenceAnswer: appendGeneratedText(item.referenceAnswer, generated.referenceAnswer, "\n\n"),
        focus: appendGeneratedText(item.focus, generated.focus, "\n"),
        expectedPoints: [...item.expectedPoints, ...generated.expectedPoints.map(point => point.trim()).filter(Boolean)],
      } : item));
      setQuestionsDirty(true);
      showFeedback("参考答案、检查重点和评分点已生成并追加到当前题目。");
    } catch (cause) {
      if (generationAssignmentIdRef.current === requestAssignmentId) setError(educationErrorMessage(cause));
    } finally {
      setGeneratingQuestionIds(current => {
        const next = new Set(current);
        next.delete(questionId);
        return next;
      });
    }
  };
  const insertTextAtTarget = (target: DirectEditorTarget, importedText: string, source?: { file: File; origin: DirectImportOrigin }) => {
    if (!importedText.trim()) throw new Error("没有识别到可插入的内容。");
    const targetDraft = draftsRef.current.find(item => item.id === target.questionId);
    if (!targetDraft) throw new Error("目标题目已不存在，请重新选择题目后导入。");
    const result = insertDirectTextAtSelection(targetDraft[target.field], target.start, target.end, importedText);
    setDrafts(current => current.map(item => item.id === target.questionId ? { ...item, [target.field]: result.text } : item));
    setQuestionsDirty(true);
    if (source) {
      setPendingSourceText(importedText);
      setPendingSourceFile(source.file);
      setPendingSourceOrigin(source.origin);
    }
    setError("");
    setFeedback("");
    if (activeQuestionIdRef.current === target.questionId) {
      editorSelectionRef.current[target.field] = { start: result.selectionStart, end: result.selectionEnd };
      setActiveEditorField(target.field);
      requestAnimationFrame(() => {
        const editor = editorRefFor(target.field).current;
        if (!editor || activeQuestionIdRef.current !== target.questionId) return;
        editor.focus();
        editor.setSelectionRange(result.selectionStart, result.selectionEnd);
      });
    }
  };
  const insertImportedContent = (target: DirectEditorTarget, importedText: string, file: File, origin: DirectImportOrigin) => {
    insertTextAtTarget(target, importedText, { file, origin });
  };
  const prepareImport = (kind: "file" | "image") => {
    const target = captureEditorTarget();
    if (!target) {
      setError("请先选择一道题目。");
      return;
    }
    preparedImportTargetRef.current = target;
    setError("");
    (kind === "file" ? fileInputRef.current : imageInputRef.current)?.click();
  };
  const runUploadedOcr = async (file: File, uploaded: OcrUploadInfo, target: DirectEditorTarget, controller: AbortController) => {
    let runtime = await getOcrRuntime(controller.signal);
    setOcrRuntime(runtime);
    const disposition = classifyOcrRuntime(runtime);
    if (disposition === "unavailable" || disposition === "fatal_error") {
      throw new Error(ocrRuntimeErrorSummary(runtime));
    }
    if (runtime.state !== "ready") {
      setOcrPhase("安装 OCR 组件");
      runtime = await installOcrRuntime(controller.signal, status => {
        setOcrInstallId(status.install_id || null);
        setOcrRuntime(status);
      });
      setOcrRuntime(runtime);
    }
    setOcrPhase("启动 OCR");
    const job = await startOcrJob(uploaded.upload_id, controller.signal);
    setOcrJobId(job.ocr_job_id);
    await pollOcrJob(job.ocr_job_id, status => setOcrPhase(status.phase), controller.signal);
    setOcrPhase("整理结果");
    const result = await getOcrResult(job.ocr_job_id, controller.signal);
    insertImportedContent(target, result.importedText || "", file, "ocr");
  };
  const clearCompletedOcr = () => {
    setOcrRuntime(null);
    setOcrSourceFile(null);
    setOcrUpload(null);
    setOcrImportTarget(null);
  };
  const finishImport = (controller: AbortController) => {
    setImporting(false);
    setOcrPhase("");
    setOcrJobId(null);
    setOcrInstallId(null);
    setOcrUploadPercent(null);
    if (ocrAbortRef.current === controller) ocrAbortRef.current = null;
  };
  const readImageAsDataUrl = (file: File) => new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => typeof reader.result === "string" ? resolve(reader.result) : reject(new Error("图片读取失败，请重试。"));
    reader.onerror = () => reject(new Error("图片读取失败，请重试。"));
    reader.readAsDataURL(file);
  });
  const handleEditorImport = async (event: ChangeEvent<HTMLInputElement>, kind: "file" | "image") => {
    const file = event.target.files?.[0];
    event.target.value = "";
    const target = preparedImportTargetRef.current || captureEditorTarget();
    preparedImportTargetRef.current = null;
    if (!file || !target) return;
    const ext = directImportFileExt(file.name);
    const textFile = DIRECT_TEXT_IMPORT_EXTS.has(ext);
    const pdfFile = ext === ".pdf";
    const imageFile = DIRECT_IMAGE_IMPORT_EXTS.has(ext);
    if ((kind === "file" && !textFile && !pdfFile && !imageFile) || (kind === "image" && !imageFile)) {
      setError(kind === "file" ? "仅支持 Markdown、TeX、TXT、PDF 或 PNG、JPG、JPEG、WEBP 文件。" : "仅支持 PNG、JPG、JPEG 或 WEBP 图片。");
      return;
    }
    if (pdfFile && file.size > 100 * 1024 * 1024) {
      setError("PDF 文件不能超过 100MB。");
      return;
    }
    if (imageFile && file.size > 20 * 1024 * 1024) {
      setError("图片文件不能超过 20MB。");
      return;
    }
    const controller = new AbortController();
    ocrAbortRef.current?.abort();
    ocrAbortRef.current = controller;
    setImporting(true);
    setOcrImportTarget(target);
    setOcrRuntime(null);
    setOcrUploadPercent(null);
    setOcrJobId(null);
    setOcrInstallId(null);
    setError("");
    setFeedback("");
    let completed = false;
    try {
      if (kind === "image") {
        setOcrPhase("读取图片");
        const dataUrl = await readImageAsDataUrl(file);
        if (controller.signal.aborted) throw new DOMException("导入已取消", "AbortError");
        insertTextAtTarget(target, buildDirectImageMarkdown(file.name, dataUrl));
      } else if (textFile) {
        setOcrPhase("读取文件");
        const importedText = await file.text();
        if (controller.signal.aborted) throw new DOMException("导入已取消", "AbortError");
        insertImportedContent(target, importedText, file, "document");
      } else {
        setOcrPhase("上传文件");
        const uploaded = await uploadOcrFile(file, setOcrUploadPercent, controller.signal);
        setOcrSourceFile(file);
        setOcrUpload(uploaded);
        await runUploadedOcr(file, uploaded, target, controller);
      }
      completed = true;
      showFeedback(`${file.name} 已插入${directEditorFieldLabel(target.field)}。`);
    } catch (cause) {
      if (!(cause instanceof DOMException && cause.name === "AbortError")) {
        setError(cause instanceof Error ? cause.message : "导入失败，请重试。");
      }
    } finally {
      if (completed) clearCompletedOcr();
      finishImport(controller);
    }
  };
  const retryOcrImport = () => {
    if (!ocrSourceFile || !ocrUpload || !ocrImportTarget || importing) return;
    const controller = new AbortController();
    ocrAbortRef.current?.abort();
    ocrAbortRef.current = controller;
    setImporting(true);
    setOcrRuntime(null);
    setOcrPhase("重新识别");
    setError("");
    void runUploadedOcr(ocrSourceFile, ocrUpload, ocrImportTarget, controller)
      .then(() => {
        showFeedback(`${ocrSourceFile.name} 已插入${directEditorFieldLabel(ocrImportTarget.field)}。`);
        clearCompletedOcr();
      })
      .catch(cause => {
        if (!(cause instanceof DOMException && cause.name === "AbortError")) {
          setError(cause instanceof Error ? cause.message : "识别失败，请重试。");
        }
      })
      .finally(() => finishImport(controller));
  };
  const cancelContentImport = () => {
    if (ocrJobId) void cancelOcrJob(ocrJobId).catch(() => undefined);
    if (ocrInstallId) void cancelOcrInstall(ocrInstallId).catch(() => undefined);
    ocrAbortRef.current?.abort();
    ocrAbortRef.current = null;
    setImporting(false);
    setOcrPhase("");
    setOcrUploadPercent(null);
    setError("OCR 已取消，可重新识别或选择其他文件。");
  };
  const closeFormulaComposer = () => {
    const target = formulaTarget;
    setFormulaOpen(false);
    setFormulaTarget(null);
    if (!target || activeQuestionIdRef.current !== target.questionId) return;
    requestAnimationFrame(() => {
      const editor = editorRefFor(target.field).current;
      if (!editor) return;
      editor.focus();
        editor.setSelectionRange(target.start, target.end);
    });
  };
  const toggleFormulaComposer = () => {
    if (formulaOpen) {
      closeFormulaComposer();
      return;
    }
    const target = captureEditorTarget();
    if (!target) {
      setError("请先选择一道题目。");
      return;
    }
    const targetDraft = draftsRef.current.find(item => item.id === target.questionId);
    if (!targetDraft) return;
    const existing = findFormulaAt(targetDraft[target.field], target.start, target.end);
    setFormulaTarget({
      ...target,
      initialValue: existing?.inner || "",
      initialPresentation: existing?.presentation || "inline",
      editing: Boolean(existing),
    });
    setFormulaOpen(true);
    setError("");
  };
  const commitFormulaFromComposer = (latex: string, presentation: FormulaPresentation) => {
    if (!formulaTarget) return;
    const targetDraft = draftsRef.current.find(item => item.id === formulaTarget.questionId);
    if (!targetDraft) {
      closeFormulaComposer();
      return;
    }
    const result = commitFormula(
      targetDraft[formulaTarget.field],
      formulaTarget.start,
      formulaTarget.end,
      latex,
      presentation,
    );
    setDrafts(current => current.map(item => item.id === formulaTarget.questionId ? { ...item, [formulaTarget.field]: result.text } : item));
    setQuestionsDirty(true);
    setFeedback("");
    setFormulaOpen(false);
    setFormulaTarget(null);
    if (activeQuestionIdRef.current === formulaTarget.questionId) {
      editorSelectionRef.current[formulaTarget.field] = { start: result.selectionStart, end: result.selectionEnd };
      setActiveEditorField(formulaTarget.field);
      requestAnimationFrame(() => {
        const editor = editorRefFor(formulaTarget.field).current;
        if (!editor) return;
        editor.focus();
        editor.setSelectionRange(result.selectionStart, result.selectionEnd);
      });
    }
  };
  const reorder = (index: number, direction: -1 | 1) => {
    if (!editable || importing) return;
    const target = index + direction;
    if (target < 0 || target >= drafts.length) return;
    const next = [...drafts];
    [next[index], next[target]] = [next[target], next[index]];
    setDrafts(rebalanceDirectQuestionDrafts(next));
    setQuestionsDirty(true);
  };
  const insertQuestion = (index: number) => {
    if (!editable || importing) return;
    const next = { ...emptyDirectQuestionDraft(index + 1), maxScore: equalDirectQuestionScores(drafts.length + 1)[index] || 0 };
    const nextList = rebalanceDirectQuestionDrafts([...drafts.slice(0, index), next, ...drafts.slice(index)]);
    setDrafts(nextList);
    setActiveId(next.id);
    setQuestionsDirty(true);
  };
  const removeQuestion = (id: string) => {
    if (!editable || importing || drafts.length <= 1) return;
    const next = rebalanceDirectQuestionDrafts(drafts.filter(item => item.id !== id));
    setDrafts(next);
    setActiveId(current => current === id ? next[Math.max(0, next.length - 1)]?.id || "" : current);
    setQuestionsDirty(true);
  };
  const saveAll = async (publish = false) => {
    if (!assignment || !editable || busy || importing || generatingQuestionIds.size > 0) return false;
    if (!title.trim()) { setError("请输入作业名称。"); return false; }
    if (!drafts.length) { setError("至少需要保留一道题目。"); return false; }
    if (publish && drafts.some(item => !item.question.trim())) { setError("发布前请补充题目内容。"); return false; }
    if (publish && !scoring.ready) { setError("发布前请补齐每道题的参考答案和有效分值，并确保总分为 100 分。"); return false; }
    const metadataWasDirty = metadataDirty;
    const questionsWereDirty = questionsDirty;
    let metadataSaved = false;
    let questionsSaved = false;
    setBusy(publish ? "publish" : "save"); setError(""); setFeedback("");
    try {
      let updated = assignment;
      if (metadataWasDirty) {
        updated = assignment.status === "published"
          ? await updatePublishedEducationAssignment(token, assignment.id, { title: title.trim(), dueAt: toIsoDateTime(dueAt) })
          : await saveDirectEducationAssignmentMetadata(token, assignment.id, { title: title.trim(), dueAt: toIsoDateTime(dueAt) });
        setMetadataDirty(false);
        metadataSaved = true;
      }
      if (questionsWereDirty) {
        updated = await updateDirectEducationQuestions(token, assignment.id, normalizeDirectQuestionDraftScores(drafts), pendingSourceText || pendingSourceFile ? {
          sourceText: pendingSourceText,
          sourceFile: pendingSourceFile,
          sourceOrigin: pendingSourceOrigin,
        } : undefined);
        const nextDrafts = directQuestionDraftsForAssignment(updated);
        setDrafts(nextDrafts);
        setActiveId(current => nextDrafts.some(item => item.id === current) ? current : nextDrafts[0]?.id || "");
        setQuestionsDirty(false);
        setPendingSourceText("");
        setPendingSourceFile(null);
        setPendingSourceOrigin("paste");
        questionsSaved = true;
      }
      if (publish) updated = await publishEducationAssignment(token, assignment.id);
      onChanged?.(updated);
      showFeedback(publish ? "题目作业已发布。" : "题目作业草稿已保存。");
      return true;
    } catch (cause) {
      const savedParts = [metadataSaved ? "作业信息" : "", questionsSaved ? "题目内容" : ""].filter(Boolean);
      const savedPrefix = savedParts.length ? `${savedParts.join("和")}已保存，但` : "";
      setError(`${savedPrefix}${educationErrorMessage(cause)}`);
      return false;
    } finally { setBusy(null); }
  };
  const savePublishedMetadata = async () => {
    if (!metadataEditing || !title.trim() || busy || generatingQuestionIds.size > 0) return;
    setBusy("metadata"); setError(""); setFeedback("");
    try {
      const updated = await updatePublishedEducationAssignment(token, assignment.id, { title: title.trim(), dueAt: toIsoDateTime(dueAt) });
      onChanged?.(updated);
      setMetadataEditing(false);
      setMetadataDirty(false);
      showFeedback("作业信息已更新。");
    } catch (cause) { setError(educationErrorMessage(cause)); }
    finally { setBusy(null); }
  };
  const removeAssignment = async () => {
    const expectedAction = assignment.status === "draft" ? "delete" : "archive";
    if (managementConfirm !== expectedAction || busy || importing || generatingQuestionIds.size > 0) return;
    setBusy("remove"); setError(""); setFeedback("");
    try {
      await removeEducationAssignment(token, assignment.id);
      onDirtyChange?.(false);
      if (onRemoved) onRemoved(assignment.id);
      else onBack();
    } catch (cause) { setError(educationErrorMessage(cause)); }
    finally { setBusy(null); }
  };
  return <DirectAssignmentShell
    theme={theme}
    title={title || assignment.title}
    eyebrow="作业发布"
    showTitle={false}
    showSubtitle={false}
    onBack={onBack}
    className="direct-review-workspace"
    actions={<>{editable && <button type="button" className="edu-button ghost" disabled={busy !== null || importing || generatingQuestionIds.size > 0 || (!metadataDirty && !questionsDirty)} onClick={() => void saveAll(false)}>{busy === "save" ? <Loader2 className="edu-spin" size={14} /> : <Save size={14} />}保存草稿</button>}{editable && <button type="button" className="edu-button primary" disabled={busy !== null || importing || generatingQuestionIds.size > 0 || !scoring.ready} onClick={() => void saveAll(true)}>{busy === "publish" ? <Loader2 className="edu-spin" size={14} /> : <Send size={14} />}{assignment.status === "published" ? "重新发布" : "确认发布"}</button>}</>}
  >
    <div className="direct-review-layout">
      <aside className="direct-question-sidebar">
        <div className="direct-sidebar-heading"><div><strong>题目列表</strong><small>{drafts.length} 道题</small></div>{editable && <button type="button" className="edu-icon-button" disabled={importing} onClick={() => insertQuestion(drafts.length)} aria-label="新增题目"><Plus size={15} /></button>}</div>
        <div className={`direct-score-summary ${scoring.ready ? "ready" : "incomplete"}`}><span>当前总分</span><strong>{scoring.total.toFixed(1)} / 100</strong><small>{scoring.invalidCount ? `还有 ${scoring.invalidCount} 道题需要补齐评分标准` : scoring.ready ? "可以发布" : "请校准题目分值"}</small></div>
        {drafts.map((question, index) => <article key={question.id} className={`direct-question-nav-item${question.id === active?.id ? " active" : ""}`}>
          <button type="button" onClick={() => setActiveId(question.id)}><span>{index + 1}</span><div><strong>{directQuestionLabel(index + 1)}</strong><small>{question.referenceAnswer.trim() && question.maxScore > 0 ? "评分标准已完成" : "待补充评分标准"}</small></div></button>
          {editable && <div className="direct-question-nav-actions"><button type="button" disabled={importing || index === 0} onClick={() => reorder(index, -1)} aria-label="上移"><ArrowUp size={13} /></button><button type="button" disabled={importing || index === drafts.length - 1} onClick={() => reorder(index, 1)} aria-label="下移"><ArrowDown size={13} /></button><button type="button" disabled={importing || drafts.length <= 1} onClick={() => removeQuestion(question.id)} aria-label="删除"><Trash2 size={13} /></button></div>}
        </article>)}
        {editable && <button type="button" className="direct-insert-button" disabled={importing} onClick={() => insertQuestion(active ? drafts.findIndex(item => item.id === active.id) + 1 : drafts.length)}><Plus size={13} />在当前题后插入</button>}
        {assignment.role === "teacher" && (assignment.status === "draft" || assignment.status === "published") && <section className="direct-management">
          <strong>作业管理</strong>
          {managementConfirm ? <div>
            <small>{managementConfirm === "delete" ? "删除后草稿、题目、评分标准和导入源文件将永久移除，无法恢复。" : "归档后作业将从师生列表隐藏，已有成绩保留。"}</small>
            <div>
              <button type="button" className="edu-button ghost" disabled={busy === "remove"} onClick={() => setManagementConfirm(null)}>取消</button>
              <button type="button" className="edu-button danger" disabled={busy !== null || importing || generatingQuestionIds.size > 0} onClick={() => void removeAssignment()}>{busy === "remove" ? managementConfirm === "delete" ? "删除中…" : "归档中…" : managementConfirm === "delete" ? "确认删除" : "确认归档"}</button>
            </div>
          </div> : assignment.status === "draft"
            ? <button type="button" className="edu-button danger" disabled={busy !== null || importing || generatingQuestionIds.size > 0} onClick={() => setManagementConfirm("delete")}><Trash2 size={14} />删除作业</button>
            : <button type="button" className="edu-button ghost" disabled={busy !== null || importing || generatingQuestionIds.size > 0} onClick={() => setManagementConfirm("archive")}>归档作业</button>}
        </section>}
      </aside>
      <main className="direct-review-main">
        {(metadataEditing || editable) && <section className="direct-metadata-card"><label className="edu-settings-field"><span>作业名称</span><input value={title} maxLength={160} disabled={!editable && !metadataEditing} onChange={event => { setTitle(event.target.value); setMetadataDirty(true); }} /></label><label className="edu-settings-field"><span>截止时间</span><input type="datetime-local" value={dueAt} disabled={!editable && !metadataEditing} onChange={event => { setDueAt(event.target.value); setMetadataDirty(true); }} /></label>{metadataEditing && <button type="button" className="edu-button primary" disabled={busy !== null || generatingQuestionIds.size > 0 || !title.trim()} onClick={() => void savePublishedMetadata()}>{busy === "metadata" ? <Loader2 className="edu-spin" size={14} /> : <Check size={14} />}保存作业信息</button>}</section>}
        <div className="direct-active-question-heading">
          <div><span className="edu-kicker">{active ? `题目 ${active.order}` : "题目编辑"}</span><h2>{active ? directQuestionLabel(active.order) : "暂无题目"}</h2></div>
          {editable && active && <div className="direct-editor-toolbar" aria-label={`插入到${directEditorFieldLabel(activeEditorField)}`}>
            <button type="button" className="direct-editor-tool-button" title="支持文档、PDF 或图片；PDF 和图片会自动 OCR" disabled={importing || busy !== null} onMouseDown={event => event.preventDefault()} onClick={() => prepareImport("file")}><FileUp size={14} />导入文件</button>
            <button type="button" className="direct-editor-tool-button" title="图片将直接插入题目，不进行 OCR" disabled={importing || busy !== null} onMouseDown={event => event.preventDefault()} onClick={() => prepareImport("image")}><ImageIcon size={14} />导入图片</button>
            <button ref={formulaAnchorRef} type="button" className={`direct-editor-tool-button${formulaOpen ? " active" : ""}`} disabled={importing || busy !== null} aria-pressed={formulaOpen} onMouseDown={event => event.preventDefault()} onClick={toggleFormulaComposer}><Sigma size={14} />公式输入</button>
            <input ref={fileInputRef} className="direct-editor-file-input" type="file" accept=".md,.tex,.txt,.pdf,.png,.jpg,.jpeg,.webp,text/markdown,text/plain,text/x-tex,application/pdf,image/png,image/jpeg,image/webp" onChange={event => void handleEditorImport(event, "file")} />
            <input ref={imageInputRef} className="direct-editor-file-input" type="file" accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp" onChange={event => void handleEditorImport(event, "image")} />
          </div>}
        </div>
        {editable && (importing || (ocrSourceFile && ocrUpload)) && <section className="direct-editor-import-status" role="status" aria-live="polite">
          <div><strong>{importing ? `${directEditorFieldLabel(ocrImportTarget?.field || activeEditorField)} · ${ocrPhase || "处理中"}` : "识别未完成"}</strong><small>{ocrUploadPercent !== null ? `上传 ${ocrUploadPercent}%` : ocrSourceFile?.name || "正在读取导入内容"}</small></div>
          {importing ? <button type="button" onClick={cancelContentImport}>取消</button> : <button type="button" onClick={retryOcrImport}>重新识别</button>}
          {ocrRuntime && <OcrRuntimeErrorPanel status={ocrRuntime} compact />}
        </section>}
        {active ? <div className="direct-content-panels">
          <section className={`direct-content-panel${editable && activeEditorField === "question" ? " active-input" : ""}`}>
            <div className="direct-content-panel-heading"><strong>题目内容</strong><small>{editable ? "可直接输入或粘贴文字，图片将直接显示" : "支持 Markdown、LaTeX、纯文本和图片"}</small></div>
            {editable ? <DirectQuestionEditor ref={questionEditorRef} value={active.question} onSelectionChange={selection => rememberEditorSelection("question", selection)} onChange={(value, selection) => { updateQuestion(active.id, { question: value }); rememberEditorSelection("question", selection); }} ariaLabel="题目内容" placeholder="直接输入或粘贴题目内容，支持 Markdown 与 LaTeX" /> : <div className="direct-content-readonly"><DirectQuestionContent text={active.question} /></div>}
          </section>
           <section className={`direct-content-panel${editable && activeEditorField === "referenceAnswer" ? " active-input" : ""}`}>
             <div className="direct-content-panel-heading"><strong>参考答案</strong>{editable ? <button type="button" className="direct-generate-button" disabled={busy !== null || importing || !active.question.trim() || generatingQuestionIds.has(active.id)} onMouseDown={event => event.preventDefault()} onClick={() => void generateStandard(active.id)}>{generatingQuestionIds.has(active.id) ? <Loader2 className="edu-spin" size={13} /> : <Brain size={13} />}{generatingQuestionIds.has(active.id) ? "生成中" : "生成答案和评分点"}</button> : <small>供教师审核和评分使用</small>}</div>
            {editable ? <DirectQuestionEditor ref={answerEditorRef} value={active.referenceAnswer} onSelectionChange={selection => rememberEditorSelection("referenceAnswer", selection)} onChange={(value, selection) => { updateQuestion(active.id, { referenceAnswer: value }); rememberEditorSelection("referenceAnswer", selection); }} ariaLabel="参考答案" placeholder="直接输入或粘贴参考答案，支持 Markdown 与 LaTeX" /> : <div className="direct-content-readonly"><DirectQuestionContent text={active.referenceAnswer || "暂无参考答案。"} /></div>}
          </section>
        </div> : <div className="direct-state"><FileText size={20} />暂无题目。</div>}
      </main>
      <aside className="direct-scoring-sidebar">
        {active ? <>
          <div className="direct-sidebar-heading"><div><strong>评分标准</strong><small>{directQuestionLabel(active.order)}</small></div></div>
          <label className="direct-editor-field"><span>题目名称</span><input value={active.kind || directQuestionLabel(active.order)} disabled={!editable} onChange={event => updateQuestion(active.id, { kind: event.target.value })} /></label>
          <label className="direct-editor-field"><span>检查重点</span><textarea rows={3} value={active.focus} disabled={!editable} onChange={event => updateQuestion(active.id, { focus: event.target.value })} /></label>
          <label className="direct-editor-field"><span>评分点</span><textarea rows={10} value={active.expectedPoints.join("\n")} disabled={!editable} onChange={event => updateQuestion(active.id, { expectedPoints: event.target.value.split(/\r?\n/).map(item => item.trim()).filter(Boolean) })} placeholder="用于AI辅助评分" /></label>
          <label className="direct-editor-field"><span>本题满分</span><input type="number" min="0.1" max="100" step="0.1" value={active.maxScore || ""} disabled={!editable} onChange={event => updateQuestion(active.id, { maxScore: Number(event.target.value) })} /></label>
          <p className="direct-score-hint">保存题目时系统会重新校准总分为 100 分。</p>
        </> : <div className="direct-state compact">请选择题目。</div>}
      </aside>
    </div>
    {formulaOpen && formulaTarget && <FormulaComposer
      key={`${formulaTarget.questionId}:${formulaTarget.field}:${formulaTarget.start}:${formulaTarget.end}:${formulaTarget.initialValue}`}
      anchorElement={formulaAnchorRef.current}
      initialValue={formulaTarget.initialValue}
      initialPresentation={formulaTarget.initialPresentation}
      editing={formulaTarget.editing}
      active
      onActivate={() => undefined}
      onCommit={commitFormulaFromComposer}
      onCancel={closeFormulaComposer}
    />}
    {error && <div className="direct-workspace-feedback error">{error}</div>}
    {feedback && <div className="direct-workspace-feedback success">{feedback}</div>}
  </DirectAssignmentShell>;
}

function DirectAssignmentStudent(props: DirectAssignmentWorkspaceProps) {
  if (!props.assignment) return <DirectAssignmentShell theme={props.theme} title="题目作业" onBack={props.onBack}><div className="direct-state"><AlertTriangle size={20} />作业数据不可用。</div></DirectAssignmentShell>;
  return <DirectAssignmentStudentContent {...props} assignment={props.assignment} />;
}

function DirectAssignmentStudentContent({ token, theme, assignment, llmConfig, onBack }: DirectAssignmentWorkspaceProps & { assignment: EducationAssignment }) {
  const items = useMemo(() => assignmentQuestionItems(assignment), [assignment]);
  const [attempts, setAttempts] = useState<DirectAttemptMap>({});
  const [activeKey, setActiveKey] = useState(items[0]?.key || "");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [completing, setCompleting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [submission, setSubmission] = useState(assignment.submission || null);
  const [releasedSubmission, setReleasedSubmission] = useState<EducationSubmission | null>(null);
  const dirtyNodesRef = useRef(new Set<number>());
  const loadingNodesRef = useRef(new Set<number>());

  useEffect(() => {
    setActiveKey(current => items.some(item => item.key === current) ? current : items[0]?.key || "");
  }, [items]);

  useEffect(() => {
    if (!submission?.id) return;
    let cancelled = false;
    if (submission.status !== "released") return;
    void loadEducationSubmission(token, submission.id).then(next => {
      if (!cancelled) setReleasedSubmission(next);
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [submission?.id, submission?.status, token]);

  const activeItem = items.find(item => item.key === activeKey) || items[0] || null;
  const activeAttempt = activeItem ? attempts[String(activeItem.nodeId)] : undefined;
  const activeQuestion = activeAttempt?.questions[activeItem?.questionIndex || 0];
  const isSubmitted = Boolean(submission);
  const completion = items.length > 0 && items.every(item => attempts[String(item.nodeId)]?.status === "completed" || assignment.assessments.find(assessment => assessment.nodeId === item.nodeId)?.attemptStatus === "completed");

  const ensureAttempt = useCallback(async (nodeId: number) => {
    if (isSubmitted || attempts[String(nodeId)] || loadingNodesRef.current.has(nodeId)) return;
    loadingNodesRef.current.add(nodeId);
    setLoading(true); setError("");
    try {
      const next = await startEducationAssessmentAttempt(token, assignment.id, nodeId);
      setAttempts(current => ({ ...current, [String(nodeId)]: next }));
    } catch (cause) {
      setError(educationErrorMessage(cause));
    } finally {
      loadingNodesRef.current.delete(nodeId);
      setLoading(false);
    }
  }, [assignment.id, attempts, isSubmitted, token]);

  useEffect(() => {
    if (activeItem) void ensureAttempt(activeItem.nodeId);
  }, [activeItem, ensureAttempt]);

  const persistAnswers = useCallback(async (nodeId: number, answers: Record<string, string>) => {
    const attempt = attempts[String(nodeId)];
    if (!attempt || isSubmitted) return attempt;
    setSaving(true); setError("");
    try {
      const saved = await saveEducationAssessmentAttempt(token, attempt.id, answers);
      setAttempts(current => ({ ...current, [String(nodeId)]: saved }));
      dirtyNodesRef.current.delete(nodeId);
      return saved;
    } catch (cause) {
      setError(educationErrorMessage(cause));
      throw cause;
    } finally { setSaving(false); }
  }, [attempts, isSubmitted, token]);

  useEffect(() => {
    if (!activeItem || !activeAttempt || !dirtyNodesRef.current.has(activeItem.nodeId) || isSubmitted) return;
    const timer = window.setTimeout(() => { void persistAnswers(activeItem.nodeId, activeAttempt.answers).catch(() => undefined); }, 800);
    return () => window.clearTimeout(timer);
  }, [activeAttempt?.answers, activeItem, isSubmitted, persistAnswers]);

  const updateAnswer = (value: string) => {
    if (!activeItem || !activeAttempt || isSubmitted) return;
    dirtyNodesRef.current.add(activeItem.nodeId);
    const next = { ...activeAttempt, answers: { ...activeAttempt.answers, [activeQuestion?.id || ""]: value } };
    setAttempts(current => ({ ...current, [String(activeItem.nodeId)]: next }));
  };

  const moveTo = async (index: number) => {
    if (!activeItem || index < 0 || index >= items.length || saving || completing) return;
    try {
      if (dirtyNodesRef.current.has(activeItem.nodeId) && activeAttempt) await persistAnswers(activeItem.nodeId, activeAttempt.answers);
      setActiveKey(items[index].key);
    } catch { /* keep the current question visible */ }
  };

  const completeCurrent = async () => {
    if (!activeItem || !activeAttempt || completing || isSubmitted || !assessmentAnswersComplete(activeAttempt)) return;
    setCompleting(true); setError("");
    try {
      if (dirtyNodesRef.current.has(activeItem.nodeId)) await persistAnswers(activeItem.nodeId, activeAttempt.answers);
      const result = await completeEducationAssessmentAttempt(token, activeAttempt.id, activeAttempt.answers);
      setAttempts(current => ({ ...current, [String(activeItem.nodeId)]: result.attempt }));
      const nextIndex = items.findIndex(item => item.key === activeItem.key) + 1;
      if (nextIndex < items.length) setActiveKey(items[nextIndex].key);
    } catch (cause) { setError(educationErrorMessage(cause)); }
    finally { setCompleting(false); }
  };

  const submit = async () => {
    if (!completion || submitting || isSubmitted) return;
    setSubmitting(true); setError("");
    try {
      const next = await submitEducationAssignment(token, assignment.id);
      setSubmission(next);
    } catch (cause) { setError(educationErrorMessage(cause)); }
    finally { setSubmitting(false); }
  };

  const releasedGrades = releasedSubmission?.grades || [];
  if (isSubmitted) {
    return <DirectAssignmentShell theme={theme} title={assignment.title} subtitle={assignment.dueAt ? `截止 ${new Date(assignment.dueAt).toLocaleString("zh-CN")}` : undefined} centerTitle leftEyebrow onBack={onBack} className="direct-student-workspace direct-student-result-workspace" actions={<span className={`direct-status-pill ${submission?.status || "submitted"}`}>{submissionStatusLabel(submission?.status || "submitted")}</span>}>
      <main className="direct-student-result">
        {releasedSubmission ? <>
          <section className="direct-result-summary"><div><span>最终得分</span><strong>{releasedSubmission.teacherTotal?.toFixed(1) ?? "—"}</strong><small>/ 100</small></div><p>{releasedSubmission.teacherSummary || "教师未填写整体评语。"}</p></section>
          <div className="direct-result-list">{releasedGrades.map((grade, index) => <article key={grade.questionId} className="direct-result-question"><header><span>{index + 1}</span><div><strong>{directQuestionLabel(index + 1)}</strong><small>{grade.teacherScore?.toFixed(1) ?? "—"} / {grade.maxScore.toFixed(1)}</small></div></header><section><h3>题目</h3><DirectQuestionContent text={grade.question} /></section><section><h3>我的答案</h3><MathText text={grade.studentAnswer} /></section><div className="direct-result-columns"><section><h3>教师评语</h3><p>{grade.teacherFeedback || "教师未填写逐题评语。"}</p></section><section><h3>参考答案与评分点</h3><DirectQuestionContent text={grade.referenceAnswer} />{grade.expectedPoints.length > 0 && <ul>{grade.expectedPoints.map((point, pointIndex) => <li key={pointIndex}><MathText text={point} /></li>)}</ul>}</section></div></article>)}</div>
        </> : <div className="direct-state"><CheckCircle2 size={22} />作业已提交，等待教师批改和发布成绩。</div>}
      </main>
    </DirectAssignmentShell>;
  }

  return <DirectAssignmentShell theme={theme} title={assignment.title} subtitle={assignment.dueAt ? `截止 ${new Date(assignment.dueAt).toLocaleString("zh-CN")}` : undefined} centerTitle leftEyebrow onBack={onBack} className="direct-student-workspace" actions={<><span className="direct-completion-pill">{items.filter(item => attempts[String(item.nodeId)]?.status === "completed" || assignment.assessments.find(assessment => assessment.nodeId === item.nodeId)?.attemptStatus === "completed").length} / {items.length} 已完成</span><button type="button" className="edu-button primary" disabled={!completion || submitting} onClick={() => void submit()}>{submitting ? <Loader2 className="edu-spin" size={14} /> : <Send size={14} />}提交作业</button></>}>
    <div className="direct-student-layout">
      <aside className="direct-question-sidebar direct-student-sidebar"><div className="direct-sidebar-heading"><div><strong>题目列表</strong><small>{items.length} 道题</small></div></div>{items.map(item => { const status = attempts[String(item.nodeId)]?.status || assignment.assessments.find(assessment => assessment.nodeId === item.nodeId)?.attemptStatus || "not_started"; return <button type="button" key={item.key} className={`direct-student-question${item.key === activeItem?.key ? " active" : ""}`} onClick={() => void moveTo(items.indexOf(item))}><span>{item.order}</span><div><strong>{directQuestionLabel(item.order)}</strong><small>{status === "completed" ? "已完成" : status === "draft" ? "作答中" : "未开始"}</small></div>{status === "completed" && <CheckCircle2 size={15} />}</button>; })}</aside>
      <main className="direct-student-main">
        {loading && !activeAttempt ? <div className="direct-state"><Loader2 className="edu-spin" size={20} />正在加载题目…</div> : activeQuestion && activeAttempt && activeItem ? <>
          <section className="direct-student-prompt"><span className="edu-kicker">{directQuestionLabel(activeItem.order)}</span><h2>{directQuestionLabel(activeItem.order)}</h2><DirectQuestionContent text={activeQuestion.question} />{activeQuestion.focus && <small>检查重点：{activeQuestion.focus}</small>}</section>
          <ProofWorkspace key={activeQuestion.id} graphId={`direct-assignment:${assignment.id}:${activeAttempt.id}`} node={syntheticDirectNode(activeQuestion.question, activeItem.nodeId, activeItem.order)} token={token} llmConfig={llmConfig} answerMode={{ key: activeQuestion.id, value: activeAttempt.answers[activeQuestion.id] || "", title: "我的作答", subtitle: "可直接输入，也可上传 PDF 或图片手稿进行 OCR", placeholder: "写下你的回答或证明思路，可以使用 Markdown 与 LaTeX 记号。", onChange: updateAnswer, onSave: async value => { if (activeItem) await persistAnswers(activeItem.nodeId, { ...activeAttempt.answers, [activeQuestion.id]: value }); } }} />
          <div className="direct-student-navigation"><button type="button" className="edu-button ghost" disabled={items.indexOf(activeItem) === 0 || saving || completing} onClick={() => void moveTo(items.indexOf(activeItem) - 1)}><ChevronLeft size={14} />上一题</button><button type="button" className="edu-button primary" disabled={saving || completing || !assessmentAnswersComplete(activeAttempt)} onClick={() => void completeCurrent()}>{completing ? <Loader2 className="edu-spin" size={14} /> : <CheckCircle2 size={14} />}{items.indexOf(activeItem) === items.length - 1 ? "完成本题" : "完成并继续"}<ChevronRight size={14} /></button></div>
        </> : <div className="direct-state"><AlertTriangle size={20} />当前题目暂不可用。</div>}
        {error && <div className="direct-workspace-feedback error">{error}</div>}
      </main>
    </div>
  </DirectAssignmentShell>;
}

function DirectAssignmentGrading(props: DirectAssignmentWorkspaceProps) {
  if (!props.assignment) return <DirectAssignmentShell theme={props.theme} title="题目作业批改" onBack={props.onBack}><div className="direct-state"><AlertTriangle size={20} />作业数据不可用。</div></DirectAssignmentShell>;
  return <DirectAssignmentGradingContent {...props} assignment={props.assignment} />;
}

function DirectAssignmentGradingContent({ token, theme, assignment, onBack, onDirtyChange }: DirectAssignmentWorkspaceProps & { assignment: EducationAssignment }) {
  const [overview, setOverview] = useState<GradingOverview | null>(null);
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
  const [submission, setSubmission] = useState<EducationSubmission | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [busy, setBusy] = useState<"load" | "evaluate" | "save" | "publish" | null>(null);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const feedbackTimer = useRef<number | null>(null);
  const showFeedback = useCallback((message: string) => {
    if (feedbackTimer.current !== null) window.clearTimeout(feedbackTimer.current);
    setFeedback(message);
    feedbackTimer.current = window.setTimeout(() => {
      feedbackTimer.current = null;
      setFeedback("");
    }, 3000);
  }, []);
  useEffect(() => () => {
    if (feedbackTimer.current !== null) window.clearTimeout(feedbackTimer.current);
  }, []);
  const [gradingDirty, setGradingDirty] = useState(false);
  const [studentPickerOpen, setStudentPickerOpen] = useState(false);

  useEffect(() => { onDirtyChange?.(gradingDirty); }, [gradingDirty, onDirtyChange]);
  const loadOverview = useCallback(async () => {
    setBusy("load"); setError("");
    try {
      const next = await loadEducationGradingOverview(token, assignment.id);
      setOverview(next);
      const firstWithSubmission = next.students.find(student => student.submissionId);
      setSelectedUserId(current => current || firstWithSubmission?.userId || next.students[0]?.userId || null);
    } catch (cause) { setError(educationErrorMessage(cause)); }
    finally { setBusy(null); }
  }, [assignment.id, token]);

  useEffect(() => { void loadOverview(); }, [loadOverview]);

  const selectedStudent = overview?.students.find(student => student.userId === selectedUserId) || null;
  useEffect(() => {
    if (!selectedStudent?.submissionId) { setSubmission(null); return; }
    let cancelled = false;
    setBusy("load"); setError("");
    void loadEducationSubmission(token, selectedStudent.submissionId).then(next => {
      if (!cancelled) { setSubmission(next); setActiveIndex(0); }
    }).catch(cause => { if (!cancelled) setError(educationErrorMessage(cause)); }).finally(() => { if (!cancelled) setBusy(null); });
    return () => { cancelled = true; };
  }, [selectedStudent?.submissionId, token]);

  const grades = submission?.grades || [];
  const active = grades[activeIndex] || null;
  const scoringIncomplete = grades.some(gradingStandardIncomplete);
  const teacherTotal = grades.reduce((sum, grade) => sum + (grade.teacherScore ?? 0), 0);
  const readOnly = submission?.status === "finalized" || submission?.status === "released";
  const updateGrade = (questionId: string, patch: Partial<QuestionGrade>) => {
    if (readOnly) return;
    setSubmission(current => current ? { ...current, grades: (current.grades || []).map(grade => grade.questionId === questionId ? { ...grade, ...patch } : grade) } : current);
    setGradingDirty(true);
  };
  const payload = () => ({ grades: grades.map(grade => ({ questionId: grade.questionId, teacherScore: grade.teacherScore ?? null, teacherFeedback: grade.teacherFeedback || "" })), teacherSummary: submission?.teacherSummary || "" });
  const save = async () => {
    if (!submission || readOnly || busy) return false;
    setBusy("save"); setError(""); setFeedback("");
    try {
      const next = await saveEducationSubmissionGrade(token, submission.id, payload());
      setSubmission(next);
      setOverview(current => {
        if (!current) return current;
        const students = current.students.map(student => student.submissionId === next.id ? {
          ...student,
          submissionStatus: next.status,
          teacherTotal: next.teacherTotal,
          updatedAt: next.updatedAt,
        } : student);
        const submittedStudents = students.filter(student => student.submissionId);
        const pendingUserIds = submittedStudents.filter(student => student.submissionStatus !== "released" && student.teacherTotal == null).map(student => student.userId);
        return {
          ...current,
          students,
          pendingUserIds,
          canPublish: submittedStudents.length > 0 && pendingUserIds.length === 0 && !current.gradesPublishedAt,
        };
      });
      setGradingDirty(false);
      showFeedback("批改草稿已保存。"); return true;
    } catch (cause) { setError(educationErrorMessage(cause)); return false; }
    finally { setBusy(null); }
  };
  const evaluate = async () => {
    if (!submission || readOnly || scoringIncomplete || busy) return;
    setBusy("evaluate"); setError(""); setFeedback("");
    try {
      const saved = await saveEducationSubmissionGrade(token, submission.id, payload());
      setSubmission(await evaluateEducationSubmission(token, saved.id));
      setGradingDirty(false);
      showFeedback("AI 评价已生成，请逐题复核并保存草稿。");
    } catch (cause) { setError(educationErrorMessage(cause)); }
    finally { setBusy(null); }
  };
  const publish = async () => {
    if (!overview?.canPublish || busy) return;
    if (gradingDirty && !(await save())) return;
    setBusy("publish"); setError(""); setFeedback("");
    try { await publishEducationGrades(token, assignment.id); await loadOverview(); showFeedback("成绩已发布。"); }
    catch (cause) { setError(educationErrorMessage(cause)); }
    finally { setBusy(null); }
  };
  const selectStudent = async (userId: number) => {
    if (userId === selectedUserId || busy) return;
    if (gradingDirty && !(await save())) return;
    setSelectedUserId(userId);
    setStudentPickerOpen(false);
  };
  const selectQuestion = async (index: number) => {
    if (index === activeIndex || busy) return;
    if (gradingDirty && !(await save())) return;
    setActiveIndex(index);
  };
  const updateTeacherSummary = (value: string) => {
    setSubmission(current => current ? { ...current, teacherSummary: value } : current);
    setGradingDirty(true);
  };
  const aiSuggestion = active?.aiSuggestedScore == null && active?.aiResult?.suggestedScore == null ? null : {
    teacherScore: Math.round(Math.min(active.maxScore, Math.max(0, Number(active.aiSuggestedScore ?? active.aiResult?.suggestedScore))) * 10) / 10,
    teacherFeedback: active.aiResult?.studentFeedback?.trim() || active.aiResult?.rationale?.trim() || "",
  };

  return <DirectAssignmentShell theme={theme} title={`${assignment.title} · 批改`} eyebrow="作业批改" showTitle={false} showSubtitle={false} onBack={onBack} className="direct-grading-workspace" actions={<><button type="button" className="edu-button primary" disabled={!overview?.canPublish || busy !== null} onClick={() => void publish()}>{busy === "publish" ? <Loader2 className="edu-spin" size={14} /> : <Send size={14} />}发布成绩</button></>}>
    <div className="direct-grading-layout">
      <aside className="direct-student-sidebar">
        <div className="direct-sidebar-heading">
          <div><strong>学生提交</strong><small>{overview?.students.length || 0} 位学生</small></div>
          <button type="button" className="direct-mobile-picker-toggle" onClick={() => setStudentPickerOpen(value => !value)} aria-expanded={studentPickerOpen}>
            {studentPickerOpen ? "收起" : selectedStudent?.studentName || "选择学生"}
          </button>
        </div>
        <div className={`direct-student-list${studentPickerOpen ? " open" : ""}`}>
          {overview?.students.map(student => <button type="button" key={student.userId} className={`direct-student-row${student.userId === selectedUserId ? " active" : ""}`} onClick={() => void selectStudent(student.userId)}>
            <div><strong>{student.studentName || "资料待补全"}</strong><small>{student.studentNumber || "无学号"}</small></div>
            <span className={`direct-grade-status ${student.submissionStatus}`}>{submissionStatusLabel(student.submissionStatus)}</span>
            <small>{student.teacherTotal == null ? "待评分" : `${student.teacherTotal.toFixed(1)} 分`}</small>
          </button>)}
        </div>
      </aside>
      <main className="direct-grading-main">
        {!submission ? <div className="direct-state"><ClipboardCheckIcon />{selectedStudent?.submissionId ? "正在加载提交…" : "请选择已有提交的学生开始批改。"}</div> : <>
          <div className="direct-grading-question-nav">{grades.map((grade, index) => <button type="button" key={grade.questionId} className={index === activeIndex ? "active" : ""} onClick={() => void selectQuestion(index)}><span>{index + 1}</span><small>{grade.teacherScore == null ? "待评分" : `${grade.teacherScore}/${grade.maxScore}`}</small></button>)}</div>
          {active ? <><section className="direct-grading-question"><span>{directQuestionLabel(activeIndex + 1)} · 满分 {active.maxScore}</span><DirectQuestionContent text={active.question} /></section><section className="direct-grading-block"><h3>学生答案</h3><MathText text={active.studentAnswer} /></section><section className="direct-grading-block"><h3>参考答案与评分点</h3><DirectQuestionContent text={active.referenceAnswer} />{active.expectedPoints.length > 0 && <ul>{active.expectedPoints.map((point, index) => <li key={index}><MathText text={point} /></li>)}</ul>}</section><section className={`direct-grading-block direct-matrix-block ${(active.matrixReport as MatrixCheckReport)?.status || "not_applicable"}`}><h3>过程检查 · {matrixStatusLabel((active.matrixReport as MatrixCheckReport)?.status)}</h3><p>{(active.matrixReport as MatrixCheckReport)?.summary || "本题没有额外的矩阵过程检查。"}</p></section><section className="direct-grading-block direct-ai-block"><div className="direct-block-heading"><h3><Brain size={15} />AI 评分建议</h3>{aiSuggestion && <button type="button" className="edu-button secondary" disabled={readOnly || busy !== null} onClick={() => updateGrade(active.questionId, aiSuggestion)}><Check size={13} />采纳建议</button>}</div>{active.aiResult?.rationale ? <><strong>建议 {active.aiSuggestedScore ?? active.aiResult.suggestedScore} / {active.maxScore}</strong><p>{active.aiResult.rationale}</p>{active.aiResult.studentFeedback && <p><b>给学生的反馈：</b>{active.aiResult.studentFeedback}</p>}</> : <p>尚未生成 AI 评价，可手工评分。</p>}</section></> : <div className="direct-state">该提交没有可评分题目。</div>}
        </>}
      </main>
      <aside className="direct-grading-score">
        {submission && active ? <><label className="direct-editor-field"><span>本题得分 <small>/ {active.maxScore}</small></span><input type="number" min="0" max={active.maxScore} step="0.1" value={active.teacherScore ?? ""} disabled={readOnly || busy !== null} onChange={event => updateGrade(active.questionId, { teacherScore: event.target.value === "" ? null : Math.round(Number(event.target.value) * 10) / 10 })} /></label><label className="direct-editor-field"><span>教师逐题评语</span><textarea rows={8} value={active.teacherFeedback || ""} disabled={readOnly || busy !== null} onChange={event => updateGrade(active.questionId, { teacherFeedback: event.target.value })} placeholder="补充或修正 AI 建议" /></label><label className="direct-editor-field"><span>整体评语</span><textarea rows={8} value={submission.teacherSummary || ""} disabled={readOnly || busy !== null} onChange={event => updateTeacherSummary(event.target.value)} placeholder="成绩发布后展示给学生" /></label><div className="direct-grading-total"><span>教师总分</span><strong>{teacherTotal.toFixed(1)} / 100</strong></div><div className="direct-grading-actions"><button type="button" className="edu-button secondary" disabled={readOnly || busy !== null || scoringIncomplete} onClick={() => void evaluate()}>{busy === "evaluate" ? <Loader2 className="edu-spin" size={14} /> : <Brain size={14} />}AI 评价</button><button type="button" className="edu-button ghost" disabled={readOnly || busy !== null} onClick={() => void save()}>{busy === "save" ? <Loader2 className="edu-spin" size={14} /> : <Save size={14} />}保存草稿</button></div></> : null}
        {submission && <p className="direct-score-hint">切换学生或题目时会自动保存当前批改。</p>}
      </aside>
    </div>
    {error && <div className="direct-workspace-feedback error">{error}</div>}
    {feedback && <div className="direct-workspace-feedback success">{feedback}</div>}
  </DirectAssignmentShell>;
}

function ClipboardCheckIcon() {
  return <CheckCircle2 size={21} />;
}
