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

function createDashboardEnvironment(fetchResponses, storageData = new Map()) {
  const timeoutHandles = [];
  const eventListeners = new Map();
  let reloadCalls = 0;
  const navigationCalls = [];
  let blockingInteraction = false;
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
    visibilityState: "visible",
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
      if (
        selector.includes("game-dialog-overlay")
        || selector.includes("data-confirming")
        || selector.includes("data-action-pending")
      ) {
        return blockingInteraction ? {} : null;
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
    location: {
      pathname: "/",
      reload() {
        reloadCalls += 1;
      },
      assign(url) {
        navigationCalls.push(url);
      },
    },
    sessionStorage: {
      getItem(key) { return storageData.get(key) ?? null; },
      setItem(key, value) { storageData.set(key, value); },
    },
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
    storageData,
    levelElement,
    hpElement,
    pendingTimeouts() {
      return timeoutHandles.filter((handle) => !handle.cleared && !handle.fired);
    },
    fetchCallCount() {
      return fetchCallCount;
    },
    reloadCalls() {
      return reloadCalls;
    },
    navigationCalls() {
      return navigationCalls;
    },
    dispatchEvent(event) {
      documentObj.dispatchEvent(event);
    },
    setBlockingInteraction(value) {
      blockingInteraction = value;
    },
    setPageShell(nextPageShell) {
      currentPageShell = nextPageShell;
    },
  };
}

test("upgrade countdowns round up minutes and always show hours and minutes", () => {
  const scriptSource = fs.readFileSync(path.resolve(__dirname, "..", "dashboard.js"), "utf8");
  for (const [remainingMs, expected] of [
    [1, "0小时1分钟"],
    [60000, "0小时1分钟"],
    [3599000, "1小时0分钟"],
    [3600000, "1小时0分钟"],
    [3660000, "1小时1分钟"],
    [90000000, "25小时0分钟"],
  ]) {
    const environment = createDashboardEnvironment([]);
    const now = Date.now();
    environment.context.Date = class extends Date {
      static now() { return now; }
    };
    environment.attributes.set("data-countdown", new Date(now + remainingMs).toISOString());
    environment.attributes.set("data-format", "hours-minutes");
    vm.runInContext(scriptSource, environment.context, { filename: "dashboard.js" });
    assert.equal(environment.countdownElement.textContent, expected);
  }
});

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

test("dashboard reloads an expired runtime countdown when the page is idle", async () => {
  const environment = createDashboardEnvironment([{ ok: true }]);
  let now = Date.now();
  environment.context.Date = class extends Date {
    static now() { return now; }
  };
  environment.attributes.set("data-countdown", new Date(now - 2000).toISOString());
  environment.attributes.delete("data-check-url");
  environment.attributes.set("data-refresh", "1");
  environment.attributes.set("data-refresh-url", "/runtime/refresh/");
  environment.attributes.set("data-refresh-method", "post");

  const scriptSource = fs.readFileSync(path.resolve(__dirname, "..", "dashboard.js"), "utf8");
  vm.runInContext(scriptSource, environment.context, { filename: "dashboard.js" });
  await flushAsyncWork();

  const [reloadTimer] = environment.pendingTimeouts();
  assert.equal(reloadTimer.delay, 500);
  reloadTimer.fired = true;
  reloadTimer.callback();

  assert.equal(environment.reloadCalls(), 1);
});

