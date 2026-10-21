"""页面注册表：导航、链接网格、表盘扇区的**唯一事实源**。

新增一个页面时只需要在这里加一条 `Page`，侧栏导航、「普通入口」链接网格、
表盘扇区（若给 `dial_label`）会自动包含它；再在 `ui/app.py` 注册路由、加模板即可。

为什么单独一个模块：
- 以前导航写死在 `base.html`、链接写死在 `chronos.html`、扇区写死在 `app.py` 三处，
  加一个页面要改三个文件，忘一处就出现"导航里有、表盘里没有"的漂移；
- 现在所有"页面清单"都从这里派生，测试 `tests/test_ui_modularity.py` 守着三处一致。

不放在这里的东西：
- 页面**数据**（指标卡、参照范围、表盘预览值）在 `ui/context.py`，它们随数据库变化；
- 页面**长相**在 `templates/`，改版式不需要动本文件。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Page:
    """一个页面在界面上的全部"身份信息"。"""

    key: str                 # 稳定标识（模板/测试用，不要随意改）
    href: str                # 路由地址
    label: str               # 侧栏与链接网格显示的名字
    group: str               # 侧栏分组
    dial_label: str = ""     # 表盘上的短名；空 = 不进表盘
    dial_idx: int = -1       # 表盘扇区序号（0 = 12 点方向，顺时针）


#: 侧栏分组顺序（也是渲染顺序）
NAV_GROUPS: tuple[str, ...] = ("总览", "分析", "数据", "其他")

#: 全部界面页面（顺序 = 侧栏顺序）
PAGES: tuple[Page, ...] = (
    Page("dashboard", "/", "首页", "总览"),
    Page("sleep", "/sleep", "睡眠分析", "分析", "睡眠", 0),
    Page("trends", "/trends", "指标趋势", "分析", "趋势", 1),
    Page("reports", "/reports", "报告", "分析", "报告", 4),
    Page("records", "/records", "记录", "数据"),
    # T11 裁决：表盘六个刻度为 睡眠/趋势/训练/提醒/报告/设置；导入是低频动作，留在普通入口
    Page("training", "/training", "训练", "数据", "训练", 2),
    Page("imports", "/imports", "导入", "数据"),
    Page("conflicts", "/conflicts", "冲突", "数据"),
    Page("alerts", "/alerts", "提醒", "其他", "提醒", 3),
    Page("settings", "/settings", "设置与隐私", "其他", "设置", 5),
    Page("chronos", "/chronos", "时钟模式", "其他"),
)

#: 表盘扇区（按 dial_idx 排序）
DIAL_PAGES: tuple[Page, ...] = tuple(sorted((p for p in PAGES if p.dial_label), key=lambda p: p.dial_idx))


def nav_groups() -> list[tuple[str, list[Page]]]:
    """[(分组名, [页面...]), ...]，供侧栏模板直接迭代。"""
    return [(group, [p for p in PAGES if p.group == group]) for group in NAV_GROUPS]


def link_grid_pages() -> list[Page]:
    """「普通入口」链接网格：除当前表盘页以外的所有页面。"""
    return [p for p in PAGES if p.key != "chronos"]


def page_by_key(key: str) -> Page:
    for page in PAGES:
        if page.key == key:
            return page
    raise KeyError(f"未注册的页面: {key}")
