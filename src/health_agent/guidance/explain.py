"""可选的 AI 解释层。

边界（比"能不能调通"更重要）：
1. 只在用户显式要求时调用，默认不联网、不花钱；
2. 只发送已算好的摘要：不含 record_id / session_id、不含截图、不含账号、不含绝对日期；
3. AI 只能"换一种说法解释"，不能改类别、重点与依据；
4. 失败（超时/网络/HTTP/输出不合规）一律退回本地解释，建议本身不受影响；
5. 输出里出现摘要之外的数字，视为编造，直接拒绝。

这些规则都写在代码里，而不是写在提示词里——提示词是建议，代码才是约束。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..ai_client.base import LLMClient, LLMError
from .contract import FORBIDDEN_ADVICE_TERMS, Guidance

PROMPT_VERSION = "guidance-explain-1.0.0"

SYSTEM_PROMPT = (
    "你是健康习惯助手。你只能解释下面已经给出的建议，不能新增事实、不能改变结论、"
    "不能做诊断，不能建议高强度训练、减重、限制饮食或为补偿饮食而加练。"
    "只能使用摘要中出现过的数字，不要编造新数字。用中文，不超过 200 字，"
    "语气平实，不要用鼓励性夸张表达。"
)

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_ABSOLUTE_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_EMPTY_PARENS = re.compile(r"（\s*）")
_SPACES = re.compile(r"[ \t]+")
#: 解释层可以复述"不构成诊断"，但不能下诊断结论，所以单独判断句式而不是禁词。
_AI_FORBIDDEN_TERMS = tuple(t for t in FORBIDDEN_ADVICE_TERMS if t not in {"诊断", "治疗"})
_DIAGNOSTIC_CLAIM = re.compile(r"(诊断为|可以诊断|确诊|治疗方案|建议治疗|需要治疗)")
MAX_OUTPUT_CHARS = 600
MAX_DIGEST_CHARS = 2000


@dataclass(frozen=True, slots=True)
class Explanation:
    text: str
    source: str  # "local" | "ai"
    error: str | None = None
    prompt_version: str = PROMPT_VERSION


def build_digest(guidance: Guidance) -> str:
    """把建议压成"可发送摘要"。

    刻意不含：record_ids、session_ids、绝对日期、备注原文、账号信息。
    """
    lines = [
        f"结论类别：{guidance.category_label}",
        f"结论：{guidance.headline}",
        f"重点：{guidance.focus}",
        "依据：",
    ]
    for item in guidance.evidence:
        lines.append(f"- {item.label}：{item.value_text}（覆盖 {item.coverage}）")
    if guidance.alternatives:
        lines.append("替代项：" + "；".join(guidance.alternatives))
    if guidance.uncertainty:
        lines.append("不确定性：" + "；".join(guidance.uncertainty))
    lines.append("边界：仅作一般健康趋势与运动习惯参考，不构成诊断，不替代医生。")
    digest = "\n".join(lines)
    # 绝对日期不是判断所需，发出去只会多暴露一层生活轨迹
    digest = _ABSOLUTE_DATE.sub("", digest)
    digest = _EMPTY_PARENS.sub("", digest)
    digest = _SPACES.sub(" ", digest)
    return digest[:MAX_DIGEST_CHARS]


def _numbers(text: str) -> set[str]:
    return set(_NUMBER.findall(text))


def validate_output(text: str, *, digest: str) -> tuple[bool, str]:
    """输出合规检查。返回 (是否通过, 失败原因)。"""
    stripped = text.strip()
    if not stripped:
        return False, "empty"
    if len(stripped) > MAX_OUTPUT_CHARS:
        return False, "too_long"
    for term in _AI_FORBIDDEN_TERMS:
        if term in stripped:
            return False, f"forbidden_term:{term}"
    if _DIAGNOSTIC_CLAIM.search(stripped):
        return False, "diagnostic_claim"
    allowed = _numbers(digest)
    unknown = _numbers(stripped) - allowed
    if unknown:
        return False, "unknown_numbers:" + ",".join(sorted(unknown)[:3])
    return True, ""


def explain(guidance: Guidance, *, client: LLMClient | None = None) -> Explanation:
    """返回解释文本。client 为 None 或任何环节失败都退回本地解释。"""
    if client is None:
        return Explanation(text=guidance.local_explanation, source="local")

    digest = build_digest(guidance)
    try:
        result = client.complete(system=SYSTEM_PROMPT, user=digest, max_tokens=400)
    except LLMError as exc:
        return Explanation(
            text=guidance.local_explanation, source="local", error=f"llm:{exc.kind}"
        )
    except Exception as exc:  # noqa: BLE001 - 解释层不能拖垮建议本身
        return Explanation(
            text=guidance.local_explanation, source="local", error=f"error:{type(exc).__name__}"
        )

    ok, reason = validate_output(result.text, digest=digest)
    if not ok:
        return Explanation(text=guidance.local_explanation, source="local", error=reason)
    return Explanation(text=result.text.strip(), source="ai")
