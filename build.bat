@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "EXIT_CODE="
cd /d "%~dp0" || goto :failed
set "PYTHONDONTWRITEBYTECODE=1"

set "PYTHON=%CD%\.venv\Scripts\python.exe"
set "WORK_DIR=build\pyinstaller-work-%RANDOM%%RANDOM%%RANDOM%"
set "PACKAGE_OUTPUT=release"
set "LOGO_FILE=public\mathweaver-icon.png"
set "WINDOWS_ICON_FILE=public\mathweaver-icon.ico"
set "FRONTEND_LOGO=build\client\mathweaver-icon.png"
set "BACKEND_REQUIREMENTS=backend\requirements.txt"
set "STORAGE_ENV_SOURCE=backend\storage.env"
set "LOCAL_DB_ENV=.env.db.local"
set "LOCAL_DB_COMPOSE=compose.db.local.yml"
set "DOCKER_DESKTOP_EXE=C:\Program Files\Docker\Docker\Docker Desktop.exe"
set "DOCKER_CLI="
set "BACKEND_SMOKE_LOG=build\backend-smoke.log"

if not defined APPDATA (
    echo APPDATA is not defined; the desktop storage configuration cannot be provisioned.
    set "EXIT_CODE=1"
    goto :failed
)
set "DESKTOP_DATA_DIR=%APPDATA%\optui\backend-data"
set "DESKTOP_STORAGE_ENV=%DESKTOP_DATA_DIR%\storage.env"

if not exist "%PYTHON%" (
    echo Missing virtual environment: %PYTHON%
    echo Create it and install the project dependencies before packaging.
    set "EXIT_CODE=1"
    goto :failed
)

if not exist "node_modules\.bin\react-router.cmd" (
    echo Missing frontend dependencies. Run npm install once before packaging.
    set "EXIT_CODE=1"
    goto :failed
)

if not exist "%BACKEND_REQUIREMENTS%" (
    echo Missing backend requirements: %BACKEND_REQUIREMENTS%
    set "EXIT_CODE=1"
    goto :failed
)

if not exist "%STORAGE_ENV_SOURCE%" (
    echo Missing local desktop database configuration: %STORAGE_ENV_SOURCE%
    echo Create it from backend\storage.env.example before packaging.
    set "EXIT_CODE=1"
    goto :failed
)

if not exist "%LOGO_FILE%" (
    echo Missing MathWeaver logo: %LOGO_FILE%
    set "EXIT_CODE=1"
    goto :failed
)

if not exist "%WINDOWS_ICON_FILE%" (
    echo Missing Windows icon: %WINDOWS_ICON_FILE%
    set "EXIT_CODE=1"
    goto :failed
)

if not exist "scripts\smoke_test_desktop_backend.py" (
    echo Missing frozen backend smoke test: scripts\smoke_test_desktop_backend.py
    set "EXIT_CODE=1"
    goto :failed
)

echo.
echo Closing project-owned MathWeaver and backend processes...
"%PYTHON%" scripts\stop_desktop_processes.py
if errorlevel 1 goto :failed

echo.
echo [1/9] Installing the complete backend packaging requirements...
"%PYTHON%" -m pip install -r "%BACKEND_REQUIREMENTS%"
if errorlevel 1 goto :failed
"%PYTHON%" -c "import alembic, cryptography, dotenv, neo4j, pymysql, sqlalchemy; import sqlalchemy.dialects.mysql.pymysql" >nul 2>&1
if errorlevel 1 (
    echo Required MySQL and Neo4j storage modules are missing from %PYTHON%.
    set "EXIT_CODE=1"
    goto :failed
)

echo.
echo [2/9] Provisioning and validating the external desktop database configuration...
for %%K in (DATABASE_URL NEO4J_URI NEO4J_USER NEO4J_PASSWORD_FILE MATHWEAVER_DATA_KEY_FILE) do (
    findstr /b /c:"%%K=" "%STORAGE_ENV_SOURCE%" >nul
    if errorlevel 1 (
        echo Missing %%K in %STORAGE_ENV_SOURCE%.
        set "EXIT_CODE=1"
        goto :failed
    )
)
if not exist "%DESKTOP_DATA_DIR%" mkdir "%DESKTOP_DATA_DIR%"
if errorlevel 1 goto :failed
copy /y "%STORAGE_ENV_SOURCE%" "%DESKTOP_STORAGE_ENV%" >nul
if errorlevel 1 goto :failed
if not exist "%DESKTOP_STORAGE_ENV%" (
    echo Desktop database configuration was not provisioned: %DESKTOP_STORAGE_ENV%
    set "EXIT_CODE=1"
    goto :failed
)
"%PYTHON%" -c "from dotenv import dotenv_values; from pathlib import Path; c=dotenv_values(r'%DESKTOP_STORAGE_ENV%'); required=('DATABASE_URL','NEO4J_URI','NEO4J_USER','NEO4J_PASSWORD_FILE','MATHWEAVER_DATA_KEY_FILE'); missing=[k for k in required if not str(c.get(k) or '').strip()]; files=('NEO4J_PASSWORD_FILE','MATHWEAVER_DATA_KEY_FILE'); absent=[k for k in files if not Path(str(c.get(k) or '')).is_file()]; raise SystemExit(('Missing storage values: '+', '.join(missing)) if missing else ('Missing storage secret files: '+', '.join(absent)) if absent else 0)"
if errorlevel 1 goto :failed

