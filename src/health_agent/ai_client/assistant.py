"""自由提问：模型只看"程序算好的数字"，只负责把话讲顺。

与报告改写同一套边界（规格 §7）：
1. 发出去的是聚合摘要，不含 record_id、不含截图路径、不含任何账号信息；
2. 模型不诊断、不给治疗方案、不预测风险——问到了也只会回答"这超出能力范围"；
3. 调用失败就明说失败并退回本地整理，绝不编一段话冒充答案；
4. 只有显式 POST 才花钱；GET 打开页面永远零网络。

为什么不做多轮对话：
- 多轮意味着要保存上下文，而上下文里就是健康数据；本地单人使用也犯不上为它建一张表；
- 一轮"问题 + 已算好的数字 → 一段回答"已经能覆盖"我看不懂这堆数字"这个真实需求。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from ..analytics.aggregate import summarize_many
from ..reports.builder import latest_data_date
from ..storage.repository import now_iso
from .base import LLMClient, LLMError
from .http_client import ChatCompletionClient

SOURCE_AI = "ai"
SOURCE_LOCAL = "local"

#: 一轮最多看这些指标：够回答问题，又不至于把提示词撑到几百行。
_FOCUS_METRICS: tuple[str, ...] = (
    "night_sleep_minutes",
    "nap_minutes",
    "sleep_avg_hr_bpm",
    "resting_hr_bpm",
    "hrv_avg_ms",
    "sleep_avg_spo2_percent",
    "respiratory_rate_avg_brpm",
    "stress_score",
    "training_index",
    "vo2max_ml_kg_min",
    "weight_kg",
    "steps_count",
)

MAX_QUESTION_CHARS = 500
MAX_ANSWER_CHARS = 2000

#: 首页与助手面板的快捷问题。点一下只是把文字填进输入框，不发请求。
QUICK_QUESTIONS: tuple[str, ...] = (
    "我这周睡得怎么样？",
    "最近有哪些数据缺失或者偏少？",
    "训练强度现在合适吗？",
    "哪些指标最近变化比较大？",
)

SYSTEM_PROMPT = """你是一个个人健康数据的解读助手。用户会给你一份**已经由程序算好的**健康数据摘要，以及一个自己的问题。

