import os
import sys
from pathlib import Path
from pprint import pprint
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.stages.extract_references import stage


def main():
    node_dict = {
        0: {
            "node_type": "定义",
            "label": "定义 1",
            "title": "向量空间",
            "content": "设 V 是…称为向量空间。",
            "_reorder_id": 0,
        },
        1: {
            "node_type": "定理",
            "label": "定理 2",
            "title": "基扩张",
            "content": "V 的任意线性无关组可扩张为基。",
            "_reorder_id": 1,
        },
        2: {
            "node_type": "推论",
            "label": "推论 3",
            "title": "",
            "content": "根据定义1和定理 2，由 Cauchy 不等式，上述定理给出...",
            "_reorder_id": 2,
        },
        3: {
            "node_type": "定理",
            "label": "定理 4",
            "title": "",
            "content": "由式(2)及 Theorem 2 知...",
            "_reorder_id": 3,
        },
        4: {
            "node_type": "命题",
            "label": "命题 5",
            "title": "",
            "content": "不存在任何引用信号的孤立节点。",
            "_reorder_id": 4,
        },
    }

    output_dir = Path("/tmp/ref_test")
    os.makedirs(output_dir, exist_ok=True)
    ctx = SimpleNamespace(output_dir=str(output_dir))
    state = stage.run(ctx, {"node_dict": node_dict})

    for idx, node in enumerate(state["node_list"]):
        signals = node["reference_signals"]
        print(f"--- node {idx} ({node.get('label')}) ---")
        print("explicit:")
        pprint([(hit["surface"], hit["match_mode"], hit["resolved_index"]) for hit in signals["explicit_targets"]])
        print("relative:")
        pprint([(hit["surface"], hit["match_mode"], hit["resolved_index"]) for hit in signals["relative_references"]])
        print("formula:")
        pprint([hit["surface"] for hit in signals["formula_references"]])
        print("named:")
        pprint([hit["surface"] for hit in signals["named_references"]])
        print("flags:")
        pprint(signals["repair_flags"])


if __name__ == "__main__":
    main()
