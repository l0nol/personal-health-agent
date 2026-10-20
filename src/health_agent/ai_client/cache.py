"""报告文案缓存。

同一份摘要 + 同一提示词 + 同一模型 = 同一份文案，没必要重复付费。
键里带上提示词版本，改提示词后旧缓存自动失效。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

from ..storage.repository import now_iso


def make_cache_key(*, digest: str, prompt_version: str, model: str) -> str:
    payload = f"{prompt_version}|{model}|{digest}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def summary_digest(summary: dict) -> str:
    return hashlib.sha256(
        json.dumps(summary, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:32]


def get_cached(conn: sqlite3.Connection, cache_key: str) -> dict | None:
    row = conn.execute(
        "SELECT payload_json, ai_model, prompt_version FROM report_cache WHERE cache_key = ?",
        (cache_key,),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or not payload.get("text"):
        return None
    return payload


def put_cached(
    conn: sqlite3.Connection,
    *,
    cache_key: str,
    report_type: str,
    period_start: str,
    period_end: str,
    digest: str,
    text: str,
    model: str,
    prompt_version: str,
    commit: bool = True,
) -> None:
    conn.execute(
        """
        INSERT INTO report_cache (
            cache_key, report_type, period_start, period_end, source_digest,
            payload_json, ai_model, prompt_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            payload_json = excluded.payload_json,
            created_at = excluded.created_at
        """,
        (
            cache_key,
            report_type,
            period_start,
            period_end,
            digest,
            json.dumps({"text": text}, ensure_ascii=False),
            model,
            prompt_version,
            now_iso(),
        ),
    )
    if commit:
        conn.commit()
