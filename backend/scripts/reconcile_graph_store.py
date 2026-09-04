"""Inspect or reconcile pending/failed/deleting graph registry records."""

from __future__ import annotations

import argparse
import json
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph-id", action="append", default=[])
    parser.add_argument("--apply", action="store_true", help="perform reconciliation; default is dry-run")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from backend.storage.database import connect_database
    from backend.storage.graph_service import reconcile_graph

    with connect_database() as db:
        if args.graph_id:
            placeholders = ",".join("?" for _ in args.graph_id)
            rows = db.execute(
                f"SELECT graph_id, graph_kind, storage_status, revision, node_count, edge_count, updated_at "
                f"FROM graph_registry WHERE graph_id IN ({placeholders}) ORDER BY graph_id",
                args.graph_id,
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT graph_id, graph_kind, storage_status, revision, node_count, edge_count, updated_at
                     FROM graph_registry
                    WHERE storage_status != 'ready'
                    ORDER BY updated_at, graph_id"""
            ).fetchall()
    records = [dict(row.items()) for row in rows]
    if not args.apply:
        print(json.dumps({"ok": True, "mode": "dry-run", "graphs": records}, ensure_ascii=False))
        return 0

    results = []
    failed = False
    for record in records:
        graph_id = str(record["graph_id"])
        try:
            result = reconcile_graph(graph_id)
            results.append({"graph_id": graph_id, "ok": True, "result": result})
        except Exception as exc:
            failed = True
            results.append({
                "graph_id": graph_id,
                "ok": False,
                "error": type(exc).__name__,
            })
    print(json.dumps({"ok": not failed, "mode": "apply", "graphs": results}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
