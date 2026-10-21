"""每日活动/训练建议。

本地规则先给出结构化结论；AI 只在显式请求时润色解释，且不能改变结论。
"""

from .contract import (
    CATEGORIES,
    CATEGORY_INSUFFICIENT,
    CATEGORY_LIGHT,
    CATEGORY_REGULAR,
    CATEGORY_REST,
    Evidence,
    Guidance,
)
from .engine import RULE_VERSION, GuidanceConfig, build_guidance, config_from_settings

__all__ = [
    "CATEGORIES",
    "CATEGORY_INSUFFICIENT",
    "CATEGORY_LIGHT",
    "CATEGORY_REGULAR",
    "CATEGORY_REST",
    "Evidence",
    "Guidance",
    "GuidanceConfig",
    "RULE_VERSION",
    "build_guidance",
    "config_from_settings",
]
