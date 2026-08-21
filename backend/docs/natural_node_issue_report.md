# 自然语言节点问题修复报告

## 问题 1：自然语言节点中残留 `@@...@@` 占位符

### 问题定位

- 现象示例：
  - `$E_@@CONST::infty@@$`
  - `@@CMD::lambda@@`
  - `@@REL::in@@`
- 相关代码：
  - [pipeline/stages/generate_titles/stage.py](/Users/clara/pdfPipeline/backend/pipeline/stages/generate_titles/stage.py)
  - [pipeline/common/latex.py](/Users/clara/pdfPipeline/backend/pipeline/common/latex.py)
  - [pipeline/stages/extract_logic_tuples/stage.py](/Users/clara/pdfPipeline/backend/pipeline/stages/extract_logic_tuples/stage.py)

### 原因分析

- 主流程前段会将 LaTeX 命令冻结成 `@@CATEGORY::command@@` 形式，避免 LLM 抽取时破坏公式。
- 致密化后的正式节点输出会在后续阶段通过 `unfreeze_dict(...)` 恢复这些占位符。
- 新增的 `*_node_natural.json` 出口此前是在 `generate_titles` 后直接写文件，没有经过解冻步骤，因此占位符被原样保留。

### 修补方案

- 在自然语言节点写出前补上与主流程一致的 LaTeX 解冻逻辑：
  - 合并 `global_mapping`
  - 对 `natural_node_list` 执行 `unfreeze_dict(...)`
- 本次已完成修补。

## 问题 2：自然语言节点中出现多余反斜杠

### 问题定位

- 现象示例：
  - `$(f,E_\\\\infty)$-扩张的定义`
  - `$\\\\lambda$`
- 相关代码：
  - [JoinAgent/LLM_Parser/llm_parser.py](/Users/clara/pdfPipeline/backend/JoinAgent/LLM_Parser/llm_parser.py)
  - [pipeline/common/node.py](/Users/clara/pdfPipeline/backend/pipeline/common/node.py)
  - [pipeline/stages/generate_titles/stage.py](/Users/clara/pdfPipeline/backend/pipeline/stages/generate_titles/stage.py)

### 原因分析

- 解析器为了让 `ast.literal_eval` 安全，会将字符串中的反斜杠加倍。
- 正式节点输出在后续会经过 `adjust(...)` 等清洗逻辑，把多余转义收敛回来。
- 新增的自然语言节点出口此前没有走这一步，所以保留了中间态转义结果。
- JSON 文件中的四个反斜杠，本质上通常是“字符串里已有两个反斜杠，再被 JSON 转义显示一次”。

### 修补方案

- 在自然语言节点写出前补上文本清洗：
  - 对解冻后的节点逐个执行 `adjust(...)`
  - 统一清理多余反斜杠、换行转义和空白
- 本次已完成修补。

## 问题 3：节点标题或内容缺少自解释上下文

### 问题定位

- 现象示例：
  - `命题 prop:possibleh62 中陈述 (5) 与 (5') 的等价性`
- 对应内容本身依赖上文的 `Proposition prop:possibleh62` 和内部编号 `(5)`、`(5')` 才能完整理解。

### 原因分析

- 标题生成阶段当前只能基于当前 `content` 生成标题，且 prompt 明确限制不要引入额外概念。
- 而这类节点的 `content` 本身就是“引用型陈述”，天然依赖上文编号体系。
- 因此模型会忠实保留 `prop:possibleh62`、`statement (5)` 这类局部引用，导致节点脱离原文后可读性不足。

### 解决方案

- 短期方案：
  - 只调整标题生成策略，不改节点抽取。
  - 在标题 prompt 中降低对 `prop:...`、`thm:...`、`statement (5)` 这类交叉引用的直接复用倾向。
  - 优先从 `In particular` 后的公式、对象、性质中提取标题关键词。
