"""连通性自检：用一次最小请求确认"地址 + 密钥 + 模型"这三件事对不对得上。

刻意做小：
- 一句话提示、极低 max_tokens、最多 0 次重试，花费与等待都可以忽略；
- 对外只给"成没成 + 一句人话原因"，不回显密钥，也不回显响应体；
- 任何异常都收敛成字符串，不把 traceback 抛到页面上。

它不改任何数据、不写缓存、不写调用日志——这是"试一试"，不是"分析一次"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

#: 各家的措辞不同，但意思就这几个。命中就说明"地址和密钥是对的，模型名不对"——
#: 这是最值得单独说清的一类失败：用户只要把模型名换成列表里有的那个就好。
_MODEL_MISSING_MARKERS = (
    "model not exist",
    "model_not_found",
    "model not found",
    "model does not exist",
    "no such model",
    "invalid model",
    "unknown model",
    "模型不存在",
    "不存在的模型",
    "无此模型",
)


def _looks_like_missing_model(detail: str) -> bool:
    lowered = (detail or "").lower()
    return any(marker in lowered for marker in _MODEL_MISSING_MARKERS)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    ok: bool
    message: str
    model: str = ""
    latency_ms: int | None = None

    @property
    def headline(self) -> str:
        return "连接成功" if self.ok else "连接失败"


@dataclass(frozen=True, slots=True)
class ModelsResult:
    """拉取"这个地址 + 这个密钥"能用的模型。"""

    ok: bool
    models: list[str] = field(default_factory=list)
    message: str = ""

    def __bool__(self) -> bool:  # 让调用方能直接 `if result:`
        return self.ok and bool(self.models)


def _describe(exc: LLMError, model: str = "") -> str:
    detail = getattr(exc, "detail", "") or ""
    if _looks_like_missing_model(detail):
        name = f"「{model}」" if model else "当前这个模型名"
        return (
            f"模型{name}在该地址不存在（{detail}）。"
            "点「保存并拉取可用模型」看一眼这个密钥实际能用哪些模型"
        )
    if exc.status_code is not None:
        hint = _HTTP_HINTS.get(exc.status_code)
        if hint:
            return f"{hint}（HTTP {exc.status_code}）{('：' + detail) if detail else ''}"
        return f"服务端返回 HTTP {exc.status_code}{('：' + detail) if detail else ''}"
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
        return ProbeResult(False, _describe(exc, model), model=model)
    except Exception as exc:  # noqa: BLE001 - 自检工具，绝不因为意外异常把页面打挂
        return ProbeResult(False, f"调用异常：{type(exc).__name__}", model=model)

    return ProbeResult(
        True,
        f"模型 {result.model or model} 已响应",
        model=result.model or model,
        latency_ms=result.latency_ms,
    )


def fetch_models(
    *,
    api_key: str,
    base_url: str,
    timeout: float = 12.0,
    transport: Any = None,
) -> ModelsResult:
    """列出这个密钥在这个地址能用的模型。永不抛异常。

    "不存在的模型"十有八九是**地址与密钥不配套**，而不是模型名写错。
    把可用列表摆出来，用户一眼就能判断该换模型还是该换地址。
    """
    if not (api_key or "").strip():
        return ModelsResult(False, message="还没有填 API 密钥，拉取模型列表需要密钥")
    if not (base_url or "").strip().startswith(("http://", "https://")):
        return ModelsResult(False, message="接口地址必须以 http:// 或 https:// 开头")

    try:
        client = ChatCompletionClient(
            api_key=api_key.strip(),
            base_url=base_url.strip(),
            model="-",  # 列模型不需要模型名，占位而已
            timeout=timeout,
            max_retries=0,
            transport=transport,
        )
    except ValueError as exc:
        return ModelsResult(False, message=str(exc))

    try:
        models = client.list_models()
    except LLMError as exc:
        return ModelsResult(False, message=_describe(exc))
    except Exception as exc:  # noqa: BLE001 - 同上，绝不把页面打挂
        return ModelsResult(False, message=f"拉取异常：{type(exc).__name__}")

    if not models:
        return ModelsResult(
            False,
            message="这个地址返回了空列表：它可能不支持 /models，请手动填模型名",
        )
    return ModelsResult(True, models=models, message=f"共 {len(models)} 个可用模型")

