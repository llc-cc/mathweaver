"""正式 Web 进程不得回退 SQLite，并暴露数据库就绪状态。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


API_SOURCE = Path(__file__).resolve().parents[1] / "api_v2.py"


def test_web_module_has_no_sqlite_import_or_connection_call() -> None:
    tree = ast.parse(API_SOURCE.read_text(encoding="utf-8"))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "sqlite3" not in imported_modules
    assert "_get_db" not in called_names


def test_ready_returns_200_when_database_query_succeeds(database) -> None:
    import api_v2

    response = api_v2.app.test_client().get("/api/v2/ready")

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "database": "ready"}


def test_ready_returns_503_without_falling_back_when_database_is_down(
    database, monkeypatch: pytest.MonkeyPatch
) -> None:
    import api_v2

    class UnavailableEngine:
        def connect(self):
            raise RuntimeError("sensitive database detail")

    monkeypatch.setattr(api_v2, "get_engine", lambda: UnavailableEngine())
    response = api_v2.app.test_client().get("/api/v2/ready")

    assert response.status_code == 503
    assert response.get_json() == {
        "ok": False,
        "database": "unavailable",
        "code": "database_unavailable",
    }
    assert "sensitive" not in response.get_data(as_text=True)
