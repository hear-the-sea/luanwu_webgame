const { test, expect } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const path = require("node:path");

const root = path.resolve(__dirname, "../..");
const pages = {};
test.beforeAll(() => {
  for (const scenario of ["guests", "troops", "defense", "exhausted", "unaffordable", "no_guests", "few_guests"]) {
    pages[scenario] = execFileSync("python", [path.join(__dirname, "fixtures/render_tasks_quick_launch.py"), scenario], {
      cwd: root, env: { ...process.env, PYTHONPATH: root }, encoding: "utf8",
    });
  }
});

async function setup(page, scenario, disabledGuest = false) {
  await page.setContent(pages[scenario]);
  for (const css of ["style", "tailwind"]) {
    await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
  }
  await page.evaluate(disableGuest => {
    window.quickLaunchSubmissions = [];
    window.quickLaunchConfirmations = [];
    window.gameConfirm = async (...args) => {
      window.quickLaunchConfirmations.push(args);
      return true;
    };
    if (disableGuest) document.querySelector('[data-task-guest][value="102"]').disabled = true;
    document.addEventListener("submit", event => {
      event.preventDefault();
      window.quickLaunchSubmissions.push({
        fields: Array.from(new FormData(event.target).entries()),
        method: event.target.method,
        action: event.target.getAttribute("action"),
      });
    });
  }, disabledGuest);
  await page.addScriptTag({ path: path.join(root, "static/js/tasks_page_core.js") });
  await page.addScriptTag({ path: path.join(root, "static/js/tasks-page.js") });
  await page.evaluate(() => document.dispatchEvent(new Event("partial-nav:loaded")));
}

test("closing task details updates the URL without reloading the task board", async ({ page }) => {
  const origin = "http://task-detail.test";
  await page.route(`${origin}/**`, route => route.fulfill({
    contentType: "text/html",
    body: pages.guests,
  }));
  await page.goto(`${origin}/gameplay/tasks/?mission=quick_launch_fixture`);
  for (const css of ["style", "tailwind"]) {
    await page.addStyleTag({ path: path.join(root, "static/css", `${css}.css`) });
  }
  await page.addScriptTag({ path: path.join(root, "static/js/tasks_page_core.js") });
  await page.addScriptTag({ path: path.join(root, "static/js/tasks-page.js") });
  await page.evaluate(() => document.dispatchEvent(new Event("partial-nav:loaded")));

  const originalTimeOrigin = await page.evaluate(() => performance.timeOrigin);
  await page.locator("[data-task-detail-overlay] [data-task-modal-close]").first().click();

  await expect(page.locator("[data-task-detail-overlay]")).toHaveCount(0);
  await expect(page).toHaveURL(`${origin}/gameplay/tasks/`);
  expect(await page.evaluate(() => performance.timeOrigin)).toBe(originalTimeOrigin);
});

