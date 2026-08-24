"""MathGraph API v2 — staged Markdown/text pipeline and local OCR pre-task."""

import io
import ipaddress
import json
import multiprocessing
import os
import queue as queue_module
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess

# Clear proxy env vars so httpx/OpenAI client makes direct connections.
# The system ALL_PROXY (e.g. Clash/V2Ray socks5 proxy) would otherwise
# route all LLM API calls through the local proxy and break httpx[socks].
for _proxy_var in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy",
                   "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(_proxy_var, None)
import sys
import tempfile
import threading
import time
import traceback
import uuid
import zipfile
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, g, jsonify, request, send_file
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash

_BACKEND = Path(__file__).parent
sys.path.insert(0, str(_BACKEND))
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from pipeline.context import PipelineContext
from pipeline.common.io import atomic_write_json
from pipeline.orchestrator import (
    FIXED_STAGE_DEFS,
    build_fixed_stage_plan,
    execute_fixed_pipeline,
)
from pipeline.stages.build_relations.stage import get_embedding
from tex_macros import extract_latex_macros, merge_latex_macros
from JoinAgent import SimpleLLM
from ocr_runtime import (
    CHUNK_SIZE,
    IMAGE_MAX_BYTES,
    PDF_MAX_BYTES,
    OcrError,
    get_ocr_manager,
)
from services.auth_service import (
    AuthenticatedUser,
    AuthService,
    AuthorizationError,
    InvalidCredentialsError,
    PasswordChangeError,
    PasswordPolicyError,
    SelfDisableError,
    UserNotFoundError,
)
from services.admin_user_service import AdminUserService
from storage.auth_repository import AuthRepository
from storage.credential_crypto import CredentialCipher, CredentialKeyring
from storage.capacity import CapacityExceeded, CapacityLimits
from storage.database import configure_database, database_is_ready
from storage.learning_repository import (
    JobSnapshot,
    LearningRepository,
    sanitize_source_pdf_meta,
)
from storage.redaction import redact_structure, redact_text
from storage.object_storage import (
    ObjectStorageConfig,
    ObjectStorageError,
    OssObjectStorage,
)


_object_storage_config = ObjectStorageConfig.from_environment()
_object_storage = (
    OssObjectStorage(_object_storage_config) if _object_storage_config is not None else None
)

def _configure_cors(flask_app: Flask) -> None:
    """按部署白名单启用 Web CORS，并保留旧桌面端的本地兼容行为。"""
    allowed_origins = [
        origin.strip()
        for origin in os.environ.get("MATHWEAVER_ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    ]
    if allowed_origins:
        CORS(flask_app, origins=allowed_origins, supports_credentials=False)
        return

    # 正式网页由 Nginx 同源访问，空白名单必须保持禁用；仅旧 Electron 可宽松跨域。
    legacy_desktop = (
        os.environ.get("AI4MATH_DESKTOP") == "1"
        and not os.environ.get("MATHWEAVER_DATABASE_URL", "").strip()
    )
    if legacy_desktop:
        CORS(flask_app, supports_credentials=False)


app = Flask(__name__)
_capacity_limits = CapacityLimits.from_environment()
app.config["MAX_CONTENT_LENGTH"] = _capacity_limits.max_upload_bytes
_configure_cors(app)


@app.errorhandler(CapacityExceeded)
def _capacity_error_response(exc: CapacityExceeded):
    return jsonify({"error": exc.code}), exc.http_status

# ── SQLite auth / history ────────────────────────────────────────────────────

_DATA_ROOT = Path(os.environ.get("MATHGRAPH_DATA_DIR", str(Path(__file__).parent))).expanduser()
_DATA_ROOT.mkdir(parents=True, exist_ok=True)
_DB_PATH = _DATA_ROOT / "auth.db"
_SOURCE_PDF_ROOT = _DATA_ROOT / "uploads" / "source_pdfs"
_PACKAGED_BACKEND_ROOT = Path(getattr(sys, "_MEIPASS", "")) / "backend"
_TEX_TEMPLATE_ROOT = (
    _PACKAGED_BACKEND_ROOT if _PACKAGED_BACKEND_ROOT.is_dir() else Path(__file__).parent
) / "assets" / "tex_templates"


def _stored_source_pdf_meta(meta: dict | None) -> dict | None:
    return sanitize_source_pdf_meta(meta)


def _get_db():
    if "db" not in g:
        g.db = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def _close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _init_db():
    with sqlite3.connect(str(_DB_PATH)) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT    UNIQUE NOT NULL,
                password_hash TEXT    NOT NULL,
                created_at    TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT    PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                created_at TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS history (
                id          TEXT    PRIMARY KEY,
                user_id     INTEGER NOT NULL,
                filename    TEXT    NOT NULL,
                node_count  INTEGER NOT NULL DEFAULT 0,
                edge_count  INTEGER NOT NULL DEFAULT 0,
                nodes_json  TEXT    NOT NULL,
                edges_json  TEXT    NOT NULL,
                source_markdown TEXT,
                latex_macros TEXT,
                source_pdf_json TEXT,
                status      TEXT    NOT NULL DEFAULT 'done',
                stage       TEXT,
                stage_label TEXT,
                stage_index INTEGER NOT NULL DEFAULT 0,
                total_stages INTEGER NOT NULL DEFAULT 0,
                stages_done_json TEXT NOT NULL DEFAULT '[]',
                source_format TEXT NOT NULL DEFAULT 'markdown',
                experimental_logic_ir INTEGER NOT NULL DEFAULT 0,
                updated_at  TEXT,
                created_at  TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id       INTEGER PRIMARY KEY,
                llm_api_url   TEXT NOT NULL DEFAULT '',
                llm_model     TEXT NOT NULL DEFAULT '',
                llm_api_key   TEXT NOT NULL DEFAULT '',
                updated_at    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS proof_workspaces (
                user_id          INTEGER NOT NULL,
                graph_id         TEXT    NOT NULL,
                node_id          INTEGER NOT NULL,
                user_proof       TEXT    NOT NULL DEFAULT '',
                versions_json    TEXT    NOT NULL DEFAULT '[]',
                ai_messages_json TEXT    NOT NULL DEFAULT '[]',
                imports_json     TEXT    NOT NULL DEFAULT '[]',
                updated_at       TEXT    NOT NULL,
                PRIMARY KEY (user_id, graph_id, node_id)
            );
        """)
        # Migrate: add llm_configs_json column if not present
        try:
            conn.execute("ALTER TABLE user_settings ADD COLUMN llm_configs_json TEXT NOT NULL DEFAULT ''")
            conn.commit()
        except Exception:
            pass  # column already exists
        try:
            conn.execute("ALTER TABLE history ADD COLUMN source_markdown TEXT")
            conn.commit()
        except Exception:
            pass  # column already exists
        try:
            conn.execute("ALTER TABLE history ADD COLUMN latex_macros TEXT")
            conn.commit()
        except Exception:
            pass  # column already exists
        try:
            conn.execute("ALTER TABLE history ADD COLUMN source_pdf_json TEXT")
            conn.commit()
        except Exception:
            pass  # column already exists
        history_migrations = (
            "ALTER TABLE history ADD COLUMN status TEXT NOT NULL DEFAULT 'done'",
            "ALTER TABLE history ADD COLUMN stage TEXT",
            "ALTER TABLE history ADD COLUMN stage_label TEXT",
            "ALTER TABLE history ADD COLUMN stage_index INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE history ADD COLUMN total_stages INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE history ADD COLUMN stages_done_json TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE history ADD COLUMN source_format TEXT NOT NULL DEFAULT 'markdown'",
            "ALTER TABLE history ADD COLUMN experimental_logic_ir INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE history ADD COLUMN updated_at TEXT",
        )
        for migration in history_migrations:
            try:
                conn.execute(migration)
                conn.commit()
            except Exception:
                pass  # column already exists
        conn.execute(
            "UPDATE history SET status = 'paused' WHERE status = 'running'"
        )
        conn.execute(
            "UPDATE history SET updated_at = created_at WHERE updated_at IS NULL"
        )
        for row_id, raw_meta in conn.execute(
            "SELECT id, source_pdf_json FROM history WHERE source_pdf_json IS NOT NULL"
        ).fetchall():
            try:
                stored_meta = _stored_source_pdf_meta(json.loads(raw_meta))
            except (TypeError, json.JSONDecodeError):
                stored_meta = None
            conn.execute(
                "UPDATE history SET source_pdf_json = ? WHERE id = ?",
                (
                    json.dumps(stored_meta, ensure_ascii=False) if stored_meta else None,
                    row_id,
                ),
            )
        conn.commit()
        try:
            conn.execute("ALTER TABLE proof_workspaces ADD COLUMN imports_json TEXT NOT NULL DEFAULT '[]'")
            conn.commit()
        except Exception:
            pass  # column already exists


_desktop_legacy_auth = (
    os.getenv("AI4MATH_DESKTOP") == "1"
    and not os.getenv("MATHWEAVER_DATABASE_URL")
)
if _desktop_legacy_auth:
    # 桌面版暂时保留旧 SQLite 认证；Web 模式绝不能隐式回退到本地文件。
    _auth_service = None
    _admin_user_service = None
    _learning_repository = None
else:
    configure_database()
    _credential_cipher = CredentialCipher(CredentialKeyring.from_environment())
    _auth_service = AuthService(AuthRepository())
    _admin_user_service = AdminUserService(AuthRepository())
    _learning_repository = LearningRepository(cipher=_credential_cipher)

if _desktop_legacy_auth:
    _init_db()


def _current_user():
    """Return user row if request carries a valid Bearer token, else None."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    if _auth_service is not None:
        user = _auth_service.authenticate(token)
        # 旧历史/设置路由仍按映射读取用户字段，迁移前保持该内部契约。
        return asdict(user) if user is not None else None
    db = _get_db()
    row = db.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?",
        (token,),
    ).fetchone()
    return row


def require_role(*allowed_roles: str):
    """要求有效 Bearer 会话及指定角色，并保留认证结果供路由复用。"""

    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = _current_user()
            if not user:
                return jsonify({"error": "not authenticated"}), 401
            if isinstance(user, dict):
                role = user.get("role")
            else:
                role = user["role"] if "role" in user.keys() else None
            if role not in allowed_roles:
                return jsonify({"error": "forbidden"}), 403
            # 同一请求只使用已经验证过的身份，避免路由层重复解析请求或自行查库。
            g.authenticated_user = user
            return view(*args, **kwargs)

        return wrapped

    return decorate


def _authenticated_actor() -> AuthenticatedUser:
    user = g.authenticated_user
    return AuthenticatedUser(
        id=user["id"],
        student_no=user["student_no"],
        email=user["email"],
        display_name=user["display_name"],
        role=user["role"],
        initial_password_pending=user["initial_password_pending"],
    )


def _public_source_pdf_meta(meta: dict | None) -> dict | None:
    stored = _stored_source_pdf_meta(meta)
    if not stored:
        return None
    return {
        key: stored[key]
        for key in ("status", "available", "error", "pdf_url", "compile_log_url")
    }


def _pending_source_pdf_meta(job_id: str) -> dict:
    return {
        "status": "compiling",
        "available": False,
        "error": None,
        "pdf_url": f"/api/v2/source-pdf/{job_id}",
        "compile_log_url": f"/api/v2/source-pdf/{job_id}/compile-log",
    }


def _update_history_source_pdf(job_id: str, meta: dict) -> None:
    if not _desktop_legacy_auth:
        job = _jobs.get(job_id)
        owner_id = job.get("_user_id") if job else None
        if owner_id is not None:
            # 后台编译只能使用任务内部已绑定的归属，绝不接受客户端提供的用户 ID。
            _learning_repository.update_source_pdf(int(owner_id), job_id, meta)
        return
    try:
        with sqlite3.connect(str(_DB_PATH)) as conn:
            conn.execute(
                "UPDATE history SET source_pdf_json = ? WHERE id = ?",
                (
                    json.dumps(_stored_source_pdf_meta(meta), ensure_ascii=False),
                    job_id,
                ),
            )
            conn.commit()
    except Exception:
        pass


def _source_pdf_dir(job_id: str) -> Path:
    return _SOURCE_PDF_ROOT / re.sub(r"[^A-Za-z0-9_.-]", "_", job_id)


def _read_source_pdf_meta(row_or_job) -> dict | None:
    if isinstance(row_or_job, dict):
        meta = row_or_job.get("source_pdf")
        if not isinstance(meta, dict):
            return None
        # 活任务内部可携带受控绝对路径；持久化字典仅携带 basename，需要在受控根下重建。
        if any(meta.get(key) for key in ("pdf_path", "source_path", "log_path")):
            return dict(meta)
        row_id = row_or_job.get("id") or row_or_job.get("job_id")
        if not row_id:
            return dict(meta)
        result = dict(meta)
        source_dir = _source_pdf_dir(str(row_id))
        for name_key, path_key in (
            ("pdf_name", "pdf_path"),
            ("source_name", "source_path"),
            ("log_name", "log_path"),
        ):
            name = Path(str(meta.get(name_key) or "")).name
            if name:
                result[path_key] = str(source_dir / name)
        return result
    raw = row_or_job["source_pdf_json"] if row_or_job and "source_pdf_json" in row_or_job.keys() else None
    if not raw:
        return None
    try:
        meta = json.loads(raw)
    except Exception:
        return None
    meta = _stored_source_pdf_meta(meta)
    if not meta:
        return None
    row_id = row_or_job["id"] if "id" in row_or_job.keys() else None
    if row_id:
        source_dir = _source_pdf_dir(str(row_id))
        for name_key, path_key in (
            ("pdf_name", "pdf_path"),
            ("source_name", "source_path"),
            ("log_name", "log_path"),
        ):
            name = Path(str(meta.get(name_key) or "")).name
            if name:
                meta[path_key] = str(source_dir / name)
    return meta


def _json_list(value):
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _complete_llm_config(config):
    if not isinstance(config, dict):
        return None
    api_url = (config.get("api_url") or "").strip()
    model_name = (config.get("model_name") or "").strip()
    api_key = (config.get("api_key") or "").strip()
    if api_url and model_name and api_key:
        # 运行时配置必须保留独立 Embedding 凭据；公开设置接口仍只返回掩码。
        return {
            "api_url": api_url,
            "model_name": model_name,
            "api_key": api_key,
            "embedding_url": (config.get("embedding_url") or "").strip(),
            "embedding_model": (config.get("embedding_model") or "").strip(),
            "embedding_api_key": (config.get("embedding_api_key") or "").strip(),
        }
    return None


def _active_user_llm_config(user):
    if not user:
        return None
    if not _desktop_legacy_auth:
        data = _learning_repository.get_runtime_settings(int(user["id"]))
        configs = data.get("configs") or []
        active_index = data.get("active_index", 0)
        active = configs[active_index] if configs and 0 <= active_index < len(configs) else (configs[0] if configs else {})
        return _complete_llm_config(active)
    db = _get_db()
    row = db.execute(
        "SELECT llm_api_url, llm_model, llm_api_key, llm_configs_json FROM user_settings WHERE user_id = ?",
        (user["id"],),
    ).fetchone()
    if not row:
        return None
    if row["llm_configs_json"]:
        try:
            data = json.loads(row["llm_configs_json"])
            configs = data.get("configs") or []
            active_index = int(data.get("active_index") or 0)
            if configs and 0 <= active_index < len(configs):
                active = configs[active_index]
            elif configs:
                active = configs[0]
            else:
                active = {}
            config = _complete_llm_config({
                "api_url": active.get("api_url"),
                "model_name": active.get("model_name"),
                "api_key": active.get("api_key"),
            })
            if config:
                return config
        except Exception:
            pass
    return _complete_llm_config({
        "api_url": row["llm_api_url"],
        "model_name": row["llm_model"],
        "api_key": row["llm_api_key"],
    })


# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.route("/api/v2/auth/register", methods=["POST"])
def auth_register():
    if _auth_service is not None:
        return jsonify({"error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    if not email or not password:
        return jsonify({"error": "email and password required"}), 400
    if len(password) < 6:
        return jsonify({"error": "password must be at least 6 characters"}), 400
    db = _get_db()
    try:
        db.execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
            (email, generate_password_hash(password), datetime.utcnow().isoformat()),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "email already registered"}), 409
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    token = secrets.token_hex(32)
    db.execute(
        "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
        (token, user["id"], datetime.utcnow().isoformat()),
    )
    db.commit()
    return jsonify({"token": token, "email": user["email"]}), 201


@app.route("/api/v2/auth/login", methods=["POST"])
def auth_login():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "JSON object required"}), 400
    if _auth_service is not None:
        identifier = body.get("identifier")
        password = body.get("password")
        if not isinstance(identifier, str) or not isinstance(password, str):
            return jsonify(
                {"error": "identifier and password must be strings"}
            ), 400
        try:
            result = _auth_service.login(identifier, password)
        except InvalidCredentialsError:
            return jsonify({"error": "学号、邮箱或密码错误"}), 401
        return jsonify({"token": result.token, "user": asdict(result.user)})

    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    db = _get_db()
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "invalid email or password"}), 401
    token = secrets.token_hex(32)
    db.execute(
        "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
        (token, user["id"], datetime.utcnow().isoformat()),
    )
    db.commit()
    return jsonify({"token": token, "email": user["email"]})


@app.route("/api/v2/auth/logout", methods=["POST"])
def auth_logout():
    auth = request.headers.get("Authorization", "")
    if _auth_service is not None:
        if auth.startswith("Bearer "):
            _auth_service.logout(auth[7:])
        return jsonify({"ok": True})
    if auth.startswith("Bearer "):
        db = _get_db()
        db.execute("DELETE FROM sessions WHERE token = ?", (auth[7:],))
        db.commit()
    return jsonify({"ok": True})


@app.route("/api/v2/auth/me")
def auth_me():
    user = _current_user()
    if not user:
        return jsonify({"error": "not authenticated"}), 401
    if _auth_service is not None:
        return jsonify({"user": user})
    return jsonify({"email": user["email"], "id": user["id"]})


@app.route("/api/v2/auth/change-password", methods=["POST"])
@require_role("student", "teacher", "admin")
def auth_change_password():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "JSON object required"}), 400
    current_password = body.get("current_password")
    new_password = body.get("new_password")
    if not isinstance(current_password, str) or not isinstance(new_password, str):
        return jsonify(
            {"error": "current_password and new_password must be strings"}
        ), 400
    auth = request.headers.get("Authorization", "")
    try:
        result = _auth_service.change_password(
            user_id=g.authenticated_user["id"],
            current_password=current_password,
            new_password=new_password,
            current_token=auth[7:],
        )
    except PasswordPolicyError:
        return jsonify({"error": "密码长度必须为 8 至 128 位"}), 400
    except PasswordChangeError:
        return jsonify({"error": "密码修改失败"}), 400
    return jsonify({"token": result.token, "user": asdict(result.user)})


