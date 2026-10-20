"""单位白名单与换算。

规格 §5 要求"单位必须在白名单中，必要时转换到标准单位并保留原始值"。
本模块只允许两类换算：时长（h/s → min）与能量（kJ → kcal）。

刻意**不提供**任何 百分比 → 时长 的换算路径：规格 §4 明确"若截图只显示比例，
另存比例字段，不得未经确认换算成时长"。这条约束由代码结构保证，而不是靠注释提醒。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ..errors import UnitNotAllowedError

#: 别名 → 该别名可能对应的标准单位。
#: 同一个中文别名可能对应多个单位（如"分"既可能是分钟也可能是评分），
#: 由目标指标的标准单位来做消歧，不做猜测。
_ALIAS_GROUPS: dict[str, tuple[str, ...]] = {
    "min": ("min", "mins", "minute", "minutes", "分钟", "分"),
    "h": ("h", "hr", "hrs", "hour", "hours", "小时"),
    "s": ("s", "sec", "secs", "second", "seconds", "秒"),
    "bpm": ("bpm", "次/分", "次/分钟", "拍/分", "拍/分钟"),
    "brpm": ("brpm", "次/分", "次/分钟", "呼吸/分"),
    "count": ("count", "counts", "次", "个", "步", "步数", "次数"),
    "kcal": ("kcal", "千卡", "大卡", "卡路里"),
    "kJ": ("kJ", "kj", "千焦"),
    "%": ("%", "percent", "pct", "百分比"),
    "ms": ("ms", "毫秒"),
    "score": ("score", "scores", "评分", "分数", "分"),
    "km": ("km", "公里", "千米"),
    "kg": ("kg", "公斤", "千克"),
    "mmHg": ("mmHg", "mmhg", "毫米汞柱"),
    "mmol/L": ("mmol/L", "mmol/l", "mmol", "毫摩尔/升"),
    "°C": ("°C", "°c", "℃", "摄氏度", "度"),
    "mL/kg/min": ("mL/kg/min", "ml/kg/min"),
}

def _build_alias_index() -> dict[str, frozenset[str]]:
    """别名反向索引。允许同一个别名出现在多个组里（如"分"：分钟 / 评分）。"""
    acc: dict[str, set[str]] = defaultdict(set)
    for unit, aliases in _ALIAS_GROUPS.items():
        for alias in aliases:
            acc[alias].add(unit)
    return {alias: frozenset(units) for alias, units in acc.items()}


UNIT_ALIASES: dict[str, frozenset[str]] = _build_alias_index()

#: 允许的换算：源单位 → 目标单位 的乘数。
#: 只收"无损且语义明确"的换算，比例换算一律不在表内。
CONVERSIONS: dict[tuple[str, str], float] = {
    ("h", "min"): 60.0,
    ("s", "min"): 1.0 / 60.0,
    ("kJ", "kcal"): 0.2390057,
}

#: 允许出现在记录里的标准单位全集。
KNOWN_UNITS: frozenset[str] = frozenset(
    {"min", "h", "s", "bpm", "brpm", "count", "kcal", "kJ", "%", "ms", "score",
     "km", "kg", "mmHg", "mmol/L", "°C", "mL/kg/min"}
)


@dataclass(frozen=True, slots=True)
class UnitResolution:
    """一次单位解析的结果。"""

    unit: str
    factor: float
    converted: bool
    inferred: bool


def _lookup(raw_unit: str) -> frozenset[str] | None:
    key = raw_unit.strip()
    if not key:
        return None
    found = UNIT_ALIASES.get(key)
    if found is None:
        found = UNIT_ALIASES.get(key.lower())
    return found


def resolve_unit(raw_unit: str | None, target_unit: str) -> UnitResolution:
    """把原始单位解析到指标的标准单位。

    - 原始单位为空：按指标的默认单位处理，并标记 inferred（需人工留意）。
    - 单位可换算（h→min、kJ→kcal）：返回换算系数，调用方负责乘算并保留原始值。
    - 其余情况一律抛 UnitNotAllowedError，不做兜底猜测。
    """
    if raw_unit is None or raw_unit.strip() == "":
        return UnitResolution(unit=target_unit, factor=1.0, converted=False, inferred=True)

    candidates = _lookup(raw_unit)
    if candidates is None:
        raise UnitNotAllowedError(raw_unit, target_unit)

    if target_unit in candidates:
        return UnitResolution(unit=target_unit, factor=1.0, converted=False, inferred=False)

    for candidate in candidates:
        factor = CONVERSIONS.get((candidate, target_unit))
        if factor is not None:
            return UnitResolution(unit=target_unit, factor=factor, converted=True, inferred=False)

    raise UnitNotAllowedError(raw_unit, target_unit)
