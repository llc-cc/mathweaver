"""Fail packaging when backend.exe contains non-whitelisted runtime data."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ALLOWED_BACKEND_FILES = {
    "backend/assets/tex_templates/elegantbook.cls",
    "backend/assets/tex_templates/elegantbook/image/cert.pdf",
    "backend/assets/tex_templates/elegantbook/image/donate.jpg",
    "backend/assets/tex_templates/elegantbook/image/founder.png",
    "backend/assets/tex_templates/elegantbook/image/K33.png",
    "backend/assets/tex_templates/elegantbook/image/scatter.pdf",
    "backend/assets/tex_templates/elegantbook/image/tlshell.png",
    "backend/assets/tex_templates/elegantbook/image/winding number.png",
}
FORBIDDEN_BACKEND_PATHS = {
    "backend/.env",
    "backend/auth.db",
    "backend/books",
    "backend/checkpoint",
}
REQUIRED_MODULES = {
    "cryptography",
    "dotenv",
    "neo4j",
    "ocr_runtime",
    "pymysql",
    "python_multipart",
    "pypdf",
    "sqlalchemy",
    "storage.database",
    "tzdata",
}
REQUIRED_DATA_PREFIXES = {
    "tzdata/zoneinfo/",
}


def archive_entries(bundle: Path) -> set[str]:
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller.utils.cliutils.archive_viewer", "-l", "-r", "-b", str(bundle)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "PyInstaller archive inspection failed")
    return {line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}


def main() -> int:
    bundle = Path(sys.argv[1] if len(sys.argv) > 1 else "dist/backend/backend.exe")
    if not bundle.is_file():
        raise FileNotFoundError(f"Backend bundle not found: {bundle}")

    entries = archive_entries(bundle)
    missing_modules = sorted(
        module for module in REQUIRED_MODULES
        if module not in entries and not any(entry.startswith(f"{module}.") for entry in entries)
    )
    missing_data = sorted(
        prefix for prefix in REQUIRED_DATA_PREFIXES
        if not any(entry.startswith(prefix) for entry in entries)
    )
    backend_files = {entry for entry in entries if entry.startswith("backend/")}
    unexpected = sorted(backend_files - ALLOWED_BACKEND_FILES)
    missing = sorted(ALLOWED_BACKEND_FILES - backend_files)
    forbidden = sorted(
        entry
        for entry in entries
        if any(entry == path or entry.startswith(f"{path}/") for path in FORBIDDEN_BACKEND_PATHS)
    )
    if missing_modules or missing_data or unexpected or missing or forbidden:
        if missing_modules:
            print("Missing required Python modules:", ", ".join(missing_modules), file=sys.stderr)
        if missing_data:
            print("Missing required runtime data:", ", ".join(missing_data), file=sys.stderr)
        if unexpected:
            print("Unexpected backend data:", ", ".join(unexpected), file=sys.stderr)
        if missing:
            print("Missing allowed static resources:", ", ".join(missing), file=sys.stderr)
        if forbidden:
            print("Forbidden runtime data:", ", ".join(forbidden), file=sys.stderr)
        return 1
    if not any(entry.startswith("frontend/") for entry in entries):
        print("Frontend static bundle is missing", file=sys.stderr)
        return 1

    print("Backend bundle contains only the approved static resources.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
