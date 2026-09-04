import base64
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

import pytest


BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import api_v2
from integrations.neo4j_handler import get_graph_store, reset_graph_store
from scripts.migrate_storage import TARGET_TABLE_ORDER
from storage.database import reset_engine


def _integration_enabled():
    return (
        os.environ.get("MATHWEAVER_INTEGRATION_TESTS") == "1"
        and bool(os.environ.get("MATHWEAVER_TEST_DATABASE_URL", "").strip())
        and bool(os.environ.get("MATHWEAVER_TEST_NEO4J_URI", "").strip())
        and bool(os.environ.get("MATHWEAVER_TEST_NEO4J_PASSWORD_FILE", "").strip())
    )


def _clear_storage():
    with api_v2.connect_database() as connection:
        connection.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in reversed((*TARGET_TABLE_ORDER, "graph_registry")):
            connection.execute(f"DELETE FROM `{table}`")
        connection.execute("SET FOREIGN_KEY_CHECKS = 1")
    store = get_graph_store()
    with store.driver.session(database=store.database) as session:
        session.run("MATCH (n) DETACH DELETE n").consume()


@contextmanager
def _storage_fixture():
    if not _integration_enabled():
        pytest.skip("set dedicated Docker MySQL/Neo4j integration-test credentials")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
        root = Path(temp_dir)
        data_key_file = root / "data-key.txt"
        data_key_file.write_text(
            base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("="),
            encoding="utf-8",
        )
        previous = (
            api_v2._DATA_ROOT,
            api_v2._SOURCE_PDF_ROOT,
            api_v2._EDUCATION_ROOT,
            api_v2._EDUCATION_SNAPSHOT_ROOT,
            api_v2._EDUCATION_ASSIGNMENT_SOURCE_ROOT,
        )
        api_v2._DATA_ROOT = root
        api_v2._SOURCE_PDF_ROOT = root / "uploads" / "source_pdfs"
        api_v2._EDUCATION_ROOT = root / "education"
        api_v2._EDUCATION_SNAPSHOT_ROOT = root / "education" / "snapshots"
        api_v2._EDUCATION_ASSIGNMENT_SOURCE_ROOT = root / "education" / "assignment_sources"
        environment = patch.dict(
            os.environ,
            {
                "DATABASE_URL": os.environ["MATHWEAVER_TEST_DATABASE_URL"],
                "NEO4J_URI": os.environ["MATHWEAVER_TEST_NEO4J_URI"],
                "NEO4J_USER": os.environ.get("MATHWEAVER_TEST_NEO4J_USER", "neo4j"),
                "NEO4J_PASSWORD_FILE": os.environ["MATHWEAVER_TEST_NEO4J_PASSWORD_FILE"],
                "MATHWEAVER_DATA_KEY_FILE": str(data_key_file),
                "MATHGRAPH_DATA_DIR": str(root),
            },
            clear=False,
        )
        environment.start()
        reset_engine()
        reset_graph_store()
        try:
            _clear_storage()
            api_v2._jobs.clear()
            api_v2._job_runtimes.clear()
            api_v2.app.config.update(TESTING=True)
            yield api_v2.app.test_client()
        finally:
            api_v2._jobs.clear()
            api_v2._job_runtimes.clear()
            _clear_storage()
            reset_graph_store()
            reset_engine()
            environment.stop()
            (
                api_v2._DATA_ROOT,
                api_v2._SOURCE_PDF_ROOT,
                api_v2._EDUCATION_ROOT,
                api_v2._EDUCATION_SNAPSHOT_ROOT,
                api_v2._EDUCATION_ASSIGNMENT_SOURCE_ROOT,
            ) = previous


def _upload_json(payload, filename):
    return io.BytesIO(json.dumps(payload, ensure_ascii=False).encode("utf-8")), filename


