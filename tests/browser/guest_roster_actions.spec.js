const { test, expect } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");
const pages = {};
test.beforeAll(() => {
  for (const mode of ["paid", "unpaid"]) {
    pages[mode] = execFileSync("python", [path.join(__dirname, "fixtures/render_guest_roster.py"), mode], {
      cwd: root, env: { ...process.env, PYTHONPATH: root }, encoding: "utf8",
    });
  }
});
for (const width of [320, 390, 768, 1280]) {
  for (const mode of ["paid", "unpaid"]) {
    test(`${width}px ${mode} roster navigation fits and aligns`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.setContent(pages[mode]);
      for (const css of ["style", "tailwind", "guest-roster"]) {
        await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
      }
      const links = page.locator(".guest-roster-actions > a");
      await expect(links).toHaveCount(2);
      await expect(links.first()).toHaveCSS("color", "rgb(255, 248, 231)");
      const boxes = await links.evaluateAll(elements => elements.map(el => {
        const rect = el.getBoundingClientRect();
        return { x: rect.x, y: rect.y, width: rect.width, height: rect.height,
          fits: el.scrollWidth <= el.clientWidth, whiteSpace: getComputedStyle(el).whiteSpace };
      }));
      for (const box of boxes) {
        expect(box.fits).toBe(true);
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(width);
      }
      if (width <= 768) {
        expect(boxes[0].y).toBeCloseTo(boxes[1].y, 0);
        expect(boxes[0].width).toBeCloseTo(boxes[1].width, 0);
        expect(boxes[0].height).toBeGreaterThanOrEqual(44);
        expect(boxes[1].height).toBeGreaterThanOrEqual(44);
        expect(boxes.every(box => box.whiteSpace === "nowrap")).toBe(true);
      }
      if (width === 390 && mode === "unpaid") {
        await page.screenshot({ path: "/tmp/guest-roster-mobile-actions.png" });
      }
    });
  }
}
