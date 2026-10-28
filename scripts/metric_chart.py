#!/usr/bin/env python3
"""把某个指标的已确认记录画成一张独立图表（HTML + 内联 SVG）。

为什么要单独一个脚本：应用的 /trends 只给 7/30/90 天三档，想看"5 个月长什么样"
就得自己画。这里不引入任何前端依赖，直接用项目自己的配色出 SVG，
既能在浏览器里看，也能无头截图存成 PNG。

用法：
    python scripts/metric_chart.py --metric weight_kg --out reports/weight-trend.html
    python scripts/metric_chart.py --metric weight_kg --from 2026-03-01 --title 体重
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date
from pathlib import Path

# 与 static/css/tokens.css 一致的深色 token（图表要跟应用同一个观感）
BG = "#060B14"
SURFACE = "#0B1524"
LINE = "rgba(126,162,200,.18)"
TEXT = "#E7EFF9"
TEXT_2 = "#9CAFC6"
TEXT_3 = "#6A7E95"
ACCENT = "#2DD4BF"
PRIMARY = "#3F7FE8"
WARN = "#E0A34A"

#: 来源不明/手工录入的记录（extraction_method 为空）用空心点标出来，
#: 其余（体重表 excel、导出包 export、截图 screenshot）都是有名有姓的，
#: 画法上不必再区分——真正要提醒的是"这两个点不知道从哪来的"。
UNMARKED = {None, ""}


def load(metric: str, db: str, start: str | None) -> list[tuple[str, float, str | None]]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    sql = (
        "SELECT record_date, value, extraction_method, notes FROM records"
        " WHERE metric_name = ? AND verification_status = 'confirmed' AND value IS NOT NULL"
    )
    params: list[object] = [metric]
    if start:
        sql += " AND record_date >= ?"
        params.append(start)
    sql += " ORDER BY record_date"
    rows = [
        (r["record_date"], float(r["value"]), r["extraction_method"] if "extraction_method" in r.keys() else None)
        for r in conn.execute(sql, params)
    ]
    conn.close()
    return rows


def nice_bounds(values: list[float]) -> tuple[float, float, float]:
    low, high = min(values), max(values)
    pad = max(1.0, (high - low) * 0.18)
    lo = int((low - pad) // 1)
    hi = int((high + pad) // 1 + 1)
    step = max(1, round((hi - lo) / 4))
    return float(lo), float(hi), float(step)


def render(rows, title: str, unit: str) -> str:
    width, height = 1120, 420
    left, right, top, bottom = 70, 40, 28, 56
    plot_w = width - left - right
    plot_h = height - top - bottom

    days = [date.fromisoformat(r[0]) for r in rows]
    values = [r[1] for r in rows]
    d0, d1 = min(days), max(days)
    span = max((d1 - d0).days, 1)
    lo, hi, step = nice_bounds(values)

    def x_of(d: date) -> float:
        return left + (d - d0).days / span * plot_w

    def y_of(v: float) -> float:
        return top + (hi - v) / (hi - lo) * plot_h

    parts: list[str] = []
    # 横向网格与刻度
    y = lo
    while y <= hi + 0.01:
        yy = y_of(y)
        parts.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{width - right}" y2="{yy:.1f}" stroke="{LINE}" stroke-width="1"/>')
        parts.append(f'<text x="{left - 12}" y="{yy + 4:.1f}" text-anchor="end" fill="{TEXT_3}" font-size="12">{y:.0f}</text>')
        y += step

    # 月份刻度
    month = date(d0.year, d0.month, 1)
    while month <= d1:
        if month >= d0:
            xx = x_of(month)
            parts.append(f'<line x1="{xx:.1f}" y1="{top}" x2="{xx:.1f}" y2="{top + plot_h}" stroke="{LINE}" stroke-width="1" stroke-dasharray="2 6"/>')
            parts.append(f'<text x="{xx:.1f}" y="{height - 26}" text-anchor="middle" fill="{TEXT_3}" font-size="12">{month.month} 月</text>')
        month = date(month.year + (month.month // 12), month.month % 12 + 1, 1)

    # 折线
    pts = " ".join(f"{x_of(d):.1f},{y_of(v):.1f}" for d, v in zip(days, values))
    parts.append(f'<polyline points="{pts}" fill="none" stroke="{PRIMARY}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" opacity=".85"/>')

    # 数据点：实心 = 体重表导入；空心 = 其它来源
    for (day, value, method), d, v in zip(rows, days, values):
        cx, cy = x_of(d), y_of(v)
        solid = method not in UNMARKED
        if solid:
            parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="{ACCENT}" stroke="{BG}" stroke-width="1.5"/>')
        else:
            parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="{SURFACE}" stroke="{WARN}" stroke-width="1.6"/>')

    for (day, value, method), d, v in zip(rows, days, values):
        if method in UNMARKED:
            cx, cy = x_of(d), y_of(v)
            parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="9" fill="none" stroke="{WARN}" stroke-width="1" opacity=".4"/>')

    # 关键点标注：最高 / 最低 / 最近
    def annotate(idx: int, label: str, color: str, dx: float, dy: float) -> None:
        d, v, _ = rows[idx]
        cx, cy = x_of(date.fromisoformat(d)), y_of(v)
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="8" fill="none" stroke="{color}" stroke-width="1.2" opacity=".55"/>')
        anchor = "end" if dx < 0 else "start"
        parts.append(
            f'<text x="{cx + dx:.1f}" y="{cy + dy:.1f}" text-anchor="{anchor}" fill="{color}" '
            f'font-size="12.5" font-weight="500">{label}</text>'
        )

    hi_i = values.index(max(values))
    lo_i = values.index(min(values))
    annotate(hi_i, f"最高 {max(values):.2f}", TEXT_2, 12, -10)
    annotate(lo_i, f"最低 {min(values):.2f}", TEXT_2, 12, 18)
    annotate(len(rows) - 1, f"最近 {values[-1]:.2f}", ACCENT, -14, -12)

    svg = f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{title}趋势">' + "".join(parts) + "</svg>"

    first, last = values[0], values[-1]
    delta = last - first
    chips = [
        ("首次", f"{first:.2f} {unit}"),
        ("最低", f"{min(values):.2f} {unit}"),
        ("最高", f"{max(values):.2f} {unit}"),
        ("最近", f"{last:.2f} {unit}"),
        ("变化", f"{delta:+.2f} {unit}"),
    ]
    chip_html = "".join(
        f'<div class="chip"><span class="k">{k}</span><span class="v">{v}</span></div>' for k, v in chips
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{title}趋势</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; background: {BG}; color: {TEXT};
         font-family: system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; }}
  .wrap {{ max-width: 1180px; margin: 0 auto; padding: 28px 30px 34px; }}
  h1 {{ font-size: 20px; font-weight: 500; margin: 0 0 4px; letter-spacing: -.01em; }}
  .sub {{ margin: 0 0 18px; color: {TEXT_3}; font-size: 12.5px; }}
  .chips {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 16px; }}
  .chip {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 12px;
           padding: 9px 14px; min-width: 96px; }}
  .chip .k {{ display: block; font-size: 11.5px; color: {TEXT_3}; }}
  .chip .v {{ font-size: 16px; font-variant-numeric: tabular-nums; }}
  .panel {{ background: linear-gradient(180deg, rgba(17,28,45,.86), rgba(10,19,33,.86));
            border: 1px solid {LINE}; border-radius: 16px; padding: 10px 8px 4px; }}
  .foot {{ margin-top: 14px; font-size: 12px; color: {TEXT_3}; line-height: 1.75; }}
  .dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%;
          vertical-align: 1px; margin-right: 5px; }}
</style></head>
<body><div class="wrap">
  <h1>{title}</h1>
  <p class="sub">{rows[0][0]} ~ {rows[-1][0]} · 共 {len(rows)} 次记录（只统计已确认）</p>
  <div class="chips">{chip_html}</div>
  <div class="panel">{svg}</div>
  <p class="foot">
    <span class="dot" style="background:{ACCENT}"></span>实心点＝有明确来源（体重表 / 导出包 / 截图）；
    <span class="dot" style="border:1.5px solid {WARN}; background:transparent"></span>空心点＝来源未标注（手工录入），数值按原样保留、未做合并。<br>
    折线只连接"有记录的日子"；7 月中旬到 8 月底没有称重，跨过去的直线不代表期间的变化。
  </p>
</div></body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metric", default="weight_kg")
    ap.add_argument("--title", default="体重")
    ap.add_argument("--unit", default="kg")
    ap.add_argument("--db", default="D:/HealthData/health.db")
    ap.add_argument("--from", dest="start", default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows = load(args.metric, args.db, args.start)
    if not rows:
        raise SystemExit("没有已确认记录")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(rows, args.title, args.unit), encoding="utf-8")
    print(f"{len(rows)} 个点 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
