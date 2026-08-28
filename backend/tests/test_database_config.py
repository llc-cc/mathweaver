"""数据库引擎与事务边界测试。"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from storage.database import (
    build_engine,
    configure_database,
    get_engine,
    get_session_factory,
    session_scope,
    validate_database_target,
)


def test_database_url_is_required_in_web_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MATHWEAVER_DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="MATHWEAVER_DATABASE_URL"):
        configure_database()


def test_build_engine_enables_stale_connection_detection() -> None:
    engine = build_engine("sqlite+pysqlite:///:memory:")
    try:
        assert engine.pool._pre_ping is True  # noqa: SLF001 - 直接验证连接池安全配置
    finally:
        engine.dispose()


def test_get_session_factory_uses_configured_engine() -> None:
    configure_database("sqlite+pysqlite:///:memory:")

    factory = get_session_factory()

    assert factory.kw["bind"] is get_engine()
    assert factory.kw["expire_on_commit"] is False


def test_session_scope_commits_on_success() -> None:
    configure_database("sqlite+pysqlite:///:memory:")
    with get_engine().begin() as connection:
        connection.execute(text("CREATE TABLE values_for_test (value INTEGER NOT NULL)"))

    with session_scope() as session:
        session.execute(text("INSERT INTO values_for_test (value) VALUES (1)"))

    with get_engine().connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM values_for_test")) == 1


def test_session_scope_rolls_back_after_exception() -> None:
    configure_database("sqlite+pysqlite:///:memory:")
    with get_engine().begin() as connection:
        connection.execute(text("CREATE TABLE values_for_test (value INTEGER NOT NULL)"))

    with pytest.raises(RuntimeError, match="force rollback"):
        with session_scope() as session:
            session.execute(text("INSERT INTO values_for_test (value) VALUES (1)"))
            raise RuntimeError("force rollback")

    with get_engine().connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM values_for_test")) == 0


def test_mysql_target_must_be_explicit_mathweaver_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MATHWEAVER_DATABASE_NAME", "mathweaver")

    assert validate_database_target(
        "mysql+pymysql://user:secret@127.0.0.1:3306/mathweaver"
    ).endswith("/mathweaver")
    with pytest.raises(RuntimeError, match="database target validation failed") as error:
        validate_database_target("mysql+pymysql://user:secret@127.0.0.1:3306/other")

    assert "secret" not in str(error.value)
