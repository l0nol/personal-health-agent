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
    assert "只能使用摘要与历史对话里出现过的数字" in SYSTEM_PROMPT


# ---------------- 页面 ----------------

def test_assistant_page_needs_no_network(tmp_path, monkeypatch):
    """打开页面不许发请求（规格 §7）：真的被调用就抛异常。"""

    def boom(*args, **kwargs):
        raise AssertionError("GET /assistant 不应创建任何 AI 客户端")

    monkeypatch.setattr(ui_app, "ChatCompletionClient", boom, raising=False)
    response = _client(tmp_path).get("/assistant")
    assert response.status_code == 200
    assert "继续提问" in response.text
    assert "发送" in response.text


def test_assistant_page_offers_quick_questions_as_prefill_links(tmp_path):
    response = _client(tmp_path).get("/assistant")
    for question in QUICK_QUESTIONS:
        assert question in response.text


def test_assistant_page_shows_local_hint_without_key(tmp_path):
    response = _client(tmp_path).get("/assistant")
    assert "还没有配置 AI 接口" in response.text


def test_assistant_post_without_key_answers_locally(tmp_path, monkeypatch):
    seen: dict[str, object] = {}

    def fake_ask(conn, question, *, settings, history=None):
        from health_agent.ai_client.assistant import Answer

        seen["question"] = question
        seen["history"] = history
        return Answer(question, "本地整理的正文", note="这是本地整理")

    monkeypatch.setattr(ui_app, "assistant_ask", fake_ask)
    client = _client(tmp_path)
    response = client.post("/assistant", data={"question": "我这周睡得怎么样"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith("#bottom")
    page = client.get("/assistant")
    assert "本地整理的正文" in page.text
    assert "我这周睡得怎么样" in page.text
    assert seen["question"] == "我这周睡得怎么样"


def test_assistant_post_with_blank_question_redirects_with_error(tmp_path):
    client = _client(tmp_path)
    response = client.post("/assistant", data={"question": "  "}, follow_redirects=False)
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert "想问" in unquote(response.headers["location"])




def test_conversation_survives_a_restart(tmp_path, monkeypatch):
    """对话落库：换一个 client（等价于重启进程）还能看到之前的一问一答。"""
    from health_agent.ai_client.assistant import Answer

    monkeypatch.setattr(
        ui_app,
        "assistant_ask",
        lambda conn, question, *, settings, history=None: Answer(question, "答：" + question, note="本地整理"),
    )
    db = tmp_path / "assistant.db"
    settings = load_settings({}, project_root=tmp_path)
    first = TestClient(create_app(settings, db_path=db))
    first.post("/assistant", data={"question": "第一个问题"}, follow_redirects=False)

    second = TestClient(create_app(load_settings({}, project_root=tmp_path), db_path=db))
    page = second.get("/assistant")
    assert "第一个问题" in page.text
    assert "答：第一个问题" in page.text


def test_conversation_keeps_only_the_last_turns(tmp_path):
    from health_agent.ai_client import chat_store
    from health_agent.storage.db import connect, init_db
    from health_agent.storage.repository import seed_catalog

    conn = init_db(tmp_path / "chat.db")
    seed_catalog(conn)
    for i in range(chat_store.MAX_TURNS + 6):
        chat_store.append(conn, {"role": "user", "text": f"问题{i}"})
    turns = chat_store.load(conn)
    assert len(turns) == chat_store.MAX_TURNS
    assert turns[-1]["text"] == f"问题{chat_store.MAX_TURNS + 5}"
    assert "问题0" not in [turn["text"] for turn in turns]


def test_clear_wipes_the_conversation(tmp_path, monkeypatch):
    from health_agent.ai_client.assistant import Answer

    monkeypatch.setattr(
        ui_app,
        "assistant_ask",
        lambda conn, question, *, settings, history=None: Answer(question, "答", note="本地整理"),
    )
    client = _client(tmp_path)
    client.post("/assistant", data={"question": "会被清掉"}, follow_redirects=False)
    assert "会被清掉" in client.get("/assistant").text
    client.post("/assistant/clear", follow_redirects=False)
    assert "会被清掉" not in client.get("/assistant").text


def test_history_is_passed_to_the_model(tmp_path, monkeypatch):
    """第二轮提问要把第一轮带上，否则"那上周呢"这种追问无从理解。"""
    from health_agent.ai_client.assistant import Answer

    seen: list[object] = []

    def fake_ask(conn, question, *, settings, history=None):
        seen.append(history)
        return Answer(question, "答", note="本地整理")

    monkeypatch.setattr(ui_app, "assistant_ask", fake_ask)
    client = _client(tmp_path)
    client.post("/assistant", data={"question": "第一问"}, follow_redirects=False)
    client.post("/assistant", data={"question": "那上周呢"}, follow_redirects=False)
    assert seen[0] == [] or seen[0] is None
    assert seen[1][0] == {"role": "user", "content": "第一问"}
    assert seen[1][1]["role"] == "assistant"


def test_ask_sends_the_whole_history_when_client_supports_chat(conn, tmp_path):
    """有 chat() 就发消息数组；历史在前、本轮在最后，system 永远第一条。"""
    _seeded(conn)
    captured: dict[str, object] = {}

    class ChattyClient:
        def chat(self, messages, *, max_tokens=600):
            captured["messages"] = messages
            captured["max_tokens"] = max_tokens
            return LLMResult(text="好的", model="chatty")

        def complete(self, **kwargs):  # pragma: no cover - 有 chat 就不该走这里
            raise AssertionError("有 chat() 时不应退回 complete()")

    history = [
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
    ]
    answer = ask(
        conn,
        "那上周呢",
        settings=_settings_with_key(tmp_path),
        client=ChattyClient(),
        history=history,
    )
    messages = captured["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1] == {"role": "user", "content": "第一问"}
    assert messages[2] == {"role": "assistant", "content": "第一答"}
    assert messages[-1]["role"] == "user"
    assert "那上周呢" in messages[-1]["content"]
    assert answer.source == SOURCE_AI


def test_client_without_chat_falls_back_to_single_turn(conn, tmp_path):
    """只实现 complete() 的客户端（如测试替身）也能用，行为退回单轮。"""
    _seeded(conn)
    client = _FakeClient(text="单轮回答")
    answer = ask(
        conn,
        "随便问问",
        settings=_settings_with_key(tmp_path),
        client=client,
        history=[{"role": "user", "content": "旧问题"}],
    )
    assert answer.text == "单轮回答"
    assert client.calls == 1


# ---------------- 对话里"取数"与"跳转"（本地确定性实现） ----------------

def test_window_words_are_understood(conn):
    from health_agent.ai_client.tools import guess_window

    _seeded(conn)
    assert guess_window("最近的睡眠", conn).end.isoformat() == "2026-10-21"
    assert "近 7 天" in guess_window("这周睡得怎么样", conn).label
    assert "近 30 天" in guess_window("体重最近 30 天", conn).label
    assert "近 14 天" in guess_window("近两周的心率", conn).label
    week = guess_window("上周呢", conn)
    assert week.end.isoformat() == "2026-10-14"
    assert (week.end - week.start).days == 6
    today = guess_window("今天的睡眠", conn)
    assert today.start == today.end


def test_metric_words_are_understood():
    from health_agent.ai_client.tools import guess_metric

    assert guess_metric("我这周睡得怎么样").name == "night_sleep_minutes"
    assert guess_metric("睡眠心率是多少").name == "sleep_avg_hr_bpm"
    assert guess_metric("体重最近 30 天有变化吗").name == "weight_kg"
    assert guess_metric("HRV 怎么样").name == "hrv_avg_ms"
    assert guess_metric("今天天气不错") is None


def test_page_words_become_links():
    from health_agent.ai_client.tools import guess_metric, page_links

    links = page_links("打开趋势页看看", None)
    assert any(link["href"] == "/trends" for link in links)
    assert any("/reports" in link["href"] for link in page_links("我想看报告", None))
    metric = guess_metric("体重趋势")
    links = page_links("体重趋势", metric)
    assert any("metric=weight_kg" in link["href"] for link in links)


def test_card_is_computed_locally(conn):
    from health_agent.ai_client.tools import guess_window, metric_card
    from health_agent.catalog import get_metric

    _seeded(conn)
    window = guess_window("这周睡眠", conn)
    card = metric_card(conn, get_metric("night_sleep_minutes"), window)
    assert card is not None
    assert card["days"] == 3
    assert card["max"] == 344
    assert [point["v"] for point in card["points"]] == [344, 296, 136]


def test_card_is_none_when_nothing_recorded(conn):
    from health_agent.ai_client.tools import guess_window, metric_card
    from health_agent.catalog import get_metric

    window = guess_window("体重最近 30 天", conn)
    assert metric_card(conn, get_metric("weight_kg"), window) is None


def test_answer_carries_card_and_links(conn, tmp_path):
    _seeded(conn)
    answer = ask(
        conn,
        "体重最近 30 天有变化吗？打开趋势页",
        settings=load_settings({}, project_root=tmp_path),
    )
    assert answer.card is None  # 没录体重，不硬凑卡片
    assert any(link["href"] == "/trends" for link in answer.refs)
    assert "本地整理" in answer.note


def test_answer_card_reaches_the_prompt(conn, tmp_path):
    _seeded(conn)
    captured: dict[str, object] = {}

    class ChattyClient:
        def chat(self, messages, *, max_tokens=600):
            captured["messages"] = messages
            return LLMResult(text="好", model="m")

    ask(
        conn,
        "这周睡眠怎么样",
        settings=_settings_with_key(tmp_path),
        client=ChattyClient(),
        history=[],
    )
    payload = captured["messages"][-1]["content"]
    assert "本次聚焦" in payload
    assert "344" in payload


# ---------------- 训练类问题：必须带上「今日建议」 ----------------

def _seed_recent_sleep(conn, *, days: int = 7, minutes: float = 420.0) -> None:
    """把最近 N 天的夜间睡眠补成已确认，让规则引擎能给出"常规训练"。"""
    from datetime import date, timedelta

    today = date.today()
    for offset in range(days):
        day = (today - timedelta(days=offset)).isoformat()
        insert_record(
            conn,
            make_record(record_date=day, value=minutes),
            status="confirmed",
        )


def test_context_carries_todays_guidance(conn):
    """问"今天练什么"只能靠「今日建议」回答——指标摘要里没有"该不该练"。"""
    _seed_recent_sleep(conn)
    context = build_context(conn)

    advice = context["今日建议"]
    assert advice["类别"] == "regular_training"
    assert advice["结论"]
    assert advice["依据"], "建议必须带依据，否则模型只能空口说"
    assert "近 14 天次数" in context["最近训练"]
    assert context["今日主观状态"]


def test_guidance_is_reflected_in_local_answer(conn):
    """没配 AI 时也要能回答"今天练什么"，而不是只会说数据不足。"""
    _seed_recent_sleep(conn)
    settings = load_settings({}, project_root=Path("__no_env__"))
    answer = ask(conn, "我今天想要健身适合练什么", settings=settings)

    assert answer.source == SOURCE_LOCAL
    assert "可以按计划训练" in answer.text
    assert "今日建议" in answer.text or "常规训练" in answer.text


def test_workout_question_resolves_to_training_page(conn):
    """口语里的"健身/练什么"要能命中训练页，否则连跳转都不给。"""
    from health_agent.ai_client.tools import resolve

    _seed_recent_sleep(conn)
    _card, refs = resolve(conn, "我今天想要健身适合练什么")
    assert any(ref.get("href") == "/training" for ref in refs)


def test_prompt_tells_model_to_ignore_stale_history():
    """历史里的旧回答可能是在没数据时说的；模型必须按本次摘要纠正，而不是沿用旧结论。"""
    from health_agent.ai_client.assistant import SYSTEM_PROMPT

    assert "以本次摘要为准" in SYSTEM_PROMPT


def test_prompt_tells_model_to_ignore_stale_history():
    """历史里的旧回答可能是在没数据时说的；模型必须按本次摘要纠正，而不是沿用旧结论。"""
    from health_agent.ai_client.assistant import SYSTEM_PROMPT

    assert "以本次摘要为准" in SYSTEM_PROMPT
