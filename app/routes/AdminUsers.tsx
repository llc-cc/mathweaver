import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { apiUrl } from "~/api";
import type { AuthState } from "../auth-model";
import { authFetch, loadAuth, subscribeAuthInvalidated } from "./auth";

interface ImportErrorDetail {
  line: number;
  field: string;
  message: string;
}

interface GeneratedCredential {
  student_no: string;
  initial_password: string;
}

interface ImportResponse {
  created: number;
  generated_credentials: GeneratedCredential[];
  errors: ImportErrorDetail[];
}

export function AdminUsersLink({ auth }: { auth: AuthState | null }) {
  if (auth?.user.role !== "admin") return null;
  return <Link className="mg-btn mg-btn-ghost" to="/admin/users">用户管理</Link>;
}

async function importStudents(auth: AuthState, file: File) {
  const form = new FormData();
  form.append("file", file);
  return authFetch(apiUrl("/api/v2/admin/users/import"), { method: "POST", body: form }, auth.token);
}

async function resetPassword(auth: AuthState, userId: number) {
  return authFetch(apiUrl(`/api/v2/admin/users/${userId}/reset-password`), { method: "POST" }, auth.token);
}

async function updateUserStatus(auth: AuthState, userId: number, isActive: boolean) {
  return authFetch(apiUrl(`/api/v2/admin/users/${userId}/status`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_active: isActive }),
  }, auth.token);
}

export function credentialsCsv(credentials: GeneratedCredential[]) {
  const quote = (value: string) => {
    // Excel/WPS 会把这些前缀解释为公式；apostrophe 只写入下载副本，不改界面值。
    const neutralized = /^(?:[\u0000-\u001f]|[\u0000-\u0020]*[=+\-@])/.test(value) ? `'${value}` : value;
    return `"${neutralized.replaceAll('"', '""')}"`;
  };
  return ["student_no,initial_password", ...credentials.map((item) => (
    `${quote(item.student_no)},${quote(item.initial_password)}`
  ))].join("\r\n");
}

