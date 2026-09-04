"""Start the frozen desktop backend and require a healthy HTTP response."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def tail(path: Path, lines: int = 80) -> str:
    if not path.is_file():
        return ""
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill.exe", "/pid", str(process.pid), "/t", "/f"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("--log", type=Path, default=Path("build/backend-smoke.log"))
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    bundle = args.bundle.resolve()
    data_dir = args.data_dir.resolve()
    log_path = args.log.resolve()
    if not bundle.is_file():
        raise FileNotFoundError(f"Backend bundle not found: {bundle}")
    if not (data_dir / "storage.env").is_file():
        raise FileNotFoundError(f"Desktop storage configuration not found: {data_dir / 'storage.env'}")

    log_path.parent.mkdir(parents=True, exist_ok=True)
    port = available_port()
    env = os.environ.copy()
    env.update(
        {
            "MATHGRAPH_PORT": str(port),
            "MATHGRAPH_DATA_DIR": str(data_dir),
            "PYTHONUNBUFFERED": "1",
        }
    )
    env.pop("MATHGRAPH_PARENT_PID", None)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

    with log_path.open("wb") as log_handle:
        process = subprocess.Popen(
            [str(bundle)],
            cwd=str(bundle.parent),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        try:
            deadline = time.monotonic() + args.timeout
            health_url = f"http://127.0.0.1:{port}/health"
            while time.monotonic() < deadline:
                exit_code = process.poll()
                if exit_code is not None:
                    log_handle.flush()
                    print(f"Frozen backend exited before health check (exit code {exit_code}).")
                    output = tail(log_path)
                    if output:
                        print("--- backend smoke log ---")
                        print(output)
                    return 1
                try:
                    with urllib.request.urlopen(health_url, timeout=1.0) as response:
                        if response.status == 200:
                            print(f"Frozen backend health check passed on port {port}.")
                            return 0
                except (OSError, urllib.error.URLError):
                    pass
                time.sleep(0.5)

            log_handle.flush()
            print(f"Frozen backend did not become healthy within {args.timeout:.0f} seconds.")
            output = tail(log_path)
            if output:
                print("--- backend smoke log ---")
                print(output)
            return 1
        finally:
            stop_process_tree(process)


if __name__ == "__main__":
    raise SystemExit(main())
