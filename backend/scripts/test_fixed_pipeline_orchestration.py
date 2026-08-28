from contextlib import ExitStack
import io
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline import orchestrator
import extractor
from pipeline.common.pipeline_cache import PipelineCacheError, PipelineStageCache
from pipeline.context import PipelineContext


EXPECTED_ORDER = [
    "correct_text",
    "segment_blocks",
    "extract_statements",
    "ensure_coverage",
    "clean_nodes",
    "split_nodes",
    "generate_titles",
    "extract_logic_tuples",
    "analysis",
    "repair",
    "extract_references",
    "repair_lite",
    "build_relations",
    "finalize_output",
]

EXPERIMENTAL_ORDER = [
    *EXPECTED_ORDER[:-2],
    "compile_logic_form",
    "normalize_predicates",
    *EXPECTED_ORDER[-2:],
]

EXPECTED_LABELS = [
    "原文内容校正",
    "文档结构识别",
    "数学知识提取",
    "遗漏知识补全",
    "无效内容清理",
    "复合知识拆分",
    "知识标题生成",
    "知识要素结构化",
    "语义信息补充",
    "知识结构修复",
    "文内引用识别",
    "引用结果校正",
    "知识关系提取",
    "图谱结果生成",
]


STAGE_MODULES = {
    "correct_text": orchestrator.correct_text_stage,
    "segment_blocks": orchestrator.segment_blocks_stage,
    "extract_statements": orchestrator.extract_statements_stage,
    "ensure_coverage": orchestrator.ensure_coverage_stage,
    "clean_nodes": orchestrator.clean_nodes_stage,
    "split_nodes": orchestrator.split_nodes_stage,
    "generate_titles": orchestrator.generate_titles_stage,
    "extract_logic_tuples": orchestrator.extract_logic_tuples_stage,
    "analysis": orchestrator.analysis_stage,
    "repair": orchestrator.repair_stage,
    "extract_references": orchestrator.extract_references_stage,
    "repair_lite": orchestrator.repair_lite_stage,
    "compile_logic_form": orchestrator.compile_logic_form_stage,
    "normalize_predicates": orchestrator.normalize_predicates_stage,
    "build_relations": orchestrator.build_relations_stage,
    "finalize_output": orchestrator.finalize_output_stage,
}


OUTPUTS = {
    "correct_text": {"chopped_text_dict": {"0": {}}, "corrected_text": "Theorem 1.1.", "correct_text_report": {}},
    "segment_blocks": {"problem_dict": {0: {}}, "segment_blocks_report": {}},
    "extract_statements": {"unsplit_statement_dict": {0: {}}},
    "ensure_coverage": {"unsplit_statement_dict": {0: {}}, "ensure_coverage_report": {"candidate_count": 0}},
    "clean_nodes": {"unsplit_statement_dict": {0: {}}, "node_cleaning_report": {"keep_count": 1}},
    "split_nodes": {"statement_without_title_dict": {0: {}}},
    "generate_titles": {"structured_input_dict": {0: {}}, "definition_axiom_dict": {}},
    "extract_logic_tuples": {"node_dict": {0: {}}, "node_list": [{}]},
    "analysis": {"node_dict": {0: {}}, "node_list": [{}], "analysis_stage_run": {"status": "resolved"}},
    "repair": {"node_dict": {0: {}}, "node_list": [{}], "repair_stage_run": {"status": "resolved"}},
    "extract_references": {"node_dict": {0: {}}, "node_list": [{}]},
    "repair_lite": {"node_dict": {0: {}}, "node_list": [{}]},
    "compile_logic_form": {"node_dict": {0: {}}, "node_list": [{}], "logic_form_local_dict": {}},
    "normalize_predicates": {"node_dict": {0: {}}, "node_list": [{}]},
    "build_relations": {"edge_list": []},
    "finalize_output": {"node_list": [{}], "edge_list": []},
}


def _mark_manifest_as_legacy_sixteen(context, *, status="paused"):
    manifest_path = Path(context.stage_cache_dir) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    legacy_cache = PipelineStageCache(
        context,
        orchestrator._legacy_sixteen_stage_plan(),
        options=manifest["options"],
    )
    manifest["plan_sha256"] = legacy_cache.plan_sha256
    manifest["status"] = status
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest_path, manifest


