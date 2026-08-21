import re


NODE_TYPE_ZH = ["定义", "定理", "命题", "引理", "推论", "性质", "公理"]

NODE_TYPE_EN_TO_ZH = {
    "Definition": "定义",
    "Theorem": "定理",
    "Proposition": "命题",
    "Lemma": "引理",
    "Corollary": "推论",
    "Property": "性质",
    "Axiom": "公理",
}

NODE_TYPE_ZH_TO_EN = {v: k for k, v in NODE_TYPE_EN_TO_ZH.items()}


EXPLICIT_REF_PATTERNS = [
    re.compile(
        r'(定义|定理|命题|引理|推论|性质|公理)\s*§?\s*'
        r'([0-9]+(?:[.\-][0-9]+)*(?:\([a-zA-Z0-9]+\))?)'
    ),
    re.compile(
        r'(Definition|Theorem|Proposition|Lemma|Corollary|Property|Axiom)s?\s*'
        r'([0-9]+(?:[.\-][0-9]+)*)',
        re.IGNORECASE,
    ),
]

TEX_REF_PATTERN = re.compile(
    r"\\(?P<macro>ref|eqref|autoref|cref|Cref|nameref)\s*\{(?P<labels>[^{}]+)\}|"
    r"\\hyperref\s*\[(?P<hyper_label>[^\[\]]+)\]",
    re.DOTALL,
)


FORMULA_REF_PATTERNS = [
    re.compile(
        r'(?:式|公式|equation|eq\.?)\s*[\(（]\s*'
        r'([0-9]+(?:[.\-][0-9]+)*|[*∗★]+|[IVX]+)\s*[\)）]',
        re.IGNORECASE,
    ),
]


RELATIVE_NODE_REF_TERMS = [
    "上述定义", "上述定理", "上述命题", "上述引理",
    "上述推论", "上述性质", "上述公理",
    "前述定义", "前述定理", "前述命题", "前述引理",
    "前述推论", "前述性质", "前述公理",
]

RELATIVE_FORMULA_REF_TERMS = ["上式", "下式", "上面式子", "前式", "前一式"]


NAMED_REF_PATTERN = re.compile(
    r'(?:由|根据|利用|应用|依据|按照)\s*'
    r'([A-Z][A-Za-z\-]+(?:\s*[-–]\s*[A-Z][A-Za-z\-]+)?)\s*'
    r'(不等式|定理|引理|公式|法则|原理|恒等式)'
)


REFERENCE_VERBS = ["根据", "由", "利用", "结合", "依照", "按照", "应用", "依据"]
DEDUCTION_VERBS = ["可知", "可得", "得到", "推出", "得出", "于是", "因此", "同理可得", "故", "推知"]


def find_explicit_refs(text):
    if not isinstance(text, str) or not text:
        return []
    results = []
    for pattern in EXPLICIT_REF_PATTERNS:
        for m in pattern.finditer(text):
            raw_type = m.group(1)
            number = m.group(2)
            node_type = NODE_TYPE_EN_TO_ZH.get(raw_type.capitalize(), raw_type)
            if node_type not in NODE_TYPE_ZH:
                continue
            results.append({
                "surface": m.group(0),
                "node_type": node_type,
                "number": number,
                "start": m.start(),
                "end": m.end(),
            })
    return results


def find_tex_refs(text):
    if not isinstance(text, str) or not text:
        return []
    results = []
    for match in TEX_REF_PATTERN.finditer(text):
        macro = match.group("macro") or "hyperref"
        labels_text = match.group("labels") or match.group("hyper_label") or ""
        for label in labels_text.split(","):
            label = label.strip()
            if not label:
                continue
            results.append(
                {
                    "surface": match.group(0),
                    "macro": macro,
                    "label_key": label,
                    "start": match.start(),
                    "end": match.end(),
                    "source_format": "tex",
                }
            )
    return results


def find_formula_refs(text):
    if not isinstance(text, str) or not text:
        return []
    results = []
    for pattern in FORMULA_REF_PATTERNS:
        for m in pattern.finditer(text):
            results.append({
                "surface": m.group(0),
                "number": m.group(1),
                "start": m.start(),
                "end": m.end(),
            })
    for term in RELATIVE_FORMULA_REF_TERMS:
        start = 0
        while True:
            idx = text.find(term, start)
            if idx < 0:
                break
            results.append({
                "surface": term,
                "number": None,
                "start": idx,
                "end": idx + len(term),
                "relative": True,
            })
            start = idx + len(term)
    return results


def find_relative_node_refs(text):
    if not isinstance(text, str) or not text:
        return []
    results = []
    for term in RELATIVE_NODE_REF_TERMS:
        start = 0
        while True:
            idx = text.find(term, start)
            if idx < 0:
                break
            results.append({
                "surface": term,
                "node_type": term[-2:],
                "start": idx,
                "end": idx + len(term),
            })
            start = idx + len(term)
    return results


def find_named_refs(text):
    if not isinstance(text, str) or not text:
        return []
    results = []
    for m in NAMED_REF_PATTERN.finditer(text):
        results.append({
            "surface": m.group(0),
            "name": m.group(1).strip(),
            "kind": m.group(2),
            "start": m.start(),
            "end": m.end(),
        })
    return results


def find_triggers(text):
    if not isinstance(text, str) or not text:
        return []
    triggers = []
    for verb in REFERENCE_VERBS:
        if verb in text:
            triggers.append({"surface": verb, "trigger_type": "reference"})
    for verb in DEDUCTION_VERBS:
        if verb in text:
            triggers.append({"surface": verb, "trigger_type": "deduction"})
    return triggers
