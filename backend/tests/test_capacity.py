from collections import namedtuple
from pathlib import Path

import pytest

from storage.capacity import CapacityExceeded, CapacityLimits


def _limits(**overrides) -> CapacityLimits:
    values = {
        "max_upload_bytes": 1024,
        "max_node_count": 10,
        "max_edge_count": 20,
        "max_history_json_bytes": 1024,
        "max_user_history_bytes": 4096,
        "min_free_disk_bytes": 100,
        "retention_days": 90,
    }
    values.update(overrides)
    return CapacityLimits(**values)


def test_capacity_environment_rejects_zero_and_non_integer_values():
    with pytest.raises(RuntimeError, match="MATHWEAVER_MAX_UPLOAD_BYTES"):
        CapacityLimits.from_environment({"MATHWEAVER_MAX_UPLOAD_BYTES": "0"})
    with pytest.raises(RuntimeError, match="MATHWEAVER_MAX_NODE_COUNT"):
        CapacityLimits.from_environment({"MATHWEAVER_MAX_NODE_COUNT": "many"})


def test_history_json_limit_uses_utf8_bytes():
    limits = _limits(max_history_json_bytes=8)

    with pytest.raises(CapacityExceeded) as caught:
        limits.validate_history_payload([{"x": "数学"}], [], None, None)

    assert caught.value.code == "history_json_too_large"
    assert caught.value.http_status == 422


def test_disk_reserve_is_checked_before_writing(monkeypatch, tmp_path: Path):
    DiskUsage = namedtuple("DiskUsage", "total used free")
    monkeypatch.setattr("storage.capacity.shutil.disk_usage", lambda _path: DiskUsage(1000, 850, 150))

    with pytest.raises(CapacityExceeded) as caught:
        _limits(min_free_disk_bytes=100).ensure_disk_capacity(tmp_path, required_bytes=60)

    assert caught.value.code == "insufficient_disk_capacity"
    assert caught.value.http_status == 507


def test_user_quota_subtracts_replaced_job_bytes():
    class Repository:
        def user_storage_bytes(self, user_id, *, exclude_history_id=None):
            assert user_id == 7
            return 3500 if exclude_history_id is None else 2500

    limits = _limits(max_user_history_bytes=4096)
    with pytest.raises(CapacityExceeded, match="user_storage_quota_exceeded"):
        limits.ensure_user_storage_capacity(Repository(), 7, 700)
    limits.ensure_user_storage_capacity(
        Repository(), 7, 700, replacing_history_id="job-1"
    )
