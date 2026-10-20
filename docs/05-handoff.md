# 交接说明（HANDOFF）· v0.1.2 + T04/T05（2026-10-20 晚更新）

> 给接手的人或 AI：读完这一份，就能知道**现在在哪、为什么这么做、下一步做什么**。
> 更细的设计取舍见 `docs/00-architecture-plan-v1.md` 的 §15 修订记录（V1.1–V1.5）。
> 任务包门禁进度见 `TASK_STATUS.md`：T00–T04 已批准，**T05 已实现、待审核**
> （`reports/T05_CHRONOS_UI_REPORT.md`），T06 未开始。

---

## 0. 一句话状态

本地优先的个人健康数据工具，**P0 功能（任务 0–7）全部完成并有测试**；
0.1.2 完成 UI 设计系统首轮落地；T04 落了 Simple 模式六页（参照范围行、五桶重排、阈值只读表等）；
T05 把时钟模式从 Demo 升级为零 JS 两步环形导航（选择→预览→进入、键盘方向键、减少动态降级）。
测试 **360 passed**，浏览器交互验证 14/14。**没有接真实设备数据，也没有接真实 AI 接口。**

---

## 1. 边界（先接受这四条，否则会做错方向）

1. **不是医疗产品**：只做趋势观察与生活习惯提示，不做诊断、不做治疗建议、不预测风险。
2. **缺失就是缺失**：`NULL` 表示没有数据，任何地方不得填 0、不得猜测、不得插值。
3. **未确认不进趋势**：`pending` 数据不进统计、基线、报告、提醒。
4. **本地优先**：数据只在本机 SQLite；AI 为可选，且只发送已算好的摘要。

---

## 2. 当前真实状态（能做 / 不能做）

| 能力 | 状态 | 证据 |
|---|---|---|
| JSON/CSV/TSV 导入 → 五桶预览 → 写入 | ✅ | `ingestion/`，`tests/test_ingestion.py` |
| 确认 / 驳回 / 改值 / 版本化修正 / 撤销批次 | ✅ | `storage/lifecycle.py`，`tests/test_lifecycle.py` |
| 冲突登记与裁决（只允许保留旧值/采用新值） | ✅ | `tests/test_lifecycle.py` |
| 窗口聚合、覆盖率、数据质量 | ✅ | `analytics/`，`tests/test_analytics.py` |
| 日报/周报 + 数字追溯（record_ids / derived_from） | ✅ | `reports/builder.py`，`tests/test_reports.py` |
| 个人基线（中位数 + MAD，退化用 IQR 兜底） | ✅ | `analytics/baseline.py` |
| 4 条提醒规则 + 去重 + 72h 冷却 + 反馈 | ✅ | `rules/engine.py`，`tests/test_rules.py` |
| AI 改写（可缓存、可降级、有调用日志） | ✅ 代码完整，**未接真实接口** | `ai_client/`，`tests/test_ai.py` |
| 备份/恢复 + 指纹校验 | ✅ 实测过 | `backup.py`，`docs/02-runbook.md` §5 |
| 界面：记录/导入/冲突/提醒/报告/设置 | ✅ 可用 | 0.1.1 交付；T04 重排（五桶顺序、三步条、阈值只读表、数据与隐私分区） |
| 界面：首页仪表盘 / 睡眠分析 / 指标趋势 | ✅ 0.1.2 新增；T04 加"参照范围"行 | `ui/charts.py`、`tests/test_ui_pages.py`、`tests/test_t04_simple_ui.py` |
| 时钟模式（表盘导航） | ✅ T05：零 JS 两步交互（选扇区→预览→进入）、键盘方向键、减少动态降级；**无自由拖动旋转**（零 JS 约束，见 T05 报告 §2） | `templates/chronos.html`、`tests/test_t05_chronos.py`、`scripts/verify_t05_browser.js` |
| 平板真机验证 | ❌ 未做，只有服务端行为证据 | `docs/03-tablet-access.md` |
| 指标与设备字段核对 | ❌ 未做，37 个全为 `provisional` | `docs/01-data-dictionary.md` |

---

## 3. 架构地图（改代码前先看这张表）

```
ui/          FastAPI + Jinja2 服务端渲染；只做展示与表单转发
  app.py     create_app 工厂、鉴权/CSRF 中间件、全部路由
  charts.py  纯几何：把序列变成 SVG 坐标（唯一"画图"的地方）
reports/     日报周报；每个数字带 record_ids 或 derived_from+window
analytics/   窗口聚合、基线、质量；只读 confirmed
rules/       提醒规则 + 去重冷却
ingestion/   读文件 → 五桶预览 → 落库（全 pending）
storage/     SQLite 迁移、repository、状态机、导出、备份
validation/  单位白名单、单行校验（纯函数，不碰数据库）
ai_client/   LLM 契约、HTTP 客户端、缓存、降级
catalog.py   37 个指标的唯一事实源（含 verification 与 range_bound）
```

**依赖方向**：`ui → reports/ingestion → analytics/rules → storage`，`storage → validation`。
反向依赖一处都不许有；SQL 只出现在 `storage/` 与 `ui/app.py` 的两处只读统计查询里。

---

## 4. 不可破坏的约定（有测试守着，但改之前先想清楚）

