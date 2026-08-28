"""教学资源所有权与成员生命周期权限测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from test_education_api import _teacher_token


def test_teacher_cannot_manage_another_teachers_class(
    database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MATHGRAPH_DATA_DIR", str(Path(database.url.database).parent))
    import api_v2

    client = api_v2.app.test_client()
    owner = _teacher_token(client, "owner-teacher@example.com")
    outsider = _teacher_token(client, "other-teacher@example.com")
    created = client.post(
        "/api/v2/edu/classes",
        json={"title": "私有班级"},
        headers={"Authorization": f"Bearer {owner}"},
    ).get_json()["class"]

    response = client.patch(
        f"/api/v2/edu/classes/{created['id']}",
        json={"title": "越权修改"},
        headers={"Authorization": f"Bearer {outsider}"},
    )

    assert response.status_code in {403, 404}
