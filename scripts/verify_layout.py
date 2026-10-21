"""版面自检：三视口无横向滚动 + 圆形容器的内容四角落在容器内。

为什么单独写这个脚本：
- 只有 200 状态码和"看起来没问题"不足以证明版式没坏；
- 圆形容器（表盘中心）的裁切是"垂直方向够、四角不够"，肉眼容易漏；
- 这个脚本把"我这次修了什么"变成"下次谁都能复现的断言"。

用法（需要可选依赖 playwright，不装就跳过，不会拖垮测试）：

    python3 scripts/verify_layout.py \
      --url "file:///path/to/preview.html" \
      --sector "label[for=d2]" \
      --container ".center"

    python3 scripts/verify_layout.py --url "http://127.0.0.1:8000/"

退出码：0 通过 / 1 版面不达标 / 2 缺少 playwright（不算失败）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

VIEWPORTS = ((390, 844), (768, 1024), (1200, 900))

FIT_PROBE = """
([containerSel, sectorSel]) => {
  const result = {docWidth: document.documentElement.scrollWidth, viewport: innerWidth, fits: [], worst: null};
  const container = document.querySelector(containerSel);
  if (!container) return result;
  const c = container.getBoundingClientRect();
  const cx = (c.left + c.right) / 2, cy = (c.top + c.bottom) / 2, R = c.width / 2;
  /* 只检查圆形容器内部的卡片：页面里其它 .card（例如"普通入口"）不算 */
  const cards = [...document.querySelectorAll('.card, .fcard, .core-card')]
    .filter(el => getComputedStyle(el).display !== 'none')
    .filter(el => container.contains(el));
  const targets = [];
  for (const card of cards) {
    for (const el of card.children) targets.push(['.' + (el.className || el.tagName), el]);
    const extra = card.querySelector('.go a') || card.querySelector('.go');
    if (extra) targets.push(['.go', extra]);
  }
  for (const [name, el] of targets) {
    const r = el.getBoundingClientRect();
    let worst = -Infinity;
    for (const [x, y] of [[r.left, r.top], [r.right, r.top], [r.left, r.bottom], [r.right, r.bottom]]) {
      worst = Math.max(worst, Math.hypot(x - cx, y - cy) - R);
    }
    if (!isFinite(worst)) continue;
    result.fits.push({name, slack: +worst.toFixed(1)});
    result.worst = result.worst === null ? worst : Math.max(result.worst, worst);
  }
  return result;
}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="版面自检（横向滚动 + 圆形容器四角）")
    parser.add_argument("--url", required=True, help="要检查的页面地址（file:// 或 http://）")
    parser.add_argument("--sector", default="", help="选中态的选择器，如 label[for=d2]；留空只检查横向滚动")
    parser.add_argument("--container", default=".center", help="圆形容器选择器")
    parser.add_argument("--viewports", default="", help="自定义视口，格式 390x844,768x1024")
    args = parser.parse_args(argv)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP: 未安装 playwright，跳过版面自检（pip install playwright && playwright install chromium）")
        return 2

    viewports = VIEWPORTS
    if args.viewports:
        viewports = tuple(
            tuple(int(part) for part in item.split("x")) for item in args.viewports.split(",")
        )

    failures: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        for width, height in viewports:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(args.url)
            if args.sector:
                page.click(args.sector)
                page.wait_for_timeout(400)
            probe = page.evaluate(FIT_PROBE, [args.container, args.sector])
            doc = probe["docWidth"]
            if doc > width:
                failures.append(f"{width}px 视口出现横向滚动：scrollWidth={doc}")
            if args.sector and probe["fits"]:
                over = [item for item in probe["fits"] if item["slack"] > 0]
                if over:
                    failures.append(
                        f"{width}px 视口内容超出圆形容器："
                        + ", ".join(f"{item['name']}+{item['slack']:.0f}px" for item in over)
                    )
            print(
                json.dumps(
                    {
                        "viewport": f"{width}x{height}",
                        "doc_width": doc,
                        "worst_slack": None if probe["worst"] is None else round(probe["worst"], 1),
                        "checked": len(probe["fits"]),
                    },
                    ensure_ascii=False,
                )
            )
            page.close()
        browser.close()

    if failures:
        print("\nFAIL:")
        for item in failures:
            print("  -", item)
        return 1
    print("\nOK: 三视口无横向滚动且内容均在容器内" if args.sector else "\nOK: 三视口无横向滚动")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