1. 同一来源+日期+指标+周期最多一条 `confirmed`（数据库部分唯一索引）。
2. 比例不得换算成时长（`units.CONVERSIONS` 里没有 `%→min`）。
3. `range_bound` 指标不得 `in_report`，`assert_catalog_invariants()` 在导入时拦截。
4. 缺失值 `None` 全程保留；图表缺失断线、柱状空心。
5. 指标定义必须带 `verification`（当前全 `provisional`）。
6. 密钥只从环境变量读；`Settings` 对密钥字段 `repr=False`。
7. 版本号只有两处：`pyproject.toml` 与 `__init__.__version__`（有测试比对）。
8. 新增页面只读就只注册 GET；写操作必须走 `<form>` + CSRF 隐藏域。

---

## 5. 关键决策与理由（避免"改进"成错误方向）

| 决策 | 理由 | 位置 |
|---|---|---|
| 主数据源=截图提取的 JSON/CSV，字段名按规格 §4 | 用户明确裁决；不引入加密包通道 | 方案 §15 V1.1 |
| 统计用标准库而非 pandas | 窗口只有 7–28 天、需逐条 record_id 追溯；重新引入判据写在文档 | 方案 §15 V1.2 |
| 缺口提示只覆盖"近 30 天在记"的指标 | 否则每天列 17 个从未记录的指标，是噪音 | `reports/builder.py` |
| AI 只在显式点击时调用 | 打开页面就调 AI 会让单页 10s，且违反规格 §7 | 方案 §15 V1.4 |
| 睡眠目标区分"用户设定/默认参考值" | 默认值写成"你的目标"是误导；且**没有年龄适配** | 方案 §15 V1.5 |
| 三套状态语义（记录/可信度/充分性）不混用 | `device_verified` 是字段状态，不是记录状态 | 设计稿 §3.7 |
| 图表手写 SVG，不引图表库 | 依赖最小化；且需要"缺失断线/区间带"这类非常规行为 | `ui/charts.py` |

---

## 6. 已知限制（不要对外宣称已解决）

- 37 个指标全部 `provisional`，**未与任何设备官方字段核对**，值域可能误拒合法值。
- 睡眠目标**没有**年龄适配（无年龄字段）；默认 420 分钟只是参考值。
- 逻辑键只到日期粒度：同来源同日第二条瞬时测量会进冲突流程（P2 指标启用前必修）。
- 无 HTTPS、单密码鉴权；不要暴露公网。
- 平板零真机验证；时钟模式无拖拽。
- 首页指标卡对区间端点指标不显示参照范围行（设计如此：区间不算均值；是否要加提示待用户确认，见 T04 报告 R1）。
- AI 费用恒记 `unavailable`（拿不到账单，不估算）。

---

## 7. 下一步（按优先级，每步都有验收口径）

| # | 事情 | 验收 |
|---|---|---|
| 1 | 用 1–2 张真实截图校正值域与单位，把核对过的改 `device_verified` | 数据字典中 `device_verified` 数量 > 0，且每项有核对记录 |
| 2 | **T06：最终回归与交付检查**（全量测试、打包自检、交付说明） | `scripts/package.py` 产物解压后测试通过 |
| 3 | 平板实机（Chrome/华为浏览器）走一遍 6 个页面 + 时钟模式 | 触控 ≥44px、无横向滚动、导入可用、表盘旋转不掉帧 |
| 4 | 逻辑键加入观测时刻（P2 前置） | 同日两次血压可并存，且各自可确认 |
| 5 | 时钟模式自由拖动旋转（**需要 JS，与零 JS 铁律冲突，须用户裁决**） | 若批准：拖动与方向键完成同一路径；否则维持现状并保留说明 |
| 6 | 提醒「不相关」反馈联动降频 | 连续 3 次不相关后该规则冷却延长，有测试 |

---

## 8. 环境与命令

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m pip install -e .
.venv/Scripts/python.exe -m pytest          # 预期 360 passed
.venv/Scripts/python.exe -m health_agent.cli serve
# 浏览器 http://127.0.0.1:8000
```

- 配置：`.env.example`（18 项，全部有注释；不填密钥也能完整使用）
- 备份：`python -m health_agent.cli backup`；恢复加 `--force`（会先留 `.bak`）
- 打包：`python scripts/package.py`（自动跑测试 + 解压自检）
- 文档重生成：`python scripts/gen_data_dictionary.py`

---

## 9. 新人先读这 6 个文件

1. `docs/01-data-dictionary.md` —— 有哪些指标、什么状态
2. `src/health_agent/catalog.py` —— 指标定义与两条不变式
3. `src/health_agent/validation/rules.py` —— 什么拒绝、什么只标记
4. `src/health_agent/storage/migrations/001_init.sql` —— 部分唯一索引是"不覆盖"的依据
5. `src/health_agent/reports/builder.py` —— 数字追溯结构
6. `src/health_agent/ui/charts.py` —— 图表为什么缺失断线、区间不画中线

---

## 10. 踩过的坑（改同类代码前先看）

项目记忆里记了 18 条（ERR-001…018），最常复发的五条：

1. **CSV 带 BOM** → `DictReader` 键名变 `\ufeffmetric_name`，一律 `utf-8-sig`。
2. **同一事实两处实现**（键函数、格式、版本号）→ 只允许一处事实源。
3. **命令可能被执行两次** → 任何写库/写文件脚本必须幂等。
4. **把网络调用放进 GET** → 读路径只读缓存，花钱的动作必须显式 POST。
5. **Jinja 宏名与上下文变量同名** → 互相遮蔽，用 `import x as y`。

完整条目见工作区记忆 `MEMORY.md` 的"经验条目（ERR-ID）"。
