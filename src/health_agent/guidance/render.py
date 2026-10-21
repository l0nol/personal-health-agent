"""把建议渲染成终端可读文本或 JSON。

渲染层不做判断：只把 Guidance 已有字段排版，避免"CLI 一套口径、JSON 另一套"。
"""

from __future__ import annotations

import json

from .contract import Guidance

_SOURCE_LABELS = {
    "device": "设备",
    "subjective": "主观反馈",
    "training_log": "训练日志",
    "preference": "偏好",
}


def render_text(guidance: Guidance, *, explanation: str | None = None) -> str:
    lines = [
        f"{guidance.for_date} 活动建议（规则 {guidance.rule_version}）",
        f"类别：{guidance.category_label}",
        f"结论：{guidance.headline}",
        f"重点：{guidance.focus}",
    ]
    if guidance.alternatives:
        lines.append("替代项：")
        lines.extend(f"  - {item}" for item in guidance.alternatives)
    if guidance.evidence:
        lines.append("依据：")
        for item in guidance.evidence:
            source = _SOURCE_LABELS.get(item.source, item.source)
            lines.append(f"  - [{source}] {item.label}：{item.value_text}（覆盖 {item.coverage}）")
    if guidance.uncertainty:
        lines.append("不确定性：")
        lines.extend(f"  - {item}" for item in guidance.uncertainty)
    lines.append("限制：")
    lines.extend(f"  - {item}" for item in guidance.limitations)

    freshness = (
        "未知" if guidance.data_freshness_days is None else f"{guidance.data_freshness_days} 天"
    )
    lines.append(f"数据新鲜度：{freshness}")
    lines.append(f"解释（{guidance.explanation_source}）：{explanation or guidance.local_explanation}")
    lines.append(f"反馈入口：{guidance.feedback_prompt}")
    lines.append(f"安全提示：{guidance.safety_notice}")
    return "\n".join(lines)


def render_json(guidance: Guidance, *, explanation: str | None = None) -> str:
    payload = guidance.to_dict()
    payload["explanation"] = explanation or guidance.local_explanation
    payload["explanation_source"] = guidance.explanation_source
    if guidance.explanation_error:
        payload["explanation_error"] = guidance.explanation_error
    return json.dumps(payload, ensure_ascii=False, indent=2)
