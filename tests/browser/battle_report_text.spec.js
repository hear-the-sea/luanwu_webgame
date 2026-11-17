const { test, expect } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");
const pages = {};
const qiankunPages = {};
const bossPassivePages = {};
test.beforeAll(() => {
  for (const viewer of ["attacker", "defender", "spectator"]) {
    pages[viewer] = execFileSync("python", [path.join(__dirname, "fixtures/render_battle_text.py"), viewer], {
      cwd: root, env: { ...process.env, PYTHONPATH: root }, encoding: "utf8",
    });
    qiankunPages[viewer] = execFileSync("python", [path.join(__dirname, "fixtures/render_battle_text.py"), viewer, "qiankun"], {
      cwd: root, env: { ...process.env, PYTHONPATH: root }, encoding: "utf8",
    });
    bossPassivePages[viewer] = execFileSync("python", [path.join(__dirname, "fixtures/render_battle_text.py"), viewer, "boss-passives"], {
      cwd: root, env: { ...process.env, PYTHONPATH: root }, encoding: "utf8",
    });
  }
});
for (const width of [320, 390, 768, 1280]) {
  for (const viewer of ["attacker", "defender", "spectator"]) {
    test(`${width}px ${viewer} sees consistent boss passive colors including historical reflections`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.setContent(bossPassivePages[viewer]);
      for (const css of ["style", "tailwind"]) {
        await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
      }
      await expect(page.locator(".event-passive-skill")).toHaveCount(3);
      await expect(page.locator(".event-passive-reflect")).toHaveCount(6);
      for (const source of ["乾坤大挪移", "明教号令", "圣火狂势"]) {
        await expect(page.locator(".event-passive-skill").filter({ hasText: source })).toHaveCSS("color", "rgb(33, 97, 58)");
        const reflections = page.locator(".reflect-text").filter({ hasText: source });
        await expect(reflections).toHaveCount(2);
        for (const reflection of await reflections.all()) {
          await expect(reflection).toHaveCSS("color", "rgb(116, 68, 147)");
          await expect(reflection).toHaveCSS("background-color", "rgba(116, 68, 147, 0.05)");
          expect(await reflection.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
        }
      }
    });
    test(`${width}px ${viewer} sees qiankun activation and reflection separately`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.setContent(qiankunPages[viewer]);
      for (const css of ["style", "tailwind"]) {
        await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
      }
      const [sourceSide, recipientSide] = {
        attacker: ["敌方", "我方"], defender: ["我方", "敌方"], spectator: ["防守方", "进攻方"],
      }[viewer];
      const notice = page.locator(".round-action-cell.defender .event-passive-row");
      await expect(notice).toHaveCount(1);
      await expect(notice).toContainText("本回合获得卸力反震");
      await expect(notice.locator(".event-unit-name")).toHaveText("张无忌");
      const reflections = page.locator(".event-qiankun-reflect");
      await expect(reflections).toHaveCount(2);
      for (let index = 0; index < 2; index++) {
        const reflection = reflections.nth(index);
        await expect(reflection).toContainText(`${sourceSide}张无忌触发乾坤大挪移`);
        await expect(reflection).toContainText(`对${recipientSide}浪翻云造成反震伤害${index ? 1234 : 4321}`);
        await expect(reflection.locator("xpath=..")).toHaveCSS("color", "rgb(116, 68, 147)");
        await expect(reflection.locator("xpath=..")).toHaveCSS("background-color", "rgba(116, 68, 147, 0.05)");
      }
      await expect(page.locator(".event-passive-message")).toHaveCount(1);
      const overflow = await page.locator(".event-qiankun-reflect, .event-passive-layout, .round-action-cell")
        .evaluateAll(elements => elements.some(element => element.scrollWidth > element.clientWidth));
      expect(overflow).toBe(false);
    });
    test(`${width}px ${viewer} sees full retaliation and unit names`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.setContent(pages[viewer]);
      for (const css of ["style", "tailwind"]) {
        await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
      }
      await expect(page.locator(".reflect-text")).toContainText("造成伤害123456");
      await expect(page.locator(".reflect-text")).toHaveCSS("color", "rgb(116, 68, 147)");
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
      const bloodthirsty = page.locator(".event-action-recovery").filter({ hasText: "嗜血狂怒" });
      await expect(bloodthirsty).toHaveCount(1);
      await expect(bloodthirsty).toHaveCSS("background-color", "rgba(39, 174, 96, 0.06)");
      await expect(bloodthirsty.locator(".event-unit-name")).toHaveText("浪翻云");
      await expect(bloodthirsty).toContainText("随机追加目标");
      await expect(page.locator(".event-passive-skill").filter({ hasText: "嗜血狂怒" })).toHaveCount(1);
      expect(await bloodthirsty.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
      const passive = page.locator(".event-passive-row .event-passive-layout");
      const passiveRow = page.locator(".round-action-list > .event-passive-row");
      await expect(passiveRow).toHaveCount(1);
      await expect(passiveRow).toHaveCSS("background-color", "rgba(39, 174, 96, 0.06)");
      await expect(passiveRow).toHaveCSS("border-left-width", "3px");
      await expect(passive).toContainText("恶来怒吼死战，崩山裂甲转为同时攻击三人！");
      await expect(passive.locator("xpath=..").locator(":scope > .event-order")).toHaveCount(0);
      const passiveLayout = await passive.evaluate(element => {
        const message = element.querySelector(".event-passive-message");
        const label = element.querySelector(".event-passive-skill");
        const messageBounds = message.getBoundingClientRect();
        const bounds = element.getBoundingClientRect();
        const labelBounds = label.getBoundingClientRect();
        const nameBounds = element.querySelector(".event-unit-name").getBoundingClientRect();
        return {
          display: getComputedStyle(element).display,
          messageBorder: getComputedStyle(message).borderWidth,
          messageBackground: getComputedStyle(message).backgroundColor,
          messageStartsAtLeft: Math.abs(messageBounds.x - bounds.x) < 1,
          messageUsesFullWidth: Math.abs(messageBounds.width - bounds.width) < 1,
          messageBelowLabel: messageBounds.y >= labelBounds.bottom,
          messageBelowName: messageBounds.y >= nameBounds.bottom,
          messageAlignedWithName: Math.abs(messageBounds.x - nameBounds.x) < 1,
          messageOutsideSkills: message.parentElement === element,
          fits: element.scrollWidth <= element.clientWidth,
        };
      });
      expect(passiveLayout).toEqual({
        display: "grid", messageBorder: "0px", messageBackground: "rgba(0, 0, 0, 0)",
        messageStartsAtLeft: true, messageUsesFullWidth: true,
        messageBelowLabel: true, messageBelowName: true, messageAlignedWithName: true,
        messageOutsideSkills: true, fits: true,
      });
      const skill = passive.locator(".event-passive-skill");
      await expect(skill).toHaveCSS("border-top-width", "1px");
      await expect(skill).toHaveCSS("color", "rgb(33, 97, 58)");
      await expect(skill.locator("strong")).toHaveText("长篇被动技能名称审计");
      const skillBounds = await skill.boundingBox();
      const valuesBounds = await passive.locator(".event-passive-extra").boundingBox();
      expect(valuesBounds.y).toBeGreaterThanOrEqual(skillBounds.y + skillBounds.height);
      if (width <= 640) {
        const nameBounds = await passive.locator(".event-unit-name").boundingBox();
        expect(skillBounds.y).toBeGreaterThanOrEqual(nameBounds.y + nameBounds.height);
        expect(Math.abs(skillBounds.x - nameBounds.x)).toBeLessThan(1);
        if (viewer === "attacker") {
          const stateBounds = await passive.locator(".battle-unit-state").boundingBox();
          expect(skillBounds.y).toBeGreaterThanOrEqual(stateBounds.y + stateBounds.height);
        }
      }
      await expect(page.locator(".round-action-list > li")).toHaveCount(3);
      const clipped = await page.locator(".event-target-detail, .event-unit-name").evaluateAll(elements =>
        elements.filter(el => el.scrollWidth > el.clientWidth && getComputedStyle(el).overflow === "hidden").length);
      expect(clipped).toBe(0);
      expect(await page.locator(".round-action-cell").first().evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    });
  }
}
