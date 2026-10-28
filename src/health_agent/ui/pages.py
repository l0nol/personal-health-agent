"""页面注册表：导航、链接网格、星环模块的**唯一事实源**。

新增一个页面时只需要在这里加一条 `Page`，侧栏导航与「普通入口」链接网格
会自动包含它；再在 `ui/app.py` 注册路由、加模板即可。

星环首页上的六个模块另有一张表 `RING_MODULES`：星星环模块不都是页面
（「恢复」没有独立页面，它指向趋势页的心率视图），所以不能直接复用 `Page`。
两张表都放在本模块，是为了让"界面上能到达的东西"只有一个出处。

为什么单独一个模块：
- 以前导航写死在 `base.html`、链接写死在 `chronos.html`、扇区写死在 `app.py` 三处，
  加一个页面要改三个文件，忘一处就出现"导航里有、星环里没有"的漂移；
- 现在所有"页面/模块清单"都从这里派生，测试 `tests/test_ui_modularity.py` 守着一致。

不放在这里的东西：
- 页面**数据**（指标卡、参照范围、模块摘要值）在 `ui/context.py`，它们随数据库变化；
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


#: 侧栏分组顺序（也是渲染顺序）
NAV_GROUPS: tuple[str, ...] = ("总览", "分析", "数据", "其他")

#: 全部界面页面（顺序 = 侧栏顺序）
PAGES: tuple[Page, ...] = (
    Page("dashboard", "/", "首页", "总览"),
    Page("sleep", "/sleep", "睡眠分析", "分析"),
    Page("trends", "/trends", "指标趋势", "分析"),
    Page("reports", "/reports", "报告", "分析"),
    Page("assistant", "/assistant", "AI 助手", "分析"),
    Page("records", "/records", "记录", "数据"),
    Page("training", "/training", "训练", "数据"),
    Page("imports", "/imports", "导入", "数据"),
    Page("conflicts", "/conflicts", "冲突", "数据"),
    Page("alerts", "/alerts", "提醒", "其他"),
    Page("settings", "/settings", "设置与隐私", "其他"),
    Page("chronos", "/chronos", "星环首页", "其他"),
)


def nav_groups() -> list[tuple[str, list[Page]]]:
    """[(分组名, [页面...]), ...]，供侧栏模板直接迭代。"""
    return [(group, [p for p in PAGES if p.group == group]) for group in NAV_GROUPS]


def link_grid_pages() -> list[Page]:
    """「普通入口」链接网格：除星环页自身以外的所有页面。"""
    return [p for p in PAGES if p.key != "chronos"]


def page_by_key(key: str) -> Page:
    for page in PAGES:
        if page.key == key:
            return page
    raise KeyError(f"未注册的页面: {key}")


# ---------------- 星环模块 ----------------

@dataclass(frozen=True, slots=True)
class RingModule:
    """星环上的一个模块。

    与 `Page` 刻意分开：`href` 是"看完摘要之后去哪儿"（可以是带查询参数的页面），
    而 `key` 是模块身份（同时决定配色与面板），两者不要求一一对应。
    """

    key: str          # 模块标识：sleep / recovery / training / trends / reports / assistant
    label: str        # 环上短标签
    idx: int          # 环上序号：0 = 12 点方向，顺时针，每格 60°
    href: str         # 详情入口
    panel_title: str  # 面板标题（比短标签长一点，能写"夜间睡眠"这种）
    accent: str       # CSS 变量名（不写具体色值：配色归 tokens.css 管）


#: 星环六模块。顺序即环上顺序（顺时针）。
#: 「恢复」不是独立页面：它把静息心率、HRV、主观反馈放在一起看，
#: 详情落到趋势页的心率视图，避免为了一个模块再造一页重复内容。
RING_MODULES: tuple[RingModule, ...] = (
    RingModule("sleep", "睡眠", 0, "/sleep", "夜间睡眠", "--c-sleep"),
    RingModule("recovery", "恢复", 1, "/trends?metric=resting_hr_bpm&days=30", "恢复与静息心率", "--c-recovery"),
    RingModule("training", "训练", 2, "/training", "训练与建议", "--c-training"),
    RingModule("reports", "报告", 3, "/reports", "当日报告", "--c-reports"),
    RingModule("assistant", "助手", 4, "/assistant", "AI 助手", "--c-assistant"),
    RingModule("trends", "趋势", 5, "/trends", "指标趋势", "--c-trends"),
)

#: 每个模块在环上的角度（0 = 12 点，顺时针 60°/格）
RING_STEP_DEG = 60


def ring_module(key: str) -> RingModule:
    for module in RING_MODULES:
        if module.key == key:
            return module
    raise KeyError(f"未注册的星环模块: {key}")


def ring_angles() -> dict[str, int]:
    return {module.key: module.idx * RING_STEP_DEG for module in RING_MODULES}

