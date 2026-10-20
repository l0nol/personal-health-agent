# T00 — 项目基线盘点报告

- **项目**：Personal Health Agent（`personal-health-agent`）
- **项目路径**：`C:\Users\Lenovo\WorkBuddy\agent\personal-health-agent`
- **盘点时间**：2026-10-20 08:35–08:45 GMT+8
- **任务包**：`Personal_Health_Agent_WorkBuddy_TaskPack_V1.0`（本次附件提供，解压于项目外部临时目录）
- **执行范围**：仅检查与记录。**未修改任何业务代码、数据库结构、依赖或测试；未删除任何文件。**
- **状态**：待审核

---

## 0. 结论摘要

| 项 | 结论 |
|---|---|
| 版本 | 0.1.2（`pyproject.toml` 与 `src/health_agent/__init__.py` 一致） |
| 测试 | **295 passed / 0 failed / 0 skipped / 31 warnings，退出码 0，19.88s** |
| 与文档一致性 | 测试数、指标数（37）、区间端点（4）、规则数（4）均与文档相符 |
| 版本管理 | **无 Git 仓库**——无法核对"未提交修改"，也无法回滚 |
| 数据库 | 工作区内**无任何 .db 文件**，库未初始化；迁移脚本可独立建库成功（11 表 / 10 索引 / 37 条指标目录） |
| UI | FastAPI + Jinja2 服务端渲染，32 个路由，13 个页面模板 + 1 个宏文件，347 行 CSS，**零 JS**，图表为手写 SVG |
| 已确认问题 | 6 条（1 条中风险技术债、1 条口径不一致、4 条文档/断言精度问题），**均在 T00 禁止修改范围内，仅记录** |
| 未验证项 | 6 类（真实截图数据、平板真机、DeepSeek 真实接口、满态 UI、备份实测、局域网鉴权真实浏览器） |
| 总体判断 | 基线可继续推进 T01；**但"无版本控制"需要在进入 T01 前由用户裁决** |

---

## 1. 版本与环境

| 项 | 实测值 | 证据 |
|---|---|---|
| 应用版本 | `0.1.2` | `pyproject.toml:7`、`src/health_agent/__init__.py:10`（两处一致） |
| Python | 3.13.14（项目 `.venv`） | `.venv/Scripts/python.exe -V` |
| 声明要求 | `requires-python = ">=3.11"` | `pyproject.toml` |
| 操作系统 | Windows（`win32`），bash 执行环境 | 会话环境 |
| 已安装关键依赖 | fastapi 0.148.0 / starlette 1.9.0 / uvicorn 0.54.0 / Jinja2 3.1.6 / httpx 0.28.1 / python-multipart 0.0.32 / pydantic 2.14.1 / pytest 9.1.1 | `.venv/Scripts/python.exe -m pip list` |
| 运行时依赖声明 | 5 项（fastapi、uvicorn[standard]、jinja2、python-multipart、httpx） | `requirements.txt` |
| 开发依赖 | `-r requirements.txt` + `pytest==9.1.1`（精确锁版本） | `requirements-dev.txt` |
| 配置文件 | 仅有 `.env.example`（18 项，全部有注释）；**无 `.env`**，当前运行在默认配置下 | `grep -c "^[A-Z_]*=" .env.example` → 18 |
| AI 密钥 | 未配置（默认空），因此本次所有 UI 冒烟均为本地模板路径 | `src/health_agent/config.py` 默认值 + 无 `.env` |
| 交付物 | `dist/personal-health-agent-0.1.2.zip`（242,305 B），sha256 与随包校验文件一致 | `sha256sum` = `a0160d86…4722a4`，与 `.zip.sha256` 相同 |

---

## 2. 版本管理与未提交修改

```
$ git rev-parse --is-inside-work-tree
fatal: not a git repository (or any of the parent directories): .git
```

