"""AI 层：接口契约、HTTP 客户端、缓存、降级。"""

from .base import LLMClient, LLMError, LLMResult
from .cache import get_cached, make_cache_key, put_cached, summary_digest
from .http_client import ChatCompletionClient
from .probe import ProbeResult, probe_connection
from .prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt, parse_response
from .summarizer import (
    SOURCE_AI,
    SOURCE_CACHE,
    SOURCE_TEMPLATE,
    Narrative,
    build_summary,
    peek_cached,
    rewrite_report,
)

__all__ = [
    "PROMPT_VERSION",
    "ProbeResult",
    "SOURCE_AI",
    "SOURCE_CACHE",
    "SOURCE_TEMPLATE",
    "SYSTEM_PROMPT",
    "ChatCompletionClient",
    "LLMClient",
    "LLMError",
    "LLMResult",
    "Narrative",
    "build_summary",
    "build_user_prompt",
    "get_cached",
    "make_cache_key",
    "parse_response",
    "peek_cached",
    "probe_connection",
    "put_cached",
    "rewrite_report",
    "summary_digest",
]
