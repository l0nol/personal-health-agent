"""提醒的持久化：去重键、冷却期、已读状态、用户反馈。

去重与冷却都在**写入前**判断，而不是写进去再过滤——否则列表里会堆积
用户已经看过的重复提醒。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any

from .repository import add_audit, now_iso

VALID_FEEDBACK = frozenset({"none", "helpful", "acted", "irrelevant"})


def alert_exists(conn: sqlite3.Connection, dedup_key: str) -> bool:
    row = conn.execute("SELECT 1 FROM alerts WHERE dedup_key = ? LIMIT 1", (dedup_key,)).fetchone()
    return row is not None


def last_alert_at(
    conn: sqlite3.Connection, rule_id: str, metric_name: str | None
) -> datetime | None:
    if metric_name is None:
        row = conn.execute(
            "SELECT created_at FROM alerts WHERE rule_id = ? AND metric_name IS NULL"
            " ORDER BY created_at DESC LIMIT 1",
            (rule_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT created_at FROM alerts WHERE rule_id = ? AND metric_name = ?"
            " ORDER BY created_at DESC LIMIT 1",
            (rule_id, metric_name),
        ).fetchone()
    if row is None:
        return None
    try:
        return datetime.fromisoformat(row["created_at"])
    except ValueError:
        return None


def in_cooldown(
    conn: sqlite3.Connection,
    rule_id: str,
    metric_name: str | None,
    cooldown_hours: int,
    *,
    now: datetime | None = None,
) -> bool:
    if cooldown_hours <= 0:
        return False
    last = last_alert_at(conn, rule_id, metric_name)
    if last is None:
        return False
    reference = now or datetime.now().astimezone()
    if last.tzinfo is None:
        last = last.astimezone()
    return reference - last < timedelta(hours=cooldown_hours)


def insert_alert(
    conn: sqlite3.Connection,
    *,
    rule_id: str,
    dedup_key: str,
    window_start: str,
    window_end: str,
    trigger_reason: str,
    metric_name: str | None = None,
    severity: str = "info",
    evidence_record_ids: tuple[str, ...] = (),
    suggested_action: str | None = None,
    cooldown_hours: int = 72,
    commit: bool = True,
) -> str:
    alert_id = uuid.uuid4().hex
    created_at = now_iso()
    cooldown_until = (
        datetime.now().astimezone() + timedelta(hours=cooldown_hours)
    ).isoformat(timespec="seconds")

    conn.execute(
        """
        INSERT INTO alerts (
            alert_id, rule_id, dedup_key, metric_name, severity, window_start, window_end,
            trigger_reason, evidence_json, suggested_action, created_at, cooldown_until
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            alert_id,
            rule_id,
            dedup_key,
            metric_name,
            severity,
            window_start,
            window_end,
            trigger_reason,
            json.dumps(list(evidence_record_ids), ensure_ascii=False),
            suggested_action,
            created_at,
            cooldown_until,
        ),
    )
    add_audit(
        conn,
        "alerts",
        alert_id,
        "create",
        after={"rule_id": rule_id, "metric_name": metric_name, "window": [window_start, window_end]},
    )
    if commit:
        conn.commit()
    return alert_id


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    try:
        item["evidence"] = json.loads(row["evidence_json"] or "[]")
    except (json.JSONDecodeError, TypeError):
        item["evidence"] = []
    return item


def list_alerts(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    unread_only: bool = False,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM alerts"
    if unread_only:
        sql += " WHERE read_at IS NULL"
    sql += " ORDER BY created_at DESC LIMIT ?"
    return [_row_to_dict(row) for row in conn.execute(sql, (limit,)).fetchall()]


def count_unread(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute("SELECT COUNT(*) AS n FROM alerts WHERE read_at IS NULL").fetchone()["n"]
    )


def mark_read(conn: sqlite3.Connection, alert_id: str) -> None:
    conn.execute(
        "UPDATE alerts SET read_at = ? WHERE alert_id = ? AND read_at IS NULL",
        (now_iso(), alert_id),
    )
    conn.commit()


def set_feedback(conn: sqlite3.Connection, alert_id: str, feedback: str) -> None:
    if feedback not in VALID_FEEDBACK:
        raise ValueError(f"反馈值不合法: {feedback!r}")
    conn.execute(
        "UPDATE alerts SET feedback = ?, read_at = COALESCE(read_at, ?) WHERE alert_id = ?",
        (feedback, now_iso(), alert_id),
    )
    add_audit(conn, "alerts", alert_id, "feedback", after={"feedback": feedback})
    conn.commit()
