import re


MAX_CHINESE_VISIBLE_CHARS = 24
MAX_ENGLISH_WORDS = 14

_MATH_ENV_RE = re.compile(
    r"\\begin\s*\{([^{}]+)\}.*?\\end\s*\{\1\}",
    flags=re.DOTALL,
)
_MATH_SPAN_RE = re.compile(
    r"\$\$.*?\$\$|\$.*?\$|\\\(.*?\\\)|\\\[.*?\\\]",
    flags=re.DOTALL,
)
_CHINESE_SENTENCE_START_RE = re.compile(r"^(?:若|如果|当|设|假设|由此|于是|因此|令)")
_ENGLISH_SENTENCE_START_RE = re.compile(
    r"^(?:if|when|let|suppose|then|therefore|assume)\b",
    flags=re.IGNORECASE,
)
_CHINESE_DECLARATIVE_RE = re.compile(
    r"(?:相当于|等于|可表示为|有唯一解|仍然?可|使.{0,12}(?:成为|变为|化为)|保持.{0,12}(?:不变|解集)|乘以\s*-?1)"
)
_ENGLISH_DECLARATIVE_RE = re.compile(
    r"\b(?:is|are|equals|implies|preserves|changes|becomes|has|have|can|may|must)\b",
    flags=re.IGNORECASE,
)


def _replace_math_spans(text, placeholder):
    without_environments = _MATH_ENV_RE.sub(placeholder, text or "")
    return _MATH_SPAN_RE.sub(placeholder, without_environments)


def chinese_visible_length(text):
    compact = _replace_math_spans(text, "式")
    compact = re.sub(r"[\s，。；：、,.!?！？;:'\"“”‘’（）()\[\]{}《》<>—–-]", "", compact)
    return len(compact)