test("dashboard reloads once when an arrival timer has no server write", async () => {
  const environment = createDashboardEnvironment([{
    ok: true,
    json: async () => ({ success: true, refreshed: 0 }),
  }]);
  const now = Date.now();
  environment.context.Date = class extends Date {
    static now() { return now; }
  };
  environment.attributes.set("data-countdown", new Date(now - 2000).toISOString());
  environment.attributes.delete("data-check-url");
  environment.attributes.set("data-refresh", "1");
  environment.attributes.set("data-refresh-url", "/missions/refresh/");
  environment.attributes.set("data-refresh-method", "post");

  const scriptSource = fs.readFileSync(path.resolve(__dirname, "..", "dashboard.js"), "utf8");
  vm.runInContext(scriptSource, environment.context, { filename: "dashboard.js" });
  await flushAsyncWork();

  assert.equal(environment.fetchCallCount(), 1);
  const [reloadTimer] = environment.pendingTimeouts();
  assert.equal(reloadTimer.delay, 500);
  reloadTimer.fired = true;
  reloadTimer.callback();
  assert.equal(environment.reloadCalls(), 1);

  const sameCountdown = createDashboardEnvironment([{
    ok: true,
    json: async () => ({ success: true, refreshed: 0 }),
  }], environment.storageData);
  sameCountdown.context.Date = environment.context.Date;
  sameCountdown.attributes.set("data-countdown", new Date(now - 2000).toISOString());
  sameCountdown.attributes.delete("data-check-url");
  sameCountdown.attributes.set("data-refresh", "1");
  sameCountdown.attributes.set("data-refresh-url", "/missions/refresh/");
  sameCountdown.attributes.set("data-refresh-method", "post");
  vm.runInContext(scriptSource, sameCountdown.context, { filename: "dashboard.js" });
  await flushAsyncWork();

  assert.equal(sameCountdown.reloadCalls(), 0);
  assert.equal(sameCountdown.countdownElement.textContent, "等待服务器结算...");
  assert.equal(sameCountdown.pendingTimeouts()[0].delay, 2000);
});

test("dashboard waits for asynchronous battle processing before reloading", async () => {
  const environment = createDashboardEnvironment([
    {
      ok: true,
      status: 200,
      json: async () => ({ success: true, refreshed: 1, pending: true }),
    },
    {
      ok: true,
      status: 200,
      json: async () => ({ success: true, refreshed: 0, pending: false }),
    },
  ]);
  let now = Date.now();
  environment.context.Date = class extends Date {
    static now() { return now; }
  };
  environment.attributes.set("data-countdown", new Date(now - 2000).toISOString());
  environment.attributes.delete("data-check-url");
  environment.attributes.set("data-refresh", "1");
  environment.attributes.set("data-refresh-url", "/runtime/refresh/");
  environment.attributes.set("data-refresh-method", "post");

  const scriptSource = fs.readFileSync(path.resolve(__dirname, "..", "dashboard.js"), "utf8");
  vm.runInContext(scriptSource, environment.context, { filename: "dashboard.js" });
  await flushAsyncWork();

  assert.equal(environment.fetchCallCount(), 1);
  assert.equal(environment.reloadCalls(), 0);
  assert.equal(environment.countdownElement.textContent, "等待服务器结算...");
  const [retryTimer] = environment.pendingTimeouts();
  assert.equal(retryTimer.delay, 2000);

  retryTimer.fired = true;
  retryTimer.callback();
  await flushAsyncWork();

  assert.equal(environment.fetchCallCount(), 2);
  const [reloadTimer] = environment.pendingTimeouts();
  assert.equal(reloadTimer.delay, 500);
  reloadTimer.fired = true;
  reloadTimer.callback();
  assert.equal(environment.reloadCalls(), 1);
});

test("dashboard refreshes separate expired activities even if one request hangs", async () => {
  const environment = createDashboardEnvironment([
    new Promise(() => {}),
    { ok: true, json: async () => ({ success: true, refreshed: 1 }) },
  ]);
  const now = Date.now();
  environment.context.Date = class extends Date {
    static now() { return now; }
  };
  environment.attributes.set("data-countdown", new Date(now - 2000).toISOString());
  environment.attributes.delete("data-check-url");
  environment.attributes.set("data-refresh", "1");
  environment.attributes.set("data-refresh-url", "/missions/refresh/");

  const secondAttributes = new Map([
    ["data-countdown", new Date(now - 2000).toISOString()],
    ["data-refresh", "1"],
    ["data-refresh-url", "/buildings/refresh/"],
  ]);
  const secondCountdown = {
    isConnected: true,
    textContent: "计算中",
    classList: createClassList(),
    getAttribute(name) { return secondAttributes.get(name) ?? null; },
    removeAttribute(name) { secondAttributes.delete(name); },
    closest() { return null; },
  };
  environment.setPageShell({
    querySelectorAll(selector) {
      if (selector === "[data-countdown]") return [environment.countdownElement, secondCountdown];
      return [];
    },
  });

  const scriptSource = fs.readFileSync(path.resolve(__dirname, "..", "dashboard.js"), "utf8");
  vm.runInContext(scriptSource, environment.context, { filename: "dashboard.js" });
  await flushAsyncWork();

  assert.equal(environment.fetchCallCount(), 2);
  assert.equal(environment.pendingTimeouts().some((timer) => timer.delay === 500), true);
});

