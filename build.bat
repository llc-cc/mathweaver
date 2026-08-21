@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "EXIT_CODE="
cd /d "%~dp0" || goto :failed

set "PYTHON=%CD%\.venv\Scripts\python.exe"
set "WORK_DIR=build\pyinstaller-work-%RANDOM%%RANDOM%%RANDOM%"
set "PACKAGE_OUTPUT=release"
set "LOGO_FILE=public\mathweaver-icon.png"
set "WINDOWS_ICON_FILE=public\mathweaver-icon.ico"
set "FRONTEND_LOGO=build\client\mathweaver-icon.png"

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

echo.
echo Closing project-owned MathWeaver and backend processes...
"%PYTHON%" scripts\stop_desktop_processes.py
if errorlevel 1 goto :failed

echo.
echo [1/6] Ensuring the PDF and multipart runtimes are installed...
"%PYTHON%" -c "import python_multipart" >nul 2>&1
if errorlevel 1 (
    "%PYTHON%" -m pip install "python-multipart>=0.0.9"
    if errorlevel 1 goto :failed
)
"%PYTHON%" -c "from importlib.metadata import version; v=tuple(int(part) for part in version('python-multipart').split('.')[:3]); raise SystemExit(0 if v >= (0, 0, 9) else 1)" >nul 2>&1
if errorlevel 1 (
    "%PYTHON%" -m pip install "python-multipart>=0.0.9"
    if errorlevel 1 goto :failed
)
"%PYTHON%" -c "import pypdf; v=tuple(int(part) for part in pypdf.__version__.split('.')[:2]); raise SystemExit(0 if v >= (5, 6) else 1)" >nul 2>&1
if errorlevel 1 (
    "%PYTHON%" -m pip install "pypdf>=5.6.0"
    if errorlevel 1 goto :failed
)

echo.
echo [2/6] Verifying the production OCR release manifest...
"%PYTHON%" scripts\verify_ocr_manifest.py backend\assets\ocr\manifest.json
if errorlevel 1 goto :failed

echo.
echo [3/6] Building the desktop frontend...
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
echo [4/6] Building backend.exe from the static-resource allowlist...
"%PYTHON%" -m PyInstaller --clean --noconfirm scripts\pyinstaller\backend.spec --distpath dist\backend --workpath "%WORK_DIR%"
if errorlevel 1 goto :failed

echo.
echo [5/6] Verifying that backend.exe contains only allowed static resources...
"%PYTHON%" scripts\verify_backend_bundle.py dist\backend\backend.exe
if errorlevel 1 goto :failed

echo.
echo [6/6] Packaging MathWeaver.exe...
echo Rechecking project-owned processes before packaging...
"%PYTHON%" scripts\stop_desktop_processes.py
if errorlevel 1 goto :failed
timeout /T 2 /NOBREAK >nul
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
pause
exit /b 0

:failed
if not defined EXIT_CODE set "EXIT_CODE=%ERRORLEVEL%"
if exist "%WORK_DIR%" rmdir /s /q "%WORK_DIR%"
echo.
echo Packaging failed with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%

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
    if %%I LSS 10 timeout /T 1 /NOBREAK >nul
)

echo.
echo Packaging stopped: release\win-unpacked\resources\app.asar is still locked.
echo No rebuild directory was created. Close MathWeaver and Codex/ChatGPT completely, then run build.bat again.
exit /b 1
