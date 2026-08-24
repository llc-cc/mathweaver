"""对比 MySQL 活跃指针与 OSS 已提交 manifest，默认只读报告漂移。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from storage.audit_service import AuditWriter
from storage.database import session_scope
from storage.metrics import operational_metrics
from storage.models import History, StorageOutbox, utc_now
from storage.object_storage import ObjectStorageError


VersionRef = tuple[int, str, str]


@dataclass(frozen=True)
class ReconciliationReport:
    referenced_versions: int
    committed_versions: int
    missing_or_corrupt_versions: tuple[VersionRef, ...]
    orphan_versions: tuple[VersionRef, ...]


class StorageReconciler:
    def __init__(self, object_storage, session_factory=session_scope) -> None:
        self._storage = object_storage
        self._session_factory = session_factory

    def scan(self) -> ReconciliationReport:
        with self._session_factory() as session:
            rows = session.execute(
                select(
                    History.user_id,
                    History.id,
                    History.storage_version,
                    History.storage_checksum,
                ).where(
                    History.deleted_at.is_(None),
                    History.storage_status == "ready",
                    History.storage_version.is_not(None),
                    History.storage_checksum.is_not(None),
                )
            ).all()
        referenced = {
            (int(row.user_id), str(row.id), str(row.storage_version)) for row in rows
        }
        missing: list[VersionRef] = []
        for row in rows:
            try:
                self._storage.verify_version(
                    int(row.user_id),
                    str(row.id),
                    str(row.storage_version),
                    str(row.storage_checksum),
                )
            except (ObjectStorageError, ValueError):
                missing.append((int(row.user_id), str(row.id), str(row.storage_version)))
        committed = self._storage.list_committed_versions()
        report = ReconciliationReport(
            referenced_versions=len(referenced),
            committed_versions=len(committed),
            missing_or_corrupt_versions=tuple(sorted(missing)),
            orphan_versions=tuple(sorted(committed - referenced)),
        )
        operational_metrics.set_reconciliation_drift(
            "missing_or_corrupt", len(report.missing_or_corrupt_versions)
        )
        operational_metrics.set_reconciliation_drift(
            "orphan", len(report.orphan_versions)
        )
        return report

    def enqueue_orphan_cleanup(self, report: ReconciliationReport) -> int:
        created = 0
        with self._session_factory() as session:
            for user_id, history_id, version_id in report.orphan_versions:
                key = f"reconcile-delete-version:{user_id}:{history_id}:{version_id}"
                exists = session.scalar(
                    select(StorageOutbox.id).where(StorageOutbox.idempotency_key == key)
                )
                if exists is not None:
                    continue
                session.add(
                    StorageOutbox(
                        user_id=user_id,
                        history_id=history_id,
                        version_id=version_id,
                        operation="delete_version",
                        idempotency_key=key,
                        payload_json={"version_id": version_id},
                        next_attempt_at=utc_now(),
                    )
                )
                created += 1
            if created:
                AuditWriter().add(
                    session,
                    actor_id=None,
                    action="reconciliation.repair",
                    subject_type="storage",
                    subject_id="orphan-versions",
                    details={"operation": "delete_version", "count": created},
                )
        return created
