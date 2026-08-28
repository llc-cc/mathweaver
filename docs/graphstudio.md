# GraphStudio 设计文档

GraphStudio 是自然语言图谱模式的重做版，与经典 `ResultScreen` **并存**：顶栏「经典版 ⇄ ✨ 新版」一键切换，选择记忆在 `localStorage`。经典视图完全不动，便于对照与回退。

## 模块
| 文件 | 职责 |
|---|---|
| `app/routes/studio-graph.ts` | 纯逻辑：重要性评分、LOD、4 种布局、锚点索引、边分类、设置持久化 |
| `app/routes/graphstudio.css` | 设计系统（明/暗双主题、`.gs-*` 前缀） |
| `app/routes/GraphStudio.tsx` | 组件：顶栏 / 左栏 / 画布 / 右面板 |
| `app/routes/home.tsx` | 接线：`experience` 开关、`StudioWrapper`、经典头「✨ 新版」按钮、dev fixture 加载器 |

## 核心机制

### 1. 重要性评分与信息密度（LOD）
`computeSalience = 0.5·类型权重 + 0.5·度中心性(归一) + 编号加权`。
**信息密度滑块**取重要性前 `density` 比例的节点为「主节点」（显示标题、尺寸更大），其余退化为彩色小点 → 整本书的高密度图谱也能一眼看清骨架。canvas 标签优先显示信息性标题（而非「定理1」），完整双语 / 编号在悬浮卡与抽屉里。

### 2. 布局（多方案可选，顶栏切换）
- **阅读顺序** `layoutReading`：严格按 `node_index_in_doc` 蛇形排布，像读书一样左→右、上→下。
- **类型泳道** `layoutSwimlane`：每种节点类型一条横道，x 为全局文档序。
- **依赖层次** `layoutDag`：纵轴为最长依赖深度（基础层→前置 N 层，带层标），同层按文档序。
- **关系网络** `force`：力导向有机聚类，稳定后冻结。

### 3. 跳转与召回（node ↔ 原文）
`buildAnchorIndex(nodes, markdown)` 重建 markdown 分块列表，按优先级把每个节点映射到原文块：
1. **主**：后端 `source_text` 归一化（去空白/LaTeX 标记/小写）后的包含匹配 —— 权威且对排版漂移鲁棒；
2. 编号标签落在引导块内；3. 数学标题精确命中块内 `$...$`；4. `surface_anchor.anchor_terms` 出现；5. 内容指纹兜底。
返回 `nodeToBlocks` / `blockToNodes` 与**召回覆盖率**（左栏展示，Evans5 实测 100%）。点节点→原文面板滚动+高亮；点原文块→选中对应节点。

### 4. 依赖类型细化与「装不下」的处理
`classifyEdge` 用关键词把后端自由关系归入小型 taxonomy（推导/使用/特化/推广/等价/定义引用/反例/举例/相关）。
**空间有限不可能把所有边标签都画出来**，所以：
- 边按类型**着色** + 左栏**图例**（一眼看懂关系种类分布）；
- 标签默认不画，**悬浮/选中边**才显示说明；
- **节点抽屉**按类型逐条枚举该节点的进/出依赖（「→ 依赖 / ← 被依赖」+ 类型 chip），点击可跳转。

### 5. 个性化（持久化于 `localStorage`）
`StudioSettings`：主题(light/dark/auto)、默认布局、信息密度、聚焦淡化、曲线连接。顶栏齿轮弹层调整。

### 6. 其它交互
搜索框（`/` 唤起）取代冗余的「图谱+节点」视图；聚焦模式（选中节点时淡化 1 跳邻域外）；缩放簇（＋/−/适应，取代旧像素高度滑块，适应缩放设下限保证标签可读）。

## 本地联调
dev fixture（仅 localhost）：`/workspace?fixture=evans5`（或 `k126`）直接加载预生成图谱进入结果视图。
用 `python3 /tmp/make_fixture.py <node.json> <edge.json> <md> <out.json> <name>` 从 `backend/test_output/<run>/` 的产物生成，落在 `public/fixtures/`（已 gitignore，可能含受版权原文）。

## 后续（建议）
- 后端为每个节点直接输出 `source_block_id`，使锚点 100% 由构造保证；
- 增强 `ensure_coverage` 抓取无编号内联定义（「称…为/记作」「We define/denote…」）并在 UI 出召回报告；
- 双节点最短依赖路径高亮、大图 minimap。

详见 `SECURITY_REVIEW.md`（公开部署前的安全项）与 `HANDOFF_STUDIO.md`（断点续作）。
