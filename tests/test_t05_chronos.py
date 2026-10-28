"""星环首页（`/` 与 `/chronos`）：结构、同口径、降级与安全。

这些断言守住四件事，比较的是"行为没走回头路"，不是像素：
1. 六个模块各有一张中心卡与一张展开面板，模块本身是 label 而不是链接
   —— 点开是看摘要，不是"以为在看预览、结果已经离开页面"；
2. 环上的数字与详细列表页同一个来源、同一套格式化（换皮不换数）；
3. 没有脚本也能用：原生 radio + label + 方向键；减少动效时不播放过渡；
4. 不造健康分、不造示例数据、不新增写接口。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from fastapi.testclient import TestClient

from health_agent.config import load_settings
from health_agent.storage.db import init_db
from health_agent.storage.lifecycle import confirm_record
from health_agent.storage.repository import insert_record, seed_catalog
from health_agent.ui.app import create_app
from health_agent.ui.auth import hash_password
from health_agent.ui.pages import RING_MODULES

from conftest import make_record, read_css


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


def _detail_list(client: TestClient) -> str:
    """切到详细列表模式并打开首页（对照用：同一份数据、同一个格式化）。"""
    client.post("/settings/mode", data={"mode": "simple", "next_path": "/"}, follow_redirects=False)
    body = client.get("/").text
    client.post("/settings/mode", data={"mode": "chronos", "next_path": "/"}, follow_redirects=False)
    return body


# ---------------- 结构：六模块 + 中心 + 面板 ----------------


def test_ring_has_six_modules_at_sixty_degrees(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/").text

    angles = re.findall(r'class="ring-input"[^>]*data-angle="(\d+)"', body)
    assert angles == ["0", "60", "120", "180", "240", "300"]
    for module in RING_MODULES:
        assert f'id="sector-{module.key}"' in body
        assert f'for="sector-{module.key}"' in body
    assert [m.key for m in RING_MODULES] == [
        "sleep",
        "recovery",
        "training",
        "reports",
        "assistant",
        "trends",
    ]


def test_default_state_is_idle_with_exactly_one_checked_radio(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/").text

    assert body.count('class="ring-input"') == 7  # 6 个模块 + 闲置
    assert len(re.findall(r'class="ring-input"[^>]*checked', body)) == 1
    assert re.search(r'<input class="ring-input"[^>]*id="sector-idle"[^>]*checked', body)


def test_modules_are_labels_not_links(tmp_path):
    """两步交互：模块本身不跳转，详情入口在面板里。"""
    with _client(tmp_path) as client:
        body = client.get("/").text

    assert body.count('class="ring-node') == 6
    assert body.count('<a class="ring-node') == 0
    assert body.count('class="panel ') == 7  # 6 个模块 + 闲置时的今日建议
    assert body.count('class="core-card') == 7


def test_every_module_has_one_panel_and_one_core(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/").text

    for module in RING_MODULES:
        assert body.count(f'data-module="{module.key}"') == 1, module.key
        assert body.count(f'data-core="{module.key}"') == 1, module.key
        # 面板里的详情入口指向真实页面（查询串里的 & 会被模板转义成 &amp;）
        escaped = module.href.replace("&", "&amp;")
        assert f'href="{module.href}"' in body or f'href="{escaped}"' in body, module.key


def test_query_param_preselects_module(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/chronos?s=training").text

    assert re.search(r'<input class="ring-input"[^>]*id="sector-training"[^>]*checked', body)
    assert not re.search(r'<input class="ring-input"[^>]*id="sector-idle"[^>]*checked', body)


def test_unknown_module_falls_back_to_idle(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/chronos?s=not-a-module").text
    assert re.search(r'<input class="ring-input"[^>]*id="sector-idle"[^>]*checked', body)


def test_chronos_keeps_equivalent_normal_navigation(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/").text

    assert "普通入口" in body
    for href in ["/sleep", "/trends", "/reports", "/records", "/imports",
                 "/conflicts", "/alerts", "/settings", "/training", "/assistant"]:
        assert f'href="{href}"' in body, href
    # 键盘路径与减少动效的说明必须在页面上，而不是只写在文档里
    assert "方向键" in body
    assert "减少动态效果" in body
    assert 'role="group"' in body


# ---------------- 数据一致性：与详细列表同口径 ----------------


def test_ring_values_match_detail_list(tmp_path):
    _seed_sleep(tmp_path, [400, 401, 402, 403, 404])
    with _client(tmp_path) as client:
        ring = client.get("/").text
        detail = _detail_list(client)

    # 同一个数值、同一个格式化（format_number）；最新一天是 400 分钟
    assert "6 小时 40 分" in ring
    assert "6 小时 40 分" in detail


def test_missing_value_is_dash_not_zero(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/").text

    assert "—" in body
    # 只禁止"把缺失写成 0"，不误伤"420 分钟"里的数字
    assert re.search(r"(?<![\d.])0 分钟", body) is None
    assert re.search(r"(?<![\d.])0 次/分", body) is None


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
        body = client.get("/").text

    assert "次/分" in body
    assert "步" in body


def test_pending_does_not_enter_ring(tmp_path):
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
        body = client.get("/").text

    assert "6 小时 50 分" not in body
    assert "暂无" in body


def test_home_and_chronos_use_one_assembler(tmp_path):
    """`/` 与 `/chronos` 是同一条装配路径，不允许长出两套数字。"""
    _seed_sleep(tmp_path, [400, 401, 402])
    with _client(tmp_path) as client:
        home = client.get("/").text
        page = client.get("/chronos").text

    assert home.count('class="panel ') == page.count('class="panel ')
    assert "6 小时 40 分" in home and "6 小时 40 分" in page


# ---------------- 降级、动效与安全 ----------------


def test_reduced_motion_turns_off_ring_transitions():
    css = read_css()
    block = re.search(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}", css, re.S)
    assert block is not None
    inner = block.group(1)
    for selector in (".needle", ".orbit-arc", ".core-card", ".panel", ".node-dot"):
        assert selector in inner, selector
    assert "transition: none" in inner
    assert "animation: none" in inner


def test_all_six_checked_states_have_css_rules():
    css = read_css()
    for module in RING_MODULES:
        assert f"#sector-{module.key}:checked ~ .ring-stage {{ --deg: {module.idx * 60}deg; }}" in css, module.key
        assert f'#sector-{module.key}:checked ~ .ring-stage .panel[data-module="{module.key}"]' in css, module.key
        assert f'#sector-{module.key}:checked ~ .ring-stage .core-card[data-core="{module.key}"]' in css, module.key


def test_no_fabricated_health_score_or_judgement(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/").text

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
