"""Web 设置、图谱历史和证明工作区的 SQLAlchemy 持久化边界。"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from storage.database import session_scope
from storage.models import History, ProofWorkspace, UserSettings, utc_now


SessionFactory = Callable[[], AbstractContextManager[Session]]


@dataclass(frozen=True)
class JobSnapshot:
    """任务可持久化字段；运行密钥和服务器绝对路径不属于该边界。"""

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
    source_origin: str
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
    # 同时按 Windows 与 POSIX 规则取名，避免部署系统差异留下绝对路径。
    name = Path(PureWindowsPath(raw).name).name
    return name if name not in {"", ".", ".."} else None


def sanitize_source_pdf_meta(meta: dict | None) -> dict | None:
    """仅持久化公开状态、URL、错误和安全文件名。"""
    if not isinstance(meta, dict):
        return None
    status = meta.get("status")
    if status not in {"compiling", "ready", "failed"}:
        status = (
            "ready"
            if meta.get("available")
            else "failed"
            if meta.get("error")
            else "compiling"
        )
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
    """所有读写都要求显式用户归属，跨用户主键不会转移。"""

    def __init__(self, session_factory: SessionFactory = session_scope) -> None:
        self._session_factory = session_factory

    def get_settings(self, user_id: int) -> dict:
        with self._session_factory() as session:
            row = session.get(UserSettings, user_id)
            if row is None:
                return {"configs": [], "active_index": 0}
            data = (
                deepcopy(row.llm_configs_json)
                if isinstance(row.llm_configs_json, dict)
                else {}
            )
            configs = data.get("configs") if isinstance(data.get("configs"), list) else []
            active_index = data.get("active_index")
            if not isinstance(active_index, int) or isinstance(active_index, bool):
                active_index = 0
            if not configs and row.llm_api_url:
                configs = [
                    {
                        "name": "默认配置",
                        "api_url": row.llm_api_url,
                        "model_name": row.llm_model,
                        "api_key": row.llm_api_key,
                    }
                ]
                active_index = 0
            return {"configs": configs, "active_index": active_index}

    def get_active_llm_config(self, user_id: int) -> dict | None:
        settings = self.get_settings(user_id)
        configs = settings["configs"]
        index = settings["active_index"]
        active = configs[index] if configs and 0 <= index < len(configs) else {}
        values = {
            "api_url": str(active.get("api_url") or "").strip(),
            "model_name": str(active.get("model_name") or "").strip(),
            "api_key": str(active.get("api_key") or "").strip(),
        }
        return values if all(values.values()) else None

    def upsert_settings(self, user_id: int, configs: list[dict], active_index: int) -> None:
        if any(not isinstance(config, dict) for config in configs):
            raise ValueError("config must be a JSON object")
        normalized = deepcopy(configs)
        active = (
            normalized[active_index]
            if normalized and 0 <= active_index < len(normalized)
            else {}
        )
        with self._session_factory() as session:
            row = session.get(UserSettings, user_id)
            if row is None:
                row = UserSettings(user_id=user_id)
                session.add(row)
            row.llm_api_url = str(active.get("api_url") or "")
            row.llm_model = str(active.get("model_name") or "")
            row.llm_api_key = str(active.get("api_key") or "")
            row.llm_configs_json = {
                "configs": normalized,
                "active_index": active_index,
            }

    def list_history(self, user_id: int, limit: int = 50) -> list[dict]:
        safe_limit = min(max(int(limit), 1), 200)
        with self._session_factory() as session:
            rows = session.scalars(
                select(History)
                .where(History.user_id == user_id)
                .order_by(History.created_at.desc(), History.id.desc())
                .limit(safe_limit)
            ).all()
            return [self._history_dict(row) for row in rows]

    def get_owned_history(self, user_id: int, history_id: str) -> dict | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(History).where(
                    History.id == history_id,
                    History.user_id == user_id,
                )
            )
            return self._history_dict(row) if row is not None else None

    def upsert_job_progress(self, user_id: int, snapshot: JobSnapshot) -> bool:
        try:
            with self._session_factory() as session:
                row = session.get(History, snapshot.job_id)
                if row is not None and int(row.user_id) != int(user_id):
                    return False
                if row is None:
                    row = History(
                        id=snapshot.job_id,
                        user_id=user_id,
                        filename=snapshot.filename,
                    )
                    session.add(row)
                row.filename = snapshot.filename
                row.node_count = len(snapshot.nodes)
                row.edge_count = len(snapshot.edges)
                row.nodes_json = deepcopy(snapshot.nodes)
                row.edges_json = deepcopy(snapshot.edges)
                row.source_markdown = snapshot.source_markdown
                row.latex_macros = json.dumps(
                    snapshot.latex_macros or {}, ensure_ascii=False
                )
                row.source_pdf_json = sanitize_source_pdf_meta(snapshot.source_pdf)
                row.status = snapshot.status
                row.stage = snapshot.stage
                row.stage_label = snapshot.stage_label
                row.stage_index = int(snapshot.stage_index)
                row.total_stages = int(snapshot.total_stages)
                row.stages_done_json = list(snapshot.stages_done)
                row.source_format = snapshot.source_format
                row.source_origin = snapshot.source_origin
                row.experimental_logic_ir = bool(snapshot.experimental_logic_ir)
                row.object_storage_prefix = snapshot.object_storage_prefix
                row.created_at = snapshot.created_at
                row.updated_at = utc_now()
            return True
        except IntegrityError:
            # 并发创建相同任务 ID 时，由数据库唯一键转换成稳定冲突结果。
            return False

    def update_source_pdf(
        self,
        history_id: str,
        safe_meta: dict,
        user_id: int | None = None,
    ) -> bool:
        with self._session_factory() as session:
            filters = [History.id == history_id]
            if user_id is not None:
                filters.append(History.user_id == user_id)
            row = session.scalar(select(History).where(*filters))
            if row is None:
                return False
            row.source_pdf_json = sanitize_source_pdf_meta(safe_meta)
            row.updated_at = utc_now()
            return True

    def delete_owned_history(self, user_id: int, history_id: str) -> bool:
        with self._session_factory() as session:
            result = session.execute(
                delete(History).where(
                    History.id == history_id,
                    History.user_id == user_id,
                )
            )
            return bool(result.rowcount)

    def list_proof_workspaces(self, user_id: int, graph_id: str) -> list[dict]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ProofWorkspace)
                .where(
                    ProofWorkspace.user_id == user_id,
                    ProofWorkspace.graph_id == graph_id,
                )
                .order_by(ProofWorkspace.node_id.asc())
            ).all()
            return [self._workspace_dict(row) for row in rows]

    def upsert_proof_workspace(
        self, user_id: int, graph_id: str, node_id: int, payload: dict
    ) -> dict:
        with self._session_factory() as session:
            row = session.get(ProofWorkspace, (user_id, graph_id, node_id))
            if row is None:
                row = ProofWorkspace(
                    user_id=user_id,
                    graph_id=graph_id,
                    node_id=node_id,
                )
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
            "source_origin": row.source_origin,
            "experimental_logic_ir": bool(row.experimental_logic_ir),
            "object_storage_prefix": row.object_storage_prefix,
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
