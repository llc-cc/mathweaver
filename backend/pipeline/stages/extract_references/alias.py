import re

from .rules import NODE_TYPE_ZH_TO_EN


_FULL_TO_HALF_PAREN = str.maketrans({"（": "(", "）": ")", "．": "."})
_LATEX_EXPR_PATTERN = re.compile(r'\$([^$]{3,60})\$')
_NON_WORD_PATTERN = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")
_ANCHOR_STOPWORDS = {
    "cdots",
    "vdots",
    "ldots",
    "alpha",
    "beta",
    "gamma",
    "mathbf",
    "mathbb",
    "operatorname",
}


def _normalize_whitespace(text):
    return re.sub(r"\s+", "", text or "")


def normalize_label(label):
    if not isinstance(label, str):
        return ""
    s = label.strip().translate(_FULL_TO_HALF_PAREN)
    return _normalize_whitespace(s)


def normalize_free_text(text):
    if not isinstance(text, str):
        return ""
    s = text.strip().translate(_FULL_TO_HALF_PAREN)
    s = _NON_WORD_PATTERN.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def extract_anchor_terms(title_text, context_texts=None, max_terms=6):
    terms = []
    seen = set()

    candidates = []
    if isinstance(title_text, str) and title_text.strip():
        candidates.append(title_text)
    if isinstance(context_texts, list):
        candidates.extend(item for item in context_texts if isinstance(item, str) and item.strip())

    for text in candidates:
        # Priority: extract intact LaTeX expressions first (structural math, e.g. W_0^{k,p}(U))
        for m in _LATEX_EXPR_PATTERN.finditer(text):
            inner = m.group(1).strip()
            if len(inner) >= 3 and any(c in inner for c in ('{', '^', '_', '\\')):
                if inner not in seen:
                    seen.add(inner)
                    terms.append(inner)
                    if len(terms) >= max_terms:
                        return terms
        # Fallback: word-level tokens for non-LaTeX text
        normalized = normalize_free_text(text)
        if not normalized:
            continue
        for piece in normalized.split():
            if len(piece) < 2:
                continue
            if piece.lower() in _ANCHOR_STOPWORDS:
                continue
            if piece in seen:
                continue
            seen.add(piece)
            terms.append(piece)
            if len(terms) >= max_terms:
                return terms
    return terms


def parse_label(label):
    if not isinstance(label, str):
        return None, None
    s = label.strip().translate(_FULL_TO_HALF_PAREN)
    m = re.match(
        r'^(定义|定理|命题|引理|推论|性质|公理|'
        r'Definition|Theorem|Proposition|Lemma|Corollary|Property|Axiom)s?\s*'
        r'([0-9]+(?:[.\-][0-9]+)*(?:\([a-zA-Z0-9]+\))?)$',
        _normalize_whitespace(s),
        re.IGNORECASE,
    )
    if not m:
        return None, None
    raw_type = m.group(1)
    if raw_type[0].isascii():
        raw_type = raw_type.capitalize()
        from .rules import NODE_TYPE_EN_TO_ZH
        node_type = NODE_TYPE_EN_TO_ZH.get(raw_type, None)
    else:
        node_type = raw_type
    return node_type, m.group(2)


def expand_aliases(label_text, title_text, node_type, context_texts=None):
    aliases = set()
    if label_text:
        raw = label_text.strip()
        aliases.add(raw)
        aliases.add(normalize_label(raw))
        nt, num = parse_label(raw)
        if nt and num:
            aliases.add(f"{nt} {num}")
            aliases.add(f"{nt}{num}")
            en = NODE_TYPE_ZH_TO_EN.get(nt)
            if en:
                aliases.add(f"{en} {num}")
                aliases.add(f"{en}{num}")
            anchor_terms = extract_anchor_terms(title_text, context_texts)
            for term in anchor_terms:
                aliases.add(f"{term} {nt} {num}")
                aliases.add(f"{term}{nt}{num}")
                aliases.add(f"{nt} {num} {term}")
                aliases.add(f"{nt}{num}{term}")
                if en:
                    aliases.add(f"{term} {en} {num}")
                    aliases.add(f"{term}{en}{num}")
                    aliases.add(f"{en} {num} {term}")
                    aliases.add(f"{en}{num}{term}")
    if not label_text and node_type and title_text:
        pass
    if title_text and len(title_text.strip()) >= 4:
        aliases.add(title_text.strip())
    return [a for a in aliases if a]


def extract_node_type_and_number(label_text, node_type_hint):
    nt, num = parse_label(label_text or "")
    if nt and num:
        return nt, num
    if label_text:
        m = re.search(r'([0-9]+(?:[.\-][0-9]+)*(?:\([a-zA-Z0-9]+\))?)', label_text)
        if m and node_type_hint:
            return node_type_hint, m.group(1)
    return None, None
