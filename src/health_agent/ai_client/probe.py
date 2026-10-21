"""连通性自检：用一次最小请求确认"地址 + 密钥 + 模型"这三件事对不对得上。

刻意做小：
- 一句话提示、极低 max_tokens、最多 0 次重试，花费与等待都可以忽略；
- 对外只给"成没成 + 一句人话原因"，不回显密钥，也不回显响应体；
- 任何异常都收敛成字符串，不把 traceback 抛到页面上。

它不改任何数据、不写缓存、不写调用日志——这是"试一试"，不是"分析一次"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import LLMError
from .http_client import ChatCompletionClient

PROBE_SYSTEM = "你是一个连通性自检助手。"
PROBE_USER = "回复两个字：可用"

#: 4xx 的具体原因能省掉用户很多猜谜时间，这里只翻译最常见的那几个。
_HTTP_HINTS = {
    400: "请求被拒（模型名或参数不被接受）",
    401: "密钥无效或已过期",
    403: "密钥无权限访问该模型",
    404: "接口路径不对（多数服务商需要以 /v1 结尾）",
    413: "请求体过大",
    422: "参数格式不被接受",
    429: "触发限流或余额不足",
}


@dataclass(frozen=True, slots=True)
class ProbeResult:
    ok: bool
    message: str
    model: str = ""
    latency_ms: int | None = None

    @property
    def headline(self) -> str:
        return "连接成功" if self.ok else "连接失败"


def _describe(exc: LLMError) -> str:
    if exc.status_code is not None:
        hint = _HTTP_HINTS.get(exc.status_code)
        if hint:
            return f"{hint}（HTTP {exc.status_code}）"
        return f"服务端返回 HTTP {exc.status_code}"
    if exc.kind == "timeout":
        return "请求超时：地址可能不通，或模型冷启动较慢"
    if exc.kind == "network":
        return "网络不通：请检查地址、代理与本机防火墙"
    if exc.kind == "config":
        return str(exc)
    return "调用失败：服务端返回了无法解析的内容"


def probe_connection(
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float = 12.0,
    transport: Any = None,
) -> ProbeResult:
    """试一次最小对话。永不抛异常，失败也是一条结果。"""
    if not (api_key or "").strip():
        return ProbeResult(False, "还没有填 API 密钥", model=model)
    if not (base_url or "").strip().startswith(("http://", "https://")):
        return ProbeResult(False, "接口地址必须以 http:// 或 https:// 开头", model=model)
    if not (model or "").strip():
        return ProbeResult(False, "还没有填模型名", model=model)

    try:
        client = ChatCompletionClient(
            api_key=api_key.strip(),
            base_url=base_url.strip(),
            model=model.strip(),
            timeout=timeout,
            max_retries=0,
            transport=transport,
        )
    except ValueError as exc:
        return ProbeResult(False, str(exc), model=model)

    try:
        result = client.complete(system=PROBE_SYSTEM, user=PROBE_USER, max_tokens=16)
    except LLMError as exc:
        return ProbeResult(False, _describe(exc), model=model)
    except Exception as exc:  # noqa: BLE001 - 自检工具，绝不因为意外异常把页面打挂
        return ProbeResult(False, f"调用异常：{type(exc).__name__}", model=model)

    return ProbeResult(
        True,
        f"模型 {result.model or model} 已响应",
        model=result.model or model,
        latency_ms=result.latency_ms,
    )
