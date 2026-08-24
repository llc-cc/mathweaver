"""Add immutable storage state and retryable cleanup outbox.

Revision ID: 20260824_04
Revises: 20260824_03
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260824_04"
down_revision: str | None = "20260824_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 历史数据先标记为 legacy，只有经过 manifest 校验的新版本才进入 ready 状态。
    op.add_column("history", sa.Column("storage_version", sa.String(32), nullable=True))
    op.add_column(
        "history",
        sa.Column("storage_status", sa.String(32), nullable=False, server_default="legacy"),
    )
    op.add_column("history", sa.Column("storage_checksum", sa.String(64), nullable=True))
    op.add_column(
        "history", sa.Column("storage_file_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "history", sa.Column("storage_bytes", sa.BigInteger(), nullable=False, server_default="0")
    )
    op.add_column("history", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_history_storage_file_count_nonnegative", "history", "storage_file_count >= 0"
    )
    op.create_check_constraint(
        "ck_history_storage_bytes_nonnegative", "history", "storage_bytes >= 0"
    )

    op.create_table(
        "storage_outbox",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("history_id", sa.String(64), nullable=False),
        sa.Column("version_id", sa.String(32), nullable=True),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempts >= 0", name="ck_storage_outbox_attempts_nonnegative"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_storage_outbox_idempotency_key",
        "storage_outbox",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_storage_outbox_status_next_attempt",
        "storage_outbox",
        ["status", "next_attempt_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_storage_outbox_status_next_attempt", table_name="storage_outbox")
    op.drop_index("ux_storage_outbox_idempotency_key", table_name="storage_outbox")
    op.drop_table("storage_outbox")
    op.drop_constraint("ck_history_storage_bytes_nonnegative", "history", type_="check")
    op.drop_constraint("ck_history_storage_file_count_nonnegative", "history", type_="check")
    op.drop_column("history", "deleted_at")
    op.drop_column("history", "storage_bytes")
    op.drop_column("history", "storage_file_count")
    op.drop_column("history", "storage_checksum")
    op.drop_column("history", "storage_status")
    op.drop_column("history", "storage_version")
