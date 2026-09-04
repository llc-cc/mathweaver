from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


class FakeServer:
    should_exit = False


def wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def run_tests() -> None:
    with tempfile.TemporaryDirectory(prefix="mathweaver-parent-watch-") as data_dir:
        os.environ["MATHGRAPH_DATA_DIR"] = data_dir
        os.environ.pop("MATHWEAVER_DESKTOP_ENV_TEST", None)
        Path(data_dir, "storage.env").write_text(
            "MATHWEAVER_DESKTOP_ENV_TEST=loaded\n", encoding="utf-8"
        )
        from desktop_app import _start_parent_exit_watcher

        assert os.environ.get("MATHWEAVER_DESKTOP_ENV_TEST") == "loaded"

        parent = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(0.2)"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        server = FakeServer()
        shutdown_complete = threading.Event()
        forced_exit_codes: list[int] = []
        old_parent_pid = os.environ.get("MATHGRAPH_PARENT_PID")
        os.environ["MATHGRAPH_PARENT_PID"] = str(parent.pid)
        try:
            watcher = _start_parent_exit_watcher(
                server,
                shutdown_complete,
                force_exit=forced_exit_codes.append,
                grace_seconds=1.0,
            )
            assert watcher is not None
            parent.wait(timeout=5)
            assert wait_until(lambda: server.should_exit)
            shutdown_complete.set()
            watcher.join(timeout=2)
            assert not watcher.is_alive()
            assert forced_exit_codes == []
        finally:
            if parent.poll() is None:
                parent.kill()
            if old_parent_pid is None:
                os.environ.pop("MATHGRAPH_PARENT_PID", None)
            else:
                os.environ["MATHGRAPH_PARENT_PID"] = old_parent_pid

        parent = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        server = FakeServer()
        shutdown_complete = threading.Event()
        forced_exit_codes = []
        os.environ["MATHGRAPH_PARENT_PID"] = str(parent.pid)
        try:
            watcher = _start_parent_exit_watcher(
                server,
                shutdown_complete,
                force_exit=forced_exit_codes.append,
                grace_seconds=0.05,
            )
            assert watcher is not None
            parent.terminate()
            parent.wait(timeout=5)
            watcher.join(timeout=2)
            assert server.should_exit
            assert forced_exit_codes == [0]
        finally:
            if parent.poll() is None:
                parent.kill()
            if old_parent_pid is None:
                os.environ.pop("MATHGRAPH_PARENT_PID", None)
            else:
                os.environ["MATHGRAPH_PARENT_PID"] = old_parent_pid

    print("desktop parent watcher tests passed")


if __name__ == "__main__":
    run_tests()
