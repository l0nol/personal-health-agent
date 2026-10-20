"""从 catalog.py 生成数据字典 docs/01-data-dictionary.md。

为什么要脚本而不是手写：指标定义是唯一事实源，文档必须能一键重生成，
否则代码改了文档就会漂移（`tests/test_docs.py` 会在漂移时失败）。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from health_agent.catalog import (  # noqa: E402
    METRICS,
    RANGE_BOUND_METRICS,
    REPORT_METRICS,
)

HEADER = """# 数据字典 V1.1

> 本文件由 `scripts/gen_data_dictionary.py` 从 `src/health_agent/catalog.py` 生成，**不要手改**。
> 字段名遵循《技术规格书 V1.0》§4 的建议命名。

## 兼容性声明（请先读这一段）

- **本表未与任何设备厂商的官方字段说明逐项核对**，全部指标标记为 `provisional`。
  单位与值域来自常规生理范围与规格文字，**不构成"已与华为数据格式兼容"的声明**。
- `provisional` 的含义：值域可能偏严或偏松。超出范围时程序**只标记、不修正、不丢弃**
  （`out_of_range` 标记 + 需人工复核），因此误判的代价是"多看一眼"，不是数据损坏。
- 升级为 `device_verified` 的前提：用真实设备页面逐字段核对单位、字段是否存在、
  以及值域边界，并在 `catalog.py` 中显式改标记。

## 通用元数据（每条记录都带）

`record_id`、`record_date`、`metric_name`、`value`、`unit`、`period_type`、`period_start`、
`period_end`、`source_app`、`source_page`、`captured_at`、`extraction_method`、
`verification_status`、`notes`。

- `value` 为 `NULL` 表示**缺失或不可读**，绝不表示 0。
- `period_type` 是记录属性，不是聚合参数：`week` / `month` 记录永不参与日趋势。
- `verification_status` 四态：`pending` / `confirmed` / `rejected` / `superseded`。

## 统计周期口径

| period_type | 含义 | 是否进日趋势 | 是否进报告 |
|---|---|---|---|
| `instant` | 单次测量（血压、体温等） | 是（同一天多条取最后一条） | 仅当其指标 `in_report=1` |
| `day` | 当日汇总 | 是 | 是（若 `in_report=1`） |
| `week` | 设备周视图的平均/汇总 | **否**（只做对照，不可拆成日值） | 否 |
| `month` | 设备月视图 | **否** | 否 |

## 范围端点指标（不得推算平均值）

以下指标只表示"区间的某一端"：`{range_metrics}`。

设备只显示区间（如 HRV 47–82 ms）时，分别入库为下限与上限。
**禁止**用这两个值推算平均值——设备没给平均，平均值就是缺失。
这些指标 `in_report=0`，注册表在导入时会断言它们不能进入报告。

## 指标表

| 字段名 | 中文 | 标准单位 | 类型 | 合理范围 | 允许周期 | 等级 | 进报告 | 验证状态 | 说明 |
|---|---|---|---|---|---|---|---|---|---|
"""


def render_rows() -> str:
    lines = []
    for metric in METRICS:
        bounds = "—"
        if metric.min_valid is not None and metric.max_valid is not None:
            bounds = f"{metric.min_valid:g} ~ {metric.max_valid:g}"
        lines.append(
            "| `{name}` | {display} | `{unit}` | {kind} | {bounds} | {periods} | {tier} | {report} | {verification} | {desc} |".format(
                name=metric.name,
                display=metric.display,
                unit=metric.unit,
                kind=metric.kind,
                bounds=bounds,
                periods="/".join(metric.period_types),
                tier=metric.tier,
                report="是" if metric.in_report else "否",
                verification=metric.verification,
                desc=metric.description or "—",
            )
        )
    return "\n".join(lines)


FOOTER = """

## 本次统计

- 指标总数：{total}
- 进报告：{report_count}
- 范围端点（不进报告）：{range_count}
- 已与设备核对（`device_verified`）：0
- 暂定定义（`provisional`）：{provisional_count}

报告实际使用的指标：`{report_metrics}`
"""


def main() -> int:
    body = HEADER.format(range_metrics="、".join(f"`{name}`" for name in RANGE_BOUND_METRICS))
    body += render_rows()
    provisional = sum(1 for m in METRICS if m.verification == "provisional")
    body += FOOTER.format(
        total=len(METRICS),
        report_count=len(REPORT_METRICS),
        range_count=len(RANGE_BOUND_METRICS),
        provisional_count=provisional,
        report_metrics="`, `".join(REPORT_METRICS),
    )
    target = PROJECT_ROOT / "docs" / "01-data-dictionary.md"
    target.write_text(body, encoding="utf-8")
    print(f"已生成 {target.relative_to(PROJECT_ROOT)}：{len(METRICS)} 个指标")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
