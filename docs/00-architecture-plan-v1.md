# Personal Health Agent — 架构与实施方案 V1.0

> 依据：`Personal_Health_Agent_Technical_Spec_V1.0.docx`（下称"规格"）+ `Personal_Health_Agent_Development_Tasks_V1.0.docx`（下称"任务书"）
> 状态：**规划稿，未写任何代码**。确认后按本文档的 Wave 逐段实现。
> 日期：2026-10-19

---

## 状态

- 当前状态：`已完成`（V1.4；W1–W7 全部交付，P0 范围）
- 说明：任务 0–7 全部交付；P1/P2 指标与功能未启用，见 §14 已知限制。

## 不卡死计数

| 项 | 计数 | 上限 |
|---|---|---|
| 方案返工轮次 | 0 | 3（安全上限 6） |
| 同签名失败 | 0 | 2 |
| 未闭环未知项 | 3（见 §10 待确认清单 U1/U2/U3） | 3 |

---

## 1. 项目启动信息

- **项目名称**：Personal Health Agent（个人健康管理助手）
- **初始需求**：按规格与任务书构建第一版（P0），先管理华为手环 11 / 华为运动健康截图可读的健康指标，完成导入→校验→落库→趋势→简报的闭环。
- **技术栈**：Python 3.11+ / SQLite / pandas / **FastAPI + Jinja2 服务端渲染**（用户已选定）/ pytest / 可选 DeepSeek API
- **是否存在终极功能**：是
- **终极功能定义**：*用户导入一份含 pending 状态的截图提取 JSON，经预览确认后，系统能给出可追溯到具体记录 ID 的 7 日趋势与每日简报；断网或 API 失败时本地记录、统计与简报仍可正常产出。*
- **技术约束**：
  - 数据默认全本地；云端只传摘要（不含姓名/账号/设备标识/原始截图）
  - 缺失值一律 `NULL`，禁止以 0 填充，禁止 AI 推断不可辨认数值
  - 未 `confirmed` 的记录不得进入任何趋势/基线/提醒计算
  - 不输出疾病诊断；心律相关仅转述设备页面文字
- **默认循环轮次**：3　**安全最大轮次**：6　**每轮最大改动点数**：3
- **角色配置**：单 Agent 承担主控/实现/验证

---

## 2. 规格差距分析（必须先裁决的 4 个真冲突 + 6 个隐含决策）

这一节是本方案的核心价值：规格里存在互相矛盾的表述，直接照做会在 Wave 2 崩掉。

### C1【阻塞】宽表 vs 长表：§4 的两种数据模型互相冲突

规格 §4 既要求「每条记录具有通用元数据 `metric_name / value / unit / period_type ...`」（→ 这是长表/EAV），又列出一串扁平字段 `night_sleep_minutes / resting_hr_bpm / steps_count ...`（→ 这是宽表）。两者不可能同时是存储主结构。

**裁决：长表为唯一事实来源（single source of truth），宽表只作为查询层的派生视图。**

| 候选 | trade-off | 结论 |
|---|---|---|
| A. 宽表 `daily_metrics(date, night_sleep_minutes, resting_hr_bpm, ...)` | pandas 直接可用、查询快；但**新增指标必须 DDL 迁移**，且来源/确认状态/提取方式被压缩成"每行一个值"，无法满足规格 §4「保留来源、提取时间、确认状态」与 §5「逐条更正/撤销」 | 否 |
| B. 长表 `records(metric_name, value, unit, source_app, verification_status, batch_id, ...)` | 新增指标零迁移；去重键、批次撤销、逐字段来源全部天然成立；趋势分析需 pivot（pandas 一行 `pivot_table` 解决） | **采用** |
| C. 双写宽表+长表 | 一致性维护成本与半迁移风险，违背"第一版不引入不必要复杂度" | 否 |

实现方式：`metric_name` **直接使用规格 §4 建议的字段名**（`night_sleep_minutes`、`resting_hr_bpm`…），因此"规格字段清单"成为指标注册表的字面内容，两种表述被统一，无需二选一。

### C2【阻塞】"重复导入不得覆盖"与"冲突记录并存"不能只靠唯一约束

规格 §5 同时要求：① 同来源+同日期+同指标+同周期重复导入要提示去重；② 冲突记录并存，不得擅自决定真值；③ 不得无提示覆盖已确认记录。

如果只加 `UNIQUE(source_app, record_date, metric_name, period_type)`，第 ② 条无法实现（第二条根本插不进去）；如果不加约束，第 ① 条无法保证。

**裁决：partial unique index + 冲突表分离。**

- 唯一索引只约束 `verification_status = 'confirmed'` 的行（SQLite 支持带 `WHERE` 的部分索引）→ 保证"同一逻辑键最多一条已确认"。
- 新导入的同逻辑键异值记录以 `pending` 正常入库，同时写入 `conflicts` 表等待裁决 → 满足"并存"。
- 裁决动作 `keep_existing | take_incoming | keep_both`，全部写 `audit_log`。

### C3【阻塞】"周视图不能拆成每日值"要求周记录与日记录**物理隔离**

规格 §4 末段要求区分日/周/月视图，且周视图范围不可拆为精确日值、不可见平均值保持 `null`。

**裁决**：`period_type ∈ {instant, day, week, month}` 是**记录属性**，不是聚合参数。趋势计算只取 `period_type IN ('day','instant')`；`week/month` 记录永不参与日趋势，只用于「对照校验」——当同一周存在 ≥5 天日记录时，可比对周记录与日聚合的偏差，偏差超阈值只打 `aggregation_mismatch` 标记，**不自动修正任一方**。