def test_agent_import_and_history_markdown():
    with _storage_fixture() as client:
        nodes = [
            {"global_id": "def-1", "title": "群", "type": "definition", "content": "群的定义"},
            {"global_id": "thm-1", "title": "拉格朗日定理", "type": "theorem", "content": "定理内容"},
        ]
        edges = [
            {"from": "def-1", "to": "thm-1", "关系名称": "supports", "关系解释": "定义支撑定理"},
            {"from": "missing", "to": "thm-1", "relation": "ignored"},
        ]
        response = client.post(
            "/api/v2/agent-import",
            data={
                "nodes_file": _upload_json(nodes, "nodes.json"),
                "edges_file": _upload_json(edges, "edges.json"),
                "markdown_file": (io.BytesIO("# 群\n\n拉格朗日定理".encode("utf-8")), "source.md"),
            },
            content_type="multipart/form-data",
        )
        assert response.status_code == 201, response.get_json()
        body = response.get_json()
        assert len(body["result"]["nodes"]) == 2
        assert len(body["result"]["edges"]) == 1
        assert body["has_markdown"] is True
        assert len(body["warnings"]) == 1

        register = client.post(
            "/api/v2/auth/register",
            json={"email": "agent-test@example.com", "password": "password123"},
        )
        assert register.status_code == 201, register.get_json()
        token = register.get_json()["token"]
        saved = client.post(
            "/api/v2/history",
            json={"job_id": body["job_id"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert saved.status_code == 201, saved.get_json()
        history_id = saved.get_json()["id"]
        markdown = client.get(
            f"/api/v2/history/{history_id}/markdown",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert markdown.status_code == 200
        assert markdown.get_json()["markdown"].startswith("# 群")


def test_normalized_ids_title_fallback_and_zero_edges():
    nodes = {
        "nodes": [
            {"id": 7, "global_id": "g-7", "title_zh": "节点甲", "title_en": "Node A", "type": "definition"},
            {"id": 9, "global_id": "g-9", "title_zh": "节点乙", "title_en": "Node B", "type": "theorem"},
        ]
    }
    normalized = api_v2._normalize_nodes(nodes)
    assert [node["id"] for node in normalized] == [7, 9]

    edges, warnings = api_v2._normalize_edges(
        [
            {"source": "节点甲", "target": "节点乙", "label": "title match"},
            {"from": "not-found", "to": "g-9", "label": "skip"},
        ],
        normalized,
        include_warnings=True,
    )
    assert len(edges) == 1
    assert edges[0]["from"] == 7 and edges[0]["to"] == 9
    assert len(warnings) == 1

    dict_nodes = api_v2._normalize_nodes({
        "dict-id-a": {"title": "Dictionary Node A"},
        "dict-id-b": {"title": "Dictionary Node B"},
    })
    dict_edges = api_v2._normalize_edges(
        [{"from": "dict-id-a", "to": "dict-id-b", "label": "dictionary key match"}],
        dict_nodes,
    )
    assert len(dict_edges) == 1

    zero_edges, zero_warnings = api_v2._normalize_edges(
        [{"from": "missing-a", "to": "missing-b"}],
        normalized,
        include_warnings=True,
    )
    assert zero_edges == []
    assert len(zero_warnings) == 1

    duplicate_nodes = api_v2._normalize_nodes([
        {"global_id": "duplicate", "title": "First"},
        {"global_id": "duplicate", "title": "Second"},
    ])
    try:
        api_v2._normalize_edges([], duplicate_nodes)
    except ValueError as exc:
        assert "Duplicate node global_id" in str(exc)
    else:
        raise AssertionError("Expected duplicate global_id to be rejected")


def test_tex_source_environments_are_preserved_for_frontend():
    nodes = [
        {
            "global_id": "exa:e1.1.8",
            "node_type": "example",
            "title": {"english": "Green's formula"},
            "remark": {
                "original_form": (
                    "Let $D\\subset{\\bf R}^{2}$.\\n"
                    "\\begin{itemize}\\n"
                    "\\item {\\bf Green's first formula:} Then\\n"
                    "\\begin{equation}\\n"
                    "\\int_D d\\omega.\\label{eq:green}\\n"
                    "\\end{equation}\\n"
                    "\\item For $\\omega=Pdx+Qdy$, continue.\\n"
                    "\\item Since this follows from {\\color{blue}{{\\bf Theorem \\ref{thm:t1.1.14}}}}.\\n"
                    "\\end{itemize}"
                )
            },
        }
    ]
    normalized = api_v2._normalize_nodes(nodes)
    content = normalized[0]["content"]
    assert "\\begin{itemize}" in content
    assert "\\item" in content
    assert "\\begin{equation}" in content
    assert "\\label{eq:green}" in content
    assert "\\int_D d\\omega." in content
    assert "\\color" in content
    assert "\\ref{thm:t1.1.14}" in content
    assert normalized[0]["source_text"] == content
    assert normalized[0]["source_statement"] == content


def test_eqnarray_source_environment_is_preserved_for_frontend():
    nodes = [
        {
            "global_id": "thm:t1.1.5",
            "node_type": "theorem",
            "title": {"english": "Properties of Pullback"},
            "remark": {
                "original_form": (
                    "If $\\omega_1,\\omega_2$ are forms, then\\n"
                    "\\begin{eqnarray}\\n"
                    "\\bbf^{\\ast}(\\omega_{1}+\\omega_{2})&=&\\bbf^{\\ast}\\omega_{1}+\\bbf^{\\ast}\\omega_{2},\\nonumber\\\\\\n"
                    "\\bbf^{\\ast}(g\\!\\omega)&=&(\\bbf^{\\ast}g)(\\bbf^{\\ast}\\omega).\\nonumber\\n"
                    "\\end{eqnarray}"
                )
            },
            "conditions": ["This follows from (\\ref{1.1.3.15})."],
        }
    ]
    normalized = api_v2._normalize_nodes(nodes)
    content = normalized[0]["content"]
    assert "\\begin{eqnarray}" in content
    assert "\\end{eqnarray}" in content
    assert "\\nonumber" in content
    assert "\\bbf^{\\ast}" in content
    assert normalized[0]["conditions"] == ["This follows from (\\ref{1.1.3.15})."]
    assert normalized[0]["source_statement"] == content


def test_agent_import_validation():
    client = api_v2.app.test_client()
    invalid = client.post(
        "/api/v2/agent-import",
        data={
            "nodes_file": (io.BytesIO(b"not-json"), "nodes.json"),
            "edges_file": _upload_json([], "edges.json"),
        },
        content_type="multipart/form-data",
    )
    assert invalid.status_code == 400

    empty = client.post(
        "/api/v2/agent-import",
        data={
            "nodes_file": _upload_json([], "nodes.json"),
            "edges_file": _upload_json([], "edges.json"),
        },
        content_type="multipart/form-data",
    )
    assert empty.status_code == 400


if __name__ == "__main__":
    if _integration_enabled():
        test_agent_import_and_history_markdown()
    else:
        print("agent import history integration test skipped (Docker test credentials not configured)")
    test_normalized_ids_title_fallback_and_zero_edges()
    test_tex_source_environments_are_preserved_for_frontend()
    test_eqnarray_source_environment_is_preserved_for_frontend()
    test_agent_import_validation()
    print("agent import tests passed")
