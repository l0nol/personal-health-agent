"""界面层：页面能开、动作能生效、鉴权与 CSRF 真的挡得住。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from health_agent.config import load_settings
from health_agent.storage.db import connect, init_db
from health_agent.storage.repository import insert_record, seed_catalog
from health_agent.ui.app import create_app
from health_agent.ui.auth import hash_password, verify_password

from conftest import make_record

DATA_DIR = Path(__file__).resolve().parents[1] / "test_data"
SAMPLE_CSV = DATA_DIR / "sample_with_issues.csv"


def _client(tmp_path, *, password_hash: str | None = None) -> TestClient:
    settings = load_settings(
        {"HEALTH_AGENT_PASSWORD_HASH": password_hash or ""}, project_root=tmp_path
    )
    app = create_app(settings, db_path=tmp_path / "ui.db")
    return TestClient(app)


def _with_record(tmp_path, **overrides) -> tuple[TestClient, str]:
    client = _client(tmp_path)
    conn = init_db(tmp_path / "ui.db")
    seed_catalog(conn)
    record_id = insert_record(conn, make_record(**overrides), status="pending")
    conn.close()
    return client, record_id


@pytest.fixture()
def client(tmp_path):
    with _client(tmp_path) as test_client:
        yield test_client


# ---------------- 密码与鉴权 ----------------

def test_password_hash_roundtrip():
    stored = hash_password("correct horse")
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("correct horse", None)


def test_pages_open_without_password_in_local_mode(client):
    for path in ["/", "/records", "/imports", "/conflicts", "/settings"]:
        assert client.get(path).status_code == 200, path


def test_password_mode_redirects_to_login(tmp_path):
    with _client(tmp_path, password_hash=hash_password("pw")) as client:
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login")


def test_login_then_access(tmp_path):
    with _client(tmp_path, password_hash=hash_password("pw")) as client:
        bad = client.post("/login", data={"password": "nope", "next": "/"}, follow_redirects=False)
        assert bad.headers["location"].startswith("/login?error=")

        ok = client.post("/login", data={"password": "pw", "next": "/"}, follow_redirects=False)
        assert ok.status_code == 303
        assert client.get("/").status_code == 200


def test_csrf_is_enforced_when_password_set(tmp_path):
    with _client(tmp_path, password_hash=hash_password("pw")) as client:
        client.post("/login", data={"password": "pw", "next": "/"}, follow_redirects=False)
        page = client.get("/records").text
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)

        conn = init_db(tmp_path / "ui.db")
        seed_catalog(conn)
        record_id = insert_record(conn, make_record(), status="pending")
        conn.close()

        assert client.post(f"/records/{record_id}/confirm", data={}).status_code == 400
        assert (
            client.post(
                f"/records/{record_id}/confirm", data={"csrf": token}, follow_redirects=False
            ).status_code
            == 303
        )


# ---------------- 页面与动作 ----------------

def test_dashboard_shows_counts(client):
    # 默认首页是星环；"记录总数"这类明细在详细列表模式里
    client.post("/settings/mode", data={"mode": "simple", "next_path": "/"}, follow_redirects=False)
    body = client.get("/").text
    assert "记录总数" in body
    assert "待确认" in body


def test_ring_home_shows_status_strip(client):
    body = client.get("/").text
    assert "待确认" in body
    assert "数据截至" in body


def test_records_page_lists_record(tmp_path):
    client, record_id = _with_record(tmp_path)
    with client:
        body = client.get("/records").text
    assert "夜间主睡眠时长" in body
    assert record_id[:8] in body or "pending" in body


def test_confirm_action_changes_status(tmp_path):
    client, record_id = _with_record(tmp_path)
    with client:
        response = client.post(f"/records/{record_id}/confirm", follow_redirects=False)
        assert response.status_code == 303

    conn = init_db(tmp_path / "ui.db")
    status = conn.execute(
        "SELECT verification_status FROM records WHERE record_id = ?", (record_id,)
    ).fetchone()["verification_status"]
    conn.close()
    assert status == "confirmed"


def test_delete_action_removes_record(tmp_path):
    client, record_id = _with_record(tmp_path)
    with client:
        client.post(f"/records/{record_id}/delete", follow_redirects=False)

    conn = init_db(tmp_path / "ui.db")
    remaining = conn.execute("SELECT COUNT(*) AS n FROM records").fetchone()["n"]
    conn.close()
    assert remaining == 0


# ---------------- 导入流程 ----------------

def test_upload_then_preview_then_commit(tmp_path):
    with _client(tmp_path) as client:
        with open(SAMPLE_CSV, "rb") as handle:
            upload = client.post(
                "/imports/upload",
                files={"file": ("sample.csv", handle, "text/csv")},
                follow_redirects=False,
            )
        assert upload.status_code == 303
        preview_url = upload.headers["location"]
        assert preview_url.startswith("/imports/preview")

        page = client.get(preview_url)
        assert page.status_code == 200
        assert "导入预览" in page.text
        assert "E_UNKNOWN_METRIC" in page.text
        staged = re.search(r'name="staged" value="([^"]+)"', page.text).group(1)

        commit = client.post("/imports/commit", data={"staged": staged}, follow_redirects=False)
        assert commit.status_code == 303

    conn = init_db(tmp_path / "ui.db")
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM records WHERE verification_status = 'pending'"
    ).fetchone()["n"]
    conn.close()
    assert pending > 0


def test_upload_rejects_unsupported_type(tmp_path):
    with _client(tmp_path) as client:
        response = client.post(
            "/imports/upload",
            files={"file": ("x.exe", b"binary", "application/octet-stream")},
            follow_redirects=False,
        )
        assert response.headers["location"].startswith("/imports?error=")


def test_preview_rejects_path_traversal(tmp_path):
    with _client(tmp_path) as client:
        response = client.get("/imports/preview?staged=../ui.db", follow_redirects=False)
        assert response.headers["location"].startswith("/imports?error=")


# ---------------- 冲突 ----------------

def test_conflict_page_and_resolution(tmp_path):
    client = _client(tmp_path)
    conn = init_db(tmp_path / "ui.db")
    seed_catalog(conn)
    existing = insert_record(conn, make_record(value=402.0), status="confirmed")
    incoming = insert_record(conn, make_record(value=430.0), status="pending")
    from health_agent.storage.repository import create_conflict, get_record

    conflict_id = create_conflict(
        conn,
        logical_key=get_record(conn, incoming)["logical_key"],
        metric_name="night_sleep_minutes",
        record_date="2026-10-01",
        existing_record_id=existing,
        incoming_record_id=incoming,
    )
    conn.commit()
    conn.close()

    with client:
        page = client.get("/conflicts")
        assert "冲突裁决" in page.text
        assert "402" in page.text and "430" in page.text

        response = client.post(
            f"/conflicts/{conflict_id}/resolve",
            data={"resolution": "take_incoming"},
            follow_redirects=False,
        )
        assert response.status_code == 303

    conn = init_db(tmp_path / "ui.db")
    statuses = {
        row["record_id"]: row["verification_status"]
        for row in conn.execute("SELECT * FROM records").fetchall()
    }
    conn.close()
    assert statuses[existing] == "superseded"
    assert statuses[incoming] == "confirmed"


# ---------------- 设置与导出 ----------------

def test_settings_save_and_clear(tmp_path):
    with _client(tmp_path) as client:
        client.post("/settings", data={"wake_time": "07:10", "notes": ""}, follow_redirects=False)
        body = client.get("/settings").text
        assert "07:10" in body

        client.post("/settings", data={"wake_time": ""}, follow_redirects=False)
        assert "07:10" not in client.get("/settings").text


def test_export_csv_has_bom_and_rows(tmp_path):
    client, _ = _with_record(tmp_path)
    with client:
        response = client.get("/export.csv")
    assert response.status_code == 200
    assert response.text.startswith("\ufeff")
    assert "record_id,record_date,metric_name" in response.text
    assert "night_sleep_minutes" in response.text


def test_export_json_is_parsable(tmp_path):
    client, _ = _with_record(tmp_path)
    with client:
        payload = json.loads(client.get("/export.json").text)
    assert payload["schema_version"] == "1.0"
    assert len(payload["records"]) == 1


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


# ---------------- 报告页 ----------------

def _seed_week(tmp_path, start_day: str = "2026-10-12") -> None:
    from datetime import date, timedelta

    from health_agent.storage.lifecycle import confirm_record

    conn = init_db(tmp_path / "ui.db")
    seed_catalog(conn)
    beginning = date.fromisoformat(start_day)
    for offset in range(7):
        record_id = insert_record(
            conn,
            make_record(record_date=(beginning + timedelta(days=offset)).isoformat(), value=400.0 + offset),
            status="pending",
        )
        confirm_record(conn, record_id)
    conn.close()


def test_reports_daily_page_renders(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/reports?day=2026-10-18").text
    assert "每日简报" in body


def test_reports_lists_number_sources(tmp_path):
    _seed_week(tmp_path)
    with _client(tmp_path) as client:
        body = client.get("/reports?day=2026-10-18").text
    assert "数字与来源" in body
    assert "条记录" in body


def test_reports_weekly_page_renders(tmp_path):
    _seed_week(tmp_path)
    with _client(tmp_path) as client:
        body = client.get("/reports?kind=weekly&day=2026-10-18").text
    assert "每周报告" in body
    assert "近 4 周夜间睡眠" in body


def test_reports_without_data_says_so(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/reports?day=2026-10-18").text
    assert "没有可用的已确认数据" in body


def test_reports_tolerates_bad_date(tmp_path):
    with _client(tmp_path) as client:
        assert client.get("/reports?day=not-a-date").status_code == 200


# ---------------- 提醒页 ----------------

def _seed_recent_sleep(tmp_path, days: int = 7, value: float = 400.0) -> None:
    from datetime import date, timedelta

    from health_agent.storage.lifecycle import confirm_record

    conn = init_db(tmp_path / "ui.db")
    seed_catalog(conn)
    today = date.today()
    for offset in range(days):
        day = (today - timedelta(days=offset)).isoformat()
        record_id = insert_record(conn, make_record(record_date=day, value=value), status="pending")
        confirm_record(conn, record_id)
    conn.close()


def test_alerts_page_empty_state(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/alerts").text
    assert "还没有提醒" in body


def test_alerts_run_creates_alert_for_missing_days(tmp_path):
    _seed_recent_sleep(tmp_path, days=2)  # 近 7 天只有 2 天 → 触发 data_gap
    with _client(tmp_path) as client:
        response = client.post("/alerts/run", follow_redirects=False)
        assert response.status_code == 303
        body = client.get("/alerts").text
    assert "data_gap" in body
    assert "缺失率" in body


def test_alerts_run_is_idempotent(tmp_path):
    _seed_recent_sleep(tmp_path, days=2)
    with _client(tmp_path) as client:
        client.post("/alerts/run", follow_redirects=False)
        first = client.get("/alerts").text.count('class="card alert')

        client.post("/alerts/run", follow_redirects=False)
        second = client.get("/alerts").text.count('class="card alert')

    assert first == second


def test_alert_read_and_feedback(tmp_path):
    _seed_recent_sleep(tmp_path, days=2)
    with _client(tmp_path) as client:
        client.post("/alerts/run", follow_redirects=False)
        page = client.get("/alerts").text
        alert_id = re.search(r'/alerts/([0-9a-f]{32})/read', page).group(1)

        badge = '<span class="tag pending">未读</span>'
        assert badge in page
        client.post(f"/alerts/{alert_id}/read", follow_redirects=False)
        assert badge not in client.get("/alerts").text

        client.post(
            f"/alerts/{alert_id}/feedback", data={"feedback": "helpful"}, follow_redirects=False
        )
        assert "helpful" in client.get("/alerts").text


def test_dashboard_shows_unread_alert_count(tmp_path):
    _seed_recent_sleep(tmp_path, days=2)
    with _client(tmp_path) as client:
        client.post("/alerts/run", follow_redirects=False)
        body = client.get("/").text
    assert "未读提醒" in body


# ---------------- AI 与报告页 ----------------

def _ai_call_count(tmp_path) -> int:
    conn = init_db(tmp_path / "ui.db")
    total = conn.execute("SELECT COUNT(*) AS n FROM ai_call_log").fetchone()["n"]
    conn.close()
    return total


def test_reports_page_does_not_invoke_ai(tmp_path):
    """打开报告页不应触发任何 AI 调用（规格 §7：只有新数据或用户请求才重新分析）。"""
    _seed_week(tmp_path)
    with _client(tmp_path) as client:
        assert client.get("/reports?day=2026-10-18").status_code == 200

    assert _ai_call_count(tmp_path) == 0


def test_rewrite_without_key_explains_instead_of_failing(tmp_path):
    _seed_week(tmp_path)
    with _client(tmp_path) as client:
        response = client.post(
            "/reports/rewrite", data={"kind": "daily", "day": "2026-10-18"}, follow_redirects=False
        )
        assert response.status_code == 303
        assert "error=" in response.headers["location"]

        body = client.get("/reports?day=2026-10-18&error=" + "x").text

    assert "本地模板文案" in body


# ---------------- AI 与报告页 ----------------


# ---------------- 0.1.1：设置页必须显示"当前生效的睡眠目标" ----------------

def test_settings_page_shows_effective_sleep_target(tmp_path):
    """用户应该能一眼看出系统用的是默认参考值还是自己设的目标。"""
    with _client(tmp_path) as client:
        body = client.get("/settings").text

    assert "睡眠目标" in body
    assert "默认参考值" in body
    assert "未按年龄" in body
