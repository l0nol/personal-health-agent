# 工作台搭建清单（前置条件 · 剩余任务）

> 用途：确认"要跑起来还缺什么"。代码侧已就绪（T06 验收结论 A），
> 剩下的是**数据、设备、配置**三类前置。本文所有"已具备"项都在 2026-10-20 实测过。

---

## 1. 现在就能跑（三行命令）

```bash
cd personal-health-agent
.venv/Scripts/python.exe -m health_agent.cli init      # 首次：建库 + 37 个指标
.venv/Scripts/python.exe -m health_agent.cli serve     # 打开 http://127.0.0.1:8000
```

不需要服务器、不需要云数据库、不需要联网。数据库首次启动自动创建。

---

## 2. 前置条件清单

| # | 前置 | 用途 | 现状 | 缺了怎么办 |
|---|---|---|---|---|
| 1 | Python 3.13 | 运行 | ✅ 已具备（项目 `.venv`，3.13.14） | 装 Python 3.12+ 后 `python -m venv .venv` |
| 2 | 依赖包 | 运行 | ✅ 已装（`requirements-dev.txt`） | `.venv/Scripts/python.exe -m pip install -r requirements-dev.txt` |
| 3 | 数据库 | 存数据 | ✅ 自动建（11 表 / 37 指标） | 删 `data/health.db` 后重跑 `init` |
| 4 | 浏览器 | 看界面 | ✅ Chrome / Edge / 华为浏览器 | 建议 Chromium 105+（表盘用到 `aspect-ratio`、`:focus-visible`） |
| 5 | 访问密码 | 局域网/平板访问 | ⬜ 可选，本机用不需要 | `.venv/Scripts/python.exe -m health_agent.cli passwd`，把输出写入 `.env` |
| 6 | 局域网开启 | 平板访问 | ⬜ 默认关闭 | `.env` 加 `HEALTH_AGENT_LAN=1` + 密码，重启 |
| 7 | 平板 | 触控与窄屏实测 | ⬜ **缺设备** | 有 Android 平板 + 同一 Wi-Fi 即可；步骤见 `docs/03-tablet-access.md` |
| 8 | 真实健康数据 | 替换示例数据 | ⬜ **缺素材** | 华为运动健康截图 → 按下表字段提取 CSV/JSON |
| 9 | DeepSeek 密钥 | 简报 AI 改写 | ⬜ 可选，不填就用本地模板文案 | `.env` 加 `DEEPSEEK_API_KEY=...` |
| 10 | 备份目录 | 数据安全 | ✅ 自动（`data/backup/`） | 已实测：`cli backup` 生成 zip + manifest（含 sha256） |

**只要 1–4 具备就能在本机完整使用；5–7 是平板场景的前置；8 是"从示例数据换成你自己的数据"的前置；9 完全可选。**

---

## 3. 真实数据的字段格式（前置 #8 的具体要求）

CSV 表头固定六列，字段名按技术规格 §4（不是华为导出格式）：

```csv
metric_name,record_date,value,unit,period_type,note
night_sleep_minutes,2026-10-19,395,min,day,截图：睡眠页 6h35m
resting_hr_bpm,2026-10-19,58,bpm,day,截图：心率页静息
hrv_min_ms,2026-10-19,47,ms,day,截图：HRV 区间下限
hrv_max_ms,2026-10-19,82,ms,day,截图：HRV 区间上限
```

- 值看不清就写 `--`（会进"待复核"，不会当 0）
- 单位只接受白名单（min/h/bpm/%/ms/step/kcal…），写错会被拒绝并给错误码
- 时段型指标（如整周记录）要带 `period_start`/`period_end`
- 指标全表见 `docs/01-data-dictionary.md`

---

## 4. 从发布产物试用（不想装开发环境时）

```bash
# 解压 dist/personal-health-agent-0.1.2.zip 后，在解压目录执行：
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pip install -e .
.venv/Scripts/python.exe -m health_agent.cli init
.venv/Scripts/python.exe -m health_agent.cli serve
```

