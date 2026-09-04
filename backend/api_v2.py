"""MathGraph API v2 — staged Markdown/text pipeline and local OCR pre-task."""

import copy
import hashlib
import io
import mimetypes
import ipaddress
import json
import multiprocessing
import os
import queue as queue_module
import re
import secrets
import shutil
import socket
import subprocess
from collections import defaultdict

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
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, g, has_app_context, jsonify, request, send_file
from flask_cors import CORS
from sqlalchemy.exc import SQLAlchemyError
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
from matrix_flow.runner import MatrixFlowRunner
from matrix_flow.grading import analyze_matrix_answer
from pipeline.stages.build_relations.stage import get_embedding
from tex_macros import extract_latex_macros, merge_latex_macros
from JoinAgent import SimpleLLM
from education_service import (
    ASSESSMENT_QUESTION_KINDS,
    apply_progress_to_path,
    build_assessment_tasks,
    build_learning_path,
    create_education_context,
    merge_ai_path,
    run_structured_education_tasks,
    validate_direct_scoring_standard_result,
)
import education_gamification as education_game
from student_context import (
    build_student_context_overview,
    build_student_context_packet,
    build_teacher_context_summary,
    context_preview,
    delete_student_context,
    export_student_context,
    load_idempotent_result,
    run_structured_proof_assist,
    save_interaction_result,
    store_interaction_with_evidence,
    update_evidence_status,
)
from ocr_runtime import (
    CHUNK_SIZE,
    IMAGE_MAX_BYTES,
    PDF_MAX_BYTES,
    OcrError,
    get_ocr_manager,
)
from runtime_capacity import ProcessCapacityPool
from storage.database import (
    DatabaseConfigurationError,
    IntegrityError,
    close_request_database,
    connect_database,
    database_health,
    get_database,
)
from storage.graph_service import (
    GraphUnavailableError,
    complete_graph_delete,
    load_graph as load_stored_graph,
    persist_graph,
    prepare_graph_delete,
)
from integrations.neo4j_handler import GraphStoreError, get_graph_store
from storage.secrets import decrypt_secret, encrypt_secret, session_token_hash

def _bounded_env_int(name: str, default: int, *, minimum: int = 1, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = _bounded_env_int(
    "MATHWEAVER_MAX_REQUEST_BYTES",
    120 * 1024 * 1024,
    minimum=1024 * 1024,
    maximum=512 * 1024 * 1024,
)
CORS(app)

# ── MySQL business storage / Neo4j graph storage ─────────────────────────────

_DATA_ROOT = Path(os.environ.get("MATHGRAPH_DATA_DIR", str(Path(__file__).parent))).expanduser()
_DATA_ROOT.mkdir(parents=True, exist_ok=True)
_RUNTIME_ROOT = Path(
    os.environ.get("MATHWEAVER_RUNTIME_DIR", str(_DATA_ROOT / "runtime"))
).expanduser()
_CAPACITY_ROOT = _RUNTIME_ROOT / "capacity"
_AI_CAPACITY_POOL = ProcessCapacityPool(
    _CAPACITY_ROOT,
    "ai",
    _bounded_env_int("MATHWEAVER_AI_MAX_ACTIVE", 4, maximum=32),
)
_PIPELINE_CAPACITY_POOL = ProcessCapacityPool(
    _CAPACITY_ROOT,
    "pipeline",
    _bounded_env_int("MATHWEAVER_PIPELINE_MAX_ACTIVE", 2, maximum=16),
)
_CAPACITY_RETRY_AFTER = _bounded_env_int(
    "MATHWEAVER_CAPACITY_RETRY_AFTER_SECONDS", 5, maximum=60
)
_JOB_MEMORY_TTL_SECONDS = _bounded_env_int(
    "MATHWEAVER_JOB_MEMORY_TTL_SECONDS", 3600, minimum=60, maximum=7 * 24 * 3600
)
_SOURCE_PDF_ROOT = _DATA_ROOT / "uploads" / "source_pdfs"
_EDUCATION_ROOT = _DATA_ROOT / "education"
_EDUCATION_SNAPSHOT_ROOT = _EDUCATION_ROOT / "snapshots"
_EDUCATION_ASSIGNMENT_SOURCE_ROOT = _EDUCATION_ROOT / "assignment_sources"
_DIRECT_ASSIGNMENT_NODE_ID = 1
_PACKAGED_BACKEND_ROOT = Path(getattr(sys, "_MEIPASS", "")) / "backend"
_TEX_TEMPLATE_ROOT = (
    _PACKAGED_BACKEND_ROOT if _PACKAGED_BACKEND_ROOT.is_dir() else Path(__file__).parent
) / "assets" / "tex_templates"


def _stored_source_pdf_meta(meta: dict | None) -> dict | None:
    if not isinstance(meta, dict):
        return None
    status = meta.get("status")
    if status not in {"compiling", "ready", "failed"}:
        status = "ready" if meta.get("available") else ("failed" if meta.get("error") else "compiling")
    stored = {
        "status": status,
        "available": bool(meta.get("available")),
        "error": meta.get("error") or None,
        "pdf_url": meta.get("pdf_url") or None,
        "compile_log_url": meta.get("compile_log_url") or None,
    }
    for path_key, name_key in (
        ("pdf_path", "pdf_name"),
        ("source_path", "source_name"),
        ("log_path", "log_name"),
    ):
        value = meta.get(path_key) or meta.get(name_key)
        if value:
            stored[name_key] = Path(str(value)).name
    return stored


def _get_db():
    return get_database()


@app.teardown_appcontext
def _close_db(exc=None):
    close_request_database(exc)


@app.errorhandler(GraphUnavailableError)
def _handle_graph_unavailable(exc):
    code = "graph_pending" if exc.status in {"pending", "deleting"} else "graph_unavailable"
    return jsonify({
        "error": code,
        "code": code,
        "graphId": exc.graph_id,
        "status": exc.status,
    }), 503


@app.errorhandler(GraphStoreError)
def _handle_graph_store_error(exc):
    app.logger.error("Neo4j graph store unavailable: %s", exc)
    return jsonify({"error": "graph_unavailable", "code": "graph_unavailable"}), 503


@app.errorhandler(DatabaseConfigurationError)
@app.errorhandler(SQLAlchemyError)
def _handle_database_unavailable(exc):
    app.logger.error("MySQL unavailable: %s", exc)
    return jsonify({"error": "database_unavailable", "code": "database_unavailable"}), 503


def _load_api_graph(graph_id: str) -> dict:
    cache = getattr(g, "graph_payload_cache", None)
    if cache is None:
        cache = {}
        g.graph_payload_cache = cache
    if graph_id not in cache:
        cache[graph_id] = load_stored_graph(graph_id)
    return cache[graph_id]


def _invalidate_api_graph(graph_id: str) -> None:
    if not has_app_context():
        return
    cache = getattr(g, "graph_payload_cache", None)
    if isinstance(cache, dict):
        cache.pop(graph_id, None)


def _current_user():
    """Return user row if request carries a valid Bearer token, else None."""
    if hasattr(g, "mathweaver_current_user"):
        return g.mathweaver_current_user
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        g.mathweaver_current_user = None
        return None
    token = auth[7:]
    db = _get_db()
    row = db.execute(
        "SELECT u.*, s.education_role FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
        (session_token_hash(token),),
    ).fetchone()
    g.mathweaver_current_user = row
    return g.mathweaver_current_user


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
    try:
        with connect_database() as conn:
            conn.execute(
                "UPDATE history SET source_pdf_json = ? WHERE id = ?",
                (
                    json.dumps(_stored_source_pdf_meta(meta), ensure_ascii=False),
                    job_id,
                ),
            )
    except Exception:
        pass


def _source_pdf_dir(job_id: str) -> Path:
    return _SOURCE_PDF_ROOT / re.sub(r"[^A-Za-z0-9_.-]", "_", job_id)


def _read_source_pdf_meta(row_or_job) -> dict | None:
    if isinstance(row_or_job, dict):
        return row_or_job.get("source_pdf")
    raw = row_or_job["source_pdf_json"] if row_or_job and "source_pdf_json" in row_or_job.keys() else None
    if not raw:
        return None
    if isinstance(raw, dict):
        meta = dict(raw)
    else:
        try:
            meta = json.loads(raw)
        except Exception:
            return None
    if not isinstance(meta, dict):
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


def reconcile_interrupted_history() -> int:
    """Mark persisted workers as paused after a backend restart.

    Runtime process state is intentionally not durable. A row left as running
    therefore cannot still have a live worker in this process and must become
    resumable (subject to its stage cache still being valid).
    """
    now = datetime.now(timezone.utc).isoformat()
    with connect_database() as conn:
        rows = conn.execute(
            "SELECT id, source_pdf_json FROM history WHERE status = 'running' FOR UPDATE"
        ).fetchall()
        for row in rows:
            source_pdf = _stored_source_pdf_meta(_read_source_pdf_meta(row))
            conn.execute(
                """UPDATE history
                      SET status = 'paused', source_pdf_json = ?, updated_at = ?
                    WHERE id = ? AND status = 'running'""",
                (
                    json.dumps(source_pdf, ensure_ascii=False) if source_pdf else None,
                    now,
                    row["id"],
                ),
            )
    return len(rows)


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
        return {"api_url": api_url, "model_name": model_name, "api_key": api_key}
    return None


def _decrypt_user_setting(row, user_id: int, field: str) -> str:
    value = row[field]
    if not value:
        return ""
    suffix = "configs" if field == "llm_configs_ciphertext" else "api-key"
    return decrypt_secret(value, aad=f"user-settings:{user_id}:{suffix}")


def _active_user_llm_config(user):
    if not user:
        return None
    db = _get_db()
    row = db.execute(
        """SELECT llm_api_url, llm_model, llm_api_key_ciphertext, llm_configs_ciphertext
             FROM user_settings WHERE user_id = ?""",
        (user["id"],),
    ).fetchone()
    if not row:
        return None
    configs_json = _decrypt_user_setting(row, int(user["id"]), "llm_configs_ciphertext")
    if configs_json:
        try:
            data = json.loads(configs_json)
            configs = data.get("configs") or []
            active_index = int(data.get("active_index") or 0)
            active = configs[active_index] if configs and 0 <= active_index < len(configs) else (configs[0] if configs else {})
            config = _complete_llm_config(active)
            if config:
                return config
        except Exception:
            pass
    return _complete_llm_config({
        "api_url": row["llm_api_url"],
        "model_name": row["llm_model"],
        "api_key": _decrypt_user_setting(row, int(user["id"]), "llm_api_key_ciphertext"),
    })


# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.route("/api/v2/auth/register", methods=["POST"])
def auth_register():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    education_role = body.get("educationRole")
    if education_role is None:
        education_role = "student"
    if education_role != "student":
        if education_role == "teacher":
            return jsonify({"error": "teacher accounts are provisioned by an administrator", "code": "teacher_registration_disabled"}), 403
        return jsonify({"error": "invalid education role"}), 400
    if not email or not password:
        return jsonify({"error": "email and password required"}), 400
    if len(password) < 6:
        return jsonify({"error": "password must be at least 6 characters"}), 400
    db = _get_db()
    try:
        db.execute(
            "INSERT INTO users (email, password_hash, can_teach, created_at) VALUES (?, ?, 0, ?)",
            (email, generate_password_hash(password), datetime.utcnow().isoformat()),
        )
        db.commit()
    except IntegrityError:
        return jsonify({"error": "email already registered"}), 409
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    token = secrets.token_hex(32)
    db.execute(
        "INSERT INTO sessions (token_hash, user_id, education_role, created_at) VALUES (?, ?, ?, ?)",
        (session_token_hash(token), user["id"], education_role, datetime.utcnow().isoformat()),
    )
    db.commit()
    return jsonify({
        "token": token,
        "email": user["email"],
        "educationRole": education_role,
        "canTeach": bool(user["can_teach"]),
    }), 201


@app.route("/api/v2/auth/login", methods=["POST"])
def auth_login():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    education_role = body.get("educationRole")
    if education_role is None:
        education_role = "student"
    if education_role not in (None, "teacher", "student"):
        return jsonify({"error": "invalid education role"}), 400
    db = _get_db()
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        if education_role == "teacher":
            return jsonify({"error": "教师账号或密码错误", "code": "teacher_login_failed"}), 401
        return jsonify({"error": "invalid email or password"}), 401
    if education_role == "teacher" and not bool(user["can_teach"]):
        return jsonify({"error": "教师账号或密码错误", "code": "teacher_login_failed"}), 401
    token = secrets.token_hex(32)
    db.execute(
        "INSERT INTO sessions (token_hash, user_id, education_role, created_at) VALUES (?, ?, ?, ?)",
        (session_token_hash(token), user["id"], education_role, datetime.utcnow().isoformat()),
    )
    db.commit()
    return jsonify({
        "token": token,
        "email": user["email"],
        "educationRole": education_role,
        "canTeach": bool(user["can_teach"]),
    })


@app.route("/api/v2/auth/logout", methods=["POST"])
def auth_logout():
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        db = _get_db()
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (session_token_hash(auth[7:]),))
        db.commit()
    return jsonify({"ok": True})


@app.route("/api/v2/auth/me")
def auth_me():
    user = _current_user()
    if not user:
        return jsonify({"error": "not authenticated"}), 401
    return jsonify({
        "email": user["email"],
        "id": user["id"],
        "educationRole": user["education_role"],
        "canTeach": bool(user["can_teach"]),
    })


# ── User settings endpoints ───────────────────────────────────────────────────

@app.route("/api/v2/settings", methods=["GET"])
def settings_get():
    user = _current_user()
    if not user:
        return jsonify({"error": "not authenticated"}), 401
    row = _get_db().execute(
        """SELECT llm_api_url, llm_model, llm_api_key_ciphertext, llm_configs_ciphertext
             FROM user_settings WHERE user_id = ?""",
        (user["id"],),
    ).fetchone()
    if not row:
        return jsonify({"configs": [], "active_index": 0})
    configs_json = _decrypt_user_setting(row, int(user["id"]), "llm_configs_ciphertext")
    if configs_json:
        try:
            data = json.loads(configs_json)
            if isinstance(data, dict):
                return jsonify(data)
        except Exception:
            pass
    if row["llm_api_url"]:
        legacy = [{
            "name": "默认配置",
            "api_url": row["llm_api_url"],
            "model_name": row["llm_model"],
            "api_key": _decrypt_user_setting(row, int(user["id"]), "llm_api_key_ciphertext"),
        }]
        return jsonify({"configs": legacy, "active_index": 0})
    return jsonify({"configs": [], "active_index": 0})


@app.route("/api/v2/settings", methods=["PUT"])
def settings_put():
    user = _current_user()
    if not user:
        return jsonify({"error": "not authenticated"}), 401
    body = request.get_json(silent=True) or {}
    configs = body.get("configs", [])
    active_index = int(body.get("active_index", 0))
    data_json = json.dumps({"configs": configs, "active_index": active_index}, ensure_ascii=False)
    active = configs[active_index] if configs and 0 <= active_index < len(configs) else {}
    user_id = int(user["id"])
    encrypted_key = encrypt_secret(
        str(active.get("api_key") or ""),
        aad=f"user-settings:{user_id}:api-key",
    )
    encrypted_configs = encrypt_secret(
        data_json,
        aad=f"user-settings:{user_id}:configs",
    )
    db = _get_db()
    db.execute(
        """INSERT INTO user_settings
             (user_id, llm_api_url, llm_model, llm_api_key_ciphertext,
              llm_configs_ciphertext, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON DUPLICATE KEY UPDATE
             llm_api_url = VALUES(llm_api_url),
             llm_model = VALUES(llm_model),
             llm_api_key_ciphertext = VALUES(llm_api_key_ciphertext),
             llm_configs_ciphertext = VALUES(llm_configs_ciphertext),
             updated_at = VALUES(updated_at)""",
        (
            user_id,
            active.get("api_url", ""),
            active.get("model_name", ""),
            encrypted_key,
            encrypted_configs,
            datetime.utcnow().isoformat(),
        ),
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
    text = str(value or "")
    for secret in secrets_to_redact:
        if secret:
            text = text.replace(str(secret), "***")
    return text


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
    root = _DATA_ROOT.resolve() / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _persistent_job_dir(job_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(job_id))
    return _job_storage_root() / safe_id


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
        "stages_done": _json_list(
            row["stages_done_json"] if "stages_done_json" in row.keys() else "[]"
        ),
        "resume_available": _history_resume_available(row),
        "updated_at": (
            row["updated_at"]
            if "updated_at" in row.keys() and row["updated_at"]
            else row["created_at"]
        ),
        "created_at": row["created_at"],
    }


