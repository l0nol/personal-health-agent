"""指标注册表的完整性检查。注册表是唯一事实源，这里守住它的不变式。"""

from __future__ import annotations

from health_agent.catalog import (
    METRIC_INDEX,
    METRICS,
    P0_METRICS,
    REPORT_METRICS,
    VALID_KINDS,
    VALID_TIERS,
    get_metric,
)
from health_agent.validation.units import KNOWN_UNITS

EXPECTED_P0 = {
    "night_sleep_minutes",
    "nap_minutes",
    "resting_hr_bpm",
    "sleep_avg_hr_bpm",
    "steps_count",
    "activity_energy_kcal",
}


def test_metric_names_are_unique():
    names = [m.name for m in METRICS]
    assert len(names) == len(set(names))


def test_every_metric_has_known_unit_and_kind():
    for metric in METRICS:
        assert metric.unit in KNOWN_UNITS, f"{metric.name} 的单位 {metric.unit} 不在白名单"
        assert metric.kind in VALID_KINDS, f"{metric.name} 的 kind 非法"
        assert metric.tier in VALID_TIERS, f"{metric.name} 的 tier 非法"


def test_every_metric_declares_a_period_type():
    for metric in METRICS:
        assert metric.period_types, f"{metric.name} 未声明统计周期"
        assert set(metric.period_types) <= {"instant", "day", "week", "month"}


def test_range_bounds_are_ordered():
    for metric in METRICS:
        if metric.min_valid is not None and metric.max_valid is not None:
            assert metric.min_valid < metric.max_valid, f"{metric.name} 的值域上下限颠倒"


def test_p0_scope_matches_spec():
    assert set(P0_METRICS) == EXPECTED_P0


def test_night_sleep_and_nap_are_separate_metrics():
    night = get_metric("night_sleep_minutes")
    nap = get_metric("nap_minutes")
    assert night.name != nap.name
    assert night.unit == nap.unit == "min"
    assert night.tier == nap.tier == "P0"


def test_resting_hr_and_sleep_hr_are_separate_metrics():
    resting = get_metric("resting_hr_bpm")
    sleeping = get_metric("sleep_avg_hr_bpm")
    assert resting.name != sleeping.name
    assert resting.unit == sleeping.unit == "bpm"


def test_only_sleep_hrv_exists():
    """设备只提供睡眠平均 HRV；不得注册"全天 HRV"字段。"""
    assert "hrv_avg_ms" in METRIC_INDEX
    assert not [n for n in METRIC_INDEX if "all_day" in n or "daily_hrv" in n]
    assert "睡眠" in get_metric("hrv_avg_ms").description


def test_sleep_structure_keeps_percent_and_minutes_apart():
    """比例字段与时长字段必须同时存在且互不替代。"""
    percent = get_metric("deep_sleep_percent")
    minutes = get_metric("deep_sleep_minutes")
    assert percent.unit == "%"
    assert minutes.unit == "min"
    assert percent.kind == "percent"
    assert "禁止" in percent.description


def test_device_scores_are_documented_as_raw_values():
    for name in ("stress_score", "deep_sleep_continuity_score", "sleep_score"):
        assert "医学" in get_metric(name).description


def test_report_metrics_are_not_p2():
    for name in REPORT_METRICS:
        assert get_metric(name).tier in {"P0", "P1"}

# ---------------- 0.1.1：定义可信度与范围端点 ----------------

def test_every_metric_declares_verification():
    from health_agent.catalog import VALID_VERIFICATION

    for metric in METRICS:
        assert metric.verification in VALID_VERIFICATION, metric.name


def test_no_metric_claims_device_verification_yet():
    """尚未取得设备官方字段说明，任何指标都不许标成 device_verified。"""
    claimed = [m.name for m in METRICS if m.verification == "device_verified"]
    assert claimed == [], f"未经核对不得声称已验证: {claimed}"


def test_range_bound_metrics_excluded_from_report():
    from health_agent.catalog import RANGE_BOUND_METRICS

    assert set(RANGE_BOUND_METRICS) == {"hrv_min_ms", "hrv_max_ms", "spo2_min_percent", "spo2_max_percent"}
    for name in RANGE_BOUND_METRICS:
        metric = get_metric(name)
        assert metric.kind == "range_bound"
        assert metric.in_report is False
        assert name not in REPORT_METRICS


def test_range_bound_metrics_describe_the_prohibition():
    from health_agent.catalog import RANGE_BOUND_METRICS

    for name in RANGE_BOUND_METRICS:
        description = get_metric(name).description
        assert ("禁止" in description) or ("不得" in description), f"{name} 未写明禁止推算"


def test_hrv_average_and_range_are_distinct_metrics():
    """平均 HRV 与区间上下限必须是三个独立指标，不能互相顶替。"""
    average = get_metric("hrv_avg_ms")
    low = get_metric("hrv_min_ms")
    high = get_metric("hrv_max_ms")

    assert average.kind == "scalar" and average.in_report is True
    assert low.kind == high.kind == "range_bound"
    assert len({average.name, low.name, high.name}) == 3


def test_invariant_rejects_range_metric_in_report():
    from dataclasses import replace

    from health_agent.catalog import assert_catalog_invariants

    bad = [replace(get_metric("hrv_min_ms"), in_report=True)]
    try:
        assert_catalog_invariants(bad)
    except ValueError as exc:
        assert "范围端点" in str(exc)
    else:
        raise AssertionError("不变式没有拦住 range_bound 指标进入报告")
