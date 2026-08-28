"""后端测试的公共路径与隔离夹具。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# 部分测试在收集阶段导入 Web 应用；显式测试连接串避免误写开发机 auth.db。
os.environ.setdefault("MATHWEAVER_DATABASE_URL", "sqlite+pysqlite:///:memory:")


@pytest.fixture
def database(tmp_path: Path):
    """为每个仓储/API 用例创建独立数据库，避免全局引擎造成跨用例污染。"""
    from storage.database import configure_database, get_engine
    from storage.models import Base

    database_path = tmp_path / "mathweaver-test.db"
    configure_database(f"sqlite+pysqlite:///{database_path.as_posix()}")
    engine = get_engine()
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
