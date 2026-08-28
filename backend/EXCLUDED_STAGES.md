# 实验旁路 Stage

`compile_logic_form` 与 `normalize_predicates` 已从默认生产流水线移出。
实现和缓存格式仍然保留，用于关系结构化实验。

## 默认主线

默认情况下，`repair_lite` 完成后直接进入 `build_relations`：

```text
repair_lite
build_relations
finalize_output
```

`build_relations` 不要求谓词树产物；缺少 `predicate_entry_list` 时会使用
文本、向量、显式引用和邻接等现有召回通道。

## 实验模式

显式启用 `experimental_logic_ir` 后，顺序变为：

```text
repair_lite
compile_logic_form
normalize_predicates
build_relations
finalize_output
```

此时谓词归一化产物会参与本次关系候选召回。该模式不会在前端设置中展示，
只供后端实验和消融评测使用。

### HTTP API

`POST /api/v2/jobs` 的 JSON 或 multipart 请求可传：

```json
{
  "experimental_logic_ir": true
}
```

省略或传 `false` 时使用 14 阶段默认主线。

### MathKG agent

所有属于同一次实验运行的命令都必须携带：

```text
--experimental-logic-ir
```

`scripts/resume_pipeline_from_stage.py` 默认只恢复 `build_relations` 与
`finalize_output`；只有显式传入 `--experimental-logic-ir` 时，才会扫描并恢复
两个旁路 stage。不要直接编辑规范缓存 JSON。

## 缓存兼容

- 旧 16 阶段固定流水线缓存可在默认恢复时迁移到 14 阶段计划。
- 迁移只保留到 `repair_lite` 为止的连续共享缓存，并重新运行关系提取和最终输出。
- 旧谓词树、谓词归一化及下游目录保留为审计证据，不会被删除或作为默认主线输入。
- 来源、选项或共享缓存校验失败时，恢复仍会拒绝继续。

## 当前定位

| Stage | 默认状态 | 实验用途 |
|---|---|---|
| `compile_logic_form` | 关闭 | 生成节点局部逻辑树 |
| `normalize_predicates` | 关闭 | 归一谓词、函数和语义键 |

除非结构化关系实验达到既定质量门槛，不应重新加入默认生产主线。
