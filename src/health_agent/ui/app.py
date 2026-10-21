"""FastAPI 应用：服务端渲染的本地界面。

页面只做四件事：看记录、确认记录、导入、裁决冲突。所有写操作都走 POST + CSRF，
所有依赖健康数据的判断都在 service 层，模板里不放业务规则。
"""

from __future__ import annotations

import hmac
import json
import time
import uuid
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__
from ..ai_client.assistant import (
    MAX_QUESTION_CHARS as ASSISTANT_MAX_QUESTION_CHARS,
)
from ..ai_client.assistant import (
    QUICK_QUESTIONS,
    Answer,
    ask as assistant_ask,
)
from ..ai_client.probe import fetch_models, probe_connection
from ..ai_client.summarizer import SOURCE_TEMPLATE, Narrative, peek_cached, rewrite_report
from ..analytics.aggregate import load_points, range_days, summarize, weekly_buckets
from ..analytics.quality import summarize_quality
from ..catalog import METRICS, RANGE_BOUND_METRICS, get_metric
from ..config import (
    AI_PRESETS,
    Settings,
    active_preset_key,
    ai_env_override_keys,
    base_url_problem,
    load_settings,
    normalize_base_url,
    preset_by_key,
    save_ai_config,
)
from ..errors import ConfigError
from ..guidance.engine import build_guidance
from ..guidance.engine import config_from_settings as guidance_config_from_settings
from ..ingestion.batch import commit_import
from ..ingestion.loader import LoadError, file_sha256, load_rows
from ..ingestion.preview import build_preview, render_text
from ..reports.builder import build_daily, build_weekly, latest_data_date
from ..reports.format import format_number
from ..rules.engine import config_from_settings, describe_sleep_target, run as run_rules
from ..storage import alerts as alert_store
from ..storage import lifecycle, preferences, repository
from ..storage import training as training_store
from ..storage.db import connect, current_version, init_db
from ..storage.exporting import delete_record, export_csv, export_json
from ..training import service as training_service
from ..training.errors import TrainingValidationError
from ..training.models import ACTIVITY_LABELS, ACTIVITY_TYPES, COMPLETION_LABELS
from .auth import SESSION_COOKIE, SessionSigner, load_secret, verify_password
from .charts import BandPoint, SeriesPoint, build_band, build_bars, build_line
from .context import (
    assistant_shortcuts,
    chronos_sectors,
    dashboard_tiles,
    metric_labels,
    reference,
)
from .pages import DIAL_PAGES, link_grid_pages, nav_groups, page_by_key

METRICS_BY_NAME = {metric.name: metric for metric in METRICS}

#: 助手快捷动作要跳的页面。路径统一从 pages.py 取，
#: 免得再加一页时"导航里有、助手按钮里还是旧地址"。
_ASSISTANT_HREFS = {
    key: page_by_key(key).href for key in ("assistant", "training", "sleep", "alerts")
}

BASE_DIR = Path(__file__).resolve().parent
ALLOWED_SUFFIXES = {".json", ".csv", ".tsv"}
PUBLIC_PATHS = ("/static", "/login", "/healthz")

#: 上传上限。健康数据的 JSON/CSV 正常在几十 KB 量级，10 MB 足够；
#: 没有上限时一次超大上传就能把内存吃满（局域网模式尤其危险）。
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _safe_next(target: str) -> str:
    r"""只允许跳回本站路径。

    挡住 `//evil.example`（协议相对）、`https://…`（绝对地址）与 `/\evil.example`
    （浏览器会把反斜杠归一成斜杠）这三类跳板。
    """
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target

def _items(rows) -> list[dict]:
    """把 sqlite Row 转成模板友好的 dict，并把 quality_flags 解析成列表。"""
    items = []
    for row in rows:
        item = dict(row)
        try:
            item["flags"] = json.loads(row["quality_flags"] or "[]")
        except (json.JSONDecodeError, TypeError):
            item["flags"] = []
        items.append(item)
    return items


