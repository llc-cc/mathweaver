"""数据库 outbox 驱动的幂等对象与本地缓存清理 worker。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select

from storage.database import session_scope
from storage.metrics import OUTBOX_STATUSES, operational_metrics
from storage.models import History, StorageOutbox


LocalCleanup = Callable[[int, str], None]


@dataclass(frozen=True)
class ProcessingSummary:
    claimed: int = 0
    succeeded: int = 0
    failed: int = 0


class StorageOutboxProcessor:
    def __init__(
        self,
        object_storage,
        *,
        worker_id: str,
        local_cleanup: LocalCleanup | None = None,
        lease_seconds: int = 60,
        batch_size: int = 20,
    ) -> None:
        self._storage = object_storage
        self._worker_id = str(worker_id)
        self._local_cleanup = local_cleanup
        self._lease_seconds = max(10, int(lease_seconds))
        self._batch_size = max(1, min(int(batch_size), 100))

    def _claim_next(self, now: datetime) -> int | None:
        with session_scope() as session:
            row = session.scalar(
                select(StorageOutbox)
                .where(
                    or_(
                        (
                            (StorageOutbox.status == "pending")
                            & (StorageOutbox.next_attempt_at <= now)
                        ),
                        (
                            (StorageOutbox.status == "processing")
                            & (StorageOutbox.lease_expires_at < now)
                        ),
                    )
                )
                .order_by(StorageOutbox.next_attempt_at.asc(), StorageOutbox.id.asc())
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return None
            row.status = "processing"
            row.lease_owner = self._worker_id
            row.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
            return row.id

    def _execute(self, row: StorageOutbox) -> None:
        if row.operation == "delete_version":
            if not row.version_id:
                raise ValueError("version_id_required")
            self._storage.delete_version(row.user_id, row.history_id, row.version_id)
            return
        if row.operation == "delete_job_versions":
            self._storage.delete_job_versions(row.user_id, row.history_id)
            return
        if row.operation == "delete_local_cache":
            if self._local_cleanup is None:
                raise RuntimeError("local_cleanup_unavailable")
            self._local_cleanup(row.user_id, row.history_id)
            return
        raise ValueError("unsupported_operation")

    def _finish(self, row_id: int, now: datetime, *, succeeded: bool) -> None:
        with session_scope() as session:
            row = session.get(StorageOutbox, row_id)
            if (
                row is None
                or row.status != "processing"
                or row.lease_owner != self._worker_id
            ):
                return
            row.lease_owner = None
            row.lease_expires_at = None
            if succeeded:
                row.status = "done"
                row.last_error_code = None
                if row.operation in {"delete_job_versions", "delete_local_cache"}:
                    cleanup_rows = session.scalars(
                        select(StorageOutbox).where(
                            StorageOutbox.user_id == row.user_id,
                            StorageOutbox.history_id == row.history_id,
                            StorageOutbox.operation.in_(
                                ("delete_job_versions", "delete_local_cache")
                            ),
                        )
                    ).all()
                    if cleanup_rows and all(item.status == "done" for item in cleanup_rows):
                        history = session.get(History, row.history_id)
                        if history is not None and history.deleted_at is not None:
                            history.storage_status = "deleted"
            else:
                row.attempts += 1
                row.status = "pending"
                row.last_error_code = "object_storage_error"
                delay = min(3600, 30 * (2 ** min(row.attempts - 1, 7)))
                row.next_attempt_at = now + timedelta(seconds=delay)

    def run_once(self, now: datetime) -> ProcessingSummary:
        claimed = succeeded = failed = 0
        for _ in range(self._batch_size):
            row_id = self._claim_next(now)
            if row_id is None:
                break
            claimed += 1
            try:
                with session_scope() as session:
                    row = session.get(StorageOutbox, row_id)
                    if row is None:
                        continue
                    self._execute(row)
            except Exception:
                # Provider 异常可能包含凭据或签名 URL；outbox 只保存稳定错误码。
                self._finish(row_id, now, succeeded=False)
                failed += 1
            else:
                self._finish(row_id, now, succeeded=True)
                succeeded += 1
        self._refresh_status_metrics()
        return ProcessingSummary(claimed, succeeded, failed)

    def _refresh_status_metrics(self) -> None:
        """指标查询失败不能把已完成的幂等清理误报为 worker 业务失败。"""
        try:
            with session_scope() as session:
                counts = dict(
                    session.execute(
                        select(StorageOutbox.status, func.count()).group_by(
                            StorageOutbox.status
                        )
                    ).all()
                )
        except Exception:
            return
        for status in OUTBOX_STATUSES:
            operational_metrics.set_outbox_status(status, int(counts.get(status, 0)))
