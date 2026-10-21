# Work Buddy 变更日志

## 记录模板

### YYYY-MM-DD — Txx — 任务标题
- 修改文件：
- 新增文件：
- 删除文件（必须解释原因，默认不允许）：
- 执行命令：
- 测试结果（总数/通过/失败/跳过/警告/退出码）：
- 代码证据：
- 未验证项目：
- 剩余风险：
- 状态：待审核

---

## 记录

### 2026-10-20 — T00 — 项目基线盘点

- **修改文件**：无（业务代码、数据库结构、依赖、测试均未改动）
- **新增文件**：
  - `reports/T00_BASELINE_REPORT.md`（基线盘点报告）
  - `TASK_STATUS.md`（项目根，此前不存在；按任务包模板建立）
  - `CHANGELOG_WORKBUDDY.md`（本文件，此前不存在；按任务包模板建立）
- **删除文件**：无
- **执行命令**：
  - `git rev-parse --is-inside-work-tree` → `fatal: not a git repository`（无版本控制）
  - `.venv/Scripts/python.exe -V` → Python 3.13.14
  - `.venv/Scripts/python.exe -m pip list` → fastapi 0.148.0 / starlette 1.9.0 / uvicorn 0.54.0 / Jinja2 3.1.6 / httpx 0.28.1 / python-multipart 0.0.32 / pytest 9.1.1
  - `.venv/Scripts/python.exe -m pytest` → **295 passed, 31 warnings in 19.88s, EXIT=0**
  - `.venv/Scripts/python.exe -m pytest --collect-only -q`（收集 295 用例）
  - `grep -h "^def test_" tests/*.py | sort | uniq -d` → 无输出（无同名测试）
  - `.venv/Scripts/python.exe -m health_agent.cli init --db <临时库>` → 建库成功，`metrics_catalog` 37 条
  - TestClient 冒烟：12 个路径（10 个页面 + `/healthz` + `/export.csv`）全部返回 200
  - 11 条静态断言逐条执行（结果见报告 §6）
  - `sha256sum dist/personal-health-agent-0.1.2.zip` → 与 `.zip.sha256` 一致
- **测试结果**：总数 295 / 通过 295 / 失败 0 / 跳过 0 / 警告 31 / 退出码 0
- **代码证据**：
  - 版本一致：`pyproject.toml:7` 与 `src/health_agent/__init__.py:10` 均为 `0.1.2`
  - 唯一索引：`storage/migrations/001_init.sql:77-78`（`uq_records_confirmed … WHERE verification_status='confirmed'`）
  - 只读 confirmed：`analytics/aggregate.py:81`（`load_points`）
  - 指标目录：`catalog.py` 37 个指标 / 4 个 range_bound / 37 个 provisional
  - 规则：`rules/engine.py:264`（`ALL_RULES` 4 条）
  - 年龄适配：`rules/engine.py:67`（`AGE_ADAPTATION_IMPLEMENTED = False`）
- **已确认问题（仅记录，未修复）**：
  - I-1 `date` 对象直通 SQLite 参数（`ui/app.py:561` → `analytics/aggregate.py:81`），触发 30 条 Python 3.12+ 弃用警告
  - I-2 `ui/app.py:21/22`、`:33/34` 两处重复 import
  - I-3 `README.md` 5 处成对重复行；`dist/.../00-START-HERE.md` 模板数与测试文件数过期
  - I-4 交付说明 4 条静态断言写法会误报（#3/#8/#10/#11）
  - I-5 `pytest` 版本两处口径不一致（`requirements-dev.txt` 精确锁 9.1.1 vs `pyproject.toml` `>=8.0`）
  - I-6 项目无 Git 仓库
- **未验证项目**：真实华为截图数据、值域与设备字段核对（37 个全 provisional）、平板真机、DeepSeek 真实接口与计费、满态 UI（库内无数据）、`cli backup/restore` 现场实测
- **剩余风险**：逻辑键仅到日期粒度；无 HTTPS 单密码；AI 费用记 `unavailable`；导入/提醒/设置三页仍为 0.1.1 结构；时钟模式无拖拽
- **状态**：待审核

