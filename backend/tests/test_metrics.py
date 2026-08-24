"""生产数据指标必须可抓取，且不能携带资源标识或敏感异常。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from storage.metrics import OperationalMetrics


def test_metrics_expose_required_low_cardinality_series() -> None:
    metrics = OperationalMetrics()
    metrics.record_database_transaction("failure")
    metrics.record_storage_failure("restore", "checksum_mismatch")
    metrics.observe_restore("success", 0.25)
    metrics.observe_storage_version(file_count=2, total_bytes=512)
    metrics.set_outbox_status("pending", 3)
    metrics.set_reconciliation_drift("orphan", 1)

    payload = metrics.render().decode("utf-8")
    for name in (
        "mathweaver_database_transactions_total",
        "mathweaver_database_pool_checked_out",
        "mathweaver_storage_failures_total",
        "mathweaver_storage_restore_seconds",
        "mathweaver_storage_version_files",
        "mathweaver_storage_version_bytes",
        "mathweaver_storage_outbox_status",
        "mathweaver_storage_reconciliation_drift",
    ):
        assert name in payload
    assert "job-" not in payload
    assert "users/" not in payload


@pytest.mark.parametrize(
    ("method", "arguments"),
    (
        ("record_storage_failure", ("restore", "job-123")),
        ("record_storage_failure", ("users/42", "checksum_mismatch")),
        ("set_outbox_status", ("task-123", 1)),
        ("set_reconciliation_drift", ("users/42", 1)),
    ),
)
def test_metrics_reject_unbounded_label_values(method: str, arguments: tuple) -> None:
    metrics = OperationalMetrics()
    with pytest.raises(ValueError, match="unsupported metric label"):
        getattr(metrics, method)(*arguments)


def test_database_session_records_commit_rollback_and_pool_usage(monkeypatch) -> None:
    from storage import database

    metrics = OperationalMetrics()
    monkeypatch.setattr(database, "operational_metrics", metrics)
    database.configure_database("sqlite+pysqlite:///:memory:")

    with database.session_scope() as session:
        session.execute(__import__("sqlalchemy").text("SELECT 1"))
    with pytest.raises(RuntimeError, match="rollback"):
        with database.session_scope():
            raise RuntimeError("rollback")

    payload = metrics.render().decode("utf-8")
    assert 'mathweaver_database_transactions_total{result="success"} 1.0' in payload
    assert 'mathweaver_database_transactions_total{result="failure"} 1.0' in payload
    assert 'mathweaver_database_pool_checked_out{process="test"} 0.0' in payload


def test_metrics_written_by_worker_process_are_visible_to_api_process(tmp_path) -> None:
    environment = os.environ.copy()
    environment["PROMETHEUS_MULTIPROC_DIR"] = str(tmp_path)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from storage.metrics import operational_metrics as m;"
                "m.record_storage_failure('delete','object_storage_error');"
                "m.set_outbox_status('pending', 4);"
                "m.set_reconciliation_drift('orphan', 2);"
                "m.pool_checkout()"
            ),
        ],
        check=True,
        env=environment,
    )
    payload = subprocess.check_output(
        [
            sys.executable,
            "-c",
            "from storage.metrics import operational_metrics as m;print(m.render().decode())",
        ],
        env=environment,
        text=True,
    )
    assert (
        'mathweaver_storage_failures_total{code="object_storage_error",operation="delete"} 1.0'
        in payload
    )
    assert 'mathweaver_storage_outbox_status{status="pending"} 4.0' in payload
    assert 'mathweaver_storage_reconciliation_drift{kind="orphan"} 2.0' in payload
    # 替代进程初始化时写入 0，异常退出进程遗留的 checkout 不得继续累加。
    assert 'mathweaver_database_pool_checked_out{process="test"} 0.0' in payload


def test_internal_metrics_endpoint_is_not_proxied_by_public_nginx() -> None:
    from api_v2 import app

    response = app.test_client().get("/internal/metrics")
    assert response.status_code == 200
    assert response.content_type.startswith("text/plain")
    assert b"mathweaver_database_transactions_total" in response.data

    nginx = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "deploy"
        / "nginx.mathweaver.conf"
    ).read_text(encoding="utf-8")
    assert "location /internal/metrics" not in nginx
