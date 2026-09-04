from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag


from backend.integrations.neo4j_handler import Neo4jHandler, graph_checksum
from backend.storage import graph_service
from backend.storage.database import DatabaseResult, DatabaseRow, _api_value, _database_value, _qmark_to_named
from backend.storage.secrets import decrypt_secret, encrypt_secret, session_token_hash


def test_qmark_translation_preserves_quoted_question_marks():
    sql = "SELECT '?', \"?\", `?`, value FROM sample WHERE a = ? AND note = 'why?'"
    named, names = _qmark_to_named(sql)
    assert named == "SELECT '?', \"?\", `?`, value FROM sample WHERE a = :p0 AND note = 'why?'"
    assert names == ("p0",)


def test_database_row_supports_name_and_index_access():
    row = DatabaseRow(("id", "created_at"), (7, datetime(2026, 9, 1, 12, 30, 15)))
    assert row[0] == 7
    assert row["id"] == 7
    assert row["created_at"] == "2026-09-01T12:30:15"
    assert dict(row.items())["id"] == 7
    mysql_metadata_row = DatabaseRow(("COLUMN_NAME",), ("education_classes",))
    assert mysql_metadata_row["column_name"] == "education_classes"


def test_database_datetime_conversion_is_utc_naive():
    converted = _database_value("2026-09-01T12:30:15+08:00")
    assert converted == datetime(2026, 9, 1, 4, 30, 15)
    assert _api_value(converted) == "2026-09-01T04:30:15"


def test_secret_roundtrip_and_wrong_key_fails_closed():
    key = bytes(range(32))
    other_key = bytes(reversed(range(32)))
    ciphertext = encrypt_secret("sensitive", aad="user-settings:1:api-key", key=key)
    assert "sensitive" not in ciphertext
    assert decrypt_secret(ciphertext, aad="user-settings:1:api-key", key=key) == "sensitive"
    with pytest.raises(InvalidTag):
        decrypt_secret(ciphertext, aad="user-settings:1:api-key", key=other_key)


def test_session_hash_is_binary_sha256_and_not_plaintext():
    digest = session_token_hash("plain-token")
    assert isinstance(digest, bytes)
    assert len(digest) == 32
    assert b"plain-token" not in digest


def test_graph_validation_preserves_order_and_rejects_missing_endpoints():
    nodes = [{"id": 2, "content": "B"}, {"id": 1, "content": "A"}]
    edges = [{"from": 2, "to": 1, "label": "uses"}]
    normalized_nodes, normalized_edges = Neo4jHandler._validate_payload(nodes, edges)
    assert normalized_nodes == nodes
    assert normalized_edges == edges
    assert graph_checksum(nodes, edges) == graph_checksum(normalized_nodes, normalized_edges)
    with pytest.raises(Exception, match="missing endpoint"):
        Neo4jHandler._validate_payload(nodes, [{"from": 2, "to": 99}])


def test_mysql_schema_has_no_graph_json_columns_and_keeps_snapshot_counts():
    schema = (Path(__file__).parents[1] / "storage" / "mysql_schema.sql").read_text(encoding="utf-8")
    assert "`nodes_json`" not in schema
    assert "`edges_json`" not in schema
    snapshot = schema.split("CREATE TABLE `education_snapshots`", 1)[1].split("CREATE TABLE", 1)[0]
    assert "`node_count` BIGINT" in snapshot
    assert "`edge_count` BIGINT" in snapshot
    assert "CREATE TABLE `graph_registry`" in schema


def test_interrupted_history_reconciliation_pauses_rows_and_strips_paths(monkeypatch, tmp_path):
    from backend import api_v2

    raw_meta = {
        "status": "ready",
        "available": True,
        "pdf_path": str(tmp_path / "uploads" / "input.pdf"),
    }
    row = DatabaseRow(
        ("id", "source_pdf_json"),
        ("stale-running", __import__("json").dumps(raw_meta)),
    )

    class FakeResult:
        def fetchall(self):
            return [row]

    class FakeConnection:
        def __init__(self):
            self.update = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, statement, parameters=None):
            if statement.startswith("SELECT id, source_pdf_json"):
                return FakeResult()
            self.update = (statement, parameters)
            return DatabaseResult(rowcount=1)

    connection = FakeConnection()
    monkeypatch.setattr(api_v2, "connect_database", lambda: connection)

    assert api_v2.reconcile_interrupted_history() == 1
    statement, parameters = connection.update
    stored = __import__("json").loads(parameters[0])
    assert "status = 'paused'" in statement
    assert parameters[2] == "stale-running"
    assert stored["pdf_name"] == "input.pdf"
    assert "pdf_path" not in stored


def test_student_context_snapshot_graph_loads_from_graph_store(monkeypatch):
    from backend import api_v2  # Ensures backend-local imports are available.
    import student_context

    expected = {"nodes": [{"id": 1}], "edges": []}
    requested = []
    monkeypatch.setattr(
        student_context,
        "load_graph",
        lambda graph_id: requested.append(graph_id) or expected,
    )

    assert student_context._snapshot_graph({"id": "snapshot-1"}) == expected
    assert requested == ["snapshot-1"]
    assert student_context._snapshot_graph({"nodes": [], "edges": []}) == {
        "nodes": [],
        "edges": [],
    }


def test_persist_graph_registers_pending_before_writing_staging(monkeypatch, tmp_path):
    from backend.storage import graph_service

    events = []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            events.append("mysql_commit" if exc_type is None else "mysql_rollback")
            return False

        def execute(self, statement, parameters=None):
            if statement.startswith("SELECT revision, staging_path"):
                return DatabaseResult(("revision", "staging_path"), [])
            if statement.startswith("INSERT INTO graph_registry"):
                events.append("pending_insert")
            elif statement.startswith("UPDATE graph_registry SET staging_path"):
                events.append("staging_path_update")
            return DatabaseResult(rowcount=1)

    monkeypatch.setattr(graph_service, "connect_database", lambda: FakeConnection())
    monkeypatch.setattr(graph_service, "_remove_staging", lambda path: events.append(("remove", path)))

    staging_path = tmp_path / "1.json"

    def stage(*_args, **_kwargs):
        events.append("stage_write")
        return staging_path

    monkeypatch.setattr(graph_service, "_stage_payload", stage)
    monkeypatch.setattr(
        graph_service,
        "finalize_graph",
        lambda record: events.append(("finalize", record["staging_path"])) or {"ok": True},
    )

    assert graph_service.persist_graph("graph-1", "history", [{"id": 1}], []) == {"ok": True}
    assert events.index("pending_insert") < events.index("stage_write")
    assert events.index("stage_write") < events.index("staging_path_update")
    assert ("finalize", str(staging_path)) in events


def test_graph_service_supports_backend_package_import():
    assert callable(graph_service.persist_graph)
