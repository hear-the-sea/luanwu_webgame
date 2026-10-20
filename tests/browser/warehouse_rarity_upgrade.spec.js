const { expect, test } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");

const root = path.resolve(__dirname, "../..");
const pages = {};
const pills = [
  { item: 101, rarity: "green", eligible: 201, nonRecruitable: 202, used: 203 },
  { item: 102, rarity: "blue", eligible: 204, nonRecruitable: 205, used: 206 },
];

test.beforeAll(() => {
  for (const scenario of ["mixed", "blocked", "empty"]) {
    pages[scenario] = execFileSync("python", [
      path.join(__dirname, "fixtures/render_warehouse_rarity_upgrade_page.py"), scenario,
    ], { cwd: root, env: { ...process.env, PYTHONPATH: root }, encoding: "utf8" });
  }
});

async function prepare(page, scenario = "mixed") {
  await page.setContent(pages[scenario]);
  for (const css of ["style", "tailwind"]) {
    await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
  }
  await page.evaluate(() => {
    window.rarityUpgradeRequests = [];
    window.alert = () => {};
    window.gameDialog = { error: async () => {} };
    window.fetch = async (url, options) => {
      window.rarityUpgradeRequests.push({ url, data: Object.fromEntries(options.body) });
      return { json: async () => ({ success: false, error: "浏览器测试截获请求" }) };
    };
  });
  await page.addScriptTag({ path: path.join(root, "static/js/warehouse_page_core.js") });
  await page.addScriptTag({ path: path.join(root, "static/js/warehouse-page.js") });
  await page.evaluate(() => document.dispatchEvent(new Event("partial-nav:loaded")));
}

function candidate(page, id) {
  return page.locator(`#guest-list-rarity-upgrade [data-guest-id="${id}"]`);
}

async function openItem(page, id) {
  await page.locator(`.js-open-guest-select-modal[data-item-id="${id}"]`).click();
  await expect(page.locator("#guest-select-modal")).toBeVisible();
  await expect(page.locator("#modal-confirm-btn")).toBeDisabled();
  await expect(page.locator("#guest-list-rarity-upgrade .selected")).toHaveCount(0);
}

async function closeModal(page) {
  await page.locator("#guest-select-modal").getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.locator("#guest-select-modal")).toBeHidden();
}

async function expectNoOverflow(page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  const overflowing = await page.locator(
    "#guest-select-modal .tw-modal-content, #guest-select-modal .tw-modal-body, "
    + "#guest-list-rarity-upgrade, #guest-list-rarity-upgrade .tw-guest-option"
  ).evaluateAll(elements => elements.filter(el => el.getClientRects().length).some(el => {
    const bounds = el.getBoundingClientRect();
    return bounds.left < 0 || bounds.right > innerWidth + 1 || el.scrollWidth > el.clientWidth + 1;
  }));
  expect(overflowing).toBe(false);
}

