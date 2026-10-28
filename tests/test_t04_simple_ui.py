"""T04 Simple 模式：参照范围行、阈值只读表、导入三步条、提醒两种空态、触控与焦点。

覆盖 T03 设计契约中本轮必须实现的部分：
- 参照范围四态（active / insufficient / no_data / range_bound）与"怎么算的"展开说明；
- 偏离基线只作中性文字标记，不着红绿（不用危险色）；
- 设置页阈值全部来自环境变量、只读，不放假控件；
- 导入三步条与预览页"可直接确认"区、五桶重排；
- 提醒页区分"从未评估"与"评估过但无新增"；
- 触控命中区 ≥44px 与 focus-visible（CSS 静态断言）。
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from health_agent.config import load_settings
from health_agent.storage.db import init_db
from health_agent.storage.lifecycle import confirm_record
from health_agent.storage.repository import insert_record, seed_catalog
from health_agent.ui.app import create_app

from conftest import make_record, read_css



def _client(tmp_path) -> TestClient:
    return TestClient(create_app(load_settings({}, project_root=tmp_path), db_path=tmp_path / "ui.db"))


def _home_in_detail_mode(client) -> str:
    """首页现在默认是星环；参照范围行是"详细列表"里的组件，先切过去再看。

    这不是放宽断言：参照范围行的四态、算法与文案一个字都没改，
    只是它所在的那一页不再是默认首页。
    """
    client.post("/settings/mode", data={"mode": "simple", "next_path": "/"}, follow_redirects=False)
    return client.get("/").text


def _home_in_detail_mode(client) -> str:
    """首页现在默认是星环；参照范围行是"详细列表"里的组件，先切过去再看。

    这不是放宽断言：参照范围行的四态、算法与文案一个字都没改，
    只是它所在的那一页不再是默认首页。
    """
    client.post("/settings/mode", data={"mode": "simple", "next_path": "/"}, follow_redirects=False)
    return client.get("/").text


def _seed_sleep(tmp_path, values: list[float]) -> None:
    """按给定顺序灌入已确认的夜间睡眠记录（列表第 0 个是今天）。"""
    conn = init_db(tmp_path / "ui.db")
    seed_catalog(conn)
    today = date.today()
    for offset, value in enumerate(values):
        day = (today - timedelta(days=offset)).isoformat()
        record_id = insert_record(
            conn,
            make_record(metric_name="night_sleep_minutes", unit="min", record_date=day, value=value),
            status="pending",
        )
        confirm_record(conn, record_id)
    conn.close()


# ---------------- 参照范围行（T03 契约） ----------------

def test_reference_row_active_shows_personal_baseline_and_how(tmp_path):
    """15 天有波动的值 → 基线可用：显示个人基线区间，且"怎么算的"可展开。"""
    # 取值 398–402 循环：MAD 不为 0，走 median_mad 主路径
    _seed_sleep(tmp_path, [400 + (i % 5) - 2 for i in range(15)])
    with _client(tmp_path) as client:
        body = _home_in_detail_mode(client)

    assert "个人基线" in body
    assert "参照" in body
    # 展开说明必须真的解释计算方式，而不是只写"个人基线"四个字
    assert "怎么算的" in body
    assert "中位数" in body
    assert "2.5×MAD" in body
    # 分母是基线窗口（28 天），不是播种天数——如实报告覆盖率
    assert "近 28 天中位数" in body
    assert "有效 15/28 天" in body


def test_reference_row_iqr_fallback_is_disclosed(tmp_path):
    """MAD 退化为 0（过半取值相同）时走 IQR 兜底，且必须如实写明方法（ERR-009）。"""
    _seed_sleep(tmp_path, [400] * 8 + [390] * 7)
    with _client(tmp_path) as client:
        body = _home_in_detail_mode(client)

    assert "个人基线" in body
    assert "2.5×IQR/1.349" in body
    assert "MAD 退化时兜底" in body
    assert "2.5×MAD）" not in body


def test_reference_row_insufficient_states_reason(tmp_path):
    """5 天样本 < 14 天 → 明说数据不足与原因，不显示任何区间。"""
    _seed_sleep(tmp_path, [400, 405, 395, 402, 398])
    with _client(tmp_path) as client:
        body = _home_in_detail_mode(client)

    assert "数据不足" in body
    assert "有效记录 5/14 天，样本不足" in body
    assert "个人基线" not in body


def test_reference_row_no_data_says_so(tmp_path):
    """从未记录 → "暂无数据"，不造区间。"""
    with _client(tmp_path) as client:
        body = _home_in_detail_mode(client)

    assert "暂无数据" in body
    assert "个人基线" not in body


def test_reference_row_absent_for_range_bound_metric(tmp_path):
    """区间端点指标不算均值，因此也没有参照范围（结构断言，不匹配裸文本）。"""
    _seed_sleep(tmp_path, [400] * 15)
    with _client(tmp_path) as client:
        body = client.get("/trends?metric=hrv_min_ms").text

    assert "不显示参照范围" in body
    assert 'class="ref-row"' not in body


def test_sleep_page_reference_card_is_separate_from_target(tmp_path):
    """睡眠页：参照范围与"目标"必须是两张信息，文案说清差异。"""
    _seed_sleep(tmp_path, [400 if i % 2 else 390 for i in range(15)])
    with _client(tmp_path) as client:
        body = client.get("/sleep").text

    assert "参照范围" in body
    assert "个人基线，动态计算" in body
    assert "目标是期望值" in body
    assert "历史基线" in body


def test_deviation_marker_is_neutral_text(tmp_path):
    """最新值偏离基线时只加中性文字标记，不用危险/警告色类。"""
    values = [300] + [400 if i % 2 else 390 for i in range(14)]
    _seed_sleep(tmp_path, values)
    with _client(tmp_path) as client:
        body = _home_in_detail_mode(client)

    assert 'class="ref-dev"' in body
    assert "偏离基线" in body
    assert "ref-dev danger" not in body
    assert "ref-dev warn" not in body


# ---------------- 设置页：阈值只读 ----------------

def test_settings_thresholds_are_read_only_table(tmp_path):
    """阈值表展示环境变量来源；页面不得出现任何可改阈值的输入控件。"""
    with _client(tmp_path) as client:
        body = client.get("/settings").text

    assert "提醒阈值" in body
    for env in (
        "HEALTH_AGENT_SLEEP_TARGET_MIN",
        "HEALTH_AGENT_BASELINE_WINDOW_DAYS",
        "HEALTH_AGENT_BASELINE_MIN_SAMPLES",
        "HEALTH_AGENT_BASELINE_MAD_FACTOR",
        "HEALTH_AGENT_DATA_GAP_RATE",
        "HEALTH_AGENT_RULE_COOLDOWN_HOURS",
    ):
        assert env in body, env
    assert "本页只读" in body
    # 假控件禁令：没有任何 input/select 的 name 对应阈值字段
    for field in (
        "sleep_target_minutes",
        "baseline_window_days",
        "baseline_min_samples",
        "baseline_mad_factor",
        "data_gap_rate",
        "rule_cooldown_hours",
    ):
        assert f'name="{field}"' not in body, field


def test_settings_has_privacy_section(tmp_path):
    """设置页有"数据与隐私"分区，说清数据只在本机。"""
    with _client(tmp_path) as client:
        body = client.get("/settings").text

    assert "数据与隐私" in body
    assert "不上传任何服务器" in body


# ---------------- 导入：三步条与五桶重排 ----------------

def test_imports_step_bar_marks_first_step(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/imports").text

    assert "step-bar" in body
    assert body.count("<li class=\"step") == 3
    assert "1. 上传" in body
    assert "is-current" in body


def test_import_preview_step_two_and_accepted_table(tmp_path):
    """预览页：第二步高亮；无可标记行也有"可直接确认"区；操作条说明写入后仍要确认。"""
    clean_csv = (
        "metric_name,record_date,value,unit,period_type\n"
        "resting_hr_bpm,2026-10-19,62,bpm,day\n"
        "resting_hr_bpm,2026-10-18,63,bpm,day\n"
    ).encode("utf-8")
    with _client(tmp_path) as client:
        upload = client.post(
            "/imports/upload",
            files={"file": ("clean.csv", clean_csv, "text/csv")},
            follow_redirects=False,
        )
        assert upload.status_code == 303
        body = client.get(upload.headers["location"]).text

    assert "2. 预览（不写库）" in body
    assert "可直接确认（2）" in body
    assert "写入后仍需在「记录」页确认" in body
    # 五桶顺序：冲突 → 待复核 → 拒绝 → 重复 → 可直接确认（需要裁决的排最前）
    labels = re.findall(r'<span class="stat-label">([^<]+)</span>', body)
    assert labels == ["总行数", "冲突（需裁决）", "待复核", "拒绝", "重复跳过", "可直接确认"]


def test_import_preview_warns_pending_after_commit(tmp_path):
    clean_csv = (
        "metric_name,record_date,value,unit,period_type\n"
        "resting_hr_bpm,2026-10-19,62,bpm,day\n"
    ).encode("utf-8")
    with _client(tmp_path) as client:
        upload = client.post(
            "/imports/upload",
            files={"file": ("clean.csv", clean_csv, "text/csv")},
            follow_redirects=False,
        )
        body = client.get(upload.headers["location"]).text

    assert "全部为 pending" in body
    assert "确认前不计入任何趋势" in body


def test_import_preview_shows_field_name_and_raw_to_normalized(tmp_path):
    """预览每行给中文名+字段名、原始值→归一值，便于对照源文件（设计 §4.4）。"""
    sample = (
        Path(__file__).resolve().parents[1] / "test_data" / "sample_with_issues.csv"
    ).read_bytes()
    with _client(tmp_path) as client:
        upload = client.post(
            "/imports/upload",
            files={"file": ("sample.csv", sample, "text/csv")},
            follow_redirects=False,
        )
        body = client.get(upload.headers["location"]).text

    # 字段名（给查数据字典的人看）
    assert "night_sleep_minutes" in body
    # 原始值 → 归一值：6.5 小时换算成 390 分钟、时长文本 1:23 解析成 83 分钟
    assert "6.5 h → 6 小时 30 分" in body
    assert "1:23 min → 1 小时 23 分" in body
    # 单位别名归一后只显示友好值（12000 次 → count 显示为"12,000 步"）
    assert "12,000 步" in body


def test_import_preview_rejected_shows_all_codes(tmp_path):
    """拒绝区列出全部错误码（可复制），不是只给第一条。"""
    sample = (
        Path(__file__).resolve().parents[1] / "test_data" / "sample_with_issues.csv"
    ).read_bytes()
    with _client(tmp_path) as client:
        upload = client.post(
            "/imports/upload",
            files={"file": ("sample.csv", sample, "text/csv")},
            follow_redirects=False,
        )
        body = client.get(upload.headers["location"]).text

    assert "E_UNKNOWN_METRIC" in body
    assert "错误码可直接选中复制" in body
    # 坏日期行给出原因
    assert "日期" in body


# ---------------- 提醒页：两种空态 ----------------

def test_alerts_empty_state_before_any_evaluation(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/alerts").text

    assert "还没有提醒" in body
    assert "没有可显示的提醒" not in body


def test_alerts_empty_state_after_evaluation_with_nothing_new(tmp_path):
    """评估过、只是当前没有可显示的 → 另一种文案（含冷却期说明）。"""
    _seed_sleep(tmp_path, [400, 405])  # 近 7 天只有 2 天 → 触发 data_gap
    with _client(tmp_path) as client:
        client.post("/alerts/run", follow_redirects=False)
        page = client.get("/alerts").text
        alert_id = re.search(r'/alerts/([0-9a-f]{32})/read', page).group(1)
        client.post(f"/alerts/{alert_id}/read", follow_redirects=False)
        body = client.get("/alerts?unread=1").text

    assert "没有可显示的提醒" in body
    assert "72 小时冷却期" in body
    assert "还没有提醒" not in body


def test_alerts_filter_by_rule(tmp_path):
    """按规则筛选：命中的规则显示，未命中的规则回到空态文案。"""
    _seed_sleep(tmp_path, [400, 405])
    with _client(tmp_path) as client:
        client.post("/alerts/run", follow_redirects=False)
        hit = client.get("/alerts?rule=data_gap").text
        miss = client.get("/alerts?rule=no_such_rule").text

    assert "data_gap" in hit
    assert "没有可显示的提醒" in miss


def test_alerts_page_has_persistent_info_card(tmp_path):
    """常驻说明卡：冷却规则与"不是诊断"声明（设计 §4.5）。"""
    with _client(tmp_path) as client:
        body = client.get("/alerts").text

    assert "alert-info" in body
    assert "同一窗口同规则只提醒一次" in body
    assert "不是诊断" in body


# ---------------- 设置页：版本与备份入口 ----------------

def test_settings_shows_version_and_schema(tmp_path):
    from health_agent import __version__

    with _client(tmp_path) as client:
        body = client.get("/settings").text

    assert __version__ in body
    assert "数据库 schema" in body
    assert "应用版本" in body


def test_settings_points_to_backup_runbook(tmp_path):
    """备份/恢复指向命令行与 runbook，不放假的一键备份按钮。"""
    with _client(tmp_path) as client:
        body = client.get("/settings").text

    assert "health_agent.cli backup" in body
    assert "docs/02-runbook.md" in body


# ---------------- 触控与焦点（CSS 静态断言） ----------------

def test_touch_targets_and_focus_ring_in_css():
    css = read_css()

    assert "--focus-ring:" in css
    assert ":focus-visible" in css
    # 主按钮命中区 ≥44px
    assert re.search(r"\.btn, button \{[^}]*min-height: 44px", css)
    # 行内小按钮视觉保持紧凑，用伪元素把命中区扩到 ≥44px
    assert re.search(r"\.mini::after \{[^}]*height: 44px", css)
    # 表单控件不低于 40px
    assert re.search(r"input, select, textarea \{[^}]*min-height: 40px", css)


def test_reference_row_css_exists():
    """参照范围行的样式存在，且不足态用警告色、active 态不用红绿。"""
    css = read_css()

    assert ".ref-row {" in css
    assert ".ref-row.is-insufficient" in css
    assert ".ref-dev" in css
    # 偏离标记不着红绿：颜色取中性文字色
    dev_rule = re.search(r"\.ref-dev \{[^}]*\}", css).group(0)
    assert "danger" not in dev_rule
    assert "var(--text-2)" in dev_rule
