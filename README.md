# Personal Health Agent

本地优先的个人健康管理助手。把华为运动健康里能读到的健康指标（截图提取 → JSON/CSV）
导入本地库，做校验、去重、趋势与简报。

**边界（写在最前面）**：只做趋势观察与一般生活习惯建议，**不做疾病诊断、不做治疗建议、
不做体重控制**。手环数据只能辅助观察，身体不适请直接就医，不要等程序提醒。

---

## 当前进度

| 阶段 | 状态 |
|---|---|
| 任务 0 环境与项目初始化 | ✅ 完成 |
| 任务 1 数据模式与导入样例 | ✅ 完成（schema / 指标注册表 / 35 个指标 / 样例 / 校验规则） |
| 任务 2 导入、预览、校验与去重 | ✅ 完成（loader / 五桶预览 / 批次 / 冲突登记 / CLI） |
| 任务 3 本地数据库与基础 UI | ✅ 完成（确认/驳回/修正/撤销/裁决 + FastAPI 界面 + 鉴权） |
| 任务 4 趋势统计与报告 | ✅ 完成（窗口聚合 / 覆盖率 / 日报周报 / 数字追溯） |
| 任务 5 规则提醒 | ✅ 完成（中位数+MAD 基线 / 4 条规则 / 去重冷却 / 已读反馈） |
| 任务 6 DeepSeek 接入与费用监控 | ✅ 完成（独立模块 / 缓存 / 调用日志 / 失败降级） |
| 任务 7 回归、打包与平板评估 | ✅ 完成（备份恢复实测 / runbook / 平板评估分列） |

### 0.1.2 变更（UI 设计落地 + 时钟模式 Demo）

| 项 | 0.1.1 | 0.1.2 |
|---|---|---|
| 导航 | 顶部一行 5 个链接 | 左侧侧栏 + 分组（总览/分析/数据/其他）；<1024px 折成横向导航 |
| 首页 | 计数卡 + 两张表 | 今日摘要、4 张指标卡（值/单位/有效天数/状态点）、睡眠柱状图 + 目标线、待处理、最近记录 |
| 睡眠分析 | 无 | 新增 `/sleep`：每日时长柱状（缺失空心柱）+ 目标线 + 阶段比例 + 明细 |
| 指标趋势 | 无 | 新增 `/trends`：折线/柱状/区间带，缺失断线，区间端点不显示均值 |
| 时钟模式 | 无 | 新增 `/chronos` Demo：6 扇区外环 + 中心摘要 + 普通入口；模式存 `settings.ui_mode` |
| 设计系统 | 176 行、1 个断点 | 设计 token（色板/字号/间距/圆角/阴影）+ 3 个断点 + 触控 44px |
| 状态语义 | 一套 tag | 记录状态 / 指标可信度 / 数据充分性三套，互不复用颜色 |

图表为**原生 SVG**，无新增依赖；所有数值仍走 `reports/format.py` 的格式化。

### 0.1.1 变更（审查修复）

| 项 | 0.1.0 | 0.1.1 |
|---|---|---|
| 睡眠目标 | 420 分钟作为默认值，提醒文案直接写"目标 7 小时"，读起来像"你的目标" | 区分「用户设定」与「默认参考值」。未配置时文案为"默认参考值 7 小时（成人常见建议 7–9 小时的下沿；未按年龄、性别或作息调整）" |
| 年龄适配 | 无 | **仍然没有**。代码里 `AGE_ADAPTATION_IMPLEMENTED = False`，有测试断言它为假——不做的功能不许暗示做了 |
| 提醒边界 | 文案未声明性质 | 触发原因末尾固定追加"仅作趋势观察，不构成诊断" |
| HRV / 血氧区间 | 只有平均值字段，区间无处安放 | 新增 4 个 `range_bound` 端点指标（`hrv_min_ms`/`hrv_max_ms`/`spo2_min_percent`/`spo2_max_percent`），`in_report=0`；注册表导入时断言它们不得进报告 |
| 指标可信度 | 未标注 | 每个指标带 `verification`；**当前 35 个全部是 `provisional`**，未经厂商字段核对 |
| 死配置 | `HEALTH_AGENT_TZ` 读入后从未使用 | 删除 |
| 设置页 | 不显示睡眠目标 | 显示当前生效值及其来源（默认参考值 / 你设定的目标），不提供第二个修改入口 |

