"""界面视图模型与数据装配：路由与模板之间的**稳定契约**。

分层约定（改代码前先看这张表）：

    路由 (app.py)  →  context.py  →  模板 (templates/)
      取数、鉴权        装配成视图模型      只负责长相

- 路由不直接拼字典给模板；模板也不做业务计算。
- 这里的 dataclass 就是"模板能拿到哪些字段"的清单：加字段只改这里 + 模板，
  不会波及统计、存储或报告层。
- 所有数值都取自既有统计函数（`summarize` / `compute_baseline` / 计数查询），
  本模块**不做第二套口径**：换皮不会换数。

星环首页的装配也在这里（`ring_view`）。它只是把已经算好的东西换了排布：
中心状态来自 `build_guidance`，六个模块各自取自己那一页的同一批数字。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from ..ai_client import chat_store
from ..analytics.aggregate import Point, load_points, range_days, summarize
from ..analytics.baseline import compute_baseline
from ..catalog import METRICS, get_metric
from ..guidance.contract import (
    CATEGORY_INSUFFICIENT,
    Guidance,
)
from ..reports.builder import build_daily
from ..reports.format import format_minutes, format_number
from ..storage import training as training_store
from .charts import SeriesPoint, build_bars, build_line, build_spark
from .pages import PAGES, RING_MODULES, page_by_key


# ---------------- 视图模型 ----------------

@dataclass(frozen=True, slots=True)
class Reference:
    """指标卡"参照范围"行。四态：active / insufficient / no_data / range_bound。"""

    state: str
    metric: str
    unit: str
    low: float | None = None
    high: float | None = None
    center: float | None = None
    threshold: float | None = None
    method: str = ""
    mad_factor: float = 0.0
    window_start: str = ""
    window_end: str = ""
    window_days: int = 0
    sample_count: int = 0
    min_samples: int = 0
    reason: str = ""


@dataclass(frozen=True, slots=True)
class Tile:
    """详细列表首页的指标卡。"""

    metric: str
    label: str
    value: float | None
    date: str | None
    unit: str
    days: int
    status: str
    href: str
    reference: Reference


@dataclass(frozen=True, slots=True)
class PendingBatch:
    """一批"待确认"记录。

    为什么按批次而不是"全部 pending"：批量确认最容易出的事故是把用户**故意留着**
    的待确认记录一起确认掉（ERR-029）。按批次确认，范围永远等于"这一个文件导进来的"，
    按钮上也能写清楚会动多少条。
    """

    batch_id: str
    file_name: str
    pending: int
    date_from: str
    date_to: str
    imported_at: str


@dataclass(frozen=True, slots=True)
class RingNode:
    """星环上的一个模块节点。

    `summary` 是节点下方那行小字：没有数据时写"—"，不写 0。
    """

    key: str
    label: str
    idx: int
    angle: int
    href: str
    panel_title: str
    accent: str
    summary: str
    has_data: bool


@dataclass(frozen=True, slots=True)
class CoreState:
    """中央状态核心的一段文案。

    只描述"现在的状态"，不产出评分：`value` 是分类标签或一个真实指标值，
    绝不是一个把多项指标加权出来的分数。
    """

    kicker: str
    value: str
    sub: str
    note: str
    state: str  # ok / partial / empty


@dataclass(frozen=True, slots=True)
class PanelFact:
    label: str
    value: str
    hint: str = ""
    #: 值比较长（阶段占比、主观反馈这类）时独占一行，避免被挤成两行碎片
    wide: bool = False


@dataclass(frozen=True, slots=True)
class PanelAction:
    label: str
    href: str
    primary: bool = False


@dataclass(frozen=True, slots=True)
class SparkRow:
    """趋势面板里的一行：一个指标 + 一张迷你图（不把多个指标叠一张图）。"""

    metric: str
    label: str
    value_text: str
    date_text: str
    href: str
    spark: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Panel:
    """模块选中后展开的信息卡。"""

    key: str
    title: str
    kicker: str
    lead: str
    state: str  # ok / partial / empty
    facts: tuple[PanelFact, ...] = ()
    chart: dict[str, Any] | None = None
    chart_caption: str = ""
    sparks: tuple[SparkRow, ...] = ()
    notes: tuple[str, ...] = ()
    actions: tuple[PanelAction, ...] = ()
    empty_title: str = ""
    empty_text: str = ""


@dataclass(frozen=True, slots=True)
class RingView:
    """星环首页的全部数据：中心、节点、每个模块的中心文案与展开面板。"""

    core: CoreState
    nodes: tuple[RingNode, ...]
    cores: dict[str, CoreState] = field(default_factory=dict)
    panels: tuple[Panel, ...] = ()
    idle_panel: Panel | None = None


# ---------------- 基础工具 ----------------

#: 页面地址的唯一来源仍是 `pages.py`；这里只是把它摊平成"名字 → 地址"，
#: 免得面板里再抄一遍 "/sleep" 这种字面量（抄错一个字母就 404）。
PAGE_HREFS: dict[str, str] = {page.key: page.href for page in PAGES}


def metric_labels() -> dict[str, str]:
    """字段名 → 中文名，供模板显示"中文名（字段名）"。"""
    return {metric.name: metric.display for metric in METRICS}


def day_series(conn, metric: str, start: date, end: date) -> list[SeriesPoint]:
    """把某指标的日序列补成"整段日期轴"，缺失填 None。

    缺失必须是 None 而不是跳过：跳过会把两段不连续的数据连成一条线。
    """
    by_date = {p.record_date: p for p in load_points(conn, metric, start, end)}
    series: list[SeriesPoint] = []
    for day in range_days(start, end):
        point = by_date.get(day.isoformat())
        series.append(
            SeriesPoint(
                label=day.strftime("%m-%d"),
                value=point.value if point else None,
                record_id=point.record_id if point else None,
            )
        )
    return series


def _clip(text: str, limit: int) -> str:
    """长度上限只在展示层截断，不改原数据。"""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def pending_batches(conn) -> list[PendingBatch]:
    """按批次汇总待确认记录（新的在前）。没有批次的手工记录不在其中。"""
    rows = conn.execute(
        """
        SELECT r.batch_id AS batch_id,
               COALESCE(b.file_name, '（无批次：手工或旧数据）') AS file_name,
               COALESCE(b.imported_at, '') AS imported_at,
               COUNT(*) AS n,
               MIN(r.record_date) AS d_from,
               MAX(r.record_date) AS d_to
          FROM records r
          LEFT JOIN import_batches b ON b.batch_id = r.batch_id
         WHERE r.verification_status = 'pending'
         GROUP BY r.batch_id
         ORDER BY imported_at DESC, b.rowid DESC, n DESC
        """
    ).fetchall()
    return [
        PendingBatch(
            batch_id=row["batch_id"] or "",
            file_name=row["file_name"],
            pending=row["n"],
            date_from=row["d_from"] or "",
            date_to=row["d_to"] or "",
            imported_at=row["imported_at"],
        )
        for row in rows
    ]


def reference(conn, metric_name: str, end: date) -> Reference:
    """参照范围行的数据：个人基线动态计算（判断全在 analytics.baseline）。"""
    spec = get_metric(metric_name)
    if spec.kind == "range_bound":
        return Reference(state="range_bound", metric=metric_name, unit=spec.unit)

    baseline = compute_baseline(conn, metric_name, end)
    if baseline.status != "active":
        state = "no_data" if baseline.sample_count == 0 else "insufficient"
        return Reference(
            state=state,
            metric=metric_name,
            unit=spec.unit,
            reason=baseline.reason,
            sample_count=baseline.sample_count,
            min_samples=baseline.min_samples,
            window_start=baseline.window_start,
            window_end=baseline.window_end,
            window_days=baseline.window_days,
        )
    return Reference(
        state="active",
        metric=metric_name,
        unit=spec.unit,
        low=baseline.center - baseline.threshold,
        high=baseline.center + baseline.threshold,
        center=baseline.center,
        threshold=baseline.threshold,
        method=baseline.method,
        mad_factor=baseline.mad_factor,
        window_start=baseline.window_start,
        window_end=baseline.window_end,
        window_days=baseline.window_days,
        sample_count=baseline.sample_count,
        min_samples=baseline.min_samples,
    )


#: 首页与环上的四个指标卡（顺序即展示顺序）
_TILE_METRICS: tuple[tuple[str, str], ...] = (
    ("night_sleep_minutes", "/sleep"),
    ("resting_hr_bpm", "/trends?metric=resting_hr_bpm"),
    ("steps_count", "/trends?metric=steps_count"),
    ("activity_energy_kcal", "/trends?metric=activity_energy_kcal"),
)


def dashboard_tiles(conn, end: date) -> list[Tile]:
    """详细列表首页的指标卡（与星环同一来源、同一窗口、同一格式化）。"""
    start = end - timedelta(days=6)
    tiles: list[Tile] = []
    for name, href in _TILE_METRICS:
        summary = summarize(conn, name, start, end)
        latest = summary.points[-1] if summary.points else None
        spec = get_metric(name)
        tiles.append(
            Tile(
                metric=name,
                label=spec.display,
                value=latest.value if latest else None,
                date=latest.record_date if latest else None,
                unit=spec.unit,
                days=summary.sample_days,
                status=summary.status,
                href=href,
                reference=reference(conn, name, end),
            )
        )
    return tiles


def _tile_foot(tile: Tile) -> str:
    return f"近 7 天有效 {tile.days}/7 天" if tile.days else "近 7 天无数据"


def _latest(conn, metric: str, end: date, days: int = 30) -> Point | None:
    summary = summarize(conn, metric, end - timedelta(days=days - 1), end)
    return summary.points[-1] if summary.points else None


# ---------------- 星环：六个模块 ----------------

def _sleep_panel(conn, end: date, *, target_minutes: int) -> tuple[Panel, CoreState, str, bool]:
    start = end - timedelta(days=6)
    summary = summarize(conn, "night_sleep_minutes", start, end)
    latest = summary.points[-1] if summary.points else None
    series = day_series(conn, "night_sleep_minutes", start, end)
    chart = build_bars(series, target=float(target_minutes), width=380, height=140)

    stages: dict[str, Point | None] = {}
    for name, label in (
        ("deep_sleep_percent", "深睡"),
        ("light_sleep_percent", "浅睡"),
        ("rem_sleep_percent", "REM"),
    ):
        stages[name] = _latest(conn, name, end)

    # 阶段占比按"深睡 22% / 浅睡 55% / REM 23%"排一行；缺哪个就留"—"，不补 0
    stage_text = " / ".join(
        f"{label} {format_number(stages[name].value, '%', name)}" if stages[name] else f"{label} —"
        for name, label in (
            ("deep_sleep_percent", "深睡"),
            ("light_sleep_percent", "浅睡"),
            ("rem_sleep_percent", "REM"),
        )
    )

    facts = [
        PanelFact(
            "最近一次",
            format_minutes(latest.value) if latest else "—",
            f"{latest.record_date} 夜" if latest else "近 7 天没有已确认记录",
        ),
        PanelFact(
            "近 7 天均值",
            format_minutes(summary.mean) if summary.sample_days >= 3 and summary.mean is not None else "样本不足",
            f"有效 {summary.sample_days}/7 天",
        ),
        PanelFact(
            "睡眠阶段",
            stage_text,
            "最近一次记录的设备占比" if any(stages.values()) else "设备未提供",
            wide=True,
        ),
    ]

    has_data = bool(summary.sample_days)
    if has_data:
        lead = (
            f"最近一次 {format_minutes(latest.value)}，"
            f"近 7 天均值 {format_minutes(summary.mean)}"
            if summary.sample_days >= 3 and summary.mean is not None and latest
            else f"最近一次 {format_minutes(latest.value)}，样本还不够看均值"
        )
        state = "ok" if summary.sample_days >= 3 else "partial"
    else:
        lead = "近 7 天没有已确认的睡眠记录。"
        state = "empty"

    core = CoreState(
        kicker="昨夜",
        value=format_minutes(latest.value) if latest else "暂无",
        sub=(
            f"近 7 天均值 {format_minutes(summary.mean)}"
            if summary.sample_days >= 3 and summary.mean is not None
            else ("记录天数不足，先看单次" if latest else "还没有睡眠记录")
        ),
        note=f"目标 {format_minutes(target_minutes)}（默认参考值）",
        state=state,
    )

    panel = Panel(
        key="sleep",
        title="夜间睡眠",
        kicker=f"近 7 天 · 截至 {end.isoformat()}",
        lead=lead,
        state=state,
        facts=tuple(facts),
        chart=chart,
        chart_caption="每日夜间睡眠时长，虚线为目标；空心柱表示当天没有已确认数据。",
        notes=(
            "只统计已确认记录；虚线目标为默认参考值（420 分钟），不是你的个人基线。",
        ),
        actions=(
            PanelAction("睡眠分析", PAGE_HREFS["sleep"], True),
            PanelAction("导入数据", PAGE_HREFS["imports"]),
        ),
        empty_title="还没有睡眠数据",
        empty_text="导入一份华为运动健康的截图提取结果，确认后这里就会有昨夜睡眠与近 7 天趋势。",
    )
    node_summary = format_minutes(latest.value) if latest else "—"
    return panel, core, node_summary, has_data


def _recovery_panel(conn, end: date, *, today: date) -> tuple[Panel, CoreState, str, bool]:
    start = end - timedelta(days=13)
    hr = summarize(conn, "resting_hr_bpm", start, end)
    hr_latest = hr.points[-1] if hr.points else None
    hr_ref = reference(conn, "resting_hr_bpm", end)
    hrv = _latest(conn, "hrv_avg_ms", end)
    spo2 = _latest(conn, "sleep_avg_spo2_percent", end)
    rr = _latest(conn, "respiratory_rate_avg_brpm", end)
    feedback = training_store.get_feedback(conn, today) or training_store.latest_feedback(conn, today)

    if hr_ref.state == "active":
        hr_hint = (
            f"个人基线 {format_number(hr_ref.low, 'bpm')}–{format_number(hr_ref.high, 'bpm')}"
        )
    elif hr_ref.state == "insufficient":
        hr_hint = f"基线样本不足（{hr_ref.sample_count}/{hr_ref.min_samples} 天）"
    else:
        hr_hint = "还没有可算基线的数据"

    deviation = ""
    if hr_latest and hr_ref.state == "active" and hr_ref.low is not None and hr_ref.high is not None:
        if hr_latest.value < hr_ref.low:
            deviation = "低于个人基线"
        elif hr_latest.value > hr_ref.high:
            deviation = "高于个人基线"
        else:
            deviation = "在个人基线内"

    subjective = "—"
    subjective_hint = "今天还没有记录主观状态"
    if feedback:
        parts = []
        if feedback.energy is not None:
            parts.append(f"精力 {feedback.energy}/5")
        if feedback.fatigue is not None:
            parts.append(f"疲劳 {feedback.fatigue}/5")
        if feedback.soreness_level is not None:
            parts.append(f"酸痛 {feedback.soreness_level}/5")
        subjective = " · ".join(parts) if parts else "已记录"
        subjective_hint = f"{feedback.feedback_date} · 你自己记录，不是设备测量"

    facts = [
        PanelFact(
            "静息心率",
            format_number(hr_latest.value, "bpm", "resting_hr_bpm") if hr_latest else "—",
            f"{hr_latest.record_date} · {hr_hint}" if hr_latest else hr_hint,
        ),
        PanelFact(
            "HRV",
            format_number(hrv.value, "ms", "hrv_avg_ms") if hrv else "—",
            f"{hrv.record_date} · 设备原值" if hrv else "设备未提供",
        ),
        PanelFact(
            "睡眠血氧",
            format_number(spo2.value, "%", "sleep_avg_spo2_percent") if spo2 else "—",
            f"{spo2.record_date} · 设备原值" if spo2 else "设备未提供",
        ),
        PanelFact(
            "呼吸率",
            format_number(rr.value, "brpm", "respiratory_rate_avg_brpm") if rr else "—",
            f"{rr.record_date} · 夜间均值" if rr else "设备未提供",
        ),
        PanelFact("今日主观", subjective, subjective_hint, wide=True),
    ]

    have_device = any([hr_latest, hrv, spo2, rr])
    have_any = have_device or feedback is not None
    if have_any:
        head = (
            f"静息心率 {format_number(hr_latest.value, 'bpm', 'resting_hr_bpm')}，{deviation}"
            if hr_latest and deviation
            else "恢复相关的数据还不齐"
        )
        lead = head if have_device else "只有主观记录，设备数据还没进来。"
        state = "ok" if hr_ref.state == "active" else "partial"
    else:
        lead = "还没有恢复相关的记录。"
        state = "empty"

    chart = build_line(day_series(conn, "resting_hr_bpm", start, end), width=380, height=140)

    core = CoreState(
        kicker="恢复",
        value=format_number(hr_latest.value, "bpm", "resting_hr_bpm") if hr_latest else "暂无",
        sub=deviation or ("静息心率还没有记录" if not hr_latest else "基线和记录天数不足"),
        note="静息心率 · 个人基线动态算",
        state=state,
    )

    panel = Panel(
        key="recovery",
        title="恢复与静息心率",
        kicker=f"近 14 天 · 截至 {end.isoformat()}",
        lead=lead,
        state=state,
        facts=tuple(facts),
        chart=chart,
        chart_caption="静息心率近 14 天走势；缺失处断线，不补值。",
        notes=(
            "参照范围 = 个人基线（近 28 天中位数 ± 2.5×MAD），样本不足时不做判断。",
            "设备只给估算值，不是医疗测量；主观反馈与设备数据分开陈列，不合并成一个「恢复分数」。",
        ),
        actions=(
            PanelAction("心率趋势", PAGE_HREFS["trends"] + "?metric=resting_hr_bpm&days=30", True),
            PanelAction("记录今天状态", PAGE_HREFS["training"] + "#feedback"),
        ),
        empty_title="还没有恢复数据",
        empty_text="静息心率、HRV 等指标导入并确认后会出现在这里；也可以在训练页记录今天的主观状态。",
    )
    node_summary = format_number(hr_latest.value, "bpm", "resting_hr_bpm") if hr_latest else "—"
    return panel, core, node_summary, have_any


def _training_panel(
    conn, end: date, *, guidance: Guidance | None
) -> tuple[Panel, CoreState, str, bool]:
    week_start = end - timedelta(days=6)
    weekly = training_store.count_sessions(conn, start=week_start, end=end)
    sessions = training_store.list_sessions(conn, start=end - timedelta(days=29), end=end, limit=4)
    prefs = training_store.get_preferences(conn)

    # 近 4 周训练次数（每周一个柱子）：数量来自训练记录，不推断负荷
    buckets: list[SeriesPoint] = []
    for offset in range(3, -1, -1):
        b_end = end - timedelta(days=offset * 7)
        b_start = b_end - timedelta(days=6)
        buckets.append(
            SeriesPoint(
                label=b_end.strftime("%m-%d"),
                value=float(training_store.count_sessions(conn, start=b_start, end=b_end)),
            )
        )
    chart = build_bars(buckets, width=380, height=130)

    last = sessions[0] if sessions else None
    last_text = "—"
    last_hint = "近 30 天没有训练记录"
    if last:
        pieces = [last.activity_label]
        if last.duration_minutes:
            pieces.append(f"{last.duration_minutes} 分钟")
        if last.body_part_labels:
            pieces.append("/".join(last.body_part_labels))
        last_text = " · ".join(pieces)
        last_hint = last.session_date

    facts = [
        PanelFact("本周训练", f"{weekly} 次", f"{week_start.isoformat()} ~ {end.isoformat()}"),
        PanelFact("最近一次", last_text, last_hint, wide=True),
        PanelFact(
            "今日建议",
            guidance.category_label if guidance else "资料不足",
            guidance.headline[:36] if guidance else "先记录睡眠与状态",
        ),
        PanelFact(
            "每周计划",
            f"{prefs.days_per_week} 天" if prefs.days_per_week else "未设置",
            "训练偏好，可在训练页修改",
        ),
    ]

    state = "ok" if sessions and guidance and guidance.category != CATEGORY_INSUFFICIENT else (
        "partial" if sessions or guidance else "empty"
    )
    lead = guidance.headline if guidance else "先补录睡眠和今天的感受，建议才有依据。"
    core = CoreState(
        kicker="今日建议",
        value=guidance.category_label if guidance else "资料不足",
        sub=_clip(guidance.headline, 34) if guidance else "还没有足够数据",
        note=f"本周已记录 {weekly} 次",
        state=state,
    )

    panel = Panel(
        key="training",
        title="训练与建议",
        kicker=f"近 4 周 · 截至 {end.isoformat()}",
        lead=lead,
        state=state,
        facts=tuple(facts),
        chart=chart,
        chart_caption="近 4 周每周已记录的训练次数；没有记录就是 0 次，不是没算出来。",
        notes=(
            "建议来自既有规则引擎（睡眠、心率、主观反馈与训练记录），不是根据界面上的数字临时编的。",
            "不安排高强度训练，也不提供减重或补偿性加练建议。",
        ),
        actions=(
            PanelAction("训练记录", PAGE_HREFS["training"], True),
            PanelAction("记录今天状态", PAGE_HREFS["training"] + "#feedback"),
        ),
        empty_title="还没有训练记录",
        empty_text="在训练页记一次训练或今天的状态，建议会跟着变。",
    )
    node_summary = f"{weekly} 次/周" if weekly else "—"
    return panel, core, node_summary, bool(sessions)


def _reports_panel(conn, end: date, *, counts: dict) -> tuple[Panel, CoreState, str, bool]:
    has_records = bool(counts.get("confirmed"))
    text = ""
    quality = ""
    if has_records:
        payload = build_daily(conn, end)
        text = _clip(payload.text, 260)
        quality = f"{payload.period_start} 简报 · {len(payload.summaries)} 项指标"

    facts = [
        PanelFact("数据截至", end.isoformat(), "最近一条已确认记录"),
        PanelFact("已确认记录", f"{counts.get('confirmed', 0)} 条", "可用于统计"),
        PanelFact("待确认", f"{counts.get('pending', 0)} 条", "确认前不进趋势"),
    ]

    state = "ok" if has_records else "empty"
    core = CoreState(
        kicker="已确认记录",
        value=f"{counts.get('confirmed', 0)} 条",
        sub="已确认记录，数字可追溯" if has_records else "还没有可生成报告的数据",
        note=f"数据截至 {end.isoformat()}",
        state=state,
    )
    panel = Panel(
        key="reports",
        title="当日报告",
        kicker=quality or f"截至 {end.isoformat()}",
        lead=text or "还没有可生成报告的数据。",
        state=state,
        facts=tuple(facts),
        notes=(
            "报告用本地模板生成，数字全部取自已确认记录；AI 改写需要你点按钮才会发生。",
            "报告是趋势观察，不构成诊断。",
        ),
        actions=(
            PanelAction("打开报告", PAGE_HREFS["reports"], True),
            PanelAction("导出 CSV", "/export.csv"),
        ),
        empty_title="还没有报告",
        empty_text="导入并确认第一批记录后，这里会出现当日与本周报告。",
    )
    node_summary = f"{counts.get('confirmed', 0)} 条"
    return panel, core, node_summary, has_records


def _assistant_panel(
    conn, *, ai_enabled: bool, ai_model: str, hrefs: dict[str, str]
) -> tuple[Panel, CoreState, str, bool]:
    turns = chat_store.load(conn)
    last_question = next((t.get("text", "") for t in reversed(turns) if t.get("role") == "user"), "")

    facts = [
        PanelFact(
            "接口状态",
            f"已配置 · {ai_model}" if ai_enabled else "未配置",
            "密钥只保存在本机 .env" if ai_enabled else "在设置页填写地址与密钥",
        ),
        PanelFact(
            "对话记录",
            f"{len(turns)} 条" if turns else "还没有",
            "存在本机数据库，不上传",
        ),
        PanelFact("上次提问", _clip(last_question, 22) if last_question else "—", ""),
    ]

    state = "ok" if ai_enabled else "partial"
    core = CoreState(
        kicker="接口状态",
        value="可用" if ai_enabled else "未配置",
        sub="解释已有结论，不诊断" if ai_enabled else "配置接口后才能对话",
        note=ai_model if ai_enabled else "设置 → AI 分析接口",
        state=state,
    )
    panel = Panel(
        key="assistant",
        title="AI 助手",
        kicker="只解释已经算好的结论",
        lead=(
            "可以问睡眠、心率、训练这类问题；回答只引用库里的数字，不诊断、不写库。"
            if ai_enabled
            else "还没有配置 AI 接口。记录、统计、提醒都不受影响；配置后可以就已有数据提问。"
        ),
        state=state,
        facts=tuple(facts),
        notes=(
            "AI 只解释已经算好的结论；预填的训练表单也要你确认后才会保存。",
            "提问会发送到你自己配置的服务商；不配置就完全不出网。",
        ),
        actions=(
            PanelAction("打开对话", hrefs.get("assistant", PAGE_HREFS["assistant"]), True),
            PanelAction("AI 接口设置", PAGE_HREFS["settings"]),
        ),
        empty_title="AI 接口未配置",
        empty_text="在设置页填一个 OpenAI 兼容接口即可；不配置也能用全部记录与统计功能。",
    )
    node_summary = "可用" if ai_enabled else "未配置"
    return panel, core, node_summary, ai_enabled


def _trends_panel(conn, end: date) -> tuple[Panel, CoreState, str, bool]:
    start = end - timedelta(days=13)
    rows: list[SparkRow] = []
    for metric, label in (
        ("night_sleep_minutes", "夜间睡眠"),
        ("resting_hr_bpm", "静息心率"),
        ("steps_count", "步数"),
        ("activity_energy_kcal", "活动消耗"),
    ):
        spec = get_metric(metric)
        summary = summarize(conn, metric, start, end)
        latest = summary.points[-1] if summary.points else None
        rows.append(
            SparkRow(
                metric=metric,
                label=label,
                value_text=format_number(latest.value, spec.unit, metric) if latest else "—",
                date_text=(
                    f"{latest.record_date} · 有效 {summary.sample_days}/14 天"
                    if latest
                    else "近 14 天无数据"
                ),
                href=f"{PAGE_HREFS['trends']}?metric={metric}&days=30",
                spark=build_spark(day_series(conn, metric, start, end), width=118, height=32),
            )
        )

    have = any(row.value_text != "—" for row in rows)
    core = CoreState(
        kicker="近 14 天",
        value=f"{sum(1 for r in rows if r.value_text != '—')}/4",
        sub="个指标有近 14 天数据" if have else "还没有可画趋势的数据",
        note="每项单独一张图",
        state="ok" if have else "empty",
    )
    panel = Panel(
        key="trends",
        title="指标趋势",
        kicker=f"近 14 天 · 截至 {end.isoformat()}",
        lead="每个指标单独一张小图——量纲不同，叠在一张图上只会制造假可比性。",
        state="ok" if have else "empty",
        sparks=tuple(rows),
        notes=(
            "缺失处断线、不补 0；点任意一行进趋势页换指标与时间范围。",
        ),
        actions=(PanelAction("全部指标趋势", PAGE_HREFS["trends"], True),),
        empty_title="还没有可画的数据",
        empty_text="导入并确认记录后，这里会按指标分列显示最近 14 天的走势。",
    )
    covered = sum(1 for r in rows if r.value_text != "—")
    return panel, core, f"{covered}/4 项", have


def ring_view(
    conn,
    *,
    guidance: Guidance | None,
    tiles: list[Tile],
    counts: dict,
    conflicts: int,
    unread: int,
    end: date,
    today: date,
    target_minutes: int,
    ai_enabled: bool,
    ai_model: str,
    hrefs: dict[str, str],
) -> RingView:
    """星环首页的全部视图数据。

    每个模块的"中心文案 + 展开面板"都来自它自己那一页已经在用的统计函数，
    这样环上的数字与点进去之后的数字必然一致。
    """
    builders = {
        "sleep": lambda: _sleep_panel(conn, end, target_minutes=target_minutes),
        "recovery": lambda: _recovery_panel(conn, end, today=today),
        "training": lambda: _training_panel(conn, end, guidance=guidance),
        "reports": lambda: _reports_panel(conn, end, counts=counts),
        "assistant": lambda: _assistant_panel(
            conn, ai_enabled=ai_enabled, ai_model=ai_model, hrefs=hrefs
        ),
        "trends": lambda: _trends_panel(conn, end),
    }

    panels: list[Panel] = []
    cores: dict[str, CoreState] = {}
    nodes: list[RingNode] = []
    for module in RING_MODULES:
        panel, core, summary, has_data = builders[module.key]()
        panels.append(panel)
        cores[module.key] = core
        nodes.append(
            RingNode(
                key=module.key,
                label=module.label,
                idx=module.idx,
                angle=module.idx * 60,
                href=module.href,
                panel_title=module.panel_title,
                accent=module.accent,
                summary=summary,
                has_data=has_data,
            )
        )

    return RingView(
        core=_core_state(guidance, counts=counts, end=end, tiles=tiles),
        nodes=tuple(nodes),
        cores=cores,
        panels=tuple(panels),
        idle_panel=_idle_panel(guidance),
    )


def _core_state(
    guidance: Guidance | None, *, counts: dict, end: date, tiles: list[Tile]
) -> CoreState:
    """默认态的中心：说"今天大概处于什么状态"，不说分数。"""
    confirmed = counts.get("confirmed", 0)
    if confirmed == 0:
        return CoreState(
            kicker="今日状态",
            value="暂无数据",
            sub="导入一份健康数据后开始",
            note="记录、统计、提醒都不需要联网",
            state="empty",
        )
    if guidance is None or guidance.category == CATEGORY_INSUFFICIENT:
        return CoreState(
            kicker="今日状态",
            value="数据不足",
            sub="先补录昨晚睡眠与今天的状态",
            note=f"已确认 {confirmed} 条 · 截至 {end.isoformat()}",
            state="partial",
        )
    coverage = next((t for t in tiles if t.metric == "night_sleep_minutes"), None)
    note = f"数据截至 {end.isoformat()}"
    if coverage is not None and coverage.days:
        note += f" · 睡眠有效 {coverage.days}/7 天"
    return CoreState(
        kicker="今日状态",
        value=guidance.category_label,
        sub=_clip(guidance.headline, 34),
        note=note,
        state="ok",
    )


def _idle_panel(guidance: Guidance | None) -> Panel:
    """默认态右侧只放一张卡：今日建议。它不随模块切换，所以单独给。"""
    if guidance is None:
        return Panel(
            key="idle",
            title="今日建议",
            kicker="",
            lead="还没有足够的数据给建议。",
            state="empty",
            empty_title="还没有建议",
            empty_text="导入睡眠记录后，这里会给出今天的建议与依据。",
            actions=(PanelAction("导入数据", PAGE_HREFS["imports"], True),),
        )
    return Panel(
        key="idle",
        title="今日建议",
        kicker=f"{guidance.for_date} · 规则 {guidance.rule_version}",
        lead=guidance.headline,
        state="ok" if guidance.category != CATEGORY_INSUFFICIENT else "partial",
        actions=(PanelAction("查看依据", PAGE_HREFS["training"], True), PanelAction("全部页面", PAGE_HREFS["records"])),
    )


def assistant_shortcuts(guidance: Guidance | None, hrefs: dict[str, str]) -> list[dict[str, str]]:
    """助手快捷动作：只做"解释 + 导航 + 预填"，不写库、不诊断。

    预填一律走 GET 参数落到 `/training` 表单，用户确认后才提交（POST + CSRF）。
    第一项固定是"去自由提问"入口：助手面板本身没有输入框（零 JS 做不了原地问答），
    所以必须有一步跳到 `/assistant`，否则用户会以为它只能点这几个按钮。
    """
    training = hrefs.get("training", "/training")
    items: list[dict[str, str]] = [
        {"label": "去提个自定义问题", "href": hrefs.get("assistant", "/assistant")},
        {"label": "今天适合练什么？", "href": training + "?from=assistant&focus=advice"},
        {"label": "打开睡眠页", "href": hrefs.get("sleep", "/sleep")},
        {"label": "看看提醒", "href": hrefs.get("alerts", "/alerts")},
    ]
    if guidance is None:
        return items
    if guidance.category == "rest":
        items.insert(
            2,
            {
                "label": "记录今天的状态",
                "href": training + "?from=assistant&focus=feedback#feedback",
            },
        )
    elif guidance.category == "light_activity":
        items.insert(
            2,
            {
                "label": "记一次轻松活动（预填）",
                "href": training + "?from=assistant&type=walking&duration=25&content=轻松步行",
            },
        )
    elif guidance.category == "regular_training":
        items.insert(
            2,
            {
                "label": "记一次训练（预填）",
                "href": training + "?from=assistant&type=strength&duration=40",
            },
        )
    return items