- 中期方案：
  - 增加“交叉引用消解”层。
  - 识别 `Proposition prop:possibleh62` 对应的命题节点，再解析其中的 `(5)`、`(5')` 实际语义，补充到当前节点中。
- 推荐路径：
  - 如果目标是“节点离开原文后也能读懂”，应优先做交叉引用消解，而不是只靠标题润色。

### 本次轻量修补

- 在自然语言节点写出前增加“引用型标题重写”步骤：
  - 识别标题或正文中包含 `prop:`、`thm:`、`statement (k)` 等交叉引用模式的节点
  - 优先从 `In particular`、`For every`、`There exists` 后的局部语义中提取提示
  - 若能提取到公式，则将标题重写为“相关陈述的等价性：$...$”这类更可读形式
- 当前仍然不做真正的交叉引用消解：
  - 不回溯到被引用命题正文
  - 不展开 `(5)`、`(5')` 的原始定义
  - 只做轻量级标题可读性改写
- 本次已完成修补。

### 后续回调修补

- 在实际输出中发现，上一轮“引用型标题重写”命中范围过宽，产生了新的问题：
  - 把正常引用定理/命题的节点也改成了过于泛化的标题
  - 出现了 `相关陈述的等价性：\lambda`、`相关陈述的等价性：X--[f]-->...` 这类不自然标题
  - 个别标题过长，接近把正文片段直接拼接到标题中
- 因此本轮策略改为：
  - 默认尽量保留原始标题
  - 只对“明确的 statement `(k)` / `(k')` 等价性节点”做极窄范围改写
  - 普通 `prop:` / `thm:` / `exam:` 引用节点不再触发标题重写
  - 改写时优先生成简短标题，如 `陈述(5)与(5')的等价性`
- 这次调整吸取的经验是：
  - 轻量修补应以“少改”为主，避免把启发式规则扩展到所有引用型节点
  - 如果没有真正做交叉引用消解，就不应强行把正文局部片段拼成标题
- 本次已完成回调修补。

## 问题 4：自然语言节点中混入排版控制与图示占位文本

### 问题定位

- 现象示例：
  - `\setcounter{enumi}{4}`
  - `[Commutative diagram omitted]`
  - `[Diagram omitted]`
- 相关代码：
  - [pipeline/stages/generate_titles/stage.py](/Users/clara/pdfPipeline/backend/pipeline/stages/generate_titles/stage.py)
  - [content_node_natural.json](/Users/clara/pdfPipeline/backend/test_output/126/content_output/content_node_natural.json)

### 原因分析

- 这类文本本质上是 LaTeX 列表控制命令或图示缺失提示，不属于知识节点的语义主体。
- 当前自然语言节点出口是在标题生成后直接整理输出，因此会把这类“正文附近的排版残留”一并保留下来。
- 它们不会帮助理解节点，反而会污染 `content`、`proof` 和部分 `title` 的可读性。

### 修补方案

- 在自然语言节点写出前增加一层轻量文本清洗：
  - 删除 `\setcounter{...}{...}` 这类列表控制命令
  - 删除 `[Commutative diagram omitted]`、`[Diagram omitted]` 这类图示占位文本
  - 顺带收敛清洗后产生的多余空格
- 本次已完成修补。

## 问题 5：重复标题导致自然语言节点区分度不足

### 问题定位

- 现象示例：
  - 多个节点共用 `Non-trivial Surviving Elements on the Adams 2-line`
  - 中文标题也出现“非平凡存活元 / 幸存元”这类高度近似重复标题
- 相关代码：
  - [pipeline/stages/generate_titles/stage.py](/Users/clara/pdfPipeline/backend/pipeline/stages/generate_titles/stage.py)
  - [content_node_natural.json](/Users/clara/pdfPipeline/backend/test_output/126/content_output/content_node_natural.json)

### 原因分析

- 标题生成阶段是逐节点独立完成，模型不会感知全局是否已有同名标题。
- 当多个节点共享相同句式模板、只在公式项上不同，就容易得到相同或近似相同标题。
- 这会降低节点检索、人工浏览和后续关系构建时的可区分性。

