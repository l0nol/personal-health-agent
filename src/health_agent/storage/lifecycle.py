"""记录的状态迁移：确认、驳回、修正、撤销批次、裁决冲突。

状态机（只允许这些迁移，其余一律报错）：

    pending ──确认──▶ confirmed ──修正──▶ superseded（旧版本保留）
      │                   │
      └──驳回──▶ rejected └──撤销批次──▶ superseded

    pending ──修正数值──▶ pending（原地改值，仍待确认）

每条迁移都写 audit_log，"谁在什么时候把什么改成了什么"永远可查。
"""

from __future__ import annotations

import json
import sqlite3

from ..errors import ConflictResolutionError, RecordStateError
from .keys import dedup_key as _dedup_key
from .repository import add_audit, get_record, insert_record, now_iso
from ..validation.result import NormalizedRecord

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"
STATUS_SUPERSEDED = "superseded"


def _require(conn: sqlite3.Connection, record_id: str) -> sqlite3.Row:
    row = get_record(conn, record_id)
    if row is None:
        raise RecordStateError(f"记录不存在: {record_id}")
    return row


def _snapshot(row: sqlite3.Row) -> dict[str, object]:
    return {
        "record_id": row["record_id"],
        "metric_name": row["metric_name"],
        "record_date": row["record_date"],
        "value": row["value"],
        "unit": row["unit"],
        "status": row["verification_status"],
        "flags": json.loads(row["quality_flags"] or "[]"),
    }


def confirm_record(
    conn: sqlite3.Connection,
    record_id: str,
    *,
    force: bool = False,
    actor: str = "local_user",
) -> None:
    """待确认 → 已确认。没有数值的记录不允许确认（没有东西可确认）。"""
    row = _require(conn, record_id)
    status = row["verification_status"]
    if status == STATUS_CONFIRMED:
        return
    if status != STATUS_PENDING:
        raise RecordStateError(f"当前状态 {status} 不能确认")

    if row["value"] is None and not force:
        raise RecordStateError("该记录没有数值（缺失或不可读），请先修正再确认，或使用 force")

    try:
        conn.execute(
            "UPDATE records SET verification_status = ?, updated_at = ? WHERE record_id = ?",
            (STATUS_CONFIRMED, now_iso(), record_id),
        )
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise RecordStateError(
            "同一来源、日期、指标与周期已存在已确认记录，请先在冲突页裁决，不能静默覆盖"
        ) from exc

    add_audit(conn, "records", record_id, "confirm", before=_snapshot(row), actor=actor)
    conn.commit()


def reject_record(
    conn: sqlite3.Connection,
    record_id: str,
    *,
    reason: str | None = None,
    actor: str = "local_user",
) -> None:
    row = _require(conn, record_id)
    if row["verification_status"] != STATUS_PENDING:
        raise RecordStateError(f"当前状态 {row['verification_status']} 不能驳回")
    conn.execute(
        "UPDATE records SET verification_status = ?, updated_at = ? WHERE record_id = ?",
        (STATUS_REJECTED, now_iso(), record_id),
    )
    add_audit(conn, "records", record_id, "reject", before=_snapshot(row), reason=reason, actor=actor)
    conn.commit()


def update_pending_value(
    conn: sqlite3.Connection,
    record_id: str,
    *,
    value: float | None,
    notes: str | None = None,
    reason: str | None = None,
    actor: str = "local_user",
) -> None:
    """修改待确认记录的数值。仍是 pending——改完还要人来确认。"""
    row = _require(conn, record_id)
    if row["verification_status"] != STATUS_PENDING:
        raise RecordStateError("只有待确认记录可以原地改值；已确认记录请用 revise_confirmed")

    before = _snapshot(row)
    flags = [f for f in json.loads(row["quality_flags"] or "[]") if f != "unreadable"]
    # dedup_key 由「槽位 + 单位 + 数值」构成，改值必须同步重算。
    # 否则：再导入被改掉的旧值会被误判成"完全重复"而静默跳过，
    # 再导入新值又会被误判成"冲突"——两种错都会让用户看不到真实情况。
    conn.execute(
        """
        UPDATE records
           SET value = ?, dedup_key = ?, quality_flags = ?, notes = COALESCE(?, notes), updated_at = ?
         WHERE record_id = ?
        """,
        (
            value,
            _dedup_key(row["logical_key"], unit=row["unit"], value=value),
            json.dumps(flags, ensure_ascii=False),
            notes,
            now_iso(),
            record_id,
        ),
    )
    add_audit(
        conn,
        "records",
        record_id,
        "update_value",
        before=before,
        after={**before, "value": value},
        reason=reason,
        actor=actor,
    )
    conn.commit()


