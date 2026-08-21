data_template07 = '''
    {{
    "契约版本":2,
    "出发节点":"后置节点global_id",
    "到达节点":"前置节点global_id",
    "关系":"逻辑依赖/无依赖",
    "依赖判据":"premise_support/explicit_reference/proof_step_support/goal_rewrite/intermediate_lemma/case_bridge/structural_property/none",
    "证据等级":"explicit/direct/indirect/none",
    "匹配证据":[
      {{
        "A字段":"title/label/content/source_original_form/conditions/conclusions/proof/derivation_context",
        "A片段":"前置节点 A 中的原文证据",
        "B字段":"title/label/content/source_original_form/conditions/conclusions/proof/derivation_context",
        "B片段":"后置节点 B 中的原文证据",
        "作用":"A 如何支持 B 的具体子目标或证明步骤"
      }}
    ],
    "变量对应":{{}},
    "缺失前提":[],
    "排除检查":{{
      "仅主题相似":false,
      "仅定义使用":false,
      "方向冲突":false,
      "作用域冲突":false,
      "使用外部知识":false
    }},
    "发布状态":"accepted/review/rejected",
    "置信度":0.0,
    "理由":"基于输入证据的简要判断"
    }}
'''

data_template07_nl = '''
    {{
    "契约版本":2,
    "出发节点":"后置节点global_id",
    "到达节点":"前置节点global_id",
    "关系":"逻辑依赖/无依赖",
    "依赖判据":"premise_support/explicit_reference/proof_step_support/goal_rewrite/intermediate_lemma/case_bridge/structural_property/none",
    "证据等级":"explicit/direct/indirect/none",
    "匹配证据":[
      {{
        "A字段":"content/source_original_form/proof/derivation_context/title/label",
        "A片段":"前置节点 A 中的原文证据",
        "B字段":"content/source_original_form/proof/derivation_context/title/label",
        "B片段":"后置节点 B 中的原文证据",
        "作用":"A 如何支持 B 的具体子目标或证明步骤"
      }}
    ],
    "变量对应":{{}},
    "缺失前提":[],
    "排除检查":{{
      "仅主题相似":false,
      "仅定义使用":false,
      "方向冲突":false,
      "作用域冲突":false,
      "使用外部知识":false
    }},
    "发布状态":"accepted/review/rejected",
    "置信度":0.0,
    "理由":"基于输入证据的简要判断"
    }}
'''

