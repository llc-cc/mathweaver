"""Checkpoint writes remain safe under the pipeline's threaded workload."""

import json

import pytest

from JoinAgent.Multi_Process.multi_process import MultiProcessor


def _processor(tmp_path) -> MultiProcessor:
    processor = MultiProcessor(
        llm=None,
        parse_method=lambda value: value,
        data_template={},
        prompt_template="",
        correction_template="",
        validator=lambda _value: True,
        checkpoint_dir=str(tmp_path),
    )
    processor.process_task = lambda index, _payload, _transform: {"index": index}
    return processor


def test_checkpoint_every_task_is_thread_safe(tmp_path) -> None:
    processor = _processor(tmp_path)
    tasks = {str(index): {"value": index} for index in range(200)}

    result = processor.multitask_perform(tasks, num_threads=16, checkpoint=1)

    assert len(result) == len(tasks)
    checkpoint_results = processor.load_checkpoint()
    assert len(checkpoint_results) == len(tasks)
    for checkpoint_path in (tmp_path / "checkpoint_0.json", tmp_path / "checkpoint_1.json"):
        assert isinstance(json.loads(checkpoint_path.read_text(encoding="utf-8")), dict)
    assert not list(tmp_path.glob("*.tmp.*"))


def test_checkpoint_worker_failure_is_propagated(tmp_path) -> None:
    processor = _processor(tmp_path)
    processor.save_checkpoint = lambda _results: (_ for _ in ()).throw(OSError("disk full"))

    with pytest.raises(RuntimeError, match="worker.*failed"):
        processor.multitask_perform({"0": {}}, num_threads=1, checkpoint=1)