echo.
echo [3/9] Preparing database services and applying schema migrations...
findstr /r /c:"^DATABASE_URL=.*@127[.]0[.]0[.]1:" /c:"^DATABASE_URL=.*@localhost:" /c:"^NEO4J_URI=.*127[.]0[.]0[.]1:" /c:"^NEO4J_URI=.*localhost:" "%STORAGE_ENV_SOURCE%" >nul
if not errorlevel 1 (
    call :ensure-local-database-stack
    if errorlevel 1 goto :failed
)
"%PYTHON%" -c "from dotenv import load_dotenv; load_dotenv(r'%DESKTOP_STORAGE_ENV%', override=True); from backend.scripts.upgrade_database import main; raise SystemExit(main([]))"
if errorlevel 1 goto :failed
"%PYTHON%" -c "from dotenv import load_dotenv; load_dotenv(r'%DESKTOP_STORAGE_ENV%', override=True); from backend.storage.database import database_health; from backend.integrations.neo4j_handler import Neo4jHandler; db=database_health(); graph=Neo4jHandler.from_environment(); gh=graph.health(); graph.close(); print('MySQL storage health:', 'ok' if db.get('ok') else db); print('Neo4j graph health:', 'ok' if gh.get('ok') else gh); raise SystemExit(0 if db.get('ok') and gh.get('ok') else 1)"
if errorlevel 1 (
    echo Database services are not healthy; packaging was stopped before producing a broken EXE.
    set "EXIT_CODE=1"
    goto :failed
)

echo.
echo [4/9] Verifying the production OCR release manifest...
"%PYTHON%" scripts\verify_ocr_manifest.py backend\assets\ocr\manifest.json
if errorlevel 1 goto :failed

echo.
echo [5/9] Building the desktop frontend...
call npm.cmd run build:desktop
if errorlevel 1 goto :failed

echo Ensuring the current MathWeaver logo is embedded in the frontend...
if not exist "build\client" mkdir "build\client"
copy /y "%LOGO_FILE%" "%FRONTEND_LOGO%" >nul
if errorlevel 1 goto :failed
if exist "build\client\favicon.ico" del /f /q "build\client\favicon.ico"
if not exist "%FRONTEND_LOGO%" (
    echo Frontend logo was not produced: %FRONTEND_LOGO%
    set "EXIT_CODE=1"
    goto :failed
)

echo.
echo [6/9] Building backend.exe from the static-resource allowlist...
"%PYTHON%" -m PyInstaller --clean --noconfirm scripts\pyinstaller\backend.spec --distpath dist\backend --workpath "%WORK_DIR%"
if errorlevel 1 goto :failed

echo.
echo [7/9] Verifying the backend bundle and required database runtimes...
"%PYTHON%" scripts\verify_backend_bundle.py dist\backend\backend.exe
if errorlevel 1 goto :failed

echo.
echo [8/9] Starting the frozen backend for an end-to-end health check...
"%PYTHON%" scripts\smoke_test_desktop_backend.py dist\backend\backend.exe "%DESKTOP_DATA_DIR%" --log "%BACKEND_SMOKE_LOG%"
if errorlevel 1 (
    echo Frozen backend smoke test failed. Review %BACKEND_SMOKE_LOG%.
    set "EXIT_CODE=1"
    goto :failed
)

echo.
echo [9/9] Packaging MathWeaver.exe...
echo Rechecking project-owned processes before packaging...
"%PYTHON%" scripts\stop_desktop_processes.py
if errorlevel 1 goto :failed
call :sleep 2
call :require-release-unlocked
if errorlevel 1 goto :failed

echo Removing the previous unpacked output so no stale logo can survive...
if exist "%PACKAGE_OUTPUT%\win-unpacked" rmdir /s /q "%PACKAGE_OUTPUT%\win-unpacked"
if exist "%PACKAGE_OUTPUT%\win-unpacked" (
    echo Could not remove the previous unpacked output.
    set "EXIT_CODE=1"
    goto :failed
)

call npm.cmd run dist:win -- --config.directories.output="%PACKAGE_OUTPUT%" --config.win.icon="%WINDOWS_ICON_FILE%"
if errorlevel 1 goto :failed

if not exist "%PACKAGE_OUTPUT%\win-unpacked\resources\mathweaver-icon.png" (
    echo Packaged logo is missing: %PACKAGE_OUTPUT%\win-unpacked\resources\mathweaver-icon.png
    set "EXIT_CODE=1"
    goto :failed
)
"%PYTHON%" -c "from pathlib import Path; import hashlib; a=Path(r'%LOGO_FILE%'); b=Path(r'%PACKAGE_OUTPUT%\win-unpacked\resources\mathweaver-icon.png'); raise SystemExit(0 if hashlib.sha256(a.read_bytes()).digest()==hashlib.sha256(b.read_bytes()).digest() else 'Packaged logo does not match public/mathweaver-icon.png')"
if errorlevel 1 goto :failed

