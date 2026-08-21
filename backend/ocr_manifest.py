"""Validation for the production OCR component manifest.

The application and release scripts use the same validator so a manifest that
passes the packaging gate is also installable by the desktop runtime.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


_RELEASE_REPO_PATHS = {
    "/ss2599/pdfPipeline/releases/download/",
    "/SJTU-AI4Math/pdfPipeline/releases/download/",
    "/SJTU-AI4MATH/MathWeaver/releases/download/",
}


def _is_mathweaver_release_url(path: str, release_version: str) -> bool:
    return any(path.startswith(prefix) for prefix in _RELEASE_REPO_PATHS) and (
        f"/releases/download/v{release_version}/" in path
    )


PART_LIMIT = int(1.5 * 1024 * 1024 * 1024)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PLACEHOLDER_VALUES = {
    "",
    "LOCKED_IN_RELEASE_MANIFEST",
    "<locked-model-revision>",
    "<revision>",
}


class ManifestValidationError(ValueError):
    """Raised when a manifest cannot describe an installable release."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ManifestValidationError(message)


def _positive_int(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ManifestValidationError(f"{label} must be a positive integer") from exc
    _require(number > 0, f"{label} must be a positive integer")
    return number


def _sha256(value: Any, label: str) -> str:
    digest = str(value or "").lower()
    _require(SHA256_RE.fullmatch(digest) is not None, f"{label} must be a SHA-256 digest")
    return digest


def _validate_archive(archive: Any, *, production: bool, release_version: str = "") -> tuple[str, int, int]:
    _require(isinstance(archive, dict), "OCR archive entries must be objects")
    role = str(archive.get("role") or "")
    _require(role in {"runtime", "models"}, "OCR archives must contain runtime and models roles")
    name = Path(str(archive.get("name") or "")).name
    _require(name == str(archive.get("name") or ""), f"Invalid OCR archive name for {role}")
    _require(name.lower().endswith((".zip", ".tar", ".tar.gz", ".tgz", ".tar.xz")), f"Invalid OCR archive format for {role}")
    archive_size = _positive_int(archive.get("size"), f"{role} archive size")
    unpacked_size = _positive_int(archive.get("unpacked_size"), f"{role} unpacked size")
    _sha256(archive.get("sha256"), f"{role} archive hash")
    extract_to = str(archive.get("extract_to") or "")
    _require(extract_to in {"runtime", "models/pipeline"}, f"Invalid extraction path for {role}")
    parts = archive.get("parts")
    _require(isinstance(parts, list) and parts, f"{role} archive must have non-empty parts")
    part_total = 0
    for part in parts:
        _require(isinstance(part, dict), f"Invalid {role} archive part")
        part_name = Path(str(part.get("name") or "")).name
        _require(part_name == str(part.get("name") or ""), f"Invalid {role} archive part name")
        part_size = _positive_int(part.get("size"), f"{role} part size")
        _require(part_size <= PART_LIMIT, f"{role} part exceeds 1.5 GiB")
        _sha256(part.get("sha256"), f"{role} part hash")
        url = str(part.get("url") or "")
        parsed = urlparse(url)
        _require(parsed.scheme == "https", f"{role} part URL must use HTTPS")
        if production:
            _require(parsed.netloc.lower() == "github.com", f"{role} part URL must use github.com")
            _require(_is_mathweaver_release_url(parsed.path, release_version), f"{role} part URL must target the MathWeaver release")
        part_total += part_size
    _require(part_total == archive_size, f"{role} part sizes do not equal archive size")
    return role, archive_size, unpacked_size


def _validate_auxiliary_asset(asset: Any, *, production: bool, release_version: str) -> None:
    _require(isinstance(asset, dict), "OCR auxiliary assets must be objects")
    name = Path(str(asset.get("name") or "")).name
    _require(name == str(asset.get("name") or "") and name, "Invalid OCR auxiliary asset name")
    _positive_int(asset.get("size"), f"OCR auxiliary asset size for {name}")
    _sha256(asset.get("sha256"), f"OCR auxiliary asset hash for {name}")
    url = str(asset.get("url") or "")
    parsed = urlparse(url)
    _require(parsed.scheme == "https", f"OCR auxiliary asset URL must use HTTPS: {name}")
    if production:
        _require(parsed.netloc.lower() == "github.com", f"OCR auxiliary asset URL must use github.com: {name}")
        _require(_is_mathweaver_release_url(parsed.path, release_version), f"OCR auxiliary asset URL must target the MathWeaver release: {name}")


def validate_manifest(manifest: Any, *, production: bool = True, base_dir: Path | None = None) -> dict[str, Any]:
    """Validate and return a manifest suitable for installation."""

    _require(isinstance(manifest, dict), "OCR manifest must be a JSON object")
    _require(int(manifest.get("schema_version") or 0) == 2, "OCR manifest schema_version must be 2")
    _require(str(manifest.get("platform") or "") == "windows-x64", "OCR manifest must target Windows x64")
    _require(str(manifest.get("python") or "") == "3.12.9", "OCR manifest must pin Python 3.12.9")
    _require(str(manifest.get("mineru") or "") == "mineru[pipeline]==3.4.4", "OCR manifest must pin MinerU 3.4.4 pipeline")
    version = str(manifest.get("version") or "")
    _require(version not in PLACEHOLDER_VALUES, "OCR component version is not locked")
    release_version = str(manifest.get("release_version") or "")
    _require(re.fullmatch(r"\d+\.\d+\.\d+", release_version) is not None, "OCR release_version must be semantic version")
    model_revision = str(manifest.get("model_revision") or "")
    _require(model_revision not in PLACEHOLDER_VALUES and re.fullmatch(r"[0-9a-f]{40}", model_revision) is not None, "OCR model revision must be a full commit SHA")
    _sha256(manifest.get("dependency_lock_sha256"), "OCR dependency lock hash")
    _sha256(manifest.get("sbom_sha256"), "OCR SBOM hash")
    _require(Path(str(manifest.get("sbom_path") or "")).name == str(manifest.get("sbom_path") or ""), "Invalid OCR SBOM path")
    _require(str(manifest.get("sbom_path") or "").endswith(".json"), "OCR SBOM path must be JSON")
    _sha256(manifest.get("calibration_sha256"), "OCR calibration hash")
    _require(Path(str(manifest.get("models_manifest_path") or "")).name == str(manifest.get("models_manifest_path") or ""), "Invalid OCR model manifest path")
    _sha256(manifest.get("models_manifest_sha256"), "OCR model manifest hash")
    calibration_pdf = Path(str(manifest.get("calibration_pdf") or ""))
    _require(not calibration_pdf.is_absolute() and ".." not in calibration_pdf.parts, "Invalid OCR calibration path")
    _require(str(manifest.get("runtime_subdir") or "") == "runtime", "OCR runtime_subdir must be runtime")
    _require(str(manifest.get("models_subdir") or "") == "models/pipeline", "OCR models_subdir must be models/pipeline")
    _require(str(manifest.get("config_subpath") or "") == "mineru.json", "OCR config_subpath must be mineru.json")
    _require(str(manifest.get("model_source") or "") == "local", "OCR model_source must be local")
    _require(str(manifest.get("inference") or "") == "cpu", "OCR inference must be cpu")
    entrypoint = str(manifest.get("entrypoint") or "")
    _require(entrypoint == "{runtime_dir}/python.exe", "OCR entrypoint must use the portable Python runtime")
    entrypoint_args = manifest.get("entrypoint_args")
    _require(entrypoint_args == ["-m", "mineru.cli.fast_api"], "OCR entrypoint_args are not pinned")
    _require(str(manifest.get("license") or "").strip(), "OCR license information is required")
    licenses = manifest.get("license_files")
    _require(isinstance(licenses, list) and all(str(item).strip() for item in licenses), "OCR license files are required")
    auxiliary_assets = manifest.get("auxiliary_assets")
    if production:
        _require(isinstance(auxiliary_assets, list) and auxiliary_assets, "OCR auxiliary assets are required")
    if auxiliary_assets is not None:
        _require(isinstance(auxiliary_assets, list), "OCR auxiliary assets must be a list")
        for asset in auxiliary_assets:
            _validate_auxiliary_asset(asset, production=production, release_version=release_version)
    archives = manifest.get("archives")
    _require(isinstance(archives, list) and len(archives) == 2, "OCR manifest must contain exactly runtime and models archives")
    archive_info = [_validate_archive(archive, production=production, release_version=release_version) for archive in archives]
    roles = {role for role, _, _ in archive_info}
    _require(roles == {"runtime", "models"}, "OCR manifest must contain exactly runtime and models roles")
    download_bytes = sum(size for _, size, _ in archive_info)
    installed_bytes = sum(size for _, _, size in archive_info)
    _require(_positive_int(manifest.get("download_bytes"), "download_bytes") == download_bytes, "download_bytes does not match archives")
    _require(_positive_int(manifest.get("installed_bytes"), "installed_bytes") == installed_bytes, "installed_bytes does not match archives")
    peak_bytes = max(download_bytes * 2, download_bytes + installed_bytes)
    required_disk = math.ceil(peak_bytes * 1.1)
    _require(_positive_int(manifest.get("required_disk_bytes"), "required_disk_bytes") == required_disk, "required_disk_bytes does not match the install peak")
    return manifest