@app.route(
    "/api/v2/admin/users/<int:user_id>/reset-password", methods=["POST"]
)
@require_role("admin")
def admin_reset_password(user_id: int):
    try:
        temporary_password = _auth_service.reset_password(
            _authenticated_actor(), user_id
        )
    except AuthorizationError:
        return jsonify({"error": "forbidden"}), 403
    except UserNotFoundError:
        return jsonify({"error": "user not found"}), 404
    return jsonify({"temporary_password": temporary_password})


@app.route("/api/v2/admin/users/<int:user_id>/status", methods=["PATCH"])
@require_role("admin")
def admin_update_user_status(user_id: int):
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("is_active"), bool):
        return jsonify({"error": "is_active must be a boolean"}), 400
    try:
        user = _auth_service.set_user_active(
            _authenticated_actor(), user_id, body["is_active"]
        )
    except AuthorizationError:
        return jsonify({"error": "forbidden"}), 403
    except UserNotFoundError:
        return jsonify({"error": "user not found"}), 404
    except SelfDisableError:
        return jsonify({"error": "cannot disable current administrator"}), 400
    return jsonify({"user": asdict(user)})


@app.route("/api/v2/admin/users/import", methods=["POST"])
@require_role("admin")
def admin_import_users():
    uploaded = request.files.get("file")
    if uploaded is None:
        return jsonify(
            {
                "created": 0,
                "generated_credentials": [],
                "errors": [
                    {"line": 0, "field": "file", "message": "file is required"}
                ],
            }
        ), 400
    if not (uploaded.filename or "").lower().endswith(".csv"):
        return jsonify(
            {
                "created": 0,
                "generated_credentials": [],
                "errors": [
                    {"line": 0, "field": "file", "message": "CSV file required"}
                ],
            }
        ), 400
    try:
        result = _admin_user_service.import_students(
            uploaded.stream, _authenticated_actor()
        )
    except AuthorizationError:
        return jsonify({"error": "forbidden"}), 403
    payload = asdict(result)
    return jsonify(payload), 400 if result.errors else 200


# ── User settings endpoints ───────────────────────────────────────────────────

@app.route("/api/v2/settings", methods=["GET"])
def settings_get():
    user = _current_user()
    if not user:
        return jsonify({"error": "not authenticated"}), 401
    if not _desktop_legacy_auth:
        return jsonify(_learning_repository.get_public_settings(int(user["id"])))
    db = _get_db()
    row = db.execute(
        "SELECT llm_api_url, llm_model, llm_api_key, llm_configs_json FROM user_settings WHERE user_id = ?",
        (user["id"],),
    ).fetchone()
    if not row:
        return jsonify({"configs": [], "active_index": 0})
    # If new multi-config format exists, use it
    if row["llm_configs_json"]:
        try:
            data = json.loads(row["llm_configs_json"])
            return jsonify(data)
        except Exception:
            pass
    # Migrate legacy single config
    if row["llm_api_url"]:
        legacy = [{"name": "默认配置", "api_url": row["llm_api_url"],
                   "model_name": row["llm_model"], "api_key": row["llm_api_key"]}]
        return jsonify({"configs": legacy, "active_index": 0})
    return jsonify({"configs": [], "active_index": 0})


@app.route("/api/v2/settings", methods=["PUT"])
def settings_put():
    user = _current_user()
    if not user:
        return jsonify({"error": "not authenticated"}), 401
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "JSON object required"}), 400
    configs = body.get("configs")
    active_index = body.get("active_index", 0)
    if not isinstance(configs, list):
        return jsonify({"error": "configs must be a list"}), 400
    if any(not isinstance(config, dict) for config in configs):
        return jsonify({"error": "each config must be a JSON object"}), 400
    if not isinstance(active_index, int) or isinstance(active_index, bool):
        return jsonify({"error": "active_index must be an integer"}), 400
    if configs and not 0 <= active_index < len(configs):
        return jsonify({"error": "active_index is out of range"}), 400
    if not configs and active_index != 0:
        return jsonify({"error": "active_index must be zero when configs is empty"}), 400
    if not _desktop_legacy_auth:
        saved = _learning_repository.upsert_settings(
            int(user["id"]), configs, active_index
        )
        return jsonify(saved)
    data_json = json.dumps({"configs": configs, "active_index": active_index}, ensure_ascii=False)
    # Also keep legacy columns from active config for backward compat
    active = configs[active_index] if configs and 0 <= active_index < len(configs) else {}
    db = _get_db()
    db.execute(
        """INSERT INTO user_settings (user_id, llm_api_url, llm_model, llm_api_key, llm_configs_json, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
               llm_api_url      = excluded.llm_api_url,
               llm_model        = excluded.llm_model,
               llm_api_key      = excluded.llm_api_key,
               llm_configs_json = excluded.llm_configs_json,
               updated_at       = excluded.updated_at""",
        (user["id"], active.get("api_url",""), active.get("model_name",""),
         active.get("api_key",""), data_json, datetime.utcnow().isoformat()),
    )
    db.commit()
    return jsonify({"ok": True})


# ── LLM / Embedding config validation ────────────────────────────────────────

_CONFIG_VALIDATION_MESSAGES = {
    "ok": "连接成功",
    "invalid_url": "API URL 无效，请填写服务商提供的 HTTP(S) API 地址。",
    "unauthorized": "API Key 无效、已过期或没有访问权限。",
    "model_not_found": "未找到该模型，请复制服务商提供的精确模型 ID。",
    "endpoint_not_found": "API 地址不存在，请检查 Base URL 是否正确。",
    "rate_limited": "请求受到限流或账户余额不足，请稍后重试并检查服务商账户。",
    "timeout": "连接超时，请检查网络或服务商状态后重试。",
    "unreachable": "无法连接到服务商，请检查 API URL 和网络。",
    "incompatible_response": "服务返回格式与 OpenAI 兼容协议不一致。",
    "provider_error": "服务商返回错误，请检查配置或稍后重试。",
}


def _config_validation_result(ok, code, latency_ms=0):
    return {
        "ok": bool(ok),
        "code": code,
        "message": _CONFIG_VALIDATION_MESSAGES[code],
        "latency_ms": max(0, int(latency_ms)),
    }


def _provider_url_error(raw_url):
    value = str(raw_url or "").strip()
    try:
        parsed = urlparse(value)
    except ValueError:
        return "invalid_url"
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return "invalid_url"

    if os.getenv("AI4MATH_DESKTOP") == "1":
        return None

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = {
            info[4][0]
            for info in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError):
        return "unreachable"
    if not addresses:
        return "unreachable"
    try:
        if any(not ipaddress.ip_address(address).is_global for address in addresses):
            return "invalid_url"
    except ValueError:
        return "invalid_url"
    return None


def _provider_failure_code(exc):
    response = getattr(exc, "response", None)
    status = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
    message = str(exc).lower()
    if status in {401, 403}:
        return "unauthorized"
    if status == 404:
        return "model_not_found" if "model" in message else "endpoint_not_found"
    if status == 429:
        return "rate_limited"
    if status in {408, 504}:
        return "timeout"
    if (
        "model" in message
        and any(marker in message for marker in ("not found", "does not exist", "invalid model"))
    ):
        return "model_not_found"
    if any(marker in message for marker in ("timed out", "timeout")):
        return "timeout"
    if any(
        marker in message
        for marker in (
            "connection refused",
            "connection error",
            "failed to establish",
            "name resolution",
            "no connection adapters",
        )
    ):
        return "unreachable"
    if any(
        marker in message
        for marker in ("json", "choices", "embedding", "response format", "decode")
    ):
        return "incompatible_response"
    return "provider_error"


_JOB_ERROR_MESSAGES = {
    "api_config": (
        "API 配置不可用",
        "请检查 API 地址、API Key 和模型名，或打开 API 配置指南重新测试。",
    ),
    "service_limit": (
        "服务额度或请求频率受限",
        "请稍后重试，并检查服务商账户余额与限流状态。",
    ),
    "network": (
        "无法连接模型服务",
        "请检查网络和 API 地址后重试。",
    ),
    "model_response": (
        "模型返回内容无法解析",
        "模型未按预期格式返回结果，建议重试；持续失败时请更换兼容模型。",
    ),
    "document_input": (
        "文档内容无法处理",
        "请确认文件非空、格式正确且内容可读取后重新上传。",
    ),
    "pipeline_stage": (
        "处理阶段未能完成",
        "该阶段没有生成后续处理所需结果。可先重试；持续失败时请查看完整错误信息。",
    ),
    "internal": (
        "处理过程中出现异常",
        "请重试；若仍然失败，请查看完整错误信息并反馈。",
    ),
}


def _classify_job_error(exc, stage=None, stage_label=None):
    message = str(exc or "")
    lowered = message.lower()
    response = getattr(exc, "response", None)
    status = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
    provider_code = _provider_failure_code(exc)

    if provider_code in {"unauthorized", "model_not_found", "endpoint_not_found"} or any(
        marker in lowered
        for marker in (
            "api key not found",
            "invalid api key",
            "incorrect api key",
            "invalid_url",
            "invalid url",
            "incomplete llm config",
            "incomplete embedding config",
        )
    ):
        code = "api_config"
    elif provider_code == "rate_limited" or any(
        marker in lowered
        for marker in (
            "rate limit",
            "ratequota",
            "quota exceeded",
            "insufficient quota",
            "insufficient balance",
            "余额不足",
            "限流",
        )
    ):
        code = "service_limit"
    elif provider_code in {"timeout", "unreachable"} or (
        isinstance(status, int) and status >= 500
    ):
        code = "network"
    elif provider_code == "incompatible_response" or any(
        marker in lowered
        for marker in (
            "failed to parse",
            "parse error",
            "parsing failed",
            "invalid model output",
            "invalid response format",
            "candidate must parse",
            "no valid task results",
            "unresolved llm task",
        )
    ):
        code = "model_response"
    elif any(
        marker in lowered
        for marker in (
            "no content provided",
            "empty document",
            "empty source",
            "unsupported file",
            "unsupported source",
            "source file is unavailable",
            "source file cannot",
            "unable to read source",
            "invalid tex source",
        )
    ):
        code = "document_input"
    elif any(
        marker in lowered
        for marker in (
            "did not produce required downstream state",
            "required downstream state",
            "missing downstream",
            "failure report",
            "stage cache",
            "pipeline stage",
        )
    ):
        code = "pipeline_stage"
    else:
        code = "internal"

    title, user_message = _JOB_ERROR_MESSAGES[code]
    if code == "pipeline_stage" and stage_label:
        title = f"{stage_label}阶段未能完成"
    return {
        "error_code": code,
        "error_title": title,
        "error": user_message,
        "stage": stage,
        "stage_label": stage_label,
    }


def _redact_error_text(value, secrets_to_redact):
    return redact_text(value, secrets=tuple(secrets_to_redact))


def _job_error_presentation(job):
    if not job or job.get("status") != "error":
        return {"error_code": None, "error_title": None, "error": None}
    if job.get("error_code") and job.get("error_title") and job.get("error_user_message"):
        return {
            "error_code": job["error_code"],
            "error_title": job["error_title"],
            "error": job["error_user_message"],
        }
    presentation = _classify_job_error(
        RuntimeError(job.get("error") or "Pipeline worker failed"),
        stage=job.get("stage"),
        stage_label=job.get("stage_label"),
    )
    return {
        "error_code": presentation["error_code"],
        "error_title": presentation["error_title"],
        "error": presentation["error"],
    }


def _probe_chat_config(config):
    started = time.perf_counter()
    try:
        llm = SimpleLLM(
            model=config["model_name"],
            api_url=config["api_url"],
            api_key=config["api_key"],
        )
        llm.suppress_error_details = True
        llm.request_timeout = (10.0, 30.0)
        llm.session.max_redirects = 0
        answer = llm.ask("这是连接测试。请只回复 OK。", temperature=0)
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("incompatible response format: empty choices")
        code = "ok"
    except Exception as exc:
        code = _provider_failure_code(exc)
    latency = (time.perf_counter() - started) * 1000
    return _config_validation_result(code == "ok", code, latency)


def _probe_embedding_config(config):
    started = time.perf_counter()
    try:
        vectors = get_embedding(
            "MathWeaver connection test",
            config["embedding_api_key"],
            config["embedding_url"],
            config["embedding_model"],
            raise_on_failure=True,
            timeout_seconds=30.0,
            max_retries_override=0,
        )
        vector = vectors[0] if isinstance(vectors, list) and vectors else None
        if (
            not isinstance(vector, list)
            or not vector
            or any(not isinstance(value, (int, float)) for value in vector)
        ):
            raise ValueError("incompatible embedding response format")
        code = "ok"
    except Exception as exc:
        code = _provider_failure_code(exc)
    latency = (time.perf_counter() - started) * 1000
    return _config_validation_result(code == "ok", code, latency)


@app.route("/api/v2/config/validate", methods=["POST"])
def validate_llm_config():
    body = request.get_json(silent=True) or {}
    target = str(body.get("target") or "all").strip().lower()
    if target not in {"all", "chat", "embedding"}:
        return jsonify({
            "error": "invalid_validation_target",
            "message": "配置验证目标无效。",
        }), 400

    config = {
        "api_url": str(body.get("api_url") or "").strip(),
        "model_name": str(body.get("model_name") or "").strip(),
        "api_key": str(body.get("api_key") or "").strip(),
        "embedding_url": str(body.get("embedding_url") or "").strip(),
        "embedding_model": str(body.get("embedding_model") or "").strip(),
        "embedding_api_key": str(body.get("embedding_api_key") or "").strip(),
    }
    if target == "chat":
        required_fields = ("api_url", "model_name", "api_key")
        missing = [field for field in required_fields if not config[field]]
    elif target == "embedding":
        missing = []
        if not config["embedding_model"]:
            missing.append("embedding_model")
        if not (config["embedding_url"] or config["api_url"]):
            missing.append("embedding_url")
        if not (config["embedding_api_key"] or config["api_key"]):
            missing.append("embedding_api_key")
    else:
        required_fields = ("api_url", "model_name", "api_key", "embedding_model")
        missing = [field for field in required_fields if not config[field]]
    if missing:
        return jsonify({
            "error": "missing_required_fields",
            "fields": missing,
            "message": "请先填写完整的待测试配置。",
        }), 400

    results = {}
    if target in {"all", "chat"}:
        llm_url_error = _provider_url_error(config["api_url"])
        results["llm"] = (
            _config_validation_result(False, llm_url_error)
            if llm_url_error
            else _probe_chat_config(config)
        )
    if target in {"all", "embedding"}:
        config["embedding_url"] = config["embedding_url"] or config["api_url"]
        config["embedding_api_key"] = config["embedding_api_key"] or config["api_key"]
        embedding_url_error = _provider_url_error(config["embedding_url"])
        results["embedding"] = (
            _config_validation_result(False, embedding_url_error)
            if embedding_url_error
            else _probe_embedding_config(config)
        )
    return jsonify({
        "ok": all(result["ok"] for result in results.values()),
        **results,
    })


# ── History endpoints ─────────────────────────────────────────────────────────

def _job_storage_root() -> Path:
    root = (Path(_DB_PATH).resolve().parent if _desktop_legacy_auth else _DATA_ROOT.resolve()) / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _persistent_job_dir(job_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(job_id))
    return _job_storage_root() / safe_id


def _restore_job_files(job: dict) -> bool:
    """仅在数据库归属已确认后恢复该用户任务，拒绝被篡改的存储前缀。"""
    if _desktop_legacy_auth or _object_storage is None:
        return False
    owner_id = job.get("_user_id")
    job_id = str(job.get("job_id") or "")
    stored_prefix = job.get("_object_storage_prefix")
    if owner_id is None or not job_id or not stored_prefix:
        return False
    storage_version = job.get("_storage_version")
    storage_checksum = job.get("_storage_checksum")
    expected_prefix = (
        _object_storage.version_prefix(int(owner_id), job_id, storage_version)
        if storage_version
        else _object_storage.task_prefix(int(owner_id), job_id)
    )
    if stored_prefix != expected_prefix:
        raise ObjectStorageError("stored OSS task prefix does not match task ownership")
    if storage_version:
        if not storage_checksum:
            raise ObjectStorageError("stored OSS version checksum is missing")
        return _object_storage.restore_version(
            int(owner_id),
            job_id,
            storage_version,
            storage_checksum,
            _persistent_job_dir(job_id),
            _source_pdf_dir(job_id),
        )
    return _object_storage.restore_job(
        int(owner_id),
        job_id,
        _persistent_job_dir(job_id),
        _source_pdf_dir(job_id),
    )


def _history_resume_available(row) -> bool:
    status = row["status"] if "status" in row.keys() else "done"
    if status not in {"paused", "error"}:
        return False
    source_markdown = row["source_markdown"] if "source_markdown" in row.keys() else None
    if not source_markdown:
        return False
    manifest_path = _persistent_job_dir(row["id"]) / "_stage_cache" / "manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(manifest, dict) and manifest.get("schema_version") == 1


def _history_item_payload(row):
    status = row["status"] if "status" in row.keys() else "done"
    return {
        "id": row["id"],
        "filename": row["filename"],
        "node_count": row["node_count"],
        "edge_count": row["edge_count"],
        "status": status or "done",
        "stage": row["stage"] if "stage" in row.keys() else None,
        "stage_label": row["stage_label"] if "stage_label" in row.keys() else None,
        "stage_index": int(row["stage_index"] or 0) if "stage_index" in row.keys() else 0,
        "total_stages": int(row["total_stages"] or 0) if "total_stages" in row.keys() else 0,
        "experimental_logic_ir": bool(
            row["experimental_logic_ir"]
            if "experimental_logic_ir" in row.keys()
            else False
        ),
        "stages_done": (
            list(row.get("stages_done") or [])
            if isinstance(row, dict)
            else _json_list(row["stages_done_json"] if "stages_done_json" in row.keys() else "[]")
        ),
        "resume_available": _history_resume_available(row),
        "updated_at": (
            row["updated_at"]
            if "updated_at" in row.keys() and row["updated_at"]
            else row["created_at"]
        ),
        "created_at": row["created_at"],
    }