### C4【阻塞】睡眠记录归属哪天，规格没写，而这是最高频的错账来源

华为运动健康"昨晚睡眠"卡片展示的日期是**醒来日**，跨夜数据若归到就寝日，所有趋势会整体错位一天。

**裁决**：`record_date` 统一为**醒来日**（即设备页面上的展示日期）。当导入数据无法判断归属（如只给了 `2026-10-18 23:40 ~ 07:10` 的时间段而无页面日期），记录强制 `pending` + `quality_flags` 追加 `ambiguous_date`，不自动确认。此规则写入 `catalog` 说明与 UI 提示文案。

### 隐含决策（规格未写，本方案给默认值，可在配置中改）

| # | 决策点 | 默认值 | 理由 |
|---|---|---|---|
| D1 | 单位白名单 | `min / h(仅转换入) / bpm / brpm / count / kcal / kJ(转换入) / % / ms / score` | 规格 §5 只要求"白名单"未给值，此处定死 |
| D2 | 换算策略 | 允许 `h→min(x60)`、`kJ→kcal(x0.239)`；**禁止** 任何 `%→min` 比例换算 | 规格 §4 明确"比例不得未经确认换算成时长" |
| D3 | 个人基线算法 | 最近 28 天、`confirmed` 日记录 ≥14 天；中心值=中位数，离散=**MAD**；偏离判定 `\|x−median\| > 2.5×1.4826×MAD` | 中位数+MAD 对生理指标离群值稳健，均值+标准差会被异常日带偏 |
| D4 | "数据不足"阈值 | 趋势展示 ≥3 天；基线 ≥14 天；周报覆盖率 ≥4/7 天才出结论 | 规格只要求"样本不足明确提示"，需给出可判定数值，否则无法验收 |
| D5 | 提醒冷却期 | 同类提醒 72 小时；同一 `dedup_key` 永久只发一次 | 规格 §6 要求去重+冷却+可关闭 |
| D6 | 平板访问鉴权 | 默认 `bind 127.0.0.1`；开启局域网必须同时开启单用户密码 + HttpOnly Cookie | **规格 §8 要求隐私，但未提局域网鉴权——这是原文的安全缺口** |

---

## 3. 模块架构

### 3.1 依赖方向（单向，无环）

```
                    ┌──────────────────────────┐
                    │        ui (FastAPI)      │
                    │  routes / templates      │
                    └────┬──────────┬──────────┘
                         │          │
                  ┌──────▼───┐  ┌───▼──────────┐
                  │ reports  │  │  ingestion   │
                  └──┬────┬──┘  └───┬──────────┘
                     │    │         │
             ┌───────▼┐ ┌─▼─────────▼──┐
             │ai_client│ │  validation  │
             └───┬────┘ └──────┬───────┘
                 │             │
              ┌──▼─────────────▼──┐     ┌──────────┐
              │     analytics     │     │  rules   │
              └────────┬──────────┘     └────┬─────┘
                       │                     │
                    ┌──▼─────────────────────▼──┐
                    │         storage           │  ← 唯一能碰 SQLite 的层
                    │  db / migrations / repo   │
                    └───────────────────────────┘
                                  ↑
                            catalog（纯静态注册表，无依赖）
```

**边界铁律**：

- `storage` 不 import 任何上层模块；所有 SQL 只出现在 `storage/`。
- `analytics` / `rules` 只读 storage，不写库（写提醒走 storage 的接口）。
- `ai_client` **只能被 `reports` 调用**，且输入必须是 `analytics` 产出的摘要 dict，禁止直接读库、禁止接触原始截图路径。
- `validation` 不写库，只返回 `ValidationResult`（pure function，可单测）。
- `ui` 不得绕过 `reports/analytics` 直接算数。

### 3.2 目录结构

