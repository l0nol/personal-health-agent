"""配置：默认值、密钥不外泄、局域网无密码必须拒绝启动。"""

from __future__ import annotations

import pytest

from health_agent.config import load_settings
from health_agent.errors import ConfigError


def test_defaults(tmp_path):
    settings = load_settings({}, project_root=tmp_path)
    assert settings.db_path == tmp_path / "data" / "health.db"
    assert settings.data_dir == tmp_path / "data"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.lan_enabled is False
    assert settings.ai_enabled is False
    assert settings.sleep_target_minutes == 420
    assert settings.baseline_window_days == 28
    assert settings.baseline_min_samples == 14


def test_lan_without_password_is_refused(tmp_path):
    with pytest.raises(ConfigError):
        load_settings({"HEALTH_AGENT_LAN": "1"}, project_root=tmp_path)


def test_lan_with_password_is_allowed(tmp_path):
    settings = load_settings(
        {"HEALTH_AGENT_LAN": "1", "HEALTH_AGENT_PASSWORD_HASH": "pbkdf2_sha256$demo"},
        project_root=tmp_path,
    )
    assert settings.lan_enabled is True


def test_api_key_never_appears_in_repr(tmp_path):
    settings = load_settings({"DEEPSEEK_API_KEY": "sk-test-should-not-leak"}, project_root=tmp_path)
    assert settings.ai_enabled is True
    assert "sk-test-should-not-leak" not in repr(settings)


def test_non_integer_port_is_refused(tmp_path):
    with pytest.raises(ConfigError):
        load_settings({"HEALTH_AGENT_PORT": "abc"}, project_root=tmp_path)


def test_baseline_window_must_cover_samples(tmp_path):
    with pytest.raises(ConfigError):
        load_settings(
            {
                "HEALTH_AGENT_BASELINE_WINDOW_DAYS": "7",
                "HEALTH_AGENT_BASELINE_MIN_SAMPLES": "14",
            },
            project_root=tmp_path,
        )


def test_absolute_db_path_is_respected(tmp_path):
    target = tmp_path / "elsewhere" / "custom.db"
    settings = load_settings({"HEALTH_AGENT_DB_PATH": str(target)}, project_root=tmp_path)
    assert settings.db_path == target


def test_rule_thresholds_flow_into_rule_config(tmp_path):
    from health_agent.rules.engine import config_from_settings

    settings = load_settings(
        {
            "HEALTH_AGENT_SLEEP_DEFICIT_DAYS": "5",
            "HEALTH_AGENT_RULE_COOLDOWN_HOURS": "24",
            "HEALTH_AGENT_DATA_GAP_RATE": "0.7",
            "HEALTH_AGENT_RESTING_HR_SHIFT_BPM": "10",
        },
        project_root=tmp_path,
    )
    config = config_from_settings(settings)

    assert config.sleep_deficit_min_days == 5
    assert config.cooldown_hours == 24
    assert config.data_gap_rate == 0.7
    assert config.resting_hr_shift_bpm == 10


def test_invalid_data_gap_rate_is_refused(tmp_path):
    with pytest.raises(ConfigError):
        load_settings({"HEALTH_AGENT_DATA_GAP_RATE": "1.5"}, project_root=tmp_path)

# ---------------- 0.1.1：配置来源与死配置清理 ----------------

def test_sleep_target_only_flagged_when_user_sets_it(tmp_path):
    default = load_settings({}, project_root=tmp_path)
    assert default.sleep_target_minutes == 420
    assert default.sleep_target_user_supplied is False

    explicit = load_settings({"HEALTH_AGENT_SLEEP_TARGET_MIN": "450"}, project_root=tmp_path)
    assert explicit.sleep_target_minutes == 450
    assert explicit.sleep_target_user_supplied is True


def test_blank_sleep_target_env_is_not_treated_as_user_value(tmp_path):
    settings = load_settings({"HEALTH_AGENT_SLEEP_TARGET_MIN": "   "}, project_root=tmp_path)
    assert settings.sleep_target_user_supplied is False
    assert settings.sleep_target_minutes == 420


def test_unused_timezone_option_was_removed(tmp_path):
    """0.1.0 里 HEALTH_AGENT_TZ 被读入却从未使用，0.1.1 已删除，避免死配置误导。"""
    settings = load_settings({"HEALTH_AGENT_TZ": "UTC"}, project_root=tmp_path)
    assert not hasattr(settings, "tz")


def test_env_file_is_read(tmp_path):
    """`.env` 是文档承诺的配置入口，必须真的被读取（2026-10-20 修）。"""
    (tmp_path / ".env").write_text(
        "# 注释行\n\nHEALTH_AGENT_PORT=8123\nDEEPSEEK_API_KEY=sk-test-only\n",
        encoding="utf-8",
    )
    settings = load_settings({}, project_root=tmp_path)

    assert settings.port == 8123
    assert settings.ai_enabled
    assert settings.deepseek_api_key == "sk-test-only"


def test_process_env_overrides_env_file(tmp_path):
    """命令行/系统环境变量优先级高于 .env，方便临时覆盖。"""
    (tmp_path / ".env").write_text("HEALTH_AGENT_PORT=8123\n", encoding="utf-8")

    settings = load_settings({"HEALTH_AGENT_PORT": "9000"}, project_root=tmp_path)

    assert settings.port == 9000


def test_env_file_tolerates_quotes_and_bad_lines(tmp_path):
    (tmp_path / ".env").write_text(
        'HEALTH_AGENT_SLEEP_TARGET_MIN="480"\n这不是一个键值行\n', encoding="utf-8"
    )

    settings = load_settings({}, project_root=tmp_path)

    assert settings.sleep_target_minutes == 480


def test_missing_env_file_is_not_an_error(tmp_path):
    settings = load_settings({}, project_root=tmp_path)  # 目录里没有 .env
    assert settings.port == 8000
