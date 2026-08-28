import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft, ArrowRight, Check, CheckCircle2, CircleHelp,
  Copy, ExternalLink, Eye, EyeOff, FileText, KeyRound, Loader2,
  Network, ScanText, Share2, ShieldCheck, Sparkles, TriangleAlert, X,
} from "lucide-react";
import { apiUrl } from "~/api";
import type { LLMConfig } from "~/routes/home";
import { PROVIDER_GUIDES, type ProviderGuide } from "./apiProviderGuides";
import "./ApiSetupGuide.css";

export type ApiGuideStep = "intro" | "provider" | "chat" | "embedding" | "test";

const STEPS: Array<{ id: ApiGuideStep; short: string; title: string }> = [
  { id: "intro", short: "认识", title: "认识必需配置" },
  { id: "provider", short: "来源", title: "获取 API URL 和 Key" },
  { id: "chat", short: "LLM", title: "配置 LLM" },
  { id: "embedding", short: "Embedding", title: "配置 Embedding" },
  { id: "test", short: "测试", title: "测试并保存" },
];

const OFFICIAL_PROVIDER_GUIDES = PROVIDER_GUIDES.filter(
  (provider) => provider.category === "official",
);
const THIRD_PARTY_PROVIDER_GUIDES = PROVIDER_GUIDES.filter(
  (provider) => provider.category === "third-party",
);

type ValidationItem = {
  ok: boolean;
  code: string;
  message: string;
  latency_ms: number;
};

type ValidationState = {
  llm?: ValidationItem;
  embedding?: ValidationItem;
};
type ValidationTarget = "chat" | "embedding";

interface ApiSetupGuideProps {
  config: LLMConfig;
  step: ApiGuideStep;
  signedIn: boolean;
  onStepChange: (step: ApiGuideStep) => void;
  onClose: () => void;
  onComplete: (config: LLMConfig, profileName: string) => Promise<void>;
}

function createGuideDraft(config: LLMConfig): LLMConfig {
  return {
    ...config,
    api_key: "",
    embedding_api_key: "",
  };
}

