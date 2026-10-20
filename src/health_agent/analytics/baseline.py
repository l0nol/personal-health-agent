"""个人基线：中位数 + MAD。

为什么不用均值 + 标准差：
- 生理指标天然有离群值（熬夜、发烧、旅行），均值会被单个异常日拉住；
- 标准差本身也被离群值放大，导致"异常"看起来正常。

MAD（绝对中位差）对离群值稳健，乘以 1.4826 后与正态分布下的标准差可比。
样本不够就明确返回 insufficient，绝不拿 3 天数据建基线。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median, quantiles

from .aggregate import load_points

#: 让 MAD 与正态分布标准差可比的常数。
MAD_SCALE = 1.4826

#: IQR 换算到标准差的常数（正态分布下 IQR ≈ 1.349σ）。
IQR_TO_SIGMA = 1.349

STATUS_ACTIVE = "active"
STATUS_INSUFFICIENT = "insufficient"


@dataclass(frozen=True, slots=True)
class Baseline:
    metric_name: str
    window_start: str
    window_end: str
    window_days: int
    sample_count: int
    min_samples: int
    mad_factor: float
    center: float | None
    spread: float | None
    threshold: float | None
    status: str
    method: str = "median_mad"
    reason: str = ""

    @property
    def active(self) -> bool:
        return self.status == STATUS_ACTIVE

    def is_deviation(self, value: float | None) -> bool:
        """该值是否偏离基线。基线不可用时一律返回 False——不猜。"""
        if not self.active or value is None or self.threshold is None or self.center is None:
            return False
        return abs(value - self.center) > self.threshold


def compute_baseline(
    conn: sqlite3.Connection,
    metric_name: str,
    end_day: date,
    *,
    window_days: int = 28,
    min_samples: int = 14,
    mad_factor: float = 2.5,
) -> Baseline:
    start_day = end_day - timedelta(days=window_days - 1)
    points = load_points(conn, metric_name, start_day.isoformat(), end_day.isoformat())
    values = [point.value for point in points]
    count = len(values)

    def _insufficient(reason: str) -> Baseline:
        return Baseline(
            metric_name=metric_name,
            window_start=start_day.isoformat(),
            window_end=end_day.isoformat(),
            window_days=window_days,
            sample_count=count,
            min_samples=min_samples,
            mad_factor=mad_factor,
            center=None,
            spread=None,
            threshold=None,
            status=STATUS_INSUFFICIENT,
            reason=reason,
        )

    if count < min_samples:
        return _insufficient(f"有效记录 {count}/{min_samples} 天，样本不足")

    center = float(median(values))
    mad = float(median([abs(value - center) for value in values]))
    method = "median_mad"
    spread = mad * MAD_SCALE

    if spread == 0:
        # 超过一半的值完全相同（整数指标很常见）时 MAD 会退化为 0。
        # 此时改用 IQR 估计离散度，而不是直接判定"基线无效"。
        quartiles = quantiles(values, n=4, method="inclusive")
        iqr = quartiles[2] - quartiles[0]
        if iqr > 0:
            method = "iqr_fallback"
            spread = iqr / IQR_TO_SIGMA

    if spread == 0:
        return _insufficient("基线离散度为 0（所有记录完全相同），无法判定偏离")

    spread = round(spread, 6)

    return Baseline(
        metric_name=metric_name,
        window_start=start_day.isoformat(),
        window_end=end_day.isoformat(),
        window_days=window_days,
        sample_count=count,
        min_samples=min_samples,
        mad_factor=mad_factor,
        center=round(center, 6),
        spread=spread,
        threshold=round(mad_factor * spread, 6),
        status=STATUS_ACTIVE,
        method=method,
    )


def save_baseline(conn: sqlite3.Connection, baseline: Baseline) -> str:
    import uuid

    from ..storage.repository import now_iso

    baseline_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO baselines (
            baseline_id, metric_name, window_days, sample_count, center_value,
            spread_value, mad_factor, method, valid_from, valid_until,
            computed_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            baseline_id,
            baseline.metric_name,
            baseline.window_days,
            baseline.sample_count,
            baseline.center if baseline.center is not None else 0.0,
            baseline.spread if baseline.spread is not None else 0.0,
            baseline.mad_factor,
            baseline.method,
            baseline.window_start,
            baseline.window_end,
            now_iso(),
            baseline.status,
        ),
    )
    conn.commit()
    return baseline_id
