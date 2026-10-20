"""OpenAI 兼容形态的 chat completions 客户端。

接口细节尚未由用户确认，所以这里的写法刻意保守：
- 端点、模型、超时、重试次数全部可配，不写死；
- 只依赖最通用的 `POST {base_url}/chat/completions` 形状；
- 任何失败都抛 LLMError，由上层降级成本地文案，绝不影响记录、统计与提醒；
- 不在日志里出现密钥、请求头或完整响应体。
"""

from __future__ import annotations

import time

import httpx

from .base import LLMError, LLMResult

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 2


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
                        "http", f"请求被拒绝 {response.status_code}", status_code=response.status_code
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
