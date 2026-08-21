"""Verify that every production OCR manifest asset exists in the pinned GitHub Release.

This is deliberately a release gate: a structurally valid manifest is not enough
if its referenced assets are missing, point at another repository, or have a
different size/digest on GitHub.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from ocr_manifest import ManifestValidationError, validate_manifest  # noqa: E402

CHUNK_SIZE = 1 << 20
REPO = "SJTU-AI4MATH/MathWeaver"


def _json_request(url: str, token: str | None) -> object:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "MathWeaver-release-gate"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        value = json.loads(response.read().decode("utf-8"))
    return value


def _release_for_tag(version: str, token: str | None) -> dict:
    """Read a release, including drafts (the tag endpoint hides drafts)."""

    try:
        value = _json_request(f"https://api.github.com/repos/{REPO}/releases/tags/v{version}", token)
        if isinstance(value, dict):
            return value
        raise RuntimeError(f"GitHub release response is not an object: v{version}")
    except (OSError, RuntimeError):
        releases = _json_request(f"https://api.github.com/repos/{REPO}/releases?per_page=100", token)
        for release in releases if isinstance(releases, list) else []:
            if isinstance(release, dict) and release.get("tag_name") == f"v{version}":
                return release
        raise RuntimeError(f"Pinned release v{version} is missing")


def _asset_url_matches(expected: str, actual: str, *, release: dict, name: str) -> bool:
    if actual == expected:
        return True
    # GitHub exposes draft assets under an untagged download path.  Keep the
    # production manifest pinned to /vX.Y.Z/; accept only the exact asset name
    # under the same repository while the release is explicitly draft.
    if not release.get("draft"):
        return False
    expected_parts = expected.rstrip("/").split("/")
    actual_parts = actual.rstrip("/").split("/")
    try:
        download_index = expected_parts.index("download")
    except ValueError:
        return False
    return (
        len(expected_parts) > download_index + 2
        and len(actual_parts) > download_index + 2
        and [part.lower() for part in actual_parts[: download_index + 1]]
        == [part.lower() for part in expected_parts[: download_index + 1]]
        and actual_parts[download_index + 1].startswith("untagged-")
        and actual_parts[-1] == name
        and "/releases/download/untagged-" in actual
    )


def _stream_sha256(url: str, token: str | None) -> str:
    headers = {"Accept": "application/octet-stream", "User-Agent": "MathWeaver-release-gate"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    digest = hashlib.sha256()
    with urlopen(Request(url, headers=headers), timeout=60) as response:
        while chunk := response.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--allow-network", action="store_true", help="Required explicitly because this gate contacts GitHub")
    parser.add_argument("--require-draft", action="store_true", help="Require the pinned release to remain a Draft")
    args = parser.parse_args()
    if not args.allow_network:
        raise SystemExit("Refusing remote verification without --allow-network")
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        validate_manifest(manifest, production=True)
        version = str(manifest["release_version"])
        release = _release_for_tag(version, args.token or None)
        if release.get("tag_name") != f"v{version}":
            raise RuntimeError("Pinned release tag is missing or does not match the manifest")
        if args.require_draft and not release.get("draft"):
            raise RuntimeError("Pinned release must remain a draft for this gate")
        assets = {str(item.get("name")): item for item in release.get("assets", []) if isinstance(item, dict)}
        for archive in manifest["archives"]:
            for part in archive["parts"]:
                name = str(part["name"])
                asset = assets.get(name)
                if not asset:
                    raise RuntimeError(f"Release asset is missing: {name}")
                if int(asset.get("size") or 0) != int(part["size"]):
                    raise RuntimeError(f"Release asset size mismatch: {name}")
                browser_url = str(asset.get("browser_download_url") or "")
                expected_url = str(part["url"])
                if not _asset_url_matches(expected_url, browser_url, release=release, name=name):
                    raise RuntimeError(f"Manifest URL does not match release asset URL: {name}")
                remote_digest = str(asset.get("digest") or "").removeprefix("sha256:").lower()
                if remote_digest and remote_digest != str(part["sha256"]).lower():
                    raise RuntimeError(f"Release asset digest mismatch: {name}")
                if not remote_digest and _stream_sha256(browser_url, args.token or None) != str(part["sha256"]).lower():
                    raise RuntimeError(f"Downloaded release asset digest mismatch: {name}")
        for auxiliary in manifest.get("auxiliary_assets", []):
            name = str(auxiliary["name"])
            asset = assets.get(name)
            if not asset:
                raise RuntimeError(f"Release auxiliary asset is missing: {name}")
            if int(asset.get("size") or 0) != int(auxiliary["size"]):
                raise RuntimeError(f"Release auxiliary asset size mismatch: {name}")
            browser_url = str(asset.get("browser_download_url") or "")
            if not _asset_url_matches(str(auxiliary["url"]), browser_url, release=release, name=name):
                raise RuntimeError(f"Manifest URL does not match release auxiliary asset URL: {name}")
            remote_digest = str(asset.get("digest") or "").removeprefix("sha256:").lower()
            if remote_digest and remote_digest != str(auxiliary["sha256"]).lower():
                raise RuntimeError(f"Release auxiliary asset digest mismatch: {name}")
            if not remote_digest and _stream_sha256(browser_url, args.token or None) != str(auxiliary["sha256"]).lower():
                raise RuntimeError(f"Downloaded release auxiliary asset digest mismatch: {name}")
    except (OSError, ValueError, json.JSONDecodeError, ManifestValidationError, RuntimeError) as exc:
        print(f"OCR release assets invalid: {exc}", file=sys.stderr)
        return 1
    print(f"OCR release assets valid: v{manifest['release_version']} ({sum(len(a['parts']) for a in manifest['archives'])} parts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
