"""对话里的"取数"和"跳转"：本地确定性实现，不依赖模型的工具调用能力。

为什么不用 function calling：
- 本项目零外部依赖、服务端渲染，一次问答要花多少钱、发多少东西必须可预知；
- 让模型"要求取数"等于再绕一圈网络，而用户想问的指标在本地一秒就能算出来；
- 最关键的是：**数字必须由程序算**。让模型参与"取数"这一步，就有机会被它编出来。

所以这里做的是"读懂问题里提到了什么"：哪个指标、哪一段时间、想去哪个页面。
读到了就把数据算好塞进 prompt，同时把跳转链接渲染在回答下面；
读不到就什么都不加——宁可不猜。
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from ..analytics.aggregate import summarize
from ..catalog import METRICS, MetricSpec
from ..reports.builder import latest_data_date
from ..ui.pages import page_by_key

#: 简称/口语 → 指标名。只收"说了就一定是这个指标"的说法，避免瞎匹配。
#: 顺序无关，匹配时按别名长度从长到短试，先长后短（"睡眠心率"优先于"睡眠"）。
_ALIASES: dict[str, tuple[str, ...]] = {
    "night_sleep_minutes": ("夜间睡眠", "睡眠时长", "睡觉", "睡眠", "睡了", "睡得", "睡"),
    "nap_minutes": ("零星小睡", "小睡", "午睡", "打盹"),
    "resting_hr_bpm": ("静息心率",),
    "sleep_avg_hr_bpm": ("睡眠平均心率", "睡眠心率"),
    "hrv_avg_ms": ("心率变异性", "hrv"),
    "sleep_avg_spo2_percent": ("睡眠血氧", "血氧"),
    "respiratory_rate_avg_brpm": ("呼吸率", "呼吸频率"),
    "stress_score": ("压力",),
    "training_index": ("训练指数", "训练负荷"),
    "vo2max_ml_kg_min": ("最大摄氧量", "摄氧量", "vo2max"),
    "weight_kg": ("体重",),
    "steps_count": ("步数", "走多少"),
    "deep_sleep_percent": ("深睡占比", "深睡比例", "深睡"),
    "light_sleep_percent": ("浅睡占比", "浅睡比例", "浅睡"),
    "rem_sleep_percent": ("快速眼动", "rem"),
    "awake_count": ("醒来次数", "清醒次数"),
    "sleep_score": ("睡眠评分",),
    "deep_sleep_continuity_score": ("深睡连续性",),
    "sleep_breathing_quality_score": ("呼吸质量",),
    "running_index": ("跑力指数", "跑力"),
}

#: 页面关键词 → 页面 key。用户说"打开报告"就该能跳过去。
_PAGE_ALIASES: dict[str, tuple[str, ...]] = {
    "sleep": ("睡眠页", "看睡眠", "睡眠分析"),
    "trends": ("趋势页", "看趋势", "指标趋势", "走势"),
    "reports": ("报告", "简报", "周报"),
    "training": (
        "训练页", "训练建议", "今天练什么", "练什么", "该练", "要不要练",
        "健身", "锻炼", "运动一下", "撸铁", "练腿", "练胸",
    ),
    "records": ("记录页", "记录列表", "所有记录"),
    "conflicts": ("冲突",),
    "alerts": ("提醒",),
    "imports": ("导入",),
    "settings": ("设置",),
}

_WINDOW_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"今天|今日|当天"), "today"),
    (re.compile(r"昨天|昨日"), "yesterday"),
    (re.compile(r"前天"), "before_yesterday"),
    (re.compile(r"上周|上个?星期"), "last_week"),
    (re.compile(r"这周|本周|最近一周|近一周|过去一周|七天|7\s*天"), "this_week"),
    (re.compile(r"一个月|30\s*天|近月"), "month"),
    (re.compile(r"28\s*天"), "d28"),
    (re.compile(r"两周|14\s*天"), "fortnight"),
)
_EXPLICIT_DAYS = re.compile(r"(?:近|最近|过去)?\s*(\d{1,3})\s*天")

MAX_CARD_POINTS = 31


@dataclass(frozen=True, slots=True)
class Window:
    start: date
    end: date
    label: str


def _end_day(conn: sqlite3.Connection) -> date:
    return latest_data_date(conn) or date.today()


def guess_window(question: str, conn: sqlite3.Connection) -> Window:
    """从问题里读出"想看哪一段"。读不出来就按近 7 天。"""
    text = (question or "").lower()
    end = _end_day(conn)

    for pattern, kind in _WINDOW_PATTERNS:
        if not pattern.search(text):
            continue
        if kind == "today":
            return Window(end, end, f"今天（{end.isoformat()}）")
        if kind == "yesterday":
            day = end - timedelta(days=1)
            return Window(day, day, f"昨天（{day.isoformat()}）")
        if kind == "before_yesterday":
            day = end - timedelta(days=2)
            return Window(day, day, f"前天（{day.isoformat()}）")
        if kind == "last_week":
            last_end = end - timedelta(days=7)
            last_start = last_end - timedelta(days=6)
            return Window(last_start, last_end, f"上周（{last_start.isoformat()} ~ {last_end.isoformat()}）")
        if kind == "month":
            days = 30
        elif kind == "d28":
            days = 28
        elif kind == "fortnight":
            days = 14
        else:
            days = 7
        return _days_window(end, days)

    explicit = _EXPLICIT_DAYS.search(text)
    if explicit:
        days = max(1, min(int(explicit.group(1)), 365))
        return _days_window(end, days)

    return _days_window(end, 7)


def _days_window(end: date, days: int) -> Window:
    if days <= 1:
        return Window(end, end, f"今天（{end.isoformat()}）")
    start = end - timedelta(days=days - 1)
    return Window(start, end, f"近 {days} 天（{start.isoformat()} ~ {end.isoformat()}）")


def guess_metric(question: str) -> MetricSpec | None:
    """认出问题里提到的指标。多个命中时取"最早出现且别名最长"的那个。"""
    text = (question or "").lower()
    best: tuple[int, int, MetricSpec] | None = None
    for metric in METRICS:
        aliases = list(_ALIASES.get(metric.name, ())) + [metric.display]
        for alias in aliases:
            key = alias.lower()
            position = text.find(key)
            if position < 0:
                continue
            score = (position, -len(key), metric)
            if best is None or (score[0], score[1]) < (best[0], best[1]):
                best = score
    return best[2] if best else None


def page_links(question: str, metric: MetricSpec | None = None) -> list[dict[str, str]]:
    """问题里提到的页面 → 跳转链接。指标命中时额外给一条"去趋势页看这个指标"。"""
    text = (question or "").lower()
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for key, aliases in _PAGE_ALIASES.items():
        if not any(alias in text for alias in aliases):
            continue
        try:
            page = page_by_key(key)
        except KeyError:
            continue
        if page.href in seen:
            continue
        seen.add(page.href)
        links.append({"label": f"打开{page.label}", "href": page.href})

    if metric is not None:
        href = f"{page_by_key('trends').href}?metric={metric.name}&days=30"
        if href not in seen:
            links.append({"label": f"看{metric.display}的趋势", "href": href})
    return links[:4]


def metric_card(conn: sqlite3.Connection, metric: MetricSpec, window: Window) -> dict | None:
    """把这段时间的指标算成一张小卡片。没有数据就返回 None，不硬凑。"""
    if window.start > window.end:
        return None
    summary = summarize(conn, metric.name, window.start, window.end)
    if summary.sample_days == 0:
        return None
    points = [
        {"d": point.record_date[5:], "v": point.value}
        for point in summary.points[-MAX_CARD_POINTS:]
    ]
    return {
        "metric": metric.name,
        "title": metric.display,
        "unit": metric.unit,
        "window": window.label,
        "days": summary.sample_days,
        "window_days": summary.window_days,
        "status": summary.status,
        "mean": summary.mean,
        "min": summary.min,
        "max": summary.max,
        "points": points,
    }


def card_prompt(card: dict | None) -> dict | None:
    """给模型的卡片摘要：不要逐日 31 个点，只要它能引用的几个数字。"""
    if not card:
        return None
    return {
        "指标": card["title"],
        "区间": card["window"],
        "有效天数": f"{card['days']}/{card['window_days']}",
        "均值": card["mean"],
        "最小": card["min"],
        "最大": card["max"],
        "单位": card["unit"],
        "数据状态": card["status"],
        "逐日": [f"{point['d']}={point['v']}" for point in card["points"]],
    }


def resolve(conn: sqlite3.Connection, question: str) -> tuple[dict | None, list[dict[str, str]]]:
    """一次把"取数"和"跳转"都解析出来。返回 (卡片, 链接)。"""
    metric = guess_metric(question)
    window = guess_window(question, conn)
    card = metric_card(conn, metric, window) if metric is not None else None
    return card, page_links(question, metric)
