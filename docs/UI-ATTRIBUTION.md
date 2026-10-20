# UI 组件来源与署名登记

本文件记录界面组件的来源、许可与修改情况。规则（T03 报告 §3.2 / 用户 2026-10-20 裁决）：

1. 只借鉴 `uiverse-io/galaxy` 的**单组件模式**，不引入构建链、不用 Tailwind 版、不用带 JS 的元素。
2. 每个实际复制过代码的组件必须在下方登记表留一行：来源 URL、创作者、许可、修改内容。
3. 未登记 = 未采用。自行实现不登记（本项目自有组件，MIT 之外的义务为零）。

## Galaxy 仓库核实记录（2026-10-20）

| 项 | 结论 | 来源 |
|---|---|---|
| 许可 | MIT（仓库级 LICENSE，2023-10-08 建立） | github.com/uiverse-io/galaxy LICENSE |
| 元素许可 | README 声明全部元素 MIT | 仓库 README "Licensing and Attribution" |
| 署名 | 非强制；希望同时标注创作者与 Uiverse.io | 同上 |
| 实现 | "Made with either CSS or Tailwind" 两类并存 | 仓库副标题 |

## T04 选型结论（2026-10-20）

八类评估（Cards / Buttons / Forms / Inputs / Toggle switches / Notifications / Tooltips / Loading indicators）
全部为**借鉴结构、自行实现**，未复制任何组件代码。逐类理由见 `reports/T03_UI_DESIGN_GALAXY_REPORT.md` §3.1。

净结果：复制代码 0 个、新增依赖 0 个、新增构建步骤 0 个。

## 采用登记表（当前为空）

| 组件 | 来源 URL | 创作者 | 许可 | 修改内容 | 采用日期 |
|---|---|---|---|---|---|
| （暂无） | | | | | |

## 采用后置门槛（T04/T05 及以后执行）

拟复制任一 Galaxy 元素代码前，必须逐条通过：

1. 纯 CSS 实现，无 JavaScript；
2. 非 Tailwind 版；
3. 键盘可达，有 focus-visible；
4. 触控命中区 ≥44×44（视觉可小，命中区不可小）；
5. 尊重 `prefers-reduced-motion`；
6. 浅色主题下对比度达标（正文 ≥4.5:1）；
7. 已在本表登记来源、创作者与修改内容。

任一条不过关 → 自行实现，不登记。
