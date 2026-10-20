"""新页面（睡眠分析 / 指标趋势 / 时钟模式）与模式持久化。

这些页面只读：不新增写接口、不改数据状态、不绕过 CSRF。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from health_agent.config import load_settings
from health_agent.storage.db import init_db
from health_agent.storage.lifecycle import confirm_record
from health_agent.storage.repository import insert_record, seed_catalog
from health_agent.ui.app import create_app

from conftest import make_record


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(load_settings({}, project_root=tmp_path), db_path=tmp_path / "ui.db"))


def _seed(tmp_path, days: int = 7) -> None:
    conn = init_db(tmp_path / "ui.db")
    seed_catalog(conn)
    today = date.today()
    for offset in range(days):
        day = (today - timedelta(days=offset)).isoformat()
        for metric, unit, value in (
            ("night_sleep_minutes", "min", 380.0 + offset * 5),
            ("deep_sleep_percent", "%", 20.0 + offset),
        ):
            record_id = insert_record(
                conn,
                make_record(metric_name=metric, unit=unit, record_date=day, value=value),
                status="pending",
            )
            confirm_record(conn, record_id)
    conn.close()


def test_sleep_page_empty_state(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/sleep").text

    assert "睡眠分析" in body
    assert "还没有睡眠数据" in body


def test_sleep_page_renders_chart_and_target(tmp_path):
    _seed(tmp_path)
    with _client(tmp_path) as client:
        body = client.get("/sleep").text

    assert "<svg" in body
    assert "每日夜间睡眠时长" in body
    # 目标必须标注来源，不能只说"目标 7 小时"
    assert "默认参考值" in body
    assert "未按年龄" in body


def test_sleep_page_marks_missing_days(tmp_path):
    _seed(tmp_path, days=2)
    with _client(tmp_path) as client:
        body = client.get("/sleep").text

    assert "数据不足" in body
    assert "不做趋势判断" in body


def test_trends_page_default_metric(tmp_path):
    _seed(tmp_path)
    with _client(tmp_path) as client:
        body = client.get("/trends").text

    assert "指标趋势" in body
    assert "仅统计已确认记录" in body
    assert "夜间主睡眠时长" in body


def test_trends_page_shows_stats_for_normal_metric(tmp_path):
    _seed(tmp_path)
    with _client(tmp_path) as client:
        body = client.get("/trends?metric=night_sleep_minutes&days=7").text

    assert "<dt>均值</dt>" in body
    assert "<dt>缺失率</dt>" in body


def test_trends_page_range_metric_has_no_average(tmp_path):
    """区间端点指标不展示均值，也不画中间线。"""
    _seed(tmp_path)
    with _client(tmp_path) as client:
        body = client.get("/trends?metric=hrv_min_ms").text

    assert "区间端点指标" in body
    assert "<dt>均值</dt>" not in body
    assert "band-area" in body or "没有" in body


def test_trends_page_unknown_metric_falls_back(tmp_path):
    with _client(tmp_path) as client:
        response = client.get("/trends?metric=not_a_metric")

    assert response.status_code == 200
    assert "指标趋势" in response.text


def test_chronos_demo_page(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/chronos").text

    assert "表盘导航" in body
    assert "普通入口" in body
    assert body.count('class="sector"') == 6


def test_mode_switch_persists_and_changes_home(tmp_path):
    with _client(tmp_path) as client:
        assert "今日摘要" in client.get("/").text

        switch = client.post("/settings/mode", data={"mode": "chronos", "next_path": "/"},
                             follow_redirects=False)
        assert switch.status_code == 303

        home = client.get("/").text
        assert "表盘导航" in home
        assert "今日摘要" not in home

        client.post("/settings/mode", data={"mode": "simple", "next_path": "/"},
                    follow_redirects=False)
        assert "今日摘要" in client.get("/").text


def test_mode_switch_rejects_unknown_value(tmp_path):
    with _client(tmp_path) as client:
        response = client.post("/settings/mode", data={"mode": "hologram"}, follow_redirects=False)

    assert response.headers["location"].startswith("/settings?error=")


def test_settings_page_has_mode_section(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/settings").text

    assert "外观" in body
    assert "时钟模式（Demo）" in body


def test_sidebar_groups_present_on_all_pages(tmp_path):
    with _client(tmp_path) as client:
        for path in ("/records", "/imports", "/alerts", "/settings"):
            body = client.get(path).text
            assert "睡眠分析" in body, path
            assert "指标趋势" in body, path


def test_new_pages_are_read_only(tmp_path):
    """新页面不应暴露任何写接口（只允许 GET）。"""
    with _client(tmp_path) as client:
        for path in ("/sleep", "/trends", "/chronos"):
            assert client.post(path, data={}).status_code == 405, path
