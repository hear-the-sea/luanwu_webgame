const { expect, test } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");
let html;

test.beforeAll(() => {
  html = execFileSync("python", [path.join(__dirname, "fixtures/render_arena_ring_page.py")], {
    cwd: root, env: { ...process.env, PYTHONPATH: root }, encoding: "utf8",
  });
});

for (const viewport of [{ width: 320, height: 568 }, { width: 390, height: 844 }, { width: 1280, height: 960 }]) {
  test(`${viewport.width}px long lineup is visible and scrolls inside the modal`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.route("**/*", route => route.abort());
    await page.setContent(html);
    for (const css of ["style", "tailwind", "arena-rings"]) {
      await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
    }
    for (const script of ["guild-modal", "arena-rings"]) {
      await page.addScriptTag({ path: path.join(root, "static/js", `${script}.js`) });
    }
    const trigger = page.locator("[data-ring-open]");
    await trigger.click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.locator(".tw-modal-header")).toBeInViewport();
    await expect(dialog.locator(".tw-raid-guest-option")).toHaveCount(25);
    await expect(dialog.getByRole("button", { name: "登台守擂" })).toBeInViewport();
    await expect(dialog.locator('input[name="guest_ids"]:checked')).toHaveCount(20);
    await dialog.locator(".tw-raid-guest-option").nth(20).click();
    await expect(dialog.locator('input[name="guest_ids"]:checked')).toHaveCount(20);
    await expect(dialog.locator("[data-ring-error]")).toHaveText("最多上阵 20 名门客。");
    await dialog.getByRole("button", { name: "清空", exact: true }).click();
    await dialog.getByRole("button", { name: "全选", exact: true }).click();
    await expect(dialog.locator('input[name="guest_ids"]:checked')).toHaveCount(20);
    const body = dialog.locator(".tw-modal-body");
    expect(await body.evaluate(el => el.clientHeight)).toBeGreaterThan(100);
    expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    await dialog.locator(".tw-raid-guest-option").first().scrollIntoViewIfNeeded();
    await expect(dialog.locator(".tw-raid-guest-option").first()).toBeInViewport();
    const troop = dialog.locator(".tw-raid-troop-row").last();
    await troop.scrollIntoViewIfNeeded();
    await expect(troop).toBeInViewport();
    await expect(troop.locator(".tw-troop-count")).toHaveText("持有 1000（庄园 600 / 钱庄 400）");
    await troop.getByRole("button", { name: "+1000" }).click();
    await expect(troop.locator("input")).toHaveValue("1000");
    await troop.locator("input").fill("1000000");
    expect(await troop.locator("input").evaluate(el => el.checkValidity())).toBe(true);
    await expect(dialog.locator("[data-ring-capacity]")).toContainText("护院 1000000");
    await dialog.evaluate(el => {
      el.querySelector("form").addEventListener("submit", event => event.preventDefault());
      el.querySelector("form").requestSubmit();
    });
    await expect(dialog.locator("[data-ring-error]")).toHaveText("护院人数超过所选门客的带兵上限。");
    await dialog.getByRole("button", { name: "清空", exact: true }).click();
    await dialog.locator(".tw-raid-guest-option").first().click();
    await expect(dialog.locator("[data-ring-capacity]")).toContainText("/ 200");
    await troop.locator("input").fill("0");
    await troop.getByRole("button", { name: "+1000" }).click();
    await expect(troop.locator("input")).toHaveValue("200");
    const otherTroop = dialog.locator(".tw-raid-troop-row").nth(19);
    await otherTroop.scrollIntoViewIfNeeded();
    await otherTroop.getByRole("button", { name: "增加一名护院20", exact: true }).click();
    await expect(otherTroop.locator("input")).toHaveValue("0");
    await dialog.evaluate(el => el.querySelector("form").requestSubmit());
    await expect(dialog.locator("[data-ring-error]")).toBeHidden();
    await dialog.getByRole("button", { name: "登台守擂" }).focus();
    await page.keyboard.press("Tab");
    await expect(dialog.locator(".tw-modal-close")).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();
    await trigger.click();
    await page.mouse.click(2, 2);
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();
  });
}


for (const mode of ["cooldown", "exhausted", "ready"]) {
  test(`challenger entry is ${mode} on mobile`, async ({ page }) => {
    const ownerHtml = execFileSync("python", [path.join(__dirname, "fixtures/render_arena_ring_page.py")], {
      cwd: root, env: { ...process.env, PYTHONPATH: root, ARENA_RING_PREVIEW_MODE: mode }, encoding: "utf8",
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.route("**/*", route => route.abort());
    await page.setContent(ownerHtml);
    for (const css of ["style", "tailwind", "arena-rings"]) {
      await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
    }
    for (const script of ["guild-modal", "arena-rings"]) {
      await page.addScriptTag({ path: path.join(root, "static/js", `${script}.js`) });
    }
    const trigger = page.getByRole("button", { name: "挑战擂主", exact: true });
    const remaining = mode === "exhausted" ? 0 : mode === "cooldown" ? 2 : 1;
    await expect(page.locator(".ring-stage-body > .ring-form-note")).toContainText(`对本任擂主剩余 ${remaining} 次挑战`);
    if (mode === "ready") {
      await expect(trigger).toBeEnabled();
      await trigger.click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await expect(page.getByRole("button", { name: "出战夺擂" })).toBeEnabled();
    } else {
      await expect(trigger).toBeDisabled();
      await expect(page.getByRole("dialog")).toBeHidden();
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}
