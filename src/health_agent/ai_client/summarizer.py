"""报告文案改写：AI 可用就用，不可用就回本地模板。

三条边界：
1. 发给模型的是**已经算好的摘要**，不含 record_id、不含原始截图、不含账号信息。
2. 模型输出必须是约定 JSON；解析失败按失败处理，绝不把半成品塞进报告。
3. 任何异常都只影响"文案好不好读"，不影响记录、统计、提醒——本地功能照常。
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass

from ..reports.builder import ReportPayload
from ..storage.repository import now_iso
from .base import LLMClient, LLMError
from .cache import get_cached, make_cache_key, put_cached, summary_digest
from .http_client import ChatCompletionClient
from .prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt, parse_response

SOURCE_AI = "ai"
SOURCE_CACHE = "cache"
SOURCE_TEMPLATE = "template"

_REPORT_TYPE_LABEL = {"daily": "每日简报", "weekly": "每周趋势"}


@dataclass(frozen=True, slots=True)
class Narrative:
    text: str
    source: str
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None

    @property
    def is_ai(self) -> bool:
        return self.source in {SOURCE_AI, SOURCE_CACHE}


def build_summary(payload: ReportPayload) -> dict:
    """给模型的最小摘要。刻意不含 record_id 与任何可识别信息。"""
    metrics = []
    for item in payload.summaries:
        if item.sample_days == 0:
            continue
        metrics.append(
            {
                "指标": item.display,
                "单位": item.unit,
                "均值": item.mean,
                "最小": item.min,
                "最大": item.max,
                "有效天数": item.sample_days,
                "窗口天数": item.window_days,
                "数据状态": item.status,
            }
        )
    return {
        "报告类型": _REPORT_TYPE_LABEL.get(payload.report_type, payload.report_type),
        "区间": [payload.period_start, payload.period_end],
        "指标": metrics,
        "数据不足的指标": list(payload.gaps),
        "程序算好的数字": [number.display for number in payload.numbers],
        "本地模板文案": payload.text,
    }


def _log_call(
    conn: sqlite3.Connection,
    *,
    model: str,
    prompt_hash: str,
    status: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    latency_ms: int | None = None,
    error_code: str | None = None,
) -> None:
    total = None
    if input_tokens is not None or output_tokens is not None:
        total = (input_tokens or 0) + (output_tokens or 0)
    conn.execute(
        """
        INSERT INTO ai_call_log (
            call_id, task_type, model, prompt_version, prompt_hash,
            input_tokens, output_tokens, total_tokens,
            cost_value, cost_currency, cost_source, latency_ms, status, error_code, created_at
        ) VALUES (?, 'report_narrative', ?, ?, ?, ?, ?, ?, NULL, NULL, 'unavailable', ?, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex,
            model,
            PROMPT_VERSION,
            prompt_hash,
            input_tokens,
            output_tokens,
            total,
            latency_ms,
            status,
            error_code,
            now_iso(),
        ),
    )
    conn.commit()


def _keys(payload: ReportPayload, settings) -> tuple[str, str, str]:
    summary = build_summary(payload)
    digest = summary_digest(summary)
    model = settings.ai_model
    return digest, model, make_cache_key(digest=digest, prompt_version=PROMPT_VERSION, model=model)


def peek_cached(conn: sqlite3.Connection, payload: ReportPayload, *, settings) -> Narrative | None:
    """只读缓存，绝不发请求。

    页面渲染走这条路径：打开报告必须立刻出内容，不能卡在网络等待上。
    """
    digest, model, cache_key = _keys(payload, settings)
    cached = get_cached(conn, cache_key)
    if cached is None:
        return None
    return Narrative(text=str(cached["text"]), source=SOURCE_CACHE, model=model)


def rewrite_report(
    conn: sqlite3.Connection,
    payload: ReportPayload,
    *,
    settings,
    client: LLMClient | None = None,
) -> Narrative:
    """返回改写后的文案。调用方永远拿到可用的 text。"""
    summary = build_summary(payload)
    digest, model, cache_key = _keys(payload, settings)

    cached = get_cached(conn, cache_key)
    if cached is not None:
        return Narrative(text=str(cached["text"]), source=SOURCE_CACHE, model=model)

    if client is None:
        if not settings.ai_enabled:
            # 没有密钥：直接给本地文案，不产生任何调用与费用
            return Narrative(text=payload.text, source=SOURCE_TEMPLATE)
        client = ChatCompletionClient(
            api_key=settings.ai_api_key or "",
            base_url=settings.ai_base_url,
            model=model,
            timeout=settings.ai_timeout_seconds,
            max_retries=settings.ai_max_retries,
        )

    try:
        # 上限留足：推理模型会先"想"一轮，预算太紧会导致正文为空（正文只要求 120 字）
        result = client.complete(system=SYSTEM_PROMPT, user=build_user_prompt(summary), max_tokens=900)
        text = parse_response(result.text)
    except LLMError as exc:
        _log_call(
            conn,
            model=model,
            prompt_hash=digest,
            status="timeout" if exc.kind == "timeout" else "error",
            error_code=exc.kind,
        )
        return Narrative(text=payload.text, source=SOURCE_TEMPLATE, model=model, error=exc.kind)
    except (ValueError, TypeError) as exc:
        _log_call(conn, model=model, prompt_hash=digest, status="error", error_code="bad_output")
        return Narrative(
            text=payload.text, source=SOURCE_TEMPLATE, model=model, error=f"bad_output: {exc}"
        )

    _log_call(
        conn,
        model=result.model,
        prompt_hash=digest,
        status="ok",
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=result.latency_ms,
    )
    put_cached(
        conn,
        cache_key=cache_key,
        report_type=payload.report_type,
        period_start=payload.period_start,
        period_end=payload.period_end,
        digest=digest,
        text=text,
        model=result.model,
        prompt_version=PROMPT_VERSION,
    )
    return Narrative(
        text=text,
        source=SOURCE_AI,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
