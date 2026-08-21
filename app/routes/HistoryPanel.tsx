import { useState, useEffect } from "react";
import { apiUrl } from "~/api";
import type { HistoryItem, HistoryPanelProps } from "./home";
import { useJobs, type RestoredJob } from "~/context/jobs";
import { captureAuthRequestIdentity, isAuthRequestIdentityCurrent, protectedFetch } from "./auth";
import {
  MAIN_PIPELINE_STAGE_COUNT,
  pipelineStageLabel,
} from "../pipeline-stages";

// ── History Panel ─────────────────────────────────────────────────────────────

const REQUEST_TIMEOUT_MS = 8000;

function authHeader(token: string) {
  return { Authorization: `Bearer ${token}` };
}

async function fetchWithTimeout(input: RequestInfo | URL, init: RequestInit = {}, token?: string) {
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await protectedFetch(input, { ...init, signal: ctrl.signal }, token);
  } finally {
    window.clearTimeout(timer);
  }
}

export function HistoryPanel({ token, llmConfig, onLoad, onResume, onClose }: HistoryPanelProps) {
  const { jobs, restoreJob } = useJobs();
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [opening, setOpening] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [resuming, setResuming] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);

  const closeSoftly = () => {
    if (closing) return;
    setClosing(true);
    window.setTimeout(onClose, 150);
  };

  useEffect(() => {
    const requestIdentity = captureAuthRequestIdentity(token);
    setLoading(true);
    setError("");
    fetchWithTimeout(apiUrl("/api/v2/history"), { headers: authHeader(token) }, token)
      .then(async (r) => {
        if (!r.ok) throw new Error(r.status === 401 ? "登录已过期，请重新登录" : "历史记录加载失败");
        return r.json();
      })
      .then((data) => {
        if (isAuthRequestIdentityCurrent(requestIdentity)) setItems(Array.isArray(data) ? data : []);
      })
      .catch((e) => {
        if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
        setItems([]);
        setError(e?.name === "AbortError" ? "历史记录加载超时，请确认后端服务正常运行" : (e?.message || "历史记录加载失败"));
      })
      .finally(() => setLoading(false));
  }, [token]);

  const displayFilename = (filename: string) => filename === "Agent 导入结果" ? "导入已有图谱" : filename;

  const openItem = async (id: string, filename: string) => {
    if (opening) return;
    setOpening(id);
    setError("");
    const requestIdentity = captureAuthRequestIdentity(token);
    try {
      const res = await fetchWithTimeout(apiUrl(`/api/v2/history/${id}`), { headers: authHeader(token) }, token);
      if (!res.ok) throw new Error("历史图谱加载失败");
      const data = await res.json();
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      onLoad({ nodes: data.nodes, edges: data.edges, latex_macros: data.latex_macros, source_pdf: data.source_pdf }, displayFilename(filename), id);
      onClose();
    } catch (e: any) {
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      setError(e?.name === "AbortError" ? "历史图谱加载超时，请稍后重试" : (e?.message || "历史图谱加载失败"));
    } finally {
      setOpening(null);
    }
  };

  const resumeItem = async (item: HistoryItem) => {
    if (resuming || !item.resume_available || jobs[item.id]?.phase === "running") return;
    if (
      !llmConfig?.api_url.trim()
      || !llmConfig.model_name.trim()
      || !llmConfig.api_key.trim()
      || !llmConfig.embedding_model.trim()
    ) {
      setError("请先在模型设置中补全 LLM 和 Embedding 配置。");
      return;
    }
    setResuming(item.id);
    setError("");
    const requestIdentity = captureAuthRequestIdentity(token);
    try {
      const res = await fetchWithTimeout(
        apiUrl(`/api/v2/history/${item.id}/resume`),
        {
          method: "POST",
          headers: {
            ...authHeader(token),
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ llm_config: llmConfig }),
        },
        token,
      );
      const body = await res.json().catch(() => ({}));
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      if (!res.ok || !body.job) {
        throw new Error(body.error || "历史任务恢复失败");
      }
      const restoredJob = body.job as RestoredJob;
      restoreJob(restoredJob);
      onResume(restoredJob);
      onClose();
    } catch (e: any) {
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      setError(
        e?.name === "AbortError"
          ? "恢复请求超时，请稍后重试"
          : (e?.message || "历史任务恢复失败"),
      );
    } finally {
      setResuming(null);
    }
  };

  const deleteItem = async (id: string) => {
    setDeleting(id);
    setError("");
    const requestIdentity = captureAuthRequestIdentity(token);
    try {
      const res = await fetchWithTimeout(apiUrl(`/api/v2/history/${id}`), { method: "DELETE", headers: authHeader(token) }, token);
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      if (!res.ok) throw new Error("删除失败");
      setItems((prev) => prev.filter((i) => i.id !== id));
    } catch (e: any) {
      if (!isAuthRequestIdentityCurrent(requestIdentity)) return;
      setError(e?.name === "AbortError" ? "删除超时，请稍后重试" : (e?.message || "删除失败"));
    } finally {
      setDeleting(null);
    }
  };

  const fmt = (iso: string) => {
    const d = new Date(iso);
    return d.toLocaleDateString("zh-CN") + " " + d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  };

  return (
    <>
    {/* 删除确认弹窗 */}
    {confirmId && (
      <div className="mg-motion-backdrop" style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1100 }}
        onClick={() => setConfirmId(null)}>
        <div className="mg-motion-dialog" style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--radius-lg)", padding: "24px 28px", width: 300, boxShadow: "var(--shadow-lg)" }}
          onClick={e => e.stopPropagation()}>
          <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>确认删除</div>
          <div style={{ fontSize: 13, color: "var(--muted)", marginBottom: 20, lineHeight: 1.6 }}>
            删除后该图谱记录将无法恢复，是否继续？
          </div>
          <div style={{ display: "flex", gap: 10 }}>
            <button
              style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", margin: 0, padding: "8px 0", borderRadius: "var(--radius)", border: "1px solid var(--line)", background: "none", fontSize: 13, cursor: "pointer", color: "var(--muted)", textAlign: "center" }}
              onClick={() => setConfirmId(null)}
            >取消</button>
            <button
              style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", margin: 0, padding: "8px 0", borderRadius: "var(--radius)", border: "none", background: "var(--danger)", color: "#fff", fontSize: 13, fontWeight: 600, cursor: "pointer", textAlign: "center" }}
              onClick={() => { deleteItem(confirmId); setConfirmId(null); }}
            >删除</button>
          </div>
        </div>
      </div>
    )}
    <div className={`mg-motion-backdrop ${closing ? "closing" : ""}`} style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,.35)",
      display: "flex", justifyContent: "flex-end", zIndex: 900,
    }} onClick={closeSoftly}>
      <div
        className={`mg-motion-drawer-right ${closing ? "closing" : ""}`}
        style={{
          width: 360, background: "var(--surface)", height: "100%", overflow: "auto",
          borderLeft: "1px solid var(--line)",
          boxShadow: "var(--shadow-lg)", padding: "24px 20px",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
          <div style={{ fontSize: 17, fontWeight: 600, fontFamily: "var(--font-display)", color: "var(--ink)" }}>历史记录</div>
          <button style={{ background: "none", border: "none", cursor: "pointer", fontSize: 18, color: "var(--muted)" }} onClick={closeSoftly}>×</button>
        </div>

        {loading && <div style={{ fontSize: 13, color: "var(--muted)" }}>加载中…</div>}
        {error && <div style={{ fontSize: 13, color: "var(--danger)", marginBottom: 12, lineHeight: 1.6 }}>{error}</div>}
        {!loading && items.length === 0 && (
          <div style={{ fontSize: 13, color: "var(--muted)" }}>暂无保存的图谱</div>
        )}

        {items.map((item) => (
          <div
            key={item.id}
            style={{
              border: "1px solid var(--line)", borderRadius: "var(--radius)", padding: "12px 14px",
              background: "var(--bg)",
              marginBottom: 10, cursor: item.status === "done" ? "pointer" : "default", transition: "border-color .15s",
            }}
            onMouseEnter={(e) => (e.currentTarget.style.borderColor = "var(--accent)")}
            onMouseLeave={(e) => (e.currentTarget.style.borderColor = "var(--line)")}
            onClick={() => {
              if (item.status === "done") openItem(item.id, item.filename);
            }}
          >
            <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, wordBreak: "break-all" }}>
              {displayFilename(item.filename)}
            </div>
            <div style={{ fontSize: 11, color: "var(--muted)" }}>
              {item.node_count} 节点 · {item.edge_count} 关系 · {fmt(item.created_at)}
            </div>
            {item.status !== "done" && (
              <div style={{ marginTop: 8, fontSize: 11, color: "var(--muted)", lineHeight: 1.6 }}>
                <div>
                  {item.status === "paused"
                    ? "已暂停"
                    : item.status === "error"
                      ? "处理失败"
                      : "处理中"}
                  {" · "}
                  已完成 {item.stages_done.length}/{item.total_stages || MAIN_PIPELINE_STAGE_COUNT} 个阶段
                </div>
                {(item.stage || item.stage_label) && (
                  <div>当前阶段：{pipelineStageLabel(item.stage, item.stage_label)}</div>
                )}
              </div>
            )}
            {(item.status === "paused" || item.status === "error") && !item.resume_available && (
              <div style={{ marginTop: 8, fontSize: 11, color: "var(--danger)" }}>
                恢复缓存不可用
              </div>
            )}
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
              <button
                className="mg-btn mg-history-action-button"
                style={{
                  flex: "0 0 calc((100% - 8px) / 2)",
                  minHeight: 32, margin: 0, boxSizing: "border-box",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  background: "var(--danger-light)", border: "1px solid var(--danger-line)",
                  color: "var(--danger)", borderRadius: 6, fontFamily: "inherit", fontSize: 11,
                  fontWeight: 600, padding: "5px 12px",
                  textAlign: "center",
                }}
                onClick={(e) => { e.stopPropagation(); setConfirmId(item.id); }}
                disabled={deleting === item.id || opening === item.id || resuming === item.id}
              >
                {opening === item.id ? "加载中…" : deleting === item.id ? "删除中…" : "删除"}
              </button>
              {(item.status === "paused" || item.status === "error") && item.resume_available && (
                <button
                  className="mg-btn mg-history-action-button"
                  style={{
                    flex: 1, minHeight: 32, margin: 0, boxSizing: "border-box",
                    display: "flex", alignItems: "center", justifyContent: "center",
                    background: "var(--accent)", border: "1px solid var(--accent)",
                    color: "#fff", borderRadius: 6, fontFamily: "inherit", fontSize: 11,
                    fontWeight: 600, padding: "5px 12px",
                    textAlign: "center",
                  }}
                  onClick={(e) => { e.stopPropagation(); void resumeItem(item); }}
                  disabled={resuming !== null || deleting === item.id || jobs[item.id]?.phase === "running"}
                >
                  {jobs[item.id]?.phase === "running"
                    ? "处理中…"
                    : resuming === item.id
                    ? "恢复中…"
                    : item.status === "paused" ? "继续处理" : "重试"}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
    </>
  );
}
