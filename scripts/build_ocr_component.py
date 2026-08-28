"""Build the pinned, self-contained Windows x64 OCR component.

The build host supplies an official CPython embeddable zip, a hashed wheelhouse
and a reviewed model snapshot.  No project venv is copied into the release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path


PINNED_REQUIREMENT = "mineru[pipeline]==3.4.4"
CHUNK_SIZE = 1 << 20


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def deterministic_zip(source: Path, destination: Path) -> None:
    files = sorted(path for path in source.rglob("*") if path.is_file())
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with path.open("rb") as source_handle, archive.open(info, "w") as target:
                shutil.copyfileobj(source_handle, target, CHUNK_SIZE)


def configure_embedded_python(runtime_dir: Path) -> None:
    # The embeddable distribution names this file ``python312._pth``.
    pth_files = sorted(runtime_dir.glob("*._pth"))
    if not pth_files:
        raise RuntimeError("Official CPython embeddable runtime is missing its *_._pth file")
    pth = pth_files[0]
    lines = [line.strip() for line in pth.read_text(encoding="utf-8").splitlines() if line.strip()]
    if "Lib/site-packages" not in lines:
        lines.append("Lib/site-packages")
    if "import site" not in lines:
        lines.append("import site")
    pth.write_text("\n".join(lines) + "\n", encoding="utf-8")


def copy_embedded_python(embed_zip: Path, destination: Path) -> None:
    with zipfile.ZipFile(embed_zip) as archive:
        archive.extractall(destination)
    configure_embedded_python(destination)


def write_sbom(path: Path, python: Path, model_revision: str, site_packages: Path) -> None:
    packages = json.loads(subprocess.check_output([str(python), "-m", "pip", "list", "--path", str(site_packages), "--format=json"], text=True))
    packages = sorted(packages, key=lambda item: (str(item.get("name", "")).lower(), str(item.get("version", ""))))
    path.write_text(
        json.dumps(
            {
                "format": "mathweaver-ocr-sbom-v1",
                "python": "3.12.9",
                "mineru": PINNED_REQUIREMENT,
                "model_revision": model_revision,
                "packages": packages,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def write_model_manifest(path: Path, model_root: Path, model_revision: str) -> None:
    files = []
    for file_path in sorted(item for item in model_root.rglob("*") if item.is_file() and item.name != path.name):
        files.append({
            "path": file_path.relative_to(model_root).as_posix(),
            "size": file_path.stat().st_size,
            "sha256": sha256(file_path),
        })
    path.write_text(json.dumps({"format": "mathweaver-model-manifest-v1", "model_revision": model_revision, "files": files}, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, required=True, help="CPython 3.12.9 build interpreter")
    parser.add_argument("--python-embed", type=Path, required=True, help="Official CPython 3.12.9 Windows x64 embeddable zip")
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True, help="Hashed Windows x64 dependency lock")
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--required-model-path", action="append", default=[], help="Required relative model path; may be repeated")
    parser.add_argument("--python-embed-sha256", required=True, help="Official CPython embeddable zip SHA-256")
    parser.add_argument("--calibration-pdf", type=Path, required=True)
    parser.add_argument("--mineru-license", type=Path, required=True)
    parser.add_argument("--notice-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--keep-build-dir", action="store_true")
    args = parser.parse_args()

    if os.name != "nt":
        raise RuntimeError("The pinned OCR component is Windows-only")
    version = subprocess.check_output([str(args.python), "-c", "import platform; print(platform.python_version())"], text=True).strip()
    if version != "3.12.9":
        raise RuntimeError(f"OCR build requires Python 3.12.9, got {version}")
    if len(args.model_revision) != 40 or any(char not in "0123456789abcdef" for char in args.model_revision.lower()):
        raise RuntimeError("--model-revision must be a full model commit SHA")
    expected_embed_sha256 = str(args.python_embed_sha256).strip().lower()
    actual_embed_sha256 = sha256(args.python_embed)
    if len(expected_embed_sha256) != 64 or any(char not in "0123456789abcdef" for char in expected_embed_sha256):
        raise RuntimeError("--python-embed-sha256 must be a 64-character hexadecimal SHA-256")
    if expected_embed_sha256 != actual_embed_sha256:
        raise RuntimeError("CPython embeddable archive SHA-256 does not match the release lock")
    for path in (args.python_embed, args.wheelhouse, args.lock, args.model_root, args.calibration_pdf, args.mineru_license, args.notice_file):
        if not path.exists():
            raise FileNotFoundError(path)

    build_root = args.output_dir / "_build-runtime"
    if build_root.exists():
        raise RuntimeError(f"Refusing to reuse build directory: {build_root}")
    runtime_dir = build_root / "runtime"
    models_dir = build_root / "models" / "pipeline"
    build_root.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    copy_embedded_python(args.python_embed, runtime_dir)
    site_packages = runtime_dir / "Lib" / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(args.python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--no-index",
            "--find-links",
            str(args.wheelhouse),
            "--require-hashes",
            "--target",
            str(site_packages),
            "-r",
            str(args.lock),
        ],
        check=True,
    )
    calibration_target = runtime_dir / "calibration" / "calibration.pdf"
    calibration_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.calibration_pdf, calibration_target)
    shutil.copyfile(args.mineru_license, runtime_dir / "LICENSE-MinerU.txt")
    shutil.copyfile(args.notice_file, runtime_dir / "NOTICE.txt")
    if args.required_model_path:
        for relative in args.required_model_path:
            relative_path = Path(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise RuntimeError(f"Invalid relative model path: {relative}")
            source = args.model_root / relative_path
            required = models_dir / relative_path
            if not source.exists():
                raise RuntimeError(f"Required pipeline model path is missing: {relative}")
            if source.is_dir():
                shutil.copytree(source, required, dirs_exist_ok=True)
            else:
                required.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, required)
    else:
        raise RuntimeError("Provide --required-model-path for every reviewed pipeline model path; copying an unbounded model directory is forbidden")
    if any(item.name == "pyvenv.cfg" for item in runtime_dir.rglob("pyvenv.cfg")):
        raise RuntimeError("Portable runtime must not contain pyvenv.cfg")
    model_manifest = args.output_dir / "models-manifest.json"
    write_model_manifest(model_manifest, models_dir, args.model_revision.lower())
    shutil.copyfile(model_manifest, models_dir / model_manifest.name)
    runtime_python = runtime_dir / "python.exe"
    subprocess.run([
        str(runtime_python),
        "-c",
        "from importlib.metadata import version; assert version('mineru') == '3.4.4'; import pypdf; import mineru.cli.fast_api",
    ], check=True)
    sbom = args.output_dir / "mineru-sbom.json"
    write_sbom(sbom, args.python, args.model_revision.lower(), site_packages)
    runtime_archive = args.output_dir / "mineru-runtime-win-x64-py312.zip"
    models_archive = args.output_dir / "mineru-models-pipeline.zip"
    deterministic_zip(runtime_dir, runtime_archive)
    deterministic_zip(models_dir, models_archive)
    print(runtime_archive)
    print(models_archive)
    print(sbom)
    print(model_manifest)
    print(f"runtime_sha256={sha256(runtime_archive)}")
    print(f"models_sha256={sha256(models_archive)}")
    if not args.keep_build_dir:
        shutil.rmtree(build_root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
