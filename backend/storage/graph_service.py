from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import DatabaseConnection, connect_database
try:
    from integrations.neo4j_handler import GraphStoreError, get_graph_store, graph_checksum
except ModuleNotFoundError:
    from backend.integrations.neo4j_handler import GraphStoreError, get_graph_store, graph_checksum


class GraphUnavailableError(RuntimeError):
    def __init__(self, graph_id: str, status: str, message: str = ""):
        super().__init__(message or f"graph {graph_id} is {status}")
        self.graph_id = graph_id
        self.status = status


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _staging_root() -> Path:
    data_root = Path(os.environ.get("MATHGRAPH_DATA_DIR", str(Path(__file__).parents[1]))).expanduser()
    root = data_root / "graph-staging"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_graph_id(graph_id: str) -> str:
    safe = "".join(char if char.isalnum() or char in "_.-" else "_" for char in graph_id)
    if not safe or safe in {".", ".."}:
        raise ValueError("invalid graph id")
    return safe


def _stage_payload(graph_id: str, revision: int, kind: str, nodes: list[dict], edges: list[dict]) -> Path:
    target_dir = _staging_root() / _safe_graph_id(graph_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{revision}.json"
    payload = {"graph_id": graph_id, "kind": kind, "revision": revision, "nodes": nodes, "edges": edges}
    fd, temporary = tempfile.mkstemp(prefix=f".{revision}-", suffix=".tmp", dir=target_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target


def _remove_staging(path_value: str | None) -> None:
    if not path_value:
        return
    path = Path(path_value).resolve()
    root = _staging_root().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("refusing to remove graph staging outside the data root") from exc
    if path.is_file():
        path.unlink()
    parent = path.parent
    if parent != root and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()


def register_graph_pending(
    db: DatabaseConnection,
    *,
    graph_id: str,
    kind: str,
    nodes: list[dict],
    edges: list[dict],
) -> dict[str, Any]:
    row = db.execute(
        "SELECT revision, staging_path FROM graph_registry WHERE graph_id = ? FOR UPDATE",
        (graph_id,),
    ).fetchone()
    revision = int(row["revision"] or 0) + 1 if row else 1
    checksum = graph_checksum(nodes, edges)
    now = _utcnow()
    db.execute(
        """INSERT INTO graph_registry
             (graph_id, graph_kind, storage_status, revision, node_count, edge_count,
              content_sha256, staging_path, last_error, created_at, updated_at)
           VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, NULL, ?, ?)
           ON DUPLICATE KEY UPDATE
             graph_kind = VALUES(graph_kind), storage_status = 'pending',
             revision = VALUES(revision), node_count = VALUES(node_count),
             edge_count = VALUES(edge_count), content_sha256 = VALUES(content_sha256),
             staging_path = VALUES(staging_path), last_error = NULL,
             updated_at = VALUES(updated_at)""",
        (
            graph_id,
            kind,
            revision,
            len(nodes),
            len(edges),
            checksum,
            None,
            now,
            now,
        ),
    )
    return {
        "graph_id": graph_id,
        "kind": kind,
        "revision": revision,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "checksum": checksum,
        "staging_path": None,
        "previous_staging_path": row["staging_path"] if row else None,
    }


def finalize_graph(record: dict[str, Any]) -> dict[str, Any]:
    graph_id = record["graph_id"]
    try:
        payload = json.loads(Path(record["staging_path"]).read_text(encoding="utf-8"))
        result = get_graph_store().replace_graph(
            graph_id,
            record["kind"],
            payload["nodes"],
            payload["edges"],
            revision=int(record["revision"]),
        )
        if result["checksum"] != record["checksum"]:
            raise GraphStoreError("Neo4j checksum does not match staged payload")
        with connect_database() as db:
            updated = db.execute(
                """UPDATE graph_registry
                      SET storage_status = 'ready', staging_path = NULL, last_error = NULL,
                          node_count = ?, edge_count = ?, content_sha256 = ?, updated_at = ?
                    WHERE graph_id = ? AND revision = ? AND storage_status = 'pending'""",
                (
                    result["node_count"],
                    result["edge_count"],
                    result["checksum"],
                    _utcnow(),
                    graph_id,
                    record["revision"],
                ),
            )
            if updated.rowcount != 1:
                raise GraphStoreError("graph registry revision changed while finalizing")
        _remove_staging(record.get("staging_path"))
        return result
    except Exception as exc:
        with connect_database() as db:
            db.execute(
                """UPDATE graph_registry SET storage_status = 'failed', last_error = ?, updated_at = ?
                    WHERE graph_id = ? AND revision = ?""",
                (str(exc), _utcnow(), graph_id, record["revision"]),
            )
        raise


def persist_graph(graph_id: str, kind: str, nodes: list[dict], edges: list[dict]) -> dict[str, Any]:
    with connect_database() as db:
        record = register_graph_pending(
            db,
            graph_id=graph_id,
            kind=kind,
            nodes=nodes,
            edges=edges,
        )

    staging: Path | None = None
    try:
        _remove_staging(record.get("previous_staging_path"))
        staging = _stage_payload(graph_id, int(record["revision"]), kind, nodes, edges)
        with connect_database() as db:
            updated = db.execute(
                """UPDATE graph_registry SET staging_path = ?, updated_at = ?
                    WHERE graph_id = ? AND revision = ? AND storage_status = 'pending'""",
                (str(staging), _utcnow(), graph_id, record["revision"]),
            )
            if updated.rowcount != 1:
                raise GraphStoreError("graph registry revision changed while staging")
        record["staging_path"] = str(staging)
    except Exception as exc:
        if staging is not None:
            _remove_staging(str(staging))
        try:
            with connect_database() as db:
                db.execute(
                    """UPDATE graph_registry SET storage_status = 'failed', last_error = ?, updated_at = ?
                        WHERE graph_id = ? AND revision = ?""",
                    (str(exc), _utcnow(), graph_id, record["revision"]),
                )
        except Exception:
            pass
        raise
    return finalize_graph(record)


def load_graph(graph_id: str) -> dict[str, Any]:
    with connect_database() as db:
        registry = db.execute(
            "SELECT * FROM graph_registry WHERE graph_id = ?",
            (graph_id,),
        ).fetchone()
    if not registry:
        raise GraphUnavailableError(graph_id, "missing")
    if registry["storage_status"] != "ready":
        raise GraphUnavailableError(graph_id, str(registry["storage_status"]))
    graph = get_graph_store().get_graph(graph_id)
    if not graph:
        raise GraphUnavailableError(graph_id, "missing")
    if int(graph["revision"]) != int(registry["revision"]):
        raise GraphUnavailableError(graph_id, "revision_mismatch")
    if graph["checksum"] != registry["content_sha256"]:
        raise GraphUnavailableError(graph_id, "checksum_mismatch")
    return graph


def prepare_graph_delete(graph_id: str) -> None:
    now = _utcnow()
    with connect_database() as db:
        row = db.execute(
            "SELECT graph_id FROM graph_registry WHERE graph_id = ? FOR UPDATE",
            (graph_id,),
        ).fetchone()
        if not row:
            raise GraphUnavailableError(graph_id, "missing")
        db.execute(
            "UPDATE graph_registry SET storage_status = 'deleting', updated_at = ? WHERE graph_id = ?",
            (now, graph_id),
        )
    get_graph_store().delete_graph(graph_id)


def complete_graph_delete(graph_id: str) -> None:
    with connect_database() as db:
        db.execute(
            "DELETE FROM graph_registry WHERE graph_id = ? AND storage_status = 'deleting'",
            (graph_id,),
        )


def delete_graph(graph_id: str) -> None:
    prepare_graph_delete(graph_id)
    complete_graph_delete(graph_id)


def _delete_business_record(db: DatabaseConnection, graph_id: str, kind: str) -> None:
    if kind == "history":
        db.execute("DELETE FROM history WHERE id = ?", (graph_id,))
        return
    if kind != "education_snapshot":
        return
    assignment_rows = db.execute(
        "SELECT id FROM education_assignments WHERE snapshot_id = ?",
        (graph_id,),
    ).fetchall()
    assignment_ids = [str(row["id"]) for row in assignment_rows]
    if assignment_ids:
        placeholders = ",".join("?" for _ in assignment_ids)
        db.execute(
            f"DELETE FROM education_ai_tasks WHERE task_key IN ({placeholders})",
            assignment_ids,
        )
        db.execute(
            f"DELETE FROM education_assignments WHERE id IN ({placeholders})",
            assignment_ids,
        )
    db.execute("DELETE FROM education_snapshots WHERE id = ?", (graph_id,))


def reconcile_graph(graph_id: str) -> dict[str, Any]:
    with connect_database() as db:
        row = db.execute("SELECT * FROM graph_registry WHERE graph_id = ?", (graph_id,)).fetchone()
    if not row:
        raise GraphUnavailableError(graph_id, "missing")
    record = dict(row.items())
    if record["storage_status"] == "ready":
        return get_graph_store().verify_graph(graph_id)
    if record["storage_status"] == "deleting":
        get_graph_store().delete_graph(graph_id)
        with connect_database() as db:
            _delete_business_record(db, graph_id, str(record["graph_kind"]))
            db.execute("DELETE FROM graph_registry WHERE graph_id = ?", (graph_id,))
        return {"ok": True, "graph_id": graph_id, "deleted": True}
    if not record.get("staging_path") or not Path(record["staging_path"]).is_file():
        raise GraphUnavailableError(graph_id, "staging_missing")
    with connect_database() as db:
        db.execute(
            "UPDATE graph_registry SET storage_status = 'pending', last_error = NULL, updated_at = ? WHERE graph_id = ?",
            (_utcnow(), graph_id),
        )
    return finalize_graph(record)
