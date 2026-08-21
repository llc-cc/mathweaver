# Relation Candidate Gold Schema

候选召回评测只接受人工复核的依赖边，不把旧 pipeline 输出当作真值。Gold 文件可使用 JSON 数组或 JSONL，每条记录包含：

```json
{
  "dependent_global_id": "后置节点 global_id",
  "support_global_id": "前置节点 global_id",
  "relation_kind": "logic",
  "explicit": false,
  "dependent_index": 42,
  "support_index": 3
}
```

- `relation_kind`：`logic` 或 `definition`。
- `explicit`：显式引用填 `true`；显式边绕过候选阶段，不计入 candidate recall。
- `dependent_index` / `support_index`：用于统计距离大于 30 的隐式逻辑边。
- 标注时必须针对选中的后置节点检查所有前文节点，不能只确认旧 pipeline 已生成的边，否则无法计算召回率。

运行评测：

```powershell
python scripts/evaluate_relation_candidates.py --candidates relation_candidates.json --gold relation_gold.jsonl --enforce
```

`--enforce` 检查 `candidate recall@30 >= 95%`、长距离逻辑召回率 `>= 90%`、总候选上限 30 和定义候选上限 10。
