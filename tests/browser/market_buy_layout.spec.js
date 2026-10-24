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
    for (let index = 0; index < 3; index += 1) {
      await expect(rows.nth(index).getByRole("button")).toHaveCount(1);
    }
    await expect(rows.nth(0).getByRole("button", { name: "购买", exact: true })).toBeEnabled();
    await expect(rows.nth(1).getByRole("button", { name: "购买", exact: true })).toBeEnabled();
    await expect(rows.nth(2).getByRole("button", { name: "购买", exact: true })).toBeDisabled();
    await expect(rows.locator('input[name="quantity"]')).toHaveCount(0);
    if (width <= 768) {
      for (const label of ["数量", "单价", "总价", "卖家", "剩余时间"]) {
        const content = await rows.nth(0).locator(`[data-label="${label}"]`).evaluate(
          el => getComputedStyle(el, "::before").content
        );
        expect(content).toBe(`"${label}"`);
      }
      await expect(rows.nth(0).getByRole("button", { name: "购买", exact: true })).toHaveCSS("min-height", "36px");
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
    const purchase = rows.nth(0).getByRole("button", { name: "购买", exact: true });
    await purchase.click();
    const dialog = page.getByRole("dialog", { name: "购买物品" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel("购买数量", { exact: true })).toBeFocused();
    const dialogBox = await dialog.boundingBox();
    expect(dialogBox.x).toBeGreaterThanOrEqual(0);
    expect(dialogBox.x + dialogBox.width).toBeLessThanOrEqual(width + 1);
    expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(purchase).toBeFocused();
    await page.getByLabel("类别", { exact: true }).selectOption("tool");
    expect(await page.evaluate(() => window.__marketFilters)).toMatchObject({
      tab: "market", view: "buy", category: "tool", rarity: "all", order_by: "-listed_at",
    });
  });
}


for (const mode of ["partial", "all"]) {
  test(`market purchase submits ${mode} with the correct quantity and keeps return state`, async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 900 });
    await page.setContent(marketHtml);
    await page.addScriptTag({ path: path.join(projectRoot, "static/js/trade_core.js") });
    await page.addScriptTag({ path: path.join(projectRoot, "static/js/trade.js") });
    await page.locator("#market-buy-section tbody tr").first().getByRole("button", { name: "购买", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "购买物品" });
    await expect(dialog).toBeVisible();
    const form = dialog.locator("[data-market-purchase]");
    await expect(form).toHaveAttribute("action", /\/market\/purchase\/1\//);
    await form.evaluate(el => el.addEventListener("submit", event => {
      event.preventDefault();
      window.__marketPurchase = Object.fromEntries(new FormData(el));
    }));
    if (mode === "partial") await dialog.getByLabel("购买数量", { exact: true }).fill("2");
    else await dialog.getByRole("button", { name: "全部", exact: true }).click();
    await expect(form.locator("[data-market-purchase-total]")).toHaveText("400");
    expect(await page.evaluate(() => window.__marketPurchase)).toBeUndefined();
    await dialog.getByRole("button", { name: "确认", exact: true }).click();
    expect(await page.evaluate(() => window.__marketPurchase)).toMatchObject({
      purchase_mode: mode, quantity: "2",
      category: "all", rarity: "all", page: "1234", order_by: "-listed_at",
    });
  });
}

test("market modal blocks invalid quantities and unaffordable totals, and resets for another listing", async ({ page }) => {
  await page.setContent(marketHtml);
  await page.addStyleTag({ path: path.join(projectRoot, "static/css/tailwind.css") });
  await page.addScriptTag({ path: path.join(projectRoot, "static/js/trade_core.js") });
  await page.addScriptTag({ path: path.join(projectRoot, "static/js/trade.js") });
  const rows = page.locator("#market-buy-section tbody tr");
  await rows.nth(0).getByRole("button", { name: "购买", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "购买物品" });
  const quantity = dialog.getByLabel("购买数量", { exact: true });
  const confirm = dialog.getByRole("button", { name: "确认", exact: true });
  for (const value of ["0", "3", "1.5", ""]) {
    await quantity.fill(value);
    await expect(confirm).toBeDisabled();
    await expect(dialog.locator("[data-market-purchase-total]")).toHaveText("—");
  }
  await quantity.fill("2");
  await expect(confirm).toBeEnabled();
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await rows.nth(1).getByRole("button", { name: "购买", exact: true }).click();
  await expect(quantity).toHaveValue("1");
  await expect(quantity).toHaveAttribute("max", "999999");
  await expect(dialog.locator("#market-purchase-item-name")).toHaveText("LongEquipmentNameWithoutSpaces".repeat(3));
  await dialog.getByRole("button", { name: "全部", exact: true }).click();
  await expect(quantity).toHaveValue("999999");
  await expect(confirm).toBeDisabled();
  await expect(dialog.locator("#market-purchase-hint")).toContainText("银两不足");
  await quantity.fill("2");
  await expect(confirm).toBeEnabled();
  await expect(dialog.locator('input[name="purchase_mode"]')).toHaveValue("partial");
});

test("market modal traps focus and prevents repeated confirmation", async ({ page }) => {
  await page.setContent(marketHtml);
  await page.addStyleTag({ path: path.join(projectRoot, "static/css/tailwind.css") });
  await page.addScriptTag({ path: path.join(projectRoot, "static/js/trade_core.js") });
  await page.addScriptTag({ path: path.join(projectRoot, "static/js/trade.js") });
  await page.locator("#market-buy-section tbody tr").first().getByRole("button", { name: "购买", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "购买物品" });
  const confirm = dialog.getByRole("button", { name: "确认", exact: true });
  await confirm.focus();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "关闭购买弹窗" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(confirm).toBeFocused();
  await dialog.locator("form").evaluate(form => {
    form.addEventListener("submit", event => {
      event.preventDefault();
      window.__purchaseAttempts = (window.__purchaseAttempts || 0) + 1;
    });
  });
  await confirm.click();
  await expect(dialog.getByRole("button", { name: "购买中…" })).toBeDisabled();
  await page.keyboard.press("Enter");
  expect(await page.evaluate(() => window.__purchaseAttempts)).toBe(1);
});

test("confirm performs one purchase POST with the chosen quantity", async ({ page }) => {
  const submissions = [];
  await page.route("https://market.example/**", async route => {
    const request = route.request();
    if (request.method() === "POST") {
      submissions.push({ url: request.url(), data: Object.fromEntries(new URLSearchParams(request.postData())) });
      await route.fulfill({ contentType: "text/html; charset=utf-8", body: "<p>购买成功</p>" });
    } else {
      await route.fulfill({ contentType: "text/html; charset=utf-8", body: request.url().endsWith("/trade/") ? marketHtml : "" });
    }
  });
  await page.goto("https://market.example/trade/");
  await page.addScriptTag({ path: path.join(projectRoot, "static/js/trade_core.js") });
  await page.addScriptTag({ path: path.join(projectRoot, "static/js/trade.js") });
  await page.locator("#market-buy-section tbody tr").first().getByRole("button", { name: "购买", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "购买物品" });
  await dialog.getByRole("button", { name: "全部", exact: true }).click();
  expect(submissions).toHaveLength(0);
  await dialog.getByRole("button", { name: "确认", exact: true }).click();
  await expect(page.getByText("购买成功")).toBeVisible();
  expect(submissions).toEqual([{
    url: "https://market.example/trade/market/purchase/1/",
    data: expect.objectContaining({ quantity: "2", purchase_mode: "all", csrfmiddlewaretoken: "layout-test-token", page: "1234" }),
  }]);
});
