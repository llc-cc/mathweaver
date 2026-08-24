from datetime import datetime, timezone

import pytest

from scripts.verify_restored_data import build_report
from storage.database import session_scope, validate_mysql_packet
from storage.models import History, User


class FakeResult:
    def scalar_one(self):
        return 4 * 1024 * 1024


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, _statement):
        return FakeResult()


class FakeEngine:
    def connect(self):
        return FakeConnection()


def test_packet_preflight_rejects_limit_below_required_payload():
    with pytest.raises(RuntimeError, match="max_allowed_packet is below"):
        validate_mysql_packet(FakeEngine(), required_bytes=5 * 1024 * 1024)


def test_restored_data_report_contains_counts_without_payload_data():
    with session_scope() as session:
        user = User.create_account("student", "S-RESTORE", None, "Restore", "hash")
        session.add(user)
        session.flush()
        session.add_all([
            History(
                id="active-job", user_id=user.id, filename="active.md",
                storage_status="ready", storage_version="a" * 32,
                storage_checksum="1" * 64, storage_file_count=2, storage_bytes=10,
            ),
            History(
                id="deleted-job", user_id=user.id, filename="deleted.md",
                storage_status="deleted", deleted_at=datetime.now(timezone.utc),
            ),
        ])

    report = build_report(session_scope, expected_history_rows=2, sample_size=5)

    assert report["history_rows"] == 2
    assert report["active_history_rows"] == 1
    assert report["deleted_history_rows"] == 1
    assert report["storage_status_counts"] == {"deleted": 1, "ready": 1}
    assert "filename" not in str(report).lower()
