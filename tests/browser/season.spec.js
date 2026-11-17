const { test, expect } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "../..");
let pages;
test.describe.configure({ mode: "default" });

test.beforeAll(() => {
  test.setTimeout(120_000);
  pages = JSON.parse(execFileSync("python", [path.join(__dirname, "fixtures/render_season.py")], {
    cwd: root, env: { ...process.env, PYTHONPATH: root }, encoding: "utf8", timeout: 120_000, maxBuffer: 8 * 1024 * 1024,
  }));
});

async function openSeason(page, mode = "open") {
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== "http://season.test") return route.abort();
    if (url.pathname === "/season/") return route.fulfill({ contentType: "text/html", body: pages[mode] });
    if (url.pathname.startsWith('/static/season-troops/')) {
      const filename = path.basename(url.pathname).replace(/\.webp$/, ".png.webp");
      const source = path.join(root, 'data/images/troops', filename);
      return fs.existsSync(source) ? route.fulfill({ path: source, contentType: filename.endsWith('.webp') ? 'image/webp' : 'image/png' }) : route.abort();
    }
    if (url.pathname === '/static/images/season-icons.svg') return route.fulfill({ path: path.join(root, 'static/images/season-icons.svg'), contentType: 'image/svg+xml' });
    if (url.pathname === '/media/guests/monkey_wu_male4.png') return route.fulfill({ path: path.join(root, 'data/images/guests/monkey_wu_male4.png'), contentType: 'image/png' });
    if (url.pathname === '/static/images/season-item-parcel.svg') return route.fulfill({ path: path.join(root, 'static/images/season-item-parcel.svg'), contentType: 'image/svg+xml' });
    const prefix = "/static/season-assets/";
    if (!url.pathname.startsWith(prefix)) return route.abort();
    const relative = url.pathname.slice(prefix.length).replace(/\.webp$/, ".png.webp");
    const source = path.resolve(root, "data/images/pvp/layers-v1", relative);
    if (!source.startsWith(path.join(root, "data/images/pvp/layers-v1") + path.sep) || !fs.existsSync(source)) {
      return route.fulfill({ status: 404, body: "missing fixture asset" });
    }
    return route.fulfill({ path: source, contentType: source.endsWith('.webp') ? 'image/webp' : 'image/png' });
  });
  await page.goto("http://season.test/season/");
  for (const css of ["style", "tailwind", "chat_widget", "season", "map-wooden-sign", "season-visual"]) {
    await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
  }
  for (const script of ["guild-modal", "season"]) {
    await page.addScriptTag({ path: path.join(root, "static/js", `${script}.js`) });
  }
  await decodeVisibleImages(page);
}

async function decodeVisibleImages(page) {
  await page.locator(".season-region:not([hidden]) img").evaluateAll(images => Promise.all(images.map(image => image.decode())));
}

async function innPoint(page, opaque) {
  return page.locator('[data-season-building="mountain:inn"]').evaluate((button, wantOpaque) => {
    const image = button.querySelector("img");
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const context = canvas.getContext("2d");
    context.drawImage(image, 0, 0);
    const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
    const box = button.getBoundingClientRect();
    if (!wantOpaque) return { x: box.x + 4, y: box.y + 4 };
    for (let y = Math.floor(canvas.height * 0.4); y < canvas.height * 0.75; y += 5) {
      for (let x = Math.floor(canvas.width * 0.3); x < canvas.width * 0.7; x += 5) {
        if (data[(y * canvas.width + x) * 4 + 3] > 240) {
          return { x: box.x + x / canvas.width * box.width, y: box.y + y / canvas.height * box.height };
        }
      }
    }
    throw new Error("No opaque inn pixel in real fixture sprite");
  }, opaque);
}

test("真实透明建筑按轮廓悬停放大，点击与键盘打开同一据点", async ({ page }) => {
  await openSeason(page);
  const building = page.locator('[data-season-building="mountain:inn"]');
  await building.scrollIntoViewIfNeeded();
  const transparent = await innPoint(page, false);
  await page.mouse.move(transparent.x, transparent.y);
  await expect(building).not.toHaveClass(/is-hovered/);
  await page.mouse.click(transparent.x, transparent.y);
  await expect(page.getByRole("dialog")).toBeHidden();
  const opaque = await innPoint(page, true);
  await page.mouse.move(opaque.x, opaque.y);
  await expect(building).toHaveClass(/is-hovered/);
  await expect(building).toHaveCSS("transform", "matrix(1.05, 0, 0, 1.05, 0, -2)");
  await page.mouse.click(opaque.x, opaque.y);
  const dialog = page.getByRole("dialog", { name: "山脚驿站" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "派遣出征" })).toBeEnabled();
  await expect(dialog.locator('input[name="troop_dao_sheng"]')).toBeHidden();
  await dialog.locator(".season-troop-config > summary").click();
  await expect(dialog.locator('input[name="troop_dao_sheng"]')).toBeVisible();
  await expect(dialog.locator(".season-troop-identity img")).not.toHaveCount(0);
  await expect.poll(() => dialog.locator(".season-troop-identity img").first().evaluate(image => image.naturalWidth)).toBeGreaterThan(0);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await building.focus();
  await page.keyboard.press("Enter");
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(building).toBeFocused();
  const sign = building.locator(".map-wooden-sign");
  await expect(sign).toContainText("山脚驿站");
  await expect(sign).toContainText("粮草收集");
  await expect(page.locator(".map-wooden-sign")).toHaveCount(10);
  await sign.click();
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await page.mouse.move(0, 0);
  await page.locator(".season-map-panel").screenshot({ path: test.info().outputPath("wooden-signs.png") });
});

