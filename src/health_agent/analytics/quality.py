"""数据质量：有多少待确认、多少带标记、各指标覆盖率如何。

质量信息必须和趋势一起展示——不然"趋势"看起来正常，实际只是三天数据被平均了。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from ..catalog import REPORT_METRICS
from .aggregate import STATUS_OK, MetricSummary, summarize


@dataclass(frozen=True, slots=True)
class QualityReport:
    window_start: str
    window_end: str
    total: int
    by_status: dict[str, int]
    flagged: int
    flag_counts: dict[str, int]
    coverage: tuple[MetricSummary, ...]

    @property
    def confirmed(self) -> int:
        return self.by_status.get("confirmed", 0)

    @property
    def pending(self) -> int:
        return self.by_status.get("pending", 0)

    @property
    def confirmed_ratio(self) -> float:
        return round(self.confirmed / self.total, 4) if self.total else 0.0

    @property
    def gaps(self) -> tuple[MetricSummary, ...]:
        """覆盖率不足以支撑结论的指标。"""
        return tuple(item for item in self.coverage if item.status != STATUS_OK)


def _flag_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT quality_flags FROM records WHERE quality_flags != '[]'"
    ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        try:
            flags = json.loads(row["quality_flags"])
        except (json.JSONDecodeError, TypeError):
            continue
        for flag in flags:
            counts[flag] = counts.get(flag, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))


def summarize_quality(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    metrics: list[str] | tuple[str, ...] = REPORT_METRICS,
) -> QualityReport:
    by_status = {
        row["verification_status"]: row["n"]
        for row in conn.execute(
            "SELECT verification_status, COUNT(*) AS n FROM records GROUP BY verification_status"
        ).fetchall()
    }
    total = int(sum(by_status.values()))
    flagged = conn.execute(
        "SELECT COUNT(*) AS n FROM records WHERE quality_flags != '[]'"
    ).fetchone()["n"]

    return QualityReport(
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        total=total,
        by_status=by_status,
        flagged=int(flagged),
        flag_counts=_flag_counts(conn),
        coverage=tuple(summarize(conn, metric, start, end) for metric in metrics),
    )
