import type { AuthState } from "../auth-model";
import { isDesktopRuntime } from "../runtime";
import type { LLMConfig, NodeLanguage, SavedSession, WorkspaceMode } from "./home";

// ── Auth helpers ──────────────────────────────────────────────────────────────

const AUTH_KEY = "mg_auth";
const LEGACY_AUTH_KEYS = ["mg_token", "mg_email"] as const;
const SESSION_KEYS: Record<WorkspaceMode, string> = {
  generate: "mg_session_generate",
  import: "mg_session_import",
};
const LEGACY_SESSION_KEYS: Record<WorkspaceMode, string[]> = {
  generate: ["mg_session_pipeline", "mg_session"],
  import: ["mg_session_agent"],
};
const NODE_LANG_KEY = "mg_node_language";
const WEB_SENSITIVE_KEYS = [
  "mg_session_generate",
  "mg_session_import",
  "mg_session_pipeline",
  "mg_session",
  "mg_session_agent",
  "mg_llm_config",
] as const;
const WEB_SENSITIVE_PREFIXES = ["mg_md_", "proof_workspace:"] as const;
export const AUTH_INVALIDATED_EVENT = "mathweaver:auth-invalidated";
let authGeneration = 0;

export interface AuthRequestIdentity {
  generation: number;
  token: string | null;
  userId: number | null;
}

export function clearWebSensitiveStorage() {
  if (typeof window === "undefined" || isDesktopRuntime()) return;
  WEB_SENSITIVE_KEYS.forEach((key) => localStorage.removeItem(key));
  // Web 升级时删除旧版无账号命名空间的原文和证明草稿，绝不迁移给当前账号。
  for (let index = localStorage.length - 1; index >= 0; index -= 1) {
    const key = localStorage.key(index);
    if (key && WEB_SENSITIVE_PREFIXES.some((prefix) => key.startsWith(prefix))) {
      localStorage.removeItem(key);
    }
  }
}

export function loadAuth(): AuthState | null {
  if (typeof window === "undefined") return null;
  clearWebSensitiveStorage();
  const hasLegacyAuth = LEGACY_AUTH_KEYS.some((key) => localStorage.getItem(key) !== null);
  if (hasLegacyAuth) {
    LEGACY_AUTH_KEYS.forEach((key) => localStorage.removeItem(key));
    localStorage.removeItem(AUTH_KEY);
    return null;
  }
  try {
    const raw = localStorage.getItem(AUTH_KEY);
    if (!raw) return null;
    const auth = JSON.parse(raw) as Partial<AuthState>;
    const user = auth.user;
    if (
      typeof auth.token !== "string" || !auth.token
      || !user || typeof user.id !== "number"
      || typeof user.display_name !== "string"
      || !["student", "teacher", "admin"].includes(user.role ?? "")
      || typeof user.initial_password_pending !== "boolean"
      || !(typeof user.student_no === "string" || user.student_no === null)
      || !(typeof user.email === "string" || user.email === null)
    ) {
      localStorage.removeItem(AUTH_KEY);
      return null;
    }
    return auth as AuthState;
  } catch {
    localStorage.removeItem(AUTH_KEY);
    return null;
  }
}

export function saveAuth(auth: AuthState) {
  if (typeof window === "undefined") return;
  authGeneration += 1;
  clearWebSensitiveStorage();
  localStorage.setItem(AUTH_KEY, JSON.stringify(auth));
  LEGACY_AUTH_KEYS.forEach((key) => localStorage.removeItem(key));
}

export function loadNodeLanguage(): NodeLanguage {
  try {
    const v = localStorage.getItem(NODE_LANG_KEY);
    return (["zh", "en", "bilingual"].includes(v ?? "") ? v : "bilingual") as NodeLanguage;
  } catch { return "bilingual"; }
}

export function saveNodeLanguage(lang: NodeLanguage) {
  try { localStorage.setItem(NODE_LANG_KEY, lang); } catch { /* quota */ }
}

export function clearAuth() {
  if (typeof window === "undefined") return;
  authGeneration += 1;
  localStorage.removeItem(AUTH_KEY);
  LEGACY_AUTH_KEYS.forEach((key) => localStorage.removeItem(key));
  clearWebSensitiveStorage();
}

function invalidateAuth() {
  clearAuth();
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(AUTH_INVALIDATED_EVENT));
  }
}

export function clearAuthAndNotify() {
  // 主动退出与服务端 401 使用同一失效信号，确保 Provider 一并丢弃前一账号的内存任务。
  invalidateAuth();
}

export function subscribeAuthInvalidated(listener: () => void) {
  if (typeof window === "undefined") return () => undefined;
  window.addEventListener(AUTH_INVALIDATED_EVENT, listener);
  return () => window.removeEventListener(AUTH_INVALIDATED_EVENT, listener);
}

export function captureAuthRequestIdentity(explicitToken?: string): AuthRequestIdentity {
  const current = loadAuth();
  const token = explicitToken ?? current?.token ?? null;
  return {
    generation: authGeneration,
    token,
    userId: current?.token === token ? current.user.id : null,
  };
}

export function isAuthRequestIdentityCurrent(identity: AuthRequestIdentity) {
  const current = loadAuth();
  // generation 同时覆盖退出后同 token 重登的极端情况，不能只比较 token 文本。
  return authGeneration === identity.generation
    && (current?.token ?? null) === identity.token
    && (current?.user.id ?? null) === identity.userId;
}

