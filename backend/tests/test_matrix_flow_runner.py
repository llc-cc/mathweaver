from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matrix_flow.runner import MatrixFlowRunner
from pipeline.common.pipeline_cache import PipelineStageCache
from pipeline.context import PipelineContext


def test_runner_uses_independent_cache_and_mounts_after_final_nodes(tmp_path):
    source_path = tmp_path / "source.md"
    source_path.write_text("# Theorem 1\nflow", encoding="utf-8")
    context = SimpleNamespace(
        file_path=str(source_path),
        stage_cache_dir=str(tmp_path / "_stage_cache"),
        output_dir=str(tmp_path / "_stage_work"),
    )
    source = (
        r"\begin{pmatrix}1&2\\3&4\end{pmatrix}"
        r"\to"
        r"\begin{pmatrix}1&2\\3&4\end{pmatrix}"
    )
    state = {
        "unsplit_statement_dict": {
            0: {
                "source_block_key": 0,
                "source_text": source,
                "pos1": {
                    "global_id": "node-1",
                    "content": source,
                    "source_span": {"start": 0, "end": len(source)},
                },
            }
        }
    }

    runner = MatrixFlowRunner(context)
    report = runner.run(state)
    assert report["status"] == "completed"
    assert report["flow_count"] == 1
    assert (tmp_path / "_matrix_flow" / "manifest.json").is_file()
    assert (tmp_path / "_matrix_flow" / "flows.json").is_file()

    final_state = {
        "node_list": [{"global_id": "node-1"}],
        "pipeline_warnings": [],
        "matrix_flow_report": report,
    }
    runner.mount(final_state)

    assert len(final_state["node_list"][0]["matrix_flows"]) == 1
    assert final_state["node_list"][0]["matrix_flows"][0]["owner"]["global_id"] == "node-1"
    mounted_manifest = __import__("json").loads(runner.manifest_path.read_text(encoding="utf-8"))
    assert mounted_manifest["stages"]["mount_final_nodes"] == {
        "status": "completed",
        "mounted_flow_count": 1,
        "unmounted_flow_count": 0,
    }

    reused_state = dict(state)
    reused = runner.run(reused_state)
    assert reused["reused"] is True


def test_runner_calculates_offsets_against_the_displayed_statement(tmp_path):
    source_path = tmp_path / "source.tex"
    source_path.write_text("source", encoding="utf-8")
    context = SimpleNamespace(
        file_path=str(source_path),
        stage_cache_dir=str(tmp_path / "_stage_cache"),
        output_dir=str(tmp_path / "_stage_work"),
        source_format="tex",
    )
    matrix_flow = (
        r"\begin{pmatrix}1&0\\0&1\end{pmatrix}"
        r"\to"
        r"\begin{pmatrix}0&1\\1&0\end{pmatrix}"
    )
    displayed = f"Statement\n{' ' * 24}\n{matrix_flow}"
    raw_source = f"\\begin{{example}}\n% MATRIX-FLOW\nStatement\n{matrix_flow}\n\\end{{example}}"
    state = {
        "unsplit_statement_dict": {
            0: {
                "source_block_key": 0,
                "source_text": raw_source,
                "pos1": {
                    "global_id": "node-1",
                    "content": displayed,
                    "source_span": {"start": 0, "end": len(raw_source)},
                    "source_file": str(source_path),
                    "tex_env_name": "example",
                },
            }
        }
    }

    runner = MatrixFlowRunner(context)
    report = runner.run(state)
    flows = __import__("json").loads(runner.flows_path.read_text(encoding="utf-8"))

    assert report["flow_count"] == 1
    flow = flows[0]
    expected_start = displayed.index(r"\begin{pmatrix}")
    assert flow["owner"]["source_span"]["start"] == expected_start
    assert flow["owner"]["source_excerpt"] == matrix_flow


def test_runner_aggregates_statement_and_proof_bindings_by_global_id(tmp_path):
    source_path = tmp_path / "source.md"
    source_path.write_text("source", encoding="utf-8")
    context = SimpleNamespace(
        file_path=str(source_path),
        stage_cache_dir=str(tmp_path / "_stage_cache"),
        output_dir=str(tmp_path / "_stage_work"),
        source_origin="ocr",
    )
    statement = r"A=\begin{pmatrix}1&0\\0&1\end{pmatrix}。"
    proof = r"证明中再次使用 $A$。"
    state = {
        "unsplit_statement_dict": {
            0: {
                "source_block_key": 0,
                "source_text": statement + proof,
                "pos1": {
                    "global_id": "node-1",
                    "content": statement,
                    "proof": proof,
                    "source_span": {"start": 0, "end": len(statement + proof)},
                },
            }
        }
    }

    runner = MatrixFlowRunner(context)
    report = runner.run(state)
    flows = __import__("json").loads(runner.flows_path.read_text(encoding="utf-8"))

    assert report["status"] == "completed"
    assert report["counts"]["named_matrix"] == 1
    assert flows[0]["source"]["kind"] == "ocr"
    assert [item["field"] for item in flows[0]["bindings"][0]["references"]] == ["proof"]