test("dashboard times out a stalled refresh and schedules a retry", async () => {
  const environment = createDashboardEnvironment([new Promise(() => {})]);
  const now = Date.now();
  environment.context.Date = class extends Date {
    static now() { return now; }
  };
  environment.attributes.set("data-countdown", new Date(now - 2000).toISOString());
  environment.attributes.delete("data-check-url");
  environment.attributes.set("data-refresh", "1");
  environment.attributes.set("data-refresh-url", "/missions/refresh/");

  const scriptSource = fs.readFileSync(path.resolve(__dirname, "..", "dashboard.js"), "utf8");
  vm.runInContext(scriptSource, environment.context, { filename: "dashboard.js" });
  await flushAsyncWork();

  const [requestTimer] = environment.pendingTimeouts();
  assert.equal(requestTimer.delay, 10000);
  requestTimer.fired = true;
  requestTimer.callback();
  await flushAsyncWork();

  assert.equal(environment.countdownElement.textContent, "刷新失败，稍后重试...");
  assert.equal(environment.pendingTimeouts()[0].delay, 2000);
});

test("dashboard opens login after an expired session instead of retrying the refresh", async () => {
  const loginUrl = "https://game.example/accounts/login/?next=/manor/api/missions/refresh/";
  const environment = createDashboardEnvironment([{
    ok: true,
    redirected: true,
    url: loginUrl,
    json: async () => { throw new Error("login page is not JSON"); },
  }]);
  const now = Date.now();
  environment.context.Date = class extends Date {
    static now() { return now; }
  };
  environment.attributes.set("data-countdown", new Date(now - 2000).toISOString());
  environment.attributes.delete("data-check-url");
  environment.attributes.set("data-refresh", "1");
  environment.attributes.set("data-refresh-url", "/manor/api/missions/refresh/");

  const scriptSource = fs.readFileSync(path.resolve(__dirname, "..", "dashboard.js"), "utf8");
  vm.runInContext(scriptSource, environment.context, { filename: "dashboard.js" });
  await flushAsyncWork();

  assert.deepEqual(environment.navigationCalls(), [loginUrl]);
  assert.equal(environment.reloadCalls(), 0);
  assert.equal(environment.pendingTimeouts().length, 0);
});

test("guest training opens login after an expired session instead of retrying", async () => {
  const loginUrl = "https://game.example/accounts/login/?next=/guests/1/check-training/";
  const environment = createDashboardEnvironment([{
    ok: true,
    redirected: true,
    url: loginUrl,
    json: async () => { throw new Error("login page is not JSON"); },
  }]);
  const scriptSource = fs.readFileSync(path.resolve(__dirname, "..", "dashboard.js"), "utf8");
  vm.runInContext(scriptSource, environment.context, { filename: "dashboard.js" });
  await flushAsyncWork();

  assert.deepEqual(environment.navigationCalls(), [loginUrl]);
  assert.equal(environment.pendingTimeouts().length, 0);
});

test("dashboard backs off after a failed runtime refresh instead of reloading in a loop", async () => {
  const environment = createDashboardEnvironment([
    {
      ok: false,
      status: 429,
      json: async () => ({ success: false, error: "状态刷新过于频繁" }),
    },
  ]);
  let now = Date.now();
  environment.context.Date = class extends Date {
    static now() { return now; }
  };
  environment.attributes.set("data-countdown", new Date(now - 2000).toISOString());
  environment.attributes.delete("data-check-url");
  environment.attributes.set("data-refresh", "1");
  environment.attributes.set("data-refresh-url", "/runtime/refresh/");
  environment.attributes.set("data-refresh-method", "post");

  const scriptSource = fs.readFileSync(path.resolve(__dirname, "..", "dashboard.js"), "utf8");
  vm.runInContext(scriptSource, environment.context, { filename: "dashboard.js" });
  await flushAsyncWork();

  assert.equal(environment.fetchCallCount(), 1);
  assert.equal(environment.reloadCalls(), 0);
  assert.equal(environment.countdownElement.textContent, "刷新失败，稍后重试...");
  const [retryTimer] = environment.pendingTimeouts();
  assert.equal(retryTimer.delay, 2000);
});

