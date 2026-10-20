# T06 — 最终回归与交付验收报告

- 日期：2026-10-20
- 版本：0.1.2（`pyproject.toml` 与 `__init__.__version__` 一致，有测试守着）
- 结论：**A. 可以交付**（附 §7 未验证事项）
- 状态：**待审核**

> 本轮同时完成用户 2026-10-20 追加要求："先做完所有功能，但给 UI 等留下可美化的接口，进行模块化"。
> 模块化是结构重构（行为不变，360 条测试全程保持绿色），不违反 T06"不增加新功能"的约束；
> 功能范围以 T00–T05 已批准内容为准。

---

## 1. 完整测试套件

| 项 | 值 |
|---|---|
| 命令 | `.venv/Scripts/python.exe -m pytest` |
| 总数 | **376** |
| 通过 | **376** |
| 失败 | 0 |
| 跳过 | 0（`grep -rn "pytest.mark.skip\|skipif\|xfail" tests/` 无输出） |
| 警告 | 1（starlette TestClient 的 httpx 弃用提示，第三方） |
| 退出码 | 0 |
| 耗时 | 38.68s |

### 与基线 295 的差异（+81，无删除/跳过/弱化）

| 任务 | 新增条数 | 测试文件 |
|---|---|---|
| T01 数据可靠性 | +16 | `test_t01_data_reliability.py`（5 个缺陷的复现 + 守门） |
| T02 安全/备份/AI | +13 | `test_t02_security_backup.py` |
| T04 Simple 模式 | +22 | `test_t04_simple_ui.py` |
| T05 时钟模式 | +14 | `test_t05_chronos.py` |
| 模块化守门 | +16 | `test_ui_modularity.py` |
| 合计 | +81 | 295 → 376 |

覆盖没有丢失：T04/T05 期间只**升级**了两条既有断言（`test_chronos_demo_page` → `test_chronos_page_structure`、
设置页"时钟模式（Demo）"文案），没有删除任何用例；`grep "^def test_" | uniq -d` 无重名。

## 2. 结构检查

### 2.1 NULL / 待确认隔离 / 单位 / HRV 区间 / 时间窗口 / 报告追溯

| 检查点 | 证据（测试名） |
|---|---|
| NULL 不填 0 | `test_status_no_data_keeps_stats_null`、`test_null_values_are_not_counted_as_samples` |
| 待确认不进统计 | `test_pending_records_never_reach_reports`、`test_pending_does_not_enter_chronos_preview` |
| 单位白名单与换算 | `test_units.py`（全量）、`test_validation.py` |
| HRV 区间无均值、不进报告 | `test_range_metric_never_feeds_average_metric`、`test_range_metrics_never_enter_report`、`test_range_bound_metrics_excluded_from_report` |
| 时间窗口口径 | `test_analytics.py` 窗口用例（7/28 天、覆盖率） |
| 报告数字可追溯 | `test_daily_numbers_are_all_traceable`（record_ids / derived_from） |

### 2.2 事务、重复、备份恢复、AI 回退

`test_lifecycle.py`（修订/冲突裁决）、`test_t01_data_reliability.py`（改值重算键、回滚）、
`test_t02_security_backup.py`（恢复失败保护、WAL 回放、上传上限、开放重定向）、`test_ai.py`（降级与缓存）。
全部通过。

### 2.3 Simple / Chronos 一致性与权限

- 数据一致性：`test_center_preview_matches_simple_dashboard_values`（同一数值字符串在两页出现）、
  `test_mode_switch_home_uses_same_dial_and_values`；代码层同源于 `ui/context.py::dashboard_tiles`。
- 权限一致性：`test_chronos_requires_login_when_password_set`、`test_pages_open_without_password_in_local_mode`。
- 写操作：`/chronos` POST → 405（`test_chronos_is_read_only`）。

### 2.4 环形导航的替代操作、减少动画、窄屏与触屏

- 替代操作：侧栏 10 页 + "普通入口"9 链接（`test_rendered_nav_contains_every_page_label`）。
- 减少动画：`test_reduced_motion_only_animates_when_allowed` + 浏览器实测 `transition-duration: 0s`。
- 窄屏：浏览器 600/768/800/900/1024/1440 六宽度 `scrollWidth == width`。
- 触屏：扇区 68px（≥44px）、按钮 min-height 44px、`.mini::after` 扩命中区（CSS 断言）。

### 2.5 安装 / 配置 / 迁移 / 备份恢复说明

`docs/02-runbook.md`（安装、启动、备份 §5）、`docs/03-tablet-access.md`（平板流程）、
`.env.example`（18 项全部有注释）、迁移 `001_init.sql` 幂等（`test_storage.py`）。

### 2.6 密钥与敏感日志

| 检查 | 结果 |
|---|---|
| 硬编码密钥扫描 `grep -rn "sk-[A-Za-z0-9]{10,}"` | 无匹配 |
| `Settings` 密钥字段 | `repr=False`（`config.py:53-54`） |
| 应用日志打印密钥 | 无（仅 `config.py` 读取与 `bool()` 判断） |
| 仓库包含 `.db/.env/node_modules/data/` | `git ls-files` 计数 0 |

### 2.7 工作区与版本

