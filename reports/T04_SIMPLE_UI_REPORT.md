# T04 — Simple 简洁模式实现报告

- 任务：按 T03 已批准方案实现六个页面的 Simple 模式（总览 / 睡眠 / 趋势 / 导入确认 / 提醒 / 设置隐私）
- 前置：T03 设计方案已批准；D-1（Chronos 浅色）、D-2（基线参数 28 天 / ≥14 天 / 2.5×MAD）已由用户 2026-10-20 裁决
- 日期：2026-10-20
- 状态：**待审核**

---

## 1. 修改文件（12 个，+407 / −128 行）

| 文件 | 改动 |
|---|---|
| `src/health_agent/ui/app.py` | 新增 `_reference()`（参照范围数据装配，只读）；三页接入 reference 上下文；提醒页接入 `rule`/`rule_ids`；设置页接入 `app_version`/`schema_version`；清理重复 import 与重复 `human` 过滤器注册；`_safe_next` 文档字符串改 raw（修 T02 遗留 SyntaxWarning） |
| `src/health_agent/storage/alerts.py` | `list_alerts()` 增加可选 `rule_id` 过滤（默认行为不变）；新增 `list_rule_ids()` |
| `src/health_agent/config.py` | 删除文件尾部重复的 5 条校验（与上方逐字重复） |
| `src/health_agent/ui/templates/_macros.html` | 新增 `banner`、`step_bar`、`reference_range`、`stat_card`、`value_cell`、`metric_cell` 六个宏 |
| `src/health_agent/ui/templates/index.html` | 指标卡换 `stat_card`（数值 + 状态点 + 参照范围行 + 偏离标记） |
| `src/health_agent/ui/templates/sleep.html` | 顶部新增"参照范围"卡，与"目标"明确分区 |
| `src/health_agent/ui/templates/trends.html` | 非区间指标新增"参照范围"卡；区间指标文案补"因此不显示参照范围" |
| `src/health_agent/ui/templates/import_preview.html` | 五桶重排（冲突→待复核→拒绝→重复→可直接确认）；每行加字段名与"原始值→归一值"；拒绝区列全部错误码；补重复桶表格 |
| `src/health_agent/ui/templates/imports.html` | 顶部三步条 |
| `src/health_agent/ui/templates/alerts.html` | 空态二态文案；筛选（全部/未读/按规则）改 GET 表单；右侧/底部常驻说明卡 |
| `src/health_agent/ui/templates/settings.html` | 新增睡眠目标卡、提醒阈值只读表、数据与隐私分区（含备份入口）、关于卡（版本/schema）；Chronos 选项维持"（Demo）"字样（不宣称未实现的旋转） |
| `src/health_agent/ui/templates/base.html` | 横幅改用 `banner` 宏 |
| `src/health_agent/ui/static/app.css` | 参照范围行、三步条、提醒双列布局、`--focus-ring` 焦点样式、按钮/输入框触控高度（≥44px / 40px）、`.mini::after` 伪元素扩命中区 |

## 2. 新增文件（2 个）

- `docs/UI-ATTRIBUTION.md`：Galaxy 选型结论（0 复制 / 0 依赖 / 0 构建变更）+ 采用登记表（当前为空）+ 采用后置七条门槛
- `tests/test_t04_simple_ui.py`：22 条测试（见 §5）

## 3. 关键实现口径

1. **参照范围行（用户 10-20 裁决组件）**：数据源唯一为 `analytics/baseline.py::compute_baseline()`（中位数 + MAD；MAD=0 时 IQR/1.349 兜底，方法名如实展示）。四态：
   - `active` → "个人基线 X–Y" + `<details>怎么算的</details>` 展开中位数、带宽、方法、窗口与覆盖率；
   - `insufficient` → "数据不足：有效记录 N/14 天，样本不足"（不说区间）；
   - `no_data` → "暂无数据"；
   - `range_bound` → 不渲染该行（区间端点不算均值）。
   - 文案铁律：只称"个人基线"、不用华为静态参考值、不着红绿；与"睡眠目标"（配置值）分两行，睡眠页有明确对照说明。
2. **偏离基线**：仅中性文字标记（`.ref-dev`，取 `--text-2` 文字色），不用红/绿/危险色（设计规范 §3.7-D）。
3. **阈值只读**：设置页表格列环境变量名，页面无任何对应 `name=` 输入控件（假控件禁令，测试守着）。
4. **待确认隔离**：pending 不进任何统计/基线/图表（既有口径，T04 未改统计代码）；导入预览明确"写入后一律 pending，确认前不计入趋势"。
5. **性能**：每页基线查询 ≤4 次（28 天窗口）。实测 28 天 × 5 指标数据下渲染 26–28ms/次，低于 T03 报告 R4 的 50ms 预算（见 §6 命令）。

