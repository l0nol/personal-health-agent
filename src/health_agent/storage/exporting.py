"""导出与删除：用户对自己的数据有完全的取走权与删除权（规格 §8）。"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from typing import Any

from .repository import add_audit, get_record, now_iso

EXPORT_COLUMNS: tuple[str, ...] = (
    "record_id",
    "record_date",
    "metric_name",
    "value",
    "unit",
    "raw_value",
    "raw_unit",
    "period_type",
    "period_start",
    "period_end",
    "source_app",
    "source_page",
    "extraction_method",
    "verification_status",
    "quality_flags",
    "batch_id",
    "notes",
    "captured_at",
    "created_at",
)


def _fetch(conn: sqlite3.Connection, status: str | None) -> list[sqlite3.Row]:
    sql = f"SELECT {', '.join(EXPORT_COLUMNS)} FROM records"
    params: list[Any] = []
    if status:
        sql += " WHERE verification_status = ?"
        params.append(status)
    sql += " ORDER BY record_date, metric_name"
    return conn.execute(sql, params).fetchall()


def export_csv(conn: sqlite3.Connection, *, status: str | None = None) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(EXPORT_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for row in _fetch(conn, status):
        writer.writerow({column: row[column] for column in EXPORT_COLUMNS})
    return buffer.getvalue()


def export_json(conn: sqlite3.Connection, *, status: str | None = None) -> str:
    records = [{column: row[column] for column in EXPORT_COLUMNS} for row in _fetch(conn, status)]
    return json.dumps(
        {
            "schema_version": "1.0",
            "exported_at": now_iso(),
            "records": records,
            # 训练日志与恢复反馈是主观记录，单独成段，不与设备指标混在 records 里。
            # 只加键、不改 schema_version：消费方按需读取，旧脚本不受影响。
            "training_sessions": _training_sessions(conn),
            "recovery_feedback": _recovery_feedback(conn),
            "training_preferences": _training_preferences(conn),
        },
        ensure_ascii=False,
        indent=2,
    )


TRAINING_COLUMNS: tuple[str, ...] = (
    "session_id",
    "session_date",
    "started_at",
    "activity_type",
    "body_parts",
    "content",
    "duration_minutes",
    "perceived_intensity",
    "completion",
    "source",
    "notes",
    "created_at",
    "updated_at",
)

FEEDBACK_COLUMNS: tuple[str, ...] = (
    "feedback_id",
    "feedback_date",
    "energy",
    "fatigue",
    "soreness_parts",
    "soreness_level",
    "discomfort",
    "discomfort_note",
    "source",
    "created_at",
    "updated_at",
)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _training_sessions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _has_table(conn, "training_sessions"):
        return []
    rows = conn.execute(
        f"SELECT {', '.join(TRAINING_COLUMNS)} FROM training_sessions ORDER BY session_date, created_at"  # noqa: S608
    ).fetchall()
    return [{column: row[column] for column in TRAINING_COLUMNS} for row in rows]


def _recovery_feedback(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _has_table(conn, "recovery_feedback"):
        return []
    rows = conn.execute(
        f"SELECT {', '.join(FEEDBACK_COLUMNS)} FROM recovery_feedback ORDER BY feedback_date"  # noqa: S608
    ).fetchall()
    return [{column: row[column] for column in FEEDBACK_COLUMNS} for row in rows]


def _training_preferences(conn: sqlite3.Connection) -> dict[str, Any] | None:
    if not _has_table(conn, "training_preferences"):
        return None
    row = conn.execute("SELECT * FROM training_preferences WHERE id = 1").fetchone()
    if row is None:
        return None
    return {
        "equipment": json.loads(row["equipment_json"] or "[]"),
        "days_per_week": row["days_per_week"],
        "time_budget_minutes": row["time_budget_minutes"],
        "avoid_movements": json.loads(row["avoid_json"] or "[]"),
    }


def export_training_csv(conn: sqlite3.Connection) -> str:
    """训练日志单独一份 CSV。列与 JSON 的 training_sessions 一致。"""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(TRAINING_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for row in _training_sessions(conn):
        writer.writerow(row)
    return buffer.getvalue()


def delete_record(conn: sqlite3.Connection, record_id: str, *, actor: str = "local_user") -> None:
    """物理删除一条记录。审计里保留"删过什么"，但数据本身不再存在。"""
    row = get_record(conn, record_id)
    if row is None:
        raise KeyError(record_id)
    before = {
        "record_id": row["record_id"],
        "metric_name": row["metric_name"],
        "record_date": row["record_date"],
        "value": row["value"],
        "status": row["verification_status"],
    }
    conn.execute("DELETE FROM records WHERE record_id = ?", (record_id,))
    add_audit(conn, "records", record_id, "delete", before=before, actor=actor)
    conn.commit()
