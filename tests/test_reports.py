"""报告层：数字必须可追溯，数据不足必须明说。"""

from __future__ import annotations

from datetime import date

import pytest

from health_agent.reports.builder import build_daily, build_weekly, latest_data_date
from health_agent.reports.format import format_delta, format_minutes, format_number
from health_agent.storage.lifecycle import confirm_record
from health_agent.storage.repository import count_records, insert_record

from conftest import make_record


def seed_sleep(conn, mapping: dict[str, float]) -> dict[str, str]:
    ids = {}
    for day, value in mapping.items():
        record_id = insert_record(
            conn, make_record(record_date=day, value=float(value)), status="pending"
        )
        confirm_record(conn, record_id)
        ids[day] = record_id
    return ids


def test_daily_numbers_are_all_traceable(conn):
    ids = seed_sleep(conn, {
        "2026-10-01": 400, "2026-10-02": 410, "2026-10-03": 420,
        "2026-10-04": 430, "2026-10-05": 440, "2026-10-06": 450,
        "2026-10-07": 460,
    })
    payload = build_daily(conn, date(2026, 10, 7))

    assert payload.all_traceable
    for number in payload.numbers:
        if number.record_ids:
            for record_id in number.record_ids:
                assert record_id in ids.values()


def test_daily_text_contains_value_and_average(conn):
    seed_sleep(conn, {
        "2026-10-01": 400, "2026-10-02": 410, "2026-10-03": 420,
        "2026-10-04": 430, "2026-10-05": 440, "2026-10-06": 450,
        "2026-10-07": 462,
    })
    payload = build_daily(conn, date(2026, 10, 7))

    assert "7 小时 42 分" in payload.text       # 当日
    assert "近 7 天平均" in payload.text
    assert "2026-10-07 简报" in payload.text


def test_daily_without_data_reports_gap_not_zero(conn):
    payload = build_daily(conn, date(2026, 10, 7))

    assert payload.numbers == ()
    assert "没有可用的已确认数据" in payload.text
    # 一条数据都没有时，连"缺哪些指标"都无从谈起——不报缺口，避免噪音
    assert payload.gaps == ()
    assert payload.all_traceable


def test_daily_insufficient_samples_refuses_trend(conn):
    seed_sleep(conn, {"2026-10-06": 400, "2026-10-07": 410})
    payload = build_daily(conn, date(2026, 10, 7))

    assert "不做趋势判断" in payload.text
    assert "近 7 天平均" not in payload.text  # 不给均值，只给当日值
    # 只有当日值，没有 7 日均值
    assert len(payload.numbers) == 1
    assert payload.numbers[0].label.endswith("（当日）")


def test_daily_gaps_only_cover_metrics_you_actually_track(conn):
    """只提示"你平时在记、今天缺了"的指标，不把从未记录的指标列成缺口。"""
    seed_sleep(conn, {
        "2026-10-01": 400, "2026-10-02": 410, "2026-10-03": 420,
        "2026-10-04": 430, "2026-10-05": 440, "2026-10-06": 450,
    })
    payload = build_daily(conn, date(2026, 10, 7))

    assert "夜间主睡眠时长" in payload.gaps
    assert "步数" not in payload.gaps
    assert len(payload.gaps) == 1


def test_daily_marks_metrics_missing_on_that_day(conn):
    seed_sleep(conn, {
        "2026-10-01": 400, "2026-10-02": 410, "2026-10-03": 420,
        "2026-10-04": 430, "2026-10-05": 440, "2026-10-06": 450,
    })
    payload = build_daily(conn, date(2026, 10, 7))

    assert "夜间主睡眠时长" in payload.gaps
    assert "当日无数据" in payload.text


def test_weekly_summary_and_delta(conn):
    seed_sleep(conn, {
        "2026-10-05": 380, "2026-10-06": 390, "2026-10-07": 400,
        "2026-10-08": 410, "2026-10-09": 420, "2026-10-10": 430, "2026-10-11": 440,
        "2026-10-12": 400, "2026-10-13": 410, "2026-10-14": 420,
        "2026-10-15": 430, "2026-10-16": 440, "2026-10-17": 450, "2026-10-18": 460,
    })
    payload = build_weekly(conn, date(2026, 10, 18))

    assert payload.report_type == "weekly"
    assert payload.period_start == "2026-10-12"
    assert payload.period_end == "2026-10-18"
    assert payload.all_traceable

    deltas = [n for n in payload.numbers if n.derived_from == "delta"]
    assert deltas, "两个窗口都有效时必须给出环比"
    assert deltas[0].value == pytest.approx(430 - 410)
    assert deltas[0].window[0] == "2026-10-05"