```
personal-health-agent/
├─ pyproject.toml
├─ requirements.txt
├─ README.md
├─ .env.example
├─ .gitignore                    # data/ *.db .env 全部忽略
├─ docs/
│  ├─ 00-architecture-plan-v1.md # 本文件
│  ├─ 01-data-dictionary.md      # 指标/单位/值域（Wave 1 产出）
│  ├─ 02-runbook.md              # 安装/启动/备份/导出/排障（Wave 7）
│  └─ 03-tablet-access.md        # 平板验证结果：已验证 / 未验证（Wave 7）
├─ data/                         # SQLite + 原始截图副本，git 忽略
├─ src/health_agent/
│  ├─ __init__.py
│  ├─ config.py                  # 环境变量读取，密钥只从这里进
│  ├─ catalog.py                 # 指标注册表（静态 dict，与 DB 同步）
│  ├─ cli.py                     # import / report / serve / backup / export
│  ├─ ingestion/
│  │  ├─ loader.py               # JSON/CSV 读入 → 统一 RawRow
│  │  ├─ adapter_doubao.py       # 豆包输出格式适配层（见 R1）
│  │  └─ batch.py                # 批次创建 / 撤销
│  ├─ validation/
│  │  ├─ units.py                # 白名单 + 换算
│  │  ├─ rules.py                # 字段/日期/周期/值域校验
│  │  └─ result.py               # ValidationResult（reject / warn+flag / ok）
│  ├─ storage/
│  │  ├─ db.py                   # 连接、PRAGMA、事务
│  │  ├─ migrations/001_init.sql
│  │  └─ repository.py           # 全部 SQL
│  ├─ analytics/
│  │  ├─ aggregate.py            # 日/周/月聚合、缺失率
│  │  ├─ baseline.py             # 中位数+MAD 基线
│  │  └─ quality.py              # 数据质量评分/标记
│  ├─ rules/
│  │  ├─ engine.py               # 触发/去重/冷却
│  │  └─ builtin.py              # 4 条内置规则
│  ├─ ai_client/
│  │  ├─ client.py               # 超时/重试/降级
│  │  ├─ prompts.py              # 模板 + 版本号
│  │  ├─ cache.py                # 摘要哈希缓存
│  │  └─ fallback.py             # 无 AI 时的模板化摘要
│  ├─ reports/
│  │  ├─ daily.py
│  │  ├─ weekly.py
│  │  └─ render.py               # 数字 → 记录 ID 追溯表
│  └─ ui/
│     ├─ app.py                  # FastAPI 实例 + 鉴权中间件
│     ├─ routes/{records,imports,reports,settings,export}.py
│     ├─ templates/*.jinja2
│     └─ static/app.css
└─ tests/
   ├─ conftest.py
   ├─ fixtures/                  # 虚构样例数据（禁止真实读数）
   ├─ test_validation.py
   ├─ test_dedup_conflict.py
   ├─ test_storage.py
   ├─ test_analytics.py
   ├─ test_rules.py
   ├─ test_ai_fallback.py
   └─ test_e2e_import.py
```

---

## 4. 数据模型（SQLite DDL 设计稿）

### 4.1 表清单

| 表 | 职责 | 关键约束 |
|---|---|---|
| `metrics_catalog` | 指标注册表：单位、值域、允许周期、P0/P1 | PK `metric_name` |
| `records` | 健康记录（长表，唯一事实来源） | 部分唯一索引（见下） |
| `import_batches` | 导入批次 | 可 `revoked`，级联撤销 |
| `conflicts` | 冲突待裁决 | 1 条 incoming ↔ 1 条 existing |
| `baselines` | 个人基线快照 | 记录算法版本与样本数 |
| `alerts` | 提醒实例 | `dedup_key` 唯一 |
| `alerts.feedback` | 已读/不相关反馈（0.1.1 起：反馈是 alerts 的列，没有独立表） | |
| `settings` | 生活背景等可选配置（全部可空） | |
| `ai_call_log` | AI 调用与费用 | `cost_source` 三态 |
| `report_cache` | 报告缓存 | `cache_key` 唯一 |
| `audit_log` | 全量变更审计 | 追加写 |
| `schema_migrations` | 迁移版本 | |

### 4.2 `records` 核心设计

```sql
CREATE TABLE records (
  record_id          TEXT PRIMARY KEY,          -- uuid4
  logical_key        TEXT NOT NULL,             -- sha256(source_app|record_date|metric_name|period_type|period_start|period_end)
  dedup_key          TEXT NOT NULL,             -- sha256(logical_key|unit|value_norm)
  record_date        TEXT NOT NULL,             -- ISO 'YYYY-MM-DD'，睡眠=醒来日
  metric_name        TEXT NOT NULL REFERENCES metrics_catalog(metric_name),
  value              REAL,                      -- NULL 合法，表示"页面未显示该值"
  unit               TEXT NOT NULL,
  raw_value          TEXT,                      -- 原始文本，如 '1:23'、'58%'
  raw_unit           TEXT,
  period_type        TEXT NOT NULL CHECK (period_type IN ('instant','day','week','month')),
  period_start       TEXT,                      -- week/month 必填
  period_end         TEXT,
  source_app         TEXT NOT NULL DEFAULT 'huawei_health',
  source_page        TEXT,                      -- 如 '睡眠-日视图'
  extraction_method  TEXT,                      -- ocr_manual | manual | device_export
  verification_status TEXT NOT NULL DEFAULT 'pending'
                     CHECK (verification_status IN ('pending','confirmed','rejected','superseded')),
  batch_id           TEXT REFERENCES import_batches(batch_id),
  superseded_by      TEXT,
  quality_flags      TEXT NOT NULL DEFAULT '[]',-- JSON array
  notes              TEXT,
  captured_at        TEXT,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL,
  CHECK (period_type NOT IN ('week','month') OR (period_start IS NOT NULL AND period_end IS NOT NULL))
);

-- 同一逻辑键最多一条"已确认" → 保证 C2 的第①条
CREATE UNIQUE INDEX uq_records_confirmed
  ON records(logical_key) WHERE verification_status = 'confirmed';

CREATE INDEX ix_records_trend   ON records(metric_name, period_type, record_date);
CREATE INDEX ix_records_batch   ON records(batch_id);
CREATE INDEX ix_records_status  ON records(verification_status, record_date);
```

**为什么 `value` 允许 NULL**：规格 §2「缺失值使用 null，不得将缺失解释为 0」。一笔"页面显示了该字段但数值被遮挡"的记录与"页面根本没这个字段"是两种状态，前者存 `value=NULL + quality_flags=['unreadable']`（保留待补），后者**不建行**。两者在统计中都算缺失，但可审计性不同。

### 4.3 状态机

