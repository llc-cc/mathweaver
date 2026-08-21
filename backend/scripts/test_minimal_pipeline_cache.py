import json
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from pipeline import orchestrator
from pipeline.common.pipeline_cache import (
    PipelineCacheError,
    decode_cache_value,
)
from pipeline.common.llm_task import _ACTIVE_RELOAD
from pipeline.common.stage_recovery import (
    new_stage_run,
    run_recoverable_task,
    write_input_manifest,
)
from pipeline.context import PipelineContext
from JoinAgent.Multi_Process.multi_process import MultiProcessor


def _context(root):
    source = Path(root) / "input.md"
    source.write_text("# Demo\n\nStatement.", encoding="utf-8")
    return PipelineContext(
        file_path=str(source),
        output_node_path=str(Path(root) / "nodes.json"),
        output_edge_path=str(Path(root) / "edges.json"),
        cache_policy="minimal",
        llm=object(),
        parser=object(),
        divider=object(),
    )


def _plan(calls=None):
    calls = calls if calls is not None else []

    def first(_context, state):
        calls.append("first")
        state["alpha"] = {1: {"value": "one"}}
        state["obsolete"] = {"remove": True}
        return state

    def second(_context, state):
        calls.append("second")
        state["alpha"][1]["value"] = "updated"
        state["beta"] = ["done"]
        state.pop("obsolete")
        return state

    return (
        orchestrator.FixedStage(
            "first",
            "First",
            first,
            produces=("alpha", "obsolete"),
            nonempty=("alpha",),
        ),
        orchestrator.FixedStage(
            "second",
            "Second",
            second,
            requires=("alpha",),
            consumes=("obsolete",),
            produces=("alpha", "beta"),
            nonempty=("alpha", "beta"),
        ),
    )


