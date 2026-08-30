import React, { useEffect, useState } from "react";
import { useJobs, type BackgroundJob } from "~/context/jobs";
import {
  MAIN_PIPELINE_STAGE_DEFS as STAGE_DEFS,
  pipelineStageLabel,
} from "~/pipeline-stages";

interface FloatingBadgeProps {
  onViewResult: (jobId: string) => void;
  hidden?: boolean;
}

export function JobDetailPanel({ job, onViewResult, onDismiss, onPause, onResume, onCancel }: {
  job: BackgroundJob;
  onViewResult: (id: string) => void;
  onDismiss: (id: string) => void;
  onPause: (id: string) => void;
  onResume: (id: string) => void;
  onCancel: (id: string) => void;
}) {
  const done = job.stagesDone;
  const current = job.stage;
  const isRunning = job.phase === "running";
  const isDone = job.phase === "done";
  const isError = job.phase === "error";
  const isPaused = job.phase === "paused";
  const currentLabel = pipelineStageLabel(current, job.stageLabel);

  return (
    <div style={{ borderTop: "1px solid var(--line)", paddingTop: 16, marginTop: 4 }}>
      {/* Filename */}
      <div style={{ fontSize: 13, fontWeight: 600, color: "var(--ink)", marginBottom: 4, wordBreak: "break-all" }}>
        {job.filename}
      </div>

      {/* Running: stage list */}
      {isRunning && (
        <>
          <div style={{ display: "flex", gap: 8, marginBottom: 4 }}>
            {/* Stage list */}
            <ul style={{ flex: 1, listStyle: "none", margin: 0, padding: 0 }}>
              {STAGE_DEFS.map(([key, label]) => {
                const isDoneStage = done.includes(key);
                const isActive = current === key && !isDoneStage;
                return (
                  <li key={key} style={{
                    display: "flex", alignItems: "center", justifyContent: "center", gap: 7,
                    padding: "3px 0", fontSize: 12,
                    color: isDoneStage ? "var(--ok)" : isActive ? "var(--accent)" : "var(--muted)",
                    fontWeight: isActive ? 600 : 400,
                  }}>
                    <span style={{
                      width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
                      background: isDoneStage ? "var(--ok)" : isActive ? "var(--accent)" : "var(--line-strong)",
                    }} />
                    {pipelineStageLabel(key, label)}
                    {isDoneStage && <span style={{ fontSize: 10 }}>✓</span>}
                  </li>
                );
              })}
            </ul>
            {/* Right: current stage + progress */}
            <div style={{ width: 100, flexShrink: 0, paddingLeft: 12, borderLeft: "1px solid var(--line)" }}>
              <div style={{ fontSize: 9, fontWeight: 700, textTransform: "uppercase", letterSpacing: ".07em", color: "var(--muted)", marginBottom: 6 }}>当前</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: "var(--accent)", lineHeight: 1.2, marginBottom: 8 }}>{currentLabel}</div>
              <div style={{ fontSize: 11, color: "var(--muted)" }}>{done.length}/{job.totalStages}</div>
              <div style={{ marginTop: 8, height: 3, background: "var(--line)", borderRadius: 2, overflow: "hidden" }}>
                <div style={{ height: "100%", width: `${job.pct}%`, background: "var(--accent)", transition: "width .4s", borderRadius: 2 }} />
              </div>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--accent)", marginTop: 4 }}>{job.pct}%</div>
            </div>
          </div>
          <button
            style={{ width: "100%", background: "var(--danger-light)", border: "1px solid var(--danger-line)", color: "var(--danger)", borderRadius: 6, padding: "7px 0", fontSize: 12, cursor: "pointer", fontWeight: 500, marginTop: 4, textAlign: "center", display: "block" }}
            onClick={() => onPause(job.id)}
            disabled={job.pendingAction !== null}
          >
            {job.pendingAction === "pause" ? "暂停中…" : "暂停处理"}
          </button>
        </>
      )}

      {/* Paused */}
      {isPaused && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ background: "#fff8e6", border: "1px solid #ead39a", borderRadius: 6, padding: "8px 10px", fontSize: 12, color: "#7a5b12", lineHeight: 1.6 }}>
            已暂停 · 将从 {currentLabel} 的缓存继续
          </div>
          {job.errorMsg && (
            <div style={{ fontSize: 11, color: "var(--danger)" }}>{job.errorMsg}</div>
          )}
          <div style={{ display: "flex", gap: 8 }}>
            <button
              style={{ flex: 1, margin: 0, boxSizing: "border-box", display: "flex", alignItems: "center", justifyContent: "center", background: "var(--accent)", color: "#fff", border: "none", borderRadius: 6, padding: "8px 0", fontSize: 12, cursor: job.pendingAction ? "wait" : "pointer", fontWeight: 500, textAlign: "center" }}
              onClick={() => onResume(job.id)}
              disabled={job.pendingAction !== null}
            >
              {job.pendingAction === "resume" ? "恢复中…" : "继续处理"}
            </button>
            <button
              style={{ flex: 1, margin: 0, boxSizing: "border-box", display: "flex", alignItems: "center", justifyContent: "center", background: "var(--danger-light)", border: "1px solid var(--danger-line)", color: "var(--danger)", borderRadius: 6, padding: "8px 0", fontSize: 12, cursor: job.pendingAction ? "wait" : "pointer", fontWeight: 500, textAlign: "center" }}
              onClick={() => {
                if (window.confirm("取消后将永久删除源文件、检查点和历史记录，且无法恢复。确定取消任务吗？")) {
                  onCancel(job.id);
                }
              }}
              disabled={job.pendingAction !== null}
            >
              {job.pendingAction === "cancel" ? "取消中…" : "取消任务"}
            </button>
          </div>
        </div>
      )}

      {/* Done */}
      {isDone && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 12, color: "var(--ok)" }}>
            处理完成 · {job.result?.nodes?.length ?? 0} 节点 · {job.result?.edges?.length ?? 0} 关系
          </div>
          <div className="mg-floating-job-actions">
            <button
              className="mg-btn mg-floating-job-action mg-floating-job-action-primary"
              onClick={() => onViewResult(job.id)}
            >
              查看图谱 →
            </button>
            <button
              className="mg-btn mg-floating-job-action mg-floating-job-action-dismiss"
              onClick={() => onDismiss(job.id)}
            >
              从列表移除
            </button>
          </div>
        </div>
      )}

      {/* Error */}
      {isError && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ background: "var(--danger-light)", border: "1px solid var(--danger-line)", borderRadius: 6, padding: "8px 10px", fontSize: 12, color: "var(--danger)", lineHeight: 1.6 }}>
            {job.errorMsg}
          </div>
          <button
            style={{ width: "100%", margin: 0, display: "flex", alignItems: "center", justifyContent: "center", boxSizing: "border-box", background: "var(--accent)", color: "#fff", border: "none", borderRadius: 6, padding: "8px 0", fontSize: 12, cursor: job.pendingAction ? "wait" : "pointer", fontWeight: 500, textAlign: "center" }}
            onClick={() => onResume(job.id)}
            disabled={job.pendingAction !== null}
          >
            {job.pendingAction === "resume" ? "重试中…" : "重试"}
          </button>
          <button
            style={{ width: "100%", margin: 0, display: "flex", alignItems: "center", justifyContent: "center", boxSizing: "border-box", background: "none", border: "1px solid var(--line)", color: "var(--muted)", borderRadius: 6, padding: "6px 0", fontSize: 12, cursor: "pointer", textAlign: "center" }}
            onClick={() => onDismiss(job.id)}
          >
            关闭
          </button>
        </div>
      )}
    </div>
  );
}

