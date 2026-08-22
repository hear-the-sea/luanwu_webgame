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
  };
}

async function loadMapPage({ searchQuery = "", results = [] } = {}) {
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
    ["manor-count", createElement()],
    ["list-title", createElement()],
    ["pagination", createElement()],
    ["prev-page", createElement()],
    ["next-page", createElement()],
    ["page-info", createElement()],
  ]);
  const calls = [];
  const alerts = [];
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
    console,
    document,
    fetch: async (url, options) => {
      calls.push({ url, options });
      return {
        ok: true,
        json: async () => ({
          success: true,
          results,
          total: 1,
          page_size: 20,
          has_more: false,
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
      setTimeout,
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
  return { calls, alerts, elements };
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