export async function protectedFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
  explicitToken?: string,
) {
  const token = explicitToken ?? loadAuth()?.token;
  if (!token && !isDesktopRuntime()) {
    // Web 端缺少会话时不发送必然失败的请求，统一触发重新登录流程。
    invalidateAuth();
    return new Response(JSON.stringify({ error: "authentication required" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(input, { ...init, headers });
  if (response.status === 401 && loadAuth()?.token === token) invalidateAuth();
  return response;
}

export async function authFetch(input: RequestInfo | URL, init: RequestInit = {}, token: string) {
  return protectedFetch(input, init, token);
}

const MD_PREFIX = "mg_md_";
const MD_INDEX_KEY = "mg_md_index"; // ordered list of jobIds with saved markdown
const MD_MAX_ENTRIES = 10;

export function saveMd(jobId: string, markdown: string) {
  if (typeof window === "undefined") return;
  if (!isDesktopRuntime()) {
    clearWebSensitiveStorage();
    return;
  }
  try {
    localStorage.setItem(MD_PREFIX + jobId, markdown);
    // maintain index, evict oldest when over limit
    const raw = localStorage.getItem(MD_INDEX_KEY);
    const index: string[] = raw ? JSON.parse(raw) : [];
    const next = [jobId, ...index.filter(id => id !== jobId)];
    if (next.length > MD_MAX_ENTRIES) {
      const evicted = next.splice(MD_MAX_ENTRIES);
      evicted.forEach(id => localStorage.removeItem(MD_PREFIX + id));
    }
    localStorage.setItem(MD_INDEX_KEY, JSON.stringify(next));
  } catch { /* quota */ }
}

export function loadMd(jobId: string): string | undefined {
  if (typeof window === "undefined") return undefined;
  if (!isDesktopRuntime()) {
    clearWebSensitiveStorage();
    return undefined;
  }
  try {
    return localStorage.getItem(MD_PREFIX + jobId) ?? undefined;
  } catch { return undefined; }
}

export function loadSession(mode: WorkspaceMode): SavedSession | null {
  if (typeof window === "undefined") return null;
  if (!isDesktopRuntime()) {
    clearWebSensitiveStorage();
    return null;
  }
  try {
    const currentKey = SESSION_KEYS[mode];
    const sourceKey = [currentKey, ...LEGACY_SESSION_KEYS[mode]]
      .find((key) => localStorage.getItem(key) !== null);
    const raw = sourceKey ? localStorage.getItem(sourceKey) : null;
    if (!raw) return null;
    const s: SavedSession = JSON.parse(raw);
    const legacySourceMode = (s.result as unknown as { source_mode?: string }).source_mode;
    s.result.source_mode = legacySourceMode === "pipeline"
      ? "generate"
      : legacySourceMode === "agent"
        ? "import"
        : legacySourceMode === "generate" || legacySourceMode === "import"
          ? legacySourceMode
          : mode;
    if (s.filename === "Agent 导入结果") {
      s.filename = "导入已有图谱";
    }
    if (s.jobId && !s.sourceMarkdown) {
      s.sourceMarkdown = loadMd(s.jobId);
    }
    if (sourceKey !== currentKey) {
      const { sourceMarkdown, ...rest } = s;
      localStorage.setItem(currentKey, JSON.stringify(rest));
    }
    return s;
  } catch { return null; }
}

export function saveSession(mode: WorkspaceMode, s: SavedSession) {
  if (typeof window === "undefined") return;
  if (!isDesktopRuntime()) {
    clearWebSensitiveStorage();
    return;
  }
  try {
    const { sourceMarkdown, ...rest } = s;
    localStorage.setItem(SESSION_KEYS[mode], JSON.stringify(rest));
    if (sourceMarkdown && s.jobId) {
      saveMd(s.jobId, sourceMarkdown);
    }
  } catch { /* quota */ }
}

export function clearSession(mode: WorkspaceMode) {
  if (typeof window === "undefined") return;
  localStorage.removeItem(SESSION_KEYS[mode]);
  LEGACY_SESSION_KEYS[mode].forEach((key) => localStorage.removeItem(key));
}

export function authHeaders(token: string) {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

// ── LLM config persistence ───────────────────────────────────────────────────

const LLM_KEY = "mg_llm_config";

export const EMPTY_LLM_CONFIG: LLMConfig = {
  api_url: "",
  model_name: "",
  api_key: "",
  embedding_url: "",
  embedding_model: "",
  embedding_api_key: "",
};

export function loadLlm(): LLMConfig {
  if (!isDesktopRuntime()) {
    clearWebSensitiveStorage();
    return { ...EMPTY_LLM_CONFIG };
  }
  try {
    const raw = localStorage.getItem(LLM_KEY);
    return raw ? { ...EMPTY_LLM_CONFIG, ...JSON.parse(raw) } : { ...EMPTY_LLM_CONFIG };
  } catch { return { ...EMPTY_LLM_CONFIG }; }
}

export function saveLlm(c: LLMConfig) {
  if (!isDesktopRuntime()) {
    clearWebSensitiveStorage();
    return;
  }
  try { localStorage.setItem(LLM_KEY, JSON.stringify(c)); } catch { /* quota */ }
}
