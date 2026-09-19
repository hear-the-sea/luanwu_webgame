const { expect, test } = require("@playwright/test");
const path = require("node:path");

const staticPath = (fileName) => path.resolve(__dirname, "../../static/js", fileName);

test("trade page delegates mode and listing interactions in a real browser", async ({ page }) => {
  await page.setContent(`
    <main data-trade-page="1" data-default-market-duration="3600">
      <button type="button" class="tw-mode-btn active" data-mode="buy">买入</button>
      <button type="button" class="tw-mode-btn" data-mode="sell"><span>卖出</span></button>
      <section id="shop-buy-section"></section>
      <section id="shop-sell-section" class="tw-shop-section-hidden"></section>
      <button
        type="button"
        class="js-open-listing-modal"
        data-item-key="test-item"
        data-item-name="测试道具"
        data-available="12"
        data-min-price="25"
        data-rarity="purple"
        data-image-url="/static/test-item.png"
      ><span>上架测试道具</span></button>
      <button
        type="button"
        class="js-open-listing-modal"
        data-item-key="fallback-item"
        data-item-name="无图道具"
        data-available="3"
        data-min-price="5"
        data-rarity="green"
      ><span>上架无图道具</span></button>
      <span class="tw-market-countdown" data-expires="2999-01-01T00:00:00.000Z"></span>
      <div id="listingModal" style="display: none">
        <button type="button" data-trade-close-modal="listing">关闭</button>
        <input id="modal-item-key" />
        <span id="modal-item-name"></span>
        <span id="modal-available"></span>
        <span id="modal-min-price"></span>
        <input id="modal-quantity" />
        <input id="modal-unit-price" />
        <span id="modal-fee"></span>
        <span id="modal-item-icon"></span>
        <label><input type="radio" name="duration" value="3600" data-trade-duration-fee="2" /></label>
      </div>
    </main>
  `);

  await page.evaluate(() => {
    window.__historyCalls = [];
    window.history.replaceState = (...args) => window.__historyCalls.push(args);
  });
  await page.addScriptTag({ path: staticPath("trade_core.js") });
  await page.addScriptTag({ path: staticPath("trade.js") });

  await page.locator('[data-mode="sell"] span').click();
  await expect(page.locator('[data-mode="sell"]')).toHaveClass(/active/);
  await expect(page.locator("#shop-buy-section")).toHaveClass(/tw-shop-section-hidden/);
  await expect(page.locator("#shop-sell-section")).not.toHaveClass(/tw-shop-section-hidden/);
  expect(await page.evaluate(() => window.__historyCalls.length)).toBe(1);

  await page.locator('[data-item-key="test-item"] span').click();
  await expect(page.locator("#listingModal")).toHaveCSS("display", "flex");
  await expect(page.locator("#modal-item-name")).toHaveText("测试道具");
  await expect(page.locator("#modal-available")).toHaveText("12");
  await expect(page.locator("#modal-unit-price")).toHaveValue("25");
  await expect(page.locator("#modal-item-icon img")).toHaveAttribute("src", "/static/test-item.png");

  await page.locator('[data-trade-close-modal="listing"]').click();
  await expect(page.locator("#listingModal")).toHaveCSS("display", "none");

  await page.locator('[data-item-key="fallback-item"] span').click();
  await expect(page.locator("#listingModal")).toHaveCSS("display", "flex");
  await expect(page.locator("#modal-item-name")).toHaveText("无图道具");
  await expect(page.locator("#modal-item-icon img")).toHaveCount(0);
  await expect(page.locator("#modal-item-icon .tw-icon-placeholder")).toHaveText("无");

  await page.locator('[data-trade-close-modal="listing"]').click();
  await page.locator('[data-item-key="test-item"] span').click();
  await expect(page.locator("#listingModal")).toHaveCSS("display", "flex");
  await expect(page.locator("#modal-item-name")).toHaveText("测试道具");
  await expect(page.locator("#modal-item-icon img")).toHaveAttribute("src", "/static/test-item.png");
});

test("trade page confirms before selling all of an item", async ({ page }) => {
  await page.setContent(`
    <main data-trade-page="1">
      <form class="shop-sell-form" method="post">
        <input name="quantity" value="1" min="1" max="12" />
        <button type="submit" class="tw-btn-trade">出售</button>
        <button
          type="submit"
          class="tw-btn-trade tw-btn-trade--danger"
          data-shop-sell-all
          data-item-name="测试道具"
          data-item-quantity="12"
        >全部卖出</button>
      </form>
    </main>
  `);

  await page.evaluate(() => {
    window.__shopSellAll = { confirms: [], submits: 0, confirmResult: false };
    window.gameConfirm = (message, options) => {
      window.__shopSellAll.confirms.push({ message, options });
      return Promise.resolve(window.__shopSellAll.confirmResult);
    };
    HTMLFormElement.prototype.submit = function () {
      window.__shopSellAll.submits += 1;
    };
  });
  await page.addScriptTag({ path: staticPath("trade_core.js") });
  await page.addScriptTag({ path: staticPath("trade.js") });

  const form = page.locator(".shop-sell-form");
  await form.locator("[data-shop-sell-all]").click();
  await expect(form.locator('input[name="quantity"]')).toHaveValue("12");
  expect(await page.evaluate(() => window.__shopSellAll.confirms)).toEqual([
    {
      message: "确认全部卖出「测试道具」×12？卖出后无法撤回。",
      options: {
        title: "全部卖出确认",
        okText: "确认卖出",
        cancelText: "保留物品",
        danger: true,
      },
    },
  ]);
  expect(await page.evaluate(() => window.__shopSellAll.submits)).toBe(0);

  await page.evaluate(() => {
    window.__shopSellAll.confirmResult = true;
  });
  await form.locator("[data-shop-sell-all]").click();
  await expect.poll(() => page.evaluate(() => window.__shopSellAll.submits)).toBe(1);
});