- Git：8 次提交，工作区在本次验收改动前干净；`*.png` 截图按 `.gitignore` 本地留档。
- 版本一致：`test_version_is_consistent_across_files` 通过。
- 旧版本可恢复：Git 历史 + `dist/` 既有 0.1.2 产物（**注意：dist 为 T04/T05 之前构建，需重新打包**）。

### 2.8 打包自检（真实执行，未覆盖发布产物）

`python scripts/package.py --out <系统临时目录>`：

- 110 文件 / 17,476 行 / 343KB zip
- 源码测试 **376 passed**；解压后自检 **376 passed**（证明包自洽、导入的是解压后的源码）
- 顺带修掉 `package.py` 两处重复代码块（self_check 导入探针、bundle 落盘各重复一次）

## 3. 测试类型分层（不混淆）

| 类型 | 是否执行 | 证据 |
|---|---|---|
| 自动化（pytest） | ✅ | 376 passed |
| 浏览器（桌面 Chrome 129 无头，puppeteer-core） | ✅ | `scripts/verify_t05_browser.js` 14/14 |
| 桌面手工点验 | ⚠️ 部分 | 通过截图与交互断言间接覆盖；未做真人逐页点验 |
| Android 模拟器/浏览器 | ❌ 未执行 | 无 Android 环境 |
| 真实 Android 平板 | ❌ 未执行 | 无设备 |

## 4. 模块化与可美化接口（用户追加要求）

| 交付 | 内容 |
|---|---|
| `ui/pages.py` | 页面注册表：导航 / 链接网格 / 表盘扇区的唯一事实源（新增页面 = 加一条） |
| `ui/context.py` | 视图模型 `Tile` / `Reference` / `Sector` + 装配函数；模板字段契约集中一处 |
| `ui/app.py` | 移除 4 个内联装配函数（−130 行），只留路由；`render()` 统一注入导航上下文 |
| `static/css/{tokens,base,components,pages}.css` | 样式四层：换肤只改 tokens；组件与页面分离 |
| `templates/base.html` | 扩展块 `styles / head / scripts / nav_extra / body_class`（加脚本、字体、主题的唯一入口） |
| `docs/06-ui-extension-guide.md` | 扩展手册：换肤、加组件、加页面、加指标、加规则、开 JS 的步骤与约束 |
| `tests/test_ui_modularity.py` | 16 条守门：注册表唯一、模板不许硬编码链接、样式分层不被揉回入口、扩展块存在、零 JS 基线 |

**行为不变证据**：重构前后全量测试均 376 passed；浏览器交互 14/14 与重构前一致；
新增一条页面只需在 `pages.py` 加一条 + 路由 + 模板（旧方式要改 3 处）。

## 5. 本轮实际改动文件

- 新增：`ui/pages.py`、`ui/context.py`、`static/css/{tokens,base,components,pages}.css`、
  `docs/06-ui-extension-guide.md`、`tests/test_ui_modularity.py`、本报告
- 修改：`ui/app.py`（抽离装配函数）、`templates/base.html`（注册表导航 + 扩展块）、
  `templates/chronos.html`（链接网格走注册表）、`tests/conftest.py`（`read_css`）、
  `tests/test_t04_simple_ui.py` / `test_t05_chronos.py`（CSS 断言改读分层）、
  `scripts/package.py`（去重）、`TASK_STATUS.md`、`CHANGELOG_WORKBUDDY.md`、`docs/05-handoff.md`
- 删除：无

## 6. 剩余风险

- R1：样式从 1 个文件变 5 个，浏览器多 4 个请求（本地回环，实测无感；未量化）。
- R2：`@import` 是串行加载，极端慢盘上首屏样式可能晚几十毫秒（本地应用，可接受）。
- R3：`app.css` 若被第三方工具直接编辑，会绕过分层约定——已由 `test_app_css_is_entry_only` 拦住。
- R4：`dist/` 产物落后于当前代码，交付前需重新 `python scripts/package.py`（会覆盖 dist，需授权）。

## 7. 未验证事项

1. 真实 Android 平板（华为浏览器）：触控、横竖屏、表盘动画帧率。
2. Safari / Firefox 渲染（只测了 Chrome 129）。
3. 屏幕阅读器播报顺序。
4. 真实华为截图全链路导入（数据均为程序生成）。
5. 真实 DeepSeek 接口（仅契约与降级实现）。

## 8. 结论

**A. 可以交付。**

阻断项：无。一般问题：无。未验证事项已在 §7 列明，不得对外宣称已验证。

> 交付前动作：批准后执行 `scripts/package.py` 重新生成 `dist/`（当前 dist 为 T04 之前的旧包）。

---

## 9. 修订记录

- **2026-10-20 22:00（用户裁决后）**：指标注册表移除血压两项（`blood_pressure_systolic_mmhg` /
  `blood_pressure_diastolic_mmhg`），执行理由：用户设备测压不准，不采集该项。
  数据字典重新生成（35 个指标），新增回归测试 `test_blood_pressure_metrics_are_removed`；
  全量测试 **378 passed**（376 + 血压移除守门 + 移动端兼容守门）。
  同一轮为联想平板/联想浏览器补充移动端兼容规则（`text-size-adjust`、`touch-action`、
  `100dvh`、`aspect-ratio` 兜底），见 `docs/03-tablet-access.md` §5。
