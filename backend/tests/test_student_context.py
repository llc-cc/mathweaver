"""学生上下文纯计算回归测试。"""

from student_context import context_preview, node_global_id


def test_legacy_node_identity_is_deterministic() -> None:
    node = {"id": 1, "content": "  convex   set "}
    assert node_global_id(node) == node_global_id(dict(node))


def test_context_preview_groups_open_gaps() -> None:
    preview = context_preview(
        {
            "contextVersion": 2,
            "currentNode": {"nodeId": 1},
            "currentState": {"masteryState": "learning"},
            "directEvidence": [{"kind": "gap", "claim": "缺口"}],
        }
    )
    assert preview["contextVersion"] == 2
    assert preview["openGaps"][0]["claim"] == "缺口"
