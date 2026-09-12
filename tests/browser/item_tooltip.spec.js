const { expect, test } = require("@playwright/test");
const path = require("node:path");

const projectPath = (...parts) => path.resolve(__dirname, "../..", ...parts);

for (const viewport of [{ width: 1280, height: 720 }, { width: 375, height: 667 }]) {
  test(`mission tooltip supports keyboard and stays in the viewport at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.setContent(`
      <div class="tw-mission-tabs"></div>
      <button type="button" class="tw-drop-entry tw-item-cell"
        style="position:fixed;right:4px;bottom:4px" aria-label="Mission reward">
        <span class="tw-drop-icon">R</span>
        <span class="tw-item-tooltip"><span class="tw-tooltip-header">Mission reward</span></span>
      </button>
    `);
    await page.addStyleTag({ path: projectPath("static/css/tailwind.css") });
    await page.addScriptTag({ path: projectPath("static/js/tooltip.js") });
    await page.addScriptTag({ path: projectPath("static/js/tasks-page.js") });
    await page.evaluate(() => document.dispatchEvent(new Event("partial-nav:loaded")));
    const trigger = page.getByRole("button", { name: "Mission reward" });
    const bubble = page.locator(".tw-item-tooltip");

    await trigger.focus();
    await page.keyboard.press("Enter");
    await expect(bubble).toBeVisible();
    await expect(bubble).toHaveCSS("position", "fixed");
    await expect.poll(async () => {
      const box = await bubble.boundingBox();
      return box && box.x >= 0 && box.y >= 0
        && box.x + box.width <= viewport.width && box.y + box.height <= viewport.height;
    }).toBeTruthy();
    await page.keyboard.press("Escape");
    await expect(bubble).toBeHidden();
  });
}

test("hybrid touch compatibility mouseover does not cancel the first tooltip click", async ({ page }) => {
  await page.setContent(`
    <label class="guest-equip-tooltip-trigger">
      <input type="checkbox"><span class="item-name">Equipment</span>
      <span class="guest-equip-tooltip-bubble">Equipment details</span>
    </label>
  `);
  await page.evaluate(() => {
    window.matchMedia = () => ({ matches: true });
  });
  await page.addScriptTag({ path: projectPath("static/js/tooltip.js") });
  await page.evaluate(() => window.initItemTooltip({
    key: "hybrid_equipment",
    cellSelector: ".guest-equip-tooltip-trigger",
    tooltipSelector: ".guest-equip-tooltip-bubble",
    trackPointer: false,
  }));
  const label = page.locator("label");
  const name = page.locator(".item-name");
  await name.dispatchEvent("mouseover");
  await name.dispatchEvent("click");
  await expect(label).toHaveClass(/is-tooltip-active/);
  await expect(page.getByRole("checkbox")).not.toBeChecked();
  await name.dispatchEvent("click");
  await expect(label).not.toHaveClass(/is-tooltip-active/);
  await page.getByRole("checkbox").check();
  await expect(page.getByRole("checkbox")).toBeChecked();
});
