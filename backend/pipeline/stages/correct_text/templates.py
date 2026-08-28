import json


data_template01 = """
{
  "corrected_units": {
    "unit_id": "corrected target text"
  },
  "warnings": [
    {
      "unit_id": "unit_id",
      "reason": "source remains ambiguous"
    }
  ]
}
"""


prompt_template01 = """
You are performing active but structure-preserving OCR recovery on mathematical text.

Repair obvious OCR corruption, duplicated words, broken spacing, mojibake, and text that
can be uniquely recovered from the supplied local context. This is transcription repair,
not mathematical rewriting.

Rules:
1. Return every target unit exactly once, using the same unit IDs and order.
2. Do not merge, split, add, delete, or reorder target units.
3. Preserve every mathematical claim, assumption, conclusion, proof step, label, citation,
   and paragraph boundary.
4. Do not invent mathematical content that is unsupported by the target and context.
5. Preserve LaTeX expressions, commands, environments, labels, citations, and
   math delimiters exactly as source text unless an OCR error is unambiguous.
6. Previous and next context are evidence only. Never copy them into corrected target units.
7. If text cannot be recovered confidently, keep it unchanged and add a warning.
8. Return only valid JSON matching this schema:
{data_template}

Previous context:
{previous_context}

Target units:
{target_units}

Next context:
{next_context}
"""


correction_prompt01 = """
Your previous answer was invalid. Correct its JSON shape and any unsupported edits by
checking it against the original target units below.

Apply the same OCR recovery rules:
- return every target unit exactly once with the same IDs and order;
- preserve LaTeX expressions and commands, mathematical meaning, labels, citations, and boundaries;
- never copy context into the target;
- keep uncertain text unchanged and record a warning;
- return only valid JSON.

Required schema:
{data_template}

Previous context:
{previous_context}

Original target units:
{target_units}

Next context:
{next_context}

Invalid previous answer:
{answer}
"""


def build_prompt(previous_context, target_units, next_context):
    return prompt_template01.format(
        data_template=data_template01,
        previous_context=json.dumps(previous_context, ensure_ascii=False, indent=2),
        target_units=json.dumps(target_units, ensure_ascii=False, indent=2),
        next_context=json.dumps(next_context, ensure_ascii=False, indent=2),
    )


def build_correction_prompt(previous_context, target_units, next_context, answer):
    return correction_prompt01.format(
        data_template=data_template01,
        previous_context=json.dumps(previous_context, ensure_ascii=False, indent=2),
        target_units=json.dumps(target_units, ensure_ascii=False, indent=2),
        next_context=json.dumps(next_context, ensure_ascii=False, indent=2),
        answer=answer,
    )


def validation01(value):
    if not isinstance(value, dict) or not value:
        return False
    corrected_units = value.get("corrected_units")
    warnings = value.get("warnings", [])
    return (
        isinstance(corrected_units, dict)
        and bool(corrected_units)
        and all(
            isinstance(unit_id, str)
            and isinstance(unit_text, str)
            and bool(unit_text.strip())
            for unit_id, unit_text in corrected_units.items()
        )
        and isinstance(warnings, list)
    )
