"""一键确认：范围永远是"一个导入批次"，不是所有待确认记录。

为什么专门测这个：批量确认最容易出的事故，是把用户**故意留着**的待确认记录
一起确认进趋势（历史事故见 ERR-029）。这里的断言就是防止"图省事写成 confirm-all"：

1. 点一次只影响这一个批次，另一批原封不动；
2. 确认完回到你点按钮的那一页（`next_path` 只接受站内路径）；
3. 不止一批时首页不给你"一键"，而是让你去逐批确认；
4. CLI 默认只预览，不加 `--yes` 不写库。
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import unquote
from urllib.parse import unquote

from fastapi.testclient import TestClient

from health_agent.cli import main as cli_main
from health_agent.config import load_settings
from health_agent.storage.db import init_db
from health_agent.ui.app import create_app

TODAY = date.today()


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(load_settings({}, project_root=tmp_path), db_path=tmp_path / "ui.db"))


def _import(tmp_path, rows: list[str], name: str) -> None:
    """走真实的"上传 → 预览 → 写入"。"""
    body = ("metric_name,record_date,value,unit,period_type\n" + "\n".join(rows) + "\n").encode("utf-8")
    with _client(tmp_path) as client:
        upload = client.post(
            "/imports/upload",
            files={"file": (name, body, "text/csv")},
            follow_redirects=False,
        )
        page = client.get(upload.headers["location"])
        staged = re.search(r'name="staged" value="([^"]+)"', page.text).group(1)
        commit = client.post("/imports/commit", data={"staged": staged}, follow_redirects=False)
        assert commit.status_code == 303


def _pending_by_batch(tmp_path) -> dict[str, int]:
    conn = init_db(tmp_path / "ui.db")
    try:
        rows = conn.execute(
            "SELECT batch_id, COUNT(*) AS n FROM records"
            " WHERE verification_status = 'pending' GROUP BY batch_id"
        ).fetchall()
        return {row["batch_id"]: row["n"] for row in rows}
    finally:
        conn.close()


def _batch_ids(tmp_path) -> list[str]:
    conn = init_db(tmp_path / "ui.db")
    try:
        rows = conn.execute("SELECT batch_id FROM import_batches ORDER BY rowid").fetchall()
        return [row["batch_id"] for row in rows]
    finally:
        conn.close()


def _seed_two_batches(tmp_path) -> tuple[str, str]:
    older = (TODAY - timedelta(days=5)).isoformat()
    newer = (TODAY - timedelta(days=1)).isoformat()
    _import(tmp_path, [f"steps_count,{older},8000,count,day"], f"old-{older}.csv")
    _import(tmp_path, [f"steps_count,{newer},9000,count,day"], f"new-{newer}.csv")
    first, second = _batch_ids(tmp_path)
    return first, second


# ---------------- 只动一个批次 ----------------


def test_confirm_one_batch_leaves_the_other_untouched(tmp_path):
    first, second = _seed_two_batches(tmp_path)
    with _client(tmp_path) as client:
        response = client.post(
            f"/imports/{second}/confirm",
            data={"next_path": "/records?status=pending"},
            follow_redirects=False,
        )
        assert response.status_code == 303

    pending = _pending_by_batch(tmp_path)
    assert second not in pending            # 这一批已确认
    assert pending[first] == 1              # 另一批原封不动


def test_confirm_returns_to_the_page_that_asked(tmp_path):
    _first, second = _seed_two_batches(tmp_path)
    with _client(tmp_path) as client:
        response = client.post(
            f"/imports/{second}/confirm",
            data={"next_path": "/records?status=pending"},
            follow_redirects=False,
        )
    location = unquote(response.headers["location"])
    assert location.startswith("/records?status=pending")
    assert "msg=" in location
    assert "已确认 1 条" in location


def test_confirm_refuses_offsite_next_path(tmp_path):
    """next_path 只接受站内相对路径，避免变成开放重定向。"""
    _first, second = _seed_two_batches(tmp_path)
    with _client(tmp_path) as client:
        response = client.post(
            f"/imports/{second}/confirm",
            data={"next_path": "https://evil.example/steal"},
            follow_redirects=False,
        )
    assert response.headers["location"].startswith("/imports")


# ---------------- 入口：记录页 / 首页 ----------------


def test_records_page_lists_batches_with_one_click(tmp_path):
    first, second = _seed_two_batches(tmp_path)
    with _client(tmp_path) as client:
        body = client.get("/records?status=pending").text

    assert "待确认" in body
    assert body.count("一键确认") == 2                       # 两批各一个按钮
    assert f'action="/imports/{first}/confirm"' in body
    assert f'action="/imports/{second}/confirm"' in body
    assert "不会动其它待确认记录" in body


def test_home_one_click_targets_the_latest_batch_only(tmp_path):
    """首页的"一键"永远指向**最近导入的那一批**，并写明条数；多批时给一个"共 N 批"的入口。"""
    first, second = _seed_two_batches(tmp_path)

    with _client(tmp_path) as client:
        body = client.get("/").text
        assert "一键确认最近一批（1 条）" in body
        assert "共 2 批" in body
        assert f'action="/imports/{second}/confirm"' in body   # 指向较新的那批

        # 点它：只确认最近一批，早的那批原样留着
        assert client.post(
            f"/imports/{second}/confirm", data={"next_path": "/"}, follow_redirects=False
        ).status_code == 303

    pending = _pending_by_batch(tmp_path)
    assert second not in pending
    assert pending[first] == 1


def test_home_one_click_disappears_when_nothing_is_pending(tmp_path):
    day = (TODAY - timedelta(days=1)).isoformat()
    _import(tmp_path, [f"steps_count,{day},8000,count,day"], "only.csv")
    batch = _batch_ids(tmp_path)[0]
    with _client(tmp_path) as client:
        assert "一键确认这 1 条" in client.get("/").text   # 只有一批时的措辞
        assert client.post(
            f"/imports/{batch}/confirm", data={"next_path": "/"}, follow_redirects=False
        ).status_code == 303
        home = client.get("/").text

    assert "一键确认" not in home            # 确认完按钮消失
    assert _pending_by_batch(tmp_path) == {}


# ---------------- CLI ----------------


def test_cli_confirm_dry_run_does_not_write(tmp_path, capsys):
    _first, second = _seed_two_batches(tmp_path)

    code = cli_main(["confirm", "--batch", second[:8], "--db", str(tmp_path / "ui.db")])
    captured = capsys.readouterr().out

    assert code == 0
    assert "这是预览" in captured
    assert len(_pending_by_batch(tmp_path)) == 2   # 什么都没变


def test_cli_confirm_yes_confirms_only_that_batch(tmp_path, capsys):
    first, second = _seed_two_batches(tmp_path)

    code = cli_main(["confirm", "--batch", second[:8], "--yes", "--db", str(tmp_path / "ui.db")])
    captured = capsys.readouterr().out

    assert code == 0
    assert "已确认 1 条" in captured
    pending = _pending_by_batch(tmp_path)
    assert second not in pending
    assert pending[first] == 1


def test_cli_confirm_unknown_prefix_fails(tmp_path, capsys):
    _seed_two_batches(tmp_path)

    code = cli_main(["confirm", "--batch", "zzzz", "--db", str(tmp_path / "ui.db")])
    assert code == 1
    assert "没有以" in capsys.readouterr().err
