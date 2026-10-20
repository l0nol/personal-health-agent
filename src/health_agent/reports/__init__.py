"""报告层：每日简报、每周趋势、数字追溯。"""

from .builder import NumberRef, ReportPayload, build_daily, build_weekly, latest_data_date
from .format import format_delta, format_minutes, format_number

__all__ = [
    "NumberRef",
    "ReportPayload",
    "build_daily",
    "build_weekly",
    "format_delta",
    "format_minutes",
    "format_number",
    "latest_data_date",
]