test("dashboard preserves the interaction grace when refresh settles after a click", async () => {
  let resolveRefresh;
  const refreshResponse = new Promise((resolve) => {
    resolveRefresh = resolve;
  });
  const environment = createDashboardEnvironment([refreshResponse]);
  let now = Date.now();
  environment.context.Date = class extends Date {
    static now() { return now; }
  };
  environment.attributes.set("data-countdown", new Date(now - 2000).toISOString());
  environment.attributes.delete("data-check-url");
  environment.attributes.set("data-refresh", "1");
  environment.attributes.set("data-refresh-url", "/runtime/refresh/");
  environment.attributes.set("data-refresh-method", "post");

  const scriptSource = fs.readFileSync(path.resolve(__dirname, "..", "dashboard.js"), "utf8");
  vm.runInContext(scriptSource, environment.context, { filename: "dashboard.js" });
  environment.dispatchEvent({ type: "pointerdown" });
  resolveRefresh({ ok: true });
  await flushAsyncWork();

  const [reloadTimer] = environment.pendingTimeouts();
  assert.equal(reloadTimer.delay, 500);
  now += 500;
  reloadTimer.fired = true;
  reloadTimer.callback();
  assert.equal(environment.reloadCalls(), 0);

  const [graceTimer] = environment.pendingTimeouts();
  assert.equal(graceTimer.delay, 1000);
  now += 1000;
  graceTimer.fired = true;
  graceTimer.callback();
  assert.equal(environment.reloadCalls(), 1);
});

test("dashboard defers reload after user interaction and cancels it on partial navigation", async () => {
  const environment = createDashboardEnvironment([{ ok: true }]);
  let now = Date.now();
  environment.context.Date = class extends Date {
    static now() { return now; }
  };
  environment.attributes.set("data-countdown", new Date(now - 2000).toISOString());
  environment.attributes.delete("data-check-url");
  environment.attributes.set("data-refresh", "1");
  environment.attributes.set("data-refresh-url", "/runtime/refresh/");
  environment.attributes.set("data-refresh-method", "post");

  const scriptSource = fs.readFileSync(path.resolve(__dirname, "..", "dashboard.js"), "utf8");
  vm.runInContext(scriptSource, environment.context, { filename: "dashboard.js" });
  await flushAsyncWork();

  const [initialReloadTimer] = environment.pendingTimeouts();
  environment.dispatchEvent({ type: "pointerdown" });
  assert.equal(initialReloadTimer.cleared, true);
  const [deferredReloadTimer] = environment.pendingTimeouts();
  assert.equal(deferredReloadTimer.delay, 1500);

  now += 1500;
  environment.setBlockingInteraction(true);
  deferredReloadTimer.fired = true;
  deferredReloadTimer.callback();
  assert.equal(environment.reloadCalls(), 0);
  const [blockedReloadTimer] = environment.pendingTimeouts();
  assert.equal(blockedReloadTimer.delay, 500);

  environment.setBlockingInteraction(false);
  now += 500;
  blockedReloadTimer.fired = true;
  blockedReloadTimer.callback();
  assert.equal(environment.reloadCalls(), 1);

  const secondEnvironment = createDashboardEnvironment([{ ok: true }]);
  now = Date.now();
  secondEnvironment.context.Date = class extends Date {
    static now() { return now; }
  };
  secondEnvironment.attributes.set("data-countdown", new Date(now - 2000).toISOString());
  secondEnvironment.attributes.delete("data-check-url");
  secondEnvironment.attributes.set("data-refresh", "1");
  secondEnvironment.attributes.set("data-refresh-url", "/runtime/refresh/");
  secondEnvironment.attributes.set("data-refresh-method", "post");
  vm.runInContext(scriptSource, secondEnvironment.context, { filename: "dashboard.js" });
  await flushAsyncWork();

  secondEnvironment.dispatchEvent({ type: "partial-nav:started" });
  assert.equal(secondEnvironment.pendingTimeouts().length, 0);
  assert.equal(secondEnvironment.reloadCalls(), 0);
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
