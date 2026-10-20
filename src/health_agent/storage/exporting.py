"""导出与删除：用户对自己的数据有完全的取走权与删除权（规格 §8）。"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from typing import Any

from .repository import add_audit, get_record, now_iso

EXPORT_COLUMNS: tuple[str, ...] = (
    "record_id",
    "record_date",
    "metric_name",
    "value",
    "unit",
    "raw_value",
    "raw_unit",
    "period_type",
    "period_start",
    "period_end",
    "source_app",
    "source_page",
    "extraction_method",
    "verification_status",
    "quality_flags",
    "batch_id",
    "notes",
    "captured_at",
    "created_at",
)


def _fetch(conn: sqlite3.Connection, status: str | None) -> list[sqlite3.Row]:
    sql = f"SELECT {', '.join(EXPORT_COLUMNS)} FROM records"
    params: list[Any] = []
    if status:
        sql += " WHERE verification_status = ?"
        params.append(status)
    sql += " ORDER BY record_date, metric_name"
    return conn.execute(sql, params).fetchall()


def export_csv(conn: sqlite3.Connection, *, status: str | None = None) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(EXPORT_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for row in _fetch(conn, status):
        writer.writerow({column: row[column] for column in EXPORT_COLUMNS})
    return buffer.getvalue()


def export_json(conn: sqlite3.Connection, *, status: str | None = None) -> str:
    records = [{column: row[column] for column in EXPORT_COLUMNS} for row in _fetch(conn, status)]
    return json.dumps(
        {"schema_version": "1.0", "exported_at": now_iso(), "records": records},
        ensure_ascii=False,
        indent=2,
    )


def delete_record(conn: sqlite3.Connection, record_id: str, *, actor: str = "local_user") -> None:
    """物理删除一条记录。审计里保留"删过什么"，但数据本身不再存在。"""
    row = get_record(conn, record_id)
    if row is None:
        raise KeyError(record_id)
    before = {
        "record_id": row["record_id"],
        "metric_name": row["metric_name"],
        "record_date": row["record_date"],
        "value": row["value"],
        "status": row["verification_status"],
    }
    conn.execute("DELETE FROM records WHERE record_id = ?", (record_id,))
    add_audit(conn, "records", record_id, "delete", before=before, actor=actor)
    conn.commit()