- **项目不在 Git 管理下**（项目根与工作区根均无 `.git`）。
- 因此 T00 要求的"Git 状态 / 未提交修改"**无法通过版本控制核对**。替代证据：文件修改时间。今日（2026-10-20 00:00 之后）被改动的文件共 20 个，集中在 UI 层与文档（`ui/app.py`、`ui/charts.py`、`ui/static/app.css`、`templates/*`、`docs/04`、`docs/05`、`README.md`、`pyproject.toml`、`scripts/package.py`、`storage/preferences.py`），与 0.1.2 的 UI 落地范围吻合，未发现孤立/无来源的陌生文件。
- `.gitignore` 已正确排除 `data/`、`*.db`、`.env`、`*.png` 等敏感项——说明**曾经按有版本控制来设计**，但仓库本身缺失。
- **风险**：T01 起将开始改代码，没有基线快照意味着任何修改都不可回滚、也无法证明"只改了任务范围内的文件"。
- **建议（需用户批准，T00 未执行）**：在 T01 开工前 `git init` + 一次基线提交（`.gitignore` 已就绪，不会提交数据与密钥）。

---

## 3. 目录与模块

```
src/health_agent/            5,867 行（含 __init__）
├─ catalog.py                37 个指标唯一事实源（含 verification / range_bound）
├─ config.py                 环境变量 → Settings
├─ errors.py                 错误码与异常
├─ validation/               units.py（单位白名单+换算）、rules.py（单行校验）、result.py
├─ storage/                  db.py、migrations/001_init.sql、keys.py（逻辑键/去重键）、
│                            repository.py、lifecycle.py、alerts.py、preferences.py、exporting.py
├─ ingestion/                loader.py、preview.py（五桶）、batch.py（落库）
├─ analytics/                aggregate.py、baseline.py、quality.py
├─ rules/                    engine.py（4 条规则）
├─ ai_client/                base.py、http_client.py、cache.py、prompts.py、summarizer.py
├─ reports/                  builder.py（追溯）、format.py（展示层格式化）
└─ ui/                       app.py（32 路由）、auth.py、charts.py（SVG 几何）、
                             static/app.css、templates/（13 页 + _macros.html）
tests/                       3,393 行，17 个测试文件 + conftest.py
scripts/                     package.py（打包+自检）、gen_data_dictionary.py
docs/                        00–05 共 6 份 + preview/ 3 份静态页
test_data/                   sample_daily_metrics.json（21 行）、sample_with_issues.csv（10 类异常）
dist/                        0.1.2 交付包（zip + 解压副本 + bundle.md + VERIFY-PROMPT.md）
```

**依赖方向**（与 `docs/05-handoff.md` §3 一致，本次抽查未发现反向依赖）：
`ui → reports / ingestion → analytics / rules → storage → validation`。

**入口**：
- Web：`python -m health_agent.cli serve`（`cli.py:175`）
- CLI 子命令：`init / import / backup / restore / export / stats / conflicts / serve / passwd`（`cli.py:42–190`）

---

## 4. 关键数据链路（符号级证据）

### 4.1 导入 → 预览 → 确认 → 修订 → 去重

| 环节 | 符号 | 位置 |
|---|---|---|
| 读文件（JSON/CSV/TSV，含 BOM/GBK） | `decode_bytes()`、`load_rows()`、`_from_json()`、`_from_delimited()` | `ingestion/loader.py:34,60,75,103` |
| 五桶分桶（accepted/review/duplicates/conflicts/rejected） | `build_preview()`、`ImportPreview` | `ingestion/preview.py:166,69` |
| 落库（全部 pending） | `commit_import()` | `ingestion/batch.py:38` |
| 逻辑键 / 去重键（唯一实现） | `logical_key()`、`dedup_key()` | `storage/keys.py:32,55` |
| 确认 / 驳回 / 改值 | `confirm_record()`、`reject_record()`、`update_pending_value()` | `storage/lifecycle.py:48,81,99` |
| 版本化修正 | `revise_confirmed()` | `storage/lifecycle.py:142` |
| 撤销批次 / 批次全确认 | `revoke_batch()`、`confirm_batch()` | `storage/lifecycle.py:192,304` |
| 冲突裁决（保留旧值/采用新值） | `resolve_conflict()`、`get_conflict()` | `storage/lifecycle.py:244,237` |
| "同一槽位最多一条 confirmed" 的数据库强制 | `CREATE UNIQUE INDEX uq_records_confirmed … WHERE verification_status='confirmed'` | `storage/migrations/001_init.sql:77-78` |

