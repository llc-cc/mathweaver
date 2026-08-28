$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Invoke-Step($Name, [scriptblock]$Block) {
    Write-Host ""
    Write-Host "==> $Name"
    & $Block
}

$npm = "npm"
$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

Invoke-Step "Install frontend packaging dependencies" {
    & $npm install
}

Invoke-Step "Verify production OCR release manifest" {
    & $python scripts\verify_ocr_manifest.py backend\assets\ocr\manifest.json
    if ($LASTEXITCODE -ne 0) {
        throw "Production OCR manifest verification failed"
    }
}

Invoke-Step "Build React static frontend" {
    $env:DESKTOP_SPA = "1"
    $env:VITE_API_ORIGIN = "__SAME_ORIGIN__"
    & $npm run build
    Remove-Item Env:\DESKTOP_SPA -ErrorAction SilentlyContinue
    Remove-Item Env:\VITE_API_ORIGIN -ErrorAction SilentlyContinue
}

Invoke-Step "Install Python packaging dependencies" {
    & $python -m pip install -r backend\requirements.txt
}

Invoke-Step "Build backend.exe with PyInstaller" {
    & $python -m PyInstaller --clean --noconfirm scripts\pyinstaller\backend.spec --distpath dist\backend --workpath build\pyinstaller-work
}

Invoke-Step "Verify backend bundle static-resource allowlist and required modules" {
    & $python scripts\verify_backend_bundle.py dist\backend\backend.exe
    if ($LASTEXITCODE -ne 0) {
        throw "Backend bundle verification failed"
    }
}

Invoke-Step "Build MathGraph.exe with electron-builder" {
    & $npm run dist:win
}

Write-Host ""
Write-Host "Done. Portable app directory: release\win-unpacked"
Write-Host "Installer output: release\MathGraph-*.exe"
