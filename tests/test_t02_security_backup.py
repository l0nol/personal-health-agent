"""T02 安全、备份与 AI 回退：为已确认缺陷建立复现测试。

缺陷编号对应 `reports/T02_SECURITY_BACKUP_AI_REPORT.md`：
  S1 登录 next 参数可跳转到站外（开放重定向）
  S2 上传无大小上限（整包读进内存）
  S3 恢复失败会先覆盖目标库，坏包能把现役库写成垃圾
  S4 恢复不清理旧的 -wal/-shm，陈旧 WAL 会被回放进新库

以及守门测试：会话 Cookie 属性、AI 无密钥/失败路径、备份不带 WAL 残留。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from health_agent.backup import (
    ARCHIVE_MEMBER,
    MANIFEST_MEMBER,
    BackupError,
    create_backup,
    restore_backup,
)
from health_agent.config import load_settings
from health_agent.storage.db import init_db
from health_agent.storage.repository import insert_record, seed_catalog
from health_agent.ui.app import create_app
from health_agent.ui.auth import hash_password

from conftest import make_record

PASSWORD = "correct horse battery staple"


def _client(tmp_path: Path, *, password_hash: str | None = None) -> TestClient:
    settings = load_settings(
        {"HEALTH_AGENT_PASSWORD_HASH": password_hash or ""}, project_root=tmp_path
    )
    return TestClient(create_app(settings, db_path=tmp_path / "ui.db"))


def _seeded_db(tmp_path: Path, *, records: int = 1) -> Path:
    db = tmp_path / "health.db"
    conn = init_db(db)
    seed_catalog(conn)
    for index in range(records):
        insert_record(
            conn,
            make_record(record_date=f"2026-10-{index + 1:02d}", value=400.0 + index),
            status="confirmed",
        )
    conn.commit()
    conn.close()
    return db


def _archive_with_payload(source: Path, target: Path, payload: bytes) -> Path:
    """造一个 sha256 自洽、但内容是垃圾的备份包。"""
    manifest = json.loads(zipfile.ZipFile(source).read(MANIFEST_MEMBER))
    manifest["db_sha256"] = hashlib.sha256(payload).hexdigest()
    manifest["db_bytes"] = len(payload)
    with zipfile.ZipFile(target, "w") as bundle:
        bundle.writestr(ARCHIVE_MEMBER, payload)
        bundle.writestr(MANIFEST_MEMBER, json.dumps(manifest))
    return target


# --------------------------------------------------------------------------
# S1 开放重定向
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    ["//evil.example", "https://evil.example/x", "/\\evil.example", "http://127.0.0.1:1/@evil"],
)
def test_login_never_redirects_off_site(tmp_path, target):
    client = _client(tmp_path, password_hash=hash_password(PASSWORD))

    response = client.post(
        "/login", data={"password": PASSWORD, "next": target}, follow_redirects=False
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location == "/", f"next={target!r} 跳到了站外：{location}"


def test_login_still_honours_internal_path(tmp_path):
    client = _client(tmp_path, password_hash=hash_password(PASSWORD))

    response = client.post(
        "/login", data={"password": PASSWORD, "next": "/records"}, follow_redirects=False
    )

    assert response.headers["location"] == "/records"


# --------------------------------------------------------------------------
# S2 上传大小上限
# --------------------------------------------------------------------------


def test_upload_rejects_oversized_file(tmp_path):
    client = _client(tmp_path)
    payload = b"[" + b'{"pad":"' + b"x" * (11 * 1024 * 1024) + b'"}' + b"]"

    response = client.post(
        "/imports/upload",
        files={"file": ("huge.json", payload, "application/json")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"], "超大文件必须被拒绝"
    uploads = tmp_path / "uploads"
    assert not any(uploads.iterdir()), "被拒绝的文件不得落盘"


# --------------------------------------------------------------------------
# S3 恢复失败不得破坏现役库
# --------------------------------------------------------------------------


def test_restore_failure_leaves_existing_database_intact(tmp_path):
    db = _seeded_db(tmp_path, records=1)
    archive = create_backup(db, backup_dir=tmp_path / "bk")
    bad = _archive_with_payload(archive, tmp_path / "bad.zip", b"definitely not sqlite")

    with pytest.raises(BackupError):
        restore_backup(bad, db, force=True)

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 1
    finally:
        conn.close()


def test_restore_failure_leaves_no_temp_files(tmp_path):
    db = _seeded_db(tmp_path, records=1)
    archive = create_backup(db, backup_dir=tmp_path / "bk")
    bad = _archive_with_payload(archive, tmp_path / "bad.zip", b"definitely not sqlite")

    with pytest.raises(BackupError):
        restore_backup(bad, db, force=True)

    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".restore-")]
    assert leftovers == []


# --------------------------------------------------------------------------
# S4 陈旧 WAL 不得回放进新库
# --------------------------------------------------------------------------


def test_restore_discards_stale_wal(tmp_path):
    db = _seeded_db(tmp_path, records=1)
    archive = create_backup(db, backup_dir=tmp_path / "bk")  # 备份里只有 1 条

    # 模拟"进程被杀"：写入第 2 条后把 -wal 留存下来
    wal_path = Path(f"{db}-wal")
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    insert_record(conn, make_record(record_date="2026-10-02", value=300.0), status="confirmed")
    conn.commit()
    stale_wal = wal_path.read_bytes()
    conn.close()  # 正常关闭会 checkpoint 并删除 WAL
    wal_path.write_bytes(stale_wal)  # 再把"崩溃现场"放回去

    restore_backup(archive, db, force=True)

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 1, (
            "陈旧 WAL 被回放进新库，恢复结果被污染"
        )
    finally:
        conn.close()


def test_restore_reports_matching_record_count(tmp_path):
    db = _seeded_db(tmp_path, records=2)
    archive = create_backup(db, backup_dir=tmp_path / "bk")

    manifest = restore_backup(archive, db, force=True)

    conn = sqlite3.connect(db)
    try:
        actual = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    finally:
        conn.close()
    assert manifest["restored_total_records"] == actual == 2


# --------------------------------------------------------------------------
# 守门
# --------------------------------------------------------------------------


def test_session_cookie_is_httponly_and_samesite(tmp_path):
    client = _client(tmp_path, password_hash=hash_password(PASSWORD))

    response = client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    cookie = response.headers["set-cookie"].lower()

    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_backup_snapshot_has_no_wal_leftover(tmp_path):
    db = _seeded_db(tmp_path, records=1)

    archive = create_backup(db, backup_dir=tmp_path / "bk")

    with zipfile.ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == {ARCHIVE_MEMBER, MANIFEST_MEMBER}


def test_backup_of_missing_database_fails_loudly(tmp_path):
    with pytest.raises(BackupError):
        create_backup(tmp_path / "nope.db")