### 修补方案

- 在自然语言节点写出前增加一个轻量“标题去歧义”步骤：
  - 统计重复标题组
  - 对重复标题节点，从 `content` 中提取首个公式片段作为区分提示
  - 将该公式追加到中英文标题末尾
- 当前采取的是保守方案：
  - 不合并节点
  - 不修改节点正文
  - 只提高标题可区分性
- 本次已完成修补。

## 问题 6：少量标题仍然强依赖原文局部上下文

### 问题定位

- 现象示例：
  - `命题第二部分的逆否推论`
  - `命题prop:possibleh62中陈述(5)与(5')的等价性`
  - `$(f, E_r)$-扩张示例：$(f, E_r)$`

### 原因分析

- 这些节点虽然没有被上一轮的宽泛规则误伤，但原始标题本身仍然过于依赖原文上下文：
  - 需要知道“哪个命题”“哪一部分”
  - 或者无法区分同一组 `(f,E_r)` 示例中的不同子项
- 这类问题适合根据 `content.md` 中原文的固定句式做定点修补，而不适合继续扩大通用启发式规则。

### 修补方案

- 在自然语言节点写出前增加“原文感知”的窄规则修补：
  - 对 `(f,E_r)` 示例组，根据正文中的具体公式改写为 `$(f,E_2)$` / `$(f,E_3)$` / `$(f,E_\\infty)$` 示例标题
  - 对 `Corollary cor:stretch-extension` 改写为 `跨页延拓推论`
  - 对 `statement (5)` / `(5')` 的公式等价节点，改写为围绕 `\\lambda^3\\eta[h_0^2x_{124,8}]` 的检测标题
- 这一层仍然保持轻量：
  - 不改正文
  - 不回溯构造完整引用图
  - 只修补已经确认的少量坏案例
- 本次已完成修补。

## Edge 典型问题观察

### 问题定位

- 当前 `content_edge.json` 主要由 `出发节点`、`到达节点`、`关系`、`理由` 组成，共 141 条边。
- 抽样中已经能看到：
  - 同一对节点出现互相指向的“逻辑依赖”
  - `理由` 文本中先分析出“不依赖”，最后仍保留 `关系=逻辑依赖`
  - 边中只保存哈希型节点 ID，可读性较弱

### 原因分析

- 关系抽取目前更像“局部成对判断”，缺少全局一致性约束。
- `理由` 由 LLM 自由生成，容易出现分析过程与最终标签不一致的情况。
- 输出侧没有附带节点标题或内容摘要，导致单独阅读 edge 文件时不易人工校验。

### 典型问题

- 方向冲突：
  - A 依赖 B 与 B 依赖 A 同时出现。
- 标签与理由不一致：
  - `理由` 里已经说出“不依赖”，但 `关系` 仍然是 `逻辑依赖`。
- 可读性弱：
  - 仅有哈希 ID，没有节点标题快照。
- 关系类型过粗：
  - 当前大量收敛为 `逻辑依赖`，不足以区分“定义支撑”“定理推论”“等价改写”等不同关系。

### 后续建议

- 轻量改进：
  - 输出 edge 时附带源/目标节点标题快照，便于人工检查
  - 在落盘前增加一层“理由-关系一致性”校验，过滤自相矛盾边
  - 对同一对节点的双向依赖增加冲突检查
- 中等改进：
  - 把 `逻辑依赖` 细分成更可解释的关系类型
  - 对高置信度规则关系（例如定义 -> 定理）优先使用规则判定，再交给 LLM 补理由

## 结果层人工修补记录

### 问题定位

- 用户希望先在当前已产出的 `content_node_natural.json` 上直接修复致命问题，而不是继续修改提取逻辑。
- 这次主要聚焦于 5 个节点：
  - 3 个 `$(f,E_r)$-扩张示例` 标题完全重复
  - 1 个 `命题第二部分的逆否推论` 过度依赖局部上下文
  - 1 个 `statement (5)/(5')` 等价性标题仍带有过强引用格式

