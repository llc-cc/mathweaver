"""Repair gem ledger audit metadata for databases already stamped at 0003.

Revision ID: 20260901_0004
Revises: 20260902_0003
Create Date: 2026-09-01
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260901_0004"
down_revision = "20260902_0003"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    return bool(op.get_bind().execute(sa.text(
        """SELECT 1 FROM information_schema.columns
             WHERE table_schema = DATABASE() AND table_name = :table_name AND column_name = :column_name"""
    ), {"table_name": table, "column_name": column}).scalar())


def upgrade() -> None:
    # The source schema and 0003 both require this column. This repair handles
    # installations that were stamped at 0003 before its final ledger DDL landed.
    if not _has_column("education_gem_ledger", "metadata_json"):
        op.execute(
            "ALTER TABLE education_gem_ledger "
            "ADD COLUMN metadata_json JSON NOT NULL DEFAULT (JSON_OBJECT()) AFTER source_type"
        )


def downgrade() -> None:
    # metadata_json is part of the 0003 source schema contract, so keep it when
    # moving the version marker back to 0003.
    pass
