"""每日建议的输出契约。

契约先于规则：模板、CLI、未来的 AI 解释都消费同一个对象，
不允许各自从数据库再拼一份"差不多的建议"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CATEGORY_REST = "rest"
CATEGORY_LIGHT = "light_activity"
CATEGORY_REGULAR = "regular_training"
CATEGORY_INSUFFICIENT = "insufficient_data"

CATEGORIES: tuple[str, ...] = (
    CATEGORY_REST,
    CATEGORY_LIGHT,
    CATEGORY_REGULAR,
    CATEGORY_INSUFFICIENT,
)

CATEGORY_LABELS: dict[str, str] = {
    CATEGORY_REST: "休息",
    CATEGORY_LIGHT: "轻松活动",
    CATEGORY_REGULAR: "常规训练",
    CATEGORY_INSUFFICIENT: "资料不足",
}

SOURCE_DEVICE = "device"
SOURCE_SUBJECTIVE = "subjective"
SOURCE_TRAINING = "training_log"
SOURCE_PREFERENCE = "preference"

#: 建议正文里禁止出现的词。命中说明规则写错了，直接抛错而不是发出去。
#: 注意：`limitations` 里的"不构成诊断""不提供减重建议"属于边界声明，
#: 不参与此守卫——守卫只约束"建议本身"。
FORBIDDEN_ADVICE_TERMS: tuple[str, ...] = (
    "高强度",
    "力竭",
    "减脂",
    "燃脂",
    "补偿",
    "诊断",
    "治疗",
)

#: 所有建议都必须带的边界声明。
BASE_LIMITATIONS: tuple[str, ...] = (
    "仅作一般健康趋势与运动习惯参考，不构成诊断，也不替代医生或教练。",
    "未记录年龄：睡眠参考值采用成人常见 7–9 小时的下沿（默认 420 分钟），未按年龄调整；"
    "青少年一般参考每晚 8–10 小时，若你处于青少年阶段请以此为准并咨询可信成年人。",
    "手环数据是消费级设备估算，不是医疗测量。",
    "不会安排高强度训练，也不提供减重、限制饮食或为补偿饮食而加练的建议。",
)

SAFETY_NOTICE = (
    "如出现胸痛、眩晕、明显气短、疼痛加重或其他明显不适，请立即停止运动；"
    "需要时向可信成年人或专业人员求助。本工具不判断原因。"
)

FEEDBACK_PROMPT = "记录今天的精力、疲劳与酸痛后，建议会更贴合你的状态（CLI: `health-agent feedback`）。"


@dataclass(frozen=True, slots=True)
class Evidence:
    """一条"依据"。

    - 设备数据必须有 `record_ids`，保证能点回原始记录；
    - 训练日志用 `session_ids`；
    - 主观反馈标明 `source=subjective`，不得伪装成设备测量。
    """

    label: str
    value_text: str
    window_start: str
    window_end: str
    coverage: str
    source: str
    record_ids: tuple[str, ...] = ()
    session_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "value_text": self.value_text,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "coverage": self.coverage,
            "source": self.source,
            "record_ids": list(self.record_ids),
            "session_ids": list(self.session_ids),
        }


@dataclass(frozen=True, slots=True)
class Guidance:
    for_date: str
    category: str
    headline: str
    focus: str
    alternatives: tuple[str, ...]
    evidence: tuple[Evidence, ...]
    limitations: tuple[str, ...]
    uncertainty: tuple[str, ...]
    feedback_prompt: str
    generated_at: str
    rule_version: str
    data_freshness_days: int | None
    safety_notice: str
    local_explanation: str
    #: AI 解释不得覆盖本地结论；这里记录最终展示的解释来源，便于审计。
    explanation_source: str = "local"
    explanation_error: str | None = None
    #: 仅供调试/测试，不进入对外 JSON 的顶层契约。
    signals: dict[str, Any] = field(default_factory=dict)

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS[self.category]

    @property
    def is_rest(self) -> bool:
        return self.category == CATEGORY_REST

    def to_dict(self, *, include_signals: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "for_date": self.for_date,
            "category": self.category,
            "category_label": self.category_label,
            "headline": self.headline,
            "focus": self.focus,
            "alternatives": list(self.alternatives),
            "evidence": [item.to_dict() for item in self.evidence],
            "limitations": list(self.limitations),
            "uncertainty": list(self.uncertainty),
            "feedback_prompt": self.feedback_prompt,
            "data_freshness_days": self.data_freshness_days,
            "generated_at": self.generated_at,
            "rule_version": self.rule_version,
            "safety_notice": self.safety_notice,
        }
        if include_signals:
            payload["signals"] = self.signals
        return payload