```
                 ┌─────────┐  用户确认   ┌───────────┐
  导入 ──────────▶│ pending │────────────▶│ confirmed │
                 └────┬────┘             └─────┬─────┘
                      │ 用户驳回                │ 修正（生成新版本）
                      ▼                        ▼
                 ┌──────────┐            ┌────────────┐
                 │ rejected │            │ superseded │──▶ 指向新 record_id
                 └──────────┘            └────────────┘
```

- `confirmed` → `superseded` 时**不删旧行**，`superseded_by` 指向新版本；趋势只取 `confirmed`。
- 批次撤销：把该 `batch_id` 下仍为 `pending` 的行删除、`confirmed` 的行转 `superseded` 并写 `audit_log(reason='batch_revoked')`——**绝不物理删除已确认数据**。

### 4.4 指标注册表（节选）

| metric_name | 单位 | 值域 | 允许周期 | 优先级 |
|---|---|---|---|---|
| `night_sleep_minutes` | min | 0–780 | day | P0 |
| `nap_minutes` | min | 0–300 | day | P0 |
| `total_sleep_minutes` | min | 0–1080 | day/week/month | P1（仅设备明确给出时入库） |
| `deep_sleep_minutes` / `light_sleep_minutes` / `rem_sleep_minutes` | min | 0–900 | day | P1 |
| `awake_count` | count | 0–40 | day | P1 |
| `resting_hr_bpm` | bpm | 25–200 | day | P0 |
| `sleep_avg_hr_bpm` | bpm | 25–200 | day | P0 |
| `daily_hr_min_bpm` / `daily_hr_max_bpm` | bpm | 25–230 | day | P1 |
| `hrv_avg_ms` / `hrv_min_ms` / `hrv_max_ms` | ms | 5–300 | day | P1 |
| `steps_count` | count | 0–100000 | day | P0 |
| `activity_energy_kcal` | kcal | 0–5000 | day | P0 |
| `spo2_avg_percent` / `_min_` / `_max_` | % | 50–100 | day | P1 |
| `respiratory_rate_avg_bpm` | brpm | 4–40 | day | P1 |
| `stress_score` | score | 0–100 | day | P1 |
| `sleep_score` | score | 0–100 | day | P1 |
| `deep_sleep_continuity_score` | score | 0–100 | day | P1 |
| `sleep_breathing_quality_score` | score | 0–100 | day | P1 |
| `arrhythmia_*`（4 项） | count | 0–1000 | day | P2 |

> 完整字典（含每个指标的中文名、来源页面、缺失语义）在 Wave 1 落为 `docs/01-data-dictionary.md`，代码侧 `catalog.py` 是唯一事实源，DB 由它 seed。

---

## 5. 导入 / 校验 / 去重流水线

### 5.1 流水线（每行独立决策，绝不整批中止）

```
文件 → loader(统一 RawRow) → ① 结构校验 → ② 日期/周期校验 → ③ 单位校验+换算
     → ④ 值域校验 → ⑤ 逻辑键/去重/冲突 → ⑥ 写库(pending) → ⑦ 预览 → 用户确认 → confirmed
```

### 5.2 失败模式处置分档（规格 §5 的落地口径）

| 失败模式 | 档位 | 具体动作 | 产出通道 |
|---|---|---|---|
| 缺 `metric_name` / 未知指标 | **拒绝** | 不入库，错误码 `E_UNKNOWN_METRIC` | 预览页"被拒绝"区 + 批次统计 |
| 日期无法判定 | **拒绝**（不自动确认） | 不入库或入 `pending` + `ambiguous_date` | 预览页高亮 |
| 单位不在白名单 | **拒绝** | 错误码 `E_UNIT_NOT_ALLOWED`，附建议单位 | 预览页 |
| 单位可换算但精度损失（`1:23` → 83min） | **放行 + 标记** | 存 `raw_value='1:23'`、`value=83`、flag `unit_converted` | 预览页标记 + 记录详情可见原始值 |
| 数值超值域 | **拒绝 + 人工复核** | 不自动修正，错误码 `E_OUT_OF_RANGE` | 预览页"待复核"区 |
| 数值格式非法（`'--'`、`''`、`'N/A'`） | **放行 + 标记** | `value=NULL` + flag `unreadable` | 记录详情 |
| 完全重复（`dedup_key` 相同） | **放行 + 汇总上报** | 跳过不重复入库，计入 `duplicate_count` | 批次报告 |
| 同逻辑键异值（冲突） | **放行 + 标记** | 入 `pending` + 写 `conflicts` | 冲突裁决页 |
| 周/月记录缺 `period_start` | **拒绝** | `E_PERIOD_INCOMPLETE` | 预览页 |
| 单行异常 | — | **不影响其余行**，逐行记结果 | 批次报告 |

验收要求（对应 execution-safety 分档验收）：E2E 必须同时覆盖「拒绝」与「放行+标记」两态。

### 5.3 去重与冲突算法

```
logical_key = sha256(f"{source_app}|{record_date}|{metric_name}|{period_type}|{period_start or ''}|{period_end or ''}")[:32]
dedup_key   = sha256(f"{logical_key}|{unit}|{round(value, 3) if value is not None else 'NULL'}")[:32]
```

- 命中 `dedup_key` → 完全重复，静默计入 `duplicate_count`（不打扰用户）。
- 命中 `logical_key` 但 `dedup_key` 不同 → **冲突**，新行 `pending` + `conflicts(status=pending)`，预览页强制展示「已有值 vs 新值」二选一。
- 若旧行是 `confirmed`，部分唯一索引会让新行**无法**被确认——除非用户先裁决（`take_incoming` 会把旧行转 `superseded` 再确认新行），从数据库层面堵住"静默覆盖"。

