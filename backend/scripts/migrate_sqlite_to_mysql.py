"""将桌面版旧 SQLite 数据安全迁移到集中式 SQLAlchemy 数据库。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

from sqlalchemy import create_engine, func, select

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from storage.models import History, LoginSession, ProofWorkspace, User, UserSettings
from storage.database import validate_database_target
from storage.learning_repository import sanitize_source_pdf_meta


REQUIRED_SOURCE_TABLES = ("users", "history", "user_settings", "proof_workspaces")


class MigrationError(RuntimeError):
    """表示可安全展示且不会包含凭据的迁移失败。"""


def _open_source_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise MigrationError("SQLite source file does not exist")
    # URI 的 mode=ro 从连接层阻断任何意外写入，源库始终只作为迁移输入。
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _source_counts(connection: sqlite3.Connection) -> dict[str, int]:
    existing = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    missing = [name for name in REQUIRED_SOURCE_TABLES if name not in existing]
    if missing:
        raise MigrationError(f"required source table is missing: {', '.join(missing)}")
    counts = {
        name: int(connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
        for name in REQUIRED_SOURCE_TABLES
    }
    counts["sessions"] = (
        int(connection.execute('SELECT COUNT(*) FROM "sessions"').fetchone()[0])
        if "sessions" in existing
        else 0
    )
    return counts


def _row_value(row: sqlite3.Row, name: str, default: Any = None) -> Any:
    return row[name] if name in row.keys() else default


def _parse_datetime(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise MigrationError(f"invalid datetime in {field}") from exc
    else:
        raise MigrationError(f"missing datetime in {field}")
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _parse_json(value: Any, expected_type: type, field: str, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError) as exc:
        raise MigrationError(f"invalid JSON in {field}") from exc
    if not isinstance(parsed, expected_type):
        raise MigrationError(f"invalid JSON type in {field}")
    return parsed


def _load_mapping(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError("user mapping is not valid JSON") from exc
    if not isinstance(data, dict):
        raise MigrationError("user mapping must be an object keyed by email")
    normalized: dict[str, dict[str, Any]] = {}
    for raw_email, raw_entry in data.items():
        if not isinstance(raw_email, str) or not raw_email.strip() or not isinstance(raw_entry, dict):
            raise MigrationError("user mapping entries must be objects keyed by email")
        email = raw_email.strip().lower()
        role = raw_entry.get("role", "student")
        if role not in {"student", "teacher", "admin"}:
            raise MigrationError("mapped role must be student, teacher, or admin")
        if "is_active" in raw_entry and not isinstance(raw_entry["is_active"], bool):
            raise MigrationError("mapped is_active must be a boolean")
        normalized[email] = dict(raw_entry)
    return normalized


def _source_rows(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return connection.execute(f'SELECT * FROM "{table}"').fetchall()


def _placeholder_student_no(user_id: int, reserved: set[str]) -> str:
    base = f"legacy-{user_id}"
    candidate = base
    suffix = 2
    while candidate in reserved:
        candidate = f"{base}-{suffix}"
        suffix += 1
    reserved.add(candidate)
    return candidate


def _build_users(rows: list[sqlite3.Row], mapping: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    reserved = {
        str(entry.get("student_no") or "").strip()
        for entry in mapping.values()
        if str(entry.get("student_no") or "").strip()
    }
    users: list[dict[str, Any]] = []
    used_student_numbers: set[str] = set()
    for row in rows:
        user_id = int(row["id"])
        email = str(_row_value(row, "email", "") or "").strip().lower() or None
        entry = mapping.get(email or "", {})
        role = entry.get("role", "student")
        mapped_student_no = str(entry.get("student_no") or "").strip() or None
        if role == "student" and mapped_student_no:
            student_no = mapped_student_no
            is_active = entry.get("is_active", True)
        elif role == "student":
            student_no = _placeholder_student_no(user_id, reserved)
            is_active = False
        else:
            # 教师和管理员角色只有映射文件显式声明才会进入此分支。
            student_no = mapped_student_no
            is_active = entry.get("is_active", True)
        if student_no and student_no in used_student_numbers:
            raise MigrationError("duplicate student number in user mapping")
        if student_no:
            used_student_numbers.add(student_no)
        created_at = _parse_datetime(row["created_at"], f"users[{user_id}].created_at")
        display_name = str(entry.get("display_name") or "").strip()
        if not display_name:
            display_name = (email or f"legacy-user-{user_id}").split("@", 1)[0]
        users.append(
            {
                "id": user_id,
                "student_no": student_no,
                "email": email,
                "display_name": display_name,
                "role": role,
                "password_hash": row["password_hash"],
                "initial_password_pending": bool(_row_value(row, "initial_password_pending", True)),
                "is_active": bool(is_active),
                "created_at": created_at,
                "updated_at": _parse_datetime(
                    _row_value(row, "updated_at", row["created_at"]),
                    f"users[{user_id}].updated_at",
                ),
            }
        )
    return users


def _build_history(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        row_id = str(row["id"])
        created_at = _parse_datetime(row["created_at"], f"history[{row_id}].created_at")
        result.append(
            {
                "id": row_id,
                "user_id": int(row["user_id"]),
                "filename": row["filename"],
                "node_count": int(_row_value(row, "node_count", 0) or 0),
                "edge_count": int(_row_value(row, "edge_count", 0) or 0),
                "nodes_json": _parse_json(row["nodes_json"], list, f"history[{row_id}].nodes_json", []),
                "edges_json": _parse_json(row["edges_json"], list, f"history[{row_id}].edges_json", []),
                "source_markdown": _row_value(row, "source_markdown"),
                "latex_macros": _row_value(row, "latex_macros"),
                "source_pdf_json": sanitize_source_pdf_meta(
                    _parse_json(
                        _row_value(row, "source_pdf_json"),
                        dict,
                        f"history[{row_id}].source_pdf_json",
                        None,
                    )
                ),
                "status": _row_value(row, "status", "done") or "done",
                "stage": _row_value(row, "stage"),
                "stage_label": _row_value(row, "stage_label"),
                "stage_index": int(_row_value(row, "stage_index", 0) or 0),
                "total_stages": int(_row_value(row, "total_stages", 0) or 0),
                "stages_done_json": _parse_json(
                    _row_value(row, "stages_done_json"), list, f"history[{row_id}].stages_done_json", []
                ),
                "source_format": _row_value(row, "source_format", "markdown") or "markdown",
                "experimental_logic_ir": bool(_row_value(row, "experimental_logic_ir", False)),
                "updated_at": _parse_datetime(
                    _row_value(row, "updated_at", row["created_at"]),
                    f"history[{row_id}].updated_at",
                ),
                "created_at": created_at,
            }
        )
    return result


def _build_settings(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        user_id = int(row["user_id"])
        api_url = _row_value(row, "llm_api_url", "") or ""
        model = _row_value(row, "llm_model", "") or ""
        api_key = _row_value(row, "llm_api_key", "") or ""
        raw_configs = _row_value(row, "llm_configs_json")
        if raw_configs:
            configs = _parse_json(raw_configs, dict, f"user_settings[{user_id}].llm_configs_json", {})
        else:
            legacy_configs = []
            if api_url or model or api_key:
                legacy_configs.append(
                    {"name": "默认配置", "api_url": api_url, "model_name": model, "api_key": api_key}
                )
            configs = {"configs": legacy_configs, "active_index": 0}
        result.append(
            {
                "user_id": user_id,
                "llm_api_url": api_url,
                "llm_model": model,
                "llm_api_key": api_key,
                "llm_configs_json": configs,
                "updated_at": _parse_datetime(row["updated_at"], f"user_settings[{user_id}].updated_at"),
            }
        )
    return result


def _build_proofs(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        key = f"{row['user_id']}:{row['graph_id']}:{row['node_id']}"
        result.append(
            {
                "user_id": int(row["user_id"]),
                "graph_id": str(row["graph_id"]),
                "node_id": int(row["node_id"]),
                "user_proof": _row_value(row, "user_proof", "") or "",
                "versions_json": _parse_json(row["versions_json"], list, f"proof[{key}].versions_json", []),
                "ai_messages_json": _parse_json(
                    row["ai_messages_json"], list, f"proof[{key}].ai_messages_json", []
                ),
                "imports_json": _parse_json(
                    _row_value(row, "imports_json"), list, f"proof[{key}].imports_json", []
                ),
                "updated_at": _parse_datetime(row["updated_at"], f"proof[{key}].updated_at"),
            }
        )
    return result


def _load_source(path: Path, mapping: dict[str, dict[str, Any]]) -> tuple[dict[str, int], dict[str, list[dict[str, Any]]]]:
    with _open_source_read_only(path) as connection:
        counts = _source_counts(connection)
        data = {
            "users": _build_users(_source_rows(connection, "users"), mapping),
            "history": _build_history(_source_rows(connection, "history")),
            "user_settings": _build_settings(_source_rows(connection, "user_settings")),
            "proof_workspaces": _build_proofs(_source_rows(connection, "proof_workspaces")),
        }
    user_ids = {row["id"] for row in data["users"]}
    for table in ("history", "user_settings", "proof_workspaces"):
        orphan_ids = {row["user_id"] for row in data[table]} - user_ids
        if orphan_ids:
            raise MigrationError(f"orphan user reference in {table}")
    return counts, data


def _create_backup(source: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = source.with_name(f"{source.name}.{timestamp}.bak")
    if backup.exists():
        raise MigrationError("backup path already exists")
    try:
        # SQLite 在线备份会合并已提交 WAL，避免普通文件复制遗漏最近数据。
        with _open_source_read_only(source) as source_connection:
            with sqlite3.connect(backup) as backup_connection:
                source_connection.backup(backup_connection)
    except Exception:
        # 仅清理本次新建的不完整备份，源库永不删除或修改。
        backup.unlink(missing_ok=True)
        raise
    return backup


def _table_count(connection, model: type) -> int:
    return int(connection.scalar(select(func.count()).select_from(model)) or 0)


def _orphan_count(connection, child_model: type) -> int:
    child = child_model.__table__
    parent = User.__table__
    statement = (
        select(func.count())
        .select_from(child.outerjoin(parent, child.c.user_id == parent.c.id))
        .where(parent.c.id.is_(None))
    )
    return int(connection.scalar(statement) or 0)


def _migrate_target(database_url: str, counts: dict[str, int], data: dict[str, list[dict[str, Any]]]) -> None:
    engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=1800)
    try:
        # 全部业务表共享同一事务，任何解析、约束或核对失败都会一起回滚。
        with engine.begin() as connection:
            target_models = (User, LoginSession, History, UserSettings, ProofWorkspace)
            if any(_table_count(connection, model) for model in target_models):
                raise MigrationError("target database is not empty")
            for model, key in (
                (User, "users"),
                (History, "history"),
                (UserSettings, "user_settings"),
                (ProofWorkspace, "proof_workspaces"),
            ):
                if data[key]:
                    connection.execute(model.__table__.insert(), data[key])
            actual = {
                "users": _table_count(connection, User),
                "history": _table_count(connection, History),
                "user_settings": _table_count(connection, UserSettings),
                "proof_workspaces": _table_count(connection, ProofWorkspace),
            }
            expected = {name: counts[name] for name in actual}
            if actual != expected or _table_count(connection, LoginSession) != 0:
                raise MigrationError("destination row counts do not match source")
            # 不依赖数据库外键开关；提交前显式核对所有用户从表都存在父账号。
            if any(
                _orphan_count(connection, model)
                for model in (History, UserSettings, ProofWorkspace)
            ):
                raise MigrationError("destination foreign-key references do not match users")
    finally:
        engine.dispose()


def _print_counts(counts: dict[str, int]) -> None:
    print(f"users: {counts['users']}")
    print(f"sessions: {counts['sessions']} (excluded)")
    print(f"history: {counts['history']}")
    print(f"user_settings: {counts['user_settings']}")
    print(f"proof_workspaces: {counts['proof_workspaces']}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True, type=Path)
    parser.add_argument("--database-url-env", required=True)
    parser.add_argument("--user-mapping", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        mapping = _load_mapping(args.user_mapping)
        if args.dry_run:
            counts, _data = _load_source(args.sqlite, mapping)
            _print_counts(counts)
            return 0
        database_url = os.environ.get(args.database_url_env)
        if not database_url:
            raise MigrationError(f"database URL environment variable is missing: {args.database_url_env}")
        try:
            database_url = validate_database_target(database_url)
        except RuntimeError as exc:
            raise MigrationError("database target validation failed") from exc
        # 非 dry-run 只迁移已固化的恢复快照，避免并发写入造成备份与目标不一致。
        backup = _create_backup(args.sqlite)
        counts, data = _load_source(backup, mapping)
        _print_counts(counts)
        _migrate_target(database_url, counts, data)
        print("migration completed and row counts verified")
        return 0
    except MigrationError as exc:
        print(f"migration failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # 未知异常只输出类型，避免驱动异常将连接密码或业务数据拼入日志。
        print(f"migration failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
