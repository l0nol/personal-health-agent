"""每日活动/训练建议引擎（本地规则）。

设计原则：
1. **安全优先**：疼痛、眩晕、明显不适 → 只给休息，不解释原因。
2. **不猜**：睡眠缺失就说缺失，训练史缺失就说缺失；不拿 0 填补，不编"近期练过腿"。
3. **可解释**：每条建议都带依据、窗口、覆盖度、限制与不确定性。
4. **有界**：不安排高强度训练，不建议为补偿饮食而加练，不做诊断。
5. **AI 可选**：本模块完全不依赖 AI；解释层可以换成本地文案。

执行顺序即优先级：不适 → 资料不足 → 休息 → 轻松活动 → 常规训练。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean

from ..analytics.aggregate import load_points, summarize
from ..analytics.baseline import compute_baseline
from ..catalog import get_metric
from ..reports.builder import latest_data_date
from ..reports.format import format_minutes, format_number
from ..storage import training as training_store
from ..storage.repository import now_iso
from ..training.models import BODY_PART_LABELS, TrainingPreferences, TrainingSession
from .contract import (
    BASE_LIMITATIONS,
    CATEGORY_INSUFFICIENT,
    CATEGORY_LIGHT,
    CATEGORY_REGULAR,
    CATEGORY_REST,
    FEEDBACK_PROMPT,
    FORBIDDEN_ADVICE_TERMS,
    SAFETY_NOTICE,
    SOURCE_DEVICE,
    SOURCE_PREFERENCE,
    SOURCE_SUBJECTIVE,
    SOURCE_TRAINING,
    Evidence,
    Guidance,
)

#: 规则版本。任何会改变结论的规则改动都必须递增，并在报告里写明差异。
RULE_VERSION = "guidance-1.0.0"

#: 默认参考值的来源说明。默认 420 分钟不是"用户的目标"，文案必须能看出区别。
SLEEP_REFERENCE_NOTE = "成人常见建议 7–9 小时的下沿"


@dataclass(frozen=True, slots=True)
class GuidanceConfig:
    sleep_target_minutes: int = 420
    sleep_target_user_supplied: bool = False
    sleep_window_days: int = 7
    min_coverage_days: int = 4
    #: 低于目标的这个比例算"偏短"，低于 severe 比例算"明显偏短"。
    short_sleep_ratio: float = 0.9
    severe_sleep_ratio: float = 0.7
    #: 设备数据超过这么多天没更新，就不再假装了解当前状态。
    stale_after_days: int = 3
    #: 主观疲劳/精力阈值（1–5）。
    fatigue_elevated: int = 3
    fatigue_rest: int = 4
    energy_low: int = 2
    energy_rest: int = 1
    soreness_elevated: int = 3
    soreness_rest: int = 4
    #: 48 小时 = 今天与昨天两个日历日（数据只到日期粒度，见 limitations）。
    same_part_window_days: int = 2
    training_history_days: int = 14
    baseline_window_days: int = 28
    baseline_min_samples: int = 14
    baseline_mad_factor: float = 2.5


def config_from_settings(settings) -> GuidanceConfig:
    return GuidanceConfig(
        sleep_target_minutes=settings.sleep_target_minutes,
        sleep_target_user_supplied=settings.sleep_target_user_supplied,
        baseline_window_days=settings.baseline_window_days,
        baseline_min_samples=settings.baseline_min_samples,
        baseline_mad_factor=settings.baseline_mad_factor,
    )


def _sleep_target_text(config: GuidanceConfig) -> str:
    target = format_minutes(config.sleep_target_minutes)
    if config.sleep_target_user_supplied:
        return f"你设定的睡眠目标 {target}"
    return f"默认参考值 {target}（{SLEEP_REFERENCE_NOTE}；未按年龄调整）"


def _guard(texts: tuple[str, ...]) -> None:
    """建议正文的禁用词守卫。

    命中的话说明规则写错了方向——直接失败，比把越界建议发给用户安全。
    """
    for text in texts:
        for term in FORBIDDEN_ADVICE_TERMS:
            if term in text:
                raise RuntimeError(f"建议文本命中禁用词 {term!r}：{text!r}")


def _recent_sessions(
    conn: sqlite3.Connection, for_date: date, days: int
) -> list[TrainingSession]:
    start = for_date - timedelta(days=days - 1)
    return training_store.list_sessions(conn, start=start, end=for_date, limit=100)


def _parts_trained_between(
    sessions: list[TrainingSession], start: date, end: date
) -> tuple[str, ...]:
    parts: list[str] = []
    for session in sessions:
        day = date.fromisoformat(session.session_date)
        if not (start <= day <= end):
            continue
        if session.completion == "skipped":
            continue
        for part in session.body_parts:
            if part not in parts:
                parts.append(part)
    return tuple(parts)


def _candidate_parts(recent: tuple[str, ...], avoid_text: tuple[str, ...]) -> tuple[str, ...]:
    """挑出近期没练、也没被用户点名避开的部位。

    父子关系做保守处理：练过"下肢"就不再安排"腿"；练过"全身"就当所有部位都练过。
    """
    blocked = set(recent)
    if "full_body" in blocked:
        return ()
    if "lower" in blocked:
        blocked.add("legs")
    if "upper" in blocked:
        blocked.update({"chest", "back", "shoulders", "arms", "push", "pull"})
    # 推/拉是复合动作，涉及的关节被练过就不再安排，避免"换了个名字练同一处"
    if any(part in blocked for part in ("chest", "shoulders", "arms")):
        blocked.add("push")
    if any(part in blocked for part in ("back", "arms")):
        blocked.add("pull")

    avoid_blob = " ".join(avoid_text)
    candidates = []
    for part in ("legs", "push", "pull", "core", "chest", "back", "shoulders", "arms"):
        if part in blocked:
            continue
        if BODY_PART_LABELS[part] in avoid_blob:
            continue
        candidates.append(part)
    return tuple(candidates)


def _session_brief(sessions: list[TrainingSession], limit: int = 3) -> str:
    items = []
    for session in sessions[:limit]:
        parts = "/".join(session.body_part_labels) or "未记录部位"
        items.append(f"{session.session_date} {session.activity_label}（{parts}）")
    return "；".join(items)


def _sleep_evidence(summary, window_days: int) -> Evidence | None:
    if not summary.points:
        return None
    last = summary.points[-1]
    if summary.sample_days >= 3:
        value_text = (
            f"均值 {format_minutes(summary.mean or 0)}，最近一晚 {format_minutes(last.value)}"
            f"（{last.record_date}）"
        )
    else:
        value_text = f"有效 {summary.sample_days} 天，最近一晚 {format_minutes(last.value)}（{last.record_date}）"
    return Evidence(
        label=f"近 {window_days} 天夜间睡眠",
        value_text=value_text,
        window_start=summary.window_start,
        window_end=summary.window_end,
        coverage=f"{summary.sample_days}/{window_days} 天",
        source=SOURCE_DEVICE,
        record_ids=summary.record_ids,
    )


def _hr_evidence(baseline, summary, baseline_points, window_days: int) -> Evidence | None:
    if not baseline.active or baseline.center is None:
        return None
    latest = (
        f"，最近 {format_number(summary.points[-1].value, 'bpm', 'resting_hr_bpm')}"
        if summary.points
        else ""
    )
    return Evidence(
        label="静息心率个人基线",
        value_text=(
            f"{format_number(baseline.center, 'bpm', 'resting_hr_bpm')} ± "
            f"{format_number(baseline.threshold, 'bpm', 'resting_hr_bpm')}"
            f"（{baseline.window_days} 天窗口，{baseline.sample_count} 个有效日）{latest}"
        ),
        window_start=baseline.window_start,
        window_end=baseline.window_end,
        coverage=f"{baseline.sample_count}/{baseline.window_days} 天",
        source=SOURCE_DEVICE,
        # 基线用的是整段窗口；这里必须给整段窗口的记录，否则"可追溯"是假的
        record_ids=tuple(point.record_id for point in baseline_points),
    )


def _steps_evidence(summary, window_days: int) -> Evidence | None:
    if summary.sample_days < 3:
        return None
    return Evidence(
        label=f"近 {window_days} 天步数",
        value_text=f"日均 {format_number(summary.mean, 'count', 'steps_count')}",
        window_start=summary.window_start,
        window_end=summary.window_end,
        coverage=f"{summary.sample_days}/{window_days} 天",
        source=SOURCE_DEVICE,
        record_ids=summary.record_ids,
    )


def _training_evidence(sessions: list[TrainingSession], days: int) -> Evidence | None:
    if not sessions:
        return None
    return Evidence(
        label=f"近 {days} 天训练记录",
        value_text=f"{len(sessions)} 次：{_session_brief(sessions)}",
        window_start=(date.fromisoformat(sessions[-1].session_date)).isoformat(),
        window_end=sessions[0].session_date,
        coverage=f"{len(sessions)} 次/{days} 天",
        source=SOURCE_TRAINING,
        session_ids=tuple(session.session_id for session in sessions[:5]),
    )


def _feedback_evidence(feedback) -> Evidence | None:
    if feedback is None:
        return None
    pieces = []
    if feedback.energy is not None:
        pieces.append(f"精力 {feedback.energy}/5")
    if feedback.fatigue is not None:
        pieces.append(f"疲劳 {feedback.fatigue}/5")
    if feedback.soreness_parts and feedback.soreness_level is not None:
        pieces.append(f"酸痛 {'/'.join(feedback.soreness_labels)} {feedback.soreness_level}/5")
    if feedback.discomfort:
        pieces.append("有不适")
    if not pieces:
        return None
    return Evidence(
        label="恢复反馈",
        value_text="，".join(pieces),
        window_start=feedback.feedback_date,
        window_end=feedback.feedback_date,
        coverage="1/1 天",
        source=SOURCE_SUBJECTIVE,
    )


def _preference_evidence(prefs: TrainingPreferences) -> Evidence | None:
    pieces = []
    if prefs.days_per_week is not None:
        pieces.append(f"每周 {prefs.days_per_week} 次")
    if prefs.time_budget_minutes is not None:
        pieces.append(f"单次约 {prefs.time_budget_minutes} 分钟")
    if prefs.equipment:
        pieces.append(f"器材 {'/'.join(prefs.equipment)}")
    if prefs.avoid_movements:
        pieces.append(f"避开 {'/'.join(prefs.avoid_movements)}")
    if not pieces:
        return None
    return Evidence(
        label="训练偏好",
        value_text="；".join(pieces),
        window_start="",
        window_end="",
        coverage="已设置",
        source=SOURCE_PREFERENCE,
    )


def build_guidance(
    conn: sqlite3.Connection,
    *,
    for_date: date | None = None,
    config: GuidanceConfig | None = None,
    today: date | None = None,
) -> Guidance:
    """生成某一天的建议。纯读：不写库、不调网络。

    `for_date` 默认今天；`today` 只用于测试注入"现在"，避免用例随系统日期漂移。
    """
    cfg = config or GuidanceConfig()
    now = today or date.today()
    target = for_date or now
    window_days = cfg.sleep_window_days
    window_start = target - timedelta(days=window_days - 1)

    sleep = summarize(conn, get_metric("night_sleep_minutes"), window_start, target)
    hr_summary = summarize(conn, get_metric("resting_hr_bpm"), window_start, target)
    steps = summarize(conn, get_metric("steps_count"), window_start, target)
    baseline_end = target - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=cfg.baseline_window_days - 1)
    baseline_points = load_points(conn, "resting_hr_bpm", baseline_start, baseline_end)
    baseline = compute_baseline(
        conn,
        "resting_hr_bpm",
        baseline_end,
        window_days=cfg.baseline_window_days,
        min_samples=cfg.baseline_min_samples,
        mad_factor=cfg.baseline_mad_factor,
    )

    sessions = _recent_sessions(conn, target, cfg.training_history_days)
    feedback = training_store.get_feedback(conn, target)
    if feedback is None:
        # 允许用昨天/前天的反馈，但必须说明"不是今天的"
        feedback = training_store.latest_feedback(conn, target, days=2)
        if feedback is not None and feedback.feedback_date != target.isoformat():
            feedback_note = f"（最近一次反馈在 {feedback.feedback_date}）"
        else:
            feedback_note = ""
    else:
        feedback_note = ""
    prefs = training_store.get_preferences(conn)

    last_sleep = sleep.points[-1] if sleep.points else None
    sleep_age = (
        (target - date.fromisoformat(last_sleep.record_date)).days if last_sleep else None
    )
    sleep_missing = sleep_age is None or sleep_age > 1
    sleep_value = last_sleep.value if last_sleep else None
    target_minutes = cfg.sleep_target_minutes
    short_sleep = sleep_value is not None and sleep_value < target_minutes * cfg.short_sleep_ratio
    severe_sleep = sleep_value is not None and sleep_value < target_minutes * cfg.severe_sleep_ratio

    fatigue = feedback.fatigue if feedback else None
    energy = feedback.energy if feedback else None
    soreness_level = feedback.soreness_level if feedback else None
    discomfort = bool(feedback.discomfort) if feedback else False

    latest_device = latest_data_date(conn)
    freshness = (target - latest_device).days if latest_device else None
    stale = freshness is not None and freshness > cfg.stale_after_days

    recent_window_start = target - timedelta(days=cfg.same_part_window_days - 1)
    recent_parts = _parts_trained_between(sessions, recent_window_start, target)
    weekly_count = len(
        [s for s in sessions if date.fromisoformat(s.session_date) >= target - timedelta(days=6)]
    )
    target_met = prefs.days_per_week is not None and weekly_count >= prefs.days_per_week

    uncertainty: list[str] = []
    if sleep_missing:
        if last_sleep is None:
            uncertainty.append("缺少最近一晚的睡眠记录（近 7 天也没有可用记录）。")
        else:
            uncertainty.append(
                f"缺少最近一晚的睡眠记录（最近一次在 {last_sleep.record_date}，{sleep_age} 天前）。"
            )
    if 0 < sleep.sample_days < cfg.min_coverage_days:
        uncertainty.append(
            f"近 {window_days} 天睡眠只有 {sleep.sample_days} 天有数据，趋势覆盖不足。"
        )
    if not sessions:
        uncertainty.append("没有任何训练记录：无法判断近期练过哪些部位，也无法按实际训练量调整。")
    if not baseline.active and hr_summary.sample_days > 0:
        reason = baseline.reason or f"有效 {baseline.sample_count} 天，至少需要 {cfg.baseline_min_samples} 天"
        uncertainty.append(f"静息心率基线本次不可用（{reason}），没有使用心率偏离作为依据。")
    if stale:
        uncertainty.append(f"设备数据已 {freshness} 天未更新，结论主要依据你的主观反馈或历史记录。")
    if prefs.updated_at is None:
        uncertainty.append("未设置训练偏好（每周次数/时长/器材），按通用建议给出。")
    if sleep.points:
        uncertainty.append("设备字段尚未与厂商说明逐项核对（provisional），数值只作趋势参考。")

    evidence: list[Evidence] = []
    sleep_item = _sleep_evidence(sleep, window_days)
    if sleep_item:
        evidence.append(sleep_item)
    hr_item = _hr_evidence(baseline, hr_summary, baseline_points, window_days)
    if hr_item:
        evidence.append(hr_item)
    steps_item = _steps_evidence(steps, window_days)
    if steps_item:
        evidence.append(steps_item)
    training_item = _training_evidence(sessions, cfg.training_history_days)
    if training_item:
        evidence.append(training_item)
    feedback_item = _feedback_evidence(feedback)
    if feedback_item:
        evidence.append(feedback_item)
    pref_item = _preference_evidence(prefs)
    if pref_item:
        evidence.append(pref_item)

    alternatives: list[str]
    category: str
    headline: str
    focus: str
    reasons: list[str] = []

    if discomfort or (soreness_level is not None and soreness_level >= cfg.soreness_rest):
        category = CATEGORY_REST
        headline = "今天以休息为主，不安排训练"
        focus = "停止当次运动，先处理不适；缓解前不做训练"
        alternatives = [
            "如果只是轻微酸痛且活动后不加重，可做 5–10 分钟缓慢走动",
            "把训练顺延，之后从低强度重新开始，不补练、不加量",
        ]
        if discomfort:
            reasons.append("你记录了疼痛、眩晕或明显不适")
        if soreness_level is not None and soreness_level >= cfg.soreness_rest:
            reasons.append(f"酸痛程度 {soreness_level}/5，已超过可继续训练的范围")
    elif sleep_missing and feedback is None:
        category = CATEGORY_INSUFFICIENT
        headline = "资料不足，今天不给具体训练安排"
        focus = "先补录昨晚睡眠，或记录今天的精力/疲劳（1–5），再看一次建议"
        alternatives = [
            "补录后用 `health-agent advice` 重新生成",
            "如果今天状态明显不佳，直接按休息处理",
        ]
        reasons.append("缺少最近一晚睡眠，也没有今天的恢复反馈")
    elif stale and (sleep_missing or feedback is None):
        category = CATEGORY_INSUFFICIENT
        headline = "资料不足，今天不给具体训练安排"
        focus = "设备数据已过期，先补录最近的睡眠与活动记录"
        alternatives = [
            "补录最近几天的睡眠后再看建议",
            "记录今天的精力与疲劳，至少让建议有主观依据",
        ]
        reasons.append(f"设备数据已 {freshness} 天未更新，且缺少睡眠或恢复反馈")
    elif (
        (fatigue is not None and fatigue >= cfg.fatigue_rest)
        or (energy is not None and energy <= cfg.energy_rest)
        or (severe_sleep and fatigue is not None and fatigue >= cfg.fatigue_elevated)
    ):
        category = CATEGORY_REST
        headline = "今天以休息为主，不安排训练"
        focus = "优先睡眠、补水与规律进食；明天再看状态决定强度"
        alternatives = [
            "如果感觉明显好转，可做 5–10 分钟缓慢走动",
            "把训练顺延到明天，不补练、不加量",
        ]
        if fatigue is not None and fatigue >= cfg.fatigue_rest:
            reasons.append(f"今天反馈疲劳 {fatigue}/5")
        if energy is not None and energy <= cfg.energy_rest:
            reasons.append(f"今天反馈精力 {energy}/5")
        if severe_sleep:
            reasons.append(f"最近一晚睡眠 {format_minutes(sleep_value or 0)}，明显低于{_sleep_target_text(cfg)}")
    elif severe_sleep:
        category = CATEGORY_LIGHT
        headline = "今天适合轻松活动，不安排正式训练"
        focus = "20–30 分钟轻松步行或活动度练习，保持能正常说话的强度"
        alternatives = [
            "10 分钟肩颈/髋部活动度",
            "如果活动后更累，直接改为休息",
        ]
        deficit_pct = round((1 - (sleep_value or 0) / target_minutes) * 100)
        reasons.append(
            f"最近一晚睡眠 {format_minutes(sleep_value or 0)}，比{_sleep_target_text(cfg)}少约 {deficit_pct}%"
        )
    elif (
        short_sleep
        or (fatigue is not None and fatigue >= cfg.fatigue_elevated)
        or (energy is not None and energy <= cfg.energy_low)
        or (soreness_level is not None and soreness_level >= cfg.soreness_elevated)
    ):
        category = CATEGORY_LIGHT
        headline = "今天适合轻松活动，不安排正式训练"
        focus = "20–30 分钟轻松步行或拉伸，强度以活动后不更累为准"
        alternatives = [
            "10 分钟活动度练习",
            "如果白天仍然困倦，改为完全休息",
        ]
        if short_sleep:
            reasons.append(
                f"最近一晚睡眠 {format_minutes(sleep_value or 0)}，低于{_sleep_target_text(cfg)}的 90%"
            )
        if fatigue is not None and fatigue >= cfg.fatigue_elevated:
            reasons.append(f"今天反馈疲劳 {fatigue}/5")
        if energy is not None and energy <= cfg.energy_low:
            reasons.append(f"今天反馈精力 {energy}/5")
        if soreness_level is not None and soreness_level >= cfg.soreness_elevated:
            reasons.append(f"酸痛程度 {soreness_level}/5")
    elif target_met:
        category = CATEGORY_LIGHT
        headline = "本周训练次数已达成，今天可以轻松活动"
        focus = "20–30 分钟散步或拉伸，把恢复留出来"
        alternatives = [
            "如果特别想练，只做 15 分钟低强度技术练习",
            "提前安排明天的训练，不追加次数",
        ]
        reasons.append(f"近 7 天已训练 {weekly_count} 次，达到你设定的每周 {prefs.days_per_week} 次")
    else:
        candidates = _candidate_parts(recent_parts, prefs.avoid_movements)
        if recent_parts and not candidates:
            category = CATEGORY_LIGHT
            headline = "近期部位都练过了，今天以恢复为主"
            focus = "20 分钟活动度或轻松步行，给练过的部位留恢复时间"
            alternatives = ["改练呼吸/放松练习", "明天再安排正式训练"]
            reasons.append(f"近 48 小时已练 {'/'.join(BODY_PART_LABELS.get(p, p) for p in recent_parts)}")
        else:
            category = CATEGORY_REGULAR
            headline = "今天可以按计划训练"
            budget = f"约 {prefs.time_budget_minutes} 分钟" if prefs.time_budget_minutes else "20–30 分钟"
            if recent_parts:
                pick = "、".join(BODY_PART_LABELS[part] for part in candidates[:2])
                focus = f"{budget}中低强度训练，优先练近期没练的部位：{pick}"
                reasons.append(
                    f"近 48 小时练过 {'/'.join(BODY_PART_LABELS.get(p, p) for p in recent_parts)}，"
                    "今天换部位更利于恢复"
                )
            elif not sessions:
                focus = f"{budget}自选中低强度全身练习，以动作质量为主"
                reasons.append("没有训练记录，先按通用强度开始")
            else:
                focus = f"{budget}中低强度训练，按你熟悉的计划进行"
                reasons.append("近期训练与恢复信号都在可训练范围")
            alternatives = ["状态一般时缩短到 20 分钟", "把自由重量换成自重或弹力带"]
            if prefs.avoid_movements:
                alternatives.append(f"避开：{'、'.join(prefs.avoid_movements)}")

    limitations = list(BASE_LIMITATIONS)
    if sleep_age is not None:
        limitations.append(
            "训练日志与设备数据都只到「日期」粒度，无法区分同一天早晚两次训练或测量。"
        )

    explanation_parts = []
    target_short = (
        f"你设定的目标 {format_minutes(cfg.sleep_target_minutes)}"
        if cfg.sleep_target_user_supplied
        else f"默认参考值 {format_minutes(cfg.sleep_target_minutes)}"
    )
    if last_sleep is not None:
        explanation_parts.append(
            f"最近一晚睡眠 {format_minutes(last_sleep.value)}（{last_sleep.record_date}，{target_short}）"
        )
    if feedback_item is not None:
        explanation_parts.append(f"你的反馈：{feedback_item.value_text}{feedback_note}")
    if recent_parts:
        explanation_parts.append(
            f"近 48 小时练过 {'/'.join(BODY_PART_LABELS.get(p, p) for p in recent_parts)}"
        )
    if not explanation_parts:
        explanation_parts.append("目前没有足够的睡眠或主观反馈记录")
    if reasons:
        explanation_parts.append("判断依据：" + "；".join(reasons))
    local_explanation = "；".join(explanation_parts) + "。因此给出上面的安排。"

    _guard((headline, focus, *alternatives, local_explanation))

    return Guidance(
        for_date=target.isoformat(),
        category=category,
        headline=headline,
        focus=focus,
        alternatives=tuple(alternatives),
        evidence=tuple(evidence),
        limitations=tuple(limitations),
        uncertainty=tuple(uncertainty),
        feedback_prompt=FEEDBACK_PROMPT,
        generated_at=now_iso(),
        rule_version=RULE_VERSION,
        data_freshness_days=freshness,
        safety_notice=SAFETY_NOTICE,
        local_explanation=local_explanation,
        signals={
            "sleep_value": sleep_value,
            "sleep_age_days": sleep_age,
            "sleep_sample_days": sleep.sample_days,
            "short_sleep": short_sleep,
            "severe_sleep": severe_sleep,
            "fatigue": fatigue,
            "energy": energy,
            "soreness_level": soreness_level,
            "discomfort": discomfort,
            "recent_parts": list(recent_parts),
            "weekly_sessions": weekly_count,
            "training_sessions": len(sessions),
            "freshness_days": freshness,
            "baseline_active": baseline.active,
            "reasons": reasons,
        },
    )
