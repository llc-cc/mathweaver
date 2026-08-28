"""Resume the fixed extractor pipeline from an unresolved repair stage run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.common.stage_recovery import recover_failed_stage_tasks
from pipeline.context import PipelineContext
from pipeline.orchestrator import execute_fixed_pipeline
from pipeline.stages.repair import stage as repair_stage


DEFAULT_INPUT = PROJECT_ROOT / "books" / "elegantbook-en-section-1.1.md"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "test_output" / "elegantbook-en-section-1.1_md_test1"


def _absolute_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _read_json(path: Path, expected_type: type):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read valid JSON cache: {path}: {exc}") from exc
    if not isinstance(value, expected_type):
        raise RuntimeError(f"Expected {expected_type.__name__} JSON in cache: {path}")
    return value


def _latest_repair_report(cache_dir: Path) -> tuple[Path, dict]:
    report_paths = list((cache_dir / "agent_state" / "stage_runs" / "repair").glob("*/failure_report.json"))
    if not report_paths:
        raise RuntimeError(f"No repair failure report exists under: {cache_dir}")
    report_path = max(report_paths, key=lambda path: path.stat().st_mtime)
    report = _read_json(report_path, dict)
    if report.get("stage") != "repair" or report.get("status") != "unresolved":
        raise RuntimeError(f"Latest repair report is not unresolved: {report_path}")
    return report_path, report


def load_repair_resume_state(cache_dir: Path) -> tuple[dict, dict]:
    report_path, report = _latest_repair_report(cache_dir)
    failed_keys = [str(key) for key in report.get("failed_task_keys") or []]
    if not failed_keys:
        raise RuntimeError(f"Repair report has no unresolved task keys: {report_path}")

    input_path = Path(str(report.get("input_dict_path") or ""))
    partial_path = Path(str(report.get("partial_result_dict_path") or ""))
    if not input_path.is_absolute():
        input_path = report_path.parent / input_path
    if not partial_path.is_absolute():
        partial_path = report_path.parent / partial_path
    saved_input = _read_json(input_path, dict)
    partial_results = _read_json(partial_path, dict)
    expected_keys = [str(key) for key in report.get("expected_task_keys") or []]
    succeeded_keys = [str(key) for key in report.get("succeeded_task_keys") or []]
    if set(partial_results) != set(succeeded_keys):
        raise RuntimeError(
            "Repair partial result keys do not match the failure report's succeeded task keys."
        )
    invalid_partial_keys = [
        str(key)
        for key, value in partial_results.items()
        if not repair_stage.validation13(value)
    ]
    if invalid_partial_keys:
        raise RuntimeError(
            "Repair partial results contain structurally invalid entries: "
            + ", ".join(invalid_partial_keys)
        )
    if set(expected_keys) != set(succeeded_keys) | set(failed_keys):
        raise RuntimeError("Repair report task partitions do not cover the expected task keys exactly.")
    if set(succeeded_keys) & set(failed_keys):
        raise RuntimeError("Repair report marks the same task as both succeeded and failed.")

    canonical_results_path = cache_dir / "repair_result_dict.json"
    if canonical_results_path.is_file() and _read_json(canonical_results_path, dict):
        raise RuntimeError(
            "repair_result_dict.json is not empty; refusing to replay repair patches over modified nodes."
        )

    node_path = cache_dir / "node_dict_after_repair.json"
    node_dict = _read_json(node_path, dict)
    if not node_dict:
        raise RuntimeError(f"Repair resume node cache is empty: {node_path}")

    rebuilt_input = repair_stage.build_repair_input_dict(node_dict)
    if rebuilt_input != saved_input:
        raise RuntimeError(
            "node_dict_after_repair.json no longer reconstructs the interrupted repair input exactly; "
            "refusing an unsafe resume."
        )

    if expected_keys != [str(key) for key in saved_input]:
        raise RuntimeError("Repair failure report task keys do not match its saved input dictionary.")

    state = {
        "node_dict": node_dict,
        "node_list": list(node_dict.values()),
        "repair_stage_run": report,
    }
    facts = {
        "report_path": report_path,
        "node_count": len(node_dict),
        "task_count": len(saved_input),
        "succeeded_task_count": len(succeeded_keys),
        "failed_task_count": len(failed_keys),
        "failed_keys": failed_keys,
        "attempt_rounds": int(report.get("attempt_rounds") or 0),
    }
    return state, facts


def resume_repair_and_downstream(
    context,
    state,
    *,
    max_recovery_rounds=2,
    edge_output_mode="structured",
    relation_prompt_profile="graph",
    on_stage_start=None,
    on_stage_complete=None,
):
    state = recover_failed_stage_tasks(
        context,
        state,
        repair_stage,
        max_rounds=max_recovery_rounds,
    )
    report = state.get("repair_stage_run")
    if not isinstance(report, dict) or report.get("status") not in {"resolved", "degraded"}:
        raise RuntimeError("Repair recovery did not produce a resolved or degraded stage report.")

    return execute_fixed_pipeline(
        context,
        state,
        start_stage="extract_references",
        edge_output_mode=edge_output_mode,
        relation_prompt_profile=relation_prompt_profile,
        on_stage_start=on_stage_start,
        on_stage_complete=on_stage_complete,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Resume the fixed extractor pipeline from the latest unresolved repair run. "
            "Only unresolved repair tasks are retried; earlier stages are loaded from cache."
        )
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--output-node")
    parser.add_argument("--output-edge")
    parser.add_argument("--output-natural-node")
    parser.add_argument("--api-url")
    parser.add_argument("--model-name")
    parser.add_argument("--api-key")
    parser.add_argument("--num-threads", type=int, default=16)
    parser.add_argument("--checkpoint", type=int, default=500)
    parser.add_argument("--max-recovery-rounds", type=int, default=2)
    parser.add_argument(
        "--expect-failed-keys",
        nargs="+",
        help="Abort unless the latest unresolved repair keys exactly match this list.",
    )
    parser.add_argument("--edge-output-mode", choices=("structured", "natural", "both"), default="structured")
    parser.add_argument("--relation-prompt-profile", choices=("graph", "formalization"), default="graph")
    parser.add_argument("--source-format", choices=("auto", "markdown", "tex"), default="auto")
    parser.add_argument("--embedding-model-name")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the interrupted cache without calling the LLM or changing stage outputs.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    input_path = _absolute_path(args.input)
    output_dir = _absolute_path(args.output_dir)
    cache_dir = output_dir / "_stage_cache"
    output_node = _absolute_path(args.output_node or output_dir / "TEST_NODE_OUT.json")
    output_edge = _absolute_path(args.output_edge or output_dir / "TEST_EDGE_OUT.json")
    output_natural_node = _absolute_path(args.output_natural_node) if args.output_natural_node else None

    if not input_path.is_file():
        raise RuntimeError(f"Input document is missing: {input_path}")
    if not cache_dir.is_dir():
        raise RuntimeError(f"Stage cache directory is missing: {cache_dir}")

    state, facts = load_repair_resume_state(cache_dir)
    if args.expect_failed_keys is not None:
        expected_failed = [str(key) for key in args.expect_failed_keys]
        if facts["failed_keys"] != expected_failed:
            raise RuntimeError(
                "Latest unresolved repair keys do not match --expect-failed-keys: "
                f"actual={facts['failed_keys']}, expected={expected_failed}"
            )
    print("Repair resume preflight passed")
    print(f"  input: {input_path}")
    print(f"  cache: {cache_dir}")
    print(f"  failure report: {facts['report_path']}")
    print(f"  nodes: {facts['node_count']}")
    print(
        f"  repair tasks: {facts['task_count']} "
        f"({facts['succeeded_task_count']} cached, {facts['failed_task_count']} unresolved)"
    )
    print(f"  unresolved keys: {', '.join(facts['failed_keys'])}")
    print(f"  previous attempt rounds: {facts['attempt_rounds']}")
    print("  continuation: repair recovery -> extract_references -> finalize_output")
    if args.dry_run:
        print("Dry run complete; no LLM request or stage write was performed.")
        return 0

    context_kwargs = {
        "file_path": str(input_path),
        "output_node_path": str(output_node),
        "output_edge_path": str(output_edge),
        "output_natural_node_path": str(output_natural_node) if output_natural_node else None,
        "api_url": args.api_url,
        "model_name": args.model_name,
        "api_key": args.api_key,
        "num_threads": args.num_threads,
        "checkpoint": args.checkpoint,
        "enable_analysis": True,
        "source_format": args.source_format,
    }
    if args.embedding_model_name:
        context_kwargs["embedding_model_name"] = args.embedding_model_name
    context = PipelineContext(**context_kwargs)
    if Path(context.output_dir).resolve() != cache_dir.resolve():
        raise RuntimeError(
            f"PipelineContext resolved a different stage cache: {context.output_dir} (expected {cache_dir})"
        )

    def stage_start(stage, index, total, _state):
        print(f"[{index + 1}/{total}] Running {stage.key}: {stage.label}", flush=True)

    def stage_complete(stage, index, total, _state):
        print(f"[{index + 1}/{total}] Completed {stage.key}", flush=True)

    final_state = resume_repair_and_downstream(
        context,
        state,
        max_recovery_rounds=args.max_recovery_rounds,
        edge_output_mode=args.edge_output_mode,
        relation_prompt_profile=args.relation_prompt_profile,
        on_stage_start=stage_start,
        on_stage_complete=stage_complete,
    )
    print("Extractor resume completed successfully")
    print(f"  nodes: {len(final_state.get('node_list') or [])} -> {output_node}")
    print(f"  edges: {len(final_state.get('edge_list') or [])} -> {output_edge}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        print(f"Resume failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
