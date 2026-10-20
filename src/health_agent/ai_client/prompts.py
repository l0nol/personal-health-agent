"""提示词与输出契约。

提示词版本号参与缓存键：改了提示词，旧缓存自动失效。
输出要求是**受限 JSON**，因为报告里的数字必须来自程序计算结果，
模型只负责组织语言，不能自己算、也不能自己加数字。
"""

from __future__ import annotations

import json

PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """你是一个个人健康数据的文字整理助手。用户会给你一份已经算好的健康数据摘要。

严格规则：
1. 只能使用摘要里出现的数字，禁止自行计算、估算或补充任何新数字。
2. 禁止做疾病诊断、禁止给出治疗或用药建议、禁止预测风险。
3. 不确定的地方直接说"数据不足"，不要猜测。
4. 语气平实，不夸张、不惊吓、不说教。
5. 不超过 120 字。
6. 只输出 JSON，格式为 {"text": "……"}，不要输出任何其它内容。"""


def build_user_prompt(summary: dict) -> str:
    return (
        "以下是已经由程序算好的摘要，请据此写一段简短说明：\n"
        + json.dumps(summary, ensure_ascii=False, indent=2)
    )


def parse_response(text: str) -> str:
    """从模型输出里取出正文。只接受约定的 JSON，其余一律视为失败。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # 有些模型会套代码块，剥掉外壳再解析
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("模型输出不是约定的 JSON")

    payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("模型输出不是 JSON 对象")

    result = payload.get("text")
    if not isinstance(result, str) or not result.strip():
        raise ValueError("模型输出缺少 text 字段")
    return result.strip()
