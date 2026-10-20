"""备份与恢复：快照一致性、指纹校验、拒绝无条件覆盖。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from health_agent.backup import (
    ARCHIVE_MEMBER,
    MANIFEST_MEMBER,
    BackupError,
    create_backup,
    inspect_backup,
    restore_backup,
)
from health_agent.cli import main
from health_agent.storage.db import init_db
from health_agent.storage.lifecycle import confirm_record
from health_agent.storage.repository import count_records, insert_record, seed_catalog

from conftest import make_record


def _populated(db_path: Path) -> None:
    conn = init_db(db_path)
    seed_catalog(conn)
    pending = insert_record(conn, make_record(record_date="2026-10-01", value=400.0), status="pending")
    confirmed = insert_record(
        conn, make_record(record_date="2026-10-02", value=410.0), status="pending"
    )
    confirm_record(conn, confirmed)
    assert pending
    conn.close()


def test_backup_contains_database_and_manifest(tmp_path):
    db = tmp_path / "health.db"
    _populated(db)

    archive = create_backup(db)

    assert archive.is_file()
    with zipfile.ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == {ARCHIVE_MEMBER, MANIFEST_MEMBER}


def test_manifest_reports_real_counts(tmp_path):
    db = tmp_path / "health.db"
    _populated(db)

    archive = create_backup(db)
    manifest = inspect_backup(archive)

    assert manifest["counts"]["pending"] == 1
    assert manifest["counts"]["confirmed"] == 1
    assert manifest["total_records"] == 2
    assert manifest["schema_version"] == 1
    assert manifest["verified"] is True


def test_backup_requires_existing_database(tmp_path):
    with pytest.raises(BackupError):
        create_backup(tmp_path / "missing.db")


def test_tampered_archive_is_rejected(tmp_path):
    db = tmp_path / "health.db"
    _populated(db)
    archive = create_backup(db)

    with zipfile.ZipFile(archive) as bundle:
        manifest = bundle.read(MANIFEST_MEMBER)

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(tampered, "w") as bundle:
        bundle.writestr(ARCHIVE_MEMBER, b"not a database")
        bundle.writestr(MANIFEST_MEMBER, manifest)

    with pytest.raises(BackupError, match="sha256"):
        inspect_backup(tampered)


def test_archive_without_database_is_rejected(tmp_path):
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(broken, "w") as bundle:
        bundle.writestr(MANIFEST_MEMBER, json.dumps({"db_sha256": "x"}))

    with pytest.raises(BackupError):
        inspect_backup(broken)


def test_restore_rebuilds_data(tmp_path):
    db = tmp_path / "health.db"
    _populated(db)
    archive = create_backup(db)

    fresh = tmp_path / "restored.db"
    manifest = restore_backup(archive, fresh)

    assert manifest["restored_total_records"] == 2
    conn = init_db(fresh)
    assert count_records(conn, status="confirmed") == 1
    conn.close()


def test_restore_refuses_to_overwrite_without_force(tmp_path):
    db = tmp_path / "health.db"
    _populated(db)
    archive = create_backup(db)

    with pytest.raises(BackupError, match="已存在"):
        restore_backup(archive, db)


def test_restore_with_force_keeps_a_copy_of_existing(tmp_path):
    db = tmp_path / "health.db"
    _populated(db)
    archive = create_backup(db)

    manifest = restore_backup(archive, db, force=True)

    assert manifest["backup_of_existing"]
    assert Path(manifest["backup_of_existing"]).is_file()


def test_restored_database_passes_integrity_check(tmp_path):
    import sqlite3

    db = tmp_path / "health.db"
    _populated(db)
    archive = create_backup(db)

    fresh = tmp_path / "restored.db"
    restore_backup(archive, fresh)

    conn = sqlite3.connect(fresh)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


# ---------------- 命令行 ----------------

def test_cli_backup_and_restore(tmp_path, capsys):
    db = tmp_path / "health.db"
    _populated(db)

    code = main(["backup", "--db", str(db)])
    assert code == 0
    assert "已备份" in capsys.readouterr().out

    archives = list((tmp_path / "backup").glob("health-*.zip"))
    assert len(archives) == 1

    target = tmp_path / "another.db"
    code = main(["restore", str(archives[0]), "--db", str(target)])
    assert code == 0
    assert "记录数: 2" in capsys.readouterr().out


def test_cli_restore_without_force_fails_loudly(tmp_path, capsys):
    db = tmp_path / "health.db"
    _populated(db)
    archive = create_backup(db)

    code = main(["restore", str(archive), "--db", str(db)])
    assert code == 1
    assert "恢复失败" in capsys.readouterr().err


def test_cli_export_csv(tmp_path, capsys):
    db = tmp_path / "health.db"
    _populated(db)
    out = tmp_path / "records.csv"

    code = main(["export", "--format", "csv", "--out", str(out), "--db", str(db)])

    assert code == 0
    text = out.read_text(encoding="utf-8-sig")
    assert text.startswith("record_id,record_date,metric_name")
    assert "2026-10-01" in text
    assert "已导出" in capsys.readouterr().out


def test_cli_export_json(tmp_path):
    db = tmp_path / "health.db"
    _populated(db)
    out = tmp_path / "records.json"

    main(["export", "--format", "json", "--out", str(out), "--db", str(db)])

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload["records"]) == 2
