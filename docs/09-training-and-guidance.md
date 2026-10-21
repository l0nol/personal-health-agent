# 训练日志与每日建议（T08 / T09）

> 这份文档面向使用者与下一任维护者：**怎么用、输出长什么样、边界在哪、哪里还没做**。
> 设计与取舍见 `reports/T08_TRAINING_DATA_REPORT.md`、`reports/T09_DAILY_GUIDANCE_REPORT.md`。

- schema：v2（`002_training.sql`）
- 规则版本：`guidance-1.0.0`
- 状态：T08 / T09 **待审核**；训练日志页面属 T10，尚未实现

---

## 1. 五分钟跑通

```bash
python -m venv .venv
source .venv/bin/activate                 # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e .

# 1) 建库（会应用 001 + 002 两个迁移）
health-agent init --db data/demo.db

# 2) 记一次训练（时长、强度可以留空）
health-agent training add --db data/demo.db \
  --date 2026-10-19 --type strength --parts "胸,肩" \
  --duration 45 --intensity 7 --content "卧推 3×8，划船 3×10"

# 3) 记今天的恢复反馈（全部可留空）
health-agent feedback --db data/demo.db \
  --energy 2 --fatigue 4 --soreness-parts 胸 --soreness-level 3

# 4) 看建议
health-agent advice --db data/demo.db --date 2026-10-20
health-agent advice --db data/demo.db --date 2026-10-20 --json
```

不装包也可以：

```bash
PYTHONPATH=src python -m health_agent.cli advice --db data/demo.db
```

---

## 2. 命令速查

| 命令 | 作用 | 关键参数 |
|---|---|---|
| `training add` | 记录一次训练 | `--date` `--type` `--parts` `--content` `--duration` `--intensity` `--completion` `--start` `--notes` |
| `training list` | 查看训练 | `--days`（默认 14）`--limit` |
| `training delete` | 删除一条 | `session_id`（支持前 8 位以外的完整 ID） |
| `feedback` | 记录/覆盖当天反馈 | `--date` `--energy` `--fatigue` `--soreness-parts` `--soreness-level` `--discomfort` `--note` |
| `prefs` | 查看/设置偏好 | `--equipment` `--days` `--time-budget` `--avoid` |
| `advice` | 生成当天建议 | `--date` `--json` `--explain` `--confirm-send` |

活动类型：`strength` / `cardio` / `mobility` / `sport` / `walking` / `other`。

训练部位：`full_body` `upper` `lower` `push` `pull` `legs` `core` `back` `chest` `shoulders` `arms`，
也可写中文：全身/上肢/下肢/推/拉/腿/核心/背/胸/肩/手臂。

**留空就是 NULL**：`--duration`、`--intensity`、`--energy`、`--fatigue` 不填时字段为 `NULL`，
不会被当成 0，也不会进入"平均强度"之类的计算。

---

## 3. 建议的四种类别

| 类别 | 含义 | 典型触发 |
|---|---|---|
| `rest` | 今天不安排训练 | 疼痛/眩晕/明显不适；酸痛 ≥4/5；疲劳 ≥4/5；严重缺睡且疲劳 |
| `light_activity` | 轻松活动，不正式训练 | 睡眠 < 目标 90%；疲劳 3/5；精力 ≤2/5；本周次数已达成；近期部位都练过 |
| `regular_training` | 可以按计划训练 | 睡眠与反馈都在可训练范围；优先练近期没练的部位 |
| `insufficient_data` | 资料不足，不给具体安排 | 最近一晚睡眠缺失且无反馈；设备数据过期且缺睡眠/反馈 |

**睡眠不足不是禁令**：睡眠偏短但自感状态好时，给的是"降低强度的轻松活动"，不是"禁止运动"。

---

## 4. 输出契约（JSON）

`advice --json` 的字段：

