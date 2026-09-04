"""Add course-scoped XP growth, gem economy, and privacy constraints.

Revision ID: 20260902_0003
Revises: 20260902_0002
Create Date: 2026-09-02
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260902_0003"
down_revision = "20260902_0002"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    return bool(op.get_bind().execute(sa.text(
        """SELECT 1 FROM information_schema.columns
             WHERE table_schema = DATABASE() AND table_name = :table_name AND column_name = :column_name"""
    ), {"table_name": table, "column_name": column}).scalar())


def _has_table(table: str) -> bool:
    return bool(op.get_bind().execute(sa.text(
        """SELECT 1 FROM information_schema.tables
             WHERE table_schema = DATABASE() AND table_name = :table_name"""
    ), {"table_name": table}).scalar())


def _has_constraint(name: str) -> bool:
    return bool(op.get_bind().execute(sa.text(
        """SELECT 1 FROM information_schema.table_constraints
             WHERE table_schema = DATABASE() AND constraint_name = :name"""
    ), {"name": name}).scalar())


def _has_index(table: str, name: str) -> bool:
    return bool(op.get_bind().execute(sa.text(
        """SELECT 1 FROM information_schema.statistics
             WHERE table_schema = DATABASE() AND table_name = :table_name AND index_name = :name"""
    ), {"table_name": table, "name": name}).scalar())


def _create(statement: str) -> None:
    op.execute(statement)


def upgrade() -> None:
    # This migration is deliberately compatible with a fresh database. The
    # initial migration reads the current schema snapshot, so all new DDL is
    # idempotent here while existing installations receive the same additions.
    op.execute(
        "ALTER TABLE education_game_events MODIFY assignment_id VARCHAR(128) COLLATE utf8mb4_bin NULL"
    )
    if not _has_column("education_game_events", "stage_key"):
        op.execute("ALTER TABLE education_game_events ADD COLUMN stage_key VARCHAR(255) COLLATE utf8mb4_bin NULL AFTER assignment_id")
    if not _has_column("education_game_events", "base_event_key"):
        op.execute("ALTER TABLE education_game_events ADD COLUMN base_event_key VARCHAR(255) COLLATE utf8mb4_bin NULL AFTER event_key")
    if not _has_column("education_gem_ledger", "metadata_json"):
        op.execute("ALTER TABLE education_gem_ledger ADD COLUMN metadata_json JSON NOT NULL DEFAULT (JSON_OBJECT()) AFTER source_type")
    if not _has_constraint("fk_education_game_events_membership"):
        op.execute(
            """ALTER TABLE education_game_events
                 ADD CONSTRAINT fk_education_game_events_membership
                 FOREIGN KEY (class_id, user_id)
                 REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE"""
        )
    if not _has_constraint("fk_education_student_achievements_membership"):
        op.execute(
            """ALTER TABLE education_student_achievements
                 ADD CONSTRAINT fk_education_student_achievements_membership
                 FOREIGN KEY (class_id, user_id)
                 REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE"""
        )

    _create("""
        CREATE TABLE IF NOT EXISTS education_game_mode_periods (
          id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          mode VARCHAR(32) NOT NULL,
          starts_on DATE NOT NULL,
          ends_on DATE NULL,
          created_at DATETIME(6) NOT NULL,
          PRIMARY KEY (id),
          UNIQUE KEY ux_education_game_mode_periods_start (class_id, starts_on),
          CONSTRAINT fk_education_game_mode_periods_class FOREIGN KEY (class_id) REFERENCES education_classes (id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    """)
    _create("""
        CREATE TABLE IF NOT EXISTS education_checkins (
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          checkin_date DATE NOT NULL,
          checkin_kind VARCHAR(32) NOT NULL,
          xp_event_id VARCHAR(128) COLLATE utf8mb4_bin NULL,
          checked_in_at DATETIME(6) NOT NULL,
          PRIMARY KEY (class_id, user_id, checkin_date),
          CONSTRAINT fk_education_checkins_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE,
          CONSTRAINT fk_education_checkins_event FOREIGN KEY (xp_event_id) REFERENCES education_game_events (id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    """)
    _create("""
        CREATE TABLE IF NOT EXISTS education_chest_openings (
          id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          chest_key VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
          chest_type VARCHAR(64) NOT NULL,
          source_ref VARCHAR(255) COLLATE utf8mb4_bin NULL,
          outcome_json JSON NOT NULL,
          opened_at DATETIME(6) NOT NULL,
          seen_at DATETIME(6) NULL,
          PRIMARY KEY (id),
          UNIQUE KEY ux_education_chest_openings_key (chest_key),
          KEY idx_education_chest_openings_user (class_id, user_id, seen_at),
          CONSTRAINT fk_education_chest_openings_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    """)
    _create("""
        CREATE TABLE IF NOT EXISTS education_student_wallets (
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          gem_balance BIGINT NOT NULL DEFAULT 0,
          lifetime_gems_earned BIGINT NOT NULL DEFAULT 0,
          updated_at DATETIME(6) NOT NULL,
          PRIMARY KEY (class_id, user_id),
          CONSTRAINT fk_education_student_wallets_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    """)
    _create("""
        CREATE TABLE IF NOT EXISTS education_gem_ledger (
          id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          event_key VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
          delta BIGINT NOT NULL,
          balance_after BIGINT NOT NULL,
          source_type VARCHAR(64) NOT NULL,
          metadata_json JSON NOT NULL DEFAULT (JSON_OBJECT()),
          created_at DATETIME(6) NOT NULL,
          PRIMARY KEY (id),
          UNIQUE KEY ux_education_gem_ledger_event (event_key),
          KEY idx_education_gem_ledger_user (class_id, user_id, created_at),
          CONSTRAINT fk_education_gem_ledger_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    """)
    _create("""
        CREATE TABLE IF NOT EXISTS education_student_inventory (
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          item_key VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
          quantity BIGINT NOT NULL DEFAULT 0,
          active_quantity BIGINT NOT NULL DEFAULT 0,
          updated_at DATETIME(6) NOT NULL,
          PRIMARY KEY (class_id, user_id, item_key),
          CONSTRAINT fk_education_student_inventory_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    """)
    _create("""
        CREATE TABLE IF NOT EXISTS education_shop_items (
          id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          item_kind VARCHAR(32) NOT NULL,
          title VARCHAR(160) NOT NULL,
          description TEXT NOT NULL,
          gem_price BIGINT NOT NULL,
          stock_quantity BIGINT NULL,
          is_active TINYINT(1) NOT NULL DEFAULT 1,
          created_by BIGINT NOT NULL,
          created_at DATETIME(6) NOT NULL,
          updated_at DATETIME(6) NOT NULL,
          PRIMARY KEY (id),
          KEY idx_education_shop_items_class (class_id, is_active, created_at),
          CONSTRAINT fk_education_shop_items_class FOREIGN KEY (class_id) REFERENCES education_classes (id) ON DELETE CASCADE,
          CONSTRAINT fk_education_shop_items_creator FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    """)
    _create("""
        CREATE TABLE IF NOT EXISTS education_shop_redemptions (
          id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          item_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          gem_cost BIGINT NOT NULL,
          status VARCHAR(32) NOT NULL,
          item_snapshot_json JSON NOT NULL,
          fulfilled_by BIGINT NULL,
          fulfilled_at DATETIME(6) NULL,
          cancelled_at DATETIME(6) NULL,
          created_at DATETIME(6) NOT NULL,
          PRIMARY KEY (id),
          KEY idx_education_shop_redemptions_class (class_id, status, created_at),
          KEY idx_education_shop_redemptions_user (class_id, user_id, created_at),
          CONSTRAINT fk_education_shop_redemptions_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE,
          CONSTRAINT fk_education_shop_redemptions_item FOREIGN KEY (item_id) REFERENCES education_shop_items (id) ON DELETE RESTRICT,
          CONSTRAINT fk_education_shop_redemptions_fulfiller FOREIGN KEY (fulfilled_by) REFERENCES users (id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    """)
    _create("""
        CREATE TABLE IF NOT EXISTS education_growth_rewards (
          id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          reward_key VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
          reward_type VARCHAR(64) NOT NULL,
          level_value BIGINT NULL,
          stage_key VARCHAR(255) COLLATE utf8mb4_bin NULL,
          payload_json JSON NOT NULL,
          status VARCHAR(32) NOT NULL DEFAULT 'pending',
          source_event_id VARCHAR(128) COLLATE utf8mb4_bin NULL,
          created_at DATETIME(6) NOT NULL,
          claimed_at DATETIME(6) NULL,
          seen_at DATETIME(6) NULL,
          PRIMARY KEY (id),
          UNIQUE KEY ux_education_growth_rewards_key (class_id, user_id, reward_key),
          KEY idx_education_growth_rewards_user (class_id, user_id, status, created_at),
          CONSTRAINT fk_education_growth_rewards_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE,
          CONSTRAINT fk_education_growth_rewards_event FOREIGN KEY (source_event_id) REFERENCES education_game_events (id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    """)
    _create("""
        CREATE TABLE IF NOT EXISTS education_student_collectibles (
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          collectible_key VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
          collectible_type VARCHAR(64) NOT NULL,
          title VARCHAR(160) NOT NULL,
          metadata_json JSON NOT NULL,
          equipped TINYINT(1) NOT NULL DEFAULT 0,
          unlocked_at DATETIME(6) NOT NULL,
          PRIMARY KEY (class_id, user_id, collectible_key),
          KEY idx_education_student_collectibles_equipped (class_id, user_id, collectible_type, equipped),
          CONSTRAINT fk_education_student_collectibles_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    """)
    _create("""
        CREATE TABLE IF NOT EXISTS education_weekly_goal_awards (
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          week_start DATE NOT NULL,
          goal_xp BIGINT NOT NULL,
          first_event_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          completed_at DATETIME(6) NULL,
          reward_id VARCHAR(128) COLLATE utf8mb4_bin NULL,
          PRIMARY KEY (class_id, user_id, week_start),
          KEY idx_education_weekly_goal_awards_class (class_id, week_start, completed_at),
          KEY idx_education_weekly_goal_awards_reward (reward_id),
          CONSTRAINT fk_education_weekly_goal_awards_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE,
          CONSTRAINT fk_education_weekly_goal_awards_event FOREIGN KEY (first_event_id) REFERENCES education_game_events (id) ON DELETE RESTRICT,
          CONSTRAINT fk_education_weekly_goal_awards_reward FOREIGN KEY (reward_id) REFERENCES education_growth_rewards (id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    """)
    _create("""
        CREATE TABLE IF NOT EXISTS education_class_xp_profiles (
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          level_value BIGINT NOT NULL DEFAULT 1,
          level_xp BIGINT NOT NULL DEFAULT 0,
          level_goal BIGINT NOT NULL,
          updated_at DATETIME(6) NOT NULL,
          PRIMARY KEY (class_id),
          CONSTRAINT fk_education_class_xp_profiles_class FOREIGN KEY (class_id) REFERENCES education_classes (id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    """)
    _create("""
        CREATE TABLE IF NOT EXISTS education_class_xp_contributions (
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          week_start DATE NOT NULL,
          xp_delta BIGINT NOT NULL,
          award_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          created_at DATETIME(6) NOT NULL,
          PRIMARY KEY (class_id, user_id, week_start),
          CONSTRAINT fk_education_class_xp_contributions_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE,
          CONSTRAINT fk_education_class_xp_contributions_award FOREIGN KEY (award_id) REFERENCES education_growth_rewards (id) ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    """)
    _create("""
        CREATE TABLE IF NOT EXISTS education_student_stage_progress (
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          user_id BIGINT NOT NULL,
          stage_key VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
          goal_xp BIGINT NOT NULL,
          current_xp BIGINT NOT NULL DEFAULT 0,
          milestone_mask BIGINT NOT NULL DEFAULT 0,
          started_at DATETIME(6) NOT NULL,
          updated_at DATETIME(6) NOT NULL,
          PRIMARY KEY (class_id, user_id, stage_key),
          CONSTRAINT fk_education_student_stage_progress_membership FOREIGN KEY (class_id, user_id) REFERENCES education_memberships (class_id, user_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    """)
    _create("""
        CREATE TABLE IF NOT EXISTS education_challenge_unlock_rules (
          assignment_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          class_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
          required_level BIGINT NULL,
          required_stage_key VARCHAR(255) COLLATE utf8mb4_bin NULL,
          required_stage_milestone BIGINT NULL,
          created_at DATETIME(6) NOT NULL,
          updated_at DATETIME(6) NOT NULL,
          PRIMARY KEY (assignment_id),
          KEY idx_education_challenge_unlock_rules_class (class_id, required_level),
          CONSTRAINT fk_education_challenge_unlock_rules_assignment FOREIGN KEY (assignment_id) REFERENCES education_assignments (id) ON DELETE CASCADE,
          CONSTRAINT fk_education_challenge_unlock_rules_class FOREIGN KEY (class_id) REFERENCES education_classes (id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    """)

    if not _has_table("education_game_events"):
        raise RuntimeError("education_game_events is required before game economy migration")
    if not _has_index("education_game_events", "idx_edu_game_events_stage"):
        op.execute("CREATE INDEX idx_edu_game_events_stage ON education_game_events (class_id, user_id, stage_key, occurred_at)")


def downgrade() -> None:
    for table in (
        "education_challenge_unlock_rules",
        "education_student_stage_progress",
        "education_class_xp_contributions",
        "education_class_xp_profiles",
        "education_weekly_goal_awards",
        "education_student_collectibles",
        "education_growth_rewards",
        "education_shop_redemptions",
        "education_shop_items",
        "education_student_inventory",
        "education_gem_ledger",
        "education_student_wallets",
        "education_chest_openings",
        "education_checkins",
        "education_game_mode_periods",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    if _has_index("education_game_events", "idx_edu_game_events_stage"):
        op.execute("DROP INDEX idx_edu_game_events_stage ON education_game_events")
    if _has_constraint("fk_education_student_achievements_membership"):
        op.execute("ALTER TABLE education_student_achievements DROP FOREIGN KEY fk_education_student_achievements_membership")
    if _has_constraint("fk_education_game_events_membership"):
        op.execute("ALTER TABLE education_game_events DROP FOREIGN KEY fk_education_game_events_membership")
    if _has_column("education_gem_ledger", "metadata_json"):
        op.execute("ALTER TABLE education_gem_ledger DROP COLUMN metadata_json")
    if _has_column("education_game_events", "base_event_key"):
        op.execute("ALTER TABLE education_game_events DROP COLUMN base_event_key")
    if _has_column("education_game_events", "stage_key"):
        op.execute("ALTER TABLE education_game_events DROP COLUMN stage_key")
    op.execute("ALTER TABLE education_game_events MODIFY assignment_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL")
