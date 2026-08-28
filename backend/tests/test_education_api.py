"""班级、快照和作业 Web 契约集成测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

from storage.database import session_scope
from storage.models import User


def _teacher_token(client, email: str) -> str:
    with session_scope() as session:
        session.add(
            User(
                email=email,
                display_name=email.split("@", 1)[0],
                role="teacher",
                password_hash=generate_password_hash("secret1"),
                initial_password_pending=False,
            )
        )
    response = client.post(
        "/api/v2/auth/login",
        json={"email": email, "password": "secret1", "educationRole": "teacher"},
    )
    assert response.status_code == 200
    return response.get_json()["token"]


def test_class_snapshot_and_assignment_routes_never_expose_internal_id(
    database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MATHGRAPH_DATA_DIR", str(Path(database.url.database).parent))
    import api_v2

    monkeypatch.setattr(api_v2, "_education_ai_task", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(api_v2, "_education_generate_initial_assessments", lambda **_kwargs: None)
    client = api_v2.app.test_client()
    token = _teacher_token(client, "api-teacher@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    created = client.post(
        "/api/v2/edu/classes", json={"title": "凸优化"}, headers=headers
    )
    assert created.status_code == 201
    class_body = created.get_json()["class"]
    assert isinstance(class_body["id"], str) and len(class_body["id"]) > 20
    assert "internalId" not in class_body

    snapshot = client.post(
        f"/api/v2/edu/classes/{class_body['id']}/snapshots",
        json={
            "sourceGraphId": "api-v1",
            "filename": "api.md",
            "nodes": [{"id": 1, "global_id": "g-1", "title_zh": "集合"}],
            "edges": [],
            "sourceMarkdown": "# API",
            "latexMacros": {},
        },
        headers=headers,
    )
    assert snapshot.status_code == 201
    snapshot_id = snapshot.get_json()["snapshot"]["id"]

    assignment = client.post(
        f"/api/v2/edu/classes/{class_body['id']}/assignments",
        json={"snapshotId": snapshot_id, "targetNodeId": 1, "title": "集合练习"},
        headers=headers,
    )
    assert assignment.status_code == 201
    assert assignment.get_json()["assignment"]["classId"] == class_body["id"]


def test_unauthenticated_user_cannot_list_classes(database) -> None:
    import api_v2

    assert api_v2.app.test_client().get("/api/v2/edu/classes").status_code == 401
