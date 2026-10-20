"""校验层：单位白名单、值域、日期与统计周期。纯函数，不碰数据库。"""

from .result import Issue, NormalizedRecord, ValidationResult
from .rules import parse_record_date, validate_row
from .units import CONVERSIONS, KNOWN_UNITS, UNIT_ALIASES, UnitResolution, resolve_unit

__all__ = [
    "CONVERSIONS",
    "Issue",
    "KNOWN_UNITS",
    "NormalizedRecord",
    "UNIT_ALIASES",
    "UnitResolution",
    "ValidationResult",
    "parse_record_date",
    "resolve_unit",
    "validate_row",
]
