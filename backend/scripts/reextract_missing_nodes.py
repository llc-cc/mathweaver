"""Re-extract curated missing and split knowledge units from the source Markdown.

The source fragments are selected from the audited Convex Optimization excerpt.
They are passed directly to the existing ``extract_statements`` input contract,
then the fixed pipeline runs through ``repair_lite`` and stops before relation
construction. Existing graph artifacts are never read as active pipeline state
or overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from pipeline.common.io import atomic_write_json, write_json  # noqa: E402
from pipeline.orchestrator import execute_fixed_pipeline  # noqa: E402
from pipeline.context import PipelineContext  # noqa: E402
from pipeline.stages.extract_statements.stage import recognize_source_label  # noqa: E402


DEFAULT_SOURCE = BACKEND_ROOT / "books" / "最优化" / "bv_cvxbook_1.1-2.3.md"
DEFAULT_OUTPUT = (
    BACKEND_ROOT
    / "books"
    / "最优化"
    / "bv_cvxbook_1.1-2.3_test1_result"
    / "missing_node.json"
)
EXPECTED_SOURCE_SHA256 = "4f0ee13b9410e5751431a10e1607880caaefcd59150d6fa7b0b89d1a27dcd908"


class _ParserProxy:
    """Keep the canonical parser, tolerating the existing empty raw-string form."""

    _EMPTY_RAW_STRING = re.compile(r"r(\"\"\"\"|\'\'\'\')(?=\s*[,}])")

    def __init__(self, parser):
        self._parser = parser

    def parse_dict(self, value):
        try:
            return self._parser.parse_dict(value)
        except RuntimeError:
            normalized = self._EMPTY_RAW_STRING.sub('""', str(value))
            if normalized == value:
                raise
            return self._parser.parse_dict(normalized)

    def __getattr__(self, name):
        return getattr(self._parser, name)


@dataclass(frozen=True)
class FragmentSpec:
    key: str
    start_line: int
    end_line: int
    reason: str


# Line ranges are deliberately source-relative and are checked against the
# audited source hash before extraction, so the model receives only verbatim
# source text and not a rewritten prompt-side reconstruction.
FRAGMENT_SPECS = (
    FragmentSpec("optimization_problem_form", 13, 22, "split node 0: problem form and symbols"),
    FragmentSpec("optimal_solution_definition", 23, 26, "split node 0: optimal solution definition"),
    FragmentSpec("chebyshev_approximation_problem", 275, 286, "split node 8: Chebyshev problem and objective meaning"),
    FragmentSpec("chebyshev_linear_program_reformulation", 288, 300, "split node 8: linear-program reformulation"),
    FragmentSpec("real_vector_matrix_notation", 611, 616, "split node 10: scalar, vector, and matrix notation"),
    FragmentSpec("vector_and_index_notation", 618, 625, "split node 10: vector display and index notation"),
    FragmentSpec("symmetric_matrix_cone_notation", 627, 634, "split node 10: symmetric matrix cones and generalized inequality"),
    FragmentSpec("function_domain_notation", 636, 647, "split node 10: function type and domain notation"),
    FragmentSpec("line_and_line_segment_parameterization", 675, 684, "split node 11: line and line-segment parameterization"),
    FragmentSpec("local_optimization_definition", 397, 402, "missing/self-contained local optimization definition"),
    FragmentSpec("global_optimization_definition_and_complexity", 437, 442, "split node 72: global solution and efficiency tradeoff"),
    FragmentSpec("global_optimization_certification", 444, 462, "missing global optimization use and certification behavior"),
    FragmentSpec("convex_initialization_for_local_optimization", 472, 476, "missing convex initialization use"),
    FragmentSpec("convex_sparse_vector_heuristic", 480, 484, "missing convex heuristic for sparse nonconvex problems"),
    FragmentSpec("convex_relaxation_lower_bounds", 496, 500, "missing convex and Lagrangian relaxation bounds"),
    FragmentSpec("hyperplane_definition", 931, 937, "split node 40: hyperplane definition"),
    FragmentSpec("hyperplane_geometric_representation", 938, 963, "split node 40: normal, offset, and orthogonal-complement view"),
    FragmentSpec("halfspace_definition", 965, 973, "split node 41: halfspace definition and convexity"),
    FragmentSpec("halfspace_geometric_properties", 976, 989, "split node 41: shifted form, boundary, and interior"),
    FragmentSpec("polyhedron_definition_and_properties", 1066, 1078, "split node 48: polyhedron definition and properties"),
    FragmentSpec("compact_polyhedron_notation", 1080, 1093, "split node 48: compact matrix notation"),
    FragmentSpec("affine_image_convexity", 1326, 1336, "split node 74: affine image of a convex set"),
    FragmentSpec("affine_inverse_image_convexity", 1337, 1344, "split node 74: affine inverse image of a convex set"),
    FragmentSpec("convex_set_scaling_and_translation", 1346, 1351, "split node 75: scaling and translation"),
    FragmentSpec("convex_set_projection", 1353, 1359, "split node 76: projection of a convex set"),
    FragmentSpec("set_sum_definition", 1361, 1365, "split node 77: set-sum definition"),
    FragmentSpec("cartesian_product_and_minkowski_sum_convexity", 1367, 1375, "split node 78: Cartesian product and sum-image argument"),
    FragmentSpec("partial_sum_definition", 1377, 1382, "split node 79: partial-sum definition"),
    FragmentSpec("partial_sum_special_cases_and_convexity", 1384, 1386, "split node 79: partial-sum special cases and convexity"),
    FragmentSpec("perspective_function_definition", 1440, 1444, "split node 67: perspective definition and domain"),
    FragmentSpec("perspective_camera_interpretation", 1446, 1453, "split node 67: pin-hole camera interpretation"),
    FragmentSpec("perspective_image_convexity", 1455, 1485, "split node 67: convexity of perspective images"),
    FragmentSpec("perspective_inverse_image_convexity", 1487, 1516, "split node 67: convexity of perspective inverse images"),
    FragmentSpec("linear_fractional_definition", 1521, 1538, "split node 68: linear-fractional definition and domain"),
    FragmentSpec("projective_matrix_representation", 1540, 1549, "split node 68: projective matrix representation"),
    FragmentSpec("projective_ray_correspondence", 1551, 1571, "split node 68: ray correspondence and evaluation"),
    FragmentSpec("linear_fractional_convexity", 1573, 1578, "split node 68: convexity preservation"),
    FragmentSpec("conditional_probability_linear_fractional_example", 1580, 1592, "split node 69: conditional-probability example and convexity"),
)



def _load_fragments(source_path: Path) -> tuple[list[dict], str]:
    source_bytes = source_path.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    if source_hash != EXPECTED_SOURCE_SHA256:
        raise RuntimeError(
            "The audited source has changed; refusing to use stale line ranges. "
            f"expected sha256={EXPECTED_SOURCE_SHA256}, got={source_hash}"
        )

    lines = source_bytes.decode("utf-8").splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line.encode("utf-8")))

    fragments = []
    for index, spec in enumerate(FRAGMENT_SPECS):
        if spec.start_line < 1 or spec.end_line > len(lines) or spec.start_line > spec.end_line:
            raise RuntimeError(f"Invalid source range for {spec.key}: {spec.start_line}-{spec.end_line}")
        text = "".join(lines[spec.start_line - 1 : spec.end_line])
        if not text.strip():
            raise RuntimeError(f"Empty source fragment: {spec.key}")
        if any(line.lstrip().startswith("#") for line in text.splitlines() if line.strip()):
            raise RuntimeError(f"Fragment unexpectedly contains a Markdown heading: {spec.key}")
        label_facts = recognize_source_label(text)
        fragments.append(
            {
                "fragment_index": index,
                "key": spec.key,
                "reason": spec.reason,
                "start_line": spec.start_line,
                "end_line": spec.end_line,
                "source_span": {
                    "start": offsets[spec.start_line - 1],
                    "end": offsets[spec.end_line],
                },
                "source_text": text,
                "label_facts": label_facts,
            }
        )
    return fragments, source_hash


def _build_initial_state(fragments: list[dict], source_path: Path) -> dict:
    problem_dict = {}
    block_reports = []
    mapping = {}
    for index, fragment in enumerate(fragments):
        key = str(index)
        text = fragment["source_text"]
        facts = fragment["label_facts"]
        span = fragment["source_span"]
        problem_dict[index] = {
            "pos1": text,
            "source_block_key": fragment["key"],
            "source_span": span,
            "source_file": str(source_path),
        }
        mapping[key] = text
        block_reports.append(
            {
                "block_id": index,
                "raw_block_id": index,
                "start_unit_id": index,
                "end_unit_id": index,
                "boundary_role": "top_level_logical_unit_start",
                "label_surface": facts.get("label", ""),
                "label_family": facts.get("family", ""),
                "logical_unit_type_hint": facts.get("node_type", ""),
                "decision_source": "curated_source_fragment",
                "evidence": ["verbatim_source_range", fragment["reason"]],
                "warnings": [],
                "block_quality_flags": [],
                "unit_ids": [index],
                "source_span": span,
            }
        )

    corrected_text = "\n\n".join(fragment["source_text"].strip() for fragment in fragments)
    segment_report = {
        "schema_version": 2,
        "source_format": "markdown",
        "source_unit_count": len(fragments),
        "problem_block_count": len(fragments),
        "environment_block_count": 0,
        "residual_block_count": 0,
        "all_units_consumed_once": True,
        "blocks": block_reports,
        "unit_assignments": {
            str(index): {
                "block_id": index,
                "role": "top_level_logical_unit_start",
                "source_batch_key": "curated_missing_fragments",
            }
            for index in range(len(fragments))
        },
        "classification_errors": [],
        "llm_attempt_count": 0,
        "curated_fragment_count": len(fragments),
    }
    return {
        "corrected_text": corrected_text,
        "problem_dict": problem_dict,
        "mapping_dict": {"curated_missing_fragments": mapping},
        "segment_blocks_report": segment_report,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Re-extract audited split/missing source fragments through repair_lite "
            "without running build_relations."
        )
    )
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--run-dir",
        help="Optional isolated working directory; defaults to a new directory beside the output.",
    )
    parser.add_argument("--num-threads", type=int, default=16)
    parser.add_argument("--checkpoint", type=int, default=500)
    parser.add_argument("--llm-engine", choices=("api", "claude_cli"), default="api")
    parser.add_argument(
        "--resume-from-stage",
        choices=("extract_statements", "analysis"),
        default="extract_statements",
        help="Resume an isolated run from analysis after earlier stages already completed.",
    )
    parser.add_argument("--api-url")
    parser.add_argument("--model-name")
    parser.add_argument("--api-key")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and list the exact source fragments without calling the pipeline.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = _build_parser().parse_args(argv)
    source_path = Path(args.source).resolve()
    output_path = Path(args.output).resolve()
    if not source_path.is_file():
        raise RuntimeError(f"Source Markdown is missing: {source_path}")
    if args.num_threads < 1 or args.checkpoint < 1:
        raise RuntimeError("--num-threads and --checkpoint must be positive")

    fragments, source_hash = _load_fragments(source_path)
    print(f"source: {source_path}")
    print(f"source_sha256: {source_hash}")
    print(f"fragment_count: {len(fragments)}")
    for fragment in fragments:
        print(
            f"  [{fragment['fragment_index']:02d}] {fragment['key']} "
            f"lines={fragment['start_line']}-{fragment['end_line']} "
            f"chars={len(fragment['source_text'])}"
        )

    if args.dry_run:
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if args.run_dir:
        run_dir = Path(args.run_dir).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
    else:
        run_dir = Path(tempfile.mkdtemp(prefix="missing_node_pipeline_"))
    work_node_path = run_dir / "stage_nodes.json"
    work_edge_path = run_dir / "stage_edges.json"
    write_json(
        str(run_dir / "missing_node_input.json"),
        {
            "source": str(source_path),
            "source_sha256": source_hash,
            "fragments": fragments,
        },
    )

    if args.resume_from_stage != "extract_statements" and not args.run_dir:
        raise RuntimeError("--run-dir is required when --resume-from-stage is not extract_statements")
    if args.resume_from_stage == "analysis":
        node_dict_path = run_dir / "_stage_cache" / "node_dict.json"
        if not node_dict_path.is_file():
            raise RuntimeError(f"Cannot resume analysis; node_dict.json is missing: {node_dict_path}")
        node_dict = json.loads(node_dict_path.read_text(encoding="utf-8"))
        if not isinstance(node_dict, dict) or not node_dict:
            raise RuntimeError(f"Cannot resume analysis; node_dict.json is empty: {node_dict_path}")
        initial_state = {
            "node_dict": node_dict,
            "node_list": list(node_dict.values()),
        }
    else:
        initial_state = _build_initial_state(fragments, source_path)

    context = PipelineContext(
        file_path=str(source_path),
        output_node_path=str(work_node_path),
        output_edge_path=str(work_edge_path),
        api_url=args.api_url,
        model_name=args.model_name,
        api_key=args.api_key,
        num_threads=args.num_threads,
        checkpoint=args.checkpoint,
        enable_analysis=True,
        enable_math_disambiguation=True,
        source_format="markdown",
        source_origin="markdown",
        llm_engine=args.llm_engine,
        cache_policy="legacy",
    )
    context.parser = _ParserProxy(context.parser)
    context.resume_task_checkpoints = args.resume_from_stage != "extract_statements"
    state = execute_fixed_pipeline(
        context,
        state=initial_state,
        start_stage=args.resume_from_stage,
        stop_stage="repair_lite",
        edge_output_mode="structured",
        relation_prompt_profile="graph",
    )
    nodes = state.get("node_list")
    if not isinstance(nodes, list) or not nodes:
        raise RuntimeError("The pre-relation pipeline produced no node_list")

    atomic_write_json(str(output_path), nodes)
    write_json(
        str(run_dir / "missing_node_manifest.json"),
        {
            "source": str(source_path),
            "source_sha256": source_hash,
            "output": str(output_path),
            "run_dir": str(run_dir),
            "started_from_stage": args.resume_from_stage,
            "stopped_after": "repair_lite",
            "build_relations_executed": False,
            "fragment_count": len(fragments),
            "node_count": len(nodes),
            "fragments": [
                {
                    key: value
                    for key, value in fragment.items()
                    if key != "source_text"
                }
                for fragment in fragments
            ],
        },
    )
    print(f"missing_node.json: {output_path}")
    print(f"node_count: {len(nodes)}")
    print(f"isolated_run_dir: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
