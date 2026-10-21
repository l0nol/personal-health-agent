"""T08 训练日志与恢复上下文：迁移、校验、仓储、导出与恢复。

覆盖路线图要求：新库、旧库迁移、重复迁移、空字段、编辑/删除、导出与恢复。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from health_agent.backup import create_backup, restore_backup
from health_agent.storage import training as store
from health_agent.storage.db import (
    MIGRATIONS_DIR,
    apply_migrations,
    current_version,
    init_db,
    table_names,
)
from health_agent.storage.exporting import export_json, export_training_csv
from health_agent.storage.repository import count_records, seed_catalog
from health_agent.training import service
from health_agent.training.errors import (
    E_ACTIVITY,
    E_BODY_PART,
    E_COMPLETION,
    E_DATE,
    E_RANGE,
    E_SORENESS_PAIR,
    TrainingValidationError,
)

TODAY = date(2026, 10, 20)


def _seed_v1_database(path: Path) -> None:
    """造一个只有 001 迁移的旧库，用来验证升级路径。"""
    conn = sqlite3.connect(path)
    try:
        conn.executescript((MIGRATIONS_DIR / "001_init.sql").read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO schema_migrations (version, filename, applied_at) VALUES (1, '001_init.sql', 'x')"
        )
        conn.execute(
            """
            INSERT INTO records (
                record_id, logical_key, dedup_key, record_date, metric_name, value, unit,
                period_type, source_app, verification_status, quality_flags, created_at, updated_at
            ) VALUES ('r1', 'k1', 'd1', '2026-10-01', 'night_sleep_minutes', 400, 'min',
                      'day', 'huawei_health', 'confirmed', '[]', 'x', 'x')
            """
        )
        conn.commit()
    finally:
        conn.close()


# ---------------- 迁移 ----------------


def test_new_database_has_training_tables(conn):
    assert current_version(conn) == 2
    assert {"training_sessions", "recovery_feedback", "training_preferences"} <= set(
        table_names(conn)
    )


def test_old_database_upgrades_without_losing_records(tmp_path):
    path = tmp_path / "old.db"
    _seed_v1_database(path)

    upgraded = init_db(path)
    try:
        assert current_version(upgraded) == 2
        assert "training_sessions" in table_names(upgraded)
        row = upgraded.execute("SELECT value FROM records WHERE record_id = 'r1'").fetchone()
        assert row["value"] == 400
    finally:
        upgraded.close()


def test_migration_is_idempotent(conn):
    before = conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
    apply_migrations(conn)
    apply_migrations(conn)
    after = conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
    assert before == after == 2
    assert current_version(conn) == 2


# ---------------- 校验 ----------------


def test_nullable_fields_stay_null(conn):
    session = service.add_session(
        conn,
        session_date="2026-10-19",
        activity_type="strength",
        body_parts="",
        duration_minutes=None,
        perceived_intensity=None,
        today=TODAY,
    )
    row = conn.execute(
        "SELECT duration_minutes, perceived_intensity, body_parts, notes FROM training_sessions"
        " WHERE session_id = ?",
        (session.session_id,),
    ).fetchone()
    assert row["duration_minutes"] is None
    assert row["perceived_intensity"] is None
    assert row["body_parts"] == ""
    assert row["notes"] is None


def test_body_parts_are_normalized_and_deduped(conn):
    session = service.add_session(
        conn,
        session_date="2026-10-19",
        activity_type="strength",
        body_parts="胸, 肩，胸, shoulders",
        today=TODAY,
    )
    assert session.body_parts == ("chest", "shoulders")


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"session_date": "2026-13-01"}, E_DATE),
        ({"session_date": "2026-10-21"}, E_DATE),
        ({"activity_type": "yoga"}, E_ACTIVITY),
        ({"body_parts": "尾巴"}, E_BODY_PART),
        ({"duration_minutes": 0}, E_RANGE),
        ({"duration_minutes": 601}, E_RANGE),
        ({"perceived_intensity": 11}, E_RANGE),
        ({"completion": "done"}, E_COMPLETION),
    ],
)
def test_invalid_session_is_rejected(conn, overrides, code):
    payload = {"session_date": "2026-10-19", "activity_type": "strength", "today": TODAY}
    payload.update(overrides)
    with pytest.raises(TrainingValidationError) as exc:
        service.add_session(conn, **payload)
    assert exc.value.code == code


def test_soreness_parts_and_level_must_pair(conn):
    with pytest.raises(TrainingValidationError) as exc:
        service.record_feedback(
            conn, feedback_date="2026-10-20", soreness_parts="胸", today=TODAY
        )
    assert exc.value.code == E_SORENESS_PAIR

    with pytest.raises(TrainingValidationError) as exc:
        service.record_feedback(
            conn, feedback_date="2026-10-20", soreness_level=3, today=TODAY
        )
    assert exc.value.code == E_SORENESS_PAIR


def test_recovery_feedback_cannot_be_marked_as_device(conn):
    """主观反馈不得伪装成设备测量：这条由数据库约束兜底，而不是靠自觉。"""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO recovery_feedback (
                feedback_id, feedback_date, energy, fatigue, soreness_parts,
                soreness_level, discomfort, source, created_at, updated_at
            ) VALUES ('x', '2026-10-20', 3, 3, '', NULL, 0, 'device', 'x', 'x')
            """
        )


# ---------------- 编辑 / 删除 / 反馈 ----------------