def test_minimal_cache_writes_stage_deltas_and_round_trips_keys():
    with tempfile.TemporaryDirectory() as tmp:
        context = _context(tmp)
        plan = _plan()
        with patch.object(orchestrator, "build_fixed_stage_plan", return_value=plan):
            state = orchestrator.execute_fixed_pipeline(context)

        assert state == {"alpha": {1: {"value": "updated"}}, "beta": ["done"]}
        cache = Path(tmp) / "_stage_cache"
        relative_files = {
            path.relative_to(cache).as_posix()
            for path in cache.rglob("*")
            if path.is_file()
        }
        assert relative_files == {
            "manifest.json",
            "stages/01_first/input.json",
            "stages/01_first/output.json",
            "stages/02_second/input.json",
            "stages/02_second/output.json",
        }
        assert not (Path(tmp) / "_stage_work").exists()

        manifest = json.loads((cache / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "done"
        manifest_text = json.dumps(manifest)
        assert "api_key" not in manifest_text
        assert "secret" not in manifest_text

        second_input = json.loads(
            (cache / "stages/02_second/input.json").read_text(encoding="utf-8")
        )
        decoded_input = decode_cache_value(second_input["values"])
        assert decoded_input["alpha"] == {1: {"value": "one"}}
        assert decoded_input["obsolete"] == {"remove": True}

        second_output = json.loads(
            (cache / "stages/02_second/output.json").read_text(encoding="utf-8")
        )
        decoded_output = decode_cache_value(second_output["values"])
        assert decoded_output == {
            "alpha": {1: {"value": "updated"}},
            "beta": ["done"],
        }
        assert second_output["removed_keys"] == ["obsolete"]


def test_resume_uses_valid_output_even_if_manifest_update_was_interrupted():
    with tempfile.TemporaryDirectory() as tmp:
        context = _context(tmp)
        with patch.object(orchestrator, "build_fixed_stage_plan", return_value=_plan()):
            orchestrator.execute_fixed_pipeline(context)

        manifest_path = Path(tmp) / "_stage_cache" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["completed_stages"] = ["first"]
        manifest["current_stage"] = "second"
        manifest["status"] = "running"
        manifest["stages"]["second"].pop("output_sha256", None)
        manifest["stages"]["second"]["status"] = "running"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        calls = []
        resumed_context = _context(tmp)
        with patch.object(
            orchestrator,
            "build_fixed_stage_plan",
            return_value=_plan(calls),
        ):
            state = orchestrator.execute_fixed_pipeline(
                resumed_context,
                resume_from_cache=True,
            )

        assert calls == []
        assert state == {"alpha": {1: {"value": "updated"}}, "beta": ["done"]}
        repaired = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert repaired["completed_stages"] == ["first", "second"]
        assert repaired["stages"]["second"]["status"] == "completed"


def test_resume_runs_only_the_first_incomplete_stage():
    with tempfile.TemporaryDirectory() as tmp:
        context = _context(tmp)
        with patch.object(orchestrator, "build_fixed_stage_plan", return_value=_plan()):
            orchestrator.execute_fixed_pipeline(context)

        cache = Path(tmp) / "_stage_cache"
        (cache / "stages/02_second/output.json").unlink()
        manifest_path = cache / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["completed_stages"] = ["first"]
        manifest["current_stage"] = "second"
        manifest["status"] = "paused"
        manifest["stages"]["second"].pop("output_sha256", None)
        manifest["stages"]["second"]["status"] = "running"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        calls = []
        resumed_context = _context(tmp)
        with patch.object(
            orchestrator,
            "build_fixed_stage_plan",
            return_value=_plan(calls),
        ):
            state = orchestrator.execute_fixed_pipeline(
                resumed_context,
                resume_from_cache=True,
            )

        assert calls == ["second"]
        assert state == {"alpha": {1: {"value": "updated"}}, "beta": ["done"]}


def test_resume_rejects_corrupted_stage_output():
    with tempfile.TemporaryDirectory() as tmp:
        context = _context(tmp)
        plan = _plan()
        with patch.object(orchestrator, "build_fixed_stage_plan", return_value=plan):
            orchestrator.execute_fixed_pipeline(context)

        output_path = Path(tmp) / "_stage_cache" / "stages/02_second/output.json"
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        payload["values"]["beta"] = ["tampered"]
        output_path.write_text(json.dumps(payload), encoding="utf-8")

        resumed_context = _context(tmp)
        with patch.object(orchestrator, "build_fixed_stage_plan", return_value=plan):
            try:
                orchestrator.execute_fixed_pipeline(
                    resumed_context,
                    resume_from_cache=True,
                )
            except PipelineCacheError as exc:
                assert "hash is invalid" in str(exc)
            else:
                raise AssertionError("A corrupted stage output must not be resumed")


def test_resume_rejects_non_contiguous_stage_outputs():
    with tempfile.TemporaryDirectory() as tmp:
        context = _context(tmp)
        plan = _plan()
        with patch.object(orchestrator, "build_fixed_stage_plan", return_value=plan):
            orchestrator.execute_fixed_pipeline(context)

        first_output = Path(tmp) / "_stage_cache" / "stages" / "01_first" / "output.json"
        first_output.unlink()
        resumed_context = _context(tmp)
        with patch.object(orchestrator, "build_fixed_stage_plan", return_value=plan):
            try:
                orchestrator.execute_fixed_pipeline(
                    resumed_context,
                    resume_from_cache=True,
                )
            except PipelineCacheError as exc:
                assert "non-contiguous" in str(exc)
            else:
                raise AssertionError("Non-contiguous stage outputs must not be resumed")


def test_recoverable_task_reuses_interrupted_matching_run():
    with tempfile.TemporaryDirectory() as tmp:
        context = SimpleNamespace(
            cache_policy="minimal",
            checkpoint_root=str(Path(tmp) / "checkpoints"),
            current_stage_key="demo_stage",
            resume_task_checkpoints=True,
        )
        input_dict = {"0": {"pos1": "alpha"}, "1": {"pos1": "beta"}}
        run_id, run_dir = new_stage_run(context, "demo_tasks")
        write_input_manifest(run_dir, run_id, "demo_tasks", input_dict)
        checkpoint_dir = run_dir / "checkpoint"
        checkpoint_dir.mkdir()
        (checkpoint_dir / "checkpoint_0.json").write_text(
            json.dumps({"0": {"value": "cached"}}),
            encoding="utf-8",
        )
        (checkpoint_dir / "checkpoint_1.json").write_text("{}", encoding="utf-8")

        calls = []

        def runner(tasks, reused_checkpoint_dir):
            calls.append((list(tasks), Path(reused_checkpoint_dir), _ACTIVE_RELOAD.get()))
            return {
                "0": {"value": "cached"},
                "1": {"value": "new"},
            }

        partial, report, selected_run_dir = run_recoverable_task(
            context,
            stage_name="demo_tasks",
            input_dict=input_dict,
            task_runner=runner,
        )

        assert selected_run_dir == run_dir
        assert calls == [(["0", "1"], checkpoint_dir, True)]
        assert partial == {
            "0": {"value": "cached"},
            "1": {"value": "new"},
        }
        assert report["status"] == "resolved"


def test_multiprocess_active_reload_skips_checkpointed_keys():
    with tempfile.TemporaryDirectory() as tmp:
        processor = MultiProcessor(
            llm=object(),
            parse_method=lambda value: value,
            data_template="",
            prompt_template="",
            correction_template="",
            validator=lambda value: True,
            checkpoint_dir=tmp,
        )
        processor.save_checkpoint({"0": {"value": "cached"}})
        calls = []

        def process_task(index, _payload, _transform):
            calls.append(index)
            return {"value": "new"}

        processor.process_task = process_task
        result = processor.multitask_perform(
            {
                0: {"pos1": "alpha"},
                1: {"pos1": "beta"},
            },
            num_threads=1,
            checkpoint=1,
            Active_Reload=True,
            checkpoint_dir=tmp,
        )

        assert calls == ["1"]
        assert result == {
            "0": {"value": "cached"},
            "1": {"value": "new"},
        }


if __name__ == "__main__":
    test_minimal_cache_writes_stage_deltas_and_round_trips_keys()
    test_resume_uses_valid_output_even_if_manifest_update_was_interrupted()
    test_resume_runs_only_the_first_incomplete_stage()
    test_resume_rejects_corrupted_stage_output()
    test_resume_rejects_non_contiguous_stage_outputs()
    test_recoverable_task_reuses_interrupted_matching_run()
    test_multiprocess_active_reload_skips_checkpointed_keys()
    print("minimal pipeline cache tests passed")
