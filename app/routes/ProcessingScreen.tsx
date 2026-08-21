import type { ProcessingScreenProps } from "./home";
import {
  MAIN_PIPELINE_STAGE_DEFS as STAGE_DEFS,
  pipelineStageLabel,
} from "../pipeline-stages";

// ── Processing Screen ────────────────────────────────────────────────────────

export function ProcessingScreen({ status, filename }: ProcessingScreenProps) {
  const done = status?.stages_done ?? [];
  const current = status?.stage ?? null;
  const pct = status
    ? Math.round((done.length / status.total_stages) * 100)
    : 0;
  const currentLabel = pipelineStageLabel(current, status?.stage_label);

  return (
    <div className="mg-root">
      <div className="mg-processing-screen">
        <div className="mg-processing-card">
          <div className="mg-processing-title">正在分析文档</div>
          <div className="mg-processing-file">{filename}</div>

          <ul className="mg-stage-list">
            {STAGE_DEFS.map(([key, label]) => {
              const isDone = done.includes(key);
              const isActive = current === key && !isDone;
              return (
                <li key={key} className={`mg-stage-item${isDone ? " done" : isActive ? " active" : ""}`}>
                  <span className="mg-stage-dot" />
                  {pipelineStageLabel(key, label)}
                  {isDone && <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--ok)" }}>✓</span>}
                </li>
              );
            })}
          </ul>

          {/* Right column: current stage focus */}
          <div className="mg-processing-right">
            <div style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: ".08em", color: "var(--text-muted)", marginBottom: 12 }}>
              当前阶段
            </div>
            <div style={{ fontSize: 28, fontWeight: 600, color: "var(--accent)", letterSpacing: "-.02em", marginBottom: 8 }}>
              {currentLabel}
            </div>
            <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
              {done.length} / {status?.total_stages ?? STAGE_DEFS.length} 阶段完成
            </div>
            <div style={{ marginTop: 24, width: "100%" }}>
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 6 }}>已完成</div>
              {done.length === 0 ? (
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>—</div>
              ) : (
                done.slice(-4).map((k) => (
                  <div key={k} style={{ fontSize: 12, color: "var(--text-secondary)", padding: "3px 0", borderBottom: "1px solid var(--border)" }}>
                    ✓ {pipelineStageLabel(k)}
                  </div>
                ))
              )}
            </div>
          </div>

          <div className="mg-progress-bar-wrap">
            <div className="mg-progress-bar-fill" style={{ width: `${pct}%` }} />
          </div>
          <div className="mg-progress-label">{pct}%</div>
        </div>
      </div>
    </div>
  );
}
