import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from pipeline.common.node import SOURCE_ENVELOPE_KEY
from scripts import build_relations_from_fixed_nodes as target


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _fixed_nodes() -> list[dict]:
    return [
        {
            "global_id": "stable-definition-id",
            "node_type": "definition",
            "content": "A set C is convex when it contains every segment between its points.",
            "title": {"english": "Convex set"},
        },
        {
            "global_id": "stable-proposition-id",
            "node_type": "proposition",
            "content": "Affine images of convex sets are convex.",
            "source_original_form": "Affine images of convex sets are convex.",
            "title": {"english": "Affine image"},
        },
    ]


def test_round4_preflight_seals_all_runtime_nodes() -> None:
    prepared = target.prepare_runtime_nodes(target.DEFAULT_INPUT_NODES)

    assert len(prepared.stable_nodes) == 90
    assert len(prepared.runtime_nodes) == 90
    assert len(prepared.runtime_to_stable_id) == 90
    assert prepared.changed_id_count == 8
    assert all(isinstance(node.get(SOURCE_ENVELOPE_KEY), dict) for node in prepared.runtime_nodes)
    assert len({node["global_id"] for node in prepared.runtime_nodes}) == 90


def test_relation_tail_starts_at_build_relations_and_publishes_stable_ids() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        input_nodes = root / "node_fixed_round4.json"
        source = root / "source.md"
        output_edge = root / "edge_fixed_round1.json"
        run_dir = root / "run"
        _write_json(input_nodes, _fixed_nodes())
        source.write_text("source", encoding="utf-8")
        observed = {}

        class FakeContext:
            def __init__(self, **kwargs):
                observed["context_kwargs"] = kwargs
                edge_parent = Path(kwargs["output_edge_path"]).parent
                self.stage_cache_dir = str(edge_parent / "_stage_cache")

        def fake_execute(context, state, **kwargs):
            observed["execute_kwargs"] = kwargs
            observed["state"] = state
            runtime_ids = [node["global_id"] for node in state["node_list"]]
            return {
                **state,
                "edge_list": [
                    {
                        "出发节点": runtime_ids[0],
                        "到达节点": runtime_ids[1],
                        "关系": "定义依赖",
                        "child_matches": [
                            {
                                "source_parent_global_id": runtime_ids[0],
                                "target_parent_global_id": runtime_ids[1],
                            }
                        ],
                    }
                ],
            }

        with (
            patch.object(target, "PipelineContext", FakeContext),
            patch.object(target, "execute_fixed_pipeline", side_effect=fake_execute),
        ):
            result = target.run_relation_tail(
                input_nodes=input_nodes,
                source=source,
                output_edge=output_edge,
                run_dir=run_dir,
                api_url="https://example.invalid/v1/chat/completions",
                model_name="test-model",
                api_key="not-used",
                embedding_api_url="https://example.invalid/v1",
                embedding_api_key="not-used",
                embedding_model_name="test-embedding",
                relation_retrieval_mode="hybrid_strict",
                relation_prompt_profile="graph",
                source_format="markdown",
                num_threads=1,
                checkpoint=1,
            )

        written = json.loads(output_edge.read_text(encoding="utf-8"))
        edge = written[0]
        child_match = edge["child_matches"][0]
        assert observed["execute_kwargs"]["start_stage"] == "build_relations"
        assert len(observed["state"]["node_list"]) == 2
        assert all(
            isinstance(node.get(SOURCE_ENVELOPE_KEY), dict)
            for node in observed["state"]["node_list"]
        )
        assert edge["出发节点"] == "stable-definition-id"
        assert edge["到达节点"] == "stable-proposition-id"
        assert child_match["source_parent_global_id"] == "stable-definition-id"
        assert child_match["target_parent_global_id"] == "stable-proposition-id"
        assert result["edge_count"] == 1


def test_embedding_cache_is_copied_into_short_run_directory() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        cache_source = root / "previous" / "_stage_cache" / "relation_embedding_cache.json"
        cache_payload = {"schema_version": 1, "vectors": {"key-1": [1.0, 2.0]}}
        _write_json(cache_source, cache_payload)

        copied_count = target._copy_embedding_cache(root / "previous", root / "new-run")

        copied = json.loads(
            (root / "new-run" / "_stage_cache" / "relation_embedding_cache.json")
            .read_text(encoding="utf-8")
        )
        assert copied_count == 1
        assert copied == cache_payload


