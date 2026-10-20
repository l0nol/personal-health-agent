"""把项目打成可直接交付/送审的压缩包。

刻意做四件事：
1. 白名单收集——只收源码、测试、文档、配置样例，绝不把 .venv、data/、.env、数据库打进去。
2. 生成 MANIFEST.json——每个文件的大小与 sha256，加上关键计数，收件方可以独立核对。
3. 生成 00-START-HERE.md——十分钟内能跑完的验证路径 + 可直接执行的静态审查断言。
4. 打完包立刻自检：解压到临时目录跑一遍 pytest，证明包是自洽的（不是"看起来完整"）。

用法：
    python scripts/package.py                # 默认输出到 dist/
    python scripts/package.py --skip-tests   # 跳过打包后自检
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from health_agent import __version__ as VERSION  # noqa: E402  版本只有一处事实源
from health_agent.catalog import METRICS  # noqa: E402
from health_agent.catalog import METRICS  # noqa: E402

PRODUCT = "personal-health-agent"

#: 目录级白名单：整个目录收进来，但按下面的规则过滤。
INCLUDE_DIRS = ("src", "tests", "docs", "test_data", "scripts")

#: 单文件白名单。
INCLUDE_FILES = (
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    ".env.example",
    ".gitignore",
)

#: 任何层级都不允许出现的路径片段。
FORBIDDEN_PARTS = (
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".git",
    "dist",
    "build",
    "*.egg-info",
    "data",
)

#: 任何层级都不允许出现的文件类型。
FORBIDDEN_SUFFIXES = (".db", ".db-wal", ".db-shm", ".pyc", ".zip", ".bak")

MAX_SINGLE_FILE_BYTES = 512 * 1024


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_allowed(path: Path) -> bool:
    rel = path.relative_to(PROJECT_ROOT)
    parts = set(rel.parts)
    if any(part in parts for part in FORBIDDEN_PARTS if not part.startswith("*")):
        return False
    if any(part.endswith("egg-info") for part in parts):
        return False
    if path.suffix in FORBIDDEN_SUFFIXES:
        return False
    if path.name == ".env" or path.name.startswith(".env.") and path.name != ".env.example":
        return False
    return path.is_file()


def collect_files() -> list[Path]:
    selected: list[Path] = []
    for name in INCLUDE_FILES:
        candidate = PROJECT_ROOT / name
        if candidate.is_file():
            selected.append(candidate)
    for directory in INCLUDE_DIRS:
        base = PROJECT_ROOT / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if _is_allowed(path):
                selected.append(path)
    return sorted(set(selected), key=lambda p: p.relative_to(PROJECT_ROOT).as_posix())


def _summary_line(output: str) -> str:
    """pytest 的最后一行常常是 warnings 说明，真正的结论要往上找。"""
    for line in reversed(output.strip().splitlines()):
        stripped = line.strip()
        if re.search(r"\d+ (passed|failed|error)", stripped):
            return stripped.strip("= ")
    return ""


def count_lines(data: bytes) -> int:
    try:
        return data.decode("utf-8").count("\n")
    except UnicodeDecodeError:
        return 0


def run_tests() -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    output = completed.stdout + completed.stderr
    match = re.search(r"(\d+) passed", output)
    failed = re.search(r"(\d+) failed", output)
    return {
        "exit_code": completed.returncode,
        "passed": int(match.group(1)) if match else None,
        "failed": int(failed.group(1)) if failed else 0,
        "summary": _summary_line(output),
    }


def build_manifest(files: list[Path], test_result: dict | None) -> dict:
    entries = []
    total_lines = 0
    for path in files:
        data = path.read_bytes()
        lines = count_lines(data)
        total_lines += lines
        entries.append(
            {
                "path": path.relative_to(PROJECT_ROOT).as_posix(),
                "bytes": len(data),
                "lines": lines,
                "sha256": _sha256(data),
            }
        )

    by_kind: dict[str, int] = {}
    for entry in entries:
        suffix = Path(entry["path"]).suffix.lstrip(".") or "other"
        by_kind[suffix] = by_kind.get(suffix, 0) + 1

    return {
        "product": PRODUCT,
        "version": VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scope": "技术规格书 V1.0 的 P0 范围（任务 0–7）",
        "python": {"minimum": "3.11", "verified_on": sys.version.split()[0]},
        "counts": {
            "files": len(entries),
            "lines": total_lines,
            "by_extension": dict(sorted(by_kind.items())),
            "tests": (test_result or {}).get("passed"),
        },
        "tests": test_result,
        "entrypoints": {
            "verify": "00-START-HERE.md",
            "cli": "python -m health_agent.cli --help",
            "web": "python -m health_agent.cli serve",
            "tests": "python -m pytest",
        },
        "excluded": [
            ".venv/（本地虚拟环境）",
            "data/（运行时数据库与上传缓存，且可能含真实健康数据）",
            "*.db / *.db-wal / *.db-shm（数据库文件）",
            ".env（真实密钥）——仅提供 .env.example",
            "__pycache__/ .pytest_cache/ *.egg-info/ dist/",
        ],
        "files": entries,
    }


START_HERE = """# 00 · 从这里开始

