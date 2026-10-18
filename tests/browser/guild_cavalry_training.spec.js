const { expect, test } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");
let managerHtml;
let memberHtml;

test.beforeAll(() => {
  const fixture = path.join(__dirname, "fixtures/render_guild_cavalry_technology.py");
  const options = { cwd: root, env: { ...process.env, PYTHONPATH: root }, encoding: "utf8" };
  managerHtml = execFileSync("python3", [fixture], options);
  memberHtml = execFileSync("python3", [fixture, "--member"], options);
});

async function prepare(page, html) {
  await page.route("http://guild-cavalry.test/**", (route) => {
    if (route.request().resourceType() === "document") {
      return route.fulfill({ contentType: "text/html", body: html });
    }
    return route.abort();
  });
  await page.goto("http://guild-cavalry.test/guilds/technology/?node=wolf_bloodthirsty_cavalry");
  for (const css of ["style", "tailwind", "guild"]) {
    await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
  }
  await page.addScriptTag({ path: path.join(root, "static/js/guild-technology-tree.js") });
}

for (const width of [320, 1280]) {
  test(`${width}px managers can open training from a deep link and preview the full batch`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await prepare(page, managerHtml);
    const dialog = page.locator("#tw-tech-dialog");
    await expect(dialog).toBeVisible();
    await expect(page.locator('[data-route-tab="wolf"]')).toHaveAttribute("aria-selected", "true");
    const quantity = dialog.locator("#cavalry-training-quantity");
    await expect(quantity).toHaveAttribute("max", "2000");
    await quantity.fill("2000");
    await expect(dialog.locator("[data-cavalry-silver]")).toHaveText("3,000,000");
    await expect(dialog.locator("[data-cavalry-grain]")).toHaveText("10,000,000");
    await expect(dialog.locator("[data-cavalry-duration]")).toHaveText("16小时40分钟");
    await expect(dialog.getByRole("button", { name: "开始训练" })).toBeEnabled();
    const bounds = await quantity.boundingBox();
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
    await quantity.fill("2001");
    await expect(dialog.locator("[data-cavalry-duration]")).toHaveText("—");
    expect(await quantity.evaluate((input) => input.checkValidity())).toBe(false);
  });
}

test("ordinary members can inspect the cavalry but cannot start training", async ({ page }) => {
  await prepare(page, memberHtml);
  const dialog = page.locator("#tw-tech-dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("仅帮主和管理员可开始训练。")).toBeVisible();
  await expect(dialog.locator("[data-cavalry-training-form]")).toHaveCount(0);
});

test("route-locked cavalry artwork is desaturated with the talent icons", async ({ page }) => {
  await prepare(page, managerHtml);
  const node = page.locator('[data-node-key="wolf_bloodthirsty_cavalry"]');
  const art = node.locator(".tw-tech-node-art");
  await expect(art).toBeVisible();
  await node.evaluate((element) => element.setAttribute("data-route-locked", "true"));
  await expect.poll(() => art.evaluate((image) => getComputedStyle(image).filter))
    .toContain("grayscale(0.68)");
  await expect.poll(() => art.evaluate((image) => getComputedStyle(image).opacity)).toBe("0.76");
});

for (const width of [320, 1280]) {
  test(`${width}px forging tree opens the equipment node and shows all daily boxes`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await prepare(page, managerHtml);
    await page.locator("#tw-tech-dialog [data-tech-dialog-close]").click();
    await page.locator('[data-route-tab="forging"]').click();
    await page.locator('[data-node-key="forging_equipment_forge"]').click();
    const detail = page.locator('[data-tech-detail="forging_equipment_forge"]');
    await expect(detail).toBeVisible();
    await expect(detail.getByRole("heading", { name: "装备锻造" })).toBeVisible();
    await expect(detail).toContainText("等级 10 / 10");
    await expect(detail).toContainText("帮会绿装箱 ×2");
    await expect(detail).toContainText("帮会精铸紫装箱 ×2");
    await expect(detail).toContainText("帮会宗师秘匣 ×2");
  });
}
