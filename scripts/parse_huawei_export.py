#!/usr/bin/env python3
"""把华为运动健康「导出包」解析成项目可导入的 CSV（按天聚合）。

和截图那条路的区别：
- 截图是**设备已经算好的结论**（评分、清醒次数、连续性……），照着抄；
- 导出包是**原始采样**（每分钟睡眠分期、每次心率、每 5 分钟压力……），
  所以这里必须**自己聚合**，聚合规则写在下面，且每条都进 notes 里说明来源。

只产出"能从原始数据算出来、且与设备口径一致"的指标。算不出来的（睡眠评分、
深睡连续性、呼吸质量、清醒次数）**一律不产出**——这些是设备算法给的，自己凑一个
数字比留空更糟。

用法：
    python scripts/parse_huawei_export.py --src "D:/HUAWEI_HEALTH_20261019170027" \\
        --out data/inbox/2026-10-26-huawei-export.csv
    python scripts/parse_huawei_export.py --src ... --inventory
    python scripts/parse_huawei_export.py --src ... --validate --db D:/HealthData/health.db
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

# ---------------- 口径参数 ----------------

#: 一次"睡眠"的判定：相邻两段之间隔超过这么多分钟，就算醒了/结束了
SESSION_GAP_MINUTES = 60
#: 夜间睡眠的起点不早于这个小时（再早算午睡/傍晚补觉）
NIGHT_START_HOUR = 18
#: 午睡判定：落在 09:00–17:59 之间的 NOON 段
NAP_HOURS = range(9, 18)

STAGE_KEYS = {
    "PROFESSIONAL_SLEEP_DEEP": "deep",
    "PROFESSIONAL_SLEEP_SHALLOW": "light",
    "PROFESSIONAL_SLEEP_DREAM": "rem",
    "PROFESSIONAL_SLEEP_WAKE": "wake",
    "PROFESSIONAL_SLEEP_NOON": "noon",
}

SOURCE_PAGE = "华为运动健康-导出包"
EXTRACTION_METHOD = "export"


def _ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000)


def _json_value(raw):
    """导出包里 value 有两种形态：纯数字字符串，或 JSON 字符串。"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return float(text)
        except ValueError:
            return None


# ---------------- 读取 ----------------

def iter_sample_points(src: Path):
    """遍历 Health detail data 目录下的所有采样点。"""
    folder = src / "Health detail data & description"
    if not folder.is_dir():
        raise SystemExit(f"找不到目录：{folder}")
    for path in sorted(folder.glob("*.json")):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"跳过 {path.name}: {exc}", file=sys.stderr)
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            for point in row.get("samplePoints") or []:
                key = point.get("key")
                start = point.get("startTime")
                end = point.get("endTime")
                if key is None or start is None:
                    continue
                yield key, start, end if end is not None else start, point.get("value")


def inventory(src: Path) -> None:
    counts: dict[str, int] = defaultdict(int)
    spans: dict[str, tuple[int, int]] = {}
    for key, start, end, _value in iter_sample_points(src):
        counts[key] += 1
        lo, hi = spans.get(key, (start, end))
        spans[key] = (min(lo, start), max(hi, end))
    print(f"{'key':<40}{'points':>8}  {'from':<12}{'to':<12}")
    for key, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        lo, hi = spans[key]
        print(f"{key:<40}{n:>8}  {_ms(lo):%Y-%m-%d}  {_ms(hi):%Y-%m-%d}")


# ---------------- 睡眠 ----------------

def _minute_timeline(src: Path) -> dict[int, str]:
    """把分期段摊平成"每分钟一个格子"。

    为什么不用"段时长相加"：导出包里同一晚可能出现**重复段**（设备/云端二次分析各存一份），
    直接相加会把一夜算成 20 多小时。按分钟落格后重复自然折叠，且相邻段自动连续。
    同一分钟若有多份记录，保留先到的那份（原始分析）。
    """
    timeline: dict[int, str] = {}
    for key, start, end, _value in iter_sample_points(src):
        stage = STAGE_KEYS.get(key)
        if stage is None or end <= start:
            continue
        minute = start - start % 60000
        while minute < end:
            timeline.setdefault(minute, stage)
            minute += 60000
    return timeline


