# T05 — 时钟模式（Chronos）实现报告

- 任务：按 T03 已批准方案实现环形导航模式，替代原 Demo
- 前置：T04 已审核通过（用户 2026-10-20 18:20 回复"继续"）
- 日期：2026-10-20
- 状态：**待审核**

---

## 1. 一句话结论

时钟模式从"点击即跳转的 Demo"升级为**选择 → 预览 → 明确进入**的两步环形导航：
六扇区用原生 radio + label 实现（零 JS、无新增依赖），键盘方向键可选、中心预览与简洁模式**同一函数取数**、
选中时表盘旋转定位（可被"减少动态效果"关闭）、常规导航与链接网格始终可用。测试 **360 passed**，
无头 Chrome 实测 **14/14** 项交互断言通过（含 600–1440px 无横向滚动）。

## 2. 交互模型（为什么这样设计）

任务书要求"环形导航可选择功能，中心区域预览；明确操作后进入详情"+"不以旋转作为唯一交互"。
本项目铁律是**零 JS**，因此用原生单选组实现：

| 环节 | 实现 | 键盘 | 触屏/鼠标 |
|---|---|---|---|
| 选择 | 6 个视觉隐藏的 `<input type=radio>` + 6 个 `<label class="sector">` | Tab 聚焦后方向键切换 | 点 68px 扇区（≥44px） |
| 预览 | CSS `:checked ~ .dial-core [data-sector]` 显示对应面板 | 随选择即时更新 | 同左 |
| 进入 | 每扇区一个「进入X →」链接，`min-height: 44px` | Tab 到链接 + Enter | 点按 |
| 旋转 | 表盘按选中扇区转到 12 点方向，标签反向转回保持正立 | 同左 | 同左 |
| 降级 | 过渡只写在 `@media (prefers-reduced-motion: no-preference)` 内 | 瞬时切换 | 瞬时切换 |
| 故障兜底 | 若 CSS 选择器整体失效：6 扇区仍在、下方"普通入口"9 个链接与侧栏导航照常可用 | — | — |

**没有实现自由拖动旋转**，原因与依据：
- 项目零 JS 是既有硬约束（`docs/04-ui-design-spec-v1.md` §8 阶段 6"不引入 JS 依赖"）；
- 设计稿 R6 已写明"拖拽在触屏上的兼容单独评估，**若不稳则只保留点击切换**"；
- 因此旋转只作为"选中扇区定位动画"，不承诺拖拽；页面 legend 如实写明"拖动自由旋转未实现"。

## 3. 数据一致性（同口径，不是嘴上说说）

- 新增 `_dashboard_tiles(conn, end)`：首页与 `/chronos` **共用同一个函数**产生指标卡数据
  （同窗口 7 天、同 `summarize`、同 `format_number`）。首页 mode=chronos 分支与 `/chronos` 路由都调它。
- 表盘中心预览取值：睡眠/趋势 = 上述 tiles；导入/提醒/报告 = 首页同源计数
  （`count_pending` / `count_unread` / `count_confirmed`）；设置 = 静态事实（"时钟"），不造数字。
- `NULL` 显示 "—"（`format_number(None)`），不显示 0；pending 不进任何统计（沿用 `load_points` 只读 confirmed）。

## 4. 修改文件（5 个改动 + 2 个新增）

| 文件 | 改动 |
|---|---|
| `ui/templates/chronos.html` | 重写：radio + label 扇区、6 个中心预览面板、6 个「进入」链接、键盘与降级说明；去掉 Demo 徽标 |
| `ui/static/app.css` | 表盘选中态/旋转/反旋/面板显示规则（纯兄弟选择器，无 `:has` 依赖）；`overflow: hidden` 裁掉旋转扫出的角；过渡仅在 no-preference；删除 `badge-demo` |
| `ui/app.py` | 新增 `_dashboard_tiles()`（首页/表盘共用）与 `_chronos_sectors()`；`/chronos` 与首页 chronos 分支传同一套上下文（**修掉一处真实缺陷**：首页 chronos 分支原先没传 tiles/sectors，表盘会空转） |
| `ui/templates/settings.html` | 模式选项文案"时钟模式（Demo）"→"时钟模式"（与真实能力一致） |
| `tests/test_ui_pages.py` | `test_chronos_demo_page` → `test_chronos_page_structure`（断言升级为 radio/label/enter 结构）；模式选项断言同步 |
| `tests/test_t05_chronos.py`（新增） | 14 条自动化测试 |
| `scripts/verify_t05_browser.js`（新增） | 可选浏览器验证脚本（puppeteer-core + 本机 Chrome，开发工具不入运行时依赖） |

## 5. 测试结果

### 5.1 自动化（pytest，服务端 + HTML 结构 + CSS 静态断言）

**截图证据**（本地留档，按 `.gitignore` 的 `*.png` 规则不入库，与"截图不进仓库"的既有隐私约定一致）：
`docs/preview/t05-chronos-default.png`（默认选中睡眠）、
`docs/preview/t05-chronos-keyboard.png`（方向键切到趋势、焦点环落在选中扇区）。