prompt_template07 = '''
你是一名严谨的数学知识图谱依赖审查员。你需要判断前置节点 A 是否为后置节点 B
提供了可定位、方向正确且不依赖外部知识的逻辑支持。
输入包含两个节点：
【前置节点 A】：
{pos1}
【后置节点 B】：
{pos2}
------------------------------------------------
【核心任务】
判断：B 是否在逻辑上依赖 A。
------------------------------------------------
【允许的逻辑依赖判据】
以下判据彼此独立，只要一项成立且通过全部排除检查，即可认为存在候选逻辑依赖：
1. premise_support：
   A 的结论满足、推出或加强 B 的某个条件。这包括但不限于
   A.conclusion_i → B.condition_j；它只是判据之一，不是逻辑依赖的完整定义。
2. explicit_reference：
   B 的陈述、证明或 derivation_context 明确引用 A 的标题、标签、结论或公式。
3. proof_step_support：
   A 直接解释 B 的 proof、derivation_context 或原文中的某一步变形、等式或推导。
4. goal_rewrite：
   A 可直接用于改写 B 的目标、结论、等式或等价形式。
5. intermediate_lemma：
   A 提供 B 的推导中明确需要的中间命题；必须指出 B 中被支持的具体步骤。
6. case_bridge：
   A 提供等价性、分类、存在性或唯一性桥梁，使 B 的分情况结论成立。
7. structural_property：
   A 证明 B 推导所需的同一对象的结构性质。
------------------------------------------------
【证据优先级】
PRIMARY：
- A：conclusions、content、source_original_form、title、label
- B：proof、derivation_context、source_original_form、content、conditions、conclusions
SECONDARY：
- subject、context、variables、logic_ast_local、predicate_entries

只能使用输入中实际出现的信息。数学常识不能代替 A、B 两侧的原文证据。
------------------------------------------------
【肯定判定的必要条件】
1. 至少命中一个允许判据。
2. “匹配证据”必须同时包含 A、B 的非空原文片段，并标明实际字段。
3. 变量、对象、量词和适用范围必须兼容；必要时填写“变量对应”。
4. A 至少支持 B 的一个可识别子目标或证明步骤；不要求 A 单独证明整个 B。
5. “缺失前提”必须为空，才能发布 accepted。
6. 关系方向必须是 B 依赖 A；输出仍为“出发节点=B，到达节点=A”。
------------------------------------------------
【必须执行的排除检查】
出现任一情况时不得发布逻辑边：
- 只有主题、符号或术语相似。
- 仅定义使用：A 仅定义了 B 使用的概念；这属于定义依赖。
- A 只是背景、动机、叙述顺序相邻或 B 之后才得到的结论。
- 变量映射、量词范围、局部条件或对象类型不兼容。
- 建立依赖需要补充输入中不存在的定理或数学事实。
- 实际方向是 B 能推出 A，而不是 A 支持 B。
- A、B 只是重复表达相似结论，没有前置到后置的推导作用。
------------------------------------------------
【分层发布】
- accepted：
  判据成立，证据等级为 explicit 或 direct，双方证据完整，排除检查均为 false，
  且缺失前提为空。输出“逻辑依赖”。
- review：
  存在合理但间接的依赖，或证据/变量映射不完整。证据等级填 indirect，
  输出“逻辑依赖”，但发布状态填 review。
- rejected：
  没有合法判据或触发排除项。输出“无依赖”，依赖判据和证据等级填 none，
  发布状态填 rejected。

置信度只用于诊断，不能替代证据和排除检查。
------------------------------------------------
【输出格式】
{data_template}
------------------------------------------------
【输出要求】
1. "出发节点"：填写 B 的 global_id  
2. "到达节点"：填写 A 的 global_id  
3. 仅输出 JSON  
4. 契约版本固定填写 2
5. accepted 的匹配证据必须能在输入字段中逐字或忽略空白后定位
6. 不得把定义使用标为逻辑依赖
------------------------------------------------
'''

prompt_template07_nl = '''
你是一名严谨的数学知识图谱依赖审查员，需要基于自然语言原文判断前置节点 A
是否为后置节点 B 提供了可定位、方向正确且不依赖外部知识的逻辑支持。
输入包含两个节点：
【前置节点 A】：
{pos1}
【后置节点 B】：
{pos2}
------------------------------------------------
【核心任务】
判断：B 是否在逻辑上依赖 A。
------------------------------------------------
【允许的判据】
1. premise_support：A 的结论满足或加强 B 的前提。
2. explicit_reference：B 明确引用 A 的标题、标签、结论或公式。
3. proof_step_support：A 直接解释 B 的某一步证明或计算。
4. goal_rewrite：A 可直接改写 B 的目标、结论或等式。
5. intermediate_lemma：A 是 B 推导中可定位的中间引理。
6. case_bridge：A 提供等价性、分类、存在性或唯一性桥梁。
7. structural_property：A 证明 B 所需的同一对象的结构性质。

A.conclusion → B.condition 只是 premise_support 的一种形式，不是唯一判据。
------------------------------------------------
【判断要求】
1. 优先检查 B 的 proof、derivation_context、source_original_form 和 content，
   再检查 conditions、conclusions。
2. accepted 必须同时指出 A、B 两侧可定位的原文证据。
3. 检查变量、对象、量词、作用域和方向。
4. 只能使用输入信息，不得用外部数学常识补写证据。
5. 定义使用必须交给定义依赖，不能作为逻辑依赖。
------------------------------------------------
【排除情况】
- 只有主题、符号或术语相似。
- 仅定义使用：A 只定义了 B 使用的概念。
- A 只是背景、动机或后续结论。
- 变量或适用范围不兼容。
- 需要输入中没有的额外定理。
- 实际推导方向相反。
- 只是重复表达，没有推导作用。
------------------------------------------------
【分层发布】
- accepted：explicit/direct 证据完整、无缺失前提、排除检查均为 false。
- review：间接合理但证据或变量映射不完整。
- rejected：无合法判据或触发任一排除项。

accepted/review 输出“逻辑依赖”；rejected 输出“无依赖”。
------------------------------------------------
【输出格式】
{data_template}
------------------------------------------------
【输出要求】
1. "出发节点"：填写 B 的 global_id
2. "到达节点"：填写 A 的 global_id
3. 仅输出 JSON
4. 契约版本固定填写 2
5. 理由要简短，并与匹配证据一致
------------------------------------------------
'''

