"""Resume the fixed extractor pipeline from relation embedding retrieval."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.config import load_env_file, resolve_embedding_config
from pipeline.context import DEFAULT_EMBEDDING_MODEL, PipelineContext
from pipeline.orchestrator import execute_fixed_pipeline
from pipeline.stages.build_relations.stage import _resolve_embedding_proxy


DEFAULT_INPUT = Path(r"D:\AI4Math\pdfPipeline\backend\books\test.tex")
DEFAULT_OUTPUT_DIR = Path(r"D:\AI4Math\pdfPipeline\backend\test_output\test_1")


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


def load_embedding_resume_state(cache_dir: Path) -> tuple[dict, dict]:
    node_path = cache_dir / "node_dict_after_predicate_normalization.json"
    node_dict = _read_json(node_path, dict)
    if not node_dict:
        raise RuntimeError(f"Predicate-normalized node cache is empty: {node_path}")

    predicate_path = cache_dir / "predicate_entry_list.json"
    predicate_entries = _read_json(predicate_path, list)

    report_path = cache_dir / "relation_retrieval_report.json"
    report = _read_json(report_path, dict)
    if report.get("status") != "embedding_failed":
        raise RuntimeError(f"Relation retrieval did not stop at embedding_failed: {report_path}")
    if report.get("publishable") is not False:
        raise RuntimeError(f"Embedding failure report is unexpectedly publishable: {report_path}")

    embedding_cache_path = cache_dir / "relation_embedding_cache.json"
    if embedding_cache_path.is_file():
        embedding_cache = _read_json(embedding_cache_path, dict)
        if embedding_cache.get("schema_version") != 1:
            raise RuntimeError(f"Unsupported relation embedding cache schema: {embedding_cache_path}")
        cached_vectors = embedding_cache.get("vectors")
        if not isinstance(cached_vectors, dict):
            raise RuntimeError(f"Embedding cache vectors must be a dictionary: {embedding_cache_path}")
    else:
        cached_vectors = {}

    state = {
        "node_dict": node_dict,
        "node_list": list(node_dict.values()),
        "predicate_entry_list": predicate_entries,
        "relation_retrieval_report": report,
    }
    embedding_stats = report.get("embedding") if isinstance(report.get("embedding"), dict) else {}
    facts = {
        "node_count": len(node_dict),
        "predicate_entry_count": len(predicate_entries),
        "cached_vector_count": len(cached_vectors),
        "failed_embedding_count": int(embedding_stats.get("failed") or 0),
        "report_path": report_path,
    }
    return state, facts


def resume_embedding_and_downstream(
    context,
    state,
    *,
    edge_output_mode="structured",
    relation_prompt_profile="graph",
    on_stage_start=None,
    on_stage_complete=None,
):
    return execute_fixed_pipeline(
        context,
        state,
        start_stage="build_relations",
        edge_output_mode=edge_output_mode,
        relation_prompt_profile=relation_prompt_profile,
        on_stage_start=on_stage_start,
        on_stage_complete=on_stage_complete,
    )


def _embedding_model_name(cli_value=None):
    return (
        cli_value
        or os.getenv("PDFPIPELINE_EMBEDDING_MODEL")
        or os.getenv("EMBEDDING_MODEL")
        or DEFAULT_EMBEDDING_MODEL
    ).strip()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Resume the fixed extractor pipeline from relation embedding retrieval. "
            "Embedding credentials are read only from EMBEDDING_API_URL and EMBEDDING_API_KEY in .env."
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
    parser.add_argument("--edge-output-mode", choices=("structured", "natural", "both"), default="structured")
    parser.add_argument("--relation-prompt-profile", choices=("graph", "formalization"), default="graph")
    parser.add_argument("--source-format", choices=("auto", "markdown", "tex"), default="auto")
    parser.add_argument("--embedding-model-name")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate cache and embedding configuration without making API requests or stage writes.",
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

    load_env_file()
    embedding = resolve_embedding_config()
    if not embedding.api_url or not embedding.api_key:
        raise RuntimeError(
            "Embedding credentials are missing. Set EMBEDDING_API_URL and EMBEDDING_API_KEY in .env."
        )
    embedding_model = _embedding_model_name(args.embedding_model_name)
    state, facts = load_embedding_resume_state(cache_dir)

    print("Embedding resume preflight passed")
    print(f"  input: {input_path}")
    print(f"  cache: {cache_dir}")
    print(f"  failure report: {facts['report_path']}")
    print(f"  nodes: {facts['node_count']}")
    print(f"  predicate entries: {facts['predicate_entry_count']}")
    print(f"  cached embedding vectors: {facts['cached_vector_count']}")
    print(f"  previously failed embeddings: {facts['failed_embedding_count']}")
    print(f"  embedding endpoint: {embedding.api_url}")
    print(f"  embedding model: {embedding_model}")
    print(f"  embedding proxy: {_resolve_embedding_proxy() or 'direct'}")
    print("  continuation: build_relations embedding -> relation LLM tasks -> finalize_output")
    if args.dry_run:
        print("Dry run complete; no API request or stage write was performed.")
        return 0

    context = PipelineContext(
        file_path=str(input_path),
        output_node_path=str(output_node),
        output_edge_path=str(output_edge),
        output_natural_node_path=str(output_natural_node) if output_natural_node else None,
        api_url=args.api_url,
        model_name=args.model_name,
        api_key=args.api_key,
        embedding_api_url=embedding.api_url,
        embedding_api_key=embedding.api_key,
        embedding_model_name=embedding_model,
        num_threads=args.num_threads,
        checkpoint=args.checkpoint,
        enable_analysis=True,
        source_format=args.source_format,
    )
    if Path(context.output_dir).resolve() != cache_dir.resolve():
        raise RuntimeError(
            f"PipelineContext resolved a different stage cache: {context.output_dir} (expected {cache_dir})"
        )

    def stage_start(stage, index, total, _state):
        print(f"[{index + 1}/{total}] Running {stage.key}: {stage.label}", flush=True)

    def stage_complete(stage, index, total, _state):
        print(f"[{index + 1}/{total}] Completed {stage.key}", flush=True)

    final_state = resume_embedding_and_downstream(
        context,
        state,
        edge_output_mode=args.edge_output_mode,
        relation_prompt_profile=args.relation_prompt_profile,
        on_stage_start=stage_start,
        on_stage_complete=stage_complete,
    )
    print("Extractor embedding resume completed successfully")
    print(f"  nodes: {len(final_state.get('node_list') or [])} -> {output_node}")
    print(f"  edges: {len(final_state.get('edge_list') or [])} -> {output_edge}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        print(f"Resume failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