- **总数 360 / 通过 360 / 失败 0 / 跳过 0 / 警告 1（starlette 第三方）/ 退出码 0**（T04 后 346 → +14）
- `tests/test_t05_chronos.py` 14 条：六 radio 与 label 一一对应、中心面板与进入链接一一对应且 href 正确、
  扇区不是链接（两步交互）、普通入口 9 链接齐全、中心值与首页**同一字符串**、缺失显示"—"、
  单位正确（次/分、步）、pending 不入预览、模式切换后首页与 `/chronos` 同值、
  过渡只在 `prefers-reduced-motion: no-preference` 内、6 个 checked 状态各有 CSS 规则、
  无"健康分/评分"类词汇、POST 405、有密码时跳登录。
- 重名扫描 `grep -h "^def test_" tests/*.py | sort | uniq -d` → 无输出。

### 5.2 模拟/浏览器（无头 Chrome 129，puppeteer-core 驱动本机 Chrome）

`node scripts/verify_t05_browser.js` → **14/14 passed**：

| 断言 | 结果 |
|---|---|
| 默认显示睡眠预览、隐藏其余 | PASS |
| 点「趋势」→ 趋势预览显示、睡眠隐藏 | PASS |
| 环 rotate(-60deg)（矩阵 0.5,-0.866,…） | PASS |
| 点「设置」→ 设置预览 | PASS |
| 方向键从设置切回睡眠 | PASS |
| 焦点环只画在当前扇区（不落在旋转后的错位） | PASS |
| 点「进入睡眠」→ 跳转 `/sleep` | PASS |
| 减少动态：`transition-duration: 0s` + 即时切换 | PASS |
| 600 / 768 / 800 / 900 / 1024 / 1440px 均无横向滚动 | PASS |
| 窄屏仍为 6 扇区 | PASS |

浏览器验证过程中修掉两个只在真实渲染里才暴露的缺陷：

1. **旋转包围盒溢出**：整块方形环旋转 120° 后包围盒为 1.366 倍（768→1049px），把文档撑出横向滚动
   （800px 下 scrollWidth=925）。修复：`.dial { overflow: hidden }`（扇区最大外径 182px < 容器半宽）。
2. **焦点环错位**：隐藏 radio 不随环旋转，键盘聚焦时浏览器默认焦点框画在**未旋转的位置**，
   看起来像落在别的扇区。修复：radio `opacity: 0` + `pointer-events: none`，
   焦点环改为 6 条 `:focus-visible ~ label[for=…]` 规则画在会旋转的 label 上。

### 5.3 真实 Android 平板

**未执行**。本机无平板、无真机调试环境；触控结论仅来自"命中区 68px ≥ 44px"的静态事实与鼠标点击等效路径。
列为未验证项，见 §7。

### 5.4 性能

- 服务端渲染：`/chronos` 25.8ms/次（28 天 × 4 指标 112 条已确认数据，TestClient 5 次均值），与首页 26.2ms 持平。
- 前端：零 JS、无定时器、无持续动画；只有选中切换时一次 450ms 的 CSS transform 过渡（`prefers-reduced-motion` 下为 0s）。
- 一次页面加载只有 1 个 HTML + 1 个 CSS 请求（静态资源已缓存则由浏览器处理）。

## 6. 兼容性限制

| 项 | 说明 |
|---|---|
| 浏览器 | 依赖 CSS 自定义属性、`aspect-ratio`、`:focus-visible`、`translateY()` 内的 `calc()`、`prefers-reduced-motion`。Chrome/Edge 90+、Safari 15+、华为浏览器（Chromium 内核）应可用；**未在旧内核上实测** |
| 未使用 `:has()` | 选择态全部用兄弟选择器实现，避免老内核不支持 `:has()` 导致表盘失效 |
| 旋转方向 | 选中第 6 扇区时等价 -300°，从第 5 扇区切过去是顺转 60°、从第 1 扇区切过去会逆转 300°（视觉上多转一圈，不影响功能） |
| 无 JS | 页面不存在任何脚本；表盘交互完全由 CSS 与原生表单控件完成 |

## 7. 未验证项目

1. 真实 Android 平板（华为浏览器）触控、软键盘、横竖屏切换。
2. Safari/Firefox 实际渲染（当前仅 Chrome 129 实测）。
3. 屏幕阅读器（VoiceOver/TalkBack）朗读顺序与"单选组"语义播报。
4. 高对比度/强制颜色模式下的表盘可读性。

## 8. 剩余风险

- R1：表盘旋转动画在低端平板上可能掉帧（450ms 一次的 transform，风险低，未实测）。
- R2：`overflow: hidden` 会裁掉扇区悬停上移 3px 的最外缘（视觉细节，无功能影响）。
- R3：选中第 6 扇区时切换方向可能"绕远路"（见 §6）。

## 9. 门禁申请

T05 完成，申请审核。审核通过后进 T06（最终回归与交付检查）。