### 5.4 豆包适配层（R1 的解法）

规格说"截图由免费 AI（如豆包）辅助提取"，但豆包输出格式不固定。因此：

- **canonical 格式**（程序内部唯一认的）：`{record_date, metric_name, value, unit, period_type, period_start, period_end, source_page, raw_value, raw_unit}` 的数组或 CSV 等价列。
- `adapter_doubao.py` 负责把非标准输出（中文列名、`1小时23分` 这类文本、`58%` 混在 value 里）规范化为 canonical。**映射失败即拒绝该行并给出可读原因**，不做模糊猜测。
- 首版不内置 OCR。截图识别仍由人在豆包完成，程序只接文本结果——符合规格"程序负责校验，AI 负责解释"的分工。

---

## 6. 统计口径与报告

### 6.1 趋势计算（`analytics/aggregate.py`）

- 有效集：`verification_status='confirmed' AND period_type IN ('instant','day')`
- 日粒度 pivot：`record_date × metric_name → value`，缺失保持 `NaN`（**绝不 `fillna(0)`**）
- 每个指标输出：`mean / median / min / max / p25 / p75 / sample_days / missing_rate / status`
- `status ∈ {ok, insufficient, no_data}`：`sample_days < 3 → insufficient`
- 7 日滚动窗口；`missing_rate > 0.5` 时该窗口结论一律降级为"数据不足"

### 6.2 报告的可追溯性（规格 §10 硬验收）

报告渲染层输出结构：

```json
{
  "text": "过去 7 天平均睡眠 6 小时 42 分，比前一周少 23 分钟。",
  "numbers": [
    {"display": "6 小时 42 分", "value": 402, "unit": "min",
     "record_ids": ["r-8f21", "r-9c03", "..."]},
    {"display": "23 分钟", "value": -23, "unit": "min",
     "derived_from": "weekly_avg_delta", "window": ["2026-10-12", "2026-10-18"]}
  ]
}
```

UI 上每个数字可点开查看来源记录 ID 列表。**衍生数字（差值/比率）必须带 `derived_from` + `window`**，否则验收不通过。

### 6.3 简报与周报

- 每日简报：昨日核心 5 指标 + 相对基线的偏离 + 数据缺口 + 1–3 条低风险建议
- 每周趋势报告：ISO 周，5 指标周聚合 + 与上周对比 + 缺失率 + 触发的提醒汇总
- 两者都由 `reports/render.py` 组装 `ReportPayload`，AI 只负责改写 `text` 字段，**不得改动 `numbers`**（AI 输出 schema 校验失败则回退模板文案）
- 缓存键：`sha256(payload_digest + prompt_version + model)`；payload 无变化直接复用，规格 §7「相同输入可缓存」

---

## 7. 提醒规则

| rule_id | 触发条件（全部为 `confirmed` 日记录） | 冷却 | 证据字段 |
|---|---|---|---|
| `sleep_deficit_trend` | 近 7 天中 ≥3 天 `night_sleep_minutes < sleep_target_min × 0.9`，且窗口覆盖率 ≥4/7 | 72h | 触发日的 record_id 列表 |
| `baseline_deviation` | 单指标连续 3 天偏离基线 `\|x−median\| > 2.5×1.4826×MAD` | 72h | 基线快照 ID + 3 条 record_id |
| `resting_hr_shift` | `resting_hr_bpm` 基线偏移 > 8 bpm 且连续 ≥3 天 | 72h | 同上 |
| `data_gap` | 近 7 天该指标 `missing_rate > 0.5` | 24h | 缺失日期列表（**这不是健康提醒，是补录提示**） |

**硬约束**：

- 每条提醒必须携带 `trigger_reason` + `evidence_record_ids` + `suggested_action`（低风险，如"今晚提前 30 分钟就寝"），规格 §6 强制要求
- `dedup_key = sha256(rule_id|metric|window_start)` 唯一 → 同一窗口同一规则只发一次
- 用户可对每条提醒标 `已读 / 不相关 / 有帮助`，`不相关` 累计 3 次对同规则 → 自动降频为周报汇总
- `arrhythmia_*` 类指标**只保存设备页面文字 + 建议就医提示**，引擎中不存在任何"诊断/风险分级"逻辑

---

## 8. AI 层设计（DeepSeek）

### 8.1 数据边界

```
analytics 摘要(dict) ──▶ ai_client.summarize()
                          ├─ 输入只含：指标名、聚合值、缺失率、偏离度、触发规则、生活背景的布尔化摘要
                          ├─ 绝不包含：姓名、账号、设备 ID、截图路径、原始逐条记录
                          └─ 输出 JSON schema 校验 → 失败则 fallback.py 渲染模板文案
```

### 8.2 关键设计

| 项 | 方案 |
|---|---|
| 鉴权 | `DEEPSEEK_API_KEY` 从环境变量读（`.env` 被 gitignore）；日志中永不打印 |
| 超时/重试 | `timeout=30s`，最多 2 次重试，指数退避；失败即降级 |
| 降级 | `fallback.py` 用固定模板 + 已算好的数字生成简报 → **满足规格 §10「AI/API 失败时本地记录与统计仍可使用」** |
| 缓存 | 见 §6.3 |
| 计费记录 | `ai_call_log.cost_source ∈ {billed, estimated, unavailable}`；Harness 计费口径未确认前一律 `unavailable`，**不虚构费用** |
| 提示词 | 带 `prompt_version`，随缓存键变化；模板固定输出 JSON，禁止自由文本 |

