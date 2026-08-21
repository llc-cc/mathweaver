# MathGraph 改动记录

---

## [当前版本，未推送] 2026-06

### 界面动效 + 健壮性打磨（2026-06-11）

#### 全局动效系统
- 新增 `mg-motion-*` 动效类：弹窗 / 抽屉 / 手风琴统一入场动画 + 柔性退场（`closing` 态延迟 140ms 卸载，不再瞬切）
- 接入范围：登录注册弹窗、账号设置弹窗、历史记录抽屉、删除确认框、后台任务卡、LLM 配置折叠区、错误详情展开

#### 网络健壮性（针对国内网络）
- **Google Fonts 非阻塞加载**（`root.tsx`）：原 `<link rel=stylesheet>` 会阻塞渲染，国内网络下载不通时整页白屏卡顿约 30s；改为 `preload as=style` + onload 提升为 stylesheet，首屏立即用系统字体兜底，Web 字体到达后无感替换
- **请求超时**：历史面板 `fetchWithTimeout` 包裹，后端不可达时明确反馈而非无限等待
- **导出健壮性**：导出前校验 `jobId`，失败时区分 HTTP 错误并提示「后端可能已重启、内存结果已失效」

#### SSR 水合修复
- `auth` / `LLM 配置` 改为挂载后（`useEffect`）再从 localStorage 注入，消除服务端渲染与客户端首帧不一致（hydration mismatch）

#### 渲染容错
- `math.tsx`：`parseMathSegments` 识别未闭合 / 被截断的数学定界符（`$ $$ \( \[ \begin{}`），优雅丢弃半截公式，不再把原始 LaTeX 源码直接吐到界面
- `asText`：节点字段为对象 / 数组时按 `text/statement/content/title` 优先级取值，不再渲染出 `[object Object]` 或 JSON

#### 图谱布局
- `studio-graph.ts`：阅读顺序改为严格跟随文档序的蛇形（boustrophedon）网格；泳道布局按类型分列、列内按文档序堆叠
- GraphStudio：悬浮提示智能定位（不溢出视口）、左栏拖拽把手二态吸附、深色模式同色系边配色
- 历史记录可直接载入 GraphStudio（`StudioWrapper` 透传 `token` / `onLoadHistory`）

### Backend 健壮性与诊断
- **history 保存去重按用户隔离**（`api_v2.py`）：去重查询加 `user_id` 条件，并在触碰内存 `_jobs` 前提前返回，避免跨用户 / 重复保存
- **LLM 调用诊断日志**（`llm.py`）：HTTP 失败打印 model / status / 耗时 / 响应体片段；无 `choices` 时打印响应；`LLM_DEBUG` 开启成功日志
- **extract_statements 丢块诊断**（`stage.py`）：对照输入块报告哪些块在提取阶段被静默丢弃（节点数骤降、每次结果不同的直接原因）
- 新增 `backend/scripts/diagnose_extraction.py`：量化 extract_statements 的 LLM 失败率，区分「网络问题」与「模型输出格式问题」并对比模型
- 新增 `AGENTS.md`：与 `CLAUDE.md` 同源的 LLM 编码行为规范

### GraphStudio — 自然语言图谱模式重构（新模式，与经典版并存）

> 设计文档：`docs/graphstudio.md`；安全审查：`SECURITY_REVIEW.md`；交接：`HANDOFF_STUDIO.md`；分支：`studio-redesign`

- **架构**：新增独立 GraphStudio 体验，与原 `ResultScreen`（经典版）并存，经典路径零改动；顶栏一键切换并持久化（`localStorage["mathgraph.experience"]`，默认 studio）。新文件 `app/routes/studio-graph.ts` / `graphstudio.css` / `GraphStudio.tsx`；`markdown.tsx` 导出 `MdBlock`
- **视觉/深色**：暖纸中性 + 衬线标题 + 发丝描边（近 Claude 风）；`theme: light/dark/auto` 切换并持久化
- **节点呈现**：重要性评分（度中心性 + 类型权重 + 编号）驱动大小；**信息密度(LOD)滑块**只标注高重要度节点、次要节点退化为彩色小点；标签优先信息性标题；缩放簇取代原「像素高度」滑块，适应缩放设下限保证可读
- **图谱顺序（多方案可选）**：`阅读顺序`(蛇形文档序) / `类型泳道` / `依赖层次`(深度+层标) / `关系网络`(力导向)
- **跳转+召回**：`buildAnchorIndex` 以后端 `source_text` 归一化包含匹配重建 node↔原文段落映射（标号/数学标题/surface_anchor/内容指纹兜底），侧栏显示召回覆盖率（Evans5 实测 100%）；点节点↔点原文双向联动
- **依赖关系细化**：`classifyEdge` 归类推导/使用/特化/推广/等价/定义引用/反例/举例/相关；边按类型着色+图例；标签空间有限故悬浮/选中才显示，节点抽屉按类型枚举进/出依赖
- **其它**：搜索框(`/`)取代冗余「图谱+节点」视图；聚焦模式；个性化设置弹层(持久化)；本地 dev fixture 加载器 `/workspace?fixture=NAME`（仅 localhost）
- **安全**：移除 `app/config.ts` 硬编码 Neo4j 明文凭据（改 `VITE_NEO4J_*` 环境变量）；⚠ 密码已进 git 历史，**必须轮换**（见 `SECURITY_REVIEW.md`）