def create_app(settings: Settings | None = None, *, db_path: Path | None = None) -> FastAPI:
    settings = settings or load_settings()
    path = Path(db_path) if db_path else settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)

    bootstrap = init_db(path)
    repository.seed_catalog(bootstrap)
    bootstrap.close()

    uploads_dir = path.parent / "uploads"
    uploads_dir.mkdir(exist_ok=True)

    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    # 统一走已经测过的人类可读格式化，避免模板里各写一套 round
    templates.env.filters["human"] = (
        lambda value, unit="", metric="": format_number(value, unit, metric)
    )
    signer = SessionSigner(load_secret(path.parent))

    app = FastAPI(title="Personal Health Agent", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.db_path = path
    app.state.uploads = uploads_dir
    app.state.signer = signer
    app.state.templates = templates
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    # ---------------- 依赖与工具 ----------------

    def get_db(request: Request):
        conn = connect(request.app.state.db_path, check_same_thread=False)
        try:
            yield conn
        finally:
            conn.close()

    def ui_mode(conn) -> str:
        return str(preferences.get_settings(conn).get("ui_mode") or "simple")

    def render(request: Request, name: str, **context):
        # 所有页面共享的上下文（导航清单来自 ui/pages.py，模板不再硬编码链接）
        context.setdefault("csrf", getattr(request.state, "csrf", ""))
        context.setdefault("active", request.url.path)
        context.setdefault("msg", request.query_params.get("msg"))
        context.setdefault("error", request.query_params.get("error"))
        context.setdefault("labels", metric_labels())
        context.setdefault("show_logout", settings.password_hash is not None)
        context.setdefault("nav_groups", nav_groups())
        context.setdefault("link_pages", link_grid_pages())
        context.setdefault("dial_pages", DIAL_PAGES)
        return templates.TemplateResponse(request, name, context)

    def go(url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    # ---------------- 鉴权中间件 ----------------

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if any(request.url.path.startswith(prefix) for prefix in PUBLIC_PATHS):
            return await call_next(request)

        session = signer.unsign(request.cookies.get(SESSION_COOKIE))
        if session is None:
            if settings.password_hash is None:
                # 本机模式：没有设密码，只监听回环地址，直接放行
                request.state.csrf = ""
            else:
                target = request.url.path
                return go(f"/login?next={quote(target)}")
        else:
            request.state.csrf = session.get("csrf", "")

        if request.method == "POST" and getattr(request.state, "csrf", ""):
            content_type = request.headers.get("content-type", "")
            if not content_type.startswith(
                ("application/x-www-form-urlencoded", "multipart/form-data")
            ):
                # 非表单 POST 一律拒绝：否则一个没有 content-type 的请求就能绕过 CSRF
                return HTMLResponse("请求格式不正确：仅接受表单提交", status_code=400)
            # 必须先 body() 再 form()：
            # Starlette 的 BaseHTTPMiddleware 只在 `_body` 被缓存时向下游重放请求体，
            # 而 `form()` 走 stream() 会把下游收到的 body 变成空 —— 于是路由里的
            # `Form(...)` 全部取到默认值（地址空、模式未知），用户看到的是"地址不能为空"
            # 这种驴唇不对马嘴的报错。2026-10-21 真实踩到（开了访问密码才会走这段）。
            await request.body()
            form = await request.form()
            sent = str(form.get("csrf", ""))
            if not hmac.compare_digest(sent, request.state.csrf):
                return HTMLResponse("CSRF 校验失败，请返回上一页刷新后重试", status_code=400)

        return await call_next(request)

    # ---------------- 登录 ----------------

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = "/"):
        if settings.password_hash is None:
            return go("/")
        return render(request, "login.html", next=next)

    @app.post("/login")
    def login(request: Request, password: str = Form(""), next: str = Form("/")):
        if not verify_password(password, settings.password_hash):
            return go("/login?error=" + quote("密码不正确"))
        session = signer.new_session()
        response = go(_safe_next(next or "/"))
        response.set_cookie(
            SESSION_COOKIE,
            signer.sign(session),
            httponly=True,
            samesite="lax",
            max_age=None,
        )
        return response

    @app.post("/logout")
    def logout():
        response = go("/login")
        response.delete_cookie(SESSION_COOKIE)
        return response

    # ---------------- 概览 ----------------

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, conn=Depends(get_db)):
        counts = {
            "total": repository.count_records(conn),
            "pending": repository.count_records(conn, status="pending"),
            "confirmed": repository.count_records(conn, status="confirmed"),
            "rejected": repository.count_records(conn, status="rejected"),
        }
        flagged = conn.execute(
            "SELECT COUNT(*) AS n FROM records WHERE quality_flags != '[]' AND verification_status != 'superseded'"
        ).fetchone()["n"]
        conflicts = conn.execute(
            "SELECT COUNT(*) AS n FROM conflicts WHERE resolution = 'pending'"
        ).fetchone()["n"]
        unread_alerts = alert_store.count_unread(conn)
        mode = ui_mode(conn)
        end = latest_data_date(conn) or date.today()
        start = end - timedelta(days=6)
        config = config_from_settings(settings)
        tiles = dashboard_tiles(conn, end)
        sleep_chart = build_bars(_series(conn, "night_sleep_minutes", start, end),
                                 target=float(config.sleep_target_minutes), height=150)
        if mode == "chronos":
            # 与 /chronos 路由同一套上下文（tiles/sectors 同源），否则表盘会空转
            home_guidance = build_guidance(
                conn, for_date=date.today(), config=guidance_config_from_settings(settings)
            )
            return render(
                request,
                "chronos.html",
                mode=mode,
                counts=counts,
                conflicts=conflicts,
                unread_alerts=unread_alerts,
                tiles=tiles,
                sectors=chronos_sectors(
                    conn, tiles, counts, conflicts, unread_alerts, end, guidance=home_guidance
                ),
                shortcuts=assistant_shortcuts(home_guidance, _ASSISTANT_HREFS),
                selected="",
                end_day=end.isoformat(),
            )
        recent = _items(repository.list_records(conn, limit=10))
        batches = conn.execute(
            "SELECT * FROM import_batches ORDER BY imported_at DESC LIMIT 5"
        ).fetchall()
        today = date.today()
        guidance = build_guidance(
            conn, for_date=today, config=guidance_config_from_settings(settings)
        )
        return render(
            request,
            "index.html",
            counts=counts,
            flagged=flagged,
            conflicts=conflicts,
            recent=recent,
            batches=batches,
            unread_alerts=unread_alerts,
            mode=mode,
            tiles=tiles,
            sleep_chart=sleep_chart,
            target_text=describe_sleep_target(config),
            end_day=end.isoformat(),
            total_records=counts["total"],
            guidance=guidance,
            today=today.isoformat(),
            activity_types=ACTIVITY_TYPES,
            activity_labels=ACTIVITY_LABELS,
        )

    # ---------------- 记录 ----------------

    @app.get("/records", response_class=HTMLResponse)
    def records(
        request: Request,
        conn=Depends(get_db),
        metric: str = "",
        status: str = "",
        limit: int = 100,
    ):
        limit = max(1, min(limit, 500))
        rows = repository.list_records(
            conn,
            metric_name=metric or None,
            status=status or None,
            limit=limit,
        )
        return render(
            request,
            "records.html",
            items=_items(rows),
            metric=metric,
            status=status,
            limit=limit,
            total=repository.count_records(conn),
        )

    @app.post("/records/{record_id}/confirm")
    def record_confirm(record_id: str, request: Request, conn=Depends(get_db)):
        try:
            lifecycle.confirm_record(conn, record_id)
            return go("/records?msg=" + quote("已确认"))
        except Exception as exc:  # noqa: BLE001 - 界面层统一转成可读提示
            return go("/records?error=" + quote(str(exc)))

    @app.post("/records/{record_id}/reject")
    def record_reject(record_id: str, request: Request, reason: str = Form(""), conn=Depends(get_db)):
        try:
            lifecycle.reject_record(conn, record_id, reason=reason or None)
            return go("/records?msg=" + quote("已驳回"))
        except Exception as exc:  # noqa: BLE001
            return go("/records?error=" + quote(str(exc)))

    @app.post("/records/{record_id}/revise")
    def record_revise(
        record_id: str,
        request: Request,
        value: str = Form(""),
        reason: str = Form(""),
        conn=Depends(get_db),
    ):
        try:
            new_value = float(value)
        except ValueError:
            return go("/records?error=" + quote("修正值必须是数字"))
        try:
            row = repository.get_record(conn, record_id)
            if row is None:
                return go("/records?error=" + quote("记录不存在"))
            if row["verification_status"] == "confirmed":
                lifecycle.revise_confirmed(conn, record_id, value=new_value, reason=reason or "手工修正")
            else:
                lifecycle.update_pending_value(conn, record_id, value=new_value, reason=reason or "手工修正")
            return go("/records?msg=" + quote("已修正"))
        except Exception as exc:  # noqa: BLE001
            return go("/records?error=" + quote(str(exc)))

    @app.post("/records/{record_id}/delete")
    def record_delete(record_id: str, request: Request, conn=Depends(get_db)):
        try:
            delete_record(conn, record_id)
            return go("/records?msg=" + quote("已删除"))
        except Exception as exc:  # noqa: BLE001
            return go("/records?error=" + quote(str(exc)))

    # ---------------- 导入 ----------------

    @app.get("/imports", response_class=HTMLResponse)
    def imports(request: Request, conn=Depends(get_db)):
        batches = conn.execute("SELECT * FROM import_batches ORDER BY imported_at DESC").fetchall()
        return render(request, "imports.html", batches=batches)

    @app.post("/imports/upload")
    async def imports_upload(request: Request, file: UploadFile = File(...)):
        name = Path(file.filename or "").name
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            return go("/imports?error=" + quote(f"不支持的文件类型 {suffix}，仅接受 json/csv/tsv"))
        staged = f"{int(time.time())}_{name}"
        target = request.app.state.uploads / staged
        # 只多读 1 字节就能判断是否超限，不用把整个大文件读进内存
        payload = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(payload) > MAX_UPLOAD_BYTES:
            return go(
                "/imports?error="
                + quote(f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB 上限，请拆分后再导入")
            )
        target.write_bytes(payload)
        return go("/imports/preview?staged=" + quote(staged))

    def _staged_path(request: Request, staged: str) -> Path | None:
        name = Path(staged).name
        if name != staged:
            return None
        candidate = request.app.state.uploads / name
        return candidate if candidate.is_file() else None

    @app.get("/imports/preview", response_class=HTMLResponse)
    def imports_preview(request: Request, staged: str = "", conn=Depends(get_db)):
        path = _staged_path(request, staged)
        if path is None:
            return go("/imports?error=" + quote("找不到待导入文件，请重新上传"))
        try:
            rows = load_rows(path)
        except LoadError as exc:
            return go("/imports?error=" + quote(str(exc)))
        preview = build_preview(rows, conn)
        return render(
            request,
            "import_preview.html",
            staged=staged,
            preview=preview,
            text=render_text(preview, limit=50),
        )

    @app.post("/imports/commit")
    def imports_commit(request: Request, staged: str = Form(""), conn=Depends(get_db)):
        path = _staged_path(request, staged)
        if path is None:
            return go("/imports?error=" + quote("找不到待导入文件"))
        try:
            rows = load_rows(path)
        except LoadError as exc:
            return go("/imports?error=" + quote(str(exc)))
        preview = build_preview(rows, conn)
        if not preview.to_insert:
            return go("/imports?error=" + quote("没有可写入的记录"))
        result = commit_import(
            conn,
            preview,
            file_name=path.name,
            file_sha256=file_sha256(path),
            source_kind=path.suffix.lstrip(".").lower(),
        )
        path.unlink(missing_ok=True)
        return go("/imports?msg=" + quote(result.summary()))

    @app.post("/imports/{batch_id}/confirm")
    def imports_confirm(batch_id: str, request: Request, conn=Depends(get_db)):
        result = lifecycle.confirm_batch(conn, batch_id)
        return go(
            "/imports?msg="
            + quote(f"已确认 {result['confirmed']} 条，跳过 {result['skipped']} 条（无值或冲突）")
        )

    @app.post("/imports/{batch_id}/revoke")
    def imports_revoke(batch_id: str, request: Request, conn=Depends(get_db)):
        try:
            result = lifecycle.revoke_batch(conn, batch_id)
            return go(
                "/imports?msg="
                + quote(
                    f"批次已撤销：删除待确认 {result['deleted_pending']} 条，"
                    f"已确认转历史 {result['superseded_confirmed']} 条"
                )
            )
        except Exception as exc:  # noqa: BLE001
            return go("/imports?error=" + quote(str(exc)))

    # ---------------- 冲突 ----------------

    @app.get("/conflicts", response_class=HTMLResponse)
    def conflicts(request: Request, conn=Depends(get_db)):
        rows = conn.execute(
            """
            SELECT c.*,
                   old.value AS old_value, old.unit AS old_unit, old.verification_status AS old_status,
                   new.value AS new_value, new.unit AS new_unit, new.verification_status AS new_status
              FROM conflicts c
              LEFT JOIN records old ON old.record_id = c.existing_record_id
              JOIN records new ON new.record_id = c.incoming_record_id
             WHERE c.resolution = 'pending'
             ORDER BY c.detected_at DESC
            """
        ).fetchall()
        return render(request, "conflicts.html", rows=rows)

    @app.post("/conflicts/{conflict_id}/resolve")
    def conflict_resolve(
        conflict_id: str,
        request: Request,
        resolution: str = Form(...),
        note: str = Form(""),
        conn=Depends(get_db),
    ):
        try:
            lifecycle.resolve_conflict(conn, conflict_id, resolution, note=note or None)
            return go("/conflicts?msg=" + quote("已裁决"))
        except Exception as exc:  # noqa: BLE001
            return go("/conflicts?error=" + quote(str(exc)))

    # ---------------- 提醒 ----------------

    @app.get("/alerts", response_class=HTMLResponse)
    def alerts_page(request: Request, conn=Depends(get_db), unread: str = "", rule: str = ""):
        unread_only = unread in {"1", "true", "on"}
        rule_id = rule or None
        items = alert_store.list_alerts(
            conn, limit=100, unread_only=unread_only, rule_id=rule_id
        )
        # 区分两种空状态："从未评估"与"评估过但无新增"，文案不同（T03 §4.6）
        ever_evaluated = (
            conn.execute("SELECT COUNT(*) AS n FROM alerts").fetchone()["n"] > 0
        )
        return render(
            request,
            "alerts.html",
            items=items,
            unread_only=unread_only,
            rule_id=rule_id,
            rule_ids=alert_store.list_rule_ids(conn),
            unread_count=alert_store.count_unread(conn),
            ever_evaluated=ever_evaluated,
        )

    @app.post("/alerts/run")
    def alerts_run(request: Request, conn=Depends(get_db)):
        config = config_from_settings(settings)
        today = date.today()
        new_alerts = run_rules(conn, config, today)
        if not new_alerts:
            return go("/alerts?msg=" + quote("没有新的提醒（可能已提醒过，或数据不足以判断）"))
        return go(f"/alerts?msg=" + quote(f"新增 {len(new_alerts)} 条提醒"))

    @app.post("/alerts/{alert_id}/read")
    def alert_read(alert_id: str, request: Request, conn=Depends(get_db)):
        alert_store.mark_read(conn, alert_id)
        return go("/alerts")

    @app.post("/alerts/{alert_id}/feedback")
    def alert_feedback(
        alert_id: str,
        request: Request,
        feedback: str = Form("none"),
        conn=Depends(get_db),
    ):
        try:
            alert_store.set_feedback(conn, alert_id, feedback)
        except ValueError as exc:
            return go("/alerts?error=" + quote(str(exc)))
        return go("/alerts?msg=" + quote("已记录反馈"))

    # ---------------- 报告 ----------------

    @app.get("/reports", response_class=HTMLResponse)
    def reports(
        request: Request,
        conn=Depends(get_db),
        kind: str = "daily",
        day: str = "",
    ):
        fallback = latest_data_date(conn) or (date.today() - timedelta(days=1))
        try:
            target = date.fromisoformat(day) if day else fallback
        except ValueError:
            target = fallback

        if kind == "weekly":
            payload = build_weekly(conn, target)
            buckets = weekly_buckets(conn, "night_sleep_minutes", target, weeks=4)
        else:
            payload = build_daily(conn, target)
            buckets = []
        quality = summarize_quality(
            conn,
            date.fromisoformat(payload.period_start),
            date.fromisoformat(payload.period_end),
        )
        # 打开页面只读缓存：报告必须立刻出内容，不能卡在网络等待上（规格 §7）
        narrative = peek_cached(conn, payload, settings=settings)
        can_rewrite = settings.ai_enabled and narrative is None
        if narrative is None:
            narrative = Narrative(text=payload.text, source=SOURCE_TEMPLATE)
        return render(
            request,
            "reports.html",
            payload=payload,
            narrative=narrative,
            can_rewrite=can_rewrite,
            quality=quality,
            kind="weekly" if kind == "weekly" else "daily",
            day=target.isoformat(),
            buckets=buckets,
        )

    @app.post("/reports/rewrite")
    def reports_rewrite(
        request: Request,
        conn=Depends(get_db),
        kind: str = Form("daily"),
        day: str = Form(""),
    ):
        """显式请求 AI 改写。只有用户点按钮时才花钱（规格 §7）。"""
        if not settings.ai_enabled:
            return go("/reports?error=" + quote("未配置 AI 密钥，报告使用本地文案"))
        fallback = latest_data_date(conn) or (date.today() - timedelta(days=1))
        try:
            target = date.fromisoformat(day) if day else fallback
        except ValueError:
            target = fallback

        payload = build_weekly(conn, target) if kind == "weekly" else build_daily(conn, target)
        narrative = rewrite_report(conn, payload, settings=settings)
        query = f"kind={kind}&day={target.isoformat()}"
        if narrative.source == SOURCE_TEMPLATE:
            reason = narrative.error or "unknown"
            return go(f"/reports?{query}&error=" + quote(f"AI 调用失败（{reason}），已保留本地文案"))
        return go(f"/reports?{query}&msg=" + quote("已用 AI 改写，结果已缓存"))

    # ---------------- AI 助手（自由提问） ----------------

    #: 最近几次回答。放内存里而不是建表：
    #: 一次问答的结果没有长期价值，也包含健康描述，落库反而多一处要清理的隐私面。
    #: 重启就没了，页面会明说这一点。
    answers: dict[str, Answer] = {}

    @app.get("/assistant", response_class=HTMLResponse)
    def assistant_page(request: Request, q: str = "", a: str = ""):
        answer = answers.get(a) if a else None
        return render(
            request,
            "assistant.html",
            question=q[:ASSISTANT_MAX_QUESTION_CHARS],
            answer=answer,
            quick_questions=QUICK_QUESTIONS,
            ai_enabled=settings.ai_enabled,
            ai_model=settings.ai_model,
            ai_key_tail=settings.ai_key_tail,
            max_chars=ASSISTANT_MAX_QUESTION_CHARS,
        )

    @app.post("/assistant")
    def assistant_submit(request: Request, conn=Depends(get_db), question: str = Form("")):
        """显式提交才花钱（规格 §7）：GET 打开页面永远零网络。"""
        text = (question or "").strip()
        if not text:
            return go("/assistant?error=" + quote("先写一句想问的，例如「我这周睡得怎么样」"))
        result = assistant_ask(conn, text, settings=settings)
        token = uuid.uuid4().hex[:12]
        answers[token] = result
        # 只留最近 20 条：内存里堆答案没有意义，也没有地方给用户清理
        while len(answers) > 20:
            answers.pop(next(iter(answers)))
        return go(f"/assistant?a={token}")

    # ---------------- 睡眠分析 / 指标趋势 / 时钟模式（只读） ----------------

    def _series(conn, metric: str, start: date, end: date):
        """把某指标的日序列补成"整段日期轴"，缺失填 None。

        缺失必须是 None 而不是跳过：跳过会把两段不连续的数据连成一条线。
        """
        by_date = {p.record_date: p for p in load_points(conn, metric, start, end)}
        series = []
        for day in range_days(start, end):
            point = by_date.get(day.isoformat())
            series.append(
                SeriesPoint(
                    label=day.strftime("%m-%d"),
                    value=point.value if point else None,
                    record_id=point.record_id if point else None,
                )
            )
        return series

    RANGE_PAIRS = {
        "hrv_min_ms": "hrv_max_ms",
        "hrv_max_ms": "hrv_min_ms",
        "spo2_min_percent": "spo2_max_percent",
        "spo2_max_percent": "spo2_min_percent",
    }

    @app.get("/sleep", response_class=HTMLResponse)
    def sleep_page(request: Request, conn=Depends(get_db), days: int = 7):
        days = 90 if days == 90 else (30 if days == 30 else 7)
        end = latest_data_date(conn) or date.today()
        start = end - timedelta(days=days - 1)
        duration = summarize(conn, "night_sleep_minutes", start, end)
        series = _series(conn, "night_sleep_minutes", start, end)
        config = config_from_settings(settings)
        chart = build_bars(series, target=float(config.sleep_target_minutes))

        stages = []
        for name, label, color in (
            ("deep_sleep_percent", "深睡", "#0F3D5C"),
            ("light_sleep_percent", "浅睡", "#5DCAA5"),
            ("rem_sleep_percent", "REM", "#85B7EB"),
        ):
            summary = summarize(conn, name, start, end)
            latest = summary.points[-1] if summary.points else None
            stages.append(
                {
                    "label": label,
                    "color": color,
                    "value": latest.value if latest else None,
                    "date": latest.record_date if latest else None,
                    "sample_days": summary.sample_days,
                }
            )

        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM records"
            " WHERE verification_status = 'pending' AND metric_name = 'night_sleep_minutes'"
        ).fetchone()["n"]

        return render(
            request,
            "sleep.html",
            days=days,
            chart=chart,
            series=series,
            duration=duration,
            stages=stages,
            rows=[item for item in series if item.value is not None][-14:],
            target_text=describe_sleep_target(config),
            reference=reference(conn, "night_sleep_minutes", end),
            pending=pending,
            end_day=end.isoformat(),
        )

    @app.get("/trends", response_class=HTMLResponse)
    def trends(
        request: Request,
        conn=Depends(get_db),
        metric: str = "night_sleep_minutes",
        days: int = 30,
    ):
        if metric not in METRICS_BY_NAME:
            metric = "night_sleep_minutes"
        spec = get_metric(metric)
        days = 90 if days == 90 else (7 if days == 7 else 30)
        end = latest_data_date(conn) or date.today()
        start = end - timedelta(days=days - 1)

        is_range = metric in RANGE_BOUND_METRICS
        if is_range:
            counterpart = RANGE_PAIRS[metric]
            low_name = metric if "min" in metric else counterpart
            high_name = counterpart if low_name == metric else metric
            lows = {p.record_date: p for p in load_points(conn, low_name, start, end)}
            highs = {p.record_date: p for p in load_points(conn, high_name, start, end)}
            band_points = []
            for day in range_days(start, end):
                key = day.isoformat()
                band_points.append(
                    BandPoint(
                        label=day.strftime("%m-%d"),
                        low=lows[key].value if key in lows else None,
                        high=highs[key].value if key in highs else None,
                    )
                )
            chart = build_band(band_points)
        else:
            series = _series(conn, metric, start, end)
            chart = build_bars(series) if spec.kind in {"count", "score"} else build_line(series)

        summary = summarize(conn, spec, start, end)
        groups = [("P0", [m for m in METRICS if m.tier == "P0"]),
                  ("P1", [m for m in METRICS if m.tier == "P1"])]
        return render(
            request,
            "trends.html",
            metric=metric,
            spec=spec,
            days=days,
            chart=chart,
            summary=summary,
            is_range=is_range,
            groups=groups,
            reference=reference(conn, metric, end),
            end_day=end.isoformat(),
        )

    # ---------------- 训练记录（T10） ----------------

    def _training_redirect_target(raw: str | None) -> str:
        """只允许从训练页返回首页或训练页，避免被当成开放跳转。"""
        target = _safe_next(raw or "/training")
        return target if target in {"/", "/training"} else "/training"

    @app.get("/training", response_class=HTMLResponse)
    def training_page(request: Request, conn=Depends(get_db), days: int = 30):
        days = days if days in {7, 30, 90} else 30
        today = date.today()
        start = today - timedelta(days=days - 1)
        sessions = training_store.list_sessions(conn, start=start, end=today, limit=100)
        feedback = training_store.get_feedback(conn, today)
        prefs = training_store.get_preferences(conn)
        guidance = build_guidance(
            conn, for_date=today, config=guidance_config_from_settings(settings)
        )
        # 助手预填：只出现在表单里，落库仍需用户点「保存」（POST + CSRF + 校验）
        prefill = {
            key: request.query_params.get(key, "")
            for key in ("from", "type", "parts", "duration", "content", "focus")
        }
        return render(
            request,
            "training.html",
            prefill=prefill,
            days=days,
            today=today.isoformat(),
            sessions=sessions,
            feedback=feedback,
            prefs=prefs,
            guidance=guidance,
            week_count=training_store.count_sessions(
                conn, start=today - timedelta(days=6), end=today
            ),
            total_count=training_store.count_sessions(conn),
            activity_types=ACTIVITY_TYPES,
            activity_labels=ACTIVITY_LABELS,
            completion_labels=COMPLETION_LABELS,
        )

    @app.post("/training/add")
    async def training_add(request: Request, conn=Depends(get_db)):
        form = await request.form()
        target = _training_redirect_target(form.get("next"))
        try:
            session = training_service.add_session(
                conn,
                session_date=str(form.get("session_date") or date.today().isoformat()),
                activity_type=str(form.get("activity_type") or ""),
                body_parts=str(form.get("body_parts") or ""),
                content=str(form.get("content") or ""),
                duration_minutes=form.get("duration_minutes"),
                perceived_intensity=form.get("perceived_intensity"),
                completion=str(form.get("completion") or "completed"),
                started_at=form.get("started_at"),
                notes=form.get("notes"),
            )
        except TrainingValidationError as exc:
            return go(f"{target}?error=" + quote(str(exc)))
        label = ACTIVITY_LABELS.get(session.activity_type, session.activity_type)
        return go(f"{target}?msg=" + quote(f"已记录：{label} {session.session_date}"))

    @app.post("/training/feedback")
    async def training_feedback(request: Request, conn=Depends(get_db)):
        form = await request.form()
        target = _training_redirect_target(form.get("next"))
        try:
            training_service.record_feedback(
                conn,
                feedback_date=str(form.get("feedback_date") or date.today().isoformat()),
                energy=form.get("energy"),
                fatigue=form.get("fatigue"),
                soreness_parts=form.get("soreness_parts"),
                soreness_level=form.get("soreness_level"),
                discomfort=form.get("discomfort") is not None,
                discomfort_note=form.get("discomfort_note"),
            )
        except TrainingValidationError as exc:
            return go(f"{target}?error=" + quote(str(exc)))
        return go(f"{target}?msg=" + quote("已记录今天的状态；同日再次提交会覆盖更新"))

    @app.post("/training/prefs")
    async def training_prefs(request: Request, conn=Depends(get_db)):
        form = await request.form()
        target = _training_redirect_target(form.get("next"))
        try:
            training_service.save_preferences(
                conn,
                equipment=form.get("equipment"),
                days_per_week=form.get("days_per_week"),
                time_budget_minutes=form.get("time_budget_minutes"),
                avoid_movements=form.get("avoid_movements"),
            )
        except TrainingValidationError as exc:
            return go(f"{target}?error=" + quote(str(exc)))
        return go(f"{target}?msg=" + quote("已保存训练偏好"))

    @app.post("/training/{session_id}/delete")
    def training_delete(session_id: str, request: Request, conn=Depends(get_db)):
        try:
            training_service.remove_session(conn, session_id)
        except TrainingValidationError as exc:
            return go("/training?error=" + quote(str(exc)))
        return go("/training?msg=" + quote("已删除训练记录"))

    @app.get("/chronos", response_class=HTMLResponse)
    def chronos(request: Request, conn=Depends(get_db), s: str = ""):
        end = latest_data_date(conn) or date.today()
        tiles = dashboard_tiles(conn, end)
        counts = {
            "total": repository.count_records(conn),
            "pending": repository.count_records(conn, status="pending"),
            "confirmed": repository.count_records(conn, status="confirmed"),
        }
        conflicts = conn.execute(
            "SELECT COUNT(*) AS n FROM conflicts WHERE resolution = 'pending'"
        ).fetchone()["n"]
        unread = alert_store.count_unread(conn)
        dial_guidance = build_guidance(
            conn, for_date=date.today(), config=guidance_config_from_settings(settings)
        )
        valid = {page.key for page in DIAL_PAGES}
        return render(
            request,
            "chronos.html",
            mode=ui_mode(conn),
            tiles=tiles,
            sectors=chronos_sectors(
                conn, tiles, counts, conflicts, unread, end, guidance=dial_guidance
            ),
            shortcuts=assistant_shortcuts(dial_guidance, _ASSISTANT_HREFS),
            selected=s if s in valid else "",
            counts=counts,
            conflicts=conflicts,
            unread_alerts=unread,
            end_day=end.isoformat(),
        )

    @app.post("/settings/mode")
    def settings_mode(
        request: Request,
        conn=Depends(get_db),
        mode: str = Form("simple"),
        next_path: str = Form("/"),
    ):
        if mode not in {"simple", "chronos"}:
            return go("/settings?error=" + quote("未知的界面模式"))
        preferences.set_settings(conn, {"ui_mode": mode})
        target = next_path if next_path.startswith("/") else "/"
        return go(target)

    # ---------------- 设置 ----------------

    def _settings_context(request: Request, conn, **overrides) -> dict:
        """设置页的全部上下文。

        抽成函数是因为「拉取可用模型」要**原地重渲染**这一页（零 JS 没法局部刷新），
        重渲染必须与首次打开给同一套字段，否则模板里少一个变量就 500。
        """
        # 阈值只读展示：全部来自环境变量，本页不提供第二个修改入口（假控件禁令）
        thresholds = [
            ("睡眠目标", f"{settings.sleep_target_minutes} 分钟", "HEALTH_AGENT_SLEEP_TARGET_MIN"),
            ("基线窗口", f"{settings.baseline_window_days} 天", "HEALTH_AGENT_BASELINE_WINDOW_DAYS"),
            ("基线最少样本", f"{settings.baseline_min_samples} 天", "HEALTH_AGENT_BASELINE_MIN_SAMPLES"),
            ("基线偏离倍数", f"{settings.baseline_mad_factor:g} × MAD", "HEALTH_AGENT_BASELINE_MAD_FACTOR"),
            ("睡眠不足天数", f"{settings.sleep_deficit_days} 天", "HEALTH_AGENT_SLEEP_DEFICIT_DAYS"),
            ("静息心率偏移", f"{settings.resting_hr_shift_bpm:g} 次/分", "HEALTH_AGENT_RESTING_HR_SHIFT_BPM"),
            ("数据缺失率", f"{settings.data_gap_rate:g}", "HEALTH_AGENT_DATA_GAP_RATE"),
            ("提醒冷却期", f"{settings.rule_cooldown_hours} 小时", "HEALTH_AGENT_RULE_COOLDOWN_HOURS"),
        ]
        context = {
            "values": preferences.get_settings(conn),
            "keys": preferences.ALLOWED_KEYS,
            "ai_enabled": settings.ai_enabled,
            "ai_presets": AI_PRESETS,
            "ai_preset": active_preset_key(settings),
            "ai_key_tail": settings.ai_key_tail,
            "ai_base_url": settings.ai_base_url,
            "ai_model": settings.ai_model,
            "ai_timeout": int(settings.ai_timeout_seconds),
            "ai_env_path": str(settings.env_path),
            "ai_shadowed": ai_env_override_keys(),
            "ai_models": [],
            "ai_models_note": "",
            "ai_model_missing": False,
            "lan_enabled": settings.lan_enabled,
            "sleep_target": describe_sleep_target(config_from_settings(settings)),
            "mode": ui_mode(conn),
            "thresholds": thresholds,
            "app_version": __version__,
            "schema_version": current_version(conn),
            "db_path": str(request.app.state.db_path),
        }
        context.update(overrides)
        return context

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, conn=Depends(get_db)):
        return render(request, "settings.html", **_settings_context(request, conn))

    @app.post("/settings")
    async def settings_save(request: Request, conn=Depends(get_db)):
        form = await request.form()
        values = {key: str(form.get(key, "")) for key in preferences.ALLOWED_KEYS}
        preferences.set_settings(conn, values)
        return go("/settings?msg=" + quote("已保存（留空表示不设置）"))

    # ---------------- AI 接口配置 ----------------

    def _ai_inputs(preset_key: str, base_url: str, model: str) -> tuple[str, str, str]:
        """把"预设 + 两个可留空字段"解析成最终生效的地址与模型。

        零 JS 的取舍：选预设没法自动把值填进输入框（没脚本做不了实时联动），
        所以约定成"留空即取预设值"——自己填了就以自己填的为准。两个方向都不打架，
        也不用假装有联动。返回的第三项是给用户看的解析说明，不参与任何判断。
        """
        preset = preset_by_key(preset_key)
        url = base_url.strip() or (preset["base_url"] if preset else "")
        name = model.strip() or (preset["model"] if preset else "")
        filled = [label for label, value in (("地址", base_url), ("模型", model)) if value.strip()]
        if preset is None:
            note = "自定义接口"
        elif not filled:
            note = f"按「{preset['label']}」预设填入地址与模型"
        elif len(filled) == 2:
            note = "地址与模型都用你填的值"
        else:
            note = f"用你填的{filled[0]}，其余按「{preset['label']}」预设"
        return url, name, note

    @app.post("/settings/ai")
    def settings_ai_save(
        request: Request,
        conn=Depends(get_db),
        action: str = Form("save"),
        preset: str = Form("custom"),
        api_key: str = Form(""),
        base_url: str = Form(""),
        model: str = Form(""),
        clear_key: str = Form(""),
    ):
        """保存或试连 AI 接口。

        - `action=test`：**只试不发**，不写文件也不落任何调用记录
        - `action=save`：写 `.env` 后立刻重新装载，无需重启
        - 密钥三种语义：填了新值 → 覆盖；勾了"清除" → 删掉；都空 → 保持原样
        """
        nonlocal settings
        url, name, note = _ai_inputs(preset, base_url, model)
        # 先看原值：把密钥粘进地址栏的人，补完协议头就认不出来了
        if url.strip().lower().startswith("sk-"):
            return go(
                "/settings?error="
                + quote(
                    "接口地址这一格填的像是 API 密钥（以 sk- 开头），"
                    "密钥请填到下面「API 密钥」那一格；接口地址应形如 https://api.deepseek.com/v1"
                )
            )
        url, norm_note = normalize_base_url(url)
        if norm_note:
            note = f"{note}，{norm_note}"
        key = api_key.strip()

        def _persist(api_key_value: str | None) -> str | None:
            """写 `.env` 并重新装载。返回错误文案，成功返回 None。"""
            nonlocal settings
            try:
                save_ai_config(settings.env_path, api_key=api_key_value, base_url=url, model=name)
            except OSError as exc:
                return f"写入 {settings.env_path.name} 失败：{type(exc).__name__}"
            try:
                reloaded = load_settings(project_root=settings.env_path.parent)
            except ConfigError as exc:
                return f"配置已写入，但重新装载失败：{exc}"
            settings = reloaded
            request.app.state.settings = reloaded
            return None

        # 报错要说到点子上：空地址、缺协议头、把密钥填错格子，是三种不同的问题，
        # 用同一句"必须以 http 开头"回过去，用户只会更困惑（2026-10-21 真实反馈）。
        problem = base_url_problem(url)
        if problem:
            return go(
                "/settings?error="
                + quote(
                    f"{problem}。可以选一个「服务商」预设自动填好，"
                    "或自己填完整地址，例如 https://api.deepseek.com/v1"
                )
            )
        if not name:
            return go(
                "/settings?error="
                + quote("还没有填模型名（例如 deepseek-chat）；选「服务商」预设可以自动带上")
            )

        if action == "test":
            # 走线程池（本函数是同步 def，FastAPI 已替我们放到线程里），不阻塞事件循环
            result = probe_connection(
                api_key=key or (settings.ai_api_key or ""),
                base_url=url,
                model=name,
                timeout=min(max(settings.ai_timeout_seconds, 5.0), 20.0),
            )
            text = f"测试连接（{note}）：{result.headline} · {result.message}"
            if result.latency_ms is not None:
                text += f" · {result.latency_ms} ms"
            if result.ok:
                text += "。确认无误后点「保存」写入配置。"
            return go(("/settings?msg=" if result.ok else "/settings?error=") + quote(text))

        if action == "models":
            # 「保存并拉取可用模型」：先把地址与密钥存下来（拉列表要用），再列模型。
            # 原地重渲染而不是跳转：零 JS 没法局部更新，重渲染能保住刚填的地址。
            problem_text = _persist("" if clear_key else (key or None))
            if problem_text:
                return go("/settings?error=" + quote(problem_text))
            found = fetch_models(
                api_key=settings.ai_api_key or "",
                base_url=url,
                timeout=min(max(settings.ai_timeout_seconds, 5.0), 20.0),
            )
            if not found:
                return go("/settings?error=" + quote(f"拉取可用模型失败：{found.message}"))
            missing = name not in found.models
            if missing:
                note = (
                    f"当前模型「{name}」不在这个密钥的可用列表里（{found.message}），"
                    f"从下面选一个替换，例如 {found.models[0]}。"
                )
            else:
                note = f"{found.message}；当前模型「{name}」在列表里。"
            return render(
                request,
                "settings.html",
                **_settings_context(
                    request,
                    conn,
                    ai_models=found.models,
                    ai_models_note=note,
                    ai_model_missing=missing,
                ),
            )

        if action == "use_model":
            # 列表里的模型是现成的，密钥在「拉取」那一步已经存好，这里只换模型名
            problem_text = _persist(None)
            if problem_text:
                return go("/settings?error=" + quote(problem_text))
            result = probe_connection(
                api_key=settings.ai_api_key or "",
                base_url=url,
                model=name,
                timeout=min(max(settings.ai_timeout_seconds, 5.0), 20.0),
            )
            if result.ok:
                latency = f"，{result.latency_ms} ms" if result.latency_ms is not None else ""
                return go("/settings?msg=" + quote(f"已换成模型 {name}，试连成功{latency}"))
            return go(
                "/settings?error="
                + quote(f"已保存模型 {name}，但试连失败：{result.message}")
            )

        if clear_key:
            new_key: str | None = ""
        elif key:
            new_key = key
        else:
            new_key = None

        problem_text = _persist(new_key)
        if problem_text:
            return go("/settings?error=" + quote(problem_text))

        message = "已保存并立即生效（" + note + "）"
        if new_key is None:
            message += "；密钥未改动"
        elif new_key == "":
            message += "；已清除密钥，简报将使用本地文案"
        shadowed = ai_env_override_keys()
        if shadowed:
            message += (
                f"；注意：{'、'.join(shadowed)} 在当前进程环境变量里也有值，"
                "环境变量优先于 .env，这项改动可能不生效"
            )
        return go("/settings?msg=" + quote(message))

    # ---------------- 导出 ----------------

    @app.get("/export.csv")
    def export_csv_route(conn=Depends(get_db)):
        body = "\ufeff" + export_csv(conn)  # BOM 让 Excel 正确识别中文
        return Response(
            content=body,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="health_records.csv"'},
        )

    @app.get("/export.json")
    def export_json_route(conn=Depends(get_db)):
        return Response(
            content=export_json(conn),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="health_records.json"'},
        )

    return app
