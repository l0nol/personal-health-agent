"""设置（全部可跳过）与导出/删除。"""

from __future__ import annotations

import json

from health_agent.storage.exporting import delete_record, export_csv, export_json
from health_agent.storage.lifecycle import confirm_record
from health_agent.storage.preferences import ALLOWED_KEYS, get_settings, set_settings
from health_agent.storage.repository import count_records, get_record, insert_record

from conftest import make_record


# ---------------- 设置 ----------------

def test_settings_empty_by_default(conn):
    """没有设置时必须能正常工作——这是规格里"可选"二字的硬要求。"""
    assert get_settings(conn) == {}


def test_settings_roundtrip(conn):
    set_settings(conn, {"wake_time": "07:00", "reminder_frequency": "daily"})
    stored = get_settings(conn)
    assert stored["wake_time"] == "07:00"
    assert stored["reminder_frequency"] == "daily"


def test_empty_string_clears_key(conn):
    set_settings(conn, {"wake_time": "07:00"})
    set_settings(conn, {"wake_time": "   "})
    assert "wake_time" not in get_settings(conn)


def test_unknown_keys_are_ignored(conn):
    set_settings(conn, {"rm_rf_everything": "yes", "wake_time": "06:30"})
    stored = get_settings(conn)
    assert "rm_rf_everything" not in stored
    assert stored["wake_time"] == "06:30"


def test_allowed_keys_are_documented_list(conn):
    assert "notes" in ALLOWED_KEYS
    set_settings(conn, {key: "x" for key in ALLOWED_KEYS})
    assert len(get_settings(conn)) == len(ALLOWED_KEYS)


# ---------------- 导出 ----------------

def test_export_csv_has_header_and_rows(conn):
    insert_record(conn, make_record(), status="pending")
    insert_record(conn, make_record(metric_name="steps_count", unit="count", value=8000.0), status="pending")

    text = export_csv(conn)
    lines = text.strip().splitlines()
    assert lines[0].startswith("record_id,record_date,metric_name")
    assert len(lines) == 3


def test_export_json_is_valid_and_parsable(conn):
    insert_record(conn, make_record(), status="pending")
    payload = json.loads(export_json(conn))
    assert payload["schema_version"] == "1.0"
    assert len(payload["records"]) == 1
    assert payload["records"][0]["metric_name"] == "night_sleep_minutes"


def test_export_filter_by_status(conn):
    record_id = insert_record(conn, make_record(), status="pending")
    insert_record(conn, make_record(metric_name="steps_count", unit="count", value=8000.0), status="pending")
    confirm_record(conn, record_id)

    confirmed = json.loads(export_json(conn, status="confirmed"))
    assert len(confirmed["records"]) == 1
    assert confirmed["records"][0]["verification_status"] == "confirmed"


def test_export_preserves_null(conn):
    insert_record(conn, make_record(metric_name="nap_minutes", value=None), status="pending")
    payload = json.loads(export_json(conn))
    assert payload["records"][0]["value"] is None


# ---------------- 删除 ----------------

def test_delete_record_removes_row_and_audits(conn):
    record_id = insert_record(conn, make_record(), status="pending")
    delete_record(conn, record_id)

    assert get_record(conn, record_id) is None
    assert count_records(conn) == 0
    audit = conn.execute(
        "SELECT action FROM audit_log WHERE entity_id = ? ORDER BY created_at", (record_id,)
    ).fetchall()
    assert [row["action"] for row in audit][-1] == "delete"
