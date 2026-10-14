const { test, expect } = require("@playwright/test");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");
const pools = (remaining, disabled = false) => `<section id="recruit-pools-section"><span>今日剩余 ${remaining} 次</span><form class="recruit-form" action="https://example.test/recruit" method="post" data-pool-name="殿试"><input name="pool" value="1"><button type="submit" ${disabled ? "disabled" : ""}>${disabled ? "今日已用尽" : "招募"}</button></form><form class="recruitment-card-form" action="https://example.test/use-card" method="post"><button type="submit">+</button></form></section>`;
async function setup(page) {
  await page.setContent(pools(1));
  await page.evaluate(() => {
    window.gameConfirm = async () => true;
    window.messages = [];
    window.gameDialog = { error: async m => window.messages.push(m), success: async () => {} };
  });
  await page.addScriptTag({ path: path.join(root, "static/js/recruitment_hall_core.js") });
  await page.addScriptTag({ path: path.join(root, "static/js/recruitment-hall.js") });
  await page.evaluate(() => document.dispatchEvent(new Event("partial-nav:loaded")));
}
for (const width of [390, 1280]) {
  test(`${width}px quota rejection replaces stale remaining count and disables draw`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    await setup(page);
    await page.route("https://example.test/recruit", route => route.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ success: false, error: "殿试今日招募次数已达上限（13次）", hall_pools_html: pools(0, true) }) }));
    await page.locator(".recruit-form button").click();
    await expect(page.locator("#recruit-pools-section")).toContainText("今日剩余 0 次");
    await expect(page.locator(".recruit-form button")).toBeDisabled();
    await expect.poll(() => page.evaluate(() => window.messages.length)).toBe(1);
  });
  test(`${width}px overlapping card and draw requests cannot overwrite quota`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    await setup(page);
    let release;
    const wait = new Promise(resolve => { release = resolve; });
    let requests = 0;
    await page.route("https://example.test/**", async route => {
      requests++;
      await wait;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ success: true, hall_pools_html: pools(0, true) }) });
    });
    await page.locator(".recruit-form button").click();
    await expect.poll(() => requests).toBe(1);
    await page.locator(".recruitment-card-form button").click();
    await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 50)));
    expect(requests).toBe(1);
    release();
    await expect(page.locator(".recruit-form button")).toBeDisabled();
  });
}