def _upsert_job_history(
    job: dict,
    status: str,
    user_id: int | None = None,
    *,
    stored_version=None,
) -> bool:
    owner_id = user_id if user_id is not None else job.get("_user_id")
    if owner_id is None:
        return False
    stage_defs = _job_stage_defs(job)
    result = job.get("result") if status == "done" else job.get("partial")
    result = result if isinstance(result, dict) else {}
    nodes = result.get("nodes") if isinstance(result.get("nodes"), list) else []
    edges = result.get("edges") if isinstance(result.get("edges"), list) else []
    if not _desktop_legacy_auth:
        created_at = job.get("created_at")
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                created_at = None
        if not isinstance(created_at, datetime):
            created_at = datetime.now(timezone.utc)
        snapshot = JobSnapshot(
            job_id=str(job["job_id"]),
            filename=job.get("filename") or "input.md",
            status=status,
            nodes=nodes,
            edges=edges,
            source_markdown=job.get("source_markdown"),
            latex_macros=job.get("latex_macros") or result.get("latex_macros") or {},
            source_pdf=job.get("source_pdf"),
            stage=job.get("stage"),
            stage_label=job.get("stage_label"),
            stage_index=int(job.get("stage_index") or 0),
            total_stages=int(job.get("total_stages") or len(stage_defs)),
            stages_done=list(job.get("stages_done") or []),
            source_format=job.get("source_format") or "markdown",
            experimental_logic_ir=bool(job.get("_experimental_logic_ir")),
            created_at=created_at,
            object_storage_prefix=job.get("_object_storage_prefix"),
        )
        try:
            persisted = (
                _learning_repository.commit_storage_version(
                    int(owner_id), snapshot, stored_version
                )
                if stored_version is not None
                else _learning_repository.upsert_job_progress(int(owner_id), snapshot)
            )
        except Exception:
            return False
        if persisted:
            job["_user_id"] = int(owner_id)
            job["_history_persisted"] = True
        return persisted
    now = datetime.utcnow().isoformat()
    source_pdf = job.get("source_pdf")
    values = {
        "filename": job.get("filename") or "input.md",
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes_json": json.dumps(nodes, ensure_ascii=False),
        "edges_json": json.dumps(edges, ensure_ascii=False),
        "source_markdown": job.get("source_markdown"),
        "latex_macros": json.dumps(
            job.get("latex_macros") or result.get("latex_macros") or {},
            ensure_ascii=False,
        ),
        "source_pdf_json": (
            json.dumps(_stored_source_pdf_meta(source_pdf), ensure_ascii=False)
            if source_pdf
            else None
        ),
        "status": status,
        "stage": job.get("stage"),
        "stage_label": job.get("stage_label"),
        "stage_index": int(job.get("stage_index") or 0),
        "total_stages": int(job.get("total_stages") or len(stage_defs)),
        "stages_done_json": json.dumps(job.get("stages_done") or [], ensure_ascii=False),
        "source_format": job.get("source_format") or "markdown",
        "experimental_logic_ir": int(bool(job.get("_experimental_logic_ir"))),
        "updated_at": now,
    }
    conn = None
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        existing = conn.execute(
            "SELECT user_id FROM history WHERE id = ?",
            (job["job_id"],),
        ).fetchone()
        if existing and int(existing[0]) != int(owner_id):
            return False
        if existing:
            assignments = ", ".join(f"{key} = ?" for key in values)
            conn.execute(
                f"UPDATE history SET {assignments} WHERE id = ? AND user_id = ?",
                (*values.values(), job["job_id"], owner_id),
            )
        else:
            columns = ["id", "user_id", *values.keys(), "created_at"]
            placeholders = ", ".join("?" for _ in columns)
            conn.execute(
                f"INSERT INTO history ({', '.join(columns)}) VALUES ({placeholders})",
                (
                    job["job_id"],
                    owner_id,
                    *values.values(),
                    job.get("created_at") or now,
                ),
            )
        conn.commit()
        job["_user_id"] = int(owner_id)
        job["_history_persisted"] = True
        return True
    except Exception:
        return False
    finally:
        if conn is not None:
            conn.close()


def _history_job_resource(row: dict) -> dict:
    result = {
        "nodes": row.get("nodes") or [],
        "edges": row.get("edges") or [],
        "latex_macros": row.get("latex_macros") or {},
        "source_pdf": _public_source_pdf_meta(row.get("source_pdf")),
    }
    return {
        "job_id": row["id"],
        "status": row.get("status") or "done",
        "filename": row.get("filename"),
        "stage": row.get("stage"),
        "stage_label": row.get("stage_label"),
        "stage_index": int(row.get("stage_index") or 0),
        "total_stages": int(row.get("total_stages") or 0),
        "stages_done": list(row.get("stages_done") or []),
        "result": result if row.get("status") == "done" else None,
        "partial": result if row.get("status") != "done" else None,
        "source_markdown": row.get("source_markdown") or "",
        "latex_macros": row.get("latex_macros") or {},
        "source_format": row.get("source_format") or "markdown",
        "source_pdf": _read_source_pdf_meta(row),
        "_user_id": row.get("user_id"),
        "_history_persisted": True,
        "_persistent_artifacts": True,
        "_object_storage_prefix": row.get("object_storage_prefix"),
        "_storage_version": row.get("storage_version"),
        "_storage_checksum": row.get("storage_checksum"),
        "_artifact_dir": str(_persistent_job_dir(row["id"])),
        "_is_live": False,
        "created_at": row.get("created_at"),
    }


def _legacy_history_job_resource(row) -> dict:
    try:
        nodes = json.loads(row["nodes_json"] or "[]")
        edges = json.loads(row["edges_json"] or "[]")
        latex_macros = json.loads(row["latex_macros"] or "{}")
    except (TypeError, json.JSONDecodeError):
        nodes, edges, latex_macros = [], [], {}
    result = {
        "nodes": nodes,
        "edges": edges,
        "latex_macros": latex_macros,
        "source_pdf": _public_source_pdf_meta(_read_source_pdf_meta(row)),
    }
    return {
        "job_id": row["id"],
        "status": row["status"] or "done",
        "filename": row["filename"],
        "result": result if (row["status"] or "done") == "done" else None,
        "partial": result if (row["status"] or "done") != "done" else None,
        "source_pdf": _read_source_pdf_meta(row),
        "source": "pipeline",
        "_artifact_dir": str(_persistent_job_dir(row["id"])),
        "_is_live": False,
    }


def _owned_job_resource(job_id: str, *, allow_desktop_missing: bool = False):
    """统一完成 Web 鉴权与任务归属检查，404 隐藏其他用户资源是否存在。"""
    if _desktop_legacy_auth:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is not None:
                return {**job, "_is_live": True}, None
        row = _get_db().execute("SELECT * FROM history WHERE id = ?", (job_id,)).fetchone()
        if row is not None:
            return _legacy_history_job_resource(row), None
        if allow_desktop_missing:
            # 仅显式桌面模式允许旧客户端用请求体导出尚未登记的节点/边。
            return None, None
        return None, (jsonify({"error": "Not found"}), 404)

    user = _current_user()
    if not user:
        return None, (jsonify({"error": "not authenticated"}), 401)
    user_id = int(user["id"])
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            if int(job.get("_user_id") or -1) != user_id:
                return None, (jsonify({"error": "Not found"}), 404)
            return {**job, "_is_live": True}, None
    row = _learning_repository.get_owned_history(user_id, job_id)
    if row is None:
        return None, (jsonify({"error": "Not found"}), 404)
    return _history_job_resource(row), None


@app.route("/api/v2/history", methods=["GET"])
def history_list():
    user = _current_user()
    if not user:
        return jsonify({"error": "not authenticated"}), 401
    if not _desktop_legacy_auth:
        rows = _learning_repository.list_history(int(user["id"]), limit=50)
        return jsonify([_history_item_payload(row) for row in rows])
    db = _get_db()
    rows = db.execute(
        """SELECT id, filename, node_count, edge_count, status, stage, stage_label,
                  stage_index, total_stages, stages_done_json, source_markdown,
                  experimental_logic_ir, updated_at, created_at
           FROM history
           WHERE user_id = ?
           ORDER BY created_at DESC
           LIMIT 50""",
        (user["id"],),
    ).fetchall()
    return jsonify([_history_item_payload(row) for row in rows])


@app.route("/api/v2/history", methods=["POST"])
def history_save():
    user = _current_user()
    if not user:
        return jsonify({"error": "not authenticated"}), 401
    body = request.get_json(silent=True) or {}
    job_id = body.get("job_id") or ""
    job = _jobs.get(job_id)
    if not job or job["status"] != "done":
        return jsonify({"error": "job not done or not found"}), 400
    if not _desktop_legacy_auth and int(job.get("_user_id") or -1) != int(user["id"]):
        return jsonify({"error": "job not done or not found"}), 400
    if not _persist_job_with_files(job, "done", int(user["id"])):
        if job.get("_capacity_error"):
            code, http_status = job["_capacity_error"]
            return jsonify({"error": code}), http_status
        return jsonify({"error": "unable to save history"}), 500
    return jsonify({"ok": True, "id": job_id}), 201


@app.route("/api/v2/history/<hist_id>", methods=["GET"])
def history_get(hist_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "not authenticated"}), 401
    if not _desktop_legacy_auth:
        row = _learning_repository.get_owned_history(int(user["id"]), hist_id)
    else:
        db = _get_db()
        row = db.execute(
            "SELECT * FROM history WHERE id = ? AND user_id = ?", (hist_id, user["id"])
        ).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    status = row["status"] if "status" in row.keys() else "done"
    if status != "done":
        return jsonify({"error": "history item is not complete", "status": status}), 409
    nodes = list(row.get("nodes") or []) if isinstance(row, dict) else json.loads(row["nodes_json"])
    # Backfill node_index_in_doc for old records that lack it (use node id as proxy)
    for n in nodes:
        if "node_index_in_doc" not in n or n["node_index_in_doc"] is None:
            n["node_index_in_doc"] = n.get("id", 0)
        if not n.get("source_statement"):
            n["source_statement"] = _node_original_statement(n)
    return jsonify({
        "id": row["id"],
        "filename": row["filename"],
        "node_count": row["node_count"],
        "edge_count": row["edge_count"],
        "created_at": row["created_at"],
        "nodes": nodes,
        "edges": list(row.get("edges") or []) if isinstance(row, dict) else json.loads(row["edges_json"]),
        "latex_macros": row.get("latex_macros", {}) if isinstance(row, dict) else json.loads(row["latex_macros"] or "{}"),
        "source_pdf": _public_source_pdf_meta(_read_source_pdf_meta(row)),
    })


def _resume_llm_config(payload):
    config = payload.get("llm_config") if isinstance(payload, dict) else None
    if not isinstance(config, dict):
        return None
    api_url = (config.get("api_url") or "").strip()
    model_name = (config.get("model_name") or "").strip()
    api_key = (config.get("api_key") or "").strip()
    embedding_model = (config.get("embedding_model") or "").strip()
    if not api_url or not model_name or not api_key or not embedding_model:
        return None
    return {
        "api_url": api_url,
        "model_name": model_name,
        "api_key": api_key,
        "embedding_url": (config.get("embedding_url") or "").strip() or api_url,
        "embedding_model": embedding_model,
        "embedding_api_key": (
            (config.get("embedding_api_key") or "").strip() or api_key
        ),
    }


@app.route("/api/v2/history/<hist_id>/resume", methods=["POST"])
def history_resume(hist_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "not authenticated"}), 401
    if not _desktop_legacy_auth:
        row = _learning_repository.get_owned_history(int(user["id"]), hist_id)
    else:
        row = _get_db().execute(
            "SELECT * FROM history WHERE id = ? AND user_id = ?",
            (hist_id, user["id"]),
        ).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    status = row["status"] if "status" in row.keys() else "done"
    if status not in {"paused", "error"}:
        return jsonify({
            "error": "Only a paused or failed history task can be resumed",
            "status": status,
        }), 409
    if not _desktop_legacy_auth and row.get("object_storage_prefix"):
        try:
            _restore_job_files({
                "job_id": hist_id,
                "_user_id": int(user["id"]),
                "_object_storage_prefix": row.get("object_storage_prefix"),
                "_storage_version": row.get("storage_version"),
                "_storage_checksum": row.get("storage_checksum"),
            })
        except ObjectStorageError:
            return jsonify({"error": "Task files could not be restored from OSS"}), 503
    if not _history_resume_available(row):
        return jsonify({"error": "Recovery cache is unavailable"}), 410
    llm_config = _resume_llm_config(request.get_json(silent=True) or {})
    if llm_config is None:
        return jsonify({"error": "Complete LLM and embedding configuration is required"}), 400

    artifact_dir = _persistent_job_dir(hist_id)
    source_format = row["source_format"] or "markdown"
    source_path = artifact_dir / _safe_upload_filename(row["filename"], source_format)
    if not source_path.is_file():
        artifact_dir.mkdir(parents=True, exist_ok=True)
        with source_path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(row["source_markdown"] or "")

    with _jobs_lock:
        job = _jobs.get(hist_id)
        if job and int(job.get("_user_id") or -1) != int(user["id"]):
            return jsonify({"error": "forbidden"}), 403
        if job and job.get("status") == "running":
            return jsonify({"error": "Pipeline worker is already running"}), 409
        if job is None:
            if isinstance(row, dict):
                latex_macros = row.get("latex_macros") or {}
                partial_nodes = row.get("nodes") or []
                partial_edges = row.get("edges") or []
            else:
                try:
                    latex_macros = json.loads(row["latex_macros"] or "{}")
                except json.JSONDecodeError:
                    latex_macros = {}
                try:
                    partial_nodes = json.loads(row["nodes_json"] or "[]")
                    partial_edges = json.loads(row["edges_json"] or "[]")
                except json.JSONDecodeError:
                    partial_nodes, partial_edges = [], []
            experimental_logic_ir = bool(
                row["experimental_logic_ir"]
                if "experimental_logic_ir" in row.keys()
                else False
            )
            stage_defs = _pipeline_stage_defs(experimental_logic_ir)
            job = {
                "job_id": hist_id,
                "status": status,
                "filename": row["filename"],
                "stage": row["stage"],
                "stage_label": row["stage_label"],
                "stage_index": int(row["stage_index"] or 0),
                "total_stages": int(row["total_stages"] or len(stage_defs)),
                "stages_done": list(row.get("stages_done") or []) if isinstance(row, dict) else _json_list(row["stages_done_json"] or "[]"),
                "result": None,
                "partial": {"nodes": partial_nodes, "edges": partial_edges},
                "error": None,
                "source_markdown": row["source_markdown"] or "",
                "latex_macros": latex_macros,
                "latex_macro_warnings": [],
                "source_format": source_format,
                "source_pdf": _read_source_pdf_meta(row),
                "source": "pipeline",
                "_artifact_dir": str(artifact_dir),
                "_md_path": str(source_path),
                "_llm_config": llm_config,
                "_enable_analysis": True,
                "_experimental_logic_ir": experimental_logic_ir,
                "_stage_defs": stage_defs,
                "_user_id": int(user["id"]),
                "_persistent_artifacts": True,
                "_history_persisted": True,
                "created_at": row["created_at"],
            }
            _jobs[hist_id] = job
        else:
            job["_llm_config"] = llm_config
            job["_history_persisted"] = True
            experimental_logic_ir = bool(job.get("_experimental_logic_ir"))
            job["_stage_defs"] = _pipeline_stage_defs(experimental_logic_ir)

    error = _begin_pipeline_resume(hist_id)
    if error:
        message, status_code = error
        return jsonify({"error": message}), status_code
    with _jobs_lock:
        snapshot = _job_tracking_snapshot(_jobs[hist_id])
    return jsonify({"ok": True, "status": "running", "job": snapshot}), 202


@app.route("/api/v2/history/<hist_id>/markdown", methods=["GET"])
def history_markdown(hist_id):
    """Return the raw markdown stored during processing (from temp file cache)."""
    user = _current_user()
    if not user:
        return jsonify({"error": "not authenticated"}), 401
    if not _desktop_legacy_auth:
        row = _learning_repository.get_owned_history(int(user["id"]), hist_id)
    else:
        db = _get_db()
        row = db.execute(
            "SELECT filename, source_markdown FROM history WHERE id = ? AND user_id = ?", (hist_id, user["id"])
        ).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    if row["source_markdown"]:
        return jsonify({"markdown": row["source_markdown"], "filename": row["filename"]})
    if not _desktop_legacy_auth:
        # Web 端不扫描服务器任意目录；恢复材料只能来自所属数据库记录和受控任务目录。
        return jsonify({"error": "markdown file not found on disk"}), 404
    # Check if markdown was saved in cache dir
    import glob
    stem = os.path.splitext(row["filename"])[0]
    patterns = [
        os.path.join(os.path.dirname(__file__), "**", f"{stem}*.md"),
        os.path.expanduser(f"~/Documents/AI4Math/**/{stem}*.md"),
    ]
    for pat in patterns:
        matches = glob.glob(pat, recursive=True)
        if matches:
            with open(matches[0], "r", encoding="utf-8") as f:
                return jsonify({"markdown": f.read(), "filename": row["filename"]})
    return jsonify({"error": "markdown file not found on disk"}), 404


@app.route("/api/v2/history/<hist_id>", methods=["DELETE"])
def history_delete(hist_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "not authenticated"}), 401
    if not _desktop_legacy_auth:
        row = _learning_repository.get_owned_history(int(user["id"]), hist_id)
    else:
        db = _get_db()
        row = db.execute(
            "SELECT status FROM history WHERE id = ? AND user_id = ?",
            (hist_id, user["id"]),
        ).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    with _jobs_lock:
        live_job = _jobs.get(hist_id)
        if live_job and live_job.get("status") == "running":
            return jsonify({"error": "Pause the running task before deleting it"}), 409
        artifact_dir = live_job.get("_artifact_dir") if live_job else None
    if not _desktop_legacy_auth:
        # 请求事务只做软删除和 outbox；OSS 与本地缓存清理由可重试 worker 完成。
        if not _learning_repository.soft_delete_history(int(user["id"]), hist_id):
            return jsonify({"error": "not found"}), 404
        with _jobs_lock:
            _jobs.pop(hist_id, None)
            runtime = _job_runtimes.pop(hist_id, None)
        if runtime and runtime.get("queue"):
            try:
                runtime["queue"].close()
            except Exception:
                pass
        return jsonify({"ok": True, "cleanup_status": "pending"})
    try:
        _cancel_job_record(
            hist_id,
            int(user["id"]),
            artifact_dir=artifact_dir or str(_persistent_job_dir(hist_id)),
            object_storage_prefix=(
                row.get("object_storage_prefix") if isinstance(row, dict) else None
            ),
        )
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    except (ObjectStorageError, OSError, ValueError) as exc:
        return jsonify({"error": f"Unable to delete history: {exc}"}), 500
    return jsonify({"ok": True})


