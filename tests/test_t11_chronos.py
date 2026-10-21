"""T11：时钟模式整合（六刻度拨盘 + 中心助手 + 拖拽增强 + 助手预填）。

重点不是"页面能打开"，而是三件事：
1. 表盘预览与对应页面同源（不另算一套口径）；
2. 没有脚本也能完整使用（单选控件 + 键盘）；
3. 助手只能预填，不能替用户保存。
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
from health_agent.ui.pages import DIAL_PAGES, page_by_key

from conftest import make_record, read_css

TODAY = date.today()


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


# ---------------- 结构与刻度 ----------------


def test_dial_has_six_ticks_with_angles(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/chronos").text

    angles = re.findall(r'class="sector-input"[^>]*data-angle="(\d+)"', body)
    assert angles == ["0", "60", "120", "180", "240", "300"]
    for page in DIAL_PAGES:
        assert f'id="sector-{page.key}"' in body
    assert [page.key for page in DIAL_PAGES] == [
        "sleep",
        "trends",
        "training",
        "alerts",
        "reports",
        "settings",
    ]


def test_idle_shows_assistant_orb_and_hint(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/chronos").text

    assert 'class="dial-orb"' in body
    assert "点我唤醒助手" in body
    assert len(re.findall(r'<input class="sector-input"[^>]*id="sector-idle"[^>]*checked', body)) == 1


def test_query_param_preselects_sector(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/chronos?s=training").text

    assert len(re.findall(r'<input class="sector-input"[^>]*id="sector-training"[^>]*checked', body)) == 1
    assert len(re.findall(r'<input class="sector-input"[^>]*id="sector-idle"[^>]*checked', body)) == 0
    assert "今日建议" in body


def test_unknown_sector_falls_back_to_idle(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/chronos?s=not-a-page").text
    assert len(re.findall(r'<input class="sector-input"[^>]*id="sector-idle"[^>]*checked', body)) == 1


# ---------------- 数据与简洁模式同源 ----------------


def test_sleep_preview_uses_same_formatted_value(tmp_path):
    _seed(tmp_path / "ui.db", "night_sleep_minutes", "min", [350, 400, 380])
    with _client(tmp_path) as client:
        chronos = client.get("/chronos").text
        simple = client.get("/").text

    # 350 分钟 = 5 小时 50 分
    assert "5 小时 50 分" in chronos
    assert "5 小时 50 分" in simple


def test_training_preview_shows_guidance_category_and_weekly_count(tmp_path):
    _seed(tmp_path / "ui.db", "night_sleep_minutes", "min", [470, 480, 465])
    conn = init_db(tmp_path / "ui.db")
    training_service.add_session(
        conn, session_date=TODAY - timedelta(days=1), activity_type="strength",
        body_parts="chest", duration_minutes=45, today=TODAY,
    )
    training_service.record_feedback(conn, feedback_date=TODAY, energy=4, fatigue=1, today=TODAY)
    conn.close()

    with _client(tmp_path) as client:
        body = client.get("/chronos?s=training").text

    assert "常规训练" in body          # guidance 类别
    assert "本周训练" in body
    assert "1 次" in body


def test_every_card_has_exactly_one_enter_link(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/chronos").text

    enters = re.findall(r'class="core-enter" href="([^"]+)"', body)
    assert enters == [page.href for page in DIAL_PAGES]
    assert page_by_key("training").href == "/training"


# ---------------- 无脚本可用 + 拖拽增强 ----------------


def test_dial_works_without_scripts(tmp_path):
    """单选控件 + label 就够用；脚本只做增强。"""
    with _client(tmp_path) as client:
        body = client.get("/chronos").text

    assert re.search(r'<input class="sector-input" type="radio"', body)
    for page in DIAL_PAGES:
        assert f'<label class="dial-fbtn" for="sector-{page.key}"' in body


def test_drag_script_is_progressive_enhancement(tmp_path):
    """脚本只在 scripts 块内，且只在拿到指针能力后才禁用触屏滚动。"""
    source = (
        Path(__file__).resolve().parents[1]
        / "src" / "health_agent" / "ui" / "templates" / "chronos.html"
    ).read_text(encoding="utf-8")

    block = re.search(r"{% block scripts %}(.*?){% endblock %}", source, re.S)
    assert block is not None
    script = block.group(1)
    assert "pointerdown" in script
    assert "nearest" in script
    assert "classList.add('has-drag')" in script
    # 拖拽只改选中，不做导航
    assert "location" not in script

    css = read_css()
    assert ".dial.has-drag { touch-action: none; }" in css
    assert ".dial {" in css and "touch-action: manipulation" in css


def test_reduced_motion_disables_transitions():
    css = read_css()
    block = re.search(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}", css, re.S)
    assert block is not None
    assert "transition: none" in block.group(1)


# ---------------- 助手：只预填，不写库 ----------------


def test_assistant_shortcuts_include_navigation_and_prefill(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/chronos").text

    assert "/sleep" in body
    assert "/training?from=assistant" in body
    assert "只解释已经算好的结论" in body


def test_training_page_prefills_from_assistant(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/training?from=assistant&type=walking&duration=25&content=轻松步行").text

    assert "助手按今天的建议预填" in body
    assert 'value="walking" selected' in body
    assert 'value="25"' in body
    assert 'value="轻松步行"' in body


def test_prefill_does_not_write_anything(tmp_path):
    with _client(tmp_path) as client:
        client.get("/training?from=assistant&type=walking&duration=25")

    conn = init_db(tmp_path / "ui.db")
    try:
        rows = conn.execute("SELECT COUNT(*) AS n FROM training_sessions").fetchone()["n"]
    finally:
        conn.close()
    assert rows == 0  # 只有 GET，不应落库


def test_prefilled_form_still_validates_on_submit(tmp_path):
    with _client(tmp_path) as client:
        response = client.post(
            "/training/add",
            data={
                "session_date": TODAY.isoformat(),
                "activity_type": "walking",
                "duration_minutes": "999",  # 预填值被改成越界
                "next": "/training",
            },
            follow_redirects=False,
        )
    assert "error=" in response.headers["location"]
