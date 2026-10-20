"""指标注册表 —— canonical schema 的唯一事实源。

命名与语义严格遵循《Personal Health Agent 技术规格书 V1.0》§4 的字段建议，
不采用任何外部导出格式的字段名。数据库的 metrics_catalog 表由本文件播种，
校验、统计、报告一律以本文件为准。

新增指标的唯一正确做法：在本文件新增一条 MetricSpec，再让迁移脚本同步
metrics_catalog 表；不要在业务代码里硬编码指标名。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PeriodType = Literal["instant", "day", "week", "month"]
Tier = Literal["P0", "P1", "P2"]
Kind = Literal["scalar", "percent", "score", "count", "range_bound"]
Verification = Literal["provisional", "device_verified"]

VALID_PERIOD_TYPES: frozenset[str] = frozenset({"instant", "day", "week", "month"})
VALID_TIERS: frozenset[str] = frozenset({"P0", "P1", "P2"})
VALID_KINDS: frozenset[str] = frozenset({"scalar", "percent", "score", "count", "range_bound"})
VALID_VERIFICATION: frozenset[str] = frozenset({"provisional", "device_verified"})


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """单个指标的规格。

    min_valid / max_valid 是"合理取值范围"，超出时不丢弃数据，只标记异常
    并要求人工复核（规格 §5：标记异常，不自动修正）。

    verification 记录这个定义有多可信：
    - `provisional`：按常规生理范围与规格文字拟定，**未与任何设备官方字段逐项核对**；
    - `device_verified`：已用真实设备页面逐字段核对过单位与值域。
    当前全部指标都是 `provisional`——本项目尚未取得可核对的设备字段说明。
    """

    name: str
    display: str
    unit: str
    kind: str
    period_types: tuple[str, ...]
    tier: str
    in_report: bool
    min_valid: float | None = None
    max_valid: float | None = None
    description: str = ""
    review_required: bool = False
    verification: str = "provisional"


def _m(
    name: str,
    display: str,
    unit: str,
    kind: str,
    period_types: tuple[str, ...],
    tier: str,
    in_report: bool,
    min_valid: float | None = None,
    max_valid: float | None = None,
    description: str = "",
    review_required: bool = False,
    verification: str = "provisional",
) -> MetricSpec:
    return MetricSpec(
        name=name,
        display=display,
        unit=unit,
        kind=kind,
        period_types=period_types,
        tier=tier,
        in_report=in_report,
        min_valid=min_valid,
        max_valid=max_valid,
        description=description,
        review_required=review_required,
        verification=verification,
    )


_DAY: tuple[str, ...] = ("day",)
# 日/周/月视图都可出现的指标。周与月记录只做对照，绝不参与日趋势。
_AGG: tuple[str, ...] = ("day", "week", "month")
_MEASURED: tuple[str, ...] = ("instant", "day")

#: 全部指标。顺序即文档与界面展示顺序。
METRICS: tuple[MetricSpec, ...] = (
    # ---------- P0：第一版必须可看趋势 ----------
    _m("night_sleep_minutes", "夜间主睡眠时长", "min", "scalar", _AGG, "P0", True, 0, 780,
       "夜间主睡眠。零星小睡单独记于 nap_minutes，二者不合并、不互相替代"),
    _m("nap_minutes", "零星小睡时长", "min", "scalar", _AGG, "P0", True, 0, 300,
       "白天小睡，与夜间主睡眠分开统计"),
    _m("resting_hr_bpm", "静息心率", "bpm", "scalar", _AGG, "P0", True, 25, 200,
       "静息心率与睡眠平均心率为两个独立指标，不得互相填充"),
    _m("sleep_avg_hr_bpm", "睡眠平均心率", "bpm", "scalar", _AGG, "P0", True, 25, 200,
       "睡眠期间平均心率，独立于静息心率"),
    _m("steps_count", "步数", "count", "count", _AGG, "P0", True, 0, 100000,
       "当日总步数"),
    _m("activity_energy_kcal", "活动热量", "kcal", "scalar", _AGG, "P0", True, 0, 5000,
       "设备给出的活动热量。不得用于指导严格限制饮食"),
    # ---------- P1：基础稳定后 ----------
    _m("total_sleep_minutes", "总睡眠时长（设备口径）", "min", "scalar", _AGG, "P1", False, 0, 1080,
       "仅当设备明确给出总睡眠时入库；程序不得自行把夜间与小睡相加后写入本字段"),
    _m("deep_sleep_minutes", "深睡时长", "min", "scalar", _DAY, "P1", False, 0, 900,
       "仅当设备给出时长时入库；若设备只给百分比，改用 deep_sleep_percent，禁止换算"),
    _m("light_sleep_minutes", "浅睡时长", "min", "scalar", _DAY, "P1", False, 0, 900,
       "仅当设备给出时长时入库，禁止由百分比换算"),
    _m("rem_sleep_minutes", "REM 时长", "min", "scalar", _DAY, "P1", False, 0, 900,
       "仅当设备给出时长时入库，禁止由百分比换算"),
    _m("deep_sleep_percent", "深睡占比", "%", "percent", _DAY, "P1", True, 0, 100,
       "设备只给出比例时使用本字段；禁止乘总时长换算成分钟"),
    _m("light_sleep_percent", "浅睡占比", "%", "percent", _DAY, "P1", True, 0, 100,
       "设备只给出比例时使用本字段；禁止换算成分钟"),
    _m("rem_sleep_percent", "REM 占比", "%", "percent", _DAY, "P1", True, 0, 100,
       "设备只给出比例时使用本字段；禁止换算成分钟"),
    _m("awake_count", "夜间醒来次数", "count", "count", _DAY, "P1", True, 0, 40,
       "睡眠期间醒来次数"),
    _m("deep_sleep_continuity_score", "深睡连续性评分", "score", "score", _DAY, "P1", True, 0, 100,
       "设备原值存储，不做二次医学解读", review_required=False),
    _m("sleep_breathing_quality_score", "睡眠呼吸质量评分", "score", "score", _DAY, "P1", True, 0, 100,
       "设备原值存储，不做二次医学解读"),
    _m("sleep_score", "睡眠评分", "score", "score", _DAY, "P1", True, 0, 100,
       "设备原值存储，不做二次医学解读"),
    _m("daily_hr_min_bpm", "日最低心率", "bpm", "scalar", _DAY, "P1", True, 25, 230,
       "当日最低心率"),
    _m("daily_hr_max_bpm", "日最高心率", "bpm", "scalar", _DAY, "P1", True, 25, 230,
       "当日最高心率"),
    _m("hrv_avg_ms", "平均 HRV", "ms", "scalar", _DAY, "P1", True, 5, 300,
       "本设备仅提供睡眠期间的平均 HRV；不得假设存在全天 HRV，也不得用本字段冒充全天值"),
    # HRV 范围端点：设备只显示区间（如 47–82 ms）时分别入库。
    # 这两个字段只表示"当天区间的一端"，**不得**用它们推算平均值。
    _m("hrv_min_ms", "HRV 区间下限", "ms", "range_bound", _DAY, "P1", False, 5, 300,
       "范围端点，仅供阅读；禁止用它和上限推算平均值（设备未给平均就只能缺着）"),
    _m("hrv_max_ms", "HRV 区间上限", "ms", "range_bound", _DAY, "P1", False, 5, 300,
       "范围端点，仅供阅读；禁止用它和下限推算平均值"),
    _m("spo2_avg_percent", "平均血氧", "%", "percent", _DAY, "P1", True, 50, 100,
       "日间/全天血氧，与睡眠平均血氧分开"),
    _m("spo2_min_percent", "血氧区间下限", "%", "range_bound", _DAY, "P1", False, 50, 100,
       "范围端点，仅设备明确显示最低值时入库；禁止与最高值平均推算均值"),
    _m("spo2_max_percent", "血氧区间上限", "%", "range_bound", _DAY, "P1", False, 50, 100,
       "范围端点，仅设备明确显示最高值时入库；禁止与最低值平均推算均值"),
    _m("sleep_avg_spo2_percent", "睡眠平均血氧", "%", "percent", _DAY, "P1", True, 50, 100,
       "睡眠期间平均血氧，独立于日间血氧"),
    _m("respiratory_rate_avg_brpm", "平均呼吸率", "brpm", "scalar", _DAY, "P1", True, 4, 40,
       "睡眠期间平均呼吸率"),
    _m("stress_score", "压力评分", "score", "score", _DAY, "P1", True, 0, 100,
       "设备原值存储，不生成任何医学结论"),
    # ---------- P2：后续扩展 ----------
    # 血压（收缩压/舒张压）已于 2026-10-20 移除：用户设备测量不准，不采集该项。
    _m("training_index", "训练指数", "score", "score", _MEASURED, "P2", False, 0, 10,
       "华为训练指数原值（如 3.6 很好）；设备原值存储，不做医学解读", review_required=True),
    _m("vo2max_ml_kg_min", "最大摄氧量", "mL/kg/min", "scalar", _MEASURED, "P2", False, 10, 90,
       "设备估算值，非实验室测量；只作趋势参考", review_required=True),
    _m("running_index", "跑力指数", "score", "score", _MEASURED, "P2", False, 0, 100,
       "华为跑力指数原值（如 21.9 D 级）；等级只记在备注，不参与计算", review_required=True),
    _m("heart_rate_spot_bpm", "单次心率测量", "bpm", "scalar", _MEASURED, "P2", False, 30, 220,
       "设备页面上的单次心率读数；不是静息心率，也不是日均值", review_required=True),
    _m("spo2_spot_percent", "单次血氧测量", "%", "percent", _MEASURED, "P2", False, 50, 100,
       "设备页面上的单次血氧读数；不是日均值", review_required=True),
    _m("blood_glucose_mmol_l", "血糖", "mmol/L", "scalar", _MEASURED, "P2", False, 1.0, 40.0,
       "仅在来源与测量方式明确时录入", review_required=True),
    _m("body_temperature_c", "体温", "°C", "scalar", _MEASURED, "P2", False, 30.0, 45.0,
       "仅在来源与测量方式明确时录入", review_required=True),
    _m("weight_kg", "体重", "kg", "scalar", _MEASURED, "P2", False, 20, 300,
       "可选记录，不作为体重控制目标"),
    _m("arrhythmia_measurement_count", "心律筛查测量次数", "count", "count", _DAY, "P2", False, 0, 1000,
       "仅保存设备页面显示的内容，不生成诊断"),
    _m("arrhythmia_normal_count", "心律筛查正常次数", "count", "count", _DAY, "P2", False, 0, 1000,
       "仅保存设备页面显示的内容，不生成诊断"),
    _m("suspected_pac_count", "疑似房早次数", "count", "count", _DAY, "P2", False, 0, 1000,
       "设备页面原始结论，必须注明来源，不得表述为医学诊断"),
    _m("suspected_af_count", "疑似房颤次数", "count", "count", _DAY, "P2", False, 0, 1000,
       "设备页面原始结论，必须注明来源，不得表述为医学诊断"),
)

METRIC_INDEX: dict[str, MetricSpec] = {m.name: m for m in METRICS}

#: P0 指标名，报告与趋势的默认范围。
P0_METRICS: tuple[str, ...] = tuple(m.name for m in METRICS if m.tier == "P0")

#: 进入日报/周报的指标名。
REPORT_METRICS: tuple[str, ...] = tuple(m.name for m in METRICS if m.in_report)

#: 范围端点类指标（HRV/血氧的 min 与 max）。只表示区间一端，不参与"平均值"。
#: 设备没给平均值时，平均值就是缺失——不从上下降推算。
RANGE_BOUND_METRICS: tuple[str, ...] = tuple(
    m.name for m in METRICS if m.kind == "range_bound"
)


def assert_catalog_invariants(metrics: "tuple[MetricSpec, ...] | list[MetricSpec]" = METRICS) -> None:
    """注册表级不变式：违反就是设计错误，导入即失败，不留到运行时。

    接受参数是为了让测试能构造"坏注册表"验证这条守卫本身有效。
    """
    offenders = [m.name for m in metrics if m.kind == "range_bound" and m.in_report]
    if offenders:
        raise ValueError(f"范围端点指标不得进入报告: {offenders}")
    unknown_verification = [
        m.name for m in metrics if m.verification not in VALID_VERIFICATION
    ]
    if unknown_verification:
        raise ValueError(f"指标的 verification 取值非法: {unknown_verification}")


assert_catalog_invariants()


class UnknownMetricError(KeyError):
    """查询了未注册的指标名。"""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name

    def __str__(self) -> str:
        return f"未注册的指标名: {self.name!r}"


def get_metric(name: str) -> MetricSpec:
    """按指标名取规格；未注册直接抛错，不做模糊匹配或猜测。"""
    try:
        return METRIC_INDEX[name]
    except KeyError:
        raise UnknownMetricError(name) from None


def is_known_metric(name: str) -> bool:
    return name in METRIC_INDEX
