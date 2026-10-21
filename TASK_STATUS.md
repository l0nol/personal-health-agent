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
| 延伸 | 界面内配置 AI 接口（用户追加） | 待审核 | `CHANGELOG_WORKBUDDY.md`（2026-10-21 条）/ `docs/02-runbook.md` §7.5 / `tests/test_ai_config.py` | 6 家预设 + 自定义 OpenAI 兼容端点；`.env` 原子写回、保存即生效不用重启；试连独立于保存；29 新测试；491 passed / 1 skipped |

## AI 接口适配状态（2026-10-21）

- **入口**：界面「设置 → AI 分析接口」，支持 DeepSeek / 通义千问（百炼兼容模式）/
  Moonshot·Kimi / 智谱 GLM / OpenAI / 本地 Ollama，以及任意自定义 OpenAI 兼容端点。
  选预设只是把地址与模型两项填好，**不是白名单**。
- **保存**：写回本机 `.env`（原子替换、保留注释与其它行、尽量 0600），随后重新装载配置，
  **不用重启**；页面上只显示密钥尾号，密钥不落日志、不进导出。
- **试连**：`action=test` 只发一句"回复两个字"（16 tokens / 0 重试），不写文件、
  不落 `ai_call_log`、不产生分析；401/403/404/429 分别翻译成可行动的提示。
- **兼容**：老 `.env` 的 `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` /
  `DEEPSEEK_TIMEOUT_SECONDS` / `DEEPSEEK_MAX_RETRIES` 继续有效，两套同时存在时以 `AI_*` 为准。
- **明确不做**：不做密钥的加密存储（明文在 `.env`，靠文件权限）；不强制 https
  （保留 `http://127.0.0.1` 本地代理）；不在 GET 渲染里发任何请求。
- **未验证**：真实密钥端到端连通（手头无可用密钥）、Ollama 本地实例、非 OpenAI 兼容的私有协议。
- 版本：0.1.3 → **0.2.0**。

## 发布与工作台状态（2026-10-21 更新）

- **在线健康台已上线**（0.1.3 口径的网页版工作台，与本地应用并行）：
  发布 https://workbuddy.link/p/yqYfuXLN1g5lE2JzcDhZZf ｜ 编辑态 https://www.workbuddy.cn/space/d/yqYfuXLN1g5lE2JzcDhZZf
  数据存资料库三张表（健康指标记录 / 训练记录 / 每日状态），页面节点下已挂载。说明见 `docs/12-online-workbench.md`。
  状态：**待审核**（代码与部署已完成；真机渲染、非登录用户写入未验证）。
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
