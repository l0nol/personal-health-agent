"""数据访问层。全部 SQL 集中在此文件，上层不写 SQL。

两个键的区别：
- logical_key：**业务唯一性**（同来源 + 同日期 + 同指标 + 同周期 + 同周期区间）
- dedup_key：**完全重复**（logical_key + 单位 + 数值）

同 logical_key 不同 dedup_key 就是"冲突"，必须人工裁决。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from ..catalog import METRICS
from ..validation.result import NormalizedRecord
from .keys import dedup_key as _dedup_key
from .keys import logical_key as _logical_key

VALID_STATUSES: frozenset[str] = frozenset({"pending", "confirmed", "rejected", "superseded"})


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def seed_catalog(conn: sqlite3.Connection) -> int:
    """把 catalog.py 的指标注册表同步进数据库。幂等。"""
    rows = [
        (
            m.name,
            m.display,
            m.unit,
            m.kind,
            m.min_valid,
            m.max_valid,
            ",".join(m.period_types),
            m.tier,
            int(m.in_report),
            int(m.review_required),
            m.description,
        )
        for m in METRICS
    ]
    conn.executemany(
        """
        INSERT INTO metrics_catalog
            (metric_name, display_name, unit, kind, min_valid, max_valid,
             period_types, tier, in_report, review_required, description)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(metric_name) DO UPDATE SET
            display_name    = excluded.display_name,
            unit            = excluded.unit,
            kind            = excluded.kind,
            min_valid       = excluded.min_valid,
            max_valid       = excluded.max_valid,
            period_types    = excluded.period_types,
            tier            = excluded.tier,
            in_report       = excluded.in_report,
            review_required = excluded.review_required,
            description     = excluded.description
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def add_audit(
    conn: sqlite3.Connection,
    entity: str,
    entity_id: str,
    action: str,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
    actor: str = "local_user",
) -> None:
    """写审计日志。不提交事务，由调用方决定。"""
    conn.execute(
        """
        INSERT INTO audit_log
            (audit_id, entity, entity_id, action, before_json, after_json, reason, actor, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex,
            entity,
            entity_id,
            action,
            None if before is None else json.dumps(before, ensure_ascii=False),
            None if after is None else json.dumps(after, ensure_ascii=False),
            reason,
            actor,
            now_iso(),
        ),
    )


def insert_record(
    conn: sqlite3.Connection,
    record: NormalizedRecord,
    *,
    batch_id: str | None = None,
    status: str = "pending",
    record_id: str | None = None,
    captured_at: str | None = None,
    commit: bool = True,
) -> str:
    """插入一条记录，默认 pending。返回 record_id。

    已确认记录的唯一性由数据库的部分唯一索引保证；这里不做"先查后写"的竞态式判重。
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"非法的记录状态: {status!r}")

    rid = record_id or uuid.uuid4().hex
    logical_key = _logical_key(
        source_app=record.source_app,
        record_date=record.record_date,
        metric_name=record.metric_name,
        period_type=record.period_type,
        period_start=record.period_start,
        period_end=record.period_end,
    )
    dedup_key = _dedup_key(logical_key, unit=record.unit, value=record.value)
    ts = now_iso()

    conn.execute(
        """
        INSERT INTO records (
            record_id, logical_key, dedup_key, record_date, metric_name, value, unit,
            raw_value, raw_unit, period_type, period_start, period_end,
            source_app, source_page, extraction_method, verification_status,
            batch_id, quality_flags, notes, captured_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rid,
            logical_key,
            dedup_key,
            record.record_date,
            record.metric_name,
            record.value,
            record.unit,
            record.raw_value,
            record.raw_unit,
            record.period_type,
            record.period_start,
            record.period_end,
            record.source_app,
            record.source_page,
            record.extraction_method,
            status,
            batch_id,
            json.dumps(list(record.flags), ensure_ascii=False),
            record.notes,
            captured_at,
            ts,
            ts,
        ),
    )
    add_audit(
        conn,
        "records",
        rid,
        "insert",
        after={
            "metric_name": record.metric_name,
            "record_date": record.record_date,
            "value": record.value,
            "unit": record.unit,
            "status": status,
            "flags": list(record.flags),
        },
        reason=f"batch={batch_id}" if batch_id else None,
    )
    if commit:
        conn.commit()
    return rid


def get_record(conn: sqlite3.Connection, record_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM records WHERE record_id = ?", (record_id,)).fetchone()


def find_by_dedup_key(conn: sqlite3.Connection, dedup_key: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM records WHERE dedup_key = ? ORDER BY created_at", (dedup_key,)
    ).fetchall()


def find_by_logical_key(conn: sqlite3.Connection, logical_key: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM records WHERE logical_key = ? ORDER BY created_at", (logical_key,)
    ).fetchall()


def count_records(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    metric_name: str | None = None,
) -> int:
    sql = "SELECT COUNT(*) AS n FROM records WHERE 1 = 1"
    params: list[Any] = []
    if status is not None:
        sql += " AND verification_status = ?"
        params.append(status)
    if metric_name is not None:
        sql += " AND metric_name = ?"
        params.append(metric_name)
    return int(conn.execute(sql, params).fetchone()["n"])


def list_records(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    metric_name: str | None = None,
    limit: int = 200,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM records WHERE 1 = 1"
    params: list[Any] = []
    if status is not None:
        sql += " AND verification_status = ?"
        params.append(status)
    if metric_name is not None:
        sql += " AND metric_name = ?"
        params.append(metric_name)
    sql += " ORDER BY record_date DESC, metric_name ASC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def count_audit(conn: sqlite3.Connection, entity: str | None = None) -> int:
    if entity is None:
        row = conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) AS n FROM audit_log WHERE entity = ?", (entity,)).fetchone()
    return int(row["n"])


_COUNT_COLUMNS = (
    "accepted_count",
    "pending_count",
    "rejected_count",
    "duplicate_count",
    "conflict_count",
)


def create_batch(
    conn: sqlite3.Connection,
    *,
    file_name: str | None = None,
    file_sha256: str | None = None,
    source_kind: str = "json",
    row_count: int = 0,
    counts: dict[str, int] | None = None,
    note: str | None = None,
) -> str:
    """创建一个导入批次。批次是撤销的最小单位。"""
    batch_id = uuid.uuid4().hex
    values = {name: int((counts or {}).get(name, 0)) for name in _COUNT_COLUMNS}
    conn.execute(
        """
        INSERT INTO import_batches (
            batch_id, imported_at, file_name, file_sha256, source_kind, row_count,
            accepted_count, pending_count, rejected_count, duplicate_count, conflict_count,
            status, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
        """,
        (
            batch_id,
            now_iso(),
            file_name,
            file_sha256,
            source_kind,
            row_count,
            values["accepted_count"],
            values["pending_count"],
            values["rejected_count"],
            values["duplicate_count"],
            values["conflict_count"],
            note,
        ),
    )
    return batch_id


def update_batch_counts(
    conn: sqlite3.Connection, batch_id: str, counts: dict[str, int]
) -> None:
    assignments = ", ".join(f"{name} = ?" for name in _COUNT_COLUMNS)
    params = [int(counts.get(name, 0)) for name in _COUNT_COLUMNS]
    conn.execute(
        f"UPDATE import_batches SET {assignments} WHERE batch_id = ?",  # noqa: S608 - 列名为固定常量
        [*params, batch_id],
    )


def get_batch(conn: sqlite3.Connection, batch_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM import_batches WHERE batch_id = ?", (batch_id,)).fetchone()


def create_conflict(
    conn: sqlite3.Connection,
    *,
    logical_key: str,
    metric_name: str,
    record_date: str,
    existing_record_id: str | None,
    incoming_record_id: str,
) -> str:
    """登记一条待裁决冲突。绝不在此处替用户选真值。"""
    conflict_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO conflicts (
            conflict_id, logical_key, metric_name, record_date,
            existing_record_id, incoming_record_id, detected_at, resolution
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (
            conflict_id,
            logical_key,
            metric_name,
            record_date,
            existing_record_id,
            incoming_record_id,
            now_iso(),
        ),
    )
    return conflict_id


def list_conflicts(
    conn: sqlite3.Connection, resolution: str | None = "pending", *, limit: int = 200
) -> list[sqlite3.Row]:
    if resolution is None:
        return conn.execute(
            "SELECT * FROM conflicts ORDER BY detected_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM conflicts WHERE resolution = ? ORDER BY detected_at DESC LIMIT ?",
        (resolution, limit),
    ).fetchall()
