import type { AuthState, LLMConfig, NodeLanguage, SavedSession, WorkspaceMode } from "./home";

// ── Auth helpers ──────────────────────────────────────────────────────────────

const AUTH_TOKEN_KEY = "mg_token";
const AUTH_EMAIL_KEY = "mg_email";
const AUTH_EDUCATION_ROLE_KEY = "mg_education_role";
const AUTH_CAN_TEACH_KEY = "mg_can_teach";
const SESSION_KEYS: Record<WorkspaceMode, string> = {
  generate: "mg_session_generate",
  import: "mg_session_import",
};
const LEGACY_SESSION_KEYS: Record<WorkspaceMode, string[]> = {
  generate: ["mg_session_pipeline", "mg_session"],
  import: ["mg_session_agent"],
};
const NODE_LANG_KEY = "mg_node_language";

export function loadAuth(): AuthState | null {
  if (typeof window === "undefined") return null;
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  const email = localStorage.getItem(AUTH_EMAIL_KEY);
  if (!token || !email) return null;
  const storedRole = localStorage.getItem(AUTH_EDUCATION_ROLE_KEY);
  const educationRole = storedRole === "teacher" || storedRole === "student" ? storedRole : null;
  const canTeach = localStorage.getItem(AUTH_CAN_TEACH_KEY) === "1";
  return { token, email, educationRole, canTeach };
}

export function saveAuth(token: string, email: string, educationRole?: "teacher" | "student" | null, canTeach?: boolean) {
  if (typeof window === "undefined") return;
  localStorage.setItem(AUTH_TOKEN_KEY, token);
  localStorage.setItem(AUTH_EMAIL_KEY, email);
  if (educationRole === "teacher" || educationRole === "student") localStorage.setItem(AUTH_EDUCATION_ROLE_KEY, educationRole);
  else localStorage.removeItem(AUTH_EDUCATION_ROLE_KEY);
  if (canTeach) localStorage.setItem(AUTH_CAN_TEACH_KEY, "1");
  else localStorage.removeItem(AUTH_CAN_TEACH_KEY);
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
  localStorage.removeItem(AUTH_TOKEN_KEY);
  localStorage.removeItem(AUTH_EMAIL_KEY);
  localStorage.removeItem(AUTH_EDUCATION_ROLE_KEY);
  localStorage.removeItem(AUTH_CAN_TEACH_KEY);
}

const MD_PREFIX = "mg_md_";
const MD_INDEX_KEY = "mg_md_index"; // ordered list of jobIds with saved markdown
const MD_MAX_ENTRIES = 10;

export function saveMd(jobId: string, markdown: string) {
  if (typeof window === "undefined") return;
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
  try {
    return localStorage.getItem(MD_PREFIX + jobId) ?? undefined;
  } catch { return undefined; }
}

export function loadSession(mode: WorkspaceMode): SavedSession | null {
  if (typeof window === "undefined") return null;
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

const EMPTY_LLM_CONFIG: LLMConfig = {
  api_url: "",
  model_name: "",
  api_key: "",
  embedding_url: "",
  embedding_model: "",
  embedding_api_key: "",
};

export function loadLlm(): LLMConfig {
  try {
    const raw = localStorage.getItem(LLM_KEY);
    return raw ? { ...EMPTY_LLM_CONFIG, ...JSON.parse(raw) } : { ...EMPTY_LLM_CONFIG };
  } catch { return { ...EMPTY_LLM_CONFIG }; }
}

export function saveLlm(c: LLMConfig) {
  try { localStorage.setItem(LLM_KEY, JSON.stringify(c)); } catch { /* quota */ }
}
