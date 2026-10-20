"""AI 层：能改写就用 AI，任何失败都必须退回本地文案，且不泄露密钥。"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from health_agent.ai_client.base import LLMError, LLMResult
from health_agent.ai_client.http_client import ChatCompletionClient
from health_agent.ai_client.summarizer import (
    SOURCE_AI,
    SOURCE_CACHE,
    SOURCE_TEMPLATE,
    build_summary,
    peek_cached,
    rewrite_report,
)
from health_agent.config import load_settings
from health_agent.reports.builder import build_daily
from health_agent.storage.lifecycle import confirm_record
from health_agent.storage.repository import insert_record

from conftest import make_record

DAY = date(2026, 10, 18)


class FakeClient:
    def __init__(self, *, text='{"text": "由模型改写的一段话"}', error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls = 0
        self.last_user_prompt = ""

    def complete(self, *, system: str, user: str, max_tokens: int = 600) -> LLMResult:
        self.calls += 1
        self.last_user_prompt = user
        if self.error is not None:
            raise self.error
        return LLMResult(text=self.text, model="fake-model", input_tokens=120, output_tokens=30)


def seed(conn) -> None:
    for offset in range(7):
        record_id = insert_record(
            conn,
            make_record(record_date=f"2026-10-{12 + offset:02d}", value=400.0 + offset),
            status="pending",
        )
        confirm_record(conn, record_id)


def settings_with_key(tmp_path):
    return load_settings({"DEEPSEEK_API_KEY": "sk-test-should-not-leak"}, project_root=tmp_path)


def settings_without_key(tmp_path):
    return load_settings({}, project_root=tmp_path)


# ---------------- 降级 ----------------

def test_without_key_returns_template_text(conn, tmp_path):
    seed(conn)
    payload = build_daily(conn, DAY)
    narrative = rewrite_report(conn, payload, settings=settings_without_key(tmp_path))

    assert narrative.source == SOURCE_TEMPLATE
    assert narrative.text == payload.text


def test_without_key_makes_no_call_and_logs_nothing(conn, tmp_path):
    seed(conn)
    payload = build_daily(conn, DAY)
    rewrite_report(conn, payload, settings=settings_without_key(tmp_path))

    assert conn.execute("SELECT COUNT(*) AS n FROM ai_call_log").fetchone()["n"] == 0


def test_ai_text_replaces_template(conn, tmp_path):
    seed(conn)
    payload = build_daily(conn, DAY)
    fake = FakeClient()
    narrative = rewrite_report(conn, payload, settings=settings_with_key(tmp_path), client=fake)

    assert narrative.source == SOURCE_AI
    assert narrative.text == "由模型改写的一段话"
    assert fake.calls == 1


def test_timeout_falls_back_with_template(conn, tmp_path):
    seed(conn)
    payload = build_daily(conn, DAY)
    fake = FakeClient(error=LLMError("timeout", "请求超时"))
    narrative = rewrite_report(conn, payload, settings=settings_with_key(tmp_path), client=fake)

    assert narrative.source == SOURCE_TEMPLATE
    assert narrative.text == payload.text
    assert narrative.error == "timeout"


def test_bad_model_output_falls_back(conn, tmp_path):
    seed(conn)
    payload = build_daily(conn, DAY)
    fake = FakeClient(text="抱歉，我无法完成")
    narrative = rewrite_report(conn, payload, settings=settings_with_key(tmp_path), client=fake)

    assert narrative.source == SOURCE_TEMPLATE
    assert narrative.text == payload.text
    assert narrative.error and narrative.error.startswith("bad_output")


def test_empty_text_is_rejected(conn, tmp_path):
    seed(conn)
    payload = build_daily(conn, DAY)
    fake = FakeClient(text='{"text": "   "}')
    narrative = rewrite_report(conn, payload, settings=settings_with_key(tmp_path), client=fake)

    assert narrative.source == SOURCE_TEMPLATE


# ---------------- 缓存 ----------------

def test_second_call_uses_cache(conn, tmp_path):
    seed(conn)
    payload = build_daily(conn, DAY)
    fake = FakeClient()
    settings = settings_with_key(tmp_path)

    first = rewrite_report(conn, payload, settings=settings, client=fake)
    second = rewrite_report(conn, payload, settings=settings, client=fake)

    assert first.source == SOURCE_AI
    assert second.source == SOURCE_CACHE
    assert fake.calls == 1
    assert second.text == first.text


def test_changed_data_produces_new_cache_key(conn, tmp_path):
    seed(conn)
    payload = build_daily(conn, DAY)
    fake = FakeClient()
    settings = settings_with_key(tmp_path)

    rewrite_report(conn, payload, settings=settings, client=fake)
    other = build_daily(conn, date(2026, 10, 17))
    rewrite_report(conn, other, settings=settings, client=fake)

    assert fake.calls == 2


# ---------------- 日志与隐私 ----------------

def test_successful_call_logs_tokens_without_cost(conn, tmp_path):
    seed(conn)
    payload = build_daily(conn, DAY)
    rewrite_report(conn, payload, settings=settings_with_key(tmp_path), client=FakeClient())

    row = conn.execute("SELECT * FROM ai_call_log ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row["status"] == "ok"
    assert row["input_tokens"] == 120
    assert row["output_tokens"] == 30
    assert row["total_tokens"] == 150
    assert row["cost_source"] == "unavailable"
    assert row["cost_value"] is None


def test_failed_call_is_logged_with_error_code(conn, tmp_path):
    seed(conn)
    payload = build_daily(conn, DAY)
    rewrite_report(
        conn,
        payload,
        settings=settings_with_key(tmp_path),
        client=FakeClient(error=LLMError("timeout", "超时")),
    )

    row = conn.execute("SELECT * FROM ai_call_log ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row["status"] == "timeout"
    assert row["error_code"] == "timeout"


def test_summary_sent_to_model_contains_no_record_ids(conn, tmp_path):
    seed(conn)
    payload = build_daily(conn, DAY)
    fake = FakeClient()
    rewrite_report(conn, payload, settings=settings_with_key(tmp_path), client=fake)

    sent = json.loads(fake.last_user_prompt.split("\n", 1)[1])
    dumped = json.dumps(sent, ensure_ascii=False)
    assert "record_id" not in dumped
    for record_id in [number for n in payload.numbers for number in n.record_ids]:
        assert record_id not in dumped


def test_repr_does_not_leak_api_key():
    client = ChatCompletionClient(
        api_key="sk-secret-value",
        base_url="https://api.example/v1",
        model="demo",
    )
    assert "sk-secret-value" not in repr(client)


# ---------------- HTTP 客户端 ----------------

def _ok_response(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


def test_http_client_parses_content_and_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok_response(
            {
                "model": "demo-model",
                "choices": [{"message": {"content": '{"text": "好的"}'}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 10},
            }
        )

    client = ChatCompletionClient(
        api_key="sk-x",
        base_url="https://api.example/v1",
        model="demo-model",
        transport=httpx.MockTransport(handler),
    )
    result = client.complete(system="s", user="u")

    assert result.text == '{"text": "好的"}'
    assert result.input_tokens == 50
    assert result.output_tokens == 10
    assert result.total_tokens == 60


def test_http_client_retries_on_server_error():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500, json={"error": "boom"})

    client = ChatCompletionClient(
        api_key="sk-x",
        base_url="https://api.example/v1",
        model="demo",
        max_retries=1,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LLMError):
        client.complete(system="s", user="u")
    assert len(calls) == 2, "5xx 应当重试一次"


def test_http_client_does_not_retry_on_auth_error():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, json={"error": "unauthorized"})

    client = ChatCompletionClient(
        api_key="sk-x",
        base_url="https://api.example/v1",
        model="demo",
        max_retries=2,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LLMError):
        client.complete(system="s", user="u")
    assert len(calls) == 1, "4xx 是配置问题，重试无意义"


def test_http_client_requires_key():
    with pytest.raises(ValueError):
        ChatCompletionClient(api_key="", base_url="https://api.example/v1", model="demo")


# ---------------- 只读缓存（页面渲染路径） ----------------

def test_peek_cached_returns_none_when_empty(conn, tmp_path):
    seed(conn)
    payload = build_daily(conn, DAY)

    assert peek_cached(conn, payload, settings=settings_with_key(tmp_path)) is None


def test_peek_cached_returns_result_after_summarize(conn, tmp_path):
    seed(conn)
    payload = build_daily(conn, DAY)
    settings = settings_with_key(tmp_path)
    rewrite_report(conn, payload, settings=settings, client=FakeClient())

    cached = peek_cached(conn, payload, settings=settings)

    assert cached is not None
    assert cached.source == SOURCE_CACHE
    assert cached.text == "由模型改写的一段话"


def test_peek_cached_never_calls_model(conn, tmp_path):
    seed(conn)
    payload = build_daily(conn, DAY)
    fake = FakeClient()
    peek_cached(conn, payload, settings=settings_with_key(tmp_path))

    assert fake.calls == 0
