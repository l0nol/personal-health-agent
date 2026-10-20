"""FastAPI 应用：服务端渲染的本地界面。

页面只做四件事：看记录、确认记录、导入、裁决冲突。所有写操作都走 POST + CSRF，
所有依赖健康数据的判断都在 service 层，模板里不放业务规则。
"""

from __future__ import annotations

import hmac
import json
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__
from ..ai_client.summarizer import SOURCE_TEMPLATE, Narrative, peek_cached, rewrite_report
from ..analytics.aggregate import load_points, range_days, summarize, weekly_buckets
from ..analytics.baseline import compute_baseline
from ..analytics.quality import summarize_quality
from ..catalog import METRICS, RANGE_BOUND_METRICS, get_metric
from ..config import Settings, load_settings
from ..ingestion.batch import commit_import
from ..ingestion.loader import LoadError, file_sha256, load_rows
from ..ingestion.preview import build_preview, render_text
from ..storage import lifecycle, preferences, repository
from ..storage.db import connect, current_version, init_db
from ..reports.builder import build_daily, build_weekly, latest_data_date
from ..reports.format import format_number
from ..rules.engine import config_from_settings, describe_sleep_target, run as run_rules
from .charts import BandPoint, SeriesPoint, build_band, build_bars, build_line

METRICS_BY_NAME = {metric.name: metric for metric in METRICS}
from ..storage import alerts as alert_store
from ..storage.exporting import delete_record, export_csv, export_json
from .auth import SESSION_COOKIE, SessionSigner, load_secret, verify_password

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

def _metric_labels() -> dict[str, str]:
    return {metric.name: metric.display for metric in METRICS}


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


def _reference(conn, metric_name: str, end: date) -> dict:
    """指标卡"参照范围"行的数据：个人基线动态计算。

    规则（T03 设计契约，用户 2026-10-20 裁决）：
    - 基线可用 → 中位数 ± mad_factor×离散度构成的个人区间；
    - 样本不足 / 离散度为 0 → 只说"数据不足"并给出原因，绝不拿默认值冒充个性化；
    - 没有任何记录 → "暂无数据"；
    - 区间端点指标（range_bound）→ 不显示参照范围。

    这里只做数据装配，判断全在 analytics.baseline；格式化交给模板的 human 过滤器。
    """
    if get_metric(metric_name).kind == "range_bound":
        return {"state": "range_bound", "unit": get_metric(metric_name).unit, "metric": metric_name}

    baseline = compute_baseline(conn, metric_name, end)
    unit = get_metric(metric_name).unit
    if baseline.status != "active":
        state = "no_data" if baseline.sample_count == 0 else "insufficient"
        return {
            "state": state,
            "reason": baseline.reason,
            "sample_count": baseline.sample_count,
            "min_samples": baseline.min_samples,
            "unit": unit,
            "metric": metric_name,
        }
    return {
        "state": "active",
        "low": baseline.center - baseline.threshold,
        "high": baseline.center + baseline.threshold,
        "center": baseline.center,
        "threshold": baseline.threshold,
        "method": baseline.method,
        "mad_factor": baseline.mad_factor,
        "window_start": baseline.window_start,
        "window_end": baseline.window_end,
        "window_days": baseline.window_days,
        "sample_count": baseline.sample_count,
        "unit": unit,
        "metric": metric_name,
    }


def _dashboard_tiles(conn, end: date) -> list[dict]:
    """首页与时钟模式共用的指标卡数据（同一来源、同一窗口、同一格式化）。

    T05 铁律"表盘中心只显示与简洁模式相同的数值"靠这一个函数保证：
    两处路由都调它，不做第二套口径；要改口径只改这里。
    """
    start = end - timedelta(days=6)
    tiles = []
    for name, href in (
        ("night_sleep_minutes", "/sleep"),
        ("resting_hr_bpm", "/trends?metric=resting_hr_bpm"),
        ("steps_count", "/trends?metric=steps_count"),
        ("activity_energy_kcal", "/trends?metric=activity_energy_kcal"),
    ):
        metric_summary = summarize(conn, name, start, end)
        latest_point = metric_summary.points[-1] if metric_summary.points else None
        tiles.append(
            {
                "metric": name,
                "label": get_metric(name).display,
                "value": latest_point.value if latest_point else None,
                "date": latest_point.record_date if latest_point else None,
                "unit": get_metric(name).unit,
                "days": metric_summary.sample_days,
                "status": metric_summary.status,
                "href": href,
                "reference": _reference(conn, name, end),
            }
        )
    return tiles