:packaged
if exist "%WORK_DIR%" rmdir /s /q "%WORK_DIR%"
echo.
echo Packaging completed: %PACKAGE_OUTPUT%\win-unpacked\MathWeaver.exe
echo Desktop database configuration: %DESKTOP_STORAGE_ENV%
echo Database services were healthy and the frozen backend passed its startup smoke test.
if not defined MATHWEAVER_BUILD_NO_PAUSE pause
exit /b 0

:failed
if not defined EXIT_CODE set "EXIT_CODE=%ERRORLEVEL%"
if exist "%WORK_DIR%" rmdir /s /q "%WORK_DIR%"
echo.
echo Packaging failed with exit code %EXIT_CODE%.
if not defined MATHWEAVER_BUILD_NO_PAUSE pause
exit /b %EXIT_CODE%

:sleep
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Sleep -Seconds %~1" >nul
exit /b %ERRORLEVEL%

:ensure-local-database-stack
if not exist "%LOCAL_DB_ENV%" (
    echo Local database endpoints are configured, but %LOCAL_DB_ENV% is missing.
    exit /b 1
)
if not exist "%LOCAL_DB_COMPOSE%" (
    echo Local database endpoints are configured, but %LOCAL_DB_COMPOSE% is missing.
    exit /b 1
)

"%PYTHON%" -c "from dotenv import dotenv_values; from pathlib import Path; from urllib.parse import unquote, urlsplit; s=dotenv_values(r'%STORAGE_ENV_SOURCE%'); l=dotenv_values(r'%LOCAL_DB_ENV%'); db=urlsplit(str(s.get('DATABASE_URL') or '')); db_local=(db.hostname or '').lower() in ('127.0.0.1','localhost'); neo=urlsplit(str(s.get('NEO4J_URI') or '')); neo_local=(neo.hostname or '').lower() in ('127.0.0.1','localhost'); mysql_ok=(not db_local) or unquote(db.password or '')==str(l.get('MYSQL_PASSWORD') or ''); np=Path(str(s.get('NEO4J_PASSWORD_FILE') or '')); neo_ok=(not neo_local) or (np.is_file() and np.read_text(encoding='utf-8-sig').strip()==str(l.get('NEO4J_PASSWORD') or '')); raise SystemExit(0 if mysql_ok and neo_ok else 'backend/storage.env credentials do not match .env.db.local')"
if errorlevel 1 exit /b 1

where docker.exe >nul 2>&1
if not errorlevel 1 set "DOCKER_CLI=docker.exe"
if not defined DOCKER_CLI if exist "C:\Program Files\Docker\Docker\resources\bin\docker.exe" set "DOCKER_CLI=C:\Program Files\Docker\Docker\resources\bin\docker.exe"
if not defined DOCKER_CLI (
    echo Docker CLI was not found. Install Docker Desktop or configure remote database endpoints.
    exit /b 1
)

"%DOCKER_CLI%" version >nul 2>&1
if not errorlevel 1 goto :docker-ready
if not exist "%DOCKER_DESKTOP_EXE%" (
    echo Docker Desktop is installed incompletely or its engine is not running.
    exit /b 1
)

echo Docker engine is not running. Starting Docker Desktop...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%DOCKER_DESKTOP_EXE%' -WindowStyle Hidden"
if errorlevel 1 exit /b 1
for /l %%I in (1,1,90) do (
    "%DOCKER_CLI%" version >nul 2>&1
    if not errorlevel 1 goto :docker-ready
    call :sleep 2
)
echo Docker Desktop did not become ready within 180 seconds.
exit /b 1

:docker-ready
echo Starting the existing MathWeaver MySQL and Neo4j containers...
"%DOCKER_CLI%" compose --env-file "%LOCAL_DB_ENV%" -f "%LOCAL_DB_COMPOSE%" up -d --wait
if errorlevel 1 (
    echo Local MySQL or Neo4j startup failed.
    exit /b 1
)
exit /b 0

:require-release-unlocked
set "APP_ASAR_DIR=release\win-unpacked\resources"
if not exist "%APP_ASAR_DIR%\app.asar" exit /b 0

for /l %%I in (1,1,10) do (
    pushd "%APP_ASAR_DIR%" || exit /b 1
    ren "app.asar" "app.asar.__mathweaver_lock_probe__" >nul 2>&1
    if not errorlevel 1 (
        ren "app.asar.__mathweaver_lock_probe__" "app.asar" >nul 2>&1
        if errorlevel 1 (
            popd
            echo Could not restore the app.asar lock probe.
            exit /b 1
        )
        popd
        exit /b 0
    )
    popd
    if %%I LSS 10 call :sleep 1
)

echo.
echo Packaging stopped: release\win-unpacked\resources\app.asar is still locked.
echo No rebuild directory was created. Close MathWeaver and Codex/ChatGPT completely, then run build.bat again.
exit /b 1
