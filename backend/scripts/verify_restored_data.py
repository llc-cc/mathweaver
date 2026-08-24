"""恢复演练后核对数据库行数、存储状态与确定性样本 manifest。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from sqlalchemy import func, select


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from storage.database import configure_database, get_engine, session_scope, validate_mysql_packet
from storage.models import History
from storage.object_storage import ObjectStorageConfig, OssObjectStorage


def build_report(
    session_factory,
    *,
    expected_history_rows: int | None = None,
    sample_size: int = 20,
    verify_objects: bool = False,
    object_storage=None,
) -> dict:
    with session_factory() as session:
        total = int(session.scalar(select(func.count()).select_from(History)) or 0)
        active = int(
            session.scalar(
                select(func.count()).select_from(History).where(History.deleted_at.is_(None))
            )
            or 0
        )
        statuses = {
            str(status): int(count)
            for status, count in session.execute(
                select(History.storage_status, func.count())
                .group_by(History.storage_status)
                .order_by(History.storage_status.asc())
            ).all()
        }
        samples = session.execute(
            select(
                History.id,
                History.user_id,
                History.storage_version,
                History.storage_checksum,
            )
            .where(
                History.deleted_at.is_(None),
                History.storage_status == "ready",
                History.storage_version.is_not(None),
                History.storage_checksum.is_not(None),
            )
            .order_by(History.id.asc())
            .limit(max(0, min(int(sample_size), 1000)))
        ).all()
    if expected_history_rows is not None and total != int(expected_history_rows):
        raise RuntimeError("restored history row count does not match expectation")
    verified = 0
    if verify_objects:
        if object_storage is None:
            raise RuntimeError("object storage is required for object verification")
        for row in samples:
            object_storage.verify_version(
                int(row.user_id), row.id, row.storage_version, row.storage_checksum
            )
            verified += 1
    return {
        "history_rows": total,
        "active_history_rows": active,
        "deleted_history_rows": total - active,
        "storage_status_counts": statuses,
        "sampled_versions": len(samples),
        "verified_object_versions": verified,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-history-rows", type=int)
    parser.add_argument("--sample-size", type=int, default=20)
    parser.add_argument("--verify-objects", action="store_true")
    args = parser.parse_args()
    try:
        configure_database()
        required_bytes = int(
            os.environ.get("MATHWEAVER_MAX_HISTORY_JSON_BYTES", str(16 * 1024 * 1024))
        )
        packet = validate_mysql_packet(get_engine(), required_bytes)
        storage = None
        if args.verify_objects:
            config = ObjectStorageConfig.from_environment()
            if config is None:
                raise RuntimeError("OSS object storage is required")
            storage = OssObjectStorage(config)
        report = build_report(
            session_scope,
            expected_history_rows=args.expected_history_rows,
            sample_size=args.sample_size,
            verify_objects=args.verify_objects,
            object_storage=storage,
        )
        report["max_allowed_packet"] = packet
        print(json.dumps(report, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"restored data verification failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