### 4.2 统计 → 报告 → 追溯

| 环节 | 符号 | 位置 |
|---|---|---|
| 只读 confirmed 的取数 | `load_points()`（`verification_status='confirmed'`） | `analytics/aggregate.py:81` |
| 窗口聚合 / 覆盖率 | `summarize()`、`summarize_many()`、`weekly_buckets()`、`tracked_metrics()` | `analytics/aggregate.py:136,177,194,108` |
| 个人基线（中位数+MAD，IQR 兜底） | `compute_baseline()`、`save_baseline()` | `analytics/baseline.py:57,125` |
| 数字追溯结构 | `NumberRef`、`ReportPayload`、`build_daily()`、`build_weekly()` | `reports/builder.py:26,48,75,148` |
| 展示层格式化 | `format_number()` | `reports/format.py` |
| 4 条提醒规则 | `ALL_RULES`（sleep_deficit_trend / resting_hr_shift / baseline_deviation / data_gap） | `rules/engine.py:264` |
| 年龄适配标记 | `AGE_ADAPTATION_IMPLEMENTED = False` | `rules/engine.py:67` |

### 4.3 备份 / AI 回退

| 环节 | 符号 | 位置 |
|---|---|---|
| 备份（SQLite 一致性快照 + manifest 指纹） | `create_backup()`、`_snapshot()`、`_sha256_file()` | `backup.py:52,39,31` |
| 指纹校验 / 恢复 | `inspect_backup()`、`restore_backup()` | `backup.py:102,127` |
| 摘要生成（只发算好的摘要） | `build_summary()` | `ai_client/summarizer.py:43` |
| 缓存键 / 读缓存 | `_keys()`、`peek_cached()` | `ai_client/summarizer.py:110,117` |
| 改写 + 失败降级 | `rewrite_report()`（失败回退 `SOURCE_TEMPLATE`） | `ai_client/summarizer.py:129` |
| 调用日志与费用 | `_log_call()`（`cost_source` 固定 `unavailable`） | `ai_client/summarizer.py:71` |

---

## 5. 测试结果

### 5.1 实际命令与输出

```bash
$ .venv/Scripts/python.exe -m pytest
295 passed, 31 warnings in 19.88s
EXIT=0
```

| 指标 | 值 |
|---|---|
| 用例总数 | 295（`grep "^def test_" tests/*.py` = 286 个函数 + 9 个参数化用例） |
| 通过 | 295 |
| 失败 | 0 |
| 跳过 | 0（无 skip/xfail 标记） |
| 警告 | 31 |
| 退出码 | 0 |
| 耗时 | 19.88s |

### 5.2 分文件用例数

| 文件 | 用例 | 文件 | 用例 |
|---|---|---|---|
| test_rules.py | 31 | test_charts.py | 10 |
| test_ui.py | 30 | test_export_settings.py | 10 |
| test_ingestion.py | 29 | test_units.py | 8 |
| test_validation.py | 23 | test_docs.py | 4 |
| test_ai.py | 19 | test_ui_pages.py | 13 |
| test_lifecycle.py | 19 | test_backup.py | 13 |
| test_analytics.py | 18 | test_storage.py | 13 |
| test_catalog.py | 17 | test_config.py | 12 |
| test_reports.py | 17 | | |

### 5.3 警告构成（31 条）

| 数量 | 内容 | 来源 |
|---|---|---|
| 30 | `DeprecationWarning: The default date adapter is deprecated as of Python 3.12` | `analytics/aggregate.py:82`（由 `test_ui.py` 8 条 + `test_ui_pages.py` 22 条触发） |
| 1 | `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated` | `.venv/.../fastapi/testclient.py:1`（第三方，与本项目代码无关） |

### 5.4 其他实测（只读）

