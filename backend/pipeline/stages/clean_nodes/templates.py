VALID_ACTIONS = {"keep", "quarantine", "manual_review"}
VALID_CONFIDENCE = {"high", "medium", "low"}


data_template_clean_nodes = '''{
  "55": {
    "action": "quarantine",
    "reason": "The node only refers to an unspecified above assertion and has no standalone mathematical statement.",
    "confidence": "high",
    "evidence": [
      "content is deictic",
      "no standalone mathematical statement"
    ]
  }
}
'''


prompt_template_clean_nodes = '''
You are reviewing extracted MathKG nodes immediately after statement extraction.

Your task is to decide whether each node can stand as an independent mathematical
knowledge-graph node. You must not rewrite, repair, merge, split, summarize, or
add nodes. Only classify each input key.

Input packet:
{pos1}

Return only valid JSON with exactly this shape:
{data_template}

Allowed action values:
- "keep": the node contains a standalone mathematical statement, definition,
  concrete exercise task, example, remark, proof-bearing assertion, or other
  meaningful mathematical content.
- "quarantine": the node has no standalone mathematical meaning and only points
  to an unspecified previous/following/above assertion, theorem, result, proof,
  or statement.
- "manual_review": the node is damaged or ambiguous, but it may contain useful
  mathematical content and should not be automatically removed.

Quarantine examples:
- "Prove the above assertion."
- "Show the above result."
- "Proof of the above theorem."
- "证明上述断言。"
- "同上。"

Keep examples:
- "Prove that every compact subset of a metric space is closed."
- "Show that \\(d(x,A)\\) is continuous in \\(x\\)."
- "Find all fixed points of the contraction mapping."

Important rules:
1. Do not quarantine a node merely because node_type is "exercise".
2. Keep concrete exercise tasks that contain an explicit mathematical object,
   proposition, equation, property, construction, or goal.
3. Quarantine only when the node itself lacks an explicit mathematical statement
   and depends on words such as above, previous, following, aforementioned,
   上述, 前述, 如上, 同上, or similar deictic references.
4. If source_text contains only a TeX exercise environment whose body is
   "Prove the above assertion.", quarantine it.
5. If there is any plausible standalone mathematical content, use
   "manual_review" instead of "quarantine".
6. Include every input node key exactly once. Do not invent keys.
7. Return JSON only.
'''


correction_prompt_clean_nodes = '''
Your previous answer was not valid for the MathKG node cleaning task.

Return only valid JSON. The top-level object keys must be node keys from the
input. Each value must be an object with:
- action: one of "keep", "quarantine", "manual_review"
- reason: a short string
- confidence: one of "high", "medium", "low"
- evidence: a list of short strings

Do not include Markdown fences or explanatory text.

Original input packet:
{pos1}

Required JSON shape:
{data_template}
'''


def validation_clean_nodes(result):
    if not isinstance(result, dict):
        return False
    for value in result.values():
        if not isinstance(value, dict):
            return False
        action = str(value.get("action", "")).strip()
        if action not in VALID_ACTIONS:
            return False
        confidence = str(value.get("confidence", "low")).strip() or "low"
        if confidence not in VALID_CONFIDENCE:
            return False
        if "reason" in value and not isinstance(value.get("reason"), str):
            return False
        if "evidence" in value and not isinstance(value.get("evidence"), list):
            return False
    return True