def test_weekly_low_coverage_is_excluded(conn):
    seed_sleep(conn, {"2026-10-12": 400, "2026-10-13": 410})
    payload = build_weekly(conn, date(2026, 10, 18))

    assert payload.numbers == ()
    assert "没有足够数据" in payload.text
    assert any("夜间主睡眠时长" in gap for gap in payload.gaps)


def test_weekly_requires_four_days_not_three(conn):
    """周报门槛是 4/7，比日趋势的 3 天更严——3 天平均不算一周。"""
    seed_sleep(conn, {"2026-10-12": 400, "2026-10-13": 410, "2026-10-14": 420})
    payload = build_weekly(conn, date(2026, 10, 18))

    assert payload.numbers == ()
    assert any("3/7" in gap for gap in payload.gaps)


def test_weekly_without_previous_week_skips_delta(conn):
    seed_sleep(conn, {
        "2026-10-12": 400, "2026-10-13": 410, "2026-10-14": 420, "2026-10-15": 430,
    })
    payload = build_weekly(conn, date(2026, 10, 18))

    assert not [n for n in payload.numbers if n.derived_from == "delta"]
    assert "无法比较" in payload.text


def test_pending_records_never_reach_reports(conn):
    insert_record(conn, make_record(record_date="2026-10-07", value=400.0), status="pending")
    payload = build_daily(conn, date(2026, 10, 7))

    assert payload.numbers == ()
    assert "没有可用的已确认数据" in payload.text


def test_latest_data_date_only_counts_confirmed(conn):
    assert latest_data_date(conn) is None
    insert_record(conn, make_record(record_date="2026-10-09", value=400.0), status="pending")
    assert latest_data_date(conn) is None
    confirmed = insert_record(conn, make_record(record_date="2026-10-05", value=400.0), status="pending")
    confirm_record(conn, confirmed)
    assert latest_data_date(conn) == date(2026, 10, 5)


# ---------------- 格式化 ----------------

@pytest.mark.parametrize(
    "minutes,expected",
    [(402, "6 小时 42 分"), (60, "1 小时"), (45, "45 分钟"), (0, "0 分钟")],
)
def test_format_minutes(minutes, expected):
    assert format_minutes(minutes) == expected


def test_format_number_by_unit():
    assert format_number(8421, "count", "steps_count") == "8,421 步"
    assert format_number(410, "kcal") == "410 千卡"
    assert format_number(58, "bpm") == "58 次/分"
    assert format_number(21, "%") == "21%"
    assert format_number(47, "ms") == "47 毫秒"
    assert format_number(None, "min") == "—"


def test_average_is_rounded_for_display():
    assert format_number(58.8571, "bpm") == "59 次/分"
    assert format_number(58.3333, "bpm") == "58 次/分"
    assert format_number(21.26, "%") == "21.3%"


def test_format_delta_keeps_sign():
    assert format_delta(27, "min") == "+27 分钟"
    assert format_delta(-23, "min") == "-23 分钟"
    assert format_delta(0, "min") == "±0 分钟"
    assert format_delta(None, "min") == "—"

# ---------------- 0.1.1：范围端点不得出现在报告里 ----------------

def test_range_metrics_never_enter_report(conn):
    """即使库里只有 HRV 上下限，报告也不能把它们当平均值展示。"""
    seed_sleep(conn, {"2026-10-07": 400})
    for day, low, high in [("2026-10-07", 47.0, 82.0)]:
        for metric, value in (("hrv_min_ms", low), ("hrv_max_ms", high)):
            record_id = insert_record(
                conn,
                make_record(metric_name=metric, unit="ms", record_date=day, value=value),
                status="pending",
            )
            confirm_record(conn, record_id)

    payload = build_daily(conn, date(2026, 10, 7))
    used_metrics = {number.metric_name for number in payload.numbers}

    assert "hrv_min_ms" not in used_metrics
    assert "hrv_max_ms" not in used_metrics
    assert "HRV 区间" not in payload.text
