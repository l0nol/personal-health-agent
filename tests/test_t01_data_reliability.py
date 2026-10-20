"""T01 数据可靠性：为已确认缺陷建立复现测试。

每条测试对应 `reports/T01_DATA_RELIABILITY_REPORT.md` 里的一个缺陷编号：
  D1 改值后 dedup_key 不刷新 → 重复/冲突判定错位
  D2 版本化修正丢失 captured_at（提取时间）
  D3 load_points 接受 date 对象 → 依赖 Python 已弃用的默认日期适配器
  D4 含冲突的批次无法撤销（外键拦住删除，批次卡在 active）

以及若干"当前行为已经正确、不许退化"的守门测试（G1–G6）。
"""

from __future__ import annotations

import sqlite3
import warnings
from datetime import date

import pytest

from health_agent.analytics.aggregate import load_points, summarize
from health_agent.catalog import REPORT_METRICS
from health_agent.ingestion.batch import commit_import
from health_agent.ingestion.loader import RawRow
from health_agent.ingestion.preview import build_preview
from health_agent.reports.builder import build_daily
from health_agent.rules.engine import RuleConfig, describe_sleep_target, evaluate
from health_agent.storage.keys import dedup_key as compute_dedup_key
from health_agent.storage.lifecycle import (
    confirm_record,
    reject_record,
    revoke_batch,
    revise_confirmed,
    update_pending_value,
)
from health_agent.storage.repository import count_records, get_batch, get_record, insert_record

from conftest import make_record


def _raw_rows(values: list[float], *, day: str = "2026-10-01") -> list[RawRow]:
    return [
        RawRow(
            index=index + 1,
            data={
                "metric_name": "night_sleep_minutes",
                "record_date": day,
                "period_type": "day",
                "value": value,
                "unit": "min",
                "source_app": "huawei_health",
                "source_page": "睡眠-日视图",
                "extraction_method": "manual",
            },
        )
        for index, value in enumerate(values)
    ]


def _import_values(conn, values: list[float], *, day: str = "2026-10-01"):
    """把一串睡眠时长值走完整的 校验 → 预览 → 落库 链路。"""
    preview = build_preview(_raw_rows(values, day=day), conn)
    result = commit_import(conn, preview, file_name="t01.json")
    return preview, result


# --------------------------------------------------------------------------
# D1 改值后 dedup_key 必须跟着变
# --------------------------------------------------------------------------


def test_dedup_key_is_recomputed_when_pending_value_changes(conn):
    record_id = insert_record(conn, make_record(value=402.0))
    before = get_record(conn, record_id)

    update_pending_value(conn, record_id, value=380.0, reason="T01 复现")

    after = get_record(conn, record_id)
    assert after["value"] == 380.0
    assert after["logical_key"] == before["logical_key"]  # 槽位没变
    assert after["dedup_key"] == compute_dedup_key(
        after["logical_key"], unit=after["unit"], value=380.0
    ), "改值后 dedup_key 仍是旧值的键，重复/冲突判定会错位"


def test_reimport_after_value_change_classifies_correctly(conn):
    record_id = insert_record(conn, make_record(value=402.0))
    update_pending_value(conn, record_id, value=380.0, reason="T01 复现")
    confirm_record(conn, record_id)

    # 再导入已经被改掉的旧值：同槽位不同值，必须进冲突，不能被当成重复跳过
    preview_old = build_preview(_raw_rows([402.0]), conn)
    assert len(preview_old.conflicts) == 1, preview_old.summary()
    assert len(preview_old.duplicates) == 0

    # 再导入与库里当前值相同的记录：应判为完全重复
    preview_same = build_preview(_raw_rows([380.0]), conn)
    assert len(preview_same.duplicates) == 1, preview_same.summary()
    assert len(preview_same.conflicts) == 0


# --------------------------------------------------------------------------
# D2 版本化修正必须保留来源与提取时间
# --------------------------------------------------------------------------


def test_revise_confirmed_preserves_provenance(conn):
    record_id = insert_record(
        conn,
        make_record(value=402.0),
        captured_at="2026-10-01T08:30:00+08:00",
    )
    confirm_record(conn, record_id)

    new_id = revise_confirmed(conn, record_id, value=390.0, reason="T01 复现")

    old = get_record(conn, record_id)
    new = get_record(conn, new_id)
    assert old["verification_status"] == "superseded"
    assert old["superseded_by"] == new_id
    assert new["value"] == 390.0
    assert new["captured_at"] == "2026-10-01T08:30:00+08:00", "修正后丢了提取时间"
    assert new["source_page"] == old["source_page"]
    assert new["extraction_method"] == old["extraction_method"]


# --------------------------------------------------------------------------
# D3 统计取数不得依赖 Python 的默认日期适配器
# --------------------------------------------------------------------------


def test_load_points_accepts_date_objects_without_deprecation_warning(conn):
    record_id = insert_record(conn, make_record(record_date="2026-10-02", value=402.0))
    confirm_record(conn, record_id)

    # UI 的 /sleep 与 /trends 直接传 date 对象进来（ui/app.py 的 _series）。
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        points = load_points(conn, "night_sleep_minutes", date(2026, 10, 1), date(2026, 10, 3))

    assert [point.record_date for point in points] == ["2026-10-02"]


# --------------------------------------------------------------------------
# D4 含冲突的批次必须能撤销
# --------------------------------------------------------------------------


