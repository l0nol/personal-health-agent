"""单行记录校验。

一次只判定一行，不做跨行判断（去重与冲突属于存储层职责）。
失败时**立即返回第一条阻塞原因**，保证错误可读、可复现，不做"尽量猜一下"。

判定顺序：指标名 → 日期 → 统计周期 → 单位 → 数值 → 值域。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from ..catalog import VALID_PERIOD_TYPES, MetricSpec, UnknownMetricError, get_metric
from ..errors import (
    E_BAD_DATE,
    E_MISSING_METRIC,
    E_NOT_A_NUMBER,
    E_PERIOD_INCOMPLETE,
    E_PERIOD_NOT_ALLOWED,
    E_UNIT_NOT_ALLOWED,
    E_UNKNOWN_METRIC,
    F_MISSING_VALUE,
    F_OUT_OF_RANGE,
    F_REVIEW_REQUIRED,
    F_UNIT_CONVERTED,
    F_UNIT_INFERRED,
    F_UNREADABLE,
    F_VALUE_PARSED_FROM_TEXT,
    UnitNotAllowedError,
)
from .result import Issue, NormalizedRecord, ValidationResult, flag, reject
from .units import resolve_unit

#: 页面上的"此处无值"占位符。命中即视为不可读，**不得**当作 0。
PLACEHOLDERS: frozenset[str] = frozenset(
    {"", "-", "--", "---", "—", "——", "n/a", "na", "null", "none", "nil", "?", "无"}
)

_PERIODS_NEEDING_RANGE: frozenset[str] = frozenset({"week", "month"})


def parse_record_date(raw: Any) -> str:
    """把日期解析为 ISO 'YYYY-MM-DD'。

    只接受明确的年月日；缺少年份、只有月份等模糊输入一律抛 ValueError，
    交由调用方拒绝该行（规格 §5：日期不明确时不得自动确认）。
    """
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, date):
        return raw.isoformat()
    if raw is None:
        raise ValueError("缺少日期")

    text = str(raw).strip()
    if not text:
        raise ValueError("日期为空")

    text = text.split("T")[0].split(" ")[0].replace("/", "-").replace(".", "-")
    for parser in (date.fromisoformat, _strptime_ymd):
        try:
            return parser(text).isoformat()  # type: ignore[operator]
        except (ValueError, TypeError):
            continue
    raise ValueError(f"无法识别的日期: {raw!r}")


def _strptime_ymd(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def _parse_duration_text(text: str) -> float | None:
    """解析 '1:23' / '0:45' 这种时长文本，返回分钟数。仅用于单位为 min 的指标。"""
    if ":" not in text:
        return None
    head, _, tail = text.partition(":")
    if not head.strip().isdigit() or not tail.strip().isdigit():
        return None
    minutes = int(tail.strip())
    if minutes >= 60:
        return None
    return float(int(head.strip()) * 60 + minutes)


def _parse_value(raw: Any, spec: MetricSpec) -> tuple[float | None, str | None, list[Issue]]:
    """返回 (数值, 原始文本, 问题列表)。数值为 None 表示缺失或不可读。"""
    issues: list[Issue] = []

    if isinstance(raw, bool):
        issues.append(reject(E_NOT_A_NUMBER, "value", "布尔值不是合法数值"))
        return None, None, issues

    if raw is None:
        issues.append(flag(F_MISSING_VALUE, "value", "该指标本次未提供数值，按缺失处理（不填 0）"))
        return None, None, issues

    if isinstance(raw, (int, float)):
        return float(raw), str(raw), issues

    if not isinstance(raw, str):
        issues.append(reject(E_NOT_A_NUMBER, "value", f"不支持的数值类型: {type(raw).__name__}"))
        return None, None, issues

    text = raw.strip()
    if text.lower() in PLACEHOLDERS:
        issues.append(flag(F_UNREADABLE, "value", f"数值不可读（原文 {raw!r}），保留原始文本待人工补录"))
        return None, text, issues

    candidate = text
    if spec.kind == "percent" and candidate.endswith("%"):
        candidate = candidate[:-1].strip()

    try:
        return float(candidate.replace(",", "")), text, issues
    except ValueError:
        pass

    if spec.unit == "min":
        minutes = _parse_duration_text(candidate)
        if minutes is not None:
            issues.append(flag(F_VALUE_PARSED_FROM_TEXT, "value", f"由文本 {text!r} 解析为 {minutes:g} 分钟"))
            return minutes, text, issues

    issues.append(reject(E_NOT_A_NUMBER, "value", f"无法解析为数值: {raw!r}"))
    return None, text, issues


def validate_row(
    raw: Mapping[str, Any],
    *,
    default_source_app: str = "huawei_health",
    period_type: str | None = None,
) -> ValidationResult:
    """校验一行原始数据。"""
    # --- 指标名 ---
    raw_metric = raw.get("metric_name")
    metric_name = "" if raw_metric is None else str(raw_metric).strip()
    if not metric_name:
        return ValidationResult(None, (reject(E_MISSING_METRIC, "metric_name", "缺少指标名"),))
    try:
        spec = get_metric(metric_name)
    except UnknownMetricError:
        return ValidationResult(
            None,
            (reject(E_UNKNOWN_METRIC, "metric_name", f"指标名未在注册表中: {metric_name!r}"),),
        )

    # --- 日期 ---
    try:
        record_date = parse_record_date(raw.get("record_date"))
    except ValueError as exc:
        return ValidationResult(None, (reject(E_BAD_DATE, "record_date", str(exc)),))

    # --- 统计周期 ---
    raw_period = period_type if period_type is not None else raw.get("period_type")
    period = "day" if raw_period is None or str(raw_period).strip() == "" else str(raw_period).strip()
    if period not in VALID_PERIOD_TYPES:
        return ValidationResult(
            None,
            (reject(E_PERIOD_NOT_ALLOWED, "period_type", f"未知的统计周期: {period!r}"),),
        )
    if period not in spec.period_types:
        return ValidationResult(
            None,
            (
                reject(
                    E_PERIOD_NOT_ALLOWED,
                    "period_type",
                    f"指标 {spec.name} 不允许 {period} 粒度，允许: {', '.join(spec.period_types)}",
                ),
            ),
        )

    period_start: str | None = None
    period_end: str | None = None
    raw_start = raw.get("period_start")
    raw_end = raw.get("period_end")
    try:
        if raw_start is not None and str(raw_start).strip() != "":
            period_start = parse_record_date(raw_start)
        if raw_end is not None and str(raw_end).strip() != "":
            period_end = parse_record_date(raw_end)
    except ValueError as exc:
        return ValidationResult(None, (reject(E_BAD_DATE, "period_start/period_end", str(exc)),))

    if period in _PERIODS_NEEDING_RANGE and (period_start is None or period_end is None):
        return ValidationResult(
            None,
            (
                reject(
                    E_PERIOD_INCOMPLETE,
                    "period_start/period_end",
                    f"{period} 粒度的记录必须给出 period_start 与 period_end",
                ),
            ),
        )

    # --- 单位 ---
    raw_unit = raw.get("unit")
    raw_unit_text = None if raw_unit is None else str(raw_unit).strip() or None
    try:
        resolution = resolve_unit(raw_unit_text, spec.unit)
    except UnitNotAllowedError as exc:
        return ValidationResult(None, (reject(E_UNIT_NOT_ALLOWED, "unit", str(exc)),))

    flags: list[str] = []
    if resolution.inferred:
        flags.append(F_UNIT_INFERRED)
    if resolution.converted:
        flags.append(F_UNIT_CONVERTED)

    # --- 数值 ---
    value, raw_value, value_issues = _parse_value(raw.get("value"), spec)
    issues: list[Issue] = list(value_issues)
    for issue in value_issues:
        if issue.severity == "reject":
            return ValidationResult(None, tuple(issues))
        flags.append(issue.code)

    if value is not None and resolution.factor != 1.0:
        value = round(value * resolution.factor, 6)

    # --- 值域 ---
    if value is not None and spec.min_valid is not None and value < spec.min_valid:
        issues.append(
            flag(
                F_OUT_OF_RANGE,
                "value",
                f"{value:g} 低于合理下限 {spec.min_valid:g}{spec.unit}，已保留原值，需人工复核",
            )
        )
        flags.append(F_OUT_OF_RANGE)
    elif value is not None and spec.max_valid is not None and value > spec.max_valid:
        issues.append(
            flag(
                F_OUT_OF_RANGE,
                "value",
                f"{value:g} 高于合理上限 {spec.max_valid:g}{spec.unit}，已保留原值，需人工复核",
            )
        )
        flags.append(F_OUT_OF_RANGE)

    if spec.review_required:
        flags.append(F_REVIEW_REQUIRED)

    record = NormalizedRecord(
        metric_name=spec.name,
        record_date=record_date,
        period_type=period,
        value=value,
        unit=spec.unit,
        raw_value=raw_value,
        raw_unit=raw_unit_text,
        period_start=period_start,
        period_end=period_end,
        source_app=str(raw.get("source_app") or default_source_app).strip(),
        source_page=None if raw.get("source_page") is None else str(raw.get("source_page")),
        extraction_method=None
        if raw.get("extraction_method") is None
        else str(raw.get("extraction_method")),
        notes=None if raw.get("notes") is None else str(raw.get("notes")),
        flags=tuple(dict.fromkeys(flags)),
    )
    return ValidationResult(record, tuple(issues))