### Landing Page 视觉重构（学术衬线 · 暖灰底）

#### 设计系统
- `landing.css` 全量 token 化：暖灰底 `--bg:#faf9f7`、暖黑 `--ink`、暖描边 `--line`、学术蓝 `--accent:#1e5aa8` + 暖金点缀 `--gold:#b08542`，圆角/阴影/字体全套变量
- 双字体系统：标题与大号数字用衬线 `Source Serif 4` + 中文 `Noto Serif SC`（Google Fonts），正文/UI 保留 `Inter`
- 冷灰蓝色系整体转暖，所有硬编码色值替换为 token

#### 解决四大诉求
- **字小/挤边**：小字地板整体抬高（卡片正文 13→14/15、标签 11→12、metrics 10→13、compare 13→14、report 11→13、阶段卡标题 16→17）；卡片 padding 普遍加大
- **框锐利粗糙**：goal/stage/journey/report 从「硬密铺格子+1px 分隔线」改为 `gap` 间隔独立卡片（14–18px 圆角 + 分层柔和阴影 + hover 升起）；compare/demo-shell/mode-panel 同步圆角化
- **字体单一**：衬线标题 + Inter 正文配对
- **不够高级**：暖灰+学术蓝+暖金冷暖对照、统一 `.18s` 过渡、demo 图谱配色转暖
- 标题孤字换行修复：`text-wrap: balance`（标题与 section 描述均分行）

#### 首屏交互（A 活体图谱 + B 粒子星座 + D 涌现入场）
- **D 涌现**：加载时节点散开 → 弹性收拢归位的入场动画（`stabilized` 后散布 + `startSimulation` 收敛）
- **A 活体**：鼠标弹性力场（`moveNode` 斥开 + 物理回弹）、持续轻微漂浮、hover 高亮、**点击节点弹出定义/定理迷你卡**；调校惯性手感（`damping:0.385`、`minVelocity:0.6`、`maxVelocity:16`），鼠标停止后节点继续滑行又不至于过飘
- **B 粒子**：背景独立 canvas 粒子星座（~78 粒子漂移 + 邻近连线 + 鼠标牵引发亮 + ∫∑∂∇∈ 数学符号），暖金/学术蓝点缀
- 封面图谱锁定缩放/平移（`zoomView/dragView: false`），尺寸固定并 `moveTo({scale:1.45})` 放大约 45%
- Demo 交互图谱限制缩放上下界（`zoom` 事件钳制 scale 到 `[0.55, 2.2]`），避免误操作缩放到无法恢复
- 层级：粒子(z0) → 图谱(z1) → wash(z2，`pointer-events:none` 点击穿透) → 文案(z4)

#### 页脚 License 落款
- 重做 footer：品牌行 + 导航链接 + 分隔线 + 法律行
- `© <year> 上海交通大学 AI4Math 课题组 · 保留所有权利` ｜ `本项目仅供学术研究与教学用途`

---

## [3f5a1f9] 2026-06-10

### 前端重构

#### home.tsx 模块拆分（3332 → 2497 行）
- 新增 `app/routes/math.tsx`：KaTeX 渲染层（`preprocessLatex`、`parseMathSegments`、`MathText`、`SmartTitle`、`KATEX_MACROS`）
- 新增 `app/routes/markdown.tsx`：Markdown 解析 + 文本-图谱双向锚点全套逻辑（`parseMdBlocks`、`MarkdownViewer`、`LinkedMarkdownViewer`、`contentFp`、text-anchor 工具函数）
- 新增 `app/routes/auth.ts`：所有 localStorage 持久化函数（auth、session、LLM config、markdown cache）
- 新增 `app/routes/AuthModal.tsx`：登录/注册弹窗组件
- 新增 `app/routes/HistoryPanel.tsx`：历史记录面板组件
- 新增 `app/routes/ProcessingScreen.tsx`：处理进度界面组件
- home.tsx 类型定义全部加 `export`，子模块按需 import type；constants/graph helpers/ResultScreen 留原位

### Bug 修复

