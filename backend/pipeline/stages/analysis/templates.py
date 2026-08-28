data_template09 = r'''
{
  "analysis_layer": {
    "boundary_analysis": {
      "context_condition_notes": [],
      "condition_conclusion_notes": []
    },
    "gap_analysis": {
      "logic_gaps": [],
      "missing_constraints": []
    },
    "definition_analysis": {
      "definitions_referenced": []
    },
    "structural_analysis": {
      "special_form_notes": []
    }
  },
  "repair_suggestion": {
    "suggested_conditions": [],
    "suggested_definitions": [],
    "repair_notes": []
  }
}
'''

prompt_template09 = r'''
你是一名数学命题诊断助手。你的任务不是重新抽取命题，也不是使用数学常识补全作者没有写出的内容，而是在给定的结构化节点基础上，识别 extraction 与原始表述之间有原文证据支持的边界问题、遗漏和特殊结构。

========================
【输入】
你将获得一个已经完成 Extraction Layer 的数学节点，其字段可能包括：
- node_type
- title
- statement_form
- remark.original_form
- subject
- context
- variables
- conditions
- conclusions
- proof
- label

========================
【任务目标】
你需要输出一份 analysis_layer，对当前节点进行二次诊断。重点识别两类问题：

1. extraction issue
指 extraction 结果中的边界不稳定或结构退化，例如：
- context 与 conditions 划分不稳
- condition 与 conclusion 被压平或混淆
- equality / equivalence / proposition family 被粗糙处理
- conclusion 中实际包含多个逻辑部分

2. source-grounded omission
指 remark.original_form 已经明确写出、但 extraction 没有忠实保留的信息，例如：
- 原文明示的变量范围或对象身份被遗漏
- 原文明示的复合对象未被提取
- 原文明示的局部条件被错误地作用于全部结论
- 原文明示的量词、约束或并列结论没有进入结构化字段

数学常识、标准教材定义、上下文中可能存在但当前 original_form 没有提供的定义，不属于可修复遗漏。

========================
【输出格式】
你必须输出一个符合以下格式的合法 JSON：
{data_template}

========================
【字段说明】
1. analysis_layer.boundary_analysis
- context_condition_notes：context 与 condition 是否存在划分歧义
- condition_conclusion_notes：condition 与 conclusion 是否存在边界不稳或结论压平

2. analysis_layer.gap_analysis
- logic_gaps：记录缺失的是哪一类逻辑信息
- missing_constraints：记录可进一步补出的约束、范围或说明

3. analysis_layer.definition_analysis
- definitions_referenced：只记录 original_form 明确引用的定义性术语。若定义内容未在当前 original_form 中出现，只能记录依赖风险，不能据此生成补写内容

4. analysis_layer.structural_analysis
- special_form_notes：记录该节点的特殊逻辑外形，例如 equivalence、equality、existence、multi-conclusion、proposition family

5. repair_suggestion
- suggested_conditions：只建议补回 original_form 中明确出现、但 extraction 遗漏的条件
- suggested_definitions：只建议补回 original_form 在当前节点内明确给出的定义内容；仅出现术语名称时必须为空
- repair_notes：只给出有 original_form 直接证据支持的结构修补建议

========================
【严格约束】
1. 不要重新抽取 subject / context / conditions / conclusions
2. 不要重写原始命题
3. 不要编造新的数学结论
4. 如果某项不存在，输出空列表 []
5. 每条 note / gap / suggestion 尽量简短，使用短句
6. 如果只是 extraction 问题，不要伪装成逻辑补全
7. remark.original_form 是当前任务中唯一的事实依据；extraction 只是待检查结果，title、analysis 常识和标准定义均不能覆盖原文
8. 禁止用“通常”“标准定义”“常见约定”补写原文。例如原文定义 semi-metric 时，不得擅自加入非负性、d(x,x)=0 等未写出的公理
9. 禁止猜测符号含义。例如当前 original_form 没有定义 [x] 时，不得猜测它是 singleton、等价类或其他对象
10. 无法从当前 original_form 确认的跨节点定义依赖，只能记入 analysis_layer 的风险说明，repair_suggestion 必须保持为空

========================
【优先判断规则】
优先检查以下问题：
- conditions 为空，但命题明显不是纯 assertion
- context 很长，且可能吸收了真正前提
- statement_form = implication，但命题本质更像 equality / equivalence / characterization
- variables 中只有裸变量，没有关键复合对象
- conclusions 中只有一条，但原文中可能有并列结论或局部条件
- original_form 明确写出的信息是否在 extraction 中被截断、遗漏或错误扩张

========================
【诊断提示】
你可以参考下面这种思路：
- 若命题本质是 conditional equality，可在 structural_analysis 中指出
- 若复合对象没有进入 variables，可在 logic_gaps 中指出
- 若只出现术语名称而没有给出定义内容，可在 definitions_referenced 中记录，但 suggested_definitions 必须为空
- 若局部条件只约束部分结论，可在 repair_notes 中指出

========================
下面是需要分析的结构化节点：
{pos1}

请只输出 analysis_layer 和 repair_suggestion 对应的合法 JSON，不要输出解释文字。
'''

correction_prompt09 = r'''
你是一个严谨的校对员。我将给你一个由大模型生成的数据结构，请你根据规定格式内容进行校对和修正。

校对的格式是：
{data_template}

以下是待校验的文本：{answer}，请你帮我校对和修正这段内容。
'''


def validation09(text):
    if not isinstance(text, dict):
        return False

    analysis_layer = text.get("analysis_layer")
    repair_suggestion = text.get("repair_suggestion")
    if not isinstance(analysis_layer, dict) or not isinstance(repair_suggestion, dict):
        return False

    analysis_fields = {
        "boundary_analysis": ("context_condition_notes", "condition_conclusion_notes"),
        "gap_analysis": ("logic_gaps", "missing_constraints"),
        "definition_analysis": ("definitions_referenced",),
        "structural_analysis": ("special_form_notes",),
    }
    for section_name, field_names in analysis_fields.items():
        section = analysis_layer.get(section_name)
        if not isinstance(section, dict):
            return False
        if any(not isinstance(section.get(field_name), list) for field_name in field_names):
            return False

    return all(
        isinstance(repair_suggestion.get(field_name), list)
        for field_name in ("suggested_conditions", "suggested_definitions", "repair_notes")
    )