def build_sleep_nights(src: Path, stats: dict | None = None) -> dict[str, dict]:
    """把每分钟格子拼成"一夜"，返回 {醒来日期: 统计}。"""
    timeline = _minute_timeline(src)
    # 夜间只用 深/浅/REM/清醒 四种格子；午睡（NOON）另算
    slots = sorted((m, st) for m, st in timeline.items() if st != "noon")

    nights: dict[str, dict] = {}
    session: list[tuple[int, str]] = []
    dropped = 0

    def flush() -> None:
        nonlocal dropped
        if not session:
            return
        bed = _ms(session[0][0])
        wake = _ms(session[-1][0] + 60000)
        minutes: dict[str, int] = defaultdict(int)
        for _minute, stage in session:
            minutes[stage] += 1
        night = minutes["deep"] + minutes["light"] + minutes["rem"]
        if night <= 0:
            return
        # 只认"夜里的那一段"：18 点后入睡，或凌晨/上午入睡
        if not (bed.hour >= NIGHT_START_HOUR or bed.hour < 12):
            dropped += 1
            return
        # 跨度超过 18 小时的"一夜"基本都是漏了断点，宁可丢掉也不写进趋势
        if (wake - bed) > timedelta(hours=18):
            dropped += 1
            return
        day = wake.date().isoformat()
        bucket = nights.setdefault(
            day,
            {"deep": 0, "light": 0, "rem": 0, "night": 0, "bed": bed, "wake": wake},
        )
        bucket["deep"] += minutes["deep"]
        bucket["light"] += minutes["light"]
        bucket["rem"] += minutes["rem"]
        bucket["night"] += night
        bucket["bed"] = min(bucket["bed"], bed)
        bucket["wake"] = max(bucket["wake"], wake)

    for minute, stage in slots:
        if session and (minute - session[-1][0]) / 60000 > SESSION_GAP_MINUTES:
            flush()
            session = []
        session.append((minute, stage))
    flush()
    if stats is not None:
        stats["dropped_sessions"] = dropped
    return nights


def build_naps(src: Path) -> dict[str, float]:
    """午睡：NOON 段按开始日期累加（只在 09:00–17:59 之间）。"""
    naps: dict[str, float] = defaultdict(float)
    for key, start, end, _value in iter_sample_points(src):
        if STAGE_KEYS.get(key) != "noon" or end <= start:
            continue
        begin = _ms(start)
        if begin.hour not in NAP_HOURS:
            continue
        naps[begin.date().isoformat()] += (end - start) / 60000
    return dict(naps)


# ---------------- 其它日指标 ----------------

def build_daily_metrics(src: Path, nights: dict[str, dict]) -> dict[str, dict]:
    """逐日聚合心率/HRV/血氧/压力/体重。

    每条都写清"用哪些采样、怎么算的"，因为这是**派生值**，不是设备直接给的结论。
    """
    resting: dict[str, list[float]] = defaultdict(list)
    hrv: dict[str, list[float]] = defaultdict(list)
    stress: dict[str, list[float]] = defaultdict(list)
    spo2_sleep: dict[str, list[float]] = defaultdict(list)
    hr_all: dict[str, list[float]] = defaultdict(list)
    weight: dict[str, float] = {}

    # 睡眠窗口：用来判断某个血氧采样是不是"睡眠中"
    windows = [
        (row["bed"], row["wake"]) for row in nights.values() if row["night"] > 0
    ]

    for key, start, end, value in iter_sample_points(src):
        when = _ms(start)
        day = when.date().isoformat()
        if key == "RESTING_HEART_RATE":
            payload = _json_value(value) or {}
            if isinstance(payload, dict) and payload.get("restBpm"):
                resting[day].append(float(payload["restBpm"]))
        elif key == "HEART_RATE_VARIABILITY":
            payload = _json_value(value) or {}
            if isinstance(payload, dict):
                rmssd = payload.get("heartRateVariabilityRMSSD")
                if rmssd:
                    hrv[day].append(float(rmssd))
        elif key == "STRESS_DATA":
            payload = _json_value(value) or {}
            if isinstance(payload, dict) and payload.get("score") is not None:
                stress[day].append(float(payload["score"]))
        elif key == "BLOOD_OXYGEN_SATURATION":
            payload = _json_value(value) or {}
            if isinstance(payload, dict) and payload.get("avgSaturation"):
                inside = any(bed <= when <= wake for bed, wake in windows)
                if inside:
                    spo2_sleep[day].append(float(payload["avgSaturation"]))
        elif key == "DATA_POINT_DYNAMIC_HEARTRATE":
            number = _json_value(value)
            if isinstance(number, (int, float)) and number > 0:
                hr_all[day].append(float(number))
        elif key == "WEIGHT_BODYFAT_BROAD":
            payload = _json_value(value) or {}
            if isinstance(payload, dict) and payload.get("bodyWeight"):
                weight[day] = float(payload["bodyWeight"])

    days = set(resting) | set(hrv) | set(stress) | set(spo2_sleep) | set(weight) | set(hr_all)
    out: dict[str, dict] = {}
    for day in days:
        row: dict[str, float] = {}
        if resting.get(day):
            row["resting_hr_bpm"] = round(statistics.median(resting[day]))
        if hrv.get(day):
            row["hrv_avg_ms"] = round(statistics.fmean(hrv[day]))
        if stress.get(day):
            row["stress_score"] = round(statistics.fmean(stress[day]))
        if spo2_sleep.get(day):
            row["sleep_avg_spo2_percent"] = round(statistics.fmean(spo2_sleep[day]))
        if hr_all.get(day):
            row["daily_hr_min_bpm"] = round(min(hr_all[day]))
            row["daily_hr_max_bpm"] = round(max(hr_all[day]))
        if day in weight:
            row["weight_kg"] = weight[day]
        if row:
            out[day] = row
    return out