export function FloatingBadge({ onViewResult, hidden = false }: FloatingBadgeProps) {
  const { jobs, latestJobId, pauseJob, resumeJob, cancelJob, dismissJob } = useJobs();
  const [open, setOpen] = useState(false);
  const [closing, setClosing] = useState(false);

  useEffect(() => {
    if (!hidden) return;
    setOpen(false);
    setClosing(false);
  }, [hidden]);

  useEffect(() => {
    if (hidden || !latestJobId || jobs[latestJobId]?.phase !== "running") return;
    setOpen(true);
    setClosing(false);
  }, [hidden, latestJobId]);

  const jobList = Object.values(jobs);
  if (hidden) return null;
  if (jobList.length === 0) return null;

  const runningCount = jobList.filter(j => j.phase === "running").length;
  const errorCount   = jobList.filter(j => j.phase === "error").length;
  const pausedCount  = jobList.filter(j => j.phase === "paused").length;
  const doneCount    = jobList.filter(j => j.phase === "done").length;

  const pillBg = runningCount > 0 ? "var(--accent)" : errorCount > 0 ? "var(--danger)" : pausedCount > 0 ? "#8a6d1d" : "var(--ok)";
  const pillText = runningCount > 0
    ? `处理中 ${runningCount > 1 ? `(${runningCount})` : ""}`
    : errorCount > 0 ? "处理失败"
    : pausedCount > 0 ? "已暂停"
    : "已完成";
  const pillIcon = runningCount > 0 ? "●" : errorCount > 0 ? "✗" : pausedCount > 0 ? "⏸" : "✓";

  const closeSoftly = () => {
    if (closing) return;
    setClosing(true);
    window.setTimeout(() => {
      setOpen(false);
      setClosing(false);
    }, 140);
  };

  const toggleOpen = () => {
    if (open) closeSoftly();
    else setOpen(true);
  };

  return (
    <>
      {/* Overlay to close panel */}
      {open && (
        <div
          className={`mg-motion-backdrop ${closing ? "closing" : ""}`}
          style={{ position: "fixed", inset: 0, zIndex: 1090 }}
          onClick={closeSoftly}
        />
      )}

      {/* Persistent floating button */}
      {/* Academic serif theme tokens — mirror .mg-upload-screen / landing so the
          badge matches the homepage & graph palette wherever it floats. */}
      <div style={{
        position: "fixed", bottom: 24, right: 24, zIndex: 1100,
        fontFamily: "var(--font-ui)",
        ["--surface" as string]: "#ffffff",
        ["--surface-alt" as string]: "#f4f2ee",
        ["--line" as string]: "#e7e2da",
        ["--line-strong" as string]: "#d8d1c6",
        ["--ink" as string]: "#1c1b19",
        ["--muted" as string]: "#6b6864",
        ["--accent" as string]: "#1e5aa8",
        ["--accent-light" as string]: "#eaf1fa",
        ["--ok" as string]: "#2f7d56",
        ["--danger" as string]: "#b42318",
        ["--danger-light" as string]: "#fff6f5",
        ["--danger-line" as string]: "#f1b8b3",
        ["--shadow" as string]: "0 4px 14px rgba(28,27,25,.07), 0 10px 28px rgba(28,27,25,.05)",
        ["--font-display" as string]: '"Source Serif 4", "Noto Serif SC", Georgia, serif',
      }}>

        {/* Slide-in task panel */}
        {open && (
          <div className={`mg-motion-float-up ${closing ? "closing" : ""}`} style={{
            position: "absolute", bottom: 52, right: 0,
            width: 340, maxHeight: "80vh", overflow: "auto",
            background: "var(--surface)", border: "1px solid var(--line)",
            borderRadius: 16, boxShadow: "var(--shadow)",
            padding: "20px 18px 18px",
          }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
              <div style={{ fontSize: 16, fontWeight: 600, fontFamily: "var(--font-display)", letterSpacing: "-.01em", color: "var(--ink)" }}>后台任务</div>
              <button
                style={{ background: "none", border: "none", fontSize: 18, color: "var(--muted)", cursor: "pointer", lineHeight: 1 }}
                onClick={closeSoftly}
              >×</button>
            </div>

            {jobList.map(job => (
              <JobDetailPanel
                key={job.id}
                job={job}
                onViewResult={(id) => { onViewResult(id); closeSoftly(); }}
                onDismiss={dismissJob}
                onPause={(id) => { void pauseJob(id); }}
                onResume={(id) => { void resumeJob(id); }}
                onCancel={(id) => { void cancelJob(id); }}
              />
            ))}
          </div>
        )}

        {/* Pill button */}
        <button
          onClick={toggleOpen}
          style={{
            display: "flex", alignItems: "center", gap: 7,
            background: pillBg, color: "#fff",
            border: "none", borderRadius: 20,
            padding: "8px 16px", fontSize: 12, fontWeight: 600,
            cursor: "pointer", boxShadow: "0 2px 14px rgba(0,0,0,.2)",
            transition: "background .2s",
          }}
        >
          <span style={{
            animation: runningCount > 0 ? "mg-pulse 1.4s ease-in-out infinite" : "none",
            fontFamily: pillIcon === "⏸" ? '"Segoe UI Symbol", sans-serif' : undefined,
            fontSize: pillIcon === "⏸" ? 16 : 10,
            lineHeight: 1,
          }}>
            {pillIcon}
          </span>
          {pillText}
          {runningCount > 0 && (
            <span style={{ fontSize: 11, opacity: .8 }}>
              {jobList.find(j => j.phase === "running")?.pct ?? 0}%
            </span>
          )}
          {doneCount > 0 && runningCount === 0 && errorCount === 0 && (
            <span style={{ background: "rgba(255,255,255,.25)", borderRadius: 10, padding: "1px 6px", fontSize: 10 }}>
              {doneCount}
            </span>
          )}
        </button>
      </div>

      <style>{`
        @keyframes mg-pulse { 0%,100%{opacity:1} 50%{opacity:.35} }
      `}</style>
    </>
  );
}
