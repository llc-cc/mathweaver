param(
  [Parameter(Mandatory = $true)][string]$ReleaseVersion,
  [Parameter(Mandatory = $true)][string]$ComponentVersion,
  [Parameter(Mandatory = $true)][string]$Python,
  [Parameter(Mandatory = $true)][string]$PythonEmbed,
  [Parameter(Mandatory = $true)][string]$PythonEmbedSha256,
  [Parameter(Mandatory = $true)][string]$Wheelhouse,
  [Parameter(Mandatory = $true)][string]$Lock,
  [Parameter(Mandatory = $true)][string]$ModelRoot,
  [Parameter(Mandatory = $true)][string]$ModelRevision,
  [Parameter(Mandatory = $true)][string]$CalibrationPdf,
  [Parameter(Mandatory = $true)][string]$MineruLicense,
  [Parameter(Mandatory = $true)][string]$NoticeFile,
  [Parameter(Mandatory = $true)][string]$OutputRoot,
  [int]$PartLimit = 67108864,
  [string[]]$RequiredModelPath = @(),
  [switch]$VerifyRemote
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$output = New-Item -ItemType Directory -Force -Path $OutputRoot
$archiveOut = Join-Path $output.FullName "archives"
$partsOut = Join-Path $output.FullName "parts"
$manifest = Join-Path $output.FullName "manifest.json"
$baseUrl = "https://github.com/SJTU-AI4MATH/MathWeaver/releases/download/v$ReleaseVersion"

if (Test-Path (Join-Path $output.FullName "_build-runtime")) {
  throw "Refusing to reuse an OCR build directory. Choose a fresh OutputRoot."
}
New-Item -ItemType Directory -Force -Path $archiveOut, $partsOut | Out-Null

$componentArgs = @(
  (Join-Path $repoRoot "scripts/build_ocr_component.py"),
  "--python", $Python,
  "--python-embed", $PythonEmbed,
  "--python-embed-sha256", $PythonEmbedSha256,
  "--wheelhouse", $Wheelhouse,
  "--lock", $Lock,
  "--model-root", $ModelRoot,
  "--model-revision", $ModelRevision,
  "--calibration-pdf", $CalibrationPdf,
  "--mineru-license", $MineruLicense,
  "--notice-file", $NoticeFile,
  "--output-dir", $archiveOut
)
foreach ($path in $RequiredModelPath) { $componentArgs += @("--required-model-path", $path) }
& $Python @componentArgs
if ($LASTEXITCODE -ne 0) { throw "OCR component build failed" }

& $Python (Join-Path $repoRoot "scripts/build_ocr_manifest.py") `
  --version $ComponentVersion `
  --release-version $ReleaseVersion `
  --base-url $baseUrl `
  --runtime-archive (Join-Path $archiveOut "mineru-runtime-win-x64-py312.zip") `
  --models-archive (Join-Path $archiveOut "mineru-models-pipeline.zip") `
  --parts-dir $partsOut `
  --output $manifest `
  --model-revision $ModelRevision `
  --dependency-lock $Lock `
  --sbom (Join-Path $archiveOut "mineru-sbom.json") `
  --models-manifest (Join-Path $archiveOut "models-manifest.json") `
  --calibration-pdf $CalibrationPdf `
  --license-file $MineruLicense `
  --notice-file $NoticeFile `
  --part-limit $PartLimit
if ($LASTEXITCODE -ne 0) { throw "OCR manifest build failed" }

& $Python (Join-Path $repoRoot "scripts/verify_ocr_manifest.py") $manifest
if ($LASTEXITCODE -ne 0) { throw "Local OCR manifest gate failed" }
if ($VerifyRemote) {
  & $Python (Join-Path $repoRoot "scripts/verify_ocr_release_assets.py") $manifest --allow-network --require-draft
  if ($LASTEXITCODE -ne 0) { throw "Remote OCR release asset gate failed" }
}

if ($VerifyRemote) {
  $publishedManifest = Join-Path $repoRoot "backend/assets/ocr/manifest.json"
  Copy-Item -LiteralPath $manifest -Destination $publishedManifest -Force
  Write-Host "Validated OCR manifest copied to $publishedManifest"
} else {
  Write-Host "Local manifest validated. It is not copied into the application until remote asset verification passes."
}
