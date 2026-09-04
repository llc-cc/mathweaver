"""教学旁路发布资产的端口、回滚和密钥安全约束。"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_UNIT = ROOT / "deploy/systemd/mathweaver-teaching-backend.service"
AUTH_BACKEND_UNIT = ROOT / "deploy/systemd/mathweaver-teaching-auth-backend.service"
AI_BACKEND_UNIT = ROOT / "deploy/systemd/mathweaver-teaching-ai-backend.service"
PIPELINE_BACKEND_UNIT = ROOT / "deploy/systemd/mathweaver-teaching-pipeline-backend.service"
FRONTEND_UNIT = ROOT / "deploy/systemd/mathweaver-teaching-frontend.service"
BACKUP_UNIT = ROOT / "deploy/systemd/mathweaver-teaching-backup.service"
BACKUP_TIMER = ROOT / "deploy/systemd/mathweaver-teaching-backup.timer"
NGINX = ROOT / "deploy/nginx/mathweaver-teaching-18080.conf"
NGINX_ROUTING = ROOT / "deploy/nginx/mathweaver-routing.conf"
NGINX_DOMAIN = ROOT / "deploy/nginx/mathweaver.cn.conf"
DEPLOY = ROOT / "scripts/deploy_teaching_release.sh"
SMOKE = ROOT / "scripts/smoke_teaching_release.sh"
BACKUP = ROOT / "scripts/backup_teaching_data.sh"
BUILD = ROOT / "scripts/build_release.ps1"
GIT_ATTRIBUTES = ROOT / ".gitattributes"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_units_bind_only_to_127_0_0_1_sidecar_ports() -> None:
    backend = _text(BACKEND_UNIT)
    auth_backend = _text(AUTH_BACKEND_UNIT)
    ai_backend = _text(AI_BACKEND_UNIT)
    pipeline_backend = _text(PIPELINE_BACKEND_UNIT)
    frontend = _text(FRONTEND_UNIT)

    assert "127.0.0.1:5002" in backend
    assert "127.0.0.1:5005" in auth_backend
    assert "127.0.0.1:5003" in ai_backend
    assert "127.0.0.1:5004" in pipeline_backend
    assert "127.0.0.1" in frontend and "5174" in frontend
    all_backends = backend + auth_backend + ai_backend + pipeline_backend
    assert "0.0.0.0" not in all_backends + frontend
    assert all_backends.count("/opt/mathweaver/.env.teaching") == 4
    assert all_backends.count("User=nginx") == 4
    assert all_backends.count("Group=nginx") == 4
    assert "User=nginx" in frontend and "Group=nginx" in frontend


def test_expensive_ai_and_pipeline_workloads_are_process_isolated() -> None:
    backend = _text(BACKEND_UNIT)
    auth_backend = _text(AUTH_BACKEND_UNIT)
    ai_backend = _text(AI_BACKEND_UNIT)
    pipeline_backend = _text(PIPELINE_BACKEND_UNIT)

    assert "--workers 4 --threads 4" in backend
    assert "--workers 2 --threads 4" in auth_backend
    assert "--backlog 1024" in auth_backend
    assert "MemoryMax=1500M" in auth_backend
    assert "--workers 2 --threads 3" in ai_backend
    assert "--workers 1 --threads 4" in pipeline_backend
    assert "MemoryMax=3G" in ai_backend
    assert "MemoryMax=4G" in pipeline_backend
    assert "--max-requests" not in pipeline_backend


def test_nginx_uses_loopback_18080_and_proxies_api_to_5002() -> None:
    nginx = _text(NGINX)

    assert re.search(r"listen\s+127\.0\.0\.1:18080\s*;", nginx)
    assert "location /api/" in nginx
    assert "proxy_pass http://127.0.0.1:$mathweaver_backend_port" in nginx
    assert "proxy_pass http://127.0.0.1:5174" in nginx


def test_nginx_routes_expensive_work_to_isolated_process_pools() -> None:
    routing = _text(NGINX_ROUTING)
    deploy = _text(DEPLOY)

    assert "default 5002;" in routing
    assert '"POST:/api/v2/auth/login" 5005;' in routing
    assert '"POST:/api/v2/auth/register" 5005;' in routing
    assert '"POST:/api/v2/proof-assist" 5003;' in routing
    assert '"~^(GET|POST|DELETE):/api/v2/jobs(?:/|$)" 5004;' in routing
    assert "zone=mathweaver_ai_pool_connections:64k" in routing
    assert "zone=mathweaver_auth_pool_connections:64k" in routing
    assert 'install_nginx_configs' in deploy
    assert 'check_legacy_listener_scope' in deploy


def test_capacity_profile_handles_expected_150_user_login_wave() -> None:
    limits = _text(NGINX)
    domain = _text(NGINX_DOMAIN)
    backend = _text(BACKEND_UNIT)

    assert "rate=30r/s" in limits
    assert "rate=80r/s" in limits
    assert "burst=650 delay=30" in domain
    assert "burst=1000 delay=120" in domain
    assert "mathweaver_api_connections 900" in domain
    assert "mathweaver_auth_pool_connections 32" in domain
    assert "--workers 4 --threads 4 --backlog 2048" in backend
    assert "location ^~ /assets/" in domain
    assert "max-age=31536000, immutable" in domain


def test_deploy_script_never_overwrites_3000_or_5001_services() -> None:
    deploy = _text(DEPLOY)

    assert "mathweaver-teaching-backend.service" in deploy
    assert "mathweaver-teaching-auth-backend.service" in deploy
    assert "mathweaver-teaching-ai-backend.service" in deploy
    assert "mathweaver-teaching-pipeline-backend.service" in deploy
    assert "mathweaver-teaching-frontend.service" in deploy
    assert "mathweaver-neo4j.service" in deploy
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
    assert 'setfacl -m u:nginx:--x "$RELEASE_DIR"' in deploy
    assert "/opt/mathweaver/current-teaching/.runtime/node" in frontend
    assert "node_modules/@react-router/serve/bin.js" in frontend
    assert "/usr/bin/npm" not in frontend


def test_service_start_uses_a_bounded_readiness_wait() -> None:
    deploy = _text(DEPLOY)

    assert "wait_for_url()" in deploy
    assert 'while [ "$attempt" -lt 30 ]' in deploy
    assert "sleep 1" in deploy
    assert 'wait_for_url "http://127.0.0.1:5002/health/ready"' in deploy
    assert 'wait_for_url "http://127.0.0.1:5003/health/ready"' in deploy
    assert 'wait_for_url "http://127.0.0.1:5004/health/ready"' in deploy
    assert 'wait_for_url "http://127.0.0.1:5005/health/ready"' in deploy
    assert 'wait_for_url "http://127.0.0.1:5174/"' in deploy


def test_start_restarts_active_sidecars_after_switching_the_release_link() -> None:
    """服务已运行时也必须加载新版本，不能只做 enable --now。"""
    deploy = _text(DEPLOY)
    start_body = re.search(
        r"^start_release\(\) \{(?P<body>.*?)^\}$",
        deploy,
        flags=re.MULTILINE | re.DOTALL,
    )

    assert start_body is not None
    assert 'systemctl enable "$BACKEND_UNIT" "$AUTH_BACKEND_UNIT" "$AI_BACKEND_UNIT" "$PIPELINE_BACKEND_UNIT" "$FRONTEND_UNIT"' in start_body["body"]
    assert 'systemctl restart "$BACKEND_UNIT" "$AUTH_BACKEND_UNIT" "$AI_BACKEND_UNIT" "$PIPELINE_BACKEND_UNIT" "$FRONTEND_UNIT"' in start_body["body"]
    assert 'systemctl enable --now "$NEO4J_UNIT"' in start_body["body"]
    assert 'systemctl enable --now "$BACKUP_TIMER"' in start_body["body"]


def test_release_uses_version_directory_and_atomic_symlink() -> None:
    deploy = _text(DEPLOY)

    assert "/opt/mathweaver/releases" in deploy
    assert "/opt/mathweaver/current-teaching" in deploy
    assert "ln -sfn" in deploy
    assert "mv -Tf" in deploy
    assert "previous-teaching" in deploy
    assert "rollback" in deploy


def test_backup_covers_all_persistent_stores_and_is_scheduled() -> None:
    backup = _text(BACKUP)
    unit = _text(BACKUP_UNIT)
    timer = _text(BACKUP_TIMER)

    assert "--single-transaction" in backup
    assert "export_graph_backup.py" in backup
    assert "data-teaching.tar.gz" in backup
    assert "sha256sum -c SHA256SUMS" in backup
    assert "/opt/mathweaver/current-teaching/scripts/backup_teaching_data.sh" in unit
    assert "OnCalendar=" in timer and "Persistent=true" in timer


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
        _text(path)
        for path in (
            BACKEND_UNIT,
            AUTH_BACKEND_UNIT,
            AI_BACKEND_UNIT,
            PIPELINE_BACKEND_UNIT,
            FRONTEND_UNIT,
            BACKUP_UNIT,
            BACKUP_TIMER,
            NGINX,
            NGINX_ROUTING,
            NGINX_DOMAIN,
            DEPLOY,
            SMOKE,
            BACKUP,
            BUILD,
        )
    )

    assert not re.search(r"(?i)(password|api[_-]?key)\s*=\s*['\"][^$'\"]+", content)
    assert "MATHWEAVER_DATABASE_URL=mysql" not in content
    assert not re.search(r"Bearer\s+[A-Za-z0-9._-]{20,}", content)
