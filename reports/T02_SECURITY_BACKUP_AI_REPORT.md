# T02 — 安全、备份与 AI 回退

- **项目**：Personal Health Agent（`personal-health-agent`）
- **执行时间**：2026-10-20 13:46–14:10 GMT+8
- **前置**：T01 已批准（用户 13:46 授权"继续02"）
- **范围**：秘密信息、认证/会话/CSRF、上传、SQL 与错误信息、备份恢复、AI 失败路径、设备原文边界
- **声明**：本轮是**代码审查 + 可复现测试**，**不是专业渗透测试**，未做网络扫描、未做真实攻击演练
- **状态**：待审核

---

## 0. 结论摘要

| 类别 | 数量 | 说明 |
|---|---|---|
| 已复现并修复的缺陷 | **4** | 开放重定向、上传无上限、恢复失败毁库、陈旧 WAL 污染恢复结果 |
| 静态审查风险（未复现） | 7 | 逐条给判断依据，不把警告等同于漏洞 |
| 已验证通过的控制项 | 9 | 有命令或测试证据 |
| 未验证 | 5 | 真实渗透、局域网真机、Windows 文件锁、真实 AI 接口、密钥文件权限 |

**最严重的一条**：`restore_backup` 会先把备份内容**直接写进现役库**再校验。一个 manifest 自洽但内容不是 SQLite 的备份包，能把正在使用的数据库写成垃圾；另一个更隐蔽的场景是——恢复后旧库的 `-wal` 被 SQLite 回放，恢复出来的库变成"备份 + 旧未 checkpoint 事务"的混合体。两条都已复现并修复。

---

## 1. 已复现缺陷与修复

### S1 登录 `next` 参数可跳转到站外（开放重定向，中）

