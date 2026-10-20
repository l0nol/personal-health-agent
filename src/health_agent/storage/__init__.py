"""存储层：SQLite 连接、迁移、记录与批次的唯一访问入口。"""

from .db import apply_migrations, connect, current_version, index_names, init_db, table_names
from .keys import dedup_key, logical_key
from .repository import (
    add_audit,
    count_audit,
    count_records,
    find_by_dedup_key,
    find_by_logical_key,
    get_record,
    insert_record,
    list_records,
    seed_catalog,
)

__all__ = [
    "add_audit",
    "apply_migrations",
    "connect",
    "count_audit",
    "count_records",
    "current_version",
    "dedup_key",
    "find_by_dedup_key",
    "find_by_logical_key",
    "get_record",
    "index_names",
    "init_db",
    "insert_record",
    "list_records",
    "logical_key",
    "seed_catalog",
    "table_names",
]
