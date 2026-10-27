const { test, expect } = require("@playwright/test");
const fs = require("node:fs");
const path = require("node:path");

const scripts = Object.fromEntries(["jail_page_core.js", "jail-page.js"].map((name) => [
  `/static/js/${name}`,
  fs.readFileSync(path.resolve(__dirname, "../../static/js", name), "utf8"),
]));
const jailUrl = "http://jail.test/jail/";

function markup(layout, released) {
  return `<!doctype html><html><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="csrf-token" content="test-token">
    <style>
      body { margin:0; }
      ${layout === "content" ? "body { overflow:hidden; } .content-area { height:100vh; overflow:auto; }" : ""}
      button, select { margin:6px; min-height:40px; }
    </style></head><body><main class="content-area">
    <section class="dashboard jail-workspace" data-jail-root="1">
      <div style="height:1100px">监牢与上方囚徒</div>
      ${released ? "<p>俘虏已释放</p>" : `<article data-prisoner-id="7">
        <h2>测试囚徒</h2>
        <button type="button" data-jail-action="observe" data-action-url="/action/observe/">察言观色</button>
        <select data-speaker-select="reason" aria-label="说客"><option value="3" data-speaker-name="张三">张三</option></select>
        <button type="button" data-jail-action="interact" data-method="reason" data-action-url="/action/interact/">陈明大势</button>
        <button type="button" data-jail-action="recruit" data-mode="standard" data-action-url="/action/recruit/">归附</button>
        <form method="post" action="/release/" data-jail-release-form data-release-prisoner-name="测试囚徒">
          <input type="hidden" name="csrfmiddlewaretoken" value="test-token">
          <button type="submit">释放</button>
        </form>
      </article>`}
      <div style="height:${released ? 100 : 1600}px">其他监牢内容</div>
    </section></main>
    <script>
      window.acceptConfirmation = true;
      window.errors = [];
      window.successes = [];
      window.gameConfirm = async () => window.acceptConfirmation;
      window.gameDialog = {
        success: async (message) => {
          window.successes.push(message);
          document.querySelector('.content-area').scrollTop = 0;
          window.scrollTo(0, 0);
        },
        error: async (message) => { window.errors.push(message); }
      };
    </script>
    <script src="/static/js/jail_page_core.js"></script>
    <script src="/static/js/jail-page.js"></script>
    </body></html>`;
}

async function routeJail(page, layout) {
  const state = { documents: 0, actions: [], fail: false, released: false };
  await page.route("http://jail.test/**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (scripts[pathname]) {
      await route.fulfill({ contentType: "application/javascript", body: scripts[pathname] });
    } else if (pathname.startsWith("/action/")) {
      state.actions.push(JSON.parse(request.postData()));
      await route.fulfill({
        status: state.fail ? 400 : 200,
        json: state.fail ? { success: false, error: "今日次数已用完" } : {
          success: true,
          result: { text: "操作完成", heart_delta: -1, affinity_delta: 1 },
        },
      });
    } else if (pathname === "/release/") {
      state.released = true;
      // Playwright routes only the first request in an HTTP redirect chain.
      // A browser redirect keeps the form navigation local to this routed fixture.
      await route.fulfill({
        contentType: "text/html",
        body: '<script>window.location.replace("/jail/")</script>',
      });
    } else {
      state.documents += 1;
      await route.fulfill({ contentType: "text/html", body: markup(layout, state.released) });
    }
  });
  await page.goto(jailUrl);
  await expect(page.locator("[data-jail-root]")).toHaveAttribute("data-bound", "1");
  return state;
}

async function scrollPosition(page, layout) {
  return page.evaluate((mode) => mode === "content" ? document.querySelector(".content-area").scrollTop : window.scrollY, layout);
}

async function setPosition(page, layout, top = 900) {
  await page.evaluate(({ mode, value }) => {
    if (mode === "content") document.querySelector(".content-area").scrollTop = value;
    else window.scrollTo(0, value);
  }, { mode: layout, value: top });
  await expect.poll(() => scrollPosition(page, layout)).toBe(top);
}

for (const viewport of [{ width: 1280, height: 720 }, { width: 390, height: 844 }]) {
  for (const layout of ["content", "window"]) {
    test.describe(`jail scroll ${layout} ${viewport.width}px`, () => {
      test.use({ viewport });

      test("observation, negotiation and recruitment restore scroll after success and reload", async ({ page }) => {
        const state = await routeJail(page, layout);
        for (const label of ["察言观色", "陈明大势", "归附"]) {
          await setPosition(page, layout);
          const documents = state.documents;
          await Promise.all([
            page.waitForEvent("load"),
            page.getByRole("button", { name: label, exact: true }).click(),
          ]);
          await expect.poll(() => state.documents).toBe(documents + 1);
          await expect.poll(() => scrollPosition(page, layout)).toBe(900);
          expect(await page.evaluate(() => sessionStorage.getItem("jail:scroll:/jail/"))).toBeNull();
        }
        expect(state.actions).toEqual([{}, { method: "reason", speaker_id: 3 }, { mode: "standard" }]);
      });

      test("release form redirect restores the nearest reachable position on a shorter page", async ({ page }) => {
        await routeJail(page, layout);
        await setPosition(page, layout);
        await Promise.all([
          page.waitForEvent("load"),
          page.getByRole("button", { name: "释放", exact: true }).click(),
        ]);
        await expect(page.getByText("俘虏已释放", { exact: true })).toBeVisible();
        const reachable = await page.evaluate((mode) => {
          const content = document.querySelector(".content-area");
          return mode === "content" ? content.scrollHeight - content.clientHeight : document.documentElement.scrollHeight - innerHeight;
        }, layout);
        expect(reachable).toBeGreaterThan(0);
        expect(reachable).toBeLessThan(900);
        await expect.poll(() => scrollPosition(page, layout)).toBe(reachable);
        expect(await page.evaluate(() => sessionStorage.getItem("jail:scroll:/jail/"))).toBeNull();
      });

      test("cancelled confirmations and failed requests leave the page and scroll unchanged", async ({ page }) => {
        const state = await routeJail(page, layout);
        await setPosition(page, layout);
        await page.evaluate(() => { window.acceptConfirmation = false; });
        for (const label of ["归附", "释放"]) {
          await page.getByRole("button", { name: label, exact: true }).click();
          await expect(page.locator("[data-prisoner-id]")).not.toHaveAttribute("data-jail-action-pending", "1");
          await expect.poll(() => scrollPosition(page, layout)).toBe(900);
        }
        expect(state.actions).toHaveLength(0);
        state.fail = true;
        await page.getByRole("button", { name: "察言观色", exact: true }).click();
        await expect.poll(() => page.evaluate(() => window.errors.length)).toBe(1);
        await expect.poll(() => scrollPosition(page, layout)).toBe(900);
        expect(state.documents).toBe(1);
        expect(state.released).toBe(false);
        expect(page.url()).toBe(jailUrl);
        expect(await page.evaluate(() => sessionStorage.getItem("jail:scroll:/jail/"))).toBeNull();
      });
    });
  }
}
