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
#: 默认模型。2026-10-21 用真实密钥核对：api.deepseek.com 实际可用的是
#: `deepseek-flash` 与 `deepseek-v4-pro`（`deepseek-chat` 会报"模型不存在"）。
DEFAULT_MODEL = "deepseek-flash"

#: 界面上的服务商预设。**只是帮你把两个字段填好**，不是白名单——
#: 任何 OpenAI 兼容端点（自建网关、本地 Ollama、其它云厂商）都能用，填自定义即可。
#: 这里的 URL 与模型名是静态参考值，可能随服务商变化，界面上允许直接改。
AI_PRESETS: tuple[dict[str, str], ...] = (
    {
        "key": "deepseek",
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-flash",
        "hint": "官方地址；实测可用 deepseek-flash / deepseek-v4-pro（后者是推理模型）",
    },
    {
        "key": "dashscope",
        "label": "通义千问（DashScope 兼容模式）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "hint": "阿里云百炼的 OpenAI 兼容入口；另有 qwen-turbo / qwen-max",
    },
    {
        "key": "moonshot",
        "label": "Moonshot / Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "hint": "老模型仍可用；新模型（kimi 系列）以「拉取可用模型」的结果为准",
    },
    {
        "key": "zhipu",
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "hint": "glm-4-flash 是免费档；另有 glm-4-air / glm-4-plus",
    },
    {
        "key": "openai",
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "hint": "国内网络通常需要代理或中转地址",
    },
    {
        "key": "ollama",
        "label": "本地 Ollama（无需密钥）",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
        "hint": "本机跑模型：先 `ollama pull qwen2.5:7b` 下载，再点「拉取可用模型」看本机到底有哪些",
    },
)


def preset_by_key(key: str) -> dict[str, str] | None:
    for preset in AI_PRESETS:
        if preset["key"] == key:
            return preset
    return None


