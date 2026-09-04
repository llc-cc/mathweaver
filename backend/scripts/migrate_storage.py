"""Offline SQLite -> MySQL + Neo4j cutover utility.

The command is dry-run by default. Apply mode requires an empty, Alembic-upgraded
MySQL target and an empty Neo4j graph store.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

BASELINE = {
    "date": "2026-09-01",
    "users": 3,
    "history": 18,
    "classes": 6,
    "snapshots": 15,
    "assignments": 14,
    "graphs": 33,
    "nodes": 1203,
    "edges": 2750,
}

TARGET_TABLE_ORDER = (
    "users",
    "sessions",
    "history",
    "user_settings",
    "proof_workspaces",
    "education_classes",
    "education_memberships",
    "education_snapshots",
    "education_course_graph_order",
    "education_assignments",
    "education_student_paths",
    "education_diagnostics",
    "education_ai_usage",
    "education_ai_tasks",
    "education_assessment_nodes",
    "education_assessment_questions",
    "education_assessment_attempts",
    "education_node_progress",
    "education_node_identities",
    "education_node_occurrences",
    "learning_interactions",
    "learning_evidence",
    "learning_evidence_nodes",
    "learning_evidence_feedback",
    "student_node_models",
    "learning_context_summaries",
    "education_assignment_submissions",
    "education_submission_question_grades",
    "education_assignment_sources",
    "education_game_events",
    "education_student_achievements",
)

USER_REFERENCE_COLUMNS = {"user_id", "owner_user_id", "created_by", "updated_by"}
JSON_COLUMNS = {
    "nodes_json",
    "edges_json",
    "latex_macros",
    "latex_macros_json",
    "source_pdf_json",
    "llm_configs_json",
}


class MigrationError(RuntimeError):
    pass


@dataclass
class SourceSnapshot:
    name: str
    source_path: Path
    snapshot_path: Path
    priority: int
    tables: dict[str, list[dict[str, Any]]]
    primary_keys: dict[str, tuple[str, ...]]


@dataclass
class MergeResult:
    tables: dict[str, list[dict[str, Any]]]
    graphs: list[dict[str, Any]]
    user_map: dict[tuple[str, int], int]
    source_counts: dict[str, dict[str, int]]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="microseconds")


def parse_source(value: str) -> tuple[str, Path]:
    name, separator, raw_path = value.partition("=")
    name = name.strip()
    if separator != "=" or not name or not raw_path.strip():
        raise argparse.ArgumentTypeError("--source must use NAME=PATH")
    return name, Path(os.path.expandvars(raw_path.strip())).expanduser().resolve()


def safe_error(exc: BaseException) -> str:
    message = str(exc)
    message = re.sub(r"mysql\+pymysql://[^\s]+", "mysql+pymysql://<redacted>", message)
    message = re.sub(r"(?i)(password|token|api[_ -]?key)\s*[=:]\s*\S+", r"\1=<redacted>", message)
    return message[:2000]


def atomic_write_report(path: Path, payload: dict[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def snapshot_sqlite(source: Path, target: Path) -> None:
    if not source.is_file():
        raise MigrationError(f"SQLite source does not exist: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(str(source), timeout=30)
    target_connection = sqlite3.connect(str(target))
    try:
        source_connection.execute("PRAGMA query_only = ON")
        source_connection.backup(target_connection)
        integrity = target_connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise MigrationError(f"SQLite backup integrity check failed for source {source}")
    finally:
        target_connection.close()
        source_connection.close()


def read_sqlite_snapshot(name: str, source_path: Path, snapshot_path: Path, priority: int) -> SourceSnapshot:
    connection = sqlite3.connect(str(snapshot_path))
    connection.row_factory = sqlite3.Row
    try:
        table_names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        unknown = sorted(table_names - set(TARGET_TABLE_ORDER))
        if unknown:
            raise MigrationError(f"source {name} contains unsupported tables: {', '.join(unknown)}")
        if "users" not in table_names:
            raise MigrationError(f"source {name} does not contain users")
        tables: dict[str, list[dict[str, Any]]] = {}
        primary_keys: dict[str, tuple[str, ...]] = {}
        for table in TARGET_TABLE_ORDER:
            if table not in table_names:
                tables[table] = []
                primary_keys[table] = ()
                continue
            quoted = table.replace('"', '""')
            columns = connection.execute(f'PRAGMA table_info("{quoted}")').fetchall()
            primary_keys[table] = tuple(
                row[1] for row in sorted(columns, key=lambda item: int(item[5] or 0)) if int(row[5] or 0) > 0
            )
            if not primary_keys[table]:
                raise MigrationError(f"source table {table} has no primary key")
            tables[table] = [dict(row) for row in connection.execute(f'SELECT * FROM "{quoted}"')]
        return SourceSnapshot(name, source_path, snapshot_path, priority, tables, primary_keys)
    finally:
        connection.close()


def normalize_email(value: Any) -> str:
    email = str(value or "").strip().lower()
    if not email or "@" not in email:
        raise MigrationError("every source user must have a valid email-shaped identity")
    return email


def parse_json_list(value: Any, *, graph_id: str, field: str) -> list[dict[str, Any]]:
    try:
        parsed = value if isinstance(value, list) else json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError) as exc:
        raise MigrationError(f"graph {graph_id} has invalid {field}") from exc
    if not isinstance(parsed, list) or any(not isinstance(item, dict) for item in parsed):
        raise MigrationError(f"graph {graph_id} {field} must be an array of objects")
    return parsed


def graph_checksum(nodes: list[dict], edges: list[dict]) -> str:
    payload = json.dumps(
        {"nodes": nodes, "edges": edges},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_graph(graph_id: str, nodes: list[dict], edges: list[dict]) -> None:
    node_ids: set[int] = set()
    for position, node in enumerate(nodes):
        node_id = node.get("id")
        if isinstance(node_id, bool) or not isinstance(node_id, int):
            raise MigrationError(f"graph {graph_id} node {position} has a non-integer id")
        if node_id in node_ids:
            raise MigrationError(f"graph {graph_id} has duplicate node id {node_id}")
        node_ids.add(node_id)
    for position, edge in enumerate(edges):
        if edge.get("from") not in node_ids or edge.get("to") not in node_ids:
            raise MigrationError(f"graph {graph_id} edge {position} references a missing endpoint")


def canonical_value(column: str, value: Any) -> Any:
    if value is None:
        return None
    if column in JSON_COLUMNS or column.endswith("_json"):
        try:
            return json.loads(value) if isinstance(value, str) else value
        except json.JSONDecodeError:
            return value
    return value


def canonical_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: canonical_value(key, value) for key, value in row.items()}


def remap_user_references(row: dict[str, Any], source: SourceSnapshot, user_map: dict[tuple[str, int], int]) -> dict[str, Any]:
    mapped = dict(row)
    for column in USER_REFERENCE_COLUMNS:
        if column not in mapped or mapped[column] is None:
            continue
        key = (source.name, int(mapped[column]))
        if key not in user_map:
            raise MigrationError(f"{source.name} row references unknown user id {mapped[column]}")
        mapped[column] = user_map[key]
    return mapped


def merge_users(sources: list[SourceSnapshot]) -> tuple[list[dict[str, Any]], dict[tuple[str, int], int]]:
    by_email: dict[str, dict[str, Any]] = {}
    for source in sorted(sources, key=lambda item: item.priority):
        for row in source.tables["users"]:
            email = normalize_email(row.get("email"))
            current = by_email.get(email)
            created_at = str(row.get("created_at") or utc_now_iso())
            if current is None:
                by_email[email] = {
                    "email": email,
                    "password_hash": row.get("password_hash") or "",
                    "created_at": created_at,
                    "can_teach": int(bool(row.get("can_teach"))),
                    "priority": source.priority,
                }
            else:
                current["can_teach"] = int(bool(current["can_teach"]) or bool(row.get("can_teach")))
                current["created_at"] = min(str(current["created_at"]), created_at)
                if source.priority >= int(current["priority"]):
                    current["password_hash"] = row.get("password_hash") or ""
                    current["priority"] = source.priority
    users: list[dict[str, Any]] = []
    email_to_new_id: dict[str, int] = {}
    for new_id, email in enumerate(sorted(by_email), start=1):
        record = by_email[email]
        users.append({
            "id": new_id,
            "email": email,
            "password_hash": record["password_hash"],
            "created_at": record["created_at"],
            "can_teach": record["can_teach"],
        })
        email_to_new_id[email] = new_id
    user_map: dict[tuple[str, int], int] = {}
    for source in sources:
        for row in source.tables["users"]:
            user_map[(source.name, int(row["id"]))] = email_to_new_id[normalize_email(row.get("email"))]
    return users, user_map


def extract_graph(source: SourceSnapshot, table: str, row: dict[str, Any]) -> dict[str, Any]:
    graph_id = str(row["id"])
    nodes = parse_json_list(row.get("nodes_json"), graph_id=graph_id, field="nodes_json")
    edges = parse_json_list(row.get("edges_json"), graph_id=graph_id, field="edges_json")
    validate_graph(graph_id, nodes, edges)
    return {
        "graph_id": graph_id,
        "kind": "history" if table == "history" else "education_snapshot",
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "checksum": graph_checksum(nodes, edges),
        "source": source.name,
    }


def transform_row(table: str, row: dict[str, Any], graph: dict[str, Any] | None = None) -> dict[str, Any]:
    transformed = dict(row)
    if table == "history":
        transformed.pop("nodes_json", None)
        transformed.pop("edges_json", None)
        transformed["node_count"] = int(graph["node_count"] if graph else transformed.get("node_count") or 0)
        transformed["edge_count"] = int(graph["edge_count"] if graph else transformed.get("edge_count") or 0)
    elif table == "education_snapshots":
        transformed.pop("nodes_json", None)
        transformed.pop("edges_json", None)
        transformed["node_count"] = int(graph["node_count"] if graph else 0)
        transformed["edge_count"] = int(graph["edge_count"] if graph else 0)
        transformed["snapshot_type"] = transformed.get("snapshot_type") or "graph"
    return transformed


def default_course_graph_order(snapshot_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    next_order: dict[str, int] = {}
    for snapshot in sorted(
        snapshot_rows,
        key=lambda row: (
            str(row.get("class_id") or ""),
            str(row.get("created_at") or ""),
            str(row.get("id") or ""),
        ),
    ):
        if str(snapshot.get("snapshot_type") or "graph") != "graph":
            continue
        class_id = str(snapshot.get("class_id") or "")
        source_graph_id = str(snapshot.get("source_graph_id") or "").strip()
        graph_key = f"source:{source_graph_id}" if source_graph_id else f"snapshot:{snapshot['id']}"
        identity = (class_id, graph_key)
        if identity in seen:
            continue
        seen.add(identity)
        sort_order = next_order.get(class_id, 0)
        next_order[class_id] = sort_order + 1
        rows.append({
            "class_id": class_id,
            "graph_key": graph_key,
            "sort_order": sort_order,
            "updated_by": snapshot["created_by"],
            "updated_at": snapshot["created_at"],
        })
    return rows


def merge_sources(sources: list[SourceSnapshot]) -> MergeResult:
    users, user_map = merge_users(sources)
    merged: dict[str, list[dict[str, Any]]] = {table: [] for table in TARGET_TABLE_ORDER}
    merged["users"] = users
    source_counts = {
        source.name: {table: len(source.tables[table]) for table in TARGET_TABLE_ORDER}
        for source in sources
    }

    # Settings are intentionally last-writer-by-priority after user remapping.
    settings_by_user: dict[int, tuple[int, dict[str, Any]]] = {}
    # Session plaintext tokens exist in memory only until their SHA-256 is computed.
    sessions_by_token: dict[str, tuple[int, dict[str, Any]]] = {}
    graph_by_id: dict[str, dict[str, Any]] = {}
    merged_rows: dict[str, dict[tuple[Any, ...], dict[str, Any]]] = {
        table: {} for table in TARGET_TABLE_ORDER if table not in {"users", "sessions", "user_settings"}
    }

    for source in sorted(sources, key=lambda item: item.priority):
        for row in source.tables["user_settings"]:
            mapped = remap_user_references(row, source, user_map)
            settings_by_user[int(mapped["user_id"])] = (source.priority, mapped)

        for row in source.tables["sessions"]:
            mapped = remap_user_references(row, source, user_map)
            token = str(mapped.pop("token", ""))
            if not token:
                raise MigrationError(f"source {source.name} contains an empty session token")
            existing = sessions_by_token.get(token)
            if existing and int(existing[1]["user_id"]) != int(mapped["user_id"]):
                raise MigrationError("the same session token belongs to different merged users")
            if existing is None or source.priority >= existing[0]:
                sessions_by_token[token] = (source.priority, mapped)

        for table in TARGET_TABLE_ORDER:
            if table in {"users", "sessions", "user_settings"}:
                continue
            pk_columns = source.primary_keys[table]
            if not pk_columns and source.tables[table]:
                raise MigrationError(f"cannot merge table {table} without a primary key")
            for raw_row in source.tables[table]:
                remapped = remap_user_references(raw_row, source, user_map)
                graph = None
                if table in {"history", "education_snapshots"}:
                    graph = extract_graph(source, table, remapped)
                    existing_graph = graph_by_id.get(graph["graph_id"])
                    if existing_graph and (
                        existing_graph["kind"] != graph["kind"]
                        or existing_graph["checksum"] != graph["checksum"]
                    ):
                        raise MigrationError(f"conflicting graph id {graph['graph_id']}")
                    graph_by_id[graph["graph_id"]] = graph
                transformed = transform_row(table, remapped, graph)
                key = tuple(transformed[column] for column in pk_columns)
                existing = merged_rows[table].get(key)
                if existing is not None and canonical_row(existing) != canonical_row(transformed):
                    raise MigrationError(f"conflicting primary key in {table}: {key}")
                merged_rows[table][key] = transformed

    from backend.storage.secrets import encrypt_secret, load_data_key

    data_key = load_data_key() if settings_by_user else None
    for user_id in sorted(settings_by_user):
        row = dict(settings_by_user[user_id][1])
        api_key = str(row.pop("llm_api_key", "") or "")
        configs_json = str(row.pop("llm_configs_json", "") or "")
        row["llm_api_key_ciphertext"] = encrypt_secret(
            api_key,
            aad=f"user-settings:{user_id}:api-key",
            key=data_key,
        )
        row["llm_configs_ciphertext"] = encrypt_secret(
            configs_json,
            aad=f"user-settings:{user_id}:configs",
            key=data_key,
        )
        merged["user_settings"].append(row)

    for token, (_priority, row) in sorted(sessions_by_token.items(), key=lambda item: item[0]):
        transformed = dict(row)
        transformed["token_hash"] = hashlib.sha256(token.encode("utf-8")).digest()
        merged["sessions"].append(transformed)

    for table, rows_by_key in merged_rows.items():
        merged[table] = [rows_by_key[key] for key in sorted(rows_by_key, key=repr)]

    if not merged["education_course_graph_order"]:
        merged["education_course_graph_order"] = default_course_graph_order(
            merged["education_snapshots"]
        )

    graphs = [graph_by_id[key] for key in sorted(graph_by_id)]
    return MergeResult(merged, graphs, user_map, source_counts)


def report_payload(result: MergeResult, *, mode: str, backups: dict[str, str]) -> dict[str, Any]:
    summary = {
        "users": len(result.tables["users"]),
        "history": len(result.tables["history"]),
        "classes": len(result.tables["education_classes"]),
        "snapshots": len(result.tables["education_snapshots"]),
        "assignments": len(result.tables["education_assignments"]),
        "graphs": len(result.graphs),
        "nodes": sum(int(item["node_count"]) for item in result.graphs),
        "edges": sum(int(item["edge_count"]) for item in result.graphs),
    }
    return {
        "ok": True,
        "mode": mode,
        "generated_at": utc_now_iso(),
        "source_backups": backups,
        "source_counts": result.source_counts,
        "merged_table_counts": {table: len(result.tables[table]) for table in TARGET_TABLE_ORDER},
        "summary": summary,
        "baseline": BASELINE,
        "baseline_delta": {key: summary[key] - int(BASELINE[key]) for key in summary},
        "graph_checksums": [
            {
                "graph_id": graph["graph_id"],
                "kind": graph["kind"],
                "node_count": graph["node_count"],
                "edge_count": graph["edge_count"],
                "sha256": graph["checksum"],
            }
            for graph in result.graphs
        ],
    }


def _filesystem_copy_path(path: Path) -> str:
    resolved = str(path.resolve())
    backslash = chr(92)
    prefix = backslash * 2 + "?" + backslash
    if os.name != "nt" or resolved.startswith(prefix):
        return resolved
    if resolved.startswith(backslash * 2):
        return prefix + "UNC" + backslash + resolved[2:]
    return prefix + resolved


def backup_file_data(source: SourceSnapshot, backup_root: Path) -> None:
    source_root = source.source_path.parent
    for directory_name in ("jobs", "education"):
        candidate = source_root / directory_name
        if candidate.is_dir():
            target = backup_root / source.name / directory_name
            shutil.copytree(
                _filesystem_copy_path(candidate),
                _filesystem_copy_path(target),
                dirs_exist_ok=False,
            )


def ensure_empty_target() -> None:
    from backend.storage.database import connect_database, database_health
    from backend.integrations.neo4j_handler import get_graph_store

    health = database_health()
    if not health.get("ok"):
        raise MigrationError("MySQL schema is not at the required Alembic revision")
    with connect_database() as db:
        non_empty = []
        for table in (*TARGET_TABLE_ORDER, "graph_registry"):
            row = db.execute(f"SELECT COUNT(*) AS count FROM `{table}`").fetchone()
            if int(row["count"] or 0):
                non_empty.append(table)
        if non_empty:
            raise MigrationError(f"MySQL target is not empty: {', '.join(non_empty)}")
    store = get_graph_store()
    with store.driver.session(database=store.database) as session:
        row = session.run("MATCH (g:Graph) RETURN count(g) AS count").single()
    if row and int(row["count"] or 0):
        raise MigrationError("Neo4j target is not empty")


def insert_rows(table: str, rows: Iterable[dict[str, Any]]) -> None:
    from backend.storage.database import connect_database

    materialized = list(rows)
    if not materialized:
        return
    columns = list(materialized[0])
    expected = set(columns)
    if any(set(row) != expected for row in materialized):
        raise MigrationError(f"merged rows for {table} have inconsistent columns")
    quoted = ", ".join(f"`{column}`" for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    values = [tuple(row[column] for column in columns) for row in materialized]
    with connect_database() as db:
        db.executemany(f"INSERT INTO `{table}` ({quoted}) VALUES ({placeholders})", values)


def apply_merge(result: MergeResult) -> None:
    from backend.storage.database import connect_database
    from backend.storage.graph_service import persist_graph

    for table in TARGET_TABLE_ORDER:
        insert_rows(table, result.tables[table])

    for graph in result.graphs:
        persist_graph(graph["graph_id"], graph["kind"], graph["nodes"], graph["edges"])

    # Target-side count verification is intentionally independent from the source merge objects.
    with connect_database() as db:
        for table in TARGET_TABLE_ORDER:
            row = db.execute(f"SELECT COUNT(*) AS count FROM `{table}`").fetchone()
            expected = len(result.tables[table])
            if int(row["count"] or 0) != expected:
                raise MigrationError(f"target row count mismatch for {table}")
        ready = db.execute(
            "SELECT COUNT(*) AS count FROM graph_registry WHERE storage_status = 'ready'"
        ).fetchone()
        if int(ready["count"] or 0) != len(result.graphs):
            raise MigrationError("not every migrated graph is ready")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", required=True, type=parse_source, metavar="NAME=PATH")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="validate and report without writing targets (default)")
    mode.add_argument("--apply", action="store_true", help="write the empty MySQL and Neo4j targets")
    parser.add_argument("--require-empty-target", action="store_true")
    parser.add_argument("--report", type=Path, default=Path("migration-report.json"))
    parser.add_argument("--backup-dir", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report_path = args.report.expanduser().resolve()
    if args.apply and not args.require_empty_target:
        raise SystemExit("--apply requires --require-empty-target")
    names = [name for name, _path in args.source]
    if len(names) != len(set(names)):
        raise SystemExit("source names must be unique")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    temporary_context = None
    if args.apply:
        backup_root = (
            args.backup_dir.expanduser().resolve()
            if args.backup_dir
            else Path(os.environ.get("MATHGRAPH_DATA_DIR", Path.cwd())).expanduser().resolve()
            / "migration-backups"
            / timestamp
        )
        backup_root.mkdir(parents=True, exist_ok=False)
    else:
        temporary_context = tempfile.TemporaryDirectory(prefix="mathweaver-migration-")
        backup_root = Path(temporary_context.name)

    report: dict[str, Any] = {
        "ok": False,
        "mode": "apply" if args.apply else "dry-run",
        "generated_at": utc_now_iso(),
    }
    try:
        sources: list[SourceSnapshot] = []
        backups: dict[str, str] = {}
        for index, (name, source_path) in enumerate(args.source):
            priority = 1000 if name.lower() == "runtime" else index
            target = backup_root / name / "auth.db"
            snapshot_sqlite(source_path, target)
            if args.apply:
                backups[name] = str(target)
            source = read_sqlite_snapshot(name, source_path, target, priority)
            sources.append(source)
            if args.apply:
                backup_file_data(source, backup_root)

        result = merge_sources(sources)
        report = report_payload(
            result,
            mode="apply" if args.apply else "dry-run",
            backups=backups,
        )
        if args.apply:
            ensure_empty_target()
            apply_merge(result)
            report["applied"] = True
        atomic_write_report(report_path, report)
        print(json.dumps({
            "ok": True,
            "mode": report["mode"],
            "report": str(report_path),
            "summary": report["summary"],
        }, ensure_ascii=False))
        return 0
    except Exception as exc:
        report.update({"ok": False, "error": safe_error(exc)})
        atomic_write_report(report_path, report)
        print(json.dumps({"ok": False, "report": str(report_path), "error": safe_error(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        if temporary_context is not None:
            temporary_context.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