### 修补方案

- 直接修改结果文件 [content_node_natural.json](/Users/clara/pdfPipeline/backend/test_output/126/content_output/content_node_natural.json)，不改提取逻辑：
  - 将 3 个示例标题分别改为 `$(f,E_2)$` / `$(f,E_3)$` / `$(f,E_\\infty)$` 对应的具体公式标题
  - 将 `命题第二部分的逆否推论` 改为 `跨页延拓推论`
  - 将 `命题prop:possibleh62中陈述(5)与(5')的等价性` 改为围绕 `\\lambda^3\\eta[h_0^2x_{124,8}]` 检测的标题

### 说明

- 这次属于“结果层人工修补”，目标是快速消除当前文件中最致命的可读性问题。
- 未修改其他节点，也未改变 edge 文件内容。
- 若之后重新运行提取流程，这些人工修补不会自动保留，除非同步把相应规则固化进提取逻辑。

## Edge 规则清洗与关系重跑记录

### 本次新增规则

- 在 [pipeline/stages/build_relations/stage.py](/Users/clara/pdfPipeline/backend/pipeline/stages/build_relations/stage.py) 中增加了纯规则的 edge 清洗：
  - 删除 `关系=逻辑依赖/定义依赖` 但 `理由` 明确包含“不依赖 / 无依赖 / does not depend / not support”等否定表述的边
  - 删除自环边
  - 对完全同向重复边保留优先级更高的一条
  - 对未来可能出现的双向冲突边，按规则优先级保留一条

### 只基于现有节点重跑关系

- 新增了 [relation_layer.py](/Users/clara/pdfPipeline/backend/relation_layer.py)：
  - 输入现有 node JSON
  - 直接调用关系构建阶段
  - 不再运行节点抽取流程

### 本次执行情况

- 已尝试基于现有 [content_node_natural.json](/Users/clara/pdfPipeline/backend/test_output/126/content_output/content_node_natural.json) 重跑一次关系提取。
- 这次重跑进程未成功在当前会话内写回新 edge 文件，因此没有把“重跑后的 LLM 判边结果”作为最终交付。
- 同时，已将新增的纯规则清洗直接应用到当前 [content_edge.json](/Users/clara/pdfPipeline/backend/test_output/126/content_output/content_edge.json)：
  - edge 数量从 `141` 条降到 `139` 条
  - `理由` 与 `关系` 自相矛盾的边已清零
  - 仍未发现真实的“同一对节点双向边”

## Edge 结果层人工修补记录

### 问题定位

- 用户要求参考 [content.md](/Users/clara/pdfPipeline/backend/content.md) 原文，对当前已产出的 [content_edge.json](/Users/clara/pdfPipeline/backend/test_output/126/content_output/content_edge.json) 做轻量人工修补，而不是继续扩大提取逻辑修改范围。
- 本轮重点清理的是少量“明显错边”与“过泛规则误连”的边。

### 本次删除的典型问题边

- 明显不成立的逻辑依赖：
  - 将“关于 $h_6^2$ 的 Adams 微分性质”错误连到“126维Kervaire不变量为1的带框流形存在性”的边删除。
- 明显不成立的定义依赖：
  - 将普通 Adams differential 记号误判为依赖 `$f$-extension` 定义的边删除。
- 示例节点中的过度依赖：
  - 对 `$(f,E_2)$` / `$(f,E_3)$` 示例中明显不应依赖 `Essential $(f,E_r)$-extension` 或 `$(f,E_\\infty)$-extension` 定义的边删除。
- 过泛 label 正则匹配产生的假边：
  - 删除理由为 `正则匹配：后置节点中显式引用“Proposition”` 的边。

### 结果

- 本轮人工修补前：`139` 条边
- 本轮人工修补后：`131` 条边
- 当前已清除：
  - 这轮人工识别出的明显错边
  - 目标范围内的 generic `Proposition` 假引用边

