from __future__ import annotations

import copy
import json
import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ocr_manifest import ManifestValidationError, validate_manifest


PART_BYTES = b"test"
PART_SHA = hashlib.sha256(PART_BYTES).hexdigest()


def valid_manifest() -> dict[str, object]:
    archive = {
        "name": "component.zip",
        "size": len(PART_BYTES),
        "unpacked_size": len(PART_BYTES),
        "sha256": PART_SHA,
        "parts": [{"name": "component.zip.part001", "url": "https://example.test/component.zip.part001", "size": len(PART_BYTES), "sha256": PART_SHA}],
    }
    runtime = copy.deepcopy(archive)
    runtime.update({"role": "runtime", "extract_to": "runtime", "name": "runtime.zip"})
    models = copy.deepcopy(archive)
    models.update({"role": "models", "extract_to": "models/pipeline", "name": "models.zip"})
    return {
        "schema_version": 2,
        "component": "mineru-pipeline-cpu",
        "version": "3.4.4-cpu-win-x64-r1",
        "release_version": "0.1.1",
        "platform": "windows-x64",
        "python": "3.12.9",
        "mineru": "mineru[pipeline]==3.4.4",
        "model_revision": "a" * 40,
        "model_source": "local",
        "inference": "cpu",
        "runtime_subdir": "runtime",
        "models_subdir": "models/pipeline",
        "config_subpath": "mineru.json",
        "entrypoint": "{runtime_dir}/python.exe",
        "entrypoint_args": ["-m", "mineru.cli.fast_api"],
        "calibration_pdf": "calibration/calibration.pdf",
        "calibration_sha256": "b" * 64,
        "dependency_lock_sha256": "c" * 64,
        "sbom_path": "mineru-sbom.json",
        "sbom_sha256": "d" * 64,
        "models_manifest_path": "models-manifest.json",
        "models_manifest_sha256": "e" * 64,
        "download_bytes": 8,
        "installed_bytes": 8,
        "required_disk_bytes": 18,
        "license": "MinerU Open Source License",
        "license_files": ["LICENSE-MinerU.txt", "NOTICE.txt"],
        "archives": [runtime, models],
    }


class OcrManifestTests(unittest.TestCase):
    def test_valid_manifest_passes_nonproduction_validation(self) -> None:
        validate_manifest(valid_manifest(), production=False)

    def test_production_source_manifest_is_installable(self) -> None:
        path = Path(__file__).resolve().parents[1] / "assets" / "ocr" / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        validate_manifest(manifest, production=True)

    def test_empty_archives_are_rejected(self) -> None:
        manifest = valid_manifest()
        manifest["archives"] = []
        with self.assertRaises(ManifestValidationError):
            validate_manifest(manifest, production=False)

    def test_missing_role_and_bad_part_sum_are_rejected(self) -> None:
        manifest = valid_manifest()
        manifest["archives"] = [manifest["archives"][0], copy.deepcopy(manifest["archives"][0])]
        with self.assertRaises(ManifestValidationError):
            validate_manifest(manifest, production=False)

        manifest = valid_manifest()
        manifest["download_bytes"] = 7
        with self.assertRaises(ManifestValidationError):
            validate_manifest(manifest, production=False)

    def test_production_urls_must_target_github_release(self) -> None:
        manifest = valid_manifest()
        part = manifest["archives"][0]["parts"][0]
        part["url"] = "https://example.test/releases/component.zip.part001"
        with self.assertRaises(ManifestValidationError):
            validate_manifest(manifest, production=True)


if __name__ == "__main__":
    unittest.main()
