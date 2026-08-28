"""正式凸优化图谱数据包的只读预检测试。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from services.graph_seed_service import validate_graph_dataset


DATASET = Path(__file__).resolve().parents[1] / "seeds" / "convex_optimization"
EXPECTED_HASHES = {
    "bv_cvxbook_1.1-2.3.md": "4f0ee13b9410e5751431a10e1607880caaefcd59150d6fa7b0b89d1a27dcd908",
    "node_fixed_round4.json": "a0b6a63e86faf0b80af481d4920c4bd9832f4cfe96d669088495385351dc51b6",
    "edge_fixed_round1.json": "3993d48cccf5b9b9cf97b5749e533f4178ffb6301c2abcdda3f91cfa687edf30",
}


def _mutable_dataset(tmp_path: Path) -> Path:
    target = tmp_path / "dataset"
    shutil.copytree(DATASET, target)
    return target


def test_official_dataset_matches_manifest_hashes() -> None:
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))

    assert {
        item["filename"]: item["sha256"] for item in manifest["files"]
    } == EXPECTED_HASHES
    assert validate_graph_dataset(DATASET)["hashes"] == EXPECTED_HASHES


def test_validator_reports_90_nodes_and_226_edges() -> None:
    report = validate_graph_dataset(DATASET)

    assert report["valid"] is True
    assert report["errors"] == []
    assert report["counts"] == {"nodes": 90, "edges": 226}


def test_validator_rejects_duplicate_node_ids(tmp_path: Path) -> None:
    dataset = _mutable_dataset(tmp_path)
    nodes_path = dataset / "node_fixed_round4.json"
    nodes = json.loads(nodes_path.read_text(encoding="utf-8"))
    nodes[1]["global_id"] = nodes[0]["global_id"]
    nodes_path.write_text(json.dumps(nodes, ensure_ascii=False), encoding="utf-8")

    report = validate_graph_dataset(dataset, verify_hashes=False)

    assert report["valid"] is False
    assert any(item["code"] == "duplicate_node_id" for item in report["errors"])


def test_validator_rejects_missing_edge_endpoint(tmp_path: Path) -> None:
    dataset = _mutable_dataset(tmp_path)
    edges_path = dataset / "edge_fixed_round1.json"
    edges = json.loads(edges_path.read_text(encoding="utf-8"))
    edges[0]["到达节点"] = "missing-node"
    edges_path.write_text(json.dumps(edges, ensure_ascii=False), encoding="utf-8")

    report = validate_graph_dataset(dataset, verify_hashes=False)

    assert report["valid"] is False
    assert any(item["code"] == "missing_edge_endpoint" for item in report["errors"])


def test_validator_warns_about_legacy_global_ids_without_mutating_data() -> None:
    nodes_path = DATASET / "node_fixed_round4.json"
    before = nodes_path.read_bytes()

    report = validate_graph_dataset(DATASET)

    legacy_warning = next(
        item for item in report["warnings"] if item["code"] == "legacy_global_id_mismatch"
    )
    assert legacy_warning["count"] == 8
    assert next(
        item for item in report["warnings"] if item["code"] == "source_mapping_coverage"
    )["mapped"] == 27
    assert nodes_path.read_bytes() == before