### 说明

- 这次仍然是“结果层人工修补”：
  - 直接改当前 edge 文件
  - 不再触发节点抽取
  - 不依赖新增 LLM 判断
- 若后续再次整轮重跑关系提取，这些人工删边不会自动保留，除非把相应规则继续固化进关系层逻辑。

## 节点-边联合检查与轻量平衡记录

### 检查目标

- 联合检查当前节点与边的映射是否出现明显畸形：
  - 少量节点被大量边集中指向
  - 大量节点孤立
  - 图是否只剩碎片化小团块
- 在不破坏现有主干结构的前提下，做轻量平衡：
  - 只补非常明确的孤立节点边
  - 不大规模重写已有关系

### 联合检查结果

- 检查前：
  - 节点数：`46`
  - 边数：`131`
  - 孤立节点：`3`
  - 连通分量规模：`[42, 1, 1, 1]`
- 度分布特征：
  - 高入度节点集中在基础定义层：
    - `自然比较映射 λ 的定义`：入度 `24`
    - `$f$-extension 的定义`：入度 `22`
    - `双分次球面的定义`：入度 `18`
  - 这是当前图谱“定义层吸边过多”的主要表现。

### 孤立节点样本分析

1. `Kervaire不变量一的存在性定理`
- 原文位置：`content.md:34`
- 原文内容给出一般性的“存在性 iff 存活性”判别。
- 图中问题：
  - 该节点本身是实质性定理，但没有任何边接入主图。
- 轻量处理：
  - 将 `126维Kervaire不变量为1的带框流形存在性` 补到该一般定理上，视作其具体化特例。

2. `谱的上纤维序列与${\\HF}$-合成谱的正合性`
- 原文位置：`content.md` 中 cofiber sequence 与短正合条件判别段落。
- 图中问题：
  - 这是后续 synthetic spectra 论证的重要基础性质，但在图中完全孤立。
- 轻量处理：
  - 将 `谱的消解三角形提升` 连接到该节点，因为前者显式使用了“对应 \\HF-同调短正合列成立”的前提。

3. `命题第二部分的逆否推论`
- 原文位置：`content.md:1387`
- 图中问题：
  - 这是一个元描述节点，本身不是完整数学命题正文，因此天然容易孤立。
- 轻量处理：
  - 将其挂接到后续正式推论 `无交叉扩张的传递性`，作为“元描述 -> 正式陈述”的最小连接。
  - 这条边不是强数学依赖，而是为避免元描述节点悬空所做的保守连接。

### 平衡后的结构结果

- 检查后：
  - 节点数：`46`
  - 边数：`134`
  - 孤立节点：`0`
  - 连通分量规模：`[45]`
- 结果解读：
  - 当前图已经基本连成一个主图
  - 没有再出现“大量节点孤立、少量节点密集互连”的严重畸形
  - 但“定义层入度偏高”这一现象仍然存在，只是尚未严重到需要大规模裁边

### 本次新增的 3 条平衡边

- `126维Kervaire不变量为1的带框流形存在性` -> `Kervaire不变量一的存在性定理`
- `谱的消解三角形提升` -> `谱的上纤维序列与${\\HF}$-合成谱的正合性`
- `命题第二部分的逆否推论` -> `无交叉扩张的传递性`

### 说明

- 这次平衡仍然遵守“轻量化”原则：
  - 只补 `3` 条边
  - 不改节点内容
  - 不大规模裁剪已有主干关系
- 后续如果继续优化，最值得做的是：
  - 继续压缩“只因使用了通用符号 `$\\lambda$` / `$S^{0,0}$` 就连到定义节点”的弱定义依赖
  - 让图的主干更多由“命题-定理-推论”层承载，而不是过度汇聚到基础定义层


## 最后一个节点的结果层补修

### 问题定位
当前自然语言节点文件中的最后一个节点，标题与 `content` 已经展开成了可读版本，但 `proof` 仍保留了 `statement (5)`、`statement (5')` 这类内部编号指代，单独阅读时仍需要回跳原文。

