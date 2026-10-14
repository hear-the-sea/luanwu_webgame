const { expect, test } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");
let html;
test.beforeAll(() => {
  html = execFileSync("python", [path.join(__dirname, "fixtures/render_arena_ring_history.py")], {
    cwd: root, env: { ...process.env, PYTHONPATH: root }, encoding: "utf8",
  });
});
for (const viewport of [{ width: 320, height: 568 }, { width: 390, height: 844 }, { width: 1280, height: 960 }]) {
  test(`${viewport.width}px ring history stays in two independent groups`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.route("**/*", route => {
      const url = decodeURIComponent(new URL(route.request().url()).pathname);
      if (url.endsWith("青龙擂-thumb.webp") || url.endsWith("白虎擂-thumb.webp")) {
        return route.fulfill({ path: path.join(root, url.replace(/^\/static\//, "static/")) });
      }
      return route.abort();
    });
    await page.setContent(html.replace("<head>", '<head><base href="https://preview.local/">'));
    for (const css of ["style", "tailwind", "chat_widget", "arena-rings"]) {
      await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
    }
    const groups = page.locator(".ring-history-group");
    await expect(groups).toHaveCount(2);
    for (const group of await groups.all()) {
      const bounds = await group.boundingBox();
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(viewport.width);
    }
    await expect(groups.first().locator("summary img")).toHaveJSProperty("naturalWidth", 176);
    await expect(groups.first().locator(".ring-history-content")).toBeHidden();
    await groups.first().locator("summary").click();
    await expect(groups.first().locator(".ring-history-record")).toHaveCount(10);
    await expect(groups.first().locator(".ring-history-content")).toBeVisible();
    await expect(groups.last().locator(".ring-history-content")).toBeHidden();
    await expect(groups.first().getByRole("link", { name: "下一页" })).toHaveAttribute("href", "?qinglong_page=2#ring-history-qinglong");
    await expect(groups.first().getByRole("link", { name: "查看战报" }).first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await groups.last().locator("summary").click();
    await expect(groups.last().locator(".ring-history-content")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await groups.last().locator("summary").click();
    await groups.first().locator("summary").scrollIntoViewIfNeeded();
    await page.screenshot({ path: `/tmp/arena-ring-history-${viewport.width}.png`, fullPage: true });
    await groups.first().locator("summary").focus();
    await page.keyboard.press("Enter");
    await expect(groups.first().locator(".ring-history-content")).toBeHidden();
  });
}