data_template07_formalization = '''
    {{
    "出发节点":"后置节点global_id",
    "到达节点":"前置节点global_id",
    "关系":"逻辑依赖/无依赖",
    "理由":"基于形式化用途的简要判断",
    "依赖类型":"premise_support/goal_rewrite/intermediate_lemma/type_constraint/structural_property/symbol_resolution/none",
    "形式化用途":"apply/rw/have/exact/use_as_assumption/typeclass_hint/unfold/none",
    "依赖强度":"direct/indirect/weak/none",
    "匹配证据":[
      {{
        "A字段":"conclusions",
        "A片段":"A 中可用于形式化的信息片段",
        "B字段":"conditions",
        "B片段":"B 中被支持的形式化片段",
        "匹配方式":"semantic_equivalent/stronger_to_weaker/provides_required_property/provides_intermediate_step/provides_type_constraint"
      }}
    ],
    "变量对应":{{}},
    "缺失前提":[],
    "置信度":0.0
    }}
'''

data_template07_formalization_nl = '''
    {{
    "出发节点":"后置节点global_id",
    "到达节点":"前置节点global_id",
    "关系":"逻辑依赖/无依赖",
    "理由":"基于自然语言原文与形式化用途的简要判断",
    "依赖类型":"premise_support/goal_rewrite/intermediate_lemma/type_constraint/structural_property/symbol_resolution/none",
    "形式化用途":"apply/rw/have/exact/use_as_assumption/typeclass_hint/unfold/none",
    "依赖强度":"direct/indirect/weak/none",
    "匹配证据":[
      {{
        "A字段":"content/proof",
        "A片段":"A 中可用于形式化的信息片段",
        "B字段":"content/proof",
        "B片段":"B 中被支持的形式化片段",
        "匹配方式":"semantic_equivalent/stronger_to_weaker/provides_required_property/provides_intermediate_step/provides_type_constraint"
      }}
    ],
    "变量对应":{{}},
    "缺失前提":[],
    "置信度":0.0
    }}
'''

