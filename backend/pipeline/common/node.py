import copy
import hashlib
import json
import re
import unicodedata


RELATION_STATEMENT_NODE_TYPES = {
    "\u6027\u8d28",  # 性质
    "\u5b9a\u7406",  # 定理
    "\u5f15\u7406",  # 引理
    "\u63a8\u8bba",  # 推论
    "property",
    "theorem",
    "lemma",
    "proposition",
    "corollary",
    "claim",
    "conjecture",
    "problem",
    "observation",
    "fact",
}

DEFINITION_NODE_TYPES = {
    "\u5b9a\u4e49",  # 定义
    "\u516c\u7406",  # 公理
    "definition",
    "axiom",
}

LOGIC_TUPLE_NON_RELATION_NODE_TYPES = {
    "\u4f8b",  # 例
    "\u4f8b\u5b50",  # 例子
    "\u793a\u4f8b",  # 示例
    "\u4e60\u9898",  # 习题
    "\u7ec3\u4e60",  # 练习
    "example",
    "exercise",
}


def is_relation_statement_node_type(value):
    text = value.strip() if isinstance(value, str) else ""
    return text in RELATION_STATEMENT_NODE_TYPES or text.lower() in RELATION_STATEMENT_NODE_TYPES


def is_definition_node_type(value):
    text = value.strip() if isinstance(value, str) else ""
    return text in DEFINITION_NODE_TYPES or text.lower() in DEFINITION_NODE_TYPES


def is_logic_tuple_node_type(value):
    text = value.strip() if isinstance(value, str) else ""
    return (
        is_relation_statement_node_type(text)
        or text in LOGIC_TUPLE_NON_RELATION_NODE_TYPES
        or text.lower() in LOGIC_TUPLE_NON_RELATION_NODE_TYPES
    )


def normalize_node_type(value):
    text = value.strip() if isinstance(value, str) else ""
    if text.isascii():
        return text.lower()
    return text


def normalize_node_types_in_tree(value):
    if isinstance(value, dict):
        if "node_type" in value:
            value["node_type"] = normalize_node_type(value.get("node_type"))
        for child in value.values():
            normalize_node_types_in_tree(child)
    elif isinstance(value, list):
        for child in value:
            normalize_node_types_in_tree(child)
    return value

STRUCTURED_LOGIC_FIELDS = (
    "statement_form",
    "subject",
    "context",
    "variables",
    "conditions",
    "conclusions",
    "logic_ast_local",
    "logic_form_rendered",
    "predicate_entries",
)

SOURCE_ENVELOPE_KEY = "_source_envelope"
SOURCE_ENVELOPE_SCHEMA_VERSION = 1
SOURCE_ENVELOPE_INTEGRITY_FIELD = "integrity_hash"
SOURCE_ENVELOPE_FIELDS = (
    "node_type",
    "content",
    "source_original_form",
    "proof",
    "label",
    "source_text",
    "source_span",
    "source_file",
    "source_block_key",
    "global_id",
)
SOURCE_ENVELOPE_NODE_FIELDS = (
    "node_type",
    "content",
    "source_original_form",
    "proof",
    "label",
    "source_span",
    "source_file",
    "global_id",
)

_IFF_MARKER_RE = re.compile(
    r"\s*(?:\\Leftrightarrow|<->|⇔|\biff\b|\bif\s+and\s+only\s+if\b|"
    r"\u5f53\u4e14\u4ec5\u5f53|\u7b49\u4ef7\u4e8e)\s*",
    flags=re.IGNORECASE,
)


def _build_label_patterns(label: str):
    label = (label or "").strip()
    if not label:
        return []
    escaped = re.escape(label)
    pattern_with_ws = re.sub(r"\\\s+", r"\\s*", escaped)
    patterns = [re.compile(pattern_with_ws)]
    compact = re.sub(r"\s+", "", label)
    if compact and compact != label:
        patterns.append(re.compile(re.escape(compact)))
    return patterns


def get_node_node_type(node):
    if not isinstance(node, dict):
        return ""
    return normalize_node_type(node.get("node_type", "") or "")


def get_node_label(node):
    if not isinstance(node, dict):
        return ""
    return node.get("label", "") or ""


def get_node_title(node, prefer="chinese"):
    if not isinstance(node, dict):
        return ""
    title = node.get("title")
    if isinstance(title, dict):
        if prefer == "english":
            return title.get("english") or title.get("chinese") or ""
        return title.get("chinese") or title.get("english") or ""
    if isinstance(title, str):
        return title
    return ""


