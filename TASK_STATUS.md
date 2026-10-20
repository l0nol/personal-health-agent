# 任务状态表

状态只允许：未开始 / 进行中 / 待审核 / 已批准 / 阻塞 / 未验证。

| 任务 ID | 任务 | 状态 | 证据/报告位置 | 审核备注 |
|---|---|---|---|---|
| T00 | 基线盘点 | 已批准 | `reports/T00_BASELINE_REPORT.md` | 用户 2026-10-20 08:52 批准（"开始下一步 T01"） |
| T01 | 数据可靠性检查与必要修复 | 已批准 | `reports/T01_DATA_RELIABILITY_REPORT.md` | 用户 2026-10-20 13:46 批准（"继续02"） |
| T02 | 安全、备份与 AI 回退 | 已批准 | `reports/T02_SECURITY_BACKUP_AI_REPORT.md` | 用户 2026-10-20 批准（"可以"）；4 缺陷修复 + 13 新测试；324 passed / 2 warnings |
| T03 | 双 UI 设计方案与 Galaxy 选型 | 已批准 | `reports/T03_UI_DESIGN_GALAXY_REPORT.md` | 用户 2026-10-20 批准 D-1（Chronos 浅色）/ D-2（基线参数 28 天 / ≥14 天 / 2.5×MAD，U5 关闭）后进 T04 |
| T04 | Simple 模式实现 | 已批准 | `reports/T04_SIMPLE_UI_REPORT.md` | 用户 2026-10-20 18:20 回复"继续"（在 T04 报告与"审核通过后进 T05"之后）；12 文件 +407/−128；346 passed |
| T05 | Chronos 模式实现 | 已批准 | `reports/T05_CHRONOS_UI_REPORT.md` | 用户 2026-10-20 18:55 回复"先做完所有功能…"（在 T05 报告之后）；零 JS 两步交互；浏览器 14/14 |
| T06 | 最终回归与交付检查 | 待审核 | `reports/T06_FINAL_ACCEPTANCE_REPORT.md` | 结论 A（可以交付）；376 passed / 浏览器 14/14 / 打包解压自检 376；含追加要求"模块化 + UI 可美化接口" |
| 延伸 | 模块化与可美化接口（用户追加） | 待审核 | `docs/06-ui-extension-guide.md` | pages.py 注册表、context.py 视图模型、CSS 四层、base.html 扩展块、16 条守门测试 |

## 发布与工作台状态（2026-10-20 21:40）

- 发布包已按授权重新生成：`dist/personal-health-agent-0.1.2.zip`（343KB / 110 文件 / sha256 `ded193bd…`），
  源码与解压后自检均 376 passed。
- 工作台搭建前置与剩余任务清单：`docs/07-workbench-checklist.md`。
- 实测：解压产物 → `cli init`（37 指标）→ `cli import --commit`（6 pending / 拒绝 4）→ `cli backup` →
  `cli serve` 11 个端点全 200。

## 状态更新规则
- Work Buddy 完成一个任务后只能标记“待审核”，不能自行标记“已批准”。
- 用户/ChatGPT审核通过后，才把该任务标记为“已批准”。
- 发现外部依赖、环境限制、缺少设备等情况，标记“阻塞”或“未验证”，并说明原因。

## 基线快照（T00，2026-10-20）

| 项 | 值 |
|---|---|
| 应用版本 | 0.1.2 |
| Python | 3.13.14（项目 `.venv`） |
| 测试基线 | 295 passed / 0 failed / 0 skipped / 31 warnings |
| 版本控制 | T00 时无 Git 仓库；2026-10-20 15:30 已 `git init -b main` 并基线提交 `060e538`（dist/ 已 gitignore） |
| 数据库 | 工作区内无 `.db`，库未初始化；迁移可独立建库（11 表 / 10 索引 / 37 指标） |
