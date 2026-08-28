"""正式图谱数据包的加载与只读预检。"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from sqlalchemy import select

from pipeline.common.node import (
    compute_global_id_from_source,
    get_node_source_original_text,
    normalize_source_text_for_id,
)
from storage.database import session_scope
from storage.models import (
    AuditLog,
    ClassMembership,
    Course,
    EducationNodeIdentity,
    EducationNodeOccurrence,
    EducationSnapshot,
    History,
    TeachingClass,
    User,
)


MANIFEST_NAME = "manifest.json"
_IMPORT_NAMESPACE = uuid.UUID("9970df66-1f1d-4867-a677-360b60219962")


class GraphSeedValidationError(RuntimeError):
    """数据包未通过预检，导入事务尚未开始。"""


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


def _stable_id(*parts: object) -> str:
    return uuid.uuid5(_IMPORT_NAMESPACE, ":".join(str(part) for part in parts)).hex


def _identity_id(public_class_id: str, global_id: str) -> str:
    return _stable_id("identity", public_class_id, global_id)


def _project_dataset(nodes: list[dict], edges: list[dict]) -> tuple[list[dict], list[dict]]:
    """生成 Web 可用的数字节点键，同时保留负责人交付字段。"""
    projected_nodes: list[dict] = []
    id_by_global: dict[str, int] = {}
    for node_id, raw in enumerate(nodes):
        node = deepcopy(raw)
        title = node.get("title") if isinstance(node.get("title"), dict) else {}
        node["id"] = node_id
        node["title_zh"] = str(title.get("chinese") or title.get("english") or "")
        node["title_en"] = str(title.get("english") or title.get("chinese") or "")
        node["source_statement"] = get_node_source_original_text(node)
        projected_nodes.append(node)
        id_by_global[str(node["global_id"])] = node_id

    projected_edges: list[dict] = []
    for raw in edges:
        edge = deepcopy(raw)
        edge["from"] = id_by_global[str(edge["出发节点"])]
        edge["to"] = id_by_global[str(edge["到达节点"])]
        edge["label"] = str(edge.get("关系") or "")
        edge["description"] = str(edge.get("理由") or "")
        projected_edges.append(edge)
    return projected_nodes, projected_edges


def import_graph_dataset(
    dataset_path: str | Path,
    teacher_email: str,
    class_title: str,
) -> dict[str, Any]:
    """在一个短事务内幂等创建课程、图谱历史、快照与节点映射。"""
    normalized_email = teacher_email.strip().lower()
    normalized_title = class_title.strip()
    if not normalized_email or not normalized_title:
        raise ValueError("teacher email and class title are required")

    report = validate_graph_dataset(dataset_path)
    if not report["valid"]:
        raise GraphSeedValidationError("graph dataset validation failed")
    loaded = load_graph_dataset(dataset_path)
    manifest = loaded["manifest"]
    dataset_key = str(manifest["datasetKey"])
    nodes, edges = _project_dataset(loaded["nodes"], loaded["edges"])

    with session_scope() as session:
        teacher = session.scalar(select(User).where(User.email == normalized_email))
        if teacher is None or teacher.role not in {"teacher", "admin"} or not teacher.is_active:
            raise LookupError("active teacher account not found")

        course_code = f"SEED-{_stable_id('course', dataset_key)[:16].upper()}"
        course = session.scalar(select(Course).where(Course.code == course_code))
        if course is None:
            course = Course(
                code=course_code,
                name=str(manifest.get("title") or normalized_title)[:255],
                description=f"Official dataset: {dataset_key}",
            )
            session.add(course)
            session.flush()

        public_class_id = _stable_id(
            "class", dataset_key, teacher.id, normalized_title
        )
        teaching_class = session.scalar(
            select(TeachingClass).where(TeachingClass.public_id == public_class_id)
        )
        if teaching_class is None:
            teaching_class = TeachingClass(
                public_id=public_class_id,
                course_id=course.id,
                teacher_id=teacher.id,
                name=normalized_title[:255],
                invite_code=_stable_id("invite", public_class_id)[:8].upper(),
            )
            session.add(teaching_class)
            session.flush()
        elif teaching_class.teacher_id != teacher.id:
            raise RuntimeError("deterministic class ownership conflict")

        membership = session.scalar(
            select(ClassMembership).where(
                ClassMembership.teaching_class_id == teaching_class.id,
                ClassMembership.student_id == teacher.id,
            )
        )
        if membership is None:
            session.add(
                ClassMembership(
                    teaching_class_id=teaching_class.id,
                    student_id=teacher.id,
                    role="teacher",
                )
            )
        elif membership.role != "teacher":
            raise RuntimeError("teacher membership role conflict")

        history_id = _stable_id("history", dataset_key, teacher.id)
        history = session.get(History, history_id)
        if history is None:
            history = History(
                id=history_id,
                user_id=teacher.id,
                filename=next(
                    item["filename"]
                    for item in manifest["files"]
                    if item["role"] == "markdown"
                ),
                node_count=len(nodes),
                edge_count=len(edges),
                nodes_json=deepcopy(nodes),
                edges_json=deepcopy(edges),
                source_markdown=loaded["markdown"],
                latex_macros="{}",
                source_pdf_json=None,
                status="done",
                stage="complete",
                stage_label="正式图谱已导入",
                stage_index=1,
                total_stages=1,
                stages_done_json=["complete"],
                source_format="markdown",
                source_origin="official_seed",
                experimental_logic_ir=False,
            )
            session.add(history)

        snapshot_id = _stable_id(
            "snapshot", dataset_key, teacher.id, public_class_id
        )
        snapshot = session.get(EducationSnapshot, snapshot_id)
        if snapshot is None:
            snapshot = EducationSnapshot(
                id=snapshot_id,
                teaching_class_id=teaching_class.id,
                source_graph_id=history_id,
                filename=history.filename,
                nodes_json=deepcopy(nodes),
                edges_json=deepcopy(edges),
                source_markdown=loaded["markdown"],
                latex_macros_json={},
                source_pdf_json=None,
                created_by=teacher.id,
            )
            session.add(snapshot)
            session.flush()

        for node in nodes:
            global_id = str(node["global_id"])
            canonical_id = _identity_id(public_class_id, global_id)
            identity = session.get(EducationNodeIdentity, canonical_id)
            if identity is None:
                identity = EducationNodeIdentity(
                    id=canonical_id,
                    teaching_class_id=teaching_class.id,
                    global_id=global_id,
                    title=str(node.get("title_zh") or node.get("title_en") or "")[:512],
                )
                session.add(identity)
            elif (
                identity.teaching_class_id != teaching_class.id
                or identity.global_id != global_id
            ):
                raise ValueError("deterministic node identity conflict")

            occurrence = session.get(
                EducationNodeOccurrence, (snapshot_id, int(node["id"]))
            )
            if occurrence is None:
                session.add(
                    EducationNodeOccurrence(
                        snapshot_id=snapshot_id,
                        node_id=int(node["id"]),
                        canonical_node_id=canonical_id,
                        global_id=global_id,
                    )
                )
            elif occurrence.canonical_node_id != canonical_id:
                raise ValueError("snapshot occurrence identity conflict")

        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.actor_id == teacher.id,
                AuditLog.action == "graph_seed.imported",
                AuditLog.subject_type == "education_snapshot",
                AuditLog.subject_id == snapshot_id,
            )
        )
        if audit is None:
            session.add(
                AuditLog(
                    actor_id=teacher.id,
                    action="graph_seed.imported",
                    subject_type="education_snapshot",
                    subject_id=snapshot_id,
                    details={
                        "datasetKey": dataset_key,
                        "historyId": history_id,
                        "classId": public_class_id,
                        "nodeCount": len(nodes),
                        "edgeCount": len(edges),
                        "hashes": report["hashes"],
                    },
                )
            )

    return {
        "ok": True,
        "datasetKey": dataset_key,
        "historyId": history_id,
        "sourceGraphId": history_id,
        "snapshotId": snapshot_id,
        "classId": public_class_id,
        "nodeCount": len(nodes),
        "edgeCount": len(edges),
        "warningCount": len(report["warnings"]),
    }