# ---------------- 组装 ----------------

UNITS = {
    "night_sleep_minutes": ("min", "夜间睡眠 = 深 + 浅 + REM 的分钟数（不含清醒段）"),
    "deep_sleep_minutes": ("min", "深睡段分钟数合计"),
    "light_sleep_minutes": ("min", "浅睡段分钟数合计"),
    "rem_sleep_minutes": ("min", "快速眼动段分钟数合计"),
    "deep_sleep_percent": ("%", "深睡分钟 / 夜间睡眠"),
    "light_sleep_percent": ("%", "浅睡分钟 / 夜间睡眠"),
    "rem_sleep_percent": ("%", "REM 分钟 / 夜间睡眠"),
    "nap_minutes": ("min", "零星小睡段分钟数合计"),
    "total_sleep_minutes": ("min", "夜间睡眠 + 零星小睡"),
    "resting_hr_bpm": ("bpm", "当日静息心率采样的中位数"),
    "hrv_avg_ms": ("ms", "当日 HRV(RMSSD) 采样的均值"),
    "sleep_avg_spo2_percent": ("%", "落在睡眠窗口内的血氧采样均值"),
    "stress_score": ("score", "当日压力采样的均值"),
    "weight_kg": ("kg", "当日最后一次体重"),
    "daily_hr_min_bpm": ("bpm", "当日动态心率最小值"),
    "daily_hr_max_bpm": ("bpm", "当日动态心率最大值"),
}

ORDER = [
    "night_sleep_minutes", "deep_sleep_minutes", "light_sleep_minutes", "rem_sleep_minutes",
    "deep_sleep_percent", "light_sleep_percent", "rem_sleep_percent",
    "nap_minutes", "total_sleep_minutes",
    "resting_hr_bpm", "hrv_avg_ms", "sleep_avg_spo2_percent", "stress_score",
    "weight_kg", "daily_hr_min_bpm", "daily_hr_max_bpm",
]


def build_rows(src: Path, start: str | None, end: str | None) -> tuple[list[dict], dict]:
    stats: dict = {}
    nights = build_sleep_nights(src, stats)
    naps = build_naps(src)
    daily = build_daily_metrics(src, nights)

    rows: list[dict] = []
    for day, night in sorted(nights.items()):
        if not (start is None or day >= start) or not (end is None or day <= end):
            continue
        total = night["night"]
        if total <= 0:
            continue
        values = {
            "night_sleep_minutes": night["night"],
            "deep_sleep_minutes": night["deep"],
            "light_sleep_minutes": night["light"],
            "rem_sleep_minutes": night["rem"],
            "deep_sleep_percent": night["deep"] / total * 100,
            "light_sleep_percent": night["light"] / total * 100,
            "rem_sleep_percent": night["rem"] / total * 100,
        }
        if day in naps:
            values["nap_minutes"] = naps[day]
            values["total_sleep_minutes"] = total + naps[day]
        rows.extend(_rows_for(day, values))

    for day, metrics in sorted(daily.items()):
        if not (start is None or day >= start) or not (end is None or day <= end):
            continue
        rows.extend(_rows_for(day, {k: v for k, v in metrics.items() if k in UNITS}))

    rows.sort(key=lambda r: (r["record_date"], ORDER.index(r["metric_name"])))
    audit = {
        "source": str(src),
        "nights": len(nights),
        "naps": len(naps),
        "days_with_daily_metrics": len(daily),
        "dropped_sessions": stats.get("dropped_sessions", 0),
        "params": {
            "session_gap_minutes": SESSION_GAP_MINUTES,
            "night_start_hour": NIGHT_START_HOUR,
            "nap_hours": [min(NAP_HOURS), max(NAP_HOURS)],
        },
    }
    return rows, audit


