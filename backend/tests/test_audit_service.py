import pytest
from sqlalchemy import select

from storage.audit_service import AuditWriter
from storage.database import session_scope
from storage.models import AuditLog, User


def _user_id() -> int:
    with session_scope() as session:
        user = User.create_account("student", "S-AUDIT", None, "Audit", "hash")
        session.add(user)
        session.flush()
        return user.id


def test_audit_rejects_unknown_or_secret_detail_fields():
    user_id = _user_id()
    with pytest.raises(ValueError, match="audit detail field is not allowed"):
        with session_scope() as session:
            AuditWriter().add(
                session,
                actor_id=user_id,
                action="settings.update",
                subject_type="user",
                subject_id=str(user_id),
                details={"api_key": "sk-secret"},
            )


def test_audit_writer_persists_only_allowlisted_details():
    user_id = _user_id()
    with session_scope() as session:
        AuditWriter().add(
            session,
            actor_id=user_id,
            action="settings.update",
            subject_type="user",
            subject_id=str(user_id),
            details={"config_count": 2},
        )

    with session_scope() as session:
        row = session.scalar(select(AuditLog))
        assert row.action == "settings.update"
        assert row.details == {"config_count": 2}
