"""每日简报与每周报告。

可追溯性是这个模块的硬指标：报告里出现的**每一个数字**都必须带 record_ids
或明确的 derived_from + window。测试会逐条核对，凑数的数字过不了。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from ..analytics.aggregate import (
    MIN_WEEK_COVERAGE_DAYS,
    MetricSummary,
    point_on,
    summarize,
    tracked_metrics,
    week_bounds,
)
from ..catalog import REPORT_METRICS, get_metric
from .format import format_delta, format_number


@dataclass(frozen=True, slots=True)
class NumberRef:
    """报告中的一个数字。

    要么能点回具体记录（record_ids 非空），要么能说明是怎么算出来的
    （derived_from + window）。两者都没有的数字不允许出现在报告里。
    """

    label: str
    display: str
    value: float | None
    unit: str
    metric_name: str
    record_ids: tuple[str, ...] = ()
    derived_from: str | None = None
    window: tuple[str, str] | None = None

    @property
    def traceable(self) -> bool:
        return bool(self.record_ids) or (bool(self.derived_from) and self.window is not None)


@dataclass(frozen=True, slots=True)
class ReportPayload:
    report_type: str
    period_start: str
    period_end: str
    summaries: tuple[MetricSummary, ...]
    numbers: tuple[NumberRef, ...]
    text: str
    gaps: tuple[str, ...]

    @property
    def all_traceable(self) -> bool:
        return all(number.traceable for number in self.numbers)

    @property
    def usable_metrics(self) -> tuple[MetricSummary, ...]:
        return tuple(item for item in self.summaries if item.usable)


def latest_data_date(conn: sqlite3.Connection) -> date | None:
    row = conn.execute(
        "SELECT MAX(record_date) AS d FROM records WHERE verification_status = 'confirmed'"
    ).fetchone()
    if not row or not row["d"]:
        return None
    return date.fromisoformat(row["d"])


def build_daily(
    conn: sqlite3.Connection,
    day: date,
    metrics: list[str] | tuple[str, ...] = REPORT_METRICS,
) -> ReportPayload:
    week_start = day - timedelta(days=6)
    tracked = tracked_metrics(conn, metrics, day - timedelta(days=29), day)
    numbers: list[NumberRef] = []
    summaries: list[MetricSummary] = []
    sentences: list[str] = []
    gaps: list[str] = []

    for metric in metrics:
        spec = get_metric(metric)
        window = summarize(conn, spec, week_start, day)
        summaries.append(window)
        today = point_on(window, day)

        if today is None:
            if spec.name in tracked:
                gaps.append(spec.display)
            continue

        numbers.append(
            NumberRef(
                label=f"{spec.display}（当日）",
                display=format_number(today.value, spec.unit, spec.name),
                value=today.value,
                unit=spec.unit,
                metric_name=spec.name,
                record_ids=(today.record_id,),
            )
        )
        sentence = f"{spec.display} {format_number(today.value, spec.unit, spec.name)}"

        if window.usable:
            numbers.append(
                NumberRef(
                    label=f"{spec.display}（近 7 天平均）",
                    display=format_number(window.mean, spec.unit, spec.name),
                    value=window.mean,
                    unit=spec.unit,
                    metric_name=spec.name,
                    record_ids=window.record_ids,
                    derived_from="mean",
                    window=(window.window_start, window.window_end),
                )
            )
            sentence += f"（近 7 天平均 {format_number(window.mean, spec.unit, spec.name)}，{window.sample_days} 天有效）"
        else:
            sentence += f"（近 7 天仅 {window.sample_days} 天有效记录，不做趋势判断）"

        sentences.append(sentence)

    if sentences:
        text = f"{day.isoformat()} 简报：" + "；".join(sentences) + "。"
    else:
        text = f"{day.isoformat()} 没有可用的已确认数据。"

    if gaps:
        text += f" 当日无数据：{'、'.join(gaps)}。"

    return ReportPayload(
        report_type="daily",
        period_start=day.isoformat(),
        period_end=day.isoformat(),
        summaries=tuple(summaries),
        numbers=tuple(numbers),
        text=text,
        gaps=tuple(gaps),
    )


def build_weekly(
    conn: sqlite3.Connection,
    end_day: date,
    metrics: list[str] | tuple[str, ...] = REPORT_METRICS,
) -> ReportPayload:
    start, _ = week_bounds(end_day)
    end = start + timedelta(days=6)
    prev_start = start - timedelta(days=7)
    prev_end = start - timedelta(days=1)
    tracked = tracked_metrics(conn, metrics, start - timedelta(days=30), end)

    numbers: list[NumberRef] = []
    summaries: list[MetricSummary] = []
    sentences: list[str] = []
    gaps: list[str] = []

    for metric in metrics:
        spec = get_metric(metric)
        current = summarize(conn, spec, start, end)
        previous = summarize(conn, spec, prev_start, prev_end)
        summaries.append(current)

        if current.sample_days < MIN_WEEK_COVERAGE_DAYS:
            if spec.name in tracked:
                gaps.append(f"{spec.display}（本周仅 {current.sample_days}/7 天有效）")
            continue

        numbers.append(
            NumberRef(
                label=f"{spec.display}（本周平均）",
                display=format_number(current.mean, spec.unit, spec.name),
                value=current.mean,
                unit=spec.unit,
                metric_name=spec.name,
                record_ids=current.record_ids,
                derived_from="mean",
                window=(current.window_start, current.window_end),
            )
        )
        sentence = f"{spec.display}平均 {format_number(current.mean, spec.unit, spec.name)}"

        if previous.sample_days >= MIN_WEEK_COVERAGE_DAYS:
            delta = round(current.mean - previous.mean, 4)
            numbers.append(
                NumberRef(
                    label=f"{spec.display}（较上周 {prev_start.isoformat()}~{prev_end.isoformat()}）",
                    display=format_delta(delta, spec.unit, spec.name),
                    value=delta,
                    unit=spec.unit,
                    metric_name=spec.name,
                    record_ids=(),
                    derived_from="delta",
                    window=(previous.window_start, current.window_end),
                )
            )
            sentence += f"，较上周 {format_delta(delta, spec.unit, spec.name)}"
        else:
            sentence += f"，上周仅 {previous.sample_days}/7 天有效，无法比较"
        sentences.append(sentence)

    if sentences:
        text = f"{start.isoformat()} ~ {end.isoformat()} 周报：" + "；".join(sentences) + "。"
    else:
        text = f"{start.isoformat()} ~ {end.isoformat()} 本周没有足够数据，不出趋势结论。"

    if gaps:
        text += f" 数据不足、未纳入结论：{'、'.join(gaps)}。"

    return ReportPayload(
        report_type="weekly",
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        summaries=tuple(summaries),
        numbers=tuple(numbers),
        text=text,
        gaps=tuple(gaps),
    )
