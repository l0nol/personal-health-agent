"""AI 助手的对话记录：只存"界面上要显示的东西"，不存 prompt 与原始响应。

三条取舍：

1. **存哪儿**：复用 `settings` 表里一个内部键，而不是新建表。
   对话是界面状态，不是"设置变更"；走 `preferences.set_settings()` 会把整段对话
   写进 `audit_log` 的 before/after（每次提问一条，还带着健康描述），
   所以这里自己读写这一个键，刻意绕开审计。
2. **存多少**：只留最近 `MAX_TURNS` 轮，超出就从头丢。
   对话本身没有长期价值，无限增长只会让备份包越来越胖。
3. **丢了怎么办**：读坏了、超时了、库被换过——一律当"没有历史"处理，
   绝不让页面因为一段历史 500。助手是可以从零开始用的。
"""

from __future__ import annotations

import json
import sqlite3

from ..storage.repository import now_iso

KEY = "assistant_chat"

#: 保留的对话轮数（一问一答算两轮）。够回溯上下文，又不至于把 prompt 撑爆。
MAX_TURNS = 16

#: 单条消息入库前的截断长度。模型偶尔会写超长段落，没必要原样留着。
MAX_TEXT = 2000


def _row(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value_json FROM settings WHERE key = ?", (KEY,)).fetchone()
    if row is None:
        return {}
    try:
        data = json.loads(row["value_json"])
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def load(conn: sqlite3.Connection) -> list[dict]:
    """取回对话记录。任何异常都返回空列表——历史丢了不影响继续用。"""
    data = _row(conn)
    turns = data.get("turns")
    if not isinstance(turns, list):
        return []
    return [turn for turn in turns if isinstance(turn, dict) and turn.get("role") in {"user", "assistant"}]


def append(conn: sqlite3.Connection, *turns: dict) -> None:
    """追加若干轮，并裁到 `MAX_TURNS`。"""
    if not turns:
        return
    current = load(conn)
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        text = str(turn.get("text") or "")[:MAX_TEXT]
        current.append(
            {
                "role": "user" if turn.get("role") == "user" else "assistant",
                "text": text,
                "at": now_iso(),
                "source": str(turn.get("source") or "")[:16],
                "model": str(turn.get("model") or "")[:64],
                "note": str(turn.get("note") or "")[:300],
                "refs": [ref for ref in (turn.get("refs") or []) if isinstance(ref, dict)][:6],
                "card": turn.get("card") if isinstance(turn.get("card"), dict) else None,
            }
        )
    payload = json.dumps({"updated_at": now_iso(), "turns": current[-MAX_TURNS:]}, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO settings (key, value_json, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value_json = excluded.value_json, updated_at = excluded.updated_at
        """,
        (KEY, payload, now_iso()),
    )
    conn.commit()


def clear(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM settings WHERE key = ?", (KEY,))
    conn.commit()


def history_for_model(turns: list[dict], *, limit: int = 8, max_chars: int = 600) -> list[dict]:
    """把对话裁成能塞进 prompt 的几轮。只取正文，不带卡片与链接。"""
    picked = [turn for turn in turns if (turn.get("text") or "").strip()][-limit:]
    return [
        {"role": turn["role"], "content": str(turn["text"])[:max_chars]}
        for turn in picked
    ]