def _first(env: Mapping[str, str], *keys: str) -> str | None:
    """按顺序取第一个非空值。

    `AI_*` 是通用名，`DEEPSEEK_*` 保留为兼容名——老 `.env` 不用改也能继续用。
    """
    for key in keys:
        raw = env.get(key)
        if raw is not None and str(raw).strip() != "":
            return str(raw)
    return None


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
    #: 本机配置文件路径。界面保存 AI 配置时写这里，`load_settings` 也从这里读。
    env_path: Path = field(default=PROJECT_ROOT / ".env")
    host: str = "127.0.0.1"
    port: int = 8000
    lan_enabled: bool = False
    password_hash: str | None = field(default=None, repr=False)
    ai_api_key: str | None = field(default=None, repr=False)
    ai_base_url: str = DEFAULT_BASE_URL
    ai_model: str = DEFAULT_MODEL
    ai_timeout_seconds: float = 20.0
    ai_max_retries: int = 2
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
        return bool(self.ai_api_key)

    @property
    def ai_key_tail(self) -> str:
        """密钥末尾 4 位，只用于界面确认"填的是哪一把"，不用于任何校验。"""
        key = (self.ai_api_key or "").strip()
        return key[-4:] if len(key) >= 12 else ""


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
        env_path=root / ".env",
        host=(env.get("HEALTH_AGENT_HOST") or "127.0.0.1").strip(),
        port=_as_int(env.get("HEALTH_AGENT_PORT"), 8000),
        lan_enabled=_as_bool(env.get("HEALTH_AGENT_LAN"), False),
        password_hash=(env.get("HEALTH_AGENT_PASSWORD_HASH") or "").strip() or None,
        ai_api_key=(_first(env, "AI_API_KEY", "DEEPSEEK_API_KEY") or "").strip() or None,
        ai_base_url=(
            _first(env, "AI_BASE_URL", "DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL
        ).strip(),
        ai_model=(_first(env, "AI_MODEL", "DEEPSEEK_MODEL") or DEFAULT_MODEL).strip(),
        ai_timeout_seconds=_as_float(
            _first(env, "AI_TIMEOUT_SECONDS", "DEEPSEEK_TIMEOUT_SECONDS"), 20.0
        ),
        ai_max_retries=_as_int(
            _first(env, "AI_MAX_RETRIES", "DEEPSEEK_MAX_RETRIES"), 2
        ),
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


#: 界面可写的 AI 配置键。写文件顺序即此顺序——同一个文件里两套名字并存时，
#: `AI_*` 在前、`DEEPSEEK_*` 在后，读的时候 `_first` 先命中 `AI_*`。
AI_ENV_KEYS = ("AI_API_KEY", "AI_BASE_URL", "AI_MODEL")


def _quote_env_value(value: str) -> str:
    """只有在会破坏 `KEY=VALUE` 解析时才加引号。"""
    if value != value.strip() or any(ch in value for ch in " \"#'"):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def save_ai_config(
    path: Path,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> None:
    """把 AI 配置写回 `.env`，文件里其它行与注释原样保留。

    三种输入语义，为的是不把"没填"误判成"清空"：

    - `None`  → 这一项不动
    - `""`    → 删掉这一行（用户主动清空）
    - 其它    → 覆盖写入

    写入是原子的：先写同目录临时文件再 `os.replace`，
    所以中途出错最多回到旧配置，不会留下半个 `.env`。
    文件权限尽量收紧到 0600（Windows 上 chmod 不生效，故忽略失败）。
    """
    updates: dict[str, str | None] = {
        "AI_API_KEY": api_key,
        "AI_BASE_URL": base_url,
        "AI_MODEL": model,
    }

    try:
        original = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else []
    except OSError:
        original = []

    lines: list[str] = []
    for raw in original:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            lines.append(raw)
            continue
        key = stripped.partition("=")[0].strip()
        if key not in updates:
            lines.append(raw)
            continue
        value = updates[key]
        if value is None:
            lines.append(raw)
            continue
        if value.strip() == "":
            continue  # 主动清空：连行一起删掉
        lines.append(f"{key}={_quote_env_value(value.strip())}")

    for key in AI_ENV_KEYS:
        value = updates[key]
        if value is None or value.strip() == "":
            continue
        if any(
            line.strip().partition("=")[0].strip() == key
            for line in lines
            if "=" in line and not line.strip().startswith("#")
        ):
            continue
        lines.append(f"{key}={_quote_env_value(value.strip())}")

    text = "\n".join(lines)
    if text:
        text += "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


#: 界面里允许"只填半截地址"的本地地址：这些默认走 http，其余默认 https。
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0"})


def normalize_base_url(raw: str) -> tuple[str, str]:
    """把用户填的接口地址补成合法 URL，返回 `(url, 说明)`。

    为什么在界面这一层做归一化：`.env` 里手写地址的人看得懂"要写完整 URL"，
    而在手机上敲键盘的人经常只敲 `api.deepseek.com/v1`。同一个语义，不该因为
    少打 8 个字符就报错——但补了什么必须明说，不能悄悄改。

    只做一件事：缺协议头时补上（本机地址补 http，其余补 https）。
    域名之外的东西不动，是否是合理网址由 `base_url_problem()` 单独判。
    """
    value = (raw or "").strip()
    if not value or value.startswith(("http://", "https://")):
        return value, ""
    host = value.split("/")[0].split(":")[0].lower()
    scheme = "http://" if host in _LOCAL_HOSTS else "https://"
    return f"{scheme}{value}", f"已补全 {scheme}"


def base_url_problem(url: str) -> str | None:
    """判断一个接口地址能不能用；能用就返回 None，否则返回一句人话。"""
    value = (url or "").strip()
    if not value:
        return "接口地址是空的"
    if value.lower().startswith("sk-"):
        return "这一格填的像是 API 密钥（以 sk- 开头），密钥请填到下面「API 密钥」那一格"
    if not value.startswith(("http://", "https://")):
        return f"接口地址缺协议头：现在填的是 {value!r}"
    rest = value.split("://", 1)[1]
    host = rest.split("/")[0].split("?")[0].split("#")[0]
    hostname = host.split(":")[0]
    if not hostname:
        return "接口地址里没有主机名"
    digits_like_ip = all(part.isdigit() for part in hostname.split(".") if part)
    if "." not in hostname and hostname.lower() not in _LOCAL_HOSTS and not digits_like_ip:
        # 不回显 hostname：这一格可能被误粘了密钥，报错信息不该把它带出去
        return "接口地址里的主机名不像网址（应形如 api.deepseek.com）"
    return None


def ai_env_override_keys(env: Mapping[str, str] | None = None) -> list[str]:
    """返回被进程环境变量直接覆盖的 AI 键名（只给键名，绝不带值）。

    用途：界面上刚保存的配置若被 shell 里的同名变量压住，要如实告诉用户
    "你保存了，但当前生效的不是它"——否则用户会以为自己的修改丢了。
    """
    source = os.environ if env is None else env
    found: list[str] = []
    for key in ("AI_API_KEY", "AI_BASE_URL", "AI_MODEL", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"):
        raw = source.get(key)
        if raw is not None and str(raw).strip() != "":
            found.append(key)
    return found


def active_preset_key(settings: Settings) -> str:
    """当前配置命中哪个预设；没命中就返回 `custom`（自定义/自建网关）。"""
    base = (settings.ai_base_url or "").rstrip("/")
    for preset in AI_PRESETS:
        if preset["base_url"].rstrip("/") == base:
            return preset["key"]
    return "custom"


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
    if settings.ai_timeout_seconds <= 0:
        raise ConfigError("AI 超时必须为正数秒")
    if settings.ai_max_retries < 0:
        raise ConfigError("AI 重试次数不能为负")
    if not settings.ai_base_url.startswith(("http://", "https://")):
        raise ConfigError("AI 接口地址必须以 http:// 或 https:// 开头")
