import { useState } from "react";
import { apiUrl } from "~/api";
import type { AuthState } from "../auth-model";
import { authFetch } from "./auth";

interface PasswordChangeModalProps {
  auth: AuthState;
  requiredHint: boolean;
  onAuth: (auth: AuthState) => void;
  onClose: () => void;
}

export function PasswordChangeModal({ auth, requiredHint, onAuth, onClose }: PasswordChangeModalProps) {
  const [editing, setEditing] = useState(!requiredHint);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!currentPassword || !newPassword) {
      setError("请填写当前密码和新密码");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const response = await authFetch(apiUrl("/api/v2/auth/change-password"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      }, auth.token);
      const payload = await response.json();
      if (!response.ok) {
        setError(payload.error ?? "密码修改失败");
        return;
      }
      onAuth(payload as AuthState);
      onClose();
    } catch {
      setError("无法连接到后端");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mg-motion-backdrop" style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,.45)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1100,
    }}>
      <div className="mg-motion-dialog" style={{
        background: "var(--surface)", borderRadius: "var(--radius-lg)", padding: "28px 32px",
        width: 390, maxWidth: "calc(100vw - 32px)", boxShadow: "var(--shadow-lg)",
      }}>
        {!editing ? (
          <>
            <h2 style={{ margin: 0, fontSize: 18 }}>建议修改初始密码</h2>
            <p style={{ color: "var(--muted)", fontSize: 13, lineHeight: 1.7 }}>
              当前仍在使用初始密码，建议尽快修改。
            </p>
            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
              <button className="mg-btn mg-btn-ghost" onClick={onClose}>暂不修改</button>
              <button className="mg-btn mg-btn-primary" onClick={() => setEditing(true)}>现在修改</button>
            </div>
          </>
        ) : (
          <>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <h2 style={{ margin: 0, fontSize: 18 }}>修改密码</h2>
              <button aria-label="关闭" className="mg-btn mg-btn-ghost" onClick={onClose}>×</button>
            </div>
            <label style={{ display: "block", marginTop: 18, fontSize: 13 }}>
              当前密码
              <input className="mg-input" type="password" autoComplete="current-password" value={currentPassword}
                onChange={(event) => setCurrentPassword(event.target.value)} style={{ width: "100%", marginTop: 6 }} />
            </label>
            <label style={{ display: "block", marginTop: 12, fontSize: 13 }}>
              新密码
              <input className="mg-input" type="password" autoComplete="new-password" value={newPassword}
                onChange={(event) => setNewPassword(event.target.value)} style={{ width: "100%", marginTop: 6 }} />
            </label>
            {error && <p role="alert" style={{ color: "var(--danger)", fontSize: 12 }}>{error}</p>}
            <button className="mg-btn mg-btn-primary" style={{ width: "100%", marginTop: 16 }} disabled={saving} onClick={submit}>
              {saving ? "修改中…" : "确认修改"}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

