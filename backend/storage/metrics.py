"""只暴露低基数标签的生产数据 Prometheus 指标。"""

from __future__ import annotations

import os
import socket
from threading import Lock

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)
from prometheus_client import values


METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST


STORAGE_OPERATIONS = frozenset({"upload", "restore", "delete", "verify", "list"})
STORAGE_ERROR_CODES = frozenset(
    {"object_storage_error", "checksum_mismatch", "configuration_error"}
)
TRANSACTION_RESULTS = frozenset({"success", "failure"})
RESTORE_RESULTS = frozenset({"success", "failure"})
OUTBOX_STATUSES = frozenset({"pending", "processing", "done", "failed"})
DRIFT_KINDS = frozenset({"orphan", "missing_or_corrupt"})
METRIC_PROCESSES = frozenset(
    {"backend", "storage_worker", "migrate", "reconciliation", "test"}
)


if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
    # 不同容器可能拥有相同 PID；hostname + PID 才满足共享数据卷上的唯一写入者约束。
    values.ValueClass = values.MultiProcessValue(
        lambda: f"{socket.gethostname()}-{os.getpid()}"
    )


def _checked(value: str, allowed: frozenset[str]) -> str:
    normalized = str(value)
    if normalized not in allowed:
        raise ValueError("unsupported metric label")
    return normalized


class OperationalMetrics:
    """集中创建并更新指标，调用方不能添加用户、任务或对象键标签。"""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry(auto_describe=True)
        self._process = _checked(
            os.environ.get("MATHWEAVER_METRICS_PROCESS", "test"), METRIC_PROCESSES
        )
        self._database_transactions = Counter(
            "mathweaver_database_transactions_total",
            "Committed and rolled-back database transactions.",
            ("result",),
            registry=self.registry,
        )
        self._pool_checked_out = Gauge(
            "mathweaver_database_pool_checked_out",
            "Currently checked-out database connections.",
            ("process",),
            # 每个固定服务角色取最后观测值；新进程的 0 会覆盖异常退出遗留值。
            multiprocess_mode="mostrecent",
            registry=self.registry,
        )
        self._storage_failures = Counter(
            "mathweaver_storage_failures_total",
            "Object storage failures by bounded operation and stable code.",
            ("operation", "code"),
            registry=self.registry,
        )
        self._restore_seconds = Histogram(
            "mathweaver_storage_restore_seconds",
            "Verified object restore duration.",
            ("result",),
            registry=self.registry,
        )
        self._version_files = Histogram(
            "mathweaver_storage_version_files",
            "Files in committed storage versions.",
            buckets=(0, 1, 5, 10, 50, 100, 500, 1000),
            registry=self.registry,
        )
        self._version_bytes = Histogram(
            "mathweaver_storage_version_bytes",
            "Bytes in committed storage versions.",
            buckets=(0, 1024, 1024**2, 10 * 1024**2, 100 * 1024**2, 1024**3),
            registry=self.registry,
        )
        self._outbox_status = Gauge(
            "mathweaver_storage_outbox_status",
            "Storage outbox rows by state.",
            ("status",),
            multiprocess_mode="mostrecent",
            registry=self.registry,
        )
        self._reconciliation_drift = Gauge(
            "mathweaver_storage_reconciliation_drift",
            "Storage drift found by the latest reconciliation scan.",
            ("kind",),
            multiprocess_mode="mostrecent",
            registry=self.registry,
        )
        self._pool_lock = Lock()
        self._checked_out = 0
        self._pool_checked_out.labels(process=self._process).set(0)

    def record_database_transaction(self, result: str) -> None:
        self._database_transactions.labels(
            result=_checked(result, TRANSACTION_RESULTS)
        ).inc()

    def pool_checkout(self) -> None:
        with self._pool_lock:
            self._checked_out += 1
            self._pool_checked_out.labels(process=self._process).set(self._checked_out)

    def pool_checkin(self) -> None:
        with self._pool_lock:
            self._checked_out = max(0, self._checked_out - 1)
            self._pool_checked_out.labels(process=self._process).set(self._checked_out)

    def record_storage_failure(self, operation: str, code: str) -> None:
        self._storage_failures.labels(
            operation=_checked(operation, STORAGE_OPERATIONS),
            code=_checked(code, STORAGE_ERROR_CODES),
        ).inc()

    def observe_restore(self, result: str, seconds: float) -> None:
        self._restore_seconds.labels(result=_checked(result, RESTORE_RESULTS)).observe(
            max(0.0, float(seconds))
        )

    def observe_storage_version(self, *, file_count: int, total_bytes: int) -> None:
        self._version_files.observe(max(0, int(file_count)))
        self._version_bytes.observe(max(0, int(total_bytes)))

    def set_outbox_status(self, status: str, count: int) -> None:
        self._outbox_status.labels(status=_checked(status, OUTBOX_STATUSES)).set(
            max(0, int(count))
        )

    def set_reconciliation_drift(self, kind: str, count: int) -> None:
        self._reconciliation_drift.labels(kind=_checked(kind, DRIFT_KINDS)).set(
            max(0, int(count))
        )

    def render(self) -> bytes:
        if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
            # API、worker 和协调命令共享数据卷；抓取端必须合并各进程写入的 mmap 指标。
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry)
            return generate_latest(registry)
        return generate_latest(self.registry)


operational_metrics = OperationalMetrics()
