const { test, expect } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");
const pages = {};
test.beforeAll(() => {
  for (const viewer of ["attacker", "defender", "spectator"]) {
    pages[viewer] = execFileSync("python", [path.join(__dirname, "fixtures/render_battle_text.py"), viewer], {
      cwd: root, env: { ...process.env, PYTHONPATH: root }, encoding: "utf8",
    });
  }
});
for (const width of [320, 390, 768, 1280]) {
  for (const viewer of ["attacker", "defender", "spectator"]) {
    test(`${width}px ${viewer} sees full retaliation and unit names`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.setContent(pages[viewer]);
      for (const css of ["style", "tailwind"]) {
        await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
      }
      await expect(page.locator(".reflect-text")).toContainText("造成伤害123456");
      await expect(page.locator(".event-effect-source")).toContainText("剑圣爆发护身剑罡");
      await expect(page.locator(".reflect-text > .event-unit-name")).toHaveCount(0);
      await expect(page.locator(".reflect-text .event-unit-summary > .event-unit-name")).toHaveText("浪翻云");
      const retaliationState = page.locator(".reflect-text .event-unit-summary > .battle-unit-state");
      await expect(retaliationState).toHaveCount(viewer === "attacker" ? 1 : 0);
      if (viewer === "attacker") {
        await expect(retaliationState).toHaveAttribute("data-unit-state-side", "attacker");
        await expect(retaliationState.locator(".battle-unit-state-fill")).toHaveAttribute("style", /percent: 0%/);
        const actor = await page.locator(".reflect-text .event-unit-name").boundingBox();
        const state = await retaliationState.boundingBox();
        expect(state.y).toBeGreaterThanOrEqual(actor.y + actor.height);
      }
      await expect(page.locator(".reflect-text")).toContainText("浪翻云被击败");
      await expect(page.locator(".status-tag")).toContainText("对剑圣造成状态：审计次目标眩晕");
      await expect(page.locator(".event-heal-effect")).toContainText("恢复生命1234567");
      await expect(page.locator(".event-heal-effect")).toContainText("恢复人数12345");
      if (width <= 640) {
        for (const selector of [".event-heal-effect"]) {
          const effect = page.locator(selector).first();
          const source = await effect.locator(":scope > span").first().boundingBox();
          const values = await effect.locator(":scope > .event-effect-values").boundingBox();
          expect(values.y).toBeGreaterThanOrEqual(source.y + source.height);
        }
      }
      await expect(page.locator(".event-action-recovery").filter({ hasText: "嗜血狂怒" })).toContainText("损失 123456 生命");
      await expect(page.locator(".round-action-list > li")).toHaveCount(3);
      const clipped = await page.locator(".event-target-detail, .event-unit-name").evaluateAll(elements =>
        elements.filter(el => el.scrollWidth > el.clientWidth && getComputedStyle(el).overflow === "hidden").length);
      expect(clipped).toBe(0);
      expect(await page.locator(".round-action-cell").first().evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    });
  }
}