## 4. 复现步骤

```bash
cd personal-health-agent
.venv/Scripts/python.exe -m pytest tests/test_t04_simple_ui.py -q   # 22 passed
.venv/Scripts/python.exe -m pytest                                  # 346 passed, 1 warning, EXIT=0
```

手工核对（真实 uvicorn，20 天示例数据，1 条留 pending）：

| 页面 | 结果 |
|---|---|
| `/` | 200；指标卡含"个人基线 6 小时 36 分–6 小时 44 分"与"怎么算的" |
| `/sleep` | 200；参照范围卡与目标分区并存 |
| `/trends?metric=night_sleep_minutes&days=30` | 200；参照范围卡 + "仅统计已确认记录" |
| `/trends?metric=hrv_min_ms` | 200；无 `ref-row`，横幅"因此不显示参照范围" |
| `/imports` → 上传 `test_data/sample_with_issues.csv` → 预览 | 200；"6.5 h → 6 小时 30 分"、"1:23 min → 1 小时 23 分"、`E_UNKNOWN_METRIC`、错误码可选中复制 |
| `/alerts` | 200；常驻说明卡 + 规则筛选下拉 |
| `/settings` | 200；阈值表 / 数据与隐私 / 版本 0.1.2 + schema v1 |
| `/static/app.css` | 200；含 `--focus-ring`、`.ref-row`、`.alerts-layout` |

服务端日志全程无 ERROR/WARNING 输出。

## 5. 测试结果

- **总数 346 / 通过 346 / 失败 0 / 跳过 0 / 警告 1（starlette TestClient 弃用提示，第三方）/ 退出码 0**
- 较 T03 基线（324）+22：`tests/test_t04_simple_ui.py` 新增 22 条，全部"先失败后通过"或直接守护新行为：
  - 参照范围四态各 1 条 + IQR 兜底方法披露 1 条（复现 ERR-009 场景：交替 390/400 使 MAD=0，断言页面如实显示"2.5×IQR/1.349（MAD 退化时兜底）"）
  - 偏离基线中性标记 1 条（断言不用 danger/warn 类）
  - 阈值只读 1 条（6 个环境变量名在页 + 6 个对应输入控件名不在页）
  - 三步条 / 预览列 / 全部错误码 / 五桶顺序 4 条
  - 提醒两种空态 + 规则筛选 + 常驻说明卡 3 条
  - 设置版本/schema/备份入口 2 条
  - CSS 触控与焦点静态断言 2 条
- `grep -h "^def test_" tests/*.py | sort | uniq -d` → 无输出（无同名测试，ERR-014 守门）

## 6. 执行命令（关键几条）

```
.venv/Scripts/python.exe -m pytest                                  # 346 passed, 1 warning, EXIT=0
.venv/Scripts/python.exe -m pytest tests/test_t04_simple_ui.py -q   # 22 passed
.venv/Scripts/python.exe -c "<计时脚本>"                             # 26.4/26.0/26.0/27.7/28.3 ms
uvicorn 真实服务 + urllib/httpx 走 8 页 + multipart 上传预览          # 全部 200，标记齐全
```

计时环境：28 天 × 5 指标（140 条已确认），TestClient 5 次均值；未走网络栈（本机回环还要再叠 1–3ms，量级不变）。

## 7. 未验证项目

1. **平板真机**：1024/800 宽度、触控 ≥44px、无横向滚动均为 CSS 设计值与静态断言，未在真实平板/浏览器实测（`docs/03-tablet-access.md` 流程未跑）。
2. **真实健康数据**：全部验证用程序生成的示例数据；未用华为截图走完整导入→确认→基线链路。
3. **禁用 JS 走导入**：本项目零 JS，静态成立，但未在浏览器关 JS 实测。
4. **提醒按规则筛选的边界**：规则很多时下拉长度未做分组/截断（当前 4 条规则，无体感问题）。
5. **`os.replace` 文件锁等 T02 遗留未验证项**不在本轮范围。

## 8. 剩余问题与风险

- R1：`range_bound` 指标在趋势页有参照范围说明横幅，但**首页指标卡**对区间指标不显示参照行（设计如此：区间指标不进 in_report、不算均值）——若用户希望首页也提示"该指标无参照"，需再裁决。
- R2：提醒页规则筛选依赖 `alerts` 表里出现过的 `rule_id`；空库时下拉只有"全部规则"（符合预期，记录在案）。
- R3：`.mini::after` 伪元素扩命中区在极窄行内可能与相邻按钮热区重叠（视觉不变、功能不变，未实测触屏）。
-  Chronos 模式（T05）未开始；本报告不含任何 Chronos 实现。

## 9. 门禁申请

T04 完成，申请审核。审核通过后再进 T05（Chronos 模式实现）。
