data_template11 = """
{{
  "global_id": "node_xxx",
  "logic_ast_local": {{
    "kind": "forall",
    "vars": [
      {{
        "sym_id": "SYM_1",
        "sort": "Type"
      }}
    ],
    "body": {{
      "kind": "imp",
      "left": {{
        "kind": "pred",
        "pred_id": "P_XXX",
        "args": [
          {{
            "kind": "sym_ref",
            "sym_id": "SYM_1"
          }}
        ]
      }},
      "right": {{
        "kind": "pred",
        "pred_id": "P_YYY_TMP_NODE_XXX",
        "args": [
          {{
            "kind": "sym_ref",
            "sym_id": "SYM_1"
          }}
        ]
      }}
    }}
  }},
  "predicate_entries": [
    {{
      "pred_id": "P_XXX",
      "canonical_name": "Xxx",
      "surface_forms": ["..."],
      "surface_template": "...",
      "arity": 1,
      "arg_sorts": ["Type"],
      "status": "builtin",
      "gloss": "..."
    }},
    {{
      "pred_id": "P_YYY_TMP_NODE_XXX",
      "canonical_name": "Yyy",
      "surface_forms": ["..."],
      "surface_template": "...",
      "arity": 1,
      "arg_sorts": ["Type"],
      "status": "generated",
      "introduced_by_node": "node_xxx",
      "gloss": "..."
    }}
  ]
}}

"""