### 2026-10-20 — T01 — 数据可靠性检查与必要修复

- **修改文件**：
  - `src/health_agent/storage/lifecycle.py`（D1 改值重算 dedup_key；D2 修订保留 captured_at；D5 修订事务回滚；D4 撤销批次先清悬空冲突 + 事务回滚）
  - `src/health_agent/analytics/aggregate.py`（D3 `load_points` 收窄 `str|date`，新增 `_iso_arg()`）
- **新增文件**：
  - `tests/test_t01_data_reliability.py`（16 条：8 复现 + 8 守门）
  - `reports/T01_DATA_RELIABILITY_REPORT.md`
- **删除文件**：无（仅清理本次测试产生的 4 个 pytest 临时目录）
- **执行命令**：
  - `.venv/Scripts/python.exe -m pytest tests/test_t01_data_reliability.py` → 修复前 7 条失败（复现确认）
  - `.venv/Scripts/python.exe -m pytest --basetemp=<新目录>` → **311 passed, 1 warning, EXIT=0**（修复后）
  - `validate_row` 实测 5 张截图 19 个观测值 → 0 拒绝
  - `grep -h "^def test_" tests/*.py | sort | uniq -d` → 无重复测试名
  - TestClient 冒烟 `/` `/sleep` `/trends` `/reports` `/chronos` → 全部 200
- **测试结果**：311 passed / 0 failed / 0 skipped / 1 warning / EXIT=0（修复前基线 295 / 31 warnings）
- **代码证据**：
  - `lifecycle.py::update_pending_value` 现在重算 `dedup_key`（`storage/keys.py:55` 的键包含 value）
  - `lifecycle.py::revise_confirmed` 传 `captured_at=row["captured_at"]`，整体 `try/except → rollback`
  - `lifecycle.py::revoke_batch` 删除 pending 前先 `DELETE FROM conflicts WHERE incoming/ existing IN (待删)` + `audit_log`
  - `aggregate.py::load_points` 用 `_iso_arg()` 把 `date` 转 ISO 字符串，不再依赖已弃用的默认日期适配器
- **已确认并修复的缺陷**：D1 dedup_key 不刷新 / D2 丢失 captured_at / D3 date 适配器弃用 / D4 含冲突批次撤销失败（外键）/ D5 修正失败留半截状态
- **未验证项目**：并发去重原子性、`resolve_conflict` 同类回滚（留 T02）、值域"不误收"（缺华为官方字段说明）、真实截图完整导入链路
- **剩余风险**：逻辑键仍只到日期粒度（P2 前置）；D5 回滚测试为修复后补写（未在修复前实跑）；无 Git 仓库
- **状态**：待审核

### 2026-10-20 — T02 — 安全、备份与 AI 回退

- **修改文件**：
  - `src/health_agent/backup.py`（`_validate_payload` 校验 SQLite magic；`restore_backup` 改为临时文件校验 + 删旧 `-wal`/`-shm` + `os.replace` 原子替换；`_discard` 清理失败不掩盖真实错误）
  - `src/health_agent/ui/app.py`（`MAX_UPLOAD_BYTES`=10MB，上传超限不落盘；`_safe_next()` 修开放重定向）
- **新增文件**：
  - `tests/test_t02_security_backup.py`（13 条：8 复现 + 5 守门）
  - `reports/T02_SECURITY_BACKUP_AI_REPORT.md`
- **删除文件**：无（仅清理本次测试产生的 8 个临时目录）
- **执行命令**：
  - `.venv/Scripts/python.exe -m pytest tests/test_t02_security_backup.py` → 修复前 8 条失败（复现确认）
  - `.venv/Scripts/python.exe -m pytest --basetemp=<新目录>` → **324 passed, 2 warnings, EXIT=0**
  - `grep -rn "sk-[A-Za-z0-9]\{10,\}" src/ scripts/ tests/ .env.example` → 无密钥字面量
  - `grep -rn arrhythmia src/health_agent/ui/ src/health_agent/rules/` → 无引用
  - TestClient 实测：`POST /login next=//evil.example` → 修复前 Location `//evil.example`，修复后 `/`
