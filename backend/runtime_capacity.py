"""Cross-process capacity leases for expensive backend workloads."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import BinaryIO


class CapacityLease:
    """An idempotently releasable OS file lock representing one active slot."""

    def __init__(self, handle: BinaryIO, slot: int) -> None:
        self._handle = handle
        self.slot = slot
        self._release_lock = threading.Lock()

    def release(self) -> None:
        with self._release_lock:
            handle = self._handle
            self._handle = None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (OSError, ImportError):
            pass
        finally:
            try:
                handle.close()
            except OSError:
                pass

    def __enter__(self) -> "CapacityLease":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()


class ProcessCapacityPool:
    """A small non-blocking semaphore shared by Gunicorn workers and services."""

    def __init__(self, root: str | os.PathLike[str], name: str, capacity: int) -> None:
        self.root = Path(root)
        self.name = name
        self.capacity = max(1, int(capacity))
        self._scan_lock = threading.Lock()

    def try_acquire(self) -> CapacityLease | None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._scan_lock:
            for slot in range(self.capacity):
                handle = self._open_slot(slot)
                if handle is None:
                    continue
                return CapacityLease(handle, slot)
        return None

    def _open_slot(self, slot: int) -> BinaryIO | None:
        path = self.root / f"{self.name}-{slot}.lock"
        handle: BinaryIO | None = None
        try:
            handle = path.open("a+b")
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except (OSError, ImportError):
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
            return None
