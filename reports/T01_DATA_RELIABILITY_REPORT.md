# T01 — 数据可靠性检查与必要修复

- **项目**：Personal Health Agent（`personal-health-agent`）
- **执行时间**：2026-10-20 08:52–09:20 GMT+8
- **前置**：T00 已批准（用户 08:52 授权"开始下一步 T01"）
- **范围**：导入→预览→确认→修订→去重→统计→报告的数据正确性；只修有证据的问题
- **状态**：待审核

---

## 0. 结论摘要

| 项 | 结论 |
|---|---|
| 复现缺陷 | **5 个**，全部有复现测试（修复前失败） |
| 修复 | 4 处业务代码（`storage/lifecycle.py` 3 处、`analytics/aggregate.py` 1 处） |
| 测试 | 新增 `tests/test_t01_data_reliability.py`（16 条：8 复现 + 8 守门） |
| 回归 | **311 passed / 1 warning**（修复前 295 passed / 31 warnings；30 条日期适配器警告随 D3 一并消除） |
| 值域核对 | 5 张真实截图的 19 个观测值经校验 **0 拒绝**（不误拒）；仍为 `provisional`（不误收未证实） |
| 未验证 | 3 项（并发去重、resolve_conflict 同类回滚、值域"不误收"） |

---

## 1. 已确认缺陷与修复（D1–D5）

### D1 改值后 `dedup_key` 不刷新 → 重复/冲突判定错位（中）

- **证据**：`storage/lifecycle.py::update_pending_value` 只 `SET value`，不更新 `dedup_key`；而 `dedup_key = digest(logical_key | unit | value)`（`storage/keys.py:55`）。
- **危害**：改值后，`dedup_key` 仍是旧值的键。后果是——
  - 再导入**被改掉的旧值** → 被判"完全重复"静默跳过（库里其实是新值）；
  - 再导入**新值** → 被判"冲突"而非"重复"。
- **修复**：`update_pending_value` 同步重算 `dedup_key = _dedup_key(logical_key, unit, value)`。
- **测试**：`test_dedup_key_is_recomputed_when_pending_value_changes`、`test_reimport_after_value_change_classifies_correctly`（修复前 2 条失败）。

### D2 版本化修正丢失 `captured_at`（提取时间）（中）

- **证据**：`revise_confirmed` 构造替换 `NormalizedRecord` 时不携带 `captured_at`，`insert_record` 未传该参数 → 新版本 `captured_at = NULL`。
- **危害**：违反任务包硬规则"保留来源、单位、时间、提取时间、确认状态和派生关系"。
- **修复**：`insert_record(..., captured_at=row["captured_at"])`。
- **测试**：`test_revise_confirmed_preserves_provenance`（修复前失败）。

### D3 统计取数依赖 Python 已弃用的默认日期适配器（中）

- **证据**：`ui/app.py` 的 `_series` 把 `date` 对象直接传给 `load_points`；`sqlite3` 用默认适配器转 ISO 字符串，自 Python 3.12 起发 `DeprecationWarning`（修复前全量测试 31 条警告里占 30 条），适配器被移除后将直接抛 `TypeError`（`/sleep`、`/trends` 会 500）。
- **修复**：`load_points` 收窄为 `str | date`，入参经 `_iso_arg()` 统一转 ISO 字符串，不再把 `date` 交给 sqlite3。
- **测试**：`test_load_points_accepts_date_objects_without_deprecation_warning`（修复前失败）。

### D4 含冲突的批次无法撤销（高，用户可见）

- **证据**：`conflicts` 表两列 `incoming_record_id`/`existing_record_id` 均 `REFERENCES records`（`001_init.sql:95-96`），`revoke_batch` 物理删除 pending 记录时撞外键，抛 `IntegrityError: FOREIGN KEY constraint failed`，批次状态卡在 `active`，界面（`ui/app.py:405` 捕获）只弹一句英文错误。
- **修复**：删除 pending 记录**之前**，先删除指向它们的冲突行（`incoming` 或 `existing` 命中本批次待删记录），并写 `audit_log(action='delete', reason='batch_revoked')`；整个撤销过程包进 `try/except → rollback`。
- **测试**：`test_revoke_batch_with_pending_conflict_succeeds`、`test_revoking_batch_keeps_conflicts_of_other_batches`、`test_audit_log_records_deleted_conflict`（修复前 3 条失败）。

### D5 修正中途失败会留下"无人认领"的半截状态（中）

- **证据**：`revise_confirmed` 先把旧记录转 `superseded` 再插入新版本；若插入失败且调用方继续提交，旧记录永久停在 `superseded` → 该数据从统计/报告里**凭空消失**。
- **修复**：整个修正过程包进 `try/except → rollback`，保证"要么两条都在，要么一条都没动"。
- **测试**：`test_revise_confirmed_rolls_back_when_insert_fails`（monkeypatch 注入 `IntegrityError`；本条为修复后补写，未在修复前实跑）。

---

## 2. T01 九项必须验证的结论

