"""阿里云 OSS 文件持久化边界；业务层只传受控目录和已认证的归属标识。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_OSS_REQUIRED_ENVIRONMENT = (
    "MATHWEAVER_OSS_ENDPOINT",
    "MATHWEAVER_OSS_BUCKET",
    "MATHWEAVER_OSS_ACCESS_KEY_ID",
    "MATHWEAVER_OSS_ACCESS_KEY_SECRET",
)


class ObjectStorageError(RuntimeError):
    """对外隐藏 SDK 请求细节和凭据的稳定存储错误。"""


@dataclass(frozen=True)
class StoredVersion:
    version_id: str
    prefix: str
    manifest_checksum: str
    file_count: int
    total_bytes: int


def _normalized_prefix(value: str) -> str:
    raw = value.strip().replace("\\", "/")
    if raw.startswith("/"):
        raise RuntimeError("MATHWEAVER_OSS_PREFIX must be a relative object prefix")
    parts = [part for part in raw.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise RuntimeError("MATHWEAVER_OSS_PREFIX must be a safe non-empty prefix")
    return "/".join(parts) + "/"


def _safe_job_id(value: str) -> str:
    job_id = str(value or "")
    if job_id in {".", ".."} or not _SAFE_JOB_ID.fullmatch(job_id):
        raise ValueError("job_id contains unsafe characters")
    return job_id


@dataclass(frozen=True)
class ObjectStorageConfig:
    """只在显式启用 OSS 时解析完整运行时配置。"""

    endpoint: str
    bucket: str
    access_key_id: str
    access_key_secret: str
    prefix: str = "mathweaver/"

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] = os.environ
    ) -> "ObjectStorageConfig | None":
        mode = str(environment.get("MATHWEAVER_OBJECT_STORAGE") or "local").strip().lower()
        if mode in {"", "local", "disabled"}:
            return None
        if mode != "oss":
            raise RuntimeError("MATHWEAVER_OBJECT_STORAGE must be local or oss")

        values = {
            name: str(environment.get(name) or "").strip()
            for name in _OSS_REQUIRED_ENVIRONMENT
        }
        missing = next((name for name, value in values.items() if not value), None)
        if missing:
            raise RuntimeError(f"{missing} is required when OSS storage is enabled")
        endpoint = values["MATHWEAVER_OSS_ENDPOINT"].rstrip("/")
        if not endpoint.lower().startswith("https://"):
            raise RuntimeError("MATHWEAVER_OSS_ENDPOINT must use HTTPS")
        prefix = _normalized_prefix(
            str(environment.get("MATHWEAVER_OSS_PREFIX") or "mathweaver/")
        )
        return cls(
            endpoint=endpoint,
            bucket=values["MATHWEAVER_OSS_BUCKET"],
            access_key_id=values["MATHWEAVER_OSS_ACCESS_KEY_ID"],
            access_key_secret=values["MATHWEAVER_OSS_ACCESS_KEY_SECRET"],
            prefix=prefix,
        )


BucketFactory = Callable[[ObjectStorageConfig], Any]


def _default_bucket_factory(config: ObjectStorageConfig):
    # 延迟导入让未启用 OSS 的桌面版和本地测试不依赖 SDK 初始化。
    import oss2

    auth = oss2.Auth(config.access_key_id, config.access_key_secret)
    return oss2.Bucket(auth, config.endpoint, config.bucket)


class OssObjectStorage:
    """在单个私有 Bucket 内按用户和任务同步、恢复及删除受控文件。"""

    def __init__(
        self,
        config: ObjectStorageConfig,
        *,
        bucket_factory: BucketFactory | None = None,
    ) -> None:
        self._config = config
        factory = bucket_factory or _default_bucket_factory
        try:
            self._bucket = factory(config)
        except Exception:
            raise ObjectStorageError("OSS client initialization failed") from None

    def task_prefix(self, user_id: int, job_id: str) -> str:
        if isinstance(user_id, bool) or int(user_id) <= 0:
            raise ValueError("user_id must be a positive integer")
        return (
            f"{self._config.prefix}users/{int(user_id)}/jobs/"
            f"{_safe_job_id(job_id)}/"
        )

    def version_prefix(self, user_id: int, job_id: str, version_id: str) -> str:
        if not re.fullmatch(r"[a-f0-9]{32}", str(version_id or "")):
            raise ValueError("version_id must be a lowercase UUID hex value")
        return f"{self.task_prefix(user_id, job_id)}versions/{version_id}/"

    @staticmethod
    def _local_files(root: Path, remote_subdir: str) -> dict[str, Path]:
        if not root.is_dir():
            return {}
        controlled_root = root.resolve()
        files: dict[str, Path] = {}
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if "_stage_work" in relative.parts or path.name.endswith(".tmp"):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            resolved = path.resolve()
            try:
                resolved.relative_to(controlled_root)
            except ValueError:
                continue
            files[f"{remote_subdir}/{relative.as_posix()}"] = resolved
        return files

    def _list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        continuation_token = ""
        try:
            while True:
                result = self._bucket.list_objects_v2(
                    prefix=prefix,
                    max_keys=1000,
                    continuation_token=continuation_token,
                )
                page = [str(item.key) for item in result.object_list]
                if any(not key.startswith(prefix) for key in page):
                    raise ObjectStorageError("OSS returned an object outside the task prefix")
                keys.extend(page)
                if not result.is_truncated:
                    break
                continuation_token = str(result.next_continuation_token or "")
                if not continuation_token:
                    raise ObjectStorageError("OSS object listing did not provide a continuation token")
        except ObjectStorageError:
            raise
        except Exception:
            raise ObjectStorageError("OSS object listing failed") from None
        return keys

    def _delete_keys(self, keys: list[str]) -> None:
        try:
            for start in range(0, len(keys), 1000):
                self._bucket.batch_delete_objects(keys[start : start + 1000])
        except Exception:
            raise ObjectStorageError("OSS object deletion failed") from None

    @staticmethod
    def _canonical_json(value: object) -> bytes:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def _read_object(self, key: str) -> bytes:
        try:
            return bytes(self._bucket.get_object(key).read())
        except Exception:
            raise ObjectStorageError("OSS version verification failed") from None

    def upload_version(
        self,
        user_id: int,
        job_id: str,
        artifact_root: Path,
        source_pdf_root: Path,
    ) -> StoredVersion:
        """先上传不可变数据对象，全部校验后最后写 manifest 作为提交标记。"""
        version_id = uuid.uuid4().hex
        prefix = self.version_prefix(user_id, job_id, version_id)
        relative_files = {
            **self._local_files(Path(artifact_root), "artifacts"),
            **self._local_files(Path(source_pdf_root), "source-pdf"),
        }
        entries: list[dict[str, object]] = []
        total_bytes = 0
        try:
            for relative, path in sorted(relative_files.items()):
                content = path.read_bytes()
                size = len(content)
                key = f"{prefix}{relative}"
                self._bucket.put_object_from_file(key, str(path))
                if int(self._bucket.get_object_meta(key).content_length) != size:
                    raise ObjectStorageError("OSS object upload verification failed")
                entries.append(
                    {
                        "path": relative,
                        "size": size,
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                )
                total_bytes += size
            manifest = {
                "schema_version": 1,
                "user_id": int(user_id),
                "job_id": _safe_job_id(job_id),
                "version_id": version_id,
                "files": entries,
            }
            manifest_bytes = self._canonical_json(manifest)
            self._bucket.put_object(f"{prefix}manifest.json", manifest_bytes)
        except ObjectStorageError:
            raise
        except Exception:
            # 未写 manifest 的残留版本不会被数据库引用，由孤儿扫描异步清理。
            raise ObjectStorageError("OSS version upload failed") from None
        return StoredVersion(
            version_id,
            prefix,
            hashlib.sha256(manifest_bytes).hexdigest(),
            len(entries),
            total_bytes,
        )

    def verify_version(
        self,
        user_id: int,
        job_id: str,
        version_id: str,
        expected_checksum: str,
    ) -> StoredVersion:
        prefix = self.version_prefix(user_id, job_id, version_id)
        manifest_bytes = self._read_object(f"{prefix}manifest.json")
        checksum = hashlib.sha256(manifest_bytes).hexdigest()
        if not re.fullmatch(r"[a-f0-9]{64}", str(expected_checksum or "")) or checksum != expected_checksum:
            raise ObjectStorageError("OSS version verification failed")
        try:
            manifest = json.loads(manifest_bytes)
            if (
                manifest.get("schema_version") != 1
                or manifest.get("user_id") != int(user_id)
                or manifest.get("job_id") != _safe_job_id(job_id)
                or manifest.get("version_id") != version_id
                or not isinstance(manifest.get("files"), list)
            ):
                raise ValueError
            entries = manifest["files"]
            expected_keys = {f"{prefix}manifest.json"}
            paths: set[str] = set()
            total_bytes = 0
            for entry in entries:
                relative = str(entry["path"])
                pure = PurePosixPath(relative)
                if (
                    len(pure.parts) < 2
                    or pure.parts[0] not in {"artifacts", "source-pdf"}
                    or any(part in {"", ".", ".."} for part in pure.parts)
                    or relative in paths
                ):
                    raise ValueError
                paths.add(relative)
                content = self._read_object(f"{prefix}{relative}")
                size = int(entry["size"])
                digest = str(entry["sha256"])
                if size < 0 or len(content) != size or hashlib.sha256(content).hexdigest() != digest:
                    raise ValueError
                total_bytes += size
                expected_keys.add(f"{prefix}{relative}")
            if set(self._list_keys(prefix)) != expected_keys:
                raise ValueError
        except ObjectStorageError:
            raise
        except Exception:
            raise ObjectStorageError("OSS version verification failed") from None
        return StoredVersion(version_id, prefix, checksum, len(entries), total_bytes)

    def sync_job(
        self,
        user_id: int,
        job_id: str,
        artifact_root: Path,
        source_pdf_root: Path,
    ) -> str:
        prefix = self.task_prefix(user_id, job_id)
        relative_files = {
            **self._local_files(Path(artifact_root), "artifacts"),
            **self._local_files(Path(source_pdf_root), "source-pdf"),
        }
        desired_keys = {f"{prefix}{relative}" for relative in relative_files}
        try:
            for relative, path in sorted(relative_files.items()):
                self._bucket.put_object_from_file(f"{prefix}{relative}", str(path))
        except Exception:
            # SDK 异常可能携带签名请求信息，不能原样进入 API 错误或日志。
            raise ObjectStorageError("OSS object upload failed") from None

        stale_keys = [key for key in self._list_keys(prefix) if key not in desired_keys]
        if stale_keys:
            self._delete_keys(stale_keys)
        return prefix

    @staticmethod
    def _restore_target(
        prefix: str,
        key: str,
        artifact_root: Path,
        source_pdf_root: Path,
    ) -> Path:
        relative_key = key[len(prefix) :]
        pure = PurePosixPath(relative_key)
        parts = pure.parts
        if (
            len(parts) < 2
            or parts[0] not in {"artifacts", "source-pdf"}
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ObjectStorageError("OSS returned an unsafe object key")
        root = Path(artifact_root if parts[0] == "artifacts" else source_pdf_root)
        controlled_root = root.resolve()
        target = root.joinpath(*parts[1:])
        try:
            target.resolve().relative_to(controlled_root)
        except ValueError:
            raise ObjectStorageError("OSS returned an unsafe object key") from None
        return target

    def restore_job(
        self,
        user_id: int,
        job_id: str,
        artifact_root: Path,
        source_pdf_root: Path,
    ) -> bool:
        prefix = self.task_prefix(user_id, job_id)
        keys = self._list_keys(prefix)
        for key in keys:
            target = self._restore_target(
                prefix,
                key,
                Path(artifact_root),
                Path(source_pdf_root),
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            try:
                self._bucket.get_object_to_file(key, str(temporary))
                os.replace(temporary, target)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise ObjectStorageError("OSS object download failed") from None
        return bool(keys)

    def delete_job(self, user_id: int, job_id: str) -> None:
        prefix = self.task_prefix(user_id, job_id)
        keys = self._list_keys(prefix)
        if keys:
            self._delete_keys(keys)
