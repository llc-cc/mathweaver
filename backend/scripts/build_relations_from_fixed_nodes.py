"""Build graph relations from the reviewed round-4 node artifact.

The script runs the production fixed pipeline tail
``build_relations -> finalize_output`` in an isolated run directory. Runtime
copies receive canonical source envelopes and source-derived IDs so the normal
finalizer contract remains active. Published edge endpoints are then mapped
back to the stable IDs stored in ``node_fixed_round4.json``.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from pipeline.common.io import atomic_write_json, write_json  # noqa: E402
from pipeline.common.node import (  # noqa: E402
    SOURCE_ENVELOPE_KEY,
    is_relation_statement_node_type,
    merge_node_with_source_envelope,
)
from pipeline.config import load_env_file, resolve_embedding_config  # noqa: E402
from pipeline.context import DEFAULT_EMBEDDING_MODEL, PipelineContext  # noqa: E402
from pipeline.orchestrator import build_fixed_stage_plan, execute_fixed_pipeline  # noqa: E402
from pipeline.stages.build_relations.stage import _resolve_embedding_proxy  # noqa: E402
from pipeline.stages.finalize_output.stage import validate_edge_endpoints  # noqa: E402


DEFAULT_WORK_ROOT = BACKEND_ROOT / "_relation_runs"

DEFAULT_RESULT_DIR = (
    BACKEND_ROOT / "books" / "最优化" / "bv_cvxbook_1.1-2.3_test1_result"
)
DEFAULT_INPUT_NODES = DEFAULT_RESULT_DIR / "node_fixed_round4.json"
DEFAULT_SOURCE = BACKEND_ROOT / "books" / "最优化" / "bv_cvxbook_1.1-2.3.md"
DEFAULT_OUTPUT_EDGE = DEFAULT_RESULT_DIR / "edge_fixed_round1.json"

EDGE_ENDPOINT_KEYS = (
    "出发节点",
    "到达节点",
    "from",
    "to",
    "source",
    "target",
    "source_id",
    "target_id",
)
CHILD_PARENT_ID_KEYS = (
    "source_parent_global_id",
    "target_parent_global_id",
)


@dataclass(frozen=True)
class PreparedNodes:
    stable_nodes: list[dict]
    runtime_nodes: list[dict]
    runtime_to_stable_id: dict[str, str]
    changed_id_count: int


def _absolute_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = BACKEND_ROOT / path
    return path.resolve()


def _read_node_list(path: Path) -> list[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read valid node JSON: {path}: {exc}") from exc
    if not isinstance(value, list) or not value:
        raise RuntimeError(f"Node input must be a nonempty JSON list: {path}")
    return value


def prepare_runtime_nodes(path: Path) -> PreparedNodes:
    """Validate stable nodes and seal runtime-only canonical copies."""

    stable_nodes = _read_node_list(path)
    stable_ids: set[str] = set()
    runtime_ids: set[str] = set()
    runtime_nodes: list[dict] = []
    runtime_to_stable_id: dict[str, str] = {}
    changed_id_count = 0

    for index, node in enumerate(stable_nodes):
        if not isinstance(node, dict):
            raise RuntimeError(f"Node at index {index} is not an object")

        stable_id = str(node.get("global_id") or "").strip()
        if not stable_id:
            raise RuntimeError(f"Node at index {index} has no global_id")
        if stable_id in stable_ids:
            raise RuntimeError(f"Duplicate stable global_id at index {index}: {stable_id}")
        stable_ids.add(stable_id)

        node_type = str(node.get("node_type") or "").strip()
        if is_relation_statement_node_type(node_type):
            source_original_form = node.get("source_original_form")
            if not isinstance(source_original_form, str) or not source_original_form.strip():
                raise RuntimeError(
                    "Relation-statement node is missing source_original_form: "
                    f"index={index}, global_id={stable_id}, node_type={node_type!r}"
                )

        runtime_node, _ = merge_node_with_source_envelope(
            copy.deepcopy(node),
            {},
            stage_name="build_relations_from_fixed_nodes",
            allowed_fields=(),
            seal=True,
        )
        runtime_id = str(runtime_node.get("global_id") or "").strip()
        if not runtime_id:
            raise RuntimeError(f"Runtime node at index {index} has no generated global_id")
        if runtime_id in runtime_ids:
            other_stable_id = runtime_to_stable_id[runtime_id]
            raise RuntimeError(
                "Two fixed nodes resolve to the same runtime source-derived global_id: "
                f"runtime_id={runtime_id}, stable_ids={other_stable_id},{stable_id}"
            )
        if not isinstance(runtime_node.get(SOURCE_ENVELOPE_KEY), dict):
            raise RuntimeError(f"Runtime node at index {index} has no source envelope")

        runtime_ids.add(runtime_id)
        runtime_to_stable_id[runtime_id] = stable_id
        runtime_nodes.append(runtime_node)
        changed_id_count += int(runtime_id != stable_id)

    return PreparedNodes(
        stable_nodes=stable_nodes,
        runtime_nodes=runtime_nodes,
        runtime_to_stable_id=runtime_to_stable_id,
        changed_id_count=changed_id_count,
    )


def _map_runtime_id(value, runtime_to_stable_id: dict[str, str], *, location: str):
    if value is None or value == "":
        return value
    runtime_id = str(value).strip()
    stable_id = runtime_to_stable_id.get(runtime_id)
    if stable_id is None:
        raise RuntimeError(f"Unknown runtime node ID at {location}: {runtime_id}")
    return stable_id


def remap_edge_ids(
    edge_list: list[dict],
    runtime_to_stable_id: dict[str, str],
) -> list[dict]:
    """Map published endpoint and child-match parent IDs to round-4 IDs."""

    if not isinstance(edge_list, list):
        raise RuntimeError("The relation pipeline did not return an edge_list")

    remapped_edges = copy.deepcopy(edge_list)
    for edge_index, edge in enumerate(remapped_edges):
        if not isinstance(edge, dict):
            raise RuntimeError(f"Edge at index {edge_index} is not an object")

        for key in EDGE_ENDPOINT_KEYS:
            if key in edge:
                edge[key] = _map_runtime_id(
                    edge[key],
                    runtime_to_stable_id,
                    location=f"edge[{edge_index}].{key}",
                )

        child_matches = edge.get("child_matches")
        if child_matches is None:
            continue
        if not isinstance(child_matches, list):
            raise RuntimeError(f"edge[{edge_index}].child_matches is not a list")
        for child_index, child_match in enumerate(child_matches):
            if not isinstance(child_match, dict):
                raise RuntimeError(
                    f"edge[{edge_index}].child_matches[{child_index}] is not an object"
                )
            for key in CHILD_PARENT_ID_KEYS:
                if key in child_match:
                    child_match[key] = _map_runtime_id(
                        child_match[key],
                        runtime_to_stable_id,
                        location=(
                            f"edge[{edge_index}].child_matches[{child_index}].{key}"
                        ),
                    )

    return remapped_edges


def _tail_stage_keys(relation_prompt_profile: str) -> list[str]:
    plan = build_fixed_stage_plan(
        edge_output_mode="structured",
        relation_prompt_profile=relation_prompt_profile,
    )
    stage_keys = [stage.key for stage in plan]
    start_index = stage_keys.index("build_relations")
    return stage_keys[start_index:]


def _planned_run_dir(output_edge: Path, requested: str | None) -> Path:
    if requested:
        return _absolute_path(requested)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return DEFAULT_WORK_ROOT / f"{output_edge.stem}_{stamp}_{suffix}"


MAX_SAFE_STAGE_PATH_LENGTH = 240


def _check_stage_path_length(run_dir: Path) -> None:
    """Fail early instead of letting Windows report a misleading FileNotFoundError."""

    probe = (
        run_dir
        / "_stage_cache"
        / "agent_state"
        / "stage_runs"
        / "build_relations_rerank"
        / "20260827T000000_0000000000"
        / ("input_manifest.json." + "0" * 32 + ".tmp")
    )
    if len(str(probe)) >= MAX_SAFE_STAGE_PATH_LENGTH:
        raise RuntimeError(
            "Run directory is too long for Windows stage artifacts: "
            f"{run_dir} (probe length={len(str(probe))}). "
            "Choose a shorter --run-dir."
        )


def _resolve_embedding_cache(path: Path) -> tuple[Path, int]:
    """Resolve and validate a previous run's relation embedding cache."""

    cache_path = path
    if path.is_dir():
        cache_path = path / "_stage_cache" / "relation_embedding_cache.json"
    if not cache_path.is_file():
        raise RuntimeError(
            "Embedding cache is missing; pass a previous run directory or "
            f"relation_embedding_cache.json: {cache_path}"
        )
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read embedding cache: {cache_path}: {exc}") from exc
    schema_version = payload.get("schema_version") if isinstance(payload, dict) else None
    vectors = payload.get("vectors") if isinstance(payload, dict) else None
    if schema_version != 1 or not isinstance(vectors, dict) or not vectors:
        raise RuntimeError(
            "Embedding cache is not a usable schema-v1 cache with vectors: "
            f"{cache_path}"
        )
    return cache_path, len(vectors)


