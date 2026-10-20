"""文档与代码的一致性防漂移：注册表里的指标必须出现在数据字典中。"""

from __future__ import annotations

from pathlib import Path

from health_agent.catalog import METRICS

DOC = Path(__file__).resolve().parents[1] / "docs" / "01-data-dictionary.md"


def test_data_dictionary_exists():
    assert DOC.is_file()


def test_every_metric_is_documented():
    text = DOC.read_text(encoding="utf-8")
    missing = [m.name for m in METRICS if m.name not in text]
    assert not missing, f"数据字典缺少指标: {missing}"


def test_data_dictionary_states_null_is_not_zero():
    text = DOC.read_text(encoding="utf-8")
    assert "NULL" in text and "绝不表示 0" in text


def test_version_is_consistent_across_files():
    """版本号只应有 pyproject 与 __init__ 两处，且必须一致。"""
    import tomllib

    import health_agent

    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == health_agent.__version__
