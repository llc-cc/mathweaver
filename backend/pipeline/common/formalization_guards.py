import re

from .node import assemble_statement_text, get_node_content, get_node_label, get_node_title, is_definition_node_type


def _as_text(value):
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _compact(text):
    return re.sub(r"\s+", "", _as_text(text))


def _node_primary_text(node):
    if not isinstance(node, dict):
        return ""
    content = get_node_content(node)
    if content:
        return content
    return assemble_statement_text(node)


def _node_search_text(node):
    if not isinstance(node, dict):
        return ""
    parts = [
        get_node_title(node),
        _node_primary_text(node),
        get_node_label(node),
    ]
    for field in ("subject", "context"):
        value = node.get(field)
        if isinstance(value, list):
            parts.extend(_as_text(item) for item in value)
    return "\n".join(part for part in parts if part)


def _contains_any(text, markers):
    return any(marker and marker in text for marker in markers)


def _contains_any_lower(text, markers):
    lowered = text.lower()
    return any(marker and marker in lowered for marker in markers)


def infer_statement_skeleton(node):
    """Infer a lightweight logical skeleton for a mathematical statement.

    The skeleton is intentionally coarse. It is used as a guardrail before or
    after autoformalization, not as a complete parser.
    """

    text = _node_primary_text(node) if isinstance(node, dict) else _as_text(node)
    title = get_node_title(node) if isinstance(node, dict) else ""
    node_type = _as_text(node.get("node_type")) if isinstance(node, dict) else ""
    statement_form = _as_text(node.get("statement_form")) if isinstance(node, dict) else ""
    search_text = "\n".join([title, text, statement_form])
    compact = _compact(search_text)
    lowered = search_text.lower()

    skeleton = {
        "kind": "other",
        "source": "rule",
        "triggers": [],
        "expected_connectives": [],
        "forbidden_connectives": [],
        "notes": [],
    }

    def mark(kind, trigger, expected=None, forbidden=None, note=None):
        skeleton["kind"] = kind
        if trigger:
            skeleton["triggers"].append(trigger)
        if expected:
            skeleton["expected_connectives"].extend(expected)
        if forbidden:
            skeleton["forbidden_connectives"].extend(forbidden)
        if note:
            skeleton["notes"].append(note)

    definition_markers = ("定义", "称为", "叫做", "记作", "is called", "is defined")
    iff_markers = ("当且仅当", "充分必要条件", "等价于", "互为充要", "\\Leftrightarrow", "↔")
    example_markers = ("例如", "例", "取", "于是", "则有", "for example")
    non_strict_chain_markers = ("\\subseteq", "⊆", "包含于", "升链", "ascending chain")
    strict_markers = ("真包含", "\\subsetneq", "⊂", "proper subset")
    multi_part_markers = ("进一步", "此外", "并且", "同时", "推论", "moreover", "furthermore")
    implication_markers = ("若", "如果", "则", "\\Rightarrow", "→", "implies", "if ")
    existence_markers = ("存在", "至少存在", "there exists", "exists")

    if is_definition_node_type(node_type) or _contains_any(search_text, definition_markers):
        mark(
            "definition",
            "definition marker",
            expected=["definitional body", "iff/predicate expansion"],
            note="定义类节点应优先展开题目条件，避免直接用 Mathlib 实现函数替代定义。",
        )
    elif _contains_any(search_text, iff_markers) or statement_form == "equivalence":
        mark(
            "equivalence",
            "iff marker",
            expected=["↔"],
            forbidden=["one-way-only →"],
            note="充分必要条件应保留双向结构。",
        )
    elif _contains_any(search_text, example_markers):
        mark(
            "example_conjunction",
            "example marker",
            expected=["∧"],
            forbidden=["top-level →"],
            note="例子类命题通常是在描述多个同时成立的事实，不应默认写成蕴含。",
        )
    elif _contains_any(search_text, non_strict_chain_markers) and not _contains_any(search_text, strict_markers):
        mark(
            "non_strict_chain",
            "non-strict inclusion marker",
            expected=["⊆", "\\subseteq", "<="],
            forbidden=["<", "⊂", "\\subsetneq"],
            note="非严格包含链允许相等，不能改成严格递增链。",
        )
    elif _contains_any(search_text, multi_part_markers):
        mark(
            "multi_part",
            "multi-part marker",
            expected=["all subclaims preserved"],
            note="复合命题需要保留多个子结论或后续推论。",
        )
    elif _contains_any_lower(lowered, existence_markers):
        mark("existence", "existence marker", expected=["∃"])
    elif _contains_any(search_text, implication_markers) or statement_form == "implication":
        mark("implication", "implication marker", expected=["→"])
    elif re.search(r"(?<![<>=!])=(?![=])", text):
        mark("equality", "equals sign", expected=["="])

    # Secondary flags: these help downstream reporting without overriding the
    # main skeleton.
    if _contains_any(search_text, multi_part_markers) and skeleton["kind"] != "multi_part":
        skeleton["notes"].append("原文含复合/递进标记，生成时需检查是否漏掉子命题。")
    if _contains_any(search_text, non_strict_chain_markers) and not _contains_any(search_text, strict_markers):
        skeleton["notes"].append("原文含非严格包含标记，Lean 输出应避免严格包含。")

    # Preserve order while removing duplicates.
    for key in ("triggers", "expected_connectives", "forbidden_connectives", "notes"):
        seen = set()
        unique = []
        for item in skeleton[key]:
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        skeleton[key] = unique

    return skeleton