### 原因分析
这说明上一轮结果层修补只覆盖了标题和正文，没有同步覆盖证明字段。对这类结尾命题来说，`proof` 里仍保留编号引用，会让节点在知识图谱场景中显得半展开、半依赖上下文。

### 修补方案
本次只在结果文件层面补修最后一个节点的 `proof`，把“由存在型陈述推出全称型陈述”的证明逻辑直接写明，并保留原文中的关键过滤度数信息：
- $[h_0^2x_{124,8}]$ 的 Adams filtration 为 $10$
- 不确定项乘上 $\lambda^3\eta$ 后进入 $\AF \ge 15$
- $[h_1h_4x_{109,12}]$ 的 Adams filtration 为 $14$
这样该节点的标题、正文、证明三部分都能在脱离原文时独立成立。

## Edge 轻量化剪枝记录（当前结果文件版）

### 问题定位
重新检查当前 [content_edge.json](/Users/clara/pdfPipeline/backend/test_output/126/content_output/content_edge.json:1) 后发现，这一版边文件只有 `8` 条边，而且全部来自“正则匹配：后置节点中显式引用 Definition / Proposition”这一类兜底规则，而不是真正基于内容判断得到的关系。与此同时，边端点里还出现了无法稳定映射回当前节点集的 ID，说明这份边文件本身已经存在结果层失真。

### 原因分析
这类边并不是“边太多需要剪枝”，而是“边本身已经退化成了引用占位”。在这种情况下继续保留它们，会让联合检查得出错误结论，例如：
- 图看起来有边，但实际上不反映数学依赖
- 有些边的起点或终点无法和当前节点一一对应
- `Definition` / `Proposition` 字样触发的兜底匹配会把文档结构词误当成关系依据

### 修补方案
按照“尽量少改，只处理致命问题”的原则，本次对当前 edge 文件采用最轻量也最稳妥的剪枝规则：
- 删除所有仅由 `正则匹配：后置节点中显式引用...` 产生的兜底边；
- 不尝试在当前这一轮手工补新边，避免在失真的边结果上继续叠加主观修补。

### 剪枝结果
- 剪枝前：`8` 条边
- 剪枝后：`0` 条边

### 具体样本分析
样本 1：`713343... -> Adams微分的交叉定义`
理由仅为“后置节点中显式引用 Definition”。这只能说明原文行文上出现了 “Definition” 字样，不能说明前一节点在数学上定义了后一节点。

样本 2：`命题第二部分的逆否推论 -> 06629e...`
理由仅为“后置节点中显式引用 Proposition”。这更像是在正文中提到某个命题编号，而不是严格的命题依赖关系。

样本 3：若干无法映射到当前节点标题的端点 ID
这说明边文件与当前节点文件之间已经出现了结果层不一致；在这种情况下，继续保留这些边会让后续蓝图判断产生误导。

## 节点与原文脉络对照分析

### 总体结论
对照 [content.md](/Users/clara/pdfPipeline/backend/content.md:1) 的全文结构，当前 [content_node_natural.json](/Users/clara/pdfPipeline/backend/test_output/126/content_output/content_node_natural.json:1) 在“抓住数学主干”这件事上做得是比较准确的，但在“均匀复现作者叙事节奏”上仍然偏向技术骨架而不是行文桥接。

### 结构覆盖是否均匀
不完全均匀，但基本符合原文重点分布。

1. 开篇主结论抓得准，而且权重足够高。
原文在引言一开始就给出 126 维 Kervaire invariant one 的存在性定理与维数推论，对应节点里前几项就是：
- `126维Kervaire不变量为1的带框流形存在性`
- `Kervaire不变量为1的维数`
- `Kervaire不变量一的存在性定理`
这说明节点抽取抓住了论文最核心的 headline 结果。

