"""界面视图模型与数据装配：路由与模板之间的**稳定契约**。

分层约定（改代码前先看这张表）：

    路由 (app.py)  →  context.py  →  模板 (templates/)
      取数、鉴权        装配成视图模型      只负责长相

- 路由不直接拼字典给模板；模板也不做业务计算。
- 这里的 dataclass 就是"模板能拿到哪些字段"的清单：加字段只改这里 + 模板，
  不会波及统计、存储或报告层。
- 所有数值都取自既有统计函数（`summarize` / `compute_baseline` / 计数查询），
  本模块**不做第二套口径**：换皮不会换数。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ..analytics.aggregate import summarize
from ..analytics.baseline import compute_baseline
from ..catalog import METRICS, get_metric
from ..guidance.contract import Guidance
from ..reports.format import format_minutes, format_number
from ..storage import alerts as alert_store
from ..storage import training as training_store
from .pages import DIAL_PAGES


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
    """首页 / 表盘右侧的指标卡。"""

    metric: str
    label: str
    value: float | None
    date: str | None
    unit: str
    days: int
    status: str
    href: str
    reference: Reference


#: 刻度配色（T11）。只表示"哪个功能被选中"，不是健康评分，也不要改成红绿。
DIAL_ACCENTS: dict[str, str] = {
    "sleep": "#7C9CFF",
    "trends": "#3ED3C0",
    "training": "#F2B04C",
    "alerts": "#F2799B",
    "reports": "#B39DFF",
    "settings": "#9FB6C4",
}


@dataclass(frozen=True, slots=True)
class Sector:
    """表盘刻度：身份来自 `pages.py`，预览值全部取自既有统计函数。

    `facts` 是中心卡的两条事实；`accent` 只用于该刻度的配色。
    """

    key: str
    idx: int
    label: str
    href: str
    kicker: str
    value_text: str
    sub: str
    facts: tuple[tuple[str, str], ...] = ()
    accent: str = ""


# ---------------- 装配函数 ----------------

def metric_labels() -> dict[str, str]:
    """字段名 → 中文名，供模板显示"中文名（字段名）"。"""
    return {metric.name: metric.display for metric in METRICS}


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


#: 首页与表盘右侧的四个指标卡（顺序即展示顺序）
_TILE_METRICS: tuple[tuple[str, str], ...] = (
    ("night_sleep_minutes", "/sleep"),
    ("resting_hr_bpm", "/trends?metric=resting_hr_bpm"),
    ("steps_count", "/trends?metric=steps_count"),
    ("activity_energy_kcal", "/trends?metric=activity_energy_kcal"),
)


def dashboard_tiles(conn, end: date) -> list[Tile]:
    """首页与时钟模式共用的指标卡（同一来源、同一窗口、同一格式化）。"""
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


def chronos_sectors(
    conn,
    tiles: list[Tile],
    counts: dict,
    conflicts: int,
    unread: int,
    end: date,
    *,
    guidance: Guidance | None = None,
) -> list[Sector]:
    """拨盘六个刻度的预览。

    所有数字都来自 `summarize()` / `reference()` / 计数 / `build_guidance()`，
    与简洁模式同一口径；没有数据就写"暂无"，不补 0。
    """
    start = end - timedelta(days=6)
    sleep = summarize(conn, "night_sleep_minutes", start, end)
    hr = summarize(conn, "resting_hr_bpm", start, end)
    hr_ref = reference(conn, "resting_hr_bpm", end)
    weekly = training_store.count_sessions(conn, start=start, end=end)
    last_session = training_store.list_sessions(conn, start=end - timedelta(days=13), end=end, limit=1)
    last_alert = alert_store.list_alerts(conn, limit=1)

    sleep_latest = sleep.points[-1] if sleep.points else None
    hr_latest = hr.points[-1] if hr.points else None

    def sleep_mean_text() -> str:
        if sleep.sample_days < 3:
            return "样本不足"
        return format_minutes(sleep.mean or 0)

    parts_text = "—"
    if last_session:
        session = last_session[0]
        gap = (end - date.fromisoformat(session.session_date)).days
        if gap <= 1:
            parts_text = "/".join(session.body_part_labels) or "未填部位"
        else:
            parts_text = f"{gap} 天前"

    preview: dict[str, tuple[str, str, str, tuple[tuple[str, str], ...]]] = {
        "sleep": (
            "昨夜睡眠",
            format_minutes(sleep_latest.value) if sleep_latest else "暂无",
            "近 7 天窗口 · 只看已确认",
            (
                ("近 7 天均值", sleep_mean_text()),
                ("覆盖", f"{sleep.sample_days}/7 天"),
            ),
        ),
        "trends": (
            "静息心率",
            format_number(hr_latest.value, "bpm", "resting_hr_bpm") if hr_latest else "暂无",
            "个人基线用中位数 + MAD",
            (
                ("最近一次", format_number(hr_latest.value, "bpm", "resting_hr_bpm") if hr_latest else "—"),
                ("基线", "已建立" if hr_ref.state == "active" else "样本不足"),
            ),
        ),
        "training": (
            "今日建议",
            guidance.category_label if guidance else "暂无",
            guidance.headline if guidance else "先记录睡眠与状态",
            (
                ("近 48 小时", parts_text),
                ("本周训练", f"{weekly} 次"),
            ),
        ),
        "alerts": (
            "未读提醒",
            f"{unread} 条",
            "同一窗口不重复 · 冷却 72 小时",
            (
                ("最近规则", last_alert[0]["rule_id"] if last_alert else "—"),
                ("待裁决冲突", f"{conflicts} 条"),
            ),
        ),
        "reports": (
            "数字可追溯",
            f"{counts.get('confirmed', 0)} 条已确认",
            f"数据截至 {end.isoformat()}",
            (
                ("待确认", f"{counts.get('pending', 0)} 条"),
                ("记录总数", f"{counts.get('total', 0)} 条"),
            ),
        ),
        "settings": (
            "界面模式",
            "时钟模式",
            "与简洁模式同一套数据",
            (
                ("睡眠目标", "默认参考值 · 环境变量可改"),
                ("模式切换", "设置页可切回简洁"),
            ),
        ),
    }

    sectors: list[Sector] = []
    for page in DIAL_PAGES:
        kicker, value_text, sub, facts = preview[page.key]
        sectors.append(
            Sector(
                key=page.key,
                idx=page.dial_idx,
                label=page.dial_label,
                href=page.href,
                kicker=kicker,
                value_text=value_text,
                sub=sub,
                facts=facts,
                accent=DIAL_ACCENTS.get(page.key, ""),
            )
        )
    return sectors


def assistant_shortcuts(guidance: Guidance | None, hrefs: dict[str, str]) -> list[dict[str, str]]:
    """助手快捷动作：只做"解释 + 导航 + 预填"，不写库、不诊断。

    预填一律走 GET 参数落到 `/training` 表单，用户确认后才提交（POST + CSRF）。
    """
    training = hrefs.get("training", "/training")
    items: list[dict[str, str]] = [
        {"label": "今天适合练什么？", "href": training + "?from=assistant&focus=advice"},
        {"label": "打开睡眠页", "href": hrefs.get("sleep", "/sleep")},
        {"label": "看看提醒", "href": hrefs.get("alerts", "/alerts")},
    ]
    if guidance is None:
        return items
    if guidance.category == "rest":
        items.insert(
            1,
            {
                "label": "记录今天的状态",
                "href": training + "?from=assistant&focus=feedback#feedback",
            },
        )
    elif guidance.category == "light_activity":
        items.insert(
            1,
            {
                "label": "记一次轻松活动（预填）",
                "href": training + "?from=assistant&type=walking&duration=25&content=轻松步行",
            },
        )
    elif guidance.category == "regular_training":
        budget = ""
        items.insert(
            1,
            {
                "label": "记一次训练（预填）",
                "href": training + f"?from=assistant&type=strength&duration={budget or 40}",
            },
        )
    return items
