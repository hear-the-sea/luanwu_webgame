const { expect, test } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");
let html;
let emptyHtml;

test.beforeAll(() => {
  const fixture = path.join(__dirname, "fixtures/render_guild_assets.py");
  const options = { cwd: root, env: { ...process.env, PYTHONPATH: root }, encoding: "utf8" };
  html = execFileSync("python3", [fixture], options);
  emptyHtml = execFileSync("python3", [fixture, "--empty"], options);
});

async function prepare(page, content) {
  await page.setContent(content);
  for (const css of ["style", "tailwind", "guild"]) {
    await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
  }
}

for (const width of [320, 375, 1280]) {
  test(`${width}px assets show troop details and item exchange without overflow`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await prepare(page, html);
    await expect(page.getByRole("heading", { name: "帮会资产", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "护院详情", exact: true })).toBeVisible();
    await page.locator(".tw-guild-troop-summary").click();
    await expect(page.locator('[data-troop-key="dao_ke"]')).toContainText("现有 117 名");
    await expect(page.locator('[data-troop-key="qiang_ke"]')).toContainText("现有 57 名");
    const cavalry = page.locator('[data-troop-key="bloodthirsty_cavalry"]');
    await expect(cavalry).toContainText("现有 20 名");
    await expect(cavalry.locator('dt[title="待兑换"] + dd')).toHaveText("20");
    await expect(cavalry.locator('dt[title="训练中"] + dd')).toHaveText("10");
    const exchange = page.locator('form[action*="/experience_fruit/exchange/"]');
    await expect(exchange).toHaveCount(1);
    await expect(exchange.getByRole("button", { name: "兑换" })).toBeEnabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    if (width === 375) await page.screenshot({ path: test.info().outputPath("guild-assets-mobile.png"), fullPage: true, animations: "disabled" });
  });
}

test("empty assets explain how to obtain troops and items", async ({ page }) => {
  await prepare(page, emptyHtml);
  await page.locator(".tw-guild-troop-summary").click();
  await expect(page.getByText("暂无帮会护院", { exact: true })).toBeVisible();
  await expect(page.getByText("暂无可兑换物品", { exact: true })).toBeVisible();
  await expect(page.locator("[data-troop-key]")).toHaveCount(0);
});
