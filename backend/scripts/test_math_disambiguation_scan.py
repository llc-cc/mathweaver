import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.common.io import read_json, write_json
from pipeline.stages.math_disambiguation.ambiguity_table import get_ambiguity_table
from pipeline.stages.math_disambiguation.stage import (
    build_disambiguation_tasks,
    scan_node_dict_for_ambiguity,
)


def build_output_payload(definition_axiom_dict, structured_input_dict, ambiguity_table=None):
    definition_ambiguous_node_dict = scan_node_dict_for_ambiguity(
        definition_axiom_dict,
        ambiguity_table=ambiguity_table,
        container_name="definition_axiom_dict",
        is_wrapped=False,
    )
    structured_ambiguous_node_dict = scan_node_dict_for_ambiguity(
        structured_input_dict,
        ambiguity_table=ambiguity_table,
        container_name="structured_input_dict",
        is_wrapped=True,
    )

    definition_llm_input_dict = build_disambiguation_tasks(definition_ambiguous_node_dict)
    structured_llm_input_dict = build_disambiguation_tasks(structured_ambiguous_node_dict)

    combined_llm_input_dict = {}
    for source_key, task in definition_llm_input_dict.items():
        combined_llm_input_dict[f"definition_axiom_dict:{source_key}"] = task
    for source_key, task in structured_llm_input_dict.items():
        combined_llm_input_dict[f"structured_input_dict:{source_key}"] = task

    return {
        "summary": {
            "definition_hit_count": len(definition_ambiguous_node_dict),
            "structured_hit_count": len(structured_ambiguous_node_dict),
            "total_hit_count": len(definition_ambiguous_node_dict) + len(structured_ambiguous_node_dict),
        },
        "definition_ambiguous_node_dict": definition_ambiguous_node_dict,
        "structured_ambiguous_node_dict": structured_ambiguous_node_dict,
        "definition_llm_input_dict": definition_llm_input_dict,
        "structured_llm_input_dict": structured_llm_input_dict,
        "combined_llm_input_dict": combined_llm_input_dict,
    }


def resolve_input_paths(args):
    if args.stage_cache_dir:
        stage_cache_dir = Path(args.stage_cache_dir).resolve()
        definition_path = stage_cache_dir / "definition_axiom_dict.json"
        structured_path = stage_cache_dir / "structured_input_dict.json"
        output_path = Path(args.output) if args.output else stage_cache_dir / "math_disambiguation_scan_preview.json"
    else:
        if not args.definition_json or not args.structured_json:
            raise ValueError("definition_json and structured_json are required when stage_cache_dir is not provided")
        definition_path = Path(args.definition_json).resolve()
        structured_path = Path(args.structured_json).resolve()
        if args.output:
            output_path = Path(args.output)
        else:
            output_path = structured_path.parent / "math_disambiguation_scan_preview.json"

    return definition_path, structured_path, output_path.resolve()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scan math ambiguity nodes and output the entries that should be sent to the disambiguation LLM.",
    )
    parser.add_argument(
        "--stage-cache-dir",
        help="Directory that contains definition_axiom_dict.json and structured_input_dict.json.",
    )
    parser.add_argument("--definition-json", help="Path to definition_axiom_dict.json.")
    parser.add_argument("--structured-json", help="Path to structured_input_dict.json.")
    parser.add_argument(
        "--output",
        help="Output JSON path. Defaults to math_disambiguation_scan_preview.json next to the inputs.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    definition_path, structured_path, output_path = resolve_input_paths(args)

    if not definition_path.exists():
        raise FileNotFoundError(f"Definition dict not found: {definition_path}")
    if not structured_path.exists():
        raise FileNotFoundError(f"Structured input dict not found: {structured_path}")

    ambiguity_table = get_ambiguity_table()
    definition_axiom_dict = read_json(str(definition_path))
    structured_input_dict = read_json(str(structured_path))

    payload = build_output_payload(
        definition_axiom_dict,
        structured_input_dict,
        ambiguity_table=ambiguity_table,
    )
    write_json(str(output_path), payload)

    summary = payload["summary"]
    print(f"definition hits: {summary['definition_hit_count']}")
    print(f"structured hits: {summary['structured_hit_count']}")
    print(f"total hits: {summary['total_hit_count']}")
    print(f"saved to: {output_path}")


if __name__ == "__main__":
    main()
