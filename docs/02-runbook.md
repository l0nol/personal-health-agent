# 运维手册（Runbook）

> 首次搭建/换机器：先看 `docs/07-workbench-checklist.md`（前置条件与验收清单）。

> 适用版本 0.1.0 · 平台 Windows（其他平台命令换成对应路径分隔符即可）
> 所有命令都在项目根目录执行。

---

## 1. 安装

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m pip install -e .
```

- `requirements.txt` 是运行时依赖（FastAPI / uvicorn / Jinja2 / python-multipart / httpx）。
- `requirements-dev.txt` 额外装 pytest（版本已锁）。
- `pip install -e .` 让 `python -m health_agent.cli` 可以直接用，不需要设 `PYTHONPATH`。

验证安装：

```bash
.venv/Scripts/python.exe -m pytest        # 预期 272 passed
```

## 2. 首次启动

```bash
cp .env.example .env          # 按需修改；不填密钥也能完整使用
.venv/Scripts/python.exe -m health_agent.cli init
.venv/Scripts/python.exe -m health_agent.cli serve
```

浏览器打开 `http://127.0.0.1:8000`。

`init` 会建库并把 40 个指标写进 `metrics_catalog`，幂等，可重复执行。

## 3. 日常流程

1. **导入**：`/imports` → 选文件 → 「上传并预览」→ 检查五个分区 → 「写入 N 条」
2. **确认**：`/records` 逐条确认，或 `/imports` 里「确认全部」（会跳过无值/冲突的记录）
3. **看报告**：`/reports` 每日简报 / 每周报告；每个数字可展开看来源记录
4. **看提醒**：`/alerts` → 「立即评估」
5. **处理冲突**：`/conflicts` 选择「保留已有」或「采用新值」

导入永远只写 `pending`，确认后才进入趋势、报告与提醒。

## 4. 备份

```bash
.venv/Scripts/python.exe -m health_agent.cli backup
# 已备份: data\backup\health-20261019-220842.zip
```

- 备份目录默认 `data/backup/`，可用 `--out` 指定。
- 包内两个文件：`health.db`（一致性快照）+ `manifest.json`（时间、schema 版本、sha256、各状态记录数）。
- 快照通过 SQLite 的 backup API 生成，**不是**直接复制文件——WAL 模式下直接复制可能拿到半个事务。

建议频率：每周一次；批量导入前、升级前各一次。备份目录请放在与数据库不同的磁盘（或同步到云盘），否则磁盘坏了两份一起丢。

## 5. 恢复

```bash
# 恢复到新路径（推荐先这样验证备份可用）
.venv/Scripts/python.exe -m health_agent.cli restore data/backup/health-20261019-220842.zip --db data/restored.db

# 覆盖现有数据库（会先自动留一份 .bak-时间戳）
.venv/Scripts/python.exe -m health_agent.cli restore data/backup/health-20261019-220842.zip --db data/health.db --force
```

安全机制：

- 恢复前校验 `manifest.db_sha256`，内容被改过直接拒绝；
- 目标库已存在且未加 `--force` 时**拒绝执行**；
- 覆盖前把原库复制为 `<db>.bak-<时间戳>`；
- 恢复后执行 `PRAGMA integrity_check`，不通过则报错。

### 实测记录（2026-10-19）

```
导入 21 条 + 确认 20 条 → 备份 health-20261019-220842.zip
删除 data/runbook.db（确认文件已不存在）
restore → 已恢复: data\runbook.db    备份时间: 2026-10-19T22:08:42   记录数: 21

对照指纹（记录数, 审计条数, 导出内容 sha256）：
  备份前: 21, 43, 3d2f77766feed241
  恢复后: 21, 43, 3d2f77766feed241
```

## 6. 导出

```bash
.venv/Scripts/python.exe -m health_agent.cli export --format csv  --out data/records.csv
.venv/Scripts/python.exe -m health_agent.cli export --format json --out data/records.json
```

也可以直接在界面 `/export.csv`、`/export.json` 下载。CSV 带 BOM，Excel 打开中文不乱码。

## 7. 局域网访问（平板 / 手机）

```bash
.venv/Scripts/python.exe -m health_agent.cli passwd     # 生成密码 hash
```

把输出的 `HEALTH_AGENT_PASSWORD_HASH=...` 写进 `.env`，同时设置：

```ini
HEALTH_AGENT_LAN=1
HEALTH_AGENT_HOST=0.0.0.0
```

重启服务，用 `http://<电脑局域网 IP>:8000` 访问。

**没有密码时程序会拒绝在局域网模式启动**——这是刻意的，健康数据不该无鉴权暴露在同网段。

风险提示：本版本没有 HTTPS，局域网内流量是明文。只在自己信任的家庭网络使用，不要在公共 Wi-Fi 或办公室开放网络下开启。

## 7.5 换 AI 服务商 / 验证密钥

两条路等价，选一条就行。

**界面（推荐，免重启）**：`设置 → AI 分析接口`

1. 选服务商（DeepSeek / 通义千问 / Kimi / 智谱 / OpenAI / 本地 Ollama）；
2. 地址和模型**留空**就是该服务商的预设值，自己填了以你填的为准；
3. 粘贴密钥 → 点「测试连接」先试一次（只发一句"回复两个字"，不保存、不写记录）；
4. 成功后再点「保存」——写回 `.env` 并立即生效，页面只显示密钥尾号。

**模型名报"不存在"怎么办**：点「保存并拉取可用模型」。它会先保存当前地址与密钥，
再请求该地址的 `GET /models`，把**这个密钥实际能用**的模型列成下拉框，选中点一次即换、自动试连。
"模型不存在"十有八九不是模型名写错，而是地址与密钥不配套（A 家的密钥配了 B 家的地址）。

