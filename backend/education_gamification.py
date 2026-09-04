"""Server-authoritative gamification rules for the education module."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone, tzinfo
import json
import uuid
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_WEEKLY_XP_GOAL = 60
DEFAULT_TIMEZONE = "Asia/Shanghai"
VALID_EXPERIENCES = frozenset({"classic", "map"})
EVENT_XP = {
    "assessment_complete": 10,
    "assignment_submit": 20,
    "assignment_ontime": 10,
}
EVENT_ORDER = {
    "assessment_complete": 0,
    "assignment_submit": 1,
    "assignment_ontime": 2,
}

ACHIEVEMENT_DEFINITIONS = (
    {
        "key": "first_step",
        "title": "初次启程",
        "description": "完成第一个学习节点考核",
    },
    {
        "key": "pathfinder",
        "title": "路径探索者",
        "description": "首次提交图谱作业",
    },
    {
        "key": "challenge_clear",
        "title": "挑战完成者",
        "description": "首次提交普通题目作业",
    },
    {
        "key": "on_time",
        "title": "准时抵达",
        "description": "首次在截止时间前提交作业",
    },
    {
        "key": "steady_learner",
        "title": "稳定学习",
        "description": "连续三周达到周目标",
    },
    {
        "key": "full_route",
        "title": "全线通关",
        "description": "完成一份图谱作业的全部必修节点",
    },
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().replace(tzinfo=None).isoformat()


def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise ValueError("timestamp must be an ISO date string")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _class_zone(timezone_name: str | None) -> tzinfo:
    requested = timezone_name or DEFAULT_TIMEZONE
    for candidate in dict.fromkeys((requested, DEFAULT_TIMEZONE)):
        try:
            return ZoneInfo(candidate)
        except ZoneInfoNotFoundError:
            continue
    # Windows and frozen runtimes may not provide an IANA zone database. The
    # first release only exposes Asia/Shanghai, whose current rules are UTC+8.
    return timezone(timedelta(hours=8), DEFAULT_TIMEZONE)


def _week_start(local_date) -> Any:
    return local_date - timedelta(days=local_date.weekday())


def _event_sort_key(row) -> tuple:
    return (
        parse_timestamp(row["occurred_at"]),
        EVENT_ORDER.get(row["event_type"], 99),
        str(row["event_key"]),
    )


def settings_payload(class_row) -> dict[str, Any]:
    experience = class_row["student_experience"] or "classic"
    if experience not in VALID_EXPERIENCES:
        experience = "classic"
    return {
        "studentExperience": experience,
        "weeklyXpGoal": int(class_row["weekly_xp_goal"] or DEFAULT_WEEKLY_XP_GOAL),
        "timezone": class_row["timezone"] or DEFAULT_TIMEZONE,
    }


def achievement_payload(definition: dict[str, str], unlocked_at: str | None = None) -> dict[str, Any]:
    return {
        "key": definition["key"],
        "title": definition["title"],
        "description": definition["description"],
        "unlocked": unlocked_at is not None,
        "unlockedAt": unlocked_at,
    }


def _validate_event_source(db: Any, class_id: str, user_id: int, assignment_id: str) -> None:
    assignment = db.execute(
        "SELECT class_id FROM education_assignments WHERE id = ?",
        (assignment_id,),
    ).fetchone()
    if not assignment or assignment["class_id"] != class_id:
        raise ValueError("assignment does not belong to class")
    membership = db.execute(
        "SELECT 1 FROM education_memberships WHERE class_id = ? AND user_id = ?",
        (class_id, user_id),
    ).fetchone()
    if not membership:
        raise ValueError("user does not belong to class")


def record_game_event(
    db: Any,
    *,
    class_id: str,
    user_id: int,
    assignment_id: str,
    event_type: str,
    event_key: str,
    occurred_at: str,
    metadata: dict[str, Any] | None = None,
) -> tuple[Any, bool]:
    if event_type not in EVENT_XP:
        raise ValueError("unknown game event type")
    if not event_key or not occurred_at:
        raise ValueError("event key and occurred_at are required")
    _validate_event_source(db, class_id, int(user_id), assignment_id)
    parse_timestamp(occurred_at)
    xp_delta = EVENT_XP[event_type]
    event_id = uuid.uuid4().hex
    cursor = db.execute(
        """INSERT IGNORE INTO education_game_events
             (id, class_id, user_id, assignment_id, event_type, event_key,
              xp_delta, occurred_at, metadata_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_id,
            class_id,
            int(user_id),
            assignment_id,
            event_type,
            event_key,
            xp_delta,
            occurred_at,
            json.dumps(metadata or {}, ensure_ascii=False),
            utc_now_iso(),
        ),
    )
    row = db.execute(
        "SELECT * FROM education_game_events WHERE event_key = ?",
        (event_key,),
    ).fetchone()
    if row is None:
        raise RuntimeError("game event was not persisted")
    if (
        row["class_id"] != class_id
        or int(row["user_id"]) != int(user_id)
        or row["assignment_id"] != assignment_id
        or row["event_type"] != event_type
        or int(row["xp_delta"]) != xp_delta
    ):
        raise ValueError("event key already belongs to a different event")
    return row, cursor.rowcount == 1


