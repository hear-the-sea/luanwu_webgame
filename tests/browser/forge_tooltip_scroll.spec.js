const { test, expect } = require("@playwright/test");
const path = require("path");

const root = path.resolve(__dirname, "../..");

for (const viewport of [{ width: 320, height: 568 }, { width: 390, height: 844 }, { width: 844, height: 390 }]) {
  test(`forge materials remain reachable at ${viewport.width}x${viewport.height}`, async ({ browser }) => {
    const context = await browser.newContext({ viewport, hasTouch: true, isMobile: true });
    const page = await context.newPage();
    await page.setContent(`
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <section class="forge-page" style="padding:60px 20px">
        <div class="forge-product-cell tw-item-cell">
          <button type="button" class="forge-product-image">查看锻造材料</button>
          <span class="tw-item-tooltip forge-item-tooltip" role="tooltip">
            <span class="tw-tooltip-header">龙胆枪</span>
            <span class="tw-tooltip-desc">生命220，武力74，敏捷18</span>
            <span class="forge-tooltip-costs">${Array.from({ length: 45 }, (_, i) =>
              `<span class="tw-attr-row"><span class="tw-attr-label">锻造材料${i + 1}</span><span class="tw-attr-value">10/100</span></span>`
            ).join("")}</span>
          </span>
        </div>
      </section>`);
    for (const file of ["tailwind.css", "production-cards.css"]) {
      await page.addStyleTag({ path: path.join(root, "static/css", file) });
    }
    for (const file of ["tooltip.js", "forge-page.js"]) {
      await page.addScriptTag({ path: path.join(root, "static/js", file) });
    }
    await page.getByRole("button", { name: "查看锻造材料" }).tap();
    const tooltip = page.getByRole("tooltip");
    await expect(tooltip).toBeVisible();
    await expect.poll(async () => tooltip.evaluate(el => {
      const box = el.getBoundingClientRect();
      return box.left >= 19 && box.top >= 19
        && box.right <= innerWidth - 19 && box.bottom <= innerHeight - 19;
    })).toBe(true);
    expect(await tooltip.evaluate(el => el.scrollHeight > el.clientHeight)).toBe(true);

    const box = await tooltip.boundingBox();
    const touch = await context.newCDPSession(page);
    const x = box.x + box.width / 2;
    const y = box.y + box.height * 0.8;
    await touch.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
    for (let step = 1; step <= 5; step++) {
      await touch.send("Input.dispatchTouchEvent", {
        type: "touchMove", touchPoints: [{ x, y: y - box.height * 0.1 * step }],
      });
    }
    await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await expect.poll(() => tooltip.evaluate(el => el.scrollTop)).toBeGreaterThan(0);
    await tooltip.evaluate(el => { el.scrollTop = el.scrollHeight; });
    await expect(tooltip.getByText("锻造材料45", { exact: true })).toBeInViewport({ ratio: 1 });
    await expect(tooltip).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(tooltip).toBeHidden();
    await context.close();
  });
}
