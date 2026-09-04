"""Finish the in-place MySQL/Neo4j production storage cutover.

Alembic revision 20260904_04 preserves the previous MySQL tables under the
``legacy_20260828_`` prefix and copies relational records into the current
contract.  This command performs the two operations that must use runtime
secrets/services: encrypting legacy per-user LLM settings and writing graph
payloads to Neo4j.  It is idempotent and never deletes the archived tables.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
for path in (str(PROJECT_ROOT), str(BACKEND_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from backend.integrations.neo4j_handler import graph_checksum  # noqa: E402
from backend.storage.database import connect_database  # noqa: E402
from backend.storage.graph_service import persist_graph  # noqa: E402
from backend.storage.secrets import encrypt_secret, load_data_key  # noqa: E402


ARCHIVE_PREFIX = "legacy_20260828_"
EXPECTED_REVISION = "20260904_04"


class LegacyStorageMigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class GraphPayload:
    graph_id: str
    kind: str
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]

    @property
    def checksum(self) -> str:
        return graph_checksum(self.nodes, self.edges)


def _table_exists(db, table: str) -> bool:
    row = db.execute(
        """SELECT COUNT(*) AS count FROM information_schema.tables
             WHERE table_schema = DATABASE() AND table_name = ?""",
        (table,),
    ).fetchone()
    return bool(row and int(row["count"] or 0))


def _require_bridge_schema() -> None:
    required = {
        "users",
        "history",
        "education_snapshots",
        "graph_registry",
        f"{ARCHIVE_PREFIX}users",
        f"{ARCHIVE_PREFIX}history",
        f"{ARCHIVE_PREFIX}education_snapshots",
    }
    with connect_database() as db:
        revision = db.execute(
            "SELECT version_num FROM alembic_version LIMIT 1"
        ).fetchone()
        if not revision or revision["version_num"] != EXPECTED_REVISION:
            raise LegacyStorageMigrationError(
                f"database must be at Alembic revision {EXPECTED_REVISION}"
            )
        missing = sorted(table for table in required if not _table_exists(db, table))
    if missing:
        raise LegacyStorageMigrationError(
            "storage bridge tables are missing: " + ", ".join(missing)
        )


def _json_array(value: Any, *, graph_id: str, column: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    try:
        payload = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError) as exc:
        raise LegacyStorageMigrationError(
            f"graph {graph_id} has invalid {column}"
        ) from exc
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise LegacyStorageMigrationError(
            f"graph {graph_id} {column} must be an array of objects"
        )
    return payload


def _validate_graph(graph: GraphPayload) -> None:
    node_ids: set[int] = set()
    for position, node in enumerate(graph.nodes):
        node_id = node.get("id")
        if isinstance(node_id, bool) or not isinstance(node_id, int):
            raise LegacyStorageMigrationError(
                f"graph {graph.graph_id} node {position} has an invalid id"
            )
        if node_id in node_ids:
            raise LegacyStorageMigrationError(
                f"graph {graph.graph_id} contains duplicate node {node_id}"
            )
        node_ids.add(node_id)
    for position, edge in enumerate(graph.edges):
        if edge.get("from") not in node_ids or edge.get("to") not in node_ids:
            raise LegacyStorageMigrationError(
                f"graph {graph.graph_id} edge {position} references a missing node"
            )


def _graph_rows() -> Iterable[GraphPayload]:
    sources = (
        (f"{ARCHIVE_PREFIX}history", "history"),
        (f"{ARCHIVE_PREFIX}education_snapshots", "education_snapshot"),
    )
    seen: dict[str, str] = {}
    with connect_database() as db:
        for table, kind in sources:
            rows = db.execute(
                f"SELECT id, nodes_json, edges_json FROM `{table}` ORDER BY id"
            ).fetchall()
            for row in rows:
                graph_id = str(row["id"])
                previous_kind = seen.get(graph_id)
                if previous_kind and previous_kind != kind:
                    raise LegacyStorageMigrationError(
                        f"graph id {graph_id} is shared by {previous_kind} and {kind}"
                    )
                seen[graph_id] = kind
                graph = GraphPayload(
                    graph_id=graph_id,
                    kind=kind,
                    nodes=_json_array(row["nodes_json"], graph_id=graph_id, column="nodes_json"),
                    edges=_json_array(row["edges_json"], graph_id=graph_id, column="edges_json"),
                )
                _validate_graph(graph)
                yield graph


def _migrate_settings(*, apply: bool) -> tuple[int, int]:
    table = f"{ARCHIVE_PREFIX}user_settings"
    with connect_database() as db:
        if not _table_exists(db, table):
            return 0, 0
        rows = db.execute(
            f"""SELECT legacy.user_id, legacy.llm_api_key, legacy.llm_configs_json,
                       current.llm_api_key_ciphertext,
                       current.llm_configs_ciphertext
                  FROM `{table}` legacy
                  JOIN user_settings current ON current.user_id = legacy.user_id
                 ORDER BY legacy.user_id"""
        ).fetchall()
    pending = [
        row
        for row in rows
        if not row["llm_api_key_ciphertext"] and not row["llm_configs_ciphertext"]
    ]
    if not apply or not pending:
        return len(rows), len(pending)

    key = load_data_key()
    migrated = 0
    for row in pending:
        user_id = int(row["user_id"])
        configs = row["llm_configs_json"]
        if isinstance(configs, (dict, list)):
            configs = json.dumps(configs, ensure_ascii=False, separators=(",", ":"))
        with connect_database() as db:
            updated = db.execute(
                """UPDATE user_settings
                      SET llm_api_key_ciphertext = ?, llm_configs_ciphertext = ?
                    WHERE user_id = ?
                      AND llm_api_key_ciphertext = ''
                      AND llm_configs_ciphertext = ''""",
                (
                    encrypt_secret(
                        str(row["llm_api_key"] or ""),
                        aad=f"user-settings:{user_id}:api-key",
                        key=key,
                    ),
                    encrypt_secret(
                        str(configs or ""),
                        aad=f"user-settings:{user_id}:configs",
                        key=key,
                    ),
                    user_id,
                ),
            )
            migrated += int(updated.rowcount == 1)
    return len(rows), migrated


def _migrate_graphs(*, apply: bool) -> tuple[int, int, int]:
    graphs = list(_graph_rows())
    migrated = 0
    skipped = 0
    if not apply:
        return len(graphs), migrated, skipped

    for index, graph in enumerate(graphs, start=1):
        with connect_database() as db:
            registry = db.execute(
                """SELECT graph_kind, storage_status, content_sha256
                     FROM graph_registry WHERE graph_id = ?""",
                (graph.graph_id,),
            ).fetchone()
        if (
            registry
            and registry["graph_kind"] == graph.kind
            and registry["storage_status"] == "ready"
            and registry["content_sha256"] == graph.checksum
        ):
            skipped += 1
            continue
        persist_graph(
            graph.graph_id,
            graph.kind,
            graph.nodes,
            graph.edges,
        )
        migrated += 1
        print(f"graph {index}/{len(graphs)} stored: {graph.graph_id}")
    return len(graphs), migrated, skipped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write encrypted settings and graph payloads (default: inspect only)",
    )
    parser.add_argument(
        "--only",
        choices=("all", "settings", "graphs"),
        default="all",
        help="limit the migration stage",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _require_bridge_schema()

    setting_total = setting_changed = 0
    graph_total = graph_changed = graph_skipped = 0
    if args.only in {"all", "settings"}:
        setting_total, setting_changed = _migrate_settings(apply=args.apply)
    if args.only in {"all", "graphs"}:
        graph_total, graph_changed, graph_skipped = _migrate_graphs(apply=args.apply)

    mode = "applied" if args.apply else "inspected"
    print(
        f"legacy storage {mode}: settings={setting_total}, "
        f"settings_written={setting_changed}, graphs={graph_total}, "
        f"graphs_written={graph_changed}, graphs_unchanged={graph_skipped}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
