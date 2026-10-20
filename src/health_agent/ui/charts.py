"""图表几何计算（纯函数）。

只做"把数值变成 SVG 坐标"，不含任何统计或业务判断：
输入必须是已经算好的序列，缺失用 None 表示。

三条不可妥协的规则（与设计规范一致）：
1. 缺失不是 0：折线在缺失处**断开**，柱状缺失画空心柱。
2. 区间指标只画上下限与之间的带，**不画中间线**——那条线等于伪造平均值。
3. 空序列返回 has_data=False，由模板显示空状态，而不是画一个空坐标系。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PAD_LEFT = 46
PAD_RIGHT = 14
PAD_TOP = 18
PAD_BOTTOM = 26


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    label: str
    value: float | None
    record_id: str | None = None


@dataclass(frozen=True, slots=True)
class BandPoint:
    label: str
    low: float | None
    high: float | None


def _domain(values: list[float]) -> tuple[float, float]:
    low, high = min(values), max(values)
    if high == low:
        return low - 1, high + 1
    span = high - low
    return low - span * 0.08, high + span * 0.08


def _ticks(low: float, high: float, count: int = 3) -> list[float]:
    if count <= 1:
        return [low]
    step = (high - low) / (count - 1)
    return [low + step * i for i in range(count)]


def _fmt(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 100:
        return f"{value:.0f}"
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _x_positions(count: int, width: int) -> list[float]:
    left, right = PAD_LEFT, width - PAD_RIGHT
    if count <= 1:
        return [(left + right) / 2]
    step = (right - left) / (count - 1)
    return [left + step * i for i in range(count)]


def _y_map(low: float, high: float, height: int):
    top, bottom = PAD_TOP, height - PAD_BOTTOM

    def scale(value: float) -> float:
        ratio = (value - low) / (high - low)
        return bottom - ratio * (bottom - top)

    return scale


def build_line(
    points: list[SeriesPoint],
    *,
    width: int = 640,
    height: int = 220,
) -> dict[str, Any]:
    values = [p.value for p in points if p.value is not None]
    model: dict[str, Any] = {
        "kind": "line",
        "width": width,
        "height": height,
        "has_data": bool(values),
        "y_ticks": [],
        "x_labels": [],
        "segments": [],
        "dots": [],
        "missing": [],
    }
    if not values:
        return model

    low, high = _domain(values)
    scale = _y_map(low, high, height)
    xs = _x_positions(len(points), width)

    model["y_ticks"] = [
        {"y": round(scale(value), 1), "label": _fmt(value)} for value in _ticks(low, high)
    ]
    indexes = [0, len(points) // 2, len(points) - 1]
    model["x_labels"] = [
        {"x": round(xs[i], 1), "text": points[i].label} for i in dict.fromkeys(indexes)
    ]

    # 连续有效点成段，缺失处断开（绝不跨越缺失连线）
    segments: list[list[str]] = []
    current: list[str] = []
    for index, point in enumerate(points):
        if point.value is None:
            if len(current) > 1:
                segments.append(current)
            current = []
            model["missing"].append({"x": round(xs[index], 1), "label": point.label})
            continue
        current.append(f"{xs[index]:.1f},{scale(point.value):.1f}")
        model["dots"].append(
            {
                "x": round(xs[index], 1),
                "y": round(scale(point.value), 1),
                "value": point.value,
                "label": point.label,
                "record_id": point.record_id,
            }
        )
    if len(current) > 1:
        segments.append(current)
    elif len(current) == 1:
        # 单点无法连线，保留点由 dots 呈现
        pass

    model["segments"] = [" ".join(segment) for segment in segments]
    return model


def build_bars(
    points: list[SeriesPoint],
    *,
    width: int = 640,
    height: int = 220,
    target: float | None = None,
) -> dict[str, Any]:
    values = [p.value for p in points if p.value is not None]
    model: dict[str, Any] = {
        "kind": "bars",
        "width": width,
        "height": height,
        "has_data": bool(values),
        "bars": [],
        "y_ticks": [],
        "x_labels": [],
        "target": None,
    }
    if not values:
        return model

    domain_values = list(values)
    if target is not None:
        domain_values.append(target)
    low, high = _domain(domain_values)
    low = min(low, 0.0)
    scale = _y_map(low, high, height)

    left, right = PAD_LEFT, width - PAD_RIGHT
    slot = (right - left) / max(len(points), 1)
    bar_width = max(min(slot * 0.55, 28), 4)
    baseline = scale(max(low, 0.0))

    for index, point in enumerate(points):
        center = left + slot * (index + 0.5)
        if point.value is None:
            model["bars"].append(
                {
                    "x": round(center - bar_width / 2, 1),
                    "y": round(baseline - 10, 1),
                    "w": round(bar_width, 1),
                    "h": 10,
                    "missing": True,
                    "label": point.label,
                }
            )
            continue
        y = scale(point.value)
        model["bars"].append(
            {
                "x": round(center - bar_width / 2, 1),
                "y": round(min(y, baseline), 1),
                "w": round(bar_width, 1),
                "h": round(abs(baseline - y), 1),
                "missing": False,
                "label": point.label,
                "value": point.value,
                "record_id": point.record_id,
            }
        )

    model["y_ticks"] = [
        {"y": round(scale(value), 1), "label": _fmt(value)} for value in _ticks(low, high)
    ]
    indexes = [0, len(points) // 2, len(points) - 1]
    model["x_labels"] = [
        {"x": round(left + slot * (i + 0.5), 1), "text": points[i].label}
        for i in dict.fromkeys(indexes)
    ]
    if target is not None:
        model["target"] = {"y": round(scale(target), 1), "label": _fmt(target)}
    return model


def build_band(
    points: list[BandPoint],
    *,
    width: int = 640,
    height: int = 220,
) -> dict[str, Any]:
    pairs = [(p.low, p.high) for p in points if p.low is not None and p.high is not None]
    model: dict[str, Any] = {
        "kind": "band",
        "width": width,
        "height": height,
        "has_data": bool(pairs),
        "area": "",
        "low_line": "",
        "high_line": "",
        "y_ticks": [],
        "x_labels": [],
    }
    if not pairs:
        return model

    low, high = _domain([value for pair in pairs for value in pair])
    scale = _y_map(low, high, height)
    xs = _x_positions(len(points), width)

    upper, lower, valid_x = [], [], []
    for index, point in enumerate(points):
        if point.low is None or point.high is None:
            continue
        valid_x.append(xs[index])
        upper.append(f"{xs[index]:.1f},{scale(point.high):.1f}")
        lower.append(f"{xs[index]:.1f},{scale(point.low):.1f}")

    if len(valid_x) >= 2:
        model["area"] = "M" + " L".join(upper) + " L" + " L".join(reversed(lower)) + " Z"
    model["high_line"] = " ".join(upper)
    model["low_line"] = " ".join(lower)
    model["y_ticks"] = [
        {"y": round(scale(value), 1), "label": _fmt(value)} for value in _ticks(low, high)
    ]
    indexes = [0, len(points) // 2, len(points) - 1]
    model["x_labels"] = [
        {"x": round(xs[i], 1), "text": points[i].label} for i in dict.fromkeys(indexes)
    ]
    return model
