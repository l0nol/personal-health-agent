"""T05 时钟模式：六扇区单选 + 中心预览 + 明确进入 + 降级与一致性。

覆盖任务书要求：
- 环形导航可选择功能、中心预览、明确操作后进入详情（两步，不直接跳转）；
- 常规导航替代（link-grid）+ 键盘路径（原生 radio 方向键）；
- 支持触屏/鼠标（label 命中区 68px ≥ 44px）；
- 减少动态效果（过渡仅在 no-preference 下启用）；
- 环形数据与简洁模式同口径（NULL 显示“—”，不显示 0）；
- 不造健康分数；无写操作；鉴权与既有页面一致。
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from health_agent.config import load_settings
from health_agent.storage.db import init_db
from health_agent.storage.lifecycle import confirm_record
from health_agent.storage.repository import insert_record, seed_catalog
from health_agent.ui.app import create_app
from health_agent.ui.auth import hash_password

from conftest import make_record

APP_CSS = Path(__file__).resolve().parents[1] / "src" / "health_agent" / "ui" / "static" / "app.css"
SECTOR_PAGES = ["/sleep", "/trends", "/imports", "/alerts", "/reports", "/settings"]


def _client(tmp_path, *, password_hash: str | None = None) -> TestClient:
    settings = load_settings(
        {"HEALTH_AGENT_PASSWORD_HASH": password_hash or ""}, project_root=tmp_path
    )
    return TestClient(create_app(settings, db_path=tmp_path / "ui.db"))


def _seed_sleep(tmp_path, values: list[float]) -> None:
    conn = init_db(tmp_path / "ui.db")
    seed_catalog(conn)
    today = date.today()
    for offset, value in enumerate(values):
        day = (today - timedelta(days=offset)).isoformat()
        record_id = insert_record(
            conn,
            make_record(metric_name="night_sleep_minutes", unit="min", record_date=day, value=value),
            status="pending",
        )
        confirm_record(conn, record_id)
    conn.close()


# ---------------- 结构：六扇区单选 + 中心预览 + 进入 ----------------

def test_dial_has_six_radio_sectors_with_matching_labels(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/chronos").text

    radios = re.findall(r'<input class="sector-input"[^>]*id="(sector-[a-z]+)"', body)
    labels = re.findall(r'<label class="sector" for="(sector-[a-z]+)"', body)
    assert len(radios) == 6
    assert radios == labels  # 每个 radio 都有对应 label，顺序一致
    assert body.count('name="sector"') == 6  # 同一组单选
    assert body.count("checked") == 1  # 默认只选中一个


def test_center_has_preview_and_enter_link_per_sector(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/chronos").text

    panels = re.findall(r'class="core-panel" data-sector="([a-z]+)"', body)
    enters = re.findall(r'class="core-enter" data-sector="([a-z]+)" href="([^"]+)"', body)
    assert panels == ["sleep", "trends", "imports", "alerts", "reports", "settings"]
    assert [e[0] for e in enters] == panels
    assert [e[1] for e in enters] == SECTOR_PAGES


def test_sectors_are_labels_not_links(tmp_path):
    """两步交互：扇区本身不跳转，避免"以为在看预览、结果已经离开页面"。"""
    with _client(tmp_path) as client:
        body = client.get("/chronos").text

    assert "<label class=\"sector\"" in body
    assert body.count('<a class="sector"') == 0
    # 明确入口只有每扇区一个「进入」链接
    assert body.count('class="core-enter"') == 6


def test_chronos_keeps_equivalent_normal_navigation(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/chronos").text

    assert "普通入口" in body
    for href in ["/", "/sleep", "/trends", "/reports", "/records", "/imports",
                 "/conflicts", "/alerts", "/settings"]:
        assert f'href="{href}"' in body, href
    # 键盘说明与减少动效说明必须在页面上
    assert "方向键" in body
    assert "减少动态效果" in body
    assert 'role="group"' in body


# ---------------- 数据一致性：与简洁模式同口径 ----------------

def test_center_preview_matches_simple_dashboard_values(tmp_path):
    _seed_sleep(tmp_path, [400, 401, 402, 403, 404])
    with _client(tmp_path) as client:
        dashboard = client.get("/").text
        chronos = client.get("/chronos").text

    # 同一个数值、同一个格式化（format_number），两处必须一致；最新一天是 400 分钟
    assert "6 小时 40 分" in dashboard
    assert "6 小时 40 分" in chronos
    assert "近 7 天有效 5/7 天" in dashboard
    assert "近 7 天有效 5/7 天" in chronos


def test_missing_value_is_dash_not_zero(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/chronos").text

    assert "—" in body
    assert "近 7 天无数据" in body
    # 计数可以是真 0，但指标值不能把缺失写成 0
    assert "0 分钟" not in body
    assert "0 次/分" not in body


def test_unit_labels_are_correct(tmp_path):
    conn = init_db(tmp_path / "ui.db")
    seed_catalog(conn)
    today = date.today()
    for offset in range(3):
        day = (today - timedelta(days=offset)).isoformat()
        for metric, unit, value in (
            ("resting_hr_bpm", "bpm", 58.0),
            ("steps_count", "count", 8260.0),
        ):
            rid = insert_record(
                conn,
                make_record(metric_name=metric, unit=unit, record_date=day, value=value),
                status="pending",
            )
            confirm_record(conn, rid)
    conn.close()

    with _client(tmp_path) as client:
        body = client.get("/chronos").text

    assert "次/分" in body
    assert "步" in body


def test_pending_does_not_enter_chronos_preview(tmp_path):
    """只有 pending 的指标，表盘预览仍是"无数据"。"""
    conn = init_db(tmp_path / "ui.db")
    seed_catalog(conn)
    for offset in range(3):
        day = (date.today() - timedelta(days=offset)).isoformat()
        insert_record(
            conn,
            make_record(metric_name="night_sleep_minutes", unit="min", record_date=day, value=410.0),
            status="pending",
        )
    conn.close()

    with _client(tmp_path) as client:
        body = client.get("/chronos").text

    assert "近 7 天无数据" in body
    assert "6 小时 50 分" not in body


def test_mode_switch_home_uses_same_dial_and_values(tmp_path):
    _seed_sleep(tmp_path, [400, 401, 402])
    with _client(tmp_path) as client:
        client.post("/settings/mode", data={"mode": "chronos", "next_path": "/"},
                    follow_redirects=False)
        home = client.get("/").text
        chronos = client.get("/chronos").text

    assert "表盘导航" in home
    assert 'class="sector"' in home
    assert "6 小时 40 分" in home and "6 小时 40 分" in chronos


# ---------------- 降级、性能与安全 ----------------

def test_reduced_motion_only_animates_when_allowed():
    css = APP_CSS.read_text(encoding="utf-8")

    block = re.search(
        r"@media \(prefers-reduced-motion: no-preference\) \{(.*?)\n\}", css, re.S
    ).group(1)
    assert ".dial-ring { transition: transform" in block
    # 过渡不允许出现在 media 查询之外（减少动态时必须是瞬时切换）
    outside = css.replace(block, "")
    assert ".dial-ring { transition" not in outside


def test_all_six_checked_states_have_css_rules():
    css = APP_CSS.read_text(encoding="utf-8")

    for key in ("sleep", "trends", "imports", "alerts", "reports", "settings"):
        assert f'#sector-{key}:checked ~ .dial-ring {{ transform: rotate' in css, key
        assert f'#sector-{key}:checked ~ .dial-core [data-sector="{key}"]' in css, key


def test_no_fabricated_health_score_or_judgement(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/chronos").text

    for word in ("健康分", "健康评分", "综合评分", "风险等级"):
        assert word not in body, word


def test_chronos_is_read_only(tmp_path):
    with _client(tmp_path) as client:
        assert client.post("/chronos", data={}).status_code == 405


def test_chronos_requires_login_when_password_set(tmp_path):
    with _client(tmp_path, password_hash=hash_password("correct horse")) as client:
        response = client.get("/chronos", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
