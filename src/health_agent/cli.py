"""命令行入口。

    python -m health_agent.cli init
    python -m health_agent.cli import test_data/sample_daily_metrics.json
    python -m health_agent.cli import <file> --commit
    python -m health_agent.cli stats
    python -m health_agent.cli conflicts
    python -m health_agent.cli serve
    python -m health_agent.cli passwd
    python -m health_agent.cli backup
    python -m health_agent.cli restore <archive.zip> [--force]
    python -m health_agent.cli export --format csv --out records.csv

import 默认只预览、不写库；要写库必须显式加 --commit。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .backup import BackupError, create_backup, restore_backup
from .config import load_settings
from .ingestion.batch import commit_import
from .ingestion.loader import LoadError, file_sha256, load_rows
from .ingestion.preview import build_preview, render_text
from .storage.db import init_db
from .storage.repository import count_records, list_conflicts, seed_catalog

STATUSES = ("pending", "confirmed", "rejected", "superseded")


def _open_db(args: argparse.Namespace):
    settings = load_settings()
    db_path = Path(args.db) if args.db else settings.db_path
    conn = init_db(db_path)
    seed_catalog(conn)
    return conn, db_path


def cmd_init(args: argparse.Namespace) -> int:
    conn, db_path = _open_db(args)
    metrics = conn.execute("SELECT COUNT(*) AS n FROM metrics_catalog").fetchone()["n"]
    print(f"数据库已就绪: {db_path}")
    print(f"指标注册表: {metrics} 条")
    conn.close()
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    conn, db_path = _open_db(args)
    try:
        rows = load_rows(args.file)
    except LoadError as exc:
        print(f"读取失败: {exc}", file=sys.stderr)
        conn.close()
        return 2

    if not rows:
        print("文件里没有记录。")
        conn.close()
        return 0

    preview = build_preview(rows, conn)
    print(f"文件: {args.file}")
    print(f"数据库: {db_path}\n")
    print(render_text(preview))

    if not args.commit:
        print("\n以上是预览，未写库。确认无误后加 --commit 写入。")
        conn.close()
        return 0

    result = commit_import(
        conn,
        preview,
        file_name=Path(args.file).name,
        file_sha256=file_sha256(args.file),
        source_kind=Path(args.file).suffix.lstrip(".").lower() or "unknown",
    )
    print("\n" + result.summary())
    print("注意：写入的记录全部为 pending，确认后才会进入趋势统计。")
    conn.close()
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    settings = load_settings()
    db_path = Path(args.db) if args.db else settings.db_path
    try:
        archive = create_backup(db_path, backup_dir=args.out)
    except BackupError as exc:
        print(f"备份失败: {exc}", file=sys.stderr)
        return 1
    print(f"已备份: {archive}")
    print("备份包内含数据库与 manifest.json（含 sha256 与记录数），恢复前会自动校验。")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    settings = load_settings()
    db_path = Path(args.db) if args.db else settings.db_path
    try:
        manifest = restore_backup(args.archive, db_path, force=args.force)
    except BackupError as exc:
        print(f"恢复失败: {exc}", file=sys.stderr)
        return 1
    print(f"已恢复: {db_path}")
    print(f"备份时间: {manifest.get('created_at')}  记录数: {manifest.get('restored_total_records')}")
    if manifest.get("backup_of_existing"):
        print(f"原库已另存: {manifest['backup_of_existing']}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from .storage.exporting import export_csv, export_json

    conn, _ = _open_db(args)
    try:
        body = export_json(conn) if args.format == "json" else export_csv(conn)
    finally:
        conn.close()

    if args.out:
        Path(args.out).write_text(body, encoding="utf-8-sig" if args.format != "json" else "utf-8")
        print(f"已导出: {args.out}")
    else:
        print(body)
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    conn, db_path = _open_db(args)
    print(f"数据库: {db_path}")
    total = count_records(conn)
    print(f"记录总数: {total}")
    for status in STATUSES:
        print(f"  {status:<10} {count_records(conn, status=status)}")

    rows = conn.execute(
        """
        SELECT metric_name, verification_status, COUNT(*) AS n
        FROM records GROUP BY metric_name, verification_status
        ORDER BY metric_name, verification_status
        """
    ).fetchall()
    if rows:
        print("\n按指标统计:")
        for row in rows:
            print(f"  {row['metric_name']:<30} {row['verification_status']:<10} {row['n']}")
    conn.close()
    return 0


def cmd_conflicts(args: argparse.Namespace) -> int:
    conn, db_path = _open_db(args)
    rows = list_conflicts(conn, "pending")
    print(f"数据库: {db_path}")
    if not rows:
        print("没有待裁决的冲突。")
        conn.close()
        return 0
    print(f"待裁决冲突: {len(rows)} 条")
    for row in rows:
        print(
            f"  {row['metric_name']:<28} {row['record_date']}  "
            f"旧={row['existing_record_id'] and row['existing_record_id'][:8]}  "
            f"新={row['incoming_record_id'][:8]}"
        )
    conn.close()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .ui.app import create_app

    settings = load_settings()
    db_path = Path(args.db) if args.db else settings.db_path
    app = create_app(settings, db_path=db_path)
    port = args.port or settings.port
    print(f"打开 http://{settings.host}:{port}  （Ctrl+C 停止）")
    print(f"数据库: {db_path}")
    uvicorn.run(app, host=settings.host, port=port, log_level="warning")
    return 0


def cmd_passwd(args: argparse.Namespace) -> int:
    import getpass

    from .ui.auth import hash_password

    first = getpass.getpass("设置访问密码: ")
    if first != getpass.getpass("再输一次: "):
        print("两次输入不一致", file=sys.stderr)
        return 1
    if len(first) < 6:
        print("密码至少 6 位", file=sys.stderr)
        return 1
    print("\n把下面这行写入 .env（同一行，不要换行）：")
    print(f"HEALTH_AGENT_PASSWORD_HASH={hash_password(first)}")
    print("\n开启局域网访问时还需设置 HEALTH_AGENT_LAN=1")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="health-agent", description="个人健康数据管理（本地）")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, handler, help_text in (
        ("init", cmd_init, "初始化数据库并同步指标注册表"),
        ("import", cmd_import, "导入 JSON/CSV；默认只预览"),
        ("stats", cmd_stats, "查看记录统计"),
        ("conflicts", cmd_conflicts, "查看待裁决冲突"),
        ("serve", cmd_serve, "启动本地界面"),
        ("passwd", cmd_passwd, "生成访问密码的 hash"),
        ("backup", cmd_backup, "备份数据库到 zip"),
        ("restore", cmd_restore, "从 zip 恢复数据库"),
        ("export", cmd_export, "导出全部记录"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--db", help="覆盖数据库路径（默认取 HEALTH_AGENT_DB_PATH）")
        sub.set_defaults(handler=handler)
        if name == "serve":
            sub.add_argument("--port", type=int, default=None, help="覆盖端口")
        if name == "backup":
            sub.add_argument("--out", help="备份目录（默认 data/backup）")
        if name == "restore":
            sub.add_argument("archive", help="要恢复的 zip")
            sub.add_argument("--force", action="store_true", help="覆盖已存在的数据库")
        if name == "export":
            sub.add_argument("--format", choices=["csv", "json"], default="csv")
            sub.add_argument("--out", help="输出文件；不填则打印到终端")
        if name == "import":
            sub.add_argument("file", help="要导入的 .json / .csv / .tsv 文件")
            sub.add_argument(
                "--commit",
                action="store_true",
                help="确认写入（不加则只预览）。写入的记录全部为 pending。",
            )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
