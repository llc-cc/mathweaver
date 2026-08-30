import { useEffect, useState } from "react";
import { ArrowLeft, BarChart3, Loader2 } from "lucide-react";
import { loadEducationAssignmentStatistics, type EducationAssignmentStatistics } from "./education";
import { educationErrorMessage } from "./education";
import "./education.css";
import "./direct-assignment.css";

function percent(value: number | null | undefined) { return value == null ? "—" : `${(value * 100).toFixed(1)}%`; }
function score(value: number | null | undefined) { return value == null ? "—" : value.toFixed(1); }

function ScoreDistribution({ items }: { items: EducationAssignmentStatistics["scoreDistribution"] }) {
  const max = Math.max(1, ...items.map(item => item.count));
  return <div className="edu-stat-bars" aria-label="成绩分布柱状图">
    {items.map(item => <div className="edu-stat-bar-item" key={item.label}>
      <div className="edu-stat-bar-value">{item.count}<small>{items.reduce((sum, current) => sum + current.count, 0) ? ` ${(item.count / items.reduce((sum, current) => sum + current.count, 0) * 100).toFixed(0)}%` : ""}</small></div>
      <svg className="edu-stat-bar-svg" viewBox="0 0 48 120" role="img" aria-label={`${item.label} 分数段 ${item.count} 人`}><rect x="8" y={120 - Math.max(4, item.count / max * 108)} width="32" height={Math.max(4, item.count / max * 108)} rx="5" /></svg>
      <strong>{item.label}</strong>
    </div>)}
  </div>;
}

function ErrorDistribution({ values }: { values: EducationAssignmentStatistics["errorDistribution"] }) {
  const entries = [{ key: "correct", label: "正确", color: "#3b9b73" }, { key: "partial", label: "部分得分", color: "#d69a3d" }, { key: "low", label: "低分/错误", color: "#cf6259" }] as const;
  const total = entries.reduce((sum, item) => sum + values[item.key], 0);
  let offset = 0;
  const segments = entries.map(item => { const start = offset; offset += total ? values[item.key] / total * 100 : 0; return { ...item, start, width: total ? values[item.key] / total * 100 : 0 }; });
  return <div className="edu-stat-donut-wrap"><div className="edu-stat-donut" role="img" aria-label={`错误分布：${entries.map(item => `${item.label}${values[item.key]}人`).join("，")}`}>
    <svg viewBox="0 0 42 42"><circle className="edu-stat-donut-track" cx="21" cy="21" r="15.9155" /><g transform="rotate(-90 21 21)">{segments.map(item => <circle key={item.key} cx="21" cy="21" r="15.9155" fill="none" stroke={item.color} strokeWidth="6" pathLength="100" strokeDasharray={`${item.width} ${100 - item.width}`} strokeDashoffset={-item.start} />)}</g></svg><strong>{total}</strong><small>题目判定</small>
  </div><div className="edu-stat-legend">{entries.map(item => <div key={item.key}><i style={{ background: item.color }} /><span>{item.label}</span><b>{values[item.key]}</b><small>{total ? `${(values[item.key] / total * 100).toFixed(1)}%` : "—"}</small></div>)}</div></div>;
}

