import json


data_template13 = r'''
{
  "node_key": "",
  "node_global_id": "",
  "field_patch": {},
  "repair_log": {
    "applied_repairs": [],
    "skipped_suggestions": [],
    "risk_notes": []
  }
}
'''

prompt_template13 = r'''
你是一名数学结构化节点修复助手。你的任务不是重新抽取命题，而是以 statement.original_form 为唯一事实依据，对 Extraction Layer 做最小、保守、可追踪的结构修复。

========================
【输入】
你将获得一个瘦身后的数学节点 payload，包含：
- node_ref：节点身份信息
- statement：原始表述与 statement_form
- extraction：当前结构化抽取结果
- analysis：analysis_layer 与 repair_suggestion
- structure_snapshot：修复前的 conclusions/subnodes 数量、subnode_specs 和局部条件；这些结构默认必须保留
========================
【任务目标】
analysis_layer 和 repair_suggestion 只是待核验的诊断假设，不是事实。请逐条与 statement.original_form 核对，只有 original_form 直接支持的建议才能进入 field_patch。

修复重点包括：

1. 条件补全
只有 repair_suggestion.suggested_conditions 中的内容在 original_form 中明确出现、且 extraction 确实遗漏时，才可以加入 conditions。
对 conditions 的 patch 必须是完整新列表：逐项原样保留原列表，再在末尾追加遗漏项。不得删除、改写或重排旧条件。
如果节点有多个 subnode，只有明确作用于全部结论的全局条件才允许追加；局部适用范围不明时必须跳过。

2. 定义依赖补全
只有 original_form 在当前节点内明确给出定义内容、且 extraction 遗漏时，才可以追加到 context。
只出现术语名称、依赖前文定义、或 analysis 根据数学常识补出的定义，一律不得应用。
不得用标准定义纠正作者定义，不得猜测 [x] 等符号含义。

3. 边界修复
本阶段只允许保留旧项并追加 original_form 明示的遗漏项，不允许删除、移动或改写已有 context / conditions / conclusions。边界问题需要删除或移动字段时，跳过并写入 risk_notes。

4. 多结论修复
如果 analysis_layer.structural_analysis 指出当前节点可能包含多个并列结论，可以将 conclusions 拆成多条。
conclusions patch 必须返回修复后的完整列表：所有未拆分结论必须保持原 id 和原文本，只能用至少两个新结论替换一个现有结论。
在 repair_log.applied_repairs 的 replaces_ids 中写出被拆分的旧 conclusion id。不得删除、合并或改写其他结论。
不要拆分只是语法上并列但数学上不可分割的表达。若不能精确保持其他结论，跳过修复。

5. statement_form 修复
如果 analysis_layer.structural_analysis 明确指出命题本质更像 equality / equivalence / characterization / existence，可以修正 statement_form。
只有 original_form 中存在明确形式标志时才可修正；如果节点已有多个 subnode 或判断不确定，保持原值。

========================
【严格约束】
1. 不要返回完整节点，只返回 patch。
2. original_form 是唯一事实依据。不得创造原文没有的数学内容，不得把 analysis_layer 或 repair_suggestion 本身当作证据。
3. 不要修改 node_key、global_id、label、title、proof 等身份字段。
4. field_patch 只能包含 statement_form、subject、context、variables、conditions、conclusions。
5. field_patch 必须是稀疏对象：只放真正修改的字段；未修改字段不得出现。没有可靠修复时输出 {{}}。
6. subject、context、variables、conditions、conclusions 的值必须都是数组；除 conclusions 的受控拆分外，数组 patch 必须完整保留旧项并只在末尾追加新项。
7. 禁止使用外部知识、标准定义、常见约定、上下文猜测来补全对象类型、定义、条件或结论。
8. 每个 field_patch 字段必须在 applied_repairs 中恰好对应一条对象记录：
   - field：被修改字段
   - operation：subject/context/variables/conditions 只能为 append；conclusions 只能为 split；statement_form 只能为 replace
   - replaces_ids：仅 conclusions split 使用，且必须包含被拆分的一个旧 conclusion id；其他操作输出 []
   - source_evidence：从 statement.original_form 原样摘录、能直接支持该修复的非空片段
   - reason：简要说明 extraction 遗漏了什么
9. 如果 source_evidence 不能从 original_form 中直接找到，必须跳过。
10. 如果某条 repair_suggestion 风险较高或证据不足，不要应用，写入 repair_log.skipped_suggestions。
11. 如果没有可靠修复，输出空 field_patch，只填写 repair_log。
12. 多子节点的局部条件和未修改 subnode_specs 必须保持原样，不得把局部条件提升成全局条件。
13. 输出必须是合法 JSON，不要输出解释文字。

========================
【输出格式】
你必须输出符合以下格式的 JSON：

{data_template}

请原样返回 node_ref.node_key 和 node_ref.global_id，并只输出 node_key、node_global_id、field_patch 和 repair_log 对应的合法 JSON，不要输出解释文字，下面是我给你的数学节点：{pos1}。
'''

