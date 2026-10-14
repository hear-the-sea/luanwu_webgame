const { expect, test } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");

const projectRoot = path.resolve(__dirname, "../..");
let marketHtml;

test.beforeAll(() => {
  marketHtml = execFileSync("python", [path.join(__dirname, "fixtures/render_market_buy_page.py")], {
    cwd: projectRoot,
    env: { ...process.env, PYTHONPATH: projectRoot },
    encoding: "utf8",
  });
});

for (const width of [320, 375, 414, 768, 769, 1280]) {
  test(`market buying stays within the viewport and keeps actions at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.setContent(marketHtml);
    for (const css of ["style", "tailwind", "chat_widget"]) {
      await page.addStyleTag({ path: path.join(projectRoot, "static/css", `${css}.css`) });
    }
    const market = page.locator("#market-buy-section");
    const box = await market.boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(width + 1);
    expect(await market.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
    for (const selector of [".tw-filter-select", ".tw-pagination", ".tw-btn-trade"]) {
      const overflowing = await market.locator(selector).evaluateAll(elements => elements.some(el => {
        const bounds = el.getBoundingClientRect();
        return bounds.x < 0 || bounds.right > innerWidth + 1 || el.scrollWidth > el.clientWidth + 1;
      }));
      // Desktop tables keep their horizontal scrolling inside the table wrapper.
      if (width <= 768 || selector !== ".tw-btn-trade") expect(overflowing).toBe(false);
    }
    const rows = market.locator("tbody tr");
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(0).getByRole("button", { name: "购买", exact: true })).toBeEnabled();
    await expect(rows.nth(1).getByRole("button", { name: "购买", exact: true })).toBeDisabled();
    await expect(rows.nth(2).getByRole("button", { name: "无法购买", exact: true })).toBeDisabled();
    await expect(rows.nth(0).locator("form")).toHaveAttribute("method", "post");
    await expect(rows.nth(0).locator("form")).toHaveAttribute("action", /\/1\//);
    if (width <= 768) {
      for (const label of ["数量", "单价", "总价", "卖家", "剩余时间"]) {
        const content = await rows.nth(0).locator(`[data-label="${label}"]`).evaluate(
          el => getComputedStyle(el, "::before").content
        );
        expect(content).toBe(`"${label}"`);
      }
      await expect(rows.nth(0).getByRole("button", { name: "购买", exact: true })).toHaveCSS("min-height", "44px");
    } else {
      await expect(market.locator("thead")).toBeVisible();
    }
    await page.evaluate(() => {
      document.querySelector(".tw-market-buy-filters").addEventListener("submit", event => {
        event.preventDefault();
        window.__marketFilters = Object.fromEntries(new FormData(event.currentTarget));
      });
    });
    await page.addScriptTag({ path: path.join(projectRoot, "static/js/trade_core.js") });
    await page.addScriptTag({ path: path.join(projectRoot, "static/js/trade.js") });
    await page.getByLabel("类别", { exact: true }).selectOption("tool");
    expect(await page.evaluate(() => window.__marketFilters)).toMatchObject({
      tab: "market", view: "buy", category: "tool", rarity: "all", order_by: "-listed_at",
    });
  });
}
