"""备份与恢复。

三个刻意的设计：
1. 用 SQLite 自己的 backup API 做快照，不直接复制文件——WAL 模式下复制文件可能拿到半个事务。
2. 备份里带 manifest（sha256 + 记录数 + schema 版本），恢复前先校验，不盲信压缩包。
3. 恢复默认拒绝覆盖已有数据库，必须显式 force，且覆盖前先留一个 .bak。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from . import __version__
from .storage.db import apply_migrations, connect, current_version
from .storage.repository import count_records

ARCHIVE_MEMBER = "health.db"
MANIFEST_MEMBER = "manifest.json"


class BackupError(Exception):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot(db_path: Path, target: Path) -> None:
    """用 SQLite backup API 生成一致性快照。"""
    source = sqlite3.connect(db_path)
    try:
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()


def create_backup(
    db_path: str | Path,
    *,
    backup_dir: str | Path | None = None,
    now: datetime | None = None,
) -> Path:
    db_path = Path(db_path)
    if not db_path.is_file():
        raise BackupError(f"数据库不存在: {db_path}")

    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target_dir = Path(backup_dir) if backup_dir else db_path.parent / "backup"
    target_dir.mkdir(parents=True, exist_ok=True)
    archive = target_dir / f"health-{stamp}.zip"

    snapshot = target_dir / f".snapshot-{stamp}.db"
    _snapshot(db_path, snapshot)

    try:
        conn = connect(snapshot)
        try:
            counts = {
                status: count_records(conn, status=status)
                for status in ("pending", "confirmed", "rejected", "superseded")
            }
            version = current_version(conn)
        finally:
            conn.close()

        manifest = {
            "created_at": (now or datetime.now()).isoformat(timespec="seconds"),
            "app_version": __version__,
            "schema_version": version,
            "db_sha256": _sha256_file(snapshot),
            "db_bytes": snapshot.stat().st_size,
            "counts": counts,
            "total_records": sum(counts.values()),
        }

        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(snapshot, arcname=ARCHIVE_MEMBER)
            bundle.writestr(
                MANIFEST_MEMBER, json.dumps(manifest, ensure_ascii=False, indent=2)
            )
    finally:
        snapshot.unlink(missing_ok=True)

    return archive


def inspect_backup(archive: str | Path) -> dict:
    """读 manifest 并校验数据库指纹。不写任何东西。"""
    archive = Path(archive)
    if not archive.is_file():
        raise BackupError(f"备份文件不存在: {archive}")

    try:
        with zipfile.ZipFile(archive) as bundle:
            names = set(bundle.namelist())
            if ARCHIVE_MEMBER not in names:
                raise BackupError("备份包里没有 health.db")
            if MANIFEST_MEMBER not in names:
                raise BackupError("备份包里没有 manifest.json，无法校验完整性")
            manifest = json.loads(bundle.read(MANIFEST_MEMBER).decode("utf-8"))
            raw = bundle.read(ARCHIVE_MEMBER)
    except zipfile.BadZipFile as exc:
        raise BackupError("备份文件损坏，不是有效的 zip") from exc

    digest = hashlib.sha256(raw).hexdigest()
    if manifest.get("db_sha256") != digest:
        raise BackupError("备份内容与 manifest 的 sha256 不一致，拒绝恢复")
    manifest["verified"] = True
    return manifest


def _discard(path: Path) -> None:
    """删掉半成品：临时库以及它自己的 WAL/SHM。

    清理失败不能盖住真正的错误（沙箱/回收站不可用时 unlink 会抛 OSError）。
    """
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


#: SQLite 文件头。备份内容连这个都不匹配，就不该落盘。
SQLITE_MAGIC = b"SQLite format 3\x00"


def _validate_payload(payload: bytes) -> None:
    """落盘前做最低限度的结构校验：不是 SQLite 文件就直接拒。

    深度校验（integrity_check）在临时文件上做——备份可能是 WAL 模式的镜像，
    内存反序列化对它不适用。
    """
    if len(payload) < 512 or not payload.startswith(SQLITE_MAGIC):
        raise BackupError("备份内容不是 SQLite 数据库文件，拒绝用它覆盖现役库")


def restore_backup(
    archive: str | Path,
    db_path: str | Path,
    *,
    force: bool = False,
    now: datetime | None = None,
) -> dict:
    """把备份恢复成现役库。

    恢复必须是**要么成功、要么什么都没变**：
    先把备份解到临时文件并校验，通过了才替换；替换前清掉旧库的 WAL/SHM
    （否则 SQLite 会把旧库未 checkpoint 的事务回放进新库，恢复结果被污染）。
    """
    archive = Path(archive)
    db_path = Path(db_path)
    manifest = inspect_backup(archive)

    if db_path.exists():
        if not force:
            raise BackupError(
                f"目标数据库已存在: {db_path}。恢复会覆盖它，请确认后使用 force"
            )
        stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        safety = db_path.with_name(f"{db_path.name}.bak-{stamp}")
        shutil.copy2(db_path, safety)
        manifest["backup_of_existing"] = str(safety)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    staged = db_path.with_name(f".restore-{stamp}-{uuid.uuid4().hex[:8]}.db")

    try:
        with zipfile.ZipFile(archive) as bundle:
            payload = bundle.read(ARCHIVE_MEMBER)
        _validate_payload(payload)  # 坏包在这里就被拒，磁盘上的现役库一点没动
        staged.write_bytes(payload)

        conn = connect(staged)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise BackupError(f"备份数据库完整性检查失败: {integrity}")
            tables = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
            ).fetchone()[0]
            if tables == 0:
                raise BackupError("备份里没有任何表，拒绝用它覆盖现役库")
            apply_migrations(conn)  # 旧备份可能缺少新迁移
            # 把 WAL 折回主文件：保证后面搬移的是完整单文件，不留旁文件
            conn.execute("PRAGMA journal_mode=DELETE")
            manifest["restored_total_records"] = count_records(conn)
        finally:
            conn.close()
    except BackupError:
        _discard(staged)
        raise
    except sqlite3.DatabaseError as exc:
        _discard(staged)
        raise BackupError(f"恢复后的数据库校验失败：{exc}") from exc
    except Exception:
        _discard(staged)
        raise

    # 旧库的 -wal/-shm 属于"被替换掉的那个库"。留着会被 SQLite 当成自己的日志回放，
    # 把备份内容覆盖成新旧混合的状态。
    for suffix in ("-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)

    try:
        os.replace(staged, db_path)  # 原子替换：目标要么是旧库，要么是新库
    except OSError as exc:
        _discard(staged)
        raise BackupError(
            f"替换数据库失败（可能有进程正在使用它，请先停止服务再恢复）：{exc}"
        ) from exc

    return manifest
