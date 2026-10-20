"""统计层：按指标聚合出可追溯的窗口摘要。

三条硬规则：
1. 只取 `confirmed` 且 `period_type IN ('day','instant')` 的记录。
   week / month 记录是设备给的聚合值，混进日趋势会重复计数。
2. 每个聚合值都带 `record_ids`——报告里出现的数字必须能点回原始记录。
3. 样本不足就明说"数据不足"，不给平均值，不编趋势。

第一版数据量在万行以内、窗口只有 7–28 天，用标准库做精确计算；
不引入 pandas（详见 docs/00-architecture-plan-v1.md §15 修订记录）。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean, median

from ..catalog import MetricSpec, get_metric

#: 少于这么多天有效记录，就不下结论。
MIN_SAMPLE_DAYS = 3
#: 一周内有效天数不足这个数，周报只报"数据不足"。
MIN_WEEK_COVERAGE_DAYS = 4

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient"
STATUS_NO_DATA = "no_data"


@dataclass(frozen=True, slots=True)
class Point:
    record_date: str
    value: float
    record_id: str


@dataclass(frozen=True, slots=True)
class MetricSummary:
    metric_name: str
    display: str
    unit: str
    window_start: str
    window_end: str
    window_days: int
    sample_days: int
    missing_days: int
    missing_rate: float
    mean: float | None
    median: float | None
    min: float | None
    max: float | None
    status: str
    points: tuple[Point, ...]

    @property
    def record_ids(self) -> tuple[str, ...]:
        return tuple(point.record_id for point in self.points)

    @property
    def has_data(self) -> bool:
        return self.sample_days > 0

    @property
    def usable(self) -> bool:
        """够不够用来下趋势结论。"""
        return self.status == STATUS_OK


def _iso(day: date) -> str:
    return day.isoformat()


def _iso_arg(value: str | date) -> str:
    """SQL 比较前统一成字符串：日期适配器已弃用，别把 date 直接交给 sqlite3。"""
    return value if isinstance(value, str) else value.isoformat()


def week_bounds(day: date) -> tuple[date, date]:
    """周一为周首、周日为周末（ISO 周）。"""
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def load_points(
    conn: sqlite3.Connection,
    metric_name: str,
    start: str | date,
    end: str | date,
) -> list[Point]:
    """取窗口内已确认的日/瞬时点位。

    `start` / `end` 允许传 `date`（UI 的 /sleep、/trends 就是这么调的），
    这里统一转成 ISO 字符串再进 SQL——**不能依赖 sqlite3 的默认日期适配器**：
    它自 Python 3.12 起已弃用，移除后传 date 会直接抛 TypeError。
    """
    rows = conn.execute(
        """
        SELECT record_date, value, record_id
          FROM records
         WHERE metric_name = ?
           AND verification_status = 'confirmed'
           AND period_type IN ('day', 'instant')
           AND value IS NOT NULL
           AND record_date BETWEEN ? AND ?
         ORDER BY record_date, created_at, rowid
        """,
        (metric_name, _iso_arg(start), _iso_arg(end)),
    ).fetchall()

    # 同一天可能有多条（血压、体温这类瞬时测量），只取最后一条，
    # 避免"一天两个值"被平均成中间值而失真。
    latest: dict[str, Point] = {}
    for row in rows:
        latest[row["record_date"]] = Point(
            record_date=row["record_date"],
            value=float(row["value"]),
            record_id=row["record_id"],
        )
    return [latest[key] for key in sorted(latest)]


def tracked_metrics(
    conn: sqlite3.Connection,
    metrics: list[str] | tuple[str, ...],
    start: date,
    end: date,
) -> frozenset[str]:
    """窗口内有任何已确认数据的指标。

    报告只对"用户确实在记"的指标提示缺口；从没记过的指标不该每天出现在
    "当日无数据"里——那是噪音，不是信息。
    """
    if not metrics:
        return frozenset()
    placeholders = ",".join("?" for _ in metrics)
    rows = conn.execute(
        f"""
        SELECT DISTINCT metric_name FROM records
         WHERE verification_status = 'confirmed'
           AND value IS NOT NULL
           AND period_type IN ('day', 'instant')
           AND record_date BETWEEN ? AND ?
           AND metric_name IN ({placeholders})
        """,
        [start.isoformat(), end.isoformat(), *metrics],
    ).fetchall()
    return frozenset(row["metric_name"] for row in rows)


def summarize(
    conn: sqlite3.Connection,
    metric: str | MetricSpec,
    start: date,
    end: date,
) -> MetricSummary:
    spec = get_metric(metric) if isinstance(metric, str) else metric
    start_s, end_s = _iso(start), _iso(end)
    window_days = (end - start).days + 1
    points = load_points(conn, spec.name, start_s, end_s)
    values = [point.value for point in points]
    sample_days = len(values)
    missing_days = max(window_days - sample_days, 0)

    if sample_days == 0:
        status = STATUS_NO_DATA
    elif sample_days < MIN_SAMPLE_DAYS:
        status = STATUS_INSUFFICIENT
    else:
        status = STATUS_OK

    has_stats = sample_days > 0
    return MetricSummary(
        metric_name=spec.name,
        display=spec.display,
        unit=spec.unit,
        window_start=start_s,
        window_end=end_s,
        window_days=window_days,
        sample_days=sample_days,
        missing_days=missing_days,
        missing_rate=round(missing_days / window_days, 4),
        mean=round(mean(values), 4) if has_stats else None,
        median=round(median(values), 4) if has_stats else None,
        min=min(values) if has_stats else None,
        max=max(values) if has_stats else None,
        status=status,
        points=tuple(points),
    )


def summarize_many(
    conn: sqlite3.Connection,
    metrics: list[str] | tuple[str, ...],
    start: date,
    end: date,
) -> tuple[MetricSummary, ...]:
    return tuple(summarize(conn, metric, start, end) for metric in metrics)


def point_on(summary: MetricSummary, day: date) -> Point | None:
    target = _iso(day)
    for point in summary.points:
        if point.record_date == target:
            return point
    return None


def weekly_buckets(
    conn: sqlite3.Connection,
    metric: str,
    end: date,
    *,
    weeks: int = 4,
) -> list[tuple[date, date, MetricSummary]]:
    """按自然周回看若干周，用于周趋势表。"""
    last_start, _ = week_bounds(end)
    buckets: list[tuple[date, date, MetricSummary]] = []
    for offset in range(weeks - 1, -1, -1):
        start = last_start - timedelta(days=7 * offset)
        stop = start + timedelta(days=6)
        buckets.append((start, stop, summarize(conn, metric, start, stop)))
    return buckets


def range_days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
