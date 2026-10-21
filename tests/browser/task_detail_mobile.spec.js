const { test, expect } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");

const root = path.resolve(__dirname, "../..");
const testOrigin = "http://task-detail.test";
let html;

test.use({ isMobile: true, hasTouch: true });

test.beforeAll(() => {
  html = execFileSync("python", [path.join(__dirname, "fixtures/render_tasks_quick_launch.py"), "troops"], {
    cwd: root, env: { ...process.env, PYTHONPATH: root }, encoding: "utf8",
  });
});

async function setup(page) {
  await page.route(`${testOrigin}/**`, route => route.fulfill({ contentType: "text/html", body: html }));
  await page.goto(`${testOrigin}/gameplay/tasks/?mission=quick_launch_fixture`);
  for (const css of ["style", "tailwind"]) {
    await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
  }
  // 使用登录后的页面滚动容器，并模拟移动端合成层对固定弹窗的限制。
  await page.evaluate(() => {
    document.querySelector("#page-shell").className = "full-layout";
    const content = document.querySelector(".content-area");
    content.classList.remove("content-area--guest");
    const missionRow = document.querySelector("#tab-junior tbody tr");
    for (let index = 0; index < 40; index++) missionRow.parentElement.appendChild(missionRow.cloneNode(true));
    content.style.transform = "translateZ(0)";
    content.scrollTop = content.scrollHeight;
  });
  const maxHeight = await page.locator(".task-detail-dialog").evaluate(element => getComputedStyle(element).maxHeight);
  for (const script of ["dashboard", "tooltip", "tasks_page_core", "tasks-page"]) {
    await page.addScriptTag({ path: path.join(root, "static/js", `${script}.js`) });
  }
  return maxHeight;
}

for (const viewport of [{ width: 320, height: 568 }, { width: 390, height: 844 }, { width: 844, height: 390 }]) {
  test(`${viewport.width}x${viewport.height} task details stay reachable after touch scrolling without changing height`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const maxHeight = await setup(page);
    const overlay = page.locator("[data-task-detail-overlay]");
    const dialog = overlay.locator(".task-detail-dialog");
    const close = overlay.locator("[data-task-modal-close]").first();
    await expect(page.locator("body > [data-task-detail-overlay]")).toHaveCount(1);
    expect(await dialog.evaluate(element => getComputedStyle(element).maxHeight)).toBe(maxHeight);
    await expect(close).toBeInViewport({ ratio: 1 });

    const body = overlay.locator(".tw-modal-body");
    const box = await body.boundingBox();
    const touch = await page.context().newCDPSession(page);
    const start = { x: box.x + box.width / 2, y: box.y + box.height * 0.8 };
    await touch.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [start] });
    for (let step = 1; step <= 5; step++) {
      await touch.send("Input.dispatchTouchEvent", {
        type: "touchMove", touchPoints: [{ x: start.x, y: start.y - box.height * 0.1 * step }],
      });
    }
    await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await expect.poll(() => body.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
    let previousScrollTop = -1;
    await expect.poll(async () => {
      const scrollTop = await body.evaluate(element => element.scrollTop);
      const stopped = scrollTop === previousScrollTop;
      previousScrollTop = scrollTop;
      return stopped;
    }).toBe(true);
    await expect(close).toBeInViewport({ ratio: 1 });
    await close.tap();
    await expect(overlay).toHaveCount(0);
    await expect(page).toHaveURL(`${testOrigin}/gameplay/tasks/`);
    await page.locator('.tw-trade-tab[data-tab="advanced"]').tap();
    await expect(page.locator("#tab-advanced")).toBeVisible();
  });
}

test("tapping the backdrop closes details and dragging from the dialog does not", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page);
  const overlay = page.locator("[data-task-detail-overlay]");
  await overlay.locator(".tw-modal-header").dispatchEvent("pointerdown");
  await overlay.dispatchEvent("click");
  await expect(overlay).toHaveCount(1);
  const timeOrigin = await page.evaluate(() => performance.timeOrigin);
  await page.touchscreen.tap(2, 2);
  await expect(overlay).toHaveCount(0);
  await expect(page).toHaveURL(`${testOrigin}/gameplay/tasks/`);
  expect(await page.evaluate(() => performance.timeOrigin)).toBe(timeOrigin);
});

test("partial navigation removes the mounted overlay and repeated initialization binds once", async ({ page }) => {
  await setup(page);
  await page.evaluate(() => {
    for (let index = 0; index < 3; index++) document.dispatchEvent(new Event("partial-nav:loaded"));
  });
  await expect(page.locator("body > [data-task-detail-overlay]")).toHaveCount(1);
  await page.evaluate(() => {
    const next = document.createElement("div");
    next.id = "page-shell";
    next.innerHTML = '<button id="next-page">下一页</button>';
    document.querySelector("#page-shell").replaceWith(next);
    document.dispatchEvent(new Event("partial-nav:loaded"));
  });
  await expect(page.locator("[data-task-detail-overlay]")).toHaveCount(0);
  await page.locator("#next-page").tap();
});