CONCEPT_LEXICON = [
    {
        "id": "associated",
        "name": "相伴关系",
        "triggers": ["相伴", "~", "∼", "associated"],
        "prefer": ["Associated"],
        "avoid": ["IsUnit"],
        "risk_code": "concept_downgrade",
        "note": "相伴关系表示两个元素相差一个单位，不能直接降级为某个元素是单位。",
        "sample_targets": ["测试5 Item 6 不可约元的定义"],
    },
    {
        "id": "vector_family_rank",
        "name": "向量组秩",
        "triggers": ["向量组的秩", "向量组秩", "rank{", "rank {"],
        "prefer": ["Module.rank", "Submodule.span"],
        "avoid": ["Matrix.rank"],
        "risk_code": "api_context_mismatch",
        "note": "题目讨论向量组秩时，优先表达为生成子空间的维数；矩阵秩只在题目明确给出矩阵时作为默认选择。",
        "sample_targets": ["报告1 Item 34/40", "报告2 Item 34"],
    },
    {
        "id": "gcd_definition",
        "name": "最大公因子定义",
        "triggers": ["最大公因子", "最大公约数", "gcd", "GCD"],
        "prefer": ["common divisor predicate", "maximal divisibility property"],
        "avoid": ["GCDMonoid.gcd", ".gcd"],
        "risk_code": "definition_as_implementation",
        "note": "定义类节点应展开共同因子与最大性条件，不应只调用已封装的 gcd 函数。",
        "sample_targets": ["测试5 Item 9 最大公因子的定义"],
    },
    {
        "id": "lcm_definition",
        "name": "最小公倍数定义",
        "triggers": ["最小公倍数", "lcm", "LCM"],
        "prefer": ["common multiple predicate", "minimal divisibility property"],
        "avoid": ["lcm", "LCMMonoid.lcm"],
        "risk_code": "definition_as_implementation",
        "note": "定义类节点应展开公倍数与最小性条件，不应只调用已封装的 lcm 函数。",
        "sample_targets": ["测试5 Item 11 最小公倍数的定义"],
    },
    {
        "id": "non_strict_inclusion",
        "name": "非严格包含",
        "triggers": ["\\subseteq", "⊆", "包含于", "升链"],
        "prefer": ["⊆", "\\subseteq", "<="],
        "avoid": ["⊂", "\\subsetneq", "<"],
        "risk_code": "strict_inclusion_mismatch",
        "note": "升链通常使用非严格包含，不能自动改成严格包含。",
        "sample_targets": ["测试5 Item 22 理想升链的定义"],
    },
    {
        "id": "divisibility_roles",
        "name": "整除角色",
        "triggers": ["因子", "整除", "倍数", "divides", "multiple"],
        "prefer": ["divisor ∣ dividend"],
        "avoid": ["role reversal"],
        "risk_code": "role_reversal",
        "note": "需要显式区分除数、被除数、倍数，避免变量顺序颠倒。",
        "sample_targets": ["测试5 Item 3 整除的定义", "测试5 Item 8/10 公因子与公倍数"],
    },
]


