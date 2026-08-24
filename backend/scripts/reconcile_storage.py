"""生成存储漂移报告；只有显式参数才写入幂等修复 outbox。"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from storage.database import configure_database
from storage.object_storage import ObjectStorageConfig, OssObjectStorage
from storage.reconciliation import StorageReconciler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enqueue-repairs", action="store_true")
    args = parser.parse_args()
    try:
        configure_database()
        config = ObjectStorageConfig.from_environment()
        if config is None:
            raise RuntimeError("OSS object storage is required")
        reconciler = StorageReconciler(OssObjectStorage(config))
        report = reconciler.scan()
        payload = asdict(report)
        payload["enqueued_repairs"] = (
            reconciler.enqueue_orphan_cleanup(report) if args.enqueue_repairs else 0
        )
        print(json.dumps(payload, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"storage reconciliation failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
