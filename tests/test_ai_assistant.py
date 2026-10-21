"""AI 助手（自由提问）：零网络打开、显式提交才花钱、失败退回本地整理。

守四件事：
1. GET /assistant 不产生任何网络请求（规格 §7：阅读路径只读）；
2. 没配密钥也能用，回答是本地整理，且明说是本地整理；
3. 调用失败不能白屏，也不能编答案；
4. 发出去的摘要里不含 record_id、截图路径等可识别信息。
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from health_agent.ai_client.assistant import (
    MAX_QUESTION_CHARS,
    QUICK_QUESTIONS,
    SOURCE_AI,
    SOURCE_LOCAL,
    ask,
    build_context,
)
from health_agent.ai_client.base import LLMError, LLMResult
from health_agent.config import load_settings
from health_agent.storage.repository import insert_record
from health_agent.ui import app as ui_app
from health_agent.ui.app import create_app

from conftest import make_record


def _seeded(conn) -> None:
    """三天睡眠 + 一天压力，够 build_context 出内容。

    必须 `status="confirmed"`：待确认记录不进任何统计（规格 §5），
    这里要测的是"有数据时怎么说话"。
    """
    for day, minutes in (("2026-10-19", 344), ("2026-10-20", 296), ("2026-10-21", 136)):
        insert_record(
            conn,
            make_record(record_date=day, value=float(minutes)),
            status="confirmed",
        )
    insert_record(
        conn,
        make_record(metric_name="stress_score", record_date="2026-10-21", value=50.0, unit="score"),
        status="confirmed",
    )


def _client(tmp_path: Path) -> TestClient:
    settings = load_settings({}, project_root=tmp_path)
    app = create_app(settings, db_path=tmp_path / "assistant.db")
    return TestClient(app)


class _FakeClient:
    def __init__(self, *, text: str = "近三天平均 259 分钟。", raises: Exception | None = None) -> None:
        self.text = text
        self.raises = raises
        self.calls = 0

    def complete(self, *, system: str, user: str, max_tokens: int = 600) -> LLMResult:
        self.calls += 1
        self.last_user = user
        self.last_system = system
        if self.raises is not None:
            raise self.raises
        return LLMResult(text=self.text, model="fake-model", input_tokens=120, output_tokens=40, latency_ms=42)


def _settings_with_key(tmp_path: Path):
    (tmp_path / ".env").write_text("AI_API_KEY=sk-test-123456\n", encoding="utf-8")
    return load_settings({}, project_root=tmp_path)


# ---------------- 上下文：只给聚合数字 ----------------

def test_context_has_numbers_but_no_record_ids(conn):
    _seeded(conn)
    context = build_context(conn)
    text = json.dumps(context, ensure_ascii=False)
    assert "record_id" not in text
    assert "logical_key" not in text
    assert "batch-" not in text
    assert context["记录总数"] == 4
    daily = context["近 7 天夜间睡眠逐日"]
    assert [item["分钟"] for item in daily] == [344, 296, 136]


def test_context_says_what_is_missing_instead_of_zero(conn):
    _seeded(conn)
    context = build_context(conn)
    # 没录的指标进"数据不足"，不是补 0
    assert context["数据不足的指标"]
    for item in context["近 7 天指标"]:
        assert item["均值"] is not None


def test_context_on_empty_database_does_not_crash(conn):
    context = build_context(conn)
    assert context["近 7 天指标"] == []
    assert context["近 7 天夜间睡眠逐日"] == []
    assert context["记录总数"] == 0


# ---------------- ask() 本体 ----------------

def test_ask_without_key_returns_local_answer(conn, tmp_path):
    _seeded(conn)
    settings = load_settings({}, project_root=tmp_path)
    answer = ask(conn, "我这周睡得怎么样？", settings=settings)
    assert answer.source == SOURCE_LOCAL
    assert "本地整理" in answer.note
    assert "平均" in answer.text
    # 没配密钥就不该有任何调用记录
    assert conn.execute("SELECT COUNT(*) FROM ai_call_log").fetchone()[0] == 0


def test_ask_uses_the_model_and_logs_the_call(conn, tmp_path):
    _seeded(conn)
    settings = _settings_with_key(tmp_path)
    client = _FakeClient(text="近三天夜间睡眠平均 259 分钟。")
    answer = ask(conn, "这周平均睡多久？", settings=settings, client=client)
    assert answer.source == SOURCE_AI
    assert answer.text.startswith("近三天")
    assert answer.model == "fake-model"
    row = conn.execute("SELECT task_type, status, input_tokens, output_tokens FROM ai_call_log").fetchone()
    assert row["task_type"] == "assistant_qa"
    assert row["status"] == "ok"
    assert row["input_tokens"] == 120


def test_ask_strips_code_fence_from_model_output(conn, tmp_path):
    _seeded(conn)
    answer = ask(
        conn,
        "问题",
        settings=_settings_with_key(tmp_path),
        client=_FakeClient(text="```text\n正文在这里\n```"),
    )
    assert answer.text == "正文在这里"


def test_ask_degrades_when_model_fails(conn, tmp_path):
    _seeded(conn)
    exc = LLMError("http", "请求被拒绝 400", status_code=400, detail="Model Not Exist")
    answer = ask(conn, "问题", settings=_settings_with_key(tmp_path), client=_FakeClient(raises=exc))
    assert answer.source == SOURCE_LOCAL
    assert "失败" in answer.note
    assert "Model Not Exist" in answer.note
    assert answer.text, "失败也必须有可读内容，不能白屏"
    row = conn.execute("SELECT status, error_code FROM ai_call_log").fetchone()
    assert row["status"] == "error"


def test_ask_handles_unexpected_exception(conn, tmp_path):
    _seeded(conn)
    answer = ask(
        conn,
        "问题",
        settings=_settings_with_key(tmp_path),
        client=_FakeClient(raises=RuntimeError("boom")),
    )
    assert answer.source == SOURCE_LOCAL
    assert "RuntimeError" in answer.note


def test_ask_rejects_blank_question(conn, tmp_path):
    answer = ask(conn, "   ", settings=load_settings({}, project_root=tmp_path))
    assert answer.error == "empty"
    assert "想问" in answer.text


def test_ask_truncates_a_very_long_question(conn, tmp_path):
    _seeded(conn)
    answer = ask(conn, "问" * (MAX_QUESTION_CHARS + 200), settings=load_settings({}, project_root=tmp_path))
    assert len(answer.question) == MAX_QUESTION_CHARS


def test_system_prompt_forbids_diagnosis():
    from health_agent.ai_client.assistant import SYSTEM_PROMPT

    assert "禁止疾病诊断" in SYSTEM_PROMPT
    assert "只能使用摘要里出现的数字" in SYSTEM_PROMPT


# ---------------- 页面 ----------------

def test_assistant_page_needs_no_network(tmp_path, monkeypatch):
    """打开页面不许发请求（规格 §7）：真的被调用就抛异常。"""

    def boom(*args, **kwargs):
        raise AssertionError("GET /assistant 不应创建任何 AI 客户端")

    monkeypatch.setattr(ui_app, "ChatCompletionClient", boom, raising=False)
    response = _client(tmp_path).get("/assistant")
    assert response.status_code == 200
    assert "提问" in response.text
    assert "问一下" in response.text


def test_assistant_page_offers_quick_questions_as_prefill_links(tmp_path):
    response = _client(tmp_path).get("/assistant")
    for question in QUICK_QUESTIONS:
        assert question in response.text


def test_assistant_page_shows_local_hint_without_key(tmp_path):
    response = _client(tmp_path).get("/assistant")
    assert "还没有配置 AI 接口" in response.text


def test_assistant_post_without_key_answers_locally(tmp_path, monkeypatch):
    seen: dict[str, str] = {}

    def fake_ask(conn, question, *, settings):
        from health_agent.ai_client.assistant import Answer

        seen["question"] = question
        return Answer(question, "本地整理的正文", note="这是本地整理")

    monkeypatch.setattr(ui_app, "assistant_ask", fake_ask)
    client = _client(tmp_path)
    response = client.post("/assistant", data={"question": "我这周睡得怎么样"}, follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/assistant?a=")
    page = client.get(location)
    assert "本地整理的正文" in page.text
    assert seen["question"] == "我这周睡得怎么样"


def test_assistant_post_with_blank_question_redirects_with_error(tmp_path):
    client = _client(tmp_path)
    response = client.post("/assistant", data={"question": "  "}, follow_redirects=False)
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert "想问" in unquote(response.headers["location"])


def test_assistant_unknown_answer_token_renders_cleanly(tmp_path):
    response = _client(tmp_path).get("/assistant?a=doesnotexist")
    assert response.status_code == 200
    assert "<h2>回答</h2>" not in response.text


def test_assistant_stores_at_most_twenty_answers(tmp_path, monkeypatch):
    from health_agent.ai_client.assistant import Answer

    monkeypatch.setattr(
        ui_app,
        "assistant_ask",
        lambda conn, question, *, settings: Answer(question, "答", note="本地整理"),
    )
    client = _client(tmp_path)
    oldest = client.post("/assistant", data={"question": "第一问"}, follow_redirects=False)
    for _ in range(25):
        client.post("/assistant", data={"question": "问"}, follow_redirects=False)
    # 只留最近 20 条：最早那条应当已经滚出内存，页面按"没有答案"渲染而不是报错
    assert "第一问" not in client.get(oldest.headers["location"]).text
    latest = client.post("/assistant", data={"question": "最后一问"}, follow_redirects=False)
    assert "最后一问" in client.get(latest.headers["location"]).text
