const { expect, test } = require("@playwright/test");
const path = require("node:path");

const projectRoot = path.resolve(__dirname, "../..");

async function prepare(page, boundCoins = 4) {
  await page.setContent(`
    <form class="luanwu-purchase-form" data-bound-coins="${boundCoins}"
      data-unbound-coins="6" data-price="3" data-product-name="洗髓丹">
      <select name="currency_mode" aria-label="春秋币使用方式">
        <option value="unbound_only">仅使用非绑定币</option>
        <option value="allow_bound">允许使用绑定币</option>
      </select>
      <input name="quantity" type="number" min="1" max="3" value="2">
      <button type="submit">立即兑换</button>
    </form>`);
  await page.addStyleTag({ path: path.join(projectRoot, "static/css/style.css") });
  await page.addScriptTag({ path: path.join(projectRoot, "static/js/dialog.js") });
  await page.addScriptTag({ path: path.join(projectRoot, "static/js/luanwu-shop.js") });
  await page.evaluate(() => {
    window.submissions = [];
    document.querySelector("form").addEventListener("submit", (event) => {
      if (event.defaultPrevented) return;
      event.preventDefault();
      window.submissions.push(Object.fromEntries(new FormData(event.target)));
    });
  });
}

for (const width of [375, 1280]) {
  for (const [button, mode] of [["允许使用绑定币", "allow_bound"], ["仅使用非绑定币", "unbound_only"]]) {
    test(`${width}px currency choice submits ${mode} with the original quantity`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await prepare(page);
      await page.getByRole("button", { name: "立即兑换" }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await expect(page.locator(".game-dialog-message")).toContainText("需春秋币 6 枚");
      const box = await page.locator(".game-dialog").boundingBox();
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(width + 1);
      await page.getByRole("button", { name: button, exact: true }).click();
      await expect.poll(() => page.evaluate(() => window.submissions)).toEqual([{ currency_mode: mode, quantity: "2" }]);
      await page.getByRole("button", { name: "立即兑换" }).click();
      expect(await page.evaluate(() => window.submissions.length)).toBe(1);
    });
  }
}

for (const method of ["close", "escape"]) {
  test(`${method} cancels the exchange without submitting`, async ({ page }) => {
    await prepare(page);
    await page.getByRole("button", { name: "立即兑换" }).click();
    if (method === "close") await page.getByRole("button", { name: "关闭", exact: true }).click();
    else await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(await page.evaluate(() => window.submissions)).toEqual([]);
    await expect(page.locator('[name="quantity"]')).toBeEnabled();
  });
}

test("wallet without bound coins submits unbound only without a dialog", async ({ page }) => {
  await prepare(page, 0);
  await page.getByRole("button", { name: "立即兑换" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await page.evaluate(() => window.submissions)).toEqual([{ currency_mode: "unbound_only", quantity: "2" }]);
});

test("missing dialog keeps an explicit currency selector with an unbound default", async ({ page }) => {
  await page.setContent('<form class="luanwu-purchase-form"><select name="currency_mode"><option value="unbound_only">仅使用非绑定币</option></select><input name="quantity" value="1"></form>');
  await page.addScriptTag({ path: path.join(projectRoot, "static/js/luanwu-shop.js") });
  await expect(page.locator('[name="currency_mode"]')).toBeVisible();
  await expect(page.locator('[name="currency_mode"]')).toHaveValue("unbound_only");
});