#### 双向跳转链接过度噪声
- 移除 Strategy 1（title 正则全文扫描）：之前所有包含节点名的词（"space"、"function"、"Sobolev" 等）在全文每处出现都变成下划线链接
- 改为**整段 onClick**：只有节点的源文本块（content 指纹匹配 / 编号标签匹配）才整段可点击
- 视觉改为左侧蓝色竖线 + hover 浅蓝背景（`mg-block-anchor`），去掉词级下划线

#### 图谱同名节点重复
- 新增 `dedupeGraph()`：按 `title_zh || title_en` 去重，同名节点合并到第一个，关联边端点重映射，自环和重复边自动过滤
- 四处 `setResult` 调用统一过滤（包括 session 恢复、error partial、FloatingBadge 入口）

### Landing Page 优化
- 全局字体上调：正文卡片 11px → 13px，标签/标注 9-10px → 11-12px，导航 12px → 13px，section 描述 14px → 15px
- Hero h1 下方间距 12px → 18px（大标题下沿贴死副标题修复）
- Edge flow 箭头旋转 90°（纵向布局中箭头指向改为朝下，语义正确）
- Agent Mode 面板描述补充使用场景句，与 Pipeline Mode 形成对照

### 工程改进
- `.gitignore`：`*.stderr.log` / `*.stdout.log` 加入忽略列表；`.claude/` 改为 `/.claude/`（不再误屏蔽 backend/.claude/skills）

### Backend 新增（从 feature/agent cherry-pick）

#### Claude CLI 批量引擎
- 新增 `backend/pipeline/common/claude_cli_engine.py`（454 行）：支持 checkpoint、重试、超时的 Claude Code CLI 批量调用引擎

#### Main Agent 控制器
- 新增 `backend/pipeline/main_agent/` 包：
  - `control.py`（1198 行）：完整 pipeline agent 控制流程
  - `toolkit.py`（1256 行）：agent 工具集
  - `extract_statements_repair.py`（623 行）：语句提取修复逻辑
- `backend/pipeline/context.py` 新增 7 个 Claude CLI 配置字段（`llm_engine`、`claude_command`、`claude_model` 等）

#### MathKG Skill 文档
- 新增 `backend/.claude/skills/mathkg-process/` 和 `backend/.codex/skills/mathkg-process/`：Claude Code / Codex 的 pipeline 调度 skill 文档

### 合并上游（git pull --ff-only from origin/main）

#### Agent Import API（队友提交）
- 新增后端路由 `/api/v2/agent-import`：接受 nodes.json + edges.json + markdown 直接生成 job，绕过流水线
- history 表新增 `source_markdown` 列
- `_normalize_nodes` / `_normalize_edges` 增强：同时支持旧版（中文字段）和新版（英文字段）两种 JSON 格式
- 新增 `_as_item_list`、`_first_value` 工具函数；`_normalize_edges` 支持返回 `warnings`

#### Landing Page（队友提交）
- 新增产品主页 `app/routes/landing.tsx` + `landing.css`：带交互式 Demo 图谱的产品介绍页
- 新增 `app/routes/mathgraph.css`
- `/` 路由改为 landing page，原应用入口变为 `/home`

---

## [当前版本（未推送）→ b7efad9] 2026-06

### 新功能

#### 文字 ↔ 图谱双向跳转（md-graph 布局）
- 原文中节点关键词自动蓝色高亮下划线标注
- **聚焦图谱模式**：点击高亮词 → 右侧图谱动画聚焦到对应节点
- **悬浮卡片模式**：点击高亮词 → 文字上方弹出词典式卡片，含节点摘要和"在图谱中查看"按钮
- **反向跳转**：点击图谱节点 → 左侧原文滚动到对应段落并闪烁高亮
- 左上角切换按钮：聚焦图谱 / 悬浮卡片
- 4 层匹配策略（覆盖率大幅提升）：
  1. 正则 anchor（label/title 文字匹配）
  2. 段落开头关键字 + 编号精确匹配（词边界、前 80 字符）
  3. LaTeX 归一化精确匹配（最小长度 5）
  4. content 指纹匹配（45 字指纹，覆盖无编号节点）

#### 节点语言模式
- 设置中新增"节点语言"选项：中文 / 双语 / English
- 控制图谱节点标签和详情面板标题语言（数学公式和原文不受影响）
- 持久化存 localStorage，刷新不丢失

#### 文档序新视图
- 替换"线性"按钮，新增"文档序"布局模式（`docorder`）
- Top-Bottom 方向：深度从上到下（Y 轴），同层按文档顺序从左到右（X 轴）
- 左侧竖向 lane strip 显示深度标签

#### 层次布局稳定化
- 改用手动固定坐标（`computeManualPositions`），不再使用 vis-network 自动 hierarchical 布局
- 过滤节点类型时，其他节点位置完全不变、不缩放、不重排
- 同层内节点按原文出现顺序（`node_index_in_doc`）排列

