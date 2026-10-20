"""把预览结果落库。

两条铁律：
1. 落库的记录**全部是 pending**——确认动作永远是人的决定，导入本身不产生 confirmed。
2. 冲突不裁决，只登记；新值以 pending 入库，等人在 /conflicts 页面或命令行选。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..storage.repository import (
    add_audit,
    create_batch,
    create_conflict,
    insert_record,
    update_batch_counts,
)
from .preview import ImportPreview


@dataclass(frozen=True, slots=True)
class ImportResult:
    batch_id: str
    counts: dict[str, int]
    record_ids: tuple[str, ...]
    conflict_ids: tuple[str, ...] = ()

    def summary(self) -> str:
        c = self.counts
        return (
            f"批次 {self.batch_id[:8]} 已写入：入库 {c['accepted_count']} 条（全部 pending），"
            f"冲突 {c['conflict_count']}，重复跳过 {c['duplicate_count']}，拒绝 {c['rejected_count']}"
        )


def commit_import(
    conn: sqlite3.Connection,
    preview: ImportPreview,
    *,
    file_name: str | None = None,
    file_sha256: str | None = None,
    source_kind: str = "json",
) -> ImportResult:
    if not preview.balanced:
        raise ValueError(
            "预览分桶总数与文件行数不一致，拒绝落库（说明有行被静默吞掉，属于 bug）"
        )

    batch_id = create_batch(
        conn,
        file_name=file_name,
        file_sha256=file_sha256,
        source_kind=source_kind,
        row_count=preview.total,
    )

    record_ids: dict[int, str] = {}
    for item in preview.to_insert:
        record_ids[item.row_index] = insert_record(
            conn,
            item.record,
            batch_id=batch_id,
            status="pending",
            commit=False,
        )

    conflict_ids: list[str] = []
    for item in preview.conflicts:
        incoming_id = record_ids[item.row_index]
        for existing_id in item.conflict_db_ids:
            conflict_ids.append(
                create_conflict(
                    conn,
                    logical_key=item.logical_key,
                    metric_name=item.record.metric_name,
                    record_date=item.record.record_date,
                    existing_record_id=existing_id,
                    incoming_record_id=incoming_id,
                )
            )
        for other_row in item.conflict_row_indexes:
            conflict_ids.append(
                create_conflict(
                    conn,
                    logical_key=item.logical_key,
                    metric_name=item.record.metric_name,
                    record_date=item.record.record_date,
                    existing_record_id=record_ids.get(other_row),
                    incoming_record_id=incoming_id,
                )
            )

    counts = {
        "accepted_count": len(preview.to_insert),
        "pending_count": len(preview.to_insert),
        "rejected_count": len(preview.rejected),
        "duplicate_count": len(preview.duplicates),
        "conflict_count": len(preview.conflicts),
    }
    update_batch_counts(conn, batch_id, counts)
    add_audit(
        conn,
        "import_batches",
        batch_id,
        "commit",
        after={"file_name": file_name, "row_count": preview.total, **counts},
    )
    conn.commit()

    return ImportResult(
        batch_id=batch_id,
        counts=counts,
        record_ids=tuple(record_ids.values()),
        conflict_ids=tuple(conflict_ids),
    )
