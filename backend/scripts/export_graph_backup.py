"""Export or restore the application-owned Neo4j graphs as a logical backup."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
for path in (str(PROJECT_ROOT), str(BACKEND_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from backend.integrations.neo4j_handler import get_graph_store  # noqa: E402
from backend.storage.database import connect_database  # noqa: E402


def _read_bundle(path: Path) -> dict:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        bundle = json.load(handle)
    if bundle.get("format") != "mathweaver-neo4j-graphs-v1":
        raise RuntimeError("unsupported graph backup format")
    if not isinstance(bundle.get("graphs"), list):
        raise RuntimeError("graph backup is missing graphs")
    return bundle


def export_graphs(output: Path) -> None:
    with connect_database() as db:
        registry = [
            dict(row)
            for row in db.execute(
                """SELECT graph_id, graph_kind, storage_status, revision,
                          node_count, edge_count, content_sha256
                     FROM graph_registry ORDER BY graph_id"""
            ).fetchall()
        ]
    store = get_graph_store()
    try:
        with store.driver.session(database=store.database) as session:
            neo4j_ids = set(
                session.run("MATCH (g:Graph) RETURN g.id AS id").value("id")
            )
        registry_ids = {row["graph_id"] for row in registry}
        if neo4j_ids != registry_ids:
            raise RuntimeError("MySQL graph registry and Neo4j graph ids differ")
        graphs = []
        for row in registry:
            graph = store.get_graph(row["graph_id"])
            if not graph:
                raise RuntimeError(f"Neo4j graph is missing: {row['graph_id']}")
            if (
                row["storage_status"] != "ready"
                or int(row["revision"]) != graph["revision"]
                or int(row["node_count"]) != graph["node_count"]
                or int(row["edge_count"]) != graph["edge_count"]
                or row["content_sha256"] != graph["checksum"]
            ):
                raise RuntimeError(f"graph registry mismatch: {row['graph_id']}")
            graphs.append(graph)
    finally:
        store.close()

    bundle = {
        "format": "mathweaver-neo4j-graphs-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "graph_count": len(graphs),
        "graphs": graphs,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(fd)
    try:
        opener = gzip.open if output.suffix == ".gz" else open
        with opener(temporary, "wt", encoding="utf-8") as handle:
            json.dump(bundle, handle, ensure_ascii=False, separators=(",", ":"))
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def restore_graphs(source: Path, *, apply: bool) -> None:
    bundle = _read_bundle(source)
    graphs = bundle["graphs"]
    if not apply:
        print(f"graph backup verified: {len(graphs)} graphs")
        return
    store = get_graph_store()
    try:
        for graph in graphs:
            result = store.replace_graph(
                graph["graph_id"],
                graph["kind"],
                graph["nodes"],
                graph["edges"],
                revision=int(graph["revision"]),
                schema_version=int(graph.get("schema_version") or 1),
            )
            if result["checksum"] != graph["checksum"]:
                raise RuntimeError(f"restored graph checksum mismatch: {graph['graph_id']}")
    finally:
        store.close()
    print(f"graph restore completed: {len(graphs)} graphs")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path)
    group.add_argument("--restore", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.output:
        if args.apply:
            parser.error("--apply is only valid with --restore")
        export_graphs(args.output.resolve())
        print(f"graph backup written: {args.output.resolve()}")
    else:
        restore_graphs(args.restore.resolve(), apply=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