def _content_original_form(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        original_form = value.get("original_form")
        return original_form if isinstance(original_form, str) else ""
    return ""


def get_node_content(node, prefer_disambiguated=True):
    if not isinstance(node, dict):
        return ""
    if prefer_disambiguated:
        disambiguated = _content_original_form(node.get("disambiguated_content"))
        if disambiguated:
            return disambiguated
    remark = node.get("remark")
    if isinstance(remark, dict) and isinstance(remark.get("original_form"), str):
        return remark.get("original_form") or ""
    content = node.get("content")
    if isinstance(content, dict):
        return content.get("original_form") or content.get("formal_statement_core") or ""
    if isinstance(content, str):
        return content
    return ""


def get_node_source_original_text(node):
    """Return the authoritative source statement used to derive ``global_id``.

    Relation-statement nodes carry an immutable ``source_original_form``.
    Definition/axiom nodes and other node types use their original ``content``.
    Deliberately do not fall back to conclusions, titles, labels, or proofs.
    """
    if not isinstance(node, dict):
        return ""

    node_type = get_node_node_type(node)
    if is_relation_statement_node_type(node_type):
        source_original_form = node.get("source_original_form")
        return source_original_form if isinstance(source_original_form, str) else ""

    return _content_original_form(node.get("content"))


def normalize_source_text_for_id(text):
    """Apply serialization-only normalization to authoritative source text."""
    if not isinstance(text, str):
        return ""
    normalized = unicodedata.normalize("NFC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", normalized).strip()


def compute_global_id_from_source(node):
    normalized_source = normalize_source_text_for_id(get_node_source_original_text(node))
    if not normalized_source:
        node_type = get_node_node_type(node) or "<unknown>"
        label = get_node_label(node) or "<unlabeled>"
        raise ValueError(
            "Cannot generate global_id: authoritative source text is missing "
            f"for node_type={node_type!r}, label={label!r}"
        )
    return hashlib.md5(normalized_source.encode("utf-8")).hexdigest()


def _source_envelope_integrity_hash(envelope):
    payload = {
        "schema_version": envelope.get("schema_version"),
        **{
            field_name: envelope.get(field_name)
            for field_name in SOURCE_ENVELOPE_FIELDS
        },
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def merge_node_with_source_envelope(
    source_node,
    derived_output,
    *,
    stage_name,
    allowed_fields,
    seal=False,
    source_metadata=None,
):
    """Seal or merge one node while keeping its source-owned fields immutable.

    This is the single public merge boundary for model-derived top-level node
    fields.  Stages may only write fields explicitly listed in ``allowed_fields``.
    Unknown or source-owned model fields are ignored and reported in the returned
    audit record.
    """
    if not isinstance(source_node, dict):
        raise TypeError(f"{stage_name}: source node must be a dict")

    merged = copy.deepcopy(source_node)
    metadata = source_metadata if isinstance(source_metadata, dict) else {}

    if seal:
        merged["node_type"] = normalize_node_type(merged.get("node_type"))
        if is_relation_statement_node_type(merged.get("node_type")):
            source_original_form = merged.get("source_original_form")
            if not isinstance(source_original_form, str) or not source_original_form.strip():
                source_original_form = _content_original_form(merged.get("content"))
            if not source_original_form:
                source_original_form = get_node_content(merged, prefer_disambiguated=False)
            merged["source_original_form"] = source_original_form

        global_id = compute_global_id_from_source(merged)
        envelope = {"schema_version": SOURCE_ENVELOPE_SCHEMA_VERSION}
        for field_name in SOURCE_ENVELOPE_FIELDS:
            if field_name == "global_id":
                value = global_id
            elif field_name in metadata:
                value = metadata.get(field_name)
            else:
                value = merged.get(field_name)
            envelope[field_name] = copy.deepcopy(value)
        envelope[SOURCE_ENVELOPE_INTEGRITY_FIELD] = (
            _source_envelope_integrity_hash(envelope)
        )
        merged[SOURCE_ENVELOPE_KEY] = envelope
    else:
        envelope = merged.get(SOURCE_ENVELOPE_KEY)
        if not isinstance(envelope, dict):
            raise ValueError(
                f"{stage_name}: source envelope is missing; this cache predates "
                "source-envelope schema v1. Rerun from extract_statements."
            )
        if envelope.get("schema_version") != SOURCE_ENVELOPE_SCHEMA_VERSION:
            raise ValueError(
                f"{stage_name}: unsupported source envelope schema "
                f"{envelope.get('schema_version')!r}; rerun from extract_statements."
            )
        envelope = copy.deepcopy(envelope)
        if envelope.get(SOURCE_ENVELOPE_INTEGRITY_FIELD) != (
            _source_envelope_integrity_hash(envelope)
        ):
            raise ValueError(
                f"{stage_name}: source envelope integrity check failed; "
                "rerun from extract_statements."
            )

        envelope_node = {
            field_name: copy.deepcopy(envelope.get(field_name))
            for field_name in SOURCE_ENVELOPE_NODE_FIELDS
            if field_name != "global_id" and envelope.get(field_name) is not None
        }
        expected_global_id = compute_global_id_from_source(envelope_node)
        if envelope.get("global_id") != expected_global_id:
            raise ValueError(
                f"{stage_name}: source envelope global_id does not match its "
                "authoritative source text"
            )

    allowed = {str(field_name) for field_name in (allowed_fields or ())}
    derived = derived_output if isinstance(derived_output, dict) else {}
    ignored_fields = sorted(str(key) for key in derived if str(key) not in allowed)
    applied_fields = []
    for field_name in allowed:
        if field_name not in derived:
            continue
        merged[field_name] = copy.deepcopy(derived[field_name])
        applied_fields.append(field_name)

    for field_name in SOURCE_ENVELOPE_NODE_FIELDS:
        value = envelope.get(field_name)
        if value is None:
            continue
        if field_name in {"source_original_form", "source_span", "source_file"} and value in ("", {}):
            if field_name not in merged:
                continue
        merged[field_name] = copy.deepcopy(value)
    merged[SOURCE_ENVELOPE_KEY] = copy.deepcopy(envelope)

    expected_global_id = compute_global_id_from_source(merged)
    if expected_global_id != envelope.get("global_id"):
        raise ValueError(
            f"{stage_name}: merged node changed the source-derived global_id"
        )

    audit = {
        "stage": str(stage_name),
        "applied_fields": sorted(applied_fields),
        "ignored_fields": ignored_fields,
        "global_id": envelope.get("global_id"),
        "sealed": bool(seal),
    }
    return merged, audit


def get_node_formal_content(node):
    if not isinstance(node, dict):
        return ""
    conclusions = node.get("conclusions")
    if isinstance(conclusions, list) and len(conclusions) == 1 and isinstance(conclusions[0], dict):
        conclusion_text = conclusions[0].get("text")
        if isinstance(conclusion_text, str) and conclusion_text.strip():
            return conclusion_text
    conclusion = node.get("conclusion")
    if isinstance(conclusion, str) and conclusion.strip():
        return conclusion
    content = node.get("content")
    if isinstance(content, dict) and isinstance(content.get("formal_statement_core"), str):
        return content.get("formal_statement_core") or ""
    remark = node.get("remark")
    if isinstance(remark, dict) and isinstance(remark.get("formal_statement_core"), str):
        return remark.get("formal_statement_core") or ""
    return get_node_content(node)


def get_node_proof(node):
    if not isinstance(node, dict):
        return ""
    proof = node.get("proof")
    if isinstance(proof, dict):
        return proof.get("text") or proof.get("text_normalized") or ""
    if isinstance(proof, str):
        return proof
    return ""


def assemble_statement_text(node):
    base = get_node_formal_content(node)
    if base:
        return base
    parts = []
    conditions = node.get("conditions")
    if isinstance(conditions, list):
        cond_texts = [c.get("text", "") for c in conditions if isinstance(c, dict) and c.get("text")]
        if cond_texts:
            parts.append("条件: " + "；".join(cond_texts))
    conclusions = node.get("conclusions")
    if isinstance(conclusions, list):
        concl_texts = [c.get("text", "") for c in conclusions if isinstance(c, dict) and c.get("text")]
        if concl_texts:
            parts.append("结论: " + "；".join(concl_texts))
    return " ".join(parts)


def get_node_field_texts(node, field_name):
    items = node.get(field_name)
    if not isinstance(items, list):
        return []

    texts = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        text = item.get("text_normalized") or item.get("text") or ""
        text = str(text).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        texts.append(text)
    return texts


def _as_text(value):
    return value.strip() if isinstance(value, str) else ""


def _as_list(value):
    return value if isinstance(value, list) else []


def _copy_list(value):
    return copy.deepcopy(value) if isinstance(value, list) else []


def discourse_source(node):
    if not isinstance(node, dict):
        return {}
    content = node.get("content")
    if isinstance(content, dict):
        return content
    remark = node.get("remark")
    if isinstance(remark, dict):
        return remark
    return {}


def split_iff_components(text):
    text = _as_text(text)
    if not text:
        return []
    parts = [_as_text(part) for part in _IFF_MARKER_RE.split(text)]
    return [part for part in parts if part]


def build_subnode_specs_from_conclusions(local_conclusions):
    specs = []
    for conclusion in _as_list(local_conclusions):
        conclusion_text = _as_text(conclusion)
        if not conclusion_text:
            continue

        components = split_iff_components(conclusion_text)
        if len(components) == 2:
            left, right = components
            specs.append(
                {
                    "index": len(specs) + 1,
                    "kind": "iff_direction",
                    "statement_form": "implication",
                    "source_conclusion": conclusion_text,
                    "equivalence_components": [left, right],
                    "conclusion": f"If {left}, then {right}.",
                }
            )
            specs.append(
                {
                    "index": len(specs) + 1,
                    "kind": "iff_direction",
                    "statement_form": "implication",
                    "source_conclusion": conclusion_text,
                    "equivalence_components": [right, left],
                    "conclusion": f"If {right}, then {left}.",
                }
            )
            continue

        if len(components) >= 3:
            for left, right in zip(components, components[1:] + components[:1]):
                specs.append(
                    {
                        "index": len(specs) + 1,
                        "kind": "iff_cycle_pair",
                        "statement_form": "equivalence",
                        "source_conclusion": conclusion_text,
                        "equivalence_components": [left, right],
                        "conclusion": f"{left} iff {right}.",
                    }
                )
            continue

        specs.append(
            {
                "index": len(specs) + 1,
                "kind": "conclusion",
                "statement_form": "",
                "source_conclusion": conclusion_text,
                "equivalence_components": [],
                "conclusion": conclusion_text,
            }
        )
    return specs


def _conclusion_texts_from_node(node):
    conclusions = node.get("conclusions") if isinstance(node, dict) else None
    if not isinstance(conclusions, list):
        return []
    return [
        item.get("text")
        for item in conclusions
        if isinstance(item, dict) and _as_text(item.get("text"))
    ]


def _generated_specs_from_node_logic(node):
    source = discourse_source(node)
    local_conclusions = source.get("local_conclusions")
    if isinstance(local_conclusions, list) and local_conclusions:
        return build_subnode_specs_from_conclusions(local_conclusions)

    conclusion_texts = _conclusion_texts_from_node(node)
    if conclusion_texts:
        return build_subnode_specs_from_conclusions(conclusion_texts)

    formal = get_node_formal_content(node)
    return build_subnode_specs_from_conclusions([formal]) if formal else []


def _normalize_subnode_specs(specs):
    if not isinstance(specs, list) or not specs:
        return []
    normalized = []
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        conclusion = _as_text(spec.get("conclusion"))
        if not conclusion:
            continue
        copied = copy.deepcopy(spec)
        copied["index"] = len(normalized) + 1
        copied["conclusion"] = conclusion
        normalized.append(copied)
    return normalized


def _is_single_unsplit_spec(specs):
    if len(specs) != 1:
        return False
    spec = specs[0]
    kind = _as_text(spec.get("kind")).lower()
    statement_form = _as_text(spec.get("statement_form")).lower()
    return kind == "unsplit" or not statement_form


def subnode_specs_from_node(node):
    direct_specs = node.get("subnode_specs") if isinstance(node, dict) else None
    if isinstance(direct_specs, list) and direct_specs:
        specs = direct_specs
    else:
        source = discourse_source(node)
        specs = source.get("subnode_specs")
    normalized = _normalize_subnode_specs(specs)
    generated = []
    if _as_text(node.get("statement_form")).lower() == "equivalence":
        generated = _generated_specs_from_node_logic(node)
        if len(generated) > 1 and (not normalized or _is_single_unsplit_spec(normalized)):
            return generated
    if normalized:
        return normalized
    return generated or _generated_specs_from_node_logic(node)


def _find_conclusion_item(conclusions, text, fallback_id):
    normalized_target = re.sub(r"\s+", " ", _as_text(text)).strip(" .;:")
    if isinstance(conclusions, list):
        for item in conclusions:
            if not isinstance(item, dict):
                continue
            item_text = _as_text(item.get("text"))
            normalized_item = re.sub(r"\s+", " ", item_text).strip(" .;:")
            if normalized_item and normalized_item == normalized_target:
                return copy.deepcopy(item)
    return {"id": fallback_id, "text": _as_text(text)}


def _subnode_from_spec(parent_node, spec):
    index = spec.get("index")
    try:
        index = int(index)
    except (TypeError, ValueError):
        index = 1

    conclusion = _find_conclusion_item(
        parent_node.get("conclusions"),
        spec.get("conclusion"),
        f"q{index}",
    )
    conclusion["id"] = conclusion.get("id") or f"q{index}"
    conclusion["text"] = _as_text(conclusion.get("text")) or _as_text(spec.get("conclusion"))

    source = discourse_source(parent_node)
    original_form = _as_text(source.get("original_form")) or get_node_content(parent_node)
    statement_form = _as_text(spec.get("statement_form")) or _as_text(parent_node.get("statement_form"))
    child_content = _as_text(spec.get("content")) or conclusion["text"]
    parent_label = get_node_label(parent_node)
    label_suffix = _as_text(spec.get("label_suffix"))
    child_label = (
        f"{parent_label}.{label_suffix}"
        if parent_label and label_suffix
        else parent_label
    )

    return {
        "index": index,
        "kind": spec.get("kind") or "conclusion",
        "node_type": get_node_node_type(parent_node),
        "proof": copy.deepcopy(parent_node.get("proof", "")),
        "label": child_label,
        "label_suffix": label_suffix,
        "statement_form": statement_form,
        "source_conclusion": _as_text(spec.get("source_conclusion")),
        "equivalence_components": _copy_list(spec.get("equivalence_components")),
        "content": child_content,
        "parent_label": parent_label,
        "parent_title": copy.deepcopy(parent_node.get("title", {})),
        "remark": {
            "original_form": child_content,
            "applicable_context": _as_text(spec.get("applicable_context")),
            "applicable_conditions_text": _copy_list(spec.get("applicable_conditions_text")),
            "parent_original_form": original_form,
        },
        "subject": _copy_list(parent_node.get("subject")),
        "context": _copy_list(parent_node.get("context")),
        "variables": _copy_list(parent_node.get("variables")),
        "applicable_conditions": _copy_list(parent_node.get("conditions")),
        "conditions": _copy_list(parent_node.get("conditions")),
        "conclusions": [conclusion],
    }


def attach_internal_subnodes(node):
    if not isinstance(node, dict):
        return node
    node = copy.deepcopy(node)
    node_type = _as_text(node.get("node_type"))
    if not is_relation_statement_node_type(node_type):
        return node

    specs = subnode_specs_from_node(node)
    if len(specs) <= 1:
        return node

    node["subnode_specs"] = copy.deepcopy(specs)
    node["sub_nodes"] = [_subnode_from_spec(node, spec) for spec in specs]
    node["subnode_display"] = "expandable"
    node["subnode_count"] = len(node["sub_nodes"])
    for field in STRUCTURED_LOGIC_FIELDS:
        node.pop(field, None)
    return node


def attach_internal_subnodes_to_dict(node_dict):
    if isinstance(node_dict, dict):
        return {
            key: attach_internal_subnodes(node)
            for key, node in node_dict.items()
        }
    if isinstance(node_dict, list):
        return [attach_internal_subnodes(node) for node in node_dict]
    return node_dict


def structured_parent_view(node):
    """Return a parent-level logic view even after internal subnodes were attached."""
    if not isinstance(node, dict):
        return node
    view = copy.deepcopy(node)
    subnodes = view.get("sub_nodes")
    if not isinstance(subnodes, list) or not subnodes:
        return view

    first = next((item for item in subnodes if isinstance(item, dict)), None)
    if first is None:
        return view
    for field in ("subject", "context", "variables", "conditions"):
        if not view.get(field) and first.get(field):
            view[field] = copy.deepcopy(first[field])
    if not view.get("statement_form") and first.get("statement_form"):
        view["statement_form"] = first["statement_form"]
    if not view.get("conclusions"):
        conclusions = []
        for subnode in subnodes:
            if isinstance(subnode, dict) and isinstance(subnode.get("conclusions"), list):
                conclusions.extend(copy.deepcopy(subnode["conclusions"]))
        if conclusions:
            view["conclusions"] = conclusions
    return view


def _iter_node_items(nodes):
    if isinstance(nodes, dict):
        return nodes.items()
    if isinstance(nodes, list):
        return ((str(index), node) for index, node in enumerate(nodes))
    return ()


def make_match_unit_key(parent_key, sub_index=None):
    if sub_index is None:
        return str(parent_key)
    return f"{parent_key}__sub{sub_index}"


def parse_match_unit_key(unit_key):
    text = str(unit_key)
    if "__sub" not in text:
        return text, None
    parent_key, _, sub_text = text.rpartition("__sub")
    try:
        return parent_key, int(sub_text)
    except ValueError:
        return parent_key, None


def _parent_lookup(nodes):
    return {str(key): node for key, node in _iter_node_items(nodes) if isinstance(node, dict)}


def make_match_unit(parent_node, subnode=None):
    parent_global_id = _as_text(parent_node.get("global_id"))
    unit = {
        "global_id": parent_global_id,
        "parent_global_id": parent_global_id,
        "node_type": parent_node.get("node_type", ""),
        "title": copy.deepcopy(parent_node.get("title", {})),
        "label": parent_node.get("label", ""),
        "proof": copy.deepcopy(parent_node.get("proof", "")),
    }

    if isinstance(subnode, dict):
        unit.update(
            {
                "sub_index": subnode.get("index", 1),
                "is_virtual_subnode": False,
                "statement_form": subnode.get("statement_form", ""),
                "remark": copy.deepcopy(subnode.get("remark", {})),
                "subject": copy.deepcopy(subnode.get("subject", [])),
                "context": copy.deepcopy(subnode.get("context", [])),
                "variables": copy.deepcopy(subnode.get("variables", [])),
                "conditions": copy.deepcopy(subnode.get("conditions", [])),
                "conclusions": copy.deepcopy(subnode.get("conclusions", [])),
                "logic_ast_local": copy.deepcopy(subnode.get("logic_ast_local", {})),
                "logic_form_rendered": subnode.get("logic_form_rendered", ""),
                "predicate_entries": copy.deepcopy(subnode.get("predicate_entries", [])),
            }
        )
        return unit

    unit.update(
        copy.deepcopy(
            {
                key: value
                for key, value in parent_node.items()
                if key not in {"sub_nodes", "subnode_display", "subnode_count"}
            }
        )
    )
    unit["global_id"] = parent_global_id
    unit["parent_global_id"] = parent_global_id
    unit["sub_index"] = 1
    unit["is_virtual_subnode"] = True
    return unit


def build_match_unit_dict(nodes):
    units = {}
    for parent_key, parent_node in _iter_node_items(nodes):
        if not isinstance(parent_node, dict):
            continue
        node_type = get_node_node_type(parent_node)
        if not (
            is_logic_tuple_node_type(node_type)
            or is_definition_node_type(node_type)
        ):
            continue
        derivation_status = parent_node.get("_derivation_status")
        logic_tuple_status = (
            derivation_status.get("extract_logic_tuples")
            if isinstance(derivation_status, dict)
            else None
        )
        if (
            isinstance(logic_tuple_status, dict)
            and logic_tuple_status.get("status") == "degraded"
        ):
            continue
        subnodes = parent_node.get("sub_nodes")
        if isinstance(subnodes, list) and subnodes:
            for subnode in subnodes:
                if not isinstance(subnode, dict):
                    continue
                units[make_match_unit_key(parent_key, subnode.get("index"))] = make_match_unit(parent_node, subnode)
        else:
            units[make_match_unit_key(parent_key)] = make_match_unit(parent_node)
    return units


def attach_fields_to_match_unit(nodes, unit_key, fields):
    parent_key, sub_index = parse_match_unit_key(unit_key)
    parents = _parent_lookup(nodes)
    parent = parents.get(str(parent_key))
    if not isinstance(parent, dict) or not isinstance(fields, dict):
        return False

    if sub_index is None:
        parent.update(copy.deepcopy(fields))
        return True

    subnodes = parent.get("sub_nodes")
    if not isinstance(subnodes, list):
        return False
    for subnode in subnodes:
        if not isinstance(subnode, dict):
            continue
        try:
            current_index = int(subnode.get("index"))
        except (TypeError, ValueError):
            continue
        if current_index == sub_index:
            subnode.update(copy.deepcopy(fields))
            return True
    return False


def match_unit_summary(unit):
    conclusions = get_node_field_texts(unit, "conclusions")
    return {
        "parent_global_id": _as_text(unit.get("parent_global_id")) or _as_text(unit.get("global_id")),
        "sub_index": unit.get("sub_index"),
        "is_virtual_subnode": bool(unit.get("is_virtual_subnode")),
        "conclusion": conclusions[0] if conclusions else get_node_formal_content(unit),
    }


def normalize_latex_backslashes(data):
    """Keep parsed LaTeX strings byte-for-byte intact.

    JSON decoding already turns serialization escapes into their runtime form.
    A second global ``\\\\ -> \\`` pass corrupts TeX row separators inside
    matrices, so this compatibility hook must not rewrite text values.
    """
    return data


def adjust(node):
    def _clean_text(text):
        if not isinstance(text, str):
            return text
        # This node value can later anchor source spans and MatrixFlow ranges.
        # Keep every character after JSON parsing; whitespace cleanup belongs to
        # a non-source display projection, never this canonical data path.
        return text

    node = copy.deepcopy(node)
    content_is_authoritative_source = not is_relation_statement_node_type(get_node_node_type(node))
    title = node.get("title")
    if isinstance(title, str):
        node["title"] = {"chinese": _clean_text(title), "english": _clean_text(title)}
    elif isinstance(title, dict):
        node["title"] = {
            "chinese": _clean_text(title.get("chinese", "")),
            "english": _clean_text(title.get("english", "")),
        }

    remark = node.get("remark")
    if isinstance(remark, dict):
        for field in (
            "original_form",
            "formal_statement_core",
            "global_context",
            "narrative_prefix",
            "proof_or_derivation_hint",
            "text_normalized",
        ):
            if isinstance(remark.get(field), str):
                remark[field] = _clean_text(remark[field])
        for field in ("local_conclusions", "excluded_from_formalization"):
            if isinstance(remark.get(field), list):
                remark[field] = [
                    _clean_text(item)
                    for item in remark[field]
                    if isinstance(item, str) and _clean_text(item)
                ]
        node["remark"] = remark

    content = node.get("content")
    if isinstance(content, dict):
        for field in (
            "original_form",
            "formal_statement_core",
            "global_context",
            "narrative_prefix",
            "proof_or_derivation_hint",
            "text_normalized",
        ):
            if content_is_authoritative_source and field == "original_form":
                continue
            if isinstance(content.get(field), str):
                content[field] = _clean_text(content[field])
        for field in ("local_conclusions", "excluded_from_formalization"):
            if isinstance(content.get(field), list):
                content[field] = [
                    _clean_text(item)
                    for item in content[field]
                    if isinstance(item, str) and _clean_text(item)
                ]
        node["content"] = content
    elif isinstance(content, str) and not content_is_authoritative_source:
        node["content"] = _clean_text(content)

    conditions = node.get("conditions")
    if isinstance(conditions, list):
        for condition in conditions:
            if isinstance(condition, dict) and isinstance(condition.get("text"), str):
                condition["text"] = _clean_text(condition.get("text"))

    conclusions = node.get("conclusions")
    if isinstance(conclusions, list):
        for conclusion in conclusions:
            if isinstance(conclusion, dict) and isinstance(conclusion.get("text"), str):
                conclusion["text"] = _clean_text(conclusion.get("text"))

    context = node.get("context")
    if isinstance(context, list):
        node["context"] = [_clean_text(x) for x in context if isinstance(x, str) and _clean_text(x)]

    proof = node.get("proof")
    if isinstance(proof, str):
        node["proof"] = _clean_text(proof)

    variables = node.get("variables")
    if isinstance(variables, list):
        for variable in variables:
            if isinstance(variable, dict):
                if isinstance(variable.get("name"), str):
                    variable["name"] = _clean_text(variable.get("name"))
                if isinstance(variable.get("type"), str):
                    variable["type"] = _clean_text(variable.get("type"))

    subject = node.get("subject")
    if isinstance(subject, list):
        node["subject"] = [_clean_text(x) for x in subject if isinstance(x, str) and _clean_text(x)]

    subnodes = node.get("sub_nodes")
    if isinstance(subnodes, list):
        cleaned_subnodes = []
        for subnode in subnodes:
            if not isinstance(subnode, dict):
                continue
            cleaned = copy.deepcopy(subnode)
            remark = cleaned.get("remark")
            if isinstance(remark, dict):
                for field in (
                    "original_form",
                    "formal_statement_core",
                    "global_context",
                    "applicable_context",
                    "parent_formal_statement_core",
                    "text_normalized",
                ):
                    if isinstance(remark.get(field), str):
                        remark[field] = _clean_text(remark[field])
                if isinstance(remark.get("local_conclusions"), list):
                    remark["local_conclusions"] = [
                        _clean_text(item)
                        for item in remark["local_conclusions"]
                        if isinstance(item, str) and _clean_text(item)
                    ]
                if isinstance(remark.get("applicable_conditions_text"), list):
                    remark["applicable_conditions_text"] = [
                        _clean_text(item)
                        for item in remark["applicable_conditions_text"]
                        if isinstance(item, str) and _clean_text(item)
                    ]
                cleaned["remark"] = remark
            if isinstance(cleaned.get("source_conclusion"), str):
                cleaned["source_conclusion"] = _clean_text(cleaned["source_conclusion"])
            for field in ("subject", "context"):
                if isinstance(cleaned.get(field), list):
                    cleaned[field] = [
                        _clean_text(item)
                        for item in cleaned[field]
                        if isinstance(item, str) and _clean_text(item)
                    ]
            for field in ("conditions", "applicable_conditions", "conclusions"):
                if isinstance(cleaned.get(field), list):
                    for item in cleaned[field]:
                        if isinstance(item, dict) and isinstance(item.get("text"), str):
                            item["text"] = _clean_text(item.get("text"))
            cleaned_subnodes.append(cleaned)
        node["sub_nodes"] = cleaned_subnodes

    return node


_SUPERSCRIPT_TRANSLATION = str.maketrans({
    "⁰": "0",
    "¹": "1",
    "²": "2",
    "³": "3",
    "⁴": "4",
    "⁵": "5",
    "⁶": "6",
    "⁷": "7",
    "⁸": "8",
    "⁹": "9",
    "ⁿ": "n",
})

_MATH_ATOM = r"(?:[A-Za-z_][A-Za-z0-9_]*\([^()]*\)|\\[A-Za-z]+(?:\s*\{[^{}]*\})*|\|[^|]+\||\([^()]+\)|\{[^{}]+\}|[A-Za-z0-9_]+)"
_PROTECTED_IDENTIFIERS = (
    "Ind",
    "Res",
    "Irr",
    "Ker",
    "Hom",
    "Aut",
    "End",
    "Ext",
    "Tor",
    "NormalIn",
    "Subgroup",
    "Subset",
    "Order",
    "Cardinality",
    "Sylow",
    "Gal",
    "Spec",
    "Proj",
    "GL",
    "SL",
    "PSL",
    "PGL",
)
_PROTECTED_IDENTIFIER_LOOKUP = {
    identifier.lower(): identifier for identifier in _PROTECTED_IDENTIFIERS
}


def _apply_iterative_substitutions(text, substitutions, max_rounds=5):
    normalized = text
    for _ in range(max_rounds):
        updated = normalized
        for pattern, replacement in substitutions:
            updated = re.sub(pattern, replacement, updated)
        if updated == normalized:
            break
        normalized = updated
    return normalized

def _apply_symbol_replacements(text, rules):
    normalized = text
    for pattern, replacement in rules:
        if isinstance(pattern, str) and pattern.startswith(r"\\"):
            boundary_pattern = rf"(?<![A-Za-z]){pattern}(?![A-Za-z])"
            normalized = re.sub(boundary_pattern, replacement, normalized)
        else:
            normalized = re.sub(pattern, replacement, normalized)
    return normalized


def _normalize_root_expressions(text):
    substitutions = [
        (
            r"\\sqrt\s*\[\s*([^{}\[\]]+?)\s*\]\s*\{\s*([^{}]+?)\s*\}",
            lambda m: f"Sqrt({m.group(1).strip()},{m.group(2).strip()})",
        ),
        (
            r"\\sqrt\s*\[\s*([^{}\[\]]+?)\s*\]\s*" + rf"({_MATH_ATOM})",
            lambda m: f"Sqrt({m.group(1).strip()},{m.group(2).strip()})",
        ),
        (
            r"([⁰¹²³⁴⁵⁶⁷⁸⁹ⁿ]+)\s*√\s*\{\s*([^{}]+?)\s*\}",
            lambda m: f"Sqrt({m.group(1).translate(_SUPERSCRIPT_TRANSLATION)},{m.group(2).strip()})",
        ),
        (
            rf"([⁰¹²³⁴⁵⁶⁷⁸⁹ⁿ]+)\s*√\s*({_MATH_ATOM})",
            lambda m: f"Sqrt({m.group(1).translate(_SUPERSCRIPT_TRANSLATION)},{m.group(2).strip()})",
        ),
        (
            r"\\sqrt\s*\{\s*([^{}]+?)\s*\}",
            lambda m: f"Sqrt(2,{m.group(1).strip()})",
        ),
        (
            rf"\\sqrt\s+({_MATH_ATOM})",
            lambda m: f"Sqrt(2,{m.group(1).strip()})",
        ),
        (
            r"√\s*\{\s*([^{}]+?)\s*\}",
            lambda m: f"Sqrt(2,{m.group(1).strip()})",
        ),
        (
            rf"√\s*({_MATH_ATOM})",
            lambda m: f"Sqrt(2,{m.group(1).strip()})",
        ),
    ]
    return _apply_iterative_substitutions(text, substitutions)


def _normalize_power_expressions(text):
    base = r"(?:[A-Za-z_][A-Za-z0-9_]*\([^()]*\)|\\[A-Za-z]+(?:\s*\{[^{}]*\})*|\|[^|]+\||\([^()]+\)|\{[^{}]+\}|[A-Za-z0-9_]+)"
    substitutions = [
        (
            rf"(?P<base>{base})\s*\^\s*\{{\s*(?P<exp>[^{{}}]+?)\s*\}}",
            lambda m: f"Power({m.group('base').strip()},{m.group('exp').strip()})",
        ),
        (
            rf"(?P<base>{base})\s*\^\s*(?P<exp>[+\-]?[A-Za-z0-9_\\]+)",
            lambda m: f"Power({m.group('base').strip()},{m.group('exp').strip()})",
        ),
    ]
    return _apply_iterative_substitutions(text, substitutions)


def _normalize_fraction_expressions(text):
    substitutions = [
        (
            r"\\(?:d|t)?frac\s*\{\s*([^{}]+?)\s*\}\s*\{\s*([^{}]+?)\s*\}",
            lambda m: f"Div({m.group(1).strip()},{m.group(2).strip()})",
        ),
        (
            rf"(?<![A-Za-z0-9_])(?P<num>{_MATH_ATOM})\s*/\s*(?P<den>{_MATH_ATOM})(?![A-Za-z0-9_])",
            lambda m: f"Div({m.group('num').strip()},{m.group('den').strip()})",
        ),
    ]
    return _apply_iterative_substitutions(text, substitutions)


def _normalize_integral_expressions(text):
    pattern = re.compile(
        r"(?P<op>\\(?:i{1,3}|o)?int|\u222b|\u222c|\u222d|\u222e)"
        r"(?:\s*\\limits)?"
        r"\s*(?:_\s*\{\s*(?P<lower>[^{}]+?)\s*\}|_\s*(?P<lower2>[^\s^]+))?"
        r"\s*(?:\^\s*\{\s*(?P<upper>[^{}]+?)\s*\}|\^\s*(?P<upper2>[^\s]+))?"
        r"\s*(?P<body>.+?)"
        r"\s*(?:\\,?\s*)?d\s*(?P<var>[A-Za-z]+)\b"
    )

    def _repl(match):
        lower = match.group("lower") or match.group("lower2") or ""
        upper = match.group("upper") or match.group("upper2") or ""
        body = (match.group("body") or "").strip()
        var = (match.group("var") or "").strip()
        if not (lower and upper and body and var):
            return match.group(0)
        return f"Integral({body},{var},{lower.strip()},{upper.strip()})"

    return _apply_iterative_substitutions(text, [(pattern, _repl)])


def _normalize_limit_expressions(text):
    pattern = re.compile(
        r"(?P<op>\\lim|\\operatorname\s*\{\s*l\s*i\s*m\s*\}|lim)"
        r"\s*(?:_\s*\{\s*(?P<variable>[A-Za-z][A-Za-z0-9_]*)\s*(?:\\to|\\rightarrow|\\Rightarrow|->|\N{RIGHTWARDS ARROW})\s*(?P<target>[^{}]+?)\s*\}"
        r"|_\s*(?P<variable2>[A-Za-z][A-Za-z0-9_]*)\s*(?:\\to|\\rightarrow|\\Rightarrow|->|\N{RIGHTWARDS ARROW})\s*(?P<target2>[A-Za-z0-9_\\\^\+\-\(\)\{\}]+))"
        r"\s*(?P<body>(?:[A-Za-z_][A-Za-z0-9_]*\([^()]*\)|\\[A-Za-z]+(?:\s*[A-Za-z0-9_\\]+)*|[A-Za-z0-9_\\]+(?:\s+[A-Za-z0-9_\\]+)*))"
    )

    def _repl(match):
        variable = (match.group("variable") or match.group("variable2") or "").strip()
        target = (match.group("target") or match.group("target2") or "").strip()
        body = (match.group("body") or "").strip()
        if not (variable and target and body):
            return match.group(0)
        return f"Limit({body},{variable},{target})"

    return _apply_iterative_substitutions(text, [(pattern, _repl)])

def _normalize_math_expressions(text):
    normalized = text
    normalized = _normalize_root_expressions(normalized)
    normalized = _normalize_fraction_expressions(normalized)
    normalized = _normalize_integral_expressions(normalized)
    normalized = _normalize_limit_expressions(normalized)
    normalized = _normalize_power_expressions(normalized)
    return normalized

def _apply_symbol_replacements(text, rules):
    normalized = text
    for pattern, replacement in rules:
        if isinstance(pattern, str) and pattern.startswith(r"\\"):
            boundary_pattern = rf"(?<![A-Za-z]){pattern}(?![A-Za-z])"
            normalized = re.sub(boundary_pattern, replacement, normalized)
        else:
            normalized = re.sub(pattern, replacement, normalized)
    return normalized



def _protect_article_a_after_be_verbs(text):
    pattern = re.compile(
        r"\b((?:there\s+)?(?:is|are|was|were|be|been|being|am))\s+(a)\b(?=\s*(?:[A-Za-z\\]|$))",
        flags=re.IGNORECASE,
    )
    protected = []

    def _repl(match):
        placeholder = f"@@ARTICLE_A_{len(protected)}@@"
        protected.append(match.group(2))
        return f"{match.group(1)} {placeholder}"

    return pattern.sub(_repl, text), protected


def _restore_protected_article_a(text, protected):
    restored = text
    for idx, article in enumerate(protected):
        restored = restored.replace(f"@@ARTICLE_A_{idx}@@", article)
    return restored


def _add_protected_span(protected, value):
    placeholder = f"@@NORMPROTECT{len(protected)}@@"
    protected.append((placeholder, value))
    return placeholder


def _operator_suffix_pattern():
    return r"(?:\s*[_^]\s*(?:\{\s*[^{}]+?\s*\}|[A-Za-z0-9_\\]+))*"


def _normalize_operatorname_name(name):
    compact = re.sub(r"\s+", "", name or "")
    return _PROTECTED_IDENTIFIER_LOOKUP.get(compact.lower())


def _protect_operatorname_spans(text, protected):
    suffix = _operator_suffix_pattern()
    pattern = re.compile(
        rf"\\operatorname\s*\{{\s*(?P<name>(?:[A-Za-z]\s*)+)\}}"
        rf"(?P<suffix>{suffix})"
        rf"(?P<call>\s*\([^()]*\))?"
    )

    def _repl(match):
        canonical_name = _normalize_operatorname_name(match.group("name"))
        if not canonical_name:
            return match.group(0)
        expression = (
            rf"\operatorname{{{canonical_name}}}"
            f"{match.group('suffix') or ''}"
            f"{match.group('call') or ''}"
        )
        return _add_protected_span(protected, expression)

    return pattern.sub(_repl, text)


def _protect_bare_operator_spans(text, protected):
    suffix = _operator_suffix_pattern()
    names = "|".join(
        re.escape(identifier)
        for identifier in sorted(_PROTECTED_IDENTIFIERS, key=len, reverse=True)
    )
    pattern = re.compile(
        rf"(?<![A-Za-z0-9_\\])"
        rf"(?P<expr>(?P<name>{names})(?P<suffix>{suffix})(?P<call>\s*\([^()]*\))?)"
        rf"(?![A-Za-z0-9_])"
    )

    def _repl(match):
        return _add_protected_span(protected, match.group("expr"))

    return pattern.sub(_repl, text)


def _protect_math_operator_spans(text, protected):
    protected_text = _protect_operatorname_spans(text, protected)
    return _protect_bare_operator_spans(protected_text, protected)


def _protect_latex_command_names(text, protected):
    pattern = re.compile(r"\\[A-Za-z]+")

    def _repl(match):
        return _add_protected_span(protected, match.group(0))

    return pattern.sub(_repl, text)


def _restore_identifiers(text, protected):
    restored = text
    for placeholder, token in protected:
        restored = restored.replace(placeholder, token)
    return restored

def normalize_text_with_variables(variables, text):
    if not isinstance(text, str):
        return text

    normalized = text
    protected_identifiers = []
    normalized = _protect_math_operator_spans(normalized, protected_identifiers)
    normalized = _normalize_math_expressions(normalized)
    relation_rules = [
        (r"≤", " <= "),
        (r"\\leq", " <= "),
        (r"\\le", " <= "),
        (r"≥", " >= "),
        (r"\\geq", " >= "),
        (r"\\ge", " >= "),
        (r"≠", " != "),
        (r"\\neq", " != "),
        (r"(?<![<>=!])=(?![=])", " = "),
    ]
    set_rules = [
        (r"∈", " in "),
        (r"\\in", " in "),
        (r"∉", " notin "),
        (r"\\notin", " notin "),
        (r"⊂", " subset "),
        (r"\\subset", " subset "),
        (r"⊆", " subseteq "),
        (r"\\subseteq", " subseteq "),
        (r"⊃", " superset "),
        (r"\\supset", " superset "),
        (r"⊇", " superseteq "),
        (r"\\supseteq", " superseteq "),
    ]
    number_set_rules = [
        (r"ℝ", " R "),
        (r"\\mathbb\{R\}", " R "),
        (r"ℤ", " Z "),
        (r"\\mathbb\{Z\}", " Z "),
        (r"ℕ", " N "),
        (r"\\mathbb\{N\}", " N "),
        (r"ℚ", " Q "),
        (r"\\mathbb\{Q\}", " Q "),
        (r"ℂ", " C "),
        (r"\\mathbb\{C\}", " C "),
    ]
    logic_rules = [
        (r"⇒", " -> "),
        (r"\\Rightarrow", " -> "),
        (r"⇔", " <-> "),
        (r"\\Leftrightarrow", " <-> "),
        (r"∧", " and "),
        (r"\\land", " and "),
        (r"∨", " or "),
        (r"\\lor", " or "),
        (r"¬", " not "),
        (r"\\neg", " not "),
        (r"∀", " forall "),
        (r"\\forall", " forall "),
        (r"∃", " exists "),
        (r"\\exists", " exists "),
    ]
    operator_rules = [
        (r"\\cdot", " * "),
        (r"·", " * "),
        (r"×", " * "),
        (r"÷", " / "),
        (r"\\div", " / "),
        (r"\+", " + "),
        (r"-(?!>)", " - "),
    ]
    mapping_rules = [
        (r"\\rightarrow", " -> "),
        (r"\\to", " -> "),
        (r"→", " -> "),
        (r"⇒", " -> "),
        (r"->", " -> "),
    ]
    decoration_rules = [
        (r"\\overline\{([^}]*)\}", r"overline(\1)"),
        (r"\\bar\{?([a-zA-Z0-9]+)\}?", r"overline(\1)"),
        (r"\\hat\{([^}]*)\}", r"hat(\1)"),
        (r"\\tilde\{([^}]*)\}", r"tilde(\1)"),
        (r"\\vec\{([^}]*)\}", r"vec(\1)"),
        (r"\\dot\{([^}]*)\}", r"dot(\1)"),
        (r"\\ddot\{([^}]*)\}", r"ddot(\1)"),
    ]
    all_rules = relation_rules + set_rules + number_set_rules + logic_rules + mapping_rules + operator_rules + decoration_rules

    normalized = _apply_symbol_replacements(normalized, all_rules)
    style_commands = [
        r"\\mathrm\{([^}]*)\}",
        r"\\mathbf\{([^}]*)\}",
        r"\\mathit\{([^}]*)\}",
        r"\\mathsf\{([^}]*)\}",
        r"\\mathtt\{([^}]*)\}",
        r"\\text\{([^}]*)\}",
    ]
    for pattern in style_commands:
        normalized = re.sub(pattern, r"\1", normalized)

    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = _protect_math_operator_spans(normalized, protected_identifiers)
    normalized = _protect_latex_command_names(normalized, protected_identifiers)
    normalized, protected_articles = _protect_article_a_after_be_verbs(normalized)

    var_map = {}
    if isinstance(variables, list):
        for variable in variables:
            if not isinstance(variable, dict):
                continue
            name = variable.get("name")
            if not (isinstance(name, str) and name.strip()):
                continue
            token = variable.get("normalize_type")
            if not (isinstance(token, str) and token.strip()):
                continue
            var_map[name] = token

    for name in sorted(var_map.keys(), key=len, reverse=True):
        token = var_map[name]
        escaped = re.escape(name)
        if re.match(r"^[A-Za-z0-9_]+$", name):
            pattern = re.compile(rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])")
            normalized = pattern.sub(lambda _match, value=token: value, normalized)
        else:
            normalized = normalized.replace(name, token)

    normalized = _restore_protected_article_a(normalized, protected_articles)
    normalized = _restore_identifiers(normalized, protected_identifiers)

    return normalized

def _apply_symbol_replacements(text, rules):
    normalized = text
    for pattern, replacement in rules:
        if isinstance(pattern, str) and pattern.startswith(r"\\"):
            boundary_pattern = rf"(?<![A-Za-z]){pattern}(?![A-Za-z])"
            normalized = re.sub(boundary_pattern, replacement, normalized)
        else:
            normalized = re.sub(pattern, replacement, normalized)
    return normalized


def normalize_node_fields(node):
    if not isinstance(node, dict):
        return node

    variables = node.get("variables", [])
    if isinstance(variables, list):
        type_counts = {}
        for variable in variables:
            if not isinstance(variable, dict):
                continue
            name = variable.get("name")
            vtype = variable.get("type")
            if not (isinstance(name, str) and name.strip()):
                continue
            if not (isinstance(vtype, str) and vtype.strip()):
                vtype_key = "var"
            else:
                vtype_key = vtype.strip().upper()
            type_counts[vtype_key] = type_counts.get(vtype_key, 0) + 1
            variable["normalize_type"] = f"{vtype_key}_{type_counts[vtype_key]}"

    remark = node.get("remark")
    original_form_normalized = ""
    formal_content = get_node_formal_content(node)
    if formal_content:
        original_form_normalized = normalize_text_with_variables(variables, formal_content)
    if isinstance(remark, dict) and formal_content:
        remark["text_normalized"] = original_form_normalized
    content_value = node.get("content")
    if isinstance(content_value, dict) and formal_content:
        content_value["text_normalized"] = original_form_normalized

    conditions = node.get("conditions")
    if isinstance(conditions, list):
        for condition in conditions:
            if isinstance(condition, dict) and isinstance(condition.get("text"), str):
                condition["text_normalized"] = normalize_text_with_variables(variables, condition.get("text"))

    conclusions = node.get("conclusions")
    if isinstance(conclusions, list):
        for conclusion in conclusions:
            if isinstance(conclusion, dict) and isinstance(conclusion.get("text"), str):
                conclusion["text_normalized"] = normalize_text_with_variables(variables, conclusion.get("text"))

    context = node.get("context")
    if isinstance(context, list):
        node["context"] = {
            "text": context,
            "text_normalized": [
                normalize_text_with_variables(variables, item) if isinstance(item, str) else item
                for item in context
            ],
        }

    subject = node.get("subject")
    if isinstance(subject, list):
        node["subject"] = {
            "text": subject,
            "text_normalized": [
                normalize_text_with_variables(variables, item) if isinstance(item, str) else item
                for item in subject
            ],
        }

    proof = node.get("proof")
    if isinstance(proof, str):
        node["proof"] = {
            "text": proof,
            "text_normalized": normalize_text_with_variables(variables, proof),
        }

    subnodes = node.get("sub_nodes")
    if isinstance(subnodes, list):
        for subnode in subnodes:
            if not isinstance(subnode, dict):
                continue
            sub_variables = subnode.get("variables", [])
            if isinstance(sub_variables, list):
                type_counts = {}
                for variable in sub_variables:
                    if not isinstance(variable, dict):
                        continue
                    name = variable.get("name")
                    vtype = variable.get("type")
                    if not (isinstance(name, str) and name.strip()):
                        continue
                    vtype_key = vtype.strip().upper() if isinstance(vtype, str) and vtype.strip() else "var"
                    type_counts[vtype_key] = type_counts.get(vtype_key, 0) + 1
                    variable["normalize_type"] = f"{vtype_key}_{type_counts[vtype_key]}"

            sub_formal = get_node_formal_content(subnode)
            sub_normalized = normalize_text_with_variables(sub_variables, sub_formal) if sub_formal else ""
            sub_remark = subnode.get("remark")
            if isinstance(sub_remark, dict) and sub_normalized:
                sub_remark["text_normalized"] = sub_normalized

            for field in ("conditions", "applicable_conditions", "conclusions"):
                values = subnode.get(field)
                if isinstance(values, list):
                    for item in values:
                        if isinstance(item, dict) and isinstance(item.get("text"), str):
                            item["text_normalized"] = normalize_text_with_variables(sub_variables, item.get("text"))

            for field in ("context", "subject"):
                value = subnode.get(field)
                if isinstance(value, list):
                    subnode[field] = {
                        "text": value,
                        "text_normalized": [
                            normalize_text_with_variables(sub_variables, item) if isinstance(item, str) else item
                            for item in value
                        ],
                    }

    global_id = compute_global_id_from_source(node)

    node_without_id = {key: value for key, value in node.items() if key != "global_id"}
    return {"global_id": global_id, **node_without_id}
