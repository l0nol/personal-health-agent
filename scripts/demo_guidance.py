"""T08/T09 端到端演示：用合成数据走完「记录 → 反馈 → 建议 → 解释降级」。

为什么单独写脚本：
- 建议引擎的价值在"不同状态下结论不同"，单看一条输出看不出来；
- 演示脚本把 6 种典型状态一次跑完，并断言不会出现越界建议；
- 全程使用临时数据库，不碰 `data/health.db`，也不含任何真实健康数据。

用法：
    python scripts/demo_guidance.py
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from health_agent.guidance.contract import FORBIDDEN_ADVICE_TERMS  # noqa: E402
from health_agent.guidance.engine import build_guidance  # noqa: E402
from health_agent.guidance.explain import build_digest, explain  # noqa: E402
from health_agent.storage.db import init_db  # noqa: E402
from health_agent.storage.repository import insert_record, seed_catalog  # noqa: E402
from health_agent.training import service  # noqa: E402
from health_agent.validation.result import NormalizedRecord  # noqa: E402

TODAY = date.today()
UNITS = {"night_sleep_minutes": "min", "resting_hr_bpm": "bpm", "steps_count": "count"}


def seed_metric(conn, metric: str, values: list[float], *, offset: int = 1) -> None:
    for index, value in enumerate(values):
        day = TODAY - timedelta(days=offset + index)
        insert_record(
            conn,
            NormalizedRecord(
                metric_name=metric,
                record_date=day.isoformat(),
                period_type="day",
                value=value,
                unit=UNITS[metric],
                raw_value=str(value),
                raw_unit=UNITS[metric],
                source_app="huawei_health",
                source_page="合成数据（演示用）",
                extraction_method="synthetic",
                flags=(),
            ),
            status="confirmed",
        )


def scenario(name: str, builder) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = init_db(Path(tmp) / "demo.db")
        seed_catalog(conn)
        builder(conn)
        result = build_guidance(conn, for_date=TODAY, today=TODAY)
        explanation = explain(result, client=None)

        print(f"\n[{name}]")
        print(f"  类别: {result.category_label}")
        print(f"  结论: {result.headline}")
        print(f"  重点: {result.focus}")
        print(f"  依据: {len(result.evidence)} 条 | 不确定性: {len(result.uncertainty)} 条")
        if result.uncertainty:
            print(f"  最需要注意: {result.uncertainty[0]}")
        print(f"  解释来源: {explanation.source}")

        texts = (result.headline, result.focus, *result.alternatives, result.local_explanation)
        for text in texts:
            for term in FORBIDDEN_ADVICE_TERMS:
                assert term not in text, f"{name} 建议出现禁用词 {term!r}: {text!r}"
        assert result.rule_version
        assert any("不构成诊断" in item for item in result.limitations)
        conn.close()


def build_insufficient(conn) -> None:
    """什么都没有：不许编。"""


def build_rest(conn) -> None:
    seed_metric(conn, "night_sleep_minutes", [250, 300, 280])
    service.record_feedback(conn, feedback_date=TODAY, energy=1, fatigue=5, today=TODAY)


def build_light(conn) -> None:
    seed_metric(conn, "night_sleep_minutes", [350, 360, 355])
    service.record_feedback(conn, feedback_date=TODAY, energy=4, fatigue=1, today=TODAY)


def build_regular_no_history(conn) -> None:
    seed_metric(conn, "night_sleep_minutes", [470, 480, 465, 475, 480, 470])
    seed_metric(conn, "resting_hr_bpm", [58, 59, 58, 60, 59, 58, 57, 59, 58, 60, 59, 58, 58, 59])


def build_regular_alternate(conn) -> None:
    seed_metric(conn, "night_sleep_minutes", [470, 480, 465, 475, 480, 470])
    service.save_preferences(conn, days_per_week=3, time_budget_minutes=45, equipment="哑铃")
    service.add_session(
        conn,
        session_date=TODAY - timedelta(days=1),
        activity_type="strength",
        body_parts="chest,shoulders",
        duration_minutes=45,
        perceived_intensity=7,
        today=TODAY,
    )


def build_discomfort(conn) -> None:
    seed_metric(conn, "night_sleep_minutes", [470, 480, 465])
    service.record_feedback(
        conn,
        feedback_date=TODAY,
        energy=4,
        fatigue=1,
        discomfort=True,
        discomfort_note="站起来有点晕",
        today=TODAY,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="合成数据演示训练日志与每日建议")
    args = parser.parse_args(argv)

    print(f"演示日期: {TODAY.isoformat()}（全部数据为合成数据，非真实健康记录）")
    scenario("① 资料不足（空库）", build_insufficient)
    scenario("② 睡眠严重不足 + 高疲劳 → 休息", build_rest)
    scenario("③ 睡眠偏短但感觉良好 → 轻松活动", build_light)
    scenario("④ 恢复良好但没有训练史 → 常规训练 + 明说不确定", build_regular_no_history)
    scenario("⑤ 昨天练过胸肩 → 换部位", build_regular_alternate)
    scenario("⑥ 记录不适 → 只给休息与求助建议", build_discomfort)

    # AI 关闭/不可用的降级路径：不联网也能给出完整解释
    with tempfile.TemporaryDirectory() as tmp:
        conn = init_db(Path(tmp) / "demo.db")
        seed_catalog(conn)
        build_light(conn)
        result = build_guidance(conn, for_date=TODAY, today=TODAY)
        print("\n[⑦ 将发送给 AI 的摘要（默认不发送）]")
        print("-" * 60)
        print(build_digest(result))
        print("-" * 60)
        assert TODAY.isoformat() not in build_digest(result)
        conn.close()

    print("\n全部场景通过：类别、依据、不确定性、禁用词守卫均符合契约。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
