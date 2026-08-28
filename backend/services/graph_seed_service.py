"""正式图谱数据包的加载与只读预检。"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from pipeline.common.node import (
    compute_global_id_from_source,
    get_node_source_original_text,
    normalize_source_text_for_id,
)


MANIFEST_NAME = "manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _has_title(node: dict[str, Any]) -> bool:
    title = node.get("title")
    if isinstance(title, str):
        return bool(title.strip())
    if isinstance(title, dict):
        return any(
            isinstance(title.get(key), str) and title[key].strip()
            for key in ("chinese", "english")
        )
    return bool(str(node.get("label") or "").strip())


def load_graph_dataset(dataset_path: str | Path) -> dict[str, Any]:
    """按清单角色加载数据；调用方只能获得解析后的副本。"""
    root = Path(dataset_path)
    manifest = _read_json(root / MANIFEST_NAME)
    by_role = {item["role"]: item for item in manifest.get("files") or []}
    required = {"markdown", "nodes", "edges"}
    if set(by_role) != required:
        raise ValueError("manifest must declare markdown, nodes and edges exactly once")
    return {
        "root": root,
        "manifest": manifest,
        "markdown": (root / by_role["markdown"]["filename"]).read_text(
            encoding="utf-8"
        ),
        "nodes": _read_json(root / by_role["nodes"]["filename"]),
        "edges": _read_json(root / by_role["edges"]["filename"]),
    }


def validate_graph_dataset(
    dataset_path: str | Path, *, verify_hashes: bool = True
) -> dict[str, Any]:
    """验证正式数据包，发现旧版差异时仅报告 warning，绝不修订源文件。"""
    root = Path(dataset_path)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    hashes: dict[str, str] = {}
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        return {
            "valid": False,
            "datasetKey": None,
            "counts": {"nodes": 0, "edges": 0},
            "hashes": {},
            "errors": [{"code": "manifest_missing", "path": str(manifest_path)}],
            "warnings": [],
        }

    try:
        manifest = _read_json(manifest_path)
        files = manifest.get("files") or []
        by_role = {item["role"]: item for item in files}
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return {
            "valid": False,
            "datasetKey": None,
            "counts": {"nodes": 0, "edges": 0},
            "hashes": {},
            "errors": [{"code": "manifest_invalid", "message": str(exc)}],
            "warnings": [],
        }

    if len(files) != 3 or set(by_role) != {"markdown", "nodes", "edges"}:
        errors.append({"code": "manifest_file_roles_invalid"})

    paths: dict[str, Path] = {}
    for item in files:
        filename = str(item.get("filename") or "")
        role = str(item.get("role") or "")
        path = root / filename
        paths[role] = path
        if not path.is_file():
            errors.append({"code": "dataset_file_missing", "filename": filename})
            continue
        actual_hash = _sha256(path)
        hashes[filename] = actual_hash
        if verify_hashes and actual_hash != str(item.get("sha256") or "").lower():
            errors.append({
                "code": "hash_mismatch",
                "filename": filename,
                "expected": item.get("sha256"),
                "actual": actual_hash,
            })

    nodes: list[Any] = []
    edges: list[Any] = []
    markdown = ""
    if all(role in paths and paths[role].is_file() for role in ("markdown", "nodes", "edges")):
        try:
            markdown = paths["markdown"].read_text(encoding="utf-8")
            raw_nodes = _read_json(paths["nodes"])
            raw_edges = _read_json(paths["edges"])
            if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
                raise TypeError("nodes and edges must be JSON arrays")
            nodes, edges = raw_nodes, raw_edges
        except (OSError, UnicodeError, TypeError, json.JSONDecodeError) as exc:
            errors.append({"code": "dataset_parse_failed", "message": str(exc)})

    expected = manifest.get("expected") or {}
    if len(nodes) != expected.get("nodeCount"):
        errors.append({
            "code": "node_count_mismatch",
            "expected": expected.get("nodeCount"),
            "actual": len(nodes),
        })
    if len(edges) != expected.get("edgeCount"):
        errors.append({
            "code": "edge_count_mismatch",
            "expected": expected.get("edgeCount"),
            "actual": len(edges),
        })

    node_ids: list[str] = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append({"code": "node_not_object", "index": index})
            continue
        global_id = str(node.get("global_id") or "").strip()
        if not global_id:
            errors.append({"code": "node_id_missing", "index": index})
        else:
            node_ids.append(global_id)
        if not _has_title(node):
            errors.append({"code": "node_title_empty", "index": index})
        if not str(node.get("content") or "").strip():
            errors.append({"code": "node_content_empty", "index": index})

    duplicates = sorted(
        item for item, count in Counter(node_ids).items() if count > 1
    )
    for global_id in duplicates:
        errors.append({"code": "duplicate_node_id", "globalId": global_id})

    known_ids = set(node_ids)
    relation_counts: Counter[str] = Counter()
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            errors.append({"code": "edge_not_object", "index": index})
            continue
        source = str(edge.get("出发节点") or "").strip()
        target = str(edge.get("到达节点") or "").strip()
        missing = [endpoint for endpoint in (source, target) if endpoint not in known_ids]
        if missing:
            errors.append({
                "code": "missing_edge_endpoint",
                "index": index,
                "endpoints": missing,
            })
        reason = str(edge.get("理由") or "").strip()
        if not reason:
            errors.append({"code": "edge_reason_empty", "index": index})
        relation = str(edge.get("关系") or "").strip()
        if not relation:
            errors.append({"code": "edge_relation_empty", "index": index})
        else:
            relation_counts[relation] += 1

    legacy_mismatches: list[int] = []
    mapped_to_source = 0
    normalized_markdown = normalize_source_text_for_id(markdown)
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        try:
            expected_id = compute_global_id_from_source(node)
        except ValueError:
            continue
        if str(node.get("global_id") or "") != expected_id:
            legacy_mismatches.append(index)
        normalized_source = normalize_source_text_for_id(
            get_node_source_original_text(node)
        )
        if normalized_source and normalized_source in normalized_markdown:
            mapped_to_source += 1

    if legacy_mismatches:
        warnings.append({
            "code": "legacy_global_id_mismatch",
            "count": len(legacy_mismatches),
            "nodeIndexes": legacy_mismatches,
        })
    if mapped_to_source != len(nodes):
        warnings.append({
            "code": "source_mapping_coverage",
            "mapped": mapped_to_source,
            "total": len(nodes),
        })
    warnings.append({
        "code": "relation_distribution",
        "counts": dict(sorted(relation_counts.items())),
    })

    # 已知差异的数量本身也受清单约束，防止交付文件被静默替换。
    known_expectations = (
        ("legacy_warning_count_mismatch", len(legacy_mismatches), "legacyGlobalIdMismatchCount"),
        ("source_mapping_count_mismatch", mapped_to_source, "continuousSourceMappingCount"),
        (
            "definition_dependency_count_mismatch",
            relation_counts.get("定义依赖", 0),
            "definitionDependencyCount",
        ),
    )
    for code, actual, key in known_expectations:
        if actual != expected.get(key):
            errors.append({
                "code": code,
                "expected": expected.get(key),
                "actual": actual,
            })

    return {
        "valid": not errors,
        "datasetKey": manifest.get("datasetKey"),
        "counts": {"nodes": len(nodes), "edges": len(edges)},
        "hashes": hashes,
        "errors": errors,
        "warnings": warnings,
    }
