import { useState } from "react";
import { apiUrl } from "~/api";
import { Logo } from "./home";
import type { AuthModalProps, AuthState } from "../auth-model";
import { isDesktopRuntime } from "../runtime";

// ── Auth Modal ────────────────────────────────────────────────────────────────

export function AuthModal({ onAuth, onSkip }: AuthModalProps) {
  const desktop = isDesktopRuntime();
  const [identifier, setIdentifier] = useState("");
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
    if (!identifier.trim() || !password) { setErr("请填写学号或邮箱和密码"); return; }
    setLoading(true);
    try {
      const res = await fetch(apiUrl("/api/v2/auth/login"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(desktop
          ? { email: identifier.trim(), password }
          : { identifier: identifier.trim(), password }),
      });
      const data = await res.json();
      if (!res.ok) { setErr(data.error ?? "操作失败"); return; }
      if (desktop && !data.user) {
        // 桌面本地后端保留旧 email 合同；在 UI 边界补齐统一 AuthState。
        const email = typeof data.email === "string" ? data.email : identifier.trim();
        onAuth({
          token: data.token,
          user: {
            id: 0,
            student_no: null,
            email,
            display_name: email,
            role: "student",
            initial_password_pending: false,
          },
        });
      } else {
        onAuth(data as AuthState);
      }
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
        width: 360, boxShadow: "var(--shadow-lg)",
      }}>
        <Logo />
        <div style={{ marginTop: 20, marginBottom: 4, fontSize: 17, fontWeight: 600 }}>
          登录
        </div>
        <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 20 }}>
          登录后可保存历史记录，随时重新查看图谱
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <input
            className="mg-input"
            type="text"
            autoComplete="username"
            placeholder="学号或邮箱"
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
          <input
            className="mg-input"
            type="password"
            autoComplete="current-password"
            placeholder="密码"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
        </div>

        {err && <p style={{ fontSize: 12, color: "var(--danger)", marginTop: 8 }}>{err}</p>}

        <button
          className="mg-btn mg-btn-primary"
          style={{ width: "100%", marginTop: 16 }}
          onClick={submit}
          disabled={loading}
        >
          {loading ? "请稍候…" : "登录"}
        </button>

        {desktop && (
          <button
            style={{ display: "block", width: "100%", marginTop: 10, background: "none", border: "none", color: "var(--muted)", fontSize: 11, cursor: "pointer" }}
            onClick={closeSoftly}
          >
            暂不登录，继续使用（本次会话结果不会保存）
          </button>
        )}
      </div>
    </div>
  );
}
