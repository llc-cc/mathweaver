from __future__ import annotations

import inspect
from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import education_gamification as game


def test_xp_events_are_never_currency_event_types():
    assert game.EVENT_XP == {
        "assessment_complete": 10,
        "assignment_submit": 20,
        "assignment_ontime": 10,
        "checkin": 5,
    }
    assert "xp_card_bonus" in game.LEARNING_EVENT_TYPES
    assert "checkin" not in game.LEARNING_EVENT_TYPES
    assert "gems" not in game.EVENT_XP


def test_growth_processor_has_no_currency_mutation_path():
    source = inspect.getsource(game.process_xp_event)
    assert "apply_gem_delta" not in source
    assert "open_currency_chest" not in source
    assert "_process_stage_xp" in source
    assert "_process_weekly_goal" in source


def test_checkin_milestone_multiplier_is_bounded_and_progressive():
    assert game._milestone_multiplier(7) == (1.5, 1)
    assert game._milestone_multiplier(15) == (2.0, 2)
    assert game._milestone_multiplier(60) == (4.0, 4)
    assert game._milestone_multiplier(180) == (8.0, 8)
    assert game._milestone_multiplier(210) == (8.0, 9)
    assert game._milestone_multiplier(8) is None


def test_stage_milestones_are_strictly_increasing():
    assert game._stage_thresholds(4) == [1, 2, 3, 4]
    assert game._stage_thresholds(17) == [5, 9, 13, 17]



def test_level_roadmap_exposes_badges_and_server_owned_upgrade_rewards():
    roadmap = game._level_roadmap(1)
    assert [item["level"] for item in roadmap] == list(range(1, 11))
    assert roadmap[0]["state"] == "current"
    assert roadmap[1]["state"] == "upcoming"
    assert all(item["rewards"][0]["kind"] == "badge" for item in roadmap)

    level_five = next(item for item in roadmap if item["level"] == 5)
    assert level_five["badgeTier"] == 1
    assert level_five["badgeStars"] == 5
    assert [reward["kind"] for reward in level_five["rewards"]] == ["badge", "choice"]
    assert "经验卡" in level_five["rewards"][1]["description"]

    level_ten = next(item for item in roadmap if item["level"] == 10)
    assert [reward["kind"] for reward in level_ten["rewards"]] == ["badge", "choice", "growth_chest", "permanent_title"]
    assert "高级课程外观" in level_ten["rewards"][2]["description"]

    later = game._level_roadmap(11)
    assert later[0]["level"] == 11
    assert later[0]["state"] == "current"
    assert later[-1]["level"] == 20


def test_historical_growth_reconciliation_is_growth_only():
    source = inspect.getsource(game.reconcile_student_growth)
    assert "_make_level_growth_rewards" in source
    assert "apply_gem_delta" not in source
    assert "open_currency_chest" not in source
    assert "_process_weekly_goal" not in source
    assert "_process_stage_xp" not in source


def test_class_xp_summary_does_not_write_on_read():
    source = inspect.getsource(game._class_xp_summary)
    assert "INSERT" not in source
    assert "UPDATE" not in source


def test_grade_release_is_the_only_excellent_assignment_chest_trigger():
    api_source = (BACKEND_ROOT / "api_v2.py").read_text(encoding="utf-8")
    assert api_source.count("grant_excellent_assignment_chest(") == 1
    assert "percentage=earned_score / possible_score * 100" in api_source


def test_game_notification_endpoint_rejects_non_array_ids():
    api_source = (BACKEND_ROOT / "api_v2.py").read_text(encoding="utf-8")
    start = api_source.index("def education_game_mark_seen")
    endpoint = api_source[start:api_source.index("@app.route", start + 1)]
    assert "not isinstance(growth_ids, list)" in endpoint
    assert "not isinstance(chest_ids, list)" in endpoint



def test_teacher_gem_award_requires_an_active_student_membership():
    source = inspect.getsource(game.grant_teacher_gems)
    assert "role = 'student'" in source
    assert "removed_at IS NULL" in source



def test_historical_backfill_skips_removed_students_and_reconciles_existing_xp():
    source = inspect.getsource(game.backfill_class_game_events)
    assert source.count("m.removed_at IS NULL") >= 3
    assert "SELECT DISTINCT e.user_id" in source


def test_shop_payload_exposes_stable_system_item_keys_without_identity_fields():
    class_id = "class-1"
    revive = game._shop_item_payload({
        "id": game._system_shop_id(class_id, "revive_card"),
        "class_id": class_id,
        "item_kind": "system",
        "title": "火花复燃卡",
        "description": "恢复一天连续签到",
        "gem_price": 150,
        "stock_quantity": None,
        "is_active": 1,
    })
    custom = game._shop_item_payload({
        "id": "custom-1",
        "class_id": class_id,
        "item_kind": "custom",
        "title": "课程奖励",
        "description": "课程内奖励",
        "gem_price": 50,
        "stock_quantity": 3,
        "is_active": 1,
    })
    assert revive["itemKey"] == "revive_card"
    assert custom["itemKey"] is None
    assert not any(key in revive for key in ("email", "name", "studentNumber", "userId"))
