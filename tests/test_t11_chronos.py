"""星环模块面板：内容、空态、诚实边界与拖拽增强。

重点不是"页面能打开"，而是四件事：
1. 六个面板的数据都来自已有的统计函数（与对应页面同一份），不另算一套；
2. 数据不足时给空态，不用示例值填充，也不把"没有记录"写成 0；
3. AI 没配置就如实说没配置，不用假对话冒充能力；
4. 拖拽只是增强：没有脚本也能点、能按方向键。
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from health_agent.config import load_settings
from health_agent.storage.db import init_db
from health_agent.storage.lifecycle import confirm_record
from health_agent.storage.repository import insert_record, seed_catalog
from health_agent.training import service as training_service
from health_agent.ui.app import create_app
from health_agent.ui.pages import RING_MODULES

from conftest import make_record, read_css

TODAY = date.today()
TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "src" / "health_agent" / "ui" / "templates" / "chronos.html"
)


def _client(tmp_path) -> TestClient:
    return TestClient(
        create_app(load_settings({}, project_root=tmp_path), db_path=tmp_path / "ui.db")
    )


def _seed(conn_db_path: Path, metric: str, unit: str, values: list[float]) -> None:
    conn = init_db(conn_db_path)
    seed_catalog(conn)
    for offset, value in enumerate(values):
        day = (TODAY - timedelta(days=offset)).isoformat()
        rid = insert_record(
            conn, make_record(metric_name=metric, unit=unit, record_date=day, value=value),
            status="pending",
        )
        confirm_record(conn, rid)
    conn.close()


def _panel(body: str, key: str) -> str:
    match = re.search(rf'<article class="panel mod-{key}.*?</article>', body, re.S)
    assert match, f"没有 {key} 面板"
    return match.group(0)


# ---------------- 睡眠 ----------------


def test_sleep_panel_shows_latest_value_stages_and_chart(tmp_path):
    db = tmp_path / "ui.db"
    _seed(db, "night_sleep_minutes", "min", [432, 400, 380])
    _seed(db, "deep_sleep_percent", "%", [22])
    _seed(db, "light_sleep_percent", "%", [55])
    _seed(db, "rem_sleep_percent", "%", [23])

    with _client(tmp_path) as client:
        panel = _panel(client.get("/").text, "sleep")

    assert "7 小时 12 分" in panel           # 432 分钟
    assert "深睡 22%" in panel and "浅睡 55%" in panel and "REM 23%" in panel
    assert "<svg" in panel                    # 近 7 天柱状图
    assert "虚线目标为默认参考值" in panel    # 目标来源要说清楚


def test_sleep_panel_empty_state_is_honest(tmp_path):
    with _client(tmp_path) as client:
        panel = _panel(client.get("/").text, "sleep")

    assert "还没有睡眠数据" in panel
    assert "6 小时" not in panel


# ---------------- 恢复 ----------------


def test_recovery_panel_keeps_subjective_and_device_separate(tmp_path):
    db = tmp_path / "ui.db"
    _seed(db, "resting_hr_bpm", "bpm", [58, 59, 57, 58, 60, 58, 59, 58, 58, 57, 58, 59, 58, 58, 58])
    _seed(db, "hrv_avg_ms", "ms", [51])

    conn = init_db(db)
    training_service.record_feedback(
        conn, feedback_date=TODAY, energy=4, fatigue=1, today=TODAY
    )
    conn.close()

    with _client(tmp_path) as client:
        panel = _panel(client.get("/").text, "recovery")

    assert "静息心率" in panel
    assert "个人基线" in panel                  # 15 天样本 → 基线可用
    assert "HRV" in panel and "51 毫秒" in panel
    assert "今日主观" in panel
    assert "精力 4/5" in panel
    # 主观数据必须标明来源，不能伪装成设备测量
    assert "你自己记录，不是设备测量" in panel


def test_recovery_panel_empty_state(tmp_path):
    with _client(tmp_path) as client:
        panel = _panel(client.get("/").text, "recovery")

    assert "还没有恢复数据" in panel
    assert "0 次/分" not in panel


# ---------------- 训练 ----------------


def test_training_panel_shows_weekly_count_and_guidance(tmp_path):
    db = tmp_path / "ui.db"
    _seed(db, "night_sleep_minutes", "min", [470, 480, 465])
    conn = init_db(db)
    training_service.add_session(
        conn, session_date=TODAY - timedelta(days=1), activity_type="strength",
        body_parts="chest", duration_minutes=45, today=TODAY,
    )
    training_service.record_feedback(conn, feedback_date=TODAY, energy=4, fatigue=1, today=TODAY)
    conn.close()

    with _client(tmp_path) as client:
        panel = _panel(client.get("/").text, "training")

    assert "本周训练" in panel and "1 次" in panel
    assert "常规训练" in panel                 # 来自规则引擎的类别，不是界面编的
    assert "近 4 周" in panel
    assert "不安排高强度训练" in panel


# ---------------- 趋势 ----------------


def test_trends_panel_has_one_spark_per_metric(tmp_path):
    db = tmp_path / "ui.db"
    _seed(db, "night_sleep_minutes", "min", [400, 410, 420])
    _seed(db, "steps_count", "count", [8000, 9000, 10000])

    with _client(tmp_path) as client:
        panel = _panel(client.get("/").text, "trends")

    rows = re.findall(r'class="spark-row" href="([^"]+)"', panel)
    assert len(rows) == 4
    assert all("metric=" in href for href in rows)
    # 一个指标一张小图：不把不同量纲叠在一张图上
    assert "量纲不同" in panel
    assert panel.count('class="spark"') == 2  # 只有两个指标有数据


# ---------------- 报告 ----------------


def test_reports_panel_uses_local_report_text(tmp_path):
    db = tmp_path / "ui.db"
    _seed(db, "night_sleep_minutes", "min", [420, 430])

    with _client(tmp_path) as client:
        panel = _panel(client.get("/").text, "reports")

    assert "简报" in panel
    assert "夜间主睡眠时长" in panel
    assert "AI 改写需要你点按钮" in panel


def test_reports_panel_empty_state(tmp_path):
    with _client(tmp_path) as client:
        panel = _panel(client.get("/").text, "reports")

    assert "还没有报告" in panel


# ---------------- AI 助手：如实说没配置 ----------------


def test_assistant_panel_says_not_configured(tmp_path):
    with _client(tmp_path) as client:
        panel = _panel(client.get("/").text, "assistant")

    assert "未配置" in panel
    assert "还没有配置 AI 接口" in panel
    assert "/settings" in panel
    # 不能出现"假装已经聊过"的内容
    assert "我" not in panel.split("panel-lead")[1][:80]


def test_assistant_panel_has_shortcuts_and_no_write(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/").text

    assert "/training?from=assistant" in body
    assert "只解释已经算好的结论" in body

    conn = init_db(tmp_path / "ui.db")
    try:
        rows = conn.execute("SELECT COUNT(*) AS n FROM training_sessions").fetchone()["n"]
    finally:
        conn.close()
    assert rows == 0


# ---------------- 拖拽只是增强 ----------------


def test_drag_script_is_progressive_enhancement():
    source = TEMPLATE.read_text(encoding="utf-8")
    block = re.search(r"{% block scripts %}(.*?){% endblock %}", source, re.S)
    assert block is not None
    script = block.group(1)

    assert "pointerdown" in script
    assert "nearest" in script
    assert "classList.add('has-drag')" in script
    # 拖拽只改选中，不做导航
    assert "location" not in script
    # 尊重减少动效
    assert "prefers-reduced-motion" in script

    css = read_css()
    assert ".orbit.has-drag { touch-action: pan-y; }" in css
    assert ".orbit {" in css and "touch-action: manipulation" in css
    # 拖拽时不要过渡，指针要跟手
    assert ".ring-stage.is-dragging .needle" in css


def test_ring_works_without_scripts(tmp_path):
    """radio + label 就够用；脚本只做最短路径与拖拽。"""
    with _client(tmp_path) as client:
        body = client.get("/").text

    assert re.search(r'<input class="ring-input" type="radio"', body)
    for module in RING_MODULES:
        assert f'<label class="ring-node mod-{module.key}" for="sector-{module.key}"' in body


def test_ring_focus_ring_is_drawn_on_the_visible_node():
    """隐藏控件的焦点环要画在用户看得见的圆点上（ERR-020）。"""
    css = read_css()
    assert "#sector-sleep:focus-visible ~ .ring-stage .ring-node[for=\"sector-sleep\"] .node-dot" in css
    assert "box-shadow: var(--focus-ring)" in css
