"""AI 接口配置：预设解析、`.env` 往返、页面保存与试连。

重点守四件事：
1. 老 `.env`（`DEEPSEEK_*`）不因为改名而失效；
2. 界面上"留空"不等于"清空"——这是最容易误伤用户数据的一处；
3. 密钥永远不出现在页面、日志或导出里；
4. 试连失败不能把整页打挂，也不能偷偷发请求（GET 必须零网络）。
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from health_agent.ai_client.probe import ProbeResult, probe_connection
from health_agent.config import (
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
from health_agent.errors import ConfigError
from health_agent.ui import app as ui_app
from health_agent.ui.app import create_app


def _client(tmp_path: Path, *, env: dict[str, str] | None = None) -> TestClient:
    settings = load_settings(env or {}, project_root=tmp_path)
    app = create_app(settings, db_path=tmp_path / "ai.db")
    return TestClient(app)


# ---------------- 预设与解析 ----------------

def test_presets_are_complete_and_unique():
    keys = [preset["key"] for preset in AI_PRESETS]
    assert len(keys) == len(set(keys))
    assert "deepseek" in keys
    for preset in AI_PRESETS:
        for field in ("key", "label", "base_url", "model", "hint"):
            assert field in preset, preset
        assert preset["base_url"].startswith(("http://", "https://"))


def test_preset_by_key():
    assert preset_by_key("deepseek")["model"] == "deepseek-chat"
    assert preset_by_key("没这个") is None


def test_active_preset_key_matches_trailing_slash(tmp_path):
    deepseek = preset_by_key("deepseek")
    settings = load_settings(
        {"AI_BASE_URL": deepseek["base_url"] + "/"}, project_root=tmp_path
    )
    assert active_preset_key(settings) == "deepseek"


def test_active_preset_key_falls_back_to_custom(tmp_path):
    settings = load_settings({"AI_BASE_URL": "http://127.0.0.1:8080/v1"}, project_root=tmp_path)
    assert active_preset_key(settings) == "custom"


# ---------------- 配置读取兼容 ----------------

def test_ai_keys_win_over_legacy_names(tmp_path):
    settings = load_settings(
        {
            "AI_API_KEY": "sk-new",
            "DEEPSEEK_API_KEY": "sk-old",
            "AI_MODEL": "qwen-plus",
            "DEEPSEEK_MODEL": "deepseek-chat",
        },
        project_root=tmp_path,
    )
    assert settings.ai_api_key == "sk-new"
    assert settings.ai_model == "qwen-plus"


def test_blank_ai_key_falls_back_to_legacy(tmp_path):
    """.env 里留一行空白的 `AI_API_KEY=` 不该把老配置顶掉。"""
    (tmp_path / ".env").write_text(
        "AI_API_KEY=\nDEEPSEEK_API_KEY=sk-from-legacy\n", encoding="utf-8"
    )
    settings = load_settings({}, project_root=tmp_path)
    assert settings.ai_api_key == "sk-from-legacy"


def test_invalid_base_url_is_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_settings({"AI_BASE_URL": "api.deepseek.com"}, project_root=tmp_path)


def test_key_tail_only_for_realistic_keys(tmp_path):
    assert load_settings({"AI_API_KEY": "sk-1234567890ab"}, project_root=tmp_path).ai_key_tail == "90ab"
    # 太短的串不当密钥展示，避免把"测试"这类占位符当尾号印在页面上
    assert load_settings({"AI_API_KEY": "short"}, project_root=tmp_path).ai_key_tail == ""


def test_ai_env_override_keys_only_leaks_names():
    found = ai_env_override_keys({"DEEPSEEK_API_KEY": "sk-secret-value"})
    assert found == ["DEEPSEEK_API_KEY"]
    assert all("sk-secret" not in item for item in found)


# ---------------- `.env` 写入 ----------------

def test_save_keeps_other_lines_and_comments(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# 我的注释\nHEALTH_AGENT_PORT=8123\nAI_API_KEY=sk-old\n\nOTHER=1\n",
        encoding="utf-8",
    )
    save_ai_config(env_file, api_key="sk-new", base_url="https://x.example/v1", model="m1")

    text = env_file.read_text(encoding="utf-8")
    assert "# 我的注释" in text
    assert "HEALTH_AGENT_PORT=8123" in text
    assert "OTHER=1" in text
    assert "AI_API_KEY=sk-new" in text
    assert "sk-old" not in text
    assert "AI_BASE_URL=https://x.example/v1" in text
    assert "AI_MODEL=m1" in text


def test_save_none_means_untouched_and_blank_means_removed(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("AI_API_KEY=sk-keep\nAI_MODEL=old-model\n", encoding="utf-8")

    save_ai_config(env_file, api_key=None, model="new-model")
    text = env_file.read_text(encoding="utf-8")
    assert "AI_API_KEY=sk-keep" in text
    assert "AI_MODEL=new-model" in text

    save_ai_config(env_file, api_key="")
    assert "AI_API_KEY" not in env_file.read_text(encoding="utf-8")


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("AI_MODEL=old\n", encoding="utf-8")
    save_ai_config(env_file, model="new")
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert leftovers == []


def test_save_creates_file_when_missing(tmp_path):
    env_file = tmp_path / "nested" / ".env"
    save_ai_config(env_file, api_key="sk-x", base_url="https://y.example/v1", model="m")
    assert "AI_API_KEY=sk-x" in env_file.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="Windows 上 chmod 不生效")
def test_save_restricts_permissions(tmp_path):
    env_file = tmp_path / ".env"
    save_ai_config(env_file, api_key="sk-x")
    assert env_file.stat().st_mode & 0o777 == 0o600


def test_save_roundtrip_is_loadable(tmp_path):
    env_file = tmp_path / ".env"
    save_ai_config(
        env_file,
        api_key="sk-roundtrip-0001",
        base_url="https://api.moonshot.cn/v1",
        model="moonshot-v1-8k",
    )
    settings = load_settings({}, project_root=tmp_path)
    assert settings.ai_api_key == "sk-roundtrip-0001"
    assert settings.ai_model == "moonshot-v1-8k"
    assert settings.ai_enabled is True


def test_save_quotes_values_that_would_break_parsing(tmp_path):
    env_file = tmp_path / ".env"
    save_ai_config(env_file, model="weird model #1")
    assert load_settings({}, project_root=tmp_path).ai_model == "weird model #1"


# ---------------- 页面：读 ----------------

def test_settings_page_never_renders_the_key(tmp_path):
    client = _client(tmp_path, env={"AI_API_KEY": "sk-very-secret-9999"})
    html = client.get("/settings").text
    assert "sk-very-secret-9999" not in html
    assert "9999" in html  # 只露尾号
    assert 'type="password"' in html


def test_settings_page_makes_no_network_call(tmp_path, monkeypatch):
    """GET 只读配置，绝不在渲染路径上发请求。"""
    calls: list[str] = []

    def boom(**kwargs):  # pragma: no cover - 被调用即失败
        calls.append("called")
        raise AssertionError("GET /settings 不应触发任何网络请求")

    monkeypatch.setattr(ui_app, "probe_connection", boom)
    client = _client(tmp_path)
    assert client.get("/settings").status_code == 200
    assert calls == []


# ---------------- 页面：写 ----------------

def test_save_from_ui_takes_effect_without_restart(tmp_path):
    client = _client(tmp_path)
    response = client.post(
        "/settings/ai",
        data={
            "action": "save",
            "preset": "dashscope",
            "api_key": "sk-ui-1234567890",
            "base_url": "",
            "model": "",
            "clear_key": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    # 预设被解析成具体值再落盘，文件里不出现"preset"这种界面概念
    assert "qwen-plus" in env_text
    assert "dashscope.aliyuncs.com" in env_text

    html = client.get("/settings").text
    assert "已启用 · 模型 <code>qwen-plus</code>" in html
    assert "已启用 · 模型 <code>deepseek-chat</code>" not in html


def test_save_with_blank_key_keeps_existing_key(tmp_path):
    (tmp_path / ".env").write_text("AI_API_KEY=sk-existing-key-0001\n", encoding="utf-8")
    client = _client(tmp_path)
    client.post(
        "/settings/ai",
        data={
            "action": "save",
            "preset": "custom",
            "api_key": "",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "clear_key": "",
        },
        follow_redirects=False,
    )
    settings = load_settings({}, project_root=tmp_path)
    assert settings.ai_api_key == "sk-existing-key-0001"


def test_clear_key_removes_it(tmp_path):
    client = _client(tmp_path, env={"AI_API_KEY": "sk-existing-key-0001"})
    client.post(
        "/settings/ai",
        data={
            "action": "save",
            "preset": "custom",
            "api_key": "",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "clear_key": "1",
        },
        follow_redirects=False,
    )
    assert load_settings({}, project_root=tmp_path).ai_api_key is None


def test_save_rejects_unusable_address(tmp_path):
    client = _client(tmp_path)
    bad_url = client.post(
        "/settings/ai",
        data={"action": "save", "preset": "custom", "base_url": "abc", "model": "m"},
        follow_redirects=False,
    )
    assert "error=" in bad_url.headers["location"]
    assert not (tmp_path / ".env").exists()

    no_model = client.post(
        "/settings/ai",
        data={
            "action": "save",
            "preset": "custom",
            "base_url": "https://api.x.com/v1",
            "model": "",
        },
        follow_redirects=False,
    )
    assert "error=" in no_model.headers["location"]


def test_empty_address_with_custom_preset_says_so(tmp_path):
    """空地址不能说成"必须以 http 开头"——那是两件事（2026-10-21 真实反馈）。"""
    client = _client(tmp_path)
    response = client.post(
        "/settings/ai",
        data={"action": "save", "preset": "custom", "base_url": "", "model": "m", "api_key": "sk-x-123456789"},
        follow_redirects=False,
    )
    message = unquote(response.headers["location"])
    assert "接口地址是空的" in message
    assert "以 http:// 或 https:// 开头" not in message


def test_address_without_scheme_is_autocompleted(tmp_path):
    client = _client(tmp_path)
    response = client.post(
        "/settings/ai",
        data={
            "action": "save",
            "preset": "custom",
            "api_key": "sk-auto-1234567890",
            "base_url": "api.deepseek.com/v1",
            "model": "deepseek-chat",
        },
        follow_redirects=False,
    )
    message = unquote(response.headers["location"])
    assert "已补全 https://" in message
    assert load_settings({}, project_root=tmp_path).ai_base_url == "https://api.deepseek.com/v1"


def test_local_address_gets_http_not_https(tmp_path):
    client = _client(tmp_path)
    client.post(
        "/settings/ai",
        data={
            "action": "save",
            "preset": "custom",
            "api_key": "sk-local-1234567890",
            "base_url": "127.0.0.1:11434/v1",
            "model": "qwen2.5:7b",
        },
        follow_redirects=False,
    )
    assert load_settings({}, project_root=tmp_path).ai_base_url == "http://127.0.0.1:11434/v1"


def test_key_pasted_into_address_field_is_explained(tmp_path):
    client = _client(tmp_path)
    response = client.post(
        "/settings/ai",
        data={"action": "save", "preset": "custom", "base_url": "sk-abcdef123456", "model": "m"},
        follow_redirects=False,
    )
    message = unquote(response.headers["location"])
    assert "API 密钥" in message
    assert "sk-abcdef123456" not in message  # 回显也不能把密钥打出来


def test_test_connection_success_does_not_save(tmp_path, monkeypatch):
    seen: dict[str, object] = {}

    def fake_probe(**kwargs):
        seen.update(kwargs)
        return ProbeResult(True, "模型 qwen-plus 已响应", model="qwen-plus", latency_ms=123)

    monkeypatch.setattr(ui_app, "probe_connection", fake_probe)
    client = _client(tmp_path)
    response = client.post(
        "/settings/ai",
        data={
            "action": "test",
            "preset": "dashscope",
            "api_key": "sk-not-saved-0001",
            "base_url": "",
            "model": "",
        },
        follow_redirects=False,
    )
    location = response.headers["location"]
    assert location.startswith("/settings?msg=")
    assert "123" in location
    # 试连只是试连：什么都不该写下去
    assert not (tmp_path / ".env").exists()
    assert seen["base_url"] == preset_by_key("dashscope")["base_url"]
    assert seen["model"] == "qwen-plus"


def test_test_connection_failure_reports_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ui_app,
        "probe_connection",
        lambda **kwargs: ProbeResult(False, "密钥无效或已过期（HTTP 401）", model="m"),
    )
    client = _client(tmp_path)
    response = client.post(
        "/settings/ai",
        data={"action": "test", "preset": "custom", "base_url": "https://a.b/v1", "model": "m"},
        follow_redirects=False,
    )
    location = response.headers["location"]
    assert location.startswith("/settings?error=")
    assert "401" in location


def test_ai_post_requires_csrf_when_password_set(tmp_path):
    settings = load_settings({"HEALTH_AGENT_PASSWORD_HASH": "x"}, project_root=tmp_path)
    app = create_app(settings, db_path=tmp_path / "ai.db")
    with TestClient(app) as client:
        # 未登录时 POST 先被鉴权挡在登录页，压根走不到写配置
        response = client.post(
            "/settings/ai", data={"action": "save"}, follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login")
    assert not (tmp_path / ".env").exists()


# ---------------- 探针本身 ----------------

def test_probe_without_key_fails_soft():
    result = probe_connection(api_key="", base_url="https://a.b/v1", model="m")
    assert result.ok is False
    assert "密钥" in result.message


def test_probe_with_bad_url_fails_soft():
    result = probe_connection(api_key="sk-x", base_url="a.b/v1", model="m")
    assert result.ok is False
    assert "http" in result.message


def test_probe_maps_http_status_to_plain_chinese():
    from health_agent.ai_client.base import LLMError
    from health_agent.ai_client.probe import _describe

    assert "密钥无效" in _describe(LLMError("http", "x", status_code=401))
    assert "路径不对" in _describe(LLMError("http", "x", status_code=404))
    assert "限流" in _describe(LLMError("http", "x", status_code=429))
    assert "超时" in _describe(LLMError("timeout", "x"))


def test_settings_dataclass_never_reprs_key(tmp_path):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "a.db", ai_api_key="sk-repr-check")
    assert "sk-repr-check" not in repr(settings)


# ---------------- 地址归一化（2026-10-21 真实反馈后补） ----------------

def test_normalize_adds_https_for_public_hosts():
    assert normalize_base_url("api.deepseek.com/v1")[0] == "https://api.deepseek.com/v1"
    assert normalize_base_url("https://api.x.com/v1") == ("https://api.x.com/v1", "")


def test_normalize_uses_http_for_local_hosts():
    for raw in ("127.0.0.1:11434/v1", "localhost:8000/v1"):
        assert normalize_base_url(raw)[0] == f"http://{raw}"


def test_normalize_explains_what_it_changed():
    url, note = normalize_base_url("api.moonshot.cn/v1")
    assert url == "https://api.moonshot.cn/v1"
    assert "已补全 https://" in note
    # 没改就别说改了
    assert normalize_base_url("https://api.x.com/v1")[1] == ""


def test_base_url_problem_messages():
    assert "空的" in base_url_problem("")
    assert "API 密钥" in base_url_problem("sk-1234567890abcdef")
    assert "缺协议头" in base_url_problem("api.x.com/v1")
    assert "主机名" in base_url_problem("https://abc")
    assert base_url_problem("https://api.deepseek.com/v1") is None
    assert base_url_problem("http://127.0.0.1:11434/v1") is None
    assert base_url_problem("http://192.168.1.9:8000/v1") is None


# ---------------- 2026-10-21 真实故障：开了密码之后 POST 表单变空 ----------------

def _logged_in_client(tmp_path, password: str = "pw-123456") -> TestClient:
    """带访问密码的应用 + 已登录会话（CSRF 中间件这段只有设了密码才会走）。"""
    from health_agent.ui.auth import hash_password

    settings = load_settings(
        {"HEALTH_AGENT_PASSWORD_HASH": hash_password(password)}, project_root=tmp_path
    )
    app = create_app(settings, db_path=tmp_path / "pw.db")
    client = TestClient(app)
    response = client.post(
        "/login", data={"password": password, "next": "/settings"}, follow_redirects=False
    )
    assert response.status_code == 303
    return client


def _csrf_of(client: TestClient, path: str = "/settings") -> str:
    import re

    match = re.search(r'name="csrf" value="([^"]+)"', client.get(path).text)
    assert match is not None, "设置页应当带 csrf 隐藏域"
    return match.group(1)


def test_post_body_survives_csrf_middleware(tmp_path):
    """CSRF 中间件校验完不能把请求体吃掉。

    根因：Starlette 的 BaseHTTPMiddleware 只有在 `_body` 被缓存时才向下游重放请求体，
    而 `request.form()` 走的是 `stream()`，会让下游收到空 body —— 于是路由里的
    `Form(...)` 全部落回默认值（地址空、模式未知），用户看到"接口地址不能为空"。
    """
    client = _logged_in_client(tmp_path)
    response = client.post(
        "/settings/ai",
        data={
            "csrf": _csrf_of(client),
            "action": "save",
            "preset": "deepseek",
            "api_key": "sk-pw-1234567890",
            "base_url": "",
            "model": "",
        },
        follow_redirects=False,
    )
    location = response.headers["location"]
    assert "error=" not in location, location

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "AI_API_KEY=sk-pw-1234567890" in env_text
    assert "deepseek.com" in env_text


def test_other_post_forms_survive_csrf_middleware(tmp_path):
    """同一个根因会影响所有 POST，顺手守一下界面模式切换。"""
    client = _logged_in_client(tmp_path)
    response = client.post(
        "/settings/mode",
        data={"csrf": _csrf_of(client), "mode": "chronos", "next_path": "/settings"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/settings"  # 而不是 /settings?error=未知的界面模式


def test_posting_a_key_into_the_url_field_is_called_out(tmp_path):
    """把密钥粘进「接口地址」是最常见的手滑，反馈要指名道姓。"""
    client = _logged_in_client(tmp_path)
    response = client.post(
        "/settings/ai",
        data={
            "csrf": _csrf_of(client),
            "action": "test",
            "preset": "custom",
            "api_key": "",
            "base_url": "sk-abcdef123456",
            "model": "deepseek-chat",
        },
        follow_redirects=False,
    )
    from urllib.parse import unquote

    location = unquote(response.headers["location"])
    assert "接口地址这一格填的像是 API 密钥" in location


def test_key_only_submission_uses_the_preset(tmp_path):
    """只填密钥、地址与模型留空：应当按预设补全并保存成功（用户实际的操作路径）。"""
    client = _logged_in_client(tmp_path)
    response = client.post(
        "/settings/ai",
        data={
            "csrf": _csrf_of(client),
            "action": "save",
            "preset": "deepseek",
            "api_key": "sk-only-key-1234567890",
            "base_url": "",
            "model": "",
        },
        follow_redirects=False,
    )
    assert "error=" not in response.headers["location"], response.headers["location"]
    settings = load_settings({}, project_root=tmp_path)
    assert settings.ai_api_key == "sk-only-key-1234567890"
    assert settings.ai_base_url.endswith("/v1")
    assert settings.ai_model == "deepseek-chat"
