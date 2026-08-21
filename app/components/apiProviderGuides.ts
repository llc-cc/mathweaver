export type ProviderGuideCategory = "official" | "third-party";

export interface ProviderGuideResource {
  label: string;
  url: string;
}

export interface ProviderGuide {
  id: string;
  name: string;
  category: ProviderGuideCategory;
  summary: string;
  apiUrl: string;
  chatModel: string;
  embeddingModel?: string;
  capability: "chat-only" | "chat-and-embedding";
  steps: string[];
  notes: string[];
  resources: ProviderGuideResource[];
  verifiedAt: string;
}

export const PROVIDER_GUIDES: ProviderGuide[] = [
  {
    id: "deepseek",
    name: "DeepSeek 官方",
    category: "official",
    summary: "DeepSeek 官方开放平台，提供其自研大模型的 API 接入服务。",
    apiUrl: "https://api.deepseek.com",
    chatModel: "deepseek-v4-flash",
    capability: "chat-only",
    steps: [
      "注册或登录 DeepSeek 开放平台。",
      "进入 API Key 页面，为 MathWeaver 创建独立的 API Key，并按需设置使用额度。",
      "从官方文档核对 API URL 与 LLM 模型 ID，妥善保存新创建的 Key。",
      "返回 MathWeaver，在下一步手动填写 LLM 配置。",
    ],
    notes: [
      "DeepSeek 在这里仅提供 LLM。配置 Embedding 时，请关闭“与 LLM 使用同一服务和 Key”。",
      "Embedding 可从千问／阿里云百炼、智谱 GLM 或 SiliconFlow 另行获取。",
    ],
    resources: [
      { label: "创建 API Key", url: "https://platform.deepseek.com/api_keys" },
      { label: "官方接入文档", url: "https://api-docs.deepseek.com/" },
      { label: "模型与价格", url: "https://api-docs.deepseek.com/quick_start/pricing" },
    ],
    verifiedAt: "2026-07-30",
  },
  {
    id: "qwen",
    name: "千问／阿里云百炼",
    category: "official",
    summary: "阿里云百炼模型服务平台，提供千问系列及多种大模型的开发接口。",
    apiUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    chatModel: "qwen-plus",
    embeddingModel: "text-embedding-v4",
    capability: "chat-and-embedding",
    steps: [
      "注册或登录阿里云，并进入百炼控制台。",
      "在目标地域创建独立的 API Key；创建成功时立即保存完整 Key 和页面显示的 API Host。",
      "从模型文档复制 LLM 与 Embedding 的精确模型 ID。",
      "返回 MathWeaver，在后续步骤手动填写对应字段。",
    ],
    notes: [
      "API Host 会随账号所选地域变化，请以创建 Key 时页面显示的地址为准。",
      "示例 URL 是 OpenAI 兼容协议的华北 2（北京）地址，其他地域不要直接照抄。",
    ],
    resources: [
      { label: "Key 获取说明", url: "https://help.aliyun.com/zh/model-studio/get-api-key/" },
      { label: "模型目录", url: "https://help.aliyun.com/zh/model-studio/models" },
      { label: "文本模型接口文档", url: "https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions" },
      { label: "Embedding 文档", url: "https://help.aliyun.com/zh/model-studio/embedding-interfaces-compatible-with-openai/" },
    ],
    verifiedAt: "2026-07-30",
  },
  {
    id: "zhipu",
    name: "智谱 GLM",
    category: "official",
    summary: "智谱官方开放平台，提供 GLM 系列大模型的开发接口与应用服务。",
    apiUrl: "https://open.bigmodel.cn/api/paas/v4/",
    chatModel: "glm-5.2",
    embeddingModel: "embedding-3",
    capability: "chat-and-embedding",
    steps: [
      "注册或登录智谱开放平台。",
      "在 API Key 管理页创建供 MathWeaver 使用的独立 Key，并设置合理的消费限额。",
      "从兼容接口与模型文档核对 API URL、LLM 和 Embedding 模型 ID。",
      "返回 MathWeaver，在后续步骤手动填写配置。",
    ],
    notes: [
      "模型 ID 必须与账号当前可调用的模型一致。",
      "不要把开放平台网页地址当成 API URL。",
    ],
    resources: [
      { label: "API Key 管理", url: "https://bigmodel.cn/usercenter/proj-mgmt/apikeys" },
      { label: "模型目录", url: "https://docs.bigmodel.cn/cn/guide/start/model-overview" },
      { label: "兼容接口文档", url: "https://docs.bigmodel.cn/cn/guide/develop/openai/introduction" },
      { label: "Embedding-3 文档", url: "https://docs.bigmodel.cn/cn/guide/models/embedding/embedding-3" },
    ],
    verifiedAt: "2026-07-30",
  },
  {
    id: "siliconflow",
    name: "SiliconFlow",
    category: "third-party",
    summary: "第三方大模型云服务平台，聚合并提供多种开源模型的 API 接入。",
    apiUrl: "https://api.siliconflow.cn/v1",
    chatModel: "deepseek-ai/DeepSeek-V4-Flash",
    embeddingModel: "Qwen/Qwen3-Embedding-8B",
    capability: "chat-and-embedding",
    steps: [
      "注册或登录 SiliconFlow。",
      "创建供 MathWeaver 使用的独立 API Key，并在平台侧设置可用额度。",
      "从模型目录核对当前可用的 LLM 与 Embedding 模型 ID。",
      "返回 MathWeaver，在后续步骤手动填写 URL、模型 ID 和 Key。",
    ],
    notes: [
      "这是第三方聚合平台，不是上述模型厂商的官方接口。",
      "数据处理、隐私政策、价格及模型可用性由第三方平台负责，请使用前自行确认。",
    ],
    resources: [
      { label: "创建 API Key", url: "https://cloud.siliconflow.cn/account/ak" },
      { label: "快速上手", url: "https://docs.siliconflow.cn/cn/userguide/quickstart" },
      { label: "模型目录", url: "https://cloud.siliconflow.cn/models" },
      { label: "Embedding 接口", url: "https://docs.siliconflow.cn/cn/api-reference/embeddings/create-embeddings" },
    ],
    verifiedAt: "2026-07-30",
  },
];
