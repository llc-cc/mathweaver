from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_production_environment_file_is_git_ignored_but_example_is_tracked():
    rules = {
        line.strip()
        for line in (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert ".env.*" in rules
    assert "!.env.example" in rules


def test_docker_contexts_exclude_environment_variants_but_keep_examples():
    root_rules = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
    backend_rules = (PROJECT_ROOT / "backend" / ".dockerignore").read_text(
        encoding="utf-8"
    )

    assert ".env.*" in root_rules
    assert "!.env.example" in root_rules
    assert "backend/.env.*" in root_rules
    assert "!backend/.env.example" in root_rules
    assert ".env.*" in backend_rules
    assert "!.env.example" in backend_rules


def test_backend_image_contains_required_tex_runtime_and_single_process_command():
    dockerfile = (PROJECT_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")

    for requirement in (
        "latexmk",
        "texlive-xetex",
        "texlive-binaries",
        "texlive-lang-chinese",
        "fonts-noto-cjk",
        "command -v synctex",
    ):
        assert requirement in dockerfile
    assert '"--workers", "1"' in dockerfile
    assert '"--threads", "8"' in dockerfile


def test_compose_mounts_runtime_env_as_backend_only_secret():
    compose = (PROJECT_ROOT / "deploy" / "docker-compose.web.yml").read_text(
        encoding="utf-8"
    )

    assert "env_file:" not in compose
    assert "mathweaver_backend_env:" in compose
    assert "file: ../.env.production" in compose
    assert "target: mathweaver_backend.env" in compose
    assert "MATHWEAVER_DATABASE_NAME: mathweaver" in compose
    for secret_name in (
        "MATHWEAVER_DATABASE_URL",
        "PDFPIPELINE_API_KEY",
        "PDFPIPELINE_EMBEDDING_API_KEY",
    ):
        assert f"${{{secret_name}" not in compose
    frontend = compose.split("  frontend:", 1)[1].split("  proxy:", 1)[0]
    assert "secrets:" not in frontend
    assert '"1"' in compose
    assert '"8"' in compose


def test_backend_image_uses_non_logging_runtime_environment_entrypoint():
    dockerfile = (PROJECT_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (
        PROJECT_ROOT / "backend" / "scripts" / "docker_entrypoint.py"
    ).read_text(encoding="utf-8")

    assert 'ENTRYPOINT ["python", "scripts/docker_entrypoint.py"]' in dockerfile
    assert "dotenv_values" in entrypoint
    assert "interpolate=False" in entrypoint
    assert "os.execvp" in entrypoint
    assert "print(value" not in entrypoint
    assert "repr(value" not in entrypoint
    assert "COPY --chown=mathweaver:mathweaver . ." not in dockerfile
    assert "chown -R root:root /app" in dockerfile
    assert "chmod -R go-w /app" in dockerfile
    assert "chmod 0555 /app/scripts/docker_entrypoint.py" in dockerfile
    assert "chown mathweaver:mathweaver /var/lib/mathweaver" in dockerfile


def test_migration_documentation_uses_host_identity_for_both_sqlite_runs():
    deployment = (PROJECT_ROOT / "docs" / "WEB_DEPLOYMENT.md").read_text(
        encoding="utf-8"
    )

    assert "--env-file .env.production" not in deployment
    assert "chmod 600 /srv/mathweaver/migration/auth.db" in deployment
    sqlite_commands = [
        block
        for block in deployment.split("docker compose")
        if "scripts/migrate_sqlite_to_mysql.py" in block
    ]
    assert len(sqlite_commands) == 2
    assert all('--user "$(id -u):$(id -g)"' in block for block in sqlite_commands)


def test_nginx_disables_buffering_for_frontend_ssr_streaming():
    nginx = (PROJECT_ROOT / "deploy" / "nginx.mathweaver.conf").read_text(
        encoding="utf-8"
    )
    frontend_location = nginx.split("location / {", 1)[1]

    assert "proxy_buffering off;" in frontend_location