严格规则：
1. 只能使用摘要里出现的数字，禁止自己计算、估算、补全或引用常识区间。
2. 禁止疾病诊断、禁止治疗或用药建议、禁止预测任何风险。用户问到这些，直接说"这超出我的能力范围，建议问医生"。
3. 摘要里没有的项目就是"数据不足"，要如实说不足，不要猜、不要用别的指标顶替。
4. 语气平实，像同事帮你看数据；不夸张、不惊吓、不说教，不使用感叹号。
5. 回答控制在 250 字以内，用短段落，不要用表格、不要用 Markdown 标题。
6. 只输出回答正文。"""


@dataclass(frozen=True, slots=True)
class Answer:
    question: str
    text: str
    source: str = SOURCE_LOCAL
    model: str = ""
    note: str = ""
    error: str = ""
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    @property
    def is_ai(self) -> bool:
        return self.source == SOURCE_AI


def _round(value: float | None, unit: str) -> float | None:
    """机器精度不是人看的精度：整数语义取整，其余留一位小数。"""
    if value is None:
        return None
    if unit in {"min", "bpm", "count", "ms", "kcal", "brpm", "mL/kg/min"}:
        return round(value)
    return round(value, 1)


def build_context(conn: sqlite3.Connection, *, end: date | None = None) -> dict:
    """给模型的最小上下文。所有数字都由本函数调用现成的统计函数算好。"""
    last = latest_data_date(conn)
    end_day = end or last or date.today()
    start = end_day - timedelta(days=6)

    summaries = summarize_many(conn, _FOCUS_METRICS, start, end_day)
    metrics: list[dict] = []
    gaps: list[str] = []
    for item in summaries:
        if item.sample_days == 0:
            gaps.append(item.display)
            continue
        metrics.append(
            {
                "指标": item.display,
                "均值": _round(item.mean, item.unit),
                "最小": _round(item.min, item.unit),
                "最大": _round(item.max, item.unit),
                "单位": item.unit,
                "有效天数": f"{item.sample_days}/{item.window_days}",
                "数据状态": item.status,
            }
        )

    sleep = next((item for item in summaries if item.metric_name == "night_sleep_minutes"), None)
    daily_sleep = [
        {"日期": point.record_date, "分钟": _round(point.value, "min")}
        for point in (sleep.points if sleep else ())
    ]

    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM records WHERE verification_status = 'pending'"
    ).fetchone()["n"]
    total = conn.execute("SELECT COUNT(*) AS n FROM records").fetchone()["n"]

    return {
        "数据截至": end_day.isoformat(),
        "窗口": f"{start.isoformat()} ~ {end_day.isoformat()}（近 7 天）",
        "记录总数": total,
        "待确认记录": pending,
        "近 7 天指标": metrics,
        "近 7 天夜间睡眠逐日": daily_sleep,
        "数据不足的指标": gaps,
        "说明": "以上数字全部由程序统计得出；没有列出的项目即为数据不足，不得推断。",
    }


def _context_lines(context: dict) -> list[str]:
    """把上下文压成几条人能读的话，用作本地兜底答案。"""
    lines: list[str] = []
    daily = context["近 7 天夜间睡眠逐日"]
    if daily:
        values = [item["分钟"] for item in daily if item["分钟"] is not None]
        if values:
            average = round(sum(values) / len(values))
            lines.append(
                f"近 7 天有 {len(values)} 天夜间睡眠数据，平均 {average} 分钟"
                f"（约 {average // 60} 小时 {average % 60} 分），"
                f"最短 {min(values)} 分钟，最长 {max(values)} 分钟。"
            )
    if context["数据不足的指标"]:
        lines.append("数据不足的指标：" + "、".join(context["数据不足的指标"]) + "。")
    if context["待确认记录"]:
        lines.append(f"还有 {context['待确认记录']} 条记录等待确认，确认后才会进入统计。")
    lines.append("以上是本地整理，按数字直说，没有额外解读。")
    return lines


def _log_call(
    conn: sqlite3.Connection,
    *,
    model: str,
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
        ) VALUES (?, 'assistant_qa', ?, 'v1', NULL, ?, ?, ?, NULL, NULL, 'unavailable', ?, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex,
            model,
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


def _clean_answer(text: str) -> str:
    """去掉模型爱加的代码块外壳；过长就截断，避免把整段论文塞进页面。"""
    value = (text or "").strip()
    if value.startswith("```"):
        value = value.strip("`")
        if value.lower().startswith("text"):
            value = value[4:]
        value = value.strip()
    return value[:MAX_ANSWER_CHARS]


def ask(
    conn: sqlite3.Connection,
    question: str,
    *,
    settings,
    client: LLMClient | None = None,
) -> Answer:
    """回答一个问题。调用方永远拿到一段可显示的文字。"""
    cleaned = (question or "").strip()
    if not cleaned:
        return Answer(cleaned, "先写一句想问的吧，例如「我这周睡得怎么样」。", error="empty")
    if len(cleaned) > MAX_QUESTION_CHARS:
        cleaned = cleaned[:MAX_QUESTION_CHARS]

    context = build_context(conn)
    local_text = "\n".join(_context_lines(context))

    if client is None and not settings.ai_enabled:
        return Answer(
            cleaned,
            local_text,
            source=SOURCE_LOCAL,
            note="还没有配置 AI 接口，这里是本地整理；配置后同一个问题会得到更通顺的回答。",
        )

    if client is None:
        client = ChatCompletionClient(
            api_key=settings.ai_api_key or "",
            base_url=settings.ai_base_url,
            model=settings.ai_model,
            timeout=settings.ai_timeout_seconds,
            max_retries=settings.ai_max_retries,
        )

    prompt = (
        "以下是程序已经算好的数据摘要：\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
        + f"\n\n用户的问题：{cleaned}"
    )

    try:
        # 上限留足：推理模型的思考过程也吃 completion token，预算太紧会拿到空正文
        result = client.complete(system=SYSTEM_PROMPT, user=prompt, max_tokens=1200)
    except LLMError as exc:
        _log_call(
            conn,
            model=settings.ai_model,
            status="timeout" if exc.kind == "timeout" else "error",
            error_code=exc.kind,
        )
        detail = getattr(exc, "detail", "") or ""
        return Answer(
            cleaned,
            local_text,
            source=SOURCE_LOCAL,
            model=settings.ai_model,
            note=f"AI 调用失败（{detail or exc.kind}），这里是本地整理。",
            error=exc.kind,
        )
    except Exception as exc:  # noqa: BLE001 - 页面不能因为一次调用异常就 500
        _log_call(conn, model=settings.ai_model, status="error", error_code=type(exc).__name__)
        return Answer(
            cleaned,
            local_text,
            source=SOURCE_LOCAL,
            model=settings.ai_model,
            note=f"AI 调用异常（{type(exc).__name__}），这里是本地整理。",
            error="exception",
        )

    text = _clean_answer(result.text)
    if not text:
        _log_call(conn, model=result.model, status="error", error_code="empty")
        return Answer(
            cleaned,
            local_text,
            source=SOURCE_LOCAL,
            model=result.model,
            note="模型返回了空内容，这里是本地整理。",
            error="empty",
        )

    _log_call(
        conn,
        model=result.model,
        status="ok",
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=result.latency_ms,
    )
    return Answer(
        cleaned,
        text,
        source=SOURCE_AI,
        model=result.model,
        latency_ms=result.latency_ms,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
