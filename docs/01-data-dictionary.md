# 数据字典 V1.1

> 本文件由 `scripts/gen_data_dictionary.py` 从 `src/health_agent/catalog.py` 生成，**不要手改**。
> 字段名遵循《技术规格书 V1.0》§4 的建议命名。

## 兼容性声明（请先读这一段）

- **本表未与任何设备厂商的官方字段说明逐项核对**，全部指标标记为 `provisional`。
  单位与值域来自常规生理范围与规格文字，**不构成"已与华为数据格式兼容"的声明**。
- `provisional` 的含义：值域可能偏严或偏松。超出范围时程序**只标记、不修正、不丢弃**
  （`out_of_range` 标记 + 需人工复核），因此误判的代价是"多看一眼"，不是数据损坏。
- 升级为 `device_verified` 的前提：用真实设备页面逐字段核对单位、字段是否存在、
  以及值域边界，并在 `catalog.py` 中显式改标记。

## 通用元数据（每条记录都带）

`record_id`、`record_date`、`metric_name`、`value`、`unit`、`period_type`、`period_start`、
`period_end`、`source_app`、`source_page`、`captured_at`、`extraction_method`、
`verification_status`、`notes`。

- `value` 为 `NULL` 表示**缺失或不可读**，绝不表示 0。
- `period_type` 是记录属性，不是聚合参数：`week` / `month` 记录永不参与日趋势。
- `verification_status` 四态：`pending` / `confirmed` / `rejected` / `superseded`。

## 统计周期口径

| period_type | 含义 | 是否进日趋势 | 是否进报告 |
|---|---|---|---|
| `instant` | 单次测量（血压、体温等） | 是（同一天多条取最后一条） | 仅当其指标 `in_report=1` |
| `day` | 当日汇总 | 是 | 是（若 `in_report=1`） |
| `week` | 设备周视图的平均/汇总 | **否**（只做对照，不可拆成日值） | 否 |
| `month` | 设备月视图 | **否** | 否 |

## 范围端点指标（不得推算平均值）

以下指标只表示"区间的某一端"：``hrv_min_ms`、`hrv_max_ms`、`spo2_min_percent`、`spo2_max_percent``。

设备只显示区间（如 HRV 47–82 ms）时，分别入库为下限与上限。
**禁止**用这两个值推算平均值——设备没给平均，平均值就是缺失。
这些指标 `in_report=0`，注册表在导入时会断言它们不能进入报告。

## 指标表

