import { useState } from "react";
import { BookOpen, LockKeyhole } from "lucide-react";
import { apiUrl } from "~/api";
import { Logo } from "./home";
import type { AuthMode, AuthModalProps } from "./home";

// ── Auth Modal ────────────────────────────────────────────────────────────────

export function AuthModal({ onAuth, onSkip }: AuthModalProps) {
  const [mode, setMode] = useState<AuthMode>("login");
  const [educationRole, setEducationRole] = useState<"teacher" | "student">("student");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [closing, setClosing] = useState(false);

  const closeSoftly = () => {
    if (closing) return;
    setClosing(true);
    window.setTimeout(onSkip, 140);
  };

  const submit = async () => {
    setErr("");
    if (!email || !password) { setErr("请填写邮箱和密码"); return; }
    setLoading(true);
    try {
      const endpoint = mode === "login" ? apiUrl("/api/v2/auth/login") : apiUrl("/api/v2/auth/register");
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password, educationRole }),
      });
      const data = await res.json();
      if (!res.ok) { setErr(data.error ?? "操作失败"); return; }
      onAuth(data.token, data.email, data.educationRole ?? null, Boolean(data.canTeach));
    } catch {
      setErr("无法连接到后端");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className={`mg-motion-backdrop ${closing ? "closing" : ""}`} style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,.45)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000,
    }}>
      <div className={`mg-motion-dialog ${closing ? "closing" : ""}`} style={{
        background: "var(--surface)", borderRadius: "var(--radius-lg)", padding: "36px 40px",
        width: "min(440px, calc(100vw - 32px))", boxSizing: "border-box", boxShadow: "var(--shadow-lg)",
      }}>
        <Logo />
        <div style={{ marginTop: 20, marginBottom: 4, fontSize: 17, fontWeight: 600 }}>
          {mode === "login" ? "登录" : "创建账号"}
        </div>
        <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 20 }}>
          请选择登录身份
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 8, width: "100%", marginBottom: 16 }}>
            {([
              ["student", "我是学生", BookOpen],
              ["teacher", "我是老师", LockKeyhole],
            ] as const).map(([role, label, Icon]) => (
              <button
                key={role}
                type="button"
                onClick={() => { setEducationRole(role); if (role === "teacher") setMode("login"); setErr(""); }}
                style={{
                  display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
                  width: "100%", minWidth: 0, minHeight: 46, boxSizing: "border-box", margin: 0, padding: "10px 8px", textAlign: "center",
                  border: `1px solid ${educationRole === role ? (role === "teacher" ? "#315a86" : "var(--accent)") : "var(--line)"}`,
                  borderRadius: 10, background: educationRole === role ? (role === "teacher" ? "#eef4fa" : "var(--accent-light)") : "var(--surface)",
                  color: "var(--ink)", cursor: "pointer",
                }}
              >
                <Icon size={15} />
                <span style={{ fontSize: 12, fontWeight: 650 }}>{label}</span>
              </button>
            ))}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <input
            className="mg-input"
            style={{ width: "100%", margin: 0, boxSizing: "border-box" }}
            type="email"
            placeholder="邮箱"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
          <input
            className="mg-input"
            style={{ width: "100%", margin: 0, boxSizing: "border-box" }}
            type="password"
            placeholder="密码（至少6位）"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
        </div>

        {err && <p style={{ fontSize: 12, color: "var(--danger)", marginTop: 8 }}>{err}</p>}

        <button
          className="mg-btn mg-btn-primary"
          style={{ width: "100%", boxSizing: "border-box", justifyContent: "center", margin: "16px 0 0" }}
          onClick={submit}
          disabled={loading}
        >
          {loading ? "请稍候…" : mode === "login" ? "登录" : "注册"}
        </button>

        <div style={{ display: "flex", justifyContent: "center", alignItems: "center", marginTop: 12, fontSize: 12, color: "var(--muted)", textAlign: "center", whiteSpace: "nowrap" }}>
          {mode === "login" && educationRole === "student" ? (
            <span style={{ display: "inline-flex", alignItems: "center" }}>
              还没有账号？
              <button style={{ margin: 0, padding: 0, background: "none", border: "none", color: "var(--accent)", cursor: "pointer", fontSize: 12, lineHeight: "inherit" }} onClick={() => { setMode("register"); setErr(""); }}>注册</button>
            </span>
          ) : mode === "register" ? (
            <span style={{ display: "inline-flex", alignItems: "center" }}>
              已有账号？
              <button style={{ margin: 0, padding: 0, background: "none", border: "none", color: "var(--accent)", cursor: "pointer", fontSize: 12, lineHeight: "inherit" }} onClick={() => { setMode("login"); setErr(""); }}>登录</button>
            </span>
          ) : <span style={{ fontSize: 11 }}>教师账号由管理员配置</span>}
        </div>

        <button
          style={{ display: "block", width: "100%", boxSizing: "border-box", margin: "10px 0 0", padding: 0, background: "none", border: "none", color: "var(--muted)", fontSize: 11, textAlign: "center", cursor: "pointer" }}
          onClick={closeSoftly}
        >
          暂不登录，继续使用（本次会话结果不会保存）
        </button>
      </div>
    </div>
  );
}