prompt_template07_formalization = '''
你是一名面向自动形式化的数学依赖分析助手。你需要判断前置节点 A 是否能在后置节点 B 的形式化过程中提供可用信息。
输入包含两个节点：
【前置节点 A】：
{pos1}
【后置节点 B】：
{pos2}
------------------------------------------------
【核心任务】
判断：A 是否能作为 B 的形式化辅助信息。
这里的目标不是只构建知识图谱边，而是识别 A 在生成 Lean/Coq/Isabelle 等形式化陈述或证明草图时是否有实际用途。
------------------------------------------------
【可判为逻辑依赖的形式化用途】
如果 A 至少满足下列一种用途，则判为 "逻辑依赖"：
1. premise_support：A 的结论可满足或补强 B 的某个条件、前提或隐含约束。
2. goal_rewrite：A 的结论可用于改写 B 的目标、等式、等价命题或规范形式。
3. intermediate_lemma：A 可作为证明 B 时需要先建立的中间引理。
4. type_constraint：A 提供 B 中变量、结构、对象的类型、实例或性质约束。
5. structural_property：A 提供 B 中对象需要的结构性质，如有限维、交换性、正规性、连续性等。
6. symbol_resolution：A 能解释 B 中某个符号、谓词或构造的含义。
------------------------------------------------
【优先使用字段】
PRIMARY：
- A.conclusions[*].text_normalized / text
- B.conditions[*].text_normalized / text
- B.conclusions[*].text_normalized / text
SECONDARY：
- subject、context、variables、analysis_layer、logic_ast_local、predicate_entries
FALLBACK：
- content、proof
------------------------------------------------
【判断流程】
1. 识别 B 的形式化目标需要哪些变量、前提、类型约束和结论。
2. 识别 A 的结论或结构信息能否用于 B 的形式化。
3. 若能使用，标明依赖类型和形式化用途，例如 apply、rw、have、exact、use_as_assumption、typeclass_hint、unfold。
4. 给出 A 与 B 中符号或变量的对应关系；无法确定则输出空对象 {{}}。
5. 如果使用 A 还需要额外条件，请列入 "缺失前提"；没有则输出 []。
6. 如果只是主题相似、术语共现或背景相关，但不能转化为明确形式化动作，判为 "无依赖" 或将依赖强度设为 "weak"。
------------------------------------------------
【输出格式】
{data_template}
------------------------------------------------
【输出要求】
1. "出发节点"：填写 B 的 global_id
2. "到达节点"：填写 A 的 global_id
3. 仅输出 JSON
4. 判为 "无依赖" 时，依赖类型、形式化用途、依赖强度分别填 "none"，匹配证据和缺失前提输出空列表
5. 不要编造不存在的数学结论；证据不足时降低置信度
------------------------------------------------
'''

prompt_template07_formalization_nl = '''
你是一名面向自动形式化的数学依赖分析助手。你需要基于自然语言原文判断前置节点 A 是否能在后置节点 B 的形式化过程中提供可用信息。
输入包含两个节点：
【前置节点 A】：
{pos1}
【后置节点 B】：
{pos2}
------------------------------------------------
【核心任务】
判断：A 是否能作为 B 的形式化辅助信息。
这里的目标不是只构建知识图谱边，而是识别 A 在生成 Lean/Coq/Isabelle 等形式化陈述或证明草图时是否有实际用途。
------------------------------------------------
【可判为逻辑依赖的形式化用途】
如果 A 至少满足下列一种用途，则判为 "逻辑依赖"：
1. premise_support：A 的命题结论可满足或补强 B 的某个条件、前提或隐含约束。
2. goal_rewrite：A 可用于改写 B 的目标、等式、等价命题或规范形式。
3. intermediate_lemma：A 可作为证明 B 时需要先建立的中间引理。
4. type_constraint：A 提供 B 中变量、结构、对象的类型、实例或性质约束。
5. structural_property：A 提供 B 中对象需要的结构性质。
6. symbol_resolution：A 能解释 B 中某个符号、谓词或构造的含义。
------------------------------------------------
【判断流程】
1. 综合阅读 A 和 B 的 content / proof / title 等自然语言信息。
2. 识别 B 的形式化目标需要哪些变量、前提、类型约束和结论。
3. 判断 A 中是否存在可直接用于 B 形式化的结论、定义性说明或结构性质。
4. 若能使用，标明依赖类型和形式化用途，例如 apply、rw、have、exact、use_as_assumption、typeclass_hint、unfold。
5. 给出 A 与 B 中符号或变量的对应关系；无法确定则输出空对象 {{}}。
6. 如果只是主题相似、术语共现或背景相关，但不能转化为明确形式化动作，判为 "无依赖" 或将依赖强度设为 "weak"。
------------------------------------------------
【输出格式】
{data_template}
------------------------------------------------
【输出要求】
1. "出发节点"：填写 B 的 global_id
2. "到达节点"：填写 A 的 global_id
3. 仅输出 JSON
4. 判为 "无依赖" 时，依赖类型、形式化用途、依赖强度分别填 "none"，匹配证据和缺失前提输出空列表
5. 不要编造不存在的数学结论；证据不足时降低置信度
------------------------------------------------
'''