- **空库冒烟**（TestClient + 临时库，未触碰项目数据）：`/`、`/records`、`/imports`、`/conflicts`、`/reports`、`/alerts`、`/settings`、`/sleep`、`/trends`、`/chronos`、`/healthz`、`/export.csv` **全部返回 200**。
- **迁移可建库**（临时库 `/tmp` 外侧）：11 张表（records、conflicts、import_batches、alerts、ai_call_log、audit_log、baselines、report_cache、metrics_catalog、settings、schema_migrations），10 个显式索引，`metrics_catalog` 预置 37 条，`schema_migrations` 1 条。
- **指标注册表**：37 个指标，`range_bound` 4 个（`hrv_min_ms`/`hrv_max_ms`/`spo2_min_percent`/`spo2_max_percent`），`verification` 全部 `provisional`（37/37），`in_report` 为 True 20 / False 17。
- **交付包完整性**：`dist/personal-health-agent-0.1.2.zip` 的 sha256 与 `.zip.sha256` 完全一致。

---

## 6. 交付说明中的 11 条静态断言复核

`dist/.../00-START-HERE.md` §二 列了 11 条可执行断言，逐条实跑结果：

| # | 断言 | 实测 | 判定 |
|---|---|---|---|
| 1 | `rules/` 不出现 arrhythmia | 无输出 | ✅ |
| 2 | 无 `fillna(0)` | 无输出 | ✅ |
| 3 | `logical_key/dedup_key` 只在 `storage/keys.py` | 另有 `rules/engine.py:60` 的 `dedup_key()`（提醒去重键，语义不同） | ⚠️ 断言口径需澄清 |
| 4 | 源码无 `sk-` 字面量 | 无输出 | ✅ |
| 5 | 无同名测试函数 | 无重复 | ✅ |
| 6 | 无 `%→min` 换算 | `CONVERSIONS` 仅 `h→min`、`s→min`、`kJ→kcal` | ✅ |
| 7 | AI 层不把密钥写日志 | `ai_client/` 内无任何 `logging/logger/print` | ✅ |
| 8 | `rules/` 不出现"诊断" | `engine.py:110` 命中，是免责声明"仅作趋势观察，不构成诊断" | ⚠️ 断言会误报 |
| 9 | 无 chart.js/echarts/plotly | 无输出 | ✅ |
| 10 | `catalog.py` 出现 `in_report=False` 与断言 | 用位置参数 `False`，**不含 `in_report=` 字样**；不变式断言在 `catalog.py:192` | ⚠️ 断言会误报（数据正确） |
| 11 | `analytics/` 不读 pending | `quality.py:32` 命中 `pending`，但只用于"统计各状态计数" | ⚠️ 断言会误报 |

**结论**：11 条里 7 条直接通过，4 条为**断言写法与实现不符导致的误报**，不代表行为违规（已核对实际数据与不变式）。建议在 T01/T06 里修订这批断言，使其匹配结构而非字面。

---

## 7. UI 现状

| 项 | 现状 | 证据 |
|---|---|---|
| 框架 | FastAPI + Jinja2 服务端渲染（无前端框架、无构建链） | `ui/app.py:16-19` |
| 路由 | 32 个（GET 20 / POST 12），含 `/healthz` | `grep -c "@app\.\(get\|post\)"` → 32 |
| 页面 | `/`、`/records`、`/imports`、`/conflicts`、`/reports`、`/alerts`、`/settings`、`/sleep`、`/trends`、`/chronos`、`/login`、`/export.csv`、`/export.json` | `ui/app.py:153–768` |
| 模板 | 13 个页面 + `_macros.html`（共 14 个文件） | `ui/templates/` |
| 样式 | 单一 `app.css`，347 行；设计 token + 3 个断点（1279 / 1023 / 767） | `ui/static/app.css:314,321,339` |
| JS | **零**（模板内无 `<script>`，`static/` 下只有 app.css） | `grep -rn "<script" templates/` 无输出 |
| 图表 | 手写 SVG：`build_line` / `build_bars` / `build_band`（缺失断线、区间只画带） | `ui/charts.py:78,141,215` |
| 动效 | 仅在 `prefers-reduced-motion: no-preference` 下启用 | `app.css:309` |
| 鉴权 | PBKDF2 密码 + HMAC 会话签名；局域网模式强制要求密码 | `ui/auth.py:28,60`；`config.py` |
| 动态能力 | 时钟模式为 Demo：扇区点击 + 键盘可达，**无拖拽旋转、无动画** | `templates/chronos.html`；`docs/05-handoff.md` §2 |

---

## 8. 已确认问题（本次实测，未修改）

