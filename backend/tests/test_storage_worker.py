from datetime import datetime, timezone

from sqlalchemy import select

from storage.database import session_scope
from storage.models import History, StorageOutbox, User
from storage.storage_worker import StorageOutboxProcessor


class FakeStorage:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.deleted: list[tuple[int, str, str]] = []

    def delete_version(self, user_id: int, history_id: str, version_id: str) -> None:
        if self.fail:
            raise RuntimeError("provider credential=https://secret.example?Signature=leak")
        self.deleted.append((user_id, history_id, version_id))

    def delete_job_versions(self, user_id: int, history_id: str) -> None:
        if self.fail:
            raise RuntimeError("provider failure")
        self.deleted.append((user_id, history_id, "all"))


def _seed_delete_version() -> int:
    with session_scope() as session:
        user = User.create_account("student", "S-WORKER", None, "Worker", "hash")
        session.add(user)
        session.flush()
        row = StorageOutbox(
            user_id=user.id,
            history_id="job-1",
            version_id="a" * 32,
            operation="delete_version",
            idempotency_key="delete-version:job-1:a",
            payload_json={"version_id": "a" * 32},
            next_attempt_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )
        session.add(row)
        session.flush()
        return row.id


def test_failed_delete_is_retried_with_stable_secret_free_error_code():
    row_id = _seed_delete_version()
    now = datetime(2026, 8, 24, 1, tzinfo=timezone.utc)
    processor = StorageOutboxProcessor(FakeStorage(fail=True), worker_id="worker-a")

    summary = processor.run_once(now)

    with session_scope() as session:
        row = session.get(StorageOutbox, row_id)
        assert row.status == "pending"
        assert row.attempts == 1
        assert row.next_attempt_at.replace(tzinfo=timezone.utc) > now
        assert row.last_error_code == "object_storage_error"
    assert summary.failed == 1


def test_successful_delete_is_idempotently_marked_done():
    row_id = _seed_delete_version()
    storage = FakeStorage()
    processor = StorageOutboxProcessor(storage, worker_id="worker-a")
    now = datetime(2026, 8, 24, 1, tzinfo=timezone.utc)

    first = processor.run_once(now)
    second = processor.run_once(now)

    with session_scope() as session:
        row = session.scalar(select(StorageOutbox).where(StorageOutbox.id == row_id))
        assert row.status == "done"
    assert storage.deleted == [(1, "job-1", "a" * 32)]
    assert first.succeeded == 1
    assert second.claimed == 0


def test_history_becomes_deleted_only_after_remote_and_local_cleanup_complete():
    now = datetime(2026, 8, 24, 1, tzinfo=timezone.utc)
    with session_scope() as session:
        user = User.create_account("student", "S-CLEAN", None, "Clean", "hash")
        session.add(user)
        session.flush()
        session.add(
            History(
                id="job-clean",
                user_id=user.id,
                filename="lesson.md",
                deleted_at=now,
                storage_status="delete_pending",
            )
        )
        session.add_all([
            StorageOutbox(
                user_id=user.id, history_id="job-clean", operation="delete_job_versions",
                idempotency_key="remote:job-clean", payload_json={}, next_attempt_at=now,
            ),
            StorageOutbox(
                user_id=user.id, history_id="job-clean", operation="delete_local_cache",
                idempotency_key="local:job-clean", payload_json={}, next_attempt_at=now,
            ),
        ])
    cleaned: list[tuple[int, str]] = []
    processor = StorageOutboxProcessor(
        FakeStorage(),
        worker_id="worker-a",
        local_cleanup=lambda user_id, history_id: cleaned.append((user_id, history_id)),
    )

    summary = processor.run_once(now)

    with session_scope() as session:
        history = session.get(History, "job-clean")
        assert history.storage_status == "deleted"
    assert summary.succeeded == 2
    assert cleaned == [(1, "job-clean")]
