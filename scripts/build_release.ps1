param(
    [string]$OutputDirectory = "dist/releases",
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$GitSha = (git -C $RepositoryRoot rev-parse --short=12 HEAD).Trim()
if (-not $GitSha) { throw "Unable to resolve release Git SHA" }

# git archive 只打包受版本控制文件；额外门禁防止密钥或运行产物被误提交后进入发布包。
$ExcludedPatterns = @(".git", ".env", "*.db", "*.log", "__pycache__", ".pytest_cache")
$ForbiddenTrackedPattern = '(^|/)(\.env[^/]*|[^/]*\.db|[^/]*\.log|__pycache__|\.pytest_cache)(/|$)'
$ForbiddenTracked = @(
    git -C $RepositoryRoot ls-files |
        Where-Object { $_ -match $ForbiddenTrackedPattern -and $_ -notmatch '(^|/)\.env[^/]*\.example$' }
)
if ($ForbiddenTracked.Count -gt 0) {
    throw "Tracked runtime or secret-like files must be removed before release"
}

$ResolvedOutput = Join-Path $RepositoryRoot $OutputDirectory
$Archive = Join-Path $ResolvedOutput "mathweaver-teaching-$GitSha.tar.gz"
if ($WhatIf) {
    Write-Output "WHATIF git archive HEAD -> $Archive"
    Write-Output "WHATIF exclusions verified: .git, .env, *.db, *.log, __pycache__, .pytest_cache"
    exit 0
}

New-Item -ItemType Directory -Force -Path $ResolvedOutput | Out-Null
git -C $RepositoryRoot archive --format=tar.gz --output=$Archive HEAD
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Archive)) {
    throw "Release archive creation failed"
}
Get-FileHash -Algorithm SHA256 -LiteralPath $Archive | Select-Object Path, Hash
