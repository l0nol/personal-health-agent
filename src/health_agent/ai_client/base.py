"""AI 客户端的最小契约。

刻意只定义"要什么"而不定义"接哪家"：换供应商只需要换一个实现，
上层（报告改写）完全不用改。这也让接口未确认时可以先跑起来。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class LLMResult:
    text: str
    model: str
    status: str = STATUS_OK
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)


class LLMError(Exception):
    """调用失败。kind 用于记日志，不用于给用户看细节。

    `detail` 是服务商自己说的一句话（截断、去掉任何像密钥的片段），
    只用来把"HTTP 400"翻译成"模型不存在"这种能照着改的话；
    它不进日志、不上报，也不参与任何判断。
    """

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        status_code: int | None = None,
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.detail = detail


class LLMClient(Protocol):
    def complete(self, *, system: str, user: str, max_tokens: int = 600) -> LLMResult:
        """发一次补全请求。失败必须抛 LLMError，不允许返回空串糊弄过去。"""
        ...