correction_prompt07 = '''
    你是一个严谨的 JSON 契约校对员。请修正格式，不得凭空增加数学依赖或证据。

    校对的格式是：
    {data_template}

    规则：
    - 契约版本必须为 2。
    - accepted 必须是“逻辑依赖”、证据等级 explicit/direct、双方证据非空、
      缺失前提为空、排除检查全部为 false。
    - review 必须是“逻辑依赖”，证据等级为 indirect。
    - rejected 必须是“无依赖”，依赖判据和证据等级为 none。
    - 不确定或缺少证据时只能降为 review/rejected，不能补造证据。

    以下是待校验的文本：{answer}
    仅输出修正后的 JSON。
    '''


correction_prompt07_formalization = '''
    你是一个严谨的校对员。我将给你一个由大模型生成的数据结构，请你根据规定格式内容进行校对和修正。

    校对的格式是：
    {data_template}

    以下是待校验的文本：{answer}。仅输出修正后的 JSON。
    '''


LOGIC_DEPENDENCY_CRITERIA = {
    "premise_support",
    "explicit_reference",
    "proof_step_support",
    "goal_rewrite",
    "intermediate_lemma",
    "case_bridge",
    "structural_property",
}
LOGIC_EVIDENCE_LEVELS = {"explicit", "direct", "indirect", "none"}
LOGIC_PUBLICATION_STATUSES = {"accepted", "review", "rejected"}
LOGIC_EVIDENCE_FIELDS = {
    "title",
    "label",
    "content",
    "source_original_form",
    "conditions",
    "conclusions",
    "proof",
    "derivation_context",
}
LOGIC_EXCLUSION_KEYS = {
    "仅主题相似",
    "仅定义使用",
    "方向冲突",
    "作用域冲突",
    "使用外部知识",
}


def _valid_logic_evidence_item(item):
    return (
        isinstance(item, dict)
        and item.get("A字段") in LOGIC_EVIDENCE_FIELDS
        and isinstance(item.get("A片段"), str)
        and item.get("A片段", "").strip()
        and item.get("B字段") in LOGIC_EVIDENCE_FIELDS
        and isinstance(item.get("B片段"), str)
        and item.get("B片段", "").strip()
        and isinstance(item.get("作用"), str)
        and item.get("作用", "").strip()
    )


def validation07(value):
    if not isinstance(value, dict) or value.get("契约版本") != 2:
        return False
    relation = value.get("关系")
    criterion = value.get("依赖判据")
    evidence_level = value.get("证据等级")
    publication_status = value.get("发布状态")
    evidence = value.get("匹配证据")
    variable_mapping = value.get("变量对应")
    missing_premises = value.get("缺失前提")
    exclusions = value.get("排除检查")
    confidence = value.get("置信度")

    if relation not in {"逻辑依赖", "无依赖"}:
        return False
    if criterion not in LOGIC_DEPENDENCY_CRITERIA | {"none"}:
        return False
    if evidence_level not in LOGIC_EVIDENCE_LEVELS:
        return False
    if publication_status not in LOGIC_PUBLICATION_STATUSES:
        return False
    if not isinstance(evidence, list) or not isinstance(variable_mapping, dict):
        return False
    if not isinstance(missing_premises, list) or not isinstance(exclusions, dict):
        return False
    if set(exclusions) != LOGIC_EXCLUSION_KEYS:
        return False
    if not all(isinstance(flag, bool) for flag in exclusions.values()):
        return False
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return False
    if not 0.0 <= float(confidence) <= 1.0:
        return False
    if not isinstance(value.get("理由"), str) or not value.get("理由", "").strip():
        return False

    if publication_status == "accepted":
        return (
            relation == "逻辑依赖"
            and criterion in LOGIC_DEPENDENCY_CRITERIA
            and evidence_level in {"explicit", "direct"}
            and bool(evidence)
            and all(_valid_logic_evidence_item(item) for item in evidence)
            and not missing_premises
            and not any(exclusions.values())
        )
    if publication_status == "review":
        return (
            relation == "逻辑依赖"
            and criterion in LOGIC_DEPENDENCY_CRITERIA
            and evidence_level == "indirect"
        )
    return (
        relation == "无依赖"
        and criterion == "none"
        and evidence_level == "none"
    )


