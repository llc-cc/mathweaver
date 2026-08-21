import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import resume_extractor_from_embedding as resume


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def test_load_embedding_resume_state_uses_predicate_normalized_cache():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = Path(tmpdir) / "_stage_cache"
        _write(cache / "node_dict_after_predicate_normalization.json", {"0": {"global_id": "node-1"}})
        _write(cache / "predicate_entry_list.json", [{"predicate_id": "p1"}])
        _write(
            cache / "relation_retrieval_report.json",
            {
                "status": "embedding_failed",
                "publishable": False,
                "embedding": {"requested": 3, "failed": 3},
            },
        )
        _write(cache / "relation_embedding_cache.json", {"schema_version": 1, "vectors": {"a": [1.0]}})

        state, facts = resume.load_embedding_resume_state(cache)

    assert list(state["node_dict"]) == ["0"]
    assert state["predicate_entry_list"] == [{"predicate_id": "p1"}]
    assert facts["cached_vector_count"] == 1
    assert facts["failed_embedding_count"] == 3


def test_embedding_resume_starts_at_build_relations():
    observed = {}

    def fake_execute(context, state, **kwargs):
        observed["context"] = context
        observed["state"] = state
        observed["kwargs"] = kwargs
        return {**state, "edge_list": []}

    state = {"node_dict": {"0": {}}, "node_list": [{}], "predicate_entry_list": []}
    with patch.object(resume, "execute_fixed_pipeline", side_effect=fake_execute):
        result = resume.resume_embedding_and_downstream(SimpleNamespace(), state)

    assert observed["kwargs"]["start_stage"] == "build_relations"
    assert result["edge_list"] == []


if __name__ == "__main__":
    test_load_embedding_resume_state_uses_predicate_normalized_cache()
    test_embedding_resume_starts_at_build_relations()
    print("embedding resume tests passed")
