"""导入链路：文件读取、五桶分桶、落库。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from health_agent.cli import main
from health_agent.ingestion.batch import commit_import
from health_agent.ingestion.loader import LoadError, load_rows
from health_agent.ingestion.preview import build_preview
from health_agent.storage.repository import count_records, get_batch, insert_record, list_conflicts

from conftest import make_record

DATA_DIR = Path(__file__).resolve().parents[1] / "test_data"
SAMPLE_JSON = DATA_DIR / "sample_daily_metrics.json"
SAMPLE_ISSUES = DATA_DIR / "sample_with_issues.csv"


def rows_of(*items: dict) -> list:
    from health_agent.ingestion.loader import RawRow

    return [RawRow(index=i, data=item) for i, item in enumerate(items, start=1)]


def base_row(**overrides) -> dict:
    row = {
        "metric_name": "night_sleep_minutes",
        "record_date": "2026-10-10",
        "value": 400,
        "unit": "min",
        "period_type": "day",
    }
    row.update(overrides)
    return row


# ---------------- 读取 ----------------

def test_load_json_with_records_key():
    rows = load_rows(SAMPLE_JSON)
    assert len(rows) == 21
    assert rows[0].data["metric_name"] == "night_sleep_minutes"


def test_load_json_bare_list(tmp_path):
    file = tmp_path / "list.json"
    file.write_text(json.dumps([base_row()]), encoding="utf-8")
    assert len(load_rows(file)) == 1


def test_load_json_single_object(tmp_path):
    file = tmp_path / "one.json"
    file.write_text(json.dumps(base_row()), encoding="utf-8")
    rows = load_rows(file)
    assert len(rows) == 1


def test_load_json_rejects_broken_file(tmp_path):
    file = tmp_path / "bad.json"
    file.write_text("{not json", encoding="utf-8")
    with pytest.raises(LoadError):
        load_rows(file)


def test_load_json_requires_records_array(tmp_path):
    file = tmp_path / "obj.json"
    file.write_text(json.dumps({"foo": 1}), encoding="utf-8")
    with pytest.raises(LoadError):
        load_rows(file)


def test_load_csv_tolerates_bom(tmp_path):
    """工具导出的 CSV 常带 BOM，键名不能变成 \\ufeffmetric_name。"""
    file = tmp_path / "bom.csv"
    file.write_bytes(
        "metric_name,record_date,value,unit,period_type\n"
        "steps_count,2026-10-10,8000,count,day\n".encode("utf-8-sig")
    )
    rows = load_rows(file)
    assert rows[0].data["metric_name"] == "steps_count"


def test_load_csv_with_gbk_encoding(tmp_path):
    file = tmp_path / "gbk.csv"
    file.write_bytes("metric_name,record_date,value,unit\nsteps_count,2026-10-10,8000,步\n".encode("gb18030"))
    rows = load_rows(file)
    assert rows[0].data["unit"] == "步"


def test_load_csv_empty_cell_becomes_none(tmp_path):
    file = tmp_path / "empty.csv"
    file.write_text("metric_name,record_date,value,unit\nnap_minutes,2026-10-10,,min\n", encoding="utf-8")
    assert load_rows(file)[0].data["value"] is None


def test_load_rejects_unsupported_suffix(tmp_path):
    file = tmp_path / "x.xlsx"
    file.write_text("a", encoding="utf-8")
    with pytest.raises(LoadError):
        load_rows(file)


def test_load_rejects_missing_file(tmp_path):
    with pytest.raises(LoadError):
        load_rows(tmp_path / "nope.json")


# ---------------- 分桶 ----------------

def test_clean_rows_go_to_accepted(conn):
    preview = build_preview(rows_of(base_row()), conn)
    assert len(preview.accepted) == 1
    assert preview.review == [] and preview.rejected == []


def test_flagged_row_goes_to_review(conn):
    preview = build_preview(rows_of(base_row(value="--")), conn)
    assert len(preview.review) == 1
    assert "unreadable" in preview.review[0].flags


def test_missing_value_goes_to_review_not_zero(conn):
    preview = build_preview(rows_of(base_row(metric_name="nap_minutes", value=None)), conn)
    assert len(preview.review) == 1
    assert preview.review[0].record.value is None


def test_unknown_metric_is_rejected(conn):
    preview = build_preview(rows_of(base_row(metric_name="nope_metric")), conn)
    assert len(preview.rejected) == 1
    assert preview.rejected[0].codes[0] == "E_UNKNOWN_METRIC"


def test_bad_date_is_rejected(conn):
    preview = build_preview(rows_of(base_row(record_date="10/10")), conn)
    assert preview.rejected[0].codes[0] == "E_BAD_DATE"


def test_duplicate_within_file_is_skipped(conn):
    preview = build_preview(rows_of(base_row(), base_row()), conn)
    assert len(preview.accepted) == 1
    assert len(preview.duplicates) == 1
    assert preview.duplicates[0].duplicate_of == "row:1"


def test_duplicate_against_database_is_skipped(conn):
    insert_record(conn, make_record(record_date="2026-10-10", value=400.0), status="confirmed")
    preview = build_preview(rows_of(base_row()), conn)
    assert len(preview.duplicates) == 1
    assert preview.duplicates[0].duplicate_of


def test_conflict_against_confirmed_record(conn):
    insert_record(conn, make_record(record_date="2026-10-10", value=402.0), status="confirmed")
    preview = build_preview(rows_of(base_row(value=410)), conn)
    assert len(preview.conflicts) == 1
    assert preview.conflicts[0].conflict_db_ids
    assert preview.accepted == [] and preview.duplicates == []


def test_conflict_within_file(conn):
    preview = build_preview(rows_of(base_row(value=400), base_row(value=410)), conn)
    assert len(preview.accepted) == 1
    assert len(preview.conflicts) == 1
    assert preview.conflicts[0].conflict_row_indexes == (1,)


def test_rejected_record_does_not_block_new_value(conn):
    insert_record(conn, make_record(record_date="2026-10-10", value=402.0), status="rejected")
    preview = build_preview(rows_of(base_row(value=410)), conn)
    assert len(preview.accepted) == 1
    assert preview.conflicts == []


def test_buckets_always_add_up(conn):
    preview = build_preview(load_rows(SAMPLE_ISSUES), conn)
    assert preview.balanced
    assert sum(preview.counts()[k] for k in ("accepted", "review", "duplicates", "conflicts", "rejected")) == 10


# ---------------- 落库 ----------------

def test_commit_writes_pending_only(conn):
    preview = build_preview(load_rows(SAMPLE_JSON), conn)
    result = commit_import(conn, preview, file_name="sample.json", source_kind="json")

    assert result.counts["accepted_count"] == 21
    assert count_records(conn, status="pending") == 21
    assert count_records(conn, status="confirmed") == 0


def test_commit_records_batch_counts(conn):
    preview = build_preview(load_rows(SAMPLE_ISSUES), conn)
    result = commit_import(conn, preview, file_name="issues.csv", source_kind="csv")
    batch = get_batch(conn, result.batch_id)

    assert batch["row_count"] == 10
    assert batch["status"] == "active"
    assert batch["pending_count"] == batch["accepted_count"]
    assert batch["duplicate_count"] == 0
    assert batch["accepted_count"] + batch["rejected_count"] == 10


def test_commit_does_not_insert_duplicates(conn):
    insert_record(conn, make_record(record_date="2026-10-10", value=400.0), status="confirmed")
    preview = build_preview(rows_of(base_row()), conn)
    commit_import(conn, preview)
    assert count_records(conn) == 1


def test_commit_registers_conflict_without_overwriting(conn):
    existing = insert_record(conn, make_record(record_date="2026-10-10", value=402.0), status="confirmed")
    preview = build_preview(rows_of(base_row(value=410)), conn)
    result = commit_import(conn, preview)

    conflicts = list_conflicts(conn, "pending")
    assert len(conflicts) == 1
    assert conflicts[0]["existing_record_id"] == existing
    assert conflicts[0]["incoming_record_id"] in result.record_ids
    # 旧记录仍然是 confirmed，新记录是 pending，两者并存
    assert count_records(conn, status="confirmed") == 1
    assert count_records(conn, status="pending") == 1


def test_second_import_is_all_duplicates(conn):
    first = build_preview(load_rows(SAMPLE_JSON), conn)
    commit_import(conn, first)
    second = build_preview(load_rows(SAMPLE_JSON), conn)
    assert len(second.duplicates) == 21
    assert second.to_insert == []


# ---------------- 命令行 ----------------

def test_cli_import_dry_run_does_not_write(tmp_path, capsys):
    db = tmp_path / "cli.db"
    code = main(["import", str(SAMPLE_JSON), "--db", str(db)])
    output = capsys.readouterr().out
    assert code == 0
    assert "未写库" in output

    from health_agent.storage.db import init_db

    conn = init_db(db)
    assert count_records(conn) == 0
    conn.close()


def test_cli_import_commit_writes_pending(tmp_path, capsys):
    db = tmp_path / "cli2.db"
    code = main(["import", str(SAMPLE_JSON), "--db", str(db), "--commit"])
    output = capsys.readouterr().out
    assert code == 0
    assert "全部为 pending" in output

    from health_agent.storage.db import init_db

    conn = init_db(db)
    assert count_records(conn, status="pending") == 21
    conn.close()


def test_cli_reports_unreadable_file(tmp_path, capsys):
    db = tmp_path / "cli3.db"
    code = main(["import", str(tmp_path / "missing.json"), "--db", str(db)])
    assert code == 2
    assert "读取失败" in capsys.readouterr().err
