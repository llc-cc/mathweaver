"""Validate the production OCR release manifest."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from ocr_manifest import ManifestValidationError, validate_manifest  # noqa: E402


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "backend/assets/ocr/manifest.json")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        validate_manifest(manifest, production=True)
    except (OSError, json.JSONDecodeError, ManifestValidationError) as exc:
        print(f"OCR manifest invalid: {exc}", file=sys.stderr)
        return 1
    print(f"OCR manifest valid: {path} (2 archives)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