def _rows_for(day: str, values: dict[str, float]) -> list[dict]:
    out = []
    for metric, value in values.items():
        if metric not in UNITS:
            continue
        unit, how = UNITS[metric]
        number = value if unit in {"kg"} else round(value)
        out.append(
            {
                "metric_name": metric,
                "record_date": day,
                "value": number,
                "unit": unit,
                "period_type": "day",
                "source_app": "huawei_health",
                "source_page": SOURCE_PAGE,
                "extraction_method": EXTRACTION_METHOD,
                "notes": f"{how}（导出包原始采样聚合）",
            }
        )
    return out


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["metric_name", "record_date", "value", "unit", "period_type",
               "source_app", "source_page", "extraction_method", "notes"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


SLEEP_FAMILY = {
    "night_sleep_minutes", "deep_sleep_minutes", "light_sleep_minutes", "rem_sleep_minutes",
    "deep_sleep_percent", "light_sleep_percent", "rem_sleep_percent",
    "nap_minutes", "total_sleep_minutes",
}


def drop_existing(rows: list[dict], db_path: str) -> tuple[list[dict], int]:
    """去掉库里已经有"已确认"记录的同日同指标。

    为什么要这么做：导出包的睡眠是**我按分期段聚合**出来的，和 App 自己算的
    可能差几分钟（设备会做二次分析）。同一格子里放两套口径，趋势就不可比了。
    库里已经有的（截图确认过的），就以设备为准。
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        taken = {
            (row["metric_name"], row["record_date"])
            for row in conn.execute(
                "SELECT metric_name, record_date FROM records"
                " WHERE verification_status IN ('confirmed', 'pending')"
            )
        }
        # 睡眠是"一整晚一个口径"：库里那天已经有设备给的睡眠，就不再混入导出包的分期，
        # 否则会出现"夜间睡眠 408，但深+浅+REM 只有 378"这种自相矛盾的一晚。
        device_nights = {
            row["record_date"]
            for row in conn.execute(
                "SELECT DISTINCT record_date FROM records"
                " WHERE metric_name = 'night_sleep_minutes'"
                "   AND verification_status IN ('confirmed', 'pending')"
            )
        }
    finally:
        conn.close()

    kept = []
    for row in rows:
        key = (row["metric_name"], row["record_date"])
        if key in taken:
            continue
        if row["metric_name"] in SLEEP_FAMILY and row["record_date"] in device_nights:
            continue
        kept.append(row)
    return kept, len(rows) - len(kept)


def validate(rows: list[dict], db_path: str) -> int:
    """和库里已有的 confirmed 值对照——这是唯一能证明"聚合口径没跑偏"的证据。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        existing = {
            (row["metric_name"], row["record_date"]): row["value"]
            for row in conn.execute(
                "SELECT metric_name, record_date, value FROM records"
                " WHERE verification_status = 'confirmed'"
            )
        }
    finally:
        conn.close()

    checked = same = 0
    print(f"{'日期':<12}{'指标':<26}{'导出包':>10}{'库里':>10}{'差':>8}")
    for row in rows:
        key = (row["metric_name"], row["record_date"])
        if key not in existing:
            continue
        checked += 1
        old = existing[key]
        new = float(row["value"])
        diff = new - old
        flag = "" if abs(diff) <= 1 else "  ← 不一致"
        if abs(diff) <= 1:
            same += 1
        print(f"{row['record_date']:<12}{row['metric_name']:<26}{new:>10.0f}{old:>10.0f}{diff:>8.0f}{flag}")
    print(f"\n可对照 {checked} 项，其中 {same} 项一致（容差 ±1）。")
    return 0 if checked and same == checked else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="华为运动健康导出包 → 可导入 CSV（按天聚合）")
    ap.add_argument("--src", required=True, help="解压后的导出目录")
    ap.add_argument("--out", help="输出 CSV（默认只打印不写文件）")
    ap.add_argument("--from", dest="start", help="起始日期 YYYY-MM-DD")
    ap.add_argument("--to", dest="end", help="结束日期 YYYY-MM-DD")
    ap.add_argument("--inventory", action="store_true", help="只列出数据键与时间范围")
    ap.add_argument("--validate", action="store_true", help="和库里已确认的值对照")
    ap.add_argument("--skip-existing", action="store_true",
                    help="丢掉库里已有（confirmed/pending）的同日同指标，避免两套口径混排")
    ap.add_argument("--db", default="D:/HealthData/health.db", help="对照用的数据库")
    args = ap.parse_args(argv)

    src = Path(args.src)
    if args.inventory:
        inventory(src)
        return 0

    rows, audit = build_rows(src, args.start, args.end)
    print(f"睡眠 {audit['nights']} 晚 · 午睡 {audit['naps']} 天 · 日指标 {audit['days_with_daily_metrics']} 天 · 共 {len(rows)} 条")
    if args.skip_existing:
        rows, dropped = drop_existing(rows, args.db)
        print(f"跳过库里已有的 {dropped} 条，剩 {len(rows)} 条")
    if args.start or args.end:
        print(f"范围 {args.start or '—'} ~ {args.end or '—'}")

    if args.out:
        write_csv(rows, Path(args.out))
        print(f"已写 {args.out}")
    if args.validate:
        return validate(rows, args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