**自由提问**：`AI 助手` 页（`/assistant`），想怎么问就怎么问。发给模型的只有程序算好的
聚合数字（近 7 天指标、逐日睡眠、数据缺口），不含 record_id、截图或账号信息；
没配密钥时用「本地整理」，调用失败也退回本地整理并说明原因。答案只存在内存里，重启即清空。

**手改 `.env`（需要重启）**：

```bash
# 只改这三行即可，老名字 DEEPSEEK_* 也仍然有效（同时存在时 AI_* 优先）
AI_API_KEY=sk-...
AI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AI_MODEL=qwen-plus
```

排查要点：

| 现象 | 原因 | 处理 |
|---|---|---|
| 提示「接口地址缺协议头」或「接口地址是空的」 | 地址没填完整（选「自定义」时尤其常见） | 现在只写域名会自动补成 `https://`；本机地址补 `http://`；实在空着就选一个「服务商」预设 |
| 提示「这一格填的像是 API 密钥」 | 密钥粘到了「接口地址」那一格 | 把密钥挪到下面的「API 密钥」格 |
| 测试连接报 401/403 | 密钥无效、已过期或没权限用这个模型 | 换密钥，或换一个该账号可用的模型名 |
| 提示「模型 XXX 在该地址不存在」 | 地址与密钥不配套，或模型名过时 | 点「保存并拉取可用模型」，从列表里挑一个 |
| 提示「模型把 max_tokens 用完就停了，正文是空的」 | 用的是**推理模型**（先想再答），预算被思考吃掉 | 正常现象，不影响保存；试连已自动按"连通成功"处理。真要看输出就换普通的非推理模型 |
| 提示「响应不是 JSON」并附一段原文 | 地址指向了网页/网关拦截页，不是 API | 照原文修地址（多半少了 `/v1`，或该地址根本不是 API） |
| 测试连接报 404 | 地址少了 `/v1` 之类的路径段 | 对照设置页「各服务商参考地址」补全 |
| 测试连接报 429 | 限流或余额不足 | 稍后重试 / 充值 |
| 保存成功但模型没变 | 启动前 `export` 过同名环境变量，环境变量优先 | 页面会明确提示是哪几个变量；去掉后重启 |
| 测试通过但报告没变 | 简报有缓存，只在新数据或主动点「用 AI 改写」时重算 | 正常行为（避免静默花钱） |

## 8. 故障排查

| 症状 | 可能原因 | 处理 |
|---|---|---|
| 启动报「未设置 HEALTH_AGENT_PASSWORD_HASH」 | `HEALTH_AGENT_LAN=1` 但没配密码 | 跑 `passwd` 生成并写入 `.env` |
| 端口被占用 | 上次进程没退干净 | `netstat -ano \| findstr :8000` 找到 PID 后 `taskkill /PID <pid> /F`，或改 `HEALTH_AGENT_PORT` |
| 导入报「文件编码无法识别」 | 文件是 GBK/UTF-16 且解码失败 | 用记事本另存为 UTF-8，再重新导入 |
| 导入后全是「重复跳过」 | 这份文件已经导入过 | 正常行为；确实要重导就先撤销旧批次 |
| 记录一直是 pending | 还没确认 | `/records` 确认，或 `/imports` 点「确认全部」 |
| 报告显示「数据不足」 | 有效记录少于 3 天（周报 4/7 天） | 补录或继续导入；系统不会用 3 天数据编趋势 |
| 提醒说「默认参考值」 | 没配 `HEALTH_AGENT_SLEEP_TARGET_MIN` | 这是如实告知：420 分钟只是参考值，未按年龄调整；设成自己的目标即可 |
| 提醒点了「立即评估」没有新增 | 已提醒过（同窗口去重）或在冷却期内 | 正常；冷却期默认 72 小时，可用 `HEALTH_AGENT_RULE_COOLDOWN_HOURS` 调 |
| 「用 AI 改写」报错 | 密钥无效 / 网络不通 / 输出不合规 | 报告会退回本地文案，功能不受影响；看日志表 `ai_call_log.error_code` |
| 页面样式在平板上错乱 | 见 `docs/03-tablet-access.md` | 目前只保证 640px 以下有断点，未做真机触控优化 |

日志位置：本版本不落文件日志，运行信息在控制台。AI 调用记录在数据库 `ai_call_log` 表：

```bash
.venv/Scripts/python.exe -c "import sqlite3;c=sqlite3.connect('data/health.db');c.row_factory=sqlite3.Row;[print(dict(r)) for r in c.execute('SELECT created_at,model,status,error_code,total_tokens FROM ai_call_log ORDER BY created_at DESC LIMIT 10')]"
```

## 9. 升级

1. 备份（第 4 节）
2. `git pull`（或覆盖源码）
3. `.venv/Scripts/python.exe -m pip install -r requirements-dev.txt`
4. `.venv/Scripts/python.exe -m health_agent.cli init` —— 会自动应用新迁移，幂等
5. 打开界面抽查：记录数、报告、提醒

数据库迁移只增不改，回滚需用第 5 节的备份恢复。

## 10. 删除数据

- 删单条：`/records` → 「删除」（写审计日志）
- 删整个库：停服务后删除 `data/health.db*` 与 `data/uploads/`、`data/backup/`

## 11. 已知限制

- 无 HTTPS、无多用户、无「忘记密码」（hash 存 `.env`，丢了就重设）
- 提醒只有站内列表，没有桌面通知或推送
- AI 费用拿不到真实账单，`ai_call_log.cost_source` 恒为 `unavailable`
- 逻辑键只到日期粒度，同来源同日多次瞬时测量会走冲突流程（P2 指标启用前需修）
- 指标合理值域未与华为官方字段逐项核对，可能误拒合法值