for (const width of [320, 1280]) {
  test(`${width}px pills explain blocked guests and submit an eligible guest`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await prepare(page);
    await expectNoOverflow(page);

    for (const [index, pill] of pills.entries()) {
      await openItem(page, pill.item);
      const list = page.locator("#guest-list-rarity-upgrade");
      const confirm = page.locator("#modal-confirm-btn");
      await expect(page.locator("#modal-hint")).toContainText("每名门客只能使用一次绿转蓝或蓝转紫丸子");
      await expect(list.locator(`.tw-guest-option[data-guest-rarity="${pill.rarity}"]:visible`)).toHaveCount(4);
      await expect(list.locator(`.tw-guest-option:not([data-guest-rarity="${pill.rarity}"]):visible`)).toHaveCount(0);
      await expect(page.locator("#guest-list-rarity-upgrade-empty")).toBeHidden();

      for (const [id, reason] of [[pill.nonRecruitable, "非招募门客"], [pill.used, "已使用过升阶丸子"]]) {
        const option = candidate(page, id);
        await expect(option).toBeVisible();
        await expect(option).toBeDisabled();
        await expect(option.locator("[data-rarity-upgrade-block-reason-text]")).toBeVisible();
        await expect(option.locator("[data-rarity-upgrade-block-reason-text]")).toContainText(reason);
        await option.locator(".tw-guest-name").dispatchEvent("click");
        await expect(option).not.toHaveClass(/\bselected\b/);
        await expect(confirm).toBeDisabled();
      }

      await expectNoOverflow(page);
      await candidate(page, pill.eligible).click();
      await expect(candidate(page, pill.eligible)).toHaveClass(/\bselected\b/);
      await expect(confirm).toBeEnabled();
      await candidate(page, pill.used).locator(".tw-guest-name").dispatchEvent("click");
      await expect(candidate(page, pill.eligible)).toHaveClass(/\bselected\b/);
      await expect(candidate(page, pill.used)).not.toHaveClass(/\bselected\b/);
      await confirm.click();
      await expect.poll(() => page.evaluate(() => window.rarityUpgradeRequests.length)).toBe(index + 1);
      const request = await page.evaluate(position => window.rarityUpgradeRequests[position], index);
      const actionUrl = await page.locator(`[data-item-id="${pill.item}"]`).getAttribute("data-action-url");
      expect(request.url).toBe(actionUrl);
      expect(request.data).toMatchObject({ guest_id: String(pill.eligible), binding_mode: "unbound" });
      await closeModal(page);
    }
  });

  test(`${width}px switching pills and mapped fragments restores option state`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await prepare(page);
    for (const pill of pills) {
      await openItem(page, pill.item);
      await candidate(page, pill.eligible).click();
      await expect(page.locator("#modal-confirm-btn")).toBeEnabled();
      await closeModal(page);
    }

    for (const [fragment, mappedGuest, pill] of [[103, 207, pills[0]], [104, 208, pills[1]]]) {
      await openItem(page, fragment);
      const list = page.locator("#guest-list-rarity-upgrade");
      await expect(list.locator(".tw-guest-option:visible:enabled")).toHaveCount(8);
      await expect(list.locator("[data-rarity-upgrade-block-reason-text]:visible")).toHaveCount(0);
      await expect(page.locator("#modal-hint")).not.toContainText("仅可招募的门客能够使用丸子");
      await expect(page.locator("#guest-list-rarity-upgrade-empty")).toBeHidden();
      await candidate(page, mappedGuest).click();
      await expect(page.locator("#modal-confirm-btn")).toBeEnabled();
      await expectNoOverflow(page);
      await closeModal(page);

      await openItem(page, pill.item);
      await expect(candidate(page, mappedGuest)).toBeVisible();
      await expect(candidate(page, mappedGuest)).toBeDisabled();
      await expect(candidate(page, mappedGuest).locator("[data-rarity-upgrade-block-reason-text]")).toBeVisible();
      await expect(list.locator(`.tw-guest-option:not([data-guest-rarity="${pill.rarity}"]):visible`)).toHaveCount(0);
      await expect(candidate(page, pill.eligible)).toBeEnabled();
      await expectNoOverflow(page);
      await closeModal(page);
    }
  });

  test(`${width}px unavailable pill targets show an empty hint and block confirmation`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    for (const scenario of ["blocked", "empty"]) {
      await prepare(page, scenario);
      for (const pill of pills) {
        await openItem(page, pill.item);
        await expect(page.locator("#guest-list-rarity-upgrade-empty")).toBeVisible();
        await expect(page.locator("#guest-list-rarity-upgrade .tw-guest-option:visible:enabled")).toHaveCount(0);
        await expectNoOverflow(page);
        await closeModal(page);
      }
      if (scenario === "blocked") {
        await openItem(page, 103);
        await expect(page.locator("#guest-list-rarity-upgrade-empty")).toBeHidden();
        await candidate(page, 207).click();
        await expect(page.locator("#modal-confirm-btn")).toBeEnabled();
        await expectNoOverflow(page);
        await closeModal(page);
      }
      expect(await page.evaluate(() => window.rarityUpgradeRequests)).toEqual([]);
    }
  });
}
