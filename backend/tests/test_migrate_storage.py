from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.scripts.migrate_storage import (
    MigrationError,
    SourceSnapshot,
    TARGET_TABLE_ORDER,
    merge_sources,
    _filesystem_copy_path,
)


def source(name, priority, *, users, sessions=None, history=None):
    tables = {table: [] for table in TARGET_TABLE_ORDER}
    tables["users"] = users
    tables["sessions"] = sessions or []
    tables["history"] = history or []
    primary_keys = {table: () for table in TARGET_TABLE_ORDER}
    primary_keys.update({"users": ("id",), "sessions": ("token",), "history": ("id",)})
    return SourceSnapshot(
        name=name,
        source_path=Path(f"{name}.db"),
        snapshot_path=Path(f"{name}-snapshot.db"),
        priority=priority,
        tables=tables,
        primary_keys=primary_keys,
    )


def history_row(graph_id, user_id):
    return {
        "id": graph_id,
        "user_id": user_id,
        "filename": "graph.md",
        "node_count": 1,
        "edge_count": 0,
        "nodes_json": '[{"id":1,"content":"A"}]',
        "edges_json": "[]",
        "created_at": "2026-08-01T00:00:00",
        "source_markdown": "A",
        "latex_macros": "{}",
        "source_pdf_json": None,
        "status": "done",
        "stage": None,
        "stage_label": None,
        "stage_index": 1,
        "total_stages": 1,
        "stages_done_json": "[]",
        "source_format": "markdown",
        "updated_at": "2026-08-01T00:00:00",
        "experimental_logic_ir": 0,
        "source_origin": "markdown",
    }


def test_runtime_user_profile_wins_while_created_at_and_can_teach_merge():
    repo = source(
        "repo",
        0,
        users=[{
            "id": 10,
            "email": "Teacher@Example.com",
            "password_hash": "repo-hash",
            "created_at": "2026-01-01T00:00:00",
            "can_teach": 0,
        }],
        history=[history_row("repo-graph", 10)],
    )
    runtime = source(
        "runtime",
        1000,
        users=[{
            "id": 99,
            "email": "teacher@example.com",
            "password_hash": "runtime-hash",
            "created_at": "2026-02-01T00:00:00",
            "can_teach": 1,
        }],
        history=[history_row("runtime-graph", 99)],
    )
    result = merge_sources([runtime, repo])
    assert result.tables["users"] == [{
        "id": 1,
        "email": "teacher@example.com",
        "password_hash": "runtime-hash",
        "created_at": "2026-01-01T00:00:00",
        "can_teach": 1,
    }]
    assert {row["user_id"] for row in result.tables["history"]} == {1}
    assert [graph["graph_id"] for graph in result.graphs] == ["repo-graph", "runtime-graph"]


def test_duplicate_session_token_for_different_users_aborts():
    src = source(
        "runtime",
        1000,
        users=[
            {"id": 1, "email": "a@example.com", "password_hash": "a", "created_at": "2026-01-01T00:00:00", "can_teach": 0},
            {"id": 2, "email": "b@example.com", "password_hash": "b", "created_at": "2026-01-01T00:00:00", "can_teach": 0},
        ],
        sessions=[
            {"token": "same", "user_id": 1, "created_at": "2026-01-01T00:00:00", "education_role": "student"},
            {"token": "same", "user_id": 2, "created_at": "2026-01-01T00:00:00", "education_role": "student"},
        ],
    )
    with pytest.raises(MigrationError, match="different merged users"):
        merge_sources([src])


def test_invalid_graph_edge_aborts_before_target_write():
    row = history_row("broken", 1)
    row["edges_json"] = '[{"from":1,"to":2}]'
    src = source(
        "repo",
        0,
        users=[{"id": 1, "email": "a@example.com", "password_hash": "a", "created_at": "2026-01-01T00:00:00", "can_teach": 0}],
        history=[row],
    )
    with pytest.raises(MigrationError, match="missing endpoint"):
        merge_sources([src])


def test_default_course_graph_order_is_derived_after_user_remapping():
    src = source(
        "runtime",
        1000,
        users=[{
            "id": 7,
            "email": "teacher@example.com",
            "password_hash": "hash",
            "created_at": "2026-01-01T00:00:00",
            "can_teach": 1,
        }],
    )
    src.tables["education_classes"] = [{
        "id": "class-1",
        "owner_user_id": 7,
        "title": "Class",
        "invite_code": "INVITE",
        "created_at": "2026-01-01T00:00:00",
        "archived_at": None,
    }]
    src.primary_keys["education_classes"] = ("id",)
    src.tables["education_snapshots"] = [
        {
            "id": "snapshot-1",
            "class_id": "class-1",
            "source_graph_id": "shared",
            "filename": "a.tex",
            "nodes_json": '[{"id":1}]',
            "edges_json": "[]",
            "source_markdown": "",
            "latex_macros_json": "{}",
            "source_pdf_json": None,
            "created_by": 7,
            "created_at": "2026-01-02T00:00:00",
            "snapshot_type": "graph",
        },
        {
            "id": "snapshot-2",
            "class_id": "class-1",
            "source_graph_id": "shared",
            "filename": "b.tex",
            "nodes_json": '[{"id":1}]',
            "edges_json": "[]",
            "source_markdown": "",
            "latex_macros_json": "{}",
            "source_pdf_json": None,
            "created_by": 7,
            "created_at": "2026-01-03T00:00:00",
            "snapshot_type": "graph",
        },
        {
            "id": "snapshot-3",
            "class_id": "class-1",
            "source_graph_id": None,
            "filename": "c.tex",
            "nodes_json": '[{"id":1}]',
            "edges_json": "[]",
            "source_markdown": "",
            "latex_macros_json": "{}",
            "source_pdf_json": None,
            "created_by": 7,
            "created_at": "2026-01-04T00:00:00",
            "snapshot_type": "graph",
        },
    ]
    src.primary_keys["education_snapshots"] = ("id",)

    result = merge_sources([src])

    assert result.tables["education_course_graph_order"] == [
        {
            "class_id": "class-1",
            "graph_key": "source:shared",
            "sort_order": 0,
            "updated_by": 1,
            "updated_at": "2026-01-02T00:00:00",
        },
        {
            "class_id": "class-1",
            "graph_key": "snapshot:snapshot-3",
            "sort_order": 1,
            "updated_by": 1,
            "updated_at": "2026-01-04T00:00:00",
        },
    ]


def test_filesystem_copy_path_enables_windows_long_paths(tmp_path):
    resolved = str(tmp_path.resolve())
    copy_path = _filesystem_copy_path(tmp_path)
    if os.name == "nt":
        prefix = chr(92) * 2 + "?" + chr(92)
        assert copy_path.startswith(prefix)
        assert copy_path.endswith(resolved)
    else:
        assert copy_path == resolved