test("三图直接开放与局部导航返回后仍可操作", async ({ page }) => {
  await openSeason(page);
  await page.getByRole("tab", { name: "河口古城" }).click();
  await decodeVisibleImages(page);
  await expect(page.locator('[data-season-region="river"]')).toBeVisible();
  await expect(page.locator('[data-season-region="mountain"]')).toBeHidden();
  await page.getByRole("tab", { name: "森林补给区" }).click();
  await decodeVisibleImages(page);
  await page.locator('[data-season-building="forest:camp"]').press("Enter");
  const dialog = page.getByRole("dialog", { name: "林间营寨" });
  await expect(dialog).not.toContainText("第 36 天开放");
  await expect(dialog).toContainText("嗜血铁骑");
  await expect(dialog).toContainText("尚无阵营占领");
  await expect(dialog.locator('select[name="mode"] option')).toHaveText(["集结攻坚"]);
  await expect(dialog.locator('form[action]')).not.toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.evaluate(html => {
    document.dispatchEvent(new CustomEvent("partial-nav:started"));
    document.querySelector("[data-season-page]").remove();
    const incoming = new DOMParser().parseFromString(html, "text/html").querySelector("[data-season-page]");
    document.querySelector(".content-area").append(incoming);
    document.dispatchEvent(new CustomEvent("partial-nav:loaded"));
  }, pages.open);
  await expect(page.locator('[data-season-region="forest"]')).toBeVisible();
  await expect(page.locator('[id="season-node-forest:camp"]')).toHaveCount(1);
  await page.getByRole("tab", { name: "山地关隘" }).click();
  await page.locator('[data-season-building="mountain:inn"]').press("Enter");
  await expect(page.getByRole("dialog", { name: "山脚驿站" })).toBeVisible();
});

test("资源收集说明逐场战斗与收益，在窄屏显示完整", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openSeason(page, "resources");
  await page.locator('[data-season-building="mountain:inn"]').press("Enter");
  const dialog = page.getByRole("dialog", { name: "山脚驿站" });
  await expect(dialog).toContainText("全胜最高 125%");
  await expect(dialog).toContainText("零胜无物资");
  await expect(dialog).toContainText("整趟占用一次当日收益档");
  await expect(dialog).toContainText("全部获胜预计物资");
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
});

test("390px地图内部可滚动，候选选择限制与搜索仍可用", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openSeason(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(await page.locator(".season-map-scroll").first().evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true);
  await page.getByRole("button", { name: "使用人物召唤卷轴", exact: true }).first().click();
  const dialog = page.getByRole("dialog", { name: "赛季结缘 · 自选候选人物" });
  const candidates = dialog.locator('input[name="template_ids"]');
  await expect(candidates).toHaveCount(4);
  for (let index = 0; index < 3; index += 1) await candidates.nth(index).check();
  await candidates.nth(3).click();
  await expect(dialog.locator('input[name="template_ids"]:checked')).toHaveCount(3);
  await expect(dialog.locator("[data-season-feedback]")).toHaveText("最多选择 3 名。");
  await dialog.locator("[data-season-candidate-search]").fill("不存在的姓名");
  await expect(dialog.locator("[data-season-candidate]:visible")).toHaveCount(3);
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
});

test("归档赛季显示结局，停止战斗但保留领奖和兑换", async ({ page }) => {
  await openSeason(page, "closed");
  await expect(page.locator("[data-season-winner]")).toContainText("小说阵营获胜");
  await expect(page.getByRole("button", { name: "调整出战阵容", exact: true })).toHaveCount(0);
  await page.locator('[data-season-building="mountain:inn"]').press("Enter");
  const dialog = page.getByRole("dialog", { name: "山脚驿站" });
  await expect(dialog).toContainText("已停止派遣");
  await expect(dialog.locator('form[action]')).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "赛季奖励", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "赛季奖励 · 功勋里程碑" })).toBeVisible();
  await expect(page.getByRole("button", { name: "领取", exact: true }).first()).toBeEnabled();
  await expect(page.getByRole("dialog", { name: "赛季奖励 · 功勋里程碑" }).getByRole("button", { name: "领取排名与阵营奖励", exact: true })).toBeVisible();
});

test("报名不绑定门客，出征使用真实队伍与阵营兵库", async ({ page }) => {
  await openSeason(page, "unregistered");
  await page.getByRole("button", { name: "报名本赛季", exact: true }).click();
  const join = page.getByRole("dialog", { name: "报名 · 声望平衡分组" });
  await expect(join.locator('input[name="guest_ids"]')).toHaveCount(0);
  await expect(join).toContainText("不占用门客");
  await page.keyboard.press("Escape");
  await page.unroute("**/*");
  await openSeason(page, "deployed");
  await page.getByRole("button", { name: "在外队伍", exact: true }).click();
  await expect(page.locator("#season-expeditions")).toContainText("行军中");
  await expect(page.getByRole("button", { name: "召回队伍", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "战场地图" }).click();
  await page.getByRole("button", { name: "阵营后勤", exact: true }).click();
  const logistics = page.getByRole("dialog", { name: "阵营后勤 · 共建兵库" });
  await expect(logistics).toContainText("后勤工坊");
  await expect(logistics).toContainText("查看护院科技");
});

test("特殊营寨展示产出归属，失守后仍可领取已有铁骑", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openSeason(page, "special_lost");
  await page.getByRole("tab", { name: "森林补给区" }).click();
  await decodeVisibleImages(page);
  await page.locator('[data-season-building="forest:camp"]').press("Enter");
  const camp = page.getByRole("dialog", { name: "林间营寨" });
  await expect(page.locator('[data-season-building="forest:camp"] .season-site-owner')).toHaveText("历史阵营占领");
  await expect(page.locator(".season-site-owner")).toHaveCount(6);
  await expect(camp).toContainText("嗜血铁骑");
  await expect(camp).toContainText("历史阵营控制");
  await expect(camp).toContainText("本阵营库存 35");
  await expect(camp).toContainText("默认满科技");
  await expect(camp).toContainText("默认满科技");
  await expect(camp.locator('input[name="troop_bloodthirsty_cavalry"]')).toHaveAttribute("max", "35");
  await expect(camp.locator('select[name="mode"] option')).toHaveText(["集结攻坚"]);
  await page.keyboard.press("Escape");
  await page.locator('[data-season-building="forest:camp"]').scrollIntoViewIfNeeded();
  await expect(page.locator('[data-season-building="forest:camp"] .season-site-owner')).toBeVisible();
  await page.locator(".season-map-panel").screenshot({ path: "/tmp/season-ownership-map.png" });
});