| 字段名 | 中文 | 标准单位 | 类型 | 合理范围 | 允许周期 | 等级 | 进报告 | 验证状态 | 说明 |
|---|---|---|---|---|---|---|---|---|---|
| `night_sleep_minutes` | 夜间主睡眠时长 | `min` | scalar | 0 ~ 780 | day/week/month | P0 | 是 | provisional | 夜间主睡眠。零星小睡单独记于 nap_minutes，二者不合并、不互相替代 |
| `nap_minutes` | 零星小睡时长 | `min` | scalar | 0 ~ 300 | day/week/month | P0 | 是 | provisional | 白天小睡，与夜间主睡眠分开统计 |
| `resting_hr_bpm` | 静息心率 | `bpm` | scalar | 25 ~ 200 | day/week/month | P0 | 是 | provisional | 静息心率与睡眠平均心率为两个独立指标，不得互相填充 |
| `sleep_avg_hr_bpm` | 睡眠平均心率 | `bpm` | scalar | 25 ~ 200 | day/week/month | P0 | 是 | provisional | 睡眠期间平均心率，独立于静息心率 |
| `steps_count` | 步数 | `count` | count | 0 ~ 100000 | day/week/month | P0 | 是 | provisional | 当日总步数 |
| `activity_energy_kcal` | 活动热量 | `kcal` | scalar | 0 ~ 5000 | day/week/month | P0 | 是 | provisional | 设备给出的活动热量。不得用于指导严格限制饮食 |
| `total_sleep_minutes` | 总睡眠时长（设备口径） | `min` | scalar | 0 ~ 1080 | day/week/month | P1 | 否 | provisional | 仅当设备明确给出总睡眠时入库；程序不得自行把夜间与小睡相加后写入本字段 |
| `deep_sleep_minutes` | 深睡时长 | `min` | scalar | 0 ~ 900 | day | P1 | 否 | provisional | 仅当设备给出时长时入库；若设备只给百分比，改用 deep_sleep_percent，禁止换算 |
| `light_sleep_minutes` | 浅睡时长 | `min` | scalar | 0 ~ 900 | day | P1 | 否 | provisional | 仅当设备给出时长时入库，禁止由百分比换算 |
| `rem_sleep_minutes` | REM 时长 | `min` | scalar | 0 ~ 900 | day | P1 | 否 | provisional | 仅当设备给出时长时入库，禁止由百分比换算 |
| `deep_sleep_percent` | 深睡占比 | `%` | percent | 0 ~ 100 | day | P1 | 是 | provisional | 设备只给出比例时使用本字段；禁止乘总时长换算成分钟 |
| `light_sleep_percent` | 浅睡占比 | `%` | percent | 0 ~ 100 | day | P1 | 是 | provisional | 设备只给出比例时使用本字段；禁止换算成分钟 |
| `rem_sleep_percent` | REM 占比 | `%` | percent | 0 ~ 100 | day | P1 | 是 | provisional | 设备只给出比例时使用本字段；禁止换算成分钟 |
| `awake_count` | 夜间醒来次数 | `count` | count | 0 ~ 40 | day | P1 | 是 | provisional | 睡眠期间醒来次数 |
| `deep_sleep_continuity_score` | 深睡连续性评分 | `score` | score | 0 ~ 100 | day | P1 | 是 | provisional | 设备原值存储，不做二次医学解读 |
| `sleep_breathing_quality_score` | 睡眠呼吸质量评分 | `score` | score | 0 ~ 100 | day | P1 | 是 | provisional | 设备原值存储，不做二次医学解读 |
| `sleep_score` | 睡眠评分 | `score` | score | 0 ~ 100 | day | P1 | 是 | provisional | 设备原值存储，不做二次医学解读 |
| `daily_hr_min_bpm` | 日最低心率 | `bpm` | scalar | 25 ~ 230 | day | P1 | 是 | provisional | 当日最低心率 |
| `daily_hr_max_bpm` | 日最高心率 | `bpm` | scalar | 25 ~ 230 | day | P1 | 是 | provisional | 当日最高心率 |
| `hrv_avg_ms` | 平均 HRV | `ms` | scalar | 5 ~ 300 | day | P1 | 是 | provisional | 本设备仅提供睡眠期间的平均 HRV；不得假设存在全天 HRV，也不得用本字段冒充全天值 |
| `hrv_min_ms` | HRV 区间下限 | `ms` | range_bound | 5 ~ 300 | day | P1 | 否 | provisional | 范围端点，仅供阅读；禁止用它和上限推算平均值（设备未给平均就只能缺着） |
| `hrv_max_ms` | HRV 区间上限 | `ms` | range_bound | 5 ~ 300 | day | P1 | 否 | provisional | 范围端点，仅供阅读；禁止用它和下限推算平均值 |
| `spo2_avg_percent` | 平均血氧 | `%` | percent | 50 ~ 100 | day | P1 | 是 | provisional | 日间/全天血氧，与睡眠平均血氧分开 |
| `spo2_min_percent` | 血氧区间下限 | `%` | range_bound | 50 ~ 100 | day | P1 | 否 | provisional | 范围端点，仅设备明确显示最低值时入库；禁止与最高值平均推算均值 |
| `spo2_max_percent` | 血氧区间上限 | `%` | range_bound | 50 ~ 100 | day | P1 | 否 | provisional | 范围端点，仅设备明确显示最高值时入库；禁止与最低值平均推算均值 |
| `sleep_avg_spo2_percent` | 睡眠平均血氧 | `%` | percent | 50 ~ 100 | day | P1 | 是 | provisional | 睡眠期间平均血氧，独立于日间血氧 |
| `respiratory_rate_avg_brpm` | 平均呼吸率 | `brpm` | scalar | 4 ~ 40 | day | P1 | 是 | provisional | 睡眠期间平均呼吸率 |
| `stress_score` | 压力评分 | `score` | score | 0 ~ 100 | day | P1 | 是 | provisional | 设备原值存储，不生成任何医学结论 |
| `blood_pressure_systolic_mmhg` | 收缩压 | `mmHg` | scalar | 50 ~ 260 | instant/day | P2 | 否 | provisional | 仅在来源与测量方式明确时录入 |
| `blood_pressure_diastolic_mmhg` | 舒张压 | `mmHg` | scalar | 30 ~ 160 | instant/day | P2 | 否 | provisional | 仅在来源与测量方式明确时录入 |
| `blood_glucose_mmol_l` | 血糖 | `mmol/L` | scalar | 1 ~ 40 | instant/day | P2 | 否 | provisional | 仅在来源与测量方式明确时录入 |
| `body_temperature_c` | 体温 | `°C` | scalar | 30 ~ 45 | instant/day | P2 | 否 | provisional | 仅在来源与测量方式明确时录入 |
| `weight_kg` | 体重 | `kg` | scalar | 20 ~ 300 | instant/day | P2 | 否 | provisional | 可选记录，不作为体重控制目标 |
| `arrhythmia_measurement_count` | 心律筛查测量次数 | `count` | count | 0 ~ 1000 | day | P2 | 否 | provisional | 仅保存设备页面显示的内容，不生成诊断 |
| `arrhythmia_normal_count` | 心律筛查正常次数 | `count` | count | 0 ~ 1000 | day | P2 | 否 | provisional | 仅保存设备页面显示的内容，不生成诊断 |
| `suspected_pac_count` | 疑似房早次数 | `count` | count | 0 ~ 1000 | day | P2 | 否 | provisional | 设备页面原始结论，必须注明来源，不得表述为医学诊断 |
| `suspected_af_count` | 疑似房颤次数 | `count` | count | 0 ~ 1000 | day | P2 | 否 | provisional | 设备页面原始结论，必须注明来源，不得表述为医学诊断 |

## 本次统计

- 指标总数：37
- 进报告：20
- 范围端点（不进报告）：4
- 已与设备核对（`device_verified`）：0
- 暂定定义（`provisional`）：37

报告实际使用的指标：`night_sleep_minutes`, `nap_minutes`, `resting_hr_bpm`, `sleep_avg_hr_bpm`, `steps_count`, `activity_energy_kcal`, `deep_sleep_percent`, `light_sleep_percent`, `rem_sleep_percent`, `awake_count`, `deep_sleep_continuity_score`, `sleep_breathing_quality_score`, `sleep_score`, `daily_hr_min_bpm`, `daily_hr_max_bpm`, `hrv_avg_ms`, `spo2_avg_percent`, `sleep_avg_spo2_percent`, `respiratory_rate_avg_brpm`, `stress_score`
