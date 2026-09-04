"""Add per-class course graph ordering.

Revision ID: 20260902_0002
Revises: 20260901_0001
Create Date: 2026-09-02
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "20260902_0002"
down_revision = "20260901_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "education_course_graph_order",
        sa.Column("class_id", sa.String(length=128, collation="utf8mb4_bin"), nullable=False),
        sa.Column("graph_key", sa.String(length=255, collation="utf8mb4_bin"), nullable=False),
        sa.Column("sort_order", sa.BigInteger(), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(["class_id"], ["education_classes.id"], name="fk_education_course_graph_order_class_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name="fk_education_course_graph_order_updated_by", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("class_id", "graph_key"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index(
        "idx_edu_course_graph_order_sort",
        "education_course_graph_order",
        ["class_id", "sort_order"],
        unique=False,
    )

    op.execute(sa.text(
        """INSERT INTO education_course_graph_order
                   (class_id, graph_key, sort_order, updated_by, updated_at)
             SELECT class_id,
                    graph_key,
                    ROW_NUMBER() OVER (
                        PARTITION BY class_id ORDER BY created_at ASC, id ASC
                    ) - 1 AS sort_order,
                    created_by,
                    created_at
               FROM (
                    SELECT id, class_id, graph_key, created_by, created_at
                      FROM (
                           SELECT keyed.*,
                                  ROW_NUMBER() OVER (
                                      PARTITION BY class_id, graph_key
                                      ORDER BY created_at ASC, id ASC
                                  ) AS graph_rank
                             FROM (
                                  SELECT id,
                                         class_id,
                                         CASE
                                           WHEN source_graph_id IS NULL OR TRIM(source_graph_id) = ''
                                             THEN CONCAT('snapshot:', id)
                                           ELSE CONCAT('source:', TRIM(source_graph_id))
                                         END AS graph_key,
                                         created_by,
                                         created_at
                                    FROM education_snapshots
                                   WHERE COALESCE(snapshot_type, 'graph') = 'graph'
                             ) AS keyed
                      ) AS ranked
                     WHERE graph_rank = 1
               ) AS deduplicated"""
    ))


def downgrade() -> None:
    op.drop_index("idx_edu_course_graph_order_sort", table_name="education_course_graph_order")
    op.drop_table("education_course_graph_order")
