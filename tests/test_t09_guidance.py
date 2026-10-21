"""T09 每日建议引擎：场景、契约、边界与 AI 降级。

对应路线图要求的测试矩阵：
睡眠不足且疲劳 / 睡眠不足但感觉良好 / 睡眠缺失 / 训练史缺失 /
近期已练同部位 / 数据过期 / 覆盖不足 / 不适反馈 / AI 关闭、超时、输出无效。

所有用例固定 `TODAY`，不随系统日期漂移。
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from health_agent.ai_client.base import LLMError, LLMResult
from health_agent.guidance.contract import (
    CATEGORY_INSUFFICIENT,
    CATEGORY_LIGHT,
    CATEGORY_REGULAR,
    CATEGORY_REST,
    FORBIDDEN_ADVICE_TERMS,
)
from health_agent.guidance.engine import GuidanceConfig, build_guidance
from health_agent.guidance.explain import build_digest, explain
from health_agent.storage.repository import insert_record
from health_agent.training import service

from conftest import make_record

TODAY = date(2026, 10, 20)
UNITS = {
    "night_sleep_minutes": "min",
    "resting_hr_bpm": "bpm",
    "steps_count": "count",
}


def add_days(conn, metric: str, values: list[float], *, start_offset: int = 1, status: str = "confirmed"):
    """按天写入记录：start_offset=1 表示从昨天开始往前。"""
    for index, value in enumerate(values):
        day = TODAY - timedelta(days=start_offset + index)
        insert_record(
            conn,
            make_record(
                metric_name=metric,
                record_date=day.isoformat(),
                value=value,
                unit=UNITS[metric],
                raw_unit=UNITS[metric],
            ),
            status=status,
        )


def good_sleep(conn):
    add_days(conn, "night_sleep_minutes", [470, 480, 465, 475, 480, 470])


def guidance(conn, **kwargs):
    kwargs.setdefault("for_date", TODAY)
    kwargs.setdefault("today", TODAY)
    return build_guidance(conn, **kwargs)


def assert_safe(texts):
    for text in texts:
        for term in FORBIDDEN_ADVICE_TERMS:
            assert term not in text, f"建议文本出现禁用词 {term!r}: {text!r}"


# ---------------- 契约 ----------------


def test_contract_fields_on_empty_database(conn):
    result = guidance(conn)
    assert result.category == CATEGORY_INSUFFICIENT
    payload = result.to_dict()
    for key in (
        "for_date",
        "category",
        "headline",
        "focus",
        "alternatives",
        "evidence",
        "limitations",
        "uncertainty",
        "feedback_prompt",
        "generated_at",
        "rule_version",
        "data_freshness_days",
        "safety_notice",
    ):
        assert key in payload, key
    assert result.rule_version.startswith("guidance-")
    json.dumps(payload, ensure_ascii=False)  # 必须可序列化


def test_limitations_always_state_scope_and_age_caveat(conn):
    result = guidance(conn)
    joined = "".join(result.limitations)
    assert "不构成诊断" in joined
    assert "未记录年龄" in joined
    assert "8–10 小时" in joined


def test_evidence_marks_sources_and_keeps_record_ids(conn):
    good_sleep(conn)
    service.add_session(conn, session_date=TODAY - timedelta(days=1), activity_type="strength", today=TODAY)
    service.record_feedback(conn, feedback_date=TODAY, energy=4, fatigue=1, today=TODAY)

    result = guidance(conn)
    sources = {item.source for item in result.evidence}
    assert {"device", "training_log", "subjective"} <= sources

    device_items = [item for item in result.evidence if item.source == "device"]
    assert device_items and all(item.record_ids for item in device_items)
    training_items = [item for item in result.evidence if item.source == "training_log"]
    assert training_items and training_items[0].session_ids


# ---------------- 场景 ----------------


def test_short_sleep_and_high_fatigue_recommends_rest(conn):
    add_days(conn, "night_sleep_minutes", [250, 300, 280])
    service.record_feedback(conn, feedback_date=TODAY, energy=2, fatigue=4, today=TODAY)

    result = guidance(conn)
    assert result.category == CATEGORY_REST
    assert "疲劳 4/5" in result.local_explanation
    assert result.signals["severe_sleep"] is True


def test_short_sleep_but_feeling_fine_is_not_a_ban(conn):
    """睡眠不足不等于禁止运动：状态好时降强度，而不是直接躺平。"""
    add_days(conn, "night_sleep_minutes", [350, 360, 355])
    service.record_feedback(conn, feedback_date=TODAY, energy=4, fatigue=1, today=TODAY)

    result = guidance(conn)
    assert result.category == CATEGORY_LIGHT
    assert result.category != CATEGORY_REST
    assert "轻松" in result.headline


def test_missing_sleep_with_feedback_does_not_claim_certainty(conn):
    service.record_feedback(conn, feedback_date=TODAY, energy=4, fatigue=1, today=TODAY)

    result = guidance(conn)
    assert result.category != CATEGORY_INSUFFICIENT
    assert any("最近一晚" in item for item in result.uncertainty)


def test_missing_sleep_and_no_feedback_is_insufficient(conn):
    result = guidance(conn)
    assert result.category == CATEGORY_INSUFFICIENT
    assert any("睡眠记录" in item for item in result.uncertainty)


def test_missing_training_history_is_stated_not_invented(conn):
    good_sleep(conn)
    result = guidance(conn)
    assert result.category == CATEGORY_REGULAR
    assert any("没有任何训练记录" in item for item in result.uncertainty)
    assert not any(item.source == "training_log" for item in result.evidence)


def test_recent_same_body_part_changes_focus(conn):
    good_sleep(conn)
    service.add_session(
        conn,
        session_date=TODAY - timedelta(days=1),
        activity_type="strength",
        body_parts="chest,shoulders",
        today=TODAY,
    )

    result = guidance(conn)
    assert result.category == CATEGORY_REGULAR
    assert "胸" not in result.focus and "肩" not in result.focus
    assert any(item.source == "training_log" for item in result.evidence)
    assert result.signals["recent_parts"] == ["chest", "shoulders"]


def test_stale_device_data_is_insufficient(conn):
    add_days(conn, "night_sleep_minutes", [470, 480], start_offset=10)
    service.record_feedback(conn, feedback_date=TODAY, energy=3, fatigue=2, today=TODAY)

    result = guidance(conn)
    assert result.category == CATEGORY_INSUFFICIENT
    assert result.data_freshness_days is not None and result.data_freshness_days >= 9
    assert any("未更新" in item for item in result.uncertainty)


def test_low_coverage_is_reported(conn):
    add_days(conn, "night_sleep_minutes", [470, 480])
    result = guidance(conn)
    assert any("覆盖不足" in item for item in result.uncertainty)


def test_discomfort_stops_training_without_diagnosing(conn):
    good_sleep(conn)
    service.record_feedback(
        conn,
        feedback_date=TODAY,
        energy=4,
        fatigue=1,
        discomfort=True,
        discomfort_note="站起来有点晕",
        today=TODAY,
    )

    result = guidance(conn)
    assert result.category == CATEGORY_REST
    assert "停止" in result.focus
    assert "求助" in result.safety_notice
    assert_safe((result.headline, result.focus, *result.alternatives, result.local_explanation))


def test_high_soreness_forces_rest(conn):
    good_sleep(conn)
    service.record_feedback(
        conn, feedback_date=TODAY, soreness_parts="legs", soreness_level=4, today=TODAY
    )
    result = guidance(conn)
    assert result.category == CATEGORY_REST


def test_pending_records_never_enter_guidance(conn):
    add_days(conn, "night_sleep_minutes", [250, 260, 255], status="pending")
    result = guidance(conn)
    assert result.category == CATEGORY_INSUFFICIENT
    assert result.signals["sleep_value"] is None


def test_weekly_target_met_suggests_recovery(conn):
    good_sleep(conn)
    service.save_preferences(conn, days_per_week=2)
    service.add_session(conn, session_date=TODAY - timedelta(days=1), activity_type="strength", today=TODAY)
    service.add_session(conn, session_date=TODAY - timedelta(days=3), activity_type="cardio", today=TODAY)

    result = guidance(conn)
    assert result.category == CATEGORY_LIGHT
    assert "已达成" in result.headline


def test_guidance_is_deterministic(conn):
    good_sleep(conn)
    service.record_feedback(conn, feedback_date=TODAY, energy=4, fatigue=2, today=TODAY)
    first = guidance(conn)
    second = guidance(conn)
    assert (first.category, first.focus, first.headline) == (
        second.category,
        second.focus,
        second.headline,
    )


def test_no_forbidden_advice_in_any_category(conn):
    scenarios = []

    # insufficient
    scenarios.append(guidance(conn))

    # rest（不适）
    service.record_feedback(conn, feedback_date=TODAY, discomfort=True, today=TODAY)
    scenarios.append(guidance(conn))

    # light（睡眠偏短）
    add_days(conn, "night_sleep_minutes", [350, 360, 355])
    service.record_feedback(conn, feedback_date=TODAY, energy=4, fatigue=1, today=TODAY)
    scenarios.append(guidance(conn))

    for result in scenarios:
        assert_safe(
            (result.headline, result.focus, *result.alternatives, result.local_explanation)
        )


def test_high_resting_hr_is_only_evidence_not_a_diagnosis(conn):
    good_sleep(conn)
    add_days(conn, "resting_hr_bpm", [58, 59, 60, 58, 59, 58, 60, 58, 59, 60, 58, 59, 58, 60, 59])
    result = guidance(conn)
    assert result.category == CATEGORY_REGULAR
    hr_items = [item for item in result.evidence if "静息心率" in item.label]
    assert hr_items
    assert "诊断" not in hr_items[0].value_text


# ---------------- AI 解释：可选、失败降级、不可覆盖结论 ----------------


class FakeClient:
    def __init__(self, *, text: str = "", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.last_user = ""

    def complete(self, *, system: str, user: str, max_tokens: int = 600) -> LLMResult:
        self.last_user = user
        if self.error is not None:
            raise self.error
        return LLMResult(text=self.text, model="fake")


def _rest_guidance(conn):
    add_days(conn, "night_sleep_minutes", [250, 260, 255])
    service.record_feedback(
        conn,
        feedback_date=TODAY,
        energy=1,
        fatigue=5,
        discomfort=True,
        discomfort_note="站起来有点晕",
        today=TODAY,
    )
    return guidance(conn)


def test_ai_disabled_uses_local_explanation(conn):
    result = _rest_guidance(conn)
    explanation = explain(result, client=None)
    assert explanation.source == "local"
    assert explanation.text == result.local_explanation


def test_ai_timeout_falls_back_to_local(conn):
    result = _rest_guidance(conn)
    explanation = explain(result, client=FakeClient(error=LLMError("timeout", "超时")))
    assert explanation.source == "local"
    assert explanation.error == "llm:timeout"
    assert explanation.text == result.local_explanation


def test_ai_number_outside_digest_is_rejected(conn):
    result = _rest_guidance(conn)
    explanation = explain(result, client=FakeClient(text="建议今天训练 999 分钟。"))
    assert explanation.source == "local"
    assert explanation.error and explanation.error.startswith("unknown_numbers")


def test_ai_forbidden_advice_is_rejected(conn):
    result = _rest_guidance(conn)
    explanation = explain(result, client=FakeClient(text="今天可以尝试高强度训练。"))
    assert explanation.source == "local"
    assert explanation.error and explanation.error.startswith("forbidden_term")


def test_ai_diagnostic_claim_is_rejected(conn):
    result = _rest_guidance(conn)
    explanation = explain(result, client=FakeClient(text="这种情况可以诊断为过度训练。"))
    assert explanation.source == "local"
    assert explanation.error == "diagnostic_claim"


def test_ai_valid_text_is_used_without_changing_conclusion(conn):
    result = _rest_guidance(conn)
    explanation = explain(
        result, client=FakeClient(text="昨晚睡眠偏短，今天先以恢复为主，别急着加练。")
    )
    assert explanation.source == "ai"
    assert result.category == CATEGORY_REST
    assert result.headline == "今天以休息为主，不安排训练"


def test_digest_contains_no_ids_or_absolute_dates(conn):
    result = _rest_guidance(conn)
    digest = build_digest(result)
    assert "2026-" not in digest
    for item in result.evidence:
        for record_id in item.record_ids:
            assert record_id not in digest
        for session_id in item.session_ids:
            assert session_id not in digest


def test_explanation_does_not_send_raw_notes(conn):
    result = _rest_guidance(conn)
    digest = build_digest(result)
    assert "站起来有点晕" not in digest  # 备注原文不进入摘要


def test_config_override_changes_rule_outcome(conn):
    """目标可配置：把参考值调低后，同一份睡眠不再是"偏短"。"""
    add_days(conn, "night_sleep_minutes", [350, 360, 355])
    strict = guidance(conn, config=GuidanceConfig(sleep_target_minutes=420))
    relaxed = guidance(conn, config=GuidanceConfig(sleep_target_minutes=360))
    assert strict.category == CATEGORY_LIGHT
    assert relaxed.category == CATEGORY_REGULAR
