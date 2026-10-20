"""鉴权与 CSRF。

设计取舍：单用户本地工具，不做账号体系。只有两种模式——
- 未设置密码：仅监听 127.0.0.1，直接可用（本机即信任边界）
- 设置了密码：必须登录，会话放在 HMAC 签名的 Cookie 里

Cookie 一律 HttpOnly + SameSite=Lax；所有写操作校验 CSRF token。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

SESSION_COOKIE = "ha_session"
SESSION_TTL_SECONDS = 12 * 3600
PBKDF2_ITERATIONS = 240_000


# ---------------- 密码 ----------------

def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


# ---------------- 会话签名 ----------------

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


class SessionSigner:
    """HMAC-SHA256 签名的最小会话实现，不引入额外依赖。"""

    def __init__(self, secret: bytes) -> None:
        if len(secret) < 16:
            raise ValueError("会话密钥至少 16 字节")
        self._secret = secret

    def sign(self, payload: dict) -> str:
        body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signature = hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{body}.{signature}"

    def unsign(self, token: str | None) -> dict | None:
        if not token or "." not in token:
            return None
        body, _, signature = token.rpartition(".")
        expected = hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return None
        try:
            payload = json.loads(_unb64(body))
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if float(payload.get("exp", 0)) < time.time():
            return None
        return payload

    def new_session(self) -> dict:
        return {
            "u": "local",
            "csrf": secrets.token_urlsafe(24),
            "exp": time.time() + SESSION_TTL_SECONDS,
        }


def load_secret(data_dir: Path) -> bytes:
    """密钥优先取环境变量，其次落在数据目录的隐藏文件里（权限由系统目录权限保证）。"""
    env_secret = os.environ.get("HEALTH_AGENT_SECRET")
    if env_secret:
        return env_secret.encode("utf-8")

    path = data_dir / ".session_secret"
    if path.is_file():
        return path.read_bytes()

    data_dir.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(32)
    path.write_bytes(secret)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return secret