def _copy_embedding_cache(source: Path, run_dir: Path) -> int:
    source, vector_count = _resolve_embedding_cache(source)
    destination = run_dir / "_stage_cache" / "relation_embedding_cache.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return vector_count


def _prepare_run_dir(path: Path, *, resume: bool) -> None:
    _check_stage_path_length(path)
    if resume:
        if not path.is_dir():
            raise RuntimeError(f"Resume run directory is missing: {path}")
        cache_path = path / "_stage_cache" / "relation_embedding_cache.json"
        _resolve_embedding_cache(cache_path)
        return
    if path.exists():
        if not path.is_dir():
            raise RuntimeError(f"Run path exists and is not a directory: {path}")
        if any(path.iterdir()):
            raise RuntimeError(f"Run directory must be empty to avoid stale caches: {path}")
    else:
        path.mkdir(parents=True, exist_ok=False)


def _embedding_model_name(cli_value: str | None) -> str:
    return (
        cli_value
        or os.getenv("PDFPIPELINE_EMBEDDING_MODEL")
        or os.getenv("EMBEDDING_MODEL")
        or DEFAULT_EMBEDDING_MODEL
    ).strip()


def run_relation_tail(
    *,
    input_nodes: Path,
    source: Path,
    output_edge: Path,
    run_dir: Path,
    api_url: str | None,
    model_name: str | None,
    api_key: str | None,
    embedding_api_url: str | None,
    embedding_api_key: str | None,
    embedding_model_name: str,
    relation_retrieval_mode: str,
    relation_prompt_profile: str,
    source_format: str,
    num_threads: int,
    checkpoint: int,
    reuse_embedding_from: Path | None = None,
    resume_run: bool = False,
) -> dict:
    prepared = prepare_runtime_nodes(input_nodes)
    if resume_run and reuse_embedding_from is not None:
        raise RuntimeError("--resume-run-dir cannot be combined with --reuse-embedding-from")
    _prepare_run_dir(run_dir, resume=resume_run)
    reused_embedding_vector_count = 0
    if resume_run:
        _, reused_embedding_vector_count = _resolve_embedding_cache(
            run_dir / "_stage_cache" / "relation_embedding_cache.json"
        )
    elif reuse_embedding_from is not None:
        reused_embedding_vector_count = _copy_embedding_cache(
            reuse_embedding_from,
            run_dir,
        )

    runtime_edge_path = run_dir / "edge_runtime.json"
    write_json(
        str(run_dir / "runtime_id_map.json"),
        [
            {
                "runtime_global_id": runtime_id,
                "stable_global_id": stable_id,
            }
            for runtime_id, stable_id in prepared.runtime_to_stable_id.items()
        ],
    )

    context = PipelineContext(
        file_path=str(source),
        output_node_path=None,
        output_edge_path=str(runtime_edge_path),
        api_url=api_url,
        model_name=model_name,
        api_key=api_key,
        embedding_api_url=embedding_api_url,
        embedding_api_key=embedding_api_key,
        embedding_model_name=embedding_model_name,
        relation_retrieval_mode=relation_retrieval_mode,
        num_threads=num_threads,
        checkpoint=checkpoint,
        enable_analysis=True,
        source_format=source_format,
        source_origin="markdown",
        cache_policy="legacy",
    )
    expected_cache_dir = (run_dir / "_stage_cache").resolve()
    if Path(context.stage_cache_dir).resolve() != expected_cache_dir:
        raise RuntimeError(
            "PipelineContext resolved an unexpected stage cache: "
            f"{context.stage_cache_dir} (expected {expected_cache_dir})"
        )
    context.resume_task_checkpoints = resume_run

    def stage_start(stage, index, total, _state):
        print(f"[{index + 1}/{total}] Running {stage.key}: {stage.label}", flush=True)

    def stage_complete(stage, index, total, _state):
        print(f"[{index + 1}/{total}] Completed {stage.key}", flush=True)

    state = {
        "node_list": prepared.runtime_nodes,
        "node_dict": {
            index: node for index, node in enumerate(prepared.runtime_nodes)
        },
    }
    final_state = execute_fixed_pipeline(
        context,
        state=state,
        start_stage="build_relations",
        edge_output_mode="structured",
        relation_prompt_profile=relation_prompt_profile,
        on_stage_start=stage_start,
        on_stage_complete=stage_complete,
    )
    remapped_edges = remap_edge_ids(
        final_state.get("edge_list"),
        prepared.runtime_to_stable_id,
    )
    validate_edge_endpoints(remapped_edges, prepared.stable_nodes)

    atomic_write_json(str(output_edge), remapped_edges)
    return {
        "node_count": len(prepared.stable_nodes),
        "edge_count": len(remapped_edges),
        "changed_runtime_id_count": prepared.changed_id_count,
        "reused_embedding_vector_count": reused_embedding_vector_count,
        "run_dir": str(run_dir),
        "output_edge": str(output_edge),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run build_relations and finalize_output from node_fixed_round4.json, "
            "then publish edge_fixed_round1.json with round-4 node IDs."
        )
    )
    parser.add_argument("--input-nodes", default=str(DEFAULT_INPUT_NODES))
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output-edge", default=str(DEFAULT_OUTPUT_EDGE))
    parser.add_argument("--run-dir")
    parser.add_argument(
        "--resume-run-dir",
        help="Resume an existing short-path relation run and rerun only unresolved tasks.",
    )
    parser.add_argument(
        "--reuse-embedding-from",
        help=(
            "Previous relation run directory or relation_embedding_cache.json "
            "to reuse; only matching text/model keys avoid new embedding calls."
        ),
    )
    parser.add_argument("--api-url")
    parser.add_argument("--model-name")
    parser.add_argument("--api-key")
    parser.add_argument("--embedding-api-url")
    parser.add_argument("--embedding-api-key")
    parser.add_argument("--embedding-model-name")
    parser.add_argument(
        "--relation-retrieval-mode",
        choices=("hybrid_strict", "sparse_preview"),
        default="hybrid_strict",
    )
    parser.add_argument(
        "--relation-prompt-profile",
        choices=("graph", "formalization"),
        default="graph",
    )
    parser.add_argument(
        "--source-format",
        choices=("auto", "markdown", "tex"),
        default="markdown",
    )
    parser.add_argument("--num-threads", type=int, default=16)
    parser.add_argument("--checkpoint", type=int, default=500)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate nodes, ID mapping, paths, and configuration without "
            "stage writes or API calls."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = _build_parser().parse_args(argv)
    input_nodes = _absolute_path(args.input_nodes)
    source = _absolute_path(args.source)
    output_edge = _absolute_path(args.output_edge)
    if args.resume_run_dir and args.run_dir:
        raise RuntimeError("--resume-run-dir cannot be combined with --run-dir")
    run_dir = (
        _absolute_path(args.resume_run_dir)
        if args.resume_run_dir
        else _planned_run_dir(output_edge, args.run_dir)
    )
    reuse_embedding_from = (
        _absolute_path(args.reuse_embedding_from)
        if args.reuse_embedding_from
        else None
    )

    if not input_nodes.is_file():
        raise RuntimeError(f"Node input is missing: {input_nodes}")
    if not source.is_file():
        raise RuntimeError(f"Source document is missing: {source}")
    if input_nodes == output_edge:
        raise RuntimeError("--output-edge must not overwrite --input-nodes")
    if args.num_threads < 1 or args.checkpoint < 1:
        raise RuntimeError("--num-threads and --checkpoint must be positive")

    load_env_file()
    embedding = resolve_embedding_config(
        args.embedding_api_url,
        args.embedding_api_key,
    )
    if args.relation_retrieval_mode == "hybrid_strict" and (
        not embedding.api_url or not embedding.api_key
    ):
        raise RuntimeError(
            "hybrid_strict requires embedding credentials. Set "
            "EMBEDDING_API_URL and EMBEDDING_API_KEY or pass the corresponding options."
        )
    embedding_model = _embedding_model_name(args.embedding_model_name)
    reused_embedding_vector_count = 0
    if args.resume_run_dir:
        _, reused_embedding_vector_count = _resolve_embedding_cache(
            run_dir / "_stage_cache" / "relation_embedding_cache.json"
        )
    elif reuse_embedding_from is not None:
        _, reused_embedding_vector_count = _resolve_embedding_cache(reuse_embedding_from)
    prepared = prepare_runtime_nodes(input_nodes)
    stage_keys = _tail_stage_keys(args.relation_prompt_profile)

    print("Relation-tail preflight passed")
    print(f"  input nodes: {input_nodes}")
    print(f"  source: {source}")
    print(f"  output edge: {output_edge}")
    print(f"  isolated run dir: {run_dir}")
    print(
        "  stage path probe length: "
        f"{len(str(run_dir / '_stage_cache' / 'agent_state' / 'stage_runs' / 'build_relations_rerank' / '20260827T000000_0000000000' / ('input_manifest.json.' + '0' * 32 + '.tmp')))}"
    )
    if args.resume_run_dir:
        print(
            f"  resume existing run: {run_dir} "
            f"({reused_embedding_vector_count} embedding vectors)"
        )
    elif reuse_embedding_from is not None:
        print(
            f"  reuse embedding cache: {reuse_embedding_from} "
            f"({reused_embedding_vector_count} vectors)"
        )
    print(f"  nodes: {len(prepared.stable_nodes)}")
    print(f"  runtime ID remaps: {prepared.changed_id_count}")
    print(f"  stages: {' -> '.join(stage_keys)}")
    print(f"  retrieval mode: {args.relation_retrieval_mode}")
    print(f"  relation prompt: {args.relation_prompt_profile}")
    print(f"  embedding endpoint: {embedding.api_url or 'not configured'}")
    print(f"  embedding model: {embedding_model}")
    print(f"  embedding proxy: {_resolve_embedding_proxy() or 'direct'}")
    if args.dry_run:
        print("Dry run complete; no API request or stage write was performed.")
        return 0

    result = run_relation_tail(
        input_nodes=input_nodes,
        source=source,
        output_edge=output_edge,
        run_dir=run_dir,
        api_url=args.api_url,
        model_name=args.model_name,
        api_key=args.api_key,
        embedding_api_url=embedding.api_url,
        embedding_api_key=embedding.api_key,
        embedding_model_name=embedding_model,
        relation_retrieval_mode=args.relation_retrieval_mode,
        relation_prompt_profile=args.relation_prompt_profile,
        source_format=args.source_format,
        num_threads=args.num_threads,
        checkpoint=args.checkpoint,
        reuse_embedding_from=reuse_embedding_from,
        resume_run=bool(args.resume_run_dir),
    )
    print("Relation-tail pipeline completed successfully")
    print(f"  nodes: {result['node_count']}")
    print(f"  edges: {result['edge_count']}")
    print(f"  reused embedding vectors: {result['reused_embedding_vector_count']}")
    print(f"  output: {result['output_edge']}")
    print(f"  diagnostics: {result['run_dir']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        print(f"Relation-tail run failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

