# `extract_references` 阶段 — 规则池实施记录

## 1. 阶段定位

在 `extract_logic_tuples`（含可选 `analysis`）之后、`build_relations` 之前新增轻量阶段，从节点原始文本中识别：

- 显式编号引用（定义/定理/命题/引理/推论/性质/公理 + 中英文编号）
- 节点级相对引用（上述定理、前述引理 …）
- 公式引用（式(2)、公式(3)、(2)、上式/下式 …）— 只记录不解析
- 命名引用（由 Cauchy 不等式 …）— 只记录不解析
- 引用触发词（根据/由/利用）与推导触发词（可知/因此/推出）

阶段产物写入节点本身，`build_relations` 的显式关系抽取函数已改为**消费本阶段产出**，不再内联 label 正则。

## 2. 文件布局

```
backend/pipeline/stages/extract_references/
├── __init__.py
├── rules.py       # 规则池（patterns / 词表）
├── alias.py       # label normalize 与中英映射
├── resolver.py    # 索引构建与解析
└── stage.py       # 阶段入口
```

## 3. 字段输出

每个节点追加 4 个字段：

| 字段 | 内容 |
|---|---|
| `locator` | `{node_index_in_doc, reorder_id}`，不依赖 global_id |
| `surface_anchor` | `{label_text, title_text, node_type, context_texts, anchor_terms}` |
| `reference_aliases` | 规范化后的别名列表（含中英映射、去/补空白） |
| `reference_signals` | 五桶：`explicit_targets` / `relative_references` / `formula_references` / `named_references` / `reference_triggers`，以及 `repair_flags` |

## 4. 规则池

### 4.1 显式编号（`EXPLICIT_REF_PATTERNS`）

```
(定义|定理|命题|引理|推论|性质|公理)\s*§?\s*([0-9]+(?:[.\-][0-9]+)*(?:\([a-zA-Z0-9]+\))?)
(Definition|Theorem|Proposition|Lemma|Corollary|Property|Axiom)s?\s*([0-9]+(?:[.\-][0-9]+)*)
```

覆盖：`定义1`、`定理 2-1`、`命题 §3.5`、`引理1.5(b)`、`Theorem 3`、`Definition 1.2`。

### 4.2 公式引用（不解析为节点）

```
(?:式|公式|equation|eq\.?)\s*[\(（]\s*([0-9.\-]+|[*∗★]+|[IVX]+)\s*[\)）]
```
加 `上式 / 下式 / 上面式子 / 前式 / 前一式` 词表。

### 4.3 相对节点引用（解析）

词表：`上述/前述 + {定义,定理,命题,引理,推论,性质,公理}`。
解析策略：**向前找最近同类型节点**，距离上限 `max_distance=30`，超出则 unresolved。

### 4.4 命名引用（不解析）

```
(?:由|根据|利用|应用|依据|按照)\s*([A-Z][A-Za-z\-]+(?:[-–][A-Z][A-Za-z\-]+)?)\s*(不等式|定理|引理|公式|法则|原理|恒等式)
```

### 4.5 触发词

- `REFERENCE_VERBS = ["根据","由","利用","结合","依照","按照","应用","依据"]`
- `DEDUCTION_VERBS = ["可知","可得","得到","推出","得出","于是","因此","同理可得","故","推知"]`

## 5. Alias 规范化

`alias.py` 对 label 自动派生：
- 半/全角括号、句点统一
- 去空白变体：`定义 1` ↔ `定义1`
- 中英映射表 `NODE_TYPE_ZH_TO_EN` / `NODE_TYPE_EN_TO_ZH`：派生 `Definition 1` / `Theorem 3`
- title 仅在长度 ≥ 4 时进入 alias，降低误匹
- 若节点存在 `context`，则额外抽取 `anchor_terms`，构造 `title/context + label` 的上下文锚点别名

这样做的原因是：

- 单本书内可能重复出现 `定义 1`、`命题 1`
- 不能只靠裸 `label` 做锚点
- 但又不能强依赖章节字段，因为很多节点没有显式章节名

因此当前策略是：

- 有标题/局部语境就补进 alias
- 没有就退回 label-only

## 6. 解析优先级

1. `(node_type, number)` 精确映射（`by_type_number_all`，若有多个候选则取最近前文）
2. `alias` normalize 映射（`by_alias_all`，若有多个候选则取最近前文）
3. `title` 唯一性 fallback（仅当该 title 在全文只对应 1 个节点时生效）
4. 其它 → `unresolved`

所有初始解析均加“优先取最近前文”的约束；若只有后文唯一候选，则先标记 unresolved，交由 `repair_lite` 再决定是否做安全恢复。

## 7. repair_flags

- `unresolved_explicit_reference`
- `relative_reference_no_anchor`
- `named_reference_recorded`（信息性）
- `formula_reference_recorded`（信息性）
- `trigger_without_target`

## 8. 与 `build_relations` 的对接

[build_relations/stage.py](../pipeline/stages/build_relations/stage.py) 中的 `extract_explicit_relations` 已重构：

- 删除原来的 `_build_label_patterns` 双重循环
- 改为遍历每个节点的 `reference_signals.explicit_targets` + `relative_references`
- 取 `resolved_index` → 生成 `(j, i)` 进入 `explicit_pairs`
- 关系类型映射保持原样（earlier 是定义/公理 → `定义依赖`，否则 → `逻辑依赖`）
- 理由字段带上 `match_mode`，便于溯源
- `formula_references` / `named_references` / unresolved 不进 `explicit_pairs`，只在调试文件落盘

## 9. 调试输出

使用现有 `save_stage_json(context.output_dir, ...)`：

- `references_dict.json` — 全量节点的信号字段
- `references_unresolved.json` — 仅保留带 `repair_flags` 的节点，供 `repair_lite` 消费

## 10. 实施要点与偏差记录

| 方案项 | 实施决定 | 理由 |
|---|---|---|
| `reference_key = def::12` | 放弃 | 与现有 `global_id` / `_reorder_id` 重复；本阶段使用下标 + `reorder_id` 做锚点 |
| 阶段插入位置 | `analysis` 之后、`build_relations` 之前 | 保持 `global_id` 由 `build_relations.normalize_node_fields` 统一生成 |
| `extract_explicit_relations` | 接管而非并行 | 避免双轨；保持 `explicit_pairs` 接口给 LLM 阶段 |
| 公式 / 命名引用 | 只记录不解析 | 节点粒度不对应公式编号；命名引用需外部知识库 |
| title 作为 alias | 仅 ≥4 字符且全文唯一时启用 | 避免"勾股定理" vs "毕达哥拉斯定理" 类误匹 |
| 相对引用距离上限 | `max_distance=30` | 章节边界信号缺失时的硬近似 |
| `reference_spans` | 未实现 | 前期价值低，按需再加 |
| 重复编号处理 | 先取最近前文，再保留候选列表 | 避免 `命题 1/定义 1` 永远指向首次出现的节点 |

## 11. 不在本阶段处理

- 多跳引用链推断
- 章节级精确边界
- 命名引用落到外部节点
- 公式编号 → 公式节点（当前节点粒度不含公式）
- LLM 辅助引用消解