def _upsert_job_history(job: dict, status: str, user_id: int | None = None) -> bool:
    owner_id = user_id if user_id is not None else job.get("_user_id")
    if owner_id is None:
        return False
    stage_defs = _job_stage_defs(job)
    result = job.get("result") if status == "done" else job.get("partial")
    result = result if isinstance(result, dict) else {}
    nodes = result.get("nodes") if isinstance(result.get("nodes"), list) else []
    edges = result.get("edges") if isinstance(result.get("edges"), list) else []
    now = datetime.utcnow().isoformat()
    source_pdf = job.get("source_pdf")
    values = {
        "filename": job.get("filename") or "input.md",
        "node_count": len(nodes),
        "edge_count": len(edges),
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
        "source_origin": job.get("source_origin") or "markdown",
        "experimental_logic_ir": int(bool(job.get("_experimental_logic_ir"))),
        "updated_at": now,
    }
    try:
        with connect_database() as conn:
            existing = conn.execute(
                "SELECT user_id FROM history WHERE id = ? FOR UPDATE",
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
        persist_graph(job["job_id"], "history", nodes, edges)
        _invalidate_api_graph(job["job_id"])
        job["_user_id"] = int(owner_id)
        job["_history_persisted"] = True
        return True
    except Exception as exc:
        app.logger.error("Unable to persist history %s: %s", job.get("job_id"), exc)
        return False


def _job_access_allowed(job: dict) -> bool:
    owner_id = job.get("_user_id")
    if owner_id is None:
        return True
    user = _current_user()
    return bool(user and int(user["id"]) == int(owner_id))


@app.route("/api/v2/history", methods=["GET"])
def history_list():
    user = _current_user()
    if not user:
        return jsonify({"error": "not authenticated"}), 401
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
    if not job:
        row = _get_db().execute(
            "SELECT status FROM history WHERE id = ? AND user_id = ?",
            (job_id, user["id"]),
        ).fetchone()
        if row and (row["status"] or "done") == "done":
            return jsonify({"ok": True, "id": job_id}), 201
        return jsonify({"error": "job not done or not found"}), 400
    if job["status"] != "done":
        return jsonify({"error": "job not done or not found"}), 400
    if not _upsert_job_history(job, "done", int(user["id"])):
        return jsonify({"error": "unable to save history"}), 500
    return jsonify({"ok": True, "id": job_id}), 201


@app.route("/api/v2/history/<hist_id>", methods=["GET"])
def history_get(hist_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "not authenticated"}), 401
    db = _get_db()
    row = db.execute(
        "SELECT * FROM history WHERE id = ? AND user_id = ?", (hist_id, user["id"])
    ).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    status = row["status"] if "status" in row.keys() else "done"
    if status != "done":
        return jsonify({"error": "history item is not complete", "status": status}), 409
    graph = _load_api_graph(hist_id)
    nodes = graph["nodes"]
    # Backfill node_index_in_doc for old records that lack it (use node id as proxy)
    for n in nodes:
        if "node_index_in_doc" not in n or n["node_index_in_doc"] is None:
            n["node_index_in_doc"] = n.get("id", 0)
        if not n.get("source_statement"):
            n["source_statement"] = _node_original_statement(n)
    # Older saved graphs may have crossed the former display normalizer, which
    # collapsed matrix row separators.  Repair only the response projection;
    # the historical JSON remains immutable.
    nodes = _legacy_display_nodes(nodes, row["source_markdown"] or "")
    return jsonify({
        "id": row["id"],
        "filename": row["filename"],
        "node_count": row["node_count"],
        "edge_count": row["edge_count"],
        "created_at": row["created_at"],
        "nodes": nodes,
        "edges": graph["edges"],
        "latex_macros": json.loads(row["latex_macros"] or "{}"),
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
    if not _history_resume_available(row):
        return jsonify({"error": "Recovery cache is unavailable"}), 410
    llm_config = _resume_llm_config(request.get_json(silent=True) or {})
    if llm_config is None:
        return jsonify({"error": "Complete LLM and embedding configuration is required"}), 400

    artifact_dir = _persistent_job_dir(hist_id)
    source_format = row["source_format"] or "markdown"
    source_origin = row["source_origin"] if "source_origin" in row.keys() else "markdown"
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
            try:
                latex_macros = json.loads(row["latex_macros"] or "{}")
            except json.JSONDecodeError:
                latex_macros = {}
            graph = _load_api_graph(hist_id)
            partial_nodes = graph["nodes"]
            partial_edges = graph["edges"]
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
                "stages_done": _json_list(row["stages_done_json"] or "[]"),
                "result": None,
                "partial": {"nodes": partial_nodes, "edges": partial_edges},
                "error": None,
                "source_markdown": row["source_markdown"] or "",
                "latex_macros": latex_macros,
                "latex_macro_warnings": [],
                "source_format": source_format,
                "source_origin": source_origin,
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
        if status_code == 429:
            return _pipeline_busy_response()
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
    db = _get_db()
    row = db.execute(
        "SELECT filename, source_markdown FROM history WHERE id = ? AND user_id = ?", (hist_id, user["id"])
    ).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    if row["source_markdown"]:
        return jsonify({"markdown": row["source_markdown"], "filename": row["filename"]})
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
    try:
        _cancel_job_record(
            hist_id,
            int(user["id"]),
            artifact_dir=artifact_dir or str(_persistent_job_dir(hist_id)),
        )
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    except (OSError, ValueError) as exc:
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
    body = request.get_json(silent=True) or {}
    user_proof = body.get("userProof") or ""
    versions = body.get("versions") if isinstance(body.get("versions"), list) else []
    ai_messages = body.get("aiMessages") if isinstance(body.get("aiMessages"), list) else []
    imports = body.get("imports") if isinstance(body.get("imports"), list) else []
    now = datetime.utcnow().isoformat()
    db = _get_db()
    db.execute(
        """INSERT INTO proof_workspaces
              (user_id, graph_id, node_id, user_proof, versions_json, ai_messages_json, imports_json, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON DUPLICATE KEY UPDATE
              user_proof       = VALUES(user_proof),
              versions_json    = VALUES(versions_json),
              ai_messages_json = VALUES(ai_messages_json),
              imports_json     = VALUES(imports_json),
              updated_at       = VALUES(updated_at)""",
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


# ── Education endpoints ─────────────────────────────────────────────────────

_EDUCATION_PROGRESS_STATES = {"not_started", "in_progress", "mastered", "needs_review"}


def _education_enabled() -> bool:
    return os.environ.get("MATHWEAVER_EDU_ENABLED", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


class EducationAIError(RuntimeError):
    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code = code
        self.status = status


def _education_llm_config(user_id: int | None = None) -> dict | None:
    dedicated = _complete_llm_config({
        "api_url": os.environ.get("MATHWEAVER_EDU_LLM_API_URL"),
        "model_name": os.environ.get("MATHWEAVER_EDU_LLM_MODEL"),
        "api_key": os.environ.get("MATHWEAVER_EDU_LLM_API_KEY"),
    })
    generic = _complete_llm_config({
        "api_url": os.environ.get("PDFPIPELINE_API_URL") or os.environ.get("LLM_API_URL"),
        "model_name": os.environ.get("PDFPIPELINE_MODEL_NAME") or os.environ.get("LLM_MODEL_NAME"),
        "api_key": os.environ.get("PDFPIPELINE_API_KEY") or os.environ.get("OPENAI_API_KEY"),
    })
    config = dedicated or generic
    if config is None and user_id is not None:
        user = _get_db().execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        config = _active_user_llm_config(user)
    if config is None:
        return None
    return {
        **config,
        "num_threads": os.environ.get("MATHWEAVER_EDU_LLM_THREADS", "4"),
    }


def _education_ai_error_code(error) -> str | None:
    if isinstance(error, EducationAIError):
        return error.code
    message = str(error or "").strip()
    if message in {"education AI is not configured", "education_ai_unconfigured"}:
        return "education_ai_unconfigured"
    if message in {"education AI daily limit reached", "education_ai_limit_reached"}:
        return "education_ai_limit_reached"
    if message in {"assessment_invalid_result", "assessment generation did not return a valid result"}:
        return "assessment_invalid_result"
    return None


def _education_ai_error_response(error, fallback_code: str, fallback_status: int = 503):
    code = _education_ai_error_code(error) or fallback_code
    status = error.status if isinstance(error, EducationAIError) else fallback_status
    response = jsonify({"error": str(error), "code": code})
    if code == "education_ai_busy":
        response.headers["Retry-After"] = str(_CAPACITY_RETRY_AFTER)
    return response, status


def _education_safe_error_message(error, config: dict | None = None) -> str:
    message = str(error or "education AI request failed")
    api_key = str((config or {}).get("api_key") or "")
    if api_key:
        message = message.replace(api_key, "[redacted]")
    return message


def _education_daily_limit() -> int:
    try:
        return max(0, int(os.environ.get("MATHWEAVER_EDU_AI_DAILY_LIMIT", "50")))
    except ValueError:
        return 50


def _education_require_user(required_role: str | None = None):
    if not _education_enabled():
        return None, (jsonify({"error": "education feature is disabled"}), 404)
    user = _current_user()
    if not user:
        return None, (jsonify({"error": "not authenticated"}), 401)
    role = user["education_role"]
    if role not in {"teacher", "student"}:
        return None, (jsonify({
            "error": "choose an education role before entering education space",
            "code": "education_role_required",
        }), 409)
    if role == "teacher" and not bool(user["can_teach"]):
        return None, (jsonify({
            "error": "teacher access has been revoked",
            "code": "teacher_access_revoked",
        }), 403)
    if required_role and role != required_role:
        return None, (jsonify({
            "error": "this action is only available in the selected education role",
            "code": "education_role_forbidden",
        }), 403)
    return user, None


def _education_student_profile(body: dict) -> tuple[str | None, str | None, tuple | None]:
    student_name = " ".join(str(body.get("studentName") or "").strip().split())
    student_number = str(body.get("studentNumber") or "").strip().upper()
    if not student_name:
        return None, None, (jsonify({"error": "student name is required", "code": "student_name_required"}), 400)
    if len(student_name) > 50:
        return None, None, (jsonify({"error": "student name is too long", "code": "student_name_invalid"}), 400)
    if not student_number:
        return None, None, (jsonify({"error": "student number is required", "code": "student_number_required"}), 400)
    if not re.fullmatch(r"[A-Z0-9_-]{1,32}", student_number):
        return None, None, (jsonify({"error": "student number is invalid", "code": "student_number_invalid"}), 400)
    return student_name, student_number, None


def _education_membership(class_id: str, user_id: int):
    return _get_db().execute(
        """SELECT m.role, m.student_name, m.student_number, c.* FROM education_memberships m
           JOIN education_classes c ON c.id = m.class_id
           WHERE m.class_id = ? AND m.user_id = ? AND m.removed_at IS NULL
             AND c.archived_at IS NULL""",
        (class_id, user_id),
    ).fetchone()


def _education_require_membership(class_id: str, user, allowed_roles=None, *, require_student_profile=True):
    membership = _education_membership(class_id, int(user["id"]))
    if not membership:
        return None, (jsonify({"error": "class not found"}), 404)
    if membership["role"] != user["education_role"]:
        return None, (jsonify({"error": "class is not available in the selected education role", "code": "education_role_forbidden"}), 403)
    if allowed_roles and membership["role"] not in allowed_roles:
        return None, (jsonify({"error": "forbidden"}), 403)
    if require_student_profile and membership["role"] == "student" and (
        not membership["student_name"] or not membership["student_number"]
    ):
        return None, (jsonify({
            "error": "student profile is incomplete",
            "code": "student_profile_required",
            "classId": class_id,
        }), 409)
    return membership, None


def _education_json(raw, default):
    if isinstance(raw, type(default)):
        return raw
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default
    return value if isinstance(value, type(default)) else default


def _education_snapshot_graph(snapshot) -> dict:
    return _load_api_graph(str(snapshot["id"]))


def _education_node_title(node: dict) -> str:
    return str(
        node.get("title_zh")
        or node.get("title_en")
        or node.get("label")
        or f"节点 {node.get('id')}"
    ).strip()


def _education_snapshot_pdf_meta(row) -> dict | None:
    stored = _education_json(row["source_pdf_json"], {})
    if not stored:
        return None
    snapshot_dir = _EDUCATION_SNAPSHOT_ROOT / row["id"]
    meta = dict(stored)
    for name_key, path_key in (
        ("pdf_name", "pdf_path"),
        ("source_name", "source_path"),
        ("log_name", "log_path"),
    ):
        name = Path(str(meta.get(name_key) or "")).name
        if name:
            meta[path_key] = str(snapshot_dir / name)
    meta["pdf_url"] = f"/api/v2/edu/snapshots/{row['id']}/source-pdf"
    meta["compile_log_url"] = f"/api/v2/edu/snapshots/{row['id']}/compile-log"
    meta["locate_url"] = f"/api/v2/edu/snapshots/{row['id']}/locate"
    return meta


def _education_public_snapshot(row, include_graph=False, *, include_source=True) -> dict:
    payload = {
        "id": row["id"],
        "classId": row["class_id"],
        "snapshotType": row["snapshot_type"] if "snapshot_type" in row.keys() else "graph",
        "sourceGraphId": row["source_graph_id"],
        "filename": row["filename"],
        "nodeCount": int(row["node_count"] or 0),
        "edgeCount": int(row["edge_count"] or 0),
        "createdAt": row["created_at"],
    }
    if "bound_assignment_count" in row.keys():
        payload["boundAssignmentCount"] = int(row["bound_assignment_count"] or 0)
    if "course_order" in row.keys() and row["course_order"] is not None:
        payload["courseOrder"] = int(row["course_order"])
    if include_graph:
        graph = _education_snapshot_graph(row)
        # Snapshots are immutable teaching records. Apply the same safe,
        # response-only compatibility projection used for normal history.
        nodes = _legacy_display_nodes(graph["nodes"], row["source_markdown"] or "")
        meta = _education_snapshot_pdf_meta(row)
        payload.update({
            "nodes": nodes,
            "edges": graph["edges"],
            "sourceMarkdown": row["source_markdown"] or "" if include_source else "",
            "latexMacros": _education_json(row["latex_macros_json"], {}) if include_source else {},
            "sourcePdf": ({
                "status": meta.get("status"),
                "available": bool(meta.get("available")),
                "error": meta.get("error"),
                "pdf_url": meta.get("pdf_url"),
                "compile_log_url": meta.get("compile_log_url"),
                "locate_url": meta.get("locate_url"),
            } if meta else None),
        })
    return payload


def _education_course_graph_key(snapshot) -> str:
    source_graph_id = str(snapshot["source_graph_id"] or "").strip()
    return f"source:{source_graph_id}" if source_graph_id else f"snapshot:{snapshot['id']}"


def _education_course_graph_groups(rows) -> dict[str, dict]:
    groups: dict[str, dict] = {}
    for row in rows:
        key = _education_course_graph_key(row)
        current = groups.get(key)
        if current is None:
            groups[key] = {"canonical": row, "rows": [row]}
            continue
        current["rows"].append(row)
        canonical = current["canonical"]
        if (str(row["created_at"] or ""), str(row["id"])) < (str(canonical["created_at"] or ""), str(canonical["id"])):
            current["canonical"] = row
    return groups


def _education_course_graph_snapshot_rows(db, class_id: str):
    return db.execute(
        """SELECT s.*,
                  (SELECT COUNT(*) FROM education_assignments a WHERE a.snapshot_id = s.id)
                    AS bound_assignment_count,
                  o.sort_order AS course_order
             FROM education_snapshots s
        LEFT JOIN education_course_graph_order o
               ON o.class_id = s.class_id
              AND o.graph_key = CASE
                    WHEN s.source_graph_id IS NOT NULL AND TRIM(s.source_graph_id) != ''
                      THEN CONCAT('source:', s.source_graph_id)
                    ELSE CONCAT('snapshot:', s.id)
                  END
            WHERE s.class_id = ? AND COALESCE(s.snapshot_type, 'graph') = 'graph'
            ORDER BY CASE WHEN o.sort_order IS NULL THEN 1 ELSE 0 END ASC,
                     o.sort_order ASC, s.created_at ASC, s.id ASC""",
        (class_id,),
    ).fetchall()


def _education_public_assessments(assignment_id: str, *, role: str | None, user_id: int | None = None) -> list[dict]:
    db = _get_db()
    node_rows = db.execute(
        """SELECT assignment_id, node_id, status, last_error, updated_at
             FROM education_assessment_nodes
            WHERE assignment_id = ? ORDER BY node_id""",
        (assignment_id,),
    ).fetchall()
    question_rows = db.execute(
        """SELECT * FROM education_assessment_questions
            WHERE assignment_id = ? ORDER BY node_id, sort_order""",
        (assignment_id,),
    ).fetchall()
    questions_by_node: dict[int, list] = defaultdict(list)
    for question in question_rows:
        questions_by_node[int(question["node_id"])].append(question)
    attempts_by_node = {}
    if role == "student" and user_id is not None:
        attempts_by_node = {
            int(attempt["node_id"]): attempt
            for attempt in db.execute(
                """SELECT node_id, status, updated_at, completed_at
                     FROM education_assessment_attempts
                    WHERE assignment_id = ? AND user_id = ?""",
                (assignment_id, user_id),
            ).fetchall()
        }

    assessments = []
    for node_row in node_rows:
        node_id = int(node_row["node_id"])
        questions = questions_by_node.get(node_id, [])
        payload = {
            "nodeId": node_id,
            "status": node_row["status"],
            "questionCount": len(questions),
            "updatedAt": node_row["updated_at"],
        }
        if role == "teacher":
            generation_error = node_row["last_error"] or ""
            payload["generationError"] = generation_error
            generation_error_code = _education_ai_error_code(generation_error)
            if generation_error_code:
                payload["generationErrorCode"] = generation_error_code
            payload["questions"] = [
                {
                    "id": question["id"],
                    "nodeId": node_id,
                    "kind": question["kind"],
                    "order": int(question["sort_order"]),
                    "question": question["question"],
                    "focus": question["focus"],
                    "expectedPoints": _education_json(question["expected_points_json"], []),
                    "referenceAnswer": question["reference_answer"] or "",
                    "maxScore": float(question["max_score"] or 0),
                    "referenceMatrixReport": _education_reference_matrix_report(question["reference_answer"] or ""),
                }
                for question in questions
            ]
        elif role == "student":
            attempt = attempts_by_node.get(node_id)
            payload["attemptStatus"] = attempt["status"] if attempt else "not_started"
            payload["attemptUpdatedAt"] = attempt["updated_at"] if attempt else None
        assessments.append(payload)
    return assessments


def _education_utc_timestamp(value):
    if not value:
        return value
    timestamp = str(value)
    if timestamp.endswith("Z") or re.search(r"[+-]\d{2}:\d{2}$", timestamp):
        return timestamp
    return f"{timestamp}Z"


def _education_public_submission_summary(assignment_id: str, user_id: int) -> dict | None:
    row = _get_db().execute(
        "SELECT * FROM education_assignment_submissions WHERE assignment_id = ? AND user_id = ?",
        (assignment_id, user_id),
    ).fetchone()
    if not row:
        return None
    payload = {
        "id": row["id"],
        "status": row["status"],
        "submittedAt": row["submitted_at"],
        "updatedAt": row["updated_at"],
        "releasedAt": row["released_at"],
    }
    if row["status"] == "released":
        payload["teacherTotal"] = float(row["teacher_total"] or 0)
        payload["teacherSummary"] = row["teacher_summary"] or ""
    return payload


def _education_public_assignment(row, *, snapshot=None, path=None, role=None, user_id=None) -> dict:
    base_path = path or _education_json(row["base_path_json"], {})
    payload = {
        "id": row["id"],
        "classId": row["class_id"],
        "assignmentType": row["assignment_type"] if "assignment_type" in row.keys() else "graph",
        "directStructureVersion": int(row["direct_structure_version"] or 0) if "direct_structure_version" in row.keys() else 0,
        "snapshotId": row["snapshot_id"],
        "title": row["title"],
        "targetNodeId": row["target_node_id"],
        "dueAt": _education_utc_timestamp(row["due_at"]),
        "status": row["status"],
        "summary": row["summary"],
        "version": row["version"],
        "publishedAt": row["published_at"],
        "gradesPublishedAt": row["grades_published_at"] if "grades_published_at" in row.keys() else None,
        "updatedAt": row["updated_at"],
        "role": role,
        "path": base_path,
        "assessments": _education_public_assessments(
            row["id"], role=role, user_id=int(user_id) if user_id is not None else None
        ),
    }
    if payload["assignmentType"] == "direct":
        payload["growthChallenge"] = education_game.challenge_access_payload(
            _get_db(),
            class_id=row["class_id"],
            assignment_id=row["id"],
            user_id=int(user_id) if role == "student" and user_id is not None else None,
        )
    if role == "student" and user_id is not None:
        payload["submission"] = _education_public_submission_summary(row["id"], int(user_id))
    if snapshot is not None:
        # Preserve the historical graph-assignment student snapshot contract.
        # Direct assignments are the exception: their imported source belongs
        # exclusively to the teacher review flow.
        include_snapshot_source = role == "teacher" or payload["assignmentType"] != "direct"
        payload["snapshot"] = _education_public_snapshot(snapshot, include_graph=True, include_source=include_snapshot_source)
    if role == "teacher":
        source = _get_db().execute(
            "SELECT filename, mime_type, source_origin, storage_name FROM education_assignment_sources WHERE assignment_id = ?",
            (row["id"],),
        ).fetchone()
        if source:
            payload["source"] = {
                "filename": source["filename"],
                "mimeType": source["mime_type"],
                "origin": source["source_origin"],
                "hasOriginalFile": bool(source["storage_name"]),
                "url": f"/api/v2/edu/assignments/{row['id']}/source" if source["storage_name"] else None,
            }
    return payload


def _education_progress_map(assignment_id: str, user_id: int) -> dict[int, dict]:
    rows = _get_db().execute(
        """SELECT node_id, state, mastery_source, diagnostic_summary, updated_at
           FROM education_node_progress WHERE assignment_id = ? AND user_id = ?""",
        (assignment_id, user_id),
    ).fetchall()
    return {
        int(row["node_id"]): {
            "state": row["state"],
            "masterySource": row["mastery_source"],
            "diagnosticSummary": row["diagnostic_summary"],
            "updatedAt": row["updated_at"],
        }
        for row in rows
    }


def _education_consume_ai_quota(user_id: int) -> tuple[bool, int]:
    limit = _education_daily_limit()
    if limit <= 0:
        return False, 0
    today = datetime.utcnow().date().isoformat()
    now = datetime.utcnow().isoformat()
    db = _get_db()
    row = db.execute(
        "SELECT request_count FROM education_ai_usage WHERE user_id = ? AND usage_day = ? FOR UPDATE",
        (user_id, today),
    ).fetchone()
    count = int(row["request_count"] or 0) if row else 0
    if count >= limit:
        db.rollback()
        return False, 0
    count += 1
    if row:
        db.execute(
            "UPDATE education_ai_usage SET request_count = ?, updated_at = ? WHERE user_id = ? AND usage_day = ?",
            (count, now, user_id, today),
        )
    else:
        db.execute(
            "INSERT INTO education_ai_usage (user_id, usage_day, request_count, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, today, count, now),
        )
    db.commit()
    return True, max(limit - count, 0)


def _education_ai_tasks(
    *,
    user_id: int,
    task_id: str,
    task_kind: str,
    tasks: dict[str, dict],
    scope: str,
):
    config = _education_llm_config(user_id)
    if not config:
        raise EducationAIError(
            "education_ai_unconfigured",
            "education AI is not configured",
            503,
        )
    capacity_lease = _AI_CAPACITY_POOL.try_acquire()
    if capacity_lease is None:
        raise EducationAIError(
            "education_ai_busy",
            "education AI capacity is busy; retry shortly",
            429,
        )
    try:
        allowed, _remaining = _education_consume_ai_quota(user_id)
        if not allowed:
            raise EducationAIError(
                "education_ai_limit_reached",
                "education AI daily limit reached",
                429,
            )
        record_id = uuid.uuid4().hex
        now = datetime.utcnow().isoformat()
        db = _get_db()
        db.execute(
            """INSERT INTO education_ai_tasks
                 (id, task_key, user_id, task_kind, scope, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'running', ?, ?)""",
            (record_id, task_id, user_id, task_kind, scope, now, now),
        )
        db.commit()
        try:
            context = create_education_context(_DATA_ROOT, config)
            checkpoint_dir = _EDUCATION_ROOT / scope / "llm_tasks" / task_kind
            result = run_structured_education_tasks(
                context=context,
                tasks=tasks,
                task_kind=task_kind,
                checkpoint_dir=checkpoint_dir,
            )
            db.execute(
                "UPDATE education_ai_tasks SET status = 'done', updated_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), record_id),
            )
            db.commit()
            return result
        except Exception as exc:
            safe_error = _education_safe_error_message(exc, config)
            db.execute(
                """UPDATE education_ai_tasks
                   SET status = 'failed', error = ?, updated_at = ? WHERE id = ?""",
                (safe_error[:1000], datetime.utcnow().isoformat(), record_id),
            )
            db.commit()
            if safe_error != str(exc):
                raise RuntimeError(safe_error) from None
            raise
    finally:
        capacity_lease.release()


def _education_ai_task(*, user_id: int, task_id: str, task_kind: str, payload: dict, scope: str):
    result = _education_ai_tasks(
        user_id=user_id,
        task_id=task_id,
        task_kind=task_kind,
        tasks={task_id: payload},
        scope=scope,
    )
    value = result.get(task_id)
    if not isinstance(value, dict):
        raise RuntimeError("education AI task did not return a valid result")
    return value


def _education_path_payload(snapshot, deterministic: dict, *, progress=None) -> dict:
    nodes = _education_snapshot_graph(snapshot)["nodes"]
    node_by_id = {int(node["id"]): node for node in nodes if isinstance(node.get("id"), int)}
    candidate_ids = deterministic.get("candidateNodeIds") or []
    base_path = [
        {
            key: step[key]
            for key in ("nodeId", "order", "stage", "role", "required", "cycle")
            if key in step
        }
        for step in deterministic.get("steps") or []
        if isinstance(step, dict)
    ]
    return {
        "targetNodeId": deterministic["targetNodeId"],
        "allowedNodeIds": candidate_ids,
        "candidateNodes": [
            {
                "nodeId": node_id,
                "title": _education_node_title(node_by_id[node_id]),
                "statement": node_by_id[node_id].get("content")
                or node_by_id[node_id].get("source_statement")
                or "",
            }
            for node_id in candidate_ids
            if node_id in node_by_id
        ],
        "dependencyEdges": deterministic.get("edges") or [],
        "basePath": base_path,
        "studentProgress": progress or {},
    }


def _education_order_warnings(path: dict) -> list[dict]:
    order_by_node = {
        int(step["nodeId"]): index
        for index, step in enumerate(path.get("steps") or [])
        if isinstance(step, dict) and isinstance(step.get("nodeId"), int)
    }
    warnings = []
    for edge in path.get("edges") or []:
        prerequisite = edge.get("from")
        dependent = edge.get("to")
        if prerequisite in order_by_node and dependent in order_by_node:
            if order_by_node[prerequisite] > order_by_node[dependent]:
                warnings.append({
                    "from": prerequisite,
                    "to": dependent,
                    "message": "前置知识排在了后续知识之后",
                })
    return warnings


def _education_path_node_ids(path: dict) -> list[int]:
    node_ids = []
    for step in path.get("steps") or []:
        if not isinstance(step, dict):
            continue
        try:
            node_id = int(step.get("nodeId"))
        except (TypeError, ValueError):
            continue
        if node_id not in node_ids:
            node_ids.append(node_id)
    return node_ids


def _education_equal_question_scores(count: int) -> list[float]:
    if count <= 0:
        return []
    base = round(100.0 / count, 1)
    scores = [base for _ in range(count)]
    scores[-1] = round(100.0 - sum(scores[:-1]), 1)
    return scores


def _education_rebalance_question_scores(db, assignment_id: str) -> None:
    rows = db.execute(
        "SELECT id FROM education_assessment_questions WHERE assignment_id = ? ORDER BY node_id, sort_order",
        (assignment_id,),
    ).fetchall()
    scores = _education_equal_question_scores(len(rows))
    db.executemany(
        "UPDATE education_assessment_questions SET max_score = ?, updated_at = ? WHERE id = ?",
        [(scores[index], datetime.utcnow().isoformat(), row["id"]) for index, row in enumerate(rows)],
    )


def _education_reference_matrix_report(reference_answer: str) -> dict:
    report = analyze_matrix_answer(reference_answer, reference_answer)
    return {
        "status": report.get("status") or "not_applicable",
        "summary": report.get("summary") or "",
        "issues": report.get("issues") or [],
        "flowCount": int(report.get("flowCount") or 0),
        "referenceFlowCount": int(report.get("referenceFlowCount") or 0),
        "referenceStatus": report.get("referenceStatus"),
    }


def _education_scoring_validation(db, assignment_id: str, *, allow_empty_points: bool = False) -> tuple[bool, dict]:
    rows = db.execute(
        "SELECT id, node_id, question, expected_points_json, reference_answer, max_score FROM education_assessment_questions WHERE assignment_id = ? ORDER BY node_id, sort_order",
        (assignment_id,),
    ).fetchall()
    if not rows:
        return True, {"totalScore": 0.0, "invalidQuestions": []}
    invalid = []
    total = 0.0
    for row in rows:
        points = _education_json(row["expected_points_json"], [])
        score = float(row["max_score"] or 0)
        total += score
        question = str(row["question"] or "").strip()
        reference_answer = str(row["reference_answer"] or "").strip()
        reason = None
        if not question or score <= 0 or not reference_answer or (not allow_empty_points and not points):
            reason = "scoring_standard_incomplete"
        elif _education_reference_matrix_report(reference_answer)["status"] in {"contradicted", "structural_invalid"}:
            reason = "reference_matrix_invalid"
        if reason:
            invalid.append({"questionId": row["id"], "nodeId": int(row["node_id"]), "reason": reason})
    return not invalid and abs(total - 100.0) < 0.001, {"totalScore": round(total, 1), "invalidQuestions": invalid}


def _education_reconcile_assessment_nodes(db, assignment_id: str, path: dict, *, initial=False) -> None:
    now = datetime.utcnow().isoformat()
    current_ids = set(_education_path_node_ids(path))
    existing_ids = {
        int(row["node_id"])
        for row in db.execute(
            "SELECT node_id FROM education_assessment_nodes WHERE assignment_id = ?",
            (assignment_id,),
        ).fetchall()
    }
    for node_id in existing_ids - current_ids:
        db.execute(
            "DELETE FROM education_assessment_questions WHERE assignment_id = ? AND node_id = ?",
            (assignment_id, node_id),
        )
        db.execute(
            "DELETE FROM education_assessment_attempts WHERE assignment_id = ? AND node_id = ?",
            (assignment_id, node_id),
        )
        db.execute(
            "DELETE FROM education_assessment_nodes WHERE assignment_id = ? AND node_id = ?",
            (assignment_id, node_id),
        )
    for node_id in current_ids - existing_ids:
        db.execute(
            """INSERT INTO education_assessment_nodes
                 (assignment_id, node_id, status, updated_at)
               VALUES (?, ?, 'pending', ?)""",
            (assignment_id, node_id, now),
        )
    if existing_ids - current_ids:
        _education_rebalance_question_scores(db, assignment_id)
    if initial:
        db.execute(
            """UPDATE education_assessment_nodes
                  SET status = 'pending', last_error = NULL, updated_at = ?
                WHERE assignment_id = ?""",
            (now, assignment_id),
        )


def _education_replace_assessment_questions(
    db,
    assignment_id: str,
    node_id: int,
    result: dict,
    expected_category: str | None = None,
) -> None:
    category = result.get("category")
    if expected_category is not None and category != expected_category:
        raise ValueError("assessment category does not match the task")
    required_kinds = ASSESSMENT_QUESTION_KINDS.get(category)
    questions = result.get("questions")
    if not required_kinds or not isinstance(questions, list):
        raise ValueError("invalid assessment result")
    if len(questions) != len(required_kinds):
        raise ValueError("assessment result does not contain the required question kinds")
    by_kind = {question.get("kind"): question for question in questions if isinstance(question, dict)}
    if len(by_kind) != len(required_kinds) or set(by_kind) != set(required_kinds):
        raise ValueError("assessment result does not contain the required question kinds")
    for question in questions:
        if (
            not isinstance(question, dict)
            or not str(question.get("question") or "").strip()
            or not str(question.get("focus") or "").strip()
            or not isinstance(question.get("expectedPoints"), list)
            or not question["expectedPoints"]
            or not all(isinstance(point, str) and point.strip() for point in question["expectedPoints"])
            or not str(question.get("referenceAnswer") or "").strip()
        ):
            raise ValueError("assessment result contains empty question fields")
    existing_scores = [
        float(row["max_score"] or 0)
        for row in db.execute(
            "SELECT max_score FROM education_assessment_questions WHERE assignment_id = ? AND node_id = ? ORDER BY sort_order",
            (assignment_id, node_id),
        ).fetchall()
    ]
    preserved_scores = existing_scores if len(existing_scores) == len(required_kinds) and sum(existing_scores) > 0 else [0.0] * len(required_kinds)
    now = datetime.utcnow().isoformat()
    db.execute(
        "DELETE FROM education_assessment_questions WHERE assignment_id = ? AND node_id = ?",
        (assignment_id, node_id),
    )
    for order, kind in enumerate(required_kinds, start=1):
        question = by_kind[kind]
        db.execute(
            """INSERT INTO education_assessment_questions
                 (id, assignment_id, node_id, kind, question, focus,
                  expected_points_json, reference_answer, max_score, sort_order, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                uuid.uuid4().hex,
                assignment_id,
                node_id,
                kind,
                str(question.get("question") or "").strip(),
                str(question.get("focus") or "").strip(),
                json.dumps(question.get("expectedPoints") or [], ensure_ascii=False),
                str(question.get("referenceAnswer") or "").strip(),
                preserved_scores[order - 1],
                order,
                now,
                now,
            ),
        )
    db.execute(
        """UPDATE education_assessment_nodes
              SET status = 'ready', last_error = NULL, updated_at = ?
            WHERE assignment_id = ? AND node_id = ?""",
        (now, assignment_id, node_id),
    )


def _education_begin_assessment_write(
    db,
    assignment_id: str,
    node_id: int,
    expected_node_updated_at: str,
    *,
    question_id: str | None = None,
    expected_question_updated_at: str | None = None,
) -> None:
    """Lock and revalidate a draft assessment immediately before mutation."""
    assignment = db.execute(
        "SELECT status, base_path_json FROM education_assignments WHERE id = ? FOR UPDATE",
        (assignment_id,),
    ).fetchone()
    path_node_ids = _education_path_node_ids(_education_json(assignment["base_path_json"], {})) if assignment else []
    node = db.execute(
        "SELECT updated_at FROM education_assessment_nodes WHERE assignment_id = ? AND node_id = ? FOR UPDATE",
        (assignment_id, node_id),
    ).fetchone()
    if (
        not assignment
        or assignment["status"] != "draft"
        or node_id not in path_node_ids
        or not node
        or node["updated_at"] != expected_node_updated_at
    ):
        raise EducationAIError(
            "assessment_draft_changed",
            "assessment draft changed before the generated result could be saved",
            409,
        )
    if question_id is not None:
        question = db.execute(
            "SELECT updated_at FROM education_assessment_questions WHERE id = ? AND assignment_id = ? AND node_id = ? FOR UPDATE",
            (question_id, assignment_id, node_id),
        ).fetchone()
        if not question or question["updated_at"] != expected_question_updated_at:
            raise EducationAIError(
                "assessment_draft_changed",
                "assessment draft changed before the generated result could be saved",
                409,
            )


def _education_mark_assessment_failed(db, assignment_id: str, node_id: int, error: str) -> None:
    db.execute(
        """UPDATE education_assessment_nodes
              SET status = 'failed', last_error = ?, updated_at = ?
            WHERE assignment_id = ? AND node_id = ?""",
        (str(error or "assessment generation failed")[:1000], datetime.utcnow().isoformat(), assignment_id, node_id),
    )


def _education_generate_initial_assessments(*, assignment_id: str, user_id: int, snapshot, path: dict) -> None:
    nodes = _education_snapshot_graph(snapshot)["nodes"]
    tasks = build_assessment_tasks(nodes, path)
    db = _get_db()
    node_ids = _education_path_node_ids(path)
    try:
        results = _education_ai_tasks(
            user_id=user_id,
            task_id=assignment_id,
            task_kind="assessment",
            tasks=tasks,
            scope=f"assignments/{assignment_id}",
        )
    except Exception as exc:
        for node_id in node_ids:
            _education_mark_assessment_failed(db, assignment_id, node_id, str(exc))
        db.commit()
        return
    for node_id in node_ids:
        result = results.get(str(node_id))
        if not isinstance(result, dict):
            _education_mark_assessment_failed(db, assignment_id, node_id, "assessment_invalid_result")
            continue
        try:
            _education_replace_assessment_questions(
                db,
                assignment_id,
                node_id,
                result,
                (tasks.get(str(node_id)) or {}).get("category"),
            )
        except (TypeError, ValueError):
            _education_mark_assessment_failed(db, assignment_id, node_id, "assessment_invalid_result")
    _education_rebalance_question_scores(db, assignment_id)
    db.commit()


@app.route("/api/v2/edu/status", methods=["GET"])
def education_status():
    user = _current_user()
    used = 0
    if user:
        today = datetime.utcnow().date().isoformat()
        row = _get_db().execute(
            "SELECT request_count FROM education_ai_usage WHERE user_id = ? AND usage_day = ?",
            (user["id"], today),
        ).fetchone()
        used = int(row["request_count"] or 0) if row else 0
    limit = _education_daily_limit()
    return jsonify({
        "enabled": _education_enabled(),
        "aiAvailable": _education_llm_config(int(user["id"]) if user else None) is not None,
        "aiDailyLimit": limit,
        "aiRemaining": max(0, limit - used),
    })


@app.route("/api/v2/edu/classes", methods=["GET", "POST"])
def education_classes():
    user, error = _education_require_user("teacher" if request.method == "POST" else None)
    if error:
        return error
    db = _get_db()
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        title = str(body.get("title") or "").strip()
        if not title:
            return jsonify({"error": "class title is required"}), 400
        class_id = uuid.uuid4().hex
        invite_code = secrets.token_hex(4).upper()
        now = datetime.utcnow().isoformat()
        db.execute(
            """INSERT INTO education_classes
                 (id, owner_user_id, title, invite_code, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (class_id, user["id"], title[:120], invite_code, now),
        )
        db.execute(
            """INSERT INTO education_memberships (class_id, user_id, role, joined_at)
               VALUES (?, ?, 'teacher', ?)""",
            (class_id, user["id"], now),
        )
        db.commit()
        return jsonify({
            "class": {
                "id": class_id,
                "title": title[:120],
                "inviteCode": invite_code,
                "role": "teacher",
                "memberCount": 1,
                "assignmentCount": 0,
                "createdAt": now,
            },
        }), 201

    assignment_count_filter = "ca.status = 'published'" if user["education_role"] == "student" else "ca.status != 'archived'"
    rows = db.execute(
        f"""SELECT c.*, m.role, m.student_name, m.student_number,
                  (SELECT COUNT(*) FROM education_memberships cm
                   WHERE cm.class_id = c.id AND cm.removed_at IS NULL) AS member_count,
                  (SELECT COUNT(*) FROM education_assignments ca
                   WHERE ca.class_id = c.id AND {assignment_count_filter}) AS assignment_count
           FROM education_memberships m
           JOIN education_classes c ON c.id = m.class_id
           WHERE m.user_id = ? AND m.role = ? AND m.removed_at IS NULL
             AND c.archived_at IS NULL
           ORDER BY c.created_at DESC""",
        (user["id"], user["education_role"]),
    ).fetchall()
    return jsonify({
        "classes": [
            {
                "id": row["id"],
                "title": row["title"],
                "inviteCode": row["invite_code"] if row["role"] == "teacher" else None,
                "role": row["role"],
                "memberCount": row["member_count"],
                "assignmentCount": row["assignment_count"],
                "studentName": row["student_name"] if row["role"] == "student" else None,
                "studentNumber": row["student_number"] if row["role"] == "student" else None,
                "profileComplete": (
                    bool(row["student_name"] and row["student_number"])
                    if row["role"] == "student" else True
                ),
                "createdAt": row["created_at"],
            }
            for row in rows
        ],
    })


@app.route("/api/v2/edu/classes/<class_id>/join", methods=["POST"])
def education_class_join(class_id: str):
    user, error = _education_require_user("student")
    if error:
        return error
    body = request.get_json(silent=True) or {}
    invite_code = str(body.get("inviteCode") or class_id).strip().upper()
    db = _get_db()
    class_row = db.execute(
        """SELECT * FROM education_classes
           WHERE archived_at IS NULL AND (id = ? OR invite_code = ?)""",
        (class_id, invite_code),
    ).fetchone()
    if not class_row or invite_code != class_row["invite_code"]:
        return jsonify({"error": "invalid invite code", "code": "invalid_invite_code"}), 404
    existing = db.execute(
        "SELECT role, removed_at FROM education_memberships WHERE class_id = ? AND user_id = ?",
        (class_row["id"], user["id"]),
    ).fetchone()
    if existing:
        if existing["role"] != "student":
            return jsonify({"error": "this account is already the teacher of this class", "code": "class_role_conflict"}), 409
        if existing["removed_at"]:
            return jsonify({
                "error": "this account has been removed from the class",
                "code": "class_membership_removed",
            }), 403
        role = "student"
        student_name, student_number, error = _education_student_profile(body)
        if error:
            return error
        try:
            db.execute(
                """UPDATE education_memberships
                   SET student_name = ?, student_number = ?
                   WHERE class_id = ? AND user_id = ?""",
                (student_name, student_number, class_row["id"], user["id"]),
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            return jsonify({
                "error": "student number is already used in this class",
                "code": "student_number_conflict",
            }), 409
    else:
        role = "student"
        student_name, student_number, error = _education_student_profile(body)
        if error:
            return error
        try:
            db.execute(
                """INSERT INTO education_memberships
                     (class_id, user_id, role, student_name, student_number, joined_at)
                   VALUES (?, ?, 'student', ?, ?, ?)""",
                (
                    class_row["id"], user["id"], student_name, student_number,
                    datetime.utcnow().isoformat(),
                ),
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            return jsonify({
                "error": "student number is already used in this class",
                "code": "student_number_conflict",
            }), 409
    return jsonify({
        "class": {
            "id": class_row["id"],
            "title": class_row["title"],
            "role": role,
            "studentName": student_name,
            "studentNumber": student_number,
            "profileComplete": True,
            "createdAt": class_row["created_at"],
        },
    })


@app.route("/api/v2/edu/classes/<class_id>/membership", methods=["PUT"])
def education_class_membership_update(class_id: str):
    user, error = _education_require_user("student")
    if error:
        return error
    membership, error = _education_require_membership(
        class_id, user, {"student"}, require_student_profile=False,
    )
    if error:
        return error
    body = request.get_json(silent=True) or {}
    student_name, student_number, error = _education_student_profile(body)
    if error:
        return error
    db = _get_db()
    try:
        db.execute(
            """UPDATE education_memberships
               SET student_name = ?, student_number = ?
               WHERE class_id = ? AND user_id = ?""",
            (student_name, student_number, class_id, user["id"]),
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        return jsonify({
            "error": "student number is already used in this class",
            "code": "student_number_conflict",
        }), 409
    return jsonify({
        "class": {
            "id": membership["id"],
            "title": membership["title"],
            "role": "student",
            "studentName": student_name,
            "studentNumber": student_number,
            "profileComplete": True,
        },
    })


def _education_require_class_teacher(class_id: str, user):
    membership, error = _education_require_membership(class_id, user, {"teacher"})
    if error:
        return None, error
    if int(membership["owner_user_id"]) != int(user["id"]):
        return None, (jsonify({"error": "forbidden"}), 403)
    return membership, None


@app.route("/api/v2/edu/classes/<class_id>", methods=["PATCH", "DELETE"])
def education_class_manage(class_id: str):
    user, error = _education_require_user("teacher")
    if error:
        return error
    _membership, error = _education_require_class_teacher(class_id, user)
    if error:
        return error
    db = _get_db()
    if request.method == "PATCH":
        body = request.get_json(silent=True) or {}
        title = str(body.get("title") or "").strip()
        if not title:
            return jsonify({"error": "class title is required", "code": "class_title_required"}), 400
        title = title[:120]
        db.execute(
            "UPDATE education_classes SET title = ? WHERE id = ?",
            (title, class_id),
        )
        db.commit()
        return jsonify({"class": {"id": class_id, "title": title}})

    now = datetime.utcnow().isoformat()
    db.execute(
        "UPDATE education_classes SET archived_at = ? WHERE id = ? AND archived_at IS NULL",
        (now, class_id),
    )
    db.commit()
    return jsonify({"ok": True, "archivedAt": now})


@app.route("/api/v2/edu/classes/<class_id>/game-summary", methods=["GET"])
def education_class_game_summary(class_id: str):
    user, error = _education_require_user()
    if error:
        return error
    membership, error = _education_require_membership(class_id, user)
    if error:
        return error
    return jsonify(
        education_game.build_game_summary(
            _get_db(),
            class_row=membership,
            user_id=int(user["id"]),
            role=membership["role"],
        )
    )


@app.route("/api/v2/edu/classes/<class_id>/game-settings", methods=["PATCH"])
def education_class_game_settings(class_id: str):
    user, error = _education_require_user("teacher")
    if error:
        return error
    membership, error = _education_require_class_teacher(class_id, user)
    if error:
        return error
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "a JSON object is required"}), 400
    unsupported = sorted(set(body) - {"studentExperience", "weeklyXpGoal"})
    if unsupported:
        return jsonify({"error": "unsupported game setting"}), 400
    if not body:
        return jsonify({"error": "studentExperience or weeklyXpGoal is required"}), 400

    if "studentExperience" in body:
        if not isinstance(body["studentExperience"], str) or body["studentExperience"] not in education_game.VALID_EXPERIENCES:
            return jsonify({"error": "studentExperience must be classic or map"}), 400

    if "weeklyXpGoal" in body:
        value = body["weeklyXpGoal"]
        if isinstance(value, bool) or not isinstance(value, int) or not 10 <= value <= 500:
            return jsonify({"error": "weeklyXpGoal must be an integer between 10 and 500"}), 400

    db = _get_db()
    try:
        row = db.execute(
            "SELECT * FROM education_classes WHERE id = ? FOR UPDATE",
            (class_id,),
        ).fetchone()
        if not row:
            db.rollback()
            return jsonify({"error": "class not found"}), 404
        previous = education_game.settings_payload(row)
        experience = body.get("studentExperience", previous["studentExperience"])
        weekly_goal = body.get("weeklyXpGoal", previous["weeklyXpGoal"])
        db.execute(
            """UPDATE education_classes
                  SET student_experience = ?, weekly_xp_goal = ?
                WHERE id = ?""",
            (experience, weekly_goal, class_id),
        )
        education_game.record_game_mode_change(
            db,
            class_id=class_id,
            previous_mode=previous["studentExperience"],
            next_mode=experience,
            timezone_name=previous["timezone"],
        )
        if previous["studentExperience"] != "map" and experience == "map":
            backfill = education_game.backfill_class_game_events(db, class_id)
            affected_users = backfill["userIds"]
        else:
            affected_users = []
        for user_id in affected_users:
            education_game.reconcile_student_growth(
                db,
                class_id=class_id,
                user_id=user_id,
            )
            education_game.reconcile_student_achievements(
                db,
                class_id=class_id,
                user_id=user_id,
                weekly_goal=weekly_goal,
            )
        db.commit()
    except Exception:
        db.rollback()
        raise

    updated = db.execute(
        "SELECT * FROM education_classes WHERE id = ?",
        (class_id,),
    ).fetchone()
    return jsonify({"settings": education_game.settings_payload(updated)})


def _education_game_student(class_id: str):
    user, error = _education_require_user(required_role="student")
    if error:
        return None, None, error
    membership, error = _education_require_membership(class_id, user, {"student"})
    if error:
        return None, None, error
    return user, membership, None


def _education_game_value_error(db, cause: ValueError):
    db.rollback()
    code = str(cause) or "game_operation_invalid"
    return jsonify({"error": code, "code": code}), 409


@app.route("/api/v2/edu/classes/<class_id>/checkins", methods=["POST"])
def education_game_checkin(class_id: str):
    user, membership, error = _education_game_student(class_id)
    if error:
        return error
    db = _get_db()
    try:
        payload = education_game.check_in_student(db, class_row=membership, user_id=int(user["id"]))
        db.commit()
        return jsonify(payload)
    except ValueError as cause:
        return _education_game_value_error(db, cause)


@app.route("/api/v2/edu/classes/<class_id>/checkins/revive", methods=["POST"])
def education_game_checkin_revive(class_id: str):
    user, membership, error = _education_game_student(class_id)
    if error:
        return error
    db = _get_db()
    try:
        checkin = education_game.revive_yesterday(db, class_row=membership, user_id=int(user["id"]))
        db.commit()
        return jsonify({"checkin": checkin})
    except ValueError as cause:
        return _education_game_value_error(db, cause)


@app.route("/api/v2/edu/classes/<class_id>/inventory/xp-card/activate", methods=["POST"])
def education_game_activate_xp_card(class_id: str):
    user, _membership, error = _education_game_student(class_id)
    if error:
        return error
    db = _get_db()
    try:
        inventory = education_game.activate_xp_card(db, class_id=class_id, user_id=int(user["id"]))
        db.commit()
        return jsonify({"inventory": inventory})
    except ValueError as cause:
        return _education_game_value_error(db, cause)


@app.route("/api/v2/edu/classes/<class_id>/growth-rewards/<reward_id>/claim", methods=["POST"])
def education_game_claim_growth_choice(class_id: str, reward_id: str):
    user, _membership, error = _education_game_student(class_id)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    option_key = body.get("optionKey")
    if not isinstance(option_key, str) or not option_key:
        return jsonify({"error": "optionKey is required"}), 400
    db = _get_db()
    try:
        reward = education_game.claim_five_level_choice(db, class_id=class_id, user_id=int(user["id"]), reward_id=reward_id, option_key=option_key)
        db.commit()
        return jsonify({"reward": reward})
    except ValueError as cause:
        return _education_game_value_error(db, cause)


@app.route("/api/v2/edu/classes/<class_id>/collectibles/equip", methods=["POST"])
def education_game_equip_collectible(class_id: str):
    user, _membership, error = _education_game_student(class_id)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    collectible_key = body.get("collectibleKey")
    if not isinstance(collectible_key, str) or not collectible_key:
        return jsonify({"error": "collectibleKey is required"}), 400
    db = _get_db()
    try:
        collectible = education_game.equip_collectible(db, class_id=class_id, user_id=int(user["id"]), collectible_key=collectible_key)
        db.commit()
        return jsonify({"collectible": collectible})
    except ValueError as cause:
        return _education_game_value_error(db, cause)


@app.route("/api/v2/edu/classes/<class_id>/game-notifications/seen", methods=["POST"])
def education_game_mark_seen(class_id: str):
    user, _membership, error = _education_game_student(class_id)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    growth_ids = body.get("growthIds", [])
    chest_ids = body.get("chestIds", [])
    if (
        not isinstance(growth_ids, list)
        or not isinstance(chest_ids, list)
        or not all(isinstance(value, str) and value for value in growth_ids)
        or not all(isinstance(value, str) and value for value in chest_ids)
    ):
        return jsonify({"error": "growthIds and chestIds must be string arrays"}), 400
    db = _get_db()
    education_game.mark_game_rewards_seen(db, class_id=class_id, user_id=int(user["id"]), growth_ids=growth_ids[:100], chest_ids=chest_ids[:100])
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/v2/edu/classes/<class_id>/leaderboards", methods=["GET"])
def education_game_leaderboard(class_id: str):
    user, error = _education_require_user()
    if error:
        return error
    _membership, error = _education_require_membership(class_id, user)
    if error:
        return error
    kind = request.args.get("kind", "xp")
    try:
        return jsonify(education_game.build_leaderboard(_get_db(), class_id=class_id, viewer_user_id=int(user["id"]), kind=kind))
    except ValueError:
        return jsonify({"error": "kind must be xp or gems"}), 400


@app.route("/api/v2/edu/classes/<class_id>/shop", methods=["GET"])
def education_game_shop(class_id: str):
    user, error = _education_require_user()
    if error:
        return error
    membership, error = _education_require_membership(class_id, user)
    if error:
        return error
    include_inactive = membership["role"] == "teacher" and request.args.get("includeInactive") == "1"
    db = _get_db()
    items = education_game.list_shop_items(db, class_id=class_id, include_inactive=include_inactive)
    db.commit()
    return jsonify({"items": items})


@app.route("/api/v2/edu/classes/<class_id>/shop/items/<item_id>/redeem", methods=["POST"])
def education_game_redeem_shop_item(class_id: str, item_id: str):
    user, _membership, error = _education_game_student(class_id)
    if error:
        return error
    db = _get_db()
    try:
        redemption = education_game.redeem_shop_item(db, class_id=class_id, user_id=int(user["id"]), item_id=item_id)
        db.commit()
        return jsonify({"redemption": redemption}), 201
    except ValueError as cause:
        return _education_game_value_error(db, cause)


@app.route("/api/v2/edu/classes/<class_id>/shop/redemptions", methods=["GET"])
def education_game_student_redemptions(class_id: str):
    user, _membership, error = _education_game_student(class_id)
    if error:
        return error
    return jsonify({"redemptions": education_game.list_student_redemptions(_get_db(), class_id=class_id, user_id=int(user["id"]))})


@app.route("/api/v2/edu/classes/<class_id>/shop/items", methods=["POST"])
def education_game_create_shop_item(class_id: str):
    user, error = _education_require_user(required_role="teacher")
    if error:
        return error
    _membership, error = _education_require_class_teacher(class_id, user)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    try:
        item = education_game.create_or_update_custom_shop_item(
            _get_db(), class_id=class_id, teacher_user_id=int(user["id"]), item_id=None,
            title=body.get("title", ""), description=body.get("description", ""), gem_price=body.get("gemPrice"),
            stock=body.get("stock"), active=body.get("active", True),
        )
        _get_db().commit()
        return jsonify({"item": item}), 201
    except ValueError as cause:
        return _education_game_value_error(_get_db(), cause)


@app.route("/api/v2/edu/classes/<class_id>/shop/items/<item_id>", methods=["PATCH"])
def education_game_update_shop_item(class_id: str, item_id: str):
    user, error = _education_require_user(required_role="teacher")
    if error:
        return error
    _membership, error = _education_require_class_teacher(class_id, user)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    db = _get_db()
    current = db.execute("SELECT * FROM education_shop_items WHERE id = ? AND class_id = ?", (item_id, class_id)).fetchone()
    if not current:
        return jsonify({"error": "shop item not found"}), 404
    try:
        item = education_game.create_or_update_custom_shop_item(
            db, class_id=class_id, teacher_user_id=int(user["id"]), item_id=item_id,
            title=body.get("title", current["title"]), description=body.get("description", current["description"]),
            gem_price=body.get("gemPrice", int(current["gem_price"])), stock=body.get("stock", current["stock_quantity"]),
            active=body.get("active", bool(current["is_active"])),
        )
        db.commit()
        return jsonify({"item": item})
    except ValueError as cause:
        return _education_game_value_error(db, cause)


@app.route("/api/v2/edu/classes/<class_id>/shop/redemptions/manage", methods=["GET"])
def education_game_manage_redemptions(class_id: str):
    user, error = _education_require_user(required_role="teacher")
    if error:
        return error
    _membership, error = _education_require_class_teacher(class_id, user)
    if error:
        return error
    return jsonify({"redemptions": education_game.list_teacher_redemptions(_get_db(), class_id=class_id)})


@app.route("/api/v2/edu/classes/<class_id>/shop/redemptions/<redemption_id>/resolve", methods=["POST"])
def education_game_resolve_redemption(class_id: str, redemption_id: str):
    user, error = _education_require_user(required_role="teacher")
    if error:
        return error
    _membership, error = _education_require_class_teacher(class_id, user)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    if not isinstance(action, str):
        return jsonify({"error": "action is required"}), 400
    db = _get_db()
    try:
        redemption = education_game.resolve_redemption(db, class_id=class_id, redemption_id=redemption_id, teacher_user_id=int(user["id"]), action=action)
        db.commit()
        return jsonify({"redemption": redemption})
    except ValueError as cause:
        return _education_game_value_error(db, cause)


@app.route("/api/v2/edu/classes/<class_id>/gems/grants", methods=["GET", "POST"])
def education_game_teacher_gem_grant(class_id: str):
    user, error = _education_require_user(required_role="teacher")
    if error:
        return error
    _membership, error = _education_require_class_teacher(class_id, user)
    if error:
        return error
    if request.method == "GET":
        return jsonify({"awards": education_game.list_teacher_gem_awards(_get_db(), class_id=class_id)})
    body = request.get_json(silent=True) or {}
    target_user_id = body.get("userId")
    amount = body.get("amount")
    reason = body.get("reason")
    if isinstance(target_user_id, bool) or not isinstance(target_user_id, int) or not isinstance(reason, str):
        return jsonify({"error": "userId, amount, and reason are required"}), 400
    db = _get_db()
    try:
        award = education_game.grant_teacher_gems(db, class_id=class_id, user_id=target_user_id, amount=amount, reason=reason, teacher_user_id=int(user["id"]))
        db.commit()
        return jsonify({"award": award})
    except ValueError as cause:
        return _education_game_value_error(db, cause)


@app.route("/api/v2/edu/assignments/<assignment_id>/growth-challenge", methods=["GET", "PUT", "DELETE"])
def education_game_challenge_rule(assignment_id: str):
    user, error = _education_require_user(required_role="teacher")
    if error:
        return error
    assignment, _snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return error
    if membership["role"] != "teacher":
        return jsonify({"error": "forbidden"}), 403
    db = _get_db()
    if request.method == "GET":
        return jsonify({"challenge": education_game.challenge_access_payload(db, class_id=assignment["class_id"], assignment_id=assignment_id, user_id=None)})
    if request.method == "DELETE":
        education_game.clear_challenge_unlock_rule(db, class_id=assignment["class_id"], assignment_id=assignment_id)
        db.commit()
        return jsonify({"ok": True})
    body = request.get_json(silent=True) or {}
    allowed = {"requiredLevel", "requiredStageKey", "requiredStageMilestone"}
    if set(body) - allowed:
        return jsonify({"error": "unsupported growth challenge field"}), 400
    try:
        challenge = education_game.set_challenge_unlock_rule(
            db, class_id=assignment["class_id"], assignment_id=assignment_id,
            required_level=body.get("requiredLevel"), required_stage_key=body.get("requiredStageKey"),
            required_stage_milestone=body.get("requiredStageMilestone"),
        )
        db.commit()
        return jsonify({"challenge": challenge})
    except ValueError as cause:
        return _education_game_value_error(db, cause)


@app.route("/api/v2/edu/classes/<class_id>/members", methods=["GET"])
def education_class_members(class_id: str):
    user, error = _education_require_user("teacher")
    if error:
        return error
    _membership, error = _education_require_class_teacher(class_id, user)
    if error:
        return error
    rows = _get_db().execute(
        """SELECT m.user_id, m.student_name, m.student_number, m.joined_at, m.removed_at
           FROM education_memberships m
           WHERE m.class_id = ? AND m.role = 'student'
           ORDER BY CASE WHEN m.removed_at IS NULL THEN 0 ELSE 1 END,
                    m.student_number, m.user_id""",
        (class_id,),
    ).fetchall()
    return jsonify({
        "members": [
            {
                "userId": row["user_id"],
                "studentName": row["student_name"],
                "studentNumber": row["student_number"],
                "profileComplete": bool(row["student_name"] and row["student_number"]),
                "joinedAt": row["joined_at"],
                "status": "removed" if row["removed_at"] else "active",
                "removedAt": row["removed_at"],
            }
            for row in rows
        ],
    })


@app.route("/api/v2/edu/classes/<class_id>/members/<int:user_id>", methods=["DELETE"])
def education_class_remove_member(class_id: str, user_id: int):
    user, error = _education_require_user("teacher")
    if error:
        return error
    _membership, error = _education_require_class_teacher(class_id, user)
    if error:
        return error
    db = _get_db()
    target = db.execute(
        "SELECT role, removed_at FROM education_memberships WHERE class_id = ? AND user_id = ?",
        (class_id, user_id),
    ).fetchone()
    if not target or target["role"] != "student":
        return jsonify({"error": "student not found", "code": "student_not_found"}), 404
    if target["removed_at"]:
        return jsonify({"ok": True, "status": "removed"})
    removed_at = datetime.utcnow().isoformat()
    db.execute(
        "UPDATE education_memberships SET removed_at = ? WHERE class_id = ? AND user_id = ?",
        (removed_at, class_id, user_id),
    )
    db.commit()
    return jsonify({"ok": True, "status": "removed", "removedAt": removed_at})


@app.route("/api/v2/edu/classes/<class_id>/members/<int:user_id>/restore", methods=["POST"])
def education_class_restore_member(class_id: str, user_id: int):
    user, error = _education_require_user("teacher")
    if error:
        return error
    _membership, error = _education_require_class_teacher(class_id, user)
    if error:
        return error
    db = _get_db()
    target = db.execute(
        "SELECT role, removed_at FROM education_memberships WHERE class_id = ? AND user_id = ?",
        (class_id, user_id),
    ).fetchone()
    if not target or target["role"] != "student":
        return jsonify({"error": "student not found", "code": "student_not_found"}), 404
    db.execute(
        "UPDATE education_memberships SET removed_at = NULL WHERE class_id = ? AND user_id = ?",
        (class_id, user_id),
    )
    db.commit()
    return jsonify({"ok": True, "status": "active"})


@app.route("/api/v2/edu/classes/<class_id>/snapshots", methods=["GET", "POST"])
def education_class_snapshots(class_id: str):
    user, error = _education_require_user()
    if error:
        return error
    membership, error = _education_require_membership(class_id, user)
    if error:
        return error
    db = _get_db()
    if request.method == "GET":
        rows = _education_course_graph_snapshot_rows(db, class_id)
        return jsonify({"snapshots": [_education_public_snapshot(row) for row in rows]})
    if membership["role"] != "teacher":
        return jsonify({"error": "forbidden"}), 403

    body = request.get_json(silent=True) or {}
    nodes = body.get("nodes")
    edges = body.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list) or not nodes:
        return jsonify({"error": "nodes and edges are required"}), 400
    node_ids = [node.get("id") for node in nodes if isinstance(node, dict)]
    if len(node_ids) != len(nodes) or any(not isinstance(node_id, int) for node_id in node_ids):
        return jsonify({"error": "snapshot nodes require integer ids"}), 400
    if len(set(node_ids)) != len(node_ids):
        return jsonify({"error": "snapshot node ids must be unique"}), 400

    snapshot_id = uuid.uuid4().hex
    source_graph_id = str(body.get("sourceGraphId") or "").strip() or None
    if source_graph_id:
        existing = db.execute(
            "SELECT * FROM education_snapshots WHERE class_id = ? AND source_graph_id = ? ORDER BY created_at ASC LIMIT 1",
            (class_id, source_graph_id),
        ).fetchone()
        if existing:
            return jsonify({
                "snapshot": _education_public_snapshot(existing, include_graph=True),
                "created": False,
            })
    source_pdf = None
    source_job_id = str(body.get("sourceJobId") or "").strip() or None
    source_history_id = source_job_id or source_graph_id
    if source_history_id:
        history_row = db.execute(
            "SELECT id, source_pdf_json FROM history WHERE id = ? AND user_id = ?",
            (source_history_id, user["id"]),
        ).fetchone()
        source_meta = _read_source_pdf_meta(history_row) if history_row else None
        if source_meta:
            target_dir = _EDUCATION_SNAPSHOT_ROOT / snapshot_id
            target_dir.mkdir(parents=True, exist_ok=True)
            source_pdf = _stored_source_pdf_meta(source_meta) or {}
            for path_key, name_key in (
                ("pdf_path", "pdf_name"),
                ("source_path", "source_name"),
                ("log_path", "log_name"),
            ):
                source_path = Path(str(source_meta.get(path_key) or ""))
                if source_path.is_file():
                    safe_name = Path(str(source_pdf.get(name_key) or source_path.name)).name
                    shutil.copy2(source_path, target_dir / safe_name)
                    source_pdf[name_key] = safe_name
            source_pdf["pdf_url"] = f"/api/v2/edu/snapshots/{snapshot_id}/source-pdf"
            source_pdf["compile_log_url"] = f"/api/v2/edu/snapshots/{snapshot_id}/compile-log"

    now = datetime.utcnow().isoformat()
    try:
        db.execute(
            """INSERT INTO education_snapshots
                 (id, class_id, source_graph_id, filename, node_count, edge_count,
                  source_markdown, latex_macros_json, source_pdf_json, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                class_id,
                source_graph_id,
                str(body.get("filename") or "教学图谱")[:255],
                len(nodes),
                len(edges),
                str(body.get("sourceMarkdown") or ""),
                json.dumps(body.get("latexMacros") if isinstance(body.get("latexMacros"), dict) else {}, ensure_ascii=False),
                json.dumps(source_pdf, ensure_ascii=False) if source_pdf else None,
                user["id"],
                now,
            ),
        )
        graph_key = f"source:{source_graph_id}" if source_graph_id else f"snapshot:{snapshot_id}"
        next_order = db.execute(
            """SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order
                 FROM education_course_graph_order
                WHERE class_id = ?""",
            (class_id,),
        ).fetchone()["next_order"]
        db.execute(
            """INSERT INTO education_course_graph_order
                 (class_id, graph_key, sort_order, updated_by, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (class_id, graph_key, int(next_order), user["id"], now),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    persist_graph(snapshot_id, "education_snapshot", nodes, edges)
    _invalidate_api_graph(snapshot_id)
    row = db.execute("SELECT * FROM education_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    return jsonify({
        "snapshot": _education_public_snapshot(row, include_graph=True),
        "created": True,
    }), 201


@app.route("/api/v2/edu/classes/<class_id>/course-graph-order", methods=["PATCH"])
def education_class_course_graph_order(class_id: str):
    user, error = _education_require_user()
    if error:
        return error
    membership, error = _education_require_membership(class_id, user)
    if error:
        return error
    if membership["role"] != "teacher":
        return jsonify({"error": "forbidden"}), 403

    body = request.get_json(silent=True) or {}
    graph_ids = body.get("graphIds")
    known_graph_ids = body.get("knownGraphIds")
    if not isinstance(graph_ids, list) or any(not isinstance(item, str) or not item.strip() for item in graph_ids):
        return jsonify({"error": "graphIds must be an array of snapshot ids", "code": "invalid_course_graph_order"}), 400
    if known_graph_ids is not None and (
        not isinstance(known_graph_ids, list)
        or any(not isinstance(item, str) or not item.strip() for item in known_graph_ids)
    ):
        return jsonify({"error": "knownGraphIds must be an array of snapshot ids", "code": "invalid_course_graph_order"}), 400
    graph_ids = [item.strip() for item in graph_ids]
    known_graph_ids = [item.strip() for item in known_graph_ids] if isinstance(known_graph_ids, list) else None
    if len(set(graph_ids)) != len(graph_ids) or (known_graph_ids is not None and len(set(known_graph_ids)) != len(known_graph_ids)):
        return jsonify({"error": "graph ids must not contain duplicates", "code": "invalid_course_graph_order"}), 400

    db = _get_db()
    rows = db.execute(
        """SELECT * FROM education_snapshots
            WHERE class_id = ? AND COALESCE(snapshot_type, 'graph') = 'graph'
            ORDER BY created_at ASC, id ASC FOR UPDATE""",
        (class_id,),
    ).fetchall()
    groups = _education_course_graph_groups(rows)
    canonical_by_id = {str(group["canonical"]["id"]): key for key, group in groups.items()}
    expected_ids = set(canonical_by_id)
    provided_ids = set(graph_ids)
    invalid_ids = provided_ids - expected_ids
    if invalid_ids:
        return jsonify({"error": "graphIds contains a graph that is not a current class course graph", "code": "invalid_course_graph_order"}), 400
    if known_graph_ids is not None and set(known_graph_ids) != expected_ids:
        return jsonify({
            "error": "course graph collection changed; reload and try again",
            "code": "course_graph_order_conflict",
        }), 409
    if provided_ids != expected_ids or len(graph_ids) != len(expected_ids):
        return jsonify({"error": "graphIds must contain every current class course graph exactly once", "code": "invalid_course_graph_order"}), 400

    now = datetime.utcnow().isoformat()
    try:
        db.execute("DELETE FROM education_course_graph_order WHERE class_id = ?", (class_id,))
        db.executemany(
            """INSERT INTO education_course_graph_order
                 (class_id, graph_key, sort_order, updated_by, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            [(class_id, canonical_by_id[snapshot_id], index, user["id"], now) for index, snapshot_id in enumerate(graph_ids)],
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return jsonify({"snapshots": [_education_public_snapshot(row) for row in _education_course_graph_snapshot_rows(db, class_id)]})


def _education_assignment_rows(class_id: str, role: str):
    where = "class_id = ? AND status != 'archived'" if role == "teacher" else "class_id = ? AND status = 'published'"
    return _get_db().execute(
        f"SELECT * FROM education_assignments WHERE {where} ORDER BY updated_at DESC",
        (class_id,),
    ).fetchall()


def _direct_question_payload(raw, index: int, score: float, *, node_id: int | None = None, allow_empty_question: bool = False) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("each direct question must be an object")
    question = str(raw.get("question") or "").strip()
    if not question and not allow_empty_question:
        raise ValueError("direct questions cannot be empty")
    raw_node_id = raw.get("nodeId", node_id)
    normalized_node_id = None
    if raw_node_id not in (None, ""):
        try:
            normalized_node_id = int(raw_node_id)
        except (TypeError, ValueError):
            raise ValueError("direct question nodeId must be a positive integer")
        if normalized_node_id <= 0:
            normalized_node_id = None
    try:
        max_score = round(float(score), 1)
    except (TypeError, ValueError):
        max_score = 0
    if not max_score or not (max_score > 0) or max_score > 100:
        raise ValueError("maxScore must be greater than 0 and at most 100")
    expected_points = raw.get("expectedPoints") or []
    if not isinstance(expected_points, list):
        expected_points = []
    return {
        "nodeId": normalized_node_id,
        "question": question,
        "focus": str(raw.get("focus") or "").strip(),
        "kind": str(raw.get("kind") or "direct").strip()[:64] or "direct",
        "expectedPoints": [str(point).strip() for point in expected_points if str(point).strip()],
        "referenceAnswer": str(raw.get("referenceAnswer") or "").strip(),
        "maxScore": max_score,
        "order": index + 1,
    }


def _direct_questions_in_order(raw_questions) -> list[dict]:
    """Normalize list order while rejecting duplicate or non-contiguous values."""
    if not isinstance(raw_questions, list):
        raise ValueError("at least one direct question is required")
    has_explicit_order = any(isinstance(item, dict) and "order" in item for item in raw_questions)
    if not has_explicit_order:
        return list(raw_questions)
    orders = []
    for item in raw_questions:
        if not isinstance(item, dict):
            raise ValueError("each direct question must be an object")
        try:
            order = int(item.get("order"))
        except (TypeError, ValueError):
            raise ValueError("direct question order must be a positive integer")
        if order < 1 or order in orders:
            raise ValueError("direct question order must be unique")
        orders.append(order)
    if sorted(orders) != list(range(1, len(raw_questions) + 1)):
        raise ValueError("direct question order must start at 1")
    return [item for _, item in sorted(zip(orders, raw_questions), key=lambda pair: pair[0])]


def _direct_question_scores(raw_questions, *, equal_if_missing=False) -> list[float]:
    supplied = []
    for item in raw_questions:
        if not isinstance(item, dict) or item.get("maxScore") in (None, ""):
            supplied.append(None)
            continue
        try:
            score = round(float(item.get("maxScore")), 1)
        except (TypeError, ValueError):
            raise ValueError("maxScore must be a number")
        if not score or not (score > 0) or score > 100:
            raise ValueError("maxScore must be greater than 0 and at most 100")
        supplied.append(score)
    if equal_if_missing and any(score is None for score in supplied):
        return _education_equal_question_scores(len(raw_questions))
    if not supplied or all(score is None for score in supplied):
        return _education_equal_question_scores(len(raw_questions))
    total = sum(score or 0 for score in supplied)
    if total <= 0:
        return _education_equal_question_scores(len(raw_questions))
    normalized = [round((score or 0) / total * 100, 1) for score in supplied]
    normalized[-1] = round(100.0 - sum(normalized[:-1]), 1)
    if normalized[-1] <= 0:
        raise ValueError("maxScore must be greater than 0 and at most 100")
    return normalized


def _direct_assignment_path(title: str, node_ids: list[int] | None = None) -> dict:
    node_ids = [_DIRECT_ASSIGNMENT_NODE_ID] if node_ids is None else list(node_ids)
    steps = [{
        "nodeId": node_id,
        "order": index + 1,
        "role": "target" if index == len(node_ids) - 1 else "prerequisite",
        "required": True,
        "rationale": "按题目顺序完成。" if len(node_ids) > 1 else "教师直接导入的作业题目。",
        "state": "not_started",
    } for index, node_id in enumerate(node_ids)]
    edges = [{
        "from": node_ids[index],
        "to": node_ids[index + 1],
        "label": "题目顺序",
        "description": "按题目顺序完成。",
    } for index in range(len(node_ids) - 1)]
    return {
        "targetNodeId": node_ids[-1] if node_ids else 0,
        "summary": title or "直接题目作业",
        "steps": steps,
        "edges": edges,
        "candidateNodeIds": node_ids,
        "hasCycles": False,
        "aiEnhanced": False,
    }


def _direct_assignment_node(title: str, question: str | None = None, order: int = 1, node_id: int = _DIRECT_ASSIGNMENT_NODE_ID) -> dict:
    is_sequence = question is not None
    node_title = f"第 {order} 题" if is_sequence else (title or "直接作业")
    content = question if question is not None else (title or "直接作业")
    return {
        "id": node_id,
        "node_type": "exercise",
        "title_zh": node_title,
        "title_en": f"Question {order}" if is_sequence else "Direct assignment",
        "label": node_title,
        "content": content,
        "statement_form": "",
        "subject": [],
        "conditions": [],
        "conclusions": [],
        "proof": None,
        "source_text": content,
    }


def _direct_assignment_edges(node_ids: list[int]) -> list[dict]:
    return [{
        "from": node_ids[index],
        "to": node_ids[index + 1],
        "label": "题目顺序",
        "description": "按题目顺序完成。",
        "strength": "ordered",
    } for index in range(len(node_ids) - 1)]


def _direct_source_filename(filename: str) -> str:
    safe = Path(filename or "题目来源").name.strip()
    return (safe or "题目来源")[:180]


def _direct_source_allowed(filename: str) -> bool:
    return Path(filename).suffix.lower() in {".md", ".txt", ".tex", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}


def _direct_source_path(assignment_id: str, storage_name: str) -> Path:
    return _EDUCATION_ASSIGNMENT_SOURCE_ROOT / assignment_id / Path(storage_name).name


@app.route("/api/v2/edu/classes/<class_id>/assignments/direct", methods=["POST"])
def education_direct_assignment_create(class_id: str):
    user, error = _education_require_user("teacher")
    if error:
        return error
    membership, error = _education_require_membership(class_id, user, {"teacher"})
    if error:
        return error
    title = str(request.form.get("title") or "").strip()[:160]
    if not title:
        return jsonify({"error": "title is required"}), 400
    raw_questions = request.form.get("questions") or "[]"
    try:
        questions = _direct_questions_in_order(json.loads(raw_questions))
    except (TypeError, json.JSONDecodeError):
        return jsonify({"error": "questions must be valid JSON"}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    scores = _education_equal_question_scores(len(questions))
    try:
        normalized_questions = [_direct_question_payload(item, index, scores[index], node_id=index + 1) for index, item in enumerate(questions)]
    except ValueError as exc:
        return jsonify({"error": str(exc), "code": "assessment_score_invalid" if "maxScore" in str(exc) else None}), 400
    source_file = request.files.get("source_file")
    source_filename = _direct_source_filename(source_file.filename if source_file and source_file.filename else request.form.get("sourceFilename") or "题目来源")
    source_origin = str(request.form.get("sourceOrigin") or "paste").strip()[:32]
    source_text = str(request.form.get("sourceText") or "")
    due_at = request.form.get("dueAt") or None
    if due_at:
        try:
            datetime.fromisoformat(due_at.replace("Z", "+00:00"))
        except ValueError:
            return jsonify({"error": "dueAt must be an ISO date string or null"}), 400
    if source_file and not _direct_source_allowed(source_filename):
        return jsonify({"error": "unsupported source file"}), 400

    assignment_id = uuid.uuid4().hex
    snapshot_id = uuid.uuid4().hex
    node_ids = [int(question["nodeId"]) for question in normalized_questions]
    nodes = [_direct_assignment_node(title, question["question"], index + 1, node_id) for index, (question, node_id) in enumerate(zip(normalized_questions, node_ids))]
    edges = _direct_assignment_edges(node_ids)
    path = _direct_assignment_path(title, node_ids)
    now = datetime.utcnow().isoformat()
    storage_name = None
    source_dir = _EDUCATION_ASSIGNMENT_SOURCE_ROOT / assignment_id
    db = _get_db()
    try:
        db.execute(
            """INSERT INTO education_snapshots
                 (id, class_id, snapshot_type, source_graph_id, filename, node_count, edge_count,
                  source_markdown, latex_macros_json, source_pdf_json, created_by, created_at)
               VALUES (?, ?, 'direct', NULL, ?, ?, ?, ?, '{}', NULL, ?, ?)""",
            (snapshot_id, class_id, f"{title}.direct", len(nodes), len(edges), source_text, user["id"], now),
        )
        db.execute(
            """INSERT INTO education_assignments
                 (id, class_id, assignment_type, direct_structure_version, snapshot_id, title, target_node_id, due_at, status,
                  base_path_json, summary, created_by, created_at, updated_at)
               VALUES (?, ?, 'direct', 1, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)""",
            (assignment_id, class_id, snapshot_id, title, node_ids[-1] if node_ids else 0, due_at, json.dumps(path, ensure_ascii=False), title, user["id"], now, now),
        )
        db.executemany("INSERT INTO education_assessment_nodes (assignment_id, node_id, status, updated_at) VALUES (?, ?, 'ready', ?)", [(assignment_id, node_id, now) for node_id in node_ids])
        db.executemany(
            """INSERT INTO education_assessment_questions
                 (id, assignment_id, node_id, kind, question, focus, expected_points_json,
                  reference_answer, max_score, sort_order, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (uuid.uuid4().hex, assignment_id, question["nodeId"], question["kind"], question["question"], question["focus"], json.dumps(question["expectedPoints"], ensure_ascii=False), question["referenceAnswer"], question["maxScore"], question["order"], now, now)
                for question in normalized_questions
            ],
        )
        if source_file and source_file.filename:
            source_dir.mkdir(parents=True, exist_ok=True)
            storage_name = source_filename
            source_file.save(source_dir / storage_name)
        if source_file or source_text:
            db.execute(
                """INSERT INTO education_assignment_sources
                     (assignment_id, filename, mime_type, source_origin, storage_name, source_text, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (assignment_id, source_filename, source_file.mimetype if source_file else (mimetypes.guess_type(source_filename)[0] or "text/plain"), source_origin, storage_name, source_text, now),
            )
        db.commit()
    except Exception:
        db.rollback()
        if source_dir.exists():
            shutil.rmtree(source_dir, ignore_errors=True)
        raise
    persist_graph(snapshot_id, "education_snapshot", nodes, edges)
    _invalidate_api_graph(snapshot_id)
    row = db.execute("SELECT * FROM education_assignments WHERE id = ?", (assignment_id,)).fetchone()
    snapshot = db.execute("SELECT * FROM education_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    return jsonify({"assignment": _education_public_assignment(row, snapshot=snapshot, role=membership["role"])}), 201


@app.route("/api/v2/edu/classes/<class_id>/assignments", methods=["GET", "POST"])
def education_class_assignments(class_id: str):
    user, error = _education_require_user()
    if error:
        return error
    membership, error = _education_require_membership(class_id, user)
    if error:
        return error
    if request.method == "GET":
        return jsonify({
            "assignments": [
                _education_public_assignment(
                    row, role=membership["role"], user_id=int(user["id"]) if membership["role"] == "student" else None
                )
                for row in _education_assignment_rows(class_id, membership["role"])
            ],
        })
    if membership["role"] != "teacher":
        return jsonify({"error": "forbidden"}), 403

    body = request.get_json(silent=True) or {}
    snapshot_id = str(body.get("snapshotId") or "").strip()
    try:
        target_node_id = int(body.get("targetNodeId"))
    except (TypeError, ValueError):
        return jsonify({"error": "targetNodeId must be an integer"}), 400
    db = _get_db()
    snapshot = db.execute(
        "SELECT * FROM education_snapshots WHERE id = ? AND class_id = ?",
        (snapshot_id, class_id),
    ).fetchone()
    if not snapshot:
        return jsonify({"error": "snapshot not found"}), 404
    try:
        deterministic = build_learning_path(
            _education_snapshot_graph(snapshot)["nodes"],
            _education_snapshot_graph(snapshot)["edges"],
            target_node_id,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    assignment_id = uuid.uuid4().hex
    path = deterministic
    try:
        ai_result = _education_ai_task(
            user_id=int(user["id"]),
            task_id=assignment_id,
            task_kind="path",
            payload=_education_path_payload(snapshot, deterministic),
            scope=f"assignments/{assignment_id}",
        )
        path = merge_ai_path(deterministic, ai_result)
    except Exception:
        path = {**deterministic, "aiEnhanced": False, "aiFallbackReason": "deterministic_fallback"}
    title = str(body.get("title") or f"学习：{target_node_id}").strip()[:160]
    now = datetime.utcnow().isoformat()
    db.execute(
        """INSERT INTO education_assignments
             (id, class_id, snapshot_id, title, target_node_id, due_at, status,
              base_path_json, summary, created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)""",
        (
            assignment_id, class_id, snapshot_id, title, target_node_id,
            body.get("dueAt") or None,
            json.dumps(path, ensure_ascii=False),
            path.get("summary") or "",
            user["id"], now, now,
        ),
    )
    _education_reconcile_assessment_nodes(db, assignment_id, path, initial=True)
    db.commit()
    _education_generate_initial_assessments(
        assignment_id=assignment_id,
        user_id=int(user["id"]),
        snapshot=snapshot,
        path=path,
    )
    row = db.execute("SELECT * FROM education_assignments WHERE id = ?", (assignment_id,)).fetchone()
    return jsonify({
        "assignment": _education_public_assignment(row, snapshot=snapshot, role="teacher"),
        "warnings": _education_order_warnings(path),
    }), 201


def _education_assignment_context(assignment_id: str, user):
    db = _get_db()
    assignment = db.execute(
        "SELECT * FROM education_assignments WHERE id = ?", (assignment_id,)
    ).fetchone()
    if not assignment:
        return None, None, None, (jsonify({"error": "assignment not found"}), 404)
    membership, error = _education_require_membership(assignment["class_id"], user)
    if error:
        return None, None, None, error
    if assignment["status"] == "archived":
        return None, None, None, (jsonify({"error": "assignment not found"}), 404)
    if membership["role"] == "student" and assignment["status"] != "published":
        return None, None, None, (jsonify({"error": "assignment not found"}), 404)
    if membership["role"] == "student" and assignment["assignment_type"] == "direct":
        challenge = education_game.challenge_access_payload(
            db, class_id=assignment["class_id"], assignment_id=assignment_id, user_id=int(user["id"])
        )
        if challenge["locked"]:
            return None, None, None, (jsonify({"error": "growth challenge is locked", "code": "growth_challenge_locked", "challenge": challenge}), 403)
    snapshot = db.execute(
        "SELECT * FROM education_snapshots WHERE id = ?", (assignment["snapshot_id"],)
    ).fetchone()
    return assignment, snapshot, membership, None


def _education_student_path(assignment, user_id: int) -> dict:
    """Return the immutable teacher path with this student's progress overlaid."""
    base_path = _education_json(assignment["base_path_json"], {})
    return apply_progress_to_path(base_path, _education_progress_map(assignment["id"], user_id))


@app.route("/api/v2/edu/assignments/<assignment_id>", methods=["GET", "PUT", "PATCH", "DELETE"])
def education_assignment(assignment_id: str):
    user, error = _education_require_user()
    if error:
        return error
    assignment, snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return error
    if request.method == "GET":
        path = _education_json(assignment["base_path_json"], {})
        if membership["role"] == "student":
            path = _education_student_path(assignment, int(user["id"]))
        return jsonify({
            "assignment": _education_public_assignment(
                assignment,
                snapshot=snapshot,
                path=path,
                role=membership["role"],
                user_id=user["id"],
            ),
        })

    if request.method == "PATCH":
        if membership["role"] != "teacher" or assignment["status"] != "published":
            return jsonify({"error": "only a teacher can edit a published assignment"}), 403
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "a JSON object is required"}), 400
        unsupported = sorted(set(body) - {"title", "dueAt"})
        if unsupported:
            return jsonify({"error": "published assignments only allow title and dueAt"}), 400
        if not body:
            return jsonify({"error": "title or dueAt is required"}), 400

        title = assignment["title"]
        if "title" in body:
            if not isinstance(body["title"], str):
                return jsonify({"error": "title must be a string"}), 400
            title = body["title"].strip()
            if not title:
                return jsonify({"error": "title is required"}), 400
            if len(title) > 160:
                return jsonify({"error": "title must be at most 160 characters"}), 400

        due_at = assignment["due_at"]
        if "dueAt" in body:
            raw_due_at = body["dueAt"]
            if raw_due_at is None or (isinstance(raw_due_at, str) and not raw_due_at.strip()):
                due_at = None
            elif not isinstance(raw_due_at, str):
                return jsonify({"error": "dueAt must be an ISO date string or null"}), 400
            else:
                due_at = raw_due_at.strip()
                try:
                    datetime.fromisoformat(due_at.replace("Z", "+00:00"))
                except ValueError:
                    return jsonify({"error": "dueAt must be an ISO date string or null"}), 400

        now = datetime.utcnow().isoformat()
        db = _get_db()
        db.execute(
            """UPDATE education_assignments
               SET title = ?, due_at = ?, updated_at = ?
               WHERE id = ? AND status = 'published'""",
            (title, due_at, now, assignment_id),
        )
        db.commit()
        updated = db.execute("SELECT * FROM education_assignments WHERE id = ?", (assignment_id,)).fetchone()
        return jsonify({
            "assignment": _education_public_assignment(updated, snapshot=snapshot, role="teacher"),
        })

    if request.method == "DELETE":
        if membership["role"] != "teacher":
            return jsonify({"error": "only a teacher can remove an assignment"}), 403
        db = _get_db()
        if assignment["status"] == "published":
            now = datetime.utcnow().isoformat()
            db.execute(
                """UPDATE education_assignments
                   SET status = 'archived', updated_at = ?
                   WHERE id = ? AND status = 'published'""",
                (now, assignment_id),
            )
            db.commit()
            return jsonify({"ok": True})
        if assignment["status"] == "draft" and assignment["assignment_type"] == "direct":
            snapshot_id = str(assignment["snapshot_id"])
            try:
                prepare_graph_delete(snapshot_id)
                _invalidate_api_graph(snapshot_id)
                db.execute(
                    "DELETE FROM education_assignments WHERE id = ? AND status = 'draft' AND assignment_type = 'direct'",
                    (assignment_id,),
                )
                db.execute(
                    "DELETE FROM education_snapshots WHERE id = ? AND snapshot_type = 'direct'",
                    (snapshot_id,),
                )
                db.commit()
                complete_graph_delete(snapshot_id)
            except Exception:
                db.rollback()
                raise

            for root, target in (
                (_EDUCATION_ASSIGNMENT_SOURCE_ROOT, _EDUCATION_ASSIGNMENT_SOURCE_ROOT / assignment_id),
                (_EDUCATION_ROOT / "assignments", _EDUCATION_ROOT / "assignments" / assignment_id),
                (_EDUCATION_SNAPSHOT_ROOT, _EDUCATION_SNAPSHOT_ROOT / snapshot_id),
            ):
                try:
                    resolved_root = root.resolve()
                    resolved_target = target.resolve()
                    if resolved_target.parent != resolved_root:
                        raise ValueError("unsafe education resource path")
                    if resolved_target.is_dir():
                        shutil.rmtree(resolved_target)
                except (OSError, ValueError) as exc:
                    app.logger.warning("Failed to remove deleted assignment resource %s: %s", target, exc)
            return jsonify({"ok": True})
        return jsonify({"error": "only direct assignment drafts can be permanently deleted"}), 403

    if membership["role"] != "teacher" or assignment["status"] != "draft":
        return jsonify({"error": "only a teacher can edit a draft"}), 403
    body = request.get_json(silent=True) or {}
    if assignment["assignment_type"] == "direct":
        unsupported = sorted(set(body) - {"title", "dueAt"})
        if unsupported:
            return jsonify({"error": "direct assignments edit questions through direct-questions"}), 400
        title = str(body.get("title") or assignment["title"]).strip()[:160]
        if not title:
            return jsonify({"error": "title is required"}), 400
        due_at = body.get("dueAt") if "dueAt" in body else assignment["due_at"]
        if due_at:
            if not isinstance(due_at, str):
                return jsonify({"error": "dueAt must be an ISO date string or null"}), 400
            try:
                datetime.fromisoformat(due_at.replace("Z", "+00:00"))
            except ValueError:
                return jsonify({"error": "dueAt must be an ISO date string or null"}), 400
        now = datetime.utcnow().isoformat()
        db = _get_db()
        db.execute(
            "UPDATE education_assignments SET title = ?, due_at = ?, summary = ?, updated_at = ? WHERE id = ?",
            (title, due_at, title, now, assignment_id),
        )
        db.execute(
            "UPDATE education_snapshots SET filename = ? WHERE id = ? AND snapshot_type = 'direct'",
            (f"{title}.direct", assignment["snapshot_id"]),
        )
        db.commit()
        updated = db.execute("SELECT * FROM education_assignments WHERE id = ?", (assignment_id,)).fetchone()
        return jsonify({"assignment": _education_public_assignment(updated, snapshot=db.execute("SELECT * FROM education_snapshots WHERE id = ?", (assignment["snapshot_id"],)).fetchone(), role="teacher")})
    current_path = _education_json(assignment["base_path_json"], {})
    raw_steps = body.get("steps") if isinstance(body.get("steps"), list) else current_path.get("steps") or []
    candidate_ids = set(current_path.get("candidateNodeIds") or [])
    normalized_steps = []
    seen = set()
    base_by_id = {step["nodeId"]: step for step in current_path.get("steps") or []}
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict) or not isinstance(raw_step.get("nodeId"), int):
            return jsonify({"error": "each step requires an integer nodeId"}), 400
        node_id = raw_step["nodeId"]
        if node_id not in candidate_ids or node_id in seen:
            return jsonify({"error": "steps contain an unknown or duplicate node"}), 400
        seen.add(node_id)
        base = base_by_id[node_id]
        normalized_steps.append({
            **base,
            "required": bool(base.get("role") == "target" or raw_step.get("required")),
            "rationale": str(raw_step.get("rationale") or base.get("rationale") or "").strip(),
        })
    target_node_id = int(assignment["target_node_id"])
    if target_node_id not in seen:
        return jsonify({"error": "the target node cannot be removed"}), 400
    missing_required = [
        step["nodeId"]
        for step in current_path.get("steps") or []
        if step.get("required") and step["nodeId"] not in seen
    ]
    if missing_required:
        return jsonify({"error": "required nodes cannot be removed"}), 400
    normalized_steps = [step for step in normalized_steps if step["nodeId"] != target_node_id] + [
        next(step for step in normalized_steps if step["nodeId"] == target_node_id)
    ]
    for index, step in enumerate(normalized_steps, start=1):
        step["order"] = index
    path = {**current_path, "steps": normalized_steps}
    if isinstance(body.get("summary"), str):
        path["summary"] = body["summary"].strip()
    now = datetime.utcnow().isoformat()
    db = _get_db()
    db.execute(
        """UPDATE education_assignments
           SET title = ?, due_at = ?, base_path_json = ?, summary = ?, updated_at = ?
           WHERE id = ?""",
        (
            str(body.get("title") or assignment["title"]).strip()[:160],
            body.get("dueAt") if "dueAt" in body else assignment["due_at"],
            json.dumps(path, ensure_ascii=False),
            path.get("summary") or "",
            now,
            assignment_id,
        ),
    )
    _education_reconcile_assessment_nodes(db, assignment_id, path)
    db.commit()
    updated = db.execute("SELECT * FROM education_assignments WHERE id = ?", (assignment_id,)).fetchone()
    return jsonify({
        "assignment": _education_public_assignment(updated, snapshot=snapshot, role="teacher"),
        "warnings": _education_order_warnings(path),
    })


@app.route("/api/v2/edu/assignments/<assignment_id>/direct-questions", methods=["PATCH"])
def education_direct_questions_update(assignment_id: str):
    user, error = _education_require_user("teacher")
    if error:
        return error
    assignment, snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return error
    if membership["role"] != "teacher" or assignment["status"] not in ("draft", "published") or assignment["assignment_type"] != "direct":
        return jsonify({"error": "only a teacher can edit direct assignment questions"}), 403
    is_multipart = request.content_type.startswith("multipart/form-data") if request.content_type else False
    if is_multipart:
        raw_questions = request.form.get("questions")
        try:
            raw_questions = json.loads(raw_questions or "[]")
        except (TypeError, json.JSONDecodeError):
            return jsonify({"error": "questions must be valid JSON"}), 400
        source_file = request.files.get("source_file")
        source_filename = _direct_source_filename(source_file.filename if source_file and source_file.filename else request.form.get("sourceFilename") or "题目来源")
        source_origin = str(request.form.get("sourceOrigin") or "paste").strip()[:32]
        source_text = str(request.form.get("sourceText") or "")
        if source_file and not _direct_source_allowed(source_filename):
            return jsonify({"error": "unsupported source file"}), 400
    else:
        body = request.get_json(silent=True) or {}
        raw_questions = body.get("questions")
        source_file = None
        source_filename = ""
        source_origin = ""
        source_text = ""
    if not isinstance(raw_questions, list) or not raw_questions:
        return jsonify({"error": "at least one direct question is required"}), 400

    source_file_path: Path | None = None

    def persist_source(db, now: str) -> None:
        nonlocal source_file_path
        if not is_multipart:
            return
        storage_name = None
        if source_file and source_file.filename:
            source_dir = _EDUCATION_ASSIGNMENT_SOURCE_ROOT / assignment_id
            source_dir.mkdir(parents=True, exist_ok=True)
            storage_name = source_filename
            source_file_path = source_dir / storage_name
            source_file.save(source_file_path)
        db.execute(
            """INSERT INTO education_assignment_sources
                 (assignment_id, filename, mime_type, source_origin, storage_name, source_text, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON DUPLICATE KEY UPDATE
                 filename = VALUES(filename),
                 mime_type = VALUES(mime_type),
                 source_origin = VALUES(source_origin),
                 storage_name = VALUES(storage_name),
                 source_text = VALUES(source_text)""",
            (assignment_id, source_filename, source_file.mimetype if source_file else (mimetypes.guess_type(source_filename)[0] or "text/plain"), source_origin, storage_name, source_text, now),
        )
        db.execute("UPDATE education_snapshots SET source_markdown = ? WHERE id = ?", (source_text, snapshot["id"]))

    if int(assignment["direct_structure_version"] or 0) != 1:
        try:
            ordered = _direct_questions_in_order(raw_questions)
            scores = _direct_question_scores(ordered)
            normalized = [_direct_question_payload(item, index, scores[index], node_id=_DIRECT_ASSIGNMENT_NODE_ID, allow_empty_question=True) for index, item in enumerate(ordered)]
        except ValueError as exc:
            return jsonify({"error": str(exc), "code": "assessment_score_invalid" if "maxScore" in str(exc) or "order" in str(exc) else None}), 400
        db = _get_db()
        now = datetime.utcnow().isoformat()
        try:
            db.execute("DELETE FROM education_assessment_questions WHERE assignment_id = ? AND node_id = ?", (assignment_id, _DIRECT_ASSIGNMENT_NODE_ID))
            db.executemany(
                """INSERT INTO education_assessment_questions
                     (id, assignment_id, node_id, kind, question, focus, expected_points_json,
                      reference_answer, max_score, sort_order, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (uuid.uuid4().hex, assignment_id, _DIRECT_ASSIGNMENT_NODE_ID, question["kind"], question["question"], question["focus"], json.dumps(question["expectedPoints"], ensure_ascii=False), question["referenceAnswer"], question["maxScore"], question["order"], now, now)
                    for question in normalized
                ],
            )
            db.execute("UPDATE education_assessment_nodes SET status = 'ready', last_error = NULL, updated_at = ? WHERE assignment_id = ? AND node_id = ?", (now, assignment_id, _DIRECT_ASSIGNMENT_NODE_ID))
            persist_source(db, now)
            db.execute("UPDATE education_assignments SET updated_at = ?, version = version + 1 WHERE id = ?", (now, assignment_id))
            db.commit()
        except Exception:
            db.rollback()
            if source_file_path and source_file_path.exists():
                source_file_path.unlink()
            raise
        updated = db.execute("SELECT * FROM education_assignments WHERE id = ?", (assignment_id,)).fetchone()
        return jsonify({"assignment": _education_public_assignment(updated, snapshot=snapshot, role="teacher")})

    try:
        ordered = _direct_questions_in_order(raw_questions)
        scores = _direct_question_scores(ordered)
    except ValueError as exc:
        return jsonify({"error": str(exc), "code": "assessment_score_invalid" if "maxScore" in str(exc) or "order" in str(exc) else None}), 400

    existing_nodes = _education_snapshot_graph(snapshot)["nodes"]
    existing_ids = []
    for node in existing_nodes:
        try:
            node_id = int(node.get("id"))
        except (TypeError, ValueError):
            continue
        if node_id > 0:
            existing_ids.append(node_id)
    next_node_id = max(existing_ids or [0]) + 1
    seen_node_ids: set[int] = set()
    normalized = []
    for index, item in enumerate(ordered):
        raw_node_id = item.get("nodeId") if isinstance(item, dict) else None
        if raw_node_id in (None, ""):
            node_id = next_node_id
            next_node_id += 1
        else:
            try:
                node_id = int(raw_node_id)
            except (TypeError, ValueError):
                return jsonify({"error": "direct question nodeId must be a positive integer"}), 400
            if node_id <= 0:
                node_id = next_node_id
                next_node_id += 1
        if node_id in seen_node_ids:
            return jsonify({"error": "direct question nodeId must be unique"}), 400
        seen_node_ids.add(node_id)
        try:
            normalized.append(_direct_question_payload(item, index, scores[index], node_id=node_id, allow_empty_question=True))
        except ValueError as exc:
            return jsonify({"error": str(exc), "code": "assessment_score_invalid" if "maxScore" in str(exc) else None}), 400

    node_ids = [int(item["nodeId"]) for item in normalized]
    nodes = [_direct_assignment_node(assignment["title"], item["question"], index + 1, item["nodeId"]) for index, item in enumerate(normalized)]
    edges = _direct_assignment_edges(node_ids)
    path = _direct_assignment_path(assignment["title"], node_ids)
    now = datetime.utcnow().isoformat()
    db = _get_db()
    try:
        db.execute(
            "UPDATE education_snapshots SET node_count = ?, edge_count = ? WHERE id = ?",
            (len(nodes), len(edges), snapshot["id"]),
        )
        db.execute("DELETE FROM education_assessment_questions WHERE assignment_id = ?", (assignment_id,))
        db.execute("DELETE FROM education_assessment_nodes WHERE assignment_id = ?", (assignment_id,))
        db.executemany("INSERT INTO education_assessment_nodes (assignment_id, node_id, status, updated_at) VALUES (?, ?, 'ready', ?)", [(assignment_id, node_id, now) for node_id in node_ids])
        db.executemany(
            """INSERT INTO education_assessment_questions
                 (id, assignment_id, node_id, kind, question, focus, expected_points_json,
                  reference_answer, max_score, sort_order, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (uuid.uuid4().hex, assignment_id, item["nodeId"], item["kind"], item["question"], item["focus"], json.dumps(item["expectedPoints"], ensure_ascii=False), item["referenceAnswer"], item["maxScore"], item["order"], now, now)
                for item in normalized
            ],
        )
        persist_source(db, now)
        db.execute("UPDATE education_assignments SET target_node_id = ?, base_path_json = ?, summary = ?, updated_at = ?, version = version + 1 WHERE id = ?", (node_ids[-1], json.dumps(path, ensure_ascii=False), assignment["title"], now, assignment_id))
        db.commit()
    except Exception:
        db.rollback()
        if source_file_path and source_file_path.exists():
            source_file_path.unlink()
        raise
    persist_graph(str(snapshot["id"]), "education_snapshot", nodes, edges)
    _invalidate_api_graph(str(snapshot["id"]))
    updated = db.execute("SELECT * FROM education_assignments WHERE id = ?", (assignment_id,)).fetchone()
    updated_snapshot = db.execute("SELECT * FROM education_snapshots WHERE id = ?", (snapshot["id"],)).fetchone()
    return jsonify({
        "assignment": _education_public_assignment(updated, snapshot=updated_snapshot, role="teacher"),
        "snapshot": _education_public_snapshot(updated_snapshot, include_graph=True),
        "path": path,
        "assessments": _education_public_assessments(assignment_id, role="teacher"),
    })


@app.route("/api/v2/edu/direct-questions/generate-standard", methods=["POST"])
def education_direct_question_generate_standard():
    user, error = _education_require_user(required_role="teacher")
    if error:
        return error
    body = request.get_json(silent=True) or {}
    question = str(body.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required", "code": "direct_scoring_generation_invalid"}), 400

    task_id = uuid.uuid4().hex
    try:
        result = _education_ai_task(
            user_id=int(user["id"]),
            task_id=task_id,
            task_kind="direct_scoring_standard",
            payload={"question": question},
            scope=f"direct-question-generations/{task_id}",
        )
    except Exception as exc:
        return _education_ai_error_response(exc, "direct_scoring_generation_failed")
    if not validate_direct_scoring_standard_result(result):
        return jsonify({"error": "direct scoring generation returned an invalid result", "code": "direct_scoring_generation_invalid"}), 502
    return jsonify({
        "referenceAnswer": str(result["referenceAnswer"]).strip(),
        "focus": str(result["focus"]).strip(),
        "expectedPoints": [str(point).strip() for point in result["expectedPoints"] if str(point).strip()],
    })


@app.route("/api/v2/edu/assignments/<assignment_id>/source", methods=["GET"])
def education_direct_source(assignment_id: str):
    user, error = _education_require_user("teacher")
    if error:
        return error
    assignment, _snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return error
    if membership["role"] != "teacher" or assignment["assignment_type"] != "direct":
        return jsonify({"error": "direct assignment source is teacher-only"}), 403
    source = _get_db().execute(
        "SELECT filename, storage_name FROM education_assignment_sources WHERE assignment_id = ?",
        (assignment_id,),
    ).fetchone()
    if not source or not source["storage_name"]:
        return jsonify({"error": "original source file not found"}), 404
    path = _direct_source_path(assignment_id, source["storage_name"]).resolve()
    root = _EDUCATION_ASSIGNMENT_SOURCE_ROOT.resolve()
    if path.parent.parent != root or not path.is_file():
        return jsonify({"error": "original source file not found"}), 404
    return send_file(path, mimetype=mimetypes.guess_type(source["filename"])[0] or "application/octet-stream", as_attachment=False, download_name=source["filename"])


def _education_teacher_draft_assessment_context(assignment_id: str, node_id: int):
    user, error = _education_require_user()
    if error:
        return None, None, None, error
    assignment, snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return None, None, None, error
    if membership["role"] != "teacher" or assignment["status"] != "draft":
        return None, None, None, (jsonify({"error": "only a teacher can edit draft assessments"}), 403)
    if node_id not in set(_education_path_node_ids(_education_json(assignment["base_path_json"], {}))):
        return None, None, None, (jsonify({"error": "node is outside the draft learning path"}), 400)
    return user, assignment, snapshot, None


def _education_assessment_payload_for_node(assignment_id: str, node_id: int) -> dict:
    return next(
        assessment
        for assessment in _education_public_assessments(assignment_id, role="teacher")
        if assessment["nodeId"] == node_id
    )


def _education_unresolved_assessment_node_ids(assignment_id: str, path: dict) -> list[int]:
    path_node_ids = _education_path_node_ids(path)
    assessment_rows = _get_db().execute(
        """SELECT n.node_id, n.status, COUNT(q.id) AS question_count
             FROM education_assessment_nodes n
             LEFT JOIN education_assessment_questions q
               ON q.assignment_id = n.assignment_id AND q.node_id = n.node_id
            WHERE n.assignment_id = ?
            GROUP BY n.node_id, n.status""",
        (assignment_id,),
    ).fetchall()
    assessment_by_node = {
        int(row["node_id"]): (row["status"], int(row["question_count"] or 0))
        for row in assessment_rows
    }
    return [
        node_id
        for node_id in path_node_ids
        if assessment_by_node.get(node_id, ("pending", 0))[0] not in {"ready", "exempt"}
        or (
            assessment_by_node.get(node_id, ("pending", 0))[0] == "ready"
            and assessment_by_node.get(node_id, ("pending", 0))[1] < 1
        )
    ]


@app.route(
    "/api/v2/edu/assignments/<assignment_id>/assessments/<int:node_id>/questions/<question_id>",
    methods=["PATCH"],
)
def education_assessment_question_update(assignment_id: str, node_id: int, question_id: str):
    user, error = _education_require_user(required_role="teacher")
    if error:
        return error
    assignment, _snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return error
    if membership["role"] != "teacher" or assignment["status"] not in {"draft", "published"} or assignment["grades_published_at"]:
        return jsonify({"error": "assessment scoring standard is frozen", "code": "assessment_scoring_frozen"}), 409
    if node_id not in set(_education_path_node_ids(_education_json(assignment["base_path_json"], {}))):
        return jsonify({"error": "node is outside the frozen learning path"}), 400
    db = _get_db()
    current = db.execute(
        "SELECT * FROM education_assessment_questions WHERE id = ? AND assignment_id = ? AND node_id = ?",
        (question_id, assignment_id, node_id),
    ).fetchone()
    if not current:
        return jsonify({"error": "assessment question not found"}), 404
    body = request.get_json(silent=True) or {}
    reference_answer = str(body.get("referenceAnswer") or "").strip()
    expected_points = body.get("expectedPoints")
    try:
        max_score = round(float(body.get("maxScore")), 1)
    except (TypeError, ValueError):
        return jsonify({"error": "maxScore must be a number", "code": "assessment_score_invalid"}), 400
    if (
        not reference_answer
        or not isinstance(expected_points, list)
        or not expected_points
        or not all(isinstance(point, str) and point.strip() for point in expected_points)
        or max_score <= 0
        or max_score > 100
    ):
        return jsonify({"error": "invalid scoring standard", "code": "assessment_scoring_invalid"}), 400
    if assignment["status"] == "published":
        current_points = _education_json(current["expected_points_json"], [])
        current_reference = str(current["reference_answer"] or "").strip()
        current_score = float(current["max_score"] or 0)
        submissions_finalized = db.execute(
            "SELECT 1 FROM education_assignment_submissions WHERE assignment_id = ? AND status IN ('finalized', 'released') LIMIT 1",
            (assignment_id,),
        ).fetchone()
        changed_existing = (
            (current_reference and reference_answer != current_reference)
            or (current_points and expected_points != current_points)
            or (current_score > 0 and abs(max_score - current_score) > 0.001)
        )
        if submissions_finalized or changed_existing:
            return jsonify({"error": "published scoring standard is frozen", "code": "assessment_scoring_frozen"}), 409
    db = _get_db()
    cursor = db.execute(
        """UPDATE education_assessment_questions
              SET reference_answer = ?, expected_points_json = ?, max_score = ?, updated_at = ?
            WHERE id = ? AND assignment_id = ? AND node_id = ?""",
        (reference_answer, json.dumps(expected_points, ensure_ascii=False), max_score, datetime.utcnow().isoformat(), question_id, assignment_id, node_id),
    )
    if cursor.rowcount == 0:
        return jsonify({"error": "assessment question not found"}), 404
    if assignment["status"] == "published":
        pending_submissions = db.execute(
            "SELECT id, snapshot_json FROM education_assignment_submissions WHERE assignment_id = ? AND status IN ('submitted', 'review_draft')",
            (assignment_id,),
        ).fetchall()
        for submission in pending_submissions:
            snapshot_payload = _education_json(submission["snapshot_json"], {})
            for item in snapshot_payload.get("questions") or []:
                if isinstance(item, dict) and str(item.get("questionId")) == question_id:
                    item["referenceAnswer"] = reference_answer
                    item["expectedPoints"] = expected_points
                    item["maxScore"] = max_score
            db.execute(
                "UPDATE education_assignment_submissions SET snapshot_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(snapshot_payload, ensure_ascii=False), datetime.utcnow().isoformat(), submission["id"]),
            )
            db.execute(
                """UPDATE education_submission_question_grades
                      SET reference_answer = ?, expected_points_json = ?, max_score = ?, updated_at = ?
                    WHERE submission_id = ? AND question_id = ?""",
                (reference_answer, json.dumps(expected_points, ensure_ascii=False), max_score, datetime.utcnow().isoformat(), submission["id"], question_id),
            )
    db.commit()
    return jsonify({"assessment": _education_assessment_payload_for_node(assignment_id, node_id)})


@app.route(
    "/api/v2/edu/assignments/<assignment_id>/assessments/<int:node_id>/regenerate",
    methods=["POST"],
)
def education_assessment_regenerate(assignment_id: str, node_id: int):
    user, assignment, snapshot, error = _education_teacher_draft_assessment_context(assignment_id, node_id)
    if error:
        return error
    path = _education_json(assignment["base_path_json"], {})
    task = build_assessment_tasks(_education_snapshot_graph(snapshot)["nodes"], path).get(str(node_id))
    if not task:
        return jsonify({"error": "node not found"}), 404
    existing = _get_db().execute(
        """SELECT question FROM education_assessment_questions
            WHERE assignment_id = ? AND node_id = ? ORDER BY sort_order""",
        (assignment_id, node_id),
    ).fetchall()
    assessment_node = _get_db().execute(
        """SELECT updated_at FROM education_assessment_nodes
             WHERE assignment_id = ? AND node_id = ?""",
        (assignment_id, node_id),
    ).fetchone()
    if not assessment_node:
        return jsonify({"error": "assessment node not found"}), 404
    expected_node_updated_at = assessment_node["updated_at"]
    task["existingQuestions"] = [row["question"] for row in existing]
    operation_id = uuid.uuid4().hex
    try:
        results = _education_ai_tasks(
            user_id=int(user["id"]),
            task_id=operation_id,
            task_kind="assessment",
            tasks={str(node_id): task},
            scope=f"assignments/{assignment_id}/assessment_regenerations/{operation_id}",
        )
        result = results.get(str(node_id))
        if not isinstance(result, dict):
            raise EducationAIError(
                "assessment_invalid_result",
                "assessment_invalid_result",
                503,
            )
        db = _get_db()
        _education_begin_assessment_write(
            db,
            assignment_id,
            node_id,
            expected_node_updated_at,
        )
        _education_replace_assessment_questions(
            db, assignment_id, node_id, result, task.get("category")
        )
        db.commit()
    except Exception as exc:
        _get_db().rollback()
        return _education_ai_error_response(exc, "assessment_regeneration_failed")
    return jsonify({"assessment": _education_assessment_payload_for_node(assignment_id, node_id)})


@app.route(
    "/api/v2/edu/assignments/<assignment_id>/assessments/<int:node_id>/questions/<question_id>/regenerate",
    methods=["POST"],
)
def education_assessment_question_regenerate(assignment_id: str, node_id: int, question_id: str):
    user, assignment, snapshot, error = _education_teacher_draft_assessment_context(assignment_id, node_id)
    if error:
        return error
    db = _get_db()
    stored = db.execute(
        """SELECT * FROM education_assessment_questions
            WHERE id = ? AND assignment_id = ? AND node_id = ?""",
        (question_id, assignment_id, node_id),
    ).fetchone()
    if not stored:
        return jsonify({"error": "assessment question not found"}), 404
    assessment_node = db.execute(
        """SELECT updated_at FROM education_assessment_nodes
             WHERE assignment_id = ? AND node_id = ?""",
        (assignment_id, node_id),
    ).fetchone()
    if not assessment_node:
        return jsonify({"error": "assessment node not found"}), 404
    expected_node_updated_at = assessment_node["updated_at"]
    expected_question_updated_at = stored["updated_at"]
    path = _education_json(assignment["base_path_json"], {})
    task = build_assessment_tasks(_education_snapshot_graph(snapshot)["nodes"], path).get(str(node_id))
    if not task:
        return jsonify({"error": "node not found"}), 404
    task["requiredKind"] = stored["kind"]
    task["existingQuestions"] = [
        row["question"]
        for row in db.execute(
            """SELECT question FROM education_assessment_questions
                WHERE assignment_id = ? AND node_id = ? AND id != ? ORDER BY sort_order""",
            (assignment_id, node_id, question_id),
        ).fetchall()
    ]
    operation_id = uuid.uuid4().hex
    try:
        results = _education_ai_tasks(
            user_id=int(user["id"]),
            task_id=operation_id,
            task_kind="assessment_question",
            tasks={question_id: task},
            scope=f"assignments/{assignment_id}/assessment_regenerations/{operation_id}",
        )
        result = results.get(question_id)
        question = result.get("question") if isinstance(result, dict) else None
        if (
            not isinstance(question, dict)
            or not isinstance(result, dict)
            or result.get("category") != task.get("category")
            or question.get("kind") != stored["kind"]
            or not str(question.get("question") or "").strip()
            or not str(question.get("focus") or "").strip()
            or not isinstance(question.get("expectedPoints"), list)
            or not question["expectedPoints"]
            or not all(isinstance(point, str) and point.strip() for point in question["expectedPoints"])
            or not str(question.get("referenceAnswer") or "").strip()
        ):
            raise EducationAIError(
                "assessment_invalid_result",
                "assessment_invalid_result",
                503,
            )
        _education_begin_assessment_write(
            db,
            assignment_id,
            node_id,
            expected_node_updated_at,
            question_id=question_id,
            expected_question_updated_at=expected_question_updated_at,
        )
        now = datetime.utcnow().isoformat()
        db.execute(
            """UPDATE education_assessment_questions
                  SET question = ?, focus = ?, expected_points_json = ?, reference_answer = ?, updated_at = ?
                WHERE id = ?""",
            (
                str(question.get("question") or "").strip(),
                str(question.get("focus") or "").strip(),
                json.dumps(question.get("expectedPoints") or [], ensure_ascii=False),
                str(question.get("referenceAnswer") or "").strip(),
                now,
                question_id,
            ),
        )
        db.execute(
            """UPDATE education_assessment_nodes
                  SET status = 'ready', last_error = NULL, updated_at = ?
                WHERE assignment_id = ? AND node_id = ?""",
            (now, assignment_id, node_id),
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        return _education_ai_error_response(exc, "assessment_regeneration_failed")
    return jsonify({"assessment": _education_assessment_payload_for_node(assignment_id, node_id)})


@app.route(
    "/api/v2/edu/assignments/<assignment_id>/assessments/<int:node_id>/questions/<question_id>",
    methods=["DELETE"],
)
def education_assessment_question_delete(assignment_id: str, node_id: int, question_id: str):
    _user, _assignment, _snapshot, error = _education_teacher_draft_assessment_context(assignment_id, node_id)
    if error:
        return error
    db = _get_db()
    assessment_node = db.execute(
        """SELECT updated_at FROM education_assessment_nodes
             WHERE assignment_id = ? AND node_id = ?""",
        (assignment_id, node_id),
    ).fetchone()
    if not assessment_node:
        return jsonify({"error": "assessment node not found"}), 404
    expected_node_updated_at = assessment_node["updated_at"]
    stored_question = db.execute(
        """SELECT updated_at FROM education_assessment_questions
             WHERE id = ? AND assignment_id = ? AND node_id = ?""",
        (question_id, assignment_id, node_id),
    ).fetchone()
    if not stored_question:
        return jsonify({"error": "assessment question not found"}), 404
    expected_question_updated_at = stored_question["updated_at"]
    try:
        _education_begin_assessment_write(
            db,
            assignment_id,
            node_id,
            expected_node_updated_at,
            question_id=question_id,
            expected_question_updated_at=expected_question_updated_at,
        )
        cursor = db.execute(
            """DELETE FROM education_assessment_questions
                WHERE id = ? AND assignment_id = ? AND node_id = ?""",
            (question_id, assignment_id, node_id),
        )
        if cursor.rowcount == 0:
            raise EducationAIError(
                "assessment_draft_changed",
                "assessment draft changed before the assessment question could be deleted",
                409,
            )
        remaining = db.execute(
            """SELECT COUNT(*) FROM education_assessment_questions
                WHERE assignment_id = ? AND node_id = ?""",
            (assignment_id, node_id),
        ).fetchone()[0]
        now = datetime.utcnow().isoformat()
        db.execute(
            """UPDATE education_assessment_nodes
                  SET status = ?, last_error = NULL, updated_at = ?
                WHERE assignment_id = ? AND node_id = ?""",
            ("ready" if remaining else "exempt", now, assignment_id, node_id),
        )
        _education_rebalance_question_scores(db, assignment_id)
        db.commit()
    except Exception as exc:
        db.rollback()
        return _education_ai_error_response(exc, "assessment_mutation_failed", 409)
    return jsonify({"assessment": _education_assessment_payload_for_node(assignment_id, node_id)})


@app.route(
    "/api/v2/edu/assignments/<assignment_id>/assessments/<int:node_id>",
    methods=["DELETE"],
)
def education_assessment_exempt(assignment_id: str, node_id: int):
    _user, _assignment, _snapshot, error = _education_teacher_draft_assessment_context(assignment_id, node_id)
    if error:
        return error
    db = _get_db()
    assessment_node = db.execute(
        """SELECT updated_at FROM education_assessment_nodes
             WHERE assignment_id = ? AND node_id = ?""",
        (assignment_id, node_id),
    ).fetchone()
    if not assessment_node:
        return jsonify({"error": "assessment node not found"}), 404
    try:
        _education_begin_assessment_write(
            db,
            assignment_id,
            node_id,
            assessment_node["updated_at"],
        )
        db.execute(
            "DELETE FROM education_assessment_questions WHERE assignment_id = ? AND node_id = ?",
            (assignment_id, node_id),
        )
        db.execute(
            """UPDATE education_assessment_nodes
                  SET status = 'exempt', last_error = NULL, updated_at = ?
                WHERE assignment_id = ? AND node_id = ?""",
            (datetime.utcnow().isoformat(), assignment_id, node_id),
        )
        _education_rebalance_question_scores(db, assignment_id)
        db.commit()
    except Exception as exc:
        db.rollback()
        return _education_ai_error_response(exc, "assessment_mutation_failed", 409)
    return jsonify({"assessment": _education_assessment_payload_for_node(assignment_id, node_id)})


@app.route(
    "/api/v2/edu/assignments/<assignment_id>/assessments/regenerate-unresolved",
    methods=["POST"],
)
def education_assessments_regenerate_unresolved(assignment_id: str):
    user, error = _education_require_user()
    if error:
        return error
    assignment, snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return error
    if membership["role"] != "teacher" or assignment["status"] != "draft":
        return jsonify({"error": "only a teacher can edit draft assessments"}), 403

    path = _education_json(assignment["base_path_json"], {})
    unresolved = _education_unresolved_assessment_node_ids(assignment_id, path)
    if not unresolved:
        return jsonify({
            "assessments": _education_public_assessments(assignment_id, role="teacher"),
            "retriedNodeIds": [],
            "readyNodeIds": [],
            "failedNodeIds": [],
        })

    nodes = _education_snapshot_graph(snapshot)["nodes"]
    all_tasks = build_assessment_tasks(nodes, path)
    tasks = {str(node_id): all_tasks[str(node_id)] for node_id in unresolved if str(node_id) in all_tasks}
    operation_id = uuid.uuid4().hex
    results = {}
    if tasks:
        try:
            results = _education_ai_tasks(
                user_id=int(user["id"]),
                task_id=operation_id,
                task_kind="assessment",
                tasks=tasks,
                scope=f"assignments/{assignment_id}/assessment_regenerations/{operation_id}",
            )
        except Exception as exc:
            return _education_ai_error_response(exc, "assessment_regeneration_failed")

    db = _get_db()
    ready_ids = []
    for node_id in unresolved:
        result = results.get(str(node_id))
        if not isinstance(result, dict):
            _education_mark_assessment_failed(db, assignment_id, node_id, "assessment_invalid_result")
            continue
        try:
            _education_replace_assessment_questions(
                db,
                assignment_id,
                node_id,
                result,
                (all_tasks.get(str(node_id)) or {}).get("category"),
            )
            ready_ids.append(node_id)
        except (TypeError, ValueError):
            _education_mark_assessment_failed(db, assignment_id, node_id, "assessment_invalid_result")
    if ready_ids:
        score_rows = db.execute(
            "SELECT max_score FROM education_assessment_questions WHERE assignment_id = ?",
            (assignment_id,),
        ).fetchall()
        if score_rows and (any(float(row["max_score"] or 0) <= 0 for row in score_rows) or abs(sum(float(row["max_score"] or 0) for row in score_rows) - 100.0) > 0.001):
            _education_rebalance_question_scores(db, assignment_id)
    db.commit()

    failed_ids = _education_unresolved_assessment_node_ids(assignment_id, path)
    return jsonify({
        "assessments": _education_public_assessments(assignment_id, role="teacher"),
        "retriedNodeIds": unresolved,
        "readyNodeIds": ready_ids,
        "failedNodeIds": failed_ids,
    })


@app.route("/api/v2/edu/assignments/<assignment_id>/publish", methods=["POST"])
def education_assignment_publish(assignment_id: str):
    user, error = _education_require_user()
    if error:
        return error
    assignment, snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return error
    if membership["role"] != "teacher":
        return jsonify({"error": "forbidden"}), 403
    is_direct_republish = assignment["status"] == "published" and assignment["assignment_type"] == "direct"
    if assignment["status"] != "draft" and not is_direct_republish:
        return jsonify({"error": "assignment is already published"}), 409
    if assignment["assignment_type"] == "direct":
        question_count = _get_db().execute(
            "SELECT COUNT(*) AS count FROM education_assessment_questions WHERE assignment_id = ?",
            (assignment_id,),
        ).fetchone()["count"]
        if not question_count:
            return jsonify({
                "error": "assessment scoring standards require review before publishing",
                "code": "assessment_scoring_required",
                "totalScore": 0.0,
                "invalidQuestions": [],
            }), 409
    unresolved = _education_unresolved_assessment_node_ids(
        assignment_id,
        _education_json(assignment["base_path_json"], {}),
    )
    if unresolved:
        return jsonify({
            "error": "assessment questions require review before publishing",
            "code": "assessment_review_required",
            "nodeIds": unresolved,
        }), 409
    scoring_ready, scoring = _education_scoring_validation(
        _get_db(),
        assignment_id,
        allow_empty_points=assignment["assignment_type"] == "direct",
    )
    if not scoring_ready:
        return jsonify({
            "error": "assessment scoring standards require review before publishing",
            "code": "assessment_scoring_required",
            **scoring,
        }), 409
    now = datetime.utcnow().isoformat()
    db = _get_db()
    db.execute(
        """UPDATE education_assignments
           SET status = 'published', published_at = ?, updated_at = ? WHERE id = ?""",
        (now, now, assignment_id),
    )
    db.commit()
    updated = db.execute("SELECT * FROM education_assignments WHERE id = ?", (assignment_id,)).fetchone()
    return jsonify({"assignment": _education_public_assignment(updated, snapshot=snapshot, role="teacher")})


@app.route("/api/v2/edu/assignments/<assignment_id>/personalize", methods=["POST"])
def education_assignment_personalize(assignment_id: str):
    """Backward-compatible no-op: published student paths stay teacher-defined."""
    user, error = _education_require_user()
    if error:
        return error
    assignment, _snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return error
    if membership["role"] != "student":
        return jsonify({"error": "only students can request their learning path"}), 403
    path = _education_student_path(assignment, int(user["id"]))
    return jsonify({"path": path})


@app.route(
    "/api/v2/edu/assignments/<assignment_id>/progress/<int:node_id>",
    methods=["PUT"],
)
def education_assignment_progress(assignment_id: str, node_id: int):
    user, error = _education_require_user()
    if error:
        return error
    assignment, snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return error
    if membership["role"] != "student":
        return jsonify({"error": "only students can update progress"}), 403
    body = request.get_json(silent=True) or {}
    state = str(body.get("state") or "").strip()
    if state not in _EDUCATION_PROGRESS_STATES:
        return jsonify({"error": "invalid progress state"}), 400
    base_path = _education_json(assignment["base_path_json"], {})
    candidate_ids = set(_education_path_node_ids(base_path))
    if node_id not in candidate_ids:
        return jsonify({"error": "node is outside the frozen learning path"}), 400
    now = datetime.utcnow().isoformat()
    db = _get_db()
    mastery_source = "self"
    if state == "mastered":
        assessment = db.execute(
            """SELECT status FROM education_assessment_nodes
                WHERE assignment_id = ? AND node_id = ?""",
            (assignment_id, node_id),
        ).fetchone()
        if not assessment or assessment["status"] in {"pending", "failed"}:
            return jsonify({
                "error": "assessment is not available",
                "code": "assessment_unavailable",
            }), 409
        if assessment["status"] == "ready":
            return jsonify({
                "error": "teacher grading must be released before this node can be marked mastered",
                "code": "assignment_review_required",
            }), 409
    db.execute(
        """INSERT INTO education_node_progress
             (assignment_id, user_id, node_id, state, mastery_source, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON DUPLICATE KEY UPDATE
             state = VALUES(state),
             mastery_source = VALUES(mastery_source),
             updated_at = VALUES(updated_at)""",
        (assignment_id, user["id"], node_id, state, mastery_source, now),
    )
    db.commit()
    path = _education_student_path(assignment, int(user["id"]))
    return jsonify({
        "progress": {"nodeId": node_id, "state": state, "updatedAt": now},
        "path": path,
    })


def _education_student_submission(assignment_id: str, user_id: int):
    return _get_db().execute(
        "SELECT * FROM education_assignment_submissions WHERE assignment_id = ? AND user_id = ?",
        (assignment_id, user_id),
    ).fetchone()


def _education_attempt_payload(attempt, questions) -> dict:
    return {
        "id": attempt["id"],
        "assignmentId": attempt["assignment_id"],
        "nodeId": int(attempt["node_id"]),
        "status": attempt["status"],
        "answers": _education_json(attempt["answers_json"], {}),
        "questions": [
            {
                "id": question["id"],
                "nodeId": int(question["node_id"]),
                "kind": question["kind"],
                "order": int(question["sort_order"]),
                "question": question["question"],
                "focus": question["focus"],
            }
            for question in questions
        ],
        "updatedAt": attempt["updated_at"],
        "completedAt": attempt["completed_at"],
    }


@app.route(
    "/api/v2/edu/assignments/<assignment_id>/assessments/<int:node_id>/attempts",
    methods=["POST"],
)
def education_assessment_attempt_start(assignment_id: str, node_id: int):
    user, error = _education_require_user()
    if error:
        return error
    assignment, _snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return error
    if membership["role"] != "student":
        return jsonify({"error": "only students can start assessments"}), 403
    if assignment["grades_published_at"] or _education_student_submission(assignment_id, int(user["id"])):
        return jsonify({"error": "assignment is already submitted", "code": "assignment_already_submitted"}), 409
    if node_id not in set(_education_path_node_ids(_education_json(assignment["base_path_json"], {}))):
        return jsonify({"error": "node is outside the frozen learning path"}), 400
    db = _get_db()
    assessment = db.execute(
        """SELECT status FROM education_assessment_nodes
            WHERE assignment_id = ? AND node_id = ?""",
        (assignment_id, node_id),
    ).fetchone()
    if not assessment or assessment["status"] in {"pending", "failed"}:
        return jsonify({"error": "assessment is not available", "code": "assessment_unavailable"}), 409
    if assessment["status"] == "exempt":
        return jsonify({"error": "this node is exempt from assessment", "code": "assessment_exempt"}), 409
    questions = db.execute(
        """SELECT * FROM education_assessment_questions
            WHERE assignment_id = ? AND node_id = ? ORDER BY sort_order""",
        (assignment_id, node_id),
    ).fetchall()
    if not questions:
        return jsonify({"error": "assessment has no questions", "code": "assessment_unavailable"}), 409
    attempt_id = uuid.uuid4().hex
    now = datetime.utcnow().isoformat()
    db.execute(
        """INSERT INTO education_assessment_attempts
             (id, assignment_id, user_id, node_id, status, answers_json, started_at, updated_at)
           VALUES (?, ?, ?, ?, 'draft', '{}', ?, ?)
           ON DUPLICATE KEY UPDATE id = id""",
        (attempt_id, assignment_id, user["id"], node_id, now, now),
    )
    db.commit()
    attempt = db.execute(
        """SELECT * FROM education_assessment_attempts
            WHERE assignment_id = ? AND user_id = ? AND node_id = ?""",
        (assignment_id, user["id"], node_id),
    ).fetchone()
    return jsonify({"attempt": _education_attempt_payload(attempt, questions)}), 201


def _education_owned_assessment_attempt(attempt_id: str, user):
    attempt = _get_db().execute(
        "SELECT * FROM education_assessment_attempts WHERE id = ? AND user_id = ?",
        (attempt_id, user["id"]),
    ).fetchone()
    if not attempt:
        return None, None, (jsonify({"error": "assessment attempt not found"}), 404)
    assignment, _snapshot, membership, error = _education_assignment_context(attempt["assignment_id"], user)
    if error:
        return None, None, error
    if membership["role"] != "student":
        return None, None, (jsonify({"error": "forbidden"}), 403)
    if assignment["grades_published_at"] or _education_student_submission(attempt["assignment_id"], int(user["id"])):
        return None, None, (jsonify({"error": "assignment is already submitted", "code": "assignment_already_submitted"}), 409)
    return attempt, assignment, None


@app.route("/api/v2/edu/assessment-attempts/<attempt_id>", methods=["PATCH"])
def education_assessment_attempt_save(attempt_id: str):
    user, error = _education_require_user()
    if error:
        return error
    attempt, _assignment, error = _education_owned_assessment_attempt(attempt_id, user)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    raw_answers = body.get("answers")
    if not isinstance(raw_answers, dict):
        return jsonify({"error": "answers must be an object"}), 400
    db = _get_db()
    question_ids = {
        row["id"]
        for row in db.execute(
            """SELECT id FROM education_assessment_questions
                WHERE assignment_id = ? AND node_id = ?""",
            (attempt["assignment_id"], attempt["node_id"]),
        ).fetchall()
    }
    if any(question_id not in question_ids or not isinstance(answer, str) for question_id, answer in raw_answers.items()):
        return jsonify({"error": "answers contain an unknown question or invalid value"}), 400
    answers = _education_json(attempt["answers_json"], {})
    answers.update(raw_answers)
    now = datetime.utcnow().isoformat()
    db.execute(
        "UPDATE education_assessment_attempts SET answers_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(answers, ensure_ascii=False), now, attempt_id),
    )
    db.commit()
    updated = db.execute("SELECT * FROM education_assessment_attempts WHERE id = ?", (attempt_id,)).fetchone()
    questions = db.execute(
        """SELECT * FROM education_assessment_questions
            WHERE assignment_id = ? AND node_id = ? ORDER BY sort_order""",
        (attempt["assignment_id"], attempt["node_id"]),
    ).fetchall()
    return jsonify({"attempt": _education_attempt_payload(updated, questions)})


@app.route("/api/v2/edu/assessment-attempts/<attempt_id>/complete", methods=["POST"])
def education_assessment_attempt_complete(attempt_id: str):
    user, error = _education_require_user()
    if error:
        return error
    attempt, assignment, error = _education_owned_assessment_attempt(attempt_id, user)
    if error:
        return error
    db = _get_db()
    questions = db.execute(
        """SELECT * FROM education_assessment_questions
            WHERE assignment_id = ? AND node_id = ? ORDER BY sort_order""",
        (attempt["assignment_id"], attempt["node_id"]),
    ).fetchall()
    class_row = db.execute(
        "SELECT * FROM education_classes WHERE id = ?",
        (assignment["class_id"],),
    ).fetchone()
    if attempt["status"] == "completed":
        return jsonify({
            "attempt": _education_attempt_payload(attempt, questions),
            "path": _education_student_path(assignment, int(user["id"])),
            "reward": education_game.build_reward_receipt(
                db,
                class_row=class_row,
                user_id=int(user["id"]),
                xp_delta=0,
                unlocked_achievements=[],
            ),
        })

    body = request.get_json(silent=True) or {}
    submitted_answers = body.get("answers", {})
    if not isinstance(submitted_answers, dict):
        return jsonify({"error": "answers must be an object"}), 400
    question_ids = {question["id"] for question in questions}
    if any(question_id not in question_ids or not isinstance(answer, str) for question_id, answer in submitted_answers.items()):
        return jsonify({"error": "answers contain an unknown question or invalid value"}), 400

    try:
        current = db.execute(
            "SELECT * FROM education_assessment_attempts WHERE id = ? AND user_id = ? FOR UPDATE",
            (attempt_id, user["id"]),
        ).fetchone()
        fresh_assignment = db.execute(
            "SELECT * FROM education_assignments WHERE id = ? FOR UPDATE",
            (assignment["id"],),
        ).fetchone()
        class_row = db.execute(
            "SELECT * FROM education_classes WHERE id = ?",
            (assignment["class_id"],),
        ).fetchone()
        if not current or not fresh_assignment:
            db.rollback()
            return jsonify({"error": "assessment attempt not found"}), 404
        assignment = fresh_assignment
        if assignment["grades_published_at"] or _education_student_submission(assignment["id"], int(user["id"])):
            db.rollback()
            return jsonify({"error": "assignment is already submitted", "code": "assignment_already_submitted"}), 409
        if current["status"] == "completed":
            reward = education_game.build_reward_receipt(
                db,
                class_row=class_row,
                user_id=int(user["id"]),
                xp_delta=0,
                unlocked_achievements=[],
            )
            db.commit()
            return jsonify({
                "attempt": _education_attempt_payload(current, questions),
                "path": _education_student_path(assignment, int(user["id"])),
                "reward": reward,
            })

        now = datetime.utcnow().isoformat()
        answers = _education_json(current["answers_json"], {})
        answers.update(submitted_answers)
        missing = [question_id for question_id in question_ids if not str(answers.get(question_id) or "").strip()]
        if missing:
            db.rollback()
            return jsonify({
                "error": "all assessment questions require an answer",
                "code": "assessment_incomplete",
                "questionIds": missing,
            }), 400
        updated_count = db.execute(
            """UPDATE education_assessment_attempts
                  SET answers_json = ?, status = 'completed', completed_at = ?, updated_at = ?
                WHERE id = ? AND status = 'draft'""",
            (json.dumps(answers, ensure_ascii=False), now, now, attempt_id),
        ).rowcount
        if updated_count != 1:
            current = db.execute(
                "SELECT * FROM education_assessment_attempts WHERE id = ? AND user_id = ?",
                (attempt_id, user["id"]),
            ).fetchone()
            reward = education_game.build_reward_receipt(
                db,
                class_row=class_row,
                user_id=int(user["id"]),
                xp_delta=0,
                unlocked_achievements=[],
            )
            db.commit()
            return jsonify({
                "attempt": _education_attempt_payload(current, questions),
                "path": _education_student_path(assignment, int(user["id"])),
                "reward": reward,
            })
        db.execute(
            """INSERT INTO education_node_progress
                 (assignment_id, user_id, node_id, state, mastery_source, updated_at)
               VALUES (?, ?, ?, 'in_progress', 'self', ?)
               ON DUPLICATE KEY UPDATE
                 state = CASE WHEN education_node_progress.state = 'mastered' THEN 'mastered' ELSE 'in_progress' END,
                 mastery_source = CASE WHEN education_node_progress.state = 'mastered' THEN education_node_progress.mastery_source ELSE 'self' END,
                 updated_at = VALUES(updated_at)""",
            (attempt["assignment_id"], user["id"], attempt["node_id"], now),
        )

        xp_delta = 0
        unlocked = []
        growth_events = []
        if education_game.settings_payload(class_row)["studentExperience"] == "map":
            awarded = education_game.award_learning_xp(
                db,
                class_row=class_row,
                class_id=assignment["class_id"],
                user_id=int(user["id"]),
                assignment_id=attempt["assignment_id"],
                event_type="assessment_complete",
                event_key=f"assessment_complete:{attempt_id}",
                occurred_at=now,
                metadata={
                    "attemptId": attempt_id,
                    "nodeId": int(attempt["node_id"]),
                    "assignmentType": assignment["assignment_type"],
                },
            )
            xp_delta = int(awarded["xpDelta"])
            unlocked = awarded["unlockedAchievements"]
            growth_events = awarded["growthRewards"]
        updated = db.execute(
            "SELECT * FROM education_assessment_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()
        reward = education_game.build_reward_receipt(
            db,
            class_row=class_row,
            user_id=int(user["id"]),
            xp_delta=xp_delta,
            unlocked_achievements=unlocked,
            growth_events=growth_events,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return jsonify({
        "attempt": _education_attempt_payload(updated, questions),
        "path": _education_student_path(assignment, int(user["id"])),
        "reward": reward,
    })


def _education_submission_context(submission_id: str, user):
    submission = _get_db().execute(
        "SELECT * FROM education_assignment_submissions WHERE id = ?",
        (submission_id,),
    ).fetchone()
    if not submission:
        return None, None, None, (jsonify({"error": "submission not found"}), 404)
    assignment, snapshot, membership, error = _education_assignment_context(submission["assignment_id"], user)
    if error:
        return None, None, None, error
    if membership["role"] == "student" and int(submission["user_id"]) != int(user["id"]):
        return None, None, None, (jsonify({"error": "submission not found"}), 404)
    return submission, assignment, snapshot, None


def _education_submission_payload(submission, *, role: str) -> dict:
    db = _get_db()
    snapshot = _education_json(submission["snapshot_json"], {})
    question_by_id = {str(item.get("questionId")): item for item in snapshot.get("questions") or [] if isinstance(item, dict)}
    member = db.execute(
        "SELECT student_name, student_number FROM education_memberships WHERE class_id = (SELECT class_id FROM education_assignments WHERE id = ?) AND user_id = ?",
        (submission["assignment_id"], submission["user_id"]),
    ).fetchone()
    payload = {
        "id": submission["id"],
        "assignmentId": submission["assignment_id"],
        "userId": int(submission["user_id"]),
        "studentName": member["student_name"] if member else None,
        "studentNumber": member["student_number"] if member else None,
        "status": submission["status"],
        "aiStatus": submission["ai_status"] if role == "teacher" else None,
        "submittedAt": submission["submitted_at"],
        "updatedAt": submission["updated_at"],
        "finalizedAt": submission["finalized_at"],
        "releasedAt": submission["released_at"],
    }
    if role == "teacher" or submission["status"] == "released":
        payload.update({
            "aiSuggestedTotal": float(submission["ai_suggested_total"]) if submission["ai_suggested_total"] is not None else None,
            "teacherTotal": float(submission["teacher_total"]) if submission["teacher_total"] is not None else None,
            "teacherSummary": submission["teacher_summary"] or "",
            "aiError": submission["ai_error"] if role == "teacher" else None,
        })
        grades = []
        for row in db.execute(
            "SELECT * FROM education_submission_question_grades WHERE submission_id = ? ORDER BY node_id, question_id",
            (submission["id"],),
        ).fetchall():
            question = question_by_id.get(str(row["question_id"]), {})
            grades.append({
                "questionId": row["question_id"],
                "nodeId": int(row["node_id"]),
                "kind": question.get("kind") or "",
                "order": int(question.get("order") or 0),
                "question": question.get("question") or "",
                "focus": question.get("focus") or "",
                "studentAnswer": row["student_answer"],
                "referenceAnswer": row["reference_answer"],
                "expectedPoints": _education_json(row["expected_points_json"], []),
                "maxScore": float(row["max_score"] or 0),
                "matrixReport": _education_json(row["matrix_report_json"], {}),
                "aiResult": _education_json(row["ai_result_json"], {}),
                "aiSuggestedScore": float(row["ai_suggested_score"]) if row["ai_suggested_score"] is not None else None,
                "teacherScore": float(row["teacher_score"]) if row["teacher_score"] is not None else None,
                "teacherFeedback": row["teacher_feedback"] or "",
            })
        payload["grades"] = grades
    return payload


@app.route("/api/v2/edu/assignments/<assignment_id>/submissions", methods=["POST"])
def education_assignment_submit(assignment_id: str):
    user, error = _education_require_user(required_role="student")
    if error:
        return error
    assignment, _snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return error
    if membership["role"] != "student":
        return jsonify({"error": "only students can submit assignments"}), 403
    if assignment["status"] != "published" or assignment["grades_published_at"]:
        return jsonify({"error": "assignment is closed", "code": "assignment_closed"}), 409

    db = _get_db()
    class_row = membership
    existing = _education_student_submission(assignment_id, int(user["id"]))
    if existing:
        return jsonify({
            "submission": _education_submission_payload(existing, role="student"),
            "reward": education_game.build_reward_receipt(
                db,
                class_row=class_row,
                user_id=int(user["id"]),
                xp_delta=0,
                unlocked_achievements=[],
            ),
        })

    submission_id = uuid.uuid4().hex
    try:
        assignment = db.execute(
            "SELECT * FROM education_assignments WHERE id = ? FOR UPDATE",
            (assignment_id,),
        ).fetchone()
        class_row = db.execute(
            "SELECT * FROM education_classes WHERE id = ?",
            (assignment["class_id"],),
        ).fetchone()
        if not assignment or assignment["status"] != "published" or assignment["grades_published_at"]:
            db.rollback()
            return jsonify({"error": "assignment is closed", "code": "assignment_closed"}), 409
        now = datetime.utcnow().isoformat()
        existing = _education_student_submission(assignment_id, int(user["id"]))
        if existing:
            reward = education_game.build_reward_receipt(
                db,
                class_row=class_row,
                user_id=int(user["id"]),
                xp_delta=0,
                unlocked_achievements=[],
            )
            db.commit()
            return jsonify({
                "submission": _education_submission_payload(existing, role="student"),
                "reward": reward,
            })

        questions = db.execute(
            "SELECT * FROM education_assessment_questions WHERE assignment_id = ? ORDER BY node_id, sort_order",
            (assignment_id,),
        ).fetchall()
        attempts = {
            int(row["node_id"]): row
            for row in db.execute(
                "SELECT * FROM education_assessment_attempts WHERE assignment_id = ? AND user_id = ?",
                (assignment_id, user["id"]),
            ).fetchall()
        }
        missing = []
        frozen_questions = []
        for question in questions:
            attempt = attempts.get(int(question["node_id"]))
            answers = _education_json(attempt["answers_json"], {}) if attempt else {}
            answer = str(answers.get(question["id"]) or "").strip()
            if not attempt or attempt["status"] != "completed" or not answer:
                missing.append(question["id"])
                continue
            frozen_questions.append({
                "questionId": question["id"],
                "nodeId": int(question["node_id"]),
                "kind": question["kind"],
                "order": int(question["sort_order"]),
                "question": question["question"],
                "focus": question["focus"],
                "expectedPoints": _education_json(question["expected_points_json"], []),
                "referenceAnswer": question["reference_answer"] or "",
                "maxScore": float(question["max_score"] or 0),
                "studentAnswer": answer,
            })
        if missing:
            db.rollback()
            return jsonify({"error": "all assessment questions must be completed before submission", "code": "assignment_incomplete", "questionIds": missing}), 400

        snapshot = {
            "assignmentVersion": int(assignment["version"] or 1),
            "assignmentType": assignment["assignment_type"],
            "dueAt": _education_utc_timestamp(assignment["due_at"]),
            "questions": frozen_questions,
        }
        db.execute(
            """INSERT INTO education_assignment_submissions
                 (id, assignment_id, user_id, status, ai_status, snapshot_json, submitted_at, updated_at)
               VALUES (?, ?, ?, 'submitted', 'not_started', ?, ?, ?)""",
            (submission_id, assignment_id, user["id"], json.dumps(snapshot, ensure_ascii=False), now, now),
        )
        db.executemany(
            """INSERT INTO education_submission_question_grades
                 (submission_id, question_id, node_id, max_score, student_answer, reference_answer, expected_points_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [(submission_id, item["questionId"], item["nodeId"], item["maxScore"], item["studentAnswer"], item["referenceAnswer"], json.dumps(item["expectedPoints"], ensure_ascii=False), now) for item in frozen_questions],
        )

        xp_delta = 0
        unlocked = []
        growth_events = []
        settings = education_game.settings_payload(class_row)
        if settings["studentExperience"] == "map":
            awarded = education_game.award_learning_xp(
                db,
                class_row=class_row,
                class_id=assignment["class_id"],
                user_id=int(user["id"]),
                assignment_id=assignment_id,
                event_type="assignment_submit",
                event_key=f"assignment_submit:{submission_id}",
                occurred_at=now,
                metadata={
                    "submissionId": submission_id,
                    "assignmentType": assignment["assignment_type"],
                },
            )
            xp_delta += int(awarded["xpDelta"])
            unlocked = awarded["unlockedAchievements"]
            growth_events.extend(awarded["growthRewards"])
            if education_game.is_on_time(now, assignment["due_at"]):
                ontime = education_game.award_learning_xp(
                    db,
                    class_row=class_row,
                    class_id=assignment["class_id"],
                    user_id=int(user["id"]),
                    assignment_id=assignment_id,
                    event_type="assignment_ontime",
                    event_key=f"assignment_ontime:{submission_id}",
                    occurred_at=now,
                    metadata={"submissionId": submission_id, "dueAt": _education_utc_timestamp(assignment["due_at"])},
                )
                xp_delta += int(ontime["xpDelta"])
                unlocked.extend(item for item in ontime["unlockedAchievements"] if item not in unlocked)
                growth_events.extend(ontime["growthRewards"])
        created = db.execute(
            "SELECT * FROM education_assignment_submissions WHERE id = ?",
            (submission_id,),
        ).fetchone()
        reward = education_game.build_reward_receipt(
            db,
            class_row=class_row,
            user_id=int(user["id"]),
            xp_delta=xp_delta,
            unlocked_achievements=unlocked,
            growth_events=growth_events,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = _education_student_submission(assignment_id, int(user["id"]))
        if existing:
            return jsonify({
                "submission": _education_submission_payload(existing, role="student"),
                "reward": education_game.build_reward_receipt(
                    db,
                    class_row=class_row,
                    user_id=int(user["id"]),
                    xp_delta=0,
                    unlocked_achievements=[],
                ),
            })
        raise
    except Exception:
        db.rollback()
        raise
    return jsonify({
        "submission": _education_submission_payload(created, role="student"),
        "reward": reward,
    }), 201


@app.route("/api/v2/edu/assignments/<assignment_id>/grading-overview", methods=["GET"])
def education_grading_overview(assignment_id: str):
    user, error = _education_require_user(required_role="teacher")
    if error:
        return error
    assignment, _snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return error
    if membership["role"] != "teacher":
        return jsonify({"error": "forbidden"}), 403
    rows = _get_db().execute(
        """SELECT m.user_id, m.student_name, m.student_number, s.id AS submission_id, s.status, s.ai_status,
                         s.submitted_at, s.ai_suggested_total, s.teacher_total, s.updated_at
                    FROM education_memberships m
                    LEFT JOIN education_assignment_submissions s
                      ON s.assignment_id = ? AND s.user_id = m.user_id
                   WHERE m.class_id = ? AND m.role = 'student' AND m.removed_at IS NULL
                   ORDER BY m.student_number, m.user_id""",
        (assignment_id, assignment["class_id"]),
    ).fetchall()
    submissions = [row for row in rows if row["submission_id"]]
    pending = [int(row["user_id"]) for row in submissions if row["status"] != "released" and row["teacher_total"] is None]
    return jsonify({
        "assignmentId": assignment_id,
        "gradesPublishedAt": assignment["grades_published_at"],
        "canPublish": bool(submissions) and not pending and not assignment["grades_published_at"],
        "pendingUserIds": pending,
        "students": [{
            "userId": int(row["user_id"]),
            "studentName": row["student_name"],
            "studentNumber": row["student_number"],
            "submissionId": row["submission_id"],
            "submissionStatus": row["status"] if row["submission_id"] else "not_submitted",
            "aiStatus": row["ai_status"] if row["submission_id"] else "not_started",
            "submittedAt": row["submitted_at"],
            "aiSuggestedTotal": float(row["ai_suggested_total"]) if row["ai_suggested_total"] is not None else None,
            "teacherTotal": float(row["teacher_total"]) if row["teacher_total"] is not None else None,
            "updatedAt": row["updated_at"],
        } for row in rows],
    })


def _education_average(values):
    return round(sum(values) / len(values), 2) if values else None


def _education_class_statistics_payload(class_id: str):
    db = _get_db()
    class_row = db.execute(
        "SELECT id, title FROM education_classes WHERE id = ? AND archived_at IS NULL",
        (class_id,),
    ).fetchone()
    if not class_row:
        return None
    students = db.execute(
        """SELECT user_id, student_name, student_number
             FROM education_memberships
            WHERE class_id = ? AND role = 'student' AND removed_at IS NULL
            ORDER BY student_number, user_id""",
        (class_id,),
    ).fetchall()
    assignments = db.execute(
        """SELECT id, title, due_at, published_at
             FROM education_assignments
            WHERE class_id = ? AND status = 'published' AND assignment_type = 'direct'
            ORDER BY COALESCE(published_at, updated_at), id""",
        (class_id,),
    ).fetchall()

    assignment_payload = []
    assignment_ids = [row["id"] for row in assignments]
    question_counts = {}
    max_scores = {}
    for assignment in assignments:
        max_score_row = db.execute(
            "SELECT COUNT(*) AS question_count, COALESCE(SUM(max_score), 0) AS max_score FROM education_assessment_questions WHERE assignment_id = ?",
            (assignment["id"],),
        ).fetchone()
        question_counts[assignment["id"]] = int(max_score_row["question_count"] or 0)
        max_scores[assignment["id"]] = float(max_score_row["max_score"] or 0)

    submissions = {}
    if assignment_ids and students:
        placeholders = ",".join("?" for _ in assignment_ids)
        student_ids = [int(row["user_id"]) for row in students]
        student_placeholders = ",".join("?" for _ in student_ids)
        rows = db.execute(
            f"""SELECT id, assignment_id, user_id, status, teacher_total, submitted_at
                   FROM education_assignment_submissions
                  WHERE assignment_id IN ({placeholders}) AND user_id IN ({student_placeholders})""",
            [*assignment_ids, *student_ids],
        ).fetchall()
        submissions = {(row["user_id"], row["assignment_id"]): row for row in rows}

    student_payload = []
    assignment_final_rates = defaultdict(list)
    for student in students:
        cells = {}
        submitted_count = 0
        graded_count = 0
        student_rates = []
        for assignment in assignments:
            assignment_id = assignment["id"]
            submission = submissions.get((student["user_id"], assignment_id))
            if not submission:
                cells[assignment_id] = {"score": None, "maxScore": max_scores[assignment_id], "status": "not_submitted", "submittedAt": None}
                continue
            submitted_count += 1
            final = submission["status"] in {"finalized", "released"} and submission["teacher_total"] is not None
            cells[assignment_id] = {
                "score": float(submission["teacher_total"]) if final else None,
                "maxScore": max_scores[assignment_id],
                "status": submission["status"],
                "submittedAt": submission["submitted_at"],
            }
            if final:
                graded_count += 1
                rate = (float(submission["teacher_total"]) / max_scores[assignment_id] * 100) if max_scores[assignment_id] else 0
                student_rates.append(rate)
                assignment_final_rates[assignment_id].append(rate)
        student_payload.append({
            "userId": int(student["user_id"]),
            "studentName": student["student_name"],
            "studentNumber": student["student_number"],
            "assignments": cells,
            "submittedCount": submitted_count,
            "gradedCount": graded_count,
            "submissionRate": round(submitted_count / len(assignments), 4) if assignments else 0,
            "averageScore": _education_average(student_rates),
            "rank": None,
        })

    ranked = sorted(
        (student for student in student_payload if student["averageScore"] is not None),
        key=lambda student: (-student["averageScore"], student["studentNumber"] or "", student["userId"]),
    )
    for rank, student in enumerate(ranked, 1):
        student["rank"] = rank

    for assignment in assignments:
        values = assignment_final_rates[assignment["id"]]
        max_score = max_scores[assignment["id"]]
        submitted = sum(1 for student in students if (student["user_id"], assignment["id"]) in submissions)
        assignment_payload.append({
            "id": assignment["id"],
            "title": assignment["title"],
            "questionCount": question_counts[assignment["id"]],
            "maxScore": max_score,
            "publishedAt": assignment["published_at"],
            "dueAt": _education_utc_timestamp(assignment["due_at"]),
            "studentCount": len(students),
            "submittedCount": submitted,
            "finalizedCount": len(values),
            "averageScore": round(sum(rate * max_score / 100 for rate in values) / len(values), 2) if values else None,
            "averageRate": round(sum(values) / len(values) / 100, 4) if values else None,
        })

    submitted_students = sum(1 for student in student_payload if student["submittedCount"] > 0)
    finalized_students = sum(1 for student in student_payload if student["gradedCount"] > 0)
    student_average_scores = [student["averageScore"] for student in student_payload if student["averageScore"] is not None]
    return {
        "classId": class_row["id"],
        "classTitle": class_row["title"],
        "generatedAt": datetime.utcnow().isoformat(),
        "assignments": assignment_payload,
        "overview": {
            "totalStudents": len(students),
            "assignmentCount": len(assignments),
            "submittedStudents": submitted_students,
            "finalizedStudents": finalized_students,
            "averageScore": _education_average(student_average_scores),
            "highestScore": max(student_average_scores) if student_average_scores else None,
            "lowestScore": min(student_average_scores) if student_average_scores else None,
            "averageSubmissionRate": _education_average([student["submissionRate"] for student in student_payload]),
        },
        "students": student_payload,
    }


def _xlsx_escape(value):
    return (
        str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&apos;")
    )


def _xlsx_column_name(index: int):
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _xlsx_cell(reference: str, value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return f'<c r="{reference}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{reference}"><v>{value}</v></c>'
    return f'<c r="{reference}" t="inlineStr"><is><t>{_xlsx_escape(value)}</t></is></c>'


def _build_xlsx_workbook(sheets):
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' + "".join(f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for index, _ in enumerate(sheets, 1)) + "</Types>",
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + "".join(f'<sheet name="{_xlsx_escape(name)}" sheetId="{index}" r:id="rId{index}"/>' for index, (name, _) in enumerate(sheets, 1)) + "</sheets></workbook>",
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>' for index, _ in enumerate(sheets, 1)) + "</Relationships>",
    }
    for index, (_name, rows) in enumerate(sheets, 1):
        xml_rows = []
        for row_index, row in enumerate(rows, 1):
            cells = "".join(_xlsx_cell(f"{_xlsx_column_name(column_index)}{row_index}", value) for column_index, value in enumerate(row, 1))
            xml_rows.append(f'<row r="{row_index}">{cells}</row>')
        files[f"xl/worksheets/sheet{index}.xml"] = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(xml_rows) + "</sheetData></worksheet>"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content.encode("utf-8"))
    output.seek(0)
    return output


def _education_class_statistics_workbook(payload):
    assignments = payload["assignments"]
    header = ["姓名", "学号"] + [assignment["title"] for assignment in assignments] + ["已提交数", "已评分数", "提交率", "平均分", "排名"]
    rows = [header]
    status_labels = {"not_submitted": "未提交", "submitted": "待批改", "review_draft": "待定稿", "finalized": "已定稿", "released": "已发布"}
    for student in payload["students"]:
        cells = [student["studentName"] or "", student["studentNumber"] or ""]
        for assignment in assignments:
            cell = student["assignments"][assignment["id"]]
            cells.append(cell["score"] if cell["score"] is not None else status_labels.get(cell["status"], "—"))
        rows.append(cells + [student["submittedCount"], student["gradedCount"], student["submissionRate"], student["averageScore"], student["rank"]])
    assignment_rows = [["作业名称", "满分", "班级人数", "提交人数", "已定稿人数", "平均分", "最高分", "最低分"]]
    for assignment in assignments:
        scores = [student["assignments"][assignment["id"]]["score"] for student in payload["students"] if student["assignments"][assignment["id"]]["score"] is not None]
        assignment_rows.append([assignment["title"], assignment["maxScore"], assignment["studentCount"], assignment["submittedCount"], assignment["finalizedCount"], assignment["averageScore"], max(scores) if scores else None, min(scores) if scores else None])
    return _build_xlsx_workbook([("班级成绩总表", rows), ("作业汇总", assignment_rows)])


def _education_student_statistics_workbook(payload, student):
    assignments = payload["assignments"]
    status_labels = {"not_submitted": "未提交", "submitted": "待批改", "review_draft": "待定稿", "finalized": "已定稿", "released": "已发布"}
    header = ["姓名", "学号"] + [assignment["title"] for assignment in assignments]
    values = [student["studentName"] or "", student["studentNumber"] or ""]
    for assignment in assignments:
        cell = student["assignments"][assignment["id"]]
        values.append(cell["score"] if cell["score"] is not None else status_labels.get(cell["status"], "—"))
    return _build_xlsx_workbook([("学生成绩", [header, values])])


@app.route("/api/v2/edu/classes/<class_id>/statistics", methods=["GET"])
def education_class_statistics(class_id: str):
    user, error = _education_require_user(required_role="teacher")
    if error:
        return error
    membership, error = _education_require_membership(class_id, user, allowed_roles={"teacher"})
    if error:
        return error
    if membership["role"] != "teacher":
        return jsonify({"error": "forbidden"}), 403
    payload = _education_class_statistics_payload(class_id)
    if payload is None:
        return jsonify({"error": "class not found"}), 404
    return jsonify(payload)


@app.route("/api/v2/edu/classes/<class_id>/statistics/export", methods=["GET"])
def education_class_statistics_export(class_id: str):
    user, error = _education_require_user(required_role="teacher")
    if error:
        return error
    membership, error = _education_require_membership(class_id, user, allowed_roles={"teacher"})
    if error:
        return error
    if membership["role"] != "teacher":
        return jsonify({"error": "forbidden"}), 403
    payload = _education_class_statistics_payload(class_id)
    if payload is None:
        return jsonify({"error": "class not found"}), 404
    filename = re.sub(r"[^\w\-.\u4e00-\u9fff]+", "_", payload["classTitle"] or "班级")
    export_date = datetime.now().strftime("%Y-%m-%d")
    return send_file(
        _education_class_statistics_workbook(payload),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"{filename}_{export_date}_成绩统计.xlsx",
    )


@app.route("/api/v2/edu/classes/<class_id>/students/<int:user_id>/statistics/export", methods=["GET"])
def education_student_statistics_export(class_id: str, user_id: int):
    user, error = _education_require_user(required_role="teacher")
    if error:
        return error
    membership, error = _education_require_membership(class_id, user, allowed_roles={"teacher"})
    if error:
        return error
    if membership["role"] != "teacher":
        return jsonify({"error": "forbidden"}), 403
    payload = _education_class_statistics_payload(class_id)
    if payload is None:
        return jsonify({"error": "class not found"}), 404
    student = next((item for item in payload["students"] if item["userId"] == user_id), None)
    if student is None:
        return jsonify({"error": "student not found"}), 404
    filename = re.sub(
        r"[^\w\-.\u4e00-\u9fff]+",
        "_",
        "_".join(filter(None, [payload["classTitle"], student["studentName"], student["studentNumber"] or str(user_id)])),
    ).strip("_") or "学生成绩统计"
    export_date = datetime.now().strftime("%Y-%m-%d")
    return send_file(
        _education_student_statistics_workbook(payload, student),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"{filename}_{export_date}_成绩统计.xlsx",
    )


@app.route("/api/v2/edu/assignments/<assignment_id>/statistics", methods=["GET"])
def education_assignment_statistics(assignment_id: str):
    user, error = _education_require_user(required_role="teacher")
    if error:
        return error
    assignment, _snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return error
    if membership["role"] != "teacher":
        return jsonify({"error": "forbidden"}), 403
    if assignment["assignment_type"] != "direct":
        return jsonify({"error": "statistics are only available for direct assignments", "code": "direct_assignment_required"}), 400

    db = _get_db()
    student_rows = db.execute(
        """SELECT user_id, student_name, student_number
             FROM education_memberships
            WHERE class_id = ? AND role = 'student' AND removed_at IS NULL
            ORDER BY student_number, user_id""",
        (assignment["class_id"],),
    ).fetchall()
    questions = db.execute(
        """SELECT id, sort_order, question, max_score
             FROM education_assessment_questions
            WHERE assignment_id = ? ORDER BY sort_order, id""",
        (assignment_id,),
    ).fetchall()
    finalized = db.execute(
        """SELECT s.id, s.user_id, s.teacher_total, s.status, m.student_name, m.student_number
             FROM education_assignment_submissions s
             JOIN education_memberships m ON m.class_id = ? AND m.user_id = s.user_id
            WHERE s.assignment_id = ? AND s.status IN ('finalized', 'released')
              AND m.role = 'student' AND m.removed_at IS NULL""",
        (assignment["class_id"], assignment_id),
    ).fetchall()
    submitted_count = db.execute(
        """SELECT COUNT(*) AS count FROM education_assignment_submissions s
             JOIN education_memberships m ON m.class_id = ? AND m.user_id = s.user_id
            WHERE s.assignment_id = ? AND m.role = 'student' AND m.removed_at IS NULL""",
        (assignment["class_id"], assignment_id),
    ).fetchone()["count"]

    max_total = sum(float(row["max_score"] or 0) for row in questions)
    scores = [float(row["teacher_total"]) for row in finalized if row["teacher_total"] is not None]
    scores.sort()
    def average(values):
        return round(sum(values) / len(values), 2) if values else None
    def median(values):
        if not values:
            return None
        middle = len(values) // 2
        value = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
        return round(value, 2)

    distribution = [{"label": label, "count": 0} for label in ("0-59", "60-69", "70-79", "80-89", "90-100")]
    for score in scores:
        rate = (score / max_total * 100) if max_total else 0
        index = 0 if rate < 60 else 1 if rate < 70 else 2 if rate < 80 else 3 if rate < 90 else 4
        distribution[index]["count"] += 1

    grade_rows = {}
    for submission in finalized:
        grade_rows[submission["id"]] = {
            row["question_id"]: float(row["teacher_score"])
            for row in db.execute(
                "SELECT question_id, teacher_score FROM education_submission_question_grades WHERE submission_id = ?",
                (submission["id"],),
            ).fetchall()
            if row["teacher_score"] is not None
        }
    question_statistics = []
    error_distribution = {"correct": 0, "partial": 0, "low": 0}
    for index, question in enumerate(questions):
        max_score = float(question["max_score"] or 0)
        values = [grade_rows[s["id"]].get(question["id"]) for s in finalized if question["id"] in grade_rows[s["id"]]]
        low = sum(1 for value in values if max_score <= 0 or value / max_score < 0.6)
        partial = sum(1 for value in values if max_score > 0 and 0.6 <= value / max_score < 0.8)
        correct = sum(1 for value in values if max_score > 0 and value / max_score >= 0.8)
        error_distribution["low"] += low
        error_distribution["partial"] += partial
        error_distribution["correct"] += correct
        avg_score = average(values)
        average_rate = round(avg_score / max_score, 4) if avg_score is not None and max_score > 0 else None
        question_statistics.append({
            "questionId": question["id"], "order": int(question["sort_order"] or index + 1),
            "label": str(question["question"] or f"第 {index + 1} 题").strip()[:100],
            "maxScore": max_score, "averageScore": avg_score, "averageRate": average_rate,
            "lowScoreCount": low, "lowScoreRate": round(low / len(values), 4) if values else None,
            "partialScoreCount": partial, "partialScoreRate": round(partial / len(values), 4) if values else None,
            "correctCount": correct, "correctRate": round(correct / len(values), 4) if values else None,
            "averageLostScore": round(max_score - avg_score, 2) if avg_score is not None else None,
        })
    difficult = sorted(
        [item for item in question_statistics if item["averageRate"] is not None],
        key=lambda item: (-item["lowScoreRate"], item["averageRate"], -item["averageLostScore"]),
    )
    student_scores = []
    for row in finalized:
        if row["teacher_total"] is not None:
            student_scores.append({"userId": int(row["user_id"]), "studentName": row["student_name"], "studentNumber": row["student_number"], "totalScore": float(row["teacher_total"])})
    student_scores.sort(key=lambda item: (-item["totalScore"], item["studentNumber"] or "", item["userId"]))
    for rank, student in enumerate(student_scores, 1):
        student["rank"] = rank
    pass_count = sum(1 for score in scores if max_total and score / max_total >= 0.6)
    return jsonify({
        "assignmentId": assignment_id, "assignmentTitle": assignment["title"], "generatedAt": datetime.utcnow().isoformat(),
        "overview": {"totalStudents": len(student_rows), "submittedStudents": int(submitted_count), "finalizedStudents": len(scores),
                      "averageScore": average(scores), "medianScore": median(scores), "highestScore": max(scores) if scores else None,
                      "lowestScore": min(scores) if scores else None, "passRate": round(pass_count / len(scores), 4) if scores else None},
        "scoreDistribution": distribution, "questionStatistics": question_statistics, "errorDistribution": error_distribution,
        "difficultQuestions": [{"questionId": item["questionId"], "order": item["order"], "label": item["label"], "averageRate": item["averageRate"], "lowScoreRate": item["lowScoreRate"], "averageLostScore": item["averageLostScore"]} for item in difficult],
        "students": student_scores,
    })


@app.route("/api/v2/edu/submissions/<submission_id>", methods=["GET"])
def education_submission_detail(submission_id: str):
    user, error = _education_require_user()
    if error:
        return error
    submission, _assignment, _snapshot, error = _education_submission_context(submission_id, user)
    if error:
        return error
    role = "teacher" if user["education_role"] == "teacher" else "student"
    if role == "student" and submission["status"] != "released":
        return jsonify({"submission": _education_submission_payload(submission, role="student")})
    return jsonify({"submission": _education_submission_payload(submission, role=role)})


def _education_prepare_submission_ai_grading(db, submission, assignment):
    grade_rows = list(db.execute(
        "SELECT * FROM education_submission_question_grades WHERE submission_id = ?",
        (submission["id"],),
    ).fetchall())
    invalid_standards = []
    is_direct_assignment = assignment["assignment_type"] == "direct"
    for grade in grade_rows:
        reference_answer = str(grade["reference_answer"] or "").strip()
        expected_points = _education_json(grade["expected_points_json"], [])
        reference_report = _education_reference_matrix_report(reference_answer) if reference_answer else {"status": "not_applicable"}
        if (
            not reference_answer
            or (not is_direct_assignment and not expected_points)
            or float(grade["max_score"] or 0) <= 0
            or reference_report["status"] in {"contradicted", "structural_invalid"}
        ):
            invalid_standards.append(str(grade["question_id"]))
    if invalid_standards:
        return grade_rows, {}, invalid_standards

    now = datetime.utcnow().isoformat()
    snapshot = _education_json(submission["snapshot_json"], {})
    question_by_id = {
        str(item.get("questionId")): item
        for item in snapshot.get("questions") or []
        if isinstance(item, dict)
    }
    tasks = {}
    for grade in grade_rows:
        question_id = str(grade["question_id"])
        question = question_by_id.get(question_id, {})
        report = analyze_matrix_answer(grade["student_answer"], grade["reference_answer"])
        db.execute(
            "UPDATE education_submission_question_grades SET matrix_report_json = ?, updated_at = ? WHERE submission_id = ? AND question_id = ?",
            (json.dumps(report, ensure_ascii=False), now, submission["id"], grade["question_id"]),
        )
        tasks[question_id] = {
            "question": question.get("question") or "",
            "focus": question.get("focus") or "",
            "maxScore": float(grade["max_score"] or 0),
            "referenceAnswer": grade["reference_answer"],
            "expectedPoints": _education_json(grade["expected_points_json"], []),
            "studentAnswer": grade["student_answer"],
            "matrixCheck": report,
        }
    return grade_rows, tasks, []


def _education_apply_submission_ai_grading(db, submission_id: str, grade_rows, results: dict):
    suggested_total = 0.0
    valid = True
    now = datetime.utcnow().isoformat()
    for grade in grade_rows:
        question_id = str(grade["question_id"])
        result = results.get(question_id)
        max_score = float(grade["max_score"] or 0)
        try:
            suggested = round(float((result or {}).get("suggestedScore")), 1)
            returned_max = float((result or {}).get("maxScore"))
        except (TypeError, ValueError):
            valid = False
            continue
        if not isinstance(result, dict) or abs(returned_max - max_score) > 0.001 or suggested < 0 or suggested > max_score:
            valid = False
            continue
        suggested_total += suggested
        db.execute(
            "UPDATE education_submission_question_grades SET ai_result_json = ?, ai_suggested_score = ?, updated_at = ? WHERE submission_id = ? AND question_id = ?",
            (json.dumps(result, ensure_ascii=False), suggested, now, submission_id, grade["question_id"]),
        )
    ai_status = "ready" if valid else "failed"
    ai_error = None if valid else "grade_question_invalid_result"
    db.execute(
        "UPDATE education_assignment_submissions SET status = 'review_draft', ai_status = ?, ai_suggested_total = ?, ai_error = ?, updated_at = ? WHERE id = ?",
        (ai_status, round(suggested_total, 1) if valid else None, ai_error, now, submission_id),
    )
    return ai_status


def _education_bulk_submission_question_results(result, submission, grade_rows):
    if (
        not isinstance(result, dict)
        or result.get("submissionId") != submission["id"]
        or result.get("studentUserId") != int(submission["user_id"])
        or not isinstance(result.get("grades"), list)
    ):
        return {}
    by_question_id = {}
    for item in result["grades"]:
        if not isinstance(item, dict):
            return {}
        question_id = item.get("questionId")
        if not isinstance(question_id, str) or question_id in by_question_id:
            return {}
        by_question_id[question_id] = item
    expected_question_ids = {str(grade["question_id"]) for grade in grade_rows}
    return by_question_id if set(by_question_id) == expected_question_ids else {}


@app.route("/api/v2/edu/submissions/<submission_id>/evaluate", methods=["POST"])
def education_submission_evaluate(submission_id: str):
    user, error = _education_require_user(required_role="teacher")
    if error:
        return error
    submission, assignment, _snapshot, error = _education_submission_context(submission_id, user)
    if error:
        return error
    if submission["status"] in {"finalized", "released"}:
        return jsonify({"error": "finalized grading cannot be reevaluated", "code": "grading_finalized"}), 409
    db = _get_db()
    grade_rows, tasks, invalid_standards = _education_prepare_submission_ai_grading(db, submission, assignment)
    if invalid_standards:
        return jsonify({
            "error": "submission scoring standards require review before AI evaluation",
            "code": "assessment_scoring_required",
            "questionIds": invalid_standards,
        }), 409
    now = datetime.utcnow().isoformat()
    db.execute(
        "UPDATE education_assignment_submissions SET ai_status = 'running', ai_error = NULL, updated_at = ? WHERE id = ?",
        (now, submission_id),
    )
    db.commit()
    try:
        results = _education_ai_tasks(
            user_id=int(user["id"]),
            task_id=submission_id,
            task_kind="grade_question",
            tasks=tasks,
            scope=f"assignments/{submission['assignment_id']}/submissions/{submission_id}",
        ) if tasks else {}
    except Exception as exc:
        safe_error = _education_safe_error_message(exc, _education_llm_config(int(user["id"])))
        db.execute(
            "UPDATE education_assignment_submissions SET status = 'review_draft', ai_status = 'failed', ai_error = ?, updated_at = ? WHERE id = ?",
            (safe_error[:1000], datetime.utcnow().isoformat(), submission_id),
        )
        db.commit()
        return _education_ai_error_response(exc, "grading_ai_failed")
    _education_apply_submission_ai_grading(db, submission_id, grade_rows, results)
    db.commit()
    updated = db.execute("SELECT * FROM education_assignment_submissions WHERE id = ?", (submission_id,)).fetchone()
    return jsonify({"submission": _education_submission_payload(updated, role="teacher")})


@app.route("/api/v2/edu/assignments/<assignment_id>/submissions/evaluate", methods=["POST"])
def education_assignment_submissions_evaluate(assignment_id: str):
    user, error = _education_require_user(required_role="teacher")
    if error:
        return error
    assignment, _snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return error
    if membership["role"] != "teacher":
        return jsonify({"error": "forbidden"}), 403
    if assignment["status"] != "published" or assignment["grades_published_at"]:
        return jsonify({"error": "assignment is closed", "code": "assignment_closed"}), 409

    db = _get_db()
    submissions = list(db.execute(
        """SELECT * FROM education_assignment_submissions
             WHERE assignment_id = ? AND status NOT IN ('finalized', 'released')
             ORDER BY user_id, id""",
        (assignment_id,),
    ).fetchall())
    if not submissions:
        return jsonify({"error": "no submitted assignments require AI evaluation", "code": "grading_no_submissions"}), 409

    prepared = {}
    invalid = []
    for submission in submissions:
        grade_rows, question_tasks, invalid_standards = _education_prepare_submission_ai_grading(db, submission, assignment)
        if invalid_standards:
            invalid.append({"submissionId": submission["id"], "userId": int(submission["user_id"]), "questionIds": invalid_standards})
            continue
        task_key = f"submission:{submission['id']}:user:{int(submission['user_id'])}"
        prepared[task_key] = (submission, grade_rows, question_tasks)
    if invalid:
        db.rollback()
        return jsonify({
            "error": "submission scoring standards require review before AI evaluation",
            "code": "assessment_scoring_required",
            "submissions": invalid,
        }), 409

    now = datetime.utcnow().isoformat()
    tasks = {}
    for task_key, (submission, _grade_rows, question_tasks) in prepared.items():
        db.execute(
            "UPDATE education_assignment_submissions SET ai_status = 'running', ai_error = NULL, updated_at = ? WHERE id = ?",
            (now, submission["id"]),
        )
        tasks[task_key] = {
            "submissionId": submission["id"],
            "studentUserId": int(submission["user_id"]),
            "questions": [
                {"questionId": question_id, **question_task}
                for question_id, question_task in question_tasks.items()
            ],
        }
    db.commit()

    try:
        results = _education_ai_tasks(
            user_id=int(user["id"]),
            task_id=assignment_id,
            task_kind="grade_submission",
            tasks=tasks,
            scope=f"assignments/{assignment_id}/bulk-grading",
        )
    except Exception as exc:
        safe_error = _education_safe_error_message(exc, _education_llm_config(int(user["id"])))
        failed_at = datetime.utcnow().isoformat()
        for submission, _grade_rows, _question_tasks in prepared.values():
            db.execute(
                "UPDATE education_assignment_submissions SET status = 'review_draft', ai_status = 'failed', ai_error = ?, updated_at = ? WHERE id = ?",
                (safe_error[:1000], failed_at, submission["id"]),
            )
        db.commit()
        return _education_ai_error_response(exc, "grading_ai_failed")

    evaluated_submission_ids = []
    failed_submission_ids = []
    for task_key, (submission, grade_rows, _question_tasks) in prepared.items():
        question_results = _education_bulk_submission_question_results(results.get(task_key), submission, grade_rows)
        ai_status = _education_apply_submission_ai_grading(db, submission["id"], grade_rows, question_results)
        if ai_status == "ready":
            evaluated_submission_ids.append(submission["id"])
        else:
            failed_submission_ids.append(submission["id"])
    db.commit()
    return jsonify({
        "assignmentId": assignment_id,
        "requestedCount": len(prepared),
        "evaluatedCount": len(evaluated_submission_ids),
        "failedCount": len(failed_submission_ids),
        "evaluatedSubmissionIds": evaluated_submission_ids,
        "failedSubmissionIds": failed_submission_ids,
    })


@app.route("/api/v2/edu/submissions/<submission_id>/grade", methods=["PATCH"])
def education_submission_grade(submission_id: str):
    user, error = _education_require_user(required_role="teacher")
    if error:
        return error
    submission, _assignment, _snapshot, error = _education_submission_context(submission_id, user)
    if error:
        return error
    if submission["status"] in {"finalized", "released"}:
        return jsonify({"error": "grading is finalized", "code": "grading_finalized"}), 409
    body = request.get_json(silent=True) or {}
    raw_grades = body.get("grades")
    if not isinstance(raw_grades, list):
        return jsonify({"error": "grades must be a list"}), 400
    db = _get_db()
    stored = {row["question_id"]: row for row in db.execute("SELECT * FROM education_submission_question_grades WHERE submission_id = ?", (submission_id,)).fetchall()}
    now = datetime.utcnow().isoformat()
    for item in raw_grades:
        if not isinstance(item, dict) or item.get("questionId") not in stored:
            return jsonify({"error": "unknown grading question"}), 400
        question_id = item["questionId"]
        score_value = item.get("teacherScore")
        score = None
        if score_value is not None:
            try:
                score = round(float(score_value), 1)
            except (TypeError, ValueError):
                return jsonify({"error": "teacherScore must be numeric"}), 400
            if score < 0 or score > float(stored[question_id]["max_score"] or 0):
                return jsonify({"error": "teacherScore is outside the question range", "code": "grading_score_invalid"}), 400
        db.execute("UPDATE education_submission_question_grades SET teacher_score = ?, teacher_feedback = ?, updated_at = ? WHERE submission_id = ? AND question_id = ?", (score, str(item.get("teacherFeedback") or "").strip(), now, submission_id, question_id))
    saved_scores = db.execute("SELECT teacher_score FROM education_submission_question_grades WHERE submission_id = ?", (submission_id,)).fetchall()
    teacher_total = round(sum(float(row["teacher_score"] or 0) for row in saved_scores), 1) if saved_scores and all(row["teacher_score"] is not None for row in saved_scores) else None
    db.execute("UPDATE education_assignment_submissions SET status = 'review_draft', teacher_total = ?, teacher_summary = ?, updated_at = ? WHERE id = ?", (teacher_total, str(body.get("teacherSummary") or "").strip(), now, submission_id))
    db.commit()
    updated = db.execute("SELECT * FROM education_assignment_submissions WHERE id = ?", (submission_id,)).fetchone()
    return jsonify({"submission": _education_submission_payload(updated, role="teacher")})


@app.route("/api/v2/edu/submissions/<submission_id>/finalize", methods=["POST"])
def education_submission_finalize(submission_id: str):
    user, error = _education_require_user(required_role="teacher")
    if error:
        return error
    submission, _assignment, _snapshot, error = _education_submission_context(submission_id, user)
    if error:
        return error
    if submission["status"] == "released":
        return jsonify({"error": "grades are already released", "code": "grades_released"}), 409
    rows = _get_db().execute("SELECT teacher_score FROM education_submission_question_grades WHERE submission_id = ?", (submission_id,)).fetchall()
    if any(row["teacher_score"] is None for row in rows):
        return jsonify({"error": "every question requires a teacher score", "code": "grading_incomplete"}), 409
    total = round(sum(float(row["teacher_score"] or 0) for row in rows), 1)
    now = datetime.utcnow().isoformat()
    db = _get_db()
    db.execute("UPDATE education_assignment_submissions SET status = 'finalized', teacher_total = ?, finalized_at = ?, updated_at = ? WHERE id = ?", (total, now, now, submission_id))
    db.commit()
    updated = db.execute("SELECT * FROM education_assignment_submissions WHERE id = ?", (submission_id,)).fetchone()
    return jsonify({"submission": _education_submission_payload(updated, role="teacher")})


@app.route("/api/v2/edu/assignments/<assignment_id>/grades/publish", methods=["POST"])
def education_assignment_publish_grades(assignment_id: str):
    user, error = _education_require_user(required_role="teacher")
    if error:
        return error
    assignment, _snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return error
    if membership["role"] != "teacher":
        return jsonify({"error": "forbidden"}), 403
    if assignment["grades_published_at"]:
        return jsonify({"error": "grades are already released", "code": "grades_released"}), 409
    db = _get_db()
    submissions = db.execute("SELECT * FROM education_assignment_submissions WHERE assignment_id = ?", (assignment_id,)).fetchall()
    pending = [int(row["user_id"]) for row in submissions if row["status"] != "released" and row["teacher_total"] is None]
    if not submissions or pending:
        return jsonify({"error": "all submitted assignments must be scored and saved before release", "code": "grading_incomplete", "userIds": pending}), 409
    now = datetime.utcnow().isoformat()
    try:
        for submission in submissions:
            node_scores = defaultdict(lambda: [0.0, 0.0])
            for grade in db.execute("SELECT node_id, max_score, teacher_score FROM education_submission_question_grades WHERE submission_id = ?", (submission["id"],)).fetchall():
                node_scores[int(grade["node_id"])][0] += float(grade["teacher_score"] or 0)
                node_scores[int(grade["node_id"])][1] += float(grade["max_score"] or 0)
            for node_id, (score, maximum) in node_scores.items():
                state = "mastered" if maximum > 0 and score / maximum >= 0.6 else "needs_review"
                db.execute(
                    """INSERT INTO education_node_progress
                         (assignment_id, user_id, node_id, state, mastery_source, diagnostic_summary, updated_at)
                       VALUES (?, ?, ?, ?, 'teacher', ?, ?)
                       ON DUPLICATE KEY UPDATE
                         state = VALUES(state), mastery_source = 'teacher', diagnostic_summary = VALUES(diagnostic_summary), updated_at = VALUES(updated_at)""",
                    (assignment_id, submission["user_id"], node_id, state, submission["teacher_summary"] or "", now),
                )
            possible_score = sum(maximum for _score, maximum in node_scores.values())
            earned_score = sum(score for score, _maximum in node_scores.values())
            if possible_score > 0:
                education_game.grant_excellent_assignment_chest(
                    db,
                    class_row=membership,
                    user_id=int(submission["user_id"]),
                    submission_id=submission["id"],
                    percentage=earned_score / possible_score * 100,
                )
            db.execute("UPDATE education_assignment_submissions SET status = 'released', released_at = ?, updated_at = ? WHERE id = ?", (now, now, submission["id"]))
        db.execute("UPDATE education_assignments SET grades_published_at = ?, updated_at = ? WHERE id = ?", (now, now, assignment_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return jsonify({"assignmentId": assignment_id, "gradesPublishedAt": now, "releasedCount": len(submissions)})


@app.route("/api/v2/edu/assignments/<assignment_id>/diagnostics", methods=["POST"])
def education_diagnostic_create(assignment_id: str):
    return jsonify({
        "error": "diagnostics_replaced",
        "code": "diagnostics_replaced",
        "message": "Use the teacher-reviewed assessment flow.",
    }), 410

    user, error = _education_require_user()
    if error:
        return error
    assignment, snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return error
    if membership["role"] != "student":
        return jsonify({"error": "only students can run diagnostics"}), 403
    body = request.get_json(silent=True) or {}
    try:
        node_id = int(body.get("nodeId"))
    except (TypeError, ValueError):
        return jsonify({"error": "nodeId must be an integer"}), 400
    base_path = _education_json(assignment["base_path_json"], {})
    if node_id not in set(base_path.get("candidateNodeIds") or []):
        return jsonify({"error": "node is outside the frozen learning path"}), 400
    nodes = _education_snapshot_graph(snapshot)["nodes"]
    node = next((item for item in nodes if item.get("id") == node_id), None)
    if not node:
        return jsonify({"error": "node not found"}), 404
    diagnostic_id = uuid.uuid4().hex
    try:
        question = _education_ai_task(
            user_id=int(user["id"]),
            task_id=diagnostic_id,
            task_kind="question",
            payload={
                "nodeId": node_id,
                "title": _education_node_title(node),
                "statement": node.get("content") or node.get("source_statement") or "",
            },
            scope=f"diagnostics/{diagnostic_id}",
        )
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    now = datetime.utcnow().isoformat()
    db = _get_db()
    db.execute(
        """INSERT INTO education_diagnostics
             (id, assignment_id, user_id, node_id, question_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            diagnostic_id, assignment_id, user["id"], node_id,
            json.dumps(question, ensure_ascii=False), now, now,
        ),
    )
    db.commit()
    return jsonify({
        "diagnostic": {
            "id": diagnostic_id,
            "assignmentId": assignment_id,
            "nodeId": node_id,
            "question": question.get("question"),
            "focus": question.get("focus") or "",
            "createdAt": now,
        },
    }), 201


@app.route("/api/v2/edu/diagnostics/<diagnostic_id>/submit", methods=["POST"])
def education_diagnostic_submit(diagnostic_id: str):
    return jsonify({
        "error": "diagnostics_replaced",
        "code": "diagnostics_replaced",
        "message": "Use the teacher-reviewed assessment flow.",
    }), 410

    user, error = _education_require_user()
    if error:
        return error
    db = _get_db()
    diagnostic = db.execute(
        "SELECT * FROM education_diagnostics WHERE id = ? AND user_id = ?",
        (diagnostic_id, user["id"]),
    ).fetchone()
    if not diagnostic:
        return jsonify({"error": "diagnostic not found"}), 404
    assignment, snapshot, membership, error = _education_assignment_context(
        diagnostic["assignment_id"], user
    )
    if error:
        return error
    if membership["role"] != "student":
        return jsonify({"error": "forbidden"}), 403
    body = request.get_json(silent=True) or {}
    answer = str(body.get("answer") or "").strip()
    if not answer:
        return jsonify({"error": "answer is required"}), 400
    nodes = _education_snapshot_graph(snapshot)["nodes"]
    node = next((item for item in nodes if item.get("id") == diagnostic["node_id"]), None)
    question = _education_json(diagnostic["question_json"], {})
    try:
        evaluation = _education_ai_task(
            user_id=int(user["id"]),
            task_id=diagnostic_id,
            task_kind="evaluate",
            payload={
                "node": {
                    "nodeId": diagnostic["node_id"],
                    "title": _education_node_title(node or {}),
                    "statement": (node or {}).get("content")
                    or (node or {}).get("source_statement")
                    or "",
                },
                "question": question.get("question") or "",
                "expectedPoints": question.get("expectedPoints") or [],
                "answer": answer,
            },
            scope=f"diagnostics/{diagnostic_id}",
        )
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    result = evaluation["result"]
    summary = evaluation["summary"]
    now = datetime.utcnow().isoformat()
    db.execute(
        """UPDATE education_diagnostics
           SET answer = ?, result = ?, summary = ?, updated_at = ? WHERE id = ?""",
        (answer, result, summary, now, diagnostic_id),
    )
    db.execute(
        """INSERT INTO education_node_progress
             (assignment_id, user_id, node_id, state, mastery_source,
              diagnostic_summary, updated_at)
           VALUES (?, ?, ?, ?, 'diagnostic', ?, ?)
           ON DUPLICATE KEY UPDATE
             state = VALUES(state),
             mastery_source = 'diagnostic',
             diagnostic_summary = VALUES(diagnostic_summary),
             updated_at = VALUES(updated_at)""",
        (
            assignment["id"], user["id"], diagnostic["node_id"],
            result, summary, now,
        ),
    )
    db.commit()
    return jsonify({
        "result": result,
        "summary": summary,
        "nextStep": evaluation.get("nextStep") or "",
    })


@app.route("/api/v2/edu/assignments/<assignment_id>/overview", methods=["GET"])
def education_assignment_overview(assignment_id: str):
    user, error = _education_require_user()
    if error:
        return error
    assignment, _snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return error
    if membership["role"] != "teacher":
        return jsonify({"error": "forbidden"}), 403
    total_steps = len(_education_json(assignment["base_path_json"], {}).get("steps") or [])
    rows = _get_db().execute(
        """SELECT u.id AS user_id, m.student_name, m.student_number,
                  SUM(CASE WHEN p.state = 'mastered' THEN 1 ELSE 0 END) AS mastered_count,
                  SUM(CASE WHEN p.state = 'needs_review' THEN 1 ELSE 0 END) AS needs_review_count,
                  MAX(p.updated_at) AS last_activity,
                  (SELECT d.summary FROM education_diagnostics d
                   WHERE d.assignment_id = ? AND d.user_id = u.id AND d.summary IS NOT NULL
                   ORDER BY d.updated_at DESC LIMIT 1) AS diagnostic_summary
           FROM education_memberships m
           JOIN users u ON u.id = m.user_id
           LEFT JOIN education_node_progress p
             ON p.assignment_id = ? AND p.user_id = u.id
           WHERE m.class_id = ? AND m.role = 'student' AND m.removed_at IS NULL
           GROUP BY u.id, m.student_name, m.student_number
           ORDER BY m.student_number, u.id""",
        (assignment_id, assignment_id, assignment["class_id"]),
    ).fetchall()
    return jsonify({
        "assignmentId": assignment_id,
        "totalSteps": total_steps,
        "students": [
            {
                "userId": row["user_id"],
                "studentName": row["student_name"],
                "studentNumber": row["student_number"],
                "profileComplete": bool(row["student_name"] and row["student_number"]),
                "masteredCount": int(row["mastered_count"] or 0),
                "needsReviewCount": int(row["needs_review_count"] or 0),
                "completionRate": (
                    round(int(row["mastered_count"] or 0) / total_steps, 4)
                    if total_steps else 0
                ),
                "lastActivityAt": row["last_activity"],
                "diagnosticSummary": row["diagnostic_summary"],
            }
            for row in rows
        ],
    })


def _education_context_node_ids(snapshot, node_id: int) -> list[int]:
    related = []
    for edge in _education_snapshot_graph(snapshot)["edges"]:
        if not isinstance(edge, dict):
            continue
        source, target = edge.get("from"), edge.get("to")
        candidate = target if source == node_id else source if target == node_id else None
        if isinstance(candidate, int) and candidate not in related:
            related.append(candidate)
    return related


@app.route(
    "/api/v2/edu/assignments/<assignment_id>/student-context",
    methods=["GET"],
)
def education_student_context(assignment_id: str):
    user, error = _education_require_user(required_role="student")
    if error:
        return error
    assignment, snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return error
    if membership["role"] != "student":
        return jsonify({"error": "only students can view their context"}), 403
    raw_node_id = request.args.get("nodeId")
    if raw_node_id is None or raw_node_id == "":
        db = _get_db()
        overview = build_student_context_overview(
            db,
            assignment=assignment,
            snapshot=snapshot,
            user_id=int(user["id"]),
        )
        db.commit()
        return jsonify(overview)
    try:
        node_id = int(raw_node_id)
    except (TypeError, ValueError):
        return jsonify({"error": "nodeId must be an integer"}), 400
    if node_id not in set(_education_path_node_ids(_education_json(assignment["base_path_json"], {}))):
        return jsonify({"error": "node is outside the frozen learning path"}), 400
    try:
        db = _get_db()
        packet = build_student_context_packet(
            db,
            assignment=assignment,
            snapshot=snapshot,
            user_id=int(user["id"]),
            node_id=node_id,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    db.commit()
    return jsonify({
        "contextVersion": packet["contextVersion"],
        "contextPreview": context_preview(packet),
        "tokenEstimate": packet["tokenEstimate"],
        "historyTokenEstimate": packet["historyTokenEstimate"],
    })


@app.route(
    "/api/v2/edu/assignments/<assignment_id>/proof-assist",
    methods=["POST"],
)
def education_proof_assist(assignment_id: str):
    user, error = _education_require_user(required_role="student")
    if error:
        return error
    assignment, snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return error
    if membership["role"] != "student":
        return jsonify({"error": "only students can use course proof assistance"}), 403
    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "").strip()
    if action not in {"hint", "check", "summarize"}:
        return jsonify({"error": "invalid action"}), 400
    try:
        node_id = int(body.get("nodeId"))
    except (TypeError, ValueError):
        return jsonify({"error": "nodeId must be an integer"}), 400
    path_node_ids = set(_education_path_node_ids(_education_json(assignment["base_path_json"], {})))
    if node_id not in path_node_ids:
        return jsonify({"error": "node is outside the frozen learning path"}), 400
    client_interaction_id = str(body.get("clientInteractionId") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9:._-]{1,128}", client_interaction_id):
        return jsonify({"error": "clientInteractionId is required or invalid"}), 400
    user_proof = str(body.get("userProof") or "")
    if len(user_proof) > 200000:
        return jsonify({"error": "userProof is too large"}), 413

    db = _get_db()
    existing = load_idempotent_result(
        db,
        assignment_id=assignment_id,
        user_id=int(user["id"]),
        client_interaction_id=client_interaction_id,
    )
    if existing:
        return jsonify(existing)
    incomplete = db.execute(
        """SELECT id FROM learning_interactions
            WHERE assignment_id = ? AND user_id = ? AND client_interaction_id = ?""",
        (assignment_id, user["id"], client_interaction_id),
    ).fetchone()
    if incomplete:
        return jsonify({
            "error": "interaction is still being finalized",
            "code": "interaction_incomplete",
        }), 409

    config = _education_llm_config(int(user["id"]))
    if not config:
        return jsonify({
            "error": "education AI is not configured",
            "code": "education_ai_unconfigured",
        }), 503
    allowed, _remaining = _education_consume_ai_quota(int(user["id"]))
    if not allowed:
        return jsonify({
            "error": "education AI daily limit reached",
            "code": "education_ai_limit_reached",
        }), 429
    try:
        packet = build_student_context_packet(
            db,
            assignment=assignment,
            snapshot=snapshot,
            user_id=int(user["id"]),
            node_id=node_id,
            user_proof=user_proof,
            action=action,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    related_node_ids = _education_context_node_ids(snapshot, node_id)
    task_record_id = uuid.uuid4().hex
    now = datetime.utcnow().isoformat()
    db.execute(
        """INSERT INTO education_ai_tasks
             (id, task_key, user_id, task_kind, scope, status, created_at, updated_at)
           VALUES (?, ?, ?, 'student_proof_assist', ?, 'running', ?, ?)""",
        (
            task_record_id,
            client_interaction_id,
            user["id"],
            f"assignments/{assignment_id}/student-context",
            now,
            now,
        ),
    )
    db.commit()
    try:
        llm_context = create_education_context(_DATA_ROOT, config)
        assist_result = run_structured_proof_assist(
            context=llm_context,
            action=action,
            packet=packet,
            allowed_related_node_ids=related_node_ids,
        )
        stored = store_interaction_with_evidence(
            db,
            assignment=assignment,
            snapshot=snapshot,
            user_id=int(user["id"]),
            node_id=node_id,
            client_interaction_id=client_interaction_id,
            action=action,
            user_proof=user_proof,
            assistant_response=assist_result["response"],
            context_packet=packet,
            learning_delta=assist_result["learningDelta"],
            classification_status=assist_result["classificationStatus"],
        )
        refreshed_packet = build_student_context_packet(
            db,
            assignment=assignment,
            snapshot=snapshot,
            user_id=int(user["id"]),
            node_id=node_id,
            user_proof=user_proof,
            action=action,
        )
        preview = context_preview(refreshed_packet)
        preview["contextVersion"] = stored["contextVersion"]
        requested_context_version = body.get("contextVersion")
        result = {
            "response": assist_result["response"],
            "interactionId": stored["interactionId"],
            "contextVersion": stored["contextVersion"],
            "contextPreview": preview,
            "stateChanges": stored["stateChanges"],
            "classificationStatus": assist_result["classificationStatus"],
            "staleContext": (
                isinstance(requested_context_version, int)
                and requested_context_version != packet["contextVersion"]
            ),
        }
        save_interaction_result(db, interaction_id=stored["interactionId"], result=result)
        db.execute(
            "UPDATE education_ai_tasks SET status = 'done', updated_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), task_record_id),
        )
        db.commit()
        return jsonify(result)
    except Exception as exc:
        safe_error = _education_safe_error_message(exc, config)
        db.rollback()
        db.execute(
            """UPDATE education_ai_tasks
               SET status = 'failed', error = ?, updated_at = ? WHERE id = ?""",
            (safe_error[:1000], datetime.utcnow().isoformat(), task_record_id),
        )
        db.commit()
        return jsonify({"error": "student proof assistance failed", "message": safe_error}), 502


@app.route("/api/v2/edu/context/evidence/<evidence_id>", methods=["PATCH"])
def education_context_evidence_feedback(evidence_id: str):
    user, error = _education_require_user(required_role="student")
    if error:
        return error
    body = request.get_json(silent=True) or {}
    status = str(body.get("status") or "").strip()
    try:
        evidence = update_evidence_status(
            _get_db(),
            evidence_id=evidence_id,
            user_id=int(user["id"]),
            new_status=status,
            note=str(body.get("note") or ""),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not evidence:
        return jsonify({"error": "evidence not found"}), 404
    _get_db().commit()
    return jsonify({"evidence": evidence})


@app.route(
    "/api/v2/edu/assignments/<assignment_id>/students/<int:student_user_id>/context-summary",
    methods=["GET"],
)
def education_teacher_student_context_summary(assignment_id: str, student_user_id: int):
    user, error = _education_require_user(required_role="teacher")
    if error:
        return error
    assignment, snapshot, membership, error = _education_assignment_context(assignment_id, user)
    if error:
        return error
    if membership["role"] != "teacher":
        return jsonify({"error": "forbidden"}), 403
    student = _get_db().execute(
        """SELECT u.id, m.student_name, m.student_number
             FROM education_memberships m
             JOIN users u ON u.id = m.user_id
            WHERE m.class_id = ? AND m.user_id = ? AND m.role = 'student'
              AND m.removed_at IS NULL""",
        (assignment["class_id"], student_user_id),
    ).fetchone()
    if not student:
        return jsonify({"error": "student not found", "code": "student_not_found"}), 404
    summary = build_teacher_context_summary(
        _get_db(),
        assignment=assignment,
        snapshot=snapshot,
        student_user_id=student_user_id,
    )
    _get_db().commit()
    return jsonify({
        "student": {
            "userId": student["id"],
            "studentName": student["student_name"],
            "studentNumber": student["student_number"],
        },
        "summary": summary,
    })


def _education_student_context_owner(class_id: str, user):
    # Data-rights endpoints remain available after a class is archived or a
    # student is removed; ownership is still constrained by user_id + class_id.
    membership = _get_db().execute(
        """SELECT m.role FROM education_memberships m
            JOIN education_classes c ON c.id = m.class_id
            WHERE m.class_id = ? AND m.user_id = ?""",
        (class_id, user["id"]),
    ).fetchone()
    if not membership:
        return None, (jsonify({"error": "class not found"}), 404)
    if membership["role"] != "student":
        return None, (jsonify({"error": "only students can manage their learning context"}), 403)
    return membership, None


@app.route("/api/v2/edu/classes/<class_id>/student-context/export", methods=["GET"])
def education_student_context_export(class_id: str):
    user, error = _education_require_user(required_role="student")
    if error:
        return error
    _membership, error = _education_student_context_owner(class_id, user)
    if error:
        return error
    payload = export_student_context(
        _get_db(), class_id=class_id, user_id=int(user["id"])
    )
    response = jsonify(payload)
    response.headers["Content-Disposition"] = (
        f'attachment; filename="mathweaver-student-context-{class_id}.json"'
    )
    return response


@app.route("/api/v2/edu/classes/<class_id>/student-context", methods=["DELETE"])
def education_student_context_delete(class_id: str):
    user, error = _education_require_user(required_role="student")
    if error:
        return error
    _membership, error = _education_student_context_owner(class_id, user)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    if str(body.get("confirmClassId") or "") != class_id:
        return jsonify({
            "error": "confirmClassId must match the course",
            "code": "context_delete_confirmation_required",
        }), 400
    db = _get_db()
    try:
        counts = delete_student_context(
            db, class_id=class_id, user_id=int(user["id"])
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return jsonify({"ok": True, **counts})


def _education_snapshot_context(snapshot_id: str, user):
    snapshot = _get_db().execute(
        "SELECT * FROM education_snapshots WHERE id = ?", (snapshot_id,)
    ).fetchone()
    if not snapshot:
        return None, (jsonify({"error": "snapshot not found"}), 404)
    _membership, error = _education_require_membership(snapshot["class_id"], user)
    if error:
        return None, error
    return snapshot, None


@app.route("/api/v2/edu/snapshots/<snapshot_id>", methods=["GET", "DELETE"])
def education_snapshot(snapshot_id: str):
    user, error = _education_require_user()
    if error:
        return error
    snapshot, error = _education_snapshot_context(snapshot_id, user)
    if error:
        return error
    if request.method == "DELETE":
        membership, membership_error = _education_require_membership(snapshot["class_id"], user)
        if membership_error:
            return membership_error
        if membership["role"] != "teacher":
            return jsonify({"error": "forbidden"}), 403

        db = _get_db()
        source_graph_id = str(snapshot["source_graph_id"] or "").strip()
        if source_graph_id:
            snapshot_rows = db.execute(
                """SELECT id FROM education_snapshots
                    WHERE class_id = ? AND source_graph_id = ?""",
                (snapshot["class_id"], source_graph_id),
            ).fetchall()
        else:
            snapshot_rows = [snapshot]
        snapshot_ids = [str(row["id"]) for row in snapshot_rows]
        snapshot_placeholders = ",".join("?" for _ in snapshot_ids)
        assignment_rows = db.execute(
            f"SELECT id FROM education_assignments WHERE snapshot_id IN ({snapshot_placeholders})",
            snapshot_ids,
        ).fetchall()
        assignment_ids = [str(row["id"]) for row in assignment_rows]
        diagnostic_ids = []
        if assignment_ids:
            assignment_placeholders = ",".join("?" for _ in assignment_ids)
            diagnostic_ids = [
                str(row["id"])
                for row in db.execute(
                    f"SELECT id FROM education_diagnostics WHERE assignment_id IN ({assignment_placeholders})",
                    assignment_ids,
                ).fetchall()
            ]

        task_keys = assignment_ids + diagnostic_ids
        try:
            for item in snapshot_ids:
                prepare_graph_delete(item)
                _invalidate_api_graph(item)
            if task_keys:
                task_placeholders = ",".join("?" for _ in task_keys)
                db.execute(
                    f"DELETE FROM education_ai_tasks WHERE task_key IN ({task_placeholders})",
                    task_keys,
                )
            if assignment_ids:
                assignment_placeholders = ",".join("?" for _ in assignment_ids)
                db.execute(
                    f"DELETE FROM education_assignments WHERE id IN ({assignment_placeholders})",
                    assignment_ids,
                )
            db.execute(
                "DELETE FROM education_course_graph_order WHERE class_id = ? AND graph_key = ?",
                (snapshot["class_id"], _education_course_graph_key(snapshot)),
            )
            db.execute(
                f"DELETE FROM education_snapshots WHERE id IN ({snapshot_placeholders})",
                snapshot_ids,
            )
            db.commit()
            for item in snapshot_ids:
                complete_graph_delete(item)
        except Exception:
            db.rollback()
            raise

        cleanup_warnings = []
        cleanup_targets = [
            *(_EDUCATION_SNAPSHOT_ROOT / item for item in snapshot_ids),
            *(_EDUCATION_ROOT / "assignments" / item for item in assignment_ids),
            *(_EDUCATION_ROOT / "diagnostics" / item for item in diagnostic_ids),
        ]
        allowed_roots = {
            _EDUCATION_SNAPSHOT_ROOT.resolve(),
            (_EDUCATION_ROOT / "assignments").resolve(),
            (_EDUCATION_ROOT / "diagnostics").resolve(),
        }
        for target in cleanup_targets:
            try:
                resolved = target.resolve()
                if resolved.parent not in allowed_roots:
                    raise ValueError("unsafe education resource path")
                if resolved.is_dir():
                    shutil.rmtree(resolved)
            except (OSError, ValueError) as exc:
                cleanup_warnings.append(f"{target.name}: {exc}")
        return jsonify({
            "ok": True,
            "deletedSnapshotIds": snapshot_ids,
            "deletedAssignmentCount": len(assignment_ids),
            "cleanupWarnings": cleanup_warnings,
        })
    return jsonify({"snapshot": _education_public_snapshot(snapshot, include_graph=True)})


@app.route("/api/v2/edu/snapshots/<snapshot_id>/source-pdf", methods=["GET"])
def education_snapshot_pdf(snapshot_id: str):
    user, error = _education_require_user()
    if error:
        return error
    snapshot, error = _education_snapshot_context(snapshot_id, user)
    if error:
        return error
    meta = _education_snapshot_pdf_meta(snapshot)
    pdf_path = Path(str((meta or {}).get("pdf_path") or ""))
    if not meta or not meta.get("available") or not pdf_path.is_file():
        return jsonify({"error": (meta or {}).get("error") or "source PDF unavailable"}), 404
    return send_file(pdf_path, mimetype="application/pdf", as_attachment=False)


@app.route("/api/v2/edu/snapshots/<snapshot_id>/compile-log", methods=["GET"])
def education_snapshot_compile_log(snapshot_id: str):
    user, error = _education_require_user()
    if error:
        return error
    snapshot, error = _education_snapshot_context(snapshot_id, user)
    if error:
        return error
    meta = _education_snapshot_pdf_meta(snapshot)
    log_path = Path(str((meta or {}).get("log_path") or ""))
    if not log_path.is_file():
        return jsonify({"error": "compile log not found"}), 404
    return send_file(log_path, mimetype="text/plain; charset=utf-8", as_attachment=False)


@app.route("/api/v2/edu/snapshots/<snapshot_id>/locate", methods=["GET"])
def education_snapshot_locate(snapshot_id: str):
    user, error = _education_require_user()
    if error:
        return error
    snapshot, error = _education_snapshot_context(snapshot_id, user)
    if error:
        return error
    try:
        node_id = int(request.args.get("node_id", ""))
    except (TypeError, ValueError):
        return jsonify({"error": "node_id must be an integer"}), 400
    nodes = _education_snapshot_graph(snapshot)["nodes"]
    node = next((item for item in nodes if item.get("id") == node_id), None)
    if not node:
        return jsonify({"error": "node not found"}), 404
    meta = _education_snapshot_pdf_meta(snapshot)
    if not meta or not meta.get("available"):
        return jsonify({"error": (meta or {}).get("error") or "source PDF unavailable"}), 404
    statement_terms = _tex_statement_terms(meta, node)
    terms = statement_terms + [term for term in _node_locator_terms(node) if term not in statement_terms]
    return jsonify({
        "node_id": node_id,
        "page": _synctex_page(meta, node) or 1,
        "search_terms": terms,
        "statement_terms": statement_terms,
        "source_statement": _node_original_statement(node),
        "source_key": _node_source_key(node),
        "pdf_url": meta["pdf_url"],
        "source_span": node.get("source_span"),
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


class PipelineCapacityUnavailable(RuntimeError):
    """The server is healthy but all safe pipeline slots are occupied."""


def _prune_terminal_jobs(now: float | None = None) -> int:
    """Bound retained in-process results without deleting persisted history."""
    cutoff = (time.time() if now is None else now) - _JOB_MEMORY_TTL_SECONDS
    disposable_artifacts: list[tuple[str, str | None]] = []
    removed_count = 0
    with _jobs_lock:
        for job_id, job in tuple(_jobs.items()):
            if job_id in _job_runtimes:
                continue
            if job.get("status") not in {"done", "error"}:
                continue
            finished_at = job.get("_finished_at")
            if finished_at is None or float(finished_at) > cutoff:
                continue
            removed = _jobs.pop(job_id, None)
            removed_count += int(removed is not None)
            if removed and not removed.get("_persistent_artifacts"):
                disposable_artifacts.append((job_id, removed.get("_artifact_dir")))
    for job_id, artifact_dir in disposable_artifacts:
        try:
            _remove_job_artifacts(job_id, artifact_dir)
        except (OSError, ValueError):
            pass
    return removed_count


def _release_capacity_leases(leases) -> None:
    for lease in reversed(tuple(leases or ())):
        lease.release()


def _acquire_pipeline_capacity_leases():
    pipeline_lease = _PIPELINE_CAPACITY_POOL.try_acquire()
    if pipeline_lease is None:
        raise PipelineCapacityUnavailable("pipeline capacity is busy")
    ai_lease = _AI_CAPACITY_POOL.try_acquire()
    if ai_lease is None:
        pipeline_lease.release()
        raise PipelineCapacityUnavailable("AI capacity is busy")
    return pipeline_lease, ai_lease


def _pipeline_busy_response():
    response = jsonify({
        "error": "server_busy",
        "code": "pipeline_capacity_busy",
        "message": "服务器正在处理其他分析任务，请稍后重试。",
        "retry_after": _CAPACITY_RETRY_AFTER,
    })
    response.headers["Retry-After"] = str(_CAPACITY_RETRY_AFTER)
    return response, 429


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
_TEX_MATRIX_ENV_RE = re.compile(
    r"\\begin\{(?P<env>matrix|pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix|array|cases|aligned|gathered|smallmatrix)\}"
    r"(?P<spec>\{[^{}]*\})?(?P<body>[\s\S]*?)\\end\{(?P=env)\}"
)


def _clean_str(s: str) -> str:
    """Strip pipeline artifacts: Python string markers and @@CMD:: placeholders."""
    s = _RE_PY_STR.sub("", s.strip())
    s = _RE_CMD_ARTIFACT.sub("", s)
    return s.strip()


def _tex_integrity_issues(text: str) -> list[str]:
    """Return non-mutating display diagnostics for canonical TeX text."""
    if not isinstance(text, str):
        return []
    issues: list[str] = []
    for match in _TEX_MATRIX_ENV_RE.finditer(text):
        body = match.group("body")
        # A single slash immediately before a row-like token is the usual
        # irreversible symptom of a former `\\\\` -> `\\` display rewrite.
        if re.search(r"(?<!\\)\\(?!\\)\s*(?=[&+-]?\d|\[[^\]]*\])", body):
            issues.append("matrix_row_separator")
    begins = re.findall(r"\\begin\{([^{}]+)\}", text)
    ends = re.findall(r"\\end\{([^{}]+)\}", text)
    if sorted(begins) != sorted(ends):
        issues.append("unpaired_environment")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
        issues.append("control_character")
    return issues


def _matrix_body_signature(body: str) -> str:
    """Compare matrix cells without treating lost row slashes as content."""
    return re.sub(r"\s+", "", re.sub(r"\\+", r"\\", body))


def _matrix_body_diff_is_lost_row_slashes(target: str, source: str) -> bool:
    """Return whether target differs from source only by missing row slashes."""
    target = re.sub(r"\s+", "", target)
    source = re.sub(r"\s+", "", source)
    target_index = source_index = 0
    lost = False
    while target_index < len(target) and source_index < len(source):
        if target[target_index] == "\\" and source[source_index] == "\\":
            target_end = target_index
            while target_end < len(target) and target[target_end] == "\\":
                target_end += 1
            source_end = source_index
            while source_end < len(source) and source[source_end] == "\\":
                source_end += 1
            target_count = target_end - target_index
            source_count = source_end - source_index
            if target_count > source_count:
                return False
            lost = lost or target_count < source_count
            target_index, source_index = target_end, source_end
            continue
        if target[target_index] != source[source_index]:
            return False
        target_index += 1
        source_index += 1
    return lost and target_index == len(target) and source_index == len(source)


def _tex_source_span(node: dict, source_markdown: str) -> str:
    span = node.get("source_span")
    if not isinstance(span, dict) or not isinstance(source_markdown, str):
        return ""
    start, end = span.get("start"), span.get("end")
    if not isinstance(start, int) or not isinstance(end, int):
        return ""
    if not 0 <= start <= end <= len(source_markdown):
        return ""
    return source_markdown[start:end]


def _restore_legacy_matrix_text(value, source_matches: dict[str, set[str]]):
    if isinstance(value, str):
        def restore(match: re.Match) -> str:
            body = match.group("body")
            choices = source_matches.get(_matrix_body_signature(body), set())
            choices = {
                source_body for source_body in choices
                if _matrix_body_diff_is_lost_row_slashes(body, source_body)
            }
            if len(choices) != 1:
                return match.group(0)
            # Preserve the display environment and array column specification
            # produced for this field; recover only the lost row separators.
            return f"\\begin{{{match.group('env')}}}{match.group('spec') or ''}{next(iter(choices))}\\end{{{match.group('env')}}}"

        return _TEX_MATRIX_ENV_RE.sub(restore, value)
    if isinstance(value, list):
        return [_restore_legacy_matrix_text(item, source_matches) for item in value]
    if isinstance(value, dict):
        return {key: _restore_legacy_matrix_text(item, source_matches) for key, item in value.items()}
    return value


def _legacy_display_nodes(nodes: list, source_markdown: str) -> list:
    """Safely repair old matrix text in an API response without writing storage.

    A restoration is allowed only when exactly one complete matrix environment
    in a retained source field has the same shape after the known slash-loss
    normalization.  Ambiguous or source-less imported graphs are left intact.
    """
    if not isinstance(nodes, list):
        return nodes
    projected = copy.deepcopy(nodes)
    for node in projected:
        if not isinstance(node, dict):
            continue
        # The recorded source span is the narrowest authority. Fall back to
        # retained node text only when a source document/span is unavailable.
        source_span_text = _tex_source_span(node, source_markdown)
        retained_sources: list[str] = [source_span_text] if source_span_text else []
        if not retained_sources:
            for key in ("source_text", "source_statement"):
                value = node.get(key)
                if isinstance(value, str) and value:
                    retained_sources.append(value)

        source_matches: dict[str, set[str]] = {}
        for source in retained_sources:
            for match in _TEX_MATRIX_ENV_RE.finditer(source):
                source_matches.setdefault(_matrix_body_signature(match.group("body")), set()).add(match.group("body"))
        if not source_matches:
            continue

        for field in (
            "title_zh", "title_en", "content", "proof", "source_text",
            "source_statement", "subject", "conditions", "conclusions",
        ):
            if field in node:
                node[field] = _restore_legacy_matrix_text(node[field], source_matches)
    return projected


def _project_display_result(result: dict, source_markdown: str) -> dict:
    """Return an immutable API display projection for a graph result."""
    projected = copy.deepcopy(result)
    if isinstance(projected.get("nodes"), list):
        projected["nodes"] = _legacy_display_nodes(projected["nodes"], source_markdown)
    return projected


def _extract_text(val) -> str:
    """Coerce a pipeline value to its canonical parsed string.

    Display formatting belongs to the frontend.  Rewriting TeX here changes
    persisted strings after MatrixFlow source spans have been calculated.
    """
    if not val:
        return ""
    if isinstance(val, str):
        return _clean_str(val)
    if isinstance(val, dict):
        raw = val.get("text") or val.get("text_normalized") or val.get("original_form") or ""
        return _clean_str(raw) if isinstance(raw, str) else ""
    return _clean_str(str(val))


def _extract_list(val) -> list:
    """Coerce a pipeline value to a list of strings."""
    if not val:
        return []
    if isinstance(val, list):
        return [_extract_text(item) for item in val if item]
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
                "content": _clean_str(str(n.get("content") or "")),
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
                "matrix_flows": n.get("matrix_flows") if isinstance(n.get("matrix_flows"), list) else [],
            })
            continue
        title_obj = n.get("title", {})
        zh = _clean_str(_title_str(title_obj) if isinstance(title_obj, dict) else str(title_obj or ""))
        en_raw = (title_obj.get("english") or zh) if isinstance(title_obj, dict) else zh
        en = _clean_str(en_raw) if isinstance(en_raw, str) else zh
        content = _clean_str(n.get("content") or "")
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
            "matrix_flows": n.get("matrix_flows") if isinstance(n.get("matrix_flows"), list) else [],
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
        nodes = _legacy_display_nodes(_normalize_nodes(raw_nodes), job.get("source_markdown") or "")
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


def _partial_result_from_state(state: dict, source_markdown: str = ""):
    raw_nodes = state.get("node_list") or state.get("node_dict")
    if isinstance(raw_nodes, dict):
        raw_nodes = list(raw_nodes.values())
    if not raw_nodes:
        return None
    return {
        "nodes": _legacy_display_nodes(_normalize_nodes(raw_nodes), source_markdown),
        "edges": [],
    }


def _execute_pipeline_worker(payload: dict, emit):
    job_id = payload["job_id"]
    md_path = payload["md_path"]
    llm = payload["llm"]
    source_format = payload.get("source_format", "auto")
    source_origin = payload.get("source_origin", "markdown")
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
            num_threads=_bounded_env_int(
                "MATHWEAVER_PIPELINE_LLM_THREADS", 4, maximum=32
            ),
            enable_analysis=True,
            source_format=source_format,
            source_origin=source_origin,
            checkpoint=_bounded_env_int(
                "MATHWEAVER_PIPELINE_CHECKPOINT_INTERVAL", 10, maximum=1000
            ),
            cache_policy="minimal",
        )
        matrix_flow_runner = MatrixFlowRunner(ctx)

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
                "partial": _partial_result_from_state(
                    stage_state,
                    payload.get("source_markdown") or "",
                ),
            })

        def _on_stage_ready(stage, index, total, stage_state):
            matrix_flow_runner.on_stage_ready(stage, index, total, stage_state)
            if stage.key == "ensure_coverage":
                report = stage_state.get("matrix_flow_report") or {}
                emit({
                    "type": "matrix_flow",
                    "status": report.get("status", "failed"),
                    "flow_count": report.get("flow_count", 0),
                    "warnings": list(report.get("warnings") or []),
                })

        state = execute_fixed_pipeline(
            ctx,
            resume_from_cache=bool(payload.get("resume")),
            edge_output_mode="structured",
            relation_prompt_profile="graph",
            experimental_logic_ir=bool(payload.get("experimental_logic_ir")),
            on_stage_start=_on_stage_start,
            on_stage_ready=_on_stage_ready,
            on_stage_complete=_on_stage_complete,
        )

        nodes = _legacy_display_nodes(
            _normalize_nodes(state.get("node_list", [])),
            payload.get("source_markdown") or "",
        )
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
                "matrix_flow": state.get("matrix_flow_report") or {
                    "status": "not_run",
                    "flow_count": 0,
                    "warnings": [],
                },
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


def _apply_pipeline_event(job_id: str, attempt_token: str | None, event: dict):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return False
        if attempt_token is not None and job.get("_attempt_token") != attempt_token:
            return False
        stage_defs = _job_stage_defs(job)
        event_type = event.get("type")
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
            job["_finished_at"] = time.time()
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
            if job.get("_history_persisted"):
                _upsert_job_history(job, "done")
        elif event_type == "error":
            job["status"] = "error"
            job["_finished_at"] = time.time()
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
            if job.get("_history_persisted"):
                _upsert_job_history(job, "error")
        elif event_type not in {"source_pdf_start", "source_pdf", "stage_start", "stage_complete"}:
            return False
        if (
            event_type in {"source_pdf", "stage_start", "stage_complete"}
            and job.get("_history_persisted")
        ):
            _upsert_job_history(job, "running")
        return event_type in {"done", "error"}


def _monitor_pipeline_process(job_id: str, attempt_token: str, process, event_queue):
    terminal_event = False
    capacity_leases = ()
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
            capacity_leases = runtime.get("capacity_leases") or ()
        paused = bool(runtime and runtime.get("pause_requested"))
        if (
            job
            and job.get("_attempt_token") == attempt_token
            and job.get("status") == "running"
            and not terminal_event
            and not paused
        ):
            job["status"] = "error"
            job["_finished_at"] = time.time()
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
            if job.get("_history_persisted"):
                _upsert_job_history(job, "error")
    try:
        event_queue.close()
    except Exception:
        pass
    _release_capacity_leases(capacity_leases)


def _job_worker_payload(job: dict, *, resume: bool):
    return {
        "job_id": job["job_id"],
        "md_path": job["_md_path"],
        "llm": dict(job["_llm_config"]),
        "source_format": job.get("source_format", "auto"),
        "source_origin": job.get("source_origin", "markdown"),
        "source_markdown": job.get("source_markdown"),
        "filename": job.get("filename"),
        "latex_macros": job.get("latex_macros") or {},
        "source_pdf": job.get("source_pdf"),
        "experimental_logic_ir": bool(job.get("_experimental_logic_ir")),
        "resume": resume,
    }


def _start_pipeline_attempt(job_id: str, *, resume: bool):
    capacity_leases = _acquire_pipeline_capacity_leases()
    process = None
    event_queue = None
    attempt_token = None
    try:
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
                "capacity_leases": capacity_leases,
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
    except Exception:
        with _jobs_lock:
            runtime = _job_runtimes.get(job_id)
            if runtime and runtime.get("attempt_token") == attempt_token:
                _job_runtimes.pop(job_id, None)
        if process is not None:
            try:
                _terminate_pipeline_process(process)
            except Exception:
                pass
        if event_queue is not None:
            try:
                event_queue.close()
            except Exception:
                pass
        _release_capacity_leases(capacity_leases)
        raise


def _run_pipeline(job_id: str, md_path: str, llm: dict, enable_analysis: bool, source_format: str = "auto", source_origin: str = "markdown"):
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
        "source_origin": source_origin,
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
    _prune_terminal_jobs()
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
        source_origin = request.form.get("source_origin", "markdown")
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
        source_origin = body.get("source_origin", "markdown")
        experimental_logic_ir = _parse_bool_flag(
            body.get("experimental_logic_ir", False)
        )

    if not text_content:
        return jsonify({"error": "No content provided"}), 400
    if not all([api_url, model_name, api_key]):
        return jsonify({"error": "Incomplete LLM config (api_url, model_name, api_key required)"}), 400
    embedding_model = (embedding_model or "").strip()
    if not embedding_model:
        return jsonify({"error": "Incomplete embedding config (embedding_model required)"}), 400
    embedding_url = (embedding_url or "").strip() or api_url
    embedding_api_key = (embedding_api_key or "").strip() or api_key

    source_format = "tex" if _looks_like_tex_source(text_content, filename) else "markdown"
    source_origin = str(source_origin or "markdown").strip().lower()
    if source_origin not in {"markdown", "ocr"}:
        return jsonify({"error": "source_origin must be markdown or ocr"}), 400
    latex_macros, latex_macro_warnings = extract_latex_macros(text_content, filename)

    user = _current_user()
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

    job = {
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
        "source_origin": source_origin,
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
        "created_at": datetime.utcnow().isoformat(),
    }
    with _jobs_lock:
        _jobs[job_id] = job

    try:
        _start_pipeline_attempt(job_id, resume=False)
    except PipelineCapacityUnavailable:
        with _jobs_lock:
            _jobs.pop(job_id, None)
        try:
            _remove_job_artifacts(job_id, tmp_dir)
        except (OSError, ValueError):
            pass
        return _pipeline_busy_response()
    except Exception:
        with _jobs_lock:
            _jobs.pop(job_id, None)
        try:
            _remove_job_artifacts(job_id, tmp_dir)
        except (OSError, ValueError):
            pass
        return jsonify({
            "error": "pipeline_start_failed",
            "message": "分析任务暂时无法启动，请稍后重试。",
        }), 503

    if user:
        _upsert_job_history(job, "running", int(user["id"]))

    return jsonify({"job_id": job_id, "status": "running"}), 202


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


def _cancel_job_record(job_id: str, owner_id: int | None, *, artifact_dir=None):
    capacity_leases = ()
    with _jobs_lock:
        job = _jobs.get(job_id)
        runtime = _job_runtimes.get(job_id)
        process = runtime.get("process") if runtime else None
        if process and process.is_alive():
            raise RuntimeError("Pipeline worker is still running")
        selected_artifact_dir = (
            job.get("_artifact_dir") if job else artifact_dir or str(_persistent_job_dir(job_id))
        )
    if owner_id is not None:
        with connect_database() as conn:
            owned = conn.execute(
                "SELECT id FROM history WHERE id = ? AND user_id = ?",
                (job_id, owner_id),
            ).fetchone()
        if owned:
            prepare_graph_delete(job_id)
            _invalidate_api_graph(job_id)
            with connect_database() as conn:
                conn.execute(
                    "DELETE FROM history WHERE id = ? AND user_id = ?",
                    (job_id, owner_id),
                )
            complete_graph_delete(job_id)
    _remove_job_artifacts(job_id, selected_artifact_dir)
    with _jobs_lock:
        runtime = _job_runtimes.pop(job_id, None)
        if runtime:
            capacity_leases = runtime.get("capacity_leases") or ()
        if runtime and runtime.get("queue"):
            try:
                runtime["queue"].close()
            except Exception:
                pass
        _jobs.pop(job_id, None)
    _release_capacity_leases(capacity_leases)


@app.route("/api/v2/agent-import", methods=["POST"])
def agent_import():
    _prune_terminal_jobs()
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

    nodes = _legacy_display_nodes(nodes, source_markdown or "")

    filename = markdown_filename or "导入已有图谱"
    job_id = str(uuid.uuid4())
    json_macros = merge_latex_macros(
        node_payload.get("latex_macros") if isinstance(node_payload, dict) else None,
        edge_payload.get("latex_macros") if isinstance(edge_payload, dict) else None,
    )
    latex_macros = merge_latex_macros(source_macros, json_macros)
    latex_macro_warnings = [*source_macro_warnings]
    course_graph_payload = json.dumps(
        {
            "nodes": nodes,
            "edges": edges,
            "sourceMarkdown": source_markdown or "",
            "latexMacros": latex_macros,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    course_graph_key = f"import:{hashlib.sha256(course_graph_payload.encode('utf-8')).hexdigest()}"
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
        "created_at": datetime.utcnow().isoformat(),
        "_finished_at": time.time(),
    }
    if is_tex:
        threading.Thread(
            target=_compile_agent_source_pdf,
            args=(job_id, source_markdown, filename),
            daemon=True,
            name=f"tex-pdf-{job_id[:8]}",
        ).start()
    return jsonify({
        "job_id": job_id,
        "courseGraphKey": course_graph_key,
        "filename": filename,
        "result": result,
        "has_markdown": bool(source_markdown),
        "warnings": [*warnings, *latex_macro_warnings],
    }), 201


@app.route("/api/v2/jobs/<job_id>/status")
def job_status(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            data = {
                k: v
                for k, v in job.items()
                if not k.startswith("_")
                and k not in (
                    "result",
                    "source_markdown",
                    "source_pdf",
                    "error",
                    "error_detail",
                    "error_user_message",
                )
            }
            data.update(_job_error_presentation(job))
            data["source_pdf"] = _public_source_pdf_meta(job.get("source_pdf"))
        else:
            data = None
    if data is None:
        user = _current_user()
        row = (
            _get_db().execute(
                """SELECT id, filename, status, stage, stage_label, stage_index,
                          total_stages, stages_done_json, source_pdf_json
                     FROM history WHERE id = ? AND user_id = ?""",
                (job_id, user["id"]),
            ).fetchone()
            if user
            else None
        )
        if not row:
            return jsonify({"error": "Not found"}), 404
        status = row["status"] or "done"
        data = {
            "job_id": row["id"],
            "status": status,
            "filename": row["filename"],
            "stage": row["stage"],
            "stage_label": row["stage_label"],
            "stage_index": int(row["stage_index"] or 0),
            "total_stages": int(row["total_stages"] or 0),
            "stages_done": json.loads(row["stages_done_json"] or "[]"),
            "source": "pipeline",
            "source_pdf": _public_source_pdf_meta(_read_source_pdf_meta(row)),
        }
        if status == "error":
            data.update({
                "error_code": "internal",
                "error_title": "分析任务未能完成",
                "error": "任务进度已安全保存，可以从历史记录中恢复。",
            })
    return jsonify(data)


@app.route("/api/v2/jobs/<job_id>/error-detail")
def job_error_detail(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify({"error": "Not found"}), 404
        if not _job_access_allowed(job):
            return jsonify({"error": "forbidden"}), 403
        if job.get("status") != "error":
            return jsonify({"error": "Error detail is only available for failed jobs"}), 409
        llm_config = job.get("_llm_config") if isinstance(job.get("_llm_config"), dict) else {}
        secrets_to_redact = (
            llm_config.get("api_key"),
            llm_config.get("embedding_api_key"),
        )
        message = _redact_error_text(job.get("error"), secrets_to_redact)
        detail = _redact_error_text(job.get("error_detail"), secrets_to_redact)
        if not message and not detail:
            return jsonify({"error": "No error detail is available"}), 409
    return jsonify({"message": message, "detail": detail})


@app.route("/api/v2/jobs/<job_id>/pause", methods=["POST"])
def pause_job(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify({"error": "Not found"}), 404
        if not _job_access_allowed(job):
            return jsonify({"error": "forbidden"}), 403
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
            if job.get("_user_id") is not None and not _upsert_job_history(job, "paused"):
                return jsonify({
                    "error": "Task paused but history persistence failed",
                    "status": "paused",
                }), 500
    return jsonify({"ok": True, "status": "paused"})


@app.route("/api/v2/jobs/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify({"error": "Not found"}), 404
        if not _job_access_allowed(job):
            return jsonify({"error": "forbidden"}), 403
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
    except (OSError, ValueError) as exc:
        return jsonify({"error": f"Unable to cancel task: {exc}"}), 500
    return jsonify({"ok": True, "status": "cancelled", "job_id": job_id})


@app.route("/api/v2/jobs/<job_id>/resume", methods=["POST"])
def resume_job(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify({"error": "Not found"}), 404
        if not _job_access_allowed(job):
            return jsonify({"error": "forbidden"}), 403
    error = _begin_pipeline_resume(job_id)
    if error:
        message, status_code = error
        if status_code == 429:
            return _pipeline_busy_response()
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
        previous_status = job.get("status")
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
        if job.get("_history_persisted"):
            _upsert_job_history(job, "running")
    try:
        _start_pipeline_attempt(job_id, resume=True)
    except PipelineCapacityUnavailable:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job:
                job["status"] = previous_status
                if job.get("_history_persisted"):
                    _upsert_job_history(job, str(previous_status))
        return "Pipeline capacity is busy", 429
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
                if job.get("_history_persisted"):
                    _upsert_job_history(job, "error")
        return f"Unable to restart pipeline worker: {exc}", 500
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
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            if job["status"] != "done":
                return jsonify({"error": "Job not complete"}), 400
            result = copy.deepcopy(job["result"])
            source_markdown = job.get("source_markdown") or ""
        else:
            result = None
            source_markdown = ""
    if result is None:
        user = _current_user()
        row = (
            _get_db().execute(
                "SELECT * FROM history WHERE id = ? AND user_id = ?",
                (job_id, user["id"]),
            ).fetchone()
            if user
            else None
        )
        if not row:
            return jsonify({"error": "Not found"}), 404
        if (row["status"] or "done") != "done":
            return jsonify({"error": "Job not complete"}), 400
        source_markdown = row["source_markdown"] or ""
        graph = _load_api_graph(job_id)
        result = {
            "nodes": copy.deepcopy(graph["nodes"]),
            "edges": copy.deepcopy(graph["edges"]),
            "latex_macros": json.loads(row["latex_macros"] or "{}"),
            "source_pdf": _public_source_pdf_meta(_read_source_pdf_meta(row)),
        }
    return jsonify(_project_display_result(result, source_markdown))


def _source_pdf_context(job_id: str) -> tuple[dict | None, list[dict]]:
    job = _jobs.get(job_id)
    if job:
        result = job.get("result") or {}
        return job.get("source_pdf"), result.get("nodes") or []
    row = _get_db().execute(
        "SELECT id, source_pdf_json FROM history WHERE id = ?", (job_id,)
    ).fetchone()
    if not row:
        return None, []
    graph = _load_api_graph(job_id)
    return _read_source_pdf_meta(row), graph["nodes"]


@app.route("/api/v2/source-pdf/<job_id>")
def source_pdf(job_id):
    meta, _nodes = _source_pdf_context(job_id)
    if not meta:
        return jsonify({"error": "source PDF not found"}), 404
    if meta.get("status") == "compiling":
        return jsonify({"error": "source PDF is compiling", "status": "compiling"}), 409
    pdf_path = Path(str(meta.get("pdf_path") or ""))
    if not meta.get("available") or not pdf_path.exists():
        return jsonify({"error": meta.get("error") or "source PDF unavailable"}), 404
    return send_file(
        pdf_path,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"{job_id}.pdf",
    )


@app.route("/api/v2/source-pdf/<job_id>/compile-log")
def source_pdf_compile_log(job_id):
    meta, _nodes = _source_pdf_context(job_id)
    if not meta:
        return jsonify({"error": "source PDF not found"}), 404
    if meta.get("status") == "compiling":
        return jsonify({"error": "source PDF is compiling", "status": "compiling"}), 409
    log_path = Path(str(meta.get("log_path") or ""))
    if not log_path.exists():
        return jsonify({"error": "compile log not found"}), 404
    return send_file(log_path, mimetype="text/plain; charset=utf-8", as_attachment=False)


@app.route("/api/v2/source-pdf/<job_id>/locate")
def source_pdf_locate(job_id):
    raw_node_id = request.args.get("node_id", "")
    try:
        node_id = int(raw_node_id)
    except Exception:
        return jsonify({"error": "node_id must be an integer"}), 400
    meta, nodes = _source_pdf_context(job_id)
    if not meta or not meta.get("available"):
        if meta and meta.get("status") == "compiling":
            return jsonify({"error": "source PDF is compiling", "status": "compiling"}), 409
        return jsonify({"error": (meta or {}).get("error") or "source PDF unavailable"}), 404
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
    job = _jobs.get(job_id)
    if not job or job["status"] != "done":
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
    fallback_payload = request.get_json(silent=True)
    if not isinstance(fallback_payload, dict):
        fallback_payload = {}
    fallback_nodes = fallback_payload.get("nodes")
    fallback_edges = fallback_payload.get("edges")
    fallback_available = isinstance(fallback_nodes, list) and isinstance(fallback_edges, list)

    job = _jobs.get(job_id)
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

    artifact_dir = job.get("_artifact_dir")
    if job.get("source") != "pipeline":
        return jsonify({
            "error": "Complete processing cache is unavailable for this job",
        }), 409

    artifact_root = Path(artifact_dir) if artifact_dir else None
    nodes_path = artifact_root / "nodes.json" if artifact_root else None
    edges_path = artifact_root / "edges.json" if artifact_root else None
    cache_path = artifact_root / "_stage_cache" if artifact_root else None
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
        elif fallback_available:
            nodes_bytes = _export_json_bytes(fallback_nodes)
            edges_bytes = _export_json_bytes(fallback_edges)
        else:
            return jsonify({
                "error": "Processing cache is missing and node/edge results are unavailable",
            }), 409

    return _export_artifact_zip(
        filename=job.get("filename") or fallback_payload.get("filename") or "processing_result",
        nodes_bytes=nodes_bytes,
        edges_bytes=edges_bytes,
        degraded=True,
    )


def _export_json_bytes(items):
    return json.dumps(items, ensure_ascii=False, indent=2).encode("utf-8")


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


@app.route("/health/live")
def health_live():
    return jsonify({"ok": True, "service": "mathweaver-api"})


@app.route("/health/ready")
def health_ready():
    mysql = database_health()
    try:
        neo4j = get_graph_store().health()
    except Exception as exc:
        app.logger.error("Neo4j readiness check failed: %s", exc)
        neo4j = {"ok": False}
    ready = bool(mysql.get("ok") and neo4j.get("ok"))
    payload = {
        "ok": ready,
        "mysql": {
            "ok": bool(mysql.get("ok")),
            "revision": mysql.get("revision"),
            "expected": mysql.get("expected"),
        },
        "neo4j": {
            "ok": bool(neo4j.get("ok")),
            "database": neo4j.get("database"),
            "constraints": neo4j.get("constraints") or [],
        },
    }
    return jsonify(payload), 200 if ready else 503


@app.route("/api/v2/ping")
def ping():
    return jsonify({"ok": True})


if __name__ == "__main__":
    reconcile_interrupted_history()
    print("MathGraph API v2  →  http://0.0.0.0:5001")
    app.run(host="0.0.0.0", port=5001, debug=False, threaded=True)
