"""提醒规则引擎。

四条规则，全部只读已确认数据：
  1. sleep_deficit_trend  近 7 天有 3 天以上睡眠明显低于目标
  2. resting_hr_shift     静息心率连续 3 天比个人基线高/低超过 8 次/分
  3. baseline_deviation   指定指标连续 3 天偏离个人基线（MAD 法）
  4. data_gap             近 7 天缺失率过高（补录提示，不是健康提醒）

刻意**没有**任何解读心律筛查结果的规则：那类字段只保存设备页面原文。
引擎也不做疾病判断，每条提醒只给触发原因、证据记录和低风险建议。
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from ..analytics.aggregate import summarize, tracked_metrics
from ..analytics.baseline import compute_baseline
from ..catalog import get_metric
from ..reports.format import format_minutes, format_number
from ..storage import alerts as alert_store


@dataclass(frozen=True, slots=True)
class RuleConfig:
    """阈值集中在这里，全部可调；界面与 CLI 都从这里构造。"""

    sleep_target_minutes: int = 420
    #: 目标是否由用户显式设定。False 时提醒文案必须说明"这是默认参考值"。
    sleep_target_user_supplied: bool = False
    sleep_deficit_ratio: float = 0.9
    sleep_deficit_min_days: int = 3
    min_coverage_days: int = 4
    deviation_days: int = 3
    baseline_window_days: int = 28
    baseline_min_samples: int = 14
    baseline_mad_factor: float = 2.5
    resting_hr_shift_bpm: float = 8.0
    deviation_metrics: tuple[str, ...] = ("sleep_avg_hr_bpm", "steps_count")
    data_gap_rate: float = 0.5
    data_gap_metrics: tuple[str, ...] = ("night_sleep_minutes", "resting_hr_bpm", "steps_count")
    cooldown_hours: int = 72


@dataclass(frozen=True, slots=True)
class AlertCandidate:
    rule_id: str
    metric_name: str | None
    severity: str
    window_start: str
    window_end: str
    trigger_reason: str
    evidence_record_ids: tuple[str, ...]
    suggested_action: str

    @property
    def dedup_key(self) -> str:
        payload = f"{self.rule_id}|{self.metric_name or ''}|{self.window_start}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


#: 本版本**没有**实现年龄适配：没有年龄字段，也没有按年龄段取不同建议。
#: 这个常量是为了让文档和测试能断言"没做"，而不是让文案自己去猜。
AGE_ADAPTATION_IMPLEMENTED: bool = False

#: 未配置目标时采用的参考区间。写在这里而不是藏在文案里，方便日后核对来源。
DEFAULT_TARGET_REFERENCE_RANGE = "成人常见建议 7–9 小时"


def describe_sleep_target(config: "RuleConfig") -> str:
    """返回睡眠目标的可读描述，明确区分"用户设定"与"程序默认"。

    这一步是刻意的：把默认值说成"你的目标"会让用户误以为系统了解他的情况。
    """
    target = format_minutes(config.sleep_target_minutes)
    if config.sleep_target_user_supplied:
        return f"你设定的目标 {target}"
    return (
        f"默认参考值 {target}（{DEFAULT_TARGET_REFERENCE_RANGE}的下沿；"
        "未按年龄、性别或作息调整）"
    )


def _window(today: date, days: int) -> tuple[date, date]:
    return today - timedelta(days=days - 1), today


def sleep_deficit_trend(
    conn: sqlite3.Connection, config: RuleConfig, today: date
) -> list[AlertCandidate]:
    metric = "night_sleep_minutes"
    spec = get_metric(metric)
    start, end = _window(today, 7)
    summary = summarize(conn, spec, start, end)

    if summary.sample_days < config.min_coverage_days:
        return []

    threshold = config.sleep_target_minutes * config.sleep_deficit_ratio
    short_days = [point for point in summary.points if point.value < threshold]
    if len(short_days) < config.sleep_deficit_min_days:
        return []

    reason = (
        f"近 7 天有 {len(short_days)} 天夜间睡眠低于 {format_minutes(threshold)}"
        f"（{describe_sleep_target(config)} 的 {config.sleep_deficit_ratio:.0%}）"
        "。仅作趋势观察，不构成诊断"
    )
    return [
        AlertCandidate(
            rule_id="sleep_deficit_trend",
            metric_name=metric,
            severity="warn",
            window_start=summary.window_start,
            window_end=summary.window_end,
            trigger_reason=reason,
            evidence_record_ids=tuple(point.record_id for point in short_days),
            suggested_action="今晚比平时提前 30 分钟上床；若连续两周以上如此，考虑就医排查原因",
        )
    ]


def _consecutive_deviation(
    conn: sqlite3.Connection,
    metric: str,
    today: date,
    days: int,
    predicate,
) -> list:
    """取最近 days 天，要求每天都有记录且每天都满足 predicate。"""
    start, end = _window(today, days)
    summary = summarize(conn, get_metric(metric), start, end)
    if summary.sample_days < days:
        return []
    if all(predicate(point.value) for point in summary.points):
        return summary.points
    return []


def resting_hr_shift(
    conn: sqlite3.Connection, config: RuleConfig, today: date
) -> list[AlertCandidate]:
    metric = "resting_hr_bpm"
    spec = get_metric(metric)
    baseline = compute_baseline(
        conn,
        metric,
        today - timedelta(days=1),
        window_days=config.baseline_window_days,
        min_samples=config.baseline_min_samples,
        mad_factor=config.baseline_mad_factor,
    )
    if not baseline.active or baseline.center is None:
        return []

    points = _consecutive_deviation(
        conn,
        metric,
        today,
        config.deviation_days,
        lambda value: abs(value - baseline.center) > config.resting_hr_shift_bpm,
    )
    if not points:
        return []

    direction = "高于" if points[0].value > baseline.center else "低于"
    reason = (
        f"静息心率连续 {config.deviation_days} 天{direction}个人基线 "
        f"{format_number(baseline.center, spec.unit)} 超过 "
        f"{config.resting_hr_shift_bpm:g} 次/分"
        f"（基线由 {baseline.sample_count} 天记录算出）"
    )
    return [
        AlertCandidate(
            rule_id="resting_hr_shift",
            metric_name=metric,
            severity="warn",
            window_start=points[0].record_date,
            window_end=points[-1].record_date,
            trigger_reason=reason,
            evidence_record_ids=tuple(point.record_id for point in points),
            suggested_action="先排除熬夜、饮酒、发烧、剧烈运动和情绪波动；持续偏高请就医确认",
        )
    ]


def baseline_deviation(
    conn: sqlite3.Connection, config: RuleConfig, today: date
) -> list[AlertCandidate]:
    candidates: list[AlertCandidate] = []
    for metric in config.deviation_metrics:
        spec = get_metric(metric)
        baseline = compute_baseline(
            conn,
            metric,
            today - timedelta(days=1),
            window_days=config.baseline_window_days,
            min_samples=config.baseline_min_samples,
            mad_factor=config.baseline_mad_factor,
        )
        if not baseline.active:
            continue

        points = _consecutive_deviation(
            conn, metric, today, config.deviation_days, baseline.is_deviation
        )
        if not points:
            continue

        reason = (
            f"{spec.display}连续 {config.deviation_days} 天偏离个人基线"
            f"（基线 {format_number(baseline.center, spec.unit, spec.name)}"
            f" ± {format_number(baseline.threshold, spec.unit, spec.name)}）"
        )
        candidates.append(
            AlertCandidate(
                rule_id="baseline_deviation",
                metric_name=metric,
                severity="info",
                window_start=points[0].record_date,
                window_end=points[-1].record_date,
                trigger_reason=reason,
                evidence_record_ids=tuple(point.record_id for point in points),
                suggested_action="留意这几天的作息与运动量变化，不需要立即处理",
            )
        )
    return candidates


def data_gap(
    conn: sqlite3.Connection, config: RuleConfig, today: date
) -> list[AlertCandidate]:
    start, end = _window(today, 7)
    tracked = tracked_metrics(conn, config.data_gap_metrics, today - timedelta(days=29), today)
    candidates: list[AlertCandidate] = []

    for metric in config.data_gap_metrics:
        if metric not in tracked:
            continue
        summary = summarize(conn, get_metric(metric), start, end)
        if summary.missing_rate <= config.data_gap_rate:
            continue
        candidates.append(
            AlertCandidate(
                rule_id="data_gap",
                metric_name=metric,
                severity="info",
                window_start=summary.window_start,
                window_end=summary.window_end,
                trigger_reason=(
                    f"{summary.display}近 7 天只有 {summary.sample_days} 天有效记录"
                    f"（缺失率 {summary.missing_rate:.0%}），趋势判断会失真"
                ),
                evidence_record_ids=summary.record_ids,
                suggested_action="补录缺失的日期；缺失过多时报告只会显示「数据不足」",
            )
        )
    return candidates


ALL_RULES = (sleep_deficit_trend, resting_hr_shift, baseline_deviation, data_gap)


def evaluate(
    conn: sqlite3.Connection, config: RuleConfig, today: date
) -> list[AlertCandidate]:
    """只算不发，供预览与测试使用。"""
    candidates: list[AlertCandidate] = []
    for rule in ALL_RULES:
        candidates.extend(rule(conn, config, today))
    return candidates


def run(
    conn: sqlite3.Connection,
    config: RuleConfig,
    today: date,
    *,
    persist: bool = True,
) -> list[AlertCandidate]:
    """评估并落库。去重与冷却在写入前拦，避免列表里堆积重复提醒。"""
    candidates = evaluate(conn, config, today)
    if not persist:
        return candidates

    stored: list[AlertCandidate] = []
    for candidate in candidates:
        if alert_store.alert_exists(conn, candidate.dedup_key):
            continue
        if alert_store.in_cooldown(
            conn, candidate.rule_id, candidate.metric_name, config.cooldown_hours
        ):
            continue
        alert_store.insert_alert(
            conn,
            rule_id=candidate.rule_id,
            dedup_key=candidate.dedup_key,
            metric_name=candidate.metric_name,
            severity=candidate.severity,
            window_start=candidate.window_start,
            window_end=candidate.window_end,
            trigger_reason=candidate.trigger_reason,
            evidence_record_ids=candidate.evidence_record_ids,
            suggested_action=candidate.suggested_action,
            cooldown_hours=config.cooldown_hours,
            commit=False,
        )
        stored.append(candidate)
    conn.commit()
    return stored


def config_from_settings(settings) -> RuleConfig:
    """把可配置项从环境变量搬到规则配置。阈值集中在这里，不散落在代码里。"""
    return RuleConfig(
        sleep_target_minutes=settings.sleep_target_minutes,
        sleep_target_user_supplied=settings.sleep_target_user_supplied,
        sleep_deficit_min_days=settings.sleep_deficit_days,
        baseline_window_days=settings.baseline_window_days,
        baseline_min_samples=settings.baseline_min_samples,
        baseline_mad_factor=settings.baseline_mad_factor,
        resting_hr_shift_bpm=settings.resting_hr_shift_bpm,
        data_gap_rate=settings.data_gap_rate,
        cooldown_hours=settings.rule_cooldown_hours,
    )