function ConfigField({
  label, value, placeholder, onChange, secret = false, hint,
}: {
  label: string;
  value: string;
  placeholder: string;
  onChange: (value: string) => void;
  secret?: boolean;
  hint?: string;
}) {
  const [revealed, setRevealed] = useState(false);
  useEffect(() => {
    if (!value) setRevealed(false);
  }, [value]);
  return (
    <label className="api-guide-field">
      <span>{label}</span>
      <div className="api-guide-input-wrap">
        <input
          type={secret && !revealed ? "password" : "text"}
          value={value}
          placeholder={placeholder}
          autoComplete={secret ? "off" : undefined}
          spellCheck={false}
          onChange={(event) => onChange(event.target.value)}
        />
        {secret && Boolean(value) && (
          <button
            type="button"
            className="api-guide-reveal"
            aria-label={revealed ? "隐藏 API Key" : "显示 API Key"}
            onClick={() => setRevealed((current) => !current)}
          >
            {revealed ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        )}
      </div>
      {hint && <small>{hint}</small>}
    </label>
  );
}

function ResultCard({ label, result, testing }: {
  label: string;
  result?: ValidationItem;
  testing: boolean;
}) {
  const stateClass = testing ? "testing" : result?.ok ? "success" : result ? "error" : "idle";
  return (
    <div className={`api-guide-result ${stateClass}`}>
      <div className="api-guide-result-icon">
        {testing
          ? <Loader2 size={18} className="api-guide-spin" />
          : result?.ok
            ? <CheckCircle2 size={18} />
            : result
              ? <TriangleAlert size={18} />
              : <CircleHelp size={18} />}
      </div>
      <div>
        <strong>{label}</strong>
        <span>
          {testing
            ? "正在连接服务…"
            : result
              ? `${result.message}${result.latency_ms ? ` · ${result.latency_ms} ms` : ""}`
              : "等待测试"}
        </span>
      </div>
    </div>
  );
}

export function ApiSetupGuide({
  config, step, signedIn, onStepChange, onClose, onComplete,
}: ApiSetupGuideProps) {
  const dialogRef = useRef<HTMLElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const providerPanelRef = useRef<HTMLElement>(null);
  const providerTriggerRef = useRef<HTMLButtonElement>(null);
  const [draft, setDraft] = useState<LLMConfig>(() => createGuideDraft(config));
  const [sameProvider, setSameProvider] = useState(
    !config.embedding_url.trim() && !config.embedding_api_key.trim(),
  );
  const [activeProvider, setActiveProvider] = useState<ProviderGuide | null>(null);
  const [copiedField, setCopiedField] = useState("");
  const [copyMessage, setCopyMessage] = useState("");
  const [validation, setValidation] = useState<ValidationState | null>(null);
  const [testingTargets, setTestingTargets] = useState<Record<ValidationTarget, boolean>>({
    chat: false,
    embedding: false,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const testing = testingTargets.chat || testingTargets.embedding;

  useEffect(() => {
    setDraft(createGuideDraft(config));
    setSameProvider(!config.embedding_url.trim() && !config.embedding_api_key.trim());
  }, [
    config.api_key,
    config.api_url,
    config.embedding_api_key,
    config.embedding_model,
    config.embedding_url,
    config.model_name,
  ]);

  useEffect(() => {
    dialogRef.current?.focus();
  }, []);

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: 0 });
  }, [step]);

  useEffect(() => {
    if (activeProvider) {
      window.requestAnimationFrame(() => providerPanelRef.current?.focus());
    }
  }, [activeProvider]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !testing && !saving) {
        event.preventDefault();
        if (activeProvider) {
          setActiveProvider(null);
          setCopiedField("");
          setCopyMessage("");
          window.requestAnimationFrame(() => providerTriggerRef.current?.focus());
          return;
        }
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const dialog = activeProvider ? providerPanelRef.current : dialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      )).filter((element) => element.offsetParent !== null);
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [activeProvider, onClose, saving, testing]);

  const currentIndex = STEPS.findIndex((item) => item.id === step);
  const resolvedConfig = useMemo<LLMConfig>(() => ({
    ...draft,
    api_key: draft.api_key.trim() ? draft.api_key : config.api_key,
    embedding_url: sameProvider ? "" : draft.embedding_url,
    embedding_api_key: sameProvider
      ? ""
      : (draft.embedding_api_key.trim() ? draft.embedding_api_key : config.embedding_api_key),
  }), [config.api_key, config.embedding_api_key, draft, sameProvider]);
  const validationOk = Boolean(validation?.llm?.ok && validation?.embedding?.ok);
  const hasValidationFailure = Boolean(
    (validation?.llm && !validation.llm.ok)
    || (validation?.embedding && !validation.embedding.ok),
  );
  const chatConfigReady = Boolean(
    resolvedConfig.api_url.trim()
    && resolvedConfig.model_name.trim()
    && resolvedConfig.api_key.trim(),
  );
  const effectiveEmbeddingUrl = resolvedConfig.embedding_url.trim() || resolvedConfig.api_url.trim();
  const embeddingConfigReady = Boolean(
    effectiveEmbeddingUrl
    && resolvedConfig.embedding_model.trim()
    && (resolvedConfig.embedding_api_key.trim() || resolvedConfig.api_key.trim()),
  );
  const embeddingUrlSource = sameProvider || !resolvedConfig.embedding_url.trim()
    ? "沿用 LLM API URL"
    : "独立 API URL";
  const embeddingKeyStatus = sameProvider || !resolvedConfig.embedding_api_key.trim()
    ? "沿用 LLM Key"
    : "已配置独立 Key";
  const passedTestCount = Number(Boolean(validation?.llm?.ok))
    + Number(Boolean(validation?.embedding?.ok));

  const updateDraft = (patch: Partial<LLMConfig>) => {
    setDraft((current) => ({ ...current, ...patch }));
    setValidation(null);
    setError("");
  };

  const openProviderGuide = (provider: ProviderGuide, trigger: HTMLButtonElement) => {
    providerTriggerRef.current = trigger;
    setActiveProvider(provider);
    setCopiedField("");
    setCopyMessage("");
  };

  const closeProviderGuide = () => {
    setActiveProvider(null);
    setCopiedField("");
    setCopyMessage("");
    window.requestAnimationFrame(() => providerTriggerRef.current?.focus());
  };

  const copyPublicValue = async (field: string, label: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopiedField(field);
      setCopyMessage(`已复制${label}`);
    } catch {
      setCopiedField("");
      setCopyMessage("复制失败，请手动选择并复制公开字段。");
    }
  };

  const goNext = () => {
    if (step === "chat" && (!draft.api_url.trim() || !draft.model_name.trim() || !resolvedConfig.api_key.trim())) {
      setError("请先填写 LLM 的 API URL、模型名和 API Key。");
      return;
    }
    if (step === "embedding" && !draft.embedding_model.trim()) {
      setError("请填写 Embedding 模型的精确 ID。");
      return;
    }
    const next = STEPS[Math.min(STEPS.length - 1, currentIndex + 1)];
    onStepChange(next.id);
    setError("");
  };

  const runValidation = async (target: ValidationTarget) => {
    const chatReady = Boolean(
      resolvedConfig.api_url.trim()
      && resolvedConfig.model_name.trim()
      && resolvedConfig.api_key.trim(),
    );
    const embeddingReady = Boolean(
      resolvedConfig.embedding_model.trim()
      && (resolvedConfig.embedding_url.trim() || resolvedConfig.api_url.trim())
      && (resolvedConfig.embedding_api_key.trim() || resolvedConfig.api_key.trim()),
    );
    if (target === "chat" && !chatReady) {
      setError("请先填写完整的 LLM 配置。");
      onStepChange("chat");
      return;
    }
    if (target === "embedding" && !embeddingReady) {
      setError("请先填写完整的 Embedding 配置。");
      onStepChange("embedding");
      return;
    }

    const resultKey: keyof ValidationState = target === "chat" ? "llm" : "embedding";
    setTestingTargets((current) => ({ ...current, [target]: true }));
    setValidation((current) => current ? { ...current, [resultKey]: undefined } : current);
    setError("");
    try {
      const response = await fetch(apiUrl("/api/v2/config/validate"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...resolvedConfig, target }),
      });
      const body = await response.json().catch(() => ({})) as Partial<ValidationState> & { message?: string };
      if (!response.ok) {
        throw new Error(body.message || "配置验证请求失败，请检查填写内容。");
      }
      const result = body[resultKey];
      if (!result) throw new Error("配置验证服务未返回测试结果。");
      setValidation((current) => ({ ...(current ?? {}), [resultKey]: result }));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "无法连接到配置验证服务。");
    } finally {
      setTestingTargets((current) => ({ ...current, [target]: false }));
    }
  };

  const finish = async () => {
    if (!validationOk) return;
    setSaving(true);
    setError("");
    try {
      await onComplete(resolvedConfig, "自定义 API 配置");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "配置保存失败，请重试。");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="api-guide-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !testing && !saving) onClose();
    }}>
      <section
        ref={dialogRef}
        className="api-guide-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="api-guide-title"
        tabIndex={-1}
      >
        <header className="api-guide-header">
          <div>
            <span className="api-guide-eyebrow"><Sparkles size={13} />新手配置向导</span>
            <h2 id="api-guide-title">连接你的 AI 模型</h2>
            <p>跟着五步完成 LLM 与 Embedding 模型配置。</p>
          </div>
          <button className="api-guide-close" aria-label="关闭配置向导" onClick={onClose} disabled={testing || saving}>
            <X size={20} />
          </button>
        </header>

        <nav className="api-guide-steps" aria-label="配置步骤">
          {STEPS.map((item, index) => (
            <button
              key={item.id}
              className={`${item.id === step ? "active" : ""}${index < currentIndex ? " done" : ""}`}
              onClick={() => { onStepChange(item.id); setError(""); }}
            >
              <span>{index < currentIndex ? <Check size={13} /> : index + 1}</span>
              <strong>{item.short}</strong>
            </button>
          ))}
        </nav>

        <div ref={bodyRef} className="api-guide-body">
          <div className="api-guide-step-heading">
            <span>步骤 {currentIndex + 1} / {STEPS.length}</span>
            <h3>{STEPS[currentIndex]?.title}</h3>
          </div>

          {step === "intro" && (
            <div className="api-guide-intro">
              <section className="api-guide-intro-summary" aria-labelledby="api-guide-why-config">
                <span>先理解用途，再开始填写</span>
                <h4 id="api-guide-why-config">MathWeaver 为什么需要模型配置？</h4>
                <p>
                  MathWeaver 负责组织文档分析流程，但理解数学内容和计算语义关联需要调用模型服务。
                  完成配置，就是告诉系统“去哪里调用、使用哪个模型，以及使用哪张访问凭证”。
                  你不需要编程，后续只需从服务平台复制并粘贴对应信息。
                </p>
              </section>

              <div className="api-guide-concept-grid">
                <article>
                  <ScanText size={20} />
                  <h4>LLM</h4>
                  <p>负责理解数学文档，抽取定义、定理与证明结构，并为后续知识图谱生成节点。</p>
                </article>
                <article>
                  <Network size={20} />
                  <h4>Embedding 模型</h4>
                  <p>把文本转成数值向量，用于比较语义并发现节点间的潜在关系。</p>
                </article>
                <article>
                  <KeyRound size={20} />
                  <h4>URL、模型名与 Key</h4>
                  <p>URL 是服务地址，模型名指定具体能力，Key 则证明你有权调用该服务。</p>
                </article>
              </div>

              <section className="api-guide-workflow" aria-labelledby="api-guide-workflow-title">
                <div className="api-guide-workflow-heading">
                  <div>
                    <span>配置完成后的工作流程</span>
                    <h4 id="api-guide-workflow-title">一次分析中，它们如何配合？</h4>
                  </div>
                  <p>打开向导不会清空已选择的文件；模型配置用于建立后续分析所需的调用连接。</p>
                </div>
                <div className="api-guide-workflow-grid">
                  <article>
                    <div><FileText size={17} /><span>01</span></div>
                    <strong>读取文档</strong>
                    <p>载入 PDF、Markdown 或 LaTeX 内容。</p>
                  </article>
                  <article>
                    <div><ScanText size={17} /><span>02</span></div>
                    <strong>LLM 理解内容</strong>
                    <p>识别概念、陈述和证明结构。</p>
                  </article>
                  <article>
                    <div><Network size={17} /><span>03</span></div>
                    <strong>Embedding 计算关联</strong>
                    <p>比较节点语义，寻找潜在联系。</p>
                  </article>
                  <article>
                    <div><Share2 size={17} /><span>04</span></div>
                    <strong>生成知识图谱</strong>
                    <p>汇总节点与关系，供你继续检查和探索。</p>
                  </article>
                </div>
              </section>

              <div className="api-guide-intro-details">
                <section>
                  <span className="api-guide-intro-detail-label">开始前准备</span>
                  <h4>你只需要准备三样东西</h4>
                  <ul>
                    <li><Check size={13} />一个可使用 API 的模型服务账号</li>
                    <li><Check size={13} />可调用的 LLM 与 Embedding 模型</li>
                    <li><Check size={13} />为 MathWeaver 单独创建的限额 Key</li>
                  </ul>
                </section>
                <section>
                  <span className="api-guide-intro-detail-label">不用提前研究术语</span>
                  <h4>后续步骤会带你逐项完成</h4>
                  <p>
                    下一步会提供常见国内来源和官方入口；随后分别填写 LLM、Embedding 模型，
                    最后测试连接。支持两种模型来自同一服务，也支持分别配置。
                  </p>
                </section>
              </div>

              <div className="api-guide-note">
                <ShieldCheck size={17} />
                <p>
                  {signedIn
                    ? "完成后配置会同步到你的 MathWeaver 账号服务端。建议使用独立、限额的 API Key。"
                    : "完成后配置保存在当前浏览器中。建议使用独立、限额的 API Key。"}
                  不要在截图、群聊或文档中分享完整 Key。
                </p>
              </div>
            </div>
          )}

          {step === "provider" && (
            <div className="api-guide-provider-step">
              <div className="api-guide-provider-intro">
                <ShieldCheck size={18} />
                <div>
                  <strong>先在服务平台创建 Key，再回到 MathWeaver 手动填写</strong>
                  <p>下面只提供公开 URL、模型 ID 和官方入口。打开教程、复制字段或访问官网都不会修改你的现有配置。</p>
                </div>
              </div>

              <section className="api-guide-provider-section" aria-labelledby="official-provider-title">
                <div className="api-guide-provider-section-heading">
                  <div>
                    <span className="api-guide-provider-kicker">模型厂商官方接口</span>
                    <h4 id="official-provider-title">官方模型服务</h4>
                  </div>
                  <p>服务关系直接，价格、隐私与模型能力以各厂商官方说明为准。</p>
                </div>
                <div className="api-guide-provider-grid">
                  {OFFICIAL_PROVIDER_GUIDES.map((provider) => (
                    <article className="api-guide-provider-card" key={provider.id}>
                      <div className="api-guide-provider-card-top">
                        <strong>{provider.name}</strong>
                        <span className="api-guide-provider-badge official">官方</span>
                      </div>
                      <p>{provider.summary}</p>
                      <div className="api-guide-provider-capabilities">
                        {provider.capability === "chat-only"
                          ? <span className="chat-only">仅 LLM</span>
                          : <><span>LLM</span><span>Embedding</span></>}
                      </div>
                      <button
                        type="button"
                        className="api-guide-provider-open"
                        onClick={(event) => openProviderGuide(provider, event.currentTarget)}
                      >
                        查看获取教程 <ArrowRight size={15} />
                      </button>
                    </article>
                  ))}
                </div>
              </section>

              <section className="api-guide-provider-section third-party" aria-labelledby="third-party-provider-title">
                <div className="api-guide-provider-section-heading">
                  <div>
                    <span className="api-guide-provider-kicker">非模型厂商官方接口</span>
                    <h4 id="third-party-provider-title">第三方聚合服务</h4>
                  </div>
                  <p>使用前请自行确认第三方平台的数据处理、隐私政策、价格与模型可用性。</p>
                </div>
                <div className="api-guide-provider-grid third-party">
                  {THIRD_PARTY_PROVIDER_GUIDES.map((provider) => (
                    <article className="api-guide-provider-card third-party" key={provider.id}>
                      <div className="api-guide-provider-card-top">
                        <strong>{provider.name}</strong>
                        <span className="api-guide-provider-badge third-party">第三方平台</span>
                      </div>
                      <p>{provider.summary}</p>
                      <div className="api-guide-provider-capabilities">
                        <span>LLM</span>
                        <span>Embedding</span>
                      </div>
                      <button
                        type="button"
                        className="api-guide-provider-open"
                        onClick={(event) => openProviderGuide(provider, event.currentTarget)}
                      >
                        查看获取教程 <ArrowRight size={15} />
                      </button>
                    </article>
                  ))}
                </div>
              </section>
            </div>
          )}

          {step === "chat" && (
            <div className="api-guide-form">
              <ConfigField
                label="LLM API URL"
                value={draft.api_url}
                placeholder="例如：https://api.example.com/v1"
                onChange={(value) => updateDraft({ api_url: value })}
                hint="填写服务商的 API Base URL，不是官网或控制台网页地址。"
              />
              <ConfigField
                label="LLM 模型名"
                value={draft.model_name}
                placeholder="例如：text-model-id"
                onChange={(value) => updateDraft({ model_name: value })}
                hint="请从服务商模型目录复制精确 ID，不能填写营销展示名称。"
              />
              <ConfigField
                label="API Key"
                value={draft.api_key}
                placeholder="例如：sk-example-key"
                secret
                onChange={(value) => updateDraft({ api_key: value })}
                hint={config.api_key
                  ? "已有 Key 不会回显到教程中；只有粘贴新 Key 才会替换它。"
                  : "Key 类似密码。请勿截图或分享完整 Key。"}
              />
              <div className="api-guide-mistake">
                <TriangleAlert size={15} />
                常见错误：把 <code>https://example.com/console</code> 这类控制台地址当作 API URL。
              </div>
            </div>
          )}

          {step === "embedding" && (
            <div className="api-guide-form">
              <label className="api-guide-toggle-row">
                <input
                  type="checkbox"
                  checked={sameProvider}
                  onChange={(event) => {
                    const enabled = event.target.checked;
                    setSameProvider(enabled);
                    setValidation(null);
                    if (!enabled) {
                      setDraft((current) => ({
                        ...current,
                        embedding_url: current.embedding_url || current.api_url,
                        embedding_api_key: current.embedding_api_key || current.api_key,
                      }));
                    }
                  }}
                />
                <span>
                  <strong>与 LLM 使用同一服务和 Key</strong>
                  <small>大多数聚合服务可直接复用；不确定时先保留勾选。</small>
                </span>
              </label>
              {!sameProvider && (
                <>
                  <ConfigField
                    label="Embedding API URL"
                    value={draft.embedding_url}
                    placeholder="例如：https://embedding.example.com/v1"
                    onChange={(value) => updateDraft({ embedding_url: value })}
                  />
                  <ConfigField
                    label="Embedding API Key"
                    value={draft.embedding_api_key}
                    placeholder="例如：sk-example-embedding-key"
                    secret
                    onChange={(value) => updateDraft({ embedding_api_key: value })}
                    hint={config.embedding_api_key ? "已有 Embedding Key 不会回显到教程中。" : undefined}
                  />
                </>
              )}
              <ConfigField
                label="Embedding 模型名"
                value={draft.embedding_model}
                placeholder="例如：embedding-model-id"
                onChange={(value) => updateDraft({ embedding_model: value })}
                hint="Embedding 是独立模型类型，不能直接填写 LLM 模型名。"
              />
              <div className="api-guide-mistake">
                <TriangleAlert size={15} />
                提供 LLM 的服务不一定提供 Embedding；若只有这一项失败，请改用独立的 Embedding 服务。
              </div>
            </div>
          )}

          {step === "test" && (
            <div className="api-guide-test">
              <div className="api-guide-test-intro">
                <div>
                  <span>保存前最后一步</span>
                  <h4>先核对配置，再分别测试两项连接</h4>
                  <p>下方展示的是即将保存的实际配置。API Key 只显示配置状态，不会回显内容。</p>
                </div>
                <strong className={chatConfigReady && embeddingConfigReady ? "ready" : "missing"}>
                  {chatConfigReady && embeddingConfigReady ? "配置已填写完整" : "仍有配置待补充"}
                </strong>
              </div>

              <section className="api-guide-config-review" aria-labelledby="api-guide-config-review-title">
                <div className="api-guide-config-review-heading">
                  <div>
                    <span>当前设置</span>
                    <h4 id="api-guide-config-review-title">模型配置总览</h4>
                  </div>
                  <p>{sameProvider ? "Embedding 沿用 LLM 的服务与 Key" : "LLM 与 Embedding 分别配置"}</p>
                </div>
                <div className="api-guide-config-review-grid">
                  <article>
                    <header>
                      <div><ScanText size={17} /><strong>LLM 配置</strong></div>
                      <span className={chatConfigReady ? "ready" : "missing"}>
                        {chatConfigReady ? "已填写" : "待补充"}
                      </span>
                    </header>
                    <dl>
                      <div>
                        <dt>API URL</dt>
                        <dd><code>{resolvedConfig.api_url || "未填写"}</code></dd>
                      </div>
                      <div>
                        <dt>模型 ID</dt>
                        <dd><code>{resolvedConfig.model_name || "未填写"}</code></dd>
                      </div>
                      <div>
                        <dt>API Key</dt>
                        <dd className="api-guide-key-status">
                          <KeyRound size={13} />
                          {resolvedConfig.api_key.trim() ? "已配置，不显示内容" : "未填写"}
                        </dd>
                      </div>
                    </dl>
                  </article>

                  <article>
                    <header>
                      <div><Network size={17} /><strong>Embedding 配置</strong></div>
                      <span className={embeddingConfigReady ? "ready" : "missing"}>
                        {embeddingConfigReady ? "已填写" : "待补充"}
                      </span>
                    </header>
                    <dl>
                      <div>
                        <dt>API URL</dt>
                        <dd>
                          <code>{effectiveEmbeddingUrl || "未填写"}</code>
                          <small>{embeddingUrlSource}</small>
                        </dd>
                      </div>
                      <div>
                        <dt>模型 ID</dt>
                        <dd><code>{resolvedConfig.embedding_model || "未填写"}</code></dd>
                      </div>
                      <div>
                        <dt>API Key</dt>
                        <dd className="api-guide-key-status">
                          <KeyRound size={13} />
                          {embeddingKeyStatus}
                        </dd>
                      </div>
                    </dl>
                  </article>
                </div>
              </section>

              <div className="api-guide-test-info-grid">
                <article>
                  <ShieldCheck size={17} />
                  <div>
                    <strong>配置保存位置</strong>
                    <p>
                      {signedIn
                        ? "测试通过并点击保存后，将同步到你的 MathWeaver 账号服务端。"
                        : "测试通过并点击保存后，将保存在当前浏览器中。"}
                    </p>
                  </div>
                </article>
                <article>
                  <CheckCircle2 size={17} />
                  <div>
                    <strong>测试会检查什么</strong>
                    <p>LLM 需返回非空文本，Embedding 需返回非空数值向量；测试不会创建分析任务。</p>
                  </div>
                </article>
                <article>
                  <KeyRound size={17} />
                  <div>
                    <strong>费用与安全</strong>
                    <p>每项测试只发出一次极短请求，可能产生极少量费用；测试不会提前保存配置。</p>
                  </div>
                </article>
              </div>

              <section className="api-guide-connection-test" aria-labelledby="api-guide-connection-test-title">
                <div className="api-guide-connection-test-heading">
                  <div>
                    <span>连接验证</span>
                    <h4 id="api-guide-connection-test-title">分别测试 LLM 与 Embedding</h4>
                  </div>
                  <strong>{passedTestCount} / 2 已通过</strong>
                </div>
                <div className="api-guide-results">
                  <ResultCard label="LLM 连接" result={validation?.llm} testing={testingTargets.chat} />
                  <ResultCard label="Embedding 连接" result={validation?.embedding} testing={testingTargets.embedding} />
                </div>
                <div className="api-guide-test-actions">
                  <button
                    className="api-guide-test-button"
                    onClick={() => runValidation("chat")}
                    disabled={testingTargets.chat || saving}
                  >
                    {testingTargets.chat
                      ? <><Loader2 size={16} className="api-guide-spin" />正在测试 LLM…</>
                      : validation?.llm?.ok ? <><Check size={15} />重新测试 LLM</> : "测试 LLM"}
                  </button>
                  <button
                    className="api-guide-test-button"
                    onClick={() => runValidation("embedding")}
                    disabled={testingTargets.embedding || saving}
                  >
                    {testingTargets.embedding
                      ? <><Loader2 size={16} className="api-guide-spin" />正在测试 Embedding…</>
                      : validation?.embedding?.ok ? <><Check size={15} />重新测试 Embedding</> : "测试 Embedding"}
                  </button>
                </div>
                {hasValidationFailure && (
                  <div className="api-guide-fix-note">
                    <TriangleAlert size={15} />
                    <p>根据失败项检查 Key、精确模型 ID 与 API URL；Embedding 单独失败时，可返回上一步改用独立服务。</p>
                  </div>
                )}
                {validationOk && (
                  <div className="api-guide-test-complete">
                    <CheckCircle2 size={16} />
                    两项连接均已通过，可以点击“保存并完成”。
                  </div>
                )}
              </section>
            </div>
          )}

          {error && <div className="api-guide-error" role="alert">{error}</div>}
        </div>

        <footer className="api-guide-footer">
          <button
            className="api-guide-secondary"
            onClick={() => onStepChange(STEPS[Math.max(0, currentIndex - 1)].id)}
            disabled={currentIndex === 0 || testing || saving}
          >
            <ArrowLeft size={15} />上一步
          </button>
          {step === "test" ? (
            <button
              className="api-guide-primary"
              onClick={finish}
              disabled={testing || saving || !validationOk}
            >
              {saving
                ? <><Loader2 size={15} className="api-guide-spin" />保存中…</>
                : validationOk
                  ? <>保存并完成 <Check size={15} /></>
                  : "完成两项测试后保存"}
            </button>
          ) : (
            <button className="api-guide-primary" onClick={goNext}>
              下一步 <ArrowRight size={15} />
            </button>
          )}
        </footer>

        {activeProvider && (
          <div
            className="api-guide-provider-overlay"
            role="presentation"
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) closeProviderGuide();
            }}
          >
            <section
              ref={providerPanelRef}
              className="api-guide-provider-panel"
              role="dialog"
              aria-modal="true"
              aria-labelledby="api-guide-provider-panel-title"
              tabIndex={-1}
            >
              <header className="api-guide-provider-panel-header">
                <div>
                  <div className="api-guide-provider-panel-labels">
                    <span className={`api-guide-provider-badge ${activeProvider.category}`}>
                      {activeProvider.category === "official" ? "官方模型服务" : "第三方聚合服务"}
                    </span>
                    <span>文档核对日期：{activeProvider.verifiedAt}</span>
                  </div>
                  <h3 id="api-guide-provider-panel-title">{activeProvider.name}</h3>
                  <p>{activeProvider.summary}</p>
                </div>
                <button
                  type="button"
                  className="api-guide-provider-panel-close"
                  aria-label={`关闭 ${activeProvider.name} 获取教程`}
                  onClick={closeProviderGuide}
                >
                  <X size={19} />
                </button>
              </header>

              <div className="api-guide-provider-panel-body">
                <section
                  className="api-guide-provider-resource-bar"
                  aria-labelledby="api-guide-provider-resource-title"
                >
                  <div className="api-guide-provider-resource-heading">
                    <div>
                      <span>建议先从这里开始</span>
                      <h4 id="api-guide-provider-resource-title">平台官方入口</h4>
                    </div>
                    <p>创建 Key，并核对接入地址、模型与价格信息。</p>
                  </div>
                  <nav className="api-guide-provider-resources" aria-label={`${activeProvider.name} 官方资源`}>
                    {activeProvider.resources.map((resource) => (
                      <a
                        key={resource.url}
                        href={resource.url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {resource.label} <ExternalLink size={14} />
                      </a>
                    ))}
                  </nav>
                  <p className="api-guide-provider-external-note">
                    网页版将在新标签页打开；桌面版将交给系统默认浏览器。仅允许打开 HTTPS 链接。
                  </p>
                </section>

                <section className="api-guide-provider-tutorial" aria-labelledby="provider-tutorial-steps">
                  <h4 id="provider-tutorial-steps">获取步骤</h4>
                  <ol>
                    {activeProvider.steps.map((tutorialStep) => (
                      <li key={tutorialStep}>{tutorialStep}</li>
                    ))}
                  </ol>
                </section>

                <section className="api-guide-provider-values" aria-labelledby="provider-public-fields">
                  <div className="api-guide-provider-values-heading">
                    <div>
                      <h4 id="provider-public-fields">可复制的公开字段</h4>
                      <p>复制后请在后续配置步骤中手动粘贴。</p>
                    </div>
                    <span><KeyRound size={14} />Key 需在平台官网创建</span>
                  </div>
                  <div className="api-guide-provider-value-grid">
                    {[
                      { id: "url", label: "API URL", value: activeProvider.apiUrl },
                      { id: "chat", label: "LLM", value: activeProvider.chatModel },
                      ...(activeProvider.embeddingModel
                        ? [{ id: "embedding", label: "Embedding 模型", value: activeProvider.embeddingModel }]
                        : []),
                    ].map((field) => {
                      const fieldKey = `${activeProvider.id}-${field.id}`;
                      const copied = copiedField === fieldKey;
                      return (
                        <div className="api-guide-provider-value" key={field.id}>
                          <span>{field.label}</span>
                          <code>{field.value}</code>
                          <button
                            type="button"
                            onClick={() => copyPublicValue(fieldKey, field.label, field.value)}
                            aria-label={`复制${field.label}`}
                          >
                            {copied ? <Check size={14} /> : <Copy size={14} />}
                            {copied ? "已复制" : `复制${field.label}`}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                  <p className="api-guide-copy-status" aria-live="polite">{copyMessage}</p>
                </section>

                <div className="api-guide-provider-notes">
                  <TriangleAlert size={17} />
                  <div>
                    {activeProvider.notes.map((note) => <p key={note}>{note}</p>)}
                  </div>
                </div>
              </div>
            </section>
          </div>
        )}
      </section>
    </div>
  );
}
