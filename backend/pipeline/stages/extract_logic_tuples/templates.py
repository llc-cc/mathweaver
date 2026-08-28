DERIVED_LOGIC_FIELDS = (
    "statement_form",
    "subject",
    "context",
    "variables",
    "conditions",
    "conclusions",
)


data_template06 = r'''{
  "statement_form": "implication / equivalence / existence / equality / characterization / other",
  "subject": ["core mathematical object"],
  "context": ["domain or ambient mathematical context"],
  "variables": [
    {"name": "variable name", "type": "variable type"}
  ],
  "conditions": [
    {"id": "c1", "text": "one atomic condition"}
  ],
  "conclusions": [
    {"id": "q1", "text": "one atomic conclusion"}
  ]
}'''


prompt_template06 = r'''
You are extracting derived logical fields from one immutable mathematical source
node.

Input node:
{pos1}

Return only valid JSON matching:
{data_template}

Rules:
1. Return exactly one derived object for the input node.
2. Never return node_type, title, content, original_form, remark, proof, label,
   global_id, source fields, or a complete node.
3. Every condition and conclusion must be atomic and preserve the mathematical
   meaning and LaTeX of the source.
4. Prefer the supplied subnode_specs when assigning conditions and conclusions.
5. Use empty lists when a derived list cannot be established from the source.
'''


correction_prompt06 = r'''
The previous logical-field extraction was invalid. Return only valid JSON matching:
{data_template}

Do not return source-owned or complete-node fields. Correct this answer:
{answer}
'''


def validation06(text):
    if not isinstance(text, dict):
        return False
    if not isinstance(text.get("statement_form"), str):
        return False
    if text.get("statement_form") not in {
        "implication",
        "equivalence",
        "existence",
        "equality",
        "characterization",
        "other",
    }:
        return False

    for field_name in ("subject", "context"):
        value = text.get(field_name)
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            return False

    variables = text.get("variables")
    if not isinstance(variables, list):
        return False
    for variable in variables:
        if (
            not isinstance(variable, dict)
            or not isinstance(variable.get("name"), str)
            or not isinstance(variable.get("type"), str)
        ):
            return False

    for field_name, prefix in (("conditions", "c"), ("conclusions", "q")):
        values = text.get(field_name)
        if not isinstance(values, list):
            return False
        for index, item in enumerate(values, start=1):
            if (
                not isinstance(item, dict)
                or item.get("id") != f"{prefix}{index}"
                or not isinstance(item.get("text"), str)
                or not item.get("text").strip()
            ):
                return False
    return True