def revise_confirmed(
    conn: sqlite3.Connection,
    record_id: str,
    *,
    value: float,
    reason: str,
    actor: str = "local_user",
) -> str:
    """修正已确认记录：旧版本转 superseded，新版本成为 confirmed。返回新 record_id。"""
    row = _require(conn, record_id)
    if row["verification_status"] != STATUS_CONFIRMED:
        raise RecordStateError("只有已确认记录走版本化修正；待确认记录请用 update_pending_value")

    before = _snapshot(row)
    try:
        conn.execute(
            "UPDATE records SET verification_status = ?, updated_at = ? WHERE record_id = ?",
            (STATUS_SUPERSEDED, now_iso(), record_id),
        )
        replacement = NormalizedRecord(
            metric_name=row["metric_name"],
            record_date=row["record_date"],
            period_type=row["period_type"],
            value=value,
            unit=row["unit"],
            raw_value=row["raw_value"],
            raw_unit=row["raw_unit"],
            period_start=row["period_start"],
            period_end=row["period_end"],
            source_app=row["source_app"],
            source_page=row["source_page"],
            extraction_method=row["extraction_method"],
            notes=row["notes"],
            flags=tuple(json.loads(row["quality_flags"] or "[]")),
        )
        new_id = insert_record(
            conn,
            replacement,
            status=STATUS_CONFIRMED,
            captured_at=row["captured_at"],  # 提取时间是来源证据，修正值不能把它抹掉
            commit=False,
        )
        conn.execute("UPDATE records SET superseded_by = ? WHERE record_id = ?", (new_id, record_id))
        add_audit(
            conn,
            "records",
            record_id,
            "revise",
            before=before,
            after={"superseded_by": new_id, "value": value},
            reason=reason,
            actor=actor,
        )
        conn.commit()
    except Exception:
        # 旧值已经转 superseded，新值没插进去 → 这条数据会从统计里凭空消失。
        # 修正必须是原子的：要么两条都在，要么一条都没动。
        conn.rollback()
        raise
    return new_id


def revoke_batch(conn: sqlite3.Connection, batch_id: str, *, actor: str = "local_user") -> dict[str, int]:
    """撤销一次导入。

    - pending 记录：物理删除（从未被确认，没有保留价值）
    - confirmed 记录：转 superseded（绝不删除已确认数据）
    """
    batch = conn.execute("SELECT * FROM import_batches WHERE batch_id = ?", (batch_id,)).fetchone()
    if batch is None:
        raise RecordStateError(f"批次不存在: {batch_id}")
    if batch["status"] == "revoked":
        raise RecordStateError("该批次已撤销")

    rows = conn.execute("SELECT * FROM records WHERE batch_id = ?", (batch_id,)).fetchall()
    pending_ids = [
        row["record_id"] for row in rows if row["verification_status"] == STATUS_PENDING
    ]
    deleted = 0
    superseded = 0
    try:
        # 先清掉指向"即将被物理删除的待确认记录"的冲突。
        # conflicts 的两个外键都指向 records，不先清就会被外键拦住，
        # 结果是整批撤销失败、批次卡在 active，界面只弹一句 FOREIGN KEY constraint failed。
        # 这些冲突的 incoming（或 existing）马上就不存在了，留着只会是永远悬空的行。
        if pending_ids:
            marks = ",".join("?" for _ in pending_ids)
            stale = conn.execute(
                "SELECT conflict_id FROM conflicts"
                f" WHERE incoming_record_id IN ({marks}) OR existing_record_id IN ({marks})",
                [*pending_ids, *pending_ids],
            ).fetchall()
            for (conflict_id,) in stale:
                conn.execute("DELETE FROM conflicts WHERE conflict_id = ?", (conflict_id,))
                add_audit(conn, "conflicts", conflict_id, "delete", reason="batch_revoked")

        for row in rows:
            status = row["verification_status"]
            if status == STATUS_PENDING:
                conn.execute("DELETE FROM records WHERE record_id = ?", (row["record_id"],))
                add_audit(conn, "records", row["record_id"], "delete", before=_snapshot(row), reason="batch_revoked")
                deleted += 1
            elif status == STATUS_CONFIRMED:
                conn.execute(
                    "UPDATE records SET verification_status = ?, updated_at = ? WHERE record_id = ?",
                    (STATUS_SUPERSEDED, now_iso(), row["record_id"]),
                )
                add_audit(conn, "records", row["record_id"], "supersede", before=_snapshot(row), reason="batch_revoked")
                superseded += 1

        conn.execute(
            "UPDATE import_batches SET status = 'revoked', revoked_at = ? WHERE batch_id = ?",
            (now_iso(), batch_id),
        )
        add_audit(
            conn,
            "import_batches",
            batch_id,
            "revoke",
            after={"deleted_pending": deleted, "superseded_confirmed": superseded},
            actor=actor,
        )
        conn.commit()
    except Exception:
        # 撤销是"全有或全无"：半删状态比撤销失败更难查
        conn.rollback()
        raise
    return {"deleted_pending": deleted, "superseded_confirmed": superseded}


