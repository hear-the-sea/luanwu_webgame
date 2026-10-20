const { expect, test } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");
const fixtures = {};

test.beforeAll(() => {
  for (const scenario of ["available", "taken", "insufficient"]) {
    fixtures[scenario] = execFileSync("python3", [
      path.join(__dirname, "fixtures/render_warehouse_chiyou_page.py"), scenario,
    ], { cwd: root, env: { ...process.env, PYTHONPATH: root }, encoding: "utf8" });
  }
});

async function prepare(page, scenario = "available") {
  await page.setContent(fixtures[scenario]);
  for (const file of ["style", "tailwind", "warehouse-choice"]) {
    await page.addStyleTag({ path: path.join(root, `static/css/${file}.css`) });
  }
  await page.evaluate(() => {
    window.captured = [];
    window.gameDialog = { error: async () => {} };
    window.fetch = async (url, options) => {
      window.captured.push({ url, data: Object.fromEntries(options.body) });
      return { json: async () => ({ success: false, error: "浏览器测试拦截" }) };
    };
  });
  for (const file of ["warehouse_page_core", "warehouse-page", "warehouse-player-choice"]) {
    await page.addScriptTag({ path: path.join(root, `static/js/${file}.js`) });
  }
  await page.evaluate(() => {
    document.dispatchEvent(new Event("partial-nav:loaded"));
    document.dispatchEvent(new Event("partial-nav:loaded"));
  });
}

const activeDialog = (page) => page.locator('.js-player-choice-modal[aria-hidden="false"]');

for (const width of [320, 1280]) {
  test(`${width}px card row only offers use; biographies are inside the dialog`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    await prepare(page);
    const actions = page.locator(".tw-warehouse-actions").first();
    await expect(actions.getByRole("button")).toHaveCount(1);
    await expect(actions.getByRole("button")).toHaveText("使用");
    await expect(actions.locator("form, select, details")).toHaveCount(0);
    await expect(activeDialog(page)).toHaveCount(0);
    await actions.getByRole("button", { name: "使用", exact: true }).click();
    const modal = activeDialog(page);
    await expect(modal).toBeVisible();
    await expect(modal).toContainText("每次消耗100张蚩尤卡");
    await expect(modal).toContainText("他拿起了离别钩");
    await expect(modal).toContainText("世袭一等侯");
    await expect(modal.locator('input[name="guest_template_key"]:checked')).toHaveCount(0);
    await expect(modal.getByRole("button", { name: "确认兑换" })).toBeDisabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(await page.evaluate(() => window.captured)).toEqual([]);
  });
}

for (const mode of ["bound", "unbound"]) {
  test(`dialog posts selected guest, CSRF and ${mode} binding exactly once`, async ({ page }) => {
    await prepare(page);
    await page.locator(`tr[data-binding-mode="${mode}"] .js-open-player-choice-modal`).click();
    const modal = activeDialog(page);
    await modal.locator('input[value="hero_di_qinglin"]').check();
    await expect(modal.getByRole("button", { name: "确认兑换" })).toBeEnabled();
    await modal.getByRole("button", { name: "确认兑换" }).click();
    await expect.poll(() => page.evaluate(() => window.captured.length)).toBe(1);
    expect((await page.evaluate(() => window.captured[0])).data).toMatchObject({
      guest_template_key: "hero_di_qinglin", binding_mode: mode, csrfmiddlewaretoken: "browser-chiyou-csrf-token",
    });
  });
}

test("escape restores focus and reopening clears the selection without spending cards", async ({ page }) => {
  await prepare(page);
  const trigger = page.locator(".js-open-player-choice-modal").first();
  await trigger.click();
  await activeDialog(page).locator('input[value="hero_yang_zheng_purple"]').check();
  await page.keyboard.press("Escape");
  await expect(activeDialog(page)).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await trigger.click();
  await expect(activeDialog(page).locator("input:checked")).toHaveCount(0);
  await expect(activeDialog(page).getByRole("button", { name: "确认兑换" })).toBeDisabled();
  await activeDialog(page).getByRole("button", { name: "取消", exact: true }).click();
  expect(await page.evaluate(() => window.captured)).toEqual([]);
});

for (const scenario of ["taken", "insufficient"]) {
  test(`${scenario} allows inspecting biographies but prevents redemption`, async ({ page }) => {
    await prepare(page, scenario);
    await page.locator(".js-open-player-choice-modal").first().click();
    const modal = activeDialog(page);
    await expect(modal).toContainText("杨铮");
    await expect(modal).toContainText("狄青麟");
    if (scenario === "taken") {
      await expect(modal.locator('input[value="hero_yang_zheng_purple"]')).toBeDisabled();
      await expect(modal).toContainText("暂无可兑换的门客");
    } else {
      await modal.locator('input[value="hero_yang_zheng_purple"]').check();
      await expect(modal).toContainText("数量不足100张");
    }
    await expect(modal.getByRole("button", { name: "确认兑换" })).toBeDisabled();
    expect(await page.evaluate(() => window.captured)).toEqual([]);
  });
}
