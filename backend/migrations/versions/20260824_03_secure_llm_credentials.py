"""Add authenticated ciphertext storage for model credentials.

Revision ID: 20260824_03
Revises: 20260821_02
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260824_03"
down_revision: str | None = "20260821_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 先允许为空，由带运行时密钥的数据迁移逐行加密；DDL 阶段绝不接触密钥。
    op.add_column(
        "user_settings",
        sa.Column("llm_secrets_encrypted_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_settings", "llm_secrets_encrypted_json")
