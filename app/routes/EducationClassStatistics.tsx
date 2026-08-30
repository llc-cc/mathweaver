import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, BarChart3, Download, Loader2, RefreshCw, Search } from "lucide-react";
import { downloadEducationClassStatistics, downloadEducationStudentStatistics, educationErrorMessage, formatDate, loadEducationClassStatistics, type ClassStatisticsCellStatus, type EducationClassStatistics } from "./education";
import "./education.css";

const STATUS_LABELS: Record<ClassStatisticsCellStatus, string> = { not_submitted: "未提交", submitted: "待批改", review_draft: "待定稿", finalized: "已定稿", released: "已发布" };
type SortKey = "studentNumber" | "studentName" | "averageScore" | "rank";

function displayScore(score: number | null) { return score == null ? "—" : score.toFixed(1); }

export function EducationClassStatistics({ token, classId, classTitle, theme, onBack }: { token: string; classId: string; classTitle: string; theme: "light" | "dark"; onBack: () => void }) {
  const [data, setData] = useState<EducationClassStatistics | null>(null);
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("studentNumber");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [studentExporting, setStudentExporting] = useState<number | null>(null);

  const load = async () => {
    setLoading(true); setError("");
    try { setData(await loadEducationClassStatistics(token, classId)); }
    catch (cause) { setError(educationErrorMessage(cause)); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, [classId, token]);

  const students = useMemo(() => {
    if (!data) return [];
    const normalized = query.trim().toLocaleLowerCase();
    return data.students.filter(student => !normalized || `${student.studentName || ""} ${student.studentNumber || ""}`.toLocaleLowerCase().includes(normalized)).sort((a, b) => {
      let result = 0;
      if (sortKey === "averageScore" || sortKey === "rank") {
        const aValue = a[sortKey];
        const bValue = b[sortKey];
        if (aValue == null && bValue != null) return 1;
        if (aValue != null && bValue == null) return -1;
        result = aValue == null || bValue == null ? 0 : sortKey === "averageScore" ? bValue - aValue : aValue - bValue;
      } else {
        result = (a[sortKey] || "").localeCompare(b[sortKey] || "", "zh-CN");
      }
      return result || (a.studentNumber || "").localeCompare(b.studentNumber || "", "zh-CN");
    });
  }, [data, query, sortKey]);

  const exportWorkbook = async () => {
    setExporting(true); setError("");
    try {
      const result = await downloadEducationClassStatistics(token, classId);
      triggerDownload(result.blob, result.filename);
    } catch (cause) { setError(educationErrorMessage(cause)); }
    finally { setExporting(false); }
  };

  const exportStudentWorkbook = async (userId: number) => {
    setStudentExporting(userId); setError("");
    try {
      const result = await downloadEducationStudentStatistics(token, classId, userId);
      triggerDownload(result.blob, result.filename);
    } catch (cause) { setError(educationErrorMessage(cause)); }
    finally { setStudentExporting(null); }
  };

  if (loading && !data) return <div className="edu-root edu-statistics-root" data-theme={theme}><div className="edu-loading"><Loader2 className="edu-spin" />正在加载班级成绩统计…</div></div>;
  if (error && !data) return <div className="edu-root edu-statistics-root" data-theme={theme}><div className="edu-empty"><BarChart3 size={34} /><strong>无法加载班级成绩统计</strong><span>{error}</span><button className="edu-button primary" onClick={onBack}><ArrowLeft size={14} />返回班级作业</button></div></div>;
  if (!data) return null;

  const matrixGridTemplate = `var(--edu-matrix-name-width) var(--edu-matrix-number-width) var(--edu-matrix-export-width) repeat(${data.assignments.length}, var(--edu-matrix-assignment-width))`;

  return <div className="direct-workspace edu-root edu-statistics-root" data-theme={theme}>
    <header className="direct-workspace-header edu-statistics-header"><div className="edu-statistics-header-left"><button type="button" className="edu-button ghost" onClick={onBack}><ArrowLeft size={15} />返回班级作业</button><span className="edu-kicker">成绩统计</span></div><div className="edu-statistics-header-center" aria-hidden="true" /><div className="edu-statistics-header-actions"><button type="button" className="edu-button primary" disabled={exporting} onClick={() => void exportWorkbook()}>{exporting ? <Loader2 className="edu-spin" size={14} /> : <Download size={14} />}导出 Excel</button></div></header>
    <main className="edu-statistics-workspace edu-class-statistics-workspace">
      {error && <div className="edu-error">{error}</div>}
      <section className="edu-statistics-head"><div><span className="edu-kicker">CLASS PERFORMANCE</span><h1>{data.classTitle || classTitle}</h1><p>更新于 {formatDate(data.generatedAt)} · 统计已发布的题目作业</p></div></section>
      <section className="edu-statistics-toolbar"><label className="edu-stat-sort"><span>排序</span><span className="edu-stat-sort-select"><select value={sortKey} onChange={event => setSortKey(event.target.value as SortKey)}><option value="studentNumber">按学号</option><option value="studentName">按姓名</option><option value="averageScore">按平均分</option><option value="rank">按排名</option></select></span></label><label className="edu-stat-search"><Search size={14} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索姓名或学号" /></label><button type="button" className="edu-button ghost" disabled={loading} onClick={() => void load()}><RefreshCw size={14} />刷新统计</button></section>
      {!data.assignments.length && <div className="edu-stat-empty-note">当前班级还没有已发布的题目作业，发布作业后即可在此查看成绩。</div>}
      {data.assignments.length > 0 && <section className="edu-class-matrix-card"><div className="edu-class-matrix-scroll"><div className="edu-class-matrix" role="table" aria-label="学生成绩矩阵"><div className="edu-class-matrix-header" role="row" style={{ gridTemplateColumns: matrixGridTemplate }}><div className="edu-class-matrix-cell edu-sticky-left edu-student-name" role="columnheader">姓名</div><div className="edu-class-matrix-cell edu-sticky-left edu-student-number" role="columnheader">学号</div><div className="edu-class-matrix-cell edu-sticky-left edu-student-export" role="columnheader">导出</div>{data.assignments.map(assignment => <div className="edu-class-matrix-cell edu-assignment-column" role="columnheader" key={assignment.id} title={assignment.title}><div className="edu-class-assignment-heading"><strong>{assignment.title}</strong><span><i className="edu-assignment-dot questions" aria-hidden="true" />{assignment.questionCount}题 <i className="edu-assignment-dot score" aria-hidden="true" />{assignment.maxScore.toFixed(1)}分</span><em>{assignment.submittedCount}/{assignment.studentCount} 已提交</em></div></div>)}</div><div className="edu-class-matrix-body" role="rowgroup">{students.map(student => <div className="edu-class-matrix-row" role="row" style={{ gridTemplateColumns: matrixGridTemplate }} key={student.userId}><div className="edu-class-matrix-cell edu-sticky-left edu-student-name" role="cell"><b>{student.studentName || "资料待补全"}</b></div><div className="edu-class-matrix-cell edu-sticky-left edu-student-number" role="cell">{student.studentNumber || "—"}</div><div className="edu-class-matrix-cell edu-sticky-left edu-student-export" role="cell"><button type="button" className="edu-student-export-button" title={`导出${student.studentName || "该学生"}的成绩`} aria-label={`导出${student.studentName || "该学生"}的成绩`} disabled={studentExporting !== null} onClick={() => void exportStudentWorkbook(student.userId)}>{studentExporting === student.userId ? <Loader2 className="edu-spin" size={18} /> : <Download size={18} />}</button></div>{data.assignments.map(assignment => { const cell = student.assignments[assignment.id]; return <div className={`edu-class-matrix-cell ${cell.score == null ? `status-${cell.status}` : "score"}`} role="cell" key={assignment.id}>{cell.score == null ? STATUS_LABELS[cell.status] : displayScore(cell.score)}</div>; })}</div>)}</div><div className="edu-class-matrix-footer" role="presentation" aria-hidden="true" /></div>{!students.length && <div className="edu-empty compact"><Search size={24} /><span>{data.students.length ? "没有匹配的学生" : "当前班级暂无有效学生"}</span></div>}</div></section>}
      {data.assignments.length > 0 && data.overview.finalizedStudents === 0 && <div className="edu-stat-empty-note">暂无已发布成绩。当前仍显示全部学生和作业状态；教师定稿并发布成绩后，平均分和排名才会生成。</div>}
    </main>
  </div>;
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
