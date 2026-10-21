"""训练日志的应用服务：先校验，再落库，最后返回领域对象。

这一层是 CLI / 未来 UI 的共同入口。界面不做校验、不拼 SQL，
否则同一条规则会在两个地方各写一遍，早晚漂移。
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from ..storage import training as store
from .errors import E_EMPTY_UPDATE, E_NOT_FOUND, TrainingValidationError
from .models import RecoveryFeedback, TrainingPreferences, TrainingSession
from .validation import (
    normalize_body_parts,
    parse_date,
    parse_optional_int,
    validate_activity_type,
    validate_completion,
    validate_days_per_week,
    validate_soreness,
    validate_started_at,
    validate_text_list,
    validate_time_budget,
)

_SESSION_EDITABLE = {
    "session_date",
    "started_at",
    "activity_type",
    "body_parts",
    "content",
    "duration_minutes",
    "perceived_intensity",
    "completion",
    "notes",
}


def add_session(
    conn: sqlite3.Connection,
    *,
    session_date: str | date,
    activity_type: str,
    body_parts: str | list[str] | tuple[str, ...] | None = None,
    content: str = "",
    duration_minutes: object = None,
    perceived_intensity: object = None,
    completion: str = "completed",
    started_at: str | None = None,
    notes: str | None = None,
    today: date | None = None,
) -> TrainingSession:
    day = parse_date(session_date, field="训练日期", today=today)
    activity = validate_activity_type(activity_type)
    parts = normalize_body_parts(body_parts)
    duration = parse_optional_int(duration_minutes, field="时长（分钟）", low=1, high=600)
    intensity = parse_optional_int(perceived_intensity, field="主观强度", low=1, high=10)
    completed = validate_completion(completion)
    started = validate_started_at(started_at, session_date=day)

    session_id = store.insert_session(
        conn,
        session_date=day.isoformat(),
        activity_type=activity,
        body_parts=parts,
        content=(content or "").strip(),
        duration_minutes=duration,
        perceived_intensity=intensity,
        completion=completed,
        started_at=started,
        notes=(notes or "").strip() or None,
    )
    session = store.get_session(conn, session_id)
    assert session is not None
    return session


def edit_session(
    conn: sqlite3.Connection, session_id: str, **changes: Any
) -> TrainingSession:
    """局部更新。未提供的字段保持原值；显式传 None 表示"清空这个字段"。"""
    unknown = set(changes) - _SESSION_EDITABLE
    if unknown:
        raise TrainingValidationError(
            E_EMPTY_UPDATE, f"不支持修改的字段: {', '.join(sorted(unknown))}"
        )
    if not changes:
        raise TrainingValidationError(E_EMPTY_UPDATE, "没有需要修改的字段")

    current = store.get_session(conn, session_id)
    if current is None:
        raise TrainingValidationError(E_NOT_FOUND, f"训练记录不存在: {session_id}")

    normalized: dict[str, Any] = {}
    if "session_date" in changes:
        normalized["session_date"] = parse_date(changes["session_date"], field="训练日期").isoformat()
    if "started_at" in changes:
        day = date.fromisoformat(normalized.get("session_date", current.session_date))
        normalized["started_at"] = validate_started_at(changes["started_at"], session_date=day)
    if "activity_type" in changes:
        normalized["activity_type"] = validate_activity_type(changes["activity_type"])
    if "body_parts" in changes:
        normalized["body_parts"] = ",".join(normalize_body_parts(changes["body_parts"]))
    if "content" in changes:
        normalized["content"] = (changes["content"] or "").strip()
    if "duration_minutes" in changes:
        normalized["duration_minutes"] = parse_optional_int(
            changes["duration_minutes"], field="时长（分钟）", low=1, high=600
        )
    if "perceived_intensity" in changes:
        normalized["perceived_intensity"] = parse_optional_int(
            changes["perceived_intensity"], field="主观强度", low=1, high=10
        )
    if "completion" in changes:
        normalized["completion"] = validate_completion(changes["completion"])
    if "notes" in changes:
        normalized["notes"] = (changes["notes"] or "").strip() or None

    store.update_session(conn, session_id, normalized)
    updated = store.get_session(conn, session_id)
    assert updated is not None
    return updated


def remove_session(conn: sqlite3.Connection, session_id: str) -> None:
    if store.get_session(conn, session_id) is None:
        raise TrainingValidationError(E_NOT_FOUND, f"训练记录不存在: {session_id}")
    store.delete_session(conn, session_id)


def record_feedback(
    conn: sqlite3.Connection,
    *,
    feedback_date: str | date,
    energy: object = None,
    fatigue: object = None,
    soreness_parts: str | list[str] | None = None,
    soreness_level: object = None,
    discomfort: bool = False,
    discomfort_note: str | None = None,
    today: date | None = None,
) -> RecoveryFeedback:
    day = parse_date(feedback_date, field="反馈日期", today=today)
    parsed_energy = parse_optional_int(energy, field="精力", low=1, high=5)
    parsed_fatigue = parse_optional_int(fatigue, field="疲劳", low=1, high=5)
    parts, level = validate_soreness(soreness_parts, soreness_level)

    feedback_id = store.upsert_feedback(
        conn,
        feedback_date=day.isoformat(),
        energy=parsed_energy,
        fatigue=parsed_fatigue,
        soreness_parts=parts,
        soreness_level=level,
        discomfort=bool(discomfort),
        discomfort_note=(discomfort_note or "").strip() or None,
    )
    row = store.get_feedback_by_id(conn, feedback_id)
    assert row is not None
    return row


def save_preferences(
    conn: sqlite3.Connection,
    *,
    equipment: str | list[str] | None = None,
    days_per_week: object = None,
    time_budget_minutes: object = None,
    avoid_movements: str | list[str] | None = None,
) -> TrainingPreferences:
    store.save_preferences(
        conn,
        equipment=validate_text_list(equipment, field="可用器材"),
        days_per_week=validate_days_per_week(days_per_week),
        time_budget_minutes=validate_time_budget(time_budget_minutes),
        avoid_movements=validate_text_list(avoid_movements, field="希望避免的动作"),
    )
    return store.get_preferences(conn)
