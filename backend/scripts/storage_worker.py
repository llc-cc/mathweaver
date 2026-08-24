"""运行一次或持续运行存储 outbox 清理任务。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import uuid


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from storage.database import configure_database
from storage.object_storage import ObjectStorageConfig, OssObjectStorage
from storage.storage_worker import StorageOutboxProcessor


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _local_cleanup(data_root: Path, _user_id: int, history_id: str) -> None:
    if not _SAFE_ID.fullmatch(history_id):
        raise ValueError("unsafe_history_id")
    targets = (data_root / "jobs" / history_id, data_root / "uploads" / "source_pdfs" / history_id)
    for target in targets:
        resolved = target.resolve()
        if resolved.parent not in {
            (data_root / "jobs").resolve(),
            (data_root / "uploads" / "source_pdfs").resolve(),
        }:
            raise ValueError("unsafe_cleanup_target")
        if resolved.exists():
            shutil.rmtree(resolved)


def build_processor() -> StorageOutboxProcessor:
    configure_database()
    config = ObjectStorageConfig.from_environment()
    if config is None:
        raise RuntimeError("OSS object storage is required for storage worker")
    data_root = Path(os.environ.get("MATHGRAPH_DATA_DIR", str(BACKEND_DIR))).resolve()
    return StorageOutboxProcessor(
        OssObjectStorage(config),
        worker_id=f"{os.environ.get('HOSTNAME', 'worker')}:{uuid.uuid4().hex[:12]}",
        local_cleanup=lambda user_id, history_id: _local_cleanup(
            data_root, user_id, history_id
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="处理当前到期任务后退出")
    parser.add_argument("--interval-seconds", type=int, default=10)
    args = parser.parse_args()
    try:
        processor = build_processor()
        while True:
            summary = processor.run_once(datetime.now(timezone.utc))
            print(json.dumps(summary.__dict__, sort_keys=True))
            if args.once:
                return 1 if summary.failed else 0
            time.sleep(max(1, args.interval_seconds))
    except Exception as exc:
        print(f"storage worker failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