prompt_template11 = """
你是“数学陈述 -> 逻辑 IR”的单节点编译器。你的任务是读取一个节点，并将其编译为该节点的局部逻辑表示。
输入节点只有两类：
1. 定义节点
2. 非定义节点（如定理、命题、引理、公理等）

你的输出必须是一个严格的 JSON 对象，且只能包含以下 3 个顶层字段：
- "global_id"
- "logic_ast_local"
- "predicate_entries"

【总原则】
1. 输入中的数学符号已经完成标准化，不需要再次做符号消歧。
2. 必须严格保留输入中的标准符号名称，例如 FUNCTION_1、VAR_1；不要改写成 FUNC_1、f、x 等其他形式。
3. 对非定义节点，优先依赖结构化字段进行编译：
   - statement_form
   - variables
   - conditions
   - conclusions
   - context
   - subject
   顶层 text_normalized 只作为辅助，不作为主语义来源。
4. 对定义节点，优先结合 title 与 text_normalized 识别“被定义的概念”以及“定义展开式”。
5. 如果某一语义片段无法安全拆成更低层逻辑结构，应优先把它表示为一个领域谓词，不要臆造细节逻辑。
6. 若无法确定某个谓词是否为 builtin，优先标记为 generated。

【输入预处理】
1. 如果是非定义节点，variables 中：
   - normalize_type 对应输出里的 sym_id
   - type 需要映射为 sort
2. sort 映射建议如下：
   - function -> Function
   - real -> Real
   - integer -> Integer
   - natural -> Natural
   - set -> Set
   - sequence -> Sequence
   - point -> Point
   - 未知类型 -> Entity

【非定义节点编译规则】
1. 根据 statement_form 决定顶层逻辑骨架。
2. 若 statement_form = "implication"：
   - 顶层通常编译为 forall
   - body 编译为 imp
   - left 来自 conditions 与必要的 context
   - right 来自 conclusions
3. 若 conditions 有多个，默认用 and 逐层左结合。
4. 若 conclusions 有多个，默认用 and 逐层左结合。
5. subject 主要作为角色提示，不必机械转成谓词。
6. context 只有在其确实表达了逻辑前提、且不与 conditions 重复时，才编入 logic_ast_local。
7. variables 中明确给出的符号，若无相反证据，默认作为 forall 绑定变量。
8. 如果正文中明确出现“存在某对象”之类结构，可在 body 中加入 exists。

【定义节点编译规则】
1. 先识别被定义的概念头，例如 BoundedOn、ContinuousOn、Compact 等。
2. 定义节点的 logic_ast_local 通常编译为：
   - forall(..., iff(定义头谓词, 定义展开式))
   或者在确有需要时使用等价结构的 AST 表示。
3. 被当前定义节点引入的谓词，必须在 predicate_entries 中加入对应条目，status 设为 "defined_here"。
4. 若可以稳定得到定义展开式，应在该 predicate entry 中加入 "definition_ast"。
5. definition_ast 应尽量使用可复用表示：
   - 用 arg_ref 引用被定义谓词的参数位置
   - 不要在 definition_ast 中重复绑定定义头参数
6. 如果正文不足以安全抽取完整 definition_ast，也仍然要产出定义头谓词，并尽量给出局部 logic_ast_local。

【logic_ast_local 结构约定】
1. 固定逻辑算子使用 kind 标识，不进入 predicate_entries。
2. 允许的固定 kind 包括：
   - "forall"
   - "exists"
   - "imp"
   - "iff"
   - "and"
   - "or"
   - "not"
   - "eq"
   - "in"
   - "le"
   - "lt"
   - "app"
   - "abs"
   - "interval"
   - "pred"
   - "sym_ref"
   - "arg_ref"
3. 各类节点建议结构：
   - forall / exists:
     {{"kind":"forall","vars":[...],"body":...}}
   - imp / iff / and / or / eq / le / lt:
     {{"kind":"imp","left":...,"right":...}}
   - not:
     {{"kind":"not","arg":...}}
   - pred:
     {{"kind":"pred","pred_id":"...","args":[...]}}
   - sym_ref:
     {{"kind":"sym_ref","sym_id":"FUNCTION_1"}}
   - interval:
     {{"kind":"interval","left":...,"right":...}}
   - in:
     {{"kind":"in","element":...,"set":...}}
   - app:
     {{"kind":"app","fn":...,"args":[...]}}
   - abs:
     {{"kind":"abs","arg":...}}
   - arg_ref:
     {{"kind":"arg_ref","index":0}}
4. 对二元连接词，统一使用 left / right。
5. 对多重 and / or，采用左结合嵌套结构。
6. forall / exists 的 vars 结构为：
   {{"sym_id":"FUNCTION_1","sort":"Function"}}

【predicate_entries 生成规则】
1. predicate_entries 是一个数组，记录本节点中使用到或引入的领域谓词。
2. 固定逻辑算子不能进入 predicate_entries。
3. 每个谓词条目建议包含以下字段：
   - "pred_id"
   - "canonical_name"
   - "surface_forms"
   - "surface_template"
   - "arity"
   - "arg_sorts"
   - "status"
   - "gloss"
4. 若为当前定义节点引入的谓词，可额外包含：
   - "introduced_by_node"
   - "definition_ast"
5. status 取值规则：
   - "builtin": 明显属于常见且稳定的数学谓词
   - "generated": 本节点局部生成的临时谓词
   - "defined_here": 由当前定义节点引入
6. 临时 pred_id 命名规则：
   - P_<UPPER_SNAKE_NAME>_TMP_<global_id>
   例如：P_BOUNDED_ON_TMP_node_15
7. canonical_name 使用简洁稳定的英文 PascalCase，例如：
   - ContinuousOn
   - BoundedOn
   - DefinedOn
8. surface_template 需要抽象掉具体标准符号，例如：
   - "FUNCTION_1 is bounded on [VAR_1, VAR_2]"
   抽象为：
   - "{{FUNC}} is bounded on {{INTERVAL}}"
9. 同一节点内不要重复输出等价的 predicate entry。

【固定算子与固定关系补充规则】
以下对象是固定逻辑/项构造，不应进入 predicate_entries，也不应生成为 P_XXX 谓词：
1. Div / quotient / 商：使用固定项算子，例如 {{"kind":"div","left":...,"right":...}} 或等价项结构，不要输出 P_DIV。
2. Power / 幂：使用 {{"kind":"power","base":...,"exponent":...}}，不要输出 P_POWER。
3. Order / 阶 / |G|：使用 {{"kind":"order","arg":...}} 或固定项结构，不要输出 P_ORDER。
4. Mul / 乘法：使用 {{"kind":"mul","args":[...]}}，不要输出 P_MUL。
5. 数字常量 1、2：使用 {{"kind":"int","value":1}}、{{"kind":"int","value":2}}，不要输出 P_ONE、P_TWO。
6. Irr(G)：使用集合值项 {{"kind":"app","fn":{{"kind":"sym_ref","sym_id":"Irr"}},"args":[G]}}。
7. χ in Irr(G) / χ is an irreducible character of G：优先用 {{"kind":"in","element":χ,"set":{{"kind":"app","fn":{{"kind":"sym_ref","sym_id":"Irr"}},"args":[G]}} }}，不要输出 P_IRR、P_IN_IRR、P_IRREDUCIBLE_CHARACTER_ON、P_IRREDUCIBLE_CHARACTER_OF 等同义谓词。
8. IndPower / ResPower 是项算子，不是领域谓词；只有表达“等于某个诱导/限制后的项”时，把它放在 eq 的 left/right 中。
9. 普通子集关系使用固定关系 kind="subset" 或 kind="in"/"subset" 风格，不要为普通 Subset 生成 P_SUBSET；但 SubsetKer、SubsetOfIrr 这类带额外结构语义的关系可以保留为领域谓词。
10. 领域谓词只记录真正的数学性质或关系，例如 NormalSubgroup、Subgroup、SubsetKer、Orthogonal、Abelian 等。

【冲突处理】
1. 若顶层 text_normalized 与结构化字段冲突，优先信任结构化字段。
2. 若 context 与 conditions 重复，不要重复编译。
3. 若某个语义片段无法可靠拆解，可保留为 pred 节点，并生成相应 predicate entry。
----------------------
示例
Input:
{{
  "node_type": "定理",
  "global_id": "node_15",
  "standardized_form": "Let FUNC_1 be a continuous function on [VAR_1, VAR_2], then FUNC_1 is bounded on [VAR_1, VAR_2].",
  "statement_form": "implication",
  "variables": [
    {{"name": "FUNC_1", "type": "function", "origin_surface": "f"}},
    {{"name": "VAR_1", "type": "real", "origin_surface": "a"}},
    {{"name": "VAR_2", "type": "real", "origin_surface": "b"}}
  ],
  "conditions": [
    {{"id": "c1", "text": "FUNC_1 is continuous on [VAR_1, VAR_2]"}}
  ],
  "conclusions": [
    {{"id": "q1", "text": "FUNC_1 is bounded on [VAR_1, VAR_2]"}}
  ]
}}
Output:
{{
  "global_id": "node_15",
  "logic_ast_local": {{
    "kind": "forall",
    "vars": [
      {{"sym_id": "FUNC_1", "sort": "Function"}},
      {{"sym_id": "VAR_1", "sort": "Real"}},
      {{"sym_id": "VAR_2", "sort": "Real"}}
    ],
    "body": {{
      "kind": "imp",
      "left": {{
        "kind": "pred",
        "pred_id": "P_CONTINUOUS_ON",
        "args": [
          {{"kind": "sym_ref", "sym_id": "FUNC_1"}},
          {{
            "kind": "interval",
            "left": {{"kind": "sym_ref", "sym_id": "VAR_1"}},
            "right": {{"kind": "sym_ref", "sym_id": "VAR_2"}}
          }}
        ]
      }},
      "right": {{
        "kind": "pred",
        "pred_id": "P_BOUNDED_ON_TMP_NODE_15",
        "args": [
          {{"kind": "sym_ref", "sym_id": "FUNC_1"}},
          {{
            "kind": "interval",
            "left": {{"kind": "sym_ref", "sym_id": "VAR_1"}},
            "right": {{"kind": "sym_ref", "sym_id": "VAR_2"}}
          }}
        ]
      }}
    }}
  }},
  "predicate_entries": [
    {{
      "pred_id": "P_CONTINUOUS_ON",
      "canonical_name": "ContinuousOn",
      "surface_forms": ["FUNC_1 is continuous on [VAR_1, VAR_2]"],
      "surface_template": "{{FUNC}} is continuous on {{INTERVAL}}",
      "arity": 2,
      "arg_sorts": ["Function", "Interval"],
      "status": "builtin",
      "gloss": "F is continuous on interval I"
    }},
    {{
      "pred_id": "P_BOUNDED_ON_TMP_NODE_15",
      "canonical_name": "BoundedOn",
      "surface_forms": ["FUNC_1 is bounded on [VAR_1, VAR_2]"],
      "surface_template": "{{FUNC}} is bounded on {{INTERVAL}}",
      "arity": 2,
      "arg_sorts": ["Function", "Interval"],
      "status": "generated",
      "introduced_by_node": "node_15",
      "gloss": "F is bounded on interval I"
    }}
  ]
}}

【输出格式要求】
你必须只输出合法 JSON，格式如下:
{data_template}
请根据上面的规则，编译下面这个节点:{pos1}，并且只返回严格 JSON。
"""

correction_prompt11 = """
请修复下面的回答，使其严格符合这个 JSON 模板：
{data_template}
待修复内容：
{answer}
"""


def validation11(text):
    return True
