import argparse
import contextlib
import json
import os
import sys
from pathlib import Path

def _find_repo_root():
    for parent in Path(__file__).resolve().parents:
        if (parent / "pipeline").is_dir() and (parent / "JoinAgent").exists():
            return str(parent)
    raise RuntimeError("Cannot locate MathKG backend repo root from skill script path.")


ROOT = _find_repo_root()
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pipeline.config import load_env_file, resolve_llm_config
from pipeline.context import DEFAULT_CHECKPOINT, DEFAULT_NUM_THREADS, PipelineContext
from pipeline.main_agent import AgentRunConfig, build_default_output_paths
from pipeline.main_agent.toolkit import AgentTool


def _json_arg(value):
    if value is None:
        return {}
    if os.path.exists(value):
        with open(value, "r", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(value)


def add_common_arguments(parser):
    parser.add_argument("input", help="Input Markdown or TeX document path used by the existing pipeline.")
    parser.add_argument("--output-dir", help="Directory for final outputs; defaults to <input>_agent_output.")
    parser.add_argument("--output-node", help="Final node JSON path.")
    parser.add_argument("--output-edge", help="Final edge JSON path.")
    parser.add_argument("--output-natural-node", help="Optional final natural-node JSON path.")
    parser.add_argument("--api-url", help="LLM API URL override.")
    parser.add_argument("--model-name", help="LLM model name override.")
    parser.add_argument("--api-key", help="LLM API key override.")
    parser.add_argument("--num-threads", type=int, default=DEFAULT_NUM_THREADS)
    parser.add_argument("--checkpoint", type=int, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--enable-analysis", action="store_true")
    parser.add_argument("--disable-math-disambiguation", action="store_true")
    parser.add_argument(
        "--experimental-logic-ir",
        action="store_true",
        help="Run compile_logic_form and normalize_predicates before relation extraction.",
    )
    parser.add_argument("--edge-output-mode", choices=["structured", "natural", "both"], default="structured")
    parser.add_argument("--relation-prompt-profile", choices=["graph", "formalization"], default="graph")
    parser.add_argument("--llm-engine", choices=["api", "claude_cli"], default="api")
    parser.add_argument("--source-format", choices=["auto", "markdown", "tex"], default="auto")
    parser.add_argument("--claude-command", default="claude")
    parser.add_argument("--claude-model", default="deepseek-v4-flash")
    parser.add_argument("--claude-agent")
    parser.add_argument("--claude-batch-size", type=int, default=8)
    parser.add_argument("--claude-timeout-seconds", type=int, default=900)
    parser.add_argument("--claude-max-retries", type=int, default=1)


def build_context_and_tool(args):
    load_env_file()
    resolved = resolve_llm_config(args.api_url, args.model_name, args.api_key)
    paths = build_default_output_paths(args.input, args.output_dir)
    context = PipelineContext(
        file_path=args.input,
        output_node_path=args.output_node or paths["node"],
        output_edge_path=args.output_edge or paths["edge"],
        output_natural_node_path=args.output_natural_node or paths["natural_node"],
        api_url=resolved.api_url,
        model_name=resolved.model_name,
        api_key=resolved.api_key,
        num_threads=args.num_threads,
        checkpoint=args.checkpoint,
        enable_analysis=args.enable_analysis,
        enable_math_disambiguation=not args.disable_math_disambiguation,
        llm_engine=args.llm_engine,
        claude_command=args.claude_command,
        claude_model=args.claude_model,
        claude_agent=args.claude_agent,
        claude_batch_size=args.claude_batch_size,
        claude_timeout_seconds=args.claude_timeout_seconds,
        claude_max_retries=args.claude_max_retries,
        source_format=args.source_format,
        execution_mode="agent",
    )
    context.agent_tool_entry = os.path.relpath(Path(__file__).resolve(), ROOT)
    config = AgentRunConfig(
        edge_output_mode=args.edge_output_mode,
        relation_prompt_profile=args.relation_prompt_profile,
        enable_analysis=args.enable_analysis,
        enable_math_disambiguation=not args.disable_math_disambiguation,
        experimental_logic_ir=args.experimental_logic_ir,
    )
    return AgentTool(context, config)


def run_command(args):
    tool = build_context_and_tool(args)
    if args.command == "scan-cache":
        return tool.scan_cache()
    if args.command == "validate-stage":
        return tool.validate_stage(args.stage)
    if args.command == "build-review-packet":
        return tool.build_review_packet(args.stage, args.source_blocks_per_chunk)
    if args.command == "run-stage":
        return tool.run_stage(args.stage)
    if args.command == "rerun-failed-tasks":
        return tool.rerun_failed_tasks(args.stage)
    if args.command == "locate-repair-context":
        return tool.locate_repair_context(_json_arg(args.repair_intent))
    if args.command == "build-repair-prompt":
        return tool.build_repair_prompt(args.repair_id)
    if args.command == "rerun-extract-statements":
        return tool.rerun_extract_statements(_json_arg(args.repair_intent))
    if args.command == "build-candidate-review-packet":
        return tool.build_candidate_review_packet(args.repair_id)
    if args.command == "apply-repair":
        return tool.apply_repair(args.repair_id, _json_arg(args.decision))
    if args.command == "load-agent-state":
        return tool.load_agent_state()
    if args.command == "next-action":
        return tool.next_action()
    if args.command == "write-agent-decision":
        decision = _json_arg(args.decision)
        return tool.write_agent_decision(decision)
    if args.command == "write-run-report":
        return tool.write_run_report()
    raise ValueError(f"Unknown command: {args.command}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Fact and action tools for Claude Code to act as the MathKG main agent."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("scan-cache", "Return stage-cache file facts and basic counts."),
        ("load-agent-state", "Read current document memory, quality facts, decisions, and report."),
        ("next-action", "Return the single unconsumed orchestration transition, with a repeat guard."),
        ("write-run-report", "Write final report from tool facts and Claude Code decisions."),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        add_common_arguments(sub)

    validate_parser = subparsers.add_parser("validate-stage", help="Return fact-only validation for one stage.")
    add_common_arguments(validate_parser)
    validate_parser.add_argument("--stage", required=True)

    review_packet_parser = subparsers.add_parser(
        "build-review-packet",
        help="Build a fact-only semantic review packet for one stage.",
    )
    add_common_arguments(review_packet_parser)
    review_packet_parser.add_argument("--stage", required=True)
    review_packet_parser.add_argument(
        "--source-blocks-per-chunk",
        type=int,
        default=8,
        help="Number of source block records to write into each review packet chunk.",
    )

    run_parser = subparsers.add_parser("run-stage", help="Execute one existing pipeline stage.")
    add_common_arguments(run_parser)
    run_parser.add_argument("--stage", required=True)

    failed_task_parser = subparsers.add_parser(
        "rerun-failed-tasks",
        help="Rerun only unresolved tasks for a supported partially failed stage.",
    )
    add_common_arguments(failed_task_parser)
    failed_task_parser.add_argument("--stage", required=True)

    locate_parser = subparsers.add_parser(
        "locate-repair-context",
        help="Locate localized source context for an extract_statements repair intent.",
    )
    add_common_arguments(locate_parser)
    locate_parser.add_argument("--repair-intent", required=True, help="JSON object or path to repair intent JSON.")

    prompt_parser = subparsers.add_parser(
        "build-repair-prompt",
        help="Build repair prompts for an existing extract_statements repair candidate.",
    )
    add_common_arguments(prompt_parser)
    prompt_parser.add_argument("--repair-id", required=True)

    rerun_parser = subparsers.add_parser(
        "rerun-extract-statements",
        help="Locate context, build prompts, and generate a sidecar extract_statements repair candidate.",
    )
    add_common_arguments(rerun_parser)
    rerun_parser.add_argument("--repair-intent", required=True, help="JSON object or path to repair intent JSON.")

    candidate_review_parser = subparsers.add_parser(
        "build-candidate-review-packet",
        help="Build a semantic review packet for a repair candidate.",
    )
    add_common_arguments(candidate_review_parser)
    candidate_review_parser.add_argument("--repair-id", required=True)

    apply_parser = subparsers.add_parser(
        "apply-repair",
        help="Apply an explicitly approved extract_statements repair candidate.",
    )
    add_common_arguments(apply_parser)
    apply_parser.add_argument("--repair-id", required=True)
    apply_parser.add_argument("--decision", required=True, help="Approved apply decision JSON object or path.")

    decision_parser = subparsers.add_parser("write-agent-decision", help="Append a Claude Code decision record.")
    add_common_arguments(decision_parser)
    decision_parser.add_argument(
        "--decision",
        required=True,
        help="JSON object or path to JSON file containing Claude Code's decision.",
    )

    args = parser.parse_args(argv)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = run_command(args)
    except Exception as exc:
        result = {
            "command": getattr(args, "command", ""),
            "error": {
                "type": exc.__class__.__name__,
                "message": str(exc),
            },
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
