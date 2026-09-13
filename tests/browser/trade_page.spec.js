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