- **测试结果**：324 passed / 0 failed / 0 skipped / 2 warnings（均为第三方 starlette 弃用提示）/ EXIT=0
- **代码证据**：
  - `backup.py:145 _validate_payload()`：magic `SQLite format 3\0` 不符即拒，坏包不落盘
  - `backup.py:220`：替换前删除 `health.db-wal` / `health.db-shm`
  - `backup.py:224`：`os.replace(staged, db_path)` 原子替换
  - `ui/app.py:52 _safe_next()`：只放行 `/` 开头、非 `//`、不含 `\` 的路径
  - `ui/app.py:358`：`await file.read(MAX_UPLOAD_BYTES + 1)` 判超限
- **已确认并修复**：S1 开放重定向（中）/ S2 上传无上限（低-中）/ S3 恢复失败毁库（高）/ S4 陈旧 WAL 污染恢复结果（高）
- **静态审查风险（未修，仅记录）**：`deepseek_base_url` 未强制 https；manifest 同包自哈希（防意外不防伪造）；`chmod 0o600` 在 Windows 无效；`.bak` 与暂存文件名秒级碰撞；`config.py` 参数校验块重复；撤销失败回显 SQLite 原文
- **未验证项目**：真实渗透测试、局域网真机、Windows 文件锁下 `os.replace` 失败路径、真实 DeepSeek 接口、恢复时存在并发写
- **剩余风险**：恢复必须在服务停止时进行（代码报错但不阻止）；备份完整性不防伪造；`resolve_conflict` 仍缺事务回滚（T01 遗留）
- **状态**：待审核

### 2026-10-20 — 基础设施 — 初始化 Git 仓库（用户授权）

- **修改文件**：`.gitignore`（新增 `dist/` 排除项，发布产物可由 `scripts/package.py` 重新生成）
- **新增文件**：`.git/`（仓库本体）；首次提交 `060e538`
- **删除文件**：无
- **执行命令**：
  - `git init -b main` → 仓库初始化（此前 `git rev-parse --is-inside-work-tree` 报 not a git repository）
  - `git add -A` → 暂存 101 个文件
  - `git ls-files --cached | grep -c -E "\.db$|\.env$|^data/|^dist/|\.png$"` → **0**（敏感文件零泄露）
  - `git commit` → `[main (root-commit) 060e538] 101 files changed, 16563 insertions(+)`
- **测试结果**：不涉及（纯版本控制操作，未触碰业务代码）
- **代码证据**：提交内容 = T02 修复后全量状态（src 54 文件 + tests 23 文件 + docs 9 + reports 3 + 根目录 4 + test_data 2）
- **未验证项目**：未配置 remote（本地仓库）；未演练后续 `git diff`/回滚工作流
- **剩余风险**：CRLF 警告（Windows 换行符归一化，git 自动处理，无功能影响）
- **状态**：已生效（用户 2026-10-20 授权"可以"）

### 2026-10-20 — T03 — 双 UI 设计方案与 Galaxy 选型

- **修改文件**：`TASK_STATUS.md`（T03 待审核）；`CHANGELOG_WORKBUDDY.md`（本记录）
- **新增文件**：`reports/T03_UI_DESIGN_GALAXY_REPORT.md`（设计交付物）
- **删除文件**：无
- **执行命令**：
  - 通读 `tasks/T03_UI_DESIGN_GALAXY.md`、`references/GALAXY_UI_REFERENCE.md`、`references/DESIGN_BRIEF.md`、`docs/04-ui-design-spec-v1.md`
  - 核对现状：`app.css`（347 行 token 与断点）、`base.html`/`index.html`/`chronos.html`/`settings.html`/`_macros.html`、32 条路由、`analytics/baseline.py::compute_baseline` 签名与 insufficient 分支
  - `grep 参照|基线|reference` 模板目录 → 确认"参照范围"行尚不存在（用户 10-20 裁决的新增组件）
  - WebFetch `github.com/uiverse-io/galaxy` → 实证：MIT（LICENSE 2023-10-08）、署名"非强制但建议创作者+Uiverse"、CSS/Tailwind 双实现、11 个类别目录
- **测试结果**：本任务零代码修改；实测全量 **324 passed / 2 warnings / EXIT=0**（`--basetemp=/tmp/phealth-t03-run5`，按 ERR-017 用全新目录名）。2 warnings = starlette TestClient 弃用提示 + `app.py:55` 文档字符串 `\e` 转义 SyntaxWarning（T02 遗留，T02 记录中"均为第三方"说法不准确，特此更正；修复留 T04，改原始字符串即可）
- **代码证据**：
  - Galaxy 选型结论：**0 个组件复制代码、0 新依赖、0 构建变更**（报告 §3.2 逐类七字段评估表）
  - "参照范围"行契约：数据源 `compute_baseline(..., window_days=28, min_samples=14, mad_factor=2.5)`，insufficient/no_data/range_bound 三态各有规定文案（报告 §2.4）
  - Chronos 结构契约：表盘 6 扇区 ↔ 六页、中心预览与 Simple 同值、拖动旋转维持"未实现"、reduced-motion 已有包裹（`app.css:309-312`）
- **已确认并记录的不一致**：任务书"Chronos 深蓝/深色" vs 用户 10-20 裁决"默认浅色、不实现深色主题" vs 现有 Demo 浅色 → 列为待裁决 D-1，默认按裁决走浅色，深色 token 集备用
- **未验证项目**：元素级 Galaxy 选型与创作者署名（采用后置门槛）、真实平板触测、reduced-motion 实机、U5 基线参数、D-1/D-3 裁决
- **剩余风险**：R1 U5 未定导致参照范围口径浮动；R4 基线查询进渲染路径的性能（已设 50ms 预算与降级文案）
- **状态**：待审核（等 D-1/D-2 裁决后进 T04）

---

### 2026-10-20 — T04 — Simple 简洁模式实现

- **修改文件**（12 个，+407/−128）：
  - `ui/app.py`：新增 `_reference()` 只读装配参照范围；三页接入；提醒页接入规则筛选；设置页接版本信息；清理重复 import / 重复 human 过滤器；`_safe_next` 文档字符串改 raw（修 T02 遗留 SyntaxWarning）
  - `storage/alerts.py`：`list_alerts()` 增加可选 `rule_id`（默认行为不变）；新增 `list_rule_ids()`
  - `config.py`：删除尾部 5 条逐字重复的校验
  - 模板 8 个：`_macros.html`（+6 宏）、`index/sleep/trends/import_preview/imports/alerts/settings/base.html`
  - `ui/static/app.css`：参照范围行、三步条、提醒双列、`--focus-ring`、触控高度 ≥44px
- **新增文件**：
  - `reports/T04_SIMPLE_UI_REPORT.md`（本任务报告）
  - `docs/UI-ATTRIBUTION.md`（Galaxy 0 复制结论 + 采用登记表 + 后置七门槛）
  - `tests/test_t04_simple_ui.py`（22 条）
- **删除文件**：无
- **执行命令**：
  - `.venv/Scripts/python.exe -m pytest` → **346 passed, 1 warning, EXIT=0**（324+22）
  - `.venv/Scripts/python.exe -m pytest tests/test_t04_simple_ui.py -q` → 22 passed
  - `grep -h "^def test_" tests/*.py | sort | uniq -d` → 无输出
  - 计时脚本（28 天 × 5 指标，140 条已确认，TestClient 5 次均值）：`/` 26.4ms / `/sleep` 26.0ms / `/trends` 26.0–27.7ms / `/settings` 28.3ms
  - 真实 uvicorn + urllib/httpx：8 页 + 静态 CSS + multipart 上传预览全部 200、标记齐全、服务端日志无 ERROR/WARNING
- **测试结果**：总数 346 / 通过 346 / 失败 0 / 跳过 0 / 警告 1（starlette 第三方弃用）/ 退出码 0
- **代码证据**：
  - 参照范围数据源唯一：`ui/app.py::_reference` → `analytics/baseline.py::compute_baseline`（中位数+MAD，IQR/1.349 兜底且方法名如实展示）
  - 阈值只读无假控件：`settings.html` 阈值表 + 测试断言 6 个 `name=` 不在页面
  - 偏离基线中性标记：`_macros.html::stat_card` 的 `.ref-dev`（CSS 取 `--text-2`，测试断言无 danger/warn）
  - 待确认隔离不变：统计仍走 `load_points`（只读 confirmed），导入预览明示"写入后一律 pending"
- **未验证项目**：平板真机（触控/宽度/横滚）、真实华为截图全链路、浏览器关 JS 实测、规则筛选下拉在规则增多后的分组
- **剩余风险**：R1 首页指标卡对区间指标不显示参照行（设计如此，待用户确认是否要提示）；R2 空库时规则下拉仅"全部规则"；R3 `.mini::after` 热区相邻重叠未触屏实测
- **状态**：待审核（通过后进 T05 Chronos 模式实现）

---

### 2026-10-20 — T05 — 时钟模式（Chronos）实现

- **修改文件**（5 个，+216/−86）：
  - `ui/templates/chronos.html`：重写为零 JS 两步交互（6 radio + 6 label 扇区、6 中心预览面板、6 个「进入」链接），去掉 Demo 徽标
  - `ui/static/app.css`：选中高亮/环旋转/标签反旋/面板显示（纯兄弟选择器，不用 `:has`）；`.dial { overflow: hidden }` 裁旋转扫角；过渡仅在 `prefers-reduced-motion: no-preference`；`sector-input` opacity 0 + pointer-events none；删除 `badge-demo`
  - `ui/app.py`：新增 `_dashboard_tiles()`（首页与表盘共用同一取数/格式化）与 `_chronos_sectors()`；`/chronos` 与首页 chronos 分支传同套上下文（修复首页 chronos 分支未传 tiles/sectors 的真实缺陷）
  - `ui/templates/settings.html`：模式选项"时钟模式（Demo）"→"时钟模式"
  - `tests/test_ui_pages.py`：`test_chronos_demo_page` 升级为 `test_chronos_page_structure`；模式文案断言同步
- **新增文件**：
  - `reports/T05_CHRONOS_UI_REPORT.md`
  - `tests/test_t05_chronos.py`（14 条）
  - `scripts/verify_t05_browser.js`（可选浏览器验证脚本，puppeteer-core + 本机 Chrome，非运行时依赖）
- **删除文件**：无
- **执行命令**：
  - `.venv/Scripts/python.exe -m pytest` → **360 passed, 1 warning, EXIT=0**（346+14）
  - `NODE_PATH=… node scripts/verify_t05_browser.js` → **14/14 passed**（无头 Chrome 129，1440×900；600–1440px 宽度扫描）
  - 计时脚本（28 天 × 4 指标）：`/chronos` 25.8ms、`/` 26.2ms（TestClient 5 次均值）
- **测试结果**：自动化 360 passed / 0 failed / 1 warning；浏览器 14/14；真机未测
- **代码证据**：
  - 同口径：`ui/app.py::_dashboard_tiles` 被 `/`（chronos 分支）与 `/chronos` 同时调用；测试断言两页出现同一格式化字符串
  - 降级：过渡只在 `@media (prefers-reduced-motion: no-preference)`；`transition-duration=0s`（Chrome 实测）
  - 两步交互：`<label class="sector">` 无 href，进入只能点 `.core-enter`
- **浏览器验证中发现并修复的缺陷**：
  - 旋转包围盒溢出：方形环 rotate(120deg) 包围盒 1.366 倍（768→1049px），800px 下 scrollWidth=925 → `.dial{overflow:hidden}` 后各宽度均无溢出
  - 焦点环错位：隐藏 radio 不随环旋转，默认焦点框画在未旋转位置 → radio `opacity:0;pointer-events:none`，焦点环改画在 label 上
- **未验证项目**：真实 Android 平板（华为浏览器）触控与横竖屏、Safari/Firefox 渲染、屏幕阅读器播报、强制颜色模式
- **剩余风险**：R1 低端平板旋转掉帧；R2 `overflow:hidden` 裁掉悬停外缘 3px；R3 第 6 扇区切换可能"绕远路"
- **状态**：待审核（通过后进 T06 最终回归）

---

### 2026-10-20 — T06 — 最终回归与交付验收（结论 A）+ 模块化重构

- **修改文件**：
  - `ui/app.py`：移除 4 个内联装配函数（`_metric_labels/_reference/_dashboard_tiles/_chronos_sectors`，约 −130 行），改为引用 `ui/context.py`；`render()` 统一注入 `nav_groups/link_pages/dial_pages`
  - `ui/templates/base.html`：导航改为遍历注册表；新增 `styles/head/scripts/nav_extra/body_class` 扩展块；样式入口保持 `/static/app.css`
  - `ui/templates/chronos.html`：「普通入口」改遍历 `link_pages`
  - `ui/static/app.css`：改为 4 行 `@import` 入口
  - `tests/conftest.py`：新增 `read_css()`（读五层样式）
  - `tests/test_t04_simple_ui.py`、`tests/test_t05_chronos.py`：CSS 断言改读分层（断言内容不变）
  - `scripts/package.py`：删除两处重复代码块（self_check 导入探针、bundle 落盘）
  - `TASK_STATUS.md`、`docs/05-handoff.md`：状态与交接同步
- **新增文件**：
  - `ui/pages.py`（页面注册表：导航/链接网格/表盘唯一事实源）
  - `ui/context.py`（视图模型 Tile/Reference/Sector + 装配函数）
  - `ui/static/css/{tokens,base,components,pages}.css`（样式四层）
  - `docs/06-ui-extension-guide.md`（换肤/加组件/加页面/加指标/加规则/开 JS 手册）
  - `tests/test_ui_modularity.py`（16 条结构守门）
  - `reports/T06_FINAL_ACCEPTANCE_REPORT.md`
- **删除文件**：无
- **执行命令**：
  - `.venv/Scripts/python.exe -m pytest` → **376 passed, 1 warning, EXIT=0**（295 基线 +81，无跳过/删除）
  - `.venv/Scripts/python.exe -m pytest --collect-only -q` → 逐文件条数（23 个文件）
  - `NODE_PATH=… node scripts/verify_t05_browser.js` → **14/14 passed**（CSS 分层后复跑）
  - `python scripts/package.py --out <系统临时目录>` → 110 文件 / 343KB；源码 376 passed；**解压自检 376 passed**
  - 浏览器 CSS 加载检查：`/static/app.css` + 4 个分层文件全部 200；`--primary=#0F3D5C`、侧栏 240px、卡片圆角 12px 生效
  - 密钥扫描 `grep -rn "sk-[A-Za-z0-9]{10,}" src/ tests/ docs/` → 无输出
  - `git ls-files | grep -cE "\.db$|\.env$|node_modules|^data/"` → 0
- **测试结果**：总数 376 / 通过 376 / 失败 0 / 跳过 0 / 警告 1（第三方）/ 退出码 0
- **代码证据**：
  - 页面清单唯一：`ui/pages.py::PAGES`；`test_base_template_loops_registry_instead_of_hardcoding` 禁止模板写死 href
  - 样式分层：`test_app_css_is_entry_only`（入口只允许 @import）、`test_tokens_layer_has_no_rules_beyond_root`
  - 扩展点：`test_base_template_keeps_extension_blocks`（styles/head/scripts/nav_extra/body_class）
  - 零 JS 基线：`test_templates_do_not_contain_inline_scripts`
- **未验证项目**：真实 Android 平板、Safari/Firefox、屏幕阅读器、真实截图全链路、真实 DeepSeek 接口
- **剩余风险**：R1 样式拆 5 文件多 4 个请求（本地回环）；R2 @import 串行加载；R3 直接改 app.css 会绕过分层（有测试拦）；R4 dist/ 落后于代码，需授权后重新打包
- **状态**：T06 待审核，结论 **A（可以交付）**

---

### 2026-10-20 — 交付：重新打包 + 工作台前置清单（按用户授权执行）

- **触发**：用户"确认还有哪些任务，以及还需要哪些前置才能搭建工作台。给予授权"。
- **执行**：
  - `python scripts/package.py` → `dist/personal-health-agent-0.1.2.zip` 343KB / 110 文件 / 17,482 行，
    sha256 `ded193bd7e9442f9feb01419d0c04a87f542d82417338e6b667ebf01ef21a6b9`；
    源码测试 376 passed，**解压自检 376 passed**
  - 解压产物到系统临时目录实测搭建链路：`cli init`（37 指标）→ `cli import`（预览 → --commit 入库 6 条 pending、拒绝 4）
    → `cli backup`（zip + manifest）→ `cli serve`：`/ /sleep /trends /chronos /records /imports /alerts /settings /healthz /export.csv /static/css/tokens.css` 全部 200
- **新增文件**：`docs/07-workbench-checklist.md`（前置条件 10 项含现状、真实数据字段格式、产物试用步骤、5 分钟验收清单、剩余任务分类）
- **修改文件**：`TASK_STATUS.md`（发布与工作台状态）、`docs/02-runbook.md`（顶部指向清单）
- **测试结果**：376 passed ×2（源码 + 解压）；未改任何业务代码
- **未验证**：依赖全新安装路径（复用现有 venv，未重装依赖）；平板真机；真实截图数据
- **状态**：T06 仍待审核；发布包已更新

---

### 2026-10-20 — 产品范围变更：移除血压指标 + 联想平板/浏览器兼容

- **触发**：用户裁决"血压观测可以先删除，因为我的设备测的不准"；并提供平板型号
  （联想 Yoga Pad Pro 12.7 元启版，优先适配联想浏览器）。
- **修改文件**：
  - `catalog.py`：删除 `blood_pressure_systolic_mmhg` / `blood_pressure_diastolic_mmhg`（含注释说明裁决），指标 37 → 35
  - `docs/01-data-dictionary.md`：由脚本重新生成（35 个指标）
  - `static/css/base.css`：`text-size-adjust: 100%`（防 Android 字体放大）、`-webkit-tap-highlight-color`、`touch-action: manipulation`、`100dvh` 兜底
  - `static/css/pages.css`：`@supports not (aspect-ratio: 1)` 表盘高度兜底（老内核不塌成 0 高）
  - 文案同步：`README.md`、`docs/00`、`docs/02`、`docs/05`、`docs/07`、`scripts/gen_data_dictionary.py`、`scripts/package.py`（"血压"示例改"体温/体重"；37 → 35）
  - `docs/03-tablet-access.md`：新增 §5 联想 Yoga Pad Pro 12.7 + 联想浏览器（三项设置、内核判定、十项验证表、已知风险）
  - `docs/07-workbench-checklist.md`：顶部加入"搭建由用户发起，未经确认不启动"；平板行落到具体型号；"明确不做"补不采集血压
  - `reports/T06_FINAL_ACCEPTANCE_REPORT.md`：追加修订记录
- **新增测试**：`test_blood_pressure_metrics_are_removed`（注册表不得复活血压）、
  `test_mobile_browser_compatibility_rules`（五条兼容规则存在）
- **执行命令**：
  - `.venv/Scripts/python.exe -m pytest` → **378 passed, 1 warning, EXIT=0**
  - `.venv/Scripts/python.exe scripts/gen_data_dictionary.py` → 35 个指标
  - `.venv/Scripts/python.exe scripts/package.py` → 111 文件 350KB，sha256 `23ea3d88…`，源码与解压自检均 378 passed
- **测试结果**：378 passed / 0 failed / 0 skipped / 1 warning（第三方）
- **未验证**：联想浏览器真机内核版本与实际渲染；12.7 英寸横竖屏；字体放大一档的实际溢出情况
- **剩余风险**：已有数据库的 `metrics_catalog` 会残留两行血压记录（不影响使用；全新 init 不会出现）
- **状态**：T06 仍待审核（报告已附修订记录）

---

### 2026-10-21 — 延伸 — 界面内配置 AI 接口（DeepSeek 及其它 OpenAI 兼容服务）

- **触发**：用户"在操作界面增设 API 接口适配 DeepSeek 等接入"。
- **新增文件**：
  - `src/health_agent/ai_client/probe.py`：试连探针（一句提示 / 16 tokens / 0 重试；
    4xx 翻成人话；永不抛异常，也不回显密钥与响应体）
  - `tests/test_ai_config.py`：29 条回归（预设解析、`.env` 往返、留空 vs 清空语义、
    密钥不上页面、GET 零网络、试连成败、CSRF/鉴权、`Settings` repr 不含密钥）
- **修改文件**：
  - `config.py`：`AI_PRESETS`（DeepSeek / 通义千问 / Kimi / 智谱 / OpenAI / Ollama 六家预设）、
    `preset_by_key()`、`active_preset_key()`、`_first()`（`AI_*` 优先、`DEEPSEEK_*` 兼容）、
    Settings 字段 `deepseek_*` → `ai_*`、新增 `env_path` / `ai_key_tail`、
    `save_ai_config()`（原子写回 `.env`，保留其它行，`None`=不改 / `""`=删除，0600）、
    `ai_env_override_keys()`（只回键名不回值）
  - `ai_client/summarizer.py`、`cli.py`：跟随字段改名
  - `ui/app.py`：`GET /settings` 增加 AI 上下文；新增 `POST /settings/ai`
    （`action=save` 写回并立即重新装载；`action=test` 只试不存）
  - `ui/templates/settings.html`：新增「AI 分析接口」卡片（预设下拉、地址、模型、
    password 型密钥输入、清除勾选、保存 / 测试连接两个按钮、服务商参考地址折叠区）
  - `ui/static/css/components.css`：`.form-actions` 与设置页 `details` 排版
  - `.env.example`、`README.md`、`docs/02-runbook.md`（新增 §7.5）、`docs/06-ui-extension-guide.md`
    （新增 §7.5 可写配置判据）、`docs/07-workbench-checklist.md`、`docs/08-workbench-overview.md`
  - `pyproject.toml` / `__init__.py`：0.1.3 → **0.2.0**
- **关键取舍**：
  - 零 JS 下选预设没法自动填表，因此约定"地址与模型**留空即取预设值**"，填了以填的为准；
  - 保存后立即生效（路由闭包读同一个 `settings` 名字，`app.state.settings` 同步更新），
    不用重启；
  - 进程环境变量仍优先于 `.env`，被压住时页面明说是哪几个变量——不然用户会以为修改丢了；
  - 密钥语义三分：填新值=覆盖、勾"清除"=删除、都空=不动。**绝不把"没填"当"清空"**。
- **执行命令**：
  - `.venv/Scripts/python.exe -m pytest -q` → **491 passed, 1 skipped, 0 failed**（共 492 条）（跳过项为 Windows 不适用的 chmod 断言）
- **未验证**：真实密钥的端到端连通（无可用密钥）；Ollama 本地实例；各厂商非 OpenAI 兼容的私有协议
- **剩余风险**：`ai_base_url` 未强制 https（保留 `http://127.0.0.1` 本地代理的合法用法，沿用 T02 裁决）
- **状态**：待审核