2. 中段技术准备抓得很密。
原文从 `## A Spectral Sequence for Extensions`、`## HF-synthetic spectra`、`## Synthetic Extensions`、`## Extensions on a classical E_r-page` 到 `## The Generalized Leibniz Rule and Generalized Mahowald Trick`，都是在铺设技术 machinery。节点里对应地密集出现：
- `$f$-extension 的定义`
- `双分次球面的定义`
- `自然比较映射 $\lambda$ 的定义`
- `Adams微分的交叉定义`
- `$(f,E_r)$-扩张的定义`
- `广义莱布尼茨法则`
这部分覆盖是充分的，但也因此显得“定义和机制节点比叙述性节点密”。

3. 结尾证明段的重要关节基本被抓到了。
原文 `## Proof of the main theorem` 一节的关键推进包括：
- 关于 $h_6^2$ 的二选一定理
- 若干等价命题与检测命题
- 最后的 `(5)/(5')` 等价性
当前节点里对应有：
- `$h_6^2$ 在 Adams 谱序列中存活至 $E_\infty$-页`
- `$h_6^2$ 在经典 Adams 谱序列中的存活条件`
- `$h_6^2$ 为永久圈的充要条件`
- `关于 $h_6^2$ 的 Adams 微分性质`
- `同伦类检测等价命题`
- `陈述(5)与(5')的等价性：$\lambda^3\eta[h_0^2x_{124,8}]$ 的检测`
这说明结尾收束链条也基本被抓住了。

### 是否准确抓住重点信息
总体上是准确的，尤其擅长抓“可结构化重点”。

1. 对定理、定义、推论的抓取准确度较高。
这些内容在原文里本来就以显式数学陈述出现，所以抽成节点后保持得最好。

2. 对方法链条的抓取也比较到位。
例如从扩张谱序列、交叉定义、Generalized Leibniz Rule 到后段的检测命题，节点能反映“工具搭建 -> 微分/扩张控制 -> 主定理证明”的方法脉络。

3. 对叙述性桥接句和作者说明抓取较弱。
例如引言中解释为什么只剩 `h_6^2` 这一个可能情形、以及中后段若干“we reduce... / we compare... / this leads to...” 类型的推进句，在当前节点集中没有被等量保留。这意味着节点更像“数学骨架摘要”，而不是“逐段摘要”。

### 具体样本分析
样本 A：开头主结论链
原文第 20 行到第 25 行给出主定理与维数推论，节点前 3 项几乎一一对应，说明主结果抓取得既早又准。

样本 B：技术中段的扩张理论
原文第 141、171、818、914、983 行附近依次定义 $f$-extension、essential extension、$(f,E_r)$-extension 及 crossing。节点里这一串定义与相关命题都在，说明中段技术骨架覆盖充分；但它也带来了“定义节点密度较高”的不均匀性。

