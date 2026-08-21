import json
import shutil
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.common.io import read_json
from pipeline.stages.extract_logic_tuples import stage as extract_logic_tuples_stage
from pipeline.stages.generate_titles import stage as generate_titles_stage


TMP_ROOT = Path(__file__).resolve().parent / "_tmp_latex_unfreeze_stage_shift"


def _make_temp_dir():
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    temp_dir = TMP_ROOT / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


def _build_context(tmp_path: Path):
    return SimpleNamespace(
        llm=object(),
        parser=SimpleNamespace(parse_dict=lambda value: value),
        num_threads=1,
        checkpoint=False,
        checkpoint_root=str(tmp_path / "checkpoint"),
        output_dir=str(tmp_path),
        output_natural_node_path=None,
    )


def _build_state():
    return {
        "statement_without_title_dict": {0: {"pos1": "unused"}},
    }


def _build_split_outputs():
    return (
        {
            0: {
                "_orig_key": 0,
                "node_type": "definition",
                "title": {"chinese": "Subset", "english": "Subset"},
                "content": r"A \subseteq B",
                "proof": "",
                "label": "Def 1",
            }
        },
        {
            1: {
                "pos1": {
                    "_orig_key": 1,
                    "node_type": "theorem",
                    "title": {"chinese": "Limit", "english": "Limit"},
                    "content": r"f \to g",
                    "proof": "",
                    "label": "Thm 1",
                },
                "_orig_key": 1,
            }
        },
    )


def test_generate_titles_preserves_latex_without_mapping():
    tmp_path = _make_temp_dir()
    try:
        context = _build_context(tmp_path)
        state = _build_state()

        with patch.object(
            generate_titles_stage,
            "run_multiprocess_task",
            return_value={"stub": "value"},
        ), patch.object(
            generate_titles_stage,
            "split_statement_with_title_dict",
            return_value=_build_split_outputs(),
        ):
            result = generate_titles_stage.run(context, state)

        definition_node = result["definition_axiom_dict"][0]
        structured_node = result["structured_input_dict"][1]["pos1"]

        assert definition_node["content"] == r"A \subseteq B"
        assert structured_node["content"] == r"f \to g"

        definition_json = read_json(str(tmp_path / "definition_axiom_dict.json"))
        structured_json = read_json(str(tmp_path / "structured_input_dict.json"))

        assert definition_json["0"]["content"] == r"A \subseteq B"
        assert structured_json["1"]["pos1"]["content"] == r"f \to g"
        assert not (tmp_path / "merged_mapping.json").exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_extract_logic_tuples_uses_direct_latex_content_without_disambiguation():
    tmp_path = _make_temp_dir()
    try:
        context = _build_context(tmp_path)
        state = _build_state()

        with patch.object(
            generate_titles_stage,
            "run_multiprocess_task",
            return_value={"stub": "value"},
        ), patch.object(
            generate_titles_stage,
            "split_statement_with_title_dict",
            return_value=_build_split_outputs(),
        ):
            state = generate_titles_stage.run(context, state)

        def _fake_extract_logic_task(**kwargs):
            logic_tuple_input_dict = kwargs["index_dict"]
            structured_node = logic_tuple_input_dict[1]["pos1"]
            assert structured_node["original_form"] == r"f \to g"
            return {
                1: {
                    0: {
                        "node_type": structured_node["node_type"],
                        "title": structured_node["title"],
                        "statement_form": "other",
                        "remark": {"original_form": structured_node["original_form"]},
                        "subject": [],
                        "context": [],
                        "variables": [],
                        "conditions": [],
                        "conclusions": [],
                        "proof": structured_node.get("proof", ""),
                        "label": structured_node["label"],
                    }
                }
            }

        with patch.object(
            extract_logic_tuples_stage,
            "run_multiprocess_task",
            side_effect=_fake_extract_logic_task,
        ):
            state = extract_logic_tuples_stage.run(context, state)

        serialized_node_dict = json.dumps(state["node_dict"], ensure_ascii=False)
        node_dict_json = read_json(str(tmp_path / "node_dict.json"))

        assert "@@" not in serialized_node_dict
        assert r"\subseteq" in serialized_node_dict
        assert r"\to" in serialized_node_dict
        assert "@@" not in json.dumps(node_dict_json, ensure_ascii=False)
        assert not (tmp_path / "merged_mapping.json").exists()
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    test_generate_titles_preserves_latex_without_mapping()
    test_extract_logic_tuples_uses_direct_latex_content_without_disambiguation()
    print("latex direct-preservation stage tests passed")
