"""把数值变成人话。

只做单位层面的格式化，不添加任何判断——"多 27 分钟"这类差值由报告层计算后传入，
这里不猜。
"""

from __future__ import annotations

UNIT_LABELS: dict[str, str] = {
    "min": "",
    "bpm": "次/分",
    "brpm": "次/分",
    "count": "",
    "kcal": "千卡",
    "%": "%",
    "ms": "毫秒",
    "score": "分",
    "km": "公里",
    "kg": "公斤",
    "mmHg": "mmHg",
    "mmol/L": "mmol/L",
    "°C": "°C",
    "mL/kg/min": "mL/kg/min",
}


def format_minutes(minutes: float) -> str:
    total = int(round(minutes))
    sign = "-" if total < 0 else ""
    total = abs(total)
    hours, mins = divmod(total, 60)
    if hours and mins:
        return f"{sign}{hours} 小时 {mins} 分"
    if hours:
        return f"{sign}{hours} 小时"
    return f"{sign}{mins} 分钟"


#: 这些单位的均值显示成整数——"58.8571 次/分"不是人能读的心率。
_INTEGER_UNITS = frozenset({"bpm", "brpm", "count", "kcal", "ms", "mmHg"})


def _trim(value: float, unit: str) -> str:
    """均值是算出来的，但显示要像人写的：整数单位取整，其余最多一位小数。"""
    if unit in _INTEGER_UNITS:
        return f"{value:,.0f}"
    rounded = round(value, 1)
    return f"{rounded:g}"


def format_number(value: float | None, unit: str, metric_name: str = "") -> str:
    if value is None:
        return "—"

    if unit == "min":
        return format_minutes(value)
    if unit == "count":
        suffix = "步" if metric_name == "steps_count" else "次"
        return f"{_trim(value, unit)} {suffix}"
    if unit == "kcal":
        return f"{_trim(value, unit)} 千卡"
    if unit == "%":
        return f"{_trim(value, unit)}%"
    if unit == "score":
        return f"{_trim(value, unit)} 分"
    label = UNIT_LABELS.get(unit, unit)
    return f"{_trim(value, unit)} {label}".strip()


def format_delta(delta: float | None, unit: str, metric_name: str = "") -> str:
    if delta is None:
        return "—"
    sign = "+" if delta > 0 else ("-" if delta < 0 else "±")
    return f"{sign}{format_number(abs(delta), unit, metric_name)}"