export default function AdminUsers() {
  const navigate = useNavigate();
  const [auth, setAuth] = useState<AuthState | null>(null);
  const [authHydrated, setAuthHydrated] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [importResult, setImportResult] = useState<ImportResponse | null>(null);
  const [credentialsUrl, setCredentialsUrl] = useState<string | null>(null);
  const [userIdText, setUserIdText] = useState("");
  const [temporaryPassword, setTemporaryPassword] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const invalidateAdminPage = useCallback(() => {
    // 外部请求判定会话失效时，一次性凭据必须与本页请求 401 一样立即销毁。
    setAuth(null);
    setFile(null);
    setImportResult(null);
    setCredentialsUrl(null);
    setUserIdText("");
    setTemporaryPassword(null);
    setMessage("");
    setError("");
    setBusy(false);
    navigate("/workspace", { replace: true });
  }, [navigate]);

  useEffect(() => () => {
    if (credentialsUrl) URL.revokeObjectURL(credentialsUrl);
  }, [credentialsUrl]);

  useEffect(() => {
    const current = loadAuth();
    setAuth(current);
    setAuthHydrated(true);
    if (!current || current.user.role !== "admin") navigate("/workspace", { replace: true });
  }, [navigate]);

  useEffect(() => subscribeAuthInvalidated(invalidateAdminPage), [invalidateAdminPage]);

  if (!authHydrated || !auth || auth.user.role !== "admin") return null;

  const handleUnauthorized = (response: Response) => {
    if (response.status !== 401) return false;
    invalidateAdminPage();
    return true;
  };

  const readJsonObject = async (response: Response): Promise<Record<string, unknown> | null> => {
    const value = await response.json().catch(() => null);
    return value && typeof value === "object" && !Array.isArray(value)
      ? value as Record<string, unknown>
      : null;
  };

  const isImportResponse = (value: Record<string, unknown> | null) => (
    !!value
    && typeof value.created === "number"
    && Array.isArray(value.generated_credentials)
    && value.generated_credentials.every((item) => (
      item && typeof item === "object"
      && typeof (item as Record<string, unknown>).student_no === "string"
      && typeof (item as Record<string, unknown>).initial_password === "string"
    ))
    && Array.isArray(value.errors)
    && value.errors.every((item) => (
      item && typeof item === "object"
      && typeof (item as Record<string, unknown>).line === "number"
      && typeof (item as Record<string, unknown>).field === "string"
      && typeof (item as Record<string, unknown>).message === "string"
    ))
  );

  const parseUserId = () => {
    const userId = Number(userIdText);
    if (!Number.isSafeInteger(userId) || userId <= 0) {
      setError("请输入有效的用户 ID");
      return null;
    }
    return userId;
  };

  const handleImport = async () => {
    if (!file) {
      setError("请选择 CSV 文件");
      return;
    }
    setBusy(true);
    setError("");
    setMessage("");
    setImportResult(null);
    setCredentialsUrl(null);
    try {
      const response = await importStudents(auth, file);
      if (handleUnauthorized(response)) return;
      const rawPayload = await readJsonObject(response);
      if (response.status === 403) {
        setError("权限不足，只有管理员可以导入学生");
        return;
      }
      if (!isImportResponse(rawPayload)) {
        setError(response.ok ? "导入响应格式错误" : String(rawPayload?.error ?? "学生导入失败"));
        return;
      }
      const payload = rawPayload as unknown as ImportResponse;
      setImportResult(payload);
      if (payload.generated_credentials.length > 0) {
        // 凭据只保留在当前页面内存中；离开页面立即撤销下载地址。
        const blob = new Blob([credentialsCsv(payload.generated_credentials)], { type: "text/csv;charset=utf-8" });
        setCredentialsUrl(URL.createObjectURL(blob));
      }
      if (response.ok) setMessage(`成功导入 ${payload.created} 名学生`);
    } catch {
      setError("无法连接到后端");
    } finally {
      setBusy(false);
    }
  };

  const handleReset = async () => {
    const userId = parseUserId();
    if (userId === null || !window.confirm(`确认重置用户 ${userId} 的密码？现有会话将失效。`)) return;
    setBusy(true);
    setError("");
    setMessage("");
    setTemporaryPassword(null);
    try {
      const response = await resetPassword(auth, userId);
      if (handleUnauthorized(response)) return;
      const payload = await readJsonObject(response);
      if (response.status === 403) {
        setError("权限不足，只有管理员可以重置密码");
        return;
      }
      if (!response.ok) {
        setError(String(payload?.error ?? "密码重置失败"));
        return;
      }
      if (typeof payload?.temporary_password !== "string") {
        setError("密码重置响应格式错误");
        return;
      }
      // 临时密码不持久化，只展示本次响应；下一次操作会覆盖它。
      setTemporaryPassword(payload.temporary_password);
    } catch {
      setError("无法连接到后端");
    } finally {
      setBusy(false);
    }
  };

  const handleStatus = async (isActive: boolean) => {
    const userId = parseUserId();
    if (userId === null) return;
    setBusy(true);
    setError("");
    setMessage("");
    setTemporaryPassword(null);
    try {
      const response = await updateUserStatus(auth, userId, isActive);
      if (handleUnauthorized(response)) return;
      const payload = await readJsonObject(response);
      if (response.status === 403) {
        setError("权限不足，只有管理员可以更新账号状态");
        return;
      }
      if (!response.ok) {
        setError(String(payload?.error ?? "账号状态更新失败"));
        return;
      }
      setMessage(`用户 ${userId} 已${isActive ? "启用" : "停用"}`);
    } catch {
      setError("无法连接到后端");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mg-root" style={{ minHeight: "100vh", padding: "32px 20px", background: "var(--bg)" }}>
      <section style={{ maxWidth: 860, margin: "0 auto" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24 }}>
          <div>
            <h1 style={{ margin: 0 }}>用户管理</h1>
            <p style={{ margin: "8px 0 0", color: "var(--muted)" }}>批量导入学生，并按用户 ID 重置密码或启停账号。</p>
          </div>
          <Link className="mg-btn mg-btn-ghost" to="/workspace">返回工作区</Link>
        </div>

        <section className="mg-card" style={{ padding: 24, marginBottom: 18 }}>
          <h2 style={{ marginTop: 0, fontSize: 17 }}>CSV 导入</h2>
          <label style={{ display: "block", fontSize: 13 }}>
            学生 CSV
            <input type="file" accept=".csv,text/csv" onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              style={{ display: "block", marginTop: 8 }} />
          </label>
          <button className="mg-btn mg-btn-primary" disabled={busy} onClick={handleImport} style={{ marginTop: 16 }}>导入学生</button>

          {importResult?.errors.length ? (
            <ul role="alert" style={{ color: "var(--danger)", paddingLeft: 20 }}>
              {importResult.errors.map((item, index) => (
                <li key={`${item.line}-${item.field}-${index}`}>第 {item.line} 行 · {item.field}：{item.message}</li>
              ))}
            </ul>
          ) : null}

          {importResult?.generated_credentials.length ? (
            <div style={{ marginTop: 18 }}>
              <strong>一次性生成凭据</strong>
              <p style={{ fontSize: 12, color: "var(--muted)" }}>请立即下载并妥善交付；离开页面后无法再次查看。</p>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <thead><tr><th align="left">学号</th><th align="left">初始密码</th></tr></thead>
                <tbody>{importResult.generated_credentials.map((item) => (
                  <tr key={item.student_no}><td>{item.student_no}</td><td>{item.initial_password}</td></tr>
                ))}</tbody>
              </table>
              {credentialsUrl && <a className="mg-btn mg-btn-primary" href={credentialsUrl} download="mathweaver-student-credentials.csv" style={{ marginTop: 12 }}>下载一次性凭据</a>}
            </div>
          ) : null}
        </section>

        <section className="mg-card" style={{ padding: 24 }}>
          <h2 style={{ marginTop: 0, fontSize: 17 }}>单个账号操作</h2>
          <p style={{ fontSize: 12, color: "var(--muted)" }}>当前后端没有用户列表接口，请输入明确的数字用户 ID。</p>
          <label style={{ display: "block", fontSize: 13 }}>
            用户 ID
            <input className="mg-input" inputMode="numeric" value={userIdText} onChange={(event) => setUserIdText(event.target.value)}
              style={{ display: "block", width: 240, marginTop: 8 }} />
          </label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 14 }}>
            <button className="mg-btn mg-btn-ghost" disabled={busy} onClick={handleReset}>重置密码</button>
            <button className="mg-btn mg-btn-primary" disabled={busy} onClick={() => handleStatus(true)}>启用账号</button>
            <button className="mg-btn mg-btn-ghost" disabled={busy} onClick={() => handleStatus(false)}>停用账号</button>
          </div>
          {temporaryPassword && (
            <div role="status" style={{ marginTop: 16, padding: 12, background: "var(--surface-alt)", borderRadius: 8 }}>
              一次性临时密码：<strong>{temporaryPassword}</strong>
            </div>
          )}
        </section>

        {message && <p role="status" style={{ color: "var(--success)", marginTop: 16 }}>{message}</p>}
        {error && <p role="alert" style={{ color: "var(--danger)", marginTop: 16 }}>{error}</p>}
      </section>
    </main>
  );
}

