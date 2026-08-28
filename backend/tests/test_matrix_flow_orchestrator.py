from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import orchestrator


def test_fixed_plan_stays_at_fourteen_stages_and_ready_hook_precedes_commit_callback():
    assert len(orchestrator.build_fixed_stage_plan()) == 14

    events = []

    def first(_context, state):
        state["a"] = 1
        return state

    def second(_context, state):
        state["b"] = state["a"] + 1
        return state

    plan = (
        orchestrator.FixedStage("first", "first", first, produces=("a",), nonempty=("a",)),
        orchestrator.FixedStage("second", "second", second, requires=("a",), produces=("b",), nonempty=("b",)),
    )
    context = SimpleNamespace(
        cache_policy="legacy",
        current_stage_key=None,
        resume_task_checkpoints=False,
        output_dir=None,
    )

    with patch.object(orchestrator, "build_fixed_stage_plan", return_value=plan):
        result = orchestrator.execute_fixed_pipeline(
            context,
            on_stage_ready=lambda stage, *_args: events.append(f"ready:{stage.key}"),
            on_stage_complete=lambda stage, *_args: events.append(f"complete:{stage.key}"),
        )

    assert result["b"] == 2
    assert events == ["ready:first", "complete:first", "ready:second", "complete:second"]
