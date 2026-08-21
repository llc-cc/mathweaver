data_template12 = '''
{{
  "candidate_group_id": "predicate_candidate_group_xxxxxx",
  "clusters": [
    {{
      "cluster_id": "cluster_xxxxxx",
      "member_entry_uids": ["..."],
      "canonical_pred_id": "P_...",
      "canonical_name": "...",
      "arity": 0,
      "arg_sorts": [],
      "aliases": ["..."],
      "reason": "..."
    }}
  ],
  "singleton_entry_uids": ["..."],
  "notes": ""
}}
'''

prompt_template12 = r'''
你是一个负责数学逻辑中间表示层构建的助手。
你的任务是：对单个 predicate candidate group（谓词候选池）中的 predicate entries 进行簇划分（clustering）。
这些 predicate entries 已经由程序侧预处理筛选过，因此它们“可能”可以归并，但并不代表一定属于同一个谓词。你的职责是在组内判断：
- 哪些 entry 表达的是同一个谓词，可以归为同一个 cluster
- 哪些 entry 不能和任何其他 entry 稳定归并，应保留为 singleton

这里的“同一个谓词”指的是：
- 在做过符号抽象之后，它们表达的是同一个语义关系
- 它们可以共享同一个 canonical_pred_id 和 canonical_name
- 它们的 arity 必须一致
- 它们的 arg_sorts 必须兼容
- 仅仅因为变量名不同、局部符号编号不同、表面写法略有差异，不应阻止归并

你必须遵守以下规则：

1. 不要因为两个 entry 主题相近、领域相近、常一起出现，就把它们归并。
2. 如果两个 entry 存在“强弱不同、一般/特殊不同、条件不同、方向不同、定义方式不同、语义角色不同”等情况，则不能归并。
3. 即使两个 entry 表面词汇非常相似，只要语义关系不同，也不能归并。
4. 同一个 cluster 内的成员必须能够共享同一个标准谓词，不允许把语义上不同的关系硬合并。
5. 对没有把握的情况，采取保守策略：宁可不合并，也不要误合并。
6. candidate group 只是局部候选池，不是最终等价类，不能默认整组可合并。
7. 你不需要重新发明新的数学知识，只能依据输入的 entry 内容、模板、签名、释义和上下文做判断。
8. 输出必须是合法 JSON，且只能输出 JSON，不要输出解释性文字，不要输出 markdown 代码块。

输出 JSON 必须严格符合如下格式：
{data_template}
字段要求如下：
- candidate_group_id: 直接沿用输入中的 candidate_group_id
- clusters: 可归并簇列表
- cluster_id: 在当前输出中自行生成，保证唯一即可
- member_entry_uids: 属于该簇的 entry uid 列表
- canonical_pred_id: 你为该簇选择的标准谓词 id，命名应简洁稳定，使用大写下划线风格，例如 P_CONTINUOUS_ON
- canonical_name: 你为该簇选择的标准谓词名，使用 PascalCase，例如 ContinuousOn
- arity: 该簇标准谓词的参数个数
- arg_sorts: 该簇标准谓词的参数类型列表
- aliases: 该簇可接受的表面模板列表，应该是抽象后的模板，而不是具体实例文本
- reason: 简洁说明为什么这些成员可以归并
- singleton_entry_uids: 不能稳定归并的 entry uid 列表
- notes: 可留空；如果有必要，可写该候选池中存在的歧义、边界问题或保守处理说明

额外要求：

1. 每个输入 entry 必须且只能出现一次：
   - 要么出现在某个 cluster 的 member_entry_uids 中
   - 要么出现在 singleton_entry_uids 中
2. 不允许遗漏 entry
3. 不允许重复分配 entry
4. 不允许输出空 cluster
5. 如果某个 cluster 只有 1 个成员，不要把它放进 clusters，应放入 singleton_entry_uids
6. canonical_pred_id、canonical_name、arity、arg_sorts 必须与该簇全部成员兼容
7. aliases 应尽量抽象为模板形式，例如：
   - "{{FUNC}} is continuous on {{INTERVAL}}"
   - "{{SET}} is compact"
   而不是保留 FUNC_1、VAR_2 这类局部编号
8. reason 要聚焦“为什么可归并”，不能只写“语义相似”这类空泛表述

示例
Input:
{{
  "candidate_group_id": "predicate_candidate_group_000001",
  "entries": [
    {{
      "entry_uid": "1:0",
      "pred_id": "P_CONTINUOUS_ON_TMP_NODE_1",
      "canonical_name": "ContinuousOn",
      "surface_forms": ["FUNC_1 is continuous on [VAR_1, VAR_2]"],
      "surface_template": "{{FUNC}} is continuous on {{INTERVAL}}",
      "arity": 2,
      "arg_sorts": ["Function", "Interval"],
      "status": "generated",
      "gloss": "F is continuous on interval I",
      "introduced_by_node": "node_1",
      "node_type": "定理",
      "usage_context": "condition"
    }},
    {{
      "entry_uid": "3:2",
      "pred_id": "P_CONTINUOUS_ON_TMP_NODE_3",
      "canonical_name": "ContinuousOn",
      "surface_forms": ["FUNC_2 is continuous on [VAR_3, VAR_4]"],
      "surface_template": "{{FUNC}} is continuous on {{INTERVAL}}",
      "arity": 2,
      "arg_sorts": ["Function", "Interval"],
      "status": "generated",
      "gloss": "F is continuous on interval I",
      "introduced_by_node": "node_3",
      "node_type": "定义",
      "usage_context": "defined_predicate"
    }},
    {{
      "entry_uid": "8:1",
      "pred_id": "P_UNIFORMLY_CONTINUOUS_ON_TMP_NODE_8",
      "canonical_name": "UniformlyContinuousOn",
      "surface_forms": ["FUNC_5 is uniformly continuous on [VAR_7, VAR_8]"],
      "surface_template": "{{FUNC}} is uniformly continuous on {{INTERVAL}}",
      "arity": 2,
      "arg_sorts": ["Function", "Interval"],
      "status": "generated",
      "gloss": "F is uniformly continuous on interval I",
      "introduced_by_node": "node_8",
      "node_type": "定义",
      "usage_context": "defined_predicate"
    }}
  ]
}}
Output:
{{
  "candidate_group_id": "predicate_candidate_group_000001",
  "clusters": [
    {{
      "cluster_id": "cluster_000001",
      "member_entry_uids": ["1:0", "3:2"],
      "canonical_pred_id": "P_CONTINUOUS_ON",
      "canonical_name": "ContinuousOn",
      "arity": 2,
      "arg_sorts": ["Function", "Interval"],
      "aliases": ["{{FUNC}} is continuous on {{INTERVAL}}"],
      "reason": "两条 entry 在符号抽象后表达同一语义关系，surface_template、arity、arg_sorts 和 gloss 均一致，仅局部符号编号与来源节点不同。"
    }}
  ],
  "singleton_entry_uids": ["8:1"],
  "notes": "UniformlyContinuousOn 与 ContinuousOn 虽然领域接近，但语义更强，不能归并。"
}}
以上是示例，不要在输出中使用。
下面是我给你的节点候选组：{pos1}请你按照上述要求进行簇划分，并输出 JSON。
'''

correction_prompt12 = r'''
    你是一个严谨的校对员。我将给你一个由大模型生成的数据结构，请你根据规定格式内容进行校对和修正。
    校对的格式是：
    {data_template}
    以下是待校验的文本：{answer}，请你帮我校对和修正这段内容。
'''


def validation12(result):
    return True
