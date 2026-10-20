"""导入预览：把文件里的每一行判成 5 种结局之一，供用户在写库前查看。

结局（互斥，总数必须等于文件行数）：
  accepted    校验通过且无标记 → 可直接确认
  review      校验通过但有标记（不可读 / 超范围 / 单位靠推断 / 由文本解析）→ 需人工看一眼
  duplicates  完全重复（同键同值）→ 跳过，不写库
  conflicts   同槽位不同值 → 写库为 pending 并登记冲突，等人工裁决
  rejected    结构性不合格（未知指标 / 坏日期 / 非白名单单位 / 周期不全）→ 不入库

预览阶段**不写任何数据**：传 conn=None 也能跑，用于不带数据库的 dry-run。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field, replace
from typing import Any

from ..errors import F_MISSING_VALUE, F_OUT_OF_RANGE, F_REVIEW_REQUIRED, F_UNIT_INFERRED, F_UNREADABLE, F_VALUE_PARSED_FROM_TEXT
from ..storage.keys import dedup_key as _dedup_key
from ..storage.keys import logical_key as _logical_key
from ..storage.repository import find_by_dedup_key, find_by_logical_key
from ..validation.result import NormalizedRecord
from ..validation.rules import validate_row
from .loader import RawRow

#: 需要人工复核的标记。unit_converted 不算：换算是确定性的，且原始值已保留。
REVIEW_FLAGS: frozenset[str] = frozenset(
    {
        F_UNREADABLE,
        F_OUT_OF_RANGE,
        F_UNIT_INFERRED,
        F_VALUE_PARSED_FROM_TEXT,
        F_MISSING_VALUE,
        F_REVIEW_REQUIRED,
    }
)

#: 参与冲突判定的既有记录状态。rejected / superseded 不应挡住新数据。
_ACTIVE_STATUSES: frozenset[str] = frozenset({"pending", "confirmed"})


@dataclass(frozen=True, slots=True)
class RejectedItem:
    row_index: int
    metric_name: str | None
    codes: tuple[str, ...]
    message: str
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PreviewItem:
    row_index: int
    record: NormalizedRecord
    flags: tuple[str, ...]
    logical_key: str
    dedup_key: str
    duplicate_of: str | None = None
    conflict_db_ids: tuple[str, ...] = ()
    conflict_row_indexes: tuple[int, ...] = ()

    @property
    def label(self) -> str:
        return f"#{self.row_index} {self.record.metric_name} {self.record.record_date}"


@dataclass
class ImportPreview:
    total: int = 0
    accepted: list[PreviewItem] = field(default_factory=list)
    review: list[PreviewItem] = field(default_factory=list)
    duplicates: list[PreviewItem] = field(default_factory=list)
    conflicts: list[PreviewItem] = field(default_factory=list)
    rejected: list[RejectedItem] = field(default_factory=list)

    @property
    def to_insert(self) -> list[PreviewItem]:
        """确认后会写库的记录：干净 + 待复核 + 冲突（全部 pending）。"""
        return [*self.accepted, *self.review, *self.conflicts]

    def counts(self) -> dict[str, int]:
        return {
            "total": self.total,
            "accepted": len(self.accepted),
            "review": len(self.review),
            "duplicates": len(self.duplicates),
            "conflicts": len(self.conflicts),
            "rejected": len(self.rejected),
        }

    @property
    def balanced(self) -> bool:
        """五个桶加起来必须等于总行数——防止某行被静默吞掉。"""
        return sum(
            len(bucket)
            for bucket in (self.accepted, self.review, self.duplicates, self.conflicts, self.rejected)
        ) == self.total

    def summary(self) -> str:
        c = self.counts()
        return (
            f"共 {c['total']} 行：可直接确认 {c['accepted']}，待复核 {c['review']}，"
            f"重复跳过 {c['duplicates']}，冲突 {c['conflicts']}，拒绝 {c['rejected']}"
        )


def _active(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    return [r for r in rows if r["verification_status"] in _ACTIVE_STATUSES]


def _format_item(item: PreviewItem, note: str = "") -> str:
    record = item.record
    value = "NULL" if record.value is None else f"{record.value:g}"
    suffix = f"  <- {note}" if note else ""
    return f"  #{item.row_index:<3} {record.metric_name:<28} {record.record_date}  {value} {record.unit}{suffix}"


def render_text(preview: ImportPreview, *, limit: int = 6) -> str:
    """把预览渲染成可读文本。超过 limit 的桶只显示前几条并注明剩余数量。"""
    c = preview.counts()
    lines = [
        f"共 {c['total']} 行 | 可直接确认 {c['accepted']} | 待复核 {c['review']} | "
        f"重复跳过 {c['duplicates']} | 冲突 {c['conflicts']} | 拒绝 {c['rejected']}"
    ]

    if preview.conflicts:
        lines.append("\n[冲突] 同日期同指标已有不同值，需人工裁决（不会自动覆盖）")
        lines.extend(
            _format_item(i, "与已有记录冲突 " + ",".join(i.conflict_db_ids)[:8] if i.conflict_db_ids else "文件内自相矛盾")
            for i in preview.conflicts[:limit]
        )
        if len(preview.conflicts) > limit:
            lines.append(f"  …… 另有 {len(preview.conflicts) - limit} 条")

    if preview.review:
        lines.append("\n[待复核] 结构合法但带标记，确认前请看一眼")
        lines.extend(_format_item(i, ",".join(i.flags)) for i in preview.review[:limit])
        if len(preview.review) > limit:
            lines.append(f"  …… 另有 {len(preview.review) - limit} 条")

    if preview.rejected:
        lines.append("\n[拒绝] 这些行不会入库")
        lines.extend(
            f"  #{i.row_index:<3} {i.metric_name or '?':<28} {i.codes[0]:<22} {i.message}"
            for i in preview.rejected[:limit]
        )
        if len(preview.rejected) > limit:
            lines.append(f"  …… 另有 {len(preview.rejected) - limit} 条")

    if preview.duplicates:
        lines.append("\n[重复跳过] 已存在完全相同的值")
        lines.extend(_format_item(i, "同 " + i.duplicate_of) for i in preview.duplicates[:limit])
        if len(preview.duplicates) > limit:
            lines.append(f"  …… 另有 {len(preview.duplicates) - limit} 条")

    if preview.accepted:
        lines.append("\n[可直接确认]")
        lines.extend(_format_item(i) for i in preview.accepted[:limit])
        if len(preview.accepted) > limit:
            lines.append(f"  …… 另有 {len(preview.accepted) - limit} 条")

    return "\n".join(lines)


def build_preview(rows: list[RawRow], conn: sqlite3.Connection | None = None) -> ImportPreview:
    """逐行判定归属。传 conn=None 时跳过"与库里比对"，用于无库的 dry-run。"""
    preview = ImportPreview(total=len(rows))
    seen_dedup: dict[str, str] = {}
    seen_logical: dict[str, tuple[str, int]] = {}

    for row in rows:
        result = validate_row(row.data)
        if not result.accepted:
            first = result.rejections[0]
            preview.rejected.append(
                RejectedItem(
                    row_index=row.index,
                    metric_name=None if row.data.get("metric_name") is None else str(row.data["metric_name"]),
                    codes=tuple(i.code for i in result.rejections),
                    message=first.message,
                    raw=dict(row.data),
                )
            )
            continue

        record = result.record
        logical = _logical_key(
            source_app=record.source_app,
            record_date=record.record_date,
            metric_name=record.metric_name,
            period_type=record.period_type,
            period_start=record.period_start,
            period_end=record.period_end,
        )
        dedup = _dedup_key(logical, unit=record.unit, value=record.value)

        item = PreviewItem(
            row_index=row.index,
            record=record,
            flags=result.flags,
            logical_key=logical,
            dedup_key=dedup,
        )

        # 1) 文件内完全重复
        if dedup in seen_dedup:
            preview.duplicates.append(replace(item, duplicate_of=seen_dedup[dedup]))
            continue
        # 2) 库里已存在完全相同的值
        if conn is not None:
            same_value = _active(find_by_dedup_key(conn, dedup))
            if same_value:
                preview.duplicates.append(replace(item, duplicate_of=same_value[0]["record_id"]))
                seen_dedup[dedup] = same_value[0]["record_id"]
                continue

        # 3) 冲突：同槽位已有不同值
        db_ids: list[str] = []
        if conn is not None:
            db_ids = [r["record_id"] for r in _active(find_by_logical_key(conn, logical))]
        row_ids: list[int] = []
        previous = seen_logical.get(logical)
        if previous is not None and previous[0] != dedup:
            row_ids.append(previous[1])

        seen_dedup[dedup] = f"row:{row.index}"
        seen_logical.setdefault(logical, (dedup, row.index))

        if db_ids or row_ids:
            preview.conflicts.append(
                replace(item, conflict_db_ids=tuple(db_ids), conflict_row_indexes=tuple(row_ids))
            )
            seen_logical[logical] = (dedup, row.index)
            continue

        # 4) 有标记的进待复核，其余进可直接确认
        if REVIEW_FLAGS & set(result.flags):
            preview.review.append(item)
        else:
            preview.accepted.append(item)

    return preview