def test_fixed_stage_plan_defaults_to_fourteen_stages_and_keeps_experimental_plan():
    assert [key for key, _ in orchestrator.FIXED_STAGE_DEFS] == EXPECTED_ORDER
    assert [label for _, label in orchestrator.FIXED_STAGE_DEFS] == EXPECTED_LABELS
    assert [stage.key for stage in orchestrator.build_fixed_stage_plan()] == EXPECTED_ORDER
    experimental = orchestrator.build_fixed_stage_plan(experimental_logic_ir=True)
    assert [stage.key for stage in experimental] == EXPERIMENTAL_ORDER
    assert experimental[-4].label == "实验旁路：谓词树生成"
    assert experimental[-3].label == "实验旁路：谓词归一化"


def test_api_and_process_md_share_the_fixed_plan_and_ignore_analysis_flag():
    import api_v2

    assert api_v2.STAGE_DEFS == list(orchestrator.FIXED_STAGE_DEFS)
    assert len(api_v2.STAGE_DEFS) == 14

    captured = {}

    def fake_execute(context, **kwargs):
        captured["context"] = context
        captured["kwargs"] = kwargs
        return {"node_list": [], "edge_list": []}

    with tempfile.TemporaryDirectory() as tmp:
        source_path = Path(tmp) / "input.md"
        source_path.write_text("# Theorem 1.1\nStatement.", encoding="utf-8")
        with patch.object(orchestrator, "execute_fixed_pipeline", fake_execute):
            nodes, edges = orchestrator.process_md(str(source_path), enable_analysis=False)

    assert nodes == []
    assert edges == []
    assert captured["context"].enable_analysis is True
    assert captured["kwargs"]["edge_output_mode"] == "structured"
    assert captured["kwargs"]["experimental_logic_ir"] is False


def test_public_process_md_wrapper_forwards_experimental_logic_ir():
    with patch.object(extractor, "_process_md", return_value=([], [])) as wrapped:
        assert extractor.process_md(
            "input.md",
            experimental_logic_ir=True,
        ) == ([], [])

    assert wrapped.call_args.kwargs["experimental_logic_ir"] is True


def test_api_progress_uses_all_fourteen_default_stages():
    import api_v2

    captured = {}

    def fake_execute(_context, *, on_stage_start, on_stage_complete, **_kwargs):
        captured["context"] = _context
        state = {}
        plan = orchestrator.build_fixed_stage_plan()
        for index, stage in enumerate(plan):
            on_stage_start(stage, index, len(plan), state)
            on_stage_complete(stage, index, len(plan), state)
        return {"node_list": [], "edge_list": []}

    job_id = "fixed-plan-test"
    with tempfile.TemporaryDirectory() as tmp:
        source_path = Path(tmp) / "input.md"
        source_path.write_text("# Theorem 1.1\nStatement.", encoding="utf-8")
        api_v2._jobs[job_id] = {"stages_done": [], "status": "running"}
        try:
            with patch.object(api_v2, "execute_fixed_pipeline", fake_execute):
                api_v2._run_pipeline(
                    job_id,
                    str(source_path),
                    {
                        "api_url": "https://example.test/v1",
                        "model_name": "test",
                        "api_key": "test",
                        "embedding_url": "https://embedding.example.test/v1",
                        "embedding_model": "embedding-test",
                        "embedding_api_key": "embedding-key",
                    },
                    enable_analysis=False,
                )
            job = api_v2._jobs[job_id]
            assert job["status"] == "done"
            assert job["total_stages"] == 14
            assert job["stage_index"] == 13
            assert job["stages_done"] == EXPECTED_ORDER
            assert captured["context"].embedding_api_url == "https://embedding.example.test/v1"
            assert captured["context"].embedding_model_name == "embedding-test"
            assert captured["context"].embedding_api_key == "embedding-key"
            assert job["result"]["warnings"] == []
            assert job["result"]["quality_summary"] == {
                "status": "ok",
                "degraded_stage_count": 0,
                "degraded_node_count": 0,
                "ignored_protected_field_count": 0,
            }
        finally:
            api_v2._jobs.pop(job_id, None)


