from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.common.node import (
    compute_global_id_from_source,
    merge_node_with_source_envelope,
)
from pipeline.stages.finalize_output.stage import (
    FINAL_OUTPUT_TRANSIENT_NODE_KEYS,
    run as run_finalize_output,
)


def _node_with_process_fields():
    node = {
        "node_type": "definition",
        "content": "A group is a set equipped with a group operation.",
        "title": {"english": "Group"},
        "logic_form_rendered": "Definition(Group)",
        "analysis_layer": {"gap_analysis": {"logic_gaps": []}},
        "formalization_guidance": {"primary_statement": "A group is ..."},
        "logic_ast_local": {"kind": "pred", "pred_id": "P_GROUP"},
        "repair_log": {"applied_repairs": []},
        "repair_status": "not_needed",
        "reference_signals": {"explicit_targets": []},
        "subnode_specs": [{"index": 1}],
        "surface_anchor": {"title_text": "Group"},
        "sub_nodes": [
            {
                "index": 1,
                "content": "The operation is associative.",
                "logic_form_rendered": "Associative(op)",
                "analysis_status": "completed",
                "logic_ast_local": {"kind": "pred", "pred_id": "P_ASSOC"},
                "repair_suggestion": {"repair_notes": []},
            }
        ],
    }
    node, _ = merge_node_with_source_envelope(
        node,
        {},
        stage_name="extract_statements",
        allowed_fields=(),
        seal=True,
        source_metadata={"source_text": node["content"]},
    )
    node["global_id"] = compute_global_id_from_source(node)
    return node


def test_finalize_output_removes_process_fields_from_nodes_and_subnodes():
    node = _node_with_process_fields()

    with tempfile.TemporaryDirectory() as temp_dir:
        output_path = Path(temp_dir) / "nodes.json"
        context = SimpleNamespace(
            output_dir=temp_dir,
            output_node_path=str(output_path),
            output_edge_path=None,
        )
        with redirect_stdout(io.StringIO()):
            state = run_finalize_output(context, {"node_list": [node], "edge_list": []})
        written_nodes = json.loads(output_path.read_text(encoding="utf-8"))

    final_node = state["node_dict"][0]
    final_subnode = final_node["sub_nodes"][0]

    assert written_nodes == state["node_list"]
    assert not FINAL_OUTPUT_TRANSIENT_NODE_KEYS.intersection(final_node)
    assert not FINAL_OUTPUT_TRANSIENT_NODE_KEYS.intersection(final_subnode)
    assert final_node["content"] == "A group is a set equipped with a group operation."
    assert final_node["logic_form_rendered"] == "Definition(Group)"
    assert final_subnode["logic_form_rendered"] == "Associative(op)"


if __name__ == "__main__":
    test_finalize_output_removes_process_fields_from_nodes_and_subnodes()
    print("finalize_output cleanup tests passed")