def get_conflict(conn: sqlite3.Connection, conflict_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM conflicts WHERE conflict_id = ?", (conflict_id,)).fetchone()
    if row is None:
        raise ConflictResolutionError(f"冲突不存在: {conflict_id}")
    return row


def resolve_conflict(
    conn: sqlite3.Connection,
    conflict_id: str,
    resolution: str,
    *,
    note: str | None = None,
    actor: str = "local_user",
) -> None:
    """裁决冲突。只有两种结局有意义，程序不替用户发明第三种。

    - keep_existing：新值转 rejected，旧值不动
    - take_incoming：旧值转 superseded，新值转 confirmed
    """
    if resolution not in {"keep_existing", "take_incoming"}:
        raise ConflictResolutionError(
            f"不支持的裁决方式 {resolution!r}。同一槽位只能有一条已确认记录，"
            "因此不存在'两条都留下'的合法结果"
        )

    conflict = get_conflict(conn, conflict_id)
    if conflict["resolution"] != STATUS_PENDING:
        raise ConflictResolutionError("该冲突已裁决，不能重复处理")

    incoming_id = conflict["incoming_record_id"]
    existing_id = conflict["existing_record_id"]

    if resolution == "keep_existing":
        conn.execute(
            "UPDATE records SET verification_status = ?, updated_at = ? WHERE record_id = ?",
            (STATUS_REJECTED, now_iso(), incoming_id),
        )
        add_audit(conn, "records", incoming_id, "reject", reason="conflict_keep_existing")
    else:
        if existing_id:
            conn.execute(
                "UPDATE records SET verification_status = ?, updated_at = ? WHERE record_id = ?",
                (STATUS_SUPERSEDED, now_iso(), existing_id),
            )
            add_audit(conn, "records", existing_id, "supersede", reason="conflict_take_incoming")
        conn.execute(
            "UPDATE records SET verification_status = ?, updated_at = ? WHERE record_id = ?",
            (STATUS_CONFIRMED, now_iso(), incoming_id),
        )
        add_audit(conn, "records", incoming_id, "confirm", reason="conflict_take_incoming")

    conn.execute(
        "UPDATE conflicts SET resolution = ?, resolved_at = ?, resolution_note = ? WHERE conflict_id = ?",
        (resolution, now_iso(), note, conflict_id),
    )
    add_audit(
        conn,
        "conflicts",
        conflict_id,
        "resolve",
        after={"resolution": resolution, "note": note},
        actor=actor,
    )
    conn.commit()


def confirm_batch(conn: sqlite3.Connection, batch_id: str, *, actor: str = "local_user") -> dict[str, int]:
    """把某批次里所有可确认的 pending 记录一次性确认。

    没有数值的、以及会撞上已确认记录的，跳过并计数——不静默处理。
    """
    # 只取一列时按位置读，这样即使调用方传入的是原生连接（未设 Row 工厂）也不会炸
    rows = conn.execute(
        "SELECT record_id FROM records WHERE batch_id = ? AND verification_status = ?",
        (batch_id, STATUS_PENDING),
    ).fetchall()
    confirmed = 0
    skipped = 0
    for row in rows:
        try:
            confirm_record(conn, row[0], actor=actor)
            confirmed += 1
        except RecordStateError:
            skipped += 1
    add_audit(conn, "import_batches", batch_id, "confirm_batch", after={"confirmed": confirmed, "skipped": skipped})
    conn.commit()
    return {"confirmed": confirmed, "skipped": skipped}
