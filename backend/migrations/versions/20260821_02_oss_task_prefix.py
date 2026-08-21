"""Store each persisted task's private OSS prefix.

Revision ID: 20260821_02
Revises: 20260821_01
Create Date: 2026-08-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260821_02"
down_revision: str | None = "20260821_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 可空字段保证现有任务继续按旧本地文件逻辑读取，无需一次性迁移历史文件。
    op.add_column(
        "history",
        sa.Column("object_storage_prefix", sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("history", "object_storage_prefix")
