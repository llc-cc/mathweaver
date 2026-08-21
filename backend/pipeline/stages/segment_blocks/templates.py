import json


ALLOWED_ROLES = {
    "top_level_logical_unit_start",
    "proof_start_or_continuation",
    "subpart_or_item",
    "heading_or_section_start",
    "ordinary_continuation",
    "reference_only",
}


data_template02 = """
{
  "units": {
    "unit_id": {
      "role": "one allowed role",
      "label_surface": "exact source label text or empty string",
      "label_family": "diagnostic label family or empty string",
      "logical_unit_type_hint": "type hint or empty string",
      "evidence": ["short evidence item"],
      "reason": "short semantic reason"
    }
  },
  "warnings": [
    {
      "unit_id": "unit_id",
      "reason": "ambiguity that should be reviewed"
    }
  ]
}
"""


prompt_template02 = """
You are classifying the semantic boundary role of an ordered stream of mathematical
text units. The result will be used to group the units into complete proposition
blocks. You must classify roles only; never rewrite, delete, merge, split, or invent
source text.

Allowed roles:
- top_level_logical_unit_start: starts an independent definition, theorem, lemma,
  proposition, corollary, claim, axiom, property, example, exercise, remark, or other
  complete top-level mathematical logic unit.
- proof_start_or_continuation: starts or continues a proof belonging to the preceding
  top-level logic unit.
- subpart_or_item: a part such as (a), (b), (i), or another internal item belonging to
  the active parent logic unit.
- heading_or_section_start: a document heading or section boundary, not a proposition.
- ordinary_continuation: explanatory text, formulas, or continuation of the active unit.
- reference_only: text whose apparent label is a citation or reference, not a new unit.

Hard rules:
1. Do not assume labels use any fixed surface format. Labels may be named, numeric,
   Roman, symbolic, custom, absent, or written in another language.
2. A label-like string is only evidence. It may instead be a citation, section number,
   subpart, or ordinary number.
3. A top-level mathematical unit may have no explicit label.
4. Proof text belongs to the preceding top-level unit and must not start a new block.
5. Internal subparts stay with their parent unit.
6. Use each unit's rule evidence as hints, not as final decisions.
7. Return every input unit ID exactly once. Preserve the input order.
8. Copy label_surface exactly from the source when present. Do not normalize it.
9. If a boundary is genuinely ambiguous, choose the conservative continuation role and
   add a warning.
10. Return only valid JSON matching this schema:
{data_template}

Ordered units and rule-generated evidence:
{unit_packet}
"""


correction_prompt02 = """
Your previous semantic-role classification was invalid or incomplete. Reclassify the
original ordered units below.

Return every original unit ID exactly once and use only the allowed roles. Do not alter
source text. A label-like string alone never proves that a new top-level unit starts.
Proofs and internal subparts must remain attached to their parent. For genuine ambiguity,
choose a conservative continuation role and add a warning.

Required schema:
{data_template}

Original ordered units and evidence:
{unit_packet}

Invalid previous answer:
{answer}
"""


def build_prompt(unit_packet):
    return prompt_template02.format(
        data_template=data_template02,
        unit_packet=json.dumps(unit_packet, ensure_ascii=False, indent=2),
    )


def build_correction_prompt(unit_packet, answer):
    return correction_prompt02.format(
        data_template=data_template02,
        unit_packet=json.dumps(unit_packet, ensure_ascii=False, indent=2),
        answer=answer,
    )


def validation02(value, expected_unit_ids=None):
    if not isinstance(value, dict):
        return False
    units = value.get("units")
    warnings = value.get("warnings", [])
    if not isinstance(units, dict) or not isinstance(warnings, list):
        return False
    if expected_unit_ids is not None and list(units) != [str(unit_id) for unit_id in expected_unit_ids]:
        return False
    for classification in units.values():
        if not isinstance(classification, dict):
            return False
        if classification.get("role") not in ALLOWED_ROLES:
            return False
        if not isinstance(classification.get("evidence", []), list):
            return False
        for field in ("label_surface", "label_family", "logical_unit_type_hint", "reason"):
            if not isinstance(classification.get(field, ""), str):
                return False
    return True
