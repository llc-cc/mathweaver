"""数据库模型测试的公共隔离配置。"""

from __future__ import annotations

import os

import pytest

from storage.database import configure_database, get_engine
from storage.models import Base


# API 模块会在测试收集阶段完成数据库启动，因此预先提供显式测试 URL。
os.environ.setdefault("MATHWEAVER_DATABASE_URL", "sqlite+pysqlite:///:memory:")


@pytest.fixture(autouse=True)
def database(monkeypatch):
    """每个测试使用独立内存库，避免事务结果跨测试泄漏。"""
    monkeypatch.setenv("MATHWEAVER_DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.delenv("MATHWEAVER_SESSION_TTL_SECONDS", raising=False)
    configure_database(os.environ["MATHWEAVER_DATABASE_URL"])
    Base.metadata.create_all(get_engine())
    yield
    Base.metadata.drop_all(get_engine())
