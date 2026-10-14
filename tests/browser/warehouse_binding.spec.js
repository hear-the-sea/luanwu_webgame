const { expect, test } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");
let html;

test.beforeAll(() => {
  html = execFileSync("python", [path.join(__dirname, "fixtures/render_warehouse_binding_page.py")], {
    cwd: root, env: { ...process.env, PYTHONPATH: root }, encoding: "utf8",
  });
});

async function prepare(page) {
  await page.setContent(html);
  for (const css of ["style", "tailwind"]) {
    await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
  }
}

for (const width of [320, 375, 1280]) {
  test(`${width}px warehouse shows binding suffix and separate quantities`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await prepare(page);
    const rows = page.locator(".tw-warehouse-table tbody tr");
    await expect(rows).toHaveCount(4);
    await expect(rows.nth(0).locator(".tw-item-name")).toHaveText("洗髓丹（绑定）");
    await expect(rows.nth(1).locator(".tw-item-name")).toHaveText("洗髓丹");
    await expect(rows.nth(0).locator("td").nth(2)).toHaveText("10");
    await expect(rows.nth(1).locator("td").nth(2)).toHaveText("3");
    await expect(rows.nth(0).getByRole("button", { name: "放入藏宝阁" })).toHaveCount(0);
    await expect(rows.nth(1).getByRole("button", { name: "放入藏宝阁" })).toBeEnabled();
    const cell = await rows.nth(0).locator(".tw-item-name").boundingBox();
    expect(cell.x + cell.width).toBeLessThanOrEqual(width + 1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  });
}

for (const mode of ["bound", "unbound"]) {
  test(`guest item modal submits the ${mode} group`, async ({ page }) => {
    await prepare(page);
    await page.evaluate(() => {
      window.captured = [];
      window.alert = () => {};
      window.gameDialog = { error: async () => {} };
      window.fetch = async (url, options) => {
        window.captured.push({ url, data: Object.fromEntries(options.body) });
        return { json: async () => ({ success: false, error: "浏览器测试拦截" }) };
      };
    });
    await page.addScriptTag({ path: path.join(root, "static/js/warehouse_page_core.js") });
    await page.addScriptTag({ path: path.join(root, "static/js/warehouse-page.js") });
    await page.evaluate(() => document.dispatchEvent(new Event("partial-nav:loaded")));
    await page.locator(`tr[data-binding-mode="${mode}"] .js-open-guest-select-modal`).click();
    await page.locator("#guest-list-xisuidan .tw-guest-option").click();
    await page.locator("#modal-confirm-btn").click();
    await expect.poll(() => page.evaluate(() => window.captured.length)).toBe(1);
    const result = await page.evaluate(() => window.captured[0]);
    expect(result.data).toMatchObject({ binding_mode: mode, guest_id: "201" });
  });
}

test("ordinary item forms carry each row's binding group", async ({ page }) => {
  await prepare(page);
  for (const mode of ["bound", "unbound"]) {
    await expect(page.locator(`tr[data-binding-mode="${mode}"] form[action*="/use/"] input[name="binding_mode"]`)).toHaveValue(mode);
  }
});
