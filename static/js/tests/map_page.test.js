const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function createElement({ dataset = {}, value = "", textContent = "" } = {}) {
  const listeners = new Map();
  return {
    listeners,
    dataset,
    value,
    textContent,
    style: {},
    disabled: false,
    className: "",
    childNodes: [],
    validationMessage: "",
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
    appendChild(child) {
      this.childNodes.push(child);
      return child;
    },
    replaceChildren(...children) {
      this.childNodes = children;
    },
    querySelector() {
      return { textContent: "北俱芦洲" };
    },
    setAttribute() {},
    setCustomValidity(message) {
      this.validationMessage = message;
    },
    reportValidity() {
      return this.validationMessage === "";
    },
  };
}

async function loadMapPage({ searchQuery = "", results = [], total = 1, pageSize = 20, hasMore = false,
  fetchImpl = null, fakeTimers = false } = {}) {
  const scriptPath = path.resolve(__dirname, "..", "map-page.js");
  const scriptSource = fs.readFileSync(scriptPath, "utf8");
  const page = createElement({
    dataset: {
      mapApiBase: "/manor/api/map/search/",
      mapBackfillApiUrl: "/manor/api/map/backfill/",
      scoutApiUrl: "/manor/api/map/scout/",
      currentManorId: "1",
      raidConfigUrlPrefix: "/manor/map/raid/",
    },
  });
  const elements = new Map([
    ["map-page", page],
    ["region-select", createElement({ value: "north" })],
    ["manor-search", createElement({ value: searchQuery })],
    ["search-btn", createElement()],
    ["manor-list", createElement()],
    ["pagination", createElement()],
    ["prev-page", createElement()],
    ["next-page", createElement()],
    ["page-info", createElement()],
    ["page-number", createElement()],
    ["page-jump", createElement()],
  ]);
  const calls = [];
  const alerts = [];
  const errors = [];
  const timers = new Map();
  let timerId = 0;
  const document = {
    cookie: "csrftoken=test-csrf-token",
    getElementById(id) {
      return elements.get(id) || null;
    },
    querySelector(selector) {
      if (selector === 'meta[name="csrf-token"]') {
        return null;
      }
      if (selector === 'input[name="csrfmiddlewaretoken"]') {
        return null;
      }
      return null;
    },
    createElement() {
      return createElement();
    },
    createTextNode(text) {
      return { textContent: text };
    },
    createDocumentFragment() {
      return createElement();
    },
  };
  const context = {
    console: { ...console, error: (...args) => errors.push(args) },
    AbortController,
    document,
    fetch: async (url, options) => {
      calls.push({ url, options });
      if (fetchImpl) return fetchImpl(url, options);
      return {
        ok: true,
        json: async () => ({
          success: true,
          results,
          total,
          page_size: pageSize,
          has_more: hasMore,
        }),
      };
    },
    window: {
      alert(message) {
        alerts.push(message);
      },
      confirm() {
        return true;
      },
      setTimeout: fakeTimers ? (callback, delay) => {
        const id = ++timerId;
        timers.set(id, { callback, delay });
        return id;
      } : setTimeout,
      clearTimeout: fakeTimers ? (id) => timers.delete(id) : clearTimeout,
    },
    encodeURIComponent,
    Number,
    String,
  };
  context.window.window = context.window;
  context.window.document = document;
  vm.createContext(context);
  vm.runInContext(scriptSource, context, { filename: "map-page.js" });
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  return { calls, alerts, elements, timers, errors };
}

test("region search explicitly requests virtual-player backfill after loading its first page", async () => {
  const { calls } = await loadMapPage();

  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, "/manor/api/map/search/?type=region&region=north&page=1");
  assert.equal(calls[1].url, "/manor/api/map/backfill/");
  assert.equal(calls[1].options.method, "POST");
  assert.equal(calls[1].options.headers["X-CSRFToken"], "test-csrf-token");
  assert.equal(calls[1].options.body, JSON.stringify({ region: "north" }));
});

test("name search does not request region backfill", async () => {
  const { calls } = await loadMapPage({ searchQuery: "target" });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/manor/api/map/search/?type=name&q=target");
});

