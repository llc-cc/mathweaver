data_template10 = """{{
  "node_type": "node type",
  "title": {{
    "chinese": "title",
    "english": "title"
  }},
  "content": "content after disambiguation",
  "original_content": "original content before disambiguation",
  "proof": "proof text",
  "label": "label"
}}"""

prompt_template10 = prompt_template_symbol_predicate_rewrite = '''
你是一名严谨的数学歧义消除助手。
你的任务是将含有歧义的数学表达改写为语义明确、形式统一的谓词化（predicate-style）表达，同时严格保持原有数学含义不变。

你将收到一个节点，格式如下：
{{
    "node_type": "定义/定理/引理/性质/命题等",
    "title": {{
        "chinese": "标题",
        "english": "title"
    }},
    "content": "数学内容，可能包含 LaTeX",
    "proof": "证明文本",
    "label": "标签",
    "ambiguity_hits": []
}}

你还会收到一个歧义表：
DEFAULT_AMBIGUITY_TABLE = {{
    "symbol": ["meaning 1", "meaning 2", ...]
}}

重要说明：
- `ambiguity_hits` 已经列出了所有必须处理的歧义命中。
- 必须结合 `content`、`proof` 和 `ambiguity_hits` 一起判断真实含义。
- 必须严格保持数学语义不变。
- 必须严格保持 JSON 结构不变。
- `node_type`、`title`、`label` 必须与输入完全一致。
- 将改写后的文本放入 `content`。
- 将原始输入文本放入 `original_content`。
- 除非 `proof` 中也出现同类歧义且为了保持一致性必须改写，否则不要修改 `proof`。
- 不要输出解释、推理过程或额外字段。

成对竖线规则：
- 如果 `ambiguity_hits[*].symbol` 是 `|...|` 或 `||...||`，必须把 `matches[*].text` 当作一个完整表达式整体处理。
- 绝不能把左竖线和右竖线拆开分别解释。
- `|x| -> Abs(x)` 用于绝对值语境。
- `|A| -> Cardinality(A)` 用于集合基数语境。
- `|G| -> Order(G)` 用于群论中的群阶语境。
- `||x|| -> Norm(x)`。

裸竖线规则：
- `a | b -> Divides(a,b)`。
- 裸中缀 `|` 不是除法。

一般谓词化改写规则：
- derivative -> Derived(function, variable)
- absolute value -> Abs(x)
- cardinality -> Cardinality(A)
- order -> Order(G)
- norm -> Norm(x)
- divides -> Divides(a,b)
- derived subgroup -> DerivedSubgroup(G)
- composition -> Compose(f,g)
- mapping -> Map(f,x)
- partial derivative -> PartialDerivative(f,x)
- equivalence -> Equivalent(a,b)
- asymptotic equivalence -> AsymptoticEqual(a,b)
- 如果没有完全匹配的标准规则，请使用 `MeaningName(argument1,argument2,...)`，其中 `MeaningName` 要简洁、清晰、规范。

输出必须是严格 JSON，且必须匹配下面这个模板：
{data_template}

下面是歧义表：
DEFAULT_AMBIGUITY_TABLE = {{
    "'": [
        "derivative",
        "partial derivative",
        "new symbol",
        "derived subgroup",
        "sequence index"
    ],
    "*": [
        "multiplication",
        "convolution",
        "group operation",
        "adjoint operator"
    ],
    "\\cdot": [
        "multiplication",
        "dot product",
        "scalar multiplication"
    ],
    "|...|": [
        "absolute value",
        "cardinality",
        "order"
    ],
    "||...||": [
        "norm",
        "absolute value"
    ],
    "|": [
        "divides",
        "conditional probability"
    ],
    "/": [
        "division",
        "quotient group",
        "set difference"
    ],
    "\\circ": [
        "function composition",
        "group composition"
    ],
    "~": [
        "equivalence",
        "asymptotic equivalence",
        "distribution"
    ],
    "\\partial": [
        "partial derivative",
        "boundary operator"
    ],
    "\\Delta": [
        "difference operator",
        "Laplacian operator"
    ],
    "\\nabla": [
        "gradient operator",
        "vector differential operator"
    ],
    "\\sum": [
        "summation",
        "direct sum"
    ],
    "\\prod": [
        "product",
        "Cartesian product"
    ],
    "\\to": [
        "mapping",
        "limit",
        "implication"
    ],
    "\\subset": [
        "subset",
        "subspace"
    ],
    "^T": [
        "transpose",
        "superscript index"
    ],
    "^*": [
        "adjoint",
        "conjugate",
        "dual"
    ]
}}

请根据上述规则改写下面这些节点：
{pos1}
'''

correction_prompt10 = '''
你是一名严谨的校对助手。
请修复下面的回答，使其严格符合这个 JSON 模板：
{data_template}

待修复内容：
{answer}
'''


def validation10(text):
    return True
