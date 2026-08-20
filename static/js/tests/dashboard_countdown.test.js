const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function flushAsyncWork() {
  return new Promise((resolve) => setImmediate(resolve));
}

function createClassList() {
  const values = new Set();
  return {
    add(value) {
      values.add(value);
    },
    remove(value) {
      values.delete(value);
    },
    contains(value) {
      return values.has(value);
    },
  };
}

function createDashboardEnvironment(fetchResponses) {
  const timeoutHandles = [];
  const eventListeners = new Map();
  const levelElement = { textContent: "等级 1" };
  const hpElement = { textContent: "生命 100/100" };
  const upgradeCell = {
    textContent: "",
    appendChild() {},
  };
  const row = {
    querySelector(selector) {
      if (selector === ".guest-level") return levelElement;
      if (selector === ".guest-hp") return hpElement;
      return null;
    },
  };
  const attributes = new Map([
    ["data-countdown", new Date(Date.now() - 1000).toISOString()],
    ["data-format", "zh"],
    ["data-check-url", "/guests/1/check-training/"],
  ]);
  const countdownElement = {
    nodeType: 1,
    isConnected: true,
    textContent: "计算中",
    classList: createClassList(),
    getAttribute(name) {
      return attributes.get(name) ?? null;
    },
    hasAttribute(name) {
      return attributes.has(name);
    },
    setAttribute(name, value) {
      attributes.set(name, String(value));
    },
    removeAttribute(name) {
      attributes.delete(name);
    },
    querySelectorAll() {
      return [];
    },
    closest(selector) {
      if (selector === "tr[data-guest-id]") return row;
      if (selector === "td") return upgradeCell;
      return null;
    },
  };

  let currentPageShell = {
    querySelectorAll(selector) {
      if (selector === "[data-countdown]") return [countdownElement];
      return [];
    },
  };

  const documentObj = {
    readyState: "complete",
    cookie: "",
    body: {
      contains(element) {
        return element === countdownElement;
      },
    },
    querySelectorAll(selector) {
      if (selector === "[data-countdown]") return [countdownElement];
      return [];
    },
    querySelector(selector) {
      if (selector === "#page-shell") return currentPageShell;
      if (selector === 'meta[name="csrf-token"]') {
        return { getAttribute: () => "test-csrf-token" };
      }
      return null;
    },
    addEventListener(type, listener) {
      eventListeners.set(type, listener);
    },
    dispatchEvent(event) {
      const listener = eventListeners.get(event.type);
      if (listener) listener(event);
    },
    createElement() {
      return {
        className: "",
        textContent: "",
      };
    },
  };

  const windowObj = {
    AbortController,
    location: { reload() {} },
    setInterval() {
      return 1;
    },
    setTimeout(callback, delay) {
      const handle = { callback, delay, cleared: false, fired: false };
      timeoutHandles.push(handle);
      return handle;
    },
    clearTimeout(handle) {
      handle.cleared = true;
    },
  };
  windowObj.window = windowObj;
  windowObj.document = documentObj;

  let fetchCallCount = 0;
  const context = {
    console: { error() {} },
    document: documentObj,
    window: windowObj,
    Node: { ELEMENT_NODE: 1 },
    MutationObserver: class {
      observe() {}
    },
    fetch: async () => {
      const response = fetchResponses[fetchCallCount];
      fetchCallCount += 1;
      return response;
    },
    setInterval: windowObj.setInterval,
  };
  vm.createContext(context);

  return {
    context,
    countdownElement,
    attributes,
    levelElement,
    hpElement,
    pendingTimeouts() {
      return timeoutHandles.filter((handle) => !handle.cleared && !handle.fired);
    },
    fetchCallCount() {
      return fetchCallCount;
    },
    setPageShell(nextPageShell) {
      currentPageShell = nextPageShell;
    },
  };
}

test("guest training countdown retries a failed status check and recovers", async () => {
  const nextTrainingEta = new Date(Date.now() + 60000).toISOString();
  const environment = createDashboardEnvironment([
    {
      ok: false,
      status: 503,
      json: async () => ({ success: false, error: "系统繁忙，请稍后再试" }),
    },
    {
      ok: true,
      status: 200,
      json: async () => ({
        success: true,
        level: 2,
        current_hp: 120,
        max_hp: 120,
        training_eta: nextTrainingEta,
      }),
    },
  ]);
  const scriptPath = path.resolve(__dirname, "..", "dashboard.js");
  const scriptSource = fs.readFileSync(scriptPath, "utf8");

  vm.runInContext(scriptSource, environment.context, { filename: "dashboard.js" });
  await flushAsyncWork();

  assert.equal(environment.fetchCallCount(), 1);
  assert.equal(environment.countdownElement.textContent, "检查失败，重试中...");
  assert.equal(environment.countdownElement.classList.contains("countdown-finished"), false);
  const [retryTimer] = environment.pendingTimeouts();
  assert.equal(retryTimer.delay, 2000);

  retryTimer.fired = true;
  retryTimer.callback();
  await flushAsyncWork();

  assert.equal(environment.fetchCallCount(), 2);
  assert.equal(environment.levelElement.textContent, "等级 2");
  assert.equal(environment.hpElement.textContent, "生命 120/120");
  assert.equal(environment.attributes.get("data-countdown"), nextTrainingEta);
  assert.equal(environment.countdownElement.textContent, "计算中");
  assert.equal(environment.countdownElement.classList.contains("countdown-finished"), false);
});

test("dashboard refreshes its countdown cache after partial navigation replaces the page shell", () => {
  const environment = createDashboardEnvironment([]);
  const nextCountdownElement = {
    nodeType: 1,
    isConnected: true,
    textContent: "旧内容",
    classList: createClassList(),
    getAttribute(name) {
      if (name === "data-countdown") return "2999-01-01T00:00:00.000Z";
      if (name === "data-format") return "zh";
      return null;
    },
    hasAttribute(name) {
      return name === "data-countdown";
    },
    setAttribute() {},
    removeAttribute() {},
    querySelectorAll() {
      return [];
    },
    closest() {
      return null;
    },
  };
  const nextPageShell = {
    querySelectorAll(selector) {
      if (selector === "[data-countdown]") return [nextCountdownElement];
      return [];
    },
  };
  const scriptPath = path.resolve(__dirname, "..", "dashboard.js");
  const scriptSource = fs.readFileSync(scriptPath, "utf8");

  vm.runInContext(scriptSource, environment.context, { filename: "dashboard.js" });
  environment.setPageShell(nextPageShell);
  environment.context.document.dispatchEvent({ type: "partial-nav:loaded" });

  assert.match(nextCountdownElement.textContent, /分钟|秒/);
});