test("blocked attack shows the reason and does not open the raid config page", async () => {
  const { alerts, elements } = await loadMapPage({
    results: [
      {
        id: 9,
        name: "高声望目标",
        region_display: "北俱芦洲",
        coordinate_x: 1,
        coordinate_y: 2,
        can_attack: false,
        attack_reason: "对方声望过高，无法攻击",
        is_protected: false,
      },
    ],
  });

  const fragment = elements.get("manor-list").childNodes[0];
  const card = fragment.childNodes.find((node) => node.className === "tw-manor-card");
  const actions = card.childNodes[1].childNodes[0];
  const attackLink = actions.childNodes[1];
  let prevented = false;

  attackLink.listeners.get("click")({
    preventDefault() {
      prevented = true;
    },
  });

  assert.equal(prevented, true);
  assert.deepEqual(alerts, ["对方声望过高，无法攻击"]);
  assert.equal(attackLink.href, "/manor/map/raid/9/");
});

test("map name color follows the server prestige range, not an unrelated attack block", async () => {
  const { elements } = await loadMapPage({
    results: [
      { id: 2, name: "免战目标", can_attack: false, is_protected: true, prestige_color: "white" },
      { id: 3, name: "高声望目标", can_attack: false, is_protected: false, prestige_color: "red" },
      { id: 4, name: "低声望目标", can_attack: false, is_protected: false, prestige_color: "green" },
    ],
  });

  const fragment = elements.get("manor-list").childNodes[0];
  const cards = fragment.childNodes.filter((node) => node.className === "tw-manor-card");
  const nameClasses = cards.map((card) => card.childNodes[0].childNodes[0].className);

  assert.equal(nameClasses[0], "tw-manor-name");
  assert.equal(nameClasses[1], "tw-manor-name map-target-prestige-higher");
  assert.equal(nameClasses[2], "tw-manor-name map-target-prestige-lower");
});

test("page jump accepts a page in range and rejects pages outside it", async () => {
  const { calls, elements } = await loadMapPage({ total: 45, pageSize: 20, hasMore: true });
  const input = elements.get("page-number");
  const jump = elements.get("page-jump");

  assert.equal(input.max, "3");
  input.value = "4";
  jump.listeners.get("click")();
  assert.match(input.validationMessage, /1 到 3/);
  assert.equal(calls.length, 2);

  input.value = "3";
  jump.listeners.get("click")();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls[2].url, "/manor/api/map/search/?type=region&region=north&page=3");
  assert.equal(input.validationMessage, "");
});

test("hung search times out instead of leaving the loading hint forever, then can retry", async () => {
  let hung = true;
  const { calls, elements, timers } = await loadMapPage({
    searchQuery: "target", fakeTimers: true,
    fetchImpl: async () => hung ? new Promise(() => {}) : {
      ok: true, json: async () => ({ success: true, results: [] }),
    },
  });
  assert.equal(elements.get("manor-list").childNodes[0].textContent, "正在加载...");
  const timer = [...timers.values()][0];
  assert.equal(timer.delay, 10000);
  timer.callback();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls[0].options.signal.aborted, true);
  assert.equal(elements.get("manor-list").childNodes[0].textContent, "搜索超时，请重试");
  assert.equal(timers.size, 0);
  hung = false;
  elements.get("search-btn").listeners.get("click")();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(elements.get("manor-list").childNodes[0].textContent, "暂无庄园数据");
});

test("an old search cannot replace a newer search result", async () => {
  const pending = [];
  const { calls, elements } = await loadMapPage({
    searchQuery: "old",
    fetchImpl: () => new Promise((resolve) => pending.push(resolve)),
  });
  elements.get("manor-search").value = "new";
  elements.get("search-btn").listeners.get("click")();
  assert.equal(calls[0].options.signal.aborted, true);
  pending[1]({ ok: true, json: async () => ({ success: true, results: [] }) });
  await new Promise((resolve) => setImmediate(resolve));
  pending[0]({ ok: true, json: async () => ({ success: false }) });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(elements.get("manor-list").childNodes[0].textContent, "暂无庄园数据");
});

test("non-success HTTP response exits loading without parsing its body", async () => {
  const { elements } = await loadMapPage({
    searchQuery: "target",
    fetchImpl: async () => ({ ok: false, status: 503, json: () => { throw new Error("must not parse"); } }),
  });
  assert.equal(elements.get("manor-list").childNodes[0].textContent, "加载失败，请稍后重试");
});