### 8.3 待确认（U1）

任务书任务 6 要求"先确认用户实际使用的 Harness/API 接口、鉴权和计费方式，不猜测接口"。因此 Wave 6 之前需要你提供：实际 endpoint、模型名、鉴权方式（是否 OpenAI 兼容 `/v1/chat/completions`）、是否有用量回传字段。**在此之前 `ai_client` 只实现接口与降级路径，不接真实网络调用。**

---

## 9. UI 与访问安全（FastAPI + SSR）

### 9.1 页面清单

| 路由 | 功能 | 对应任务 |
|---|---|---|
| `GET /` | 今日概览：5 指标卡 + 数据质量条 + 最近简报 | Task 3/4 |
| `GET /records` | 记录列表：按指标/日期/状态筛选，显示来源与 flag | Task 3 |
| `POST /records/{id}/confirm` `.../reject` | 单条确认/驳回 | Task 3 |
| `POST /records/{id}/revise` | 修正 → 生成新版本 | Task 3 |
| `GET /imports` `POST /imports/upload` | 上传 → **预览页**（拒绝/待复核/重复/冲突四区分列） | Task 2 |
| `POST /imports/{id}/confirm` | 整批确认 | Task 2 |
| `POST /imports/{id}/revoke` | 撤销批次 | Task 3 |
| `GET /conflicts` `POST /conflicts/{id}/resolve` | 冲突二选一 | Task 2 |
| `GET /reports/daily` `GET /reports/weekly` | 报告 + 数字可追溯 | Task 4 |
| `GET /settings` | 生活背景（**全部可跳过，留空系统仍工作**） | Task 3 |
| `GET /export?fmt=csv\|json` | 导出全量 | Task 3 |
| `POST /danger/delete` | 删除记录/本地库（二次确认） | Task 3 |

### 9.2 安全设计（补规格 §8 的缺口）

- 默认 `uvicorn --host 127.0.0.1`；局域网开关为**显式配置项** `HEALTH_AGENT_LAN=1`
- 开启局域网时**强制**设置 `HEALTH_AGENT_PASSWORD_HASH`（PBKDF2），否则拒绝启动
- 会话 Cookie：`HttpOnly` + `SameSite=Lax` + 随机 secret；无 CSRF token 的写操作一律拒绝
- 不做公网暴露；文档明确"局域网即信任边界，家庭 Wi-Fi 之外不开放"
- 日志脱敏：只记 `metric_name` 与计数，**不记数值、不记密钥、不记完整请求体**
- 备份：`data/backup/YYYYMMDD-HHMMSS.zip`，包含 db + 配置快照（不含密钥）；恢复流程写进 runbook 并实测

### 9.3 平板访问（Task 7）

- 已验证/未验证必须分开列（任务书硬要求）
- 首版预期：局域网 IP + 密码可访问；已验证 = 页面渲染、表单提交、导出下载
- 未验证 = Safari/Chrome 平板的触摸手势细节、后台常驻、通知推送（P2 才做）

---

## 10. 待确认清单

| # | 问题 | 影响 | 默认（若你不回复） |
|---|---|---|---|
| U1 | DeepSeek 实际接口/鉴权/计费口径 | Wave 6 | 只写接口与降级，不接网络 |
| U2 | 豆包导出样例长什么样（真能拿到 JSON 吗？还是只能手抄） | Wave 2 适配层复杂度 | 按 canonical JSON + 宽松中文 CSV 两路做 |
| U3 | 是否有 1–2 张真实截图 + 对应正确读数作基准样本 | 决定能否做 OCR 回归测试 | 只用虚构样本，明确标注"未用真实数据验证" |
| U4 | 睡眠记录归属日（默认醒来日）是否与你的使用习惯一致 | 全部趋势 | 醒来日 |
| U5 | 基线参数（28 天窗口 / ≥14 天 / 2.5 MAD）是否接受 | 提醒灵敏度 | 按 D3 |
| U6 | 平板是否需要"多人访问"（家人共用） | 权限模型 | 单用户 |
| U7 | 是否需要保留原始截图副本在本地 | 存储与隐私 | 保留在 `data/screenshots/`，git 忽略 |

---

## 11. Wave 执行计划

每个 Wave 单独交付报告（任务书要求的 7 字段模板）。**未跑过的测试一律标"未运行"并写原因。**

| Wave | 对应任务 | 交付物 | 出口判据（可验证） |
|---|---|---|---|
| **W1** | Task 0+1 | 项目骨架、`pyproject`、`.env.example`、`catalog.py`、`001_init.sql`、虚构样例数据、`docs/01-data-dictionary.md`、schema 单测 | `pytest` 全绿（≥8 例）；`sqlite3 data/health.db ".schema"` 输出含部分唯一索引；README 能指导一次成功启动 |
| **W2** | Task 2 | `ingestion/*`、`validation/*`、批次与冲突、CLI `import --dry-run` | 覆盖 7 类场景（正常/缺失/错单位/重复/冲突/未知指标/日期不明）测试通过；dry-run 输出四区分列计数 |
| **W3** | Task 3 | `storage/repository.py`、FastAPI 骨架、记录/导入/冲突/设置/导出页面、鉴权 | 重启后数据仍在；空设置下系统可用；未登录访问 302；导出 CSV 行数 == 库内行数 |
| **W4** | Task 4 | `analytics/*`、日报/周报、数字追溯 | 报告中每个数字能点开看到 record_id；数据不足时输出"数据不足"且不编造趋势 |
| **W5** | Task 5 | `rules/*`、提醒页、反馈 | 5 类场景测试：触发/不触发/连续触发/数据缺失/重复提醒去重 |
| **W6** | Task 6 | `ai_client/*`、调用日志、缓存、降级 | 断网模拟下简报仍生成（走 fallback）；日志中 grep 不到密钥 |
| **W7** | Task 7 | 全量回归、`docs/02-runbook.md`、`docs/03-tablet-access.md`、备份/恢复实测 | 新环境按 README 可启动；平板"已验证/未验证"分列；备份→删库→恢复数据一致 |

