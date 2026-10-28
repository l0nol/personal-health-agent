"""单行校验。每条失败路径都要有稳定的错误码，且缺失绝不变成 0。"""

from __future__ import annotations

import pytest

from health_agent.errors import (
    E_BAD_DATE,
    E_MISSING_METRIC,
    E_NOT_A_NUMBER,
    E_PERIOD_INCOMPLETE,
    E_PERIOD_NOT_ALLOWED,
    E_UNIT_NOT_ALLOWED,
    E_UNKNOWN_METRIC,
    F_MISSING_VALUE,
    F_OUT_OF_RANGE,
    F_UNIT_CONVERTED,
    F_UNIT_INFERRED,
    F_UNREADABLE,
    F_VALUE_PARSED_FROM_TEXT,
)
from health_agent.validation.rules import parse_record_date, validate_row


def row(**overrides):
    base = {
        "metric_name": "night_sleep_minutes",
        "record_date": "2026-10-01",
        "value": 402,
        "unit": "min",
        "period_type": "day",
    }
    base.update(overrides)
    return base


def codes(result):
    return {issue.code for issue in result.issues}


# ---------------- 缺失与不可读：绝不补 0 ----------------

def test_missing_value_stays_null():
    result = validate_row(row(value=None))
    assert result.accepted
    assert result.record is not None
    assert result.record.value is None
    assert F_MISSING_VALUE in result.flags


def test_placeholder_text_is_unreadable_not_zero():
    result = validate_row(row(value="--"))
    assert result.accepted
    assert result.record.value is None
    assert F_UNREADABLE in result.flags
    assert result.record.raw_value == "--"


def test_absent_value_key_stays_null():
    payload = row()
    payload.pop("value")
    result = validate_row(payload)
    assert result.record.value is None
    assert F_MISSING_VALUE in result.flags


# ---------------- 数值解析与换算 ----------------

def test_duration_text_is_parsed_to_minutes():
    result = validate_row(row(value="1:23"))
    assert result.record.value == 83.0
    assert F_VALUE_PARSED_FROM_TEXT in result.flags


def test_hour_is_converted_and_raw_value_kept():
    result = validate_row(row(value=6.5, unit="h"))
    assert result.record.value == 390.0
    assert result.record.raw_value == "6.5"
    assert result.record.raw_unit == "h"
    assert F_UNIT_CONVERTED in result.flags


def test_kilojoule_is_converted_to_kcal():
    result = validate_row(
        row(metric_name="activity_energy_kcal", value=500, unit="kJ")
    )
    assert result.record.unit == "kcal"
    assert result.record.value == pytest.approx(119.50285)
    assert F_UNIT_CONVERTED in result.flags


def test_percent_suffix_is_stripped():
    result = validate_row(
        row(metric_name="deep_sleep_percent", value="21%", unit="%")
    )
    assert result.record.value == 21.0
    assert result.record.unit == "%"


def test_missing_unit_is_inferred_and_flagged():
    result = validate_row(row(unit=None))
    assert result.accepted
    assert result.record.unit == "min"
    assert F_UNIT_INFERRED in result.flags


# ---------------- 值域：标记而不丢弃 ----------------

def test_out_of_range_is_flagged_but_value_kept():
    result = validate_row(row(value=900))
    assert result.accepted
    assert result.record.value == 900.0
    assert F_OUT_OF_RANGE in result.flags
    assert F_OUT_OF_RANGE in codes(result)


def test_negative_sleep_is_flagged():
    result = validate_row(row(value=-5))
    assert result.accepted
    assert result.record.value == -5.0
    assert F_OUT_OF_RANGE in result.flags


# ---------------- 拒绝路径 ----------------

def test_unknown_metric_is_rejected():
    result = validate_row(row(metric_name="unknown_metric_x"))
    assert not result.accepted
    assert result.record is None
    assert E_UNKNOWN_METRIC in codes(result)


def test_missing_metric_name_is_rejected():
    payload = row()
    payload.pop("metric_name")
    result = validate_row(payload)
    assert not result.accepted
    assert E_MISSING_METRIC in codes(result)


def test_bad_date_is_rejected():
    result = validate_row(row(record_date="not-a-date"))
    assert not result.accepted
    assert E_BAD_DATE in codes(result)


def test_unit_not_allowed_is_rejected():
    result = validate_row(row(value=400, unit="%"))
    assert not result.accepted
    assert E_UNIT_NOT_ALLOWED in codes(result)


def test_boolean_value_is_rejected():
    result = validate_row(row(value=True))
    assert not result.accepted
    assert E_NOT_A_NUMBER in codes(result)


def test_object_value_is_rejected():
    result = validate_row(row(value={"v": 1}))
    assert not result.accepted
    assert E_NOT_A_NUMBER in codes(result)


def test_unparsable_text_is_rejected():
    result = validate_row(row(value="约六小时"))
    assert not result.accepted
    assert E_NOT_A_NUMBER in codes(result)


# ---------------- 统计周期 ----------------

def test_period_not_allowed_for_metric():
    result = validate_row(row(metric_name="steps_count", period_type="instant"))
    assert not result.accepted
    assert E_PERIOD_NOT_ALLOWED in codes(result)


def test_week_record_without_range_is_rejected():
    result = validate_row(row(period_type="week"))
    assert not result.accepted
    assert E_PERIOD_INCOMPLETE in codes(result)


def test_week_record_with_range_is_accepted():
    result = validate_row(
        row(period_type="week", period_start="2026-10-05", period_end="2026-10-11")
    )
    assert result.accepted
    assert result.record.period_type == "week"
    assert result.record.period_start == "2026-10-05"


def test_unknown_period_type_is_rejected():
    result = validate_row(row(period_type="quarter"))
    assert not result.accepted
    assert E_PERIOD_NOT_ALLOWED in codes(result)


# ---------------- 日期解析 ----------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-10-01", "2026-10-01"),
        ("2026/10/01", "2026-10-01"),
        ("2026-10-01 07:10", "2026-10-01"),
        ("2026-1-9", "2026-01-09"),
    ],
)
def test_parse_record_date_accepts_common_forms(raw, expected):
    assert parse_record_date(raw) == expected


@pytest.mark.parametrize("raw", ["", "10/01", "2026-13-01", None])
def test_parse_record_date_rejects_ambiguous_input(raw):
    with pytest.raises(ValueError):
        parse_record_date(raw)


# ---------------- 备注列的两种列名 ----------------

def test_note_and_notes_columns_both_map_to_notes():
    """手工 CSV 一直写 `note`，模型字段叫 `notes`——两个都要收，否则来源说明会被静默丢弃。"""
    legacy = validate_row(row(note="睡眠-日详情：夜间睡眠 7 小时 0 分钟"))
    assert legacy.accepted
    assert legacy.record.notes == "睡眠-日详情：夜间睡眠 7 小时 0 分钟"

    current = validate_row(row(notes="夜间睡眠 7 小时 0 分钟"))
    assert current.accepted
    assert current.record.notes == "夜间睡眠 7 小时 0 分钟"


def test_no_note_column_leaves_notes_empty():
    result = validate_row(row())
    assert result.accepted
    assert result.record.notes is None
