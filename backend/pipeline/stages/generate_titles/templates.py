data_template05 = r'''{
  "title": {
    "chinese": "concise objective mathematical fact",
    "english": "concise objective mathematical fact"
  }
}'''


prompt_template05 = r'''
You are naming one immutable mathematical source node.

Input node:
{pos1}

Return only valid JSON matching:
{data_template}

Rules:
1. Return only title. Never return node_type, content, remark, proof, label,
   global_id, source fields, sub_nodes, or a complete node.
2. The title must state the node's exact mathematical fact in compact written
   language. It must identify what the node defines, proves, constructs, or
   computes, rather than merely naming the broad topic it discusses.
3. Do not force every title into a generic topic noun phrase. For a theorem,
   proposition, property, identity, or conclusion, prefer a short objective
   declarative statement when that is clearer.
4. Choose the title form according to the source node:
   - Definition: name the exact object being defined, normally as
     "<object> 的定义" / "Definition of <object>".
   - Theorem, proposition, lemma, or property: state the principal mathematical
     fact, relation, equivalence, or formula.
   - Example or computation: state the concrete construction, representation,
     or computed result illustrated by the example.
5. Avoid conversational, editorial, or vague summary wording such as
   "关于……的讨论", "……的介绍", "……的说明", "我们看到……",
   "Discussion of ...", "Introduction to ...", "A Look at ...", or
   "Some Properties of ...".
6. Do not use a bare generic concept such as "矩阵", "分块矩阵", or
   "Matrix Properties" when the source supports a more discriminating factual
   title such as "矩阵乘法结合律" or "分块矩阵的尺寸条件".
7. If the node contains two coequal central facts, retain both compactly. Do
   not title the node from only one incidental sentence, and do not include
   secondary proof details.
8. The Chinese and English titles must express the same fact. Keep both concise,
   formal, and readable as standalone headings.
9. Preserve mathematical notation and LaTeX exactly when it is needed.
10. Do not invent objects, properties, hypotheses, or conclusions.
11. Use an empty string for a language only when a faithful short title in that
    language cannot be produced; at least one title must be non-empty.

Examples:

Example 1 — definition
Source meaning: An \(n\times m\) array of elements of a field is called an
\(n\times m\) matrix.
Good output:
{{"title": {{"chinese": "\(n\times m\) 矩阵的定义",
             "english": "Definition of an \(n\times m\) Matrix"}}}}

Example 2 — proposition
Source meaning: If \(A\) and \(B\) are invertible, then \(AB\) is invertible
and \((AB)^{{-1}}=B^{{-1}}A^{{-1}}\).
Good output:
{{"title": {{"chinese": "可逆矩阵的乘积仍可逆，且 \((AB)^{{-1}}=B^{{-1}}A^{{-1}}\)",
             "english": "A Product of Invertible Matrices Is Invertible, with \((AB)^{{-1}}=B^{{-1}}A^{{-1}}\)"}}}}

Example 3 — concrete representation
Source meaning: A matrix product is represented using the scalar products of
the row vectors of the first matrix and the column vectors of the second.
Good output:
{{"title": {{"chinese": "矩阵乘积可表示为行向量与列向量的标量积",
             "english": "A Matrix Product as Scalar Products of Rows and Columns"}}}}
'''


correction_prompt05 = r'''
The previous title result was invalid. Return only valid JSON matching:
{data_template}

Do not return source-owned or complete-node fields. Correct this answer:
{answer}

Preserve the source-grounded, objective mathematical fact in the title. Do not
replace it with conversational wording or a broad topic-only phrase.
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
    )