**依赖关系**：W1 → W2 → W3 → W4 → (W5, W6 可并行) → W7。W6 在 U1 未确认前只做接口与降级。

---

## 12. 测试矩阵（规格 §10 验收 → 用例映射）

| 规格验收项 | 测试用例 | 断言 |
|---|---|---|
| 可导入含确认状态的 JSON/CSV 并在界面预览 | `test_e2e_import.py::test_preview_shows_all_sections` | 四区计数与输入行数一致 |
| 缺失值识别 | `test_validation.py::test_missing_value_is_null` | `value is None` 且**不出现 0** |
| 单位错误识别 | `::test_unit_not_in_whitelist` | 返回 `E_UNIT_NOT_ALLOWED` |
| 重复记录识别 | `test_dedup_conflict.py::test_exact_duplicate_skipped` | `duplicate_count==1`，库内行数不变 |
| 统计周期不明识别 | `::test_period_incomplete_rejected` | `E_PERIOD_INCOMPLETE` |
| 未确认数据不进趋势 | `test_analytics.py::test_pending_excluded` | 聚合样本数只计 confirmed |
| 日/周趋势 + 数据不足标注 | `::test_insufficient_samples` | `status=='insufficient'` |
| 报告中数字可追溯 | `::test_numbers_traceable` | 每个 number 的 record_ids 均存在于库 |
| AI 失败本地可用 | `test_ai_fallback.py::test_ai_down_still_reports` | 抛 `ConnectionError` 时仍产出非空简报 |
| 冲突记录 | `test_dedup_conflict.py::test_conflict_requires_resolution` | 新行 `pending` 且 `conflicts` 有 1 条 |

---

## 13. 风险清单

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | 豆包输出格式不定 | 导入器反复改 | canonical 格式 + 适配层隔离（§5.4） |
| R2 | OCR 数字误读（6↔8、3↔5） | 假趋势 | 全部 `pending` 强制人工确认 + 值域校验 + 保留 `raw_value` 对照 |
| R3 | 睡眠日期归属歧义 | 整体错位一天 | 固定"醒来日" + `ambiguous_date` 标记（C4） |
| R4 | 唯一约束 vs "冲突并存" 矛盾 | 要么覆盖要么插不进 | 部分唯一索引 + `conflicts` 表（C2） |
| R5 | 局域网泄露健康数据 | 隐私事故 | 默认本机 + 强制密码 + CSRF + 不做公网（§9.2） |
| R6 | Harness 计费口径不明 | 费用虚报 | `cost_source='unavailable'`，不估算 |
| R7 | pandas 时区/夏令时 | 日期错位 | 只存 `YYYY-MM-DD` 字符串，全程 naive local date |
| R8 | "顺手"把睡眠比例换算成时长 | 假数据 | 代码层禁止 `%→min` 换算路径，只打 flag |
| R9 | 规格膨胀（P1/P2 提前做） | 第一版拖长 | Wave 计划冻结 P0；P1 指标只入 catalog 不出报告 |

---

## 14. 已知限制（本方案本身的局限）

1. 所有指标值域（§4.4）来自常规生理范围常识，**未与华为官方字段说明逐项核对**；Wave 1 落地时应以你实际页面截图为准校正，否则会误拒合法值。
2. 睡眠结构类字段（`deep_sleep_continuity_score` 等）在华为 App 中的确切含义与是否稳定出现未验证。
3. 基线算法 D3 的参数未用真实数据调过，首月大概率需要按你的数据分布调一次。
4. 平板端的实际可用性完全未测（无设备），当前只有设计假设。
5. 本方案未包含任何 OCR 能力；截图→文本这一步仍是人工 + 豆包。

---

## 15. 修订记录

### V1.1（2026-10-19，任务 0 开工前校准）

用户明确：**主数据源与字段命名体系一律以第一版（技术规格书 V1.0）为准**。据此收敛：

| 项 | V1.0 方案 | V1.1 裁决 |
|---|---|---|
| 主数据源 | 截图提取的 JSON/CSV（豆包辅助）+ 华为导出包两种混用 | **只保留第一版口径**：截图提取结果 → JSON/CSV 导入。不引入华为隐私中心加密包通道，不新增 `huawei_health_loader` |
| 字段命名 | canonical 用规格 §4 字段名，另备外部映射 | **canonical 即规格 §4 字段名**，已固化为 `src/health_agent/catalog.py`（0.1.1 起 37 个指标；2026-10-20 移除血压两项后为 35 个）。不为任何外部导出格式的字段名让路 |
| OCR 截图通道 | 主通道 | 降为**实验性**，输出一律 `pending`，不纳入 P0 主流程 |
| U1 DeepSeek 接口 | 待确认 | **仍为待确认**。仅保留 `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` 两个可配置项的占位默认值，不写任何网络调用代码（任务 6 之前） |