> 全部位于 T00 禁止修改范围内（业务代码/文档/依赖），仅登记，交由 T01/T02 或用户裁决。

### I-1（中）`date` 对象直接进 SQLite 参数，依赖 Python 默认日期适配器

- **现象**：31 条测试警告中 30 条来自此路径。
- **证据链**：`ui/app.py:561` 的 `_series(conn, metric, start: date, end: date)` 把 `date` 对象传给 `load_points()`；而 `analytics/aggregate.py:81` 的签名声明 `start: str, end: str`，实际收到 `date`（`baseline.py:67` 则正确传 `.isoformat()`）。`sqlite3` 用默认适配器把 `date` 转成 ISO 字符串，并在 Python 3.12+ 发出 `DeprecationWarning`。
- **影响**：当前**功能正常**；但默认适配器已被标记弃用，未来 Python 版本移除后会直接抛 `InterfaceError/TypeError`（趋势页与睡眠页会 500）。同时类型注解与实际传参不符，属于契约不一致。
- **建议**：T01 里统一在 `load_points()` 边界上 `str` 化（或在 `db.connect()` 注册适配器），并补一条 `-W error::DeprecationWarning` 的回归测试。

### I-2（低）`ui/app.py` 两处重复 import

- `ui/app.py:21` 与 `:22` 是同一句 `from ..ai_client.summarizer import …`；`:33` 与 `:34` 是同一句 `from ..reports.format import format_number`。
- 属于合并/粘贴残留，运行无影响，但会让"事实只有一处"这类约定失效（同 ERR-002 家族）。

### I-3（低）README 与交付说明存在重复内容与过期数字

- `README.md` 有 5 处成对重复行：`DEEPSEEK_TIMEOUT_SECONDS`/`DEEPSEEK_MAX_RETRIES` 表格行（157-160）、"无 HTTPS…"（230-231）、"AI 费用…"与"AI 通道…"（242-243 / 245-246）。
- `dist/.../00-START-HERE.md:98-99` 称"10 个模板 + 宏""295 条测试，8 个文件"；实测为 **14 个模板文件**、**17 个测试文件 + conftest**（用例数 295 是对的）。

### I-4（低）交付说明的静态断言有 4 条会误报

见 §6 的 #3 / #8 / #10 / #11。断言写的是字面匹配，实现用的是位置参数与否定式文案。

### I-5（中，口径）pytest 版本两处声明不一致

- `requirements-dev.txt`：`pytest==9.1.1`（精确锁）
- `pyproject.toml`：`dev = ["pytest>=8.0"]`
- 同一事实两个来源、且约束强度不同（与项目记忆 ERR-002 同类）。实际安装 9.1.1。

### I-6（中，环境）项目无版本控制

见 §2。这不是代码缺陷，但它使 T00 的"检查未提交修改"与后续任务的"只改范围内文件"都缺少可验证手段。

---

## 9. 未验证事项（不得视为通过）

| # | 项 | 原因 | 需要什么才能验证 |
|---|---|---|---|
| U1 | 真实华为截图数据链路 | `test_data/` 两份样例**全部为虚构**，项目内无真实截图/导出件 | 用户提供 1–2 张真实截图或导出文件 |
| U2 | 指标值域与设备字段核对 | 37 个指标全为 `provisional`，未与厂商字段对照 | 同上 + 逐项核对记录 |
| U3 | 平板真机（触控 ≥44px、无横向滚动、下载） | 无平板设备；只有服务端行为证据 | 真机 Chrome/华为浏览器走 6 个页面 |
| U4 | DeepSeek 真实接口与计费 | 未配置密钥；实现按 OpenAI 兼容形态假设；`cost_source` 固定 `unavailable` | 用户提供接口凭证与计费口径 |
| U5 | 满态 UI（有数据时的图表/报告/提醒） | 工作区库未初始化、无数据；本次仅验证空态 200 | 导入样例后人工走查（T01 可做） |
| U6 | 备份/恢复实测 | 本次只跑测试（`test_backup.py` 13 条通过），**未单独执行 `cli backup/restore`** | T02 现场跑一次并留指纹 |

> 说明：U5 的"空态 200"不等于"页面在真实数据下正确"；U6 的"测试通过"不等于"备份链在本机可用"。按任务包规则，以上均标"未验证"。

