"""单位白名单与换算。重点守两条：别名消歧靠目标单位，比例绝不换算成时长。"""

from __future__ import annotations

import pytest

from health_agent.errors import UnitNotAllowedError
from health_agent.validation.units import CONVERSIONS, resolve_unit


def test_hour_converts_to_minutes():
    result = resolve_unit("h", "min")
    assert result.unit == "min"
    assert result.factor == 60.0
    assert result.converted is True
    assert result.inferred is False


def test_chinese_unit_alias_is_accepted():
    assert resolve_unit("分钟", "min").unit == "min"
    assert resolve_unit("次/分", "bpm").unit == "bpm"
    assert resolve_unit("千卡", "kcal").unit == "kcal"


def test_ambiguous_alias_resolved_by_target_unit():
    """"分"既可能是分钟也可能是评分，只能由指标的标准单位来消歧。"""
    assert resolve_unit("分", "min").unit == "min"
    assert resolve_unit("分", "score").unit == "score"


def test_kilojoule_converts_to_kcal():
    result = resolve_unit("kJ", "kcal")
    assert result.converted is True
    assert pytest.approx(result.factor, rel=1e-6) == 0.2390057


def test_empty_unit_is_inferred_and_flagged():
    result = resolve_unit(None, "min")
    assert result.unit == "min"
    assert result.inferred is True
    assert result.factor == 1.0


def test_unknown_unit_is_rejected():
    with pytest.raises(UnitNotAllowedError):
        resolve_unit("光年", "min")


def test_percent_cannot_become_minutes():
    """规格 §4：比例不得未经确认换算成时长。代码层直接没有这条换算路径。"""
    assert ("%", "min") not in CONVERSIONS
    with pytest.raises(UnitNotAllowedError):
        resolve_unit("18%", "min")
    with pytest.raises(UnitNotAllowedError):
        resolve_unit("%", "min")


def test_conversion_table_has_no_percent_source():
    assert not [pair for pair in CONVERSIONS if pair[0] == "%"]
