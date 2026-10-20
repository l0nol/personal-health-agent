"""配置读取。

只从这里读环境变量，密钥不进入任何日志、异常信息或代码。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError

#: 项目根目录（src 的上一级）。
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


def read_env_file(path: Path) -> dict[str, str]:
    """读取 KEY=VALUE 形式的 .env 文件（零依赖，不引第三方库）。

    - 不存在或读不了 → 返回空字典，绝不因此启动失败
    - 忽略空行与 `#` 注释；允许值两侧带引号
    - 只做最朴素的解析：不支持多行值、变量插值等花哨语法
    """
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _as_bool(raw: str | None, default: bool) -> bool:
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(raw: str | None, default: int) -> int:
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"配置项应为整数，实际为 {raw!r}") from exc


def _as_float(raw: str | None, default: float) -> float:
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"配置项应为数字，实际为 {raw!r}") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    db_path: Path
    host: str = "127.0.0.1"
    port: int = 8000
    lan_enabled: bool = False
    password_hash: str | None = field(default=None, repr=False)
    deepseek_api_key: str | None = field(default=None, repr=False)
    deepseek_base_url: str = DEFAULT_BASE_URL
    deepseek_model: str = DEFAULT_MODEL
    deepseek_timeout_seconds: float = 20.0
    deepseek_max_retries: int = 2
    sleep_target_minutes: int = 420
    #: 用户是否显式配置过睡眠目标。用于区分"用户设定的目标"与"程序给的参考值"——
    #: 不区分这两者，就会在提醒里把默认值说成"你的目标"，这是误导。
    sleep_target_user_supplied: bool = False
    baseline_window_days: int = 28
    baseline_min_samples: int = 14
    baseline_mad_factor: float = 2.5
    rule_cooldown_hours: int = 72
    sleep_deficit_days: int = 3
    resting_hr_shift_bpm: float = 8.0
    data_gap_rate: float = 0.5

    @property
    def ai_enabled(self) -> bool:
        """是否配置了可用的 AI 密钥。未配置时全部走本地降级文案。"""
        return bool(self.deepseek_api_key)


def load_settings(
    env: Mapping[str, str] | None = None,
    *,
    project_root: Path | None = None,
) -> Settings:
    """从环境变量装载配置并做合法性校验。

    取值顺序：进程环境变量 > 项目根目录的 `.env` 文件 > 内置默认值。
    这样 `.env` 是"本机配置入口"，而临时覆盖（如测试、命令行前置变量）仍然有效。
    """
    root = PROJECT_ROOT if project_root is None else project_root
    merged = read_env_file(root / ".env")
    source = os.environ if env is None else env
    merged.update({key: str(value) for key, value in source.items()})
    env = merged

    data_dir = Path(env.get("HEALTH_AGENT_DATA_DIR", "data"))
    if not data_dir.is_absolute():
        data_dir = root / data_dir

    db_path = Path(env.get("HEALTH_AGENT_DB_PATH", str(data_dir / "health.db")))
    if not db_path.is_absolute():
        db_path = root / db_path

    settings = Settings(
        data_dir=data_dir,
        db_path=db_path,
        host=(env.get("HEALTH_AGENT_HOST") or "127.0.0.1").strip(),
        port=_as_int(env.get("HEALTH_AGENT_PORT"), 8000),
        lan_enabled=_as_bool(env.get("HEALTH_AGENT_LAN"), False),
        password_hash=(env.get("HEALTH_AGENT_PASSWORD_HASH") or "").strip() or None,
        deepseek_api_key=(env.get("DEEPSEEK_API_KEY") or "").strip() or None,
        deepseek_base_url=(env.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).strip(),
        deepseek_model=(env.get("DEEPSEEK_MODEL") or DEFAULT_MODEL).strip(),
        deepseek_timeout_seconds=_as_float(env.get("DEEPSEEK_TIMEOUT_SECONDS"), 20.0),
        deepseek_max_retries=_as_int(env.get("DEEPSEEK_MAX_RETRIES"), 2),
        sleep_target_minutes=_as_int(env.get("HEALTH_AGENT_SLEEP_TARGET_MIN"), 420),
        sleep_target_user_supplied=bool(
            (env.get("HEALTH_AGENT_SLEEP_TARGET_MIN") or "").strip()
        ),
        baseline_window_days=_as_int(env.get("HEALTH_AGENT_BASELINE_WINDOW_DAYS"), 28),
        baseline_min_samples=_as_int(env.get("HEALTH_AGENT_BASELINE_MIN_SAMPLES"), 14),
        baseline_mad_factor=_as_float(env.get("HEALTH_AGENT_BASELINE_MAD_FACTOR"), 2.5),
        rule_cooldown_hours=_as_int(env.get("HEALTH_AGENT_RULE_COOLDOWN_HOURS"), 72),
        sleep_deficit_days=_as_int(env.get("HEALTH_AGENT_SLEEP_DEFICIT_DAYS"), 3),
        resting_hr_shift_bpm=_as_float(env.get("HEALTH_AGENT_RESTING_HR_SHIFT_BPM"), 8.0),
        data_gap_rate=_as_float(env.get("HEALTH_AGENT_DATA_GAP_RATE"), 0.5),
    )
    validate_settings(settings)
    return settings


def validate_settings(settings: Settings) -> None:
    """配置互斥约束。宁可拒绝启动，也不要在无保护状态下暴露健康数据。"""
    if settings.lan_enabled and not settings.password_hash:
        raise ConfigError(
            "已开启局域网访问（HEALTH_AGENT_LAN=1）但未设置 HEALTH_AGENT_PASSWORD_HASH。"
            "健康数据不得在无鉴权状态下暴露给同网段设备。"
        )
    if not (1 <= settings.port <= 65535):
        raise ConfigError(f"端口不合法: {settings.port}")
    if settings.baseline_min_samples < 1:
        raise ConfigError("基线最少样本数必须 ≥ 1")
    if settings.baseline_window_days < settings.baseline_min_samples:
        raise ConfigError("基线窗口天数不得小于最少样本数")
    if settings.sleep_target_minutes <= 0:
        raise ConfigError("睡眠目标时长必须为正数（分钟）")
    if not 0 <= settings.data_gap_rate <= 1:
        raise ConfigError("缺失率阈值必须在 0~1 之间")
    if settings.rule_cooldown_hours < 0:
        raise ConfigError("提醒冷却期不能为负")
    if settings.sleep_deficit_days < 1:
        raise ConfigError("睡眠不足天数阈值至少为 1")
    if settings.resting_hr_shift_bpm <= 0:
        raise ConfigError("静息心率偏移阈值必须为正数")
    if settings.deepseek_timeout_seconds <= 0:
        raise ConfigError("AI 超时必须为正数秒")
    if settings.deepseek_max_retries < 0:
        raise ConfigError("AI 重试次数不能为负")
