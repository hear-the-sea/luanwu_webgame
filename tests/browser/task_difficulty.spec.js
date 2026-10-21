const { test, expect } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");

const root = path.resolve(__dirname, "../..");
const testOrigin = "http://task-difficulty.test";
let html;

test.beforeAll(() => {
  html = execFileSync("python", [path.join(__dirname, "fixtures/render_task_difficulty.py")], {
    cwd: root, env: { ...process.env, PYTHONPATH: root }, encoding: "utf8",
  });
});

async function setup(page, width) {
  await page.setViewportSize({ width, height: 844 });
  await page.route(`${testOrigin}/**`, route => route.fulfill({ contentType: "text/html", body: html }));
  await page.goto(`${testOrigin}/gameplay/tasks/`);
  for (const name of ["style", "tailwind", "tasks-difficulty"]) {
    await page.addStyleTag({ path: path.join(root, "static/css", `${name}.css`) });
  }
  for (const name of ["tasks_page_core", "tasks-page"]) {
    await page.addScriptTag({ path: path.join(root, "static/js", `${name}.js`) });
  }
}

for (const width of [781, 1280]) {
  test(`${width}px shows the difficulty column after the task name in every category`, async ({ page }) => {
    await setup(page, width);
    for (const tab of ["junior", "intermediate", "advanced", "series", "guild"]) {
      await page.locator(`[data-tab="${tab}"]`).click();
      const table = page.locator(`#tab-${tab} table`);
      await expect(table.locator("th").nth(1)).toHaveText("任务难度");
      await expect(table.locator("th").nth(1)).toBeVisible();
      await expect(table.locator("tbody tr").first().locator("td")).toHaveCount(5);
      await expect(table.locator("[data-task-difficulty]")).toHaveCount(0);
      await expect(table.locator("tbody tr").first().locator("td").nth(1).locator(".attr-icon").first()).toBeVisible();
    }
    await page.locator('[data-tab="junior"]').click();
    const star = page.locator("#tab-junior td.tw-mission-difficulty-col .attr-star");
    await expect(star).toHaveCount(1);
    expect(await star.evaluate(element => getComputedStyle(element).backgroundImage)).toContain("sprite");
    expect(await star.evaluate(element => getComputedStyle(element).backgroundPosition)).toBe("-48px 0px");
    await expect(page.locator("#tab-junior td.tw-mission-difficulty-col .task-difficulty-rating")).toHaveAttribute("title", "1");
    await page.locator('[data-tab="advanced"]').click();
    await expect(page.locator("#tab-advanced td.tw-mission-difficulty-col .attr-crown")).toHaveCount(2);
    await expect(page.locator("#tab-advanced td.tw-mission-difficulty-col .task-difficulty-rating")).toHaveAttribute("title", "128");
    await expect(page.locator("#tab-advanced td.tw-mission-difficulty-col .task-difficulty-pending")).toHaveText("待评估");
  });
}

for (const width of [320, 390, 780]) {
  test(width + "px hides difficulty and removes its button in every category", async ({ page }) => {
    await setup(page, width);
    for (const tab of ["junior", "intermediate", "advanced", "series", "guild"]) {
      await page.locator('[data-tab="' + tab + '"]').click();
      const table = page.locator("#tab-" + tab + " table");
      await expect(table.locator("th.tw-mission-difficulty-col")).toBeHidden();
      await expect(table.locator("[data-task-difficulty]")).toHaveCount(0);
      await expect(table.locator(".task-difficulty-trigger")).toHaveCount(0);
      await expect(table.locator(".tw-mission-name-link").first()).toBeVisible();
    }
    await expect(page.locator(".task-difficulty-popover")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}

for (const width of [781, 1280]) {
  test(width + "px keeps the longest mixed difficulty on a single line", async ({ page }) => {
    await setup(page, width);
    await page.locator('[data-tab="series"]').click();
    const rating = page.locator("#tab-series td.tw-mission-difficulty-col .task-difficulty-rating");
    await expect(rating).toBeVisible();
    for (const [icon, count] of Object.entries({ crown: 1, sun: 3, moon: 3, star: 3 })) {
      await expect(rating.locator(".attr-" + icon)).toHaveCount(count);
    }
    const bounds = await rating.locator(".attr-icon").evaluateAll(elements =>
      elements.map(element => {
        const rect = element.getBoundingClientRect();
        return { x: rect.x, y: rect.y, right: rect.right };
      })
    );
    expect(new Set(bounds.map(box => box.y)).size).toBe(1);
    const cell = await page.locator("#tab-series td.tw-mission-difficulty-col").boundingBox();
    expect(cell.width).toBeGreaterThanOrEqual(200);
    expect(bounds[0].x).toBeGreaterThanOrEqual(cell.x);
    expect(bounds[bounds.length - 1].right).toBeLessThanOrEqual(cell.x + cell.width);
    await expect(rating).toBeInViewport({ ratio: 1 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}
