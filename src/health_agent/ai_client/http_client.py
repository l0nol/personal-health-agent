"""OpenAI 兼容形态的 chat completions 客户端。

接口细节尚未由用户确认，所以这里的写法刻意保守：
- 端点、模型、超时、重试次数全部可配，不写死；
- 只依赖最通用的 `POST {base_url}/chat/completions` 形状；
- 任何失败都抛 LLMError，由上层降级成本地文案，绝不影响记录、统计与提醒；
- 不在日志里出现密钥、请求头或完整响应体。
"""

from __future__ import annotations

import re
import time

import httpx

from .base import LLMError, LLMResult

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 2

#: 服务商回显的细节里可能带密钥片段（有些中转站会把 Authorization 原样带回来），
#: 上页面前先把 `sk-****` 之类的片段抹掉——页面是会被截图、被转发的地方。
_SECRET_PATTERN = re.compile(r"(sk-|Bearer\s+)[A-Za-z0-9_\-]{4,}", re.IGNORECASE)


def _parse_model_ids(data: object) -> list[str]:
    """从两种常见形态里取出模型 id，去重、保持服务商给的顺序。"""
    if not isinstance(data, dict):
        return []
    raw: object = data.get("data")
    if not isinstance(raw, list):
        raw = data.get("models")
    if not isinstance(raw, list):
        return []

    seen: set[str] = set()
    ids: list[str] = []
    for item in raw:
        name = ""
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            for key in ("id", "name", "model"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    name = value.strip()
                    break
        if name and name not in seen:
            seen.add(name)
            ids.append(name)
    return ids


def _clean_detail(text: str, limit: int = 200) -> str:
    """截断并抹掉像密钥的片段。只用于"翻译错误"，不做任何逻辑判断。"""
    value = _SECRET_PATTERN.sub(r"\1<已隐藏>", str(text or "")).strip()
    value = " ".join(value.split())
    return value[:limit]


def _error_detail(response: httpx.Response) -> str:
    """从错误响应里取一句人话：`error.message` / `message` / `detail` 都认。"""
    try:
        data = response.json()
    except ValueError:
        return _clean_detail(response.text)
    if not isinstance(data, dict):
        return ""
    error = data.get("error")
    if isinstance(error, dict):
        for key in ("message", "msg", "detail", "code"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return _clean_detail(value)
    if isinstance(error, str) and error.strip():
        return _clean_detail(error)
    for key in ("message", "msg", "detail"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return _clean_detail(value)
    return ""


class ChatCompletionClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("缺少 API 密钥，不应创建客户端")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._max_retries = max(0, max_retries)
        self._transport = transport

    def __repr__(self) -> str:
        return f"ChatCompletionClient(model={self._model!r}, base_url={self._base_url!r})"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, url: str, payload: dict) -> httpx.Response:
        with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
            return client.post(url, json=payload, headers=self._headers())

    def list_models(self) -> list[str]:
        """`GET {base_url}/models` —— 这个密钥在这个地址到底能用哪些模型。

        很多"模型不存在"其实不是模型名写错，而是**地址和密钥根本不配套**
        （拿 A 家的密钥去 B 家的地址）。列出可用模型是最快把这两件事对上号的办法。

        OpenAI 兼容形态返回 `{"data": [{"id": "..."}]}`；Ollama 原生形态返回
        `{"models": [{"name": "..."}]}`，两种都认。失败一律抛 LLMError。
        """
        url = f"{self._base_url}/models"
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                response = client.get(url, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise LLMError("timeout", "请求超时") from exc
        except httpx.HTTPError as exc:
            raise LLMError("network", f"网络错误：{type(exc).__name__}") from exc

        if response.status_code >= 400:
            raise LLMError(
                "http",
                f"请求被拒绝 {response.status_code}",
                status_code=response.status_code,
                detail=_error_detail(response),
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError("http", "响应不是合法 JSON") from exc

        return _parse_model_ids(data)

    def complete(self, *, system: str, user: str, max_tokens: int = 600) -> LLMResult:
        url = f"{self._base_url}/chat/completions"
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        }
        started = time.monotonic()
        last_error: LLMError | None = None

        for attempt in range(self._max_retries + 1):
            try:
                response = self._post(url, payload)
            except httpx.TimeoutException as exc:
                last_error = LLMError("timeout", "请求超时")
            except httpx.HTTPError as exc:
                last_error = LLMError("network", f"网络错误：{type(exc).__name__}")
            else:
                latency_ms = int((time.monotonic() - started) * 1000)
                if response.status_code >= 500:
                    last_error = LLMError(
                        "http", f"服务端错误 {response.status_code}", status_code=response.status_code
                    )
                elif response.status_code >= 400:
                    # 4xx 是配置或鉴权问题，重试没有意义
                    raise LLMError(
                        "http",
                        f"请求被拒绝 {response.status_code}",
                        status_code=response.status_code,
                        detail=_error_detail(response),
                    )
                else:
                    return self._parse(response, latency_ms)

            if attempt < self._max_retries:
                time.sleep(0.5 * (2**attempt))

        assert last_error is not None
        raise last_error

    def _parse(self, response: httpx.Response, latency_ms: int) -> LLMResult:
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError("http", "响应不是合法 JSON") from exc

        choices = data.get("choices") or []
        if not choices:
            raise LLMError("http", "响应缺少 choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LLMError("http", "响应内容为空")

        usage = data.get("usage") or {}
        return LLMResult(
            text=content,
            model=str(data.get("model") or self._model),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_ms=latency_ms,
        )
