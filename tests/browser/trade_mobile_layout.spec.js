const { expect, test } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");

const root = path.resolve(__dirname, "../..");
const fixture = path.join(__dirname, "fixtures/render_trade_mobile_page.py");

for (const [view, section, labels] of [
  ["sell", "#market-sell-section", ["拥有数量", "参考价"]],
  ["my_listings", "#market-my-section", ["数量", "单价", "总价", "状态", "上架时间", "剩余时间"]],
  ["browse", "#auction-browse-section", ["中标名额", "最低中标价", "出价人数", "我的出价", "状态"]],
  ["my_bids", "#auction-my-bids-section", ["中标名额", "最低中标价", "我的出价", "状态", "出价金额", "出价时间"]],
]) {
  for (const width of [320, 375, 768, 1280]) {
    test(`${view} remains usable at ${width}px`, async ({ page }) => {
      const html = execFileSync("python", [fixture, view], { cwd: root, env: { ...process.env, PYTHONPATH: root }, encoding: "utf8" });
      await page.setViewportSize({ width, height: 700 });
      await page.setContent(html);
      for (const css of ["style", "tailwind", "chat_widget"]) {
        await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
      }
      const area = page.locator(section);
      expect(await area.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
      if (width <= 768) {
        for (const label of labels) {
          const cell = area.locator(`td[data-label="${label}"]`).first();
          await expect(cell).toBeVisible();
          expect(await cell.evaluate(el => getComputedStyle(el, "::before").content)).toBe(`"${label}"`);
        }
        expect(await area.locator(".tw-shop-table-wrapper").first().evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
      } else {
        await expect(area.locator("thead").first()).toBeVisible();
      }
      if (view === "sell") {
        await page.addScriptTag({ path: path.join(root, "static/js/trade_core.js") });
        await page.addScriptTag({ path: path.join(root, "static/js/trade.js") });
        await area.getByRole("button", { name: "上架", exact: true }).click();
        const dialog = page.getByRole("dialog", { name: "上架物品" });
        await expect(dialog).toBeVisible();
        expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
        await expect(dialog.getByLabel("上架数量")).toBeVisible();
        await expect(dialog.getByRole("button", { name: "确认上架" })).toBeVisible();
      }
    });
  }
}
