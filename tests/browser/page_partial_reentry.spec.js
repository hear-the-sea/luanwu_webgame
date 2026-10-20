const { expect, test } = require("@playwright/test");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");

async function replaceContent(page, html) {
  await page.evaluate((markup) => {
    document.body.innerHTML = markup;
    document.dispatchEvent(new Event("partial-nav:loaded"));
    document.dispatchEvent(new Event("partial-nav:loaded"));
  }, html);
}

test("registration limits and exact lineup validation survive partial page reentry", async ({ page }) => {
  const markup = `<section class="tw-arena-signup-panel" data-max-selected="2" data-required-selected="2">
    <form><input type="checkbox" class="arena-guest-checkbox" aria-label="甲">
    <input type="checkbox" class="arena-guest-checkbox" aria-label="乙">
    <input type="checkbox" class="arena-guest-checkbox" aria-label="丙">
    <button type="submit">报名</button></form></section>`;
  await page.setContent(markup);
  await page.evaluate(() => {
    window.errors = [];
    window.gameDialog = { error: async (message) => { window.errors.push(message); } };
  });
  await page.addScriptTag({ path: path.join(root, "static/js/arena-registration.js") });
  await replaceContent(page, markup);
  await page.getByRole("checkbox", { name: "甲" }).check();
  await page.getByRole("button", { name: "报名" }).click();
  expect(await page.evaluate(() => window.errors)).toEqual(["请恰好选择 2 名门客（当前已选 1 名）"]);
  await page.getByRole("checkbox", { name: "乙" }).check();
  await page.getByRole("checkbox", { name: "丙" }).click();
  await expect(page.getByRole("checkbox", { name: "丙" })).not.toBeChecked();
  expect(await page.evaluate(() => window.errors.length)).toBe(2);
});

test("forge decomposition retains a single confirmation after partial page reentry", async ({ page }) => {
  const markup = '<form class="js-decompose-form"><button type="submit">分解</button></form>';
  await page.setContent(markup);
  await page.evaluate(() => {
    window.prompts = 0;
    window.gameDialog = { danger: async () => { window.prompts += 1; return false; } };
  });
  await page.addScriptTag({ path: path.join(root, "static/js/forge-page.js") });
  await replaceContent(page, markup);
  await page.getByRole("button", { name: "分解" }).click();
  expect(await page.evaluate(() => window.prompts)).toBe(1);
});

test("shop pagination and currency choice initialize on replacement content without resubscription", async ({ page }) => {
  const markup = `<div class="luanwu-product-grid" style="display:grid;grid-template-columns:1fr;height:60px;overflow:hidden">
    <div class="luanwu-product-card" style="height:50px">甲</div>
    <div class="luanwu-product-card" style="height:50px">乙</div></div>
    <nav class="luanwu-shop__pagination" hidden><button data-shop-page-previous>上一页</button>
    <span data-shop-page-info></span><button data-shop-page-next>下一页</button></nav>
    <form class="luanwu-purchase-form" data-bound-coins="2" data-unbound-coins="3" data-price="1" data-product-name="材料">
    <select name="currency_mode" aria-label="币种"><option value="unbound_only">非绑定</option></select>
    <input name="quantity" value="1" aria-label="数量"><button type="submit">兑换</button></form>`;
  await page.setContent(markup);
  await page.evaluate(() => {
    window.prompts = 0;
    window.gameDialog = { confirm: async () => { window.prompts += 1; return null; } };
  });
  await page.addScriptTag({ path: path.join(root, "static/js/luanwu-shop.js") });
  await replaceContent(page, markup);
  await expect(page.locator("[data-shop-page-info]")).toHaveText("1 / 2");
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.locator("[data-shop-page-info]")).toHaveText("2 / 2");
  await page.getByRole("button", { name: "兑换", exact: true }).click();
  expect(await page.evaluate(() => window.prompts)).toBe(1);
});
