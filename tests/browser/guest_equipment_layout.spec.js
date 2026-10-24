const { test, expect } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");

const root = path.resolve(__dirname, "../..");
let html;
test.beforeAll(() => {
  html = execFileSync("python", [path.join(__dirname, "fixtures/render_guest_equipment.py")], {
    cwd: root, env: { ...process.env, PYTHONPATH: root }, encoding: "utf8",
  });
});

for (const width of [320, 390, 768, 1024, 1280, 1600]) {
  test(`${width}px equipment names stay visible and controls do not overlap`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1100 });
    await page.setContent(html);
    for (const css of ["style", "tailwind", "guest-detail"]) {
      await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
    }
    const chips = page.locator(".equip-chip");
    await expect(chips).toHaveCount(7);
    const layout = await chips.evaluateAll(elements => elements.map(chip => {
      const rect = chip.getBoundingClientRect();
      const name = chip.querySelector(".equip-chip-name");
      const text = name.getBoundingClientRect();
      const check = chip.querySelector("input").getBoundingClientRect();
      const title = chip.closest(".equip-line").querySelector(".equip-line-title").getBoundingClientRect();
      return {
        fits: name.scrollWidth <= name.clientWidth && text.height <= parseFloat(getComputedStyle(name).lineHeight) * 2 + 1,
        x: rect.x, right: rect.right, bottom: rect.bottom, textBottom: text.bottom,
        separated: check.right <= text.x && title.right <= rect.x,
      };
    }));
    for (const item of layout) {
      expect(item.fits).toBe(true);
      expect(item.separated).toBe(true);
      expect(item.x).toBeGreaterThanOrEqual(0);
      expect(item.right).toBeLessThanOrEqual(width);
      expect(item.textBottom).toBeLessThanOrEqual(item.bottom);
    }
    const ornaments = page.locator(".equip-line").filter({ hasText: "饰品：" });
    const boxes = await ornaments.locator(".equip-chip, .equip-open").evaluateAll(elements =>
      elements.map(element => {
        const r = element.getBoundingClientRect();
        return { x: r.x, y: r.y, right: r.right, bottom: r.bottom };
      }));
    for (let i = 1; i < boxes.length; i++) {
      expect(boxes[i].y >= boxes[i - 1].bottom || boxes[i].x >= boxes[i - 1].right).toBe(true);
    }
    const rows = page.locator(".equip-line");
    const helmet = await rows.nth(0).boundingBox();
    const armor = await rows.nth(1).boundingBox();
    expect(helmet.y).toBeCloseTo(armor.y, 0);
    expect(armor.x).toBeGreaterThanOrEqual(helmet.x + helmet.width);
    const card = await page.locator(".equip-card").boundingBox();
    expect(card.height).toBeLessThan(460);
    await expect(chips.first().locator(".guest-equip-tooltip-bubble")).toContainText("传说·九霄龙吟玄天鎏金战盔");
    await chips.first().click();
    await expect(chips.first().locator("input")).toBeChecked();
    await page.mouse.move(0, 0);
    if (width === 390 || width === 1280) {
      await page.screenshot({ path: `/tmp/guest-equipment-${width}.png`, fullPage: true });
    }
  });
}
