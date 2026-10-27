const { test, expect } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "../..");
let markup;

test.beforeAll(() => {
  markup = execFileSync("python", [path.join(__dirname, "fixtures/render_home_season.py")], {
    cwd: root,
    env: { ...process.env, PYTHONPATH: root },
    encoding: "utf8",
    timeout: 30_000,
  });
});

for (const width of [1280, 800, 390]) {
  test(`首页赛季队伍在 ${width}px 下倒计时与按钮保持完整`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.route("**/*", route => {
      const url = new URL(route.request().url());
      if (url.origin !== "http://home.test") return route.abort();
      if (url.pathname === "/") return route.fulfill({ contentType: "text/html", body: markup });
      if (!url.pathname.startsWith("/static/")) return route.fulfill({ status: 404, body: "" });
      const source = path.resolve(root, "static", url.pathname.slice(8));
      if (!source.startsWith(path.join(root, "static") + path.sep) || !fs.existsSync(source)) {
        return route.fulfill({ status: 404, body: "" });
      }
      return route.fulfill({ path: source });
    });
    await page.goto("http://home.test/");
    const events = page.locator("[data-season-home-event]");
    await expect(events).toHaveCount(3);
    await expect(events.first()).toContainText("赛季集结：河口古城");
    await expect(events.nth(1)).toContainText("赛季驻军：青石关");
    await expect(events.last()).toContainText("赛季采集：山脚驿站");
    await expect(events.last()).toContainText("采集已胜 2 / 5 场");
    await expect(events.last()).toContainText("等待第 3 场");
    await expect(events.last()).toContainText("下一场战斗");
    await expect(events.first().getByRole("button", { name: "取消集结" })).toBeVisible();
    await expect(events.last().getByRole("button", { name: "撤退", exact: true })).toBeVisible();
    for (const event of await events.all()) {
      await expect(event.locator(".countdown")).toHaveText(/\d{2}:\d{2}:\d{2}/);
      expect(await event.locator(".countdown").evaluate(element => element.getBoundingClientRect().height)).toBeLessThan(30);
      const link = event.getByRole("link", { name: "查看赛季队伍" });
      await expect(link).toHaveAttribute("href", "/manor/season/?season_id=1#season-team-status");
      const bounds = await event.evaluate(element => ({
        right: element.getBoundingClientRect().right,
        actionRight: element.querySelector("a").getBoundingClientRect().right,
      }));
      expect(bounds.actionRight).toBeLessThanOrEqual(bounds.right + 1);
      const button = event.getByRole("button");
      const buttonBounds = await button.boundingBox();
      expect(buttonBounds.x + buttonBounds.width).toBeLessThanOrEqual(bounds.right + 1);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `/tmp/home-season-events-${width}.png`, fullPage: true });
  });
}
