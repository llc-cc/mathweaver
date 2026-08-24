param(
    [Parameter(Mandatory = $true)]
    [int]$ExpectedHistoryRows
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $env:MATHWEAVER_DATABASE_URL) {
    throw "MATHWEAVER_DATABASE_URL is required"
}

& python backend/scripts/verify_restored_data.py `
    --expected-history-rows $ExpectedHistoryRows
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