def test_edit_session_updates_and_audits(conn):
    session = service.add_session(
        conn, session_date="2026-10-19", activity_type="strength", duration_minutes=40, today=TODAY
    )
    updated = service.edit_session(conn, session.session_id, duration_minutes=30, notes="膝盖不适，减量")
    assert updated.duration_minutes == 30
    assert updated.notes == "膝盖不适，减量"
    actions = [
        row["action"]
        for row in conn.execute(
            "SELECT action FROM audit_log WHERE entity = 'training_sessions' ORDER BY created_at"
        ).fetchall()
    ]
    assert "update" in actions


def test_delete_session_removes_row_and_audits(conn):
    session = service.add_session(
        conn, session_date="2026-10-19", activity_type="cardio", today=TODAY
    )
    service.remove_session(conn, session.session_id)
    assert store.get_session(conn, session.session_id) is None
    actions = [
        row["action"]
        for row in conn.execute(
            "SELECT action FROM audit_log WHERE entity_id = ?", (session.session_id,)
        ).fetchall()
    ]
    assert "delete" in actions


def test_delete_missing_session_reports_clear_error(conn):
    with pytest.raises(TrainingValidationError):
        service.remove_session(conn, "nope")


def test_feedback_upsert_keeps_one_row_per_day(conn):
    service.record_feedback(
        conn, feedback_date="2026-10-20", energy=2, fatigue=4, today=TODAY
    )
    service.record_feedback(
        conn, feedback_date="2026-10-20", energy=4, fatigue=2, today=TODAY
    )
    rows = store.list_feedback(conn, start="2026-10-20", end="2026-10-20")
    assert len(rows) == 1
    assert rows[0].energy == 4
    assert rows[0].fatigue == 2
    assert rows[0].source == "subjective"


def test_feedback_lookup_falls_back_within_window(conn):
    service.record_feedback(conn, feedback_date="2026-10-19", fatigue=3, today=TODAY)
    assert store.get_feedback(conn, "2026-10-20") is None
    fallback = store.latest_feedback(conn, TODAY, days=2)
    assert fallback is not None and fallback.feedback_date == "2026-10-19"


# ---------------- 列表 / 偏好 / 隔离 ----------------


def test_list_sessions_filters_by_window(conn):
    service.add_session(conn, session_date="2026-10-01", activity_type="strength", today=TODAY)
    service.add_session(conn, session_date="2026-10-19", activity_type="cardio", today=TODAY)

    recent = store.list_sessions(conn, start="2026-10-10", end="2026-10-20")
    assert [session.session_date for session in recent] == ["2026-10-19"]
    assert store.count_sessions(conn, start="2026-10-01", end="2026-10-20") == 2


def test_training_logs_do_not_touch_device_records(conn):
    service.add_session(conn, session_date="2026-10-19", activity_type="strength", today=TODAY)
    service.record_feedback(conn, feedback_date="2026-10-19", fatigue=2, today=TODAY)
    assert count_records(conn) == 0


def test_preferences_roundtrip_and_validation(conn):
    saved = service.save_preferences(
        conn,
        equipment="哑铃, 弹力带",
        days_per_week=3,
        time_budget_minutes=45,
        avoid_movements="跳箱",
    )
    assert saved.days_per_week == 3
    assert saved.time_budget_minutes == 45
    assert saved.equipment == ("哑铃", "弹力带")

    with pytest.raises(TrainingValidationError) as exc:
        service.save_preferences(conn, days_per_week=9)
    assert exc.value.code == E_RANGE


# ---------------- 导出与备份 ----------------


def test_export_includes_training_without_breaking_records(conn):
    service.add_session(
        conn, session_date="2026-10-19", activity_type="strength", body_parts="legs", today=TODAY
    )
    service.record_feedback(conn, feedback_date="2026-10-19", fatigue=3, today=TODAY)
    service.save_preferences(conn, days_per_week=2)

    payload = json.loads(export_json(conn))
    assert payload["schema_version"] == "1.0"  # 只加键，不改版本
    assert len(payload["training_sessions"]) == 1
    assert payload["training_sessions"][0]["body_parts"] == "legs"
    assert len(payload["recovery_feedback"]) == 1
    assert payload["recovery_feedback"][0]["source"] == "subjective"
    assert payload["training_preferences"]["days_per_week"] == 2

    csv_text = export_training_csv(conn)
    assert csv_text.splitlines()[0].startswith("session_id,session_date,started_at,activity_type")


def test_backup_and_restore_keep_training_logs(tmp_path):
    db = tmp_path / "health.db"
    conn = init_db(db)
    seed_catalog(conn)
    service.add_session(conn, session_date="2026-10-19", activity_type="strength", today=TODAY)
    conn.close()

    archive = create_backup(db)
    restored_path = tmp_path / "restored.db"
    restore_backup(archive, restored_path)

    restored = init_db(restored_path)
    try:
        assert store.count_sessions(restored) == 1
        assert current_version(restored) == 2
    finally:
        restored.close()


def test_restoring_old_backup_applies_new_migration(tmp_path):
    """旧备份（schema v1）恢复后必须自动升级，否则训练功能直接不可用。"""
    old_db = tmp_path / "old.db"
    _seed_v1_database(old_db)
    archive = create_backup(old_db)

    target = tmp_path / "restored.db"
    manifest = restore_backup(archive, target)

    restored = init_db(target)
    try:
        assert current_version(restored) == 2
        assert "training_sessions" in table_names(restored)
        assert manifest["restored_total_records"] == 1
    finally:
        restored.close()
