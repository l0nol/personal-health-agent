"""去重与冲突判定用的键。

两个键的职责必须分清（混用会导致"改了值就当成新记录"，或"不同视图被误判重复"）：

  ``logical_key`` —— 同一"槽位"。同一来源 + 同一天 + 同一指标 + 同一统计周期
                     + 同一周期起止，视为同一槽位。槽位相同但值不同 = **冲突**。
  ``dedup_key``   —— 同一个"值"。在槽位基础上再叠加单位与数值，完全相同 = 重复。

刻意不把 ``source_page`` 纳入键：同一指标的日视图与周视图若都带日期，
用 ``period_type`` 区分已经足够；把视图名纳入键会让同一数据被重复入库。
"""

from __future__ import annotations

import hashlib

_KEY_LEN = 32


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_KEY_LEN]


def _normalize_value(value: float | None) -> str:
    if value is None:
        return "NULL"
    # 收敛到 6 位小数：6761 与 6761.0 必须是同一个值，
    # 但 47.1234 与 47.1235 这种真实差异不能被合并掉。
    return f"{round(float(value), 6):.6f}"


def logical_key(
    *,
    source_app: str,
    record_date: str,
    metric_name: str,
    period_type: str,
    period_start: str | None = None,
    period_end: str | None = None,
) -> str:
    return _digest(
        "|".join(
            [
                source_app,
                record_date,
                metric_name,
                period_type,
                period_start or "",
                period_end or "",
            ]
        )
    )


def dedup_key(logical: str, *, unit: str, value: float | None) -> str:
    return _digest(f"{logical}|{unit}|{_normalize_value(value)}")
