"""统计层：窗口聚合、数据质量、个人基线。"""

from .aggregate import (
    MIN_SAMPLE_DAYS,
    MIN_WEEK_COVERAGE_DAYS,
    STATUS_INSUFFICIENT,
    STATUS_NO_DATA,
    STATUS_OK,
    MetricSummary,
    Point,
    load_points,
    point_on,
    range_days,
    summarize,
    summarize_many,
    week_bounds,
    weekly_buckets,
)
from .baseline import Baseline, compute_baseline, save_baseline
from .quality import QualityReport, summarize_quality

__all__ = [
    "MIN_SAMPLE_DAYS",
    "MIN_WEEK_COVERAGE_DAYS",
    "STATUS_INSUFFICIENT",
    "STATUS_NO_DATA",
    "STATUS_OK",
    "Baseline",
    "MetricSummary",
    "Point",
    "QualityReport",
    "compute_baseline",
    "load_points",
    "point_on",
    "range_days",
    "save_baseline",
    "summarize",
    "summarize_many",
    "summarize_quality",
    "week_bounds",
    "weekly_buckets",
]
