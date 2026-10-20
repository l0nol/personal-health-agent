"""基线与提醒规则：触发 / 不触发 / 连续触发 / 数据缺失 / 去重 / 冷却 / 反馈。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from health_agent.analytics.baseline import compute_baseline
from health_agent.rules.engine import (
    ALL_RULES,
    RuleConfig,
    baseline_deviation,
    data_gap,
    evaluate,
    resting_hr_shift,
    run,
    sleep_deficit_trend,
)
from health_agent.storage.alerts import (
    list_alerts,
    mark_read,
    set_feedback,
    count_unread,
)
from health_agent.storage.lifecycle import confirm_record
from health_agent.storage.repository import insert_record

from conftest import make_record

TODAY = date(2026, 10, 19)


def seed(conn, metric: str, unit: str, mapping: dict[str, float]) -> dict[str, str]:
    ids = {}
    for day, value in mapping.items():
        record_id = insert_record(
            conn,
            make_record(metric_name=metric, unit=unit, record_date=day, value=float(value)),
            status="pending",
        )
        confirm_record(conn, record_id)
        ids[day] = record_id
    return ids


def days_ending(end: date, count: int) -> list[str]:
    return [(end - timedelta(days=offset)).isoformat() for offset in range(count - 1, -1, -1)]


# ---------------- 基线 ----------------

def test_baseline_needs_enough_samples(conn):
    seed(conn, "resting_hr_bpm", "bpm", dict(zip(days_ending(TODAY, 5), [60] * 5)))
    baseline = compute_baseline(conn, "resting_hr_bpm", TODAY, min_samples=14)

    assert baseline.status == "insufficient"
    assert baseline.center is None
    assert baseline.is_deviation(200) is False  # 基线不可用时不判偏离


def test_baseline_center_is_median(conn):
    values = [58, 59, 60, 61, 62] * 6  # 30 天
    seed(conn, "resting_hr_bpm", "bpm", dict(zip(days_ending(TODAY, 30), values)))
    baseline = compute_baseline(conn, "resting_hr_bpm", TODAY, min_samples=14)

    assert baseline.active
    assert baseline.center == pytest.approx(60.0)
    assert baseline.spread is not None and baseline.spread > 0
    assert baseline.threshold == pytest.approx(2.5 * baseline.spread, rel=1e-6)


def test_baseline_deviation_uses_threshold(conn):
    values = [58, 59, 60, 61, 62] * 6
    seed(conn, "resting_hr_bpm", "bpm", dict(zip(days_ending(TODAY, 30), values)))
    baseline = compute_baseline(conn, "resting_hr_bpm", TODAY, min_samples=14)

    assert baseline.is_deviation(60) is False
    assert baseline.is_deviation(baseline.center + baseline.threshold + 1) is True
    assert baseline.is_deviation(baseline.center - baseline.threshold - 1) is True


def test_baseline_falls_back_to_iqr_when_mad_is_zero(conn):
    """整数指标经常出现"一半以上取值相同"，MAD 会退化成 0，此时不能直接判无效。"""
    values = [58] * 20 + [62] * 8
    seed(conn, "resting_hr_bpm", "bpm", dict(zip(days_ending(TODAY, 28), values)))
    baseline = compute_baseline(conn, "resting_hr_bpm", TODAY, min_samples=14)

    assert baseline.active
    assert baseline.method == "iqr_fallback"
    assert baseline.spread and baseline.spread > 0


def test_baseline_all_identical_is_insufficient(conn):
    seed(conn, "resting_hr_bpm", "bpm", dict(zip(days_ending(TODAY, 28), [58] * 28)))
    baseline = compute_baseline(conn, "resting_hr_bpm", TODAY, min_samples=14)

    assert baseline.status == "insufficient"
    assert "离散度" in baseline.reason


# ---------------- 规则 1：睡眠不足趋势 ----------------

def test_sleep_deficit_triggers_on_three_short_days(conn):
    mapping = dict(zip(days_ending(TODAY, 7), [350, 360, 340, 430, 440, 450, 430]))
    ids = seed(conn, "night_sleep_minutes", "min", mapping)

    candidates = sleep_deficit_trend(conn, RuleConfig(), TODAY)

    assert len(candidates) == 1
    assert candidates[0].rule_id == "sleep_deficit_trend"
    assert len(candidates[0].evidence_record_ids) == 3
    assert set(candidates[0].evidence_record_ids) <= set(ids.values())


def test_sleep_deficit_not_triggered_on_two_short_days(conn):
    mapping = dict(zip(days_ending(TODAY, 7), [350, 360, 430, 440, 450, 430, 460]))
    seed(conn, "night_sleep_minutes", "min", mapping)

    assert sleep_deficit_trend(conn, RuleConfig(), TODAY) == []


def test_sleep_deficit_skipped_when_coverage_too_low(conn):
    mapping = dict(zip(days_ending(TODAY, 3), [350, 340, 330]))
    seed(conn, "night_sleep_minutes", "min", mapping)

    assert sleep_deficit_trend(conn, RuleConfig(), TODAY) == []


def test_sleep_deficit_boundary_is_not_below(conn):
    """恰好等于阈值不算"低于"——边界不该靠浮点运气决定。"""
    threshold = 420 * 0.9
    mapping = dict(zip(days_ending(TODAY, 7), [threshold] * 4 + [460] * 3))
    seed(conn, "night_sleep_minutes", "min", mapping)

    assert sleep_deficit_trend(conn, RuleConfig(), TODAY) == []


def test_sleep_target_is_configurable(conn):
    mapping = dict(zip(days_ending(TODAY, 7), [380, 385, 390, 460, 470, 480, 460]))
    seed(conn, "night_sleep_minutes", "min", mapping)

    assert sleep_deficit_trend(conn, RuleConfig(), TODAY) == []
    strict = RuleConfig(sleep_target_minutes=480)
    assert len(sleep_deficit_trend(conn, strict, TODAY)) == 1


# ---------------- 规则 2：静息心率偏移 ----------------

def _resting_hr_baseline(conn) -> None:
    values = [58, 59, 60, 61, 62] * 6
    seed(conn, "resting_hr_bpm", "bpm", dict(zip(days_ending(TODAY - timedelta(days=3), 30), values)))


def test_resting_hr_shift_triggers_on_three_days(conn):
    _resting_hr_baseline(conn)
    seed(conn, "resting_hr_bpm", "bpm", dict(zip(days_ending(TODAY, 3), [74, 75, 76])))

    candidates = resting_hr_shift(conn, RuleConfig(), TODAY)

    assert len(candidates) == 1
    assert "高于" in candidates[0].trigger_reason
    assert len(candidates[0].evidence_record_ids) == 3


def test_resting_hr_shift_needs_consecutive_days(conn):
    _resting_hr_baseline(conn)
    seed(conn, "resting_hr_bpm", "bpm", {
        days_ending(TODAY, 3)[0]: 74,
        days_ending(TODAY, 3)[2]: 76,
    })

    assert resting_hr_shift(conn, RuleConfig(), TODAY) == []


def test_resting_hr_shift_ignores_small_changes(conn):
    _resting_hr_baseline(conn)
    seed(conn, "resting_hr_bpm", "bpm", dict(zip(days_ending(TODAY, 3), [63, 64, 63])))

    assert resting_hr_shift(conn, RuleConfig(), TODAY) == []


# ---------------- 规则 3：基线偏离 ----------------

def test_baseline_deviation_triggers_for_configured_metric(conn):
    values = [8000, 8200, 8400, 8600, 8800] * 6
    seed(conn, "steps_count", "count", dict(zip(days_ending(TODAY - timedelta(days=3), 30), values)))
    seed(conn, "steps_count", "count", dict(zip(days_ending(TODAY, 3), [2000, 2100, 2200])))

    candidates = baseline_deviation(conn, RuleConfig(), TODAY)

    assert len(candidates) == 1
    assert candidates[0].metric_name == "steps_count"
    assert candidates[0].severity == "info"


# ---------------- 规则 4：数据缺口 ----------------

def test_data_gap_triggers_on_low_coverage(conn):
    seed(conn, "night_sleep_minutes", "min", dict(zip(days_ending(TODAY, 2), [400, 410])))

    candidates = data_gap(conn, RuleConfig(), TODAY)

    assert len(candidates) == 1
    assert candidates[0].rule_id == "data_gap"
    assert "缺失率" in candidates[0].trigger_reason


def test_data_gap_ignores_metrics_you_never_record(conn):
    seed(conn, "night_sleep_minutes", "min", dict(zip(days_ending(TODAY, 7), [400] * 7)))

    candidates = data_gap(conn, RuleConfig(), TODAY)

    assert candidates == []


# ---------------- 去重、冷却、反馈 ----------------

def test_duplicate_alert_is_not_stored_twice(conn):
    mapping = dict(zip(days_ending(TODAY, 7), [350, 360, 340, 430, 440, 450, 430]))
    seed(conn, "night_sleep_minutes", "min", mapping)

    first = run(conn, RuleConfig(), TODAY)
    second = run(conn, RuleConfig(), TODAY)

    assert first and second == []
    assert len([a for a in list_alerts(conn) if a["rule_id"] == "sleep_deficit_trend"]) == 1


def test_cooldown_blocks_next_window(conn):
    seed(conn, "night_sleep_minutes", "min", dict(zip(days_ending(TODAY, 2), [400, 410])))

    first = run(conn, RuleConfig(), TODAY)
    second = run(conn, RuleConfig(), TODAY + timedelta(days=1))

    assert first
    assert second == [], "72 小时冷却期内不应重复提醒"


def test_zero_cooldown_allows_next_window(conn):
    seed(conn, "night_sleep_minutes", "min", dict(zip(days_ending(TODAY, 2), [400, 410])))

    run(conn, RuleConfig(cooldown_hours=0), TODAY)
    second = run(conn, RuleConfig(cooldown_hours=0), TODAY + timedelta(days=1))

    assert len(second) == 1


def test_mark_read_and_feedback(conn):
    seed(conn, "night_sleep_minutes", "min", dict(zip(days_ending(TODAY, 2), [400, 410])))
    run(conn, RuleConfig(), TODAY)
    alert = list_alerts(conn)[0]

    assert count_unread(conn) == 1
    mark_read(conn, alert["alert_id"])
    assert count_unread(conn) == 0

    set_feedback(conn, alert["alert_id"], "irrelevant")
    assert list_alerts(conn)[0]["feedback"] == "irrelevant"


def test_invalid_feedback_is_refused(conn):
    seed(conn, "night_sleep_minutes", "min", dict(zip(days_ending(TODAY, 2), [400, 410])))
    run(conn, RuleConfig(), TODAY)
    alert = list_alerts(conn)[0]

    with pytest.raises(ValueError):
        set_feedback(conn, alert["alert_id"], "whatever")


def test_alert_carries_reason_evidence_and_action(conn):
    seed(conn, "night_sleep_minutes", "min", dict(zip(days_ending(TODAY, 2), [400, 410])))
    run(conn, RuleConfig(), TODAY)
    alert = list_alerts(conn)[0]

    assert alert["trigger_reason"]
    assert alert["suggested_action"]
    assert isinstance(alert["evidence"], list)


# ---------------- 边界 ----------------

def test_evaluate_does_not_persist(conn):
    seed(conn, "night_sleep_minutes", "min", dict(zip(days_ending(TODAY, 2), [400, 410])))
    candidates = evaluate(conn, RuleConfig(), TODAY)

    assert candidates
    assert list_alerts(conn) == []


def test_no_rule_interprets_arrhythmia():
    """心律筛查只能保存设备原文，规则引擎里不允许出现相关判断。"""
    import inspect

    from health_agent.rules import engine

    source = inspect.getsource(engine)
    for keyword in ("arrhythmia", "suspected_af", "suspected_pac"):
        assert keyword not in source


def test_all_rules_are_callable():
    assert len(ALL_RULES) == 4
    assert all(callable(rule) for rule in ALL_RULES)

# ---------------- 0.1.1：睡眠目标的来源必须说清楚 ----------------

def _deficit_rows() -> dict[str, float]:
    return dict(zip(days_ending(TODAY, 7), [350, 360, 340, 430, 440, 450, 430]))


def test_default_target_is_described_as_reference(conn):
    """未配置目标时，文案必须说"默认参考值"，不能冒充"你的目标"。"""
    seed(conn, "night_sleep_minutes", "min", _deficit_rows())
    candidate = sleep_deficit_trend(conn, RuleConfig(), TODAY)[0]

    assert "默认参考值" in candidate.trigger_reason
    assert "你的目标" not in candidate.trigger_reason
    assert "未按年龄" in candidate.trigger_reason


def test_user_supplied_target_is_described_as_user_setting(conn):
    seed(conn, "night_sleep_minutes", "min", _deficit_rows())
    config = RuleConfig(sleep_target_minutes=480, sleep_target_user_supplied=True)
    candidate = sleep_deficit_trend(conn, config, TODAY)[0]

    assert "你设定的目标 8 小时" in candidate.trigger_reason
    assert "默认参考值" not in candidate.trigger_reason


def test_age_adaptation_is_explicitly_not_implemented():
    from health_agent.rules import engine

    assert engine.AGE_ADAPTATION_IMPLEMENTED is False
    assert "未按年龄" in engine.describe_sleep_target(RuleConfig())


def test_sleep_alert_stays_within_trend_scope(conn):
    """提醒只做趋势观察，不得出现诊断性表述。"""
    seed(conn, "night_sleep_minutes", "min", _deficit_rows())
    candidate = sleep_deficit_trend(conn, RuleConfig(), TODAY)[0]
    text = candidate.trigger_reason + " " + candidate.suggested_action

    assert "仅作趋势观察" in candidate.trigger_reason
    assert "不构成诊断" in candidate.trigger_reason
    for forbidden in ("患有", "病情", "处方", "确诊", "诊断结果"):
        assert forbidden not in text


def test_baseline_insufficient_produces_no_alert(conn):
    """样本不够建基线时，基线类提醒必须完全不出现（而不是拿 5 天数据硬算）。"""
    seed(conn, "resting_hr_bpm", "bpm", dict(zip(days_ending(TODAY, 5), [60, 61, 62, 63, 64])))

    assert resting_hr_shift(conn, RuleConfig(), TODAY) == []
    assert baseline_deviation(conn, RuleConfig(), TODAY) == []


def test_settings_flag_flows_into_rule_config(tmp_path):
    from health_agent.config import load_settings
    from health_agent.rules.engine import config_from_settings

    default_config = config_from_settings(load_settings({}, project_root=tmp_path))
    assert default_config.sleep_target_user_supplied is False

    user_config = config_from_settings(
        load_settings({"HEALTH_AGENT_SLEEP_TARGET_MIN": "465"}, project_root=tmp_path)
    )
    assert user_config.sleep_target_user_supplied is True
    assert user_config.sleep_target_minutes == 465
