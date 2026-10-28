"""命令行入口。

    python -m health_agent.cli init
    python -m health_agent.cli import test_data/sample_daily_metrics.json
    python -m health_agent.cli import <file> --commit
    python -m health_agent.cli stats
    python -m health_agent.cli conflicts
    python -m health_agent.cli confirm --list
    python -m health_agent.cli confirm --batch 262bc873 --yes
    python -m health_agent.cli serve
    python -m health_agent.cli passwd
    python -m health_agent.cli backup
    python -m health_agent.cli restore <archive.zip> [--force]
    python -m health_agent.cli export --format csv --out records.csv

import 默认只预览、不写库；要写库必须显式加 --commit。
confirm 默认也只预览，加 --yes 才真的确认；确认范围永远是"一个批次"，不是所有 pending。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from .backup import BackupError, create_backup, restore_backup
from .config import load_settings
from .guidance.engine import build_guidance, config_from_settings
from .guidance.explain import build_digest, explain
from .guidance.render import render_json, render_text
from .ingestion.batch import commit_import
from .ingestion.loader import LoadError, file_sha256, load_rows
from .ingestion.preview import build_preview, render_text as render_preview_text
from .storage import training as training_store
from .storage.db import init_db
from .storage.repository import count_records, list_conflicts, seed_catalog
from .training import service as training_service
from .training.errors import TrainingValidationError
from .training.models import ACTIVITY_TYPES, COMPLETION_LABELS

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
    print(render_preview_text(preview))

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


def cmd_confirm(args: argparse.Namespace) -> int:
    """按批次确认待确认记录。

    为什么必须指定批次：批量确认最容易出的事故是把用户**故意留着**的待确认记录
    一起确认掉。这里只认批次（或它的唯一前缀），并且默认只预览。
    """
    from .storage import lifecycle

    conn, db_path = _open_db(args)
    batches = [
        row
        for row in conn.execute(
            """
            SELECT r.batch_id AS batch_id,
                   COALESCE(b.file_name, '（无批次）') AS file_name,
                   COUNT(*) AS n, MIN(r.record_date) AS d_from, MAX(r.record_date) AS d_to
              FROM records r LEFT JOIN import_batches b ON b.batch_id = r.batch_id
             WHERE r.verification_status = 'pending'
             GROUP BY r.batch_id
             ORDER BY b.imported_at DESC, b.rowid DESC, n DESC
            """
        ).fetchall()
    ]
    if args.list or not args.batch:
        print(f"数据库: {db_path}")
        if not batches:
            print("没有待确认记录。")
            conn.close()
            return 0
        print(f"待确认 {sum(b['n'] for b in batches)} 条，分 {len(batches)} 批：")
        for batch in batches:
            key = (batch["batch_id"] or "—")[:8]
            print(
                f"  {key:<9} {batch['file_name']:<44} {batch['n']:>3} 条  "
                f"{batch['d_from']} ~ {batch['d_to']}"
            )
        print("\n用 `confirm --batch <前缀> --yes` 确认某一批。")
        conn.close()
        return 0 if args.list else 2

    prefix = args.batch.strip().lower()
    hits = [b for b in batches if (b["batch_id"] or "").lower().startswith(prefix)]
    if not hits:
        print(f"没有以 {prefix!r} 开头的待确认批次。先跑 `confirm --list`。", file=sys.stderr)
        conn.close()
        return 1
    if len(hits) > 1:
        print(f"{prefix!r} 匹配到 {len(hits)} 个批次，请写得更长一点：", file=sys.stderr)
        for b in hits:
            print(f"  {b['batch_id'][:8]}  {b['file_name']}  {b['n']} 条", file=sys.stderr)
        conn.close()
        return 1

    target = hits[0]
    print(f"批次 {target['batch_id'][:8]}  {target['file_name']}")
    print(f"将确认 {target['n']} 条（{target['d_from']} ~ {target['d_to']}），其它批次的待确认记录不受影响。")
    if not args.yes:
        print("这是预览。确认无误后加 --yes 执行。")
        conn.close()
        return 0

    result = lifecycle.confirm_batch(conn, target["batch_id"])
    print(f"已确认 {result['confirmed']} 条，跳过 {result['skipped']} 条（无值或与已确认记录冲突）。")
    conn.close()
    return 0


def cmd_training(args: argparse.Namespace) -> int:
    conn, db_path = _open_db(args)
    try:
        if args.training_command == "add":
            session = training_service.add_session(
                conn,
                session_date=args.date or date.today(),
                activity_type=args.type,
                body_parts=args.parts,
                content=args.content or "",
                duration_minutes=args.duration,
                perceived_intensity=args.intensity,
                completion=args.completion,
                started_at=args.start,
                notes=args.notes,
            )
            parts = "/".join(session.body_part_labels) or "未记录部位"
            duration = f"{session.duration_minutes} 分钟" if session.duration_minutes else "时长未填"
            print(f"已记录训练 {session.session_id[:8]}：{session.session_date} {session.activity_label}")
            print(f"  部位: {parts} | {duration} | {COMPLETION_LABELS[session.completion]}")
            print("提示：主观强度与时长可以留空；留空就是 NULL，不会被当成 0。")
        elif args.training_command == "list":
            start = None
            if args.days:
                start = date.today() - timedelta(days=args.days - 1)
            sessions = training_store.list_sessions(conn, start=start, limit=args.limit)
            print(f"数据库: {db_path}")
            if not sessions:
                print("没有训练记录。")
            for session in sessions:
                parts = "/".join(session.body_part_labels) or "未记录部位"
                duration = f"{session.duration_minutes} 分钟" if session.duration_minutes else "—"
                print(
                    f"  {session.session_id[:8]}  {session.session_date}  "
                    f"{session.activity_label:<8} {parts:<12} {duration:<8} "
                    f"{COMPLETION_LABELS[session.completion]}"
                )
        else:
            training_service.remove_session(conn, args.session_id)
            print(f"已删除训练记录 {args.session_id[:8]}")
    except TrainingValidationError as exc:
        print(f"操作失败（{exc.code}）：{exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    return 0


def cmd_feedback(args: argparse.Namespace) -> int:
    conn, _ = _open_db(args)
    try:
        feedback = training_service.record_feedback(
            conn,
            feedback_date=args.date or date.today(),
            energy=args.energy,
            fatigue=args.fatigue,
            soreness_parts=args.soreness_parts,
            soreness_level=args.soreness_level,
            discomfort=args.discomfort,
            discomfort_note=args.note,
        )
    except TrainingValidationError as exc:
        print(f"记录失败（{exc.code}）：{exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    pieces = []
    if feedback.energy is not None:
        pieces.append(f"精力 {feedback.energy}/5")
    if feedback.fatigue is not None:
        pieces.append(f"疲劳 {feedback.fatigue}/5")
    if feedback.soreness_parts:
        pieces.append(f"酸痛 {'/'.join(feedback.soreness_labels)} {feedback.soreness_level}/5")
    if feedback.discomfort:
        pieces.append("有不适")
    print(f"已记录 {feedback.feedback_date} 反馈：" + ("；".join(pieces) or "（全部留空）"))
    print("同一天再次提交会覆盖更新，不会产生两条互相矛盾的反馈。")
    return 0


def cmd_prefs(args: argparse.Namespace) -> int:
    conn, _ = _open_db(args)
    try:
        if any(
            value is not None
            for value in (args.equipment, args.days, args.time_budget, args.avoid)
        ):
            prefs = training_service.save_preferences(
                conn,
                equipment=args.equipment,
                days_per_week=args.days,
                time_budget_minutes=args.time_budget,
                avoid_movements=args.avoid,
            )
            print("已保存训练偏好。")
        else:
            prefs = training_store.get_preferences(conn)
    except TrainingValidationError as exc:
        print(f"保存失败（{exc.code}）：{exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    equipment = "/".join(prefs.equipment) or "未设置"
    avoid = "/".join(prefs.avoid_movements) or "未设置"
    days = prefs.days_per_week if prefs.days_per_week is not None else "未设置"
    budget = f"{prefs.time_budget_minutes} 分钟" if prefs.time_budget_minutes else "未设置"
    print(f"每周训练日: {days}")
    print(f"单次时间预算: {budget}")
    print(f"可用器材: {equipment}")
    print(f"希望避免的动作: {avoid}")
    return 0


def cmd_advice(args: argparse.Namespace) -> int:
    from dataclasses import replace

    settings = load_settings()
    conn, _ = _open_db(args)
    try:
        target = date.fromisoformat(args.date) if args.date else date.today()
        guidance = build_guidance(
            conn, for_date=target, config=config_from_settings(settings)
        )
    except ValueError as exc:
        print(f"日期不合法：{exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    explanation = guidance.local_explanation
    source = "local"

    if args.explain:
        digest = build_digest(guidance)
        if not args.confirm_send:
            print("将发送以下摘要（不含记录 ID、绝对日期、截图与账号）：")
            print("-" * 60)
            print(digest)
            print("-" * 60)
            print("未发送。确认无误后追加 --confirm-send 才会调用 AI。")
            return 0

        if not settings.ai_enabled:
            print("未配置 DEEPSEEK_API_KEY，改用本地解释（建议本身不受影响）。", file=sys.stderr)
        else:
            from .ai_client.http_client import ChatCompletionClient

            client = ChatCompletionClient(
                api_key=settings.ai_api_key or "",
                base_url=settings.ai_base_url,
                model=settings.ai_model,
                timeout=settings.ai_timeout_seconds,
                max_retries=settings.ai_max_retries,
            )
            result = explain(guidance, client=client)
            source = result.source
            if result.source == "ai":
                explanation = result.text
            else:
                print(f"AI 解释不可用（{result.error}），已退回本地解释。", file=sys.stderr)
            guidance = replace(
                guidance, explanation_source=source, explanation_error=result.error
            )

    if args.json:
        print(render_json(guidance, explanation=explanation))
    else:
        print(render_text(guidance, explanation=explanation))
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
        ("confirm", cmd_confirm, "按批次确认待确认记录（默认只预览）"),
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
        if name == "confirm":
            sub.add_argument("--list", action="store_true", help="只列出待确认批次")
            sub.add_argument("--batch", default="", help="批次 id（可只写前缀）")
            sub.add_argument("--yes", action="store_true", help="真的执行确认（不加只预览）")
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

    # ---------------- 训练日志与建议（T08 / T09） ----------------

    def add_db(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--db", help="覆盖数据库路径（默认取 HEALTH_AGENT_DB_PATH）")

    training = subparsers.add_parser("training", help="训练日志：记录 / 查看 / 删除")
    training_sub = training.add_subparsers(dest="training_command", required=True)

    training_add = training_sub.add_parser("add", help="记录一次训练")
    add_db(training_add)
    training_add.add_argument("--date", help="训练日期 YYYY-MM-DD（默认今天）")
    training_add.add_argument("--type", required=True, choices=list(ACTIVITY_TYPES), help="活动类型")
    training_add.add_argument("--parts", default="", help="训练部位，逗号分隔（如 胸,肩 或 legs,core）")
    training_add.add_argument("--content", default="", help="训练内容，如 深蹲 3×8")
    training_add.add_argument("--duration", default=None, help="时长（分钟，1–600，可留空）")
    training_add.add_argument("--intensity", default=None, help="主观强度（1–10，可留空）")
    training_add.add_argument("--completion", default="completed", choices=["completed", "partial", "skipped"])
    training_add.add_argument("--start", default=None, help="开始时间 HH:MM（可留空）")
    training_add.add_argument("--notes", default=None, help="备注（可留空）")
    training_add.set_defaults(handler=cmd_training)

    training_list = training_sub.add_parser("list", help="查看训练记录")
    add_db(training_list)
    training_list.add_argument("--days", type=int, default=14, help="回看天数（默认 14）")
    training_list.add_argument("--limit", type=int, default=50)
    training_list.set_defaults(handler=cmd_training)

    training_delete = training_sub.add_parser("delete", help="删除一条训练记录")
    add_db(training_delete)
    training_delete.add_argument("session_id")
    training_delete.set_defaults(handler=cmd_training)

    feedback = subparsers.add_parser("feedback", help="记录今天的精力/疲劳/酸痛")
    add_db(feedback)
    feedback.add_argument("--date", help="反馈日期 YYYY-MM-DD（默认今天）")
    feedback.add_argument("--energy", default=None, help="精力 1–5（可留空）")
    feedback.add_argument("--fatigue", default=None, help="疲劳 1–5（可留空）")
    feedback.add_argument("--soreness-parts", default=None, help="酸痛部位，逗号分隔（与程度成对填写）")
    feedback.add_argument("--soreness-level", default=None, help="酸痛程度 1–5")
    feedback.add_argument("--discomfort", action="store_true", help="有疼痛/眩晕/明显不适")
    feedback.add_argument("--note", default=None, help="不适说明（可留空）")
    feedback.set_defaults(handler=cmd_feedback)

    prefs = subparsers.add_parser("prefs", help="查看/设置训练偏好")
    add_db(prefs)
    prefs.add_argument("--equipment", default=None, help="可用器材，逗号分隔")
    prefs.add_argument("--days", default=None, help="每周训练日 1–7")
    prefs.add_argument("--time-budget", default=None, help="单次时间预算（分钟 5–240）")
    prefs.add_argument("--avoid", default=None, help="希望避免的动作，逗号分隔")
    prefs.set_defaults(handler=cmd_prefs)

    advice = subparsers.add_parser("advice", help="生成某天的活动/训练建议")
    add_db(advice)
    advice.add_argument("--date", help="目标日期 YYYY-MM-DD（默认今天）")
    advice.add_argument("--json", action="store_true", help="输出 JSON（含依据与不确定性）")
    advice.add_argument("--explain", action="store_true", help="请求 AI 润色解释（先展示将发送的摘要）")
    advice.add_argument(
        "--confirm-send",
        action="store_true",
        help="与 --explain 连用：确认已看完摘要，允许发送",
    )
    advice.set_defaults(handler=cmd_advice)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