def validation07_formalization(value):
    if not isinstance(value, dict):
        return False
    if value.get("关系") not in {"逻辑依赖", "无依赖"}:
        return False
    return isinstance(value.get("理由"), str) and bool(value.get("理由", "").strip())


data_template08 = '''
     {{
    "出发节点":"后置节点global_id",
    "到达节点":"前置节点global_id",
    "关系":"定义依赖/无依赖",
    "理由":"基于原文的简要判断"
    }}
'''

data_template08_nl = '''
     {{
    "出发节点":"后置节点global_id",
    "到达节点":"前置节点global_id",
    "关系":"定义依赖/无依赖",
    "理由":"基于原文自然语言内容的简要判断"
    }}
'''

prompt_template08 = '''
你是一名数学知识图谱构建助手，需要判断两个数学节点之间是否存在定义依赖关系。
输入包含两个节点：
【前置节点 A（定义节点）】：
{pos1}
【后置节点 B】：
{pos2}
------------------------------------------------
【核心任务】
判断：B 是否在定义上依赖 A。
------------------------------------------------
【核心定义（必须遵循）】
定义依赖指：
如果前置节点 A 中定义的数学概念，
在后置节点 B 的表达中被使用或引用，
则认为存在定义依赖。
------------------------------------------------
【判断流程（必须执行）】
1. 识别 A 中定义的核心概念（concept）
   - 如果 A 是原样定义/公理节点，优先使用 title 和 content
   - 如果 A 是结构化节点，可结合 subject / context / conditions
2. 在 B 的以下位置查找该概念是否出现：
   - subject（normalized，如存在）
   - context（normalized，如存在）
   - conditions[*].text_normalized（如存在）
   - conclusions[*].text_normalized（如存在）
   - title / content（作为缺失结构化字段时的回退）
------------------------------------------------
【输出格式】
{data_template}
------------------------------------------------
【输出要求】
1. "出发节点"：填写 B 的 global_id  
2. "到达节点"：填写 A 的 global_id  
3. 仅输出 JSON  
4. 理由必须简短说明后置节点中使用了哪个定义概念  
------------------------------------------------
'''

prompt_template08_nl = '''
你是一名数学知识图谱构建助手，需要基于自然语言原文判断两个数学节点之间是否存在定义依赖关系。
输入包含两个节点：
【前置节点 A（定义节点）】：
{pos1}
【后置节点 B】：
{pos2}
------------------------------------------------
【核心任务】
判断：B 是否在定义上依赖 A。
------------------------------------------------
【核心定义（必须遵循）】
定义依赖指：
如果 A 中定义的数学概念在 B 的自然语言描述中被使用或引用，
则认为存在定义依赖。
------------------------------------------------
【判断流程（必须执行）】
1. 直接从 A 的输入文本中识别定义概念，不要求特定字段格式。
2. 在 B 的输入文本中判断该概念是否被使用或引用。
3. 若输入包含多字段，按自然语言整体语义综合判断。
------------------------------------------------
【输出格式】
{data_template}
------------------------------------------------
【输出要求】
1. "出发节点"：填写 B 的 global_id
2. "到达节点"：填写 A 的 global_id
3. 仅输出 JSON
4. 理由必须简短说明后置节点中出现了哪个定义概念
------------------------------------------------
'''

