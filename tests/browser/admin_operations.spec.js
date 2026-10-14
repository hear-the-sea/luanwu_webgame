const { expect, test } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");

const projectRoot = path.resolve(__dirname, "../..");
let operationsHtml;

test.beforeAll(() => {
  operationsHtml = execFileSync("python", [path.join(__dirname, "fixtures/render_admin_operations.py")], {
    cwd: projectRoot,
    env: { ...process.env, PYTHONPATH: projectRoot },
    encoding: "utf8",
  });
});

for (const width of [320, 390, 1440]) {
  test(`后台工作台在 ${width} 像素支持中文搜索并保留手动折叠状态`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.setContent(operationsHtml);
    await page.addScriptTag({ path: path.join(projectRoot, "static/js/admin-operations.js") });
    const technical = page.locator("details").filter({ has: page.locator("summary", { hasText: "配置与技术记录" }) });
    await expect(technical).not.toHaveAttribute("open", "");
    await technical.locator("summary").click();
    await expect(technical).toHaveAttribute("open", "");
    const input = page.getByLabel("查找管理功能");
    await input.fill("擂台");
    const visibleRows = page.locator("[data-operation-name]:visible");
    await expect(visibleRows).toHaveCount(4);
    for (const text of await visibleRows.allTextContents()) expect(text).toContain("擂台");
    await input.fill("不存在的功能");
    await expect(page.getByRole("status")).toBeVisible();
    await expect(visibleRows).toHaveCount(0);
    await input.fill("");
    await expect(page.getByRole("status")).toBeHidden();
    await expect(technical).toHaveAttribute("open", "");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await technical.locator("summary").click();
    await input.fill("擂台");
    await input.fill("");
    await expect(technical).not.toHaveAttribute("open", "");
  });
}
