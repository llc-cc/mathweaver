import argparse
import json
from pathlib import Path


PRESET_NON_EXAMPLE_FORMALIZATION = "non-example-formalization"

NON_EXAMPLE_DEFINITION_FIELDS = [
    "global_id",
    "node_type",
    "content",
    "label",
    "title",
    "logic_form_rendered",
]

NON_EXAMPLE_OTHER_FIELDS = [
    "global_id",
    "node_type",
    "label",
    "title",
    "logic_form_rendered",
    "statement_form",
    "subject",
    "context",
    "variables",
    "conditions",
    "conclusions",
    "proof",
    "remark",
]


def parse_csv_option(value):
    if not value:
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = [value]
    items = []
    for raw_item in raw_items:
        for item in str(raw_item).replace(";", ",").split(","):
            item = item.strip()
            if item:
                items.append(item)
    return items


def load_node_dict(path):
    with Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        return {str(key): value for key, value in data.items()}
    if isinstance(data, list):
        return {str(index): value for index, value in enumerate(data)}
    raise ValueError("Input JSON must be a node dictionary or a node list.")


def get_node_type(node):
    if not isinstance(node, dict):
        return ""
    value = node.get("node_type")
    return value if isinstance(value, str) else ""


def iter_paths(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            key = str(key)
            path = f"{prefix}.{key}" if prefix else key
            yield path
            yield from iter_paths(child, path)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield from iter_paths(item, prefix)


def scan_available_keys(node_dict):
    node_types = set()
    top_level_keys = set()
    nested_paths = set()

    for node in node_dict.values():
        if not isinstance(node, dict):
            continue
        node_type = get_node_type(node)
        if node_type:
            node_types.add(node_type)
        top_level_keys.update(str(key) for key in node.keys())
        nested_paths.update(iter_paths(node))

    return {
        "node_types": sorted(node_types),
        "top_level_keys": sorted(top_level_keys),
        "paths": sorted(nested_paths),
    }


def path_get(value, parts):
    if not parts:
        return True, value

    head = parts[0]
    tail = parts[1:]

    if isinstance(value, dict):
        if head not in value:
            return False, None
        return path_get(value[head], tail)

    if isinstance(value, list):
        found_items = []
        found_any = False
        for item in value:
            found, child = path_get(item, parts)
            if found:
                found_any = True
                found_items.append(child)
        return found_any, found_items

    return False, None


def select_node_fields(node, keys, paths, include_missing=False):
    if not keys and not paths:
        return node

    selected = {}
    for key in keys:
        if isinstance(node, dict) and key in node:
            selected[key] = node[key]
        elif include_missing:
            selected[key] = None

    for path in paths:
        parts = [part for part in path.split(".") if part]
        if not parts:
            continue
        found, value = path_get(node, parts)
        if found:
            selected[path] = value
        elif include_missing:
            selected[path] = None

    return selected


def filter_and_select(node_dict, node_types, keys, paths, include_missing=False):
    wanted_types = set(node_types)
    result = {}
    for node_key, node in node_dict.items():
        if wanted_types and get_node_type(node) not in wanted_types:
            continue
        result[str(node_key)] = select_node_fields(
            node,
            keys=keys,
            paths=paths,
            include_missing=include_missing,
        )
    return result


def pick_fields(node, fields):
    return {field: node.get(field) for field in fields}


def extract_non_example_formalization_view(node_dict):
    result = {}
    for node_key, node in node_dict.items():
        if not isinstance(node, dict):
            continue
        node_type = get_node_type(node)
        if node_type == "例子":
            continue
        fields = NON_EXAMPLE_DEFINITION_FIELDS if node_type == "定义" else NON_EXAMPLE_OTHER_FIELDS
        result[str(node_key)] = pick_fields(node, fields)
    return result


def default_preset_output_path(input_path, preset):
    path = Path(input_path)
    if preset == PRESET_NON_EXAMPLE_FORMALIZATION:
        return str(path.with_name(f"{path.stem}_selected_non_examples.json"))
    return None


def write_json(data, output_path=None, pretty=False):
    indent = 4 if pretty else None
    text = json.dumps(data, ensure_ascii=False, indent=indent)
    if output_path:
        path = Path(output_path)
        if path.parent:
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Extract selected node types and keys from a pipeline node JSON file."
    )
    parser.add_argument("--input", required=True, help="Path to node_dict.json or TEST_NODE_OUT.json.")
    parser.add_argument("--output", help="Optional output JSON path. Prints to stdout when omitted.")
    parser.add_argument(
        "--preset",
        choices=[PRESET_NON_EXAMPLE_FORMALIZATION],
        help=(
            "Reusable extraction profile. "
            f"{PRESET_NON_EXAMPLE_FORMALIZATION}: exclude 例子 nodes and keep the formalization/evaluation field set."
        ),
    )
    parser.add_argument(
        "--node-types",
        action="append",
        help="Comma-separated node types to keep, for example: 定理,定义,引理.",
    )
    parser.add_argument(
        "--keys",
        action="append",
        help="Comma-separated top-level keys to extract, for example: node_type,title,remark.",
    )
    parser.add_argument(
        "--paths",
        action="append",
        help="Comma-separated nested paths to extract, for example: title.chinese,remark.original_form.",
    )
    parser.add_argument(
        "--list-keys",
        action="store_true",
        help="Output available node types, top-level keys, and nested paths as a JSON dictionary.",
    )
    parser.add_argument(
        "--include-missing",
        action="store_true",
        help="Include selected missing keys or paths with null values.",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    node_dict = load_node_dict(args.input)

    if args.list_keys:
        write_json(scan_available_keys(node_dict), args.output, args.pretty)
        return 0

    if args.preset == PRESET_NON_EXAMPLE_FORMALIZATION:
        result = extract_non_example_formalization_view(node_dict)
        output_path = args.output or default_preset_output_path(args.input, args.preset)
    else:
        result = filter_and_select(
            node_dict,
            node_types=parse_csv_option(args.node_types),
            keys=parse_csv_option(args.keys),
            paths=parse_csv_option(args.paths),
            include_missing=args.include_missing,
        )
        output_path = args.output

    write_json(result, output_path, args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