#### 旧历史记录自动恢复原文
- 打开历史记录时，若 localStorage 无原文，自动从服务器磁盘找回并写入
- 新增后端端点 `/api/v2/history/<id>/markdown`

#### 结果页 Settings 齿轮常驻
- 登录状态下结果页 header 始终显示 ⚙ 按钮，随时修改语言和 LLM 配置

#### 历史记录删除确认弹窗
- 点击"删除"先弹出确认框（取消 / 红色删除），防止误删

### Bug 修复

- **右边栏无法滚动**：wrapper div 缺少 `display: flex`，导致 `.mg-drawer-inner` 无法获得有界高度；`.mg-drawer.open` 补加 `flex: 1; min-height: 0`，右边栏内容现在可正常上下滚动
- **高度滑块遮挡右边栏滚动条**：`高度` 滑块固定在 `right: 10`，与右边栏展开时的滚动条重叠；改为动态定位，右边栏可见时自动向左移 `rightPanelWidth + 10`
- **右侧分隔条拖动**：拖动开始时禁用 CSS transition（`width .25s ease`），消除无响应感
- **层次布局过滤器**：切换节点类型后不再重排、不缩放（改用固定坐标 + DataSet.update）
- **悬浮卡片内容**：LaTeX 原始命令改为纯文本截断（stripForTooltip）
- **语言按钮文字居中**：Settings 弹窗语言选择器 segmented control 文字对齐修复
- **取消处理按钮文字居中**：后台任务面板"取消处理"按钮文字居中

### 工程改进

- **apiUrl 工具函数**：新增 `app/api.ts`，统一通过 `VITE_API_ORIGIN` 环境变量或默认 `http://127.0.0.1:5001` 拼接后端地址；所有 `fetch` 调用从硬编码相对路径改用 `apiUrl()`，方便部署到不同域名
- **CSS 变量迁移**：硬编码颜色（`#B91C1C`、`#1A3A6B`、`#6B6860` 等）统一替换为设计 token（`var(--danger)`、`var(--accent)`、`var(--muted)` 等），支持主题化

### Backend Pipeline 改进

#### ensure_coverage 新阶段
- 正则扫描原文，找出所有带编号的数学陈述（Theorem X.Y / Definition X.Y 等）
- 与已提取节点对比，对漏掉的调用 LLM 补充提取
- 位置：`normalize_predicates` 之后、`build_relations` 之前
- 补充节点同样记录 `source_text` 字段

#### generate_titles label 规则修正
- 原文有编号时（如 "Theorem 5.1"），`label` 字段现在正确保留原文编号
- 之前明确禁止 label 包含编号，导致大量节点 label 为空

#### anchor_terms 改进（C2）
- `extract_anchor_terms()` 优先提取完整 LaTeX 表达式（`W_0^{k,p}(U)` 等）再分词
- 之前所有数学符号被拆成无用碎片

#### source_text 字段（新文档生效）
- `extract_statements` 提取后将原始块文本保存到节点的 `source_text` 字段
- `finalize_output` 保留此字段（不删除）
- API `_normalize_nodes()` 输出 `source_text`，前端 Strategy 4 使用它精确定位原文

#### API 新增返回字段
- `_normalize_nodes()` 新增：`surface_anchor`、`node_index_in_doc`、`source_text`
- 历史记录接口为旧记录自动回填 `node_index_in_doc`

#### Pipeline 阶段说明
- `compile_logic_form` → `normalize_predicates` → `build_relations` 强依赖链，必须同时开启
- 新增 `ensure_coverage` 阶段，进度条总数 13 个阶段
- 详见 `backend/EXCLUDED_STAGES.md`

### UI 细节

- 右侧分隔条改为细条（6px），与左侧保持一致，三点装饰
- 设置弹窗语言按钮改为 segmented control 风格
- 上传页 LLM 配置区域重新设计（accordion 卡片，去掉蓝色大按钮）
- 后台任务面板阶段名称居中显示
- 取消处理按钮文字居中

---

## [3198e56] 2026-06-06

- 右侧面板折叠/展开功能

## [2fa7ca9] 2026-06-06

- CSS legacy aliases 修复（渲染失效问题）
- Drawer resize handle 修复（full-graph 模式）
- × 按钮遮挡修复

## [c7c71b5] 2026-06-06

- 视觉重设计（CSS design tokens、lucide 图标、白底有色边框节点）
- 深度层次布局（BFS 最长路径算法）

## [1cb24e9] 2026-06-06

- 拖拽分隔栏改为直接 DOM 操作，mouseup 才 setState

## [fe13496] 2026-06-06

- 可拖拽分隔条（左右两侧）