test("阵营建设自动升级，低资源也展示正常建设进度且没有手动升级入口", async ({ page }) => {
  await openSeason(page, "automatic_development");
  await expect(page.locator('input[name="action"][value="upgrade"]')).toHaveCount(0);
  await page.getByRole("button", { name: "阵营后勤", exact: true }).click();
  const logistics = page.getByRole("dialog", { name: "阵营后勤 · 共建兵库" });
  await expect(logistics.locator("[data-season-construction-points]")).toHaveText("500");
  await expect(logistics.locator("[data-season-development-next]")).toContainText("距下一阶段还差");
  await expect(logistics).toContainText("设施自动升级");
  await expect(logistics).toContainText("查看护院科技");
  await expect(logistics.getByRole("button", { name: "升级", exact: true })).toHaveCount(0);
  await expect(logistics.locator("progress")).toHaveCount(3);
});

test("集结列表可加入派遣并显示车轮战驻防，发起人有取消入口", async ({ page }) => {
  await openSeason(page, "coalition");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.locator('[data-season-building="mountain:gate"]').press("Enter");
  const rallies = page.getByRole("dialog", { name: "青石关", exact: true });
  await expect(rallies).toContainText("集结盟友");
  await expect(rallies).toContainText("人出发");
  const countdown = rallies.locator("[data-season-countdown]").first();
  const before = await countdown.textContent();
  await expect.poll(() => countdown.textContent()).not.toBe(before);
  await rallies.getByRole("button", { name: "加入集结", exact: true }).click();
  const join = page.getByRole("dialog", { name: "加入集结 · 青石关" });
  await expect(join).toBeVisible();
  await expect(join.locator('input[name="action"]')).toHaveValue("rally_join");
  await expect(join.locator('input[name="rally_id"]')).not.toHaveValue("");
  await expect(join.locator("[data-season-guest-limit]")).toHaveText("最多 3 名");
  await join.locator(".season-troop-config > summary").click();
  await expect(join.locator('input[name="troop_dao_sheng"]')).toBeVisible();
  const preview = join.locator("[data-season-frontline-preview]");
  await expect(preview).toHaveAttribute("data-season-frontline-other-troops", "2000");
  await join.locator('input[name="troop_dao_sheng"]').fill("1500");
  await expect(preview).toContainText("预计本波约 1285 人");
  await expect(preview).toContainText("实际以战斗为准");
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "战场地图" }).click();
  await page.getByRole("tab", { name: "河口古城" }).click();
  await decodeVisibleImages(page);
  await page.locator('[data-season-building="river:docks"]').press("Enter");
  const docks = page.getByRole("dialog", { name: "渡口码头" });
  await docks.locator('[data-season-pane-tab="details"]').click();
  await expect(docks).toContainText("驻防队伍");
  await expect(docks).toContainText("己方驻防门客");
  await expect(docks).toContainText("盟友驻防门客");
  await expect(docks).toContainText("护院");
  await expect(docks).toContainText("驻防队伍");
  await page.keyboard.press("Escape");
  await page.unroute("**/*");
  await openSeason(page, "coalition_leader");
  await page.getByRole("button", { name: "在外队伍", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "我的队伍", exact: true }).getByRole("button", { name: "取消集结", exact: true })).toBeVisible();
});

test("待分组可先派门客讨伐，古城开放日使用赛季配置", async ({ page }) => {
  await openSeason(page, "registration");
  await expect(page.locator(".season-page")).toContainText("等待分组");
  await page.locator('[data-season-building="mountain:inn"]').press("Enter");
  const waiting = page.getByRole("dialog", { name: "山脚驿站" });
  await expect(waiting).toContainText("分组前可先探索与讨伐");
  await expect(waiting.locator("[data-season-dispatch]")).toHaveCount(1);
  await expect(waiting.locator('input[name^="troop_"]')).toHaveCount(0);
  await expect(waiting.locator('select[name="mode"] option')).toHaveText(["资源收集"]);
  await page.keyboard.press("Escape");
  await page.unroute("**/*");
  await openSeason(page, "city_closed");
  await page.getByRole("tab", { name: "河口古城" }).click();
  await decodeVisibleImages(page);
  await page.locator('[data-season-building="river:town"]').press("Enter");
  const town = page.getByRole("dialog", { name: "河口古城" });
  await expect(town).toContainText("当前可查看战况");
  await expect(town.locator("[data-season-dispatch]")).toHaveCount(0);
});