---

## 10. 已知风险（来自代码与文档，非本次新发现）

| 风险 | 依据 | 影响 |
|---|---|---|
| 逻辑键只到日期粒度 | `storage/keys.py:32`；`docs/05-handoff.md` §6 | 同来源同日第二条瞬时测量（早晚各一次血压）会进冲突流程，启用 P2 前必修 |
| 无 HTTPS、单密码 | `docs/03-tablet-access.md` | 只可本机/可信局域网使用 |
| AI 费用不可核 | `summarizer.py:71` | 只能记 `unavailable`，不能对外报"花了多少" |
| 睡眠目标无年龄适配 | `rules/engine.py:67` `AGE_ADAPTATION_IMPLEMENTED = False` | 文案已如实声明，但用户可能误读为个性化 |
| 导入/提醒/设置三页仍是 0.1.1 结构 | `docs/05-handoff.md` §2 | 与 `docs/04` 设计稿 §8 阶段 4 有差距，T03–T05 的起点 |
| 时钟模式无拖拽、无动画 | `templates/chronos.html` | T05 的主要工作量 |

---

## 11. 下一步建议（供审核决策，未执行）

1. **先解决 I-6（版本控制）**：T01 会改代码，没有 Git 就无法证明"只改了范围内文件"。建议授权 `git init` + 基线提交（`.gitignore` 已就绪）。
2. **T01 的优先项**（按证据强度排序）：
   - I-1 日期适配器（有 30 条警告做证据，且是未来必然断裂点）；
   - 复查 `revise_confirmed()` → 部分唯一索引在"先 supersede 再插入"路径下是否仍只留一条 confirmed（T01 必须验证项第 7 条）；
   - 复核 `pending` 能否经 `/trends`、`/sleep`、`/reports` 的任一路径进入统计（当前只在 `load_points()` 一处过滤，需确认没有第二条取数路径）。
3. **顺手项（低风险、可并入 T01）**：I-2 重复 import、I-3 文档过期数字、I-4 断言写法、I-5 pytest 口径。
4. **不要做的**：在 T01 里顺手重构 UI 或改数据库结构；T03–T05 的 UI 工作应以本报告 §7 的现状为起点，而不是以 `docs/04` 的设计稿为准（设计稿存在未落地章节）。

---

## 12. 本次实际执行的命令清单

```bash
# 盘点
ls -la ; find . -type f（排除 .venv/.pytest_cache）
cat requirements.txt requirements-dev.txt pyproject.toml .gitignore
git rev-parse --is-inside-work-tree                  # fatal: not a git repository
.venv/Scripts/python.exe -V                          # 3.13.14
.venv/Scripts/python.exe -m pip list

# 测试（未新增/未删改任何测试）
.venv/Scripts/python.exe -m pytest                   # 295 passed, 31 warnings, EXIT=0
.venv/Scripts/python.exe -m pytest --collect-only -q
grep -h "^def test_" tests/*.py | sort | uniq -d     # 无输出

# 结构
.venv/Scripts/python.exe -m health_agent.cli init --db <临时库>   # 建库成功，37 条指标
.venv/Scripts/python.exe -c "sqlite3 读 sqlite_master / pragma table_info"

# 冒烟（TestClient，临时库，未触碰项目 data/）
GET / /records /imports /conflicts /reports /alerts /settings /sleep /trends /chronos /healthz /export.csv

# 静态断言 11 条（见 §6）
grep -rn … src/（arrhythmia / fillna(0) / sk- / chart.js / 诊断 …）

# 文档一致性
grep -c "^[A-Z_]*=" .env.example                      # 18
sha256sum dist/personal-health-agent-0.1.2.zip        # 与 .sha256 一致
```

**未执行**：任何写业务代码、改依赖、改数据库结构、删文件、`git init`、启动常驻服务、调用真实 AI 接口。

---

## 13. 状态

- T00 已完成，状态标记为**待审核**。
- 已更新 `TASK_STATUS.md`（T00 行）与 `CHANGELOG_WORKBUDDY.md`（追加 T00 条目）。
- **未开始 T01**，等待用户/ChatGPT 审核与明确授权。
