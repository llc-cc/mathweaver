# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path.cwd()
BACKEND = ROOT / "backend"
FRONTEND_STATIC_DIR = ROOT / "build" / "client"

if not FRONTEND_STATIC_DIR.is_dir():
    raise FileNotFoundError(f"Frontend build not found: {FRONTEND_STATIC_DIR}")

STATIC_RESOURCES = [
    (FRONTEND_STATIC_DIR, "frontend"),
    (BACKEND / "assets" / "tex_templates" / "elegantbook.cls", "backend/assets/tex_templates"),
    (BACKEND / "assets" / "tex_templates" / "elegantbook" / "image" / "cert.pdf", "backend/assets/tex_templates/elegantbook/image"),
    (BACKEND / "assets" / "tex_templates" / "elegantbook" / "image" / "donate.jpg", "backend/assets/tex_templates/elegantbook/image"),
    (BACKEND / "assets" / "tex_templates" / "elegantbook" / "image" / "founder.png", "backend/assets/tex_templates/elegantbook/image"),
    (BACKEND / "assets" / "tex_templates" / "elegantbook" / "image" / "K33.png", "backend/assets/tex_templates/elegantbook/image"),
    (BACKEND / "assets" / "tex_templates" / "elegantbook" / "image" / "scatter.pdf", "backend/assets/tex_templates/elegantbook/image"),
    (BACKEND / "assets" / "tex_templates" / "elegantbook" / "image" / "tlshell.png", "backend/assets/tex_templates/elegantbook/image"),
    (BACKEND / "assets" / "tex_templates" / "elegantbook" / "image" / "winding number.png", "backend/assets/tex_templates/elegantbook/image"),
]

FORBIDDEN_RUNTIME_DATA = {
    (BACKEND / ".env").resolve(),
    (BACKEND / "auth.db").resolve(),
    (BACKEND / "books").resolve(),
    (BACKEND / "checkpoint").resolve(),
}

datas = []
for source, destination in STATIC_RESOURCES:
    source = source.resolve()
    if source in FORBIDDEN_RUNTIME_DATA:
        raise ValueError(f"Forbidden runtime data cannot be bundled: {source}")
    if not source.exists():
        raise FileNotFoundError(f"Required static resource not found: {source}")
    datas.append((str(source), destination))

hiddenimports = ["ocr_manifest"]
for package in ["pipeline", "JoinAgent", "tools", "integrations", "python_multipart", "pypdf"]:
    hiddenimports.extend(collect_submodules(package))

a = Analysis(
    [str(BACKEND / "desktop_app.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