def _chronos_sectors(tiles, counts, conflicts, unread, end: date) -> list[dict]:
    """表盘六扇区：预览值全部取自 _dashboard_tiles / 计数，不新算任何数。"""
    sleep, hr = tiles[0], tiles[1]

    def foot(tile) -> str:
        return f"近 7 天有效 {tile['days']}/7 天" if tile["days"] else "近 7 天无数据"

    return [
        {"key": "sleep", "idx": 0, "label": "睡眠", "href": "/sleep",
         "kicker": "昨夜睡眠", "sub": foot(sleep),
         "value_text": format_number(sleep["value"], sleep["unit"], sleep["metric"])},
        {"key": "trends", "idx": 1, "label": "趋势", "href": "/trends",
         "kicker": "静息心率", "sub": foot(hr),
         "value_text": format_number(hr["value"], hr["unit"], hr["metric"])},
        {"key": "imports", "idx": 2, "label": "导入", "href": "/imports",
         "kicker": "待确认记录", "sub": "写入后仍需逐条确认",
         "value_text": f"{counts['pending']} 条"},
        {"key": "alerts", "idx": 3, "label": "提醒", "href": "/alerts",
         "kicker": "未读提醒", "sub": "同一窗口不重复 · 冷却 72 小时",
         "value_text": f"{unread} 条"},
        {"key": "reports", "idx": 4, "label": "报告", "href": "/reports",
         "kicker": "已确认记录", "sub": f"数据截至 {end.isoformat()}",
         "value_text": f"{counts['confirmed']} 条"},
        {"key": "settings", "idx": 5, "label": "设置", "href": "/settings",
         "kicker": "界面模式", "sub": "数据只在本机存储",
         "value_text": "时钟"},
    ]


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
        context.setdefault("csrf", getattr(request.state, "csrf", ""))
        context.setdefault("active", request.url.path)
        context.setdefault("msg", request.query_params.get("msg"))
        context.setdefault("error", request.query_params.get("error"))
        context.setdefault("labels", _metric_labels())
        context.setdefault("show_logout", settings.password_hash is not None)
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
        tiles = _dashboard_tiles(conn, end)
        sleep_chart = build_bars(_series(conn, "night_sleep_minutes", start, end),
                                 target=float(config.sleep_target_minutes), height=150)
        if mode == "chronos":
            # 与 /chronos 路由同一套上下文（tiles/sectors 同源），否则表盘会空转
            return render(
                request,
                "chronos.html",
                mode=mode,
                counts=counts,
                conflicts=conflicts,
                unread_alerts=unread_alerts,
                tiles=tiles,
                sectors=_chronos_sectors(tiles, counts, conflicts, unread_alerts, end),
                end_day=end.isoformat(),
            )
        recent = _items(repository.list_records(conn, limit=10))
        batches = conn.execute(
            "SELECT * FROM import_batches ORDER BY imported_at DESC LIMIT 5"
        ).fetchall()
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
            reference=_reference(conn, "night_sleep_minutes", end),
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
            reference=_reference(conn, metric, end),
            end_day=end.isoformat(),
        )

    @app.get("/chronos", response_class=HTMLResponse)
    def chronos(request: Request, conn=Depends(get_db)):
        end = latest_data_date(conn) or date.today()
        tiles = _dashboard_tiles(conn, end)
        counts = {
            "pending": repository.count_records(conn, status="pending"),
            "confirmed": repository.count_records(conn, status="confirmed"),
        }
        conflicts = conn.execute(
            "SELECT COUNT(*) AS n FROM conflicts WHERE resolution = 'pending'"
        ).fetchone()["n"]
        unread = alert_store.count_unread(conn)
        return render(
            request,
            "chronos.html",
            mode=ui_mode(conn),
            tiles=tiles,
            sectors=_chronos_sectors(tiles, counts, conflicts, unread, end),
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

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, conn=Depends(get_db)):
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
        return render(
            request,
            "settings.html",
            values=preferences.get_settings(conn),
            keys=preferences.ALLOWED_KEYS,
            ai_enabled=settings.ai_enabled,
            lan_enabled=settings.lan_enabled,
            sleep_target=describe_sleep_target(config_from_settings(settings)),
            mode=ui_mode(conn),
            thresholds=thresholds,
            app_version=__version__,
            schema_version=current_version(conn),
            db_path=str(request.app.state.db_path),
        )

    @app.post("/settings")
    async def settings_save(request: Request, conn=Depends(get_db)):
        form = await request.form()
        values = {key: str(form.get(key, "")) for key in preferences.ALLOWED_KEYS}
        preferences.set_settings(conn, values)
        return go("/settings?msg=" + quote("已保存（留空表示不设置）"))

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
