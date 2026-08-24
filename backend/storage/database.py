"""数据库引擎与事务会话管理。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from storage.metrics import operational_metrics

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_PRODUCTION_DATABASE_NAME = "mathweaver"


def validate_database_target(database_url: str) -> str:
    """阻止生产 MySQL 误连其他业务库；SQLite 仅用于测试和显式迁移。"""
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


def configure_database(url: str | None = None) -> None:
    """配置唯一的数据库连接；运行环境必须显式提供连接串。"""
    global _engine, _session_factory

    database_url = url or os.environ.get("MATHWEAVER_DATABASE_URL")
    if not database_url:
        raise RuntimeError("MATHWEAVER_DATABASE_URL must be configured")
    database_url = validate_database_target(database_url)

    # 先完整构造新资源；构造失败时保留当前可用连接，避免全局状态半更新。
    new_engine = create_engine(
        database_url,
        pool_pre_ping=True,
        pool_recycle=1800,
    )
    # 每个替换后的 engine 独立注册池事件，避免重配置时继续统计已释放连接池。
    event.listen(new_engine, "checkout", lambda *_args: operational_metrics.pool_checkout())
    event.listen(new_engine, "checkin", lambda *_args: operational_metrics.pool_checkin())
    new_session_factory = sessionmaker(bind=new_engine, expire_on_commit=False)
    previous_engine = _engine
    _engine = new_engine
    _session_factory = new_session_factory
    if previous_engine is not None:
        # 切换完成后释放旧连接池，避免重配后遗留数据库连接。
        previous_engine.dispose()


def get_engine() -> Engine:
    """返回已配置引擎，延迟配置仍遵循环境变量必填约束。"""
    if _engine is None:
        configure_database()
    assert _engine is not None
    return _engine


def database_is_ready() -> bool:
    """执行无副作用查询作为 Web readiness，失败时不暴露驱动详情。"""
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except (SQLAlchemyError, RuntimeError):
        return False


def validate_mysql_packet(engine: Engine, required_bytes: int) -> int:
    """校验大 JSON 上限并预留 1 MiB 协议开销，只返回数值而不泄露连接信息。"""
    required = int(required_bytes)
    if required <= 0:
        raise ValueError("required_bytes must be positive")
    with engine.connect() as connection:
        configured = int(connection.execute(text("SELECT @@max_allowed_packet")).scalar_one())
    if configured < required + 1024 * 1024:
        raise RuntimeError("max_allowed_packet is below the required payload boundary")
    return configured


@contextmanager
def session_scope() -> Iterator[Session]:
    """提供原子事务：成功提交，异常时回滚所有本次写入。"""
    global _session_factory

    if _session_factory is None:
        configure_database()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
        session.commit()
        operational_metrics.record_database_transaction("success")
    except Exception:
        # 异常路径必须撤销未提交数据，防止部分业务状态被持久化。
        session.rollback()
        operational_metrics.record_database_transaction("failure")
        raise
    finally:
        session.close()