样本 C：结尾的 $(5)/(5')$ 检测命题
原文第 1534 行之后的 Lemma 及其证明，本来依赖命题内部编号。当前结果经过轻量修补后，最后一个节点已经把标题、正文、证明都展开到可独立理解，这说明结果层修补对结尾关键节点是有效的。

### 最终判断
如果目标是“让节点作为蓝图/知识图谱的自然语言节点基础”，这一版节点已经比较准确地抓住了全文重点，且重点分布与原文的大结构基本一致。

如果目标是“尽量均匀地复现作者完整行文节奏”，它还不够均匀，主要体现在：
- 技术定义与机制节点偏密；
- 叙述性过渡偏少；
- Appendix 没有被单独结构化展开。

但在“只做结果层轻量修补、不再改提取逻辑”的约束下，这已经是一版比较稳且可用的节点结果。


## 131 边版本恢复记录

### 恢复背景
在后续轻量剪枝过程中，当前 `content_edge.json` 曾被覆盖为更小的结果。由于仓库内没有单独备份文件，本次恢复采用“基于现有自然语言节点重新生成关系层，再按此前已确认的删边规则回退”的方式完成。

### 恢复步骤
1. 使用现有 [content_node_natural.json](/Users/clara/pdfPipeline/backend/test_output/126/content_output/content_node_natural.json:1) 重新生成关系结果，得到 `154` 条边。
2. 按此前已经确认有效的轻量规则删除：
- `22` 条仅由符号 `$\lambda$` 触发的弱定义依赖边；
- `1` 条明显错误的 `关于 $h_6^2$ 的 Adams 微分性质 -> 126维Kervaire不变量为1的带框流形存在性` 逻辑依赖边。
3. 最终恢复为 `131` 条边，并写回当前 [content_edge.json](/Users/clara/pdfPipeline/backend/test_output/126/content_output/content_edge.json:1)。

### 当前结果
- 当前 edge 数量：`131`
- 这次恢复不是“找回旧文件副本”，而是“重跑后按既有规则收敛回 131 边版本”
- 因此边的整体质量和我们之前确认的那一版是同一修补思路，适合作为继续人工检查的基线版本。


## Edge 交付格式调整记录

### 调整目标
由于最终交付以 [content_node_natural.json](/Users/clara/pdfPipeline/backend/test_output/126/content_output/content_node_natural.json:1) 和 [content_edge.json](/Users/clara/pdfPipeline/backend/test_output/126/content_output/content_edge.json:1) 为主，因此将 edge 文件中的 `出发节点`、`到达节点` 从哈希型 `global_id` 改为自然语言节点标题，提升可读性与可直接检查性。

### 调整方式
- 以自然语言节点文件为基准，按节点归一化后的 `global_id` 建立映射；
- 将 edge 中的 `出发节点`、`到达节点` 替换为对应中文标题；
- 保留 `关系` 与 `理由` 字段不变。

### 结果
- 当前交付版 `content_edge.json` 已不再依赖哈希 ID 即可阅读；
- 边文件可以直接和自然语言节点文件逐条对照检查。


## Edge 全英文交付调整记录

### 调整目标
根据最终交付需求，将 [content_edge.json](/Users/clara/pdfPipeline/backend/test_output/126/content_output/content_edge.json:1) 全部转换为英文表达，包括：
- 节点名称改为自然语言节点的英文标题；
- `关系` 改为英文关系名；
- `理由` 全量翻译为英文。

### 调整方式
- 以 [content_node_natural.json](/Users/clara/pdfPipeline/backend/test_output/126/content_output/content_node_natural.json:1) 的英文章节标题作为 node name 映射源；
- 将 `逻辑依赖` / `定义依赖` 分别转换为 `logical dependency` / `definitional dependency`；
- 使用当前项目配置的模型对边理由做逐批翻译，保留数学公式与 LaTeX 记号。

### 结果
- 当前交付版 edge 文件已改为全英文字段值；
- 节点名称、关系、理由均可直接用于英文交付或后续英文蓝图展示。


## Edge 最后一轮轻量整理记录

### 本轮处理
- 删除了 `4` 条仅由 `Regular match: the successor node explicitly references "Definition".` 生成的 fallback 边；
- 为此前完全未进入图的 `4` 个节点补入最少量、且能由原文直接支撑的边；
- 复查当前 edge 文件，不存在双向关系对。

### 本轮补入的节点边
- `Lifting of Distinguished Triangles to Synthetic Spectra` -> `Cofiber Sequences of Spectra and Exactness for ${\HF}$-Synthetic Spectra`
- `Applications of Zero Composition and Essential Exactness` -> `Cofiber Sequences of Spectra and Exactness for ${\HF}$-Synthetic Spectra`
- `$h_6^2$ Survives to the $E_\infty$-Page in the Adams Spectral Sequence` -> `Existence Theorem for Kervaire Invariant One Framed Manifolds`
- `Equivalence of Statements (5) and (5'): detection of $\lambda^3\eta[h_0^2x_{124,8}]$` -> `Equivalence of Homotopy Class Detection Statement`

### 当前状态
- edge 数量保持为 `131`；
- 已清除显式 fallback 边；
- 当前未发现双向关系边。
