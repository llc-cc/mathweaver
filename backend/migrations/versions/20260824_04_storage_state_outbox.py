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
    # batch 模式在 SQLite 恢复演练中重建表，在 MySQL 仍生成普通 ALTER TABLE。
    with op.batch_alter_table("history") as batch_op:
        batch_op.add_column(sa.Column("storage_version", sa.String(32), nullable=True))
        batch_op.add_column(
            sa.Column("storage_status", sa.String(32), nullable=False, server_default="legacy")
        )
        batch_op.add_column(sa.Column("storage_checksum", sa.String(64), nullable=True))
        batch_op.add_column(
            sa.Column("storage_file_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("storage_bytes", sa.BigInteger(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_check_constraint(
            "ck_history_storage_file_count_nonnegative", "storage_file_count >= 0"
        )
        batch_op.create_check_constraint(
            "ck_history_storage_bytes_nonnegative", "storage_bytes >= 0"
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
    with op.batch_alter_table("history") as batch_op:
        batch_op.drop_constraint("ck_history_storage_bytes_nonnegative", type_="check")
        batch_op.drop_constraint("ck_history_storage_file_count_nonnegative", type_="check")
        batch_op.drop_column("deleted_at")
        batch_op.drop_column("storage_bytes")
        batch_op.drop_column("storage_file_count")
        batch_op.drop_column("storage_checksum")
        batch_op.drop_column("storage_status")
        batch_op.drop_column("storage_version")