> 说明：**任务 0–7 全部完成**（P0 范围）。未配置 AI 密钥时全部功能照常，简报使用本地模板文案；
> 配置后可在报告页点「用 AI 改写」按需调用。运维与限制见 `docs/02-runbook.md`。

---

## 环境

- Windows（开发机），Python ≥ 3.11
- 依赖清单见 `requirements.txt` / `pyproject.toml`（FastAPI、uvicorn、Jinja2、httpx）
- 统计层用标准库实现：窗口只有 7–28 天、数据量在万行以内，不需要 pandas。
  等真的需要复杂重采样或数据量上一个量级时再引入（判据写在方案文档 §15）。

## 安装

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m pip install -e .
```

`requirements.txt` 是运行时依赖；`requirements-dev.txt` 在其基础上加 pytest（版本已锁）；
`pip install -e .` 让命令行入口可以直接用，不必再设 `PYTHONPATH`。

## 启动

```bash
# 全部测试
.venv/Scripts/python.exe -m pytest

# 界面
.venv/Scripts/python.exe -m health_agent.cli serve
# 浏览器打开 http://127.0.0.1:8000

# 命令行
.venv/Scripts/python.exe -m health_agent.cli init
.venv/Scripts/python.exe -m health_agent.cli import test_data/sample_daily_metrics.json          # 只预览
.venv/Scripts/python.exe -m health_agent.cli import test_data/sample_daily_metrics.json --commit  # 写库
.venv/Scripts/python.exe -m health_agent.cli stats
.venv/Scripts/python.exe -m health_agent.cli conflicts
```

### 界面里有什么

| 页面 | 能做什么 |
|---|---|
| `/` | 记录总数、待确认数、待裁决冲突、带标记记录数，最近记录与导入 |
| `/records` | 按指标/状态筛选；确认、驳回、改值、删除 |
| `/imports` | 上传 JSON/CSV/TSV → 预览 → 写入；批次确认全部 / 撤销 |
| `/conflicts` | 新旧值对照，选择「保留已有」或「采用新值」 |
| `/sleep` | 睡眠时长柱状图 + 目标线（标注来源）+ 阶段结构 + 明细；数据不足不给趋势 |
| `/trends` | 多指标折线/柱状/区间带；缺失断线；区间端点不显示均值 |
| `/chronos` | 时钟模式 Demo：外环导航 + 中心摘要 + 普通入口 |
| `/reports` | 每日简报 / 每周报告；每个数字可展开看来源记录；数据质量与覆盖率 |
| `/alerts` | 提醒列表；立即评估；标记已读；三条反馈（有帮助/已照做/不相关） |
| `/settings` | 生活背景（全部可跳过）；运行信息；导出入口 |
| `/export.csv` `/export.json` | 全量导出（CSV 带 BOM，Excel 中文不乱码） |

**访问控制**：未设密码时只监听 127.0.0.1；要开局域网必须先设密码。

```bash
.venv/Scripts/python.exe -m health_agent.cli passwd   # 生成 HEALTH_AGENT_PASSWORD_HASH
```

预期：`295 passed`。测试覆盖：指标注册表不变式、单位白名单与换算、单行校验的全部
拒绝与标记路径、迁移幂等、部分唯一索引拦截静默覆盖、配置约束、文档一致性、
JSON/CSV 读取（含 BOM 与 GBK）、五桶分桶、批次计数、冲突登记、CLI 干跑不写库、状态迁移、鉴权与 CSRF、导出、设置读写、窗口聚合（确认制/缺失率/样本不足）、
报告数字追溯、周报 4/7 覆盖率门槛、基线（MAD/IQR 兜底）、
4 条规则的触发/不触发/边界/去重/冷却/反馈、
AI 降级与缓存、HTTP 重试策略、提示词输出校验、
备份完整性校验与恢复（含篡改拒绝）。

### 导入的五个结局

每次导入，每一行必然落到且只落到一个桶里（`preview.balanced` 会断言总数守恒）：

| 桶 | 含义 | 是否入库 |
|---|---|---|
| `accepted` | 校验通过、无标记 | 是，`pending` |
| `review` | 有标记（不可读 / 超范围 / 单位推断 / 文本解析 / 缺值） | 是，`pending` |
| `duplicates` | 完全重复（同键同值） | 否，跳过 |
| `conflicts` | 同槽位不同值 | 是，`pending` + 登记冲突待裁决 |
| `rejected` | 结构性不合格 | 否，给出错误码 |

**所有入库记录都是 `pending`**，导入本身永远不会产生 `confirmed`。

## 配置

复制 `.env.example` 为 `.env` 后按需填写。**密钥只放环境变量，禁止写进代码**。

```bash
cp .env.example .env
```

关键项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `HEALTH_AGENT_DB_PATH` | `data/health.db` | 本地库路径 |
| `HEALTH_AGENT_LAN` | `0` | 置 1 允许局域网访问；**必须同时设置密码 hash，否则拒绝启动** |
| `DEEPSEEK_API_KEY` | 空 | 为空时 AI 全部走本地模板降级，不影响记录与统计 |
| `DEEPSEEK_MODEL` | `deepseek-chat` | 模型可切换，不硬编码 |
| `HEALTH_AGENT_SLEEP_TARGET_MIN` | `420` | 睡眠目标（分钟），用于趋势提醒 |
| `HEALTH_AGENT_BASELINE_MAD_FACTOR` | `2.5` | 基线偏离阈值倍数 |
| `HEALTH_AGENT_RULE_COOLDOWN_HOURS` | `72` | 同类提醒冷却小时数 |
| `HEALTH_AGENT_SLEEP_DEFICIT_DAYS` | `3` | 近 7 天几天睡眠不足才提醒 |
| `HEALTH_AGENT_RESTING_HR_SHIFT_BPM` | `8` | 静息心率偏离基线多少才提醒 |
| `HEALTH_AGENT_DATA_GAP_RATE` | `0.5` | 缺失率超过多少提示补录 |
| `DEEPSEEK_TIMEOUT_SECONDS` | `20` | AI 请求超时（秒） |
| `DEEPSEEK_MAX_RETRIES` | `2` | AI 请求重试次数（4xx 不重试） |
| `DEEPSEEK_TIMEOUT_SECONDS` | `20` | AI 请求超时（秒） |
| `DEEPSEEK_MAX_RETRIES` | `2` | AI 请求重试次数（4xx 不重试） |

## 目录

```
src/health_agent/
├─ catalog.py      指标注册表（canonical schema 唯一事实源）
├─ config.py       环境变量 → Settings
├─ errors.py       错误码与异常
├─ validation/     单位白名单、单行校验（纯函数）
├─ storage/        SQLite 连接、迁移、keys（去重键）、repository
├─ ingestion/      导入与批次（任务 2）
├─ analytics/      聚合与基线（任务 4）
├─ rules/          提醒引擎（任务 5）
├─ ai_client/      DeepSeek 封装（任务 6）
├─ reports/        日报 / 周报（任务 4）
└─ ui/             FastAPI 界面（任务 3）
```

依赖方向是单向的：`ui → reports → analytics → storage`，
`ui → ingestion → validation`。SQL 只出现在 `storage/` 内。

## 提醒规则

| rule_id | 触发条件 | 冷却 |
|---|---|---|
| `sleep_deficit_trend` | 近 7 天 ≥3 天夜间睡眠低于目标 90%，且覆盖 ≥4/7 天 | 72h |
| `resting_hr_shift` | 静息心率连续 3 天偏离个人基线超过 8 次/分 | 72h |
| `baseline_deviation` | 睡眠平均心率 / 步数连续 3 天偏离基线（中位数 + MAD） | 72h |
| `data_gap` | 近 7 天缺失率 > 50%（补录提示，不是健康提醒） | 72h |

每条提醒都带：触发原因、依据的 record_id 列表、一条低风险建议。
同一窗口同一规则只发一次（`dedup_key` 唯一），不同窗口受冷却期约束。

**个人基线**用中位数 + MAD，而不是均值 + 标准差——生理数据天然有离群值，
均值会被单个熬夜夜拉偏。MAD 退化为 0 时（整数指标常见）自动改用 IQR 估计，
仍为 0 才判定"样本过于单一、无法建基线"。

**没有任何规则解读心律筛查结果**：那类字段只保存设备页面原文，测试会扫描源码确保这一点。

## AI 使用方式

**默认不调用。** 打开报告页只读缓存，页面响应 30–120ms；要 AI 改写必须点按钮。

- 发送内容：已算好的摘要（指标名、均值、区间、缺口），**不含 record_id、不含截图、不含账号**
- 输出约束：模型必须返回 `{"text": "…"}`；解析失败按失败处理，不把半成品塞进报告
- 缓存键：`提示词版本 + 模型 + 摘要哈希`，同一份摘要不重复付费
- 调用日志：`ai_call_log` 记录模型、Token、耗时、状态；**费用一律记 `unavailable`**，
  不拿官方标价冒充实际账单
- 失败处理：超时/网络/4xx/输出不合规 → 退回本地模板文案，记录/统计/提醒完全不受影响

接口契约：`LLMClient` 协议 + OpenAI 兼容的 `POST {base_url}/chat/completions`。
**最终接口尚未由用户确认**，换供应商只需换一个实现类。

## 数据规则（改动代码前请先读）

1. **缺失就是 `NULL`**，绝不以 0 填充，绝不猜测无法辨认的数值。
2. 截图/OCR 提取的结果一律先 `pending`，人工确认后才变 `confirmed`。
3. 未 `confirmed` 的数据不进任何趋势、基线或提醒计算。
4. 同一来源 + 日期 + 指标 + 周期的记录最多一条 `confirmed`（数据库部分唯一索引强制）；
   冲突并存于 `pending`，须人工裁决，**不允许静默覆盖**。
5. `period_type` 是记录属性：`week` / `month` 记录永不参与日趋势。
6. 比例不得换算成时长（如深睡 18% 不得乘总时长变成分钟）。
7. 设备给出的评分（压力、深睡连续性等）原样存储，不做二次医学解读。

## 已知限制

- 指标合理值域目前来自常规生理范围，**尚未逐项与华为官方字段说明核对**，35 个指标全部标记
  `provisional`，可能过严或过松（超出范围只标记、不修正、不丢弃）。
- **睡眠目标未做年龄适配**：没有年龄字段，默认 420 分钟只是参考值，提醒文案会如实说明。
- 无 HTTPS；局域网模式仅单密码鉴权，不要暴露到公网（详见 `docs/03-tablet-access.md`）。
- 无 HTTPS；局域网模式仅单密码鉴权，不要暴露到公网（详见 `docs/03-tablet-access.md`）。
- 没有真实截图样本做回归，`test_data/` 里的数据全部是**编造的**。
- 平板端（局域网访问）完全未测。
- 界面没有 HTTPS：局域网使用即等于信任该网络，不要在公共 Wi-Fi 上开启。
- 没有「忘记密码」机制——密码 hash 存在 `.env` 里，丢了就删掉重设。
- 会话密钥存在 `data/.session_secret`，删除它会让所有已登录会话失效。
- **逻辑键只到「日期」粒度**：同来源同日第二条瞬时测量（如早晚各一次体温）会被判为冲突，
  而不是当成两次测量。启用 P2 指标（体温/体重/血糖）前必须把观测时刻纳入逻辑键。
- 简报与周报使用固定模板文案，尚未接 AI 改写（任务 6）。
- 提醒只做站内列表，没有桌面通知或手机推送；需要主动查看 `/alerts`。
- 「不相关」反馈目前只记录，尚未据此自动降频（方案里有这个设计，未实现）。
- AI 费用无法从当前接口拿到账单数据，日志中 `cost_source` 固定为 `unavailable`；
  接入真实计费后再补。
- AI 通道只在明确点击时触发，因此报告不会"自动变好看"；这是刻意取舍（避免静默花钱）。
- AI 费用无法从当前接口拿到账单数据，日志中 `cost_source` 固定为 `unavailable`；
  接入真实计费后再补。
- AI 通道只在明确点击时触发，因此报告不会"自动变好看"；这是刻意取舍（避免静默花钱）。
- CSV 读入时如果文件带 BOM，需要用 `utf-8-sig` 打开——任务 2 的 loader 必须处理这一点
  （`test_data/sample_with_issues.csv` 在编写时就踩到过这个坑）。

## 文档

- `docs/00-architecture-plan-v1.md` — 架构与实施方案（含 4 个规格冲突的裁决 + 修订记录）
- `docs/01-data-dictionary.md` — 数据字典（由 `catalog.py` 派生）
- `docs/02-runbook.md` — **运维手册**：安装、备份、恢复、排障、升级
- `docs/03-tablet-access.md` — 平板访问评估（已验证 / 未验证分列）

## 备份与恢复

```bash
.venv/Scripts/python.exe -m health_agent.cli backup
.venv/Scripts/python.exe -m health_agent.cli restore data/backup/health-<时间戳>.zip --force
```

备份包 = SQLite 一致性快照（backup API，不是文件复制）+ manifest（sha256 / 记录数 / schema 版本）。
恢复前校验指纹，目标库已存在时必须显式 `--force`，且会先留 `.bak`。

实测：21 条记录 + 43 条审计 → 备份 → 删库 → 恢复 → 记录数、审计数、导出内容指纹完全一致。