def find_applicable_concepts(node):
    text = _node_search_text(node) if isinstance(node, dict) else _as_text(node)
    lowered = text.lower()
    concepts = []
    for concept in CONCEPT_LEXICON:
        matched = []
        for trigger in concept["triggers"]:
            if not trigger:
                continue
            if trigger.lower() in lowered:
                matched.append(trigger)
        if concept["id"] == "vector_family_rank" and "向量组" in text and "秩" in text:
            matched.append("向量组+秩")
        if matched:
            item = {key: value for key, value in concept.items() if key != "triggers"}
            item["matched_triggers"] = matched
            concepts.append(item)
    return concepts


def extract_role_bindings(node):
    text = _node_primary_text(node) if isinstance(node, dict) else _as_text(node)
    compact = _compact(text)
    bindings = []

    def add(role_type, source, **roles):
        bindings.append({"type": role_type, "source": source, "roles": roles})

    # Chinese divisibility patterns: "b 是 a 的因子" means b | a.
    for match in re.finditer(r"([A-Za-zα-ωΑ-Ω][\w₀-₉₁₂₃₄₅₆₇₈₉]*)是([A-Za-zα-ωΑ-Ω][\w₀-₉₁₂₃₄₅₆₇₈₉]*)的因子", compact):
        add("divisibility", match.group(0), divisor=match.group(1), dividend=match.group(2))

    for match in re.finditer(r"([A-Za-zα-ωΑ-Ω][\w₀-₉₁₂₃₄₅₆₇₈₉]*)整除([A-Za-zα-ωΑ-Ω][\w₀-₉₁₂₃₄₅₆₇₈₉]*)", compact):
        add("divisibility", match.group(0), divisor=match.group(1), dividend=match.group(2))

    for match in re.finditer(r"([A-Za-zα-ωΑ-Ω][\w₀-₉₁₂₃₄₅₆₇₈₉]*)是([A-Za-zα-ωΑ-Ω][\w₀-₉₁₂₃₄₅₆₇₈₉]*)的倍数", compact):
        add("multiple", match.group(0), multiple=match.group(1), base=match.group(2), divisibility=f"{match.group(2)} ∣ {match.group(1)}")

    vector_count = re.search(r"含(?:有)?([A-Za-z][\w]*)个向量", compact)
    if vector_count:
        add("vector_family_cardinality", vector_count.group(0), cardinality=vector_count.group(1))

    ambient_dimension = re.search(r"([A-Za-z][\w]*)维", compact)
    if ambient_dimension:
        add("ambient_dimension", ambient_dimension.group(0), dimension=ambient_dimension.group(1))

    if "向量组的秩" in compact or "向量组秩" in compact:
        add("vector_family_rank", "向量组秩", preferred_object="span dimension", avoid="Matrix.rank unless matrix is explicit")

    return bindings


def build_formalizer_context_payload(node):
    """Build a layered M2-style payload for downstream formalizer prompts."""

    if not isinstance(node, dict):
        node = {"content": _as_text(node)}

    primary_statement = _node_primary_text(node)
    auxiliary_context = {
        "title": get_node_title(node),
        "label": get_node_label(node),
        "node_type": node.get("node_type", ""),
        "statement_form": node.get("statement_form", ""),
        "subject": node.get("subject", []) if isinstance(node.get("subject"), list) else [],
        "context": node.get("context", []) if isinstance(node.get("context"), list) else [],
        "variables": node.get("variables", []) if isinstance(node.get("variables"), list) else [],
    }
    return {
        "primary_statement": primary_statement,
        "auxiliary_context": auxiliary_context,
        "statement_skeleton": infer_statement_skeleton(node),
        "role_bindings": extract_role_bindings(node),
        "concept_hints": find_applicable_concepts(node),
        "context_policy": [
            "Primary statement is authoritative.",
            "Auxiliary context may disambiguate terms but must not add new assumptions.",
            "Title and label help naming only; they must not override the statement body.",
            "Do not add nonzero, positivity, maximality, finiteness, or strictness assumptions unless present in the primary statement.",
        ],
    }


def _lean_has_any(lean_code, tokens):
    return any(token in lean_code for token in tokens)


def _source_has_nonzero_or_positive(source_text):
    return _contains_any(source_text, ("非零", "不为零", "正", ">0", "> 0", "nonzero", "non-zero", "positive"))


