"""Web 学习数据的 SQLAlchemy 持久化边界。"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from storage.credential_crypto import CredentialCipher, CredentialConfigurationError
from storage.database import session_scope
from storage.models import History, ProofWorkspace, StorageOutbox, UserSettings, utc_now
from storage.object_storage import StoredVersion


SessionFactory = Callable[[], AbstractContextManager[Session]]


@dataclass(frozen=True)
class JobSnapshot:
    """任务生命周期可持久化字段；运行密钥与内部绝对路径不属于该边界。"""

    job_id: str
    filename: str
    status: str
    nodes: list[dict]
    edges: list[dict]
    source_markdown: str | None
    latex_macros: dict
    source_pdf: dict | None
    stage: str | None
    stage_label: str | None
    stage_index: int
    total_stages: int
    stages_done: list[str]
    source_format: str
    experimental_logic_ir: bool
    created_at: datetime
    object_storage_prefix: str | None = None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _safe_basename(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    # 同时按 Windows 与 POSIX 规则取名，防止不同部署系统对反斜杠解释不一致。
    name = Path(PureWindowsPath(raw).name).name
    return name if name not in {"", ".", ".."} else None


def sanitize_source_pdf_meta(meta: dict | None) -> dict | None:
    """仅保留公开状态、URL、错误和安全文件名，绝不持久化绝对路径。"""
    if not isinstance(meta, dict):
        return None
    status = meta.get("status")
    if status not in {"compiling", "ready", "failed"}:
        status = "ready" if meta.get("available") else ("failed" if meta.get("error") else "compiling")
    safe: dict[str, Any] = {
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
        name = _safe_basename(meta.get(path_key) or meta.get(name_key))
        if name:
            safe[name_key] = name
    return safe


class LearningRepository:
    """按显式用户身份读写设置、任务历史和证明工作区。"""

    def __init__(
        self,
        session_factory: SessionFactory = session_scope,
        *,
        cipher: CredentialCipher | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    def _credential_cipher(self) -> CredentialCipher:
        if self._cipher is None:
            raise CredentialConfigurationError("credential cipher is required")
        return self._cipher

    @staticmethod
    def _settings_aad(user_id: int) -> str:
        return f"user:{int(user_id)}:llm-settings:v1"

    def _decrypt_settings_secrets(self, row: UserSettings) -> dict[str, dict[str, str]]:
        if row.llm_secrets_encrypted_json is None:
            # Web 模式不兼容读取历史明文；必须先运行显式数据迁移。
            raise CredentialConfigurationError("stored credentials require migration")
        value = self._credential_cipher().decrypt_json(
            row.llm_secrets_encrypted_json,
            aad=self._settings_aad(row.user_id),
        )
        if any(
            not isinstance(config_id, str) or not isinstance(secret, dict)
            for config_id, secret in value.items()
        ):
            raise CredentialConfigurationError("stored credential payload is invalid")
        return value

    @staticmethod
    def _public_settings(
        descriptors: list[dict],
        secrets: dict[str, dict[str, str]],
        active_index: int,
    ) -> dict:
        configs: list[dict] = []
        for descriptor in descriptors:
            public = deepcopy(descriptor)
            config_secrets = secrets.get(str(public.get("config_id") or ""), {})
            has_api_key = bool(config_secrets.get("api_key"))
            has_embedding_key = bool(config_secrets.get("embedding_api_key"))
            public.update(
                {
                    "has_api_key": has_api_key,
                    "api_key_masked": "********" if has_api_key else "",
                    "has_embedding_api_key": has_embedding_key,
                    "embedding_api_key_masked": "********" if has_embedding_key else "",
                }
            )
            configs.append(public)
        return {"configs": configs, "active_index": active_index}

    def get_public_settings(self, user_id: int) -> dict:
        with self._session_factory() as session:
            row = session.get(UserSettings, user_id)
            if row is None:
                return {"configs": [], "active_index": 0}
            data = deepcopy(row.llm_configs_json) if isinstance(row.llm_configs_json, dict) else {}
            configs = data.get("configs") if isinstance(data.get("configs"), list) else []
            active_index = data.get("active_index")
            if not isinstance(active_index, int) or isinstance(active_index, bool):
                active_index = 0
            return self._public_settings(
                configs,
                self._decrypt_settings_secrets(row),
                active_index,
            )

    def get_runtime_settings(self, user_id: int) -> dict:
        with self._session_factory() as session:
            row = session.get(UserSettings, user_id)
            if row is None:
                return {"configs": [], "active_index": 0}
            data = deepcopy(row.llm_configs_json) if isinstance(row.llm_configs_json, dict) else {}
            descriptors = data.get("configs") if isinstance(data.get("configs"), list) else []
            active_index = data.get("active_index")
            if not isinstance(active_index, int) or isinstance(active_index, bool):
                active_index = 0
            secrets = self._decrypt_settings_secrets(row)
            configs: list[dict] = []
            for descriptor in descriptors:
                runtime = deepcopy(descriptor)
                config_secrets = secrets.get(str(runtime.get("config_id") or ""), {})
                runtime["api_key"] = str(config_secrets.get("api_key") or "")
                runtime["embedding_api_key"] = str(
                    config_secrets.get("embedding_api_key") or ""
                )
                configs.append(runtime)
            return {"configs": configs, "active_index": active_index}

    def upsert_settings(self, user_id: int, configs: list[dict], active_index: int) -> dict:
        if any(not isinstance(config, dict) for config in configs):
            # 仓储也拒绝错误类型，防止绕过 HTTP 校验的内部调用产生半写入或 500。
            raise ValueError("config must be a JSON object")
        if (
            not isinstance(active_index, int)
            or isinstance(active_index, bool)
            or (configs and not 0 <= active_index < len(configs))
            or (not configs and active_index != 0)
        ):
            raise ValueError("active_index is out of range")
        with self._session_factory() as session:
            row = session.get(UserSettings, user_id)
            if row is None:
                row = UserSettings(user_id=user_id)
                session.add(row)
                existing_descriptors: list[dict] = []
                existing_secrets: dict[str, dict[str, str]] = {}
            else:
                data = row.llm_configs_json if isinstance(row.llm_configs_json, dict) else {}
                existing_descriptors = (
                    deepcopy(data.get("configs"))
                    if isinstance(data.get("configs"), list)
                    else []
                )
                existing_secrets = self._decrypt_settings_secrets(row)
            owned_ids = {
                str(config.get("config_id"))
                for config in existing_descriptors
                if isinstance(config, dict) and config.get("config_id")
            }
            normalized: list[dict] = []
            next_secrets: dict[str, dict[str, str]] = {}
            seen_ids: set[str] = set()
            for raw_config in configs:
                descriptor = deepcopy(raw_config)
                supplied_id = str(descriptor.get("config_id") or "").strip()
                if supplied_id:
                    if supplied_id not in owned_ids:
                        raise ValueError("config_id is not owned by this user")
                    config_id = supplied_id
                else:
                    config_id = uuid.uuid4().hex
                if config_id in seen_ids:
                    raise ValueError("config_id must be unique")
                seen_ids.add(config_id)

                previous = deepcopy(existing_secrets.get(config_id, {}))
                api_key = str(descriptor.pop("api_key", "") or "").strip()
                embedding_key = str(
                    descriptor.pop("embedding_api_key", "") or ""
                ).strip()
                if api_key:
                    previous["api_key"] = api_key
                elif descriptor.pop("clear_api_key", False) is True:
                    previous.pop("api_key", None)
                if embedding_key:
                    previous["embedding_api_key"] = embedding_key
                elif descriptor.pop("clear_embedding_api_key", False) is True:
                    previous.pop("embedding_api_key", None)
                if not supplied_id and not previous.get("api_key"):
                    raise ValueError("api_key is required for a new config")
                if previous:
                    next_secrets[config_id] = previous

                for public_only in (
                    "has_api_key",
                    "api_key_masked",
                    "has_embedding_api_key",
                    "embedding_api_key_masked",
                ):
                    descriptor.pop(public_only, None)
                descriptor["config_id"] = config_id
                normalized.append(descriptor)

            active = normalized[active_index] if normalized else {}
            row.llm_api_url = str(active.get("api_url") or "")
            row.llm_model = str(active.get("model_name") or "")
            row.llm_api_key = ""
            row.llm_configs_json = {"configs": normalized, "active_index": active_index}
            row.llm_secrets_encrypted_json = self._credential_cipher().encrypt_json(
                next_secrets,
                aad=self._settings_aad(user_id),
            )
            return self._public_settings(normalized, next_secrets, active_index)

    def list_history(self, user_id: int, limit: int = 50) -> list[dict]:
        safe_limit = min(max(int(limit), 1), 200)
        with self._session_factory() as session:
            # 列表只取摘要列，避免每次打开历史页加载节点、边和正文大 JSON。
            rows = session.execute(
                select(
                    History.id,
                    History.filename,
                    History.node_count,
                    History.edge_count,
                    History.status,
                    History.stage,
                    History.stage_label,
                    History.stage_index,
                    History.total_stages,
                    History.stages_done_json,
                    History.experimental_logic_ir,
                    History.updated_at,
                    History.created_at,
                )
                .where(History.user_id == user_id, History.deleted_at.is_(None))
                .order_by(History.created_at.desc(), History.id.desc())
                .limit(safe_limit)
            ).all()
            return [
                {
                    **dict(row._mapping),
                    "stages_done": list(row.stages_done_json or []),
                    "updated_at": _iso(row.updated_at),
                    "created_at": _iso(row.created_at),
                }
                for row in rows
            ]

    def get_owned_history(self, user_id: int, history_id: str) -> dict | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(History).where(
                    History.id == history_id,
                    History.user_id == user_id,
                    History.deleted_at.is_(None),
                )
            )
            return self._history_dict(row) if row is not None else None

    @staticmethod
    def _apply_snapshot(row: History, snapshot: JobSnapshot) -> None:
        row.filename = snapshot.filename
        row.node_count = len(snapshot.nodes)
        row.edge_count = len(snapshot.edges)
        row.nodes_json = deepcopy(snapshot.nodes)
        row.edges_json = deepcopy(snapshot.edges)
        row.source_markdown = snapshot.source_markdown
        row.latex_macros = json.dumps(snapshot.latex_macros or {}, ensure_ascii=False)
        row.source_pdf_json = sanitize_source_pdf_meta(snapshot.source_pdf)
        row.status = snapshot.status
        row.stage = snapshot.stage
        row.stage_label = snapshot.stage_label
        row.stage_index = int(snapshot.stage_index)
        row.total_stages = int(snapshot.total_stages)
        row.stages_done_json = list(snapshot.stages_done)
        row.source_format = snapshot.source_format
        row.experimental_logic_ir = bool(snapshot.experimental_logic_ir)
        row.created_at = snapshot.created_at
        row.updated_at = utc_now()

    def upsert_job_progress(self, user_id: int, snapshot: JobSnapshot) -> bool:
        try:
            with self._session_factory() as session:
                row = session.get(History, snapshot.job_id)
                if row is not None and (
                    int(row.user_id) != int(user_id) or row.deleted_at is not None
                ):
                    # 主键全局唯一，已有记录的归属不能被新请求覆盖或转移。
                    return False
                if row is None:
                    row = History(id=snapshot.job_id, user_id=user_id, filename=snapshot.filename)
                    session.add(row)
                self._apply_snapshot(row, snapshot)
                row.object_storage_prefix = snapshot.object_storage_prefix
            return True
        except IntegrityError:
            # 并发插入同一任务 ID 时由数据库唯一键兜底，调用方得到稳定冲突结果。
            return False

    def commit_storage_version(
        self, user_id: int, snapshot: JobSnapshot, stored: StoredVersion
    ) -> bool:
        """任务快照、版本指针和旧版本清理事件必须在同一数据库事务提交。"""
        try:
            with self._session_factory() as session:
                row = session.get(History, snapshot.job_id)
                if row is not None and (
                    int(row.user_id) != int(user_id) or row.deleted_at is not None
                ):
                    return False
                if row is None:
                    row = History(id=snapshot.job_id, user_id=user_id, filename=snapshot.filename)
                    session.add(row)
                old_version = row.storage_version
                self._apply_snapshot(row, snapshot)
                row.object_storage_prefix = stored.prefix
                row.storage_version = stored.version_id
                row.storage_status = "ready"
                row.storage_checksum = stored.manifest_checksum
                row.storage_file_count = int(stored.file_count)
                row.storage_bytes = int(stored.total_bytes)
                if old_version and old_version != stored.version_id:
                    session.add(
                        StorageOutbox(
                            user_id=user_id,
                            history_id=snapshot.job_id,
                            version_id=old_version,
                            operation="delete_version",
                            idempotency_key=(
                                f"delete-version:{user_id}:{snapshot.job_id}:{old_version}"
                            ),
                            payload_json={"version_id": old_version},
                            next_attempt_at=utc_now(),
                        )
                    )
            return True
        except IntegrityError:
            return False

    def soft_delete_history(self, user_id: int, history_id: str) -> bool:
        with self._session_factory() as session:
            row = session.scalar(
                select(History).where(
                    History.id == history_id,
                    History.user_id == user_id,
                    History.deleted_at.is_(None),
                )
            )
            if row is None:
                return False
            row.deleted_at = utc_now()
            row.storage_status = "delete_pending"
            session.add(
                StorageOutbox(
                    user_id=user_id,
                    history_id=history_id,
                    version_id=row.storage_version,
                    operation="delete_job_versions",
                    idempotency_key=f"delete-job-versions:{user_id}:{history_id}",
                    payload_json={},
                    next_attempt_at=utc_now(),
                )
            )
            return True

    def update_source_pdf(self, user_id: int, history_id: str, safe_meta: dict) -> bool:
        with self._session_factory() as session:
            row = session.scalar(
                select(History).where(History.id == history_id, History.user_id == user_id)
            )
            if row is None:
                return False
            row.source_pdf_json = sanitize_source_pdf_meta(safe_meta)
            row.updated_at = utc_now()
            return True

    def delete_owned_history(self, user_id: int, history_id: str) -> bool:
        return self.soft_delete_history(user_id, history_id)

    def list_proof_workspaces(self, user_id: int, graph_id: str) -> list[dict]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ProofWorkspace)
                .where(ProofWorkspace.user_id == user_id, ProofWorkspace.graph_id == graph_id)
                .order_by(ProofWorkspace.node_id.asc())
            ).all()
            return [self._workspace_dict(row) for row in rows]

    def upsert_proof_workspace(
        self, user_id: int, graph_id: str, node_id: int, payload: dict
    ) -> dict:
        with self._session_factory() as session:
            row = session.get(ProofWorkspace, (user_id, graph_id, node_id))
            if row is None:
                row = ProofWorkspace(user_id=user_id, graph_id=graph_id, node_id=node_id)
                session.add(row)
            row.user_proof = str(payload.get("userProof") or "")
            row.versions_json = deepcopy(payload.get("versions") or [])
            row.ai_messages_json = deepcopy(payload.get("aiMessages") or [])
            row.imports_json = deepcopy(payload.get("imports") or [])
            row.updated_at = utc_now()
            session.flush()
            return self._workspace_dict(row)

    @staticmethod
    def _history_dict(row: History) -> dict:
        try:
            latex_macros = json.loads(row.latex_macros or "{}")
        except (TypeError, json.JSONDecodeError):
            latex_macros = {}
        return {
            "id": row.id,
            "user_id": row.user_id,
            "filename": row.filename,
            "node_count": row.node_count,
            "edge_count": row.edge_count,
            "nodes": deepcopy(row.nodes_json or []),
            "edges": deepcopy(row.edges_json or []),
            "source_markdown": row.source_markdown,
            "latex_macros": latex_macros if isinstance(latex_macros, dict) else {},
            "source_pdf": deepcopy(row.source_pdf_json),
            "status": row.status,
            "stage": row.stage,
            "stage_label": row.stage_label,
            "stage_index": row.stage_index,
            "total_stages": row.total_stages,
            "stages_done": list(row.stages_done_json or []),
            "source_format": row.source_format,
            "experimental_logic_ir": bool(row.experimental_logic_ir),
            "object_storage_prefix": row.object_storage_prefix,
            "storage_version": row.storage_version,
            "storage_status": row.storage_status,
            "storage_checksum": row.storage_checksum,
            "storage_file_count": row.storage_file_count,
            "storage_bytes": row.storage_bytes,
            "updated_at": _iso(row.updated_at),
            "created_at": _iso(row.created_at),
        }

    @staticmethod
    def _workspace_dict(row: ProofWorkspace) -> dict:
        return {
            "nodeId": row.node_id,
            "userProof": row.user_proof or "",
            "versions": deepcopy(row.versions_json or []),
            "aiMessages": deepcopy(row.ai_messages_json or []),
            "imports": deepcopy(row.imports_json or []),
            "updatedAt": _iso(row.updated_at),
        }
