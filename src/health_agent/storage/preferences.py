"""可选生活背景设置。

规格 §2：可选设置不得强制填写。所以这里没有"必填"，没有默认值，
没有任何一条业务逻辑依赖某个键存在。空着系统照常工作。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from .repository import add_audit, now_iso

#: 允许的键。写入未知键直接忽略，避免设置表被当成任意 KV 存储滥用。
ALLOWED_KEYS: tuple[str, ...] = (
    "wake_time",           # 大致起床时间，如 "07:00"
    "sleep_time",          # 大致就寝时间，如 "23:30"
    "work_schedule",       # 课程/工作时间，如 "周一至周五 8:00-17:00"
    "habit_goals",         # 习惯目标，如 "每天走 6000 步"
    "reminder_frequency",  # 提醒频率：daily / weekly / off
    "notes",               # 补充说明（自由文本）
)

#: 界面状态类键：由 UI 自己读写，不出现在"生活背景"表单里。
#: 单独列出是为了避免用户保存设置时被空值清掉。
INTERNAL_KEYS: tuple[str, ...] = ("ui_mode",)

WRITABLE_KEYS: frozenset[str] = frozenset(ALLOWED_KEYS) | frozenset(INTERNAL_KEYS)


def get_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT key, value_json FROM settings").fetchall()
    result: dict[str, Any] = {}
    for row in rows:
        try:
            result[row["key"]] = json.loads(row["value_json"])
        except json.JSONDecodeError:
            # 坏数据不炸整个页面，原样返回字符串并允许用户覆盖
            result[row["key"]] = row["value_json"]
    return result


def set_settings(conn: sqlite3.Connection, values: Mapping[str, Any]) -> dict[str, Any]:
    """写入设置。空字符串 / None 表示"清空这个键"，不是"存一个空值"。"""
    before = get_settings(conn)
    for key, value in values.items():
        if key not in WRITABLE_KEYS:
            continue
        if value is None or (isinstance(value, str) and value.strip() == ""):
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            conn.execute(
                """
                INSERT INTO settings (key, value_json, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), now_iso()),
            )
    after = get_settings(conn)
    if before != after:
        add_audit(conn, "settings", "global", "update", before=before, after=after)
    conn.commit()
    return after