data_template08_formalization = '''
     {{
    "出发节点":"后置节点global_id",
    "到达节点":"前置节点global_id",
    "关系":"定义依赖/无依赖",
    "理由":"基于定义在形式化中如何被使用的简要判断",
    "依赖类型":"definition_dependency/none",
    "形式化用途":"type_check/unfold_definition/notation_resolution/predicate_meaning/structure_field/library_hint/none",
    "匹配证据":[
      {{
        "A字段":"title/content/conditions/conclusions",
        "A片段":"A 中定义概念或定义参数的文本片段",
        "B字段":"subject/context/conditions/conclusions/content/proof",
        "B片段":"B 中使用该定义概念的文本片段",
        "匹配方式":"concept_use/alias_use/parameter_binding/notation_use/predicate_use"
      }}
    ],
    "变量对应":{{}},
    "缺失前提":[],
    "定义概念":"A 中定义的概念名称",
    "概念别名":[],
    "B中使用位置":[
      {{
        "字段":"subject/context/conditions/conclusions/content/proof",
        "片段":"B 中使用该定义概念的文本片段"
      }}
    ],
    "定义使用方式":"type_check/unfold_definition/notation_resolution/predicate_meaning/structure_field/library_hint/none",
    "形式化提示":"该定义对 B 的形式化声明或证明有什么帮助",
    "定义参数":[],
    "参数绑定":{{}},
    "缺失参数":[],
    "依赖强度":"direct/indirect/weak/none",
    "置信度":0.0
    }}
'''

data_template08_formalization_nl = '''
     {{
    "出发节点":"后置节点global_id",
    "到达节点":"前置节点global_id",
    "关系":"定义依赖/无依赖",
    "理由":"基于自然语言原文与定义在形式化中如何被使用的简要判断",
    "依赖类型":"definition_dependency/none",
    "形式化用途":"type_check/unfold_definition/notation_resolution/predicate_meaning/structure_field/library_hint/none",
    "匹配证据":[
      {{
        "A字段":"content/proof/title",
        "A片段":"A 中定义概念或定义参数的文本片段",
        "B字段":"content/proof/title",
        "B片段":"B 中使用该定义概念的文本片段",
        "匹配方式":"concept_use/alias_use/parameter_binding/notation_use/predicate_use"
      }}
    ],
    "变量对应":{{}},
    "缺失前提":[],
    "定义概念":"A 中定义的概念名称",
    "概念别名":[],
    "B中使用位置":[
      {{
        "字段":"content/proof/title",
        "片段":"B 中使用该定义概念的文本片段"
      }}
    ],
    "定义使用方式":"type_check/unfold_definition/notation_resolution/predicate_meaning/structure_field/library_hint/none",
    "形式化提示":"该定义对 B 的形式化声明或证明有什么帮助",
    "定义参数":[],
    "参数绑定":{{}},
    "缺失参数":[],
    "依赖强度":"direct/indirect/weak/none",
    "置信度":0.0
    }}
'''

