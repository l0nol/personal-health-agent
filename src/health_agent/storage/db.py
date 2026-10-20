"""SQLite 连接与迁移。

所有 SQL 只出现在 storage 包内；上层模块一律通过 repository 访问数据。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).with_name("migrations")

BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    filename   TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
"""


def connect(db_path: str | Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """打开连接。不建表，只做连接级设置。

    check_same_thread=False 仅供"每请求一个连接、用完即关"的 Web 层使用：
    连接不会被多个线程共享，只是创建与使用可能落在不同线程。
    """
    path = Path(db_path)
    if path.parent and str(path.parent) not in {"", "."}:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def migration_files() -> list[tuple[int, Path]]:
    """按版本号排序的迁移文件列表。文件名形如 001_init.sql。"""
    files: list[tuple[int, Path]] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        prefix = path.name.split("_", 1)[0]
        try:
            version = int(prefix)
        except ValueError:
            continue
        files.append((version, path))
    return files


def current_version(conn: sqlite3.Connection) -> int:
    conn.executescript(BOOTSTRAP_SQL)
    row = conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations").fetchone()
    return int(row["v"])


def apply_migrations(conn: sqlite3.Connection) -> int:
    """应用未执行的迁移，返回最终版本号。

    迁移文件内的语句全部使用 IF NOT EXISTS，因此失败后重跑是安全的。
    """
    version = current_version(conn)
    for file_version, path in migration_files():
        if file_version <= version:
            continue
        sql = path.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute(
            "INSERT OR REPLACE INTO schema_migrations (version, filename, applied_at)"
            " VALUES (?, ?, datetime('now'))",
            (file_version, path.name),
        )
        conn.commit()
        version = file_version
    return version


def init_db(db_path: str | Path) -> sqlite3.Connection:
    """连接 + 建表。幂等，可重复调用。"""
    conn = connect(db_path)
    apply_migrations(conn)
    return conn


def table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        " ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]


def index_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        " ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]
