from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any, Iterable, Iterator, Mapping, Sequence

from flask import g
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

_SCHEMA_REVISION = "20260901_0004"
_ENGINE: Engine | None = None
_ENGINE_URL: str | None = None
_SESSION_FACTORY: sessionmaker[Session] | None = None
_ENGINE_LOCK = threading.Lock()
_PRODUCTION_DATABASE_NAME = "mathweaver"
_ISO_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?$"
)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DatabaseConfigurationError(RuntimeError):
    pass


class DatabaseRow(Mapping[str, Any]):
    def __init__(self, keys: Sequence[str], values: Sequence[Any]):
        self._keys = tuple(keys)
        self._values = tuple(_api_value(value) for value in values)
        self._mapping = dict(zip(self._keys, self._values))
        self._casefold_mapping = {
            str(name).casefold(): value for name, value in self._mapping.items()
        }

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        try:
            return self._mapping[key]
        except KeyError:
            return self._casefold_mapping[key.casefold()]

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def keys(self):
        return self._mapping.keys()

    def values(self):
        return self._mapping.values()

    def items(self):
        return self._mapping.items()


class DatabaseResult:
    def __init__(self, keys: Sequence[str] = (), rows: Sequence[Sequence[Any]] = (), rowcount: int = 0):
        self._rows = [DatabaseRow(keys, row) for row in rows]
        self.rowcount = rowcount
        self._offset = 0

    def fetchone(self) -> DatabaseRow | None:
        if self._offset >= len(self._rows):
            return None
        row = self._rows[self._offset]
        self._offset += 1
        return row

    def fetchall(self) -> list[DatabaseRow]:
        rows = self._rows[self._offset :]
        self._offset = len(self._rows)
        return rows

    def __iter__(self) -> Iterator[DatabaseRow]:
        return iter(self.fetchall())


class DatabaseConnection:
    """Small compatibility surface over a SQLAlchemy 2.0 MySQL connection.

    Existing endpoint code still uses DB-API-style execute/fetch calls. This
    boundary keeps those call sites surgical while the runtime is MySQL-only.
    SQL is sent through SQLAlchemy and qmark placeholders are converted to the
    PyMySQL driver format without rewriting quoted question marks.
    """

    def __init__(self, connection: Connection):
        self._connection = connection

    def execute(self, statement: str, parameters: Sequence[Any] | None = None) -> DatabaseResult:
        sql, names = _qmark_to_named(statement)
        values = tuple(_database_value(value) for value in (parameters or ()))
        if len(values) != len(names):
            raise ValueError(f"SQL parameter count mismatch: expected {len(names)}, got {len(values)}")
        result = self._connection.execute(text(sql), dict(zip(names, values)))
        return _buffer_result(result)

    def executemany(self, statement: str, parameters: Iterable[Sequence[Any]]) -> DatabaseResult:
        sql, names = _qmark_to_named(statement)
        rows = []
        for row in parameters:
            values = tuple(_database_value(value) for value in row)
            if len(values) != len(names):
                raise ValueError(f"SQL parameter count mismatch: expected {len(names)}, got {len(values)}")
            rows.append(dict(zip(names, values)))
        if not rows:
            return DatabaseResult()
        result = self._connection.execute(text(sql), rows)
        return _buffer_result(result)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "DatabaseConnection":
        return self

    def __exit__(self, exc_type, exc, _tb) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()


