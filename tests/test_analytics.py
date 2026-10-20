"""统计层：只算已确认数据、样本不足要明说、同日多值只取最后一条。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from health_agent.analytics.aggregate import (
    STATUS_INSUFFICIENT,
    STATUS_NO_DATA,
    STATUS_OK,
    load_points,
    summarize,
    week_bounds,
    weekly_buckets,
)
from health_agent.storage.lifecycle import confirm_record
from health_agent.storage.repository import insert_record

from conftest import make_record


def seed(conn, values_by_date: dict[str, float], *, metric="night_sleep_minutes", unit="min", confirm=True):
    ids = {}
    for day, value in values_by_date.items():
        record_id = insert_record(
            conn, make_record(metric_name=metric, unit=unit, record_date=day, value=float(value)),
            status="pending",
        )
        if confirm:
            confirm_record(conn, record_id)
        ids[day] = record_id
    return ids


def test_only_confirmed_records_are_counted(conn):
    seed(conn, {"2026-10-01": 400, "2026-10-02": 410})
    seed(conn, {"2026-10-03": 420}, confirm=False)

    summary = summarize(conn, "night_sleep_minutes", date(2026, 10, 1), date(2026, 10, 3))

    assert summary.sample_days == 2
    assert summary.mean == 405
    assert "2026-10-03" not in [point.record_date for point in summary.points]


def test_missing_rate_reflects_window(conn):
    seed(conn, {"2026-10-01": 400, "2026-10-03": 420})
    summary = summarize(conn, "night_sleep_minutes", date(2026, 10, 1), date(2026, 10, 7))

    assert summary.window_days == 7
    assert summary.sample_days == 2
    assert summary.missing_days == 5
    assert summary.missing_rate == pytest.approx(5 / 7, rel=1e-3)


def test_status_insufficient_below_three_days(conn):
    seed(conn, {"2026-10-01": 400, "2026-10-02": 410})
    summary = summarize(conn, "night_sleep_minutes", date(2026, 10, 1), date(2026, 10, 7))

    assert summary.status == STATUS_INSUFFICIENT
    assert summary.usable is False


def test_status_ok_at_three_days(conn):
    seed(conn, {"2026-10-01": 400, "2026-10-02": 410, "2026-10-03": 420})
    summary = summarize(conn, "night_sleep_minutes", date(2026, 10, 1), date(2026, 10, 7))

    assert summary.status == STATUS_OK
    assert summary.usable is True
    assert summary.mean == 410
    assert summary.median == 410
    assert summary.min == 400 and summary.max == 420


def test_status_no_data_keeps_stats_null(conn):
    summary = summarize(conn, "steps_count", date(2026, 10, 1), date(2026, 10, 7))

    assert summary.status == STATUS_NO_DATA
    assert summary.mean is None
    assert summary.min is None
    assert summary.max is None
    assert summary.record_ids == ()


def test_summary_carries_record_ids(conn):
    ids = seed(conn, {"2026-10-01": 400, "2026-10-02": 410, "2026-10-03": 420})
    summary = summarize(conn, "night_sleep_minutes", date(2026, 10, 1), date(2026, 10, 3))

    assert set(summary.record_ids) == set(ids.values())


def test_null_values_are_not_counted_as_samples(conn):
    insert_record(conn, make_record(metric_name="nap_minutes", value=None), status="confirmed")
    summary = summarize(conn, "nap_minutes", date(2026, 10, 1), date(2026, 10, 1))

    assert summary.sample_days == 0
    assert summary.status == STATUS_NO_DATA


def test_instant_records_from_different_sources_keep_last(conn):
    """不同来源的同日瞬时记录可以并存，统计时只取最后一条，避免两个值被平均。"""
    first = insert_record(
        conn,
        make_record(metric_name="weight_kg", unit="kg", value=68.4, source_app="manual_entry"),
        status="confirmed",
    )
    second = insert_record(
        conn,
        make_record(metric_name="weight_kg", unit="kg", value=78.9, source_app="smart_scale"),
        status="confirmed",
    )

    summary = summarize(conn, "weight_kg", date(2026, 10, 1), date(2026, 10, 1))

    assert summary.sample_days == 1
    assert summary.points[0].value == 78.9
    assert summary.points[0].record_id == second
    assert first not in summary.record_ids


def test_same_source_second_instant_reading_is_blocked(conn):
    """已知限制：逻辑键只到"日期"粒度，同来源同日第二条瞬时测量只能走冲突流程。

    修法是把观测时刻纳入逻辑键（见 README 已知限制）。在 P2 指标启用前必须先修。
    """
    from health_agent.errors import RecordStateError
    from health_agent.storage.lifecycle import confirm_record as confirm

    first = insert_record(conn, make_record(metric_name="weight_kg", unit="kg", value=68.4), status="pending")
    confirm(conn, first)

    second = insert_record(conn, make_record(metric_name="weight_kg", unit="kg", value=78.9), status="pending")
    with pytest.raises(RecordStateError):
        confirm(conn, second)


def test_week_records_are_excluded_from_daily_trend(conn):
    """周视图记录不能混进日趋势——否则同一周会被重复计算。"""
    insert_record(
        conn,
        make_record(
            metric_name="night_sleep_minutes",
            record_date="2026-10-05",
            period_type="week",
            period_start="2026-10-05",
            period_end="2026-10-11",
            value=400.0,
        ),
        status="confirmed",
    )
    summary = summarize(conn, "night_sleep_minutes", date(2026, 10, 5), date(2026, 10, 11))

    assert summary.sample_days == 0
    assert summary.status == STATUS_NO_DATA


def test_load_points_is_sorted_and_unique_per_day(conn):
    seed(conn, {"2026-10-02": 410, "2026-10-01": 400})
    points = load_points(conn, "night_sleep_minutes", "2026-10-01", "2026-10-02")

    assert [point.record_date for point in points] == ["2026-10-01", "2026-10-02"]


def test_week_bounds_is_monday_to_sunday():
    start, end = week_bounds(date(2026, 10, 14))  # 周三
    assert start == date(2026, 10, 12)
    assert end == date(2026, 10, 18)
    assert start.weekday() == 0


def test_weekly_buckets_are_chronological(conn):
    buckets = weekly_buckets(conn, "night_sleep_minutes", date(2026, 10, 19), weeks=3)

    assert len(buckets) == 3
    starts = [start for start, _, _ in buckets]
    assert starts == sorted(starts)
    assert starts[-1] == date(2026, 10, 19)
    assert all((end - start).days == 6 for start, end, _ in buckets)


def test_summarize_accepts_metric_spec_and_name(conn):
    seed(conn, {"2026-10-01": 400, "2026-10-02": 410, "2026-10-03": 420})
    from health_agent.catalog import get_metric

    by_name = summarize(conn, "night_sleep_minutes", date(2026, 10, 1), date(2026, 10, 3))
    by_spec = summarize(conn, get_metric("night_sleep_minutes"), date(2026, 10, 1), date(2026, 10, 3))

    assert by_name == by_spec


def test_window_is_inclusive(conn):
    seed(conn, {"2026-10-01": 400, "2026-10-02": 410, "2026-10-03": 420})
    summary = summarize(conn, "night_sleep_minutes", date(2026, 10, 1), date(2026, 10, 2))

    assert summary.window_days == 2
    assert summary.sample_days == 2

# ---------------- 0.1.1：周期与范围值的统计口径 ----------------

def test_month_records_excluded_from_daily_trend(conn):
    """月视图是设备自己的汇总，混进日趋势就是重复计数。"""
    insert_record(
        conn,
        make_record(
            metric_name="night_sleep_minutes",
            record_date="2026-10-01",
            period_type="month",
            period_start="2026-10-01",
            period_end="2026-10-31",
            value=400.0,
        ),
        status="confirmed",
    )
    summary = summarize(conn, "night_sleep_minutes", date(2026, 10, 1), date(2026, 10, 31))

    assert summary.sample_days == 0
    assert summary.status == STATUS_NO_DATA


def test_instant_records_enter_daily_window(conn):
    """单次测量可以进日窗口，但不能因为窗口是 3 天就当成 3 个样本。"""
    insert_record(
        conn,
        make_record(metric_name="weight_kg", unit="kg", record_date="2026-10-02",
                    period_type="instant", value=79.0),
        status="confirmed",
    )
    summary = summarize(conn, "weight_kg", date(2026, 10, 1), date(2026, 10, 3))

    assert summary.window_days == 3
    assert summary.sample_days == 1
    assert summary.status == STATUS_INSUFFICIENT


def test_range_metric_never_feeds_average_metric(conn):
    """只有 HRV 上下限时，平均 HRV 必须是"无数据"——不能拿两个端点算平均。"""
    seed(conn, {"2026-10-01": 47, "2026-10-02": 52}, metric="hrv_min_ms", unit="ms")
    seed(conn, {"2026-10-01": 82, "2026-10-02": 79}, metric="hrv_max_ms", unit="ms")

    average = summarize(conn, "hrv_avg_ms", date(2026, 10, 1), date(2026, 10, 2))
    lower = summarize(conn, "hrv_min_ms", date(2026, 10, 1), date(2026, 10, 2))

    assert average.status == STATUS_NO_DATA
    assert average.mean is None
    assert lower.sample_days == 2
    assert lower.mean == 49.5  # 这是"每日下限的平均"，不是 HRV 平均