def check_formalization_risks(node, lean_code=""):
    """Return semantic-risk tags for a generated formalization.

    If lean_code is empty, only source-side risks such as OCR suspicion are
    reported. This keeps the function useful inside the extraction pipeline.
    """

    source_text = _node_search_text(node) if isinstance(node, dict) else _as_text(node)
    lean_code = _as_text(lean_code)
    skeleton = infer_statement_skeleton(node)
    concepts = find_applicable_concepts(node)
    risks = []

    def add(code, severity, message, evidence=None, sample_targets=None):
        risk = {
            "code": code,
            "severity": severity,
            "message": message,
        }
        if evidence:
            risk["evidence"] = evidence
        if sample_targets:
            risk["sample_targets"] = sample_targets
        risks.append(risk)

    if "获相等" in source_text:
        add(
            "ocr_suspicious_text",
            "high",
            "文本含疑似 OCR 错误“获相等”，应先做文本修复再形式化。",
            evidence="获相等",
            sample_targets=["报告1 Item 37 秩相等的两个向量组不一定等价"],
        )

    if not lean_code:
        return risks

    if skeleton["kind"] == "equivalence" and not _lean_has_any(lean_code, ("↔", "<->", " iff ")):
        add("wrong_connector", "high", "原题是充分必要/等价结构，但 Lean 输出未显式保留双向连接。")

    if skeleton["kind"] == "example_conjunction" and _lean_has_any(lean_code, ("→", "->")) and not _lean_has_any(lean_code, ("∧", "/\\")):
        add("wrong_connector", "medium", "原题是例子类并列事实，但 Lean 输出更像单向蕴含。")

    if skeleton["kind"] == "non_strict_chain" and _lean_has_any(lean_code, ("⊂", "\\subsetneq")):
        add("strict_inclusion_mismatch", "high", "原题是非严格包含链，但 Lean 输出使用了严格包含。")
    if skeleton["kind"] == "non_strict_chain" and re.search(r"(?<![<>=])<(?!=)", lean_code):
        add("strict_inclusion_mismatch", "high", "原题是非严格包含链，但 Lean 输出出现了严格小于/严格包含风格符号。")

    if not _source_has_nonzero_or_positive(source_text) and _lean_has_any(lean_code, ("≠ 0", "!= 0", "> 0", "0 <")):
        add("extra_condition", "medium", "Lean 输出添加了原题未明确给出的非零或正性条件。")

    for concept in concepts:
        avoid_hits = [token for token in concept.get("avoid", []) if token and token in lean_code]
        prefer_hits = [token for token in concept.get("prefer", []) if token and token in lean_code]
        if avoid_hits and not prefer_hits:
            add(
                concept["risk_code"],
                "high",
                concept["note"],
                evidence=", ".join(avoid_hits),
                sample_targets=concept.get("sample_targets"),
            )

    if skeleton["kind"] == "definition":
        lowered_lean = lean_code.lower()
        if ("最大公因子" in source_text or "gcd" in source_text.lower()) and "gcd" in lowered_lean and "∣" not in lean_code:
            add("definition_as_implementation", "high", "最大公因子定义疑似被 Mathlib gcd 实现函数替代，缺少共同因子与最大性谓词。")
        if ("最小公倍数" in source_text or "lcm" in source_text.lower()) and "lcm" in lowered_lean and "∣" not in lean_code:
            add("definition_as_implementation", "high", "最小公倍数定义疑似被 lcm 实现函数替代，缺少公倍数与最小性谓词。")

    return risks


def attach_formalization_guidance(node, lean_code=""):
    if not isinstance(node, dict):
        return node
    enriched = dict(node)
    payload = build_formalizer_context_payload(enriched)
    risks = check_formalization_risks(enriched, lean_code=lean_code)
    enriched["formalization_guidance"] = {
        "primary_statement": payload["primary_statement"],
        "statement_skeleton": payload["statement_skeleton"],
        "role_bindings": payload["role_bindings"],
        "concept_hints": payload["concept_hints"],
        "context_policy": payload["context_policy"],
        "semantic_risks": risks,
    }
    return enriched


__all__ = [
    "CONCEPT_LEXICON",
    "attach_formalization_guidance",
    "build_formalizer_context_payload",
    "check_formalization_risks",
    "extract_role_bindings",
    "find_applicable_concepts",
    "infer_statement_skeleton",
]