### V1.2（2026-10-19，任务 4 完成后）

| 项 | V1.0 方案 | 实际裁决 |
|---|---|---|
| 统计实现 | 规格 §9 建议用 pandas | **改为标准库**。统计只要 7–28 天窗口 + 逐条 record_id 追溯，stdlib 更精确、零依赖。重新引入的判据：单次窗口超过 5 万行，或需要复杂重采样。 |
| 报告缺口提示 | 列出所有无数据指标 | **只列"用户确实在记、本次缺失"的指标**（近 30 天有已确认记录）。否则每天列 17 个从未记录的指标，是噪音不是信息。 |
| 均值显示 | 直接输出浮点 | 整数单位（bpm/kcal/步数）取整，其余最多一位小数——"58.8571 次/分"不该出现在给人看的简报里。 |
| instant 记录 | 未定义同一天多次测量 | 已知限制：逻辑键只到日期粒度，同来源同日第二条瞬时记录会进冲突流程。启用 P2 指标前必须把观测时刻纳入键。 |

### V1.3（2026-10-19，任务 5 完成后）

| 项 | V1.0 方案 | 实际裁决 |
|---|---|---|
| 基线离散度 | 中位数 + MAD | 保留，但补 **MAD=0 时用 IQR 兜底**：整数指标（静息心率）经常一半以上取值相同，MAD 会退化成 0，直接判"基线无效"会让提醒长期失灵。 |
| 阈值可调 | 未明确在哪配 | 5 个阈值进环境变量（睡眠目标/不足天数/心率偏移/缺失率/冷却期），`config_from_settings` 统一注入。 |
| 心律字段 | 只保存设备原文 | 规则引擎源码中**不出现**任何 arrhythmia 关键词，测试直接扫描源码断言。 |

### V1.4（2026-10-19，任务 6 完成后）

| 项 | V1.0 方案 | 实际裁决 |
|---|---|---|
| AI 触发时机 | 生成报告时调用 | **改为按需**：GET 只读缓存，POST `/reports/rewrite` 才调用。实测"打开页面即调用"导致单页 10s；且规格 §7 本就要求"只有新数据或用户请求时才重新分析"。 |
| 接口假设 | 待确认 | 实现 `LLMClient` 协议 + OpenAI 兼容 `chat/completions`，端点/模型/超时/重试全部可配；未确认前不硬编码具体字段。 |
| 费用 | 记录 Token 与费用 | Token 有就记；**费用记 `unavailable`**，不做估算（规格 §7 明确不得虚构费用）。 |
| 输出校验 | 未定义 | 强制 `{"text": "…"}` 结构，解析失败即降级；模型数字必须来自摘要，提示词里写死这条。 |

### V1.4（2026-10-19，任务 6 完成后）

| 项 | V1.0 方案 | 实际裁决 |
|---|---|---|
| AI 触发时机 | 生成报告时调用 | **改为按需**：GET 只读缓存，POST `/reports/rewrite` 才调用。实测"打开页面即调用"导致单页 10s；且规格 §7 本就要求"只有新数据或用户请求时才重新分析"。 |
| 接口假设 | 待确认 | 实现 `LLMClient` 协议 + OpenAI 兼容 `chat/completions`，端点/模型/超时/重试全部可配；未确认前不硬编码具体字段。 |
| 费用 | 记录 Token 与费用 | Token 有就记；**费用记 `unavailable`**，不做估算（规格 §7 明确不得虚构费用）。 |
| 输出校验 | 未定义 | 强制 `{"text": "…"}` 结构，解析失败即降级；模型数字必须来自摘要，提示词里写死这条。 |

### V1.5（0.1.1，审查修复）

| 项 | 处理 |
|---|---|
| 睡眠目标默认值被当成"用户的目标" | 新增 `sleep_target_user_supplied`；未配置时文案明确写"默认参考值…未按年龄、性别或作息调整" |
| 是否做了年龄适配 | 没做。`AGE_ADAPTATION_IMPLEMENTED = False`，测试断言，文档与文案均如实说明 |
| HRV / 血氧区间被平均的风险 | 新增 `range_bound` 类型与 4 个端点指标；注册表导入即断言"端点指标不得进报告"；`REPORT_METRICS` 不含它们 |
| 指标定义可信度不可见 | 新增 `verification` 字段（`provisional` / `device_verified`），当前 35 个全为 `provisional` |
| 文档与代码漂移 | `alert_feedback` 表不存在（实际是 `alerts.feedback` 列）；规则表述误写 `total_sleep_minutes`（实际用 `night_sleep_minutes`）——均已更正 |
| 死配置 | 删除从未被读取使用的 `HEALTH_AGENT_TZ` |

原 §5.4「豆包适配层」的结论不变：程序只接文本结果，格式不适配就拒绝该行，
不做模糊猜测。区别仅在于**不再设计第二条加密包解析通道**。

---


## 16. 交付材料清单（最终交付时应齐备）

- 使用说明（README + runbook）
- 部署/启动说明（Windows 一条命令）
- 备份与恢复说明（含实测记录）
- 数据导出说明
- 故障排查（AI 不可用、DB 锁、端口占用、平板连不上）
- 已知限制清单（本文件 §14 的演进版）
- **本文件 §14 第 1 条的时间点**：Wave 1 结束前必须用真实页面校正值域表