def _submission_due_at(submission, assignment) -> str | None:
    snapshot = {}
    try:
        snapshot = json.loads(submission["snapshot_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        pass
    if isinstance(snapshot, dict) and "dueAt" in snapshot:
        return snapshot.get("dueAt")
    return assignment["due_at"]


def is_on_time(submitted_at: str, due_at: str | None) -> bool:
    if not due_at:
        return False
    try:
        return parse_timestamp(submitted_at) <= parse_timestamp(due_at)
    except ValueError:
        return False


def backfill_class_game_events(db: Any, class_id: str) -> dict[str, Any]:
    affected_users: set[int] = set()
    created_count = 0

    attempts = db.execute(
        """SELECT aa.*, a.assignment_type
             FROM education_assessment_attempts aa
             JOIN education_assignments a ON a.id = aa.assignment_id
             JOIN education_memberships m
               ON m.class_id = a.class_id AND m.user_id = aa.user_id
              AND m.role = 'student' AND m.removed_at IS NULL
            WHERE a.class_id = ? AND aa.status = 'completed'
              AND aa.completed_at IS NOT NULL
            ORDER BY aa.completed_at, aa.id""",
        (class_id,),
    ).fetchall()
    for attempt in attempts:
        _event, created = record_game_event(
            db,
            class_id=class_id,
            user_id=int(attempt["user_id"]),
            assignment_id=attempt["assignment_id"],
            event_type="assessment_complete",
            event_key=f"assessment_complete:{attempt['id']}",
            occurred_at=attempt["completed_at"],
            metadata={
                "attemptId": attempt["id"],
                "nodeId": int(attempt["node_id"]),
                "assignmentType": attempt["assignment_type"],
            },
        )
        affected_users.add(int(attempt["user_id"]))
        created_count += int(created)

    submissions = db.execute(
        """SELECT s.*, a.class_id, a.assignment_type, a.due_at
             FROM education_assignment_submissions s
             JOIN education_assignments a ON a.id = s.assignment_id
             JOIN education_memberships m
               ON m.class_id = a.class_id AND m.user_id = s.user_id
              AND m.role = 'student' AND m.removed_at IS NULL
            WHERE a.class_id = ?
            ORDER BY s.submitted_at, s.id""",
        (class_id,),
    ).fetchall()
    for submission in submissions:
        user_id = int(submission["user_id"])
        _event, created = record_game_event(
            db,
            class_id=class_id,
            user_id=user_id,
            assignment_id=submission["assignment_id"],
            event_type="assignment_submit",
            event_key=f"assignment_submit:{submission['id']}",
            occurred_at=submission["submitted_at"],
            metadata={
                "submissionId": submission["id"],
                "assignmentType": submission["assignment_type"],
            },
        )
        affected_users.add(user_id)
        created_count += int(created)
        due_at = _submission_due_at(submission, submission)
        if is_on_time(submission["submitted_at"], due_at):
            _event, created = record_game_event(
                db,
                class_id=class_id,
                user_id=user_id,
                assignment_id=submission["assignment_id"],
                event_type="assignment_ontime",
                event_key=f"assignment_ontime:{submission['id']}",
                occurred_at=submission["submitted_at"],
                metadata={
                    "submissionId": submission["id"],
                    "dueAt": due_at,
                },
            )
            created_count += int(created)

    existing_users = db.execute(
        """SELECT DISTINCT e.user_id
             FROM education_game_events e
             JOIN education_memberships m
               ON m.class_id = e.class_id AND m.user_id = e.user_id
              AND m.role = 'student' AND m.removed_at IS NULL
            WHERE e.class_id = ?""",
        (class_id,),
    ).fetchall()
    affected_users.update(int(row["user_id"]) for row in existing_users)
    return {
        "createdEventCount": created_count,
        "userIds": sorted(affected_users),
    }


def _full_route_submission(db: Any, assignment_id: str, user_id: int) -> bool:
    assignment = db.execute(
        "SELECT assignment_type FROM education_assignments WHERE id = ?",
        (assignment_id,),
    ).fetchone()
    if not assignment or assignment["assignment_type"] != "graph":
        return False
    required_count = db.execute(
        """SELECT COUNT(*) AS count
             FROM education_assessment_nodes
            WHERE assignment_id = ? AND status != 'exempt'""",
        (assignment_id,),
    ).fetchone()["count"]
    if not required_count:
        return False
    completed_count = db.execute(
        """SELECT COUNT(*) AS count
             FROM education_assessment_attempts
            WHERE assignment_id = ? AND user_id = ? AND status = 'completed'
              AND node_id IN (
                    SELECT node_id FROM education_assessment_nodes
                     WHERE assignment_id = ? AND status != 'exempt'
              )""",
        (assignment_id, user_id, assignment_id),
    ).fetchone()["count"]
    return int(completed_count) >= int(required_count)


def _achievement_candidates(
    db: Any,
    class_id: str,
    user_id: int,
    weekly_goal: int,
) -> dict[str, tuple[str, str]]:
    rows = db.execute(
        """SELECT e.*, a.assignment_type
             FROM education_game_events e
             JOIN education_assignments a ON a.id = e.assignment_id
            WHERE e.class_id = ? AND e.user_id = ?""",
        (class_id, user_id),
    ).fetchall()
    rows = sorted(rows, key=_event_sort_key)
    candidates: dict[str, tuple[str, str]] = {}
    weekly_totals: defaultdict[Any, int] = defaultdict(int)
    zone_name = db.execute(
        "SELECT timezone FROM education_classes WHERE id = ?",
        (class_id,),
    ).fetchone()["timezone"] or DEFAULT_TIMEZONE
    zone = _class_zone(zone_name)

    for row in rows:
        event_ref = (row["id"], row["occurred_at"])
        local_dt = parse_timestamp(row["occurred_at"]).astimezone(zone)
        week = _week_start(local_dt.date())
        weekly_totals[week] += int(row["xp_delta"])

        if row["event_type"] == "assessment_complete":
            candidates.setdefault("first_step", event_ref)
        elif row["event_type"] == "assignment_submit":
            if row["assignment_type"] == "graph":
                candidates.setdefault("pathfinder", event_ref)
                if _full_route_submission(db, row["assignment_id"], user_id):
                    candidates.setdefault("full_route", event_ref)
            elif row["assignment_type"] == "direct":
                candidates.setdefault("challenge_clear", event_ref)
        elif row["event_type"] == "assignment_ontime":
            candidates.setdefault("on_time", event_ref)

        if "steady_learner" not in candidates and weekly_totals[week] >= weekly_goal:
            streak = 1
            previous = week - timedelta(days=7)
            while weekly_totals.get(previous, 0) >= weekly_goal:
                streak += 1
                previous -= timedelta(days=7)
            if streak >= 3:
                candidates["steady_learner"] = event_ref

    return candidates


def reconcile_student_achievements(
    db: Any,
    *,
    class_id: str,
    user_id: int,
    weekly_goal: int,
) -> list[dict[str, Any]]:
    existing = {
        row["achievement_key"]
        for row in db.execute(
            """SELECT achievement_key FROM education_student_achievements
                WHERE class_id = ? AND user_id = ?""",
            (class_id, user_id),
        ).fetchall()
    }
    candidates = _achievement_candidates(db, class_id, user_id, weekly_goal)
    newly_unlocked = []
    for definition in ACHIEVEMENT_DEFINITIONS:
        key = definition["key"]
        candidate = candidates.get(key)
        if key in existing or candidate is None:
            continue
        source_event_id, unlocked_at = candidate
        inserted = db.execute(
            """INSERT IGNORE INTO education_student_achievements
                 (class_id, user_id, achievement_key, source_event_id, unlocked_at)
               VALUES (?, ?, ?, ?, ?)""",
            (class_id, user_id, key, source_event_id, unlocked_at),
        )
        if inserted.rowcount == 1:
            existing.add(key)
            newly_unlocked.append(achievement_payload(definition, unlocked_at))
    return newly_unlocked


def build_achievements(db: Any, class_id: str, user_id: int) -> list[dict[str, Any]]:
    unlocked = {
        row["achievement_key"]: row["unlocked_at"]
        for row in db.execute(
            """SELECT achievement_key, unlocked_at
                FROM education_student_achievements
               WHERE class_id = ? AND user_id = ?""",
            (class_id, user_id),
        ).fetchall()
    }
    return [
        achievement_payload(definition, unlocked.get(definition["key"]))
        for definition in ACHIEVEMENT_DEFINITIONS
    ]


def build_game_profile(
    db: Any,
    *,
    class_id: str,
    user_id: int,
    weekly_goal: int,
    timezone_name: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    rows = db.execute(
        """SELECT event_type, event_key, xp_delta, occurred_at
             FROM education_game_events
            WHERE class_id = ? AND user_id = ?""",
        (class_id, user_id),
    ).fetchall()
    zone = _class_zone(timezone_name)
    current_local = parse_timestamp(now_utc or utc_now()).astimezone(zone)
    current_week = _week_start(current_local.date())
    weekly_totals: defaultdict[Any, int] = defaultdict(int)
    active_days: set[Any] = set()
    total_xp = 0
    for row in rows:
        total_xp += int(row["xp_delta"])
        local_dt = parse_timestamp(row["occurred_at"]).astimezone(zone)
        week = _week_start(local_dt.date())
        weekly_totals[week] += int(row["xp_delta"])
        if week == current_week:
            active_days.add(local_dt.date())

    weekly_xp = int(weekly_totals.get(current_week, 0))
    anchor = current_week if weekly_xp >= weekly_goal else current_week - timedelta(days=7)
    consecutive = 0
    while weekly_totals.get(anchor, 0) >= weekly_goal:
        consecutive += 1
        anchor -= timedelta(days=7)

    return {
        "totalXp": total_xp,
        "level": total_xp // 100 + 1,
        "levelXp": total_xp % 100,
        "nextLevelXp": 100,
        "weeklyXp": weekly_xp,
        "weeklyGoal": int(weekly_goal),
        "activeDaysThisWeek": len(active_days),
        "consecutiveGoalWeeks": consecutive,
    }


def build_game_summary(
    db: Any,
    *,
    class_row,
    user_id: int,
    role: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    settings = settings_payload(class_row)
    enabled = settings["studentExperience"] == "map"
    is_student = role == "student"
    return {
        "enabled": bool(enabled),
        "settings": settings,
        "profile": (
            build_game_profile(
                db,
                class_id=class_row["id"],
                user_id=int(user_id),
                weekly_goal=settings["weeklyXpGoal"],
                timezone_name=settings["timezone"],
                now_utc=now_utc,
            )
            if enabled and is_student
            else None
        ),
        "achievements": (
            build_achievements(db, class_row["id"], int(user_id))
            if enabled and is_student
            else []
        ),
    }


def build_reward_receipt(
    db: Any,
    *,
    class_row,
    user_id: int,
    xp_delta: int,
    unlocked_achievements: list[dict[str, Any]],
    now_utc: datetime | None = None,
) -> dict[str, Any] | None:
    settings = settings_payload(class_row)
    if settings["studentExperience"] != "map":
        return None
    profile = build_game_profile(
        db,
        class_id=class_row["id"],
        user_id=int(user_id),
        weekly_goal=settings["weeklyXpGoal"],
        timezone_name=settings["timezone"],
        now_utc=now_utc,
    )
    return {
        "xpDelta": int(xp_delta),
        "totalXp": profile["totalXp"],
        "level": profile["level"],
        "levelXp": profile["levelXp"],
        "nextLevelXp": profile["nextLevelXp"],
        "weeklyXp": profile["weeklyXp"],
        "weeklyGoal": profile["weeklyGoal"],
        "unlockedAchievements": unlocked_achievements,
    }

# Course-scoped growth and currency extensions.  These functions deliberately
# keep XP events and gem ledger mutations on separate paths.
from datetime import date
import hashlib
import math
import secrets

EVENT_XP = {
    "assessment_complete": 10,
    "assignment_submit": 20,
    "assignment_ontime": 10,
    "checkin": 5,
}
EVENT_ORDER = {
    "checkin": 0,
    "assessment_complete": 1,
    "assignment_submit": 2,
    "assignment_ontime": 3,
    "xp_card_bonus": 4,
}
LEARNING_EVENT_TYPES = frozenset({"assessment_complete", "assignment_submit", "assignment_ontime", "xp_card_bonus"})
XP_CARD_ELIGIBLE_TYPES = frozenset({"assessment_complete", "assignment_submit"})
GEM_INVENTORY_KEYS = frozenset({"revive_card", "xp_card"})
GROWTH_REWARD_TYPES = frozenset({"level_up", "five_level_choice", "growth_chest", "weekly_badge", "stage_milestone"})


def _as_json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _date_iso(value: date) -> str:
    return value.isoformat()


def _membership_exists(db: Any, class_id: str, user_id: int, *, active_only: bool = True) -> bool:
    sql = "SELECT 1 FROM education_memberships WHERE class_id = ? AND user_id = ?"
    if active_only:
        sql += " AND removed_at IS NULL"
    return bool(db.execute(sql, (class_id, int(user_id))).fetchone())


def _validate_event_source(db: Any, class_id: str, user_id: int, assignment_id: str | None) -> None:
    if not _membership_exists(db, class_id, int(user_id)):
        raise ValueError("user does not belong to this active class")
    if assignment_id is None:
        return
    assignment = db.execute(
        "SELECT class_id FROM education_assignments WHERE id = ?", (assignment_id,)
    ).fetchone()
    if not assignment or assignment["class_id"] != class_id:
        raise ValueError("assignment does not belong to class")


def _assignment_stage_key(db: Any, assignment_id: str | None) -> str | None:
    if not assignment_id:
        return None
    row = db.execute(
        """SELECT a.assignment_type, a.snapshot_id, s.snapshot_type, s.source_graph_id
             FROM education_assignments a
             LEFT JOIN education_snapshots s ON s.id = a.snapshot_id
            WHERE a.id = ?""",
        (assignment_id,),
    ).fetchone()
    if not row or row["assignment_type"] != "graph" or row["snapshot_type"] != "graph":
        return None
    source_graph_id = str(row["source_graph_id"] or "").strip()
    return f"source:{source_graph_id}" if source_graph_id else f"snapshot:{row['snapshot_id']}"


def record_game_event(
    db: Any,
    *,
    class_id: str,
    user_id: int,
    assignment_id: str | None,
    event_type: str,
    event_key: str,
    occurred_at: str,
    metadata: dict[str, Any] | None = None,
    xp_delta: int | None = None,
    stage_key: str | None = None,
    base_event_key: str | None = None,
) -> tuple[Any, bool]:
    if event_type not in EVENT_XP and event_type != "xp_card_bonus":
        raise ValueError("unknown game event type")
    if not event_key or not occurred_at:
        raise ValueError("event key and occurred_at are required")
    _validate_event_source(db, class_id, int(user_id), assignment_id)
    parse_timestamp(occurred_at)
    expected_xp = EVENT_XP.get(event_type)
    if xp_delta is None:
        xp_delta = expected_xp
    if isinstance(xp_delta, bool) or not isinstance(xp_delta, int) or xp_delta <= 0 or xp_delta > 30:
        raise ValueError("xp delta must be an integer from 1 to 30")
    if expected_xp is not None and xp_delta != expected_xp:
        raise ValueError("xp delta does not match event type")
    if event_type == "xp_card_bonus" and not base_event_key:
        raise ValueError("xp card bonus requires a base event key")
    stage_key = stage_key if stage_key is not None else _assignment_stage_key(db, assignment_id)
    event_id = uuid.uuid4().hex
    cursor = db.execute(
        """INSERT IGNORE INTO education_game_events
             (id, class_id, user_id, assignment_id, stage_key, event_type, event_key,
              base_event_key, xp_delta, occurred_at, metadata_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_id,
            class_id,
            int(user_id),
            assignment_id,
            stage_key,
            event_type,
            event_key,
            base_event_key,
            int(xp_delta),
            occurred_at,
            json.dumps(metadata or {}, ensure_ascii=False),
            utc_now_iso(),
        ),
    )
    row = db.execute("SELECT * FROM education_game_events WHERE event_key = ?", (event_key,)).fetchone()
    if row is None:
        raise RuntimeError("game event was not persisted")
    if (
        row["class_id"] != class_id
        or int(row["user_id"]) != int(user_id)
        or row["assignment_id"] != assignment_id
        or row["event_type"] != event_type
        or int(row["xp_delta"]) != int(xp_delta)
        or (row["base_event_key"] or None) != (base_event_key or None)
    ):
        raise ValueError("event key already belongs to a different event")
    return row, cursor.rowcount == 1


def _sum_total_xp(db: Any, class_id: str, user_id: int, *, excluding_event_id: str | None = None) -> int:
    sql = "SELECT COALESCE(SUM(xp_delta), 0) AS total FROM education_game_events WHERE class_id = ? AND user_id = ?"
    params: list[Any] = [class_id, int(user_id)]
    if excluding_event_id:
        sql += " AND id != ?"
        params.append(excluding_event_id)
    row = db.execute(sql, tuple(params)).fetchone()
    return int(row["total"] or 0) if row else 0


def _growth_reward(
    db: Any,
    *,
    class_id: str,
    user_id: int,
    reward_key: str,
    reward_type: str,
    payload: dict[str, Any],
    source_event_id: str | None = None,
    level_value: int | None = None,
    stage_key: str | None = None,
    status: str = "pending",
) -> tuple[Any, bool]:
    if reward_type not in GROWTH_REWARD_TYPES:
        raise ValueError("invalid growth reward type")
    now = utc_now_iso()
    reward_id = uuid.uuid4().hex
    cursor = db.execute(
        """INSERT IGNORE INTO education_growth_rewards
             (id, class_id, user_id, reward_key, reward_type, level_value, stage_key,
              payload_json, status, source_event_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            reward_id,
            class_id,
            int(user_id),
            reward_key,
            reward_type,
            level_value,
            stage_key,
            json.dumps(payload, ensure_ascii=False),
            status,
            source_event_id,
            now,
        ),
    )
    row = db.execute(
        """SELECT * FROM education_growth_rewards
             WHERE class_id = ? AND user_id = ? AND reward_key = ?""",
        (class_id, int(user_id), reward_key),
    ).fetchone()
    if not row:
        raise RuntimeError("growth reward was not persisted")
    if row["reward_type"] != reward_type:
        raise ValueError("growth reward key already belongs to another type")
    return row, cursor.rowcount == 1


def _add_collectible(
    db: Any,
    *,
    class_id: str,
    user_id: int,
    collectible_key: str,
    collectible_type: str,
    title: str,
    metadata: dict[str, Any] | None = None,
) -> bool:
    cursor = db.execute(
        """INSERT IGNORE INTO education_student_collectibles
             (class_id, user_id, collectible_key, collectible_type, title, metadata_json, equipped, unlocked_at)
           VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
        (
            class_id,
            int(user_id),
            collectible_key,
            collectible_type,
            title,
            json.dumps(metadata or {}, ensure_ascii=False),
            utc_now_iso(),
        ),
    )
    return cursor.rowcount == 1


def _level_window_summary(db: Any, class_id: str, user_id: int, level: int) -> dict[str, Any]:
    lower = max(0, (int(level) - 2) * 100)
    upper = (int(level) - 1) * 100
    rows = db.execute(
        """SELECT id, event_type, event_key, xp_delta, occurred_at
             FROM education_game_events
            WHERE class_id = ? AND user_id = ?""",
        (class_id, int(user_id)),
    ).fetchall()
    sources: dict[str, int] = defaultdict(int)
    assessments = submissions = on_time = 0
    running = 0
    for row in sorted(rows, key=_event_sort_key):
        event_start = running
        event_end = running + int(row["xp_delta"])
        overlap = max(0, min(event_end, upper) - max(event_start, lower))
        running = event_end
        if not overlap:
            continue
        sources[str(row["event_type"])] += overlap
        if row["event_type"] == "assessment_complete":
            assessments += 1
        elif row["event_type"] == "assignment_submit":
            submissions += 1
        elif row["event_type"] == "assignment_ontime":
            on_time += 1
    return {
        "xpGained": max(0, upper - lower),
        "xpSources": dict(sources),
        "assessmentsCompleted": assessments,
        "assignmentsSubmitted": submissions,
        "onTimeSubmissions": on_time,
        "weeklyGoalCompletions": 0,
        "stageMilestones": [],
    }

def _choice_catalog(level: int) -> list[dict[str, Any]]:
    tier = (int(level) - 1) // 5 + 1
    return [
        {
            "key": "cosmetic",
            "title": f"第 {tier} 阶成长边框",
            "kind": "cosmetic",
            "collectibleKey": f"level_frame:{level}",
            "description": "课程内个人等级卡、徽章与榜单装饰。",
        },
        {
            "key": "xp_card",
            "title": "经验卡 × 1",
            "kind": "inventory",
            "itemKey": "xp_card",
            "quantity": 1,
            "description": "激活后下一次节点考核或作业提交获得额外 XP。",
        },
        {
            "key": "title",
            "title": f"第 {tier} 阶探索者",
            "kind": "title",
            "collectibleKey": f"social_title:{level}",
            "description": "仅在本课程成长卡与排行榜中展示。",
        },
    ]


def _growth_chest_catalog(level: int) -> list[dict[str, Any]]:
    tier = (int(level) - 1) // 5 + 1
    return [
        {"kind": "cosmetic", "collectibleKey": f"advanced_frame:{level}", "title": f"第 {tier} 阶高级课程外观"},
        {"kind": "challenge_entitlement", "collectibleKey": f"level_challenge:{level}", "title": f"等级 {level} 高级挑战资格"},
    ]


def _level_roadmap(current_level: int) -> list[dict[str, Any]]:
    current = max(1, int(current_level))
    start = ((current - 1) // 5) * 5 + 1
    roadmap: list[dict[str, Any]] = []
    for level in range(start, start + 10):
        tier = (level - 1) // 5 + 1
        stars = (level - 1) % 5 + 1
        rewards = [
            {
                "kind": "badge",
                "title": f"第 {tier} 阶 {stars} 星徽章",
                "description": "升级后点亮本级徽章。",
            }
        ]
        if level % 5 == 0:
            choices = _choice_catalog(level)
            rewards.append(
                {
                    "kind": "choice",
                    "title": "成长奖励三选一",
                    "description": "、".join(str(option["title"]) for option in choices),
                }
            )
        if level % 10 == 0:
            fixed_rewards = _growth_chest_catalog(level)
            rewards.extend(
                [
                    {
                        "kind": "growth_chest",
                        "title": "高级成长箱",
                        "description": "、".join(str(item["title"]) for item in fixed_rewards),
                    },
                    {
                        "kind": "permanent_title",
                        "title": f"课程成长者 · {level}",
                        "description": "永久课程成长称号。",
                    },
                ]
            )
        roadmap.append(
            {
                "level": level,
                "badgeTier": tier,
                "badgeStars": stars,
                "state": "completed" if level < current else "current" if level == current else "upcoming",
                "rewards": rewards,
            }
        )
    return roadmap


def _make_level_growth_rewards(
    db: Any,
    *,
    class_id: str,
    user_id: int,
    before_total: int,
    after_total: int,
    source_event_id: str,
    event_outcomes: dict[str, Any],
) -> list[dict[str, Any]]:
    before_level = before_total // 100 + 1
    after_level = after_total // 100 + 1
    created: list[dict[str, Any]] = []
    for level in range(before_level + 1, after_level + 1):
        summary = _level_window_summary(db, class_id, int(user_id), level)
        summary["weeklyGoalCompletions"] = int(event_outcomes.get("weeklyGoalCompleted", False))
        summary["stageMilestones"] = list(event_outcomes.get("stageMilestones", []))
        payload = {
            "kind": "level_up",
            "level": level,
            "badgeTier": (level - 1) // 5 + 1,
            "badgeStars": (level - 1) % 5 + 1,
            "summary": summary,
        }
        _row, was_created = _growth_reward(
            db,
            class_id=class_id,
            user_id=int(user_id),
            reward_key=f"level:{level}",
            reward_type="level_up",
            payload=payload,
            source_event_id=source_event_id,
            level_value=level,
            status="pending",
        )
        if was_created:
            created.append(payload)
        if level % 5 == 0:
            choice_payload = {
                "kind": "five_level_choice",
                "level": level,
                "catalogVersion": 1,
                "options": _choice_catalog(level),
            }
            choice, choice_created = _growth_reward(
                db,
                class_id=class_id,
                user_id=int(user_id),
                reward_key=f"five-level:{level}",
                reward_type="five_level_choice",
                payload=choice_payload,
                source_event_id=source_event_id,
                level_value=level,
                status="pending",
            )
            if choice_created:
                created.append({"kind": "five_level_choice", "id": choice["id"], "level": level})
        if level % 10 == 0:
            fixed_rewards = _growth_chest_catalog(level)
            cosmetic_key = str(fixed_rewards[0]["collectibleKey"])
            entitlement_key = str(fixed_rewards[1]["collectibleKey"])
            chest_payload = {
                "kind": "growth_chest",
                "level": level,
                "title": "高级成长箱",
                "containsGems": False,
                "fixedRewards": fixed_rewards,
            }
            chest, chest_created = _growth_reward(
                db,
                class_id=class_id,
                user_id=int(user_id),
                reward_key=f"growth-chest:{level}",
                reward_type="growth_chest",
                payload=chest_payload,
                source_event_id=source_event_id,
                level_value=level,
                status="opened",
            )
            if chest_created:
                _add_collectible(db, class_id=class_id, user_id=int(user_id), collectible_key=cosmetic_key, collectible_type="cosmetic", title=f"第 {tier} 阶高级课程外观", metadata={"scope": "course", "level": level, "variant": "advanced"})
                _add_collectible(db, class_id=class_id, user_id=int(user_id), collectible_key=entitlement_key, collectible_type="challenge_entitlement", title=f"等级 {level} 高级挑战资格", metadata={"level": level})
                created.append({"kind": "growth_chest", "id": chest["id"], "level": level, "containsGems": False})
            title_key = f"permanent_title:{level}"
            title_reward, title_created = _growth_reward(
                db,
                class_id=class_id,
                user_id=int(user_id),
                reward_key=f"permanent-title:{level}",
                reward_type="level_up",
                payload={"kind": "permanent_title", "level": level, "collectibleKey": title_key, "title": f"课程成长者 · {level}"},
                source_event_id=source_event_id,
                level_value=level,
                status="opened",
            )
            if title_created:
                _add_collectible(db, class_id=class_id, user_id=int(user_id), collectible_key=title_key, collectible_type="title", title=f"课程成长者 · {level}", metadata={"scope": "course", "level": level, "permanent": True})
                created.append({"kind": "permanent_title", "id": title_reward["id"], "level": level})
    return created


def reconcile_student_growth(db: Any, *, class_id: str, user_id: int) -> list[dict[str, Any]]:
    """Create any missing level rewards from immutable historical XP events.

    This intentionally reconciles only level-derived rewards. It never replays
    weekly or stage side effects, and never touches the gem wallet or ledger.
    """
    rows = db.execute(
        """SELECT id, event_type, event_key, xp_delta, occurred_at
             FROM education_game_events
            WHERE class_id = ? AND user_id = ?""",
        (class_id, int(user_id)),
    ).fetchall()
    total_xp = 0
    created: list[dict[str, Any]] = []
    for event in sorted(rows, key=_event_sort_key):
        after_total = total_xp + int(event["xp_delta"])
        created.extend(_make_level_growth_rewards(
            db,
            class_id=class_id,
            user_id=int(user_id),
            before_total=total_xp,
            after_total=after_total,
            source_event_id=event["id"],
            event_outcomes={"weeklyGoalCompleted": False, "stageMilestones": []},
        ))
        total_xp = after_total
    return created


def _stage_goal_snapshot(db: Any, class_id: str, stage_key: str) -> int:
    if stage_key.startswith("source:"):
        condition = "COALESCE(NULLIF(TRIM(s.source_graph_id), ''), '') = ?"
        parameter = stage_key.split(":", 1)[1]
    elif stage_key.startswith("snapshot:"):
        condition = "s.id = ?"
        parameter = stage_key.split(":", 1)[1]
    else:
        return 4
    node_row = db.execute(
        f"""SELECT COUNT(*) AS count
              FROM education_assignments a
              JOIN education_snapshots s ON s.id = a.snapshot_id
              JOIN education_assessment_nodes n ON n.assignment_id = a.id
             WHERE a.class_id = ? AND a.assignment_type = 'graph' AND a.status = 'published'
               AND s.snapshot_type = 'graph' AND n.status != 'exempt' AND {condition}""",
        (class_id, parameter),
    ).fetchone()
    assignment_row = db.execute(
        f"""SELECT COUNT(*) AS count
              FROM education_assignments a
              JOIN education_snapshots s ON s.id = a.snapshot_id
             WHERE a.class_id = ? AND a.assignment_type = 'graph' AND a.status = 'published'
               AND s.snapshot_type = 'graph' AND {condition}""",
        (class_id, parameter),
    ).fetchone()
    return max(4, int(node_row["count"] or 0) * 10 + int(assignment_row["count"] or 0) * 20)


def _stage_thresholds(goal_xp: int) -> list[int]:
    thresholds: list[int] = []
    for numerator in (1, 2, 3, 4):
        threshold = math.ceil(int(goal_xp) * numerator / 4)
        thresholds.append(max((thresholds[-1] + 1) if thresholds else 1, threshold))
    return thresholds


def _process_stage_xp(db: Any, event: Any) -> list[str]:
    stage_key = event["stage_key"] or None
    if not stage_key or event["event_type"] not in LEARNING_EVENT_TYPES:
        return []
    class_id = event["class_id"]
    user_id = int(event["user_id"])
    existing = db.execute(
        """SELECT * FROM education_student_stage_progress
             WHERE class_id = ? AND user_id = ? AND stage_key = ? FOR UPDATE""",
        (class_id, user_id, stage_key),
    ).fetchone()
    now = utc_now_iso()
    if not existing:
        goal = _stage_goal_snapshot(db, class_id, stage_key)
        db.execute(
            """INSERT INTO education_student_stage_progress
                 (class_id, user_id, stage_key, goal_xp, current_xp, milestone_mask, started_at, updated_at)
               VALUES (?, ?, ?, ?, 0, 0, ?, ?)""",
            (class_id, user_id, stage_key, goal, now, now),
        )
        existing = db.execute(
            """SELECT * FROM education_student_stage_progress
                 WHERE class_id = ? AND user_id = ? AND stage_key = ? FOR UPDATE""",
            (class_id, user_id, stage_key),
        ).fetchone()
    current_xp = int(existing["current_xp"]) + int(event["xp_delta"])
    goal_xp = int(existing["goal_xp"])
    mask = int(existing["milestone_mask"])
    created: list[str] = []
    milestone_payloads = {
        0: ("25%", "阶段徽章第 1 星", "badge", f"stage_badge:{stage_key}:1"),
        1: ("50%", "阶段主题外观装饰", "cosmetic", f"stage_cosmetic:{stage_key}"),
        2: ("75%", "阶段高级挑战资格", "challenge_entitlement", f"stage_challenge:{stage_key}"),
        3: ("100%", "阶段掌握徽记", "badge", f"stage_mastery:{stage_key}"),
    }
    for index, threshold in enumerate(_stage_thresholds(goal_xp)):
        if current_xp < threshold or mask & (1 << index):
            continue
        percent, title, collectible_type, collectible_key = milestone_payloads[index]
        reward, was_created = _growth_reward(
            db,
            class_id=class_id,
            user_id=user_id,
            reward_key=f"stage:{stage_key}:{percent}",
            reward_type="stage_milestone",
            payload={"kind": "stage_milestone", "stageKey": stage_key, "percent": percent, "title": title, "containsGems": False},
            source_event_id=event["id"],
            stage_key=stage_key,
            status="opened",
        )
        mask |= 1 << index
        if was_created:
            _add_collectible(db, class_id=class_id, user_id=user_id, collectible_key=collectible_key, collectible_type=collectible_type, title=title, metadata={"stageKey": stage_key, "milestone": percent})
            created.append(percent)
    db.execute(
        """UPDATE education_student_stage_progress
              SET current_xp = ?, milestone_mask = ?, updated_at = ?
            WHERE class_id = ? AND user_id = ? AND stage_key = ?""",
        (current_xp, mask, now, class_id, user_id, stage_key),
    )
    return created


def _current_week_xp(db: Any, class_id: str, user_id: int, week_start: date, timezone_name: str) -> int:
    zone = _class_zone(timezone_name)
    rows = db.execute(
        """SELECT xp_delta, occurred_at FROM education_game_events
             WHERE class_id = ? AND user_id = ?""",
        (class_id, int(user_id)),
    ).fetchall()
    return sum(
        int(row["xp_delta"])
        for row in rows
        if _week_start(parse_timestamp(row["occurred_at"]).astimezone(zone).date()) == week_start
    )


def _active_student_count(db: Any, class_id: str) -> int:
    row = db.execute(
        """SELECT COUNT(*) AS count FROM education_memberships
             WHERE class_id = ? AND role = 'student' AND removed_at IS NULL""",
        (class_id,),
    ).fetchone()
    return int(row["count"] or 0)


def _class_xp_goal(db: Any, class_id: str) -> int:
    return max(500, _active_student_count(db, class_id) * 400)


def _contribute_class_xp(db: Any, *, class_id: str, user_id: int, week_start: date, reward_id: str) -> bool:
    now = utc_now_iso()
    inserted = db.execute(
        """INSERT IGNORE INTO education_class_xp_contributions
             (class_id, user_id, week_start, xp_delta, award_id, created_at)
           VALUES (?, ?, ?, 100, ?, ?)""",
        (class_id, int(user_id), _date_iso(week_start), reward_id, now),
    )
    if inserted.rowcount != 1:
        return False
    db.execute(
        """INSERT IGNORE INTO education_class_xp_profiles
             (class_id, level_value, level_xp, level_goal, updated_at)
           VALUES (?, 1, 0, ?, ?)""",
        (class_id, _class_xp_goal(db, class_id), now),
    )
    profile = db.execute(
        "SELECT * FROM education_class_xp_profiles WHERE class_id = ? FOR UPDATE", (class_id,)
    ).fetchone()
    current = int(profile["level_xp"]) + 100
    level = int(profile["level_value"])
    goal = int(profile["level_goal"])
    while current >= goal:
        current -= goal
        level += 1
        goal = _class_xp_goal(db, class_id)
    db.execute(
        """UPDATE education_class_xp_profiles
              SET level_value = ?, level_xp = ?, level_goal = ?, updated_at = ?
            WHERE class_id = ?""",
        (level, current, goal, now, class_id),
    )
    return True


def _process_weekly_goal(db: Any, *, event: Any, class_row: Any) -> bool:
    settings = settings_payload(class_row)
    zone = _class_zone(settings["timezone"])
    week_start = _week_start(parse_timestamp(event["occurred_at"]).astimezone(zone).date())
    class_id = event["class_id"]
    user_id = int(event["user_id"])
    db.execute(
        """INSERT IGNORE INTO education_weekly_goal_awards
             (class_id, user_id, week_start, goal_xp, first_event_id)
           VALUES (?, ?, ?, ?, ?)""",
        (class_id, user_id, _date_iso(week_start), int(settings["weeklyXpGoal"]), event["id"]),
    )
    award = db.execute(
        """SELECT * FROM education_weekly_goal_awards
             WHERE class_id = ? AND user_id = ? AND week_start = ? FOR UPDATE""",
        (class_id, user_id, _date_iso(week_start)),
    ).fetchone()
    if award["completed_at"]:
        return False
    weekly_xp = _current_week_xp(db, class_id, user_id, week_start, settings["timezone"])
    if weekly_xp < int(award["goal_xp"]):
        return False
    reward, _created = _growth_reward(
        db,
        class_id=class_id,
        user_id=user_id,
        reward_key=f"weekly-badge:{_date_iso(week_start)}",
        reward_type="weekly_badge",
        payload={"kind": "weekly_badge", "weekStart": _date_iso(week_start), "goalXp": int(award["goal_xp"]), "containsGems": False},
        source_event_id=event["id"],
        status="opened",
    )
    now = utc_now_iso()
    changed = db.execute(
        """UPDATE education_weekly_goal_awards
              SET completed_at = ?, reward_id = ?
            WHERE class_id = ? AND user_id = ? AND week_start = ? AND completed_at IS NULL""",
        (now, reward["id"], class_id, user_id, _date_iso(week_start)),
    )
    if changed.rowcount != 1:
        return False
    _add_collectible(db, class_id=class_id, user_id=user_id, collectible_key=f"weekly_badge:{_date_iso(week_start)}", collectible_type="badge", title=f"{_date_iso(week_start)} 周目标徽章", metadata={"weekStart": _date_iso(week_start)})
    _contribute_class_xp(db, class_id=class_id, user_id=user_id, week_start=week_start, reward_id=reward["id"])
    return True


def process_xp_event(db: Any, *, event: Any, class_row: Any) -> dict[str, Any]:
    """Create only growth-side state for an already-persisted XP event.

    This function never calls the gem wallet, gem ledger, or chest service.
    """
    before_total = _sum_total_xp(db, event["class_id"], int(event["user_id"]), excluding_event_id=event["id"])
    after_total = before_total + int(event["xp_delta"])
    stage_milestones = _process_stage_xp(db, event)
    weekly_complete = _process_weekly_goal(db, event=event, class_row=class_row)
    rewards = _make_level_growth_rewards(
        db,
        class_id=event["class_id"],
        user_id=int(event["user_id"]),
        before_total=before_total,
        after_total=after_total,
        source_event_id=event["id"],
        event_outcomes={"weeklyGoalCompleted": weekly_complete, "stageMilestones": stage_milestones},
    )
    return {"xpDelta": int(event["xp_delta"]), "growthRewards": rewards, "weeklyGoalCompleted": weekly_complete, "stageMilestones": stage_milestones}


def _inventory_row_for_update(db: Any, class_id: str, user_id: int, item_key: str) -> Any:
    now = utc_now_iso()
    db.execute(
        """INSERT IGNORE INTO education_student_inventory
             (class_id, user_id, item_key, quantity, active_quantity, updated_at)
           VALUES (?, ?, ?, 0, 0, ?)""",
        (class_id, int(user_id), item_key, now),
    )
    return db.execute(
        """SELECT * FROM education_student_inventory
             WHERE class_id = ? AND user_id = ? AND item_key = ? FOR UPDATE""",
        (class_id, int(user_id), item_key),
    ).fetchone()


def add_inventory_item(db: Any, *, class_id: str, user_id: int, item_key: str, quantity: int, event_key: str) -> None:
    if item_key not in GEM_INVENTORY_KEYS or quantity <= 0:
        raise ValueError("invalid inventory reward")
    row = _inventory_row_for_update(db, class_id, int(user_id), item_key)
    db.execute(
        """UPDATE education_student_inventory SET quantity = ?, updated_at = ?
             WHERE class_id = ? AND user_id = ? AND item_key = ?""",
        (int(row["quantity"]) + int(quantity), utc_now_iso(), class_id, int(user_id), item_key),
    )


def activate_xp_card(db: Any, *, class_id: str, user_id: int) -> dict[str, int]:
    row = _inventory_row_for_update(db, class_id, int(user_id), "xp_card")
    if int(row["quantity"]) <= 0:
        raise ValueError("xp_card_unavailable")
    db.execute(
        """UPDATE education_student_inventory
              SET quantity = quantity - 1, active_quantity = active_quantity + 1, updated_at = ?
            WHERE class_id = ? AND user_id = ? AND item_key = 'xp_card'""",
        (utc_now_iso(), class_id, int(user_id)),
    )
    return {"available": int(row["quantity"]) - 1, "active": int(row["active_quantity"]) + 1}


def _consume_active_xp_card(db: Any, *, class_id: str, user_id: int) -> bool:
    row = _inventory_row_for_update(db, class_id, int(user_id), "xp_card")
    if int(row["active_quantity"]) <= 0:
        return False
    changed = db.execute(
        """UPDATE education_student_inventory
              SET active_quantity = active_quantity - 1, updated_at = ?
            WHERE class_id = ? AND user_id = ? AND item_key = 'xp_card' AND active_quantity > 0""",
        (utc_now_iso(), class_id, int(user_id)),
    )
    return changed.rowcount == 1


def award_learning_xp(
    db: Any,
    *,
    class_row: Any,
    class_id: str,
    user_id: int,
    assignment_id: str,
    event_type: str,
    event_key: str,
    occurred_at: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event, created = record_game_event(
        db,
        class_id=class_id,
        user_id=int(user_id),
        assignment_id=assignment_id,
        event_type=event_type,
        event_key=event_key,
        occurred_at=occurred_at,
        metadata=metadata,
    )
    outcomes = {"xpDelta": 0, "growthRewards": [], "unlockedAchievements": []}
    if not created:
        return outcomes
    processed = process_xp_event(db, event=event, class_row=class_row)
    outcomes["xpDelta"] += int(processed["xpDelta"])
    outcomes["growthRewards"].extend(processed["growthRewards"])
    if event_type in XP_CARD_ELIGIBLE_TYPES and _consume_active_xp_card(db, class_id=class_id, user_id=int(user_id)):
        bonus_delta = min(30, int(event["xp_delta"]))
        bonus, bonus_created = record_game_event(
            db,
            class_id=class_id,
            user_id=int(user_id),
            assignment_id=assignment_id,
            event_type="xp_card_bonus",
            event_key=f"xp_card_bonus:{event_key}",
            occurred_at=occurred_at,
            metadata={"baseEventKey": event_key, "baseEventType": event_type},
            xp_delta=bonus_delta,
            stage_key=event["stage_key"],
            base_event_key=event_key,
        )
        if bonus_created:
            bonus_processed = process_xp_event(db, event=bonus, class_row=class_row)
            outcomes["xpDelta"] += int(bonus_processed["xpDelta"])
            outcomes["growthRewards"].extend(bonus_processed["growthRewards"])
    outcomes["unlockedAchievements"] = reconcile_student_achievements(
        db,
        class_id=class_id,
        user_id=int(user_id),
        weekly_goal=settings_payload(class_row)["weeklyXpGoal"],
    )
    return outcomes


def _achievement_candidates(
    db: Any,
    class_id: str,
    user_id: int,
    weekly_goal: int,
) -> dict[str, tuple[str, str]]:
    rows = db.execute(
        """SELECT e.*, a.assignment_type
             FROM education_game_events e
             LEFT JOIN education_assignments a ON a.id = e.assignment_id
            WHERE e.class_id = ? AND e.user_id = ?""",
        (class_id, user_id),
    ).fetchall()
    rows = sorted(rows, key=_event_sort_key)
    candidates: dict[str, tuple[str, str]] = {}
    weekly_totals: defaultdict[Any, int] = defaultdict(int)
    zone_name = db.execute("SELECT timezone FROM education_classes WHERE id = ?", (class_id,)).fetchone()["timezone"] or DEFAULT_TIMEZONE
    zone = _class_zone(zone_name)
    for row in rows:
        event_ref = (row["id"], row["occurred_at"])
        local_dt = parse_timestamp(row["occurred_at"]).astimezone(zone)
        week = _week_start(local_dt.date())
        weekly_totals[week] += int(row["xp_delta"])
        if row["event_type"] == "assessment_complete":
            candidates.setdefault("first_step", event_ref)
        elif row["event_type"] == "assignment_submit":
            if row["assignment_type"] == "graph":
                candidates.setdefault("pathfinder", event_ref)
                if _full_route_submission(db, row["assignment_id"], user_id):
                    candidates.setdefault("full_route", event_ref)
            elif row["assignment_type"] == "direct":
                candidates.setdefault("challenge_clear", event_ref)
        elif row["event_type"] == "assignment_ontime":
            candidates.setdefault("on_time", event_ref)
        if "steady_learner" not in candidates and weekly_totals[week] >= weekly_goal:
            streak = 1
            previous = week - timedelta(days=7)
            while weekly_totals.get(previous, 0) >= weekly_goal:
                streak += 1
                previous -= timedelta(days=7)
            if streak >= 3:
                candidates["steady_learner"] = event_ref
    return candidates



def _wallet_for_update(db: Any, class_id: str, user_id: int) -> Any:
    now = utc_now_iso()
    db.execute(
        """INSERT IGNORE INTO education_student_wallets
             (class_id, user_id, gem_balance, lifetime_gems_earned, updated_at)
           VALUES (?, ?, 0, 0, ?)""",
        (class_id, int(user_id), now),
    )
    return db.execute(
        """SELECT * FROM education_student_wallets
             WHERE class_id = ? AND user_id = ? FOR UPDATE""",
        (class_id, int(user_id)),
    ).fetchone()


def apply_gem_delta(
    db: Any,
    *,
    class_id: str,
    user_id: int,
    event_key: str,
    delta: int,
    source_type: str,
    count_toward_lifetime: bool,
    metadata: dict[str, Any] | None = None,
) -> dict[str, int]:
    if not event_key or not isinstance(delta, int) or delta == 0:
        raise ValueError("a non-zero gem ledger change is required")
    wallet = _wallet_for_update(db, class_id, int(user_id))
    existing = db.execute(
        "SELECT * FROM education_gem_ledger WHERE event_key = ?", (event_key,)
    ).fetchone()
    if existing:
        if existing["class_id"] != class_id or int(existing["user_id"]) != int(user_id) or int(existing["delta"]) != int(delta):
            raise ValueError("gem event key already belongs to another operation")
        return {"balance": int(existing["balance_after"]), "lifetimeGemsEarned": int(wallet["lifetime_gems_earned"])}
    next_balance = int(wallet["gem_balance"]) + int(delta)
    if next_balance < 0:
        raise ValueError("insufficient_gems")
    lifetime = int(wallet["lifetime_gems_earned"]) + (int(delta) if count_toward_lifetime and delta > 0 else 0)
    now = utc_now_iso()
    db.execute(
        """UPDATE education_student_wallets
              SET gem_balance = ?, lifetime_gems_earned = ?, updated_at = ?
            WHERE class_id = ? AND user_id = ?""",
        (next_balance, lifetime, now, class_id, int(user_id)),
    )
    db.execute(
        """INSERT INTO education_gem_ledger
             (id, class_id, user_id, event_key, delta, balance_after, source_type, metadata_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (uuid.uuid4().hex, class_id, int(user_id), event_key, int(delta), next_balance, source_type, json.dumps(metadata or {}, ensure_ascii=False), now),
    )
    return {"balance": next_balance, "lifetimeGemsEarned": lifetime}


def _chest_payload(row: Any) -> dict[str, Any]:
    outcome = _as_json(row["outcome_json"], {})
    return {
        "id": row["id"],
        "kind": "currency_chest",
        "chestType": row["chest_type"],
        "outcome": outcome,
        "openedAt": row["opened_at"],
        "seenAt": row["seen_at"],
    }


def _roll_currency_chest(chest_type: str, *, multiplier: float = 1.0, tier: int = 0) -> dict[str, Any]:
    if chest_type in {"weekly_checkin", "excellent_assignment"}:
        high_basis = 3_000
        jackpot_basis = 100
    elif chest_type == "checkin_milestone":
        high_basis = int(min(3 + int(tier), 10) * 1_000)
        jackpot_basis = int(min(0.1 + 0.1 * int(tier), 0.8) * 1_000)
    else:
        raise ValueError("unknown currency chest type")
    roll = secrets.randbelow(100_000)
    scale = lambda value: max(1, int(round(value * multiplier)))
    if roll < 100_000 - high_basis - jackpot_basis:
        return {"kind": "gems", "gemDelta": scale(secrets.randbelow(21) + 20)}
    if roll < 100_000 - jackpot_basis:
        return {"kind": "gems", "gemDelta": scale(100)}
    jackpot = secrets.choice(("gems", "revive_card", "xp_card"))
    if jackpot == "gems":
        return {"kind": "gems", "gemDelta": scale(999), "jackpot": True}
    return {"kind": "item", "itemKey": jackpot, "quantity": 1, "jackpot": True}


def open_currency_chest(
    db: Any,
    *,
    class_id: str,
    user_id: int,
    chest_key: str,
    chest_type: str,
    source_ref: str | None = None,
    multiplier: float = 1.0,
    tier: int = 0,
) -> dict[str, Any]:
    """Persist a deterministic opening before changing wallet or inventory."""
    outcome = _roll_currency_chest(chest_type, multiplier=multiplier, tier=tier)
    chest_id = uuid.uuid4().hex
    now = utc_now_iso()
    inserted = db.execute(
        """INSERT IGNORE INTO education_chest_openings
             (id, class_id, user_id, chest_key, chest_type, source_ref, outcome_json, opened_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (chest_id, class_id, int(user_id), chest_key, chest_type, source_ref, json.dumps(outcome, ensure_ascii=False), now),
    )
    row = db.execute("SELECT * FROM education_chest_openings WHERE chest_key = ?", (chest_key,)).fetchone()
    if not row:
        raise RuntimeError("currency chest was not persisted")
    if row["class_id"] != class_id or int(row["user_id"]) != int(user_id) or row["chest_type"] != chest_type:
        raise ValueError("chest key already belongs to another operation")
    if inserted.rowcount == 1:
        stored = _as_json(row["outcome_json"], {})
        if stored.get("kind") == "gems":
            apply_gem_delta(
                db,
                class_id=class_id,
                user_id=int(user_id),
                event_key=f"chest:{chest_key}",
                delta=int(stored.get("gemDelta") or 0),
                source_type="currency_chest",
                count_toward_lifetime=True,
            )
        elif stored.get("kind") == "item":
            add_inventory_item(
                db,
                class_id=class_id,
                user_id=int(user_id),
                item_key=str(stored.get("itemKey")),
                quantity=int(stored.get("quantity") or 1),
                event_key=f"chest:{chest_key}",
            )
    return _chest_payload(row)


def _milestone_multiplier(days: int) -> tuple[float, int] | None:
    if days == 7:
        return 1.5, 1
    if days == 15:
        return 2.0, 2
    if days == 30:
        return 3.0, 3
    if days == 60:
        return 4.0, 4
    if days > 60 and days % 30 == 0:
        tier = 4 + (days - 60) // 30
        return float(min(4 + (days - 60) // 30, 8)), tier
    return None



def _paused_on(db: Any, class_id: str, local_day: date) -> bool:
    row = db.execute(
        """SELECT 1 FROM education_game_mode_periods
             WHERE class_id = ? AND mode = 'classic' AND starts_on <= ?
               AND (ends_on IS NULL OR ends_on >= ?)
             LIMIT 1""",
        (class_id, _date_iso(local_day), _date_iso(local_day)),
    ).fetchone()
    return bool(row)


def record_game_mode_change(db: Any, *, class_id: str, previous_mode: str, next_mode: str, timezone_name: str) -> None:
    if previous_mode == next_mode:
        return
    local_day = parse_timestamp(utc_now()).astimezone(_class_zone(timezone_name)).date()
    if next_mode == "classic":
        db.execute(
            """INSERT IGNORE INTO education_game_mode_periods
                 (id, class_id, mode, starts_on, ends_on, created_at)
               VALUES (?, ?, 'classic', ?, NULL, ?)""",
            (uuid.uuid4().hex, class_id, _date_iso(local_day), utc_now_iso()),
        )
    elif next_mode == "map":
        db.execute(
            """UPDATE education_game_mode_periods
                  SET ends_on = ?
                WHERE class_id = ? AND mode = 'classic' AND ends_on IS NULL""",
            (_date_iso(local_day - timedelta(days=1)), class_id),
        )


def _checkin_status(db: Any, *, class_id: str, user_id: int, timezone_name: str, now_utc: datetime | None = None) -> dict[str, Any]:
    zone = _class_zone(timezone_name)
    now_local = parse_timestamp(now_utc or utc_now()).astimezone(zone)
    today = now_local.date()
    rows = db.execute(
        """SELECT checkin_date, checkin_kind FROM education_checkins
             WHERE class_id = ? AND user_id = ? ORDER BY checkin_date DESC""",
        (class_id, int(user_id)),
    ).fetchall()
    kind_by_day = {date.fromisoformat(str(row["checkin_date"])[:10]): row["checkin_kind"] for row in rows}
    today_kind = kind_by_day.get(today)
    streak = 0
    cursor = today
    if cursor not in kind_by_day and not _paused_on(db, class_id, cursor):
        cursor -= timedelta(days=1)
    while True:
        if cursor in kind_by_day:
            streak += 1
            cursor -= timedelta(days=1)
            continue
        if _paused_on(db, class_id, cursor):
            cursor -= timedelta(days=1)
            continue
        break
    week_start = _week_start(today)
    genuine_days_week = sum(1 for day, kind in kind_by_day.items() if kind == "genuine" and _week_start(day) == week_start)
    genuine_total = sum(1 for kind in kind_by_day.values() if kind == "genuine")
    yesterday = today - timedelta(days=1)
    can_revive = (
        today_kind == "genuine"
        and yesterday not in kind_by_day
        and not _paused_on(db, class_id, yesterday)
        and (today - timedelta(days=2)) in kind_by_day
    )
    inventory = db.execute(
        """SELECT item_key, quantity FROM education_student_inventory
             WHERE class_id = ? AND user_id = ?""",
        (class_id, int(user_id)),
    ).fetchall()
    quantities = {row["item_key"]: int(row["quantity"] or 0) for row in inventory}
    week_days = [
        {
            "date": _date_iso(day),
            "kind": kind_by_day.get(day),
            "paused": bool(_paused_on(db, class_id, day)),
            "isToday": day == today,
        }
        for day in (week_start + timedelta(days=offset) for offset in range(7))
    ]
    return {
        "todayCheckedIn": today_kind == "genuine",
        "todayKind": today_kind,
        "streakDays": streak,
        "weeklyGenuineDays": genuine_days_week,
        "totalGenuineDays": genuine_total,
        "canReviveYesterday": bool(can_revive and quantities.get("revive_card", 0) > 0),
        "reviveCards": quantities.get("revive_card", 0),
        "weekDays": week_days,
    }


def check_in_student(db: Any, *, class_row: Any, user_id: int, now_utc: datetime | None = None) -> dict[str, Any]:
    settings = settings_payload(class_row)
    if settings["studentExperience"] != "map":
        raise ValueError("game_mode_paused")
    class_id = class_row["id"]
    zone = _class_zone(settings["timezone"])
    now = parse_timestamp(now_utc or utc_now())
    local_day = now.astimezone(zone).date()
    existing = db.execute(
        """SELECT * FROM education_checkins WHERE class_id = ? AND user_id = ? AND checkin_date = ?""",
        (class_id, int(user_id), _date_iso(local_day)),
    ).fetchone()
    if existing:
        return {"alreadyCheckedIn": True, "checkin": _checkin_status(db, class_id=class_id, user_id=int(user_id), timezone_name=settings["timezone"], now_utc=now), "openedChests": []}
    event, created = record_game_event(
        db,
        class_id=class_id,
        user_id=int(user_id),
        assignment_id=None,
        event_type="checkin",
        event_key=f"checkin:{class_id}:{user_id}:{_date_iso(local_day)}",
        occurred_at=now.isoformat(),
        metadata={"localDate": _date_iso(local_day)},
        stage_key=None,
    )
    inserted = db.execute(
        """INSERT IGNORE INTO education_checkins
             (class_id, user_id, checkin_date, checkin_kind, xp_event_id, checked_in_at)
           VALUES (?, ?, ?, 'genuine', ?, ?)""",
        (class_id, int(user_id), _date_iso(local_day), event["id"], now.isoformat()),
    )
    if inserted.rowcount != 1:
        return {"alreadyCheckedIn": True, "checkin": _checkin_status(db, class_id=class_id, user_id=int(user_id), timezone_name=settings["timezone"], now_utc=now), "openedChests": []}
    xp_result = process_xp_event(db, event=event, class_row=class_row) if created else {"xpDelta": 0, "growthRewards": []}
    opened: list[dict[str, Any]] = []
    status = _checkin_status(db, class_id=class_id, user_id=int(user_id), timezone_name=settings["timezone"], now_utc=now)
    week_start = _week_start(local_day)
    if int(status["weeklyGenuineDays"]) >= 5:
        opened.append(open_currency_chest(
            db,
            class_id=class_id,
            user_id=int(user_id),
            chest_key=f"weekly-checkin:{class_id}:{user_id}:{_date_iso(week_start)}",
            chest_type="weekly_checkin",
            source_ref=_date_iso(week_start),
        ))
    milestone = _milestone_multiplier(int(status["totalGenuineDays"]))
    if milestone:
        multiplier, tier = milestone
        opened.append(open_currency_chest(
            db,
            class_id=class_id,
            user_id=int(user_id),
            chest_key=f"checkin-milestone:{class_id}:{user_id}:{int(status['totalGenuineDays'])}",
            chest_type="checkin_milestone",
            source_ref=str(int(status["totalGenuineDays"])),
            multiplier=multiplier,
            tier=tier,
        ))
    unlocked = reconcile_student_achievements(db, class_id=class_id, user_id=int(user_id), weekly_goal=settings["weeklyXpGoal"])
    return {
        "alreadyCheckedIn": False,
        "checkin": status,
        "openedChests": opened,
        "reward": build_reward_receipt(db, class_row=class_row, user_id=int(user_id), xp_delta=int(xp_result["xpDelta"]), unlocked_achievements=unlocked, growth_events=xp_result.get("growthRewards") or []),
    }


def revive_yesterday(db: Any, *, class_row: Any, user_id: int, now_utc: datetime | None = None) -> dict[str, Any]:
    settings = settings_payload(class_row)
    if settings["studentExperience"] != "map":
        raise ValueError("game_mode_paused")
    class_id = class_row["id"]
    zone = _class_zone(settings["timezone"])
    now = parse_timestamp(now_utc or utc_now())
    today = now.astimezone(zone).date()
    status = _checkin_status(db, class_id=class_id, user_id=int(user_id), timezone_name=settings["timezone"], now_utc=now)
    if not status["canReviveYesterday"]:
        raise ValueError("revive_not_available")
    row = _inventory_row_for_update(db, class_id, int(user_id), "revive_card")
    if int(row["quantity"]) <= 0:
        raise ValueError("revive_card_unavailable")
    yesterday = today - timedelta(days=1)
    changed = db.execute(
        """UPDATE education_student_inventory SET quantity = quantity - 1, updated_at = ?
             WHERE class_id = ? AND user_id = ? AND item_key = 'revive_card' AND quantity > 0""",
        (utc_now_iso(), class_id, int(user_id)),
    )
    if changed.rowcount != 1:
        raise ValueError("revive_card_unavailable")
    db.execute(
        """INSERT INTO education_checkins
             (class_id, user_id, checkin_date, checkin_kind, xp_event_id, checked_in_at)
           VALUES (?, ?, ?, 'revived', NULL, ?)""",
        (class_id, int(user_id), _date_iso(yesterday), now.isoformat()),
    )
    return _checkin_status(db, class_id=class_id, user_id=int(user_id), timezone_name=settings["timezone"], now_utc=now)



def _system_shop_id(class_id: str, item_key: str) -> str:
    return hashlib.sha256(f"education-shop:{class_id}:{item_key}".encode("utf-8")).hexdigest()[:32]


def ensure_system_shop_items(db: Any, class_id: str) -> None:
    owner = db.execute("SELECT owner_user_id FROM education_classes WHERE id = ?", (class_id,)).fetchone()
    if not owner:
        raise ValueError("class not found")
    now = utc_now_iso()
    items = (
        ("revive_card", "火花复燃卡", "补回恰好漏掉的一天连续签到；不会补发 XP 或宝石箱。", 150),
        ("xp_card", "经验卡", "激活后下一次节点考核或作业提交获得同等基础 XP，最多 +30。", 100),
    )
    for key, title, description, price in items:
        db.execute(
            """INSERT IGNORE INTO education_shop_items
                 (id, class_id, item_kind, title, description, gem_price, stock_quantity, is_active, created_by, created_at, updated_at)
               VALUES (?, ?, 'system', ?, ?, ?, NULL, 1, ?, ?, ?)""",
            (_system_shop_id(class_id, key), class_id, title, description, price, int(owner["owner_user_id"]), now, now),
        )


def _shop_item_payload(row: Any) -> dict[str, Any]:
    class_id = str(row["class_id"])
    item_key = next((key for key in ("revive_card", "xp_card") if row["id"] == _system_shop_id(class_id, key)), None)
    return {
        "id": row["id"],
        "kind": row["item_kind"],
        "itemKey": item_key,
        "title": row["title"],
        "description": row["description"],
        "gemPrice": int(row["gem_price"]),
        "stock": int(row["stock_quantity"]) if row["stock_quantity"] is not None else None,
        "active": bool(row["is_active"]),
    }


def list_shop_items(db: Any, *, class_id: str, include_inactive: bool = False) -> list[dict[str, Any]]:
    ensure_system_shop_items(db, class_id)
    sql = "SELECT * FROM education_shop_items WHERE class_id = ?"
    if not include_inactive:
        sql += " AND is_active = 1"
    sql += " ORDER BY item_kind = 'system' DESC, created_at, id"
    return [_shop_item_payload(row) for row in db.execute(sql, (class_id,)).fetchall()]


def redeem_shop_item(db: Any, *, class_id: str, user_id: int, item_id: str) -> dict[str, Any]:
    ensure_system_shop_items(db, class_id)
    item = db.execute("SELECT * FROM education_shop_items WHERE id = ? FOR UPDATE", (item_id,)).fetchone()
    if not item or item["class_id"] != class_id or not bool(item["is_active"]):
        raise ValueError("shop_item_unavailable")
    if item["stock_quantity"] is not None and int(item["stock_quantity"]) <= 0:
        raise ValueError("shop_item_out_of_stock")
    redemption_id = uuid.uuid4().hex
    cost = int(item["gem_price"])
    apply_gem_delta(
        db,
        class_id=class_id,
        user_id=int(user_id),
        event_key=f"shop-redemption:{redemption_id}",
        delta=-cost,
        source_type="shop_redemption",
        count_toward_lifetime=False,
    )
    now = utc_now_iso()
    immediate = item["item_kind"] == "system"
    status = "fulfilled" if immediate else "pending"
    db.execute(
        """INSERT INTO education_shop_redemptions
             (id, class_id, user_id, item_id, gem_cost, status, item_snapshot_json, fulfilled_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (redemption_id, class_id, int(user_id), item_id, cost, status, json.dumps(_shop_item_payload(item), ensure_ascii=False), now if immediate else None, now),
    )
    if item["stock_quantity"] is not None:
        db.execute("UPDATE education_shop_items SET stock_quantity = stock_quantity - 1, updated_at = ? WHERE id = ?", (now, item_id))
    if immediate:
        key = "revive_card" if item_id == _system_shop_id(class_id, "revive_card") else "xp_card"
        add_inventory_item(db, class_id=class_id, user_id=int(user_id), item_key=key, quantity=1, event_key=f"shop-redemption:{redemption_id}")
    return {"id": redemption_id, "item": _shop_item_payload(item), "gemCost": cost, "status": status, "createdAt": now}


def _redemption_payload(row: Any, *, teacher: bool = False) -> dict[str, Any]:
    payload = {
        "id": row["id"],
        "itemId": row["item_id"],
        "item": _as_json(row["item_snapshot_json"], {}),
        "gemCost": int(row["gem_cost"]),
        "status": row["status"],
        "createdAt": row["created_at"],
        "fulfilledAt": row["fulfilled_at"],
        "cancelledAt": row["cancelled_at"],
    }
    if teacher:
        payload["studentName"] = row["student_name"] or "学生"
    return payload


def list_student_redemptions(db: Any, *, class_id: str, user_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        """SELECT * FROM education_shop_redemptions WHERE class_id = ? AND user_id = ? ORDER BY created_at DESC""",
        (class_id, int(user_id)),
    ).fetchall()
    return [_redemption_payload(row) for row in rows]


def list_teacher_redemptions(db: Any, *, class_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        """SELECT r.*, m.student_name FROM education_shop_redemptions r
             JOIN education_memberships m ON m.class_id = r.class_id AND m.user_id = r.user_id
            WHERE r.class_id = ? ORDER BY r.created_at DESC""",
        (class_id,),
    ).fetchall()
    return [_redemption_payload(row, teacher=True) for row in rows]


def resolve_redemption(db: Any, *, class_id: str, redemption_id: str, teacher_user_id: int, action: str) -> dict[str, Any]:
    if action not in {"fulfill", "cancel"}:
        raise ValueError("invalid_redemption_action")
    redemption = db.execute("SELECT * FROM education_shop_redemptions WHERE id = ? FOR UPDATE", (redemption_id,)).fetchone()
    if not redemption or redemption["class_id"] != class_id:
        raise ValueError("redemption_not_found")
    if redemption["status"] != "pending":
        raise ValueError("redemption_not_pending")
    now = utc_now_iso()
    if action == "fulfill":
        db.execute(
            """UPDATE education_shop_redemptions SET status = 'fulfilled', fulfilled_by = ?, fulfilled_at = ? WHERE id = ?""",
            (int(teacher_user_id), now, redemption_id),
        )
    else:
        item = db.execute("SELECT * FROM education_shop_items WHERE id = ? FOR UPDATE", (redemption["item_id"],)).fetchone()
        apply_gem_delta(
            db,
            class_id=class_id,
            user_id=int(redemption["user_id"]),
            event_key=f"shop-refund:{redemption_id}",
            delta=int(redemption["gem_cost"]),
            source_type="shop_refund",
            count_toward_lifetime=False,
        )
        if item and item["stock_quantity"] is not None:
            db.execute("UPDATE education_shop_items SET stock_quantity = stock_quantity + 1, updated_at = ? WHERE id = ?", (now, item["id"]))
        db.execute("UPDATE education_shop_redemptions SET status = 'cancelled', cancelled_at = ? WHERE id = ?", (now, redemption_id))
    updated = db.execute("SELECT * FROM education_shop_redemptions WHERE id = ?", (redemption_id,)).fetchone()
    return _redemption_payload(updated)


def create_or_update_custom_shop_item(db: Any, *, class_id: str, teacher_user_id: int, item_id: str | None, title: str, description: str, gem_price: int, stock: int | None, active: bool) -> dict[str, Any]:
    title = title.strip()
    description = description.strip()
    if not title or len(title) > 160 or len(description) > 2_000:
        raise ValueError("invalid_shop_item")
    if isinstance(gem_price, bool) or not isinstance(gem_price, int) or not 1 <= gem_price <= 100_000:
        raise ValueError("invalid_shop_price")
    if stock is not None and (isinstance(stock, bool) or not isinstance(stock, int) or stock < 0):
        raise ValueError("invalid_shop_stock")
    now = utc_now_iso()
    if item_id:
        row = db.execute("SELECT * FROM education_shop_items WHERE id = ? FOR UPDATE", (item_id,)).fetchone()
        if not row or row["class_id"] != class_id or row["item_kind"] != "custom":
            raise ValueError("shop_item_not_found")
        db.execute(
            """UPDATE education_shop_items
                  SET title = ?, description = ?, gem_price = ?, stock_quantity = ?, is_active = ?, updated_at = ?
                WHERE id = ?""",
            (title, description, gem_price, stock, int(bool(active)), now, item_id),
        )
    else:
        item_id = uuid.uuid4().hex
        db.execute(
            """INSERT INTO education_shop_items
                 (id, class_id, item_kind, title, description, gem_price, stock_quantity, is_active, created_by, created_at, updated_at)
               VALUES (?, ?, 'custom', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (item_id, class_id, title, description, gem_price, stock, int(bool(active)), int(teacher_user_id), now, now),
        )
    row = db.execute("SELECT * FROM education_shop_items WHERE id = ?", (item_id,)).fetchone()
    return _shop_item_payload(row)


def grant_teacher_gems(db: Any, *, class_id: str, user_id: int, amount: int, reason: str, teacher_user_id: int) -> dict[str, Any]:
    if isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= 500:
        raise ValueError("invalid_gem_amount")
    reason = reason.strip()
    if not reason or len(reason) > 500:
        raise ValueError("gem_reason_required")
    student = db.execute(
        """SELECT 1 FROM education_memberships
             WHERE class_id = ? AND user_id = ? AND role = 'student' AND removed_at IS NULL""",
        (class_id, int(user_id)),
    ).fetchone()
    if not student:
        raise ValueError("student_not_in_class")
    event_key = f"teacher-gem:{class_id}:{int(user_id)}:{uuid.uuid4().hex}"
    wallet = apply_gem_delta(
        db,
        class_id=class_id,
        user_id=int(user_id),
        event_key=event_key,
        delta=int(amount),
        source_type="teacher_gem_award",
        count_toward_lifetime=True,
        metadata={"reason": reason, "awardedBy": int(teacher_user_id)},
    )
    return {"kind": "teacher_gem_award", "amount": int(amount), "reason": reason, "balance": wallet["balance"]}


def list_teacher_gem_awards(db: Any, *, class_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        """SELECT l.*, m.student_name FROM education_gem_ledger l
             JOIN education_memberships m ON m.class_id = l.class_id AND m.user_id = l.user_id
            WHERE l.class_id = ? AND l.source_type = 'teacher_gem_award'
            ORDER BY l.created_at DESC""",
        (class_id,),
    ).fetchall()
    return [
        {
            "amount": int(row["delta"]),
            "studentName": row["student_name"] or "学生",
            "reason": _as_json(row["metadata_json"], {}).get("reason") or "",
            "createdAt": row["created_at"],
        }
        for row in rows
    ]


def grant_excellent_assignment_chest(db: Any, *, class_row: Any, user_id: int, submission_id: str, percentage: float) -> dict[str, Any] | None:
    if settings_payload(class_row)["studentExperience"] != "map" or float(percentage) <= 80:
        return None
    return open_currency_chest(
        db,
        class_id=class_row["id"],
        user_id=int(user_id),
        chest_key=f"excellent-assignment:{class_row['id']}:{submission_id}",
        chest_type="excellent_assignment",
        source_ref=submission_id,
    )



def claim_five_level_choice(db: Any, *, class_id: str, user_id: int, reward_id: str, option_key: str) -> dict[str, Any]:
    reward = db.execute(
        """SELECT * FROM education_growth_rewards WHERE id = ? AND class_id = ? AND user_id = ? FOR UPDATE""",
        (reward_id, class_id, int(user_id)),
    ).fetchone()
    if not reward or reward["reward_type"] != "five_level_choice":
        raise ValueError("growth_choice_not_found")
    if reward["status"] != "pending":
        raise ValueError("growth_choice_already_claimed")
    payload = _as_json(reward["payload_json"], {})
    options = payload.get("options") if isinstance(payload.get("options"), list) else []
    option = next((item for item in options if isinstance(item, dict) and item.get("key") == option_key), None)
    if not option:
        raise ValueError("invalid_growth_choice")
    if option.get("kind") == "inventory":
        add_inventory_item(db, class_id=class_id, user_id=int(user_id), item_key="xp_card", quantity=1, event_key=f"growth-choice:{reward_id}")
    elif option.get("kind") in {"cosmetic", "title"}:
        _add_collectible(
            db,
            class_id=class_id,
            user_id=int(user_id),
            collectible_key=str(option["collectibleKey"]),
            collectible_type=str(option["kind"]),
            title=str(option["title"]),
            metadata={"scope": "course", "sourceRewardId": reward_id, "level": payload.get("level")},
        )
    else:
        raise ValueError("invalid_growth_choice")
    now = utc_now_iso()
    db.execute("UPDATE education_growth_rewards SET status = 'claimed', claimed_at = ? WHERE id = ?", (now, reward_id))
    return {"id": reward_id, "kind": "five_level_choice", "claimedOption": option_key, "claimedAt": now}


def equip_collectible(db: Any, *, class_id: str, user_id: int, collectible_key: str) -> dict[str, Any]:
    collectible = db.execute(
        """SELECT * FROM education_student_collectibles
             WHERE class_id = ? AND user_id = ? AND collectible_key = ? FOR UPDATE""",
        (class_id, int(user_id), collectible_key),
    ).fetchone()
    if not collectible or collectible["collectible_type"] not in {"cosmetic", "title"}:
        raise ValueError("collectible_not_equippable")
    collectible_type = collectible["collectible_type"]
    db.execute(
        """UPDATE education_student_collectibles SET equipped = 0
             WHERE class_id = ? AND user_id = ? AND collectible_type = ?""",
        (class_id, int(user_id), collectible_type),
    )
    db.execute(
        """UPDATE education_student_collectibles SET equipped = 1
             WHERE class_id = ? AND user_id = ? AND collectible_key = ?""",
        (class_id, int(user_id), collectible_key),
    )
    return {"key": collectible_key, "type": collectible_type, "title": collectible["title"], "equipped": True}


def mark_game_rewards_seen(db: Any, *, class_id: str, user_id: int, growth_ids: list[str], chest_ids: list[str]) -> None:
    now = utc_now_iso()
    for reward_id in dict.fromkeys(growth_ids):
        db.execute(
            """UPDATE education_growth_rewards SET seen_at = ?
                 WHERE id = ? AND class_id = ? AND user_id = ? AND seen_at IS NULL""",
            (now, reward_id, class_id, int(user_id)),
        )
    for chest_id in dict.fromkeys(chest_ids):
        db.execute(
            """UPDATE education_chest_openings SET seen_at = ?
                 WHERE id = ? AND class_id = ? AND user_id = ? AND seen_at IS NULL""",
            (now, chest_id, class_id, int(user_id)),
        )


def _collectible_payload(row: Any) -> dict[str, Any]:
    return {
        "key": row["collectible_key"],
        "type": row["collectible_type"],
        "title": row["title"],
        "metadata": _as_json(row["metadata_json"], {}),
        "equipped": bool(row["equipped"]),
        "unlockedAt": row["unlocked_at"],
    }


def _growth_reward_payload(row: Any) -> dict[str, Any]:
    payload = _as_json(row["payload_json"], {})
    return {
        "id": row["id"],
        "kind": payload.get("kind") or row["reward_type"],
        "rewardType": row["reward_type"],
        "status": row["status"],
        "level": int(row["level_value"]) if row["level_value"] is not None else payload.get("level"),
        "stageKey": row["stage_key"] or payload.get("stageKey"),
        "payload": payload,
        "createdAt": row["created_at"],
        "claimedAt": row["claimed_at"],
        "seenAt": row["seen_at"],
    }


def _weekly_goal_summary(db: Any, *, class_id: str, user_id: int, profile: dict[str, Any], timezone_name: str, now_utc: datetime | None = None) -> dict[str, Any]:
    now_local = parse_timestamp(now_utc or utc_now()).astimezone(_class_zone(timezone_name))
    current_week = _week_start(now_local.date())
    row = db.execute(
        """SELECT * FROM education_weekly_goal_awards
             WHERE class_id = ? AND user_id = ? AND week_start = ?""",
        (class_id, int(user_id), _date_iso(current_week)),
    ).fetchone()
    goal = int(row["goal_xp"]) if row else int(profile["weeklyGoal"])
    completed = bool(row and row["completed_at"])
    return {"weekStart": _date_iso(current_week), "xp": int(profile["weeklyXp"]), "goalXp": goal, "completed": completed, "completedAt": row["completed_at"] if row else None}


def _class_xp_summary(db: Any, *, class_id: str, timezone_name: str, now_utc: datetime | None = None) -> dict[str, Any]:
    profile = db.execute(
        "SELECT * FROM education_class_xp_profiles WHERE class_id = ?",
        (class_id,),
    ).fetchone()
    if profile:
        level = int(profile["level_value"])
        level_xp = int(profile["level_xp"])
        level_goal = int(profile["level_goal"])
    else:
        # Reading a game summary must not begin a write transaction. The first
        # weekly contribution persists this same initial snapshot atomically.
        level = 1
        level_xp = 0
        level_goal = _class_xp_goal(db, class_id)
    local_date = parse_timestamp(now_utc or utc_now()).astimezone(_class_zone(timezone_name)).date()
    count = db.execute(
        """SELECT COUNT(*) AS count FROM education_weekly_goal_awards
             WHERE class_id = ? AND week_start = ? AND completed_at IS NOT NULL""",
        (class_id, _date_iso(_week_start(local_date))),
    ).fetchone()
    return {"level": level, "levelXp": level_xp, "levelGoal": level_goal, "weeklyGoalCompleters": int(count["count"] or 0)}

def _stage_progress_payloads(db: Any, *, class_id: str, user_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        """SELECT * FROM education_student_stage_progress
             WHERE class_id = ? AND user_id = ? ORDER BY started_at, stage_key""",
        (class_id, int(user_id)),
    ).fetchall()
    result = []
    for row in rows:
        thresholds = _stage_thresholds(int(row["goal_xp"]))
        current = int(row["current_xp"])
        result.append({
            "stageKey": row["stage_key"],
            "goalXp": int(row["goal_xp"]),
            "currentXp": current,
            "milestones": [{"percent": percent, "thresholdXp": threshold, "completed": current >= threshold} for percent, threshold in zip((25, 50, 75, 100), thresholds)],
            "completed": current >= thresholds[-1],
        })
    return result


def build_game_profile(
    db: Any,
    *,
    class_id: str,
    user_id: int,
    weekly_goal: int,
    timezone_name: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    rows = db.execute(
        """SELECT event_type, event_key, xp_delta, occurred_at
             FROM education_game_events WHERE class_id = ? AND user_id = ?""",
        (class_id, int(user_id)),
    ).fetchall()
    zone = _class_zone(timezone_name)
    current_local = parse_timestamp(now_utc or utc_now()).astimezone(zone)
    current_week = _week_start(current_local.date())
    weekly_totals: defaultdict[Any, int] = defaultdict(int)
    active_days: set[Any] = set()
    total_xp = 0
    for row in rows:
        total_xp += int(row["xp_delta"])
        local_dt = parse_timestamp(row["occurred_at"]).astimezone(zone)
        week = _week_start(local_dt.date())
        weekly_totals[week] += int(row["xp_delta"])
        if week == current_week:
            active_days.add(local_dt.date())
    weekly_snapshot = db.execute(
        """SELECT goal_xp FROM education_weekly_goal_awards
             WHERE class_id = ? AND user_id = ? AND week_start = ?""",
        (class_id, int(user_id), _date_iso(current_week)),
    ).fetchone()
    current_goal = int(weekly_snapshot["goal_xp"]) if weekly_snapshot else int(weekly_goal)
    weekly_xp = int(weekly_totals.get(current_week, 0))
    anchor = current_week if weekly_xp >= current_goal else current_week - timedelta(days=7)
    consecutive = 0
    while weekly_totals.get(anchor, 0) >= current_goal:
        consecutive += 1
        anchor -= timedelta(days=7)
    level = total_xp // 100 + 1
    return {
        "totalXp": total_xp,
        "level": level,
        "levelXp": total_xp % 100,
        "nextLevelXp": 100,
        "weeklyXp": weekly_xp,
        "weeklyGoal": current_goal,
        "activeDaysThisWeek": len(active_days),
        "consecutiveGoalWeeks": consecutive,
        "badgeTier": (level - 1) // 5 + 1,
        "badgeStars": (level - 1) % 5 + 1,
    }



def build_game_summary(
    db: Any,
    *,
    class_row: Any,
    user_id: int,
    role: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    settings = settings_payload(class_row)
    enabled = settings["studentExperience"] == "map"
    is_student = role == "student"
    if not enabled or not is_student:
        return {"enabled": bool(enabled), "settings": settings, "profile": None, "achievements": [], "growth": None, "checkin": None, "wallet": None, "inventory": None, "unreadCurrencyRewards": []}
    class_id = class_row["id"]
    profile = build_game_profile(db, class_id=class_id, user_id=int(user_id), weekly_goal=settings["weeklyXpGoal"], timezone_name=settings["timezone"], now_utc=now_utc)
    rewards = db.execute(
        """SELECT * FROM education_growth_rewards
             WHERE class_id = ? AND user_id = ? ORDER BY created_at, id""",
        (class_id, int(user_id)),
    ).fetchall()
    reward_payloads = [_growth_reward_payload(row) for row in rewards]
    collectibles = [_collectible_payload(row) for row in db.execute(
        """SELECT * FROM education_student_collectibles
             WHERE class_id = ? AND user_id = ? ORDER BY collectible_type, title, collectible_key""",
        (class_id, int(user_id)),
    ).fetchall()]
    wallet = db.execute("SELECT * FROM education_student_wallets WHERE class_id = ? AND user_id = ?", (class_id, int(user_id))).fetchone()
    inventory_rows = db.execute("SELECT * FROM education_student_inventory WHERE class_id = ? AND user_id = ?", (class_id, int(user_id))).fetchall()
    inventory_by_key = {row["item_key"]: row for row in inventory_rows}
    def inventory_value(key: str, field: str) -> int:
        row = inventory_by_key.get(key)
        return int(row[field] or 0) if row else 0
    unread_chests = db.execute(
        """SELECT * FROM education_chest_openings
             WHERE class_id = ? AND user_id = ? AND seen_at IS NULL ORDER BY opened_at, id""",
        (class_id, int(user_id)),
    ).fetchall()
    level_events = [item for item in reward_payloads if item["kind"] == "level_up" and item["status"] == "pending"]
    five_choices = [item for item in reward_payloads if item["kind"] == "five_level_choice" and item["status"] == "pending"]
    growth_chests = [item for item in reward_payloads if item["kind"] == "growth_chest"]
    permanent_titles = [item for item in reward_payloads if item["kind"] == "permanent_title"]
    return {
        "enabled": True,
        "settings": settings,
        "profile": profile,
        "achievements": build_achievements(db, class_id, int(user_id)),
        "growth": {
            "badgeTier": profile["badgeTier"],
            "badgeStars": profile["badgeStars"],
            "levelRoadmap": _level_roadmap(profile["level"]),
            "unreadLevelUps": level_events,
            "pendingFiveLevelChoices": five_choices,
            "growthChests": growth_chests,
            "permanentTitles": permanent_titles,
            "collectibles": collectibles,
            "weeklyGoal": _weekly_goal_summary(db, class_id=class_id, user_id=int(user_id), profile=profile, timezone_name=settings["timezone"], now_utc=now_utc),
            "classXp": _class_xp_summary(db, class_id=class_id, timezone_name=settings["timezone"], now_utc=now_utc),
            "stages": _stage_progress_payloads(db, class_id=class_id, user_id=int(user_id)),
        },
        "checkin": _checkin_status(db, class_id=class_id, user_id=int(user_id), timezone_name=settings["timezone"], now_utc=now_utc),
        "wallet": {"balance": int(wallet["gem_balance"]) if wallet else 0, "lifetimeGemsEarned": int(wallet["lifetime_gems_earned"]) if wallet else 0},
        "inventory": {"reviveCard": inventory_value("revive_card", "quantity"), "xpCard": inventory_value("xp_card", "quantity"), "activeXpCards": inventory_value("xp_card", "active_quantity")},
        "unreadCurrencyRewards": [_chest_payload(row) for row in unread_chests],
    }


def build_reward_receipt(
    db: Any,
    *,
    class_row: Any,
    user_id: int,
    xp_delta: int,
    unlocked_achievements: list[dict[str, Any]],
    growth_events: list[dict[str, Any]] | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any] | None:
    settings = settings_payload(class_row)
    if settings["studentExperience"] != "map":
        return None
    profile = build_game_profile(db, class_id=class_row["id"], user_id=int(user_id), weekly_goal=settings["weeklyXpGoal"], timezone_name=settings["timezone"], now_utc=now_utc)
    return {
        "xpDelta": int(xp_delta),
        "totalXp": profile["totalXp"],
        "level": profile["level"],
        "levelXp": profile["levelXp"],
        "nextLevelXp": profile["nextLevelXp"],
        "weeklyXp": profile["weeklyXp"],
        "weeklyGoal": profile["weeklyGoal"],
        "unlockedAchievements": unlocked_achievements,
        "growthEvents": growth_events or [],
    }


def build_leaderboard(db: Any, *, class_id: str, viewer_user_id: int, kind: str) -> dict[str, Any]:
    if kind not in {"xp", "gems"}:
        raise ValueError("invalid_leaderboard")
    if kind == "xp":
        rows = db.execute(
            """SELECT m.user_id, m.student_name, COALESCE(SUM(e.xp_delta), 0) AS score
                 FROM education_memberships m
                 LEFT JOIN education_game_events e ON e.class_id = m.class_id AND e.user_id = m.user_id
                WHERE m.class_id = ? AND m.role = 'student' AND m.removed_at IS NULL
                GROUP BY m.user_id, m.student_name
                ORDER BY score DESC, COALESCE(m.student_name, ''), m.user_id""",
            (class_id,),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT m.user_id, m.student_name, COALESCE(w.lifetime_gems_earned, 0) AS score
                 FROM education_memberships m
                 LEFT JOIN education_student_wallets w ON w.class_id = m.class_id AND w.user_id = m.user_id
                WHERE m.class_id = ? AND m.role = 'student' AND m.removed_at IS NULL
                ORDER BY score DESC, COALESCE(m.student_name, ''), m.user_id""",
            (class_id,),
        ).fetchall()
    entries = [
        {"rank": index + 1, "displayName": row["student_name"] or "同学", "score": int(row["score"] or 0), "isSelf": int(row["user_id"]) == int(viewer_user_id)}
        for index, row in enumerate(rows)
    ]
    return {"kind": kind, "entries": entries}


def set_challenge_unlock_rule(db: Any, *, class_id: str, assignment_id: str, required_level: int | None, required_stage_key: str | None, required_stage_milestone: int | None) -> dict[str, Any]:
    assignment = db.execute("SELECT * FROM education_assignments WHERE id = ?", (assignment_id,)).fetchone()
    if not assignment or assignment["class_id"] != class_id or assignment["assignment_type"] != "direct":
        raise ValueError("growth_challenge_requires_direct_assignment")
    if required_level is not None:
        if isinstance(required_level, bool) or not isinstance(required_level, int) or required_level < 10 or required_level % 10:
            raise ValueError("growth_challenge_level_must_be_a_multiple_of_ten")
    if required_stage_key is not None and (not isinstance(required_stage_key, str) or not required_stage_key.startswith(("source:", "snapshot:"))):
        raise ValueError("invalid_growth_challenge_stage")
    if required_stage_milestone is not None and required_stage_milestone not in {25, 50, 75, 100}:
        raise ValueError("invalid_growth_challenge_milestone")
    if (required_level is None) == (required_stage_key is None):
        raise ValueError("choose_exactly_one_growth_challenge_rule")
    if required_stage_key is None and required_stage_milestone is not None:
        raise ValueError("stage_milestone_requires_stage")
    now = utc_now_iso()
    db.execute(
        """INSERT INTO education_challenge_unlock_rules
             (assignment_id, class_id, required_level, required_stage_key, required_stage_milestone, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON DUPLICATE KEY UPDATE required_level = VALUES(required_level), required_stage_key = VALUES(required_stage_key),
             required_stage_milestone = VALUES(required_stage_milestone), updated_at = VALUES(updated_at)""",
        (assignment_id, class_id, required_level, required_stage_key, required_stage_milestone, now, now),
    )
    return challenge_access_payload(db, class_id=class_id, assignment_id=assignment_id, user_id=None)


def clear_challenge_unlock_rule(db: Any, *, class_id: str, assignment_id: str) -> None:
    db.execute("DELETE FROM education_challenge_unlock_rules WHERE assignment_id = ? AND class_id = ?", (assignment_id, class_id))


def challenge_access_payload(db: Any, *, class_id: str, assignment_id: str, user_id: int | None) -> dict[str, Any]:
    rule = db.execute("SELECT * FROM education_challenge_unlock_rules WHERE assignment_id = ? AND class_id = ?", (assignment_id, class_id)).fetchone()
    if not rule:
        return {"isGrowthChallenge": False, "locked": False}
    payload: dict[str, Any] = {
        "isGrowthChallenge": True,
        "locked": False,
        "requiredLevel": int(rule["required_level"]) if rule["required_level"] is not None else None,
        "requiredStageKey": rule["required_stage_key"],
        "requiredStageMilestone": int(rule["required_stage_milestone"]) if rule["required_stage_milestone"] is not None else None,
    }
    if user_id is None:
        return payload
    if rule["required_level"] is not None:
        level = _sum_total_xp(db, class_id, int(user_id)) // 100 + 1
        payload["locked"] = level < int(rule["required_level"])
    else:
        stage = db.execute(
            """SELECT current_xp, goal_xp FROM education_student_stage_progress
                 WHERE class_id = ? AND user_id = ? AND stage_key = ?""",
            (class_id, int(user_id), rule["required_stage_key"]),
        ).fetchone()
        required_percent = int(rule["required_stage_milestone"] or 75)
        payload["locked"] = not stage or int(stage["current_xp"]) < _stage_thresholds(int(stage["goal_xp"]))[(required_percent // 25) - 1]
    return payload


def is_student_challenge_unlocked(db: Any, *, class_id: str, assignment_id: str, user_id: int) -> bool:
    return not bool(challenge_access_payload(db, class_id=class_id, assignment_id=assignment_id, user_id=int(user_id)).get("locked"))
