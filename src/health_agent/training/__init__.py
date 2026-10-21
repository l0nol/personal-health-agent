"""训练日志：主观记录 + 恢复上下文。

与 `records`（设备观测）分开建模，避免把"我觉得很累"写成"设备测得疲劳值"。
"""

from .models import (
    ACTIVITY_LABELS,
    ACTIVITY_TYPES,
    BODY_PART_LABELS,
    BODY_PARTS,
    COMPLETIONS,
    RecoveryFeedback,
    TrainingPreferences,
    TrainingSession,
)

__all__ = [
    "ACTIVITY_LABELS",
    "ACTIVITY_TYPES",
    "BODY_PART_LABELS",
    "BODY_PARTS",
    "COMPLETIONS",
    "RecoveryFeedback",
    "TrainingPreferences",
    "TrainingSession",
]
