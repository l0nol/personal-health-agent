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
from ..reports.format import format_number
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


@dataclass(frozen=True, slots=True)
class Sector:
    """表盘扇区：身份信息来自 `pages.py`，预览值来自同一份 tiles/计数。"""

    key: str
    idx: int
    label: str
    href: str
    kicker: str
    value_text: str
    sub: str


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


def chronos_sectors(tiles: list[Tile], counts: dict, conflicts: int, unread: int, end: date) -> list[Sector]:
    """表盘六扇区：身份来自 pages.py，预览值全部取自 tiles / 计数，不新算任何数。"""
    sleep, hr = tiles[0], tiles[1]
    preview = {
        "sleep": ("昨夜睡眠", format_number(sleep.value, sleep.unit, sleep.metric), _tile_foot(sleep)),
        "trends": ("静息心率", format_number(hr.value, hr.unit, hr.metric), _tile_foot(hr)),
        "imports": ("待确认记录", f"{counts['pending']} 条", "写入后仍需逐条确认"),
        "alerts": ("未读提醒", f"{unread} 条", "同一窗口不重复 · 冷却 72 小时"),
        "reports": ("已确认记录", f"{counts['confirmed']} 条", f"数据截至 {end.isoformat()}"),
        "settings": ("界面模式", "时钟", "数据只在本机存储"),
    }
    sectors: list[Sector] = []
    for page in DIAL_PAGES:
        kicker, value_text, sub = preview[page.key]
        sectors.append(
            Sector(
                key=page.key,
                idx=page.dial_idx,
                label=page.dial_label,
                href=page.href,
                kicker=kicker,
                value_text=value_text,
                sub=sub,
            )
        )
    return sectors
