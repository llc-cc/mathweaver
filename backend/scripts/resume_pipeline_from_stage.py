"""Resume the late MathKG pipeline from an existing stage cache.

This script intentionally delegates every stage execution to the official
MathKG agent stage wrapper.  It never edits canonical cache JSON directly.

The defaults match ``extractor.py`` in this repository, so the interrupted run
can be resumed with:

    python scripts/resume_pipeline_from_stage.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENT_TOOL = (
    PROJECT_ROOT
    / ".codex"
    / "skills"
    / "mathkg-process"
    / "scripts"
    / "mathkg_agent_tool.py"
)

EXPERIMENTAL_LOGIC_IR_STAGES = (
    "compile_logic_form",
    "normalize_predicates",
)
MAINLINE_RESUMABLE_STAGES = (
    "build_relations",
    "finalize_output",
)
RESUMABLE_STAGES = (
    *EXPERIMENTAL_LOGIC_IR_STAGES,
    *MAINLINE_RESUMABLE_STAGES,
)

DEFAULT_INPUT = PROJECT_ROOT / "books" / "elegantbook-en-section-1.1.md"
DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT / "test_output" / "elegantbook-en-section-1.1_md"
)


def _absolute_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read valid JSON cache: {path}: {exc}") from exc


def _require_nonempty_json(path: Path, expected_type: type) -> int:
    if not path.is_file():
        raise RuntimeError(f"Required cache file is missing: {path}")
    value = _read_json(path)
    if not isinstance(value, expected_type) or not value:
        raise RuntimeError(
            f"Required cache must be a non-empty {expected_type.__name__}: {path}"
        )
    return len(value)


def _select_compile_source(cache_dir: Path) -> tuple[Path, int]:
    candidates = (
        cache_dir / "agent_state" / "node_dict_after_repair_lite.json",
        cache_dir / "references_dict.json",
        cache_dir / "node_dict_after_repair.json",
        cache_dir / "node_dict.json",
    )
    for path in candidates:
        if path.is_file():
            return path, _require_nonempty_json(path, dict)
    raise RuntimeError(
        "No usable node cache exists for compile_logic_form. Expected one of: "
        + ", ".join(str(path) for path in candidates)
    )


def _preflight_start_stage(
    start_stage: str,
    cache_dir: Path,
    output_edge: Path,
    *,
    experimental_logic_ir: bool,
) -> list[str]:
    facts: list[str] = []
    if start_stage == "compile_logic_form":
        source_path, count = _select_compile_source(cache_dir)
        facts.append(f"source cache: {source_path} ({count} nodes)")
    elif start_stage == "normalize_predicates":
        node_count = _require_nonempty_json(
            cache_dir / "node_dict_normalized.json", dict
        )
        logic_count = _require_nonempty_json(
            cache_dir / "logic_form_local_dict.json", dict
        )
        facts.append(f"normalized nodes: {node_count}")
        facts.append(f"compiled logic records: {logic_count}")
    elif start_stage == "build_relations":
        if experimental_logic_ir:
            node_count = _require_nonempty_json(
                cache_dir / "node_dict_after_predicate_normalization.json", dict
            )
            facts.append(f"predicate-normalized nodes: {node_count}")
        else:
            source_path, count = _select_compile_source(cache_dir)
            facts.append(f"mainline node cache: {source_path} ({count} nodes)")
    elif start_stage == "finalize_output":
        if experimental_logic_ir:
            node_path = cache_dir / "node_dict_after_predicate_normalization.json"
            node_count = _require_nonempty_json(node_path, dict)
            facts.append(f"predicate-normalized nodes: {node_count}")
        else:
            node_path, node_count = _select_compile_source(cache_dir)
            facts.append(f"mainline node cache: {node_path} ({node_count} nodes)")
        structured_edges = cache_dir / "agent_state" / "edge_list_structured.json"
        edge_path = structured_edges if structured_edges.is_file() else output_edge
        edge_count = _require_nonempty_json(edge_path, list)
        facts.append(f"cached edges: {edge_path} ({edge_count} edges)")
    return facts


def _latest_unresolved_failures(
    cache_dir: Path,
    *,
    experimental_logic_ir: bool,
) -> list[dict]:
    stage_runs = cache_dir / "agent_state" / "stage_runs"
    if not stage_runs.is_dir():
        return []

    unresolved = []
    for stage_dir in sorted(path for path in stage_runs.iterdir() if path.is_dir()):
        if (
            not experimental_logic_ir
            and stage_dir.name in EXPERIMENTAL_LOGIC_IR_STAGES
        ):
            continue
        reports = sorted(stage_dir.glob("*/failure_report.json"))
        if not reports:
            continue
        report_path = reports[-1]
        try:
            report = _read_json(report_path)
        except RuntimeError:
            continue
        if not isinstance(report, dict) or report.get("status") == "resolved":
            continue
        failed_keys = report.get("failed_task_keys")
        if not isinstance(failed_keys, list) or not failed_keys:
            continue
        unresolved.append(
            {
                "stage": str(report.get("stage") or stage_dir.name),
                "failed_task_keys": [str(key) for key in failed_keys],
                "report_path": report_path,
            }
        )
    return unresolved


def _common_tool_args(args, input_path: Path, output_dir: Path) -> list[str]:
    output_node = _absolute_path(args.output_node or output_dir / "TEST_NODE_OUT.json")
    output_edge = _absolute_path(args.output_edge or output_dir / "TEST_EDGE_OUT.json")
    output_natural_node = _absolute_path(
        args.output_natural_node or output_dir / "TEST_NODE_NATURAL_OUT.json"
    )
    common = [
        str(input_path),
        "--output-dir",
        str(output_dir),
        "--output-node",
        str(output_node),
        "--output-edge",
        str(output_edge),
        "--output-natural-node",
        str(output_natural_node),
        "--num-threads",
        str(args.num_threads),
        "--checkpoint",
        str(args.checkpoint),
        "--enable-analysis",
        "--edge-output-mode",
        args.edge_output_mode,
        "--relation-prompt-profile",
        args.relation_prompt_profile,
        "--source-format",
        args.source_format,
    ]
    if args.experimental_logic_ir:
        common.append("--experimental-logic-ir")
    if args.api_url:
        common.extend(("--api-url", args.api_url))
    if args.model_name:
        common.extend(("--model-name", args.model_name))
    return common


def _run_stage(command: list[str], stage: str, dry_run: bool) -> dict | None:
    stage_command = [*command, "--stage", stage]
    if dry_run:
        print("  " + subprocess.list2cmdline(stage_command))
        return None

    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        stage_command,
        cwd=PROJECT_ROOT,
        env=child_env,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    stdout = completed.stdout.strip()
    if completed.returncode != 0:
        if stdout:
            print(stdout, file=sys.stderr)
        raise RuntimeError(
            f"Stage wrapper failed for {stage} with exit code {completed.returncode}"
        )
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Stage wrapper returned invalid JSON for {stage}: {stdout[:500]}"
        ) from exc

    validation = result.get("validation") if isinstance(result, dict) else None
    issues = validation.get("issues", []) if isinstance(validation, dict) else []
    errors = [
        issue
        for issue in issues
        if isinstance(issue, dict) and issue.get("severity") == "error"
    ]
    if errors:
        details = "; ".join(str(issue.get("message") or issue) for issue in errors)
        raise RuntimeError(f"Stage {stage} failed validation: {details}")
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Resume relation extraction and finalization from existing cache. "
            "The logic IR side path is included only with --experimental-logic-ir."
        )
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--output-node")
    parser.add_argument("--output-edge")
    parser.add_argument("--output-natural-node")
    parser.add_argument("--from-stage", choices=RESUMABLE_STAGES, default="build_relations")
    parser.add_argument("--stop-after", choices=RESUMABLE_STAGES, default="finalize_output")
    parser.add_argument("--num-threads", type=int, default=16)
    parser.add_argument("--checkpoint", type=int, default=500)
    parser.add_argument("--edge-output-mode", choices=("structured", "natural", "both"), default="structured")
    parser.add_argument("--relation-prompt-profile", choices=("graph", "formalization"), default="graph")
    parser.add_argument("--source-format", choices=("auto", "markdown", "tex"), default="markdown")
    parser.add_argument("--api-url")
    parser.add_argument("--model-name")
    parser.add_argument(
        "--experimental-logic-ir",
        action="store_true",
        help="Include compile_logic_form and normalize_predicates before relations.",
    )
    parser.add_argument(
        "--strict-upstream",
        action="store_true",
        help="Abort instead of continuing when an older unresolved task report exists.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate cache and print wrapper commands without executing stages.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    input_path = _absolute_path(args.input)
    output_dir = _absolute_path(args.output_dir)
    output_edge = _absolute_path(args.output_edge or output_dir / "TEST_EDGE_OUT.json")
    cache_dir = output_dir / "_stage_cache"

    if not AGENT_TOOL.is_file():
        raise RuntimeError(f"MathKG agent stage wrapper is missing: {AGENT_TOOL}")
    if not input_path.is_file():
        raise RuntimeError(f"Input document is missing: {input_path}")
    if not cache_dir.is_dir():
        raise RuntimeError(f"Stage cache directory is missing: {cache_dir}")

    active_stages = (
        RESUMABLE_STAGES
        if args.experimental_logic_ir
        else MAINLINE_RESUMABLE_STAGES
    )
    if args.from_stage not in active_stages or args.stop_after not in active_stages:
        raise RuntimeError(
            "compile_logic_form and normalize_predicates require "
            "--experimental-logic-ir"
        )
    start_index = active_stages.index(args.from_stage)
    stop_index = active_stages.index(args.stop_after)
    if stop_index < start_index:
        raise RuntimeError("--stop-after cannot be earlier than --from-stage")
    stages = active_stages[start_index : stop_index + 1]

    print("Resume preflight")
    print(f"  input: {input_path}")
    print(f"  cache: {cache_dir}")
    print(f"  stages: {' -> '.join(stages)}")
    for fact in _preflight_start_stage(
        args.from_stage,
        cache_dir,
        output_edge,
        experimental_logic_ir=args.experimental_logic_ir,
    ):
        print(f"  {fact}")

    unresolved = _latest_unresolved_failures(
        cache_dir,
        experimental_logic_ir=args.experimental_logic_ir,
    )
    if unresolved:
        print("\nWarning: unresolved upstream task reports were found:")
        for item in unresolved:
            keys = item["failed_task_keys"]
            preview = ", ".join(keys[:10]) + (" ..." if len(keys) > 10 else "")
            print(f"  {item['stage']}: {len(keys)} task(s) [{preview}]")
        if args.strict_upstream:
            raise RuntimeError(
                "Unresolved upstream task reports exist; remove --strict-upstream "
                "only when intentionally forcing a late-stage resume."
            )
        print("  Continuing because late-stage resume was explicitly requested.")

    base_command = [
        sys.executable,
        str(AGENT_TOOL),
        "run-stage",
        *_common_tool_args(args, input_path, output_dir),
    ]

    if args.dry_run:
        print("\nDry-run commands:")
    for position, stage in enumerate(stages, start=1):
        if not args.dry_run:
            print(f"\n[{position}/{len(stages)}] Running {stage} ...", flush=True)
        result = _run_stage(base_command, stage, args.dry_run)
        if result is not None:
            duration = result.get("duration_seconds", "?")
            invalidation = result.get("downstream_invalidation")
            moved = invalidation.get("moved_count", 0) if isinstance(invalidation, dict) else 0
            print(f"[{position}/{len(stages)}] {stage} passed ({duration}s, archived {moved} stale artifacts)")

    if args.dry_run:
        print("\nDry run complete; no pipeline stage was executed.")
    else:
        print("\nResume completed successfully.")
        print(f"  node output: {_absolute_path(args.output_node or output_dir / 'TEST_NODE_OUT.json')}")
        print(f"  edge output: {output_edge}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        print(f"Resume failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
