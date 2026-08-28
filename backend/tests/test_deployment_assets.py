"""教学旁路发布资产的端口、回滚和密钥安全约束。"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_UNIT = ROOT / "deploy/systemd/mathweaver-teaching-backend.service"
FRONTEND_UNIT = ROOT / "deploy/systemd/mathweaver-teaching-frontend.service"
NGINX = ROOT / "deploy/nginx/mathweaver-teaching-18080.conf"
DEPLOY = ROOT / "scripts/deploy_teaching_release.sh"
SMOKE = ROOT / "scripts/smoke_teaching_release.sh"
BUILD = ROOT / "scripts/build_release.ps1"
GIT_ATTRIBUTES = ROOT / ".gitattributes"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_units_bind_only_to_127_0_0_1_sidecar_ports() -> None:
    backend = _text(BACKEND_UNIT)
    frontend = _text(FRONTEND_UNIT)

    assert "127.0.0.1:5002" in backend
    assert "127.0.0.1" in frontend and "5174" in frontend
    assert "0.0.0.0" not in backend + frontend
    assert "/opt/mathweaver/.env.teaching" in backend
    assert "User=nginx" in backend and "Group=nginx" in backend
    assert "User=nginx" in frontend and "Group=nginx" in frontend


def test_nginx_uses_18080_and_proxies_api_to_5002() -> None:
    nginx = _text(NGINX)

    assert re.search(r"listen\s+18080\s*;", nginx)
    assert "location /api/" in nginx
    assert "proxy_pass http://127.0.0.1:5002" in nginx
    assert "proxy_pass http://127.0.0.1:5174" in nginx


def test_deploy_script_never_overwrites_3000_or_5001_services() -> None:
    deploy = _text(DEPLOY)

    assert "mathweaver-teaching-backend.service" in deploy
    assert "mathweaver-teaching-frontend.service" in deploy
    assert "systemctl restart mathweaver.service" not in deploy
    assert "systemctl stop mathweaver.service" not in deploy
    assert "proxy_pass http://127.0.0.1:5001" not in deploy
    assert ":3000" not in deploy


def test_deploy_uses_python_311_instead_of_the_legacy_python3_alias() -> None:
    deploy = _text(DEPLOY)

    assert "PYTHON_BIN=python3.11" in deploy
    assert '"$PYTHON_BIN" -m venv' in deploy
    assert "python3 -m venv" not in deploy


def test_web_server_install_skips_the_desktop_electron_binary() -> None:
    deploy = _text(DEPLOY)

    assert 'ELECTRON_SKIP_BINARY_DOWNLOAD=1 npm --prefix "$RELEASE_DIR" ci' in deploy


def test_sidecars_use_a_release_node_runtime_and_minimal_root_traverse_acl() -> None:
    deploy = _text(DEPLOY)
    frontend = _text(FRONTEND_UNIT)

    assert 'install -D -m 0755 "$node_binary" "$RELEASE_DIR/.runtime/node"' in deploy
    assert 'setfacl -m u:nginx:--x "$ROOT"' in deploy
    assert "/opt/mathweaver/current-teaching/.runtime/node" in frontend
    assert "node_modules/@react-router/serve/bin.js" in frontend
    assert "/usr/bin/npm" not in frontend


def test_service_start_uses_a_bounded_readiness_wait() -> None:
    deploy = _text(DEPLOY)

    assert "wait_for_url()" in deploy
    assert 'while [ "$attempt" -lt 30 ]' in deploy
    assert "sleep 1" in deploy
    assert 'wait_for_url "http://127.0.0.1:5002/api/v2/ready"' in deploy
    assert 'wait_for_url "http://127.0.0.1:5174/"' in deploy


def test_release_uses_version_directory_and_atomic_symlink() -> None:
    deploy = _text(DEPLOY)

    assert "/opt/mathweaver/releases" in deploy
    assert "/opt/mathweaver/current-teaching" in deploy
    assert "ln -sfn" in deploy
    assert "mv -Tf" in deploy
    assert "previous-teaching" in deploy
    assert "rollback" in deploy


def test_release_builder_excludes_runtime_and_secret_files() -> None:
    build = _text(BUILD)

    for excluded in (".git", ".env", "*.db", "*.log", "__pycache__", ".pytest_cache"):
        assert excluded in build
    assert "WhatIf" in build


def test_linux_deployment_assets_are_forced_to_lf_in_release_archives() -> None:
    attributes = _text(GIT_ATTRIBUTES)

    assert "*.sh text eol=lf" in attributes
    assert "*.service text eol=lf" in attributes
    assert "deploy/nginx/*.conf text eol=lf" in attributes


def test_no_secret_literal_exists_in_deploy_assets() -> None:
    content = "\n".join(
        _text(path) for path in (BACKEND_UNIT, FRONTEND_UNIT, NGINX, DEPLOY, SMOKE, BUILD)
    )

    assert not re.search(r"(?i)(password|api[_-]?key)\s*=\s*['\"][^$'\"]+", content)
    assert "MATHWEAVER_DATABASE_URL=mysql" not in content
    assert not re.search(r"Bearer\s+[A-Za-z0-9._-]{20,}", content)
