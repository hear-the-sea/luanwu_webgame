const { expect, test } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");
const pages = {};

test.beforeAll(() => {
  for (const name of ["panel", "appearance", "confirm"]) {
    pages[name] = execFileSync("python", [path.join(__dirname, "fixtures/render_custom_guest_page.py"), name], {
      cwd: root, env: { ...process.env, PYTHONPATH: root }, encoding: "utf8", maxBuffer: 16 * 1024 * 1024,
    });
  }
});

async function prepare(page, name) {
  await page.setContent(pages[name]);
  for (const css of ["style", "tailwind", "chat_widget", "guest-detail", "custom-guest"]) {
    await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
  }
}

for (const width of [390, 1280]) {
  for (const name of ["panel", "appearance", "confirm"]) {
    test(`${width}px ${name} stays within viewport and exposes controls`, async ({ page }) => {
      await page.setViewportSize({ width, height: 950 });
      await prepare(page, name);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
      if (name === "panel") {
        await expect(page.getByRole("button", { name: "吞噬", exact: true })).toBeEnabled();
        await page.getByText("重置成长方向", { exact: true }).click();
        await expect(page.getByRole("button", { name: "使用千年朱果×1" })).toBeVisible();
      } else if (name === "appearance") {
        await page.route("http://custom-guest.test/**", route => {
          if (route.request().isNavigationRequest()) {
            return route.fulfill({ contentType: "text/html", body: pages.appearance });
          }
          if (route.request().url().includes("avatar_group=")) {
            return route.fulfill({
              contentType: "application/json",
              json: {
                success: true,
                page: 1,
                pages: 1,
                total: 1,
                avatars: [{
                  key: "preview/guest.webp",
                  label: "预览门客",
                  url: "data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=",
                }],
              },
            });
          }
          return route.abort();
        });
        await page.goto("http://custom-guest.test/appearance/");
        for (const css of ["style", "tailwind", "chat_widget", "guest-detail", "custom-guest"]) {
          await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
        }
        await page.addScriptTag({ path: path.join(root, "static/js/custom-guest.js") });
        const avatarGroup = page.locator("[data-avatar-group]").first();
        await avatarGroup.locator("summary").click();
        const avatarChoice = page.locator('input[name="avatar_choice"]');
        await expect(avatarChoice).toBeVisible();
        await avatarChoice.check();
        await expect(page.locator('input[name="avatar_key"]')).toHaveValue("preview/guest.webp");
        expect(await page.locator(".custom-avatar-option img").first().evaluate(image => image.naturalWidth)).toBeGreaterThan(0);
        if (width === 1280) {
          expect((await page.locator(".custom-appearance").boundingBox()).width).toBeGreaterThan(700);
        }
      } else {
        await expect(page.getByRole("button", { name: "确认升阶", exact: true })).toBeVisible();
        await expect(page.getByRole("button", { name: "确认升阶", exact: true })).toHaveCSS("color", "rgb(255, 248, 231)");
        await expect(page.getByRole("link", { name: "取消", exact: true })).toHaveCSS("color", "rgb(58, 31, 10)");
        await expect(page.locator('input[name="confirmation"]')).toHaveValue("signed-preview-token");
      }
      await page.screenshot({ path: `/tmp/custom-guest-${name}-${width}.png`, fullPage: true, animations: "disabled" });
    });
  }
}

for (const width of [390, 1280]) {
  test(`${width}px cultivation confirms in a popup before submitting`, async ({ page }) => {
    await page.setViewportSize({ width, height: 950 });
    await prepare(page, "panel");
    await page.evaluate(() => {
      const base = document.createElement("base");
      base.href = "http://custom-guest.test/";
      document.head.appendChild(base);
    });
    await page.addScriptTag({ path: path.join(root, "static/js/dialog.js") });
    await page.addScriptTag({ path: path.join(root, "static/js/custom-guest.js") });
    let submitted = 0;
    await page.route("http://custom-guest.test/**", async route => {
      const request = route.request();
      if (request.headers()["x-requested-with"] === "XMLHttpRequest") {
        await route.fulfill({ json: {
          success: true, confirmation: "signed-popup-token",
          preview: { title: "吞噬装备", summary: "敏捷之流云踏雪靴 × 1", detail: "敏捷权重 +50。装备将被消耗。" },
        } });
      } else {
        expect(request.postData()).toContain("confirmation=signed-popup-token");
        submitted += 1;
        await route.fulfill({ contentType: "text/html; charset=utf-8", body: "<p>吞噬完成</p>" });
      }
    });
    await expect(page.getByText("升阶权重需满足5000", { exact: false })).toBeVisible();
    await expect(page.locator("#custom-equipment option").last()).toContainText("敏捷权重+50/件");
    await page.locator("#custom-equipment").selectOption("100:bound");
    await page.getByRole("button", { name: "吞噬", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    expect(submitted).toBe(0);
    await dialog.getByRole("button", { name: "取消", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    expect(submitted).toBe(0);
    await page.getByRole("button", { name: "吞噬", exact: true }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.screenshot({ path: `/tmp/custom-guest-popup-${width}.png`, animations: "disabled" });
    await page.getByRole("dialog").getByRole("button", { name: "确认", exact: true }).click();
    await expect(page.getByText("吞噬完成", { exact: true })).toBeVisible();
    expect(submitted).toBe(1);
  });
}