export function EducationAssignmentStatistics({ token, assignmentId, assignmentTitle, dueAt, theme, onBack }: { token: string; assignmentId: string; assignmentTitle: string; dueAt?: string | null; theme: "light" | "dark"; onBack: () => void }) {
  const [data, setData] = useState<EducationAssignmentStatistics | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { let cancelled = false; setError(""); void loadEducationAssignmentStatistics(token, assignmentId).then(value => { if (!cancelled) setData(value); }).catch(cause => { if (!cancelled) setError(educationErrorMessage(cause)); }); return () => { cancelled = true; }; }, [assignmentId, token]);
  if (error) return <div className="edu-root edu-statistics-root" data-theme={theme}><div className="edu-empty"><BarChart3 size={34} /><strong>无法加载作业统计</strong><span>{error}</span><button className="edu-button primary" onClick={onBack}><ArrowLeft size={14} />返回作业列表</button></div></div>;
  if (!data) return <div className="edu-root edu-statistics-root" data-theme={theme}><div className="edu-loading"><Loader2 className="edu-spin" />正在加载作业统计…</div></div>;
  const hasScores = data.overview.finalizedStudents > 0;
  return <div className="direct-workspace edu-root edu-statistics-root" data-theme={theme}><header className="direct-workspace-header edu-statistics-header"><div className="edu-statistics-header-left"><button type="button" className="edu-button ghost" onClick={onBack}><ArrowLeft size={15} />返回作业列表</button><span className="edu-kicker">查看统计</span></div><div className="edu-statistics-header-center"><div className="edu-statistics-title-row"><strong>{data.assignmentTitle || assignmentTitle} - 单次作业统计</strong>{dueAt && <span>截止 {new Date(dueAt).toLocaleString("zh-CN")}</span>}</div></div><div /></header><main className="edu-statistics-workspace">
    <section className="edu-stat-overview-grid">{[["班级人数", data.overview.totalStudents], ["已提交", data.overview.submittedStudents], ["已定稿", data.overview.finalizedStudents], ["平均分", score(data.overview.averageScore)], ["最高分", score(data.overview.highestScore)], ["最低分", score(data.overview.lowestScore)], ["中位数", score(data.overview.medianScore)], ["及格率", percent(data.overview.passRate)]] .map(([label, value]) => <article className="edu-card edu-stat-kpi" key={String(label)}><small>{label}</small><strong>{value}</strong></article>)}</section>
    {!hasScores && <div className="edu-stat-empty-note">暂无已定稿成绩。当前进度：已提交 {data.overview.submittedStudents} 人，已定稿 {data.overview.finalizedStudents} 人。</div>}
    <div className="edu-stat-grid"><section className="edu-card edu-stat-card"><div className="edu-card-title"><BarChart3 size={18} /><div><strong>成绩分布</strong><small>按总得分率分为五个区间</small></div></div><ScoreDistribution items={data.scoreDistribution} /></section><section className="edu-card edu-stat-card"><div className="edu-card-title"><BarChart3 size={18} /><div><strong>逐题判定分布</strong><small>正确、部分得分、低分/错误</small></div></div><ErrorDistribution values={data.errorDistribution} /></section></div>
    <section className="edu-card edu-stat-card"><div className="edu-card-title"><BarChart3 size={18} /><div><strong>逐题平均得分率</strong><small>辅助线：60% 部分得分，80% 正确</small></div></div><div className="edu-question-bars">{data.questionStatistics.map(item => <div className="edu-question-bar-row" key={item.questionId}><span title={item.label}>第 {item.order} 题</span><div className="edu-question-bar-track"><i style={{ width: `${Math.max(0, Math.min(100, (item.averageRate || 0) * 100))}%` }} /><em>{percent(item.averageRate)}</em></div></div>)}</div></section>
    <section className="edu-card edu-stat-card"><div className="edu-card-title"><BarChart3 size={18} /><div><strong>易错题</strong><small>按低分率、平均得分率和平均失分排序</small></div></div><div className="edu-table edu-stat-table"><div className="edu-table-row head"><span>题目</span><span>平均得分率</span><span>低分率</span><span>平均失分</span></div>{data.difficultQuestions.map((item, index) => <div className={`edu-table-row ${index < 3 ? "edu-stat-highlight" : ""}`} key={item.questionId}><span><b>第 {item.order} 题</b><small>{item.label}</small></span><span>{percent(item.averageRate)}</span><span>{percent(item.lowScoreRate)}</span><span>{score(item.averageLostScore)}</span></div>)}</div></section>
    <section className="edu-card edu-stat-card"><div className="edu-card-title"><BarChart3 size={18} /><div><strong>学生成绩</strong><small>仅展示本次作业已定稿成绩</small></div></div><div className="edu-table edu-stat-table"><div className="edu-table-row head"><span>排名</span><span>学生</span><span>学号</span><span>总分</span></div>{data.students.map(item => <div className="edu-table-row" key={item.userId}><span>{item.rank}</span><span>{item.studentName || "资料待补全"}</span><span>{item.studentNumber || "—"}</span><span><b>{item.totalScore.toFixed(1)}</b></span></div>)}</div></section>
  </main></div>;
}