- **复现**：带密码模式 POST `/login`：
  | `next` 取值 | 修复前 Location |
  |---|---|
  | `//evil.example` | `//evil.example` |
  | `https://evil.example/x` | `https://evil.example/x` |
  | `/\evil.example` | 原样（浏览器会把 `\` 归一成 `/`） |
- **影响**：攻击者构造 `http://127.0.0.1:8000/login?next=//evil.example` 发给用户，用户登录后被送到站外钓鱼页。局域网模式下有实际意义。
- **修复**：`ui/app.py:52` 新增 `_safe_next()`，只放行以 `/` 开头、不以 `//` 开头、且不含 `\` 的路径；`ui/app.py:183` 使用它。
- **测试**：`test_login_never_redirects_off_site`（4 组参数，修复前全失败）、`test_login_still_honours_internal_path`。

### S2 上传无大小上限（低-中）

- **证据**：`imports_upload` 原为 `target.write_bytes(await file.read())`，整包读进内存，无任何上限。局域网模式下一次超大上传即可把进程内存吃满。
- **修复**：`ui/app.py:49` 定义 `MAX_UPLOAD_BYTES = 10 MB`；`ui/app.py:358` 只多读 1 字节判断超限，超限直接回错误页且**不落盘**。
- **测试**：`test_upload_rejects_oversized_file`（11 MB 文件，修复前被接受并落盘）。

### S3 恢复失败会先毁掉现役库（高）

- **复现**：构造"manifest 里的 sha256 与内容自洽、但内容不是 SQLite"的备份包 → `restore_backup` 先把 20 字节垃圾写进 `health.db`，再打开做 integrity_check → 抛 `sqlite3.DatabaseError: file is not a database`。
- **实测结果**：恢复失败后目标库**已损坏**（`SELECT` 直接报 `file is not a database`），只剩手动 `.bak-*` 可救。
- **修复**（`backup.py`）：
  1. `_validate_payload()`（`:145`）先验 SQLite magic（`SQLite format 3\0`），不合格**在落盘前**拒绝；
  2. 备份解到临时文件 `.restore-<时间戳>-<随机>.db`，在临时文件上做 `integrity_check`、`apply_migrations`、记录数统计；
  3. `PRAGMA journal_mode=DELETE` 把 WAL 折回主文件，保证搬移的是完整单文件；
  4. 全部通过才 `os.replace()` 原子替换（`:224`）；
  5. 任一步失败只删临时文件，现役库**一个字节都没动**。
- **测试**：`test_restore_failure_leaves_existing_database_intact`、`test_restore_failure_leaves_no_temp_files`（修复前均失败）。

### S4 陈旧 WAL 被回放进恢复后的库（高，静默数据污染）

- **复现**：备份只含 1 条记录；库里另有 1 条已提交但未 checkpoint 的记录（模拟进程被杀，`-wal` 留存）→ 恢复后实际记录数是 **2**，恢复出来的库被旧 WAL 污染；而 manifest 报告的 `restored_total_records` 是 1，**报告数与实际数不一致**。
- **根因**：只替换了 `health.db`，没处理旧库的 `health.db-wal` / `health.db-shm`。SQLite 打开时会把这些日志当作自己的。
- **修复**：替换前删除 `-wal` / `-shm`（`backup.py:220`）。
- **测试**：`test_restore_discards_stale_wal`（修复前 `assert 2 == 1` 失败）、`test_restore_reports_matching_record_count`。

---

## 2. 静态审查风险（未复现，仅记录判断依据）

| # | 位置 | 风险 | 判断依据 / 为什么未修 |
|---|---|---|---|
| R1 | `ai_client/http_client.py:56` + `config.py` | `deepseek_base_url` 未强制 https，配成 http 时 API Key 明文过网 | 本地单用户工具，且可能有人用本地代理（http://127.0.0.1）；强行限制会破坏合法用法 → 记录，不修 |
| R2 | `backup.py:121` | manifest 的 sha256 存在同一个 zip 里——**防意外损坏，不防伪造** | 攻击者能同时改 db 与 manifest。单用户本地备份，威胁模型下可接受；不宣称"防篡改" |
| R3 | `ui/auth.py:112` | `chmod(0o600)` 在 Windows 上不生效（只改只读属性） | 平台限制，无法在应用层解决；已在文档标注"密钥文件依赖目录权限" |
| R4 | `backup.py:144` | `.bak-<秒级时间戳>` 同秒恢复两次会覆盖上一份 `.bak` | 需人工同一秒连续两次 `--force` 恢复；概率极低 → 记录 |
| R5 | `ui/app.py:357` | 暂存文件名 `<秒级时间戳>_<原名>`，同秒同名上传会互相覆盖 | UI 一次只传一个文件；概率低 → 记录 |
| R6 | `config.py:135-152` | 参数校验块**重复了一遍**（6 条 raise 写两次） | 无功能影响，属复制残留；T02 不做无关重构 → 记录 |
| R7 | `ui/app.py:405` | 撤销失败时把 `str(exc)`（含 SQLite 英文原文）拼进 URL 回显 | 本地工具，错误信息不含密钥与数据内容 → 记录 |

> 另有 T01 遗留的 `resolve_conflict` 缺事务回滚（两个 UPDATE 之间无外部失败源），本轮仍未修，见 §5。

---

## 3. 已验证通过的控制项

| # | 项 | 证据 |
|---|---|---|
| 1 | 源码/脚本/测试/样例配置无密钥字面量 | `grep -rn "sk-[A-Za-z0-9]\{10,\}"` → 无输出；`.env.example` 的 `DEEPSEEK_API_KEY` / `HEALTH_AGENT_SECRET` / `PASSWORD_HASH` 均为空值 |
| 2 | 密钥不进日志、不进 repr | `ChatCompletionClient.__repr__` 不含 key；`ai_client/` 全目录无 `logging/print`；`Settings` 对密钥字段 `repr=False`；测试 `test_repr_does_not_leak_api_key` |
| 3 | 密码存储 | PBKDF2-SHA256，240,000 次迭代 + 16 字节随机盐，`hmac.compare_digest` 比较（`ui/auth.py:28,34`） |
| 4 | 会话 | HMAC-SHA256 签名 + 12h 过期；Cookie `HttpOnly` + `SameSite=Lax`。新增守门测试 `test_session_cookie_is_httponly_and_samesite` |
| 5 | CSRF | 有会话时，非表单 content-type 一律 400；表单缺 token 或 token 不符一律 400（`ui/app.py` guard 中间件）；既有 `test_csrf_is_enforced_when_password_set` |
| 6 | 局域网保护 | `lan_enabled` 且无密码 → 拒绝启动（`config.py::validate_settings`） |
| 7 | 上传类型与路径 | 后缀白名单 `.json/.csv/.tsv`；`Path(filename).name` 去路径；`_staged_path` 校验 `basename == 入参`；`test_upload_rejects_unsupported_type`、`test_preview_rejects_path_traversal` |
| 8 | SQL 注入 | 全部查询为参数化（`?` 占位），无字符串拼接值；仅 `tracked_metrics` 用占位符拼 `IN (...)`，列名不在拼接范围内 |
| 9 | AI 失败路径 | 无密钥→本地模板且零调用、零日志；超时/网络/4xx/5xx/非 JSON/缺 `text` 字段→全部降级为模板并记 `ai_call_log`；`test_ai.py` 19 条覆盖 |
| 10 | AI 不绕人工确认、不上传原始数据 | `build_summary` 只给"指标名/单位/均值/极值/有效天数/状态"，**不含 record_id、不含原图、不含账号**；AI 只改文案，数字仍来自 `NumberRef`；`test_summary_sent_to_model_contains_no_record_ids` |
| 11 | 设备筛查结果只存原文 | `grep -rn arrhythmia src/ui src/rules` → 无引用；规则引擎 4 条规则均不读该字段；字段只作为计数存 `records` |

---

## 4. 修改文件

| 文件 | 改动 |
|---|---|
| `src/health_agent/backup.py` | `_validate_payload()`（magic 校验）；`restore_backup` 改为"临时文件 → 校验 → 删旧 sidecar → 原子替换"；`_discard()` 保证清理失败不掩盖真实错误 |
| `src/health_agent/ui/app.py` | `MAX_UPLOAD_BYTES` + 上传超限拒绝；`_safe_next()` 修开放重定向 |
| `tests/test_t02_security_backup.py` | **新增** 13 条测试（8 复现 + 5 守门） |

未改：数据库结构、依赖、`auth.py`、`ai_client/`、`config.py`。

---

## 5. 测试结果

| 命令 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest tests/test_t02_security_backup.py` | 修复前 8 条失败 → 修复后 **13 passed** |
| `.venv/Scripts/python.exe -m pytest --basetemp=<新目录>` | **324 passed, 2 warnings in 74.74s，退出码 0** |
| 对比 T01 基线 | 311 passed → 324 passed（+13） |
| 警告 | 2 条，均来自第三方 `fastapi/testclient.py` 的 starlette/httpx 弃用提示，与本项目代码无关 |
| 既有备份测试 | `test_backup.py` 13 条全通过（含 CLI backup/restore、篡改拒绝、无 force 拒绝覆盖） |

