"""校验结果的统一结构。

一次校验的结果只有两种命运：
- `reject`：该行不入库，必须给出可读原因与错误码；
- `flag`：放行入库，但打上标记，标记必须能被下游（预览页 / 确认接口 / 统计）消费。

绝不存在"静默丢弃"或"静默放行"的第三种情况。
"""

from __future__ import annotations

from dataclasses import dataclass, field

SEVERITY_REJECT = "reject"
SEVERITY_FLAG = "flag"


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    field: str
    message: str
    severity: str


def reject(code: str, field_name: str, message: str) -> Issue:
    return Issue(code=code, field=field_name, message=message, severity=SEVERITY_REJECT)


def flag(code: str, field_name: str, message: str) -> Issue:
    return Issue(code=code, field=field_name, message=message, severity=SEVERITY_FLAG)


@dataclass(frozen=True, slots=True)
class NormalizedRecord:
    """校验通过后的单条记录（尚未确认，status 由存储层决定）。"""

    metric_name: str
    record_date: str
    period_type: str
    value: float | None
    unit: str
    raw_value: str | None = None
    raw_unit: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    source_app: str = "huawei_health"
    source_page: str | None = None
    extraction_method: str | None = None
    notes: str | None = None
    flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_value(self) -> bool:
        return self.value is not None


@dataclass(frozen=True, slots=True)
class ValidationResult:
    record: NormalizedRecord | None
    issues: tuple[Issue, ...] = field(default_factory=tuple)

    @property
    def accepted(self) -> bool:
        """无 reject 级问题即可入库（仍为 pending）。"""
        return not any(i.severity == SEVERITY_REJECT for i in self.issues)

    @property
    def rejections(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity == SEVERITY_REJECT)

    @property
    def flag_issues(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity == SEVERITY_FLAG)

    @property
    def flags(self) -> tuple[str, ...]:
        """下游可消费的全部标记。

        来源有两处：记录自身的 flags（单位换算、值由文本解析等）与 flag 级问题
        （不可读、超范围等）。此处合并去重，避免调用方只看到一半。
        """
        codes: list[str] = list(self.record.flags) if self.record is not None else []
        codes.extend(i.code for i in self.flag_issues)
        return tuple(dict.fromkeys(codes))

    def describe(self) -> str:
        if self.accepted:
            return "通过" + (f"（标记：{', '.join(self.flags)}）" if self.flags else "")
        return "拒绝: " + "; ".join(f"{i.code} {i.message}" for i in self.rejections)