def test_api_result_exposes_degraded_quality_summary():
    import api_v2

    def fake_execute(_context, **_kwargs):
        return {
            "node_list": [],
            "edge_list": [],
            "pipeline_warnings": ["extract_logic_tuples retained one source node."],
            "quality_summary": {
                "status": "degraded",
                "degraded_stage_count": 1,
                "degraded_node_count": 1,
                "ignored_protected_field_count": 0,
            },
        }

    job_id = "degraded-quality-test"
    with tempfile.TemporaryDirectory() as tmp:
        source_path = Path(tmp) / "input.md"
        source_path.write_text("# Remark\nSource text.", encoding="utf-8")
        api_v2._jobs[job_id] = {"stages_done": [], "status": "running"}
        try:
            with patch.object(api_v2, "execute_fixed_pipeline", fake_execute):
                api_v2._run_pipeline(
                    job_id,
                    str(source_path),
                    {
                        "api_url": "https://example.test/v1",
                        "model_name": "test",
                        "api_key": "test",
                        "embedding_url": "https://embedding.example.test/v1",
                        "embedding_model": "embedding-test",
                        "embedding_api_key": "embedding-key",
                    },
                    enable_analysis=False,
                )
            result = api_v2._jobs[job_id]["result"]
            assert result["warnings"] == [
                "extract_logic_tuples retained one source node."
            ]
            assert result["quality_summary"]["status"] == "degraded"
            assert result["quality_summary"]["degraded_node_count"] == 1
        finally:
            api_v2._jobs.pop(job_id, None)


def test_api_upload_preserves_windows_newlines_in_temporary_source():
    import api_v2

    captured = {}

    def deferred_start(job_id, *, resume):
        captured["job_id"] = job_id
        captured["resume"] = resume

    source = "# Heading\r\n\r\nTheorem 1.1. Statement.\r\n"
    with patch.object(api_v2, "_start_pipeline_attempt", side_effect=deferred_start):
        response = api_v2.app.test_client().post(
            "/api/v2/jobs",
            data={
                "file": (io.BytesIO(source.encode("utf-8")), "input.md"),
                "api_url": "https://example.test/v1",
                "model_name": "test",
                "api_key": "test",
                "embedding_model": "embedding-test",
            },
            content_type="multipart/form-data",
        )

    assert response.status_code == 202, response.get_json()
    job_id = response.get_json()["job_id"]
    source_path = Path(api_v2._jobs[job_id]["_md_path"])
    try:
        assert source_path.read_bytes() == source.encode("utf-8")
        submitted_config = api_v2._jobs[job_id]["_llm_config"]
        assert submitted_config["embedding_url"] == "https://example.test/v1"
        assert submitted_config["embedding_model"] == "embedding-test"
        assert submitted_config["embedding_api_key"] == "test"
        assert captured == {"job_id": job_id, "resume": False}
    finally:
        api_v2._jobs.pop(job_id, None)
        shutil.rmtree(source_path.parent)


def test_api_upload_requires_embedding_model():
    import api_v2

    response = api_v2.app.test_client().post(
        "/api/v2/jobs",
        json={
            "text": "# Heading\n\nStatement.",
            "filename": "input.md",
            "api_url": "https://example.test/v1",
            "model_name": "test",
            "api_key": "test",
        },
    )

    assert response.status_code == 400
    assert "embedding_model" in response.get_json()["error"]


def test_api_upload_preserves_explicit_embedding_config():
    import api_v2

    captured = {}

    def deferred_start(job_id, *, resume):
        captured["job_id"] = job_id
        captured["resume"] = resume

    with patch.object(api_v2, "_start_pipeline_attempt", side_effect=deferred_start):
        response = api_v2.app.test_client().post(
            "/api/v2/jobs",
            json={
                "text": "# Heading\n\nStatement.",
                "filename": "input.md",
                "api_url": "https://example.test/v1",
                "model_name": "test",
                "api_key": "test",
                "embedding_url": "https://embedding.example.test/v1",
                "embedding_model": "embedding-test",
                "embedding_api_key": "embedding-key",
            },
        )

    assert response.status_code == 202, response.get_json()
    job_id = response.get_json()["job_id"]
    source_path = Path(api_v2._jobs[job_id]["_md_path"])
    try:
        submitted_config = api_v2._jobs[job_id]["_llm_config"]
        assert submitted_config["embedding_url"] == "https://embedding.example.test/v1"
        assert submitted_config["embedding_model"] == "embedding-test"
        assert submitted_config["embedding_api_key"] == "embedding-key"
        assert api_v2._jobs[job_id]["_experimental_logic_ir"] is False
        assert api_v2._jobs[job_id]["total_stages"] == 14
        assert captured == {"job_id": job_id, "resume": False}
    finally:
        api_v2._jobs.pop(job_id, None)
        shutil.rmtree(source_path.parent)


