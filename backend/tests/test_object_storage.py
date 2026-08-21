from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.verify_oss_storage import run_smoke
from storage.object_storage import (
    ObjectStorageConfig,
    ObjectStorageError,
    OssObjectStorage,
)


class FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_upload = False

    def put_object_from_file(self, key: str, filename: str) -> None:
        if self.fail_upload:
            raise RuntimeError("provider leaked-secret-value")
        self.objects[key] = Path(filename).read_bytes()

    def get_object_to_file(self, key: str, filename: str) -> None:
        Path(filename).write_bytes(self.objects[key])

    def list_objects_v2(
        self,
        *,
        prefix: str,
        max_keys: int,
        continuation_token: str,
    ) -> SimpleNamespace:
        del max_keys, continuation_token
        rows = [SimpleNamespace(key=key) for key in sorted(self.objects) if key.startswith(prefix)]
        return SimpleNamespace(
            object_list=rows,
            is_truncated=False,
            next_continuation_token="",
        )

    def batch_delete_objects(self, keys: list[str]) -> None:
        for key in keys:
            self.objects.pop(key, None)

    def objects_with_prefix(self, prefix: str) -> dict[str, bytes]:
        return {key: value for key, value in self.objects.items() if key.startswith(prefix)}


def configured_storage(bucket: FakeBucket, *, prefix: str = "mathweaver/") -> OssObjectStorage:
    config = ObjectStorageConfig(
        endpoint="https://oss-cn-example.aliyuncs.com",
        bucket="private-test-bucket",
        access_key_id="test-access-key-id",
        access_key_secret="test-access-key-secret",
        prefix=prefix,
    )
    return OssObjectStorage(config, bucket_factory=lambda _config: bucket)


def test_local_mode_does_not_require_oss_configuration() -> None:
    assert ObjectStorageConfig.from_environment({}) is None
    assert ObjectStorageConfig.from_environment({"MATHWEAVER_OBJECT_STORAGE": "local"}) is None


def test_oss_config_rejects_incomplete_enabled_configuration() -> None:
    with pytest.raises(RuntimeError, match="MATHWEAVER_OSS_ENDPOINT"):
        ObjectStorageConfig.from_environment({"MATHWEAVER_OBJECT_STORAGE": "oss"})


def test_oss_config_requires_https_and_safe_prefix() -> None:
    base = {
        "MATHWEAVER_OBJECT_STORAGE": "oss",
        "MATHWEAVER_OSS_ENDPOINT": "http://oss-cn-example.aliyuncs.com",
        "MATHWEAVER_OSS_BUCKET": "private-test-bucket",
        "MATHWEAVER_OSS_ACCESS_KEY_ID": "id",
        "MATHWEAVER_OSS_ACCESS_KEY_SECRET": "secret",
    }
    with pytest.raises(RuntimeError, match="HTTPS"):
        ObjectStorageConfig.from_environment(base)

    base["MATHWEAVER_OSS_ENDPOINT"] = "https://oss-cn-example.aliyuncs.com"
    base["MATHWEAVER_OSS_PREFIX"] = "../shared/"
    with pytest.raises(RuntimeError, match="MATHWEAVER_OSS_PREFIX"):
        ObjectStorageConfig.from_environment(base)


def test_task_prefix_is_scoped_to_user_and_rejects_unsafe_job_id() -> None:
    storage = configured_storage(FakeBucket())

    assert storage.task_prefix(7, "job-1") == "mathweaver/users/7/jobs/job-1/"
    assert storage.task_prefix(8, "job-1") == "mathweaver/users/8/jobs/job-1/"
    with pytest.raises(ValueError):
        storage.task_prefix(7, "../job-1")
    with pytest.raises(ValueError):
        storage.task_prefix(0, "job-1")


def test_sync_restore_and_delete_round_trip(tmp_path: Path) -> None:
    bucket = FakeBucket()
    storage = configured_storage(bucket)
    artifact_root = tmp_path / "jobs" / "job-1"
    source_root = tmp_path / "source" / "job-1"
    (artifact_root / "_stage_cache").mkdir(parents=True)
    (artifact_root / "input.md").write_text("source", encoding="utf-8")
    (artifact_root / "_stage_cache" / "manifest.json").write_text("{}", encoding="utf-8")
    source_root.mkdir(parents=True)
    (source_root / "source.pdf").write_bytes(b"%PDF-1.4\n%%EOF")

    prefix = storage.sync_job(7, "job-1", artifact_root, source_root)

    assert bucket.objects[f"{prefix}artifacts/input.md"] == b"source"
    assert bucket.objects[f"{prefix}source-pdf/source.pdf"].startswith(b"%PDF-")
    shutil.rmtree(artifact_root)
    shutil.rmtree(source_root)

    assert storage.restore_job(7, "job-1", artifact_root, source_root) is True
    assert (artifact_root / "input.md").read_text(encoding="utf-8") == "source"
    assert (source_root / "source.pdf").read_bytes().startswith(b"%PDF-")

    storage.delete_job(7, "job-1")
    assert not bucket.objects_with_prefix(prefix)


def test_sync_skips_transient_files_and_removes_stale_remote_objects(tmp_path: Path) -> None:
    bucket = FakeBucket()
    storage = configured_storage(bucket)
    artifact_root = tmp_path / "job"
    source_root = tmp_path / "source"
    (artifact_root / "_stage_work").mkdir(parents=True)
    (artifact_root / "_stage_work" / "secret.txt").write_text("temporary", encoding="utf-8")
    (artifact_root / "result.json.tmp").write_text("temporary", encoding="utf-8")
    (artifact_root / "nodes.json").write_text("[]", encoding="utf-8")
    prefix = storage.task_prefix(7, "job-1")
    bucket.objects[f"{prefix}artifacts/stale.json"] = b"stale"

    storage.sync_job(7, "job-1", artifact_root, source_root)

    assert f"{prefix}artifacts/nodes.json" in bucket.objects
    assert f"{prefix}artifacts/stale.json" not in bucket.objects
    assert all("_stage_work" not in key and not key.endswith(".tmp") for key in bucket.objects)


def test_restore_rejects_remote_traversal_key(tmp_path: Path) -> None:
    bucket = FakeBucket()
    storage = configured_storage(bucket)
    prefix = storage.task_prefix(7, "job-1")
    bucket.objects[f"{prefix}artifacts/../../outside.txt"] = b"private"

    with pytest.raises(ObjectStorageError, match="unsafe object key"):
        storage.restore_job(7, "job-1", tmp_path / "job", tmp_path / "source")
    assert not (tmp_path / "outside.txt").exists()


def test_provider_error_details_are_not_exposed(tmp_path: Path) -> None:
    bucket = FakeBucket()
    bucket.fail_upload = True
    storage = configured_storage(bucket)
    artifact_root = tmp_path / "job"
    artifact_root.mkdir()
    (artifact_root / "input.md").write_text("source", encoding="utf-8")

    with pytest.raises(ObjectStorageError) as raised:
        storage.sync_job(7, "job-1", artifact_root, tmp_path / "source")
    assert "leaked-secret-value" not in str(raised.value)


def test_smoke_check_round_trips_marker_and_cleans_remote_objects(tmp_path: Path) -> None:
    bucket = FakeBucket()
    storage = configured_storage(bucket)

    run_smoke(storage, tmp_path, user_id=1, job_id="smoke-test")

    assert bucket.objects == {}
