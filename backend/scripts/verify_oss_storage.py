"""执行一次不泄露配置值的 OSS 上传、恢复和清理自检。"""

from __future__ import annotations

import shutil
import sys
import tempfile
import uuid
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from storage.object_storage import (  # noqa: E402
    ObjectStorageConfig,
    ObjectStorageError,
    OssObjectStorage,
)


def run_smoke(
    storage: OssObjectStorage,
    working_root: Path,
    *,
    user_id: int = 1,
    job_id: str | None = None,
) -> None:
    """往随机任务前缀写入探针，确认可恢复后无条件清理远端对象。"""

    checked_job_id = job_id or f"smoke-{uuid.uuid4().hex}"
    artifact_root = Path(working_root) / "artifacts" / checked_job_id
    source_root = Path(working_root) / "source-pdf" / checked_job_id
    marker = f"mathweaver-oss-smoke:{uuid.uuid4().hex}".encode("ascii")
    marker_path = artifact_root / "smoke.marker"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_bytes(marker)

    try:
        storage.sync_job(user_id, checked_job_id, artifact_root, source_root)
        # 删除本地副本后再恢复，避免把“本地文件仍存在”误判为 OSS 下载成功。
        shutil.rmtree(artifact_root)
        if not storage.restore_job(user_id, checked_job_id, artifact_root, source_root):
            raise ObjectStorageError("OSS smoke restore returned no objects")
        if marker_path.read_bytes() != marker:
            raise ObjectStorageError("OSS smoke restored unexpected content")
    finally:
        # 自检对象没有业务价值；无论校验成功与否都只清理本次随机任务前缀。
        storage.delete_job(user_id, checked_job_id)


def main() -> int:
    try:
        config = ObjectStorageConfig.from_environment()
        if config is None:
            raise RuntimeError("OSS storage is not enabled")
        storage = OssObjectStorage(config)
        with tempfile.TemporaryDirectory(prefix="mathweaver-oss-smoke-") as directory:
            run_smoke(storage, Path(directory))
    except (OSError, RuntimeError, ObjectStorageError):
        # 固定错误文本避免 SDK 请求、Bucket 或凭据细节进入终端和流水线日志。
        print("OSS storage verification failed", file=sys.stderr)
        return 1
    print("OSS storage verification succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