test("五类军务有明确分工，侦察与运输按模式切换表单", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openSeason(page);
  await page.getByRole("button", { name: "战局详情", exact: true }).click();
  await page.locator(".season-duty-guide > summary").click();
  await expect(page.getByRole("region", { name: "五类军务分工" }).locator("article")).toHaveCount(5);
  await page.locator(".season-duty-guide > summary").click();
  await expect(page.getByRole("region", { name: "战局与今日贡献" })).toContainText("今日个人贡献");
  await page.keyboard.press("Escape");
  await page.locator('[data-season-building="mountain:ruins"]').press("Enter");
  const ruins = page.getByRole("dialog", { name: "古代遗迹" });
  await ruins.locator('select[name="mode"]').selectOption("explore");
  await expect(ruins.locator('[data-season-mode-fields="explore"]')).toBeVisible();
  await expect(ruins.locator('select[name="scout_site_key"] option')).toHaveCount(6);
  await expect(ruins.locator('[data-season-mode-fields="pve"]')).toBeHidden();
  await ruins.locator('select[name="mode"]').selectOption("pve");
  await expect(ruins.locator('[data-season-mode-fields="pve"]')).toContainText("铁料360");
  await expect(ruins.locator('select[name="scout_site_key"]')).toBeHidden();
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "河口古城" }).click();
  await page.locator('[data-season-building="river:docks"]').press("Enter");
  const docks = page.getByRole("dialog", { name: "渡口码头" });
  await expect(docks.locator('option[value="intercept"]')).toHaveAttribute("disabled", "");
  const transportForm = docks.locator('form:has(input[name="action"][value="dispatch"])');
  await docks.locator('[data-season-pane-tab="personal"]').click();
  await transportForm.locator('select[name="mode"]').selectOption("transport");
  await expect(docks.locator('select[name="target_site_key"]')).toBeVisible();
  await expect(docks.locator('[data-season-mode-fields="transport"]')).toContainText("按需交付");
  await expect(docks.locator('[data-season-mode-fields="npc_intercept"]')).toBeHidden();
  expect(await docks.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.keyboard.press("Escape");
  await page.screenshot({ path: "/tmp/season-development-mobile.png", fullPage: true });
});

test("地图整合队伍，两个主页签与旧军务链接均可用", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 960 });
  await openSeason(page);
  const battle = page.locator('[data-season-view="battle"]');
  await expect(battle).toBeVisible();
  await expect(page.locator('[data-season-view="army"]')).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "集结军务" })).toHaveCount(0);
  await expect(page.locator('[data-season-view="reports"]')).toHaveCount(0);
  await expect(page.getByRole("region", { name: "我的队伍", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "战场地图" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "贡献排行" })).toBeFocused();
  await expect(page.locator('[data-season-view="ranking"]')).toBeVisible();
  await page.evaluate(() => { window.location.hash = "season-expeditions"; });
  const teams = page.getByRole("dialog", { name: "我的队伍", exact: true });
  await expect(teams).toBeVisible();
  await page.reload();
  for (const css of ["style", "tailwind", "chat_widget", "season", "map-wooden-sign", "season-visual"]) {
    await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
  }
  for (const script of ["guild-modal", "season"]) {
    await page.addScriptTag({ path: path.join(root, "static/js", `${script}.js`) });
  }
  await expect(teams).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "战场地图" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole("button", { name: "在外队伍", exact: true }).click();
  await expect(teams).toBeVisible();
  await teams.screenshot({ path: "/tmp/season-map-team-status.png" });
});

test("资源新版明确收益档与三资源训练，没需求不创建运输", async ({ page }) => {
  await openSeason(page, "resources");
  await page.getByRole("button", { name: "阵营后勤", exact: true }).click();
  const economy = page.getByRole("dialog", { name: "阵营后勤 · 共建兵库" });
  await expect(economy).toContainText("普通护院");
  await expect(economy).toContainText("目标");
  await page.keyboard.press("Escape");
  await page.locator('[data-season-building="mountain:inn"]').press("Enter");
  const inn = page.getByRole("dialog", { name: "山脚驿站" });
  await expect(inn).toContainText("粮草7500");
  await expect(inn).toContainText("全部获胜预计物资");
  await expect(inn).toContainText("战败或召回时按已胜场结算");
  await expect(inn).toContainText("全额收益 · 剩 4 次");
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "河口古城", exact: true }).click();
  await page.locator('[data-season-building="river:docks"]').press("Enter");
  const docks = page.getByRole("dialog", { name: "渡口码头" });
  await expect(docks.locator('option[value="transport"]')).toHaveAttribute("disabled", "");
  await expect(docks.locator('select[name="target_site_key"] option')).toHaveText(["请选择有需求的己方据点"]);
});