def test_api_upload_can_enable_experimental_logic_ir_plan():
    import api_v2

    with patch.object(api_v2, "_start_pipeline_attempt"):
        response = api_v2.app.test_client().post(
            "/api/v2/jobs",
            json={
                "text": "# Heading\n\nStatement.",
                "filename": "input.md",
                "api_url": "https://example.test/v1",
                "model_name": "test",
                "api_key": "test",
                "embedding_model": "embedding-test",
                "experimental_logic_ir": True,
            },
        )

    assert response.status_code == 202, response.get_json()
    job_id = response.get_json()["job_id"]
    source_path = Path(api_v2._jobs[job_id]["_md_path"])
    try:
        job = api_v2._jobs[job_id]
        assert job["_experimental_logic_ir"] is True
        assert job["total_stages"] == 16
        assert [key for key, _ in job["_stage_defs"]] == EXPERIMENTAL_ORDER
    finally:
        api_v2._jobs.pop(job_id, None)
        shutil.rmtree(source_path.parent)


def test_executor_runs_analysis_and_repair_even_when_context_flag_is_false():
    calls = []
    relation_node_sources = []

    def fake_runner(stage_key):
        def run(_context, state, **_kwargs):
            calls.append(stage_key)
            state.update(OUTPUTS[stage_key])
            if stage_key == "repair_lite":
                state["node_dict"] = {0: {"source": "repair_lite"}}
                state["node_list"] = list(state["node_dict"].values())
            if stage_key == "build_relations":
                relation_node_sources.extend(
                    node.get("source") for node in state["node_list"]
                )
            return state

        return run

    with ExitStack() as stack:
        for key, module in STAGE_MODULES.items():
            stack.enter_context(patch.object(module, "run", fake_runner(key)))
        context = SimpleNamespace(enable_analysis=False, output_edge_path=None)
        state = orchestrator.execute_fixed_pipeline(context)

    assert calls == EXPECTED_ORDER
    assert relation_node_sources == ["repair_lite"]
    assert state["analysis_stage_run"]["status"] == "resolved"
    assert state["repair_stage_run"]["status"] == "resolved"


def test_executor_experimental_plan_feeds_predicate_evidence_to_relations():
    calls = []
    relation_predicates = []

    def fake_runner(stage_key):
        def run(_context, state, **_kwargs):
            calls.append(stage_key)
            state.update(OUTPUTS[stage_key])
            if stage_key == "normalize_predicates":
                state["predicate_entry_list"] = [{"pred_id": "P"}]
            if stage_key == "build_relations":
                relation_predicates.extend(state.get("predicate_entry_list") or [])
            return state

        return run

    with ExitStack() as stack:
        for key, module in STAGE_MODULES.items():
            stack.enter_context(patch.object(module, "run", fake_runner(key)))
        context = SimpleNamespace(enable_analysis=True, output_edge_path=None)
        orchestrator.execute_fixed_pipeline(
            context,
            experimental_logic_ir=True,
        )

    assert calls == EXPERIMENTAL_ORDER
    assert relation_predicates == [{"pred_id": "P"}]


