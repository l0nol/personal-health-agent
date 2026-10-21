"""训练日志的纯函数校验。

不放数据库、不读环境变量、不看时间以外的东西：
同样的输入必然得到同样的输出，方便把每条拒绝路径单独写成测试。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime

from .errors import (
    E_ACTIVITY,
    E_BODY_PART,
    E_COMPLETION,
    E_DATE,
    E_RANGE,
    E_SORENESS_PAIR,
    TrainingValidationError,
)
from .models import (
    ACTIVITY_TYPES,
    BODY_PART_ALIASES,
    BODY_PARTS,
    COMPLETIONS,
)

_PART_SPLIT = re.compile(r"[,，、/\s]+")


def parse_date(
    raw: str | date,
    *,
    field: str = "日期",
    today: date | None = None,
    allow_future: bool = False,
) -> date:
    """解析 ISO 日期，并拒绝未来日期。

    未来日期通常是用户手滑（把 2026 写成 2027）。放进去会污染"最近训练"判断，
    所以在入口就拒掉，而不是等建议引擎去猜。
    """
    if isinstance(raw, date):
        value = raw
    else:
        text = (raw or "").strip()
        try:
            value = date.fromisoformat(text)
        except ValueError:
            raise TrainingValidationError(
                E_DATE, f"{field}格式应为 YYYY-MM-DD，实际为 {text!r}", field=field
            ) from None
    if not allow_future:
        bound = today or date.today()
        if value > bound:
            raise TrainingValidationError(
                E_DATE, f"{field}不能是未来日期（{value.isoformat()} > {bound.isoformat()}）", field=field
            )
    return value


def parse_optional_int(
    raw: object,
    *,
    field: str,
    low: int,
    high: int,
) -> int | None:
    """把可选的整数字段解析成 int 或 None。

    None / 空字符串 = 未填写（保留 NULL），绝不补 0。
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if text == "":
            return None
        try:
            value = int(text)
        except ValueError:
            raise TrainingValidationError(
                E_RANGE, f"{field}应为整数，实际为 {text!r}", field=field
            ) from None
    elif isinstance(raw, bool):
        # bool 是 int 的子类；把 True 当 1 会悄悄制造假数据
        raise TrainingValidationError(E_RANGE, f"{field}应为整数，不接受布尔值", field=field)
    elif isinstance(raw, int):
        value = raw
    else:
        raise TrainingValidationError(E_RANGE, f"{field}应为整数，实际为 {type(raw).__name__}", field=field)

    if not low <= value <= high:
        raise TrainingValidationError(
            E_RANGE, f"{field}应在 {low}–{high} 之间，实际为 {value}", field=field
        )
    return value


def normalize_body_parts(raw: str | Iterable[str] | None) -> tuple[str, ...]:
    """把用户输入规范成有序、去重的枚举元组。

    接受 `"胸, 三头"` 这类写法，也接受列表；中文别名会映射到枚举。
    **不猜测**：出现未知部位直接报错，而不是忽略。
    """
    if raw is None:
        return ()
    if isinstance(raw, str):
        tokens = [token for token in _PART_SPLIT.split(raw.strip()) if token]
    else:
        tokens = []
        for item in raw:
            text = str(item).strip()
            if not text:
                continue
            tokens.extend(token for token in _PART_SPLIT.split(text) if token)

    resolved: set[str] = set()
    for token in tokens:
        key = token.strip().lower()
        if key in BODY_PARTS:
            resolved.add(key)
            continue
        alias = BODY_PART_ALIASES.get(token.strip())
        if alias:
            resolved.add(alias)
            continue
        raise TrainingValidationError(
            E_BODY_PART,
            f"未识别的训练部位 {token!r}；允许值：{', '.join(BODY_PARTS)}（也可用中文：胸/背/腿/肩/核心）",
            field="部位",
        )
    # 按枚举顺序输出，保证同一组部位在导出与对比时顺序稳定
    return tuple(part for part in BODY_PARTS if part in resolved)


def validate_activity_type(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value not in ACTIVITY_TYPES:
        raise TrainingValidationError(
            E_ACTIVITY, f"活动类型应为 {'/'.join(ACTIVITY_TYPES)}，实际为 {raw!r}", field="活动类型"
        )
    return value


def validate_completion(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value not in COMPLETIONS:
        raise TrainingValidationError(
            E_COMPLETION, f"完成状态应为 {'/'.join(COMPLETIONS)}，实际为 {raw!r}", field="完成状态"
        )
    return value


def validate_started_at(raw: str | None, *, session_date: date) -> str | None:
    """校验可选开始时间（HH:MM 或完整 ISO）。

    只做格式与"同一天"校验，不做时区换算：用户填的是本地墙上时间。
    """
    if raw is None or str(raw).strip() == "":
        return None
    text = str(raw).strip()
    if re.fullmatch(r"\d{2}:\d{2}", text):
        hour, minute = (int(part) for part in text.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise TrainingValidationError(E_DATE, f"开始时间不合法：{text!r}", field="开始时间")
        iso = f"{session_date.isoformat()}T{text}:00"
    else:
        try:
            iso = datetime.fromisoformat(text).isoformat(timespec="minutes")
        except ValueError:
            raise TrainingValidationError(
                E_DATE, f"开始时间应为 HH:MM 或 ISO 时间，实际为 {text!r}", field="开始时间"
            ) from None
        if iso[:10] != session_date.isoformat():
            raise TrainingValidationError(
                E_DATE,
                f"开始时间 {iso} 与训练日期 {session_date.isoformat()} 不在同一天",
                field="开始时间",
            )
    return iso


def validate_soreness(
    parts: str | Iterable[str] | None, level: object
) -> tuple[tuple[str, ...], int | None]:
    """酸痛部位与程度必须成对出现，或同时留空。

    只有"腿很酸"没有程度，和只有"3 分"没有部位，都无法用于建议——
    宁可让用户补一个字段，也不要半个事实。
    """
    normalized = normalize_body_parts(parts)
    parsed_level = parse_optional_int(level, field="酸痛程度", low=1, high=5)
    if bool(normalized) != (parsed_level is not None):
        raise TrainingValidationError(
            E_SORENESS_PAIR,
            "酸痛部位与酸痛程度需要同时填写，或同时留空",
            field="酸痛",
        )
    return normalized, parsed_level


def validate_days_per_week(raw: object) -> int | None:
    return parse_optional_int(raw, field="每周训练日", low=1, high=7)


def validate_time_budget(raw: object) -> int | None:
    return parse_optional_int(raw, field="单次时间预算（分钟）", low=5, high=240)


def validate_text_list(raw: str | Iterable[str] | None, *, field: str) -> tuple[str, ...]:
    """把自由文本列表规范化：去空白、去重、保序。

    器材与"希望避免的动作"是自由文本，不做枚举校验——用户可能写
    "哑铃（可调）"这种设备页面上的原话，硬套枚举只会逼他撒谎。
    """
    if raw is None:
        return ()
    items = _PART_SPLIT.split(raw.strip()) if isinstance(raw, str) else [str(item) for item in raw]
    seen: list[str] = []
    for item in items:
        text = item.strip()
        if text and text not in seen:
            seen.append(text)
    if len(seen) > 30:
        raise TrainingValidationError(E_RANGE, f"{field}最多 30 项", field=field)
    return tuple(seen)