for (const width of [390, 1280]) {
  for (const scenario of ["guests", "troops"]) {
    test(`${width}px ${scenario} quick launch submits the first full squad through the normal form`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await setup(page, scenario);
      await page.locator(".tw-raid-guest-option").filter({ has: page.locator('[data-task-guest][value="105"]') }).click();
      await expect(page.locator('[data-task-guest][value="105"]')).toBeChecked();
      if (scenario === "troops") await page.locator('[data-task-troop][data-troop-key="sword"]').fill("7");
      await page.locator("[data-task-quick-launch]").click();
      await expect.poll(() => page.evaluate(() => window.quickLaunchSubmissions.length)).toBe(1);
      const submissions = await page.evaluate(() => window.quickLaunchSubmissions);
      expect(submissions).toHaveLength(1);
      const confirmations = await page.evaluate(() => window.quickLaunchConfirmations);
      expect(confirmations).toHaveLength(1);
      if (scenario === "troops") {
        expect(confirmations[0][0]).toContain("2 种护院");
        expect(confirmations[0][0]).toContain("每种携带 1 个（共 2 个）");
        expect(confirmations[0][0]).toContain("派遣 3 名门客");
      } else {
        expect(confirmations[0][0]).toContain("选择 3 名门客");
        expect(confirmations[0][0]).not.toContain("护院");
      }
      expect(submissions[0].method).toBe("post");
      expect(submissions[0].action).toMatch(/accept/);
      const fields = new URLSearchParams(submissions[0].fields);
      expect(fields.get("mission_key")).toBe("quick_launch_fixture");
      expect(fields.get("csrfmiddlewaretoken")).toBe("quick-launch-fixture-csrf");
      expect(fields.getAll("guest_ids")).toEqual(["101", "102", "103"]);
      if (scenario === "troops") {
        expect(fields.get("troop_sword")).toBe("1");
        expect(fields.get("troop_bow")).toBe("1");
        expect(fields.get("troop_empty")).toBe("0");
      } else {
        expect(Array.from(fields.keys()).some(key => key.startsWith("troop_"))).toBe(false);
      }
      await expect(page.locator("#selected-guest-count")).toHaveText("3");
    });
  }

  test(`${width}px confirmation waits before submitting and partial navigation binds once`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await setup(page, "troops");
    for (let index = 0; index < 3; index++) {
      await page.evaluate(() => document.dispatchEvent(new Event("partial-nav:loaded")));
    }
    const header = page.locator(".tw-task-launch-heading");
    const quick = header.locator("[data-task-quick-launch]");
    await expect(quick).toBeVisible();
    await expect(page.locator("[data-task-quick-help]")).toHaveCount(0);
    const titleBox = await header.locator("h4").boundingBox();
    const quickBox = await quick.boundingBox();
    expect(quickBox.x).toBeGreaterThan(titleBox.x);
    expect(quickBox.y).toBeLessThan(titleBox.y + titleBox.height + 40);
    expect(await header.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    await page.evaluate(() => {
      window.gameConfirm = (...args) => {
        window.quickLaunchConfirmations.push(args);
        return new Promise(resolve => { window.resolveQuickLaunchConfirmation = resolve; });
      };
    });
    await quick.click();
    const confirmations = await page.evaluate(() => window.quickLaunchConfirmations);
    expect(confirmations).toHaveLength(1);
    expect(confirmations[0][1]).toMatchObject({ title: "快速出征", okText: "确认出征" });
    await expect(quick).toBeDisabled();
    await expect(page.locator("[data-task-submit]")).toBeDisabled();
    expect(await page.evaluate(() => window.quickLaunchSubmissions)).toHaveLength(0);
    await page.evaluate(() => {
      document.dispatchEvent(new Event("partial-nav:loaded"));
      document.querySelector("[data-task-quick-launch]").click();
      document.querySelector("[data-task-submit]").click();
    });
    expect(await page.evaluate(() => window.quickLaunchConfirmations)).toHaveLength(1);
    expect(await page.evaluate(() => window.quickLaunchSubmissions)).toHaveLength(0);
    await page.evaluate(() => window.resolveQuickLaunchConfirmation(true));
    await expect.poll(() => page.evaluate(() => window.quickLaunchSubmissions.length)).toBe(1);
    expect(await page.evaluate(() => window.quickLaunchSubmissions)).toHaveLength(1);
  });

  test(`${width}px cancelling quick launch preserves the original manual configuration`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await setup(page, "troops");
    await page.locator(".tw-raid-guest-option").filter({ has: page.locator('[data-task-guest][value="105"]') }).click();
    await page.locator('[data-task-troop][data-troop-key="sword"]').fill("7");
    const before = await page.locator("[data-task-loadout]").evaluate(form => Array.from(new FormData(form).entries()));
    await page.evaluate(() => {
      window.gameConfirm = async (...args) => {
        window.quickLaunchConfirmations.push(args);
        return false;
      };
    });
    await page.locator("[data-task-quick-launch]").click();
    await expect(page.locator("[data-task-quick-launch]")).toBeEnabled();
    await expect(page.locator("[data-task-submit]")).toBeEnabled();
    expect(await page.locator("[data-task-loadout]").evaluate(form => Array.from(new FormData(form).entries()))).toEqual(before);
    expect(await page.evaluate(() => window.quickLaunchSubmissions)).toHaveLength(0);
    expect(await page.evaluate(() => window.quickLaunchConfirmations)).toHaveLength(1);
    await expect(page.locator("#selected-guest-count")).toHaveText("1");
    await expect(page.locator('[data-task-troop][data-troop-key="sword"]')).toHaveValue("7");
  });

  for (const scenario of ["exhausted", "unaffordable", "no_guests"]) {
    test(`${width}px ${scenario} cannot quick launch`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await setup(page, scenario);
      const quick = page.locator("[data-task-quick-launch]");
      await expect(quick).toBeVisible();
      await expect(quick).toBeDisabled();
      await quick.evaluate(button => button.click());
      expect(await page.evaluate(() => window.quickLaunchSubmissions)).toHaveLength(0);
      expect(await page.evaluate(() => window.quickLaunchConfirmations)).toHaveLength(0);
    });
  }

  test(`${width}px defense retains automatic deployment without quick launch`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await setup(page, "defense");
    await expect(page.locator("[data-task-quick-launch]")).toHaveCount(0);
    await expect(page.locator("[data-task-quick-help]")).toHaveCount(0);
    await expect(page.locator(".tw-modal-body")).toContainText("自动使用庄园内所有空闲门客与护院");
  });
}

test("quick launch excludes disabled guests and uses all guests below the limit", async ({ page }) => {
  await setup(page, "guests", true);
  await page.locator("[data-task-quick-launch]").click();
  await expect.poll(() => page.evaluate(() => window.quickLaunchSubmissions.length)).toBe(1);
  let fields = new URLSearchParams((await page.evaluate(() => window.quickLaunchSubmissions))[0].fields);
  expect(fields.getAll("guest_ids")).toEqual(["101", "103", "104"]);
  expect((await page.evaluate(() => window.quickLaunchConfirmations))[0][0]).toContain("选择 3 名门客");
  await setup(page, "few_guests");
  await page.locator("[data-task-quick-launch]").click();
  await expect.poll(() => page.evaluate(() => window.quickLaunchSubmissions.length)).toBe(1);
  fields = new URLSearchParams((await page.evaluate(() => window.quickLaunchSubmissions))[0].fields);
  expect(fields.getAll("guest_ids")).toEqual(["101", "102"]);
  expect((await page.evaluate(() => window.quickLaunchConfirmations))[0][0]).toContain("选择 2 名门客");
});

test("history restore re-enables launch controls after a submitted quick launch", async ({ page }) => {
  await setup(page, "troops");
  await page.locator("[data-task-quick-launch]").click();
  await expect.poll(() => page.evaluate(() => window.quickLaunchSubmissions.length)).toBe(1);
  await expect(page.locator("[data-task-quick-launch]")).toBeDisabled();
  await expect(page.locator("[data-task-submit]")).toBeDisabled();

  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));

  await expect(page.locator("[data-task-quick-launch]")).toBeEnabled();
  await expect(page.locator("[data-task-submit]")).toBeEnabled();
  await page.locator("[data-task-quick-launch]").click();
  await expect.poll(() => page.evaluate(() => window.quickLaunchSubmissions.length)).toBe(2);
});
