"""训练日志的校验错误。

错误码是给上层（CLI / 界面 / 测试）分支用的，消息是给人看的。
两者都保留，避免上层去解析中文文案。
"""

from __future__ import annotations


class TrainingValidationError(ValueError):
    def __init__(self, code: str, message: str, *, field: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.field = field

    def __str__(self) -> str:
        return str(self.args[0])


E_DATE = "E_TRAINING_DATE"
E_ACTIVITY = "E_ACTIVITY_TYPE"
E_BODY_PART = "E_BODY_PART"
E_COMPLETION = "E_COMPLETION"
E_RANGE = "E_RANGE"
E_SORENESS_PAIR = "E_SORENESS_PAIR"
E_NOT_FOUND = "E_NOT_FOUND"
E_EMPTY_UPDATE = "E_EMPTY_UPDATE"
