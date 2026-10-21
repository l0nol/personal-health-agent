"""存储层：迁移幂等、去重键一致性、部分唯一索引真的挡住了静默覆盖。"""

from __future__ import annotations

import sqlite3

import pytest

from health_agent.catalog import METRICS
from health_agent.storage.db import current_version, index_names, init_db, migration_files, table_names
from health_agent.storage.keys import dedup_key, logical_key
from health_agent.storage.repository import (
    count_audit,
    count_records,
    find_by_logical_key,
    get_record,
    insert_record,
    seed_catalog,
)

from conftest import make_record

EXPECTED_TABLES = {
    "schema_migrations",
    "metrics_catalog",
    "import_batches",
    "records",
    "conflicts",
    "baselines",
    "alerts",
    "settings",
    "ai_call_log",
    "report_cache",
    "audit_log",
}


def test_migrations_are_idempotent(db_file):
    conn = init_db(db_file)
    first = current_version(conn)
    conn.close()

    conn = init_db(db_file)
    second = current_version(conn)
    conn.close()

    # 与迁移文件数量对齐，而不是写死版本号：新增迁移时这条断言仍然有效
    assert first == second == migration_files()[-1][0]


def test_all_tables_created(conn):
    assert EXPECTED_TABLES <= set(table_names(conn))


def test_partial_unique_index_exists(conn):
    assert "uq_records_confirmed" in index_names(conn)


def test_seed_catalog_writes_every_metric(conn):
    total = conn.execute("SELECT COUNT(*) AS n FROM metrics_catalog").fetchone()["n"]
    assert total == len(METRICS)
    # 重复播种不应产生重复行
    assert seed_catalog(conn) == len(METRICS)
    again = conn.execute("SELECT COUNT(*) AS n FROM metrics_catalog").fetchone()["n"]
    assert again == len(METRICS)


def test_logical_key_is_stable_and_value_independent():
    a = logical_key(
        source_app="huawei_health",
        record_date="2026-10-01",
        metric_name="night_sleep_minutes",
        period_type="day",
    )
    b = logical_key(
        source_app="huawei_health",
        record_date="2026-10-01",
        metric_name="night_sleep_minutes",
        period_type="day",
    )
    c = logical_key(
        source_app="huawei_health",
        record_date="2026-10-02",
        metric_name="night_sleep_minutes",
        period_type="day",
    )
    assert a == b
    assert a != c


def test_dedup_key_distinguishes_values():
    logical = logical_key(
        source_app="huawei_health",
        record_date="2026-10-01",
        metric_name="night_sleep_minutes",
        period_type="day",
    )
    assert dedup_key(logical, unit="min", value=402.0) == dedup_key(logical, unit="min", value=402.0)
    assert dedup_key(logical, unit="min", value=402.0) != dedup_key(logical, unit="min", value=403.0)
    assert dedup_key(logical, unit="min", value=None) == dedup_key(logical, unit="min", value=None)


def test_insert_pending_record_and_read_back(conn):
    record_id = insert_record(conn, make_record())
    row = get_record(conn, record_id)
    assert row is not None
    assert row["verification_status"] == "pending"
    assert row["value"] == 402.0
    assert row["quality_flags"] == "[]"
    assert count_records(conn) == 1


def test_null_value_is_stored_as_null(conn):
    record_id = insert_record(conn, make_record(metric_name="nap_minutes", value=None))
    row = get_record(conn, record_id)
    assert row["value"] is None


def test_insert_writes_audit_log(conn):
    insert_record(conn, make_record())
    insert_record(conn, make_record(record_date="2026-10-02"))
    assert count_audit(conn, "records") == 2


def test_second_confirmed_record_on_same_logical_key_is_blocked(conn):
    insert_record(conn, make_record(), status="confirmed")
    with pytest.raises(sqlite3.IntegrityError):
        insert_record(conn, make_record(), status="confirmed")
    conn.rollback()
    assert count_records(conn, status="confirmed") == 1


def test_multiple_pending_records_can_coexist_for_conflict_review(conn):
    first = insert_record(conn, make_record(), status="pending")
    second = insert_record(conn, make_record(value=410.0), status="pending")
    rows = find_by_logical_key(conn, get_record(conn, first)["logical_key"])
    assert {r["record_id"] for r in rows} == {first, second}
    assert count_records(conn, status="pending") == 2


def test_rejected_record_does_not_block_confirmation(conn):
    insert_record(conn, make_record(), status="rejected")
    insert_record(conn, make_record(), status="confirmed")
    assert count_records(conn, status="confirmed") == 1
    assert count_records(conn, status="rejected") == 1


def test_invalid_status_is_refused(conn):
    with pytest.raises(ValueError):
        insert_record(conn, make_record(), status="maybe")