| # | 要求 | 结论 | 证据 |
|---|---|---|---|
| 1 | pending 不进统计/图表/报告/提醒 | ✅ 已核 | `load_points` 仅取 `verification_status='confirmed'`（`aggregate.py`）；`tracked_metrics` 同；`test_pending_records_never_enter_statistics`、`test_rules_do_not_read_pending_data` |
| 2 | NULL ≠ 0，缺失日不填 0 | ✅ 已核 | `load_points` 带 `value IS NOT NULL`；`summarize` 对 NULL 记 `missing_days`、`mean=None`；`test_null_value_is_missing_not_zero` |
| 3 | 单位与语义正确；比例不当时长 | ✅ 已核 | `CONVERSIONS` 仅 `h→min`、`s→min`、`kJ→kcal`，无 `%→min`；`test_units.py` 8 条通过 |
| 4 | HRV 区间不伪装成均值 | ✅ 已核 | `hrv_min_ms`/`hrv_max_ms` 为 `range_bound` 且 `in_report=False`；`hrv_avg_ms` 是设备给的独立均值指标；`test_range_bound_metrics_never_enter_reports` |
| 5 | 派生数据用对来源与窗口 | ✅ 已核 | `build_weekly` 的 `derived_from='delta'` 带 `window=(prev.start, current.end)`；`build_daily` 均值带 `record_ids`；`test_report_numbers_stay_traceable` |
| 6 | 修订/插入失败时事务一致 | ✅ 已修复 | D5（revise 回滚）+ D4（revoke 回滚）；`confirm_record` 原有 IntegrityError 回滚 |
| 7 | 去重不因状态转换失效 | ✅ 已核+修复 | 驳回/取代后不再挡新数据（`_ACTIVE_STATUSES` 只含 pending/confirmed）；改值后的键错位已由 D1 修复；`test_rejected_record_does_not_block_reimport`、`test_superseded_record_does_not_block_reimport` |
| 8 | 睡眠建议不在年龄未知时声称个性化 | ✅ 已核 | `AGE_ADAPTATION_IMPLEMENTED=False`；`describe_sleep_target` 默认文案"未按年龄、性别或作息调整"；`test_sleep_target_text_never_claims_personalization` |
| 9 | 报告关键数字可追溯 | ✅ 已核 | `NumberRef.traceable`（record_ids 或 derived_from+window 二者必具其一）；`test_report_numbers_stay_traceable` |

---

## 3. 值域核对（用 5 张真实截图）

把用户提供的华为截图里的 19 个观测值喂进 `validate_row`：

```
观测值总数: 19  被拒绝: 0
```

覆盖：静息心率 60、心率范围 56/142、夜间睡眠 305、深睡 18%、浅睡 69%、REM 13%、深睡连续性 76、清醒 0、呼吸质量 98、平均心率 63、HRV 47（区间 15/106）、血氧 97（94/99）、呼吸率 12、小睡 70。

**结论**：当前值域**不误拒**这批真实数据。但这只证明了一半——"不误收"（值域是否过松）仍需华为官方字段说明才能核对，因此 37 个指标仍保持 `provisional`，未在本轮擅自修改任何 `min_valid/max_valid`。

---

## 4. 修改文件清单

| 文件 | 改动 |
|---|---|
| `src/health_agent/storage/lifecycle.py` | `update_pending_value` 重算 dedup_key（D1）；`revise_confirmed` 保留 captured_at + 事务回滚（D2/D5）；`revoke_batch` 先清悬空冲突 + 事务回滚（D4）；新增 `from .keys import dedup_key` |
| `src/health_agent/analytics/aggregate.py` | `load_points` 参数收窄为 `str\|date`，新增 `_iso_arg()`（D3） |
| `tests/test_t01_data_reliability.py` | **新增** 16 条测试（8 复现 + 8 守门） |

未改：`catalog.py`、`validation/`、`reports/`、`rules/`、`ui/`、数据库结构、依赖。

---

## 5. 测试结果

| 命令 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest --basetemp=<新目录>` | **311 passed, 1 warning in 25.85s，退出码 0** |
| 修复前基线（T00） | 295 passed, 31 warnings |
| 新增用例 | 16 条（`tests/test_t01_data_reliability.py`） |
| 剩余 1 条警告 | `StarletteDeprecationWarning`（`fastapi/testclient.py` 第三方，与本项目代码无关） |
| 重复测试名扫描 | `grep -h "^def test_" tests/*.py \| sort \| uniq -d` → 无输出 |
| UI 冒烟（TestClient 临时库） | `/`、`/sleep`、`/trends`、`/reports`、`/chronos` 全部 200 |

> 说明：本机沙箱会拦截 pytest 对临时目录的批量清理，改用 `--basetemp=<全新目录名>` 规避；测试产生的 4 个临时目录已清理干净。

---

## 6. 未验证事项（不得视为通过）

| # | 项 | 原因 | 归属 |
|---|---|---|---|
| U1 | 并发/多线程下去重的原子性 | 本机无并发场景，仅靠数据库部分唯一索引兜底；未做并发压测 | 需 T02 或专项 |
| U2 | `resolve_conflict` 缺同类事务回滚（与 D5 同构） | 两个 UPDATE 之间无外部失败源，风险低；本轮按"最小修改"未动 | T02 可顺手补 |
| U3 | 值域"不误收"（过松） | 无华为官方字段说明，37 指标仍 `provisional` | 需用户提供官方字段文档 |
| U4 | 真实截图经导入→确认→报告全链路 | 本轮只对观测值做校验核对，未跑完整导入 | T02 或用户提供导出文件 |

---

## 7. 剩余风险

1. **逻辑键仍只到日期粒度**（`storage/keys.py:32`）：同来源同日第二条瞬时测量仍会进冲突流程——启用 P2 指标（血压/体温/体重）前必须把观测时刻纳入键，这是 T00 已列出的已知限制，本轮未动。
2. **D5 回滚测试为修复后补写**：虽按代码路径可证修复前会失败，但未在修复前实跑，特此披露。
3. 无 Git 仓库（T00 已上报，待用户裁决 `git init`）——本轮修复无法用 diff 佐证"只改了 3 个文件"，以文件时间与手工清单代替。

---

## 8. 状态

- T01 已完成，状态标记为**待审核**。
- 已更新 `TASK_STATUS.md`（T01 → 待审核）与 `CHANGELOG_WORKBUDDY.md`（追加 T01 条目）。
- **未开始 T02**，等待用户审核与授权。
