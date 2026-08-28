SPLIT_NODE_FIELDS = ("sub_nodes",)
SPLIT_SUBNODE_FIELDS = (
    "index",
    "content",
    "conclusion",
    "kind",
    "statement_form",
    "source_conclusion",
    "equivalence_components",
    "applicable_context",
    "applicable_conditions_text",
    "label_suffix",
)


data_template04 = r'''{
  "sub_nodes": [
    {
      "index": 1,
      "content": "one complete derived statement unit",
      "conclusion": "one atomic conclusion or implication direction",
      "kind": "conclusion / iff_direction / iff_cycle_pair / unsplit",
      "statement_form": "implication / equivalence / equality / existence / characterization / other",
      "source_conclusion": "the corresponding source conclusion",
      "equivalence_components": [],
      "applicable_context": "",
      "applicable_conditions_text": [],
      "label_suffix": ""
    }
  ]
}'''


prompt_template04 = r'''
You are decomposing one mathematical relation statement into internal conclusion
units. The input parent node is source-owned and immutable.

Input parent node:
{pos1}

Return only valid JSON matching this schema:
{data_template}

Rules:
1. Return only sub_nodes. Never return parent_node, node_type, proof, label,
   parent_label, title, global_id, content for the parent, or other parent fields.
2. Each sub-node must express exactly one conclusion or one implication direction.
3. Preserve LaTeX and mathematical meaning. Do not invent conditions or results.
4. If no split is needed, return one sub-node with kind "unsplit".
5. index is 1-based and follows source order.
6. applicable_conditions_text and equivalence_components must be lists of strings.
'''


correction_prompt04 = r'''
The previous split result was invalid. Return only valid JSON matching:
{data_template}

Do not return any complete parent-node fields. Correct this answer:
{answer}
'''


def validation04(text):
    if not isinstance(text, dict):
        return False
    sub_nodes = text.get("sub_nodes")
    if not isinstance(sub_nodes, list) or not sub_nodes:
        return False

    required_string_fields = (
        "content",
        "conclusion",
        "kind",
        "statement_form",
    )
    optional_string_fields = (
        "source_conclusion",
        "applicable_context",
        "label_suffix",
    )
    for expected_index, sub_node in enumerate(sub_nodes, start=1):
        if not isinstance(sub_node, dict):
            return False
        if sub_node.get("index") != expected_index:
            return False
        if any(
            not isinstance(sub_node.get(field_name), str)
            or not sub_node.get(field_name).strip()
            for field_name in required_string_fields
        ):
            return False
        if any(
            field_name in sub_node
            and not isinstance(sub_node.get(field_name), str)
            for field_name in optional_string_fields
        ):
            return False
        for field_name in ("equivalence_components", "applicable_conditions_text"):
            value = sub_node.get(field_name, [])
            if not isinstance(value, list) or not all(
                isinstance(item, str) for item in value
            ):
                return False
    return True