correction_prompt13 = r'''
你是一名严格的 JSON 校对员。请把下面的大模型输出修正为合法 JSON，并确保它符合指定格式。

指定格式是：
{data_template}

待修正内容是：
{answer}

要求：
1. 只输出合法 JSON。
2. 必须包含 node_key、node_global_id、field_patch 和 repair_log。
3. field_patch 只能包含 statement_form、subject、context、variables、conditions、conclusions。
4. field_patch 必须是稀疏对象，不得补入空字段。
5. 不得创造缺失的 source_evidence 或修复内容；原输出缺失时保留空 patch。
6. 不要补充解释文字。
'''


def validation13(text):
    if not isinstance(text, dict):
        return False

    if not isinstance(text.get("node_key"), str) or not text.get("node_key").strip():
        return False

    try:
        json.dumps(text, ensure_ascii=False)
    except TypeError:
        return False

    field_patch = text.get("field_patch")
    if not isinstance(field_patch, dict):
        return False

    repair_log = text.get("repair_log")
    if not isinstance(repair_log, dict):
        return False

    allowed_patch_fields = {
        "statement_form",
        "subject",
        "context",
        "variables",
        "conditions",
        "conclusions",
    }
    if any(key not in allowed_patch_fields for key in field_patch):
        return False

    for field_name, value in field_patch.items():
        if field_name == "statement_form":
            if not isinstance(value, str) or not value.strip():
                return False
        elif not isinstance(value, list) or not value:
            return False

    applied_repairs = repair_log.get("applied_repairs")
    if not isinstance(applied_repairs, list):
        return False
    if any(not isinstance(repair_log.get(name), list) for name in ("skipped_suggestions", "risk_notes")):
        return False

    expected_operations = {
        "statement_form": "replace",
        "subject": "append",
        "context": "append",
        "variables": "append",
        "conditions": "append",
        "conclusions": "split",
    }
    repair_fields = []
    for repair in applied_repairs:
        if not isinstance(repair, dict):
            return False
        field_name = repair.get("field")
        if field_name not in allowed_patch_fields:
            return False
        if repair.get("operation") != expected_operations[field_name]:
            return False
        if not isinstance(repair.get("replaces_ids"), list):
            return False
        if field_name == "conclusions":
            if len(repair["replaces_ids"]) != 1 or not all(
                isinstance(item, str) and item.strip() for item in repair["replaces_ids"]
            ):
                return False
        elif repair["replaces_ids"]:
            return False
        if not isinstance(repair.get("source_evidence"), str) or not repair["source_evidence"].strip():
            return False
        if not isinstance(repair.get("reason"), str) or not repair["reason"].strip():
            return False
        repair_fields.append(field_name)

    if sorted(repair_fields) != sorted(field_patch):
        return False

    return True