---

## 6. 未验证事项（不得视为通过）

| # | 项 | 原因 |
|---|---|---|
| U1 | 真实攻击演练 / 渗透测试 | 本轮只做代码审查与单元级复现，未做网络层扫描与社工链路 |
| U2 | 局域网真机（手机/平板浏览器）访问下的会话与 CSRF | 无第二台设备 |
| U3 | Windows 文件锁下 `os.replace` 的真实失败路径 | 测试环境无法稳定模拟"另一进程独占 DB"；代码已把 `OSError` 转成可读的 `BackupError` |
| U4 | 真实 DeepSeek 接口的鉴权/计费/限流行为 | 无密钥；仍为 OpenAI 兼容形态假设 |
| U5 | 删除 `-wal` 时若有其他进程正在写库 | 依赖"恢复前停止服务"这一人工前提，未做并发验证 |

---

## 7. 剩余风险

1. **恢复必须在服务停止时进行**：`os.replace` 与删除 `-wal` 都假设没有其他进程在写这个库。代码在 Windows 上会因文件占用而报错（可读提示），但**不会自动阻止**。
2. **备份完整性只防意外、不防伪造**（R2）：本地威胁模型下接受，不对外宣称"防篡改"。
3. **`resolve_conflict` 仍无事务回滚**（T01 遗留）：两个 UPDATE 之间没有外部失败源，风险低，但和已修的 `revise_confirmed` 不同构。
4. **密钥文件权限在 Windows 上靠目录权限**（R3）。

---

## 8. 状态

- T02 已完成，状态标记为**待审核**。
- 已更新 `TASK_STATUS.md`（T02 → 待审核）与 `CHANGELOG_WORKBUDDY.md`（追加 T02 条目）。
- **未开始 T03**，等待用户审核与授权。
