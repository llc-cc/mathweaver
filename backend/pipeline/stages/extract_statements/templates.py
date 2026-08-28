data_template03 = """{{
  0: {{
    "node_type": "node type",
    "content": r\"\"\"complete mathematical statement\"\"\",
    "proof": r\"\"\"proof text or empty string\"\"\",
    "label": "exact source label or empty string"
  }}
}}"""


prompt_template03 = r"""
You are extracting mathematical logic units from one source block. Return one
dictionary entry per independent top-level unit found in the block.

Each entry must contain exactly:
- node_type: one appropriate mathematical unit type.
- content: the complete statement, including all shared assumptions, hypotheses,
  subparts, and conclusions.
- proof: only the proof belonging to that unit, or "" when no proof is present.
- label: the exact label surface at the start of the current top-level unit, or "".

Label rules:
1. A valid label does not need to contain a node type. Examples include `(1.2)`,
   `(A.3)`, `2.4.1`, `A.3`, `T1`, `Theorem 2.4`, `Lemma IV`, and `定理3.2`.
2. Copy the label exactly as it appears at the beginning of the current unit.
3. Do not use section numbers, internal subparts such as `(a)` or `(b)`, labels
   appearing only inside prose, labels cited by a proof, or references such as
   `By Theorem 2.4`.
4. If no reliable top-level label is present, return "".

Extraction rules:
1. Preserve the source language and mathematical meaning.
2. Do not omit a parent statement's shared setup when its conclusions are written
   as subparts.
3. Do not invent, translate, normalize, or repair unsupported source content.
4. Preserve LaTeX expressions, commands, environments, and math delimiters exactly as source text.
5. Proof text must belong to the current unit and must not include a neighboring
   proposition.
6. When node_type is English, use lowercase canonical names such as theorem,
   lemma, proposition, corollary, definition, example, exercise, or remark.
   Do not output capitalized node_type values such as Theorem or Lemma.
7. Return only a valid dictionary matching this schema:
{data_template}

Source block:
{pos1}
"""


correction_prompt03 = """
Your previous extraction was invalid. Return only a valid dictionary matching the
required schema. Preserve node_type, content, proof, and label fields. Do not invent
labels or mathematical content.

Required schema:
{data_template}

Invalid previous answer:
{answer}
"""


def validation03(value):
    if not isinstance(value, dict):
        return False
    for node in value.values():
        if not isinstance(node, dict):
            return False
        if any(field not in node for field in ("node_type", "content", "proof", "label")):
            return False
        if not all(isinstance(node.get(field), str) for field in ("node_type", "content", "proof", "label")):
            return False
    return True


tex_residual_data_template = """{{
  0: {{
    "node_type": "standard mathematical logic unit type",
    "source_quote": r\"\"\"one complete, contiguous, verbatim quote from the source span\"\"\",
    "label": "exact explicit source label or empty string"
  }}
}}"""


tex_residual_prompt_template = r"""
You are finding mathematical logic units stated in TeX prose outside all recognized
theorem-like and proof environments. Return one entry per independent unit, or an
empty dictionary when this span contains no such unit.

Allowed units include definitions, theorems, lemmas, propositions, corollaries,
claims, axioms, properties, examples, exercises, conjectures, problems,
observations, facts, and remarks.

For every returned unit:
- node_type must be one standard unit type.
- source_quote must be one complete and contiguous verbatim substring of the source
  span. Include all assumptions, defining conditions, displayed formulas, subparts,
  and conclusions needed to make the unit self-contained.
- Copy source_quote character for character. In particular, preserve Chinese
  full-width punctuation (，。；：), ASCII punctuation, spaces, tabs, line breaks,
  and every TeX backslash exactly; do not normalize one form into another. The
  program will reject the whole result unless source_quote occurs exactly once in
  the supplied source span.
- Encode every source_quote with the raw triple-quoted Python string form shown in
  the schema: r\"\"\"...\"\"\". Do not replace source whitespace with the two
  literal characters \t or \n, and do not add JSON-style backslash escaping. This
  format is parsed by the pipeline's existing LaTeX-preserving dictionary parser.
- Return separately stated concepts as separate entries even when the author puts
  them in the same \par block; do not merge definitions of different terms.
- label must be copied exactly when an explicit label begins the quoted unit;
  otherwise it must be "".

Do not return:
- citations or references to an earlier theorem, definition, example, or section;
- proof steps, calculations, transitions, motivation, roadmap, or section summary;
- locally repeated conclusions or summaries introduced by phrases such as
  "由此我们得到", "简而言之", "容易看到", "从以上定理可以看到",
  "therefore we obtain", or "in summary";
- caution/instruction lists that merely recap how earlier defined operations are
  used;
- isolated terminology or notation without its defining statement;
- incomplete hypotheses, conclusions, formulas, or sentence fragments;
- any rewritten, translated, normalized, or invented text.

Return only a dictionary matching this schema:
{data_template}

Exact residual TeX source span:
{pos1}
"""


tex_residual_correction_template = r"""
Your previous residual-TeX discovery was invalid. Return only a dictionary matching
the schema below. Every source_quote must be copied as one contiguous verbatim
substring of the supplied source span. Return an empty dictionary if no qualifying
unit exists.

Required schema:
{data_template}

Invalid previous answer:
{answer}
"""


def validation_tex_residual(value):
    if not isinstance(value, dict):
        return False
    for node in value.values():
        if not isinstance(node, dict):
            return False
        if set(node) != {"node_type", "source_quote", "label"}:
            return False
        if not all(isinstance(node.get(field), str) for field in ("node_type", "source_quote", "label")):
            return False
    return True
