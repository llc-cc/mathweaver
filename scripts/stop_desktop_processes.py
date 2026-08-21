"""Stop MathWeaver processes that can lock desktop packaging outputs."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST_BACKEND = os.path.normcase(str(PROJECT_ROOT / "dist" / "backend" / "backend.exe"))
RELEASE_ROOT = os.path.normcase(str(PROJECT_ROOT / "release"))
RELEASE_EXECUTABLES = {"backend.exe", "mathweaver.exe", "mathgraph.exe"}

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_PATH = 260


class ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


def _is_within(path: str, directory: str) -> bool:
    try:
        return os.path.commonpath((path, directory)) == directory
    except ValueError:
        return False


def _is_project_desktop_process(executable: str) -> bool:
    normalized = os.path.normcase(os.path.abspath(executable))
    if normalized == DIST_BACKEND:
        return True
    return (
        _is_within(normalized, RELEASE_ROOT)
        and os.path.basename(normalized) in RELEASE_EXECUTABLES
    )


def _processes() -> dict[int, tuple[int, str]]:
    if sys.platform != "win32":
        return {}

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())

    found: dict[int, tuple[int, str]] = {}
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        has_entry = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while has_entry:
            pid = int(entry.th32ProcessID)
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                try:
                    buffer = ctypes.create_unicode_buffer(32768)
                    size = wintypes.DWORD(len(buffer))
                    if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                        found[pid] = (int(entry.th32ParentProcessID), buffer.value)
                finally:
                    kernel32.CloseHandle(handle)
            has_entry = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def _owned_processes() -> dict[int, tuple[int, str]]:
    return {
        pid: process
        for pid, process in _processes().items()
        if _is_project_desktop_process(process[1])
    }


def main() -> int:
    owned = _owned_processes()
    if not owned:
        print("No project-owned desktop processes are running.")
        return 0

    root_pids = [pid for pid, (parent_pid, _) in owned.items() if parent_pid not in owned]
    for pid in root_pids:
        executable = owned[pid][1]
        print(f"Stopping PID {pid}: {executable}")
        result = subprocess.run(
            ["taskkill.exe", "/pid", str(pid), "/t", "/f"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            print(message or f"taskkill failed with exit code {result.returncode}", file=sys.stderr)

    deadline = time.monotonic() + 10
    remaining = _owned_processes()
    while remaining and time.monotonic() < deadline:
        time.sleep(0.25)
        remaining = _owned_processes()

    if remaining:
        for pid, (_, executable) in remaining.items():
            print(f"Still running PID {pid}: {executable}", file=sys.stderr)
        return 1

    print("Project-owned desktop processes stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