test("精简版攻城只留一个入口，仓库接订单、集市调剂公共资源", async ({ page }) => {
  await openSeason(page, "simple");
  await page.getByRole("button", { name: "运输补给 · 港口仓库", exact: true }).click();
  const warehouse = page.getByRole("dialog", { name: "港口仓库", exact: true });
  await expect(warehouse).toContainText("补给");
  await warehouse.locator('[data-season-pane-tab="stock"]').click();
  await expect(warehouse.getByRole("region", { name: "募训营产兵概况" })).toContainText("普通护院");
  await expect(warehouse.getByRole("region", { name: "募训营产兵概况" })).toContainText("自动训练");
  await warehouse.locator('[data-season-pane-tab="orders"]').click();
  await expect(warehouse).toContainText("按需交付");
  await expect(warehouse).toContainText("青石关");
  await expect(warehouse.locator('select[name="target_site_key"] option')).toContainText(["请选择有需求的己方据点", "青石关"]);
  await expect(warehouse.getByRole("button", { name: "派遣补给队", exact: true })).toBeEnabled();
  await expect(warehouse.locator('[data-season-travel-note]')).toHaveText('基础单程 15 分钟');
  await expect(warehouse).toContainText('满额最高90贡献');
  await expect(warehouse).toContainText('停留45 分钟');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await warehouse.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await warehouse.screenshot({ path: "/tmp/season-simple-warehouse.png" });
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "河口古城", exact: true }).click();
  await page.locator('[data-season-building="river:docks"]').press("Enter");
  const docks = page.getByRole("dialog", { name: "渡口码头", exact: true });
  await expect(docks.locator('input[name="action"][value="rally_create"]')).toHaveCount(1);
  await docks.locator('[data-season-pane-tab="rally"]').click();
  await expect(docks.locator('option[value="siege"]')).toHaveText("集结攻城");
  await expect(docks.locator('option[value="attack"],option[value="npc_intercept"],option[value="transport"]')).toHaveCount(0);
  await expect(docks).toContainText("人满即出发");
  await page.keyboard.press("Escape");
  await page.locator('[data-season-building="river:market"]').press("Enter");
  const market = page.getByRole("dialog", { name: "沿岸集市", exact: true });
  await expect(market.locator('[data-season-pane="trade"]')).toHaveCount(0);
  await expect(market.locator('[data-season-pane="exchange"]')).toHaveCount(1);
  await expect(market).toContainText('占领集市后可查看');
  await market.locator('[data-season-pane-tab="exchange"]').click();
  await expect(market.getByRole("region", { name: "军需调剂" })).toBeVisible();
  expect(await market.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await market.screenshot({ path: "/tmp/season-simple-market.png" });
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "森林补给区", exact: true }).click();
  await page.locator('[data-season-building="forest:granary"]').press("Enter");
  const granary = page.getByRole("dialog", { name: "前线粮仓", exact: true });
  await expect(granary.locator('option[value="pve"]')).toHaveCount(0);
  await expect(granary).toContainText("粮产加成");
});

 test("少人军令、驻军口粮预览与可选集结人数在窄屏可用", async ({ page }) => {
  await openSeason(page, "simple");
  await expect(page.getByRole("region", { name: "当前阵营目标" })).toBeVisible();
  await expect(page.getByLabel("个人奖励进度")).toContainText("可领取");
  await page.locator('[data-season-building="mountain:gate"]').press("Enter");
  const gate = page.getByRole("dialog", { name: "青石关", exact: true });
  await expect(gate.locator('[data-season-mode]')).toHaveValue("garrison");
  await expect(gate.locator('select[name="station_hours"] option[value="8"]')).toHaveText('8 小时');
  await expect(gate).toContainText('驻守每小时 10 贡献');
  await gate.locator('select[name="station_hours"]').selectOption("4");
  await gate.locator(".season-troop-config > summary").click();
  await gate.locator('input[name="troop_dao_sheng"]').fill("100");
  await expect(gate.locator('[data-season-food-preview]')).toHaveText(/预带 8 粮 · 驻军预计消耗 32 粮/);
  await gate.locator('[data-season-pane-tab="details"]').click();
  await expect(gate.getByLabel("攻城接力进度")).toBeVisible();
  await expect(gate.getByLabel("箭塔血量", { exact: true })).toHaveAttribute("max", "100000");
  await expect(gate.getByLabel("占领进度", { exact: true })).toHaveCount(0);
  await expect(gate).toContainText("守军");
  await expect(gate).toContainText("普通阶段最多 8 回合");
  await expect(gate.getByLabel("城内补给", { exact: true })).toBeVisible();
  await expect(gate).toContainText("驻防队伍");
  await expect(gate.locator('option[value="repair"]')).toHaveCount(0);
  await gate.locator('[data-season-pane-tab="personal"]').click();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await gate.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await gate.screenshot({ path: "/tmp/season-small-team-garrison.png" });
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "森林补给区", exact: true }).click();
  await page.locator('[data-season-building="forest:camp"]').press("Enter");
  const camp = page.getByRole("dialog", { name: "林间营寨", exact: true });
  await camp.locator('select[name="rally_target_members"]').selectOption("1");
  await expect(camp.locator('select[name="rally_target_members"]')).toHaveValue("1");
  await expect(camp).toContainText("单人立即出发");
  expect(await camp.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await camp.screenshot({ path: "/tmp/season-small-team-rally.png" });
});

 test("集市货品仅占领方可见，物品有图且普通商店保持开放", async ({ page }) => {
  await openSeason(page, "simple");
  await page.getByRole("tab", { name: "河口古城", exact: true }).click();
  await page.locator('[data-season-building="river:market"]').press("Enter");
  let market = page.getByRole("dialog", { name: "沿岸集市", exact: true });
  await expect(market.locator('[data-season-pane="trade"]')).toHaveCount(0);
  await expect(market.locator('[data-season-pane="exchange"]')).toHaveCount(1);
  await expect(market.locator('input[name="action"][value="market_purchase"]')).toHaveCount(0);
  await expect(market.locator('input[name="action"][value="rally_create"]')).toHaveCount(1);
  await page.keyboard.press("Escape");
  await openSeason(page, "market_owned");
  await page.getByRole("tab", { name: "河口古城", exact: true }).click();
  await expect(page.locator('[data-season-building="river:market"] .map-wooden-sign')).toContainText("占领");
  await page.locator('[data-season-building="river:market"]').press("Enter");
  market = page.getByRole("dialog", { name: "沿岸集市", exact: true });
  const stock = market.getByRole("region", { name: "占领货摊" });
  await expect(stock).toContainText("己方经营");
  await expect(stock).toContainText("物品绑定");
  await expect(stock.locator('.season-item-picture img')).not.toHaveCount(0);
  await expect.poll(() => stock.locator('.season-item-picture img').first().evaluate(image => image.naturalWidth)).toBeGreaterThan(0);
  await expect(stock.locator('input[name="action"][value="market_purchase"]')).toHaveCount(3);
  for (const button of await stock.getByRole("button", { name: "兑换", exact: true }).all()) {
    await expect(button).toBeEnabled();
  }
  await market.locator('[data-season-pane-tab="exchange"]').click();
  await expect(market.getByRole("region", { name: "军需调剂" })).toContainText("损耗15%");
  await expect(market.getByRole("region", { name: "军需调剂" })).toContainText("公共粮草 200 → 公共铁料 56");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await market.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await market.locator('[data-season-pane-tab="trade"]').click();
  await stock.screenshot({ path: "/tmp/season-market-owned-stock.png" });
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '阵营商店', exact: true }).click();
  const shop = page.locator('#season-shop [role=dialog]');
  await expect(shop.locator('.season-item-picture img')).not.toHaveCount(0);
  await expect.poll(() => shop.locator('.season-item-picture img').first().evaluate(image => image.naturalWidth)).toBeGreaterThan(0);
  await shop.screenshot({ path: '/tmp/season-faction-shop-items.png' });
});

 test("周目标领奖、单次预约与手动配兵在少人界面清楚可用", async ({ page }) => {
  await openSeason(page, "playable");
  await page.getByRole("button", { name: "赛季奖励", exact: true }).click();
  const rewards = page.getByRole("dialog", { name: "赛季奖励 · 功勋里程碑" });
  const weekly = rewards.getByRole("region", { name: "每周战局与奖励" });
  await expect(weekly).toContainText("小说 10");
  await expect(weekly.getByRole("button", { name: "领取周奖励", exact: true })).toBeEnabled();
  await expect(weekly).toContainText("个人参与奖不要求阵营获胜");
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "森林补给区", exact: true }).click();
  await page.locator('[data-season-building="forest:camp"]').press("Enter");
  const reservation = page.getByRole("region", { name: "一次性预约参战" });
  await reservation.locator("summary").click();
  await expect(reservation).toContainText("成功、失败或到期后均不再代派");
  await expect(reservation.locator('input[name="troop_bloodthirsty_cavalry"]')).toHaveCount(0);
  await expect(reservation.getByRole("button", { name: "确认授权一次参战" })).toBeEnabled();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await reservation.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await expect(reservation.locator('input[name="node_key"]')).toHaveValue("forest:camp");
  await reservation.screenshot({ path: "/tmp/season-reservation-mobile.png" });
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "山地关隘", exact: true }).click();
  await page.getByRole("tab", { name: "战场地图", exact: true }).click();
  await page.locator('[data-season-building="mountain:inn"]').press("Enter");
  const inn = page.getByRole("dialog", { name: "山脚驿站", exact: true });
  await inn.locator('input[name="guest_ids"]').first().check();
  await expect(inn.getByRole("button", { name: "自动配兵" })).toHaveCount(0);
  await inn.locator(".season-troop-config > summary").click();
  await inn.locator('input[name="troop_dao_sheng"]').fill("100");
  const ordinary = await inn.locator('[data-season-troops]').evaluateAll(inputs => inputs.filter(input => input.name !== "troop_bloodthirsty_cavalry").reduce((sum, input) => sum + Number(input.value), 0));
  expect(ordinary).toBeGreaterThan(0);
  await expect(inn.locator('input[name="troop_bloodthirsty_cavalry"]')).toHaveValue("0");
  expect(await inn.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
});

 test("地图集中显示军令、用途和补给缺口，侧栏不再重复展示", async ({ page }) => {
  await openSeason(page, "simple");
  await expect(page.locator('.season-command-rail')).toHaveCount(0);
  await expect(page.getByRole('region', { name: '赛季地图' }).getByLabel('阵营积分')).toBeVisible();
  await expect(page.getByLabel('地图快捷操作')).toBeVisible();
  const gate = page.locator('[data-season-building="mountain:gate"] .map-wooden-sign');
  await expect(gate).toContainText('缺料待援');
  await expect(page.locator('[data-season-building="mountain:inn"] .map-wooden-sign')).toContainText('粮草收集');
  await page.getByRole('tab', { name: '河口古城', exact: true }).click();
  await decodeVisibleImages(page);
  await page.locator('.season-map-panel').screenshot({ path: '/tmp/season-map-hud-river-desktop.png' });
  const sign = page.locator('[data-season-building="river:market"] .map-wooden-sign');
  await sign.scrollIntoViewIfNeeded();
  const unobscured = await sign.evaluate(element => {
    const box = element.getBoundingClientRect();
    return [[.2,.2],[.8,.8],[.5,.5]].every(([x,y]) => element.contains(document.elementFromPoint(box.x + box.width*x, box.y + box.height*y)));
  });
  expect(unobscured).toBe(true);
  await sign.click();
  await expect(page.getByRole('dialog', { name: '沿岸集市', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: '/tmp/season-map-hud-mobile.png', fullPage: true });
});

 test("建筑弹窗按操作分屏，护院图标配兵与已选兵力可用", async ({ page }) => {
  await openSeason(page, "simple");
  await page.locator('[data-season-building="mountain:inn"]').press('Enter');
  const inn = page.getByRole('dialog', { name: '山脚驿站', exact: true });
  await expect(inn.locator('[data-season-pane="personal"]')).toBeVisible();
  await expect(inn.locator('[data-season-pane="details"]')).toBeHidden();
  await expect(inn.locator('input[name="troop_dao_sheng"]')).toBeHidden();
  await expect(inn.locator('.season-dispatch-risk')).toHaveCount(0);
  await inn.locator('input[name="guest_ids"]').first().check();
  await expect(inn.getByRole('button', { name: '上次配置' })).toHaveCount(0);
  await inn.locator('.season-troop-config > summary').click();
  await inn.locator('input[name="troop_dao_sheng"]').fill('100');
  await inn.locator('.season-troop-config > summary').click();
  await expect(inn.locator('[data-season-troop-count]')).not.toHaveText('已选0人');
  await expect(inn.getByRole("tab")).toHaveCount(0);
  await inn.locator(".season-troop-config > summary").click();
  await inn.locator(".season-troop-config > summary").click();
  await expect(inn.locator('input[name="guest_ids"]').first()).toBeChecked();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await inn.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await inn.screenshot({ path: '/tmp/season-compact-inn.png' });
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '运输补给 · 港口仓库', exact: true }).click();
  const warehouse = page.getByRole('dialog', { name: '港口仓库', exact: true });
  await expect(warehouse.locator('[data-season-pane="orders"]')).toBeVisible();
  await expect(warehouse.locator('[data-season-pane="development"]')).toBeHidden();
  await warehouse.locator('[data-season-pane-tab="stock"]').click();
  await expect(warehouse.locator('[data-season-pane="stock"]')).toContainText('箭神');
  await warehouse.locator('[data-season-pane-tab="development"]').click();
  await expect(warehouse.getByRole('region', { name: '阵营自动发展' })).toBeVisible();
  await warehouse.locator('[data-season-pane-tab="orders"]').click();
  await warehouse.screenshot({ path: '/tmp/season-compact-warehouse.png' });
  await page.keyboard.press('Escape');
  await page.getByRole('tab', { name: '河口古城', exact: true }).click();
  await page.locator('[data-season-building="river:market"]').press('Enter');
  const market = page.getByRole('dialog', { name: '沿岸集市', exact: true });
  await expect(market.locator('[data-season-pane="war"]')).toBeVisible();
  await expect(market.locator('[data-season-pane="trade"]')).toBeHidden();
  await expect(market.locator('input[name="action"][value="market_purchase"]')).toHaveCount(0);
  await market.screenshot({ path: '/tmp/season-compact-market.png' });
});


