"""生产上传、历史 JSON、用户配额与磁盘保留空间边界。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil


class CapacityExceeded(RuntimeError):
    def __init__(self, code: str, http_status: int) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = int(http_status)


@dataclass(frozen=True)
class PayloadSize:
    node_count: int
    edge_count: int
    encoded_bytes: int


@dataclass(frozen=True)
class CapacityLimits:
    max_upload_bytes: int = 100 * 1024 * 1024
    max_node_count: int = 100_000
    max_edge_count: int = 300_000
    max_history_json_bytes: int = 64 * 1024 * 1024
    max_user_history_bytes: int = 10 * 1024 * 1024 * 1024
    min_free_disk_bytes: int = 2 * 1024 * 1024 * 1024
    retention_days: int = 90

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] = os.environ
    ) -> "CapacityLimits":
        defaults = cls()
        mapping = {
            "max_upload_bytes": "MATHWEAVER_MAX_UPLOAD_BYTES",
            "max_node_count": "MATHWEAVER_MAX_NODE_COUNT",
            "max_edge_count": "MATHWEAVER_MAX_EDGE_COUNT",
            "max_history_json_bytes": "MATHWEAVER_MAX_HISTORY_JSON_BYTES",
            "max_user_history_bytes": "MATHWEAVER_MAX_USER_HISTORY_BYTES",
            "min_free_disk_bytes": "MATHWEAVER_MIN_FREE_DISK_BYTES",
            "retention_days": "MATHWEAVER_HISTORY_RETENTION_DAYS",
        }
        values: dict[str, int] = {}
        for field, name in mapping.items():
            raw = environment.get(name)
            if raw is None or str(raw).strip() == "":
                values[field] = getattr(defaults, field)
                continue
            try:
                parsed = int(str(raw).strip())
            except ValueError:
                raise RuntimeError(f"{name} must be a positive integer") from None
            if parsed <= 0:
                raise RuntimeError(f"{name} must be a positive integer")
            values[field] = parsed
        if values["max_user_history_bytes"] < values["max_history_json_bytes"]:
            raise RuntimeError(
                "MATHWEAVER_MAX_USER_HISTORY_BYTES must cover one history payload"
            )
        return cls(**values)

    @staticmethod
    def _encoded(value: object) -> bytes:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def validate_upload_size(self, size: int) -> None:
        if int(size) > self.max_upload_bytes:
            raise CapacityExceeded("upload_too_large", 413)

    def validate_history_payload(
        self,
        nodes: list,
        edges: list,
        source_markdown: str | None,
        source_pdf: dict | None,
    ) -> PayloadSize:
        if len(nodes) > self.max_node_count:
            raise CapacityExceeded("node_count_exceeded", 422)
        if len(edges) > self.max_edge_count:
            raise CapacityExceeded("edge_count_exceeded", 422)
        encoded_bytes = sum(
            len(self._encoded(value))
            for value in (nodes, edges, source_markdown or "", source_pdf or {})
        )
        if encoded_bytes > self.max_history_json_bytes:
            raise CapacityExceeded("history_json_too_large", 422)
        return PayloadSize(len(nodes), len(edges), encoded_bytes)

    def ensure_disk_capacity(self, path: Path, required_bytes: int) -> None:
        probe = Path(path).resolve()
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        free = int(shutil.disk_usage(probe).free)
        if free - max(0, int(required_bytes)) < self.min_free_disk_bytes:
            raise CapacityExceeded("insufficient_disk_capacity", 507)

    def ensure_user_storage_capacity(
        self,
        repository,
        user_id: int,
        incoming_bytes: int,
        *,
        replacing_history_id: str | None = None,
    ) -> None:
        used = int(
            repository.user_storage_bytes(
                user_id, exclude_history_id=replacing_history_id
            )
        )
        if used + max(0, int(incoming_bytes)) > self.max_user_history_bytes:
            raise CapacityExceeded("user_storage_quota_exceeded", 413)
