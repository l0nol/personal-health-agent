"""错误码与公共异常。

错误码稳定、可枚举，界面与日志都引用这里的常量，避免各处散落字符串。
"""

from __future__ import annotations

# ---- 拒绝类（该行不入库）----
E_MISSING_METRIC = "E_MISSING_METRIC"
E_UNKNOWN_METRIC = "E_UNKNOWN_METRIC"
E_BAD_DATE = "E_BAD_DATE"
E_PERIOD_INCOMPLETE = "E_PERIOD_INCOMPLETE"
E_PERIOD_NOT_ALLOWED = "E_PERIOD_NOT_ALLOWED"
E_UNIT_NOT_ALLOWED = "E_UNIT_NOT_ALLOWED"
E_NOT_A_NUMBER = "E_NOT_A_NUMBER"
E_MULTIPLE_METRICS = "E_MULTIPLE_METRICS"

# ---- 标记类（放行，但必须人工复核）----
F_MISSING_VALUE = "missing_value"
F_UNREADABLE = "unreadable"
F_OUT_OF_RANGE = "out_of_range"
F_UNIT_CONVERTED = "unit_converted"
F_UNIT_INFERRED = "unit_inferred"
F_VALUE_PARSED_FROM_TEXT = "value_parsed_from_text"
F_AMBIGUOUS_DATE = "ambiguous_date"
F_REVIEW_REQUIRED = "review_required"


class HealthAgentError(Exception):
    """本项目所有自定义异常的基类。"""


class ConfigError(HealthAgentError):
    """配置不合法（例如开启局域网但未设置密码）。"""


class UnitNotAllowedError(HealthAgentError):
    """单位不在白名单内，或无法换算到指标的标准单位。"""

    def __init__(self, raw_unit: str, target_unit: str) -> None:
        super().__init__(f"单位 {raw_unit!r} 无法用于标准单位 {target_unit!r}")
        self.raw_unit = raw_unit
        self.target_unit = target_unit


class RecordStateError(HealthAgentError):
    """记录当前状态不允许该操作（例如把已确认记录再驳回）。"""


class ConflictResolutionError(HealthAgentError):
    """冲突裁决请求不合法（已裁决过、或选择了不支持的裁决方式）。"""
