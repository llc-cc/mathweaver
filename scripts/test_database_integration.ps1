[CmdletBinding()]
param(
    [switch]$KeepContainers
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $projectRoot ".env.db.local"
$composeFile = Join-Path $projectRoot "compose.db.test.yml"
$python = Join-Path $projectRoot "backend\.venv\Scripts\python.exe"
$neo4jPasswordFile = Join-Path $projectRoot "deploy\secrets\neo4j_password"

if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Missing $envFile. Create local database credentials before running integration tests."
}
if (-not (Test-Path -LiteralPath $neo4jPasswordFile)) {
    throw "Missing $neo4jPasswordFile."
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing backend virtual environment Python: $python"
}

$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$docker = if ($dockerCommand) {
    $dockerCommand.Source
} else {
    "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
}
if (-not (Test-Path -LiteralPath $docker)) {
    throw "Docker CLI was not found."
}
$dockerBin = Split-Path -Parent $docker
if (($env:PATH -split ";") -notcontains $dockerBin) {
    $env:PATH = "$dockerBin;$env:PATH"
}
$localEnvironment = @{}
foreach ($line in Get-Content -LiteralPath $envFile) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
        continue
    }
    $name, $value = $trimmed -split "=", 2
    if ($name -and $null -ne $value) {
        $localEnvironment[$name.Trim()] = $value.Trim()
    }
}
foreach ($required in @("MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD", "NEO4J_PASSWORD")) {
    if (-not $localEnvironment.ContainsKey($required) -or -not $localEnvironment[$required]) {
        throw "Missing $required in $envFile."
    }
}

$encodedMysqlPassword = [Uri]::EscapeDataString($localEnvironment["MYSQL_PASSWORD"])
$composeArgs = @("compose", "--env-file", $envFile, "-f", $composeFile)

try {
    & $docker @composeArgs up -d --wait
    if ($LASTEXITCODE -ne 0) {
        throw "Docker test database startup failed with exit code $LASTEXITCODE."
    }

    $env:DATABASE_URL = "mysql+pymysql://mathweaver:${encodedMysqlPassword}@127.0.0.1:3307/mathweaver_test?charset=utf8mb4"
    $env:NEO4J_URI = "neo4j://127.0.0.1:17687"
    $env:NEO4J_USER = "neo4j"
    $env:NEO4J_PASSWORD_FILE = $neo4jPasswordFile

    & $python -m backend.scripts.upgrade_database
    if ($LASTEXITCODE -ne 0) {
        throw "Alembic upgrade failed with exit code $LASTEXITCODE."
    }

    $env:MATHWEAVER_INTEGRATION_TESTS = "1"
    $env:MATHWEAVER_TEST_DATABASE_URL = $env:DATABASE_URL
    $env:MATHWEAVER_TEST_NEO4J_URI = $env:NEO4J_URI
    $env:MATHWEAVER_TEST_NEO4J_USER = $env:NEO4J_USER
    $env:MATHWEAVER_TEST_NEO4J_PASSWORD_FILE = $neo4jPasswordFile

    & $python -m pytest -q -p no:cacheprovider `
        backend/scripts/test_education_api.py `
        backend/scripts/test_paused_history_resume.py `
        backend/scripts/test_agent_import.py
    if ($LASTEXITCODE -ne 0) {
        throw "Database integration tests failed with exit code $LASTEXITCODE."
    }
} finally {
    if (-not $KeepContainers) {
        & $docker @composeArgs down
    }
}
