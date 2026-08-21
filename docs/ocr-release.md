# OCR component release

The desktop base installer deliberately contains only the OCR manifest and
license notices. Build the component separately on Windows x64 with CPython
3.12:

```powershell
.\scripts\release_ocr.ps1 `
  -ReleaseVersion 0.1.1 `
  -ComponentVersion 3.4.4-cpu-win-x64-r1 `
  -Python C:\Python312\python.exe `
  -PythonEmbed C:\downloads\python-3.12.9-embed-amd64.zip `
  -PythonEmbedSha256 <official-cpython-zip-sha256> `
  -Wheelhouse C:\MathWeaverWheelhouse `
  -Lock C:\MathWeaverWheelhouse\mineru-win-x64.lock.txt `
  -ModelRoot C:\MathWeaverModels\mineru-pipeline `
  -ModelRevision <locked-40-character-commit-sha> `
  -CalibrationPdf C:\MathWeaverModels\calibration.pdf `
  -MineruLicense C:\MathWeaverModels\LICENSE-MinerU.txt `
  -NoticeFile C:\MathWeaverModels\NOTICE.txt `
  -RequiredModelPath layout-models `
  -RequiredModelPath formula-models `
  -OutputRoot C:\MathWeaverOcrBuild\3.4.4-cpu-win-x64-r1
```

The script refuses to reuse a build directory, requires a full model commit
and official CPython archive hash, builds both streamed archives, splits and
hashes them, validates the production manifest, and only then copies it to
`backend/assets/ocr/manifest.json`. Use `-VerifyRemote` after uploading all
parts to the draft GitHub Release to verify the live asset sizes and digests.
The default `-PartLimit` is 64 MiB so uploads remain reliable on restricted
networks; it may be raised up to 1.5 GiB when the release transport supports
larger requests.
The checked-in `.github/workflows/ocr-release.yml` expects the same input paths
as `MATHWEAVER_OCR_*` machine environment variables on the dedicated
`mathweaver-ocr` Windows runner; it never falls back to the project venv.
`MATHWEAVER_OCR_MODEL_PATHS` is a semicolon-separated allowlist of the exact
pipeline model directories/files to copy; an unbounded model root is rejected.

The lower-level manifest command remains available for release debugging:

```powershell
python scripts/build_ocr_manifest.py `
  --version 3.4.4-cpu-win-x64-r1 `
  --release-version 0.1.1 `
  --model-revision <locked-40-character-commit-sha> `
  --base-url https://github.com/SJTU-AI4MATH/MathWeaver/releases/download/v0.1.1 `
  --runtime-archive release\ocr\mineru-runtime-win-x64-py312.zip `
  --models-archive release\ocr\mineru-models-pipeline.zip `
  --parts-dir release\ocr\parts `
  --output backend\assets\ocr\manifest.json `
  --dependency-lock C:\MathWeaverWheelhouse\mineru-win-x64.lock.txt `
  --sbom release\ocr\mineru-sbom.json `
  --models-manifest release\ocr\models-manifest.json `
  --calibration-pdf C:\MathWeaverModels\calibration.pdf `
  --license-file C:\MathWeaverModels\LICENSE-MinerU.txt `
  --notice-file C:\MathWeaverModels\NOTICE.txt `
  --part-limit 67108864
```

Publish all runtime/model parts to the `v0.1.1` release before copying the
generated manifest into the application. `verify_ocr_manifest.py` enforces
schema v2, Windows x64 CPython 3.12.9, MinerU 3.4.4 CPU, a pinned model
revision, non-empty runtime/models archives, HTTPS GitHub release URLs,
SHA-256 values, the model manifest and the 1.5 GiB per-part limit. The
placeholder manifest in the repository is intentionally rejected, so
`build.bat` cannot produce a candidate EXE until the real release assets have
been supplied. The installer resumes validated parts, verifies each archive,
self-tests the local API with the calibration PDF, and atomically updates
`%LOCALAPPDATA%\MathWeaver\ocr\current.json`.