这是一个**本地优先的个人健康数据管理工具**的完整交付包（v0.1.2）。
P0 功能（任务 0–7）已完成；界面为 FastAPI + Jinja2 服务端渲染，图表是手写 SVG，无前端框架、无图表库。
本文件是给审阅者/验证者的入口，10 分钟内可以走完。

> **它不是医疗产品**：只做趋势观察与生活习惯提示，不做诊断、不做治疗建议。
> 所有数字都来自用户确认过的记录，缺失就是缺失，不一概按 0 处理。

---

## 一、10 分钟验证路径

### 1. 装依赖并跑测试（唯一必须先做的事）

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt   # macOS/Linux 用 .venv/bin/python
.venv/Scripts/python.exe -m pytest
```

预期最后一行：`@@TESTS@@ passed`。若数字对不上，说明包被改动过——用 `MANIFEST.json` 核对 sha256。

### 2. 跑一遍真实的导入→确认→报告链路

```bash
.venv/Scripts/python.exe -m health_agent.cli init --db demo.db
.venv/Scripts/python.exe -m health_agent.cli import test_data/sample_daily_metrics.json --db demo.db
# ↑ 只预览：应显示 21 行，可直接确认 20，待复核 1，拒绝 0，且**没有写库**

.venv/Scripts/python.exe -m health_agent.cli import test_data/sample_daily_metrics.json --db demo.db --commit
.venv/Scripts/python.exe -m health_agent.cli stats --db demo.db
# ↑ 应为 21 条，全部 pending，confirmed 0
```

### 3. 看界面

```bash
.venv/Scripts/python.exe -m health_agent.cli serve --db demo.db
# 浏览器打开 http://127.0.0.1:8000
```

按顺序看：`/imports` 上传 → 预览 → 写入；`/records` 确认；`/reports` 看简报与来源；
`/alerts` 点「立即评估」。

新界面（0.1.2）：

- `/` 今日摘要：4 张指标卡（值/单位/有效天数/状态点）、睡眠柱状图 + 目标虚线、待处理
- `/sleep` 睡眠分析：缺失日画空心柱；阶段比例只显示设备给的，不换算
- `/trends` 指标趋势：选 `hrv_min_ms` 或 `spo2_min_percent` 会看到**区间带**，且统计区没有"均值"
- `/chronos` 时钟模式 Demo：6 扇区外环 + 中心摘要 + 普通入口（键盘 Tab 可达）

`docs/preview/` 下有三份静态 HTML（内联样式），不装 Python 也能看版式。

---

## 二、可直接执行的静态审查断言

这些不是"请注意"，是可以在解压后的根目录直接跑的命令，每条都给了预期结果。

| # | 命令 | 预期 | 为什么重要 |
|---|---|---|---|
| 1 | `grep -rn "arrhythmia" src/health_agent/rules/` | 无输出 | 规则引擎不得解读心律筛查结果 |
| 2 | `grep -rn "fillna(0)" src/` | 无输出 | 缺失值不得被填成 0 |
| 3 | `grep -rn "def logical_key\\|def dedup_key" src/` | 只有 `storage/keys.py` 两处 | 去重定义只能有一份实现 |
| 4 | `grep -rn "sk-" src/` | 无输出 | 源码里不得出现密钥字面量 |
| 5 | `grep -h "^def test_" tests/*.py \\| sort \\| uniq -d` | 无输出 | 同名测试会静默覆盖，必须无重复 |
| 6 | `grep -rn "%\\", \\"min\\"" src/health_agent/validation/units.py` | 无 `("%", "min")` 换算项 | 比例不得换算成时长 |
| 7 | `grep -rn "logger\\|print(" src/health_agent/ai_client/` | 不出现密钥变量 | AI 层不得把密钥写日志 |
| 8 | `grep -rn "diagnos\\|诊断" src/health_agent/rules/` | 无输出 | 提醒不做医学判断 |
| 9 | `grep -rn -e "chart.js" -e "echarts" -e "plotly" src/` | 无输出 | 图表是手写 SVG，没有图表库 |
| 10 | `grep -rn "range_bound" src/health_agent/catalog.py` | 出现 `in_report=False` 与断言 | 区间端点不得进报告 |
| 11 | `grep -rn "pending" src/health_agent/analytics/` | 无输出 | 统计层不得读取待确认数据 |

第 1、3、5、6 条有对应的自动化测试，静态检查只是给审阅者的快速抓手。

---

## 三、包内有什么

```
00-START-HERE.md       ← 你正在看的
MANIFEST.json          文件清单 + 每个文件的 sha256 + 关键计数
README.md              产品说明、进度、限制
pyproject.toml         包元数据（pytest 配置里已设 pythonpath=src）
requirements.txt       运行时依赖（5 个）
requirements-dev.txt   开发依赖（+pytest，版本锁死）
.env.example           全部配置项，含注释；不含任何真实密钥
src/health_agent/      源码（约 4.4k 行）
  catalog.py           @@METRICS@@ 个指标的唯一事实源
  validation/          单位白名单、单行校验（纯函数）
  storage/             SQLite 迁移、repository、状态机、备份
  ingestion/           读文件 → 五桶预览 → 落库
  analytics/           窗口聚合、基线（中位数+MAD）、数据质量
  rules/               4 条提醒规则 + 去重/冷却
  ai_client/           接口契约、HTTP 客户端、缓存、降级
  reports/             日报周报 + 数字追溯
  ui/                  FastAPI + Jinja2；charts.py 是纯几何 SVG；10 个模板 + 宏
tests/                 @@TESTS@@ 条测试，8 个文件
test_data/             2 份虚构样例（正常 + 10 条各类异常）
docs/
  00-architecture-plan-v1.md   方案与 4 个规格冲突的裁决 + 修订记录
  01-data-dictionary.md        指标字典（由 catalog.py 派生）
  02-runbook.md                安装/备份/恢复/排障
  03-tablet-access.md          平板访问：已验证 vs 未验证
  04-ui-design-spec-v1.md      UI 设计规范（含实施状态与偏差）
  05-handoff.md                **交接说明：现状、约定、下一步**（新对话先读它）
  preview/                     三份静态页面预览（内联样式）
scripts/package.py     本交付包就是它打的，可复现
scripts/gen_data_dictionary.py  数据字典重生成
```

---

## 四、数字从哪来（可核对的设计约束）

- **只统计 `confirmed`**：未确认的数据不进趋势、不进基线、不进提醒。
- **样本不足就说不足**：日趋势 <3 天、周报 <4/7 天，一律输出"数据不足"，不给均值、不给环比。
- **报告数字可追溯**：每个数字要么带 `record_ids`（能点开看原始记录），要么带
  `derived_from + window`（说明从哪段窗口算的）。两者都没有的数字进不了报告，有测试守着。
- **不覆盖已确认数据**：同一来源+日期+指标+周期最多一条 confirmed，由数据库部分唯一索引强制；
  冲突只能新建 pending 并登记，等人裁决。
- **AI 不碰数字**：只把算好的摘要改写成通顺的话，输出强制 `{"text": "..."}`，
  解析失败就退回本地模板；没有密钥时完全离线可用。

---

## 五、这个包**没有**解决的问题（请重点审这里）

1. **指标合理值域未与华为官方字段逐项核对**——可能误拒合法值。这是最该先修的一条。
2. **逻辑键只到日期粒度**：同来源同日第二条瞬时测量（如早晚各一次血压）会进冲突流程。
   P0/P1 指标都是日汇总，不受影响；启用 P2 血压/体温/体重前必须先改。
3. **平板端零真机验证**：只有服务端行为证据，触控/布局/下载全未测。见 `docs/03-tablet-access.md`。
4. **DeepSeek 接口是假设**：实现的是 OpenAI 兼容形态，端点/模型可配，但未经真实接口确认。
5. **AI 费用记 `unavailable`**：拿不到真实账单，不做估算。
6. 无 HTTPS、单密码鉴权、无多用户；不要暴露到公网。

---

## 六、接手继续做的话

先读 `docs/05-handoff.md`：里面有当前真实状态、不可破坏的 8 条约定、
关键决策的理由、已知限制和下一步优先级。看完再动代码。

## 七、要审代码的话，建议顺序

1. `src/health_agent/catalog.py` —— 指标定义与命名，后面的东西都依赖它
2. `src/health_agent/validation/rules.py` —— 什么情况拒绝、什么情况只标记
3. `src/health_agent/ingestion/preview.py` —— 五桶分桶，总数必须守恒
4. `src/health_agent/storage/migrations/001_init.sql` —— 部分唯一索引是"不覆盖"的实现依据
5. `src/health_agent/analytics/aggregate.py` + `reports/builder.py` —— 统计口径与追溯
6. `src/health_agent/rules/engine.py` —— 提醒条件与冷却
7. `src/health_agent/ai_client/summarizer.py` —— 降级路径
"""


def render_template(text: str, manifest: dict) -> str:
    """把模板里的占位符换成实际数字，避免测试数/指标数一变就要手改文档。"""
    tests = (manifest.get("tests") or {}).get("passed")
    return (
        text.replace("@@TESTS@@", str(tests if tests is not None else "未运行"))
        .replace("@@METRICS@@", str(len(METRICS)))
    )


def render_template(text: str, manifest: dict) -> str:
    """把模板里的占位符换成实际数字，避免测试数/指标数一变就要手改文档。"""
    tests = (manifest.get("tests") or {}).get("passed")
    return (
        text.replace("@@TESTS@@", str(tests if tests is not None else "未运行"))
        .replace("@@METRICS@@", str(len(METRICS)))
    )


def write_distribution(target: Path, files: list[Path], manifest: dict) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for path in files:
        relative = path.relative_to(PROJECT_ROOT)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)

    (target / "00-START-HERE.md").write_text(render_template(START_HERE, manifest), encoding="utf-8")
    (target / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def zip_distribution(source: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                bundle.write(path, arcname=path.relative_to(source.parent).as_posix())


def self_check(archive: Path) -> dict:
    """解压到临时目录跑一遍测试，证明包本身自洽。"""
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(tmp)
        roots = [p for p in Path(tmp).iterdir() if p.is_dir()]
        if len(roots) != 1:
            raise RuntimeError(f"压缩包内应有且只有一个顶层目录，实际 {len(roots)} 个")
        root = roots[0]

        # 先确认 pytest 真的会导入"解压后"的源码，而不是本地 editable 安装的那份
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, 'src'); import health_agent; print(health_agent.__file__)",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        imported_from = probe.stdout.strip()
        if str(root) not in imported_from:
            raise RuntimeError(f"自检导入了错误的源码：{imported_from}")

        # 先确认 pytest 真的会导入"解压后"的源码，而不是本地 editable 安装的那份
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, 'src'); import health_agent; print(health_agent.__file__)",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        imported_from = probe.stdout.strip()
        if str(root) not in imported_from:
            raise RuntimeError(f"自检导入了错误的源码：{imported_from}")

        completed = subprocess.run(
            [sys.executable, "-m", "pytest"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=600,
        )
        output = completed.stdout + completed.stderr
        match = re.search(r"(\d+) passed", output)
        return {
            "extracted_dir": root.name,
            "imported_from": imported_from,
            "exit_code": completed.returncode,
            "passed": int(match.group(1)) if match else None,
            "summary": _summary_line(output),
        }


BUNDLE_HEADER = """# {product} v{version} —— 源码合订本

> 生成时间：{generated_at}
> 共 {files} 个文件 / {lines} 行；测试 {tests}
> 每个文件用四反引号包裹，路径见 `===== FILE: ... =====`。
> 这是给"只能读文本、不能跑代码"的审阅环境准备的；需要实际运行时请用同名 zip。
"""


def build_bundle(files: list[Path], manifest: dict) -> str:
    parts = [
        BUNDLE_HEADER.format(
            product=manifest["product"],
            version=manifest["version"],
            generated_at=manifest["generated_at"],
            files=manifest["counts"]["files"],
            lines=manifest["counts"]["lines"],
            tests=(manifest["tests"] or {}).get("summary") or "未运行",
        )
    ]
    for path in files:
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            parts.append("\n===== FILE: " + relative + " =====  （二进制文件，已跳过）\n")
            continue
        language = {
            ".py": "python", ".sql": "sql", ".html": "html", ".css": "css",
            ".md": "markdown", ".toml": "toml", ".json": "json", ".csv": "csv",
        }.get(path.suffix, "text")
        parts.append("\n===== FILE: " + relative + " =====\n")
        parts.append("````" + language + "\n" + text.rstrip() + "\n````\n")
    return "".join(parts)


VERIFY_PROMPT = """# 给审阅模型的验证指令（可直接粘贴）

你收到的是 `personal-health-agent` v0.1.0 的交付包（或它的源码合订本）。
这是一个本地优先的个人健康数据管理工具，请按下面的顺序验证，**不要改代码**，只出结论。

## 背景边界（先接受这三条，再评判）

1. 它**不是医疗产品**：只做趋势观察与生活习惯提示，不做诊断、不做治疗建议。
2. 数据缺失一律是 `NULL`，不允许填 0、不允许模型猜数值。
3. 未确认（pending）的数据不得进入任何统计、报告或提醒。

## 第一轮：能不能跑（如果环境允许执行）

```
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m pytest
```

预期 `@@TESTS@@ passed`。若数字不同，用 `MANIFEST.json` 里的 sha256 核对文件是否被改动。

## 第二轮：规格符合性（逐条给"符合/不符合/无法判断 + 证据位置"）

| # | 规格要求 | 去哪儿看 |
|---|---|---|
| 1 | 导入先预览，用户确认后才成为正式记录 | `ingestion/preview.py`、`ingestion/batch.py` |
| 2 | 重复导入提示去重，不覆盖已确认记录 | `storage/migrations/001_init.sql` 的部分唯一索引、`storage/lifecycle.py` |
| 3 | 冲突记录并存，由人裁决 | `storage/lifecycle.py::resolve_conflict` |
| 4 | 缺失值用 null，不解释为 0 | `validation/rules.py`、`analytics/aggregate.py` |
| 5 | 比例不得换算成时长 | `validation/units.py` 的 `CONVERSIONS` |
| 6 | 报告里每个数字可追溯到记录 | `reports/builder.py::NumberRef` |
| 7 | AI 失败时本地功能仍可用 | `ai_client/summarizer.py`、`reports/builder.py` |
| 8 | 心律筛查只保存设备原文，不生成诊断 |
| 9 | 区间端点指标只画区间带，不显示均值 | `/trends?metric=hrv_min_ms`、`ui/charts.py::build_band` |
| 10 | 缺失日不连线、不补 0 | `ui/charts.py`、`tests/test_charts.py` |
| 11 | 待确认数据不进趋势与图表 | `analytics/aggregate.py` 的 `verification_status = 'confirmed'` |
| 12 | 时钟模式有等价的普通导航 | `templates/chronos.html` 的「普通入口」区块 | `rules/engine.py`（源码中不应出现 arrhythmia） |

## 第三轮：找真问题（这是最有价值的部分）

请重点检查以下几类，**每条都要给出文件:行号**：

- 有没有路径能让 `pending` 数据进入统计？
- 部分唯一索引能否被绕过（比如先 supersede 再插入，是否会留下两条 confirmed）？
- 单位换算是否存在精度或语义错误（例如把百分比乘成分钟）？
- 报告数字的追溯链是否真的闭合（`derived_from` 是否可能指错窗口）？
- 备份恢复是否存在数据丢失窗口？
- 密钥、密钥派生参数、会话签名是否有明显弱点？
- 测试是否在"自证"（只测实现细节、没测行为）？

## 输出格式

```
结论：可交付 / 需修改后交付 / 不可交付
阻断问题：N 条（每条：文件:行号 + 问题 + 为什么阻断）
一般问题：N 条
无法判断：N 条（说明缺什么信息）
```

不要因为"测试全绿"就给通过——测试是我写的，可能和实现一样错。
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打包交付物")
    parser.add_argument("--out", default="dist", help="输出目录（默认 dist/）")
    parser.add_argument("--skip-tests", action="store_true", help="跳过测试与解压自检")
    args = parser.parse_args(argv)

    files = collect_files()
    oversized = [
        f for f in files if f.stat().st_size > MAX_SINGLE_FILE_BYTES
    ]
    if oversized:
        print("以下文件超过 512KB，不适合送审，请确认：", file=sys.stderr)
        for path in oversized:
            print(f"  - {path.relative_to(PROJECT_ROOT)}", file=sys.stderr)
        return 1

    test_result = None if args.skip_tests else run_tests()
    manifest = build_manifest(files, test_result)

    out_dir = (PROJECT_ROOT / args.out).resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    target = out_dir / f"{PRODUCT}-{VERSION}"
    write_distribution(target, files, manifest)

    bundle = out_dir / f"{PRODUCT}-{VERSION}.bundle.md"
    bundle.write_text(build_bundle(files, manifest), encoding="utf-8")
    (out_dir / "VERIFY-PROMPT.md").write_text(
        render_template(VERIFY_PROMPT, manifest), encoding="utf-8"
    )

    bundle = out_dir / f"{PRODUCT}-{VERSION}.bundle.md"
    bundle.write_text(build_bundle(files, manifest), encoding="utf-8")
    (out_dir / "VERIFY-PROMPT.md").write_text(
        render_template(VERIFY_PROMPT, manifest), encoding="utf-8"
    )

    archive = out_dir / f"{PRODUCT}-{VERSION}.zip"
    zip_distribution(target, archive)
    digest = _sha256(archive.read_bytes())
    (out_dir / f"{PRODUCT}-{VERSION}.zip.sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )

    print(f"打包目录: {target}")
    print(f"压缩包  : {archive}  ({archive.stat().st_size / 1024:.0f} KB)")
    print(f"sha256  : {digest}")
    print(f"合订本  : {bundle}  ({bundle.stat().st_size / 1024:.0f} KB)")
    print(f"文件数  : {manifest['counts']['files']}  行数: {manifest['counts']['lines']}")
    if test_result:
        print(f"测试    : {test_result['summary']}")

    if not args.skip_tests:
        check = self_check(archive)
        print(f"解压自检: {check['summary']}（目录 {check['extracted_dir']}）")
        if check["exit_code"] != 0:
            print("解压后测试未通过，交付包不可用", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