# ── Proof workspace endpoints ────────────────────────────────────────────────

def _workspace_row_to_dict(row):
    return {
        "nodeId": row["node_id"],
        "userProof": row["user_proof"] or "",
        "versions": _json_list(row["versions_json"]),
        "aiMessages": _json_list(row["ai_messages_json"]),
        "imports": _json_list(row["imports_json"]),
        "updatedAt": row["updated_at"],
    }


@app.route("/api/v2/proof-workspaces/<graph_id>", methods=["GET"])
def proof_workspace_list(graph_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "not authenticated"}), 401
    if not 1 <= len(graph_id) <= 64:
        return jsonify({"error": "graph_id length must be 1 to 64"}), 400
    if not _desktop_legacy_auth:
        rows = _learning_repository.list_proof_workspaces(int(user["id"]), graph_id)
        return jsonify({"workspaces": rows})
    db = _get_db()
    rows = db.execute(
        """SELECT node_id, user_proof, versions_json, ai_messages_json, imports_json, updated_at
           FROM proof_workspaces
           WHERE user_id = ? AND graph_id = ?
           ORDER BY node_id ASC""",
        (user["id"], graph_id),
    ).fetchall()
    return jsonify({"workspaces": [_workspace_row_to_dict(row) for row in rows]})


@app.route("/api/v2/proof-workspaces/<graph_id>/<int:node_id>", methods=["PUT"])
def proof_workspace_save(graph_id, node_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "not authenticated"}), 401
    if not 1 <= len(graph_id) <= 64:
        return jsonify({"error": "graph_id length must be 1 to 64"}), 400
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "JSON object required"}), 400
    user_proof = body.get("userProof") or ""
    versions = body.get("versions") if isinstance(body.get("versions"), list) else []
    ai_messages = body.get("aiMessages") if isinstance(body.get("aiMessages"), list) else []
    imports = body.get("imports") if isinstance(body.get("imports"), list) else []
    if not _desktop_legacy_auth:
        workspace = _learning_repository.upsert_proof_workspace(
            int(user["id"]),
            graph_id,
            node_id,
            {
                "userProof": user_proof,
                "versions": versions,
                "aiMessages": ai_messages,
                "imports": imports,
            },
        )
        return jsonify({"ok": True, "workspace": workspace})
    now = datetime.utcnow().isoformat()
    db = _get_db()
    db.execute(
        """INSERT INTO proof_workspaces
              (user_id, graph_id, node_id, user_proof, versions_json, ai_messages_json, imports_json, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id, graph_id, node_id) DO UPDATE SET
              user_proof       = excluded.user_proof,
              versions_json    = excluded.versions_json,
              ai_messages_json = excluded.ai_messages_json,
              imports_json     = excluded.imports_json,
              updated_at       = excluded.updated_at""",
        (
            user["id"], graph_id, node_id, user_proof,
            json.dumps(versions, ensure_ascii=False),
            json.dumps(ai_messages, ensure_ascii=False),
            json.dumps(imports, ensure_ascii=False),
            now,
        ),
    )
    db.commit()
    return jsonify({
        "ok": True,
        "workspace": {
            "nodeId": node_id,
            "userProof": user_proof,
            "versions": versions,
            "aiMessages": ai_messages,
            "imports": imports,
            "updatedAt": now,
        },
    })


def _ocr_error_response(exc: OcrError):
    payload = {
        "error": exc.code,
        "message": exc.message,
        "retryable": exc.retryable,
        "error_code": exc.code,
    }
    if exc.code == "ocr_component_unavailable":
        payload["installable"] = False
    return jsonify(payload), exc.status_code


@app.route("/api/v2/ocr/runtime", methods=["GET"])
def ocr_runtime_status():
    return jsonify(get_ocr_manager().runtime_status())


@app.route("/api/v2/ocr/runtime/install", methods=["POST"])
def ocr_runtime_install():
    try:
        return jsonify(get_ocr_manager().start_install()), 202
    except OcrError as exc:
        return _ocr_error_response(exc)


@app.route("/api/v2/ocr/runtime/install/<install_id>", methods=["GET"])
def ocr_runtime_install_status(install_id: str):
    state = get_ocr_manager().runtime_status()
    if state.get("install_id") != install_id:
        return jsonify({"error": "install_not_found", "message": "install task not found"}), 404
    return jsonify(state)


@app.route("/api/v2/ocr/runtime/install/<install_id>/cancel", methods=["POST"])
def ocr_runtime_install_cancel(install_id: str):
    try:
        return jsonify(get_ocr_manager().cancel_install(install_id))
    except OcrError as exc:
        return _ocr_error_response(exc)


@app.route("/api/v2/ocr/recovery", methods=["GET"])
def ocr_recovery_list():
    return jsonify({"jobs": get_ocr_manager().list_recovery_jobs()})


@app.route("/api/v2/ocr/uploads", methods=["POST"])
def ocr_upload_create():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"error": "invalid_file", "message": "file is required"}), 400
    manager = get_ocr_manager()
    writer = None
    try:
        suffix = Path(upload.filename).suffix.lower()
        max_bytes = PDF_MAX_BYTES if suffix == ".pdf" else IMAGE_MAX_BYTES
        content_length = request.content_length or 0
        if content_length > max_bytes + 2 * CHUNK_SIZE:
            return jsonify({"error": "file_too_large", "message": "request exceeds the OCR upload limit"}), 413
        writer = manager.begin_upload(upload.filename)
        while True:
            chunk = upload.stream.read(CHUNK_SIZE)
            if not chunk:
                break
            writer.write(chunk)
        return jsonify(writer.finish()), 201
    except OcrError as exc:
        if writer:
            writer.abort()
        return _ocr_error_response(exc)
    except Exception as exc:
        if writer:
            writer.abort()
        return jsonify({"error": "upload_failed", "message": str(exc), "retryable": True}), 400


@app.route("/api/v2/ocr/uploads/<upload_id>", methods=["DELETE"])
def ocr_upload_delete(upload_id: str):
    try:
        get_ocr_manager().delete_upload(upload_id)
        return jsonify({"ok": True})
    except OcrError as exc:
        return _ocr_error_response(exc)


@app.route("/api/v2/ocr/jobs", methods=["POST"])
def ocr_job_create():
    body = request.get_json(silent=True) or {}
    try:
        return jsonify(get_ocr_manager().create_job(str(body.get("upload_id") or ""))), 202
    except OcrError as exc:
        return _ocr_error_response(exc)


@app.route("/api/v2/ocr/jobs/<ocr_job_id>", methods=["GET"])
def ocr_job_status(ocr_job_id: str):
    try:
        return jsonify(get_ocr_manager().get_job(ocr_job_id))
    except OcrError as exc:
        return _ocr_error_response(exc)


@app.route("/api/v2/ocr/jobs/<ocr_job_id>/result", methods=["GET"])
def ocr_job_result(ocr_job_id: str):
    try:
        return jsonify(get_ocr_manager().get_result(ocr_job_id))
    except OcrError as exc:
        return _ocr_error_response(exc)


@app.route("/api/v2/ocr/jobs/<ocr_job_id>/cancel", methods=["POST"])
def ocr_job_cancel(ocr_job_id: str):
    try:
        return jsonify(get_ocr_manager().cancel_job(ocr_job_id))
    except OcrError as exc:
        return _ocr_error_response(exc)


@app.route("/api/v2/ocr/jobs/<ocr_job_id>/retry", methods=["POST"])
def ocr_job_retry(ocr_job_id: str):
    try:
        return jsonify(get_ocr_manager().retry_job(ocr_job_id)), 202
    except OcrError as exc:
        return _ocr_error_response(exc)


@app.route("/api/v2/ocr/recovery/<ocr_job_id>", methods=["DELETE"])
def ocr_recovery_delete(ocr_job_id: str):
    try:
        return jsonify(get_ocr_manager().delete_recovery(ocr_job_id))
    except OcrError as exc:
        return _ocr_error_response(exc)


@app.route("/api/v2/proof-import-ocr", methods=["POST"])
def proof_import_ocr():
    return jsonify({
        "error": "ocr_api_replaced",
        "message": "Use the streaming OCR upload and job APIs.",
        "retryable": False,
    }), 410