def test_default_resume_migrates_legacy_sixteen_stage_cache():
    calls = []
    relation_predicates = []

    def fake_runner(stage_key):
        def run(_context, state, **_kwargs):
            calls.append(stage_key)
            state.update(OUTPUTS[stage_key])
            if stage_key == "normalize_predicates":
                state["predicate_entry_list"] = [{"pred_id": "legacy"}]
            if stage_key == "build_relations":
                relation_predicates.extend(state.get("predicate_entry_list") or [])
            return state

        return run

    with tempfile.TemporaryDirectory() as tmp:
        source_path = Path(tmp) / "input.md"
        source_path.write_text("# Theorem 1.1\nStatement.", encoding="utf-8")
        context = PipelineContext(
            file_path=str(source_path),
            output_node_path=str(Path(tmp) / "nodes.json"),
            output_edge_path=str(Path(tmp) / "edges.json"),
            cache_policy="minimal",
            llm=object(),
            parser=object(),
            divider=object(),
        )
        with ExitStack() as stack:
            for key, module in STAGE_MODULES.items():
                stack.enter_context(patch.object(module, "run", fake_runner(key)))
            orchestrator.execute_fixed_pipeline(
                context,
                experimental_logic_ir=True,
            )
            manifest_path, legacy_manifest = _mark_manifest_as_legacy_sixteen(
                context
            )
            legacy_manifest["current_stage"] = "build_relations"
            legacy_manifest["completed_stages"] = EXPERIMENTAL_ORDER[:-2]
            manifest_path.write_text(
                json.dumps(legacy_manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            calls.clear()
            relation_predicates.clear()
            state = orchestrator.execute_fixed_pipeline(
                context,
                resume_from_cache=True,
            )

        cache = Path(context.stage_cache_dir)
        manifest = json.loads((cache / "manifest.json").read_text(encoding="utf-8"))
        assert calls == ["build_relations", "finalize_output"]
        assert relation_predicates == []
        assert state["edge_list"] == []
        assert (cache / "stages" / "13_compile_logic_form" / "output.json").is_file()
        assert (cache / "stages" / "14_normalize_predicates" / "output.json").is_file()
        assert (cache / "stages" / "13_build_relations" / "output.json").is_file()
        assert manifest["migration_history"][-1]["reason"] == (
            "logic_ir_stages_moved_to_experimental_sidecar"
        )
        assert manifest["migration_history"][-1]["invalidated_stages"] == [
            "compile_logic_form",
            "normalize_predicates",
            "build_relations",
            "finalize_output",
        ]


def test_default_resume_rejects_corrupted_legacy_shared_prefix():
    def fake_runner(stage_key):
        def run(_context, state, **_kwargs):
            state.update(OUTPUTS[stage_key])
            return state

        return run

    with tempfile.TemporaryDirectory() as tmp:
        source_path = Path(tmp) / "input.md"
        source_path.write_text("# Theorem 1.1\nStatement.", encoding="utf-8")
        context = PipelineContext(
            file_path=str(source_path),
            output_node_path=str(Path(tmp) / "nodes.json"),
            output_edge_path=str(Path(tmp) / "edges.json"),
            cache_policy="minimal",
            llm=object(),
            parser=object(),
            divider=object(),
        )
        with ExitStack() as stack:
            for key, module in STAGE_MODULES.items():
                stack.enter_context(patch.object(module, "run", fake_runner(key)))
            orchestrator.execute_fixed_pipeline(
                context,
                experimental_logic_ir=True,
            )
            _mark_manifest_as_legacy_sixteen(context)
            repair_lite_output = (
                Path(context.stage_cache_dir)
                / "stages"
                / "12_repair_lite"
                / "output.json"
            )
            damaged = json.loads(repair_lite_output.read_text(encoding="utf-8"))
            damaged["stage"] = "damaged_repair_lite"
            repair_lite_output.write_text(
                json.dumps(damaged, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            try:
                orchestrator.execute_fixed_pipeline(
                    context,
                    resume_from_cache=True,
                )
            except PipelineCacheError as exc:
                assert "repair_lite" in str(exc)
            else:
                raise AssertionError("A damaged legacy shared prefix must be rejected")


def test_executor_can_resume_from_named_stage():
    calls = []

    def fake_runner(stage_key):
        def run(_context, state, **_kwargs):
            calls.append(stage_key)
            state.update(OUTPUTS[stage_key])
            return state

        return run

    with ExitStack() as stack:
        for key, module in STAGE_MODULES.items():
            stack.enter_context(patch.object(module, "run", fake_runner(key)))
        context = SimpleNamespace(enable_analysis=True, output_edge_path=None)
        state = orchestrator.execute_fixed_pipeline(
            context,
            dict(OUTPUTS["analysis"]),
            start_stage="repair",
        )

    assert calls == EXPECTED_ORDER[EXPECTED_ORDER.index("repair"):]
    assert state["repair_stage_run"]["status"] == "resolved"
    assert "edge_list" in state


def test_executor_rejects_unknown_resume_stage():
    try:
        orchestrator.execute_fixed_pipeline(
            SimpleNamespace(),
            {},
            start_stage="missing_stage",
        )
    except ValueError as exc:
        assert "missing_stage" in str(exc)
    else:
        raise AssertionError("Unknown resume stage should fail before execution")


if __name__ == "__main__":
    test_fixed_stage_plan_defaults_to_fourteen_stages_and_keeps_experimental_plan()
    test_api_and_process_md_share_the_fixed_plan_and_ignore_analysis_flag()
    test_public_process_md_wrapper_forwards_experimental_logic_ir()
    test_api_progress_uses_all_fourteen_default_stages()
    test_api_result_exposes_degraded_quality_summary()
    test_api_upload_preserves_windows_newlines_in_temporary_source()
    test_api_upload_requires_embedding_model()
    test_api_upload_preserves_explicit_embedding_config()
    test_api_upload_can_enable_experimental_logic_ir_plan()
    test_executor_runs_analysis_and_repair_even_when_context_flag_is_false()
    test_executor_experimental_plan_feeds_predicate_evidence_to_relations()
    test_default_resume_migrates_legacy_sixteen_stage_cache()
    test_default_resume_rejects_corrupted_legacy_shared_prefix()
    test_executor_can_resume_from_named_stage()
    test_executor_rejects_unknown_resume_stage()
    print("fixed pipeline orchestration tests passed")
