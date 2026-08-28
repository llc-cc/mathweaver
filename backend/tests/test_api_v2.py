"""核心 Web 路由使用统一仓储的集成测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from storage.learning_repository import JobSnapshot, LearningRepository


def _register(client, email: str) -> str:
    response = client.post(
        "/api/v2/auth/register", json={"email": email, "password": "secret1"}
    )
    assert response.status_code == 201
    return response.get_json()["token"]


def test_settings_history_markdown_and_proof_routes_avoid_sqlite(
    database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MATHGRAPH_DATA_DIR", str(Path(database.url.database).parent))
    import api_v2

    monkeypatch.setattr(
        api_v2,
        "_get_db",
        lambda: (_ for _ in ()).throw(AssertionError("core route called SQLite")),
    )
    client = api_v2.app.test_client()
    token = _register(client, "routes@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    settings = {
        "configs": [{"name": "正式", "api_url": "https://api.example", "model_name": "m", "api_key": "k"}],
        "active_index": 0,
    }
    assert client.put("/api/v2/settings", json=settings, headers=headers).status_code == 200
    assert client.get("/api/v2/settings", headers=headers).get_json() == settings

    user_id = client.get("/api/v2/auth/me", headers=headers).get_json()["id"]
    LearningRepository().upsert_job_progress(
        user_id,
        JobSnapshot(
            job_id="route-graph",
            filename="route.md",
            status="done",
            nodes=[{"id": 1}],
            edges=[],
            source_markdown="# Route graph",
            latex_macros={},
            source_pdf=None,
            stage="complete",
            stage_label="完成",
            stage_index=1,
            total_stages=1,
            stages_done=["complete"],
            source_format="markdown",
            source_origin="markdown",
            experimental_logic_ir=False,
            created_at=datetime.now(timezone.utc),
        ),
    )
    assert client.get("/api/v2/history", headers=headers).status_code == 200
    markdown = client.get(
        "/api/v2/history/route-graph/markdown", headers=headers
    )
    assert markdown.get_json()["markdown"] == "# Route graph"

    workspace = {
        "userProof": "proof",
        "versions": [],
        "aiMessages": [],
        "imports": [],
    }
    assert client.put(
        "/api/v2/proof-workspaces/route-graph/1", json=workspace, headers=headers
    ).status_code == 200
    listed = client.get(
        "/api/v2/proof-workspaces/route-graph", headers=headers
    ).get_json()
    assert listed["workspaces"][0]["userProof"] == "proof"