def test_runner_writes_rejected_candidates_only_to_review_artifact(tmp_path):
    source_path = tmp_path / "source.md"
    source_path.write_text("source", encoding="utf-8")
    context = SimpleNamespace(
        file_path=str(source_path),
        stage_cache_dir=str(tmp_path / "_stage_cache"),
        output_dir=str(tmp_path / "_stage_work"),
        source_origin="ocr",
    )
    source = r"A=\begin{array}{cc}1&2\\3\end{array}"
    state = {
        "unsplit_statement_dict": {
            0: {
                "source_block_key": 0,
                "source_text": source,
                "pos1": {"global_id": "node-1", "content": source},
            }
        }
    }

    runner = MatrixFlowRunner(context)
    report = runner.run(state)
    flows = __import__("json").loads(runner.flows_path.read_text(encoding="utf-8"))
    review = __import__("json").loads((runner.root / "review_artifact.json").read_text(encoding="utf-8"))

    assert report["flow_count"] == 0
    assert flows == []
    assert review["rejected_candidates"][0]["reason"] == "non_rectangular_matrix"


def test_source_origin_participates_in_sidecar_cache_key(tmp_path):
    source_path = tmp_path / "source.md"
    source_path.write_text("source", encoding="utf-8")
    source = r"A=\begin{pmatrix}1\end{pmatrix}"
    state = {
        "unsplit_statement_dict": {
            0: {
                "source_block_key": 0,
                "source_text": source,
                "pos1": {"global_id": "node-1", "content": source},
            }
        }
    }
    markdown = MatrixFlowRunner(SimpleNamespace(
        file_path=str(source_path),
        stage_cache_dir=str(tmp_path / "_stage_cache"),
        output_dir=str(tmp_path / "_stage_work"),
        source_origin="markdown",
    )).run(state)
    ocr = MatrixFlowRunner(SimpleNamespace(
        file_path=str(source_path),
        stage_cache_dir=str(tmp_path / "_stage_cache"),
        output_dir=str(tmp_path / "_stage_work"),
        source_origin="ocr",
    )).run(state)

    assert markdown["cache_key"] != ocr["cache_key"]
    assert ocr["reused"] is False


def test_runner_aggregates_fields_from_distinct_wrappers_by_global_id(tmp_path):
    source_path = tmp_path / "source.md"
    source_path.write_text("source", encoding="utf-8")
    context = SimpleNamespace(
        file_path=str(source_path),
        stage_cache_dir=str(tmp_path / "_stage_cache"),
        output_dir=str(tmp_path / "_stage_work"),
    )
    statement = r"A=\begin{pmatrix}1\end{pmatrix}。"
    proof = r"证明使用 $A$。"
    state = {
        "unsplit_statement_dict": {
            0: {"source_block_key": "statement", "source_text": statement, "pos1": {"global_id": "node-1", "content": statement}},
            1: {"source_block_key": "proof", "source_text": proof, "pos1": {"global_id": "node-1", "proof": proof}},
        }
    }

    runner = MatrixFlowRunner(context)
    runner.run(state)
    flows = __import__("json").loads(runner.flows_path.read_text(encoding="utf-8"))

    assert len(flows) == 1
    assert [item["field"] for item in flows[0]["bindings"][0]["references"]] == ["proof"]


def test_pipeline_context_and_stage_cache_preserve_source_origin(tmp_path):
    source_path = tmp_path / "source.md"
    source_path.write_text("source", encoding="utf-8")
    context = PipelineContext(
        file_path=str(source_path),
        output_node_path=str(tmp_path / "nodes.json"),
        source_origin="ocr",
        cache_policy="minimal",
        llm=object(),
        parser=object(),
        divider=object(),
    )

    manifest = PipelineStageCache(context, ())._new_manifest()

    assert context.source_origin == "ocr"
    assert manifest["source"]["origin"] == "ocr"
