"""训练日志的数据访问层。

与 `storage/repository.py` 的分工：
- `records` 存设备观测，`training_sessions` / `recovery_feedback` 存用户主观记录；
- 两者不共享键、不共享状态机、不互相填充；
- SQL 只允许出现在 storage 包内，训练模块不直接写 SQL。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, timedelta
from typing import Any

from ..training.models import RecoveryFeedback, TrainingPreferences, TrainingSession
from .repository import add_audit, now_iso


def _parts_to_text(parts: tuple[str, ...]) -> str:
    return ",".join(parts)


def _text_to_parts(text: str | None) -> tuple[str, ...]:
    if not text:
        return ()
    return tuple(part for part in text.split(",") if part)


def _session_from_row(row: sqlite3.Row) -> TrainingSession:
    return TrainingSession(
        session_id=row["session_id"],
        session_date=row["session_date"],
        started_at=row["started_at"],
        activity_type=row["activity_type"],
        body_parts=_text_to_parts(row["body_parts"]),
        content=row["content"] or "",
        duration_minutes=row["duration_minutes"],
        perceived_intensity=row["perceived_intensity"],
        completion=row["completion"],
        source=row["source"],
        notes=row["notes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _feedback_from_row(row: sqlite3.Row) -> RecoveryFeedback:
    return RecoveryFeedback(
        feedback_id=row["feedback_id"],
        feedback_date=row["feedback_date"],
        energy=row["energy"],
        fatigue=row["fatigue"],
        soreness_parts=_text_to_parts(row["soreness_parts"]),
        soreness_level=row["soreness_level"],
        discomfort=bool(row["discomfort"]),
        discomfort_note=row["discomfort_note"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        source=row["source"],
    )


# ---------------- 训练场次 ----------------


def insert_session(
    conn: sqlite3.Connection,
    *,
    session_date: str,
    activity_type: str,
    body_parts: tuple[str, ...] = (),
    content: str = "",
    duration_minutes: int | None = None,
    perceived_intensity: int | None = None,
    completion: str = "completed",
    started_at: str | None = None,
    source: str = "manual",
    notes: str | None = None,
    commit: bool = True,
) -> str:
    session_id = uuid.uuid4().hex
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO training_sessions (
            session_id, session_date, started_at, activity_type, body_parts, content,
            duration_minutes, perceived_intensity, completion, source, notes,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            session_date,
            started_at,
            activity_type,
            _parts_to_text(body_parts),
            content,
            duration_minutes,
            perceived_intensity,
            completion,
            source,
            notes,
            ts,
            ts,
        ),
    )
    add_audit(
        conn,
        "training_sessions",
        session_id,
        "insert",
        after={
            "session_date": session_date,
            "activity_type": activity_type,
            "body_parts": list(body_parts),
            "duration_minutes": duration_minutes,
            "perceived_intensity": perceived_intensity,
            "completion": completion,
        },
    )
    if commit:
        conn.commit()
    return session_id


def get_session(conn: sqlite3.Connection, session_id: str) -> TrainingSession | None:
    row = conn.execute(
        "SELECT * FROM training_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    return _session_from_row(row) if row else None


def list_sessions(
    conn: sqlite3.Connection,
    *,
    start: str | date | None = None,
    end: str | date | None = None,
    limit: int = 200,
) -> list[TrainingSession]:
    sql = "SELECT * FROM training_sessions WHERE 1 = 1"
    params: list[Any] = []
    if start is not None:
        sql += " AND session_date >= ?"
        params.append(start if isinstance(start, str) else start.isoformat())
    if end is not None:
        sql += " AND session_date <= ?"
        params.append(end if isinstance(end, str) else end.isoformat())
    sql += " ORDER BY session_date DESC, COALESCE(started_at, '') DESC, created_at DESC LIMIT ?"
    params.append(limit)
    return [_session_from_row(row) for row in conn.execute(sql, params).fetchall()]


def count_sessions(
    conn: sqlite3.Connection, *, start: str | date | None = None, end: str | date | None = None
) -> int:
    sql = "SELECT COUNT(*) AS n FROM training_sessions WHERE 1 = 1"
    params: list[Any] = []
    if start is not None:
        sql += " AND session_date >= ?"
        params.append(start if isinstance(start, str) else start.isoformat())
    if end is not None:
        sql += " AND session_date <= ?"
        params.append(end if isinstance(end, str) else end.isoformat())
    return int(conn.execute(sql, params).fetchone()["n"])


def latest_session_date(conn: sqlite3.Connection) -> date | None:
    row = conn.execute("SELECT MAX(session_date) AS d FROM training_sessions").fetchone()
    return date.fromisoformat(row["d"]) if row and row["d"] else None


def update_session(
    conn: sqlite3.Connection,
    session_id: str,
    changes: dict[str, Any],
    *,
    commit: bool = True,
) -> None:
    """按字段局部更新。调用方负责先校验，本函数只写库并留审计。"""
    row = conn.execute(
        "SELECT * FROM training_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        raise KeyError(session_id)

    before = {key: row[key] for key in row.keys() if key not in {"created_at", "updated_at"}}
    assignments = ", ".join(f"{column} = ?" for column in changes)
    conn.execute(
        f"UPDATE training_sessions SET {assignments}, updated_at = ? WHERE session_id = ?",  # noqa: S608 - 列名来自固定白名单
        [*changes.values(), now_iso(), session_id],
    )
    after_row = conn.execute(
        "SELECT * FROM training_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    after = {key: after_row[key] for key in after_row.keys() if key not in {"created_at", "updated_at"}}
    add_audit(conn, "training_sessions", session_id, "update", before=before, after=after)
    if commit:
        conn.commit()


def delete_session(
    conn: sqlite3.Connection, session_id: str, *, commit: bool = True
) -> None:
    row = conn.execute(
        "SELECT * FROM training_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        raise KeyError(session_id)
    conn.execute("DELETE FROM training_sessions WHERE session_id = ?", (session_id,))
    add_audit(
        conn,
        "training_sessions",
        session_id,
        "delete",
        before={
            "session_date": row["session_date"],
            "activity_type": row["activity_type"],
            "body_parts": row["body_parts"],
        },
    )
    if commit:
        conn.commit()


# ---------------- 恢复反馈 ----------------


def upsert_feedback(
    conn: sqlite3.Connection,
    *,
    feedback_date: str,
    energy: int | None = None,
    fatigue: int | None = None,
    soreness_parts: tuple[str, ...] = (),
    soreness_level: int | None = None,
    discomfort: bool = False,
    discomfort_note: str | None = None,
    commit: bool = True,
) -> str:
    """一天最多一条：同一天重复提交是"更新"，不是"再记一条"。

    这样"昨天的疲劳"不会被今天覆盖，也不会出现两条互相矛盾的反馈。
    """
    existing = conn.execute(
        "SELECT * FROM recovery_feedback WHERE feedback_date = ?", (feedback_date,)
    ).fetchone()
    ts = now_iso()
    if existing is None:
        feedback_id = uuid.uuid4().hex
        conn.execute(
            """
            INSERT INTO recovery_feedback (
                feedback_id, feedback_date, energy, fatigue, soreness_parts,
                soreness_level, discomfort, discomfort_note, source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'subjective', ?, ?)
            """,
            (
                feedback_id,
                feedback_date,
                energy,
                fatigue,
                _parts_to_text(soreness_parts),
                soreness_level,
                int(discomfort),
                discomfort_note,
                ts,
                ts,
            ),
        )
        add_audit(
            conn,
            "recovery_feedback",
            feedback_id,
            "insert",
            after={"feedback_date": feedback_date, "energy": energy, "fatigue": fatigue},
        )
    else:
        feedback_id = existing["feedback_id"]
        conn.execute(
            """
            UPDATE recovery_feedback
               SET energy = ?, fatigue = ?, soreness_parts = ?, soreness_level = ?,
                   discomfort = ?, discomfort_note = ?, updated_at = ?
             WHERE feedback_id = ?
            """,
            (
                energy,
                fatigue,
                _parts_to_text(soreness_parts),
                soreness_level,
                int(discomfort),
                discomfort_note,
                ts,
                feedback_id,
            ),
        )
        add_audit(
            conn,
            "recovery_feedback",
            feedback_id,
            "update",
            before={
                "energy": existing["energy"],
                "fatigue": existing["fatigue"],
                "soreness_level": existing["soreness_level"],
                "discomfort": existing["discomfort"],
            },
            after={
                "energy": energy,
                "fatigue": fatigue,
                "soreness_level": soreness_level,
                "discomfort": int(discomfort),
            },
        )
    if commit:
        conn.commit()
    return feedback_id


def get_feedback(conn: sqlite3.Connection, feedback_date: str | date) -> RecoveryFeedback | None:
    key = feedback_date if isinstance(feedback_date, str) else feedback_date.isoformat()
    row = conn.execute(
        "SELECT * FROM recovery_feedback WHERE feedback_date = ?", (key,)
    ).fetchone()
    return _feedback_from_row(row) if row else None


def get_feedback_by_id(conn: sqlite3.Connection, feedback_id: str) -> RecoveryFeedback | None:
    row = conn.execute(
        "SELECT * FROM recovery_feedback WHERE feedback_id = ?", (feedback_id,)
    ).fetchone()
    return _feedback_from_row(row) if row else None


def latest_feedback(
    conn: sqlite3.Connection, end: date, *, days: int = 3
) -> RecoveryFeedback | None:
    start = end - timedelta(days=days - 1)
    row = conn.execute(
        """
        SELECT * FROM recovery_feedback
         WHERE feedback_date BETWEEN ? AND ?
         ORDER BY feedback_date DESC
         LIMIT 1
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchone()
    return _feedback_from_row(row) if row else None