2026-10-20 实测记录（用已有 venv + `PYTHONPATH=src`，未重复装依赖）：

| 步骤 | 结果 |
|---|---|
| 解压产物 | 110 文件 |
| `cli init` | 建库成功，指标注册表 37 条 |
| `cli import sample_with_issues.csv` | 预览正常 → `--commit` 入库 6 条 pending / 拒绝 4 |
| `cli backup` | 生成 zip + manifest（sha256） |
| `cli serve` + 11 个端点 | `/ /sleep /trends /chronos /records /imports /alerts /settings /healthz /export.csv /static/css/tokens.css` **全部 200** |

---

## 5. 搭好后逐条验收（5 分钟）

- [ ] 打开 `http://127.0.0.1:8000` → 看到"今日摘要"与 4 张指标卡
- [ ] 指标卡有"参照范围"行：数据不足时写"数据不足"，不会显示假区间
- [ ] `/imports` 上传 CSV → 预览页五桶顺序为 冲突 → 待复核 → 拒绝 → 重复 → 可直接确认
- [ ] 写入后到 `/records` 逐条"确认" → 数据才出现在 `/trends`
- [ ] `/chronos` 点扇区只预览，点「进入」才跳转；系统开"减少动态"后不旋转
- [ ] `/settings` 阈值表只读、显示环境变量名；"数据与隐私"能导出 CSV/JSON
- [ ] `cli backup` 生成备份；`cli restore <zip>` 能恢复（覆盖现有库要加 `--force`，会先留 `.bak`）
- [ ] 平板：同一 Wi-Fi + `HEALTH_AGENT_LAN=1` + 密码，触控无横向滚动

---

## 6. 剩余任务（按性质分类，不混在一起）

### 6.1 待你审核（代码已完成）

| 任务 | 状态 | 说明 |
|---|---|---|
| T06 最终回归与交付验收 | 待审核 | 结论 A；`reports/T06_FINAL_ACCEPTANCE_REPORT.md` |
| 模块化与可美化接口（追加） | 待审核 | `docs/06-ui-extension-guide.md` |

### 6.2 受前置条件限制（不是代码没写）

| 事项 | 卡在哪 | 需要什么 |
|---|---|---|
| 平板真机验证 | 缺设备 | Android 平板（Chrome / 华为浏览器） |
| 真实截图全链路 | 缺素材 | 1–2 张华为运动健康截图 → 按 §3 提取 CSV |
| 指标 `provisional` → `device_verified` | 同上 | 每核对一个字段，改 `catalog.py` 的 `verification` 并记录依据 |
| AI 摘要真实调用 | 缺密钥 | DeepSeek API Key（不填也能用） |

### 6.3 需要你拍板的功能（有验收口径，但会动数据/规则语义）

| 任务 | 验收口径 | 风险 |
|---|---|---|
| 逻辑键加入观测时刻 | 同日两次血压可并存、各自可确认 | 改唯一性定义 + 表结构迁移，会影响导入去重 |
| 提醒「不相关」反馈联动降频 | 连续 3 次不相关后该规则冷却延长，有测试 | 改规则冷却语义，需确认"连续"的窗口 |
| HTTPS + 多人访问 | — | 当前是单密码 + 本机/局域网，上公网必须先做这个 |

### 6.4 明确不做（避免误会）

- 不做医疗诊断/风险预测；不做"健康总分"
- 不接华为隐私中心加密包通道（数据源已裁决为截图提取）
- 不做深色主题（D-1 裁决：浅色即可，深色 token 预留但不启用）
- 不引入前端框架/构建链（零 JS 是当前基线，加脚本走 `base.html` 的 `scripts` 块）

---

## 7. 一句话结论

**代码和发布包已就绪；要在本机用起来，只缺"你自己的数据"。**
平板与 AI 密钥是可选增强，不是前置。