def english_word_count(text):
    compact = _replace_math_spans(text, "MATH")
    return len(re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*", compact))


def title_text_within_limits(text, language):
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped:
        return True
    if re.search(r"[。！？.!?]\s*$", stripped):
        return False
    if language == "chinese":
        return (
            not _CHINESE_SENTENCE_START_RE.match(stripped)
            and not _CHINESE_DECLARATIVE_RE.search(stripped)
            and chinese_visible_length(stripped) <= MAX_CHINESE_VISIBLE_CHARS
        )
    return (
        not _ENGLISH_SENTENCE_START_RE.match(stripped)
        and not _ENGLISH_DECLARATIVE_RE.search(stripped)
        and english_word_count(stripped) <= MAX_ENGLISH_WORDS
    )


data_template05 = r'''{
  "title": {
    "chinese": "concise type-aware mathematical heading",
    "english": "concise type-aware mathematical heading"
  }
}'''


prompt_template05 = r'''
You are naming one immutable mathematical source node for a knowledge graph.

Input node:
{pos1}

Source-provided title hint, if any:
{source_title_hint}

Required visible node-kind wording:
{title_kind_requirement}

Return only valid JSON matching:
{data_template}

Think silently before answering:
1. Identify whether the node is a definition, theorem-like fact, example,
   exercise/problem, remark/note, or observation.
2. Identify the mathematical knowledge being named. Do not copy the whole
   statement, exercise question, answer, proof, or computation.

Title priority:
1. Preserve a reliable source-provided name such as "秩定理", "Urysohn's
   lemma", or "高斯消元法的应用". Light punctuation or word-order cleanup is
   allowed, but do not replace its core name with a new summary.
2. Otherwise use an established mathematical name when the source supports it.
3. Otherwise use a compact noun phrase naming the object and its definition,
   property, criterion, relation, method, example, exercise, remark, or
   observation.
4. Only as a last resort use the shortest faithful semantic description.

Rules by node type:
- Definition: "<object> 的定义" / "Definition of <object>".
- Axiom: "<system or object> 的公理" / "Axioms of <system or object>".
- Theorem, proposition, property, fact, or claim: use a conventional name or a
  compact phrase such as "行列式的换行变号性质". Do not restate all hypotheses
  and conclusions.
- Lemma or corollary: use a conventional name or a compact heading that remains
  visibly a lemma or corollary when no established name exists.
- Example: the title must say what knowledge it exemplifies and must visibly
  contain 示例, 例子, or 反例 in Chinese and Example or Counterexample in
  English. Prefer subtypes such as 计算示例, 应用示例, or 反例 when supported.
- Exercise: the title must say what knowledge or ability it practises and must
  visibly contain 习题, 练习, or 问题 in Chinese and Exercise or Problem in
  English. Prefer 计算习题, 证明习题, or 应用习题 when supported. Never reveal
  the answer merely to create the title.
- Problem: keep it visibly a 问题 / Problem.
- Remark or note: state which concept, convention, limitation, or pitfall it
  comments on, and visibly contain 注释, 备注, or 说明 in Chinese and Remark or
  Note in English.
- Observation: keep it visibly an 观察 / Observation.

Style and length:
- Use a standalone noun phrase, not a complete sentence.
- Target 6-16 visible Chinese characters and 3-10 English words.
- Never exceed 24 visible Chinese characters or 14 English words. LaTeX counts
  as one mathematical unit rather than by raw source length.
- Do not begin with 若, 如果, 当, 设, 假设, 由此, If, When, Let, Suppose, or
  Then. Do not end with sentence punctuation.
- Avoid "关于……的讨论", "……的介绍", "Discussion of ...",
  "Introduction to ...", "A Look at ...", and "Some Properties of ...".
- Preserve necessary mathematical notation and LaTeX exactly, but omit formulas
  that are not needed to distinguish the concept.
- The Chinese and English titles must express the same knowledge and node kind.
- Do not invent objects, properties, hypotheses, conclusions, or source names.
- Return only title. Never return node_type, content, remark, proof, label,
  global_id, source fields, sub_nodes, or a complete node.
- Use an empty string for a language only when a faithful short title cannot be
  produced; at least one title must be non-empty.

Examples:

Definition:
Source meaning: An invertible matrix is a square matrix possessing an inverse.
Good: {{"title": {{"chinese": "可逆矩阵的定义",
                    "english": "Definition of an Invertible Matrix"}}}}

Property:
Source meaning: Interchanging two rows changes the determinant's sign.
Good: {{"title": {{"chinese": "行列式的换行变号性质",
                    "english": "Sign Change under a Determinant Row Swap"}}}}

Example:
Source meaning: Gaussian elimination reduces one augmented matrix to row-echelon form.
Good: {{"title": {{"chinese": "高斯消元法的示例",
                    "english": "Example of Gaussian Elimination"}}}}

Computation example:
Source meaning: A third-order determinant is evaluated with row-addition operations.
Good: {{"title": {{"chinese": "三阶行列式的倍加法计算示例",
                    "english": "Example of Computing a Third-Order Determinant by Row Addition"}}}}

Exercise:
Source meaning: Prove that elementary row operations preserve the solution set.
Good: {{"title": {{"chinese": "初等行变换保解性的证明习题",
                    "english": "Proof Exercise on Solution Preservation under Row Operations"}}}}

Remark:
Source meaning: The note explains why left and right inverses are equivalent here.
Good: {{"title": {{"chinese": "关于可逆矩阵定义的注释",
                    "english": "Remark on the Definition of an Invertible Matrix"}}}}
'''


correction_prompt05 = r'''
The previous title result was invalid. Return only valid JSON matching:
{data_template}

Input node:
{pos1}

Source-provided title hint, if any:
{source_title_hint}

Required visible node-kind wording:
{title_kind_requirement}

Invalid answer:
{answer}

Correct it into a concise standalone mathematical heading. Preserve the core of
the source title hint. Examples, exercises/problems, remarks/notes, and
observations must visibly identify their node kind. Do not restate the source,
reveal an exercise answer, begin with sentence-style condition words, or exceed
24 visible Chinese characters / 14 English words. Return title JSON only.
'''


def validation05(text):
    if not isinstance(text, dict):
        return False
    title = text.get("title")
    if not isinstance(title, dict):
        return False
    chinese = title.get("chinese", "")
    english = title.get("english", "")
    return (
        isinstance(chinese, str)
        and isinstance(english, str)
        and bool(chinese.strip() or english.strip())
        and title_text_within_limits(chinese, "chinese")
        and title_text_within_limits(english, "english")
    )