test("门客每日两次共享额度，耗尽后禁选且后勤收益明确", async ({ page }) => {
  await openSeason(page, "quota_exhausted");
  await page.locator('[data-season-building="mountain:inn"]').press("Enter");
  const inn = page.getByRole("dialog", { name: "山脚驿站", exact: true });
  await expect(inn).toContainText("粮草7500");
  await expect(inn.locator('.season-guest-list input[name="guest_ids"]:disabled')).toHaveCount(1);
  await expect(inn).toContainText("今日剩 0/2 次");
  await expect(inn).toContainText("物资进入阵营仓库");
  await expect(inn.getByRole('button', { name: '上次配置' })).toHaveCount(0);
  await expect(inn.getByRole('button', { name: '自动配兵' })).toHaveCount(0);
});


test("运输窗口内可报名拦截与护送，切换模式仅提交当前车队", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openSeason(page, 'convoys');
  await page.getByRole('tab', { name: '河口古城', exact: true }).click();
  await page.locator('[data-season-building="river:docks"]').press('Enter');
  const docks = page.getByRole('dialog', { name: '渡口码头', exact: true });
  await docks.locator('[data-season-pane-tab="personal"]').click();
  const form = docks.locator('[data-season-pane="personal"] form');
  const mode = form.locator('[data-season-mode]');
  await mode.selectOption('intercept');
  const attackTarget = form.locator('[data-season-mode-fields="intercept"] select');
  await expect(attackTarget.locator('option')).toHaveCount(2);
  await attackTarget.selectOption({ index: 1 });
  const enemy = await attackTarget.inputValue();
  expect(await form.evaluate(element => new FormData(element).getAll('target_run_id'))).toEqual([enemy]);
  await mode.selectOption('escort');
  const escortTarget = form.locator('[data-season-mode-fields="escort"] select');
  await expect(escortTarget.locator('option')).toHaveCount(2);
  await expect(attackTarget).toBeDisabled();
  await escortTarget.selectOption({ index: 1 });
  const friend = await escortTarget.inputValue();
  expect(friend).not.toEqual(enemy);
  expect(await form.evaluate(element => new FormData(element).getAll('target_run_id'))).toEqual([friend]);
  await expect(form.locator('[data-season-travel-note]')).toHaveText('基础单程 15 分钟');
  await expect(docks).toContainText('窗口结束');
  expect(await docks.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await expect(page.locator('.chat-widget')).toBeHidden();
  await docks.screenshot({ path: '/tmp/season-convoy-join-mobile.png' });
  await mode.selectOption('pve');
  expect(await form.evaluate(element => new FormData(element).getAll('target_run_id'))).toEqual([]);
  await expect(form.locator('[data-season-travel-note]')).toHaveText('基础单程 30 分钟');
});

