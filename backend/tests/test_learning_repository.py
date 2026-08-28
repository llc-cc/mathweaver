"""设置、图谱历史和证明工作区仓储测试。"""

from __future__ import annotations

from datetime import datetime, timezone

from storage.database import session_scope
from storage.learning_repository import JobSnapshot, LearningRepository
from storage.models import User


def _create_user(email: str) -> int:
    with session_scope() as session:
        user = User(
            email=email,
            display_name=email.split("@", 1)[0],
            role="student",
            password_hash="not-used",
            initial_password_pending=False,
        )
        session.add(user)
        session.flush()
        return user.id


def _snapshot(job_id: str, markdown: str = "# Convexity") -> JobSnapshot:
    return JobSnapshot(
        job_id=job_id,
        filename="convexity.md",
        status="done",
        nodes=[{"id": 1, "name": "Convex set"}],
        edges=[{"source": 1, "target": 2, "type": "prerequisite"}],
        source_markdown=markdown,
        latex_macros={"RR": "\\mathbb{R}"},
        source_pdf={"status": "ready", "available": True, "pdf_path": "C:/tmp/a.pdf"},
        stage="complete",
        stage_label="完成",
        stage_index=4,
        total_stages=4,
        stages_done=["extract", "relations"],
        source_format="markdown",
        source_origin="official_graph",
        experimental_logic_ir=False,
        created_at=datetime.now(timezone.utc),
    )


def test_settings_round_trip_preserves_active_configuration(database) -> None:
    user_id = _create_user("settings@example.com")
    repository = LearningRepository()
    configs = [{"name": "正式", "api_url": "https://api.example", "model_name": "m", "api_key": "k"}]

    repository.upsert_settings(user_id, configs, 0)

    assert repository.get_settings(user_id) == {"configs": configs, "active_index": 0}
    assert repository.get_active_llm_config(user_id) == {
        "api_url": "https://api.example",
        "model_name": "m",
        "api_key": "k",
    }


def test_history_graph_round_trip_preserves_nodes_edges_and_markdown(database) -> None:
    user_id = _create_user("history@example.com")
    repository = LearningRepository()
    markdown = "定义。" * 30000

    assert repository.upsert_job_progress(user_id, _snapshot("graph-1", markdown)) is True
    saved = repository.get_owned_history(user_id, "graph-1")

    assert saved is not None
    assert saved["nodes"] == [{"id": 1, "name": "Convex set"}]
    assert saved["edges"][0]["type"] == "prerequisite"
    assert saved["source_markdown"] == markdown
    assert saved["source_origin"] == "official_graph"
    assert saved["source_pdf"]["pdf_name"] == "a.pdf"
    assert "pdf_path" not in saved["source_pdf"]


def test_history_id_cannot_be_taken_over_by_another_user(database) -> None:
    owner_id = _create_user("owner@example.com")
    other_id = _create_user("other@example.com")
    repository = LearningRepository()
    assert repository.upsert_job_progress(owner_id, _snapshot("shared-id")) is True

    assert repository.upsert_job_progress(other_id, _snapshot("shared-id")) is False
    assert repository.get_owned_history(other_id, "shared-id") is None


def test_user_cannot_read_another_users_proof_workspace(database) -> None:
    owner_id = _create_user("proof-owner@example.com")
    other_id = _create_user("proof-other@example.com")
    repository = LearningRepository()
    repository.upsert_proof_workspace(
        owner_id,
        "graph-1",
        7,
        {"userProof": "private", "versions": [{"v": 1}]},
    )

    assert repository.list_proof_workspaces(other_id, "graph-1") == []
    assert repository.list_proof_workspaces(owner_id, "graph-1")[0]["userProof"] == "private"
