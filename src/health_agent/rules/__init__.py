"""提醒规则引擎：4 条内置规则 + 去重/冷却。"""

from .engine import (
    ALL_RULES,
    AlertCandidate,
    RuleConfig,
    baseline_deviation,
    config_from_settings,
    data_gap,
    evaluate,
    resting_hr_shift,
    run,
    sleep_deficit_trend,
)

__all__ = [
    "ALL_RULES",
    "AlertCandidate",
    "RuleConfig",
    "baseline_deviation",
    "config_from_settings",
    "data_gap",
    "evaluate",
    "resting_hr_shift",
    "run",
    "sleep_deficit_trend",
]
