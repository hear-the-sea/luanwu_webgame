const { test, expect } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");

const root = path.resolve(__dirname, "../..");
let html;
test.beforeAll(() => {
  html = execFileSync("python", [path.join(__dirname, "fixtures/render_guide_page.py")], {
    cwd: root, env: { ...process.env, PYTHONPATH: root }, encoding: "utf8",
  });
});

for (const width of [320, 390, 1280]) {
  test(`${width}px guide chapters are readable and navigation reaches each chapter`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.setContent(html);
    for (const css of ["style", "tailwind", "guide"]) {
      await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
    }
    await page.addScriptTag({ path: path.join(root, "static/js/guide-page.js") });
    const guide = page.locator("#guide-page");
    await expect(guide.locator(".guide-section")).toHaveCount(5);
    const links = guide.locator("[data-guide-nav] a");
    await expect(links).toHaveCount(5);
    await expect(guide.locator('.guide-section:visible')).toHaveCount(1);
    await expect(page.locator("#guests")).toBeVisible();
    await expect(links.first()).toHaveAttribute("aria-selected", "true");
    const reader = guide.locator(".guide-reader");
    expect(await reader.evaluate(el => el.scrollHeight > el.clientHeight)).toBe(true);
    await reader.hover();
    const navBefore = await guide.locator("[data-guide-nav]").boundingBox();
    await page.mouse.wheel(0, 250);
    await expect.poll(() => reader.evaluate(el => el.scrollTop)).toBeGreaterThan(0);
    const navAfter = await guide.locator("[data-guide-nav]").boundingBox();
    expect(navAfter.y).toBeCloseTo(navBefore.y, 0);
    await page.getByRole("tab", { name: "护院" }).click();
    expect(await reader.evaluate(el => el.scrollTop)).toBe(0);
    for (const anchor of await links.evaluateAll(elements => elements.map(el => el.getAttribute("href")))) {
      await expect(page.locator(anchor)).toHaveCount(1);
    }
    for (const id of ["guests", "troops", "work", "expeditions", "economy"]) {
      await page.locator(`[data-guide-nav] a[href="#${id}"]`).click();
      await expect(page).toHaveURL(new RegExp(`#${id}$`));
      await expect(page.locator(`#${id}`)).toBeInViewport();
      await expect(guide.locator('.guide-section:visible')).toHaveCount(1);
      await expect(guide.locator('[role="tab"][aria-selected="true"]')).toHaveAttribute("aria-controls", id);
      await expect(guide.locator('[role="tabpanel"]:visible')).toHaveAttribute("id", id);
    }
    await links.last().press("ArrowRight");
    await expect(links.first()).toBeFocused();
    await expect(page.locator("#guests")).toBeVisible();
    await links.first().press("End");
    await expect(page.locator("#economy")).toBeVisible();
    await links.last().press("Home");
    await expect(page.locator("#guests")).toBeVisible();
    await expect(page.locator("#combat")).toContainText("20%");
    await expect(page.locator("#guests")).toContainText("黑、灰、绿、红（暂未开放）、蓝、紫、橙");
    await expect(page.locator("#wealth")).toContainText("不会自动变成银两");
    const overflow = await guide.locator("p, .guide-definitions dd, .guide-index").evaluateAll(elements =>
      elements.filter(el => el.scrollWidth > el.clientWidth + 1).map(el => el.textContent.slice(0, 40)));
    expect(overflow).toEqual([]);
    const box = await guide.boundingBox();
    expect(await guide.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(width);
    if (width === 390 || width === 1280) {
      await page.locator("#guests").screenshot({ path: `/tmp/game-guide-guests-${width}.png` });
      await page.locator(".guide-header").scrollIntoViewIfNeeded();
      await page.screenshot({ path: `/tmp/game-guide-simple-${width}.png`, fullPage: true });
    }
  });
}

test("guide tabs initialize after leaving and returning through partial navigation", async ({ page }) => {
  await page.setContent(html);
  await page.addScriptTag({ path: path.join(root, "static/js/guide-page.js") });
  await page.getByRole("tab", { name: "护院" }).click();
  await expect(page.locator("#troops")).toBeVisible();
  await page.evaluate((markup) => {
    const main = document.querySelector(".content-area");
    main.innerHTML = "<p>暂时离开攻略</p>";
    document.dispatchEvent(new Event("partial-nav:loaded"));
    const fresh = new DOMParser().parseFromString(markup, "text/html");
    main.innerHTML = fresh.querySelector(".content-area").innerHTML;
    document.dispatchEvent(new Event("partial-nav:loaded"));
    document.dispatchEvent(new Event("partial-nav:loaded"));
  }, html);
  await page.getByRole("tab", { name: "打工" }).click();
  await expect(page.locator("#work")).toBeVisible();
  await expect(page.locator('.guide-section:visible')).toHaveCount(1);
  await page.getByRole("tab", { name: "战斗" }).click();
  await expect(page.locator("#expeditions")).toBeVisible();
});
