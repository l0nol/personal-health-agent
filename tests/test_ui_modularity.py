"""模块化守门：页面注册表、导航一致性、样式分层、模板扩展点。

这些测试不验业务，只验"结构没走回头路"：
- 页面清单只有 pages.py 一处事实源，导航/链接网格/表盘不得各写一份；
- 样式分层（tokens/base/components/pages）不被重新揉回 app.css；
- base.html 保留扩展块，方便后续加脚本/样式/导航项而不改框架。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import read_css
from health_agent.config import load_settings
from health_agent.ui.app import create_app
from health_agent.ui.pages import DIAL_PAGES, NAV_GROUPS, PAGES, link_grid_pages, nav_groups, page_by_key

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "src" / "health_agent" / "ui" / "templates"
STATIC = ROOT / "src" / "health_agent" / "ui" / "static"


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(load_settings({}, project_root=tmp_path), db_path=tmp_path / "ui.db"))


# ---------------- 页面注册表 ----------------

def test_registry_has_unique_keys_and_hrefs():
    keys = [p.key for p in PAGES]
    hrefs = [p.href for p in PAGES]
    assert len(keys) == len(set(keys))
    assert len(hrefs) == len(set(hrefs))
    assert set(p.group for p in PAGES) <= set(NAV_GROUPS)


def test_dial_pages_are_six_and_indexed_0_to_5():
    assert len(DIAL_PAGES) == 6
    assert [p.dial_idx for p in DIAL_PAGES] == list(range(6))


def test_link_grid_excludes_chronos_itself():
    keys = [p.key for p in link_grid_pages()]
    assert "chronos" not in keys
    assert len(keys) == len(PAGES) - 1


def test_every_registered_page_returns_200(tmp_path):
    with _client(tmp_path) as client:
        for page in PAGES:
            response = client.get(page.href)
            assert response.status_code == 200, page.href


def test_nav_groups_cover_all_pages():
    grouped = [p for _, items in nav_groups() for p in items]
    assert {p.key for p in grouped} == {p.key for p in PAGES}


def test_page_by_key_raises_for_unknown():
    with pytest.raises(KeyError):
        page_by_key("nope")


# ---------------- 模板不再硬编码页面清单 ----------------

def test_base_template_loops_registry_instead_of_hardcoding():
    source = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    # 除品牌链接（href="/"）外，导航 href 必须来自 nav_groups，不允许写死回模板
    for page in PAGES:
        if page.href == "/":
            continue
        assert f'href="{page.href}"' not in source, f"base.html 硬编码了 {page.href}"
    assert "nav_groups" in source


def test_chronos_link_grid_comes_from_registry():
    source = (TEMPLATES / "chronos.html").read_text(encoding="utf-8")
    assert "link_pages" in source
    for page in link_grid_pages():
        assert f'href="{page.href}"' not in source, f"chronos.html 硬编码了 {page.href}"


def test_rendered_nav_contains_every_page_label(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/").text
    for page in PAGES:
        assert page.label in body, page.label


def test_dial_sectors_match_registry(tmp_path):
    with _client(tmp_path) as client:
        body = client.get("/chronos").text
    for page in DIAL_PAGES:
        assert f'id="sector-{page.key}"' in body
        assert f'<label class="sector" for="sector-{page.key}"' in body
        assert f'href="{page.href}"' in body
        assert page.dial_label in body


# ---------------- 样式分层 ----------------

def test_app_css_is_entry_only():
    entry = (STATIC / "app.css").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", entry, flags=re.S)
    # 除 @import 外不应有任何规则
    assert "@import" in code
    leftovers = [line for line in code.splitlines() if line.strip() and not line.strip().startswith("@import")]
    assert leftovers == [], leftovers


def test_css_layers_exist_and_imported_in_order():
    entry = (STATIC / "app.css").read_text(encoding="utf-8")
    order = re.findall(r'@import url\("(css/[a-z]+\.css)"\);', entry)
    assert order == [
        "css/tokens.css",
        "css/base.css",
        "css/components.css",
        "css/pages.css",
    ]
    for rel in order:
        assert (STATIC / rel).is_file(), rel


def test_tokens_layer_has_no_rules_beyond_root():
    tokens = (STATIC / "css" / "tokens.css").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", tokens, flags=re.S)
    # tokens 只允许 :root 一个块
    blocks = re.findall(r"([^{}]+)\{", code)
    assert [b.strip() for b in blocks] == [":root"]


def test_core_tokens_are_documented():
    css = read_css()
    for token in ("--primary", "--accent", "--surface", "--text", "--focus-ring", "--r-card"):
        assert f"{token}:" in css, token


# ---------------- 模板扩展点 ----------------

def test_base_template_keeps_extension_blocks():
    source = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    for block in ("styles", "head", "scripts", "nav_extra", "body_class"):
        assert f"{{% block {block} %}}" in source, block


def test_templates_do_not_contain_inline_scripts():
    """零 JS 基线：任何模板里出现 <script> 都必须是经过裁决的（当前没有）。"""
    for path in TEMPLATES.glob("*.html"):
        source = re.sub(r"\{#.*?#\}", "", path.read_text(encoding="utf-8"), flags=re.S)
        assert "<script" not in source, path.name