prompt_template08_formalization = '''
你是一名面向自动形式化的数学定义依赖分析助手。你需要判断前置定义节点 A 是否能在后置节点 B 的形式化过程中提供定义、符号、谓词或类型信息。
输入包含两个节点：
【前置节点 A（定义节点）】：
{pos1}
【后置节点 B】：
{pos2}
------------------------------------------------
【核心任务】
判断：B 是否在形式化上依赖 A 的定义。
这里的目标不是只判断概念是否出现，而是说明 A 的定义如何帮助 B 被形式化。
------------------------------------------------
【可判为定义依赖的使用方式】
如果 A 中定义的概念在 B 中有下列用途之一，则判为 "定义依赖"：
1. type_check：用于确定 B 中对象、变量或结构的类型。
2. unfold_definition：B 的形式化需要展开 A 的定义。
3. notation_resolution：A 解释 B 中符号、记号、缩写或构造。
4. predicate_meaning：A 解释 B 中谓词或性质的语义。
5. structure_field：A 提供结构字段、参数或构造方式。
6. library_hint：A 帮助把 B 中概念映射到形式化库中的定义名或类型类。
------------------------------------------------
【判断流程】
1. 从 A 的 title、content、conditions、conclusions 中识别定义概念、别名和参数。
2. 在 B 的 subject、context、variables、conditions、conclusions、content、proof 中查找该定义是否被使用。
3. 标明定义使用方式、定义参数和参数绑定。
4. 如果 B 使用了概念但缺少定义参数，请列入 "缺失参数"。
5. 将可用于形式化的定义动作同步写入 "形式化用途"，并给出匹配证据和变量对应。
6. 如果只是主题相似或术语泛泛相关，不能说明形式化用途，则判为 "无依赖"。
------------------------------------------------
【输出格式】
{data_template}
------------------------------------------------
【输出要求】
1. "出发节点"：填写 B 的 global_id
2. "到达节点"：填写 A 的 global_id
3. 仅输出 JSON
4. 判为 "无依赖" 时，依赖类型、形式化用途、定义使用方式和依赖强度填 "none"，匹配证据、位置、别名、参数、缺失前提、缺失参数输出空列表
5. 不要编造定义；证据不足时降低置信度
------------------------------------------------
'''

prompt_template08_formalization_nl = '''
你是一名面向自动形式化的数学定义依赖分析助手。你需要基于自然语言原文判断前置定义节点 A 是否能在后置节点 B 的形式化过程中提供定义、符号、谓词或类型信息。
输入包含两个节点：
【前置节点 A（定义节点）】：
{pos1}
【后置节点 B】：
{pos2}
------------------------------------------------
【核心任务】
判断：B 是否在形式化上依赖 A 的定义。
这里的目标不是只判断概念是否出现，而是说明 A 的定义如何帮助 B 被形式化。
------------------------------------------------
【可判为定义依赖的使用方式】
如果 A 中定义的概念在 B 中有下列用途之一，则判为 "定义依赖"：
1. type_check：用于确定 B 中对象、变量或结构的类型。
2. unfold_definition：B 的形式化需要展开 A 的定义。
3. notation_resolution：A 解释 B 中符号、记号、缩写或构造。
4. predicate_meaning：A 解释 B 中谓词或性质的语义。
5. structure_field：A 提供结构字段、参数或构造方式。
6. library_hint：A 帮助把 B 中概念映射到形式化库中的定义名或类型类。
------------------------------------------------
【判断流程】
1. 从 A 的自然语言文本中识别定义概念、别名和参数。
2. 在 B 的自然语言文本中判断该定义概念是否被使用。
3. 标明定义使用方式、定义参数和参数绑定。
4. 如果 B 使用了概念但缺少定义参数，请列入 "缺失参数"。
5. 将可用于形式化的定义动作同步写入 "形式化用途"，并给出匹配证据和变量对应。
6. 如果只是主题相似或术语泛泛相关，不能说明形式化用途，则判为 "无依赖"。
------------------------------------------------
【输出格式】
{data_template}
------------------------------------------------
【输出要求】
1. "出发节点"：填写 B 的 global_id
2. "到达节点"：填写 A 的 global_id
3. 仅输出 JSON
4. 判为 "无依赖" 时，依赖类型、形式化用途、定义使用方式和依赖强度填 "none"，匹配证据、位置、别名、参数、缺失前提、缺失参数输出空列表
5. 不要编造定义；证据不足时降低置信度
------------------------------------------------
'''

correction_prompt08 = '''
    你是一个严谨的校对员。我将给你一个由大模型生成的数据结构，请你根据规定格式内容进行校对和修正。

    校对的格式是：
    {data_template}

    以下是待校验的文本：{answer}，请你帮我校对和修正这段内容。
    '''


def validation08(text):
    return True