def _pool_setting(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(0, min(maximum, value))


def validate_database_target(database_url: str) -> str:
    """Prevent production configuration from silently targeting another schema."""
    try:
        parsed = make_url(database_url)
    except Exception:
        raise RuntimeError("database target validation failed") from None
    if parsed.get_backend_name() == "sqlite":
        return database_url
    expected_name = os.environ.get("MATHWEAVER_DATABASE_NAME", "").strip()
    integration_target = os.environ.get("MATHWEAVER_TEST_DATABASE_URL", "").strip()
    integration_allowed = (
        os.environ.get("MATHWEAVER_INTEGRATION_TESTS") == "1"
        and database_url == integration_target
        and parsed.database == "mathweaver_test"
    )
    if (
        parsed.get_backend_name() != "mysql"
        or not integration_allowed
        and (
            expected_name != _PRODUCTION_DATABASE_NAME
            or parsed.database != expected_name
        )
    ):
        raise RuntimeError("database target validation failed")
    return database_url


def _database_url() -> str:
    url = (
        os.environ.get("MATHWEAVER_DATABASE_URL", "").strip()
        or os.environ.get("DATABASE_URL", "").strip()
    )
    if not url:
        raise DatabaseConfigurationError(
            "MATHWEAVER_DATABASE_URL or DATABASE_URL is required"
        )
    if not url.startswith("mysql+pymysql://"):
        raise DatabaseConfigurationError("database URL must use mysql+pymysql")
    return url


def build_engine(database_url: str) -> Engine:
    validated_url = validate_database_target(database_url)
    options: dict[str, Any] = {
        "pool_pre_ping": True,
        "pool_recycle": 1800,
    }
    if make_url(validated_url).get_backend_name() == "mysql":
        options.update({
            "isolation_level": "READ COMMITTED",
            "future": True,
            "pool_size": max(1, _pool_setting("MATHWEAVER_DB_POOL_SIZE", 4, 32)),
            "max_overflow": _pool_setting("MATHWEAVER_DB_MAX_OVERFLOW", 4, 64),
            "pool_timeout": max(1, _pool_setting("MATHWEAVER_DB_POOL_TIMEOUT", 10, 60)),
            "pool_use_lifo": True,
        })
    return create_engine(validated_url, **options)


def configure_database(database_url: str | None = None) -> None:
    global _ENGINE, _ENGINE_URL, _SESSION_FACTORY
    resolved_url = database_url or os.environ.get("MATHWEAVER_DATABASE_URL")
    if not resolved_url:
        raise RuntimeError("MATHWEAVER_DATABASE_URL must be configured")
    new_engine = build_engine(resolved_url)
    new_factory = sessionmaker(bind=new_engine, expire_on_commit=False)
    with _ENGINE_LOCK:
        previous = _ENGINE
        _ENGINE = new_engine
        _ENGINE_URL = resolved_url
        _SESSION_FACTORY = new_factory
    if previous is not None:
        previous.dispose()


def get_engine() -> Engine:
    global _ENGINE, _ENGINE_URL, _SESSION_FACTORY
    with _ENGINE_LOCK:
        if _ENGINE is None:
            url = _database_url()
            _ENGINE = build_engine(url)
            _ENGINE_URL = url
            _SESSION_FACTORY = sessionmaker(bind=_ENGINE, expire_on_commit=False)
        return _ENGINE


def reset_engine() -> None:
    global _ENGINE, _ENGINE_URL, _SESSION_FACTORY
    with _ENGINE_LOCK:
        if _ENGINE is not None:
            _ENGINE.dispose()
        _ENGINE = None
        _ENGINE_URL = None
        _SESSION_FACTORY = None


def get_session_factory() -> sessionmaker[Session]:
    if _SESSION_FACTORY is None:
        get_engine()
    assert _SESSION_FACTORY is not None
    return _SESSION_FACTORY


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def database_is_ready() -> bool:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except (SQLAlchemyError, RuntimeError):
        return False


def connect_database() -> DatabaseConnection:
    return DatabaseConnection(get_engine().connect())


def get_database() -> DatabaseConnection:
    if "db" not in g:
        g.db = connect_database()
    return g.db


def close_request_database(_exc: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def database_health() -> dict[str, Any]:
    try:
        with connect_database() as db:
            db.execute("SELECT 1 AS ok").fetchone()
            row = db.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
            registry = db.execute(
                """SELECT COUNT(*) AS count
                     FROM information_schema.tables
                    WHERE table_schema = DATABASE() AND table_name = 'graph_registry'"""
            ).fetchone()
            course_graph_order = db.execute(
                """SELECT COUNT(*) AS count
                     FROM information_schema.tables
                     WHERE table_schema = DATABASE() AND table_name = 'education_course_graph_order'"""
            ).fetchone()
            game_tables = db.execute(
                """SELECT COUNT(*) AS count FROM information_schema.tables
                     WHERE table_schema = DATABASE() AND table_name IN (
                       'education_game_mode_periods', 'education_checkins', 'education_chest_openings',
                       'education_student_wallets', 'education_gem_ledger', 'education_student_inventory',
                       'education_shop_items', 'education_shop_redemptions', 'education_growth_rewards',
                       'education_student_collectibles', 'education_weekly_goal_awards', 'education_class_xp_profiles',
                       'education_class_xp_contributions', 'education_student_stage_progress', 'education_challenge_unlock_rules'
                     )"""
            ).fetchone()
            gem_ledger_metadata = db.execute(
                """SELECT COUNT(*) AS count
                     FROM information_schema.columns
                    WHERE table_schema = DATABASE()
                      AND table_name = 'education_gem_ledger'
                      AND column_name = 'metadata_json'"""
            ).fetchone()
            forbidden = db.execute(
                """SELECT COUNT(*) AS count
                     FROM information_schema.columns
                    WHERE table_schema = DATABASE()
                      AND table_name IN ('history', 'education_snapshots')
                      AND column_name IN ('nodes_json', 'edges_json')"""
            ).fetchone()
            constraints = db.execute(
                """SELECT constraint_name
                     FROM information_schema.table_constraints
                    WHERE table_schema = DATABASE()
                      AND constraint_name IN (
                        'ux_users_email', 'fk_sessions_user_id',
                        'fk_history_user_id', 'fk_education_snapshots_class_id',
                        'fk_education_game_events_membership',
                        'fk_education_student_achievements_membership'
                      )"""
            ).fetchall()
        revision = row["version_num"] if row else None
        constraint_names = {item["constraint_name"] for item in constraints}
        expected_constraints = {
            "ux_users_email",
            "fk_sessions_user_id",
            "fk_history_user_id",
            "fk_education_snapshots_class_id",
            "fk_education_game_events_membership",
            "fk_education_student_achievements_membership",
        }
        ok = (
            revision == _SCHEMA_REVISION
            and int(registry["count"] or 0) == 1
            and int(course_graph_order["count"] or 0) == 1
            and int(game_tables["count"] or 0) == 15
            and int(gem_ledger_metadata["count"] or 0) == 1
            and int(forbidden["count"] or 0) == 0
            and constraint_names == expected_constraints
        )
        return {
            "ok": ok,
            "revision": revision,
            "expected": _SCHEMA_REVISION,
            "graph_registry": int(registry["count"] or 0) == 1,
            "course_graph_order": int(course_graph_order["count"] or 0) == 1,
            "game_economy_tables": int(game_tables["count"] or 0) == 15,
            "gem_ledger_metadata": int(gem_ledger_metadata["count"] or 0) == 1,
            "forbidden_graph_columns": int(forbidden["count"] or 0),
            "constraints": sorted(constraint_names),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "expected": _SCHEMA_REVISION}


def _buffer_result(result) -> DatabaseResult:
    rowcount = int(result.rowcount or 0)
    if not result.returns_rows:
        return DatabaseResult(rowcount=rowcount)
    keys = list(result.keys())
    rows = [tuple(row) for row in result.fetchall()]
    return DatabaseResult(keys, rows, rowcount)


def _database_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return value
    if isinstance(value, str):
        if _ISO_DATETIME_RE.fullmatch(value):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        if _DATE_RE.fullmatch(value):
            return date.fromisoformat(value)
    return value


def _api_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _qmark_to_named(sql: str) -> tuple[str, tuple[str, ...]]:
    out: list[str] = []
    names: list[str] = []
    quote: str | None = None
    escaped = False
    for char in sql:
        if quote:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
            out.append(char)
        elif char == "?":
            name = f"p{len(names)}"
            names.append(name)
            out.append(f":{name}")
        else:
            out.append(char)
    return "".join(out), tuple(names)