def test_revoke_batch_with_pending_conflict_succeeds(conn):
    _import_values(conn, [402.0])  # 第一批：正常入库
    _, second = _import_values(conn, [380.0])  # 第二批：同槽位不同值 → 登记冲突
    assert len(second.conflict_ids) == 1
    assert count_records(conn) == 2

    result = revoke_batch(conn, second.batch_id)

    assert result["deleted_pending"] == 1
    assert count_records(conn) == 1, "只应剩下第一批的记录"
    assert conn.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0] == 0, (
        "指向已删记录的冲突必须一并清掉，否则永远是死悬空"
    )
    assert get_batch(conn, second.batch_id)["status"] == "revoked"


def test_revoking_batch_keeps_conflicts_of_other_batches(conn):
    _import_values(conn, [402.0])
    _, second = _import_values(conn, [380.0])  # 冲突：existing=第一批, incoming=第二批
    _import_values(conn, [300.0], day="2026-10-02")  # 第三批，与冲突无关

    revoke_batch(conn, second.batch_id)

    # 冲突的 existing 仍在库里，但 incoming 已被撤销 → 这条冲突无法裁决，应清掉
    assert conn.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0] == 0
    assert count_records(conn) == 2


def test_audit_log_records_deleted_conflict(conn):
    _import_values(conn, [402.0])
    _, second = _import_values(conn, [380.0])
    revoke_batch(conn, second.batch_id)

    rows = conn.execute("SELECT action, reason FROM audit_log WHERE entity = 'conflicts'").fetchall()
    assert any(row["action"] == "delete" and row["reason"] == "batch_revoked" for row in rows)


def test_revise_confirmed_rolls_back_when_insert_fails(conn, monkeypatch):
    """修正中途失败时，旧值必须还是 confirmed——不能变成"没人认领"的 superseded。"""
    import health_agent.storage.lifecycle as lifecycle_module

    record_id = insert_record(conn, make_record(value=402.0))
    confirm_record(conn, record_id)

    def boom(*args, **kwargs):
        raise sqlite3.IntegrityError("forced failure")

    monkeypatch.setattr(lifecycle_module, "insert_record", boom)

    with pytest.raises(sqlite3.IntegrityError):
        revise_confirmed(conn, record_id, value=390.0, reason="T01 复现")

    row = get_record(conn, record_id)
    assert row["verification_status"] == "confirmed", "失败的修正不得留下半截状态"
    assert row["value"] == 402.0


# --------------------------------------------------------------------------
# G1–G6 守门：这些行为当前是对的，改代码不许弄坏
# --------------------------------------------------------------------------


def test_pending_records_never_enter_statistics(conn):
    record_id = insert_record(conn, make_record(value=402.0))  # 仍是 pending

    summary = summarize(conn, "night_sleep_minutes", date(2026, 10, 1), date(2026, 10, 7))

    assert summary.sample_days == 0
    assert summary.status == "no_data"
    assert summary.mean is None
    assert record_id not in summary.record_ids


def test_null_value_is_missing_not_zero(conn):
    # 直接落一条"已确认但没有数值"的记录：设备显示"--"，值就是 NULL
    insert_record(conn, make_record(value=None), status="confirmed")

    summary = summarize(conn, "night_sleep_minutes", date(2026, 10, 1), date(2026, 10, 7))

    assert summary.sample_days == 0, "NULL 不得被当成 0 参与统计"
    assert summary.missing_days == 7
    assert summary.mean is None


def test_range_bound_metrics_never_enter_reports():
    assert "hrv_min_ms" not in REPORT_METRICS
    assert "hrv_max_ms" not in REPORT_METRICS
    assert "hrv_avg_ms" in REPORT_METRICS, "设备给的均值是独立指标，不是从区间端点算出来的"


def test_report_numbers_stay_traceable(conn):
    record_id = insert_record(conn, make_record(value=402.0))
    confirm_record(conn, record_id)

    payload = build_daily(conn, date(2026, 10, 1))

    assert payload.all_traceable
    assert any(record_id in number.record_ids for number in payload.numbers)


def test_sleep_target_text_never_claims_personalization():
    default_text = describe_sleep_target(RuleConfig())
    assert "默认参考值" in default_text
    assert "未按年龄" in default_text

    user_text = describe_sleep_target(RuleConfig(sleep_target_user_supplied=True))
    assert "你设定的目标" in user_text


def test_rules_do_not_read_pending_data(conn):
    insert_record(conn, make_record(metric_name="resting_hr_bpm", value=58.0))  # pending

    candidates = evaluate(conn, RuleConfig(data_gap_metrics=("resting_hr_bpm",)), date(2026, 10, 1))

    assert candidates == []


def test_rejected_record_does_not_block_reimport(conn):
    """驳回一条记录后，同一个值再导入必须能正常进来，不能被判成重复。"""
    record_id = insert_record(conn, make_record(value=402.0))
    reject_record(conn, record_id)

    preview = build_preview(_raw_rows([402.0]), conn)

    assert len(preview.accepted) == 1, preview.summary()
    assert len(preview.duplicates) == 0


def test_superseded_record_does_not_block_reimport(conn):
    record_id = insert_record(conn, make_record(value=402.0))
    confirm_record(conn, record_id)
    revise_confirmed(conn, record_id, value=390.0, reason="T01 守门")

    preview = build_preview(_raw_rows([402.0]), conn)

    # 旧值已 superseded，不再是"活跃槽位"；导入旧值只会与 390 冲突，不算重复
    assert len(preview.duplicates) == 0
    assert len(preview.conflicts) == 1