test("五人集结以成员头像和空席展示，窄屏可直接加入", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openSeason(page, "coalition");
  await page.getByRole("button", { name: /可加入集结/ }).click();
  const rally = page.locator("#season-rallies .season-rally").first();
  await expect(rally.locator(".season-party-slot")).toHaveCount(5);
  await expect(rally.locator(".season-party-slot--empty")).toHaveCount(4);
  await expect(rally.locator(".season-party-slot--empty").first()).toContainText("待加入");
  await expect.poll(() => rally.locator(".season-portrait img").evaluate(image => image.naturalWidth)).toBeGreaterThan(0);
  expect(await rally.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.getByRole("dialog", { name: "可加入的集结", exact: true }).screenshot({ path: "/tmp/season-map-rally-finder.png" });
  await rally.getByRole("button", { name: "加入集结", exact: true }).click();
  const join = page.getByRole("dialog", { name: "加入集结 · 青石关" });
  await join.locator(".season-guest-choice .season-portrait").first().click();
  await expect(join.locator('input[name="guest_ids"]').first()).toBeChecked();
  expect(await join.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await join.screenshot({ path: "/tmp/season-visual-party-mobile.png" });
});

test("战利品展示实际道具图，人物卡与资源图标可辨识", async ({ page }) => {
  await openSeason(page, "simple");
  await page.getByRole("button", { name: "赛季奖励", exact: true }).click();
  const rewards = page.getByRole("dialog", { name: "赛季奖励 · 功勋里程碑" });
  await expect(rewards.locator(".season-loot-card")).toHaveCount(4);
  const item = rewards.locator(".season-item-picture img").first();
  await expect.poll(() => item.evaluate(image => image.naturalWidth)).toBeGreaterThan(0);
  await expect(rewards.locator(".season-loot-card").first().getByRole("button", { name: "领取", exact: true })).toBeEnabled();
  await rewards.screenshot({ path: "/tmp/season-visual-rewards-desktop.png" });
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('[data-season-building="mountain:inn"]').press("Enter");
  const inn = page.getByRole("dialog", { name: "山脚驿站", exact: true });
  await expect.poll(() => inn.locator(".season-guest-choice .season-portrait img").first().evaluate(image => image.naturalWidth)).toBeGreaterThan(0);
  await expect.poll(() => inn.locator(".season-yield-strip use").first().evaluate(icon => icon.getBBox().width)).toBeGreaterThan(0);
  await inn.screenshot({ path: "/tmp/season-visual-resource-mobile.png" });
  const portrait = inn.locator(".season-guest-choice .season-portrait img").first();
  await portrait.evaluate(image => { image.src = "/media/missing-season-portrait.png"; });
  await expect(portrait).toBeHidden();
  await inn.locator(".season-guest-choice .season-portrait").first().click();
  await expect(inn.locator('input[name="guest_ids"]').first()).toBeChecked();
});

test("忙碌与目标易主后，建筑内既有集结仍可查看和取消", async ({ page }) => {
  await openSeason(page, "rally_target_changed");
  await page.locator('[data-season-building="mountain:gate"]').press("Enter");
  const gate = page.getByRole("dialog", { name: "青石关", exact: true });
  await gate.locator('[data-season-pane-tab="rally"]').click();
  await expect(gate.getByRole("button", { name: "取消集结", exact: true })).toBeEnabled();
  await expect(gate.locator('input[name="action"][value="rally_create"]')).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "进攻队状态", exact: true }).click();
  const teams = page.getByRole("dialog", { name: "我的队伍", exact: true });
  await expect(teams.getByRole("button", { name: "取消集结", exact: true })).toBeEnabled();
});