def test_long_run_directory_is_rejected_before_stage_creation() -> None:
    long_run_dir = Path("D:/") / ("x" * 220)

    try:
        target._check_stage_path_length(long_run_dir)
    except RuntimeError as exc:
        assert "too long for Windows" in str(exc)
    else:
        raise AssertionError("Expected an overlong stage path to be rejected")


def test_resume_run_reuses_existing_cache_and_enables_task_checkpoints() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        input_nodes = root / "node_fixed_round4.json"
        source = root / "source.md"
        output_edge = root / "edge_fixed_round1.json"
        run_dir = root / "run"
        _write_json(input_nodes, _fixed_nodes())
        source.write_text("source", encoding="utf-8")
        _write_json(
            run_dir / "_stage_cache" / "relation_embedding_cache.json",
            {"schema_version": 1, "vectors": {"key-1": [1.0]}},
        )
        observed = {}

        class FakeContext:
            def __init__(self, **kwargs):
                self.stage_cache_dir = str(
                    Path(kwargs["output_edge_path"]).parent / "_stage_cache"
                )
                self.resume_task_checkpoints = False

        def fake_execute(context, state, **kwargs):
            observed["resume_task_checkpoints"] = context.resume_task_checkpoints
            return {**state, "edge_list": []}

        with (
            patch.object(target, "PipelineContext", FakeContext),
            patch.object(target, "execute_fixed_pipeline", side_effect=fake_execute),
        ):
            result = target.run_relation_tail(
                input_nodes=input_nodes,
                source=source,
                output_edge=output_edge,
                run_dir=run_dir,
                api_url=None,
                model_name=None,
                api_key=None,
                embedding_api_url="https://example.invalid/v1",
                embedding_api_key="not-used",
                embedding_model_name="test-embedding",
                relation_retrieval_mode="hybrid_strict",
                relation_prompt_profile="graph",
                source_format="markdown",
                num_threads=1,
                checkpoint=1,
                resume_run=True,
            )

        assert observed["resume_task_checkpoints"] is True
        assert result["reused_embedding_vector_count"] == 1
        assert json.loads(output_edge.read_text(encoding="utf-8")) == []


def test_failed_pipeline_does_not_publish_output() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        input_nodes = root / "node_fixed_round4.json"
        source = root / "source.md"
        output_edge = root / "edge_fixed_round1.json"
        _write_json(input_nodes, _fixed_nodes())
        source.write_text("source", encoding="utf-8")

        class FakeContext:
            def __init__(self, **kwargs):
                edge_parent = Path(kwargs["output_edge_path"]).parent
                self.stage_cache_dir = str(edge_parent / "_stage_cache")

        with (
            patch.object(target, "PipelineContext", FakeContext),
            patch.object(
                target,
                "execute_fixed_pipeline",
                side_effect=RuntimeError("stage failed"),
            ),
        ):
            try:
                target.run_relation_tail(
                    input_nodes=input_nodes,
                    source=source,
                    output_edge=output_edge,
                    run_dir=root / "run",
                    api_url=None,
                    model_name=None,
                    api_key=None,
                    embedding_api_url="https://example.invalid/v1",
                    embedding_api_key="not-used",
                    embedding_model_name="test-embedding",
                    relation_retrieval_mode="hybrid_strict",
                    relation_prompt_profile="graph",
                    source_format="markdown",
                    num_threads=1,
                    checkpoint=1,
                )
            except RuntimeError as exc:
                assert str(exc) == "stage failed"
            else:
                raise AssertionError("Expected the mocked pipeline to fail")

        assert not output_edge.exists()


if __name__ == "__main__":
    test_round4_preflight_seals_all_runtime_nodes()
    test_relation_tail_starts_at_build_relations_and_publishes_stable_ids()
    test_embedding_cache_is_copied_into_short_run_directory()
    test_long_run_directory_is_rejected_before_stage_creation()
    test_resume_run_reuses_existing_cache_and_enables_task_checkpoints()
    test_failed_pipeline_does_not_publish_output()
    print("build_relations_from_fixed_nodes tests passed")