def list_feedback(
    conn: sqlite3.Connection, *, start: str | date | None = None, end: str | date | None = None
) -> list[RecoveryFeedback]:
    sql = "SELECT * FROM recovery_feedback WHERE 1 = 1"
    params: list[Any] = []
    if start is not None:
        sql += " AND feedback_date >= ?"
        params.append(start if isinstance(start, str) else start.isoformat())
    if end is not None:
        sql += " AND feedback_date <= ?"
        params.append(end if isinstance(end, str) else end.isoformat())
    sql += " ORDER BY feedback_date DESC"
    return [_feedback_from_row(row) for row in conn.execute(sql, params).fetchall()]


# ---------------- 训练偏好 ----------------


def get_preferences(conn: sqlite3.Connection) -> TrainingPreferences:
    row = conn.execute("SELECT * FROM training_preferences WHERE id = 1").fetchone()
    if row is None:
        return TrainingPreferences()
    return TrainingPreferences(
        equipment=tuple(json.loads(row["equipment_json"] or "[]")),
        days_per_week=row["days_per_week"],
        time_budget_minutes=row["time_budget_minutes"],
        avoid_movements=tuple(json.loads(row["avoid_json"] or "[]")),
        updated_at=row["updated_at"],
    )


def save_preferences(
    conn: sqlite3.Connection,
    *,
    equipment: tuple[str, ...] = (),
    days_per_week: int | None = None,
    time_budget_minutes: int | None = None,
    avoid_movements: tuple[str, ...] = (),
    commit: bool = True,
) -> None:
    before = get_preferences(conn)
    conn.execute(
        """
        INSERT INTO training_preferences
            (id, equipment_json, days_per_week, time_budget_minutes, avoid_json, updated_at)
        VALUES (1, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            equipment_json = excluded.equipment_json,
            days_per_week = excluded.days_per_week,
            time_budget_minutes = excluded.time_budget_minutes,
            avoid_json = excluded.avoid_json,
            updated_at = excluded.updated_at
        """,
        (
            json.dumps(list(equipment), ensure_ascii=False),
            days_per_week,
            time_budget_minutes,
            json.dumps(list(avoid_movements), ensure_ascii=False),
            now_iso(),
        ),
    )
    after = get_preferences(conn)
    add_audit(
        conn,
        "training_preferences",
        "global",
        "update",
        before={"days_per_week": before.days_per_week, "time_budget": before.time_budget_minutes},
        after={"days_per_week": after.days_per_week, "time_budget": after.time_budget_minutes},
    )
    if commit:
        conn.commit()
