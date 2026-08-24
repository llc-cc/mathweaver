"""验证生产迁移后的 MySQL 关键持久化契约。"""

from __future__ import annotations

from datetime import datetime, timezone
import os
import uuid

import pytest
from sqlalchemy import delete, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from storage.database import configure_database, get_engine, session_scope
from storage.models import History, StorageOutbox, User


_CI_DATABASE_URL = os.environ.get("MATHWEAVER_DATABASE_URL", "")
try:
    _RUNS_AGAINST_MYSQL = make_url(_CI_DATABASE_URL).get_backend_name() == "mysql"
except Exception:
    _RUNS_AGAINST_MYSQL = False


@pytest.mark.mysql
@pytest.mark.skipif(not _RUNS_AGAINST_MYSQL, reason="disposable MySQL is not configured")
def test_migrated_mysql_supports_storage_pointer_and_outbox_transaction() -> None:
    """覆盖 JSON、外键与 outbox 幂等约束，防止只验证迁移命令退出码。"""
    suffix = uuid.uuid4().hex
    history_id = f"ci-{suffix}"
    idempotency_key = f"delete:{history_id}:v1"
    configure_database(_CI_DATABASE_URL)

    tables = set(inspect(get_engine()).get_table_names())
    assert {"users", "history", "storage_outbox", "user_settings"} <= tables

    user_id: int | None = None
    try:
        with session_scope() as session:
            user = User.create_account(
                role="student",
                student_no=f"ci-{suffix}",
                email=f"ci-{suffix}@example.invalid",
                display_name="CI MySQL",
                password_hash="not-a-real-password-hash",
            )
            session.add(user)
            session.flush()
            user_id = int(user.id)
            session.add(
                History(
                    id=history_id,
                    user_id=user_id,
                    filename="integration.pdf",
                    nodes_json=[{"id": 1}],
                    edges_json=[],
                    storage_version="v1",
                    storage_status="ready",
                    storage_checksum="a" * 64,
                    storage_file_count=1,
                    storage_bytes=128,
                )
            )
            session.add(
                StorageOutbox(
                    user_id=user_id,
                    history_id=history_id,
                    version_id="v1",
                    operation="delete_version",
                    idempotency_key=idempotency_key,
                    payload_json={"reason": "ci"},
                    status="pending",
                    next_attempt_at=datetime.now(timezone.utc),
                )
            )

        with session_scope() as session:
            stored = session.scalar(select(History).where(History.id == history_id))
            assert stored is not None
            assert stored.nodes_json == [{"id": 1}]
            assert stored.storage_checksum == "a" * 64

        with pytest.raises(IntegrityError):
            with session_scope() as session:
                session.add(
                    StorageOutbox(
                        user_id=user_id,
                        history_id=history_id,
                        version_id="v1",
                        operation="delete_version",
                        idempotency_key=idempotency_key,
                        payload_json={},
                        status="pending",
                        next_attempt_at=datetime.now(timezone.utc),
                    )
                )
    finally:
        # CI 使用共享的临时库运行后续检查，因此只删除本测试带唯一前缀的数据。
        with session_scope() as session:
            session.execute(
                delete(StorageOutbox).where(StorageOutbox.idempotency_key == idempotency_key)
            )
            session.execute(delete(History).where(History.id == history_id))
            if user_id is not None:
                session.execute(delete(User).where(User.id == user_id))
