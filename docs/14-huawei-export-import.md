# 华为运动健康「导出包」→ 项目数据

> 2026-10-26 新增的数据入口，与"截图转录"并行。
> 一句话：**截图是设备算好的结论，导出包是原始采样**——前者准、后者全。

---

## 1. 包是怎么来的

App：我 → 隐私 → 导出数据 → 生成 `HUAWEI_HEALTH_<时间戳>.zip`。

- 173 个文件、约 43 MB，7 个目录
- **全部 AES-256 加密**（zip `compress_type=99`）。Python 标准库 `zipfile` 读不了这种，
  即使给对密码也会抛 `NotImplementedError`
- 解密只能用 7-Zip / WinRAR 之类：

```
"C:\Program Files\7-Zip\7z.exe" x -o"D:\hw_export" "D:\HUAWEI_HEALTH_20261019170027.zip"
```

> 导出密码由 App 在导出时设定，**项目不保存**。忘了就重新导一次。

---

## 2. 目录里有什么（实测这份包）

| 目录 | 内容 | 有没有用 |
|---|---|---|
| `Health detail data & description/` | 47 个 json，每分钟睡眠分期、每次心率、压力、血氧、体重…… | **主力**，本项目只用这个 |
| `SportsHealth data & desciption/` | 95 个 `.xls`（步数、运动记录、设备信息…） | 暂未解析（老 .xls 格式） |
| `Sample sequence data & description/` | 连续采样序列（含一个 27 MB 的） | 粒度太细，目录里没有对应指标 |
| `Motion path / Sport per minute / *run plan / route` | 运动轨迹与训练计划 | 本项目不涉及 |

字段说明表 `Data Field Description.xlsx` 在第一个目录里，可对照字段含义。

---

## 3. 指标映射与聚合口径

导出包给的是**采样**，所以要自己聚合。每个指标的算法都写进了 CSV 的 `notes` 列。

| 指标 | 源 key | 怎么算 |
|---|---|---|
| `night_sleep_minutes` | `PROFESSIONAL_SLEEP_DEEP/SHALLOW/DREAM` | 深+浅+REM 的分钟数（不含清醒段） |
| `deep/light/rem_sleep_minutes` | 同上 | 各阶段分钟数 |
| `deep/light/rem_sleep_percent` | 同上 | 各阶段 / 夜间睡眠 × 100（取整） |
| `nap_minutes` | `PROFESSIONAL_SLEEP_NOON` | 09:00–17:59 的午睡段合计 |
| `total_sleep_minutes` | 上面两者 | 夜间 + 午睡（只在有午睡的日子产出） |
| `resting_hr_bpm` | `RESTING_HEART_RATE.restBpm` | 当日采样中位数 |
| `hrv_avg_ms` | `HEART_RATE_VARIABILITY.heartRateVariabilityRMSSD` | 当日采样均值 |
| `sleep_avg_spo2_percent` | `BLOOD_OXYGEN_SATURATION.avgSaturation` | 只取落在睡眠窗口内的采样，求均值 |
| `stress_score` | `STRESS_DATA.score` | 当日采样均值 |
| `weight_kg` | `WEIGHT_BODYFAT_BROAD.bodyWeight` | 当日最后一次 |
| `daily_hr_min/max_bpm` | `DATA_POINT_DYNAMIC_HEARTRATE` | 当日最小/最大 |

**不产出**（设备算法给的，自己算就是编）：

- `sleep_score`（睡眠评分）——导出包里没有
- `deep_sleep_continuity_score`、`sleep_breathing_quality_score`——同上
- `awake_count`（清醒次数）——设备对"算一次觉醒"有阈值（通常 ≥5 分钟），
  从 1 分钟分期段反推必然对不上，宁可留空

---

## 4. 两条硬规则（都在脚本里）

**① 分期段会重复，必须按分钟落格去重。**
同一晚可能同时存在"原始分析"和"云端二次分析"两份段，直接加时长会把一夜算成 23 小时
（实测 2026-06-03 就是）。脚本把每段摊成"每分钟一个格子"，重复自然折叠。

**② 同一晚只能有一个口径。**
库里某天已经有设备给的 `night_sleep_minutes`（截图确认过的），这一天就**整晚不用**导出包的分期——
否则会出现"夜间睡眠 408，但深+浅+REM 只有 378"这种自相矛盾的一晚。
`--skip-existing` 负责这件事，按"睡眠家族"整体跳过。

---

## 5. 校验：和截图对过

用库里由截图确认的 7 晚（2026-10-13 ~ 10-19）对照：

| 日期 | 导出包 vs 库里 |
|---|---|
| 第 1 晚 | 完全一致 |
| 第 2 晚 | 差 −30 分钟 |
| 第 3 晚 | 完全一致 |
| 第 4 晚 | 完全一致 |
| 第 5 晚 | 完全一致 |
| 第 6 晚 | 完全一致 |
| 第 7 晚 | 差 +4 分钟 |

> 具体分钟数属于个人健康数据，不写进公开仓库；复现时用
> `--validate` 会自己打印对照表（只在本机显示）。

5/7 完全一致。两处差异的原因：**设备会把"半夜起来很久又躺回去"合并成一晚**，
脚本按 60 分钟间隔切会话，会把后半夜那段切开；10-19 的 4 分钟差异同源。
这类差异只影响个别夜晚，所以采用"同一晚只留一个口径"的规则，不改数字去凑。

---

## 6. 命令

```bash
# 看包里有哪些数据、时间范围
python scripts/parse_huawei_export.py --src "D:/HUAWEI_HEALTH_20261019170027" --inventory

# 和库里已确认的值对照（只读）
python scripts/parse_huawei_export.py --src ... --validate --from 2026-10-13

# 生成可导入 CSV（跳过库中已有的同格与同晚睡眠）
python scripts/parse_huawei_export.py --src ... --skip-existing \
  --out data/inbox/2026-10-26-huawei-export.csv

# 预览 → 确认后写库（全部 pending）
.venv/Scripts/python.exe -m health_agent.cli import data/inbox/2026-10-26-huawei-export.csv
.venv/Scripts/python.exe -m health_agent.cli import data/inbox/2026-10-26-huawei-export.csv --commit
```

入库后仍要在「记录」页按批次确认（或首页"一键确认最近一批"）才进趋势。

---

## 7. 已知限制

1. `SportsHealth` 里的 `.xls` 没解析——**步数、活动热量**仍然只能靠截图；
2. 导出包的睡眠是**本地、当前分析结果**的切片，App 可能在此之后重新分析过；
3. 心率/HRV/血氧/压力是"当日采样聚合"，与 App 卡片上的算法值可能差一点（App 会做加权/去噪）；
4. 没有睡眠评分、清醒次数、连续性——这几项只有截图能给。
