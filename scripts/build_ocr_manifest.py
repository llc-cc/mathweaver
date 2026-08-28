"""Build the deterministic production OCR manifest and release parts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tarfile
import zipfile
from pathlib import Path


PART_LIMIT = int(1.5 * 1024 * 1024 * 1024)
CHUNK_SIZE = 1 << 20


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def unpacked_size(path: Path) -> int:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            return sum(item.file_size for item in archive.infolist() if not item.is_dir())
    with tarfile.open(path) as archive:
        return sum(item.size for item in archive.getmembers() if item.isfile())


def ensure_calibration(archive_path: Path, calibration_name: str, calibration_sha256: str) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        names = {item.filename.replace("\\", "/") for item in archive.infolist()}
        if calibration_name not in names:
            raise RuntimeError(f"Runtime archive does not contain {calibration_name}")
        with archive.open(calibration_name) as handle:
            digest = hashlib.sha256()
            while chunk := handle.read(CHUNK_SIZE):
                digest.update(chunk)
        if digest.hexdigest() != calibration_sha256:
            raise RuntimeError(f"Calibration hash in runtime archive does not match {calibration_name}")


def ensure_archive_files(archive_path: Path, required: set[str]) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        names = {item.filename.replace("\\", "/") for item in archive.infolist() if not item.is_dir()}
    missing = sorted(required - names)
    if missing:
        raise RuntimeError(f"Runtime archive is missing required notices: {', '.join(missing)}")


def split_archive(source: Path, role: str, parts_dir: Path, base_url: str, part_limit: int = PART_LIMIT) -> dict[str, object]:
    parts_dir.mkdir(parents=True, exist_ok=True)
    size = source.stat().st_size
    if part_limit <= 0 or part_limit > PART_LIMIT:
        raise ValueError(f"part_limit must be between 1 and {PART_LIMIT} bytes")
    count = max(1, math.ceil(size / part_limit))
    parts: list[dict[str, object]] = []
    with source.open("rb") as handle:
        for index in range(count):
            part_name = f"{source.name}.part{index + 1:03d}"
            part_path = parts_dir / part_name
            remaining = min(part_limit, size - index * part_limit)
            with part_path.open("wb") as part:
                copied = 0
                while copied < remaining:
                    chunk = handle.read(min(CHUNK_SIZE, remaining - copied))
                    if not chunk:
                        raise RuntimeError(f"Unexpected EOF while splitting {source}")
                    part.write(chunk)
                    copied += len(chunk)
            parts.append({
                "name": part_name,
                "url": f"{base_url.rstrip('/')}/{part_name}",
                "size": part_path.stat().st_size,
                "sha256": sha256(part_path),
            })
    return {
        "role": role,
        "name": source.name,
        "sha256": sha256(source),
        "size": size,
        "unpacked_size": unpacked_size(source),
        "extract_to": "runtime" if role == "runtime" else "models/pipeline",
        "parts": parts,
    }


def auxiliary_asset(source: Path, base_url: str) -> dict[str, object]:
    """Describe a small release asset that is not part of an archive."""

    return {
        "name": source.name,
        "url": f"{base_url.rstrip('/')}/{source.name}",
        "size": source.stat().st_size,
        "sha256": sha256(source),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--release-version", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--runtime-archive", type=Path, required=True)
    parser.add_argument("--models-archive", type=Path, required=True)
    parser.add_argument("--parts-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--dependency-lock", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--models-manifest", type=Path, required=True)
    parser.add_argument("--calibration-pdf", type=Path, required=True)
    parser.add_argument("--license-file", type=Path, required=True)
    parser.add_argument("--notice-file", type=Path, required=True)
    parser.add_argument("--part-limit", type=int, default=PART_LIMIT)
    args = parser.parse_args()

    for path in (
        args.runtime_archive,
        args.models_archive,
        args.dependency_lock,
        args.sbom,
        args.models_manifest,
        args.calibration_pdf,
        args.license_file,
        args.notice_file,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)
    if len(args.model_revision) != 40 or any(char not in "0123456789abcdef" for char in args.model_revision.lower()):
        raise ValueError("--model-revision must be a full 40-character commit SHA")

    calibration_name = "calibration/calibration.pdf"
    calibration_digest = sha256(args.calibration_pdf)
    ensure_calibration(args.runtime_archive, calibration_name, calibration_digest)
    ensure_archive_files(args.runtime_archive, {"LICENSE-MinerU.txt", "NOTICE.txt"})
    runtime = split_archive(args.runtime_archive, "runtime", args.parts_dir, args.base_url, args.part_limit)
    models = split_archive(args.models_archive, "models", args.parts_dir, args.base_url, args.part_limit)
    archives = [runtime, models]
    download_bytes = sum(int(item["size"]) for item in archives)
    installed_bytes = sum(int(item["unpacked_size"]) for item in archives)
    required_disk_bytes = math.ceil(max(download_bytes * 2, download_bytes + installed_bytes) * 1.1)
    manifest = {
        "schema_version": 2,
        "component": "mineru-pipeline-cpu",
        "version": args.version,
        "release_version": args.release_version,
        "platform": "windows-x64",
        "python": "3.12.9",
        "mineru": "mineru[pipeline]==3.4.4",
        "model_revision": args.model_revision.lower(),
        "model_source": "local",
        "inference": "cpu",
        "runtime_subdir": "runtime",
        "models_subdir": "models/pipeline",
        "config_subpath": "mineru.json",
        "entrypoint": "{runtime_dir}/python.exe",
        "entrypoint_args": ["-m", "mineru.cli.fast_api"],
        "calibration_pdf": calibration_name,
        "calibration_sha256": calibration_digest,
        "dependency_lock_sha256": sha256(args.dependency_lock),
        "sbom_path": args.sbom.name,
        "sbom_sha256": sha256(args.sbom),
        "models_manifest_path": args.models_manifest.name,
        "models_manifest_sha256": sha256(args.models_manifest),
        "auxiliary_assets": [
            auxiliary_asset(args.dependency_lock, args.base_url),
            auxiliary_asset(args.sbom, args.base_url),
            auxiliary_asset(args.models_manifest, args.base_url),
            auxiliary_asset(args.license_file, args.base_url),
            auxiliary_asset(args.notice_file, args.base_url),
        ],
        "download_bytes": download_bytes,
        "installed_bytes": installed_bytes,
        "required_disk_bytes": required_disk_bytes,
        "license": "MinerU Open Source License (Apache 2.0 with additional conditions)",
        "license_files": ["LICENSE-MinerU.txt", "NOTICE.txt"],
        "archives": archives,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.output} with {sum(len(item['parts']) for item in archives)} parts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
