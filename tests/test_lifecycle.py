"""状态迁移：确认 / 驳回 / 改值 / 版本化修正 / 撤销批次 / 冲突裁决。"""

from __future__ import annotations

import pytest

from health_agent.errors import ConflictResolutionError, RecordStateError
from health_agent.storage.lifecycle import (
    confirm_batch,
    confirm_record,
    reject_record,
    resolve_conflict,
    revoke_batch,
    revise_confirmed,
    update_pending_value,
)
from health_agent.storage.repository import (
    count_records,
    create_batch,
    create_conflict,
    get_batch,
    get_record,
    insert_record,
    list_conflicts,
)

from conftest import make_record


def _pending(conn, **overrides) -> str:
    return insert_record(conn, make_record(**overrides), status="pending")


# ---------------- 确认 ----------------

def test_confirm_moves_pending_to_confirmed(conn):
    record_id = _pending(conn)
    confirm_record(conn, record_id)
    assert get_record(conn, record_id)["verification_status"] == "confirmed"
    assert count_records(conn, status="confirmed") == 1


def test_confirm_is_idempotent(conn):
    record_id = _pending(conn)
    confirm_record(conn, record_id)
    confirm_record(conn, record_id)
    assert count_records(conn, status="confirmed") == 1


def test_confirm_refuses_record_without_value(conn):
    record_id = _pending(conn, metric_name="nap_minutes", value=None)
    with pytest.raises(RecordStateError):
        confirm_record(conn, record_id)
    assert get_record(conn, record_id)["verification_status"] == "pending"


def test_confirm_with_force_allows_empty_value(conn):
    record_id = _pending(conn, metric_name="nap_minutes", value=None)
    confirm_record(conn, record_id, force=True)
    assert get_record(conn, record_id)["verification_status"] == "confirmed"


def test_confirm_cannot_overwrite_confirmed_slot(conn):
    """同一槽位已有已确认记录时，第二次确认必须失败——不能静默覆盖。"""
    first = insert_record(conn, make_record(value=402.0), status="confirmed")
    second = _pending(conn, value=410.0)

    with pytest.raises(RecordStateError):
        confirm_record(conn, second)

    assert get_record(conn, first)["verification_status"] == "confirmed"
    assert get_record(conn, second)["verification_status"] == "pending"


def test_confirm_rejects_unknown_record(conn):
    with pytest.raises(RecordStateError):
        confirm_record(conn, "no-such-id")


# ---------------- 驳回 ----------------

def test_reject_pending(conn):
    record_id = _pending(conn)
    reject_record(conn, record_id, reason="截图遮挡")
    assert get_record(conn, record_id)["verification_status"] == "rejected"


def test_reject_confirmed_is_refused(conn):
    record_id = _pending(conn)
    confirm_record(conn, record_id)
    with pytest.raises(RecordStateError):
        reject_record(conn, record_id)


# ---------------- 改值 ----------------

def test_update_pending_value_clears_unreadable_flag(conn):
    record_id = _pending(conn, value=None)
    conn.execute(
        "UPDATE records SET quality_flags = ? WHERE record_id = ?",
        ('["unreadable"]', record_id),
    )
    conn.commit()

    update_pending_value(conn, record_id, value=420.0, reason="对着截图手工补录")

    row = get_record(conn, record_id)
    assert row["value"] == 420.0
    assert row["quality_flags"] == "[]"
    assert row["verification_status"] == "pending"


def test_update_value_on_confirmed_is_refused(conn):
    record_id = _pending(conn)
    confirm_record(conn, record_id)
    with pytest.raises(RecordStateError):
        update_pending_value(conn, record_id, value=1.0)


# ---------------- 版本化修正 ----------------

