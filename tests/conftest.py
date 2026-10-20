from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from health_agent.storage.db import init_db  # noqa: E402
from health_agent.storage.repository import seed_catalog  # noqa: E402
from health_agent.validation.result import NormalizedRecord  # noqa: E402


@pytest.fixture()
def db_file(tmp_path: Path) -> Path:
    return tmp_path / "health.db"


@pytest.fixture()
def conn(db_file: Path):
    connection = init_db(db_file)
    seed_catalog(connection)
    try:
        yield connection
    finally:
        connection.close()


def make_record(**overrides) -> NormalizedRecord:
    base = dict(
        metric_name="night_sleep_minutes",
        record_date="2026-10-01",
        period_type="day",
        value=402.0,
        unit="min",
        raw_value="402",
        raw_unit="min",
        source_app="huawei_health",
        source_page="睡眠-日视图",
        extraction_method="manual",
        flags=(),
    )
    base.update(overrides)
    return NormalizedRecord(**base)


#: 样式分层顺序（与 static/app.css 的 @import 一致）
_CSS_FILES = (
    "tokens.css",
    "base.css",
    "components.css",
    "pages.css",
)


def read_css() -> str:
    """读取全部样式（入口 app.css + 四个分层文件），供 CSS 静态断言使用。

    app.css 现在只是 @import 入口，具体规则在 static/css/ 下按层拆分；
    测试统一用本函数，避免"断言写死某个文件路径"。
    """
    static = ROOT / "src" / "health_agent" / "ui" / "static"
    parts = [(static / "app.css").read_text(encoding="utf-8")]
    parts += [(static / "css" / name).read_text(encoding="utf-8") for name in _CSS_FILES]
    return "\n".join(parts)
