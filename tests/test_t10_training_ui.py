"""T10：首页建议卡与训练记录页。

只验行为与契约，不追逐像素：
- 今日建议卡必须显示类别、依据、为什么、边界，且与 guidance 同源；
- 训练页的三个表单（训练/反馈/偏好）能写库、能报错、能删除；
- 快速记录不把用户困在表单里；错误信息可读；
- 零 JS 基线与触控目标不被破坏。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from health_agent.config import load_settings
from health_agent.storage import training as training_store
from health_agent.storage.db import init_db
from health_agent.storage.repository import insert_record, seed_catalog
from health_agent.ui.app import create_app

from conftest import make_record, read_css

TODAY = date.today()


def _seed_sleep(db_path: Path, values: list[float]) -> None:
    conn = init_db(db_path)
    seed_catalog(conn)
    for index, value in enumerate(values):
        day = TODAY - timedelta(days=index + 1)
        insert_record(
            conn,
            make_record(
                metric_name="night_sleep_minutes",
                record_date=day.isoformat(),
                value=value,
                unit="min",
                raw_unit="min",
            ),
            status="confirmed",
        )
    conn.close()


def _client(tmp_path) -> TestClient:
    return TestClient(
        create_app(load_settings({}, project_root=tmp_path), db_path=tmp_path / "ui.db")
    )


# ---------------- 训练页 ----------------


def test_training_page_is_registered_and_has_forms(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/training").text

    assert "训练记录" in body
    assert 'action="/training/add"' in body
    assert 'action="/training/feedback"' in body
    assert 'action="/training/prefs"' in body
    assert "还没有训练记录" in body
    # 表单必须带 CSRF 隐藏域（有密码时中间件会校验）
    assert body.count('name="csrf"') >= 3


def test_training_page_is_in_sidebar(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/").text
    assert 'href="/training"' in body
    assert "训练" in body


def test_add_session_then_it_appears(tmp_path):
    with _client(tmp_path) as client:
        response = client.post(
            "/training/add",
            data={
                "session_date": TODAY.isoformat(),
                "activity_type": "strength",
                "body_parts": "胸,肩",
                "duration_minutes": "45",
                "perceived_intensity": "7",
                "completion": "completed",
                "content": "卧推 3×8",
                "next": "/training",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        body = client.get("/training").text

    assert "力量训练" in body
    assert "胸 / 肩" in body
    assert "45 分钟" in body
    assert "7/10" in body


def test_add_session_rejects_bad_duration_with_readable_error(tmp_path):
    with _client(tmp_path) as client:
        response = client.post(
            "/training/add",
            data={
                "session_date": TODAY.isoformat(),
                "activity_type": "strength",
                "duration_minutes": "999",
                "next": "/training",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "error=" in response.headers["location"]
        body = client.get(response.headers["location"]).text

    assert "时长" in body and "600" in body
    with _client(tmp_path) as client:
        assert "还没有训练记录" in client.get("/training").text


def test_feedback_form_upserts_and_shows_saved_state(tmp_path):
    with _client(tmp_path) as client:
        client.post(
            "/training/feedback",
            data={
                "feedback_date": TODAY.isoformat(),
                "energy": "2",
                "fatigue": "4",
                "soreness_parts": "胸",
                "soreness_level": "3",
                "next": "/training",
            },
            follow_redirects=False,
        )
        body = client.get("/training").text

    assert "今天已记录" in body
    assert "再次提交会覆盖更新" in body
    assert 'value="2" selected' in body  # 精力回填
    assert 'value="4" selected' in body  # 疲劳回填


def test_feedback_pair_validation_is_explained(tmp_path):
    with _client(tmp_path) as client:
        response = client.post(
            "/training/feedback",
            data={"feedback_date": TODAY.isoformat(), "soreness_parts": "胸", "next": "/training"},
            follow_redirects=False,
        )
        body = client.get(response.headers["location"]).text

    assert "同时填写" in body or "同时留空" in body


def test_preferences_roundtrip(tmp_path):
    with _client(tmp_path) as client:
        client.post(
            "/training/prefs",
            data={
                "days_per_week": "3",
                "time_budget_minutes": "45",
                "equipment": "哑铃, 弹力带",
                "avoid_movements": "跳箱",
                "next": "/training",
            },
            follow_redirects=False,
        )
        body = client.get("/training").text

    assert 'value="3"' in body
    assert "哑铃, 弹力带" in body
    assert "跳箱" in body


def test_delete_session_from_page(tmp_path):
    with _client(tmp_path) as client:
        client.post(
            "/training/add",
            data={
                "session_date": TODAY.isoformat(),
                "activity_type": "cardio",
                "next": "/training",
            },
            follow_redirects=False,
        )
        conn = init_db(tmp_path / "ui.db")
        session_id = training_store.list_sessions(conn)[0].session_id
        conn.close()

        response = client.post(f"/training/{session_id}/delete", follow_redirects=False)
        assert response.status_code == 303
        assert "已删除" in client.get("/training").text or "还没有训练记录" in client.get(
            "/training"
        ).text


def test_add_does_not_redirect_offsite(tmp_path):
    with _client(tmp_path) as client:
        response = client.post(
            "/training/add",
            data={
                "session_date": TODAY.isoformat(),
                "activity_type": "walking",
                "next": "//evil.example",
            },
            follow_redirects=False,
        )
    location = response.headers["location"]
    assert location.startswith("/")
    assert "evil.example" not in location


# ---------------- 首页建议卡 ----------------


def test_home_shows_guidance_card_with_evidence_and_why(tmp_path):
    db_path = tmp_path / "ui.db"
    _seed_sleep(db_path, [250, 260, 255])
    with _client(tmp_path) as client:
        client.post(
            "/training/feedback",
            data={"feedback_date": TODAY.isoformat(), "energy": "1", "fatigue": "5"},
            follow_redirects=False,
        )
        # 详细列表模式：今日建议卡 + 近 7 天睡眠图（星环页也复用这张建议卡，但布局不同）
        client.post("/settings/mode", data={"mode": "simple", "next_path": "/"}, follow_redirects=False)
        # 详细列表模式：今日建议卡 + 近 7 天睡眠图（星环页也复用这张建议卡，但布局不同）
        client.post("/settings/mode", data={"mode": "simple", "next_path": "/"}, follow_redirects=False)
        body = client.get("/").text

    assert "今日建议" in body
    assert "休息" in body
    assert "guidance-card cat-rest" in body
    assert "近 7 天夜间睡眠" in body
    assert "覆盖" in body
    assert "为什么这样建议" in body
    assert "不构成诊断" in body
    assert "不合成" in body and "健康总分" in body


def test_home_guidance_empty_state_is_honest(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/").text

    assert "资料不足" in body
    assert "先补录昨晚睡眠" in body


def test_home_quick_add_returns_home(tmp_path):
    with _client(tmp_path) as client:
        response = client.post(
            "/training/add",
            data={
                "session_date": TODAY.isoformat(),
                "activity_type": "mobility",
                "body_parts": "核心",
                "next": "/",
            },
            follow_redirects=False,
        )
        assert response.headers["location"].startswith("/?msg=")
        body = client.get(response.headers["location"]).text

    assert "拉伸/灵活性" in body
    assert "已记录" in body


# ---------------- 结构与可访问性 ----------------


def test_guidance_css_has_semantic_colors_and_mobile_rules():
    css = read_css()
    for selector in (
        ".guidance-card",
        ".guidance-card.cat-rest",
        ".guidance-card.cat-light_activity",
        ".guidance-card.cat-regular_training",
        ".guidance-card.cat-insufficient_data",
        ".guidance-facts",
        ".form-grid",
    ):
        assert selector in css, selector
    assert "@media (max-width: 767px)" in css


def test_training_templates_contain_no_inline_script():
    root = Path(__file__).resolve().parents[1] / "src" / "health_agent" / "ui" / "templates"
    for name in ("index.html", "training.html", "_macros.html"):
        assert "<script" not in (root / name).read_text(encoding="utf-8")