| 字段 | 说明 |
|---|---|
| `for_date` | 建议针对的日期 |
| `category` / `category_label` | 四种类别及中文名 |
| `headline` | 一句话结论 |
| `focus` | 今天做什么 |
| `alternatives` | 替代项（状态变化时怎么调整） |
| `evidence[]` | 依据：`label` / `value_text` / `window_start` / `window_end` / `coverage` / `source` / `record_ids` / `session_ids` |
| `limitations[]` | 边界声明（固定包含"不构成诊断""未记录年龄"） |
| `uncertainty[]` | 不确定性与缺失说明 |
| `feedback_prompt` | 反馈入口提示 |
| `data_freshness_days` | 最新设备数据距目标日期多少天；无数据为 `null` |
| `generated_at` | 生成时间 |
| `rule_version` | 规则版本，便于回溯结论为什么变了 |
| `safety_notice` | 安全提示 |
| `explanation` / `explanation_source` | 解释文本及来源（`local` / `ai`） |

`evidence.source` 取值：`device`（设备记录，带 `record_ids`）、`training_log`（带 `session_ids`）、
`subjective`（用户主观反馈）、`preference`（训练偏好）。

---

## 5. AI 解释：默认关闭

```bash
# 第一步：只打印将发送的摘要，不联网
health-agent advice --explain

# 第二步：确认后允许发送（需 DEEPSEEK_API_KEY）
health-agent advice --explain --confirm-send
```

发送内容只有：类别、结论、重点、依据的文字描述、替代项、不确定性、边界声明。
**不发送**：`record_id`、`session_id`、绝对日期、备注原文、截图、账号、整库。

AI 输出会经过校验：

- 出现"高强度/力竭/减脂/燃脂/补偿"等词 → 拒绝；
- 出现"诊断为/可以诊断/治疗方案"等句式 → 拒绝；
- 出现摘要之外的数字 → 拒绝（视为编造）；
- 超时、网络错误、HTTP 错误、空输出 → 退回本地解释。

任何情况下，`category`、`focus`、`evidence` 都由本地规则决定，AI 只改"解释这段话"。

---

## 6. 数据与隐私

- 训练日志与恢复反馈存在本地 SQLite 的独立表里，不与设备指标 `records` 混表。
- `recovery_feedback.source` 由数据库约束固定为 `subjective`，无法写成设备测量。
- 导出：`export.json` 新增 `training_sessions` / `recovery_feedback` / `training_preferences` 三个键，
  `records` 与 `schema_version` 保持不变；训练日志另有 CSV：`export_training_csv()`。
- 备份：全库快照天然包含训练表；恢复旧备份时会自动应用 `002` 迁移。

---

## 7. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `E_BODY_PART` | 部位不在枚举/别名表 | 用枚举或中文别名；自由描述写进 `--content` |
| `E_SORENESS_PAIR` | 只填了部位或只填了程度 | 两者同时填，或同时留空 |
| `E_TRAINING_DATE` | 日期格式错，或填了未来日期 | 用 `YYYY-MM-DD`，不要填明天 |
| 建议总是"资料不足" | 没有最近睡眠记录，也没有当天反馈 | 先导入/补录睡眠，或 `feedback --energy/--fatigue` |
| 建议总是"常规训练"且没有部位 | 没有训练记录 | 用 `training add` 记 1–2 次，建议会开始换部位 |
| `--explain` 只打印摘要 | 这是两步确认的第一步 | 加 `--confirm-send`；未配置密钥时会自动用本地解释 |
| 旧库打不开 | 未跑迁移 | 直接执行任意 `health-agent` 命令都会触发 `init_db` 迁移 |

---

## 8. 还没做的事（不要误以为已有）

| 项 | 状态 | 归属 |
|---|---|---|
| 训练日志页面 / 首页建议卡 | 已实现（T10，待审核） | — |
| Chronos 模式整合 | 未实现 | T11 |
| 真实 AI 接口验证 | **未验证**（无密钥） | T12 |
| 真实华为数据验证 | **未验证**（全部合成数据） | T13 |
| 平板真机 | **未验证** | T13 |
| 同日多次训练的时段区分 | 未实现（只到日期粒度） | 后续 |
| 年龄适配睡眠目标 | 未实现（文案已声明） | 后续 |

---

## 9. 自动化验证

```bash
python -m pytest tests/test_t08_training.py tests/test_t09_guidance.py -q   # 53 passed
python -m pytest tests/test_t10_training_ui.py -q                           # 14 passed
python -m pytest                                                            # 463 passed
PYTHONPATH=src python scripts/demo_guidance.py                              # 6 个场景 + 摘要预览
```
