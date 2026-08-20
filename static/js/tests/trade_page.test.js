const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function createCountdownElement({ expires, remaining, onText } = {}) {
  let text = "";
  return {
    dataset: {
      ...(expires ? { expires } : {}),
      ...(remaining !== undefined ? { remaining: String(remaining) } : {}),
    },
    isConnected: true,
    style: {},
    get textContent() {
      return text;
    },
    set textContent(value) {
      if (onText) onText(value);
      text = value;
    },
  };
}

function createTradePageHarness() {
  let marketTextWrites = 0;
  const marketCountdown = createCountdownElement({
    expires: "2999-01-01T00:00:00.000Z",
    onText: () => {
      marketTextWrites += 1;
    },
  });
  const auctionCountdown = createCountdownElement({ remaining: 120 });
  const intervals = [];
  const root = {
    dataset: {},
    addEventListener() {},
    removeEventListener() {},
    contains() {
      return true;
    },
    querySelectorAll(selector) {
      return selector === ".tw-market-countdown" ? [marketCountdown] : [];
    },
    querySelector(selector) {
      return selector === ".auction-countdown" ? auctionCountdown : null;
    },
  };
  const documentObj = {
    readyState: "complete",
    querySelector(selector) {
      return selector === '[data-trade-page="1"]' ? root : null;
    },
    addEventListener() {},
  };
  const tradeCore = {
    DEFAULT_MARKET_DURATION: 86400,
    ONE_HOUR_SECONDS: 3600,
    parseInteger(value, fallback) {
      const parsed = Number.parseInt(value, 10);
      return Number.isFinite(parsed) ? parsed : fallback;
    },
    formatMarketCountdown(value) {
      return `market:${value}`;
    },
    formatAuctionCountdown(value) {
      return `auction:${value}`;
    },
  };
  const windowObj = {
    TradePageCore: tradeCore,
    setInterval(callback) {
      intervals.push(callback);
      return intervals.length;
    },
    clearInterval() {},
  };
  windowObj.window = windowObj;
  windowObj.document = documentObj;

  const context = {
    console: { error() {} },
    document: documentObj,
    window: windowObj,
  };
  vm.createContext(context);
  const scriptPath = path.resolve(__dirname, "..", "trade.js");
  vm.runInContext(fs.readFileSync(scriptPath, "utf8"), context, { filename: "trade.js" });

  return { auctionCountdown, intervals, marketCountdown, marketTextWrites: () => marketTextWrites };
}

test("trade page shares one countdown interval for market and auction timers", () => {
  const harness = createTradePageHarness();

  assert.equal(harness.intervals.length, 1);
  harness.intervals[0]();
  harness.intervals[0]();

  assert.match(harness.marketCountdown.textContent, /^market:/);
  assert.equal(harness.marketTextWrites(), 1);
  assert.equal(harness.auctionCountdown.textContent, "auction:118");
});