def _proof_assist_prompt(action, node, user_proof):
    action_notes = {
        "hint": "给出下一步证明提示。只提示方向、可尝试的定义/引理/关键观察，不要给出完整证明。",
        "check": "检查学生当前证明中是否存在错误、跳步或未证明断言。若没有明显错误，说明当前步骤看起来合理，但不是形式化验证。",
        "summarize": "总结学生当前证明思路、已经使用的关键结构，以及接下来仍需要补齐的部分。",
    }
    title = node.get("title_zh") or node.get("title_en") or node.get("label") or f"节点 {node.get('id', '')}"
    payload = {
        "title": title,
        "node_type": node.get("node_type"),
        "statement": node.get("content"),
        "statement_form": node.get("statement_form"),
        "conditions": node.get("conditions") or [],
        "conclusions": node.get("conclusions") or [],
        "student_proof": user_proof or "",
    }
    return f"""你是数学教学中的证明辅导助手。请严格遵守：
1. 不要直接给出完整标准证明或最终答案。
2. 不要复述教材证明。
3. 回答应围绕学生当前输入，帮助其继续推进。
4. 如果学生输入为空，也只给出入手方向，不展开完整证明。
5. 用中文回答，必要时保留数学符号和 LaTeX。

本次任务：{action_notes[action]}

节点与学生输入如下：
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""


@app.route("/api/v2/proof-assist", methods=["POST"])
def proof_assist():
    body = request.get_json(silent=True) or {}
    action = (body.get("action") or "").strip()
    if action not in {"hint", "check", "summarize"}:
        return jsonify({"error": "invalid action"}), 400
    node = body.get("node")
    if not isinstance(node, dict):
        return jsonify({"error": "node required"}), 400
    user_proof = body.get("userProof") or ""
    user = _current_user()
    llm_config = _complete_llm_config(body.get("llm_config")) or _active_user_llm_config(user)
    if not llm_config:
        return jsonify({
            "error": "needs_llm_config",
            "message": "请先完成 LLM 配置后再使用 AI 辅助证明。",
        }), 400
    try:
        llm = SimpleLLM(
            model=llm_config["model_name"],
            api_url=llm_config["api_url"],
            api_key=llm_config["api_key"],
        )
        response = llm.ask(_proof_assist_prompt(action, node, user_proof), temperature=1)
    except Exception as exc:
        return jsonify({"error": "llm_failed", "message": str(exc)}), 502
    return jsonify({"response": response})


# ── In-memory job store ───────────────────────────────────────────────────────

_jobs: dict[str, dict] = {}
_jobs_lock = threading.RLock()
_job_runtimes: dict[str, dict] = {}

STAGE_DEFS = list(FIXED_STAGE_DEFS)


def _pipeline_stage_defs(experimental_logic_ir=False):
    if not experimental_logic_ir:
        return list(STAGE_DEFS)
    return [
        (stage.key, stage.label)
        for stage in build_fixed_stage_plan(experimental_logic_ir=True)
    ]


def _job_stage_defs(job):
    stage_defs = job.get("_stage_defs")
    if isinstance(stage_defs, (list, tuple)) and all(
        isinstance(item, (list, tuple)) and len(item) == 2
        for item in stage_defs
    ):
        return [(str(item[0]), str(item[1])) for item in stage_defs]
    return _pipeline_stage_defs(bool(job.get("_experimental_logic_ir")))


NODE_TYPE_ORDER = ["定义", "公理", "定理", "引理", "推论", "性质", "命题", "例子"]


def _title_str(title):
    if isinstance(title, dict):
        return title.get("chinese") or title.get("english") or ""
    return str(title) if title else ""


_RE_PY_STR = re.compile(r'^r?"{1,3}|"{1,3}$')
_RE_CMD_ARTIFACT = re.compile(r'@@CMD::')
_RE_TEX_LABEL = re.compile(r"\\label\s*\{[^{}]*\}")
_RE_TEX_EQREF = re.compile(r"\\eqref\s*\{([^{}]+)\}")
_RE_TEX_REF = re.compile(r"\\(?:autoref|cref|Cref|nameref|ref)\s*\{([^{}]+)\}")
_RE_TEX_COLOR_BF_GROUP = re.compile(r"\{\\color\s*\{[^{}]+\}\s*\{\{\\bf\s+([^{}]*)\}\}\}")
_RE_TEX_COLOR_BF = re.compile(r"\\color\s*\{[^{}]+\}\s*\{\{\\bf\s+([^{}]*)\}\}")
_RE_TEX_COLOR_GROUP = re.compile(r"\{\\color\s*\{[^{}]+\}\s*\{([^{}]*)\}\}")
_RE_TEX_COLOR = re.compile(r"\\color\s*\{[^{}]+\}\s*\{([^{}]*)\}")
_RE_TEX_TEXTBF = re.compile(r"\\textbf\s*\{([^{}]*)\}")
_RE_TEX_BF_GROUP = re.compile(r"\{\\bf\s+([^{}]*)\}")
_RE_TEX_EMPH = re.compile(r"\\(?:emph|textit)\s*\{([^{}]*)\}")
_RE_TEX_DISPLAY_ENV = re.compile(
    r"\\begin\{(equation\*?|align\*?|eqnarray\*?|gather\*?|multline\*?)\}([\s\S]*?)\\end\{\1\}",
    re.MULTILINE,
)
_RE_TEX_LIST_ENV = re.compile(
    r"\\begin\{(itemize|enumerate)\}([\s\S]*?)\\end\{\1\}",
    re.MULTILINE,
)


def _clean_str(s: str) -> str:
    """Strip pipeline artifacts: Python string markers and @@CMD:: placeholders."""
    s = _RE_PY_STR.sub("", s.strip())
    s = _RE_CMD_ARTIFACT.sub("", s)
    return s.strip()


def _read_tex_group(text: str, start: int) -> tuple[str | None, int]:
    if start >= len(text) or text[start] != "{":
        return None, start
    depth = 0
    escaped = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:idx], idx + 1
    return None, start


def _unwrap_tex_color_commands(text: str) -> str:
    needle = "\\color"
    out: list[str] = []
    pos = 0
    while True:
        start = text.find(needle, pos)
        if start < 0:
            out.append(text[pos:])
            break
        out.append(text[pos:start])
        cursor = start + len(needle)
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        color, cursor_after_color = _read_tex_group(text, cursor)
        if color is None:
            out.append(needle)
            pos = start + len(needle)
            continue
        cursor = cursor_after_color
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        body, cursor_after_body = _read_tex_group(text, cursor)
        if body is None:
            pos = cursor
            continue
        if out and out[-1].endswith("{") and cursor_after_body < len(text) and text[cursor_after_body] == "}":
            out[-1] = out[-1][:-1]
            cursor_after_body += 1
        out.append(body)
        pos = cursor_after_body
    return "".join(out)


LATEX_N_COMMANDS = {
    "nabla",
    "natural",
    "neg",
    "neq",
    "ne",
    "ni",
    "not",
    "notin",
    "notni",
    "notag",
    "nonumber",
    "nolimits",
    "newline",
    "newcommand",
    "renewcommand",
    "newenvironment",
    "renewenvironment",
    "newtheorem",
    "newpage",
    "noindent",
    "null",
    "nu",
    "nwarrow",
    "nearrow",
    "nleftarrow",
    "nrightarrow",
    "nleftrightarrow",
    "nLeftarrow",
    "nRightarrow",
    "nLeftrightarrow",
    "nless",
    "ngtr",
    "nleq",
    "ngeq",
    "nleqq",
    "ngeqq",
    "nleqslant",
    "ngeqslant",
    "nsubset",
    "nsupset",
    "nsubseteq",
    "nsupseteq",
    "nsubseteqq",
    "nsupseteqq",
    "nmid",
    "nparallel",
    "ncong",
    "nprec",
    "nsucc",
    "npreceq",
    "nsucceq",
    "nexists",
    "nVDash",
    "nVdash",
    "nvDash",
    "nvdash",
}


def _normalize_tex_for_display(text: str) -> str:
    """Convert common TeX text environments into MathText-friendly text.

    This is intentionally a presentation-layer conversion. Canonical pipeline
    cache keeps the original TeX; API consumers get text that the existing
    KaTeX fragment renderer can display without exposing raw item/equation
    environments.
    """
    def decode_escaped_newlines(value: str) -> str:
        out: list[str] = []
        i = 0
        while i < len(value):
            if value.startswith("\\n", i):
                j = i + 2
                command = "n"
                while j < len(value) and value[j].isalpha():
                    command += value[j]
                    j += 1
                if command not in LATEX_N_COMMANDS:
                    out.append("\n")
                    i += 2
                    continue
            out.append(value[i])
            i += 1
        return "".join(out)

    text = decode_escaped_newlines(text)
    if not text or not any(token in text for token in (
        "\\begin{", "\\end{", "\\item", "\\bf", "\\textbf",
        "\\label{", "\\ref{", "\\eqref{", "\\color{", "\\nonumber",
    )):
        return text

    def display_repl(match: re.Match) -> str:
        body = _RE_TEX_LABEL.sub("", match.group(2))
        body = re.sub(r"\\nonumber\b", "", body).strip()
        if not body:
            return ""
        if match.group(1).startswith(("align", "eqnarray")):
            body = "\\begin{aligned}\n" + body + "\n\\end{aligned}"
        return f"\n\n$$\n{body}\n$$\n\n"

    def split_tex_items(body: str) -> list[str]:
        parts: list[str] = []
        current: list[str] = []
        pos = 0
        current_label = ""
        for match in re.finditer(r"\\item(?:\s*\[([^\]]*)\])?", body):
            if current:
                prefix = f"{current_label} " if current_label else ""
                parts.append((prefix + "".join(current)).strip())
                current = []
            current_label = (match.group(1) or "").strip()
            pos = match.end()
            next_match = re.search(r"\\item(?:\s*\[[^\]]*\])?", body[pos:])
            end = pos + next_match.start() if next_match else len(body)
            current.append(body[pos:end])
            pos = end
        if current:
            prefix = f"{current_label} " if current_label else ""
            parts.append((prefix + "".join(current)).strip())
        if not parts and body.strip():
            parts.append(body.strip())
        return [part for part in parts if part]

    def list_repl(match: re.Match) -> str:
        ordered = match.group(1) == "enumerate"
        items = split_tex_items(match.group(2))
        lines = []
        for idx, item in enumerate(items, 1):
            prefix = f"{idx}. " if ordered else "- "
            lines.append(prefix + item.strip())
        return "\n\n" + "\n".join(lines) + "\n\n"

    previous = None
    while previous != text:
        previous = text
        text = _RE_TEX_DISPLAY_ENV.sub(display_repl, text)
        text = _RE_TEX_LIST_ENV.sub(list_repl, text)

    text = _RE_TEX_LABEL.sub("", text)
    text = re.sub(r"\\nonumber\b", "", text)
    text = _RE_TEX_EQREF.sub(lambda m: f"({m.group(1).split(':')[-1]})", text)
    text = _RE_TEX_REF.sub(lambda m: m.group(1).split(":")[-1], text)
    text = _unwrap_tex_color_commands(text)
    text = _RE_TEX_COLOR_BF_GROUP.sub(r"\1", text)
    text = _RE_TEX_COLOR_BF.sub(r"\1", text)
    previous = None
    while previous != text:
        previous = text
        text = _RE_TEX_COLOR_GROUP.sub(r"\1", text)
        text = _RE_TEX_COLOR.sub(r"\1", text)

    def font_repl(match: re.Match) -> str:
        body = match.group(1).strip()
        if re.fullmatch(r"[A-Za-z]+", body):
            return f"\\mathbf{{{body}}}"
        return body

    text = _RE_TEX_TEXTBF.sub(font_repl, text)
    text = _RE_TEX_BF_GROUP.sub(font_repl, text)
    text = _RE_TEX_EMPH.sub(r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_text(val) -> str:
    """Coerce a pipeline value to a plain string."""
    if not val:
        return ""
    if isinstance(val, str):
        return _normalize_tex_for_display(_clean_str(val))
    if isinstance(val, dict):
        raw = val.get("text") or val.get("text_normalized") or val.get("original_form") or ""
        return _normalize_tex_for_display(_clean_str(raw)) if isinstance(raw, str) else ""
    return _normalize_tex_for_display(_clean_str(str(val)))


def _extract_list(val) -> list:
    """Coerce a pipeline value to a list of strings."""
    if not val:
        return []
    if isinstance(val, list):
        return [_extract_text(item) if isinstance(item, (dict, list)) else _normalize_tex_for_display(str(item)) for item in val if item]
    if isinstance(val, dict):
        # e.g. {"text": ["group G"], "text_normalized": [...]}
        inner = val.get("text") or []
        if isinstance(inner, list):
            return [str(s) for s in inner if s]
        if isinstance(inner, str) and inner:
            return [inner]
    return []


def _as_item_list(payload, item_keys):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in item_keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
        if payload and all(isinstance(value, dict) for value in payload.values()):
            return list(payload.values())
    return None


def _first_value(data, keys, default=""):
    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return value
    return default


def _normalize_nodes(node_payload):
    node_list = _as_item_list(node_payload, ("nodes", "node_list", "node_dict"))
    if node_list is None:
        raise ValueError("Unsupported node JSON structure")
    if isinstance(node_payload, dict) and not any(key in node_payload for key in ("nodes", "node_list", "node_dict")):
        node_list = [
            {**value, "global_id": value.get("global_id") or key}
            for key, value in node_payload.items()
        ]

    out = []
    for i, n in enumerate(node_list or []):
        if not isinstance(n, dict):
            raise ValueError(f"Node at index {i} is not an object")
        remark = n.get("remark") or {}
        raw_source_text = n.get("source_text")
        source_statement = n.get("source_original_form") or n.get("source_statement")
        if not source_statement:
            source_statement = n.get("original_form")
        if not source_statement and isinstance(remark, dict):
            source_statement = remark.get("original_form")
        if not source_statement:
            source_statement = n.get("content")
        if not source_statement:
            source_statement = raw_source_text
        if isinstance(source_statement, dict):
            source_statement = source_statement.get("original_form") or source_statement.get("text") or ""
        source_statement = _clean_str(source_statement) if isinstance(source_statement, str) else ""
        source_text = _extract_text(raw_source_text) if raw_source_text else _extract_text(source_statement)
        if "title_zh" in n or "title_en" in n:
            locator = n.get("locator") if isinstance(n.get("locator"), dict) else {}
            out.append({
                "id": n.get("id") if isinstance(n.get("id"), int) else i,
                "global_id": n.get("global_id") or n.get("parent_global_id") or "",
                "node_type": n.get("node_type", ""),
                "title_zh": _clean_str(str(n.get("title_zh") or n.get("title_en") or "")),
                "title_en": _clean_str(str(n.get("title_en") or n.get("title_zh") or "")),
                "label": n.get("label", ""),
                "content": _normalize_tex_for_display(_clean_str(str(n.get("content") or ""))),
                "statement_form": n.get("statement_form", ""),
                "subject": _extract_list(n.get("subject")),
                "conditions": _extract_list(n.get("conditions")),
                "conclusions": _extract_list(n.get("conclusions")),
                "proof": _extract_text(n.get("proof")),
                "formalization_guidance": n.get("formalization_guidance"),
                "node_index_in_doc": n.get("node_index_in_doc", locator.get("node_index_in_doc", i)),
                "surface_anchor": n.get("surface_anchor"),
                "source_text": source_text,
                "source_statement": source_statement,
                "source_span": n.get("source_span"),
                "source_file": n.get("source_file"),
                "tex_label_key": n.get("tex_label_key"),
                "tex_env_name": n.get("tex_env_name"),
                "coverage_recovered": bool(n.get("coverage_recovered")),
                "analysis_status": n.get("analysis_status"),
                "repair_status": n.get("repair_status"),
            })
            continue
        title_obj = n.get("title", {})
        zh = _clean_str(_title_str(title_obj) if isinstance(title_obj, dict) else str(title_obj or ""))
        en_raw = (title_obj.get("english") or zh) if isinstance(title_obj, dict) else zh
        en = _clean_str(en_raw) if isinstance(en_raw, str) else zh
        content = _normalize_tex_for_display(_clean_str(n.get("content") or ""))
        if not content:
            remark = n.get("remark") or {}
            content = _extract_text(remark.get("original_form") or remark)
        # Preserve global_id so _normalize_edges can match edges by id
        global_id = n.get("global_id") or n.get("parent_global_id") or ""
        locator = n.get("locator") if isinstance(n.get("locator"), dict) else {}
        out.append({
            "id": i,
            "global_id": global_id,
            "node_type": n.get("node_type", ""),
            "title_zh": zh,
            "title_en": en,
            "label": n.get("label", ""),
            "content": content,
            "statement_form": n.get("statement_form", ""),
            "subject": _extract_list(n.get("subject")),
            "conditions": _extract_list(n.get("conditions")),
            "conclusions": _extract_list(n.get("conclusions")),
            "proof": _extract_text(n.get("proof")),
            "formalization_guidance": n.get("formalization_guidance"),
            "node_index_in_doc": locator.get("node_index_in_doc", i),
            "surface_anchor": n.get("surface_anchor"),
            "source_text": source_text,
            "source_statement": source_statement,
            "source_span": n.get("source_span"),
            "source_file": n.get("source_file"),
            "tex_label_key": n.get("tex_label_key"),
            "tex_env_name": n.get("tex_env_name"),
            "coverage_recovered": bool(n.get("coverage_recovered")),
            "analysis_status": n.get("analysis_status"),
            "repair_status": n.get("repair_status"),
        })
    return out


def _normalize_edges(edge_payload, nodes, include_warnings=False):
    edge_list = _as_item_list(edge_payload, ("edges", "edge_list", "relations"))
    if edge_list is None:
        raise ValueError("Unsupported edge JSON structure")

    # Primary: match by global_id (what build_relations actually writes)
    gid_map: dict[str, int] = {}
    id_map: dict[str, int] = {}
    for nd in nodes:
        id_map[str(nd["id"])] = nd["id"]
        if nd.get("global_id"):
            global_id = str(nd["global_id"])
            if global_id in gid_map:
                raise ValueError(f"Duplicate node global_id: {global_id}")
            gid_map[global_id] = nd["id"]

    # Fallback: match by title (in case global_id is missing)
    title_map: dict[str, int] = {}
    for nd in nodes:
        if nd["title_zh"] and nd["title_zh"] not in title_map:
            title_map[nd["title_zh"]] = nd["id"]
        if nd["title_en"] and nd["title_en"] not in title_map:
            title_map[nd["title_en"]] = nd["id"]

    out = []
    warnings = []
    node_ids = {nd["id"] for nd in nodes}
    for i, e in enumerate(edge_list or []):
        if not isinstance(e, dict):
            warnings.append(f"Skipped edge {i}: edge is not an object")
            continue
        src_raw = _first_value(e, ("from", "source", "source_id", "出发节点", "起始节点"))
        dst_raw = _first_value(e, ("to", "target", "target_id", "到达节点", "终止节点"))
        src = str(src_raw).strip()
        dst = str(dst_raw).strip()
        fid = src_raw if isinstance(src_raw, int) and src_raw in node_ids else None
        tid = dst_raw if isinstance(dst_raw, int) and dst_raw in node_ids else None
        if fid is None:
            fid = gid_map.get(src) if src in gid_map else id_map.get(src, title_map.get(src))
        if tid is None:
            tid = gid_map.get(dst) if dst in gid_map else id_map.get(dst, title_map.get(dst))
        if fid is not None and tid is not None and fid != tid:
            out.append({
                "from": fid,
                "to": tid,
                "label": _first_value(e, ("label", "relation", "name", "关系名称", "关系")),
                "description": _first_value(e, ("description", "explanation", "reason", "关系解释", "理由")),
                "strength": _first_value(e, ("strength", "关系强度")),
            })
        else:
            warnings.append(f"Skipped edge {i}: endpoints could not be matched ({src} -> {dst})")
    return (out, warnings) if include_warnings else out


def _snapshot_partial(job: dict, state: dict):
    """Save whatever nodes exist so far; shown on error screen."""
    raw_nodes = state.get("node_list") or state.get("node_dict")
    if isinstance(raw_nodes, dict):
        raw_nodes = list(raw_nodes.values())
    if raw_nodes:
        nodes = _normalize_nodes(raw_nodes)
        job["partial"] = {"nodes": nodes, "edges": []}


def _looks_like_tex_source(text: str, filename: str = "") -> bool:
    name = (filename or "").lower()
    if name.endswith(".tex"):
        return True
    sample = text[:20000]
    tex_markers = (
        "\\documentclass",
        "\\begin{document}",
        "\\begin{theorem}",
        "\\begin{lemma}",
        "\\begin{definition}",
        "\\newtheorem",
    )
    return any(marker in sample for marker in tex_markers)


def _safe_upload_filename(filename: str, source_format: str) -> str:
    """Return a Windows-safe temporary filename for uploaded source text."""
    raw_name = (filename or "").replace("\\", "/").split("/")[-1].strip()
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw_name).strip(" .")
    if not safe_name:
        safe_name = "input.tex" if source_format == "tex" else "input.md"

    suffix = Path(safe_name).suffix.lower()
    if suffix not in {".md", ".txt", ".tex"}:
        suffix = ".tex" if source_format == "tex" else ".md"
        safe_name = f"{Path(safe_name).stem or 'input'}{suffix}"
    return safe_name


def _tex_compile_startup_options():
    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return creationflags, startupinfo


def _short_log(path: Path, limit: int = 4000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return text[-limit:] if len(text) > limit else text


def _tex_error_summary(log_path: Path) -> str:
    """Return the actionable LaTeX error without exposing a full compiler log in the UI."""
    log = _short_log(log_path, limit=24000)
    if not log:
        return "LaTeX compilation failed"
    patterns = (
        r"! LaTeX Error: ([^\r\n]+)",
        r"! Package [^\r\n]+ Error: ([^\r\n]+)",
        r"! ([^\r\n]+)",
    )
    for pattern in patterns:
        matches = re.findall(pattern, log)
        if matches:
            return re.sub(r"\s+", " ", matches[-1]).strip()[:500]
    return "LaTeX compilation failed; see the compile log for details"


def _tex_compile_has_errors(log_path: Path) -> bool:
    if not log_path.exists():
        return False
    log = log_path.read_text(encoding="utf-8", errors="replace")
    # XeLaTeX may recover and emit a syntactically valid but visibly damaged PDF.
    # Do not publish that partial output as the source original.
    return bool(re.search(r"^! ", log, flags=re.MULTILINE))


def _copy_bundled_tex_resources(source_text: str, out_dir: Path) -> list[str]:
    class_names = re.findall(
        r"\\documentclass(?:\s*\[[^\]]*\])?\s*\{\s*([^{}]+?)\s*\}",
        source_text,
    )
    copied: list[str] = []
    for class_name in class_names:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "", class_name)
        if not safe_name:
            continue
        template = _TEX_TEMPLATE_ROOT / f"{safe_name}.cls"
        target = out_dir / template.name
        if template.is_file() and not target.exists():
            shutil.copy2(template, target)
            copied.append(template.name)
        companion_dir = _TEX_TEMPLATE_ROOT / safe_name
        if companion_dir.is_dir():
            for item in companion_dir.iterdir():
                target = out_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                elif not target.exists():
                    shutil.copy2(item, target)
            copied.append(f"{safe_name}/")
    return copied


def _is_valid_pdf(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size < 32:
            return False
        with path.open("rb") as pdf:
            if pdf.read(5) != b"%PDF-":
                return False
            pdf.seek(max(0, path.stat().st_size - 2048))
            return b"%%EOF" in pdf.read()
    except OSError:
        return False


def _compile_tex_source_pdf(job_id: str, source_text: str, filename: str) -> dict:
    out_dir = _source_pdf_dir(job_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_upload_filename(filename, "tex")
    if not safe_name.lower().endswith(".tex"):
        safe_name = f"{Path(safe_name).stem or 'input'}.tex"
    tex_path = out_dir / safe_name
    log_path = out_dir / "compile.log"
    tex_path.write_text(source_text, encoding="utf-8")
    bundled_resources = _copy_bundled_tex_resources(source_text, out_dir)

    meta = {
        "status": "compiling",
        "available": False,
        "error": None,
        "pdf_path": str(out_dir / f"{tex_path.stem}.pdf"),
        "source_path": str(tex_path),
        "log_path": str(log_path),
        "pdf_url": f"/api/v2/source-pdf/{job_id}",
        "compile_log_url": f"/api/v2/source-pdf/{job_id}/compile-log",
    }
    latexmk = shutil.which("latexmk")
    xelatex = shutil.which("xelatex")
    creationflags, startupinfo = _tex_compile_startup_options()

    def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=str(out_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )

    try:
        logs: list[str] = []
        if bundled_resources:
            logs.append("$ bundled TeX resources\n" + ", ".join(bundled_resources))
        proc: subprocess.CompletedProcess[str] | None = None
        if latexmk:
            proc = run_command([
                latexmk,
                "-xelatex",
                "-interaction=nonstopmode",
                "-synctex=1",
                tex_path.name,
            ])
            logs.append("$ latexmk\n" + (proc.stdout or "") + "\n" + (proc.stderr or ""))
        # latexmk can leave a zero-byte or partial PDF behind after an error.
        # Only a structurally valid file should suppress the XeLaTeX fallback.
        if not _is_valid_pdf(Path(meta["pdf_path"])) and xelatex:
            xelatex_cmd = [
                xelatex,
                "-interaction=nonstopmode",
                "-synctex=1",
                tex_path.name,
            ]
            proc = run_command(xelatex_cmd)
            logs.append("$ xelatex pass 1\n" + (proc.stdout or "") + "\n" + (proc.stderr or ""))
            if Path(meta["pdf_path"]).exists():
                proc2 = run_command(xelatex_cmd)
                logs.append("$ xelatex pass 2\n" + (proc2.stdout or "") + "\n" + (proc2.stderr or ""))
        if not latexmk and not xelatex:
            meta["error"] = "latexmk/xelatex not found"
            meta["status"] = "failed"
            log_path.write_text(meta["error"], encoding="utf-8")
            return meta
        log_path.write_text("\n\n".join(logs), encoding="utf-8", errors="replace")
        pdf_path = Path(meta["pdf_path"])
        if _is_valid_pdf(pdf_path) and not _tex_compile_has_errors(log_path):
            meta["available"] = True
            meta["status"] = "ready"
        else:
            meta["status"] = "failed"
            meta["error"] = _tex_error_summary(log_path)
    except subprocess.TimeoutExpired:
        meta["status"] = "failed"
        meta["error"] = "LaTeX compilation timed out"
        log_path.write_text(meta["error"], encoding="utf-8")
    except Exception as exc:
        meta["status"] = "failed"
        meta["error"] = str(exc)
        log_path.write_text(meta["error"], encoding="utf-8")
    return meta


def _compile_agent_source_pdf(job_id: str, source_text: str, filename: str) -> None:
    try:
        meta = _compile_tex_source_pdf(job_id, source_text, filename)
    except Exception as exc:
        meta = _pending_source_pdf_meta(job_id)
        meta.update({"status": "failed", "error": str(exc)})
    job = _jobs.get(job_id)
    if not job:
        return
    job["source_pdf"] = meta
    result = job.get("result")
    if isinstance(result, dict):
        result["source_pdf"] = _public_source_pdf_meta(meta)
    if (
        not _desktop_legacy_auth
        and job.get("_user_id") is not None
        and not _persist_job_with_files(job, "done")
    ):
        _mark_persistence_error(job)
        return
    _update_history_source_pdf(job_id, meta)


def _node_locator_terms(node: dict) -> list[str]:
    raw_terms = [
        node.get("label"),
        node.get("title_zh"),
        node.get("title_en"),
        node.get("tex_label_key"),
        node.get("content"),
        node.get("source_text"),
    ]
    terms: list[str] = []
    for raw in raw_terms:
        if not raw:
            continue
        text = str(raw)
        text = re.sub(r"\\(?:begin|end|label|tag)\s*\{[^{}]*\}", " ", text)
        text = re.sub(r"\\[A-Za-z@]+", " ", text)
        text = re.sub(r"[{}$]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if 4 <= len(text) <= 120 and text not in terms:
            terms.append(text)
    return terms[:5]


def _node_source_key(node: dict) -> str:
    return str(node.get("tex_label_key") or node.get("label") or "").strip()


def _node_original_statement(node: dict) -> str:
    raw = node.get("source_statement")
    if not raw:
        raw = node.get("original_form")
    if not raw and isinstance(node.get("remark"), dict):
        raw = node["remark"].get("original_form")
    if not raw:
        raw = node.get("content")
    if not raw:
        raw = node.get("source_text")
    if isinstance(raw, dict):
        raw = raw.get("original_form") or raw.get("text") or ""
    return _clean_str(raw) if isinstance(raw, str) else ""


def _tex_statement_terms(meta: dict, node: dict) -> list[str]:
    """Derive the printed statement title from the exact TeX source key."""
    source_path = Path(str(meta.get("source_path") or ""))
    source_key = _node_source_key(node)
    if not source_key:
        return []

    marker = "{" + source_key + "}"
    source = _node_original_statement(node)
    if marker not in source:
        if not source_path.exists():
            return []
        source = source_path.read_text(encoding="utf-8", errors="replace")
    marker_index = source.find(marker)
    if marker_index < 0:
        return []
    begin_index = source.rfind("\\begin{", 0, marker_index)
    if begin_index < 0:
        return []
    header_end = source.find("\n", begin_index)
    header = source[begin_index:header_end if header_end >= 0 else marker_index]

    candidates: list[str] = []
    optional_title = re.search(r"\[([^\]]+)\]", header)
    if optional_title:
        candidates.append(optional_title.group(1))
    for value in re.findall(r"\{([^{}]+)\}", header):
        if value != source_key and not value.isalpha():
            candidates.append(value)

    terms: list[str] = []
    for value in candidates:
        text = re.sub(r"\\[A-Za-z@]+", " ", value)
        text = re.sub(r"[{}$]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if 3 <= len(text) <= 120 and text not in terms:
            terms.append(text)
    return terms


def _source_line_for_node(source_path: Path, node: dict) -> int | None:
    if not source_path.exists():
        return None
    source = source_path.read_text(encoding="utf-8", errors="replace")
    span = node.get("source_span") if isinstance(node.get("source_span"), dict) else None
    if span:
        try:
            start = int(span.get("start", 0))
        except Exception:
            start = -1
        if start >= 0:
            return source.count("\n", 0, min(start, len(source))) + 1

    # Some Agent imports only preserve the environment key in the display label.
    # Source spans remain authoritative when they are available above.
    label = _node_source_key(node)
    if not label:
        return None
    env_name = str(node.get("tex_env_name") or "").strip()
    marker = "{" + label + "}"
    search_from = 0
    while True:
        marker_index = source.find(marker, search_from)
        if marker_index < 0:
            return None
        env_index = source.rfind("\\begin{", 0, marker_index)
        if env_index >= 0:
            env_end = source.find("}", env_index)
            found_env = source[env_index + len("\\begin{"):env_end] if env_end >= 0 else ""
            if not env_name or found_env == env_name:
                return source.count("\n", 0, env_index) + 1
        search_from = marker_index + len(marker)


def _synctex_page(meta: dict, node: dict) -> int | None:
    pdf_path = Path(str(meta.get("pdf_path") or ""))
    source_path = Path(str(meta.get("source_path") or ""))
    line = _source_line_for_node(source_path, node)
    if not line or not pdf_path.exists() or not source_path.exists():
        return None
    synctex = shutil.which("synctex")
    if not synctex:
        return None
    command = [synctex, "view", "-i", f"{line}:1:{source_path}", "-o", str(pdf_path)]
    creationflags, startupinfo = _tex_compile_startup_options()
    try:
        proc = subprocess.run(
            command,
            cwd=str(pdf_path.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
    except Exception:
        return None
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    match = re.search(r"\bPage:\s*(\d+)", text)
    return int(match.group(1)) if match else None


def _partial_result_from_state(state: dict):
    raw_nodes = state.get("node_list") or state.get("node_dict")
    if isinstance(raw_nodes, dict):
        raw_nodes = list(raw_nodes.values())
    if not raw_nodes:
        return None
    return {"nodes": _normalize_nodes(raw_nodes), "edges": []}


def _execute_pipeline_worker(payload: dict, emit):
    job_id = payload["job_id"]
    md_path = payload["md_path"]
    llm = payload["llm"]
    source_format = payload.get("source_format", "auto")
    active_stage = None
    active_stage_label = None

    try:
        source_pdf = payload.get("source_pdf")
        if source_format == "tex" and payload.get("source_markdown") and not source_pdf:
            active_stage = "compile_source_pdf"
            active_stage_label = "编译 PDF 原文"
            emit({
                "type": "source_pdf_start",
                "stage": active_stage,
                "stage_label": active_stage_label,
            })
            source_pdf = _compile_tex_source_pdf(
                job_id,
                payload["source_markdown"],
                payload.get("filename") or "input.tex",
            )
            emit({"type": "source_pdf", "source_pdf": source_pdf})
            active_stage = None
            active_stage_label = None

        job_dir = Path(md_path).parent
        ctx = PipelineContext(
            file_path=md_path,
            output_node_path=str(job_dir / "nodes.json"),
            output_edge_path=str(job_dir / "edges.json"),
            api_url=llm["api_url"],
            model_name=llm["model_name"],
            api_key=llm["api_key"],
            embedding_api_url=llm["embedding_url"],
            embedding_api_key=llm["embedding_api_key"],
            embedding_model_name=llm["embedding_model"],
            enable_analysis=True,
            source_format=source_format,
            checkpoint=1,
            cache_policy="minimal",
        )

        def _on_stage_start(stage, index, total, _state):
            nonlocal active_stage, active_stage_label
            active_stage = stage.key
            active_stage_label = stage.label
            emit({
                "type": "stage_start",
                "stage": stage.key,
                "stage_label": stage.label,
                "stage_index": index,
                "total_stages": total,
            })

        def _on_stage_complete(stage, index, total, stage_state):
            emit({
                "type": "stage_complete",
                "stage": stage.key,
                "stage_index": index,
                "total_stages": total,
                "partial": _partial_result_from_state(stage_state),
            })

        state = execute_fixed_pipeline(
            ctx,
            resume_from_cache=bool(payload.get("resume")),
            edge_output_mode="structured",
            relation_prompt_profile="graph",
            experimental_logic_ir=bool(payload.get("experimental_logic_ir")),
            on_stage_start=_on_stage_start,
            on_stage_complete=_on_stage_complete,
        )

        nodes = _normalize_nodes(state.get("node_list", []))
        edges = _normalize_edges(state.get("edge_list", []), nodes)
        emit({
            "type": "done",
            "source_pdf": source_pdf,
            "result": {
                "nodes": nodes,
                "edges": edges,
                "latex_macros": payload.get("latex_macros") or {},
                "source_pdf": _public_source_pdf_meta(source_pdf),
                "warnings": list(state.get("pipeline_warnings") or []),
                "quality_summary": state.get("quality_summary")
                or {
                    "status": "ok",
                    "degraded_stage_count": 0,
                    "degraded_node_count": 0,
                    "ignored_protected_field_count": 0,
                },
            },
        })
    except Exception as exc:
        secrets_to_redact = (llm.get("api_key"), llm.get("embedding_api_key"))
        error_detail = _redact_error_text(traceback.format_exc(), secrets_to_redact)
        error_message = _redact_error_text(exc, secrets_to_redact)
        presentation = _classify_job_error(
            exc,
            stage=active_stage,
            stage_label=active_stage_label,
        )
        emit({
            "type": "error",
            "error": error_message,
            "error_detail": error_detail,
            "error_code": presentation["error_code"],
            "error_title": presentation["error_title"],
            "error_user_message": presentation["error"],
        })
        print(error_detail)


def _pipeline_process_main(payload: dict, event_queue, attempt_token: str):
    def emit(event):
        event_queue.put({"attempt_token": attempt_token, **event})

    _execute_pipeline_worker(payload, emit)


def _mark_persistence_error(job: dict, *, preserve_status: bool = False) -> None:
    """将数据库失败转换为稳定公开状态，不暴露驱动异常、SQL 或运行密钥。"""
    if not preserve_status:
        job["status"] = "error"
        job["result"] = None
    job["_persistence_error"] = True
    job["error"] = "Task progress persistence failed"
    job["error_code"] = "persistence_error"
    job["error_title"] = "任务进度保存失败"
    job["error_user_message"] = "任务进度未能安全保存，请稍后重试。"
    job.pop("error_detail", None)


def _persist_job_with_files(
    job: dict,
    status: str,
    user_id: int | None = None,
) -> bool:
    """先持久化受控文件，再提交同一状态的 MySQL 快照。"""
    owner_id = user_id if user_id is not None else job.get("_user_id")
    if owner_id is None:
        return False
    result = job.get("result") if status == "done" else job.get("partial")
    result = result if isinstance(result, dict) else {}
    try:
        _capacity_limits.validate_history_payload(
            result.get("nodes") if isinstance(result.get("nodes"), list) else [],
            result.get("edges") if isinstance(result.get("edges"), list) else [],
            job.get("source_markdown"),
            job.get("source_pdf"),
        )
        local_bytes = sum(
            path.stat().st_size
            for root in (
                _persistent_job_dir(str(job["job_id"])),
                _source_pdf_dir(str(job["job_id"])),
            )
            if root.is_dir()
            for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
        if not _desktop_legacy_auth:
            _capacity_limits.ensure_user_storage_capacity(
                _learning_repository,
                int(owner_id),
                local_bytes,
                replacing_history_id=str(job["job_id"]),
            )
    except CapacityExceeded as exc:
        job["_capacity_error"] = (exc.code, exc.http_status)
        return False
    if not _desktop_legacy_auth and _object_storage is not None:
        try:
            stored_version = _object_storage.upload_version(
                int(owner_id),
                str(job["job_id"]),
                _persistent_job_dir(str(job["job_id"])),
                _source_pdf_dir(str(job["job_id"])),
            )
        except (ObjectStorageError, OSError, ValueError):
            return False
        job["_object_storage_prefix"] = stored_version.prefix
        return _upsert_job_history(
            job, status, int(owner_id), stored_version=stored_version
        )
    return _upsert_job_history(job, status, int(owner_id))


def _persist_job_state(job: dict, status: str) -> bool:
    if not job.get("_history_persisted"):
        return True
    if _persist_job_with_files(job, status):
        return True
    _mark_persistence_error(job)
    return False


def _apply_pipeline_event(job_id: str, attempt_token: str | None, event: dict):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return False
        if attempt_token is not None and job.get("_attempt_token") != attempt_token:
            return False
        event_type = event.get("type")
        if job.get("_persistence_error"):
            # 数据库已失去可信同步后忽略后续 worker 事件，避免重新伪报 done。
            return event_type in {"done", "error"}
        stage_defs = _job_stage_defs(job)
        if event_type == "source_pdf_start":
            job["stage"] = event.get("stage")
            job["stage_label"] = event.get("stage_label")
        elif event_type == "source_pdf":
            job["source_pdf"] = event.get("source_pdf")
        elif event_type == "stage_start":
            index = int(event.get("stage_index") or 0)
            job["stage"] = event.get("stage")
            job["stage_label"] = event.get("stage_label")
            job["stage_index"] = index
            job["total_stages"] = int(event.get("total_stages") or len(stage_defs))
            job["stages_done"] = [key for key, _ in stage_defs[:index]]
        elif event_type == "stage_complete":
            key = event.get("stage")
            if key and key not in job["stages_done"]:
                job["stages_done"].append(key)
            if event.get("partial"):
                job["partial"] = event["partial"]
        elif event_type == "done":
            job["status"] = "done"
            job["stage"] = None
            job["stage_label"] = None
            job["stage_index"] = max(0, len(stage_defs) - 1)
            job["total_stages"] = len(stage_defs)
            job["stages_done"] = [key for key, _ in stage_defs]
            job["result"] = event.get("result")
            if event.get("source_pdf") is not None:
                job["source_pdf"] = event.get("source_pdf")
            job["error"] = None
            job.pop("error_detail", None)
            job.pop("error_code", None)
            job.pop("error_title", None)
            job.pop("error_user_message", None)
            _persist_job_state(job, "done")
        elif event_type == "error":
            job["status"] = "error"
            job["error"] = event.get("error") or "Pipeline worker failed"
            job["error_detail"] = event.get("error_detail") or ""
            presentation = _classify_job_error(
                RuntimeError(job["error"]),
                stage=job.get("stage"),
                stage_label=job.get("stage_label"),
            )
            job["error_code"] = event.get("error_code") or presentation["error_code"]
            job["error_title"] = event.get("error_title") or presentation["error_title"]
            job["error_user_message"] = (
                event.get("error_user_message") or presentation["error"]
            )
            _persist_job_state(job, "error")
        if event_type in {"source_pdf_start", "source_pdf", "stage_start", "stage_complete"}:
            _persist_job_state(job, job.get("status") or "running")
        return event_type in {"done", "error"}


def _monitor_pipeline_process(job_id: str, attempt_token: str, process, event_queue):
    terminal_event = False
    while True:
        try:
            event = event_queue.get(timeout=0.2)
        except queue_module.Empty:
            if not process.is_alive():
                break
            continue
        if event.get("attempt_token") != attempt_token:
            continue
        terminal_event = _apply_pipeline_event(job_id, attempt_token, event) or terminal_event
        if terminal_event:
            break

    process.join(timeout=5)
    with _jobs_lock:
        runtime = _job_runtimes.get(job_id)
        job = _jobs.get(job_id)
        if runtime and runtime.get("attempt_token") == attempt_token:
            _job_runtimes.pop(job_id, None)
        paused = bool(runtime and runtime.get("pause_requested"))
        if (
            job
            and job.get("_attempt_token") == attempt_token
            and job.get("status") == "running"
            and not terminal_event
            and not paused
        ):
            job["status"] = "error"
            raw_error = f"Pipeline worker exited unexpectedly (exit code {process.exitcode})"
            presentation = _classify_job_error(
                RuntimeError(raw_error),
                stage=job.get("stage"),
                stage_label=job.get("stage_label"),
            )
            job["error"] = raw_error
            job["error_code"] = presentation["error_code"]
            job["error_title"] = presentation["error_title"]
            job["error_user_message"] = presentation["error"]
            _persist_job_state(job, "error")
    try:
        event_queue.close()
    except Exception:
        pass


def _job_worker_payload(job: dict, *, resume: bool):
    return {
        "job_id": job["job_id"],
        "md_path": job["_md_path"],
        "llm": dict(job["_llm_config"]),
        "source_format": job.get("source_format", "auto"),
        "source_markdown": job.get("source_markdown"),
        "filename": job.get("filename"),
        "latex_macros": job.get("latex_macros") or {},
        "source_pdf": job.get("source_pdf"),
        "experimental_logic_ir": bool(job.get("_experimental_logic_ir")),
        "resume": resume,
    }


def _start_pipeline_attempt(job_id: str, *, resume: bool):
    with _jobs_lock:
        job = _jobs[job_id]
        attempt_token = uuid.uuid4().hex
        context = multiprocessing.get_context("spawn")
        event_queue = context.Queue()
        process = context.Process(
            target=_pipeline_process_main,
            args=(_job_worker_payload(job, resume=resume), event_queue, attempt_token),
            daemon=True,
            name=f"pipeline-{job_id[:8]}",
        )
        job["_attempt_token"] = attempt_token
        runtime = {
            "attempt_token": attempt_token,
            "process": process,
            "queue": event_queue,
            "pause_requested": False,
        }
        _job_runtimes[job_id] = runtime
        try:
            process.start()
        except Exception:
            _job_runtimes.pop(job_id, None)
            try:
                event_queue.close()
            except Exception:
                pass
            raise
        monitor = threading.Thread(
            target=_monitor_pipeline_process,
            args=(job_id, attempt_token, process, event_queue),
            daemon=True,
            name=f"pipeline-monitor-{job_id[:8]}",
        )
        runtime["monitor"] = monitor
        monitor.start()


def _run_pipeline(job_id: str, md_path: str, llm: dict, enable_analysis: bool, source_format: str = "auto"):
    """Synchronous compatibility entry used by focused tests and scripts."""
    job = _jobs[job_id]
    payload = {
        **_job_worker_payload(
            {
                **job,
                "job_id": job_id,
                "_md_path": md_path,
                "_llm_config": llm,
            },
            resume=False,
        ),
        "source_format": source_format,
    }
    _execute_pipeline_worker(
        payload,
        lambda event: _apply_pipeline_event(job_id, None, event),
    )


def _parse_bool_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@app.route("/api/v2/jobs", methods=["POST"])
def create_job():
    user = _current_user()
    if not _desktop_legacy_auth and not user:
        # 先鉴权再解析内容或创建目录，匿名请求不能留下任何服务器副作用。
        return jsonify({"error": "not authenticated"}), 401
    text_content = None
    filename = "input.md"

    if "file" in request.files:
        f = request.files["file"]
        filename = f.filename or "input.md"
        text_content = f.read().decode("utf-8", errors="replace")
        api_url = request.form.get("api_url", "")
        model_name = request.form.get("model_name", "")
        api_key = request.form.get("api_key", "")
        embedding_url = request.form.get("embedding_url", "")
        embedding_model = request.form.get("embedding_model", "")
        embedding_api_key = request.form.get("embedding_api_key", "")
        enable_analysis = request.form.get("enable_analysis", "false").lower() == "true"
        experimental_logic_ir = _parse_bool_flag(
            request.form.get("experimental_logic_ir", "false")
        )
    else:
        body = request.get_json(silent=True) or {}
        text_content = body.get("text", "")
        filename = body.get("filename", "input.md")
        api_url = body.get("api_url", "")
        model_name = body.get("model_name", "")
        api_key = body.get("api_key", "")
        embedding_url = body.get("embedding_url", "")
        embedding_model = body.get("embedding_model", "")
        embedding_api_key = body.get("embedding_api_key", "")
        enable_analysis = body.get("enable_analysis", False)
        experimental_logic_ir = _parse_bool_flag(
            body.get("experimental_logic_ir", False)
        )

    if not text_content:
        return jsonify({"error": "No content provided"}), 400
    encoded_input_bytes = len(str(text_content).encode("utf-8"))
    _capacity_limits.validate_upload_size(encoded_input_bytes)
    _capacity_limits.ensure_disk_capacity(_DATA_ROOT, encoded_input_bytes)
    if not _desktop_legacy_auth and user and not str(api_key or "").strip():
        # 浏览器只知道“已配置”状态；实际 Key 始终由服务端按当前用户解密。
        stored_config = _active_user_llm_config(user)
        if stored_config:
            api_url = stored_config["api_url"]
            model_name = stored_config["model_name"]
            api_key = stored_config["api_key"]
            embedding_url = stored_config.get("embedding_url", "")
            embedding_model = stored_config.get("embedding_model", embedding_model)
            embedding_api_key = stored_config.get("embedding_api_key", "")
    if not all([api_url, model_name, api_key]):
        return jsonify({"error": "Incomplete LLM config (api_url, model_name, api_key required)"}), 400
    embedding_model = (embedding_model or "").strip()
    if not embedding_model:
        return jsonify({"error": "Incomplete embedding config (embedding_model required)"}), 400
    embedding_url = (embedding_url or "").strip() or api_url
    embedding_api_key = (embedding_api_key or "").strip() or api_key

    source_format = "tex" if _looks_like_tex_source(text_content, filename) else "markdown"
    latex_macros, latex_macro_warnings = extract_latex_macros(text_content, filename)

    job_id = str(uuid.uuid4())
    stage_defs = _pipeline_stage_defs(experimental_logic_ir)
    if user:
        tmp_dir = str(_persistent_job_dir(job_id))
        Path(tmp_dir).mkdir(parents=True, exist_ok=False)
    else:
        tmp_dir = tempfile.mkdtemp()
    safe_name = _safe_upload_filename(filename, source_format)
    md_path = os.path.join(tmp_dir, safe_name)
    with open(md_path, "w", encoding="utf-8", newline="") as f:
        f.write(text_content)

    _jobs[job_id] = {
        "job_id": job_id,
        "status": "running",
        "filename": filename,
        "stage": None,
        "stage_label": None,
        "stage_index": 0,
        "total_stages": len(stage_defs),
        "stages_done": [],
        "result": None,
        "error": None,
        "source_markdown": text_content,
        "latex_macros": latex_macros,
        "latex_macro_warnings": latex_macro_warnings,
        "source_format": source_format,
        "source_pdf": None,
        "source": "pipeline",
        "_artifact_dir": tmp_dir,
        "_md_path": md_path,
        "_llm_config": {
            "api_url": api_url,
            "model_name": model_name,
            "api_key": api_key,
            "embedding_url": embedding_url,
            "embedding_model": embedding_model,
            "embedding_api_key": embedding_api_key,
        },
        "_enable_analysis": enable_analysis,
        "_experimental_logic_ir": experimental_logic_ir,
        "_stage_defs": stage_defs,
        "_user_id": int(user["id"]) if user else None,
        "_persistent_artifacts": bool(user),
        "_history_persisted": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    if not _desktop_legacy_auth:
        if not _persist_job_with_files(_jobs[job_id], "running", int(user["id"])):
            failed_job = _jobs.pop(job_id)
            try:
                _remove_job_artifacts(job_id, failed_job.get("_artifact_dir"))
            except (OSError, ValueError):
                pass
            if failed_job.get("_capacity_error"):
                code, http_status = failed_job["_capacity_error"]
                return jsonify({"error": code}), http_status
            return jsonify({"error": "Unable to persist job progress"}), 500

    _start_pipeline_attempt(job_id, resume=False)

    return jsonify({"job_id": job_id}), 202


def _read_uploaded_json(upload, label):
    if not upload or not upload.filename:
        raise ValueError(f"{label} file is required")
    if not upload.filename.lower().endswith(".json"):
        raise ValueError(f"{label} file must be JSON")
    try:
        return json.loads(upload.read().decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} file is not valid UTF-8 JSON: {exc}") from exc


def _set_cache_manifest_status(job: dict, status: str):
    artifact_dir = job.get("_artifact_dir")
    if not artifact_dir:
        return
    manifest_path = Path(artifact_dir) / "_stage_cache" / "manifest.json"
    if not manifest_path.is_file():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(manifest, dict):
        return
    manifest["status"] = status
    manifest.pop("error", None)
    atomic_write_json(str(manifest_path), manifest)


def _cleanup_job_stage_work(job: dict):
    artifact_dir = job.get("_artifact_dir")
    if not artifact_dir:
        return
    artifact_root = Path(artifact_dir).resolve()
    work_dir = (artifact_root / "_stage_work").resolve()
    if work_dir.parent == artifact_root and work_dir.name == "_stage_work":
        shutil.rmtree(work_dir, ignore_errors=True)
    cache_root = (artifact_root / "_stage_cache").resolve()
    if cache_root.parent == artifact_root and cache_root.name == "_stage_cache" and cache_root.is_dir():
        for temp_path in cache_root.rglob("*.tmp"):
            if temp_path.is_file():
                try:
                    temp_path.unlink()
                except OSError:
                    pass


def _terminate_pipeline_process(process):
    if not process:
        return
    try:
        alive = process.is_alive()
    except (AssertionError, ValueError):
        alive = False
    if not alive:
        return
    if os.name == "nt" and process.pid:
        subprocess.run(
            ["taskkill.exe", "/pid", str(process.pid), "/t", "/f"],
            capture_output=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    else:
        process.terminate()
    process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)


def _remove_job_artifacts(job_id: str, artifact_dir: str | os.PathLike | None):
    if artifact_dir:
        artifact_path = Path(artifact_dir).resolve()
        persistent_root = _job_storage_root().resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
        allowed = (
            artifact_path == _persistent_job_dir(job_id).resolve()
            or artifact_path.parent == temp_root
        )
        if not allowed or artifact_path in {persistent_root, temp_root}:
            raise ValueError(f"Refusing to remove unexpected job directory: {artifact_path}")
        if artifact_path.exists():
            shutil.rmtree(artifact_path)
    source_pdf_path = _source_pdf_dir(job_id).resolve()
    source_pdf_root = _SOURCE_PDF_ROOT.resolve()
    if source_pdf_path.parent != source_pdf_root:
        raise ValueError(f"Refusing to remove unexpected source PDF directory: {source_pdf_path}")
    if source_pdf_path.exists():
        shutil.rmtree(source_pdf_path)


def _cancel_job_record(
    job_id: str,
    owner_id: int | None,
    *,
    artifact_dir=None,
    object_storage_prefix: str | None = None,
):
    with _jobs_lock:
        job = _jobs.get(job_id)
        runtime = _job_runtimes.get(job_id)
        process = runtime.get("process") if runtime else None
        if process and process.is_alive():
            raise RuntimeError("Pipeline worker is still running")
        selected_artifact_dir = (
            job.get("_artifact_dir") if job else artifact_dir or str(_persistent_job_dir(job_id))
        )
        selected_object_prefix = (
            job.get("_object_storage_prefix") if job else object_storage_prefix
        )
    if (
        owner_id is not None
        and not _desktop_legacy_auth
        and _object_storage is not None
        and selected_object_prefix
    ):
        expected_prefix = _object_storage.task_prefix(int(owner_id), job_id)
        if selected_object_prefix != expected_prefix:
            raise ObjectStorageError("stored OSS task prefix does not match task ownership")
        _object_storage.delete_job(int(owner_id), job_id)
    _remove_job_artifacts(job_id, selected_artifact_dir)
    if owner_id is not None and not _desktop_legacy_auth:
        _learning_repository.delete_owned_history(int(owner_id), job_id)
    elif owner_id is not None:
        conn = sqlite3.connect(str(_DB_PATH))
        try:
            conn.execute(
                "DELETE FROM history WHERE id = ? AND user_id = ?",
                (job_id, owner_id),
            )
            conn.commit()
        finally:
            conn.close()
    with _jobs_lock:
        runtime = _job_runtimes.pop(job_id, None)
        if runtime and runtime.get("queue"):
            try:
                runtime["queue"].close()
            except Exception:
                pass
        _jobs.pop(job_id, None)


@app.route("/api/v2/agent-import", methods=["POST"])
def agent_import():
    user = _current_user()
    if not _desktop_legacy_auth and not user:
        return jsonify({"error": "not authenticated"}), 401
    try:
        node_payload = _read_uploaded_json(request.files.get("nodes_file"), "Node")
        edge_payload = _read_uploaded_json(request.files.get("edges_file"), "Edge")
        nodes = _normalize_nodes(node_payload)
        if not nodes:
            return jsonify({"error": "Node JSON contains no nodes"}), 400
        edges, warnings = _normalize_edges(edge_payload, nodes, include_warnings=True)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    markdown_upload = request.files.get("markdown_file")
    source_markdown = None
    markdown_filename = None
    source_macros = {}
    source_macro_warnings = []
    if markdown_upload and markdown_upload.filename:
        markdown_filename = markdown_upload.filename
        if not markdown_filename.lower().endswith((".md", ".txt", ".tex")):
            return jsonify({"error": "Source file must end with .md, .txt, or .tex"}), 400
        source_markdown = markdown_upload.read().decode("utf-8", errors="replace")
        source_macros, source_macro_warnings = extract_latex_macros(source_markdown, markdown_filename)

    filename = markdown_filename or "导入已有图谱"
    job_id = str(uuid.uuid4())
    json_macros = merge_latex_macros(
        node_payload.get("latex_macros") if isinstance(node_payload, dict) else None,
        edge_payload.get("latex_macros") if isinstance(edge_payload, dict) else None,
    )
    latex_macros = merge_latex_macros(source_macros, json_macros)
    latex_macro_warnings = [*source_macro_warnings]
    is_tex = bool(markdown_filename and markdown_filename.lower().endswith(".tex"))
    source_pdf = _pending_source_pdf_meta(job_id) if is_tex else None
    result = {
        "nodes": nodes,
        "edges": edges,
        "latex_macros": latex_macros,
        "source_pdf": _public_source_pdf_meta(source_pdf),
    }
    _jobs[job_id] = {
        "job_id": job_id,
        "status": "done",
        "filename": filename,
        "stage": None,
        "stage_label": None,
        "stage_index": 0,
        "total_stages": 0,
        "stages_done": [],
        "result": result,
        "error": None,
        "source_markdown": source_markdown,
        "source_pdf": source_pdf,
        "latex_macros": latex_macros,
        "latex_macro_warnings": latex_macro_warnings,
        "source": "agent",
        "_user_id": int(user["id"]) if user else None,
        "_history_persisted": False,
        "_persistent_artifacts": bool(user),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if not _desktop_legacy_auth and not _persist_job_with_files(
        _jobs[job_id], "done", int(user["id"])
    ):
        _jobs.pop(job_id, None)
        return jsonify({"error": "Unable to persist imported job"}), 500
    if is_tex:
        threading.Thread(
            target=_compile_agent_source_pdf,
            args=(job_id, source_markdown, filename),
            daemon=True,
            name=f"tex-pdf-{job_id[:8]}",
        ).start()
    return jsonify({
        "job_id": job_id,
        "filename": filename,
        "result": result,
        "has_markdown": bool(source_markdown),
        "warnings": [*warnings, *latex_macro_warnings],
    }), 201


@app.route("/api/v2/jobs/<job_id>/status")
def job_status(job_id):
    job, error = _owned_job_resource(job_id)
    if error:
        return error
    data = {
        k: v
        for k, v in job.items()
        if not k.startswith("_")
        and k not in (
            "result",
            "partial",
            "source_markdown",
            "source_pdf",
            "error",
            "error_detail",
            "error_user_message",
        )
    }
    data.update(_job_error_presentation(job))
    data["source_pdf"] = _public_source_pdf_meta(job.get("source_pdf"))
    data["persistence_error"] = bool(job.get("_persistence_error"))
    return jsonify(data)


@app.route("/api/v2/jobs/<job_id>/error-detail")
def job_error_detail(job_id):
    job, error = _owned_job_resource(job_id)
    if error:
        return error
    if job.get("status") != "error":
        return jsonify({"error": "Error detail is only available for failed jobs"}), 409
    llm_config = job.get("_llm_config") if isinstance(job.get("_llm_config"), dict) else {}
    secrets_to_redact = (llm_config.get("api_key"), llm_config.get("embedding_api_key"))
    message = _redact_error_text(job.get("error"), secrets_to_redact)
    detail = _redact_error_text(job.get("error_detail"), secrets_to_redact)
    if not message and not detail:
        return jsonify({"error": "No error detail is available"}), 409
    return jsonify({"message": message, "detail": detail})


@app.route("/api/v2/jobs/<job_id>/pause", methods=["POST"])
def pause_job(job_id):
    resource, error = _owned_job_resource(job_id)
    if error:
        return error
    if not resource.get("_is_live"):
        return jsonify({"error": "Pipeline worker is not available"}), 409
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify({"error": "Not found"}), 404
        if job.get("status") == "paused":
            return jsonify({"ok": True, "status": "paused", "note": "already paused"})
        if job.get("status") != "running":
            return jsonify({
                "error": "Only a running job can be paused",
                "status": job.get("status"),
            }), 409
        runtime = _job_runtimes.get(job_id)
        if not runtime:
            return jsonify({"error": "Pipeline worker is not available"}), 409
        runtime["pause_requested"] = True
        process = runtime.get("process")
        attempt_token = runtime.get("attempt_token")

    try:
        _terminate_pipeline_process(process)
    except Exception as exc:
        with _jobs_lock:
            current = _job_runtimes.get(job_id)
            if current and current.get("attempt_token") == attempt_token:
                current["pause_requested"] = False
        return jsonify({"error": f"Unable to stop pipeline worker: {exc}"}), 500

    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify({"error": "Not found"}), 404
        if job.get("status") == "done":
            return jsonify({"ok": True, "status": "done", "note": "already finished"})
        if job.get("_attempt_token") == attempt_token:
            job["status"] = "paused"
            job["error"] = None
            job.pop("error_detail", None)
            job.pop("error_code", None)
            job.pop("error_title", None)
            job.pop("error_user_message", None)
            _set_cache_manifest_status(job, "paused")
            _cleanup_job_stage_work(job)
            if job.get("_user_id") is not None and not _persist_job_with_files(job, "paused"):
                return jsonify({
                    "error": "Task paused but history persistence failed",
                    "status": "paused",
                }), 500
    return jsonify({"ok": True, "status": "paused"})


@app.route("/api/v2/jobs/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id):
    resource, error = _owned_job_resource(job_id)
    if error:
        return error
    if not resource.get("_is_live"):
        return jsonify({"error": "Live task is not available"}), 409
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify({"error": "Not found"}), 404
        if job.get("status") != "paused":
            return jsonify({
                "error": "Only a paused job can be cancelled",
                "status": job.get("status"),
            }), 409
        owner_id = job.get("_user_id")
    try:
        _cancel_job_record(job_id, owner_id)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    except (ObjectStorageError, OSError, ValueError) as exc:
        return jsonify({"error": f"Unable to cancel task: {exc}"}), 500
    return jsonify({"ok": True, "status": "cancelled", "job_id": job_id})


@app.route("/api/v2/jobs/<job_id>/resume", methods=["POST"])
def resume_job(job_id):
    resource, error = _owned_job_resource(job_id)
    if error:
        return error
    if not resource.get("_is_live"):
        return jsonify({"error": "Use history resume for a persisted task"}), 409
    error = _begin_pipeline_resume(job_id)
    if error:
        message, status_code = error
        return jsonify({"error": message}), status_code
    return jsonify({"ok": True, "status": "running", "job_id": job_id}), 202


def _begin_pipeline_resume(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return "Not found", 404
        if job.get("status") not in {"paused", "error"}:
            return "Only a paused or failed job can be resumed", 409
        source_path = Path(job.get("_md_path") or "")
        if not source_path.is_file():
            return "Pipeline source file is unavailable", 410
        if not isinstance(job.get("_llm_config"), dict):
            return "Pipeline runtime configuration is unavailable", 409
        old_runtime = _job_runtimes.get(job_id)
        if old_runtime and old_runtime.get("process") and old_runtime["process"].is_alive():
            return "Pipeline worker is still running", 409
        previous_state = deepcopy(job)
        job["status"] = "running"
        job["error"] = None
        job.pop("error_detail", None)
        job.pop("error_code", None)
        job.pop("error_title", None)
        job.pop("error_user_message", None)
        job["result"] = None
        stage_defs = _job_stage_defs(job)
        stage_keys = [key for key, _ in stage_defs]
        stage_labels = dict(stage_defs)
        completed = [
            key
            for key in stage_keys
            if key in set(job.get("stages_done") or [])
        ]
        next_index = min(len(completed), max(0, len(stage_defs) - 1))
        next_stage = (
            stage_keys[len(completed)]
            if len(completed) < len(stage_keys)
            else None
        )
        job["_stage_defs"] = stage_defs
        job["total_stages"] = len(stage_defs)
        job["stages_done"] = completed
        job["stage"] = next_stage
        job["stage_label"] = stage_labels.get(next_stage)
        job["stage_index"] = next_index
        _set_cache_manifest_status(job, "running")
        if job.get("_history_persisted") and not _persist_job_with_files(job, "running"):
            # worker 只能在 running 已持久化后启动；失败时恢复原任务状态供安全重试。
            job.clear()
            job.update(previous_state)
            _mark_persistence_error(job, preserve_status=True)
            _set_cache_manifest_status(job, previous_state.get("status") or "paused")
            return "Task progress persistence failed", 503
        job.pop("_persistence_error", None)
    try:
        _start_pipeline_attempt(job_id, resume=True)
    except Exception as exc:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job:
                job["status"] = "error"
                presentation = _classify_job_error(
                    exc,
                    stage=job.get("stage"),
                    stage_label=job.get("stage_label"),
                )
                job["error"] = str(exc)
                job["error_code"] = presentation["error_code"]
                job["error_title"] = presentation["error_title"]
                job["error_user_message"] = presentation["error"]
                _persist_job_state(job, "error")
        return "Unable to restart pipeline worker", 500
    return None


def _job_tracking_snapshot(job: dict):
    stage_defs = _job_stage_defs(job)
    return {
        "job_id": job["job_id"],
        "status": job.get("status"),
        "filename": job.get("filename"),
        "stage": job.get("stage"),
        "stage_label": job.get("stage_label"),
        "stage_index": int(job.get("stage_index") or 0),
        "total_stages": int(job.get("total_stages") or len(stage_defs)),
        "stages_done": list(job.get("stages_done") or []),
        "source_markdown": job.get("source_markdown") or "",
        "experimental_logic_ir": bool(job.get("_experimental_logic_ir")),
    }


@app.route("/api/v2/jobs/<job_id>/result")
def job_result(job_id):
    job, error = _owned_job_resource(job_id)
    if error:
        return error
    if job["status"] != "done":
        return jsonify({"error": "Job not complete"}), 400
    return jsonify(redact_structure(job["result"]))


def _source_pdf_context(job: dict) -> tuple[dict | None, list[dict]]:
    result = job.get("result") or job.get("partial") or {}
    meta = _read_source_pdf_meta(job)
    if meta:
        job_root = _controlled_source_pdf_job_root(job["job_id"])
        if job_root is None:
            return None, result.get("nodes") or []
        controlled = dict(meta)
        for name_key, path_key in (
            ("pdf_name", "pdf_path"),
            ("source_name", "source_path"),
            ("log_name", "log_path"),
        ):
            path = _controlled_source_pdf_file(
                job["job_id"], meta, name_key, path_key, job_root=job_root
            )
            if path is None:
                controlled.pop(path_key, None)
            else:
                controlled[path_key] = str(path)
        meta = controlled
    return meta, result.get("nodes") or []


def _controlled_source_pdf_job_root(job_id: str) -> Path | None:
    """返回 canonical source-PDF job 根；目录 junction 解析到根外时拒绝。"""
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(job_id))
    if not safe_id or safe_id in {".", ".."} or Path(safe_id).name != safe_id:
        return None
    canonical_root = _SOURCE_PDF_ROOT.resolve()
    lexical_job_root = canonical_root / safe_id
    if lexical_job_root.parent != canonical_root:
        return None
    resolved_job_root = lexical_job_root.resolve()
    # job 目录不能是任何 reparse 跳转；即使目标仍在总根内也会造成跨任务文件错位。
    if resolved_job_root != lexical_job_root or resolved_job_root.parent != canonical_root:
        return None
    return resolved_job_root


def _controlled_source_pdf_file(
    job_id: str,
    meta: dict,
    name_key: str,
    path_key: str,
    *,
    job_root: Path | None = None,
) -> Path | None:
    raw_name = meta.get(name_key) or meta.get(path_key)
    name = Path(str(raw_name or "")).name
    if not name:
        return None
    root = job_root or _controlled_source_pdf_job_root(job_id)
    if root is None:
        return None
    lexical_candidate = root / name
    if lexical_candidate.parent != root:
        return None
    candidate = lexical_candidate.resolve()
    # 文件本身为 symlink 时，解析后父目录必须仍是已验证的 canonical job 根。
    if candidate.parent != root:
        return None
    return candidate


@app.route("/api/v2/source-pdf/<job_id>")
def source_pdf(job_id):
    job, error = _owned_job_resource(job_id)
    if error:
        return error
    meta, _nodes = _source_pdf_context(job)
    if not meta:
        return jsonify({"error": "source PDF not found"}), 404
    if meta.get("status") == "compiling":
        return jsonify({"error": "source PDF is compiling", "status": "compiling"}), 409
    pdf_path = _controlled_source_pdf_file(job_id, meta, "pdf_name", "pdf_path")
    if meta.get("available") and pdf_path is not None and not pdf_path.is_file():
        try:
            _restore_job_files(job)
        except ObjectStorageError:
            return jsonify({"error": "Source PDF could not be restored from OSS"}), 503
        pdf_path = _controlled_source_pdf_file(job_id, meta, "pdf_name", "pdf_path")
    if not meta.get("available") or pdf_path is None or not pdf_path.is_file():
        return jsonify({"error": meta.get("error") or "source PDF unavailable"}), 404
    return send_file(
        pdf_path,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"{job_id}.pdf",
    )


@app.route("/api/v2/source-pdf/<job_id>/compile-log")
def source_pdf_compile_log(job_id):
    job, error = _owned_job_resource(job_id)
    if error:
        return error
    meta, _nodes = _source_pdf_context(job)
    if not meta:
        return jsonify({"error": "source PDF not found"}), 404
    if meta.get("status") == "compiling":
        return jsonify({"error": "source PDF is compiling", "status": "compiling"}), 409
    log_path = _controlled_source_pdf_file(job_id, meta, "log_name", "log_path")
    if log_path is not None and not log_path.is_file():
        try:
            _restore_job_files(job)
        except ObjectStorageError:
            return jsonify({"error": "Compile log could not be restored from OSS"}), 503
        log_path = _controlled_source_pdf_file(job_id, meta, "log_name", "log_path")
    if log_path is None or not log_path.is_file():
        return jsonify({"error": "compile log not found"}), 404
    return send_file(log_path, mimetype="text/plain; charset=utf-8", as_attachment=False)


@app.route("/api/v2/source-pdf/<job_id>/locate")
def source_pdf_locate(job_id):
    job, error = _owned_job_resource(job_id)
    if error:
        return error
    raw_node_id = request.args.get("node_id", "")
    try:
        node_id = int(raw_node_id)
    except Exception:
        return jsonify({"error": "node_id must be an integer"}), 400
    meta, nodes = _source_pdf_context(job)
    if not meta or not meta.get("available"):
        if meta and meta.get("status") == "compiling":
            return jsonify({"error": "source PDF is compiling", "status": "compiling"}), 409
        return jsonify({"error": (meta or {}).get("error") or "source PDF unavailable"}), 404
    pdf_path = _controlled_source_pdf_file(job_id, meta, "pdf_name", "pdf_path")
    source_path = _controlled_source_pdf_file(job_id, meta, "source_name", "source_path")
    if (
        (pdf_path is not None and not pdf_path.is_file())
        or (source_path is not None and not source_path.is_file())
    ):
        try:
            _restore_job_files(job)
        except ObjectStorageError:
            return jsonify({"error": "Source files could not be restored from OSS"}), 503
    node = next((n for n in nodes if isinstance(n, dict) and n.get("id") == node_id), None)
    if not node:
        return jsonify({"error": "node not found"}), 404
    page = _synctex_page(meta, node) or 1
    statement_terms = _tex_statement_terms(meta, node)
    terms = statement_terms + [term for term in _node_locator_terms(node) if term not in statement_terms]
    return jsonify({
        "node_id": node_id,
        "page": page,
        "search_terms": terms,
        "statement_terms": statement_terms,
        "source_statement": _node_original_statement(node),
        "source_key": _node_source_key(node),
        "pdf_url": (meta.get("pdf_url") or f"/api/v2/source-pdf/{job_id}"),
        "source_span": node.get("source_span"),
    })


@app.route("/api/v2/export/<job_id>", methods=["POST"])
def export_html(job_id):
    job, error = _owned_job_resource(job_id)
    if error:
        return error
    if job["status"] != "done":
        return jsonify({"error": "Job not done"}), 400

    result = job["result"]
    html = _build_export_html(job["filename"], result["nodes"], result["edges"], result.get("latex_macros") or job.get("latex_macros") or {})
    stem = Path(job["filename"]).stem
    return send_file(
        io.BytesIO(html.encode("utf-8")),
        mimetype="text/html",
        as_attachment=True,
        download_name=f"{stem}_mathgraph.html",
    )


@app.route("/api/v2/export/<job_id>/artifacts", methods=["POST"])
def export_artifacts(job_id):
    job, error = _owned_job_resource(job_id, allow_desktop_missing=True)
    if error:
        return error
    fallback_payload = request.get_json(silent=True)
    if not isinstance(fallback_payload, dict):
        fallback_payload = {}
    fallback_nodes = fallback_payload.get("nodes")
    fallback_edges = fallback_payload.get("edges")
    fallback_available = isinstance(fallback_nodes, list) and isinstance(fallback_edges, list)

    if not job:
        if not fallback_available:
            return jsonify({"error": "Job not found"}), 404
        return _export_artifact_zip(
            filename=fallback_payload.get("filename") or "processing_result",
            nodes_bytes=_export_json_bytes(fallback_nodes),
            edges_bytes=_export_json_bytes(fallback_edges),
            degraded=True,
        )
    if job.get("status") != "done":
        return jsonify({"error": "Job is not complete"}), 409

    artifact_dir = (
        job.get("_artifact_dir")
        if _desktop_legacy_auth
        else str(_persistent_job_dir(job_id))
    )
    if job.get("_is_live") and job.get("source") != "pipeline":
        return jsonify({
            "error": "Complete processing cache is unavailable for this job",
        }), 409

    artifact_root = Path(artifact_dir) if artifact_dir else None
    nodes_path = artifact_root / "nodes.json" if artifact_root else None
    edges_path = artifact_root / "edges.json" if artifact_root else None
    cache_path = artifact_root / "_stage_cache" if artifact_root else None
    local_cache_missing = not (
        artifact_root
        and artifact_root.is_dir()
        and nodes_path
        and nodes_path.is_file()
        and edges_path
        and edges_path.is_file()
        and cache_path
        and cache_path.is_dir()
    )
    if local_cache_missing and job.get("_object_storage_prefix"):
        try:
            _restore_job_files(job)
        except ObjectStorageError:
            return jsonify({"error": "Processing files could not be restored from OSS"}), 503
    has_complete_cache = bool(
        artifact_root
        and artifact_root.is_dir()
        and nodes_path
        and nodes_path.is_file()
        and edges_path
        and edges_path.is_file()
        and cache_path
        and cache_path.is_dir()
    )

    if has_complete_cache:
        return _export_artifact_zip(
            filename=job.get("filename") or "processing_result",
            nodes_bytes=nodes_path.read_bytes(),
            edges_bytes=edges_path.read_bytes(),
            cache_path=cache_path,
            artifact_root=artifact_root,
        )

    if nodes_path and nodes_path.is_file() and edges_path and edges_path.is_file():
        nodes_bytes = nodes_path.read_bytes()
        edges_bytes = edges_path.read_bytes()
    else:
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        result_nodes = result.get("nodes")
        result_edges = result.get("edges")
        if isinstance(result_nodes, list) and isinstance(result_edges, list):
            nodes_bytes = _export_json_bytes(result_nodes)
            edges_bytes = _export_json_bytes(result_edges)
        elif fallback_available and _desktop_legacy_auth:
            nodes_bytes = _export_json_bytes(fallback_nodes)
            edges_bytes = _export_json_bytes(fallback_edges)
        else:
            return jsonify({
                "error": "Processing cache is missing and node/edge results are unavailable",
            }), 409

    return _export_artifact_zip(
        filename=job.get("filename") or (
            fallback_payload.get("filename") if _desktop_legacy_auth else None
        ) or "processing_result",
        nodes_bytes=nodes_bytes,
        edges_bytes=edges_bytes,
        degraded=True,
    )


def _export_json_bytes(items):
    return json.dumps(redact_structure(items), ensure_ascii=False, indent=2).encode("utf-8")


def _export_artifact_zip(
    *,
    filename,
    nodes_bytes,
    edges_bytes,
    cache_path=None,
    artifact_root=None,
    degraded=False,
):
    raw_stem = Path(str(filename or "processing_result")).stem
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw_stem).strip(" .") or "processing_result"
    archive_root = Path(stem)
    archive_file = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024, mode="w+b")
    try:
        with zipfile.ZipFile(archive_file, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr((archive_root / "nodes.json").as_posix(), nodes_bytes)
            bundle.writestr((archive_root / "edges.json").as_posix(), edges_bytes)
            if not degraded and cache_path and artifact_root:
                bundle.writestr((archive_root / "_stage_cache").as_posix().rstrip("/") + "/", b"")
                for path in sorted(cache_path.rglob("*")):
                    if path.is_symlink():
                        continue
                    relative = path.relative_to(artifact_root)
                    archive_name = (archive_root / relative).as_posix()
                    if path.is_dir():
                        bundle.writestr(archive_name.rstrip("/") + "/", b"")
                    elif path.is_file():
                        bundle.write(path, archive_name)
        archive_file.seek(0)
        response = send_file(
            archive_file,
            mimetype="application/zip",
            as_attachment=True,
            download_name=(
                f"{stem}_nodes_edges.zip"
                if degraded
                else f"{stem}_processing_result.zip"
            ),
        )
        response.headers["X-MathGraph-Export-Mode"] = (
            "nodes-edges-only" if degraded else "complete"
        )
        if degraded:
            response.headers["X-MathGraph-Export-Warning"] = "processing-cache-missing"
        response.headers["Access-Control-Expose-Headers"] = (
            "Content-Disposition, X-MathGraph-Export-Mode, X-MathGraph-Export-Warning"
        )
        response.call_on_close(archive_file.close)
        return response
    except Exception:
        archive_file.close()
        raise


def _build_export_html(title: str, nodes: list, edges: list, latex_macros: dict | None = None) -> str:
    nodes_json = json.dumps(nodes, ensure_ascii=False)
    edges_json = json.dumps(edges, ensure_ascii=False)
    macros_json = json.dumps(latex_macros or {}, ensure_ascii=False)

    NODE_COLORS = {
        "定义": "#3B6FBF", "公理": "#7A6B4A", "定理": "#2E7D5E",
        "引理": "#5B8E78", "推论": "#6B5B9E", "性质": "#4A7A8E",
        "命题": "#8E6B4A", "例子": "#7A8E4A",
    }
    color_js = json.dumps(NODE_COLORS, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>MathGraph — {title}</title>
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.27/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.27/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.27/dist/contrib/auto-render.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:Inter,system-ui,sans-serif;background:#F7F6F2;color:#111110;height:100vh;display:flex;flex-direction:column}}
header{{height:48px;display:flex;align-items:center;justify-content:space-between;padding:0 20px;background:#fff;border-bottom:1px solid #E2DFD8;flex-shrink:0}}
header h1{{font-size:15px;font-weight:600;letter-spacing:.01em}}
header span{{font-size:12px;color:#6B6860}}
#app{{display:flex;flex:1;overflow:hidden}}
#graph{{flex:1;background:#EFEDE7}}
#detail{{width:380px;background:#fff;border-left:1px solid #E2DFD8;overflow-y:auto;padding:24px;flex-shrink:0;display:none}}
#detail.open{{display:block}}
.badge{{display:inline-block;padding:2px 8px;border-radius:3px;font-size:11px;font-weight:600;color:#fff;margin-right:6px}}
.section{{margin-top:20px}}
.section h3{{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:#6B6860;margin-bottom:8px}}
.tag{{display:inline-block;background:#F0EEE8;border:1px solid #E2DFD8;border-radius:3px;padding:2px 7px;font-size:12px;margin:2px 2px 2px 0}}
.risk{{background:#FEF3C7;border:1px solid #FCD34D;border-radius:4px;padding:8px 10px;font-size:12px;margin-top:6px}}
pre{{background:#1C1C1E;color:#E5E5E7;padding:12px;border-radius:4px;font-size:11px;overflow-x:auto;white-space:pre-wrap;font-family:'JetBrains Mono',monospace}}
</style>
</head>
<body>
<header>
  <h1>MathGraph — {title}</h1>
  <span id="stats"></span>
</header>
<div id="app">
  <div id="graph"></div>
  <div id="detail">
    <div id="detail-content"></div>
  </div>
</div>
<script>
const NODES_RAW = {nodes_json};
const EDGES_RAW = {edges_json};
const LATEX_MACROS = {macros_json};
const COLORS = {color_js};

document.getElementById('stats').textContent =
  NODES_RAW.length + ' 节点 · ' + EDGES_RAW.length + ' 关系';

const visNodes = NODES_RAW.map(n => ({{
  id: n.id,
  label: n.title_zh || n.title_en || ('节点' + n.id),
  color: {{background: COLORS[n.node_type] || '#95A5A6', border: 'transparent',
           highlight: {{background: COLORS[n.node_type] || '#95A5A6', border: '#111110'}}}},
  font: {{color:'#fff', size:13}},
  shape: 'box',
  margin: 10,
  _data: n,
}}));

const visEdges = EDGES_RAW.map((e,i) => ({{
  id: i, from: e.from, to: e.to,
  label: e.label,
  arrows: 'to',
  color: {{color:'#C8C5BE', highlight:'#1A3A6B'}},
  font: {{size:10, color:'#6B6860', strokeWidth:0}},
}}));

const network = new vis.Network(
  document.getElementById('graph'),
  {{nodes: new vis.DataSet(visNodes), edges: new vis.DataSet(visEdges)}},
  {{
    physics:{{solver:'forceAtlas2Based',forceAtlas2Based:{{gravitationalConstant:-50,centralGravity:0.01,springLength:200,avoidOverlap:0.5}},stabilization:{{iterations:1500}}}},
    interaction:{{hover:true,zoomView:true,dragView:true}},
    edges:{{smooth:{{type:'cubicBezier',roundness:0.5}},width:1.5}},
  }}
);

network.on('click', params => {{
  const detail = document.getElementById('detail');
  const content = document.getElementById('detail-content');
  if (!params.nodes.length) {{ detail.classList.remove('open'); return; }}
  const node = NODES_RAW[params.nodes[0]];
  const color = COLORS[node.node_type] || '#95A5A6';
  const fg = node.formalization_guidance;
  const risks = fg && fg.semantic_risks && fg.semantic_risks.length ?
    fg.semantic_risks.map(r => `<div class="risk">⚠ ${{r.message}}</div>`).join('') : '';
  const skeleton = fg && fg.statement_skeleton ?
    `<div class="tag">${{fg.statement_skeleton.kind}}</div>` +
    (fg.statement_skeleton.expected_connectives || []).map(c => `<div class="tag">${{c}}</div>`).join('') : '';
  content.innerHTML = `
    <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:16px">
      <span class="badge" style="background:${{color}}">${{node.node_type}}</span>
      <h2 style="font-size:16px;font-weight:600">${{node.title_zh}}</h2>
    </div>
    ${{node.title_en ? `<div style="font-size:12px;color:#6B6860;margin-bottom:16px">${{node.title_en}}</div>` : ''}}
    <div class="section">
      <h3>原文</h3>
      <p style="font-size:13px;line-height:1.7;color:#1A1A1A">${{node.content || '—'}}</p>
    </div>
    ${{node.statement_form ? `<div class="section"><h3>命题形式</h3><div class="tag">${{node.statement_form}}</div></div>` : ''}}
    ${{(node.conditions||[]).length ? `<div class="section"><h3>条件</h3>${{(node.conditions||[]).map(c=>`<div class="tag">${{typeof c==='string'?c:JSON.stringify(c)}}</div>`).join('')}}</div>` : ''}}
    ${{(node.conclusions||[]).length ? `<div class="section"><h3>结论</h3>${{(node.conclusions||[]).map(c=>`<div class="tag">${{typeof c==='string'?c:JSON.stringify(c)}}</div>`).join('')}}</div>` : ''}}
    ${{skeleton ? `<div class="section"><h3>形式化骨架</h3>${{skeleton}}</div>` : ''}}
    ${{risks ? `<div class="section"><h3>语义风险</h3>${{risks}}</div>` : ''}}
  `;
  if (window.renderMathInElement) {{
    window.renderMathInElement(content, {{
      delimiters: [
        {{left: '$$', right: '$$', display: true}},
        {{left: '\\\\[', right: '\\\\]', display: true}},
        {{left: '$', right: '$', display: false}},
        {{left: '\\\\(', right: '\\\\)', display: false}},
      ],
      throwOnError: false,
      strict: false,
      macros: LATEX_MACROS,
    }});
  }}
  detail.classList.add('open');
}});
</script>
</body>
</html>"""


@app.route("/api/v2/ping")
def ping():
    if not _desktop_legacy_auth and not database_is_ready():
        return jsonify({"ok": False, "error": "database_unavailable"}), 503
    return jsonify({"ok": True})


if __name__ == "__main__":
    print("MathGraph API v2  →  http://0.0.0.0:5001")
    app.run(host="0.0.0.0", port=5001, debug=False, threaded=True)
