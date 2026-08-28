"""数据库引擎与事务会话管理。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_PRODUCTION_DATABASE_NAME = "mathweaver"


def validate_database_target(database_url: str) -> str:
    """阻止生产 MySQL 误连其他业务库；SQLite 只用于隔离测试。"""
    try:
        parsed = make_url(database_url)
    except Exception:
        raise RuntimeError("database target validation failed") from None
    if parsed.get_backend_name() == "sqlite":
        return database_url

    expected_name = os.environ.get("MATHWEAVER_DATABASE_NAME", "").strip()
    if (
        parsed.get_backend_name() != "mysql"
        or expected_name != _PRODUCTION_DATABASE_NAME
        or parsed.database != expected_name
    ):
        # 错误不得回显连接串、库名或密码，避免启动日志泄露生产凭据。
        raise RuntimeError("database target validation failed")
    return database_url


def build_engine(database_url: str) -> Engine:
    """构造带失效连接检测的唯一数据库引擎。"""
    return create_engine(
        validate_database_target(database_url),
        pool_pre_ping=True,
        pool_recycle=1800,
    )


def configure_database(database_url: str | None = None) -> None:
    """配置 Web 数据库；缺少连接串时禁止静默回退 SQLite。"""
    global _engine, _session_factory

    resolved_url = database_url or os.environ.get("MATHWEAVER_DATABASE_URL")
    if not resolved_url:
        raise RuntimeError("MATHWEAVER_DATABASE_URL must be configured")

    # 先完整构造新资源；失败时保留现有连接，避免全局状态半更新。
    new_engine = build_engine(resolved_url)
    new_session_factory = sessionmaker(bind=new_engine, expire_on_commit=False)
    previous_engine = _engine
    _engine = new_engine
    _session_factory = new_session_factory
    if previous_engine is not None:
        previous_engine.dispose()


def get_engine() -> Engine:
    """返回已配置引擎，延迟初始化仍遵循连接串必填约束。"""
    if _engine is None:
        configure_database()
    assert _engine is not None
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """返回绑定当前引擎的会话工厂。"""
    if _session_factory is None:
        configure_database()
    assert _session_factory is not None
    return _session_factory


def database_is_ready() -> bool:
    """执行无副作用查询作为就绪检查，失败时不暴露驱动详情。"""
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except (SQLAlchemyError, RuntimeError):
        return False


@contextmanager
def session_scope() -> Iterator[Session]:
    """提供原子事务：成功提交，异常时回滚本次全部写入。"""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        # 异常路径必须撤销未提交数据，防止部分业务状态被持久化。
        session.rollback()
        raise
    finally:
        session.close()