test("地图队伍入口保留返程与失效目标预约的管理", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openSeason(page, "returning_lost");
  await expect(page.getByRole("tab", { name: "山地关隘", exact: true }).locator(".season-region-rallies")).toHaveText("1");
  await page.getByRole("button", { name: "驻防队状态", exact: true }).click();
  const teams = page.getByRole("dialog", { name: "我的队伍", exact: true });
  await expect(teams).toContainText("返程中");
  await expect(teams).toContainText("渡口码头");
  await expect(teams.getByRole("button", { name: "召回队伍", exact: true })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.getByRole("region", { name: "我的队伍", exact: true }).screenshot({ path: "/tmp/season-map-team-strip.png" });
  await page.unroute("**/*");
  await openSeason(page, "pending_reservation_lost");
  await page.getByRole("button", { name: /预约 青石关/ }).click();
  await expect(teams.getByRole("button", { name: "取消预约", exact: true })).toBeEnabled();
  await expect(teams.locator('input[name="action"][value="reservation_cancel"]')).toHaveCount(1);
});

for (const width of [1280, 390]) {
  test(`赛季使用游戏内置纵向滚动条 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 720 });
    await openSeason(page, "simple");
    const content = page.locator(".content-area");
    await expect(content).toHaveCSS("overflow-y", "auto");
    expect(await content.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
    await content.hover();
    await page.mouse.wheel(0, 1200);
    await expect.poll(() => content.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    expect(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight + 1)).toBe(true);
    await expect(page.getByRole("region", { name: "我的队伍", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "进攻队状态", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "我的队伍", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    if (width === 390) {
      const map = page.locator('.season-region:not([hidden]) .season-map-scroll');
      expect(await map.evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true);
      await map.evaluate(element => { element.scrollLeft = 120; });
      expect(await map.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
    }
  });
}
