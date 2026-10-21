"""训练日志领域模型。

这里的对象只描述"用户记录了什么"，不描述"设备测到了什么"。
设备观测仍然走 `records` 表，两者在 schema 层就是分开的。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ActivityType = Literal["strength", "cardio", "mobility", "sport", "walking", "other"]
Completion = Literal["completed", "partial", "skipped"]

#: 允许的活动类型。顺序即 CLI 帮助与界面展示顺序。
ACTIVITY_TYPES: tuple[str, ...] = ("strength", "cardio", "mobility", "sport", "walking", "other")

#: 活动类型的中文标签（仅用于展示，不参与校验）。
ACTIVITY_LABELS: dict[str, str] = {
    "strength": "力量训练",
    "cardio": "有氧",
    "mobility": "拉伸/灵活性",
    "sport": "球类/专项运动",
    "walking": "步行",
    "other": "其他",
}

#: 训练部位枚举。`full_body` 表示未细分，不等于"全身都练过"。
BODY_PARTS: tuple[str, ...] = (
    "full_body",
    "upper",
    "lower",
    "push",
    "pull",
    "legs",
    "core",
    "back",
    "chest",
    "shoulders",
    "arms",
)

BODY_PART_LABELS: dict[str, str] = {
    "full_body": "全身",
    "upper": "上肢",
    "lower": "下肢",
    "push": "推",
    "pull": "拉",
    "legs": "腿",
    "core": "核心",
    "back": "背",
    "chest": "胸",
    "shoulders": "肩",
    "arms": "手臂",
}

#: 中文 → 枚举。允许用户用日常说法录入，但落库只存枚举。
BODY_PART_ALIASES: dict[str, str] = {
    "全身": "full_body",
    "上肢": "upper",
    "下肢": "lower",
    "推": "push",
    "拉": "pull",
    "腿": "legs",
    "腿部": "legs",
    "核心": "core",
    "腹": "core",
    "腹部": "core",
    "背": "back",
    "背部": "back",
    "胸": "chest",
    "胸部": "chest",
    "肩": "shoulders",
    "肩部": "shoulders",
    "手臂": "arms",
    "胳膊": "arms",
}

COMPLETIONS: tuple[str, ...] = ("completed", "partial", "skipped")

COMPLETION_LABELS: dict[str, str] = {
    "completed": "完成",
    "partial": "部分完成",
    "skipped": "跳过",
}


@dataclass(frozen=True, slots=True)
class TrainingSession:
    session_id: str
    session_date: str
    started_at: str | None
    activity_type: str
    body_parts: tuple[str, ...]
    content: str
    duration_minutes: int | None
    perceived_intensity: int | None
    completion: str
    source: str
    notes: str | None
    created_at: str
    updated_at: str

    @property
    def activity_label(self) -> str:
        return ACTIVITY_LABELS.get(self.activity_type, self.activity_type)

    @property
    def body_part_labels(self) -> tuple[str, ...]:
        return tuple(BODY_PART_LABELS.get(part, part) for part in self.body_parts)


@dataclass(frozen=True, slots=True)
class RecoveryFeedback:
    feedback_id: str
    feedback_date: str
    energy: int | None
    fatigue: int | None
    soreness_parts: tuple[str, ...]
    soreness_level: int | None
    discomfort: bool
    discomfort_note: str | None
    created_at: str
    updated_at: str
    #: 固定值，不参与写入参数。存在这个字段是为了让导出与展示都能看出
    #: "这是主观反馈，不是设备测量"。
    source: str = "subjective"

    @property
    def soreness_labels(self) -> tuple[str, ...]:
        return tuple(BODY_PART_LABELS.get(part, part) for part in self.soreness_parts)


@dataclass(frozen=True, slots=True)
class TrainingPreferences:
    equipment: tuple[str, ...] = field(default_factory=tuple)
    days_per_week: int | None = None
    time_budget_minutes: int | None = None
    avoid_movements: tuple[str, ...] = field(default_factory=tuple)
    updated_at: str | None = None