def test_revise_confirmed_keeps_history(conn):
    record_id = _pending(conn)
    confirm_record(conn, record_id)

    new_id = revise_confirmed(conn, record_id, value=430.0, reason="截图看错了")

    old = get_record(conn, record_id)
    new = get_record(conn, new_id)
    assert old["verification_status"] == "superseded"
    assert old["superseded_by"] == new_id
    assert old["value"] == 402.0            # 旧值仍在
    assert new["verification_status"] == "confirmed"
    assert new["value"] == 430.0

    audit = conn.execute(
        "SELECT action FROM audit_log WHERE entity_id = ? ORDER BY created_at", (record_id,)
    ).fetchall()
    assert {row["action"] for row in audit} >= {"insert", "confirm", "revise"}


def test_revise_refuses_pending_record(conn):
    record_id = _pending(conn)
    with pytest.raises(RecordStateError):
        revise_confirmed(conn, record_id, value=1.0, reason="x")


# ---------------- 批次撤销 ----------------

def test_revoke_batch_deletes_pending_and_supersedes_confirmed(conn):
    batch_id = create_batch(conn, file_name="a.json")
    pending_id = insert_record(conn, make_record(), batch_id=batch_id, status="pending")
    confirmed_id = insert_record(
        conn, make_record(metric_name="steps_count", unit="count", value=8000.0),
        batch_id=batch_id, status="pending",
    )
    confirm_record(conn, confirmed_id)

    result = revoke_batch(conn, batch_id)

    assert result == {"deleted_pending": 1, "superseded_confirmed": 1}
    assert get_record(conn, pending_id) is None
    assert get_record(conn, confirmed_id)["verification_status"] == "superseded"
    assert get_batch(conn, batch_id)["status"] == "revoked"


def test_revoke_twice_is_refused(conn):
    batch_id = create_batch(conn, file_name="a.json")
    revoke_batch(conn, batch_id)
    with pytest.raises(RecordStateError):
        revoke_batch(conn, batch_id)


# ---------------- 冲突裁决 ----------------

def _make_conflict(conn):
    existing = insert_record(conn, make_record(value=402.0), status="confirmed")
    incoming = insert_record(conn, make_record(value=430.0), status="pending")
    conflict_id = create_conflict(
        conn,
        logical_key=get_record(conn, incoming)["logical_key"],
        metric_name="night_sleep_minutes",
        record_date="2026-10-01",
        existing_record_id=existing,
        incoming_record_id=incoming,
    )
    conn.commit()
    return conflict_id, existing, incoming


def test_resolve_keep_existing(conn):
    conflict_id, existing, incoming = _make_conflict(conn)
    resolve_conflict(conn, conflict_id, "keep_existing")

    assert get_record(conn, existing)["verification_status"] == "confirmed"
    assert get_record(conn, incoming)["verification_status"] == "rejected"
    assert list_conflicts(conn, "pending") == []


def test_resolve_take_incoming(conn):
    conflict_id, existing, incoming = _make_conflict(conn)
    resolve_conflict(conn, conflict_id, "take_incoming")

    assert get_record(conn, existing)["verification_status"] == "superseded"
    assert get_record(conn, incoming)["verification_status"] == "confirmed"
    assert count_records(conn, status="confirmed") == 1


def test_resolve_twice_is_refused(conn):
    conflict_id, _, _ = _make_conflict(conn)
    resolve_conflict(conn, conflict_id, "keep_existing")
    with pytest.raises(ConflictResolutionError):
        resolve_conflict(conn, conflict_id, "take_incoming")


def test_keep_both_is_not_a_legal_resolution(conn):
    conflict_id, _, _ = _make_conflict(conn)
    with pytest.raises(ConflictResolutionError):
        resolve_conflict(conn, conflict_id, "keep_both")


# ---------------- 批量确认 ----------------

def test_confirm_batch_skips_records_without_value(conn):
    batch_id = create_batch(conn, file_name="a.json")
    insert_record(conn, make_record(), batch_id=batch_id, status="pending")
    insert_record(conn, make_record(metric_name="nap_minutes", value=None), batch_id=batch_id, status="pending")

    result = confirm_batch(conn, batch_id)

    assert result == {"confirmed": 1, "skipped": 1}
    assert count_records(conn, status="confirmed") == 1
    assert count_records(conn, status="pending") == 1
