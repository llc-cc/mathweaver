"""Create the MySQL business schema and graph registry.

Revision ID: 20260901_0001
Revises:
Create Date: 2026-09-01
"""
from __future__ import annotations

import re
from pathlib import Path

from alembic import op

revision = "20260901_0001"
down_revision = None
branch_labels = None
depends_on = None


def _statements() -> list[str]:
    path = Path(__file__).resolve().parents[2] / "storage" / "mysql_schema.sql"
    text = path.read_text(encoding="utf-8")
    uncommented = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("--")
    )
    return [part.strip() for part in uncommented.split(";") if part.strip()]


def upgrade() -> None:
    for statement in _statements():
        op.execute(statement)


def downgrade() -> None:
    sql = (Path(__file__).resolve().parents[2] / "storage" / "mysql_schema.sql").read_text(encoding="utf-8")
    tables = re.findall(r"CREATE TABLE `([^`]+)`", sql)
    op.execute("SET FOREIGN_KEY_CHECKS = 0")
    for table in reversed(tables):
        op.execute(f"DROP TABLE IF EXISTS `{table}`")
    op.execute("SET FOREIGN_KEY_CHECKS = 1")
