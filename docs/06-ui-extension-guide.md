# UI 扩展与美化指南

> 给后续改界面的人（或 AI）：这份文件说明**改哪里、不用改哪里**。
> 原则：换皮不换数、加页面不改框架、加交互先保证降级可用。

---

## 1. 分层地图（先看这张）

```
ui/app.py        路由：取数、鉴权、表单转发。不写样式、不拼展示字符串
   ↓
ui/context.py    视图模型（Tile/Reference/Sector）+ 装配函数。模板能拿到哪些字段看这里
ui/pages.py      页面注册表：导航 / 链接网格 / 表盘扇区的唯一事实源
   ↓
ui/templates/    Jinja2 模板：只负责长相
ui/static/       样式四层（见下）
```

**数据只有一份口径**：模板里的数字全部来自 `context.py` 调用的
`summarize()` / `compute_baseline()` / 计数查询，换皮不会换数。

---

## 2. 样式分四层（美化从第 1 层开始）

`static/app.css` 只是入口，实际规则在 `static/css/`：

| 文件 | 放什么 | 什么时候改 |
|---|---|---|
| `css/tokens.css` | 颜色、字号、圆角、阴影、侧栏宽度、字体 | **换肤/调主题首先改这里**；不要在别处硬编码颜色 |
| `css/base.css` | reset、排版、应用外壳（侧栏/内容区/页头） | 改页面骨架时（所有页面都会受影响） |
| `css/components.css` | 卡片、指标卡、表格、标签、按钮、表单、图表、步骤条… | 改可复用组件外观时 |
| `css/pages.css` | 页面私有样式（当前：时钟模式表盘） | 新页面有独特版式时 |

常见美化动作：

```css
/* 想换主色：只改 tokens.css */
:root {
  --primary: #123456;       /* 主色 */
  --primary-soft: #E3EEF6;  /* 主色的浅底，用于选中态 */
  --accent: #0E8C7F;        /* 辅助色（正向） */
}
```

> 约定：一处语义一个 token。新增颜色先加到 `tokens.css` 再引用，
> 否则下次换肤会漏掉硬编码的那一处。

---

## 3. 新增一个页面（三步）

**第一步**：在 `ui/pages.py` 的 `PAGES` 里加一条：

```python
Page("weight", "/weight", "体重", "分析", "体重", 6)   # 最后两个参数 = 表盘短名、扇区序号
```

加完导航、时钟模式的「普通入口」链接网格会自动包含它；如果给了 `dial_label`，
表盘会多一个扇区（记得同步 `context.chronos_sectors()` 的预览映射，否则 KeyError 会提醒你）。

**第二步**：在 `ui/app.py` 注册只读路由并渲染模板：

```python
@app.get("/weight", response_class=HTMLResponse)
def weight_page(request: Request, conn=Depends(get_db)):
    end = latest_data_date(conn) or date.today()
    return render(request, "weight.html", tiles=dashboard_tiles(conn, end), end_day=end.isoformat())
```

**第三步**：新建 `templates/weight.html`，继承 `base.html`：

```jinja
{% extends "base.html" %}
{% block title %}体重{% endblock %}
{% block content %}
<h1>体重</h1>
{% from "_macros.html" import stat_card %}
<section class="tiles">{% for tile in tiles %}{{ stat_card(tile) }}{% endfor %}</section>
{% endblock %}
```

最后跑 `tests/test_ui_modularity.py`：它会自动检查新页面能 200、导航里有它、
模板没写死链接。**写页面时不要硬编码 href，一律用 `page.href` / `link_pages`。**

---

## 4. 新增一个可复用组件

1. 在 `templates/_macros.html` 加宏（只接收数据，不查库）；
2. 在 `css/components.css` 加样式，颜色/圆角全部用 token；
3. 给它写一条 HTML 断言测试（`tests/test_t04_simple_ui.py` 里有现成写法）。

命名约定：宏名不要与上下文变量同名（历史上 `chart` 互相遮蔽过，统一 `import chart as render_chart`）。

---

## 5. 想加 JS（默认零 JS）

项目默认零 JS，所有交互都能在无脚本下完成。确需脚本时：

1. 只在 `base.html` 的 `{% block scripts %}` 里加，不要散落在页面模板；
2. **渐进增强**：没有脚本时页面必须仍可用（导航、表单提交不能依赖脚本）；
3. 尊重 `prefers-reduced-motion`，不要做持续高频动画；
4. 不要在 GET 渲染里发起写操作或外部请求；
5. 加完把 `tests/test_ui_modularity.py::test_templates_do_not_contain_inline_scripts`
   放宽为"允许在 scripts 块内"，并在报告里写明理由。

---

## 6. 新增一个健康指标

指标是数据层的事，不动 UI：

1. `src/health_agent/catalog.py` 加一行 `_m(...)`（名称、显示名、单位、类型、值域、`in_report`）；
2. 跑 `python scripts/gen_data_dictionary.py` 重新生成 `docs/01-data-dictionary.md`
   （有测试检查"每个指标都在字典里"）；
3. 想让它出现在首页指标卡：改 `context.py` 的 `_TILE_METRICS`；
4. 想让它进报告：确认 `catalog` 里 `in_report=True`（`range_bound` 指标会被断言拦住）。

## 7. 新增一条提醒规则

1. `src/health_agent/rules/engine.py` 的 `ALL_RULES` 加规则函数（返回 reason / suggestion / evidence）；
2. 阈值从 `config.py` 的环境变量读，**不要写死数字**，也不要加"能在界面上改"的假控件；
3. 在 `tests/test_rules.py` 加触发/去重/冷却测试；
4. 提醒卡片会自动出现——模板按 `rule_id` 渲染，不需要改 UI。

---

## 8. 守门测试（改完跑一遍）

```bash
.venv/Scripts/python.exe -m pytest tests/test_ui_modularity.py   # 结构没走回头路
.venv/Scripts/python.exe -m pytest                               # 全量
```

`test_ui_modularity.py` 守着这些事：

- 页面清单只有 `pages.py` 一处，模板里不许出现写死的导航 href；
- 表盘扇区与注册表一致；
- `app.css` 只做 `@import`，四个分层文件按序存在；
- `tokens.css` 里只有 `:root`（别把规则塞进 token 层）；
- `base.html` 保留 `styles / head / scripts / nav_extra / body_class` 扩展块；
- 模板里没有 `<script>`（零 JS 基线）。
