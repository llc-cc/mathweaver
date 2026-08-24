"""固定事件结构的安全审计写入，禁止任意请求详情进入审计表。"""

from __future__ import annotations

from storage.database import session_scope
from storage.models import AuditLog
from storage.redaction import redact_sensitive


_ACTION_FIELDS = {
    "auth.login": {"result", "reason"},
    "password.change": {"sessions_revoked"},
    "admin.password_reset": {"initial_password_pending"},
    "admin.user_status": {"is_active"},
    "admin.user_import": {"created", "updated", "failed"},
    "settings.update": {"config_count"},
    "history.delete": {"storage_status"},
    "reconciliation.repair": {"operation", "count"},
}
_SUBJECT_TYPES = {"user", "history", "storage", "import"}


class AuditWriter:
    def add(
        self,
        session,
        *,
        actor_id: int | None,
        action: str,
        subject_type: str,
        subject_id: str,
        details: dict,
    ) -> None:
        allowed = _ACTION_FIELDS.get(action)
        if allowed is None:
            raise ValueError("audit action is not allowed")
        if subject_type not in _SUBJECT_TYPES:
            raise ValueError("audit subject type is not allowed")
        cleaned = redact_sensitive(details)
        if not isinstance(cleaned, dict) or set(cleaned) - allowed:
            raise ValueError("audit detail field is not allowed")
        session.add(
            AuditLog(
                actor_id=actor_id,
                action=action,
                subject_type=subject_type,
                subject_id=str(subject_id)[:128],
                details=cleaned,
            )
        )


class AuditService:
    def __init__(self, session_factory=session_scope, writer: AuditWriter | None = None):
        self._session_factory = session_factory
        self._writer = writer or AuditWriter()

    def record(self, **event) -> None:
        with self._session_factory() as session:
            self._writer.add(session, **event)
