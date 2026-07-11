const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function createElement(tagName) {
  const listeners = new Map();
  const element = {
    tagName,
    className: "",
    textContent: "",
    attributes: new Map(),
    children: [],
    style: {},
    dataset: {},
    classList: {
      values: new Set(),
      add(value) {
        this.values.add(value);
        element.className = Array.from(this.values).join(" ");
      },
      contains(value) {
        return this.values.has(value);
      },
    },
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    remove() {
      this.removed = true;
    },
    setAttribute(name, value) {
      this.attributes.set(name, value);
    },
    getAttribute(name) {
      return this.attributes.get(name);
    },
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
    dispatchEvent(event) {
      const listener = listeners.get(event.type);
      if (listener) {
        listener.call(this, event);
      }
    },
    querySelector(selector) {
      const className = selector.startsWith(".") ? selector.slice(1) : selector;
      const directMatch = this.children.find((child) => child.className.split(" ").includes(className));
      if (directMatch) {
        return directMatch;
      }
      for (const child of this.children) {
        const nestedMatch = child.querySelector(selector);
        if (nestedMatch) {
          return nestedMatch;
        }
      }
      return null;
    },
  };
  return element;
}

function createNotificationsHarness({
  pathname = "/gameplay/recruitment/",
  unread = "0",
  withSidebarLink = true,
  withTopLink = false,
  random = 0.5,
} = {}) {
  const scriptPath = path.resolve(__dirname, "..", "notifications.js");
  const scriptSource = fs.readFileSync(scriptPath, "utf8");
  const timers = [];
  const clearedTimerIds = [];
  let nextTimerId = 1;
  const documentListeners = new Map();
  const navigation = {
    sidebar: withSidebarLink ? createElement("a") : null,
    top: withTopLink ? createElement("a") : null,
  };
  if (navigation.sidebar) navigation.sidebar.dataset.unread = unread;
  if (navigation.top) navigation.top.dataset.unread = unread;
  const toastContainer = createElement("div");

  const documentObj = {
    body: createElement("body"),
    getElementById(id) {
      if (id === "nav-messages-link") return navigation.sidebar;
      if (id === "nav-messages-link-top") return navigation.top;
      if (id === "toast-container") return toastContainer;
      return null;
    },
    createElement,
    addEventListener(type, listener) {
      const listeners = documentListeners.get(type) || [];
      listeners.push(listener);
      documentListeners.set(type, listeners);
      if (type === "DOMContentLoaded") {
        listener();
      }
    },
    dispatchEvent(event) {
      for (const listener of documentListeners.get(event.type) || []) {
        listener.call(this, event);
      }
    },
  };

  class FakeWebSocket {
    constructor() {
      this.closeCalls = 0;
      this.closed = false;
      FakeWebSocket.instances.push(this);
    }

    close() {
      this.closeCalls += 1;
      this.closed = true;
    }
  }
  FakeWebSocket.instances = [];

  function setFakeTimeout(fn, delay = 0) {
    const timer = {
      id: nextTimerId,
      fn,
      delay,
      active: true,
    };
    nextTimerId += 1;
    timers.push(timer);
    return timer.id;
  }

  function clearFakeTimeout(timerId) {
    clearedTimerIds.push(timerId);
    const timer = timers.find((candidate) => candidate.id === timerId);
    if (timer) timer.active = false;
  }

  const mathObj = Object.create(Math);
  mathObj.random = () => random;

  const context = {
    document: documentObj,
    window: {
      location: {
        protocol: "http:",
        host: "example.test",
        pathname,
      },
      setTimeout: setFakeTimeout,
      clearTimeout: clearFakeTimeout,
      WebSocket: FakeWebSocket,
    },
    WebSocket: FakeWebSocket,
    Math: mathObj,
    setTimeout: setFakeTimeout,
    clearTimeout: clearFakeTimeout,
  };
  context.window.window = context.window;
  context.window.document = documentObj;
  vm.createContext(context);

  vm.runInContext(scriptSource, context, { filename: "notifications.js" });

  return {
    documentObj,
    activeTimers() {
      return timers.filter((timer) => timer.active);
    },
    clearedTimerIds,
    navigation,
    runTimer(timer) {
      assert.equal(timer.active, true);
      timer.active = false;
      timer.fn();
    },
    sockets: FakeWebSocket.instances,
    timers,
    toastContainer,
    emit(payload) {
      FakeWebSocket.instances[0].onmessage({ data: JSON.stringify(payload) });
    },
  };
}

test("notification toast uses unified semantic structure", () => {
  const harness = createNotificationsHarness();

  harness.emit({
    kind: "system",
    title: "招募完成",
    body: "门客候选已更新",
  });

  const toast = harness.toastContainer.children[0];
  assert.ok(toast.className.includes("toast toast-system"));
  assert.equal(toast.getAttribute("role"), "status");
  assert.equal(toast.getAttribute("aria-live"), "polite");
  assert.equal(toast.querySelector(".toast-icon"), null);
  assert.equal(toast.querySelector(".toast-title").textContent, "招募完成");
  assert.equal(toast.querySelector(".toast-body").textContent, "门客候选已更新");
  assert.equal(toast.querySelector(".toast-close").getAttribute("aria-label"), "关闭通知");

  toast.querySelector(".toast-close").dispatchEvent({ type: "click" });

  assert.ok(toast.classList.contains("toast-leaving"));
});

test("domain details without kind do not trigger a specialized branch", () => {
  const harness = createNotificationsHarness({ pathname: "/notifications-test/" });

  harness.emit({
    title: "未分类通知",
    body: "保留原始正文",
    data: { building_key: "granary", level: 2 },
  });

  const toast = harness.toastContainer.children[0];
  assert.ok(toast.className.includes("toast toast-system"));
  assert.equal(toast.querySelector(".toast-body").textContent, "保留原始正文");
});

test("canonical top-level fields override conflicting data", () => {
  const harness = createNotificationsHarness({ pathname: "/notifications-test/" });

  harness.emit({
    type: "notification",
    kind: "guild",
    title: "顶层标题",
    body: "顶层正文",
    timestamp: "2026-07-10T12:00:00+00:00",
    data: {
      type: "nested-type",
      kind: "system",
      title: "嵌套标题",
      body: "嵌套正文",
      timestamp: "nested-timestamp",
      building_key: "granary",
      level: 2,
    },
  });

  const toast = harness.toastContainer.children[0];
  assert.ok(toast.className.includes("toast toast-guild"));
  assert.equal(toast.querySelector(".toast-title").textContent, "顶层标题");
  assert.equal(toast.querySelector(".toast-body").textContent, "顶层正文");
});

test("canonical data fields drive specialized notification toasts", () => {
  const cases = [
    {
      payload: {
        type: "notification",
        kind: "system",
        title: "粮仓升级完成",
        body: "",
        building_key: "legacy-granary",
        level: 99,
        data: { building_key: "granary", level: 2 },
      },
      expectedBody: "当前等级 Lv2",
    },
    {
      payload: {
        kind: "system",
        title: "农田升级完成",
        building_key: "farm",
        level: 5,
      },
      expectedBody: "当前等级 Lv5",
    },
    {
      payload: {
        type: "notification",
        kind: "system",
        title: "军阵研究完成",
        body: "",
        data: { tech_key: "formation", level: 4 },
      },
      expectedBody: "当前等级 Lv4",
    },
    {
      payload: {
        type: "notification",
        kind: "battle",
        title: "战报更新",
        body: "",
        data: { mission_key: "patrol", mission_name: "边境巡逻" },
      },
      expectedBody: "边境巡逻 已完成",
    },
    {
      payload: {
        type: "notification",
        kind: "auction_won",
        title: "拍卖中标",
        body: "",
        data: { item_name: "青铜剑", quantity: 2, price: 88 },
      },
      expectedBody: "青铜剑 x2，成交价 88 金条",
    },
    {
      payload: {
        type: "notification",
        kind: "auction_outbid",
        title: "竞拍出局",
        body: "",
        data: { item_name: "青铜剑", new_price: 99 },
      },
      expectedBody: "青铜剑，当前最低中标价 99 金条",
    },
  ];

  for (const { payload, expectedBody } of cases) {
    const harness = createNotificationsHarness({ pathname: "/notifications-test/" });
    harness.emit(payload);

    const toast = harness.toastContainer.children[0];
    assert.equal(toast.querySelector(".toast-body")?.textContent, expectedBody);
  }
});

test("notification count follows replacement navigation nodes", () => {
  const harness = createNotificationsHarness({ unread: "2", withTopLink: true });
  const oldSidebar = harness.navigation.sidebar;
  const oldTop = harness.navigation.top;
  const newSidebar = createElement("a");
  const newTop = createElement("a");
  const existingBadge = createElement("span");
  existingBadge.className = "nav-badge";
  existingBadge.textContent = "0";
  newTop.appendChild(existingBadge);
  newSidebar.dataset.unread = "0";
  newTop.dataset.unread = "0";
  harness.navigation.sidebar = newSidebar;
  harness.navigation.top = newTop;

  harness.documentObj.dispatchEvent({ type: "partial-nav:loaded" });

  assert.equal(newSidebar.dataset.unread, "2");
  assert.equal(newTop.dataset.unread, "2");
  assert.equal(newTop.children.filter((child) => child.tagName === "span").length, 1);
  assert.equal(newTop.querySelector(".nav-badge"), existingBadge);
  assert.equal(existingBadge.textContent, "2");

  harness.emit({ kind: "system", title: "新通知", body: "正文" });

  assert.equal(newSidebar.textContent, "消息 (3)");
  assert.equal(newSidebar.dataset.unread, "3");
  assert.equal(newTop.dataset.unread, "3");
  assert.equal(existingBadge.textContent, "3");
  assert.equal(oldSidebar.dataset.unread, "2");
  assert.equal(oldTop.dataset.unread, "2");
});

test("notification runtime starts before navigation nodes exist", () => {
  const harness = createNotificationsHarness({ withSidebarLink: false });
  const newSidebar = createElement("a");
  newSidebar.dataset.unread = "7";
  harness.navigation.sidebar = newSidebar;

  harness.documentObj.dispatchEvent({ type: "partial-nav:loaded" });

  assert.equal(newSidebar.textContent, "消息 (7)");
  assert.equal(newSidebar.dataset.unread, "7");

  harness.emit({ kind: "system", title: "新通知", body: "正文" });

  assert.equal(newSidebar.textContent, "消息 (8)");
  assert.equal(newSidebar.dataset.unread, "8");
});

test("terminal authentication closes do not reconnect", () => {
  for (const code of [4401, 4403]) {
    const harness = createNotificationsHarness();

    harness.sockets[0].onclose({ code });

    assert.equal(harness.activeTimers().length, 0);
    assert.equal(harness.sockets.length, 1);
  }

  const transientHarness = createNotificationsHarness();
  transientHarness.sockets[0].onclose({ code: 1006 });

  assert.equal(transientHarness.activeTimers().length, 1);
  transientHarness.runTimer(transientHarness.activeTimers()[0]);
  assert.equal(transientHarness.sockets.length, 2);
});

test("transient service closes schedule only one backoff reconnect", () => {
  const harness = createNotificationsHarness();
  const firstSocket = harness.sockets[0];

  firstSocket.onclose({ code: 1013 });
  firstSocket.onclose({ code: 1013 });

  assert.equal(harness.activeTimers().length, 1);
  assert.equal(harness.sockets.length, 1);

  harness.runTimer(harness.activeTimers()[0]);

  assert.equal(harness.sockets.length, 2);
});

test("transient closes after open use capped exponential backoff", () => {
  const harness = createNotificationsHarness({ random: 0.5 });
  const observedDelays = [];

  for (const expectedDelay of [2000, 4000, 8000, 15000, 15000]) {
    const socket = harness.sockets.at(-1);
    socket.onopen();
    socket.onclose({ code: 1013 });

    const reconnectTimer = harness.activeTimers().find((timer) => timer.delay !== 30000);
    assert.ok(reconnectTimer);
    observedDelays.push(reconnectTimer.delay);
    assert.equal(reconnectTimer.delay, expectedDelay);
    harness.runTimer(reconnectTimer);
  }

  assert.deepEqual(observedDelays, [2000, 4000, 8000, 15000, 15000]);
});

test("positive reconnect jitter never exceeds the actual delay cap", () => {
  const harness = createNotificationsHarness({ random: 1 });
  const observedDelays = [];

  for (let attempt = 0; attempt < 5; attempt += 1) {
    const socket = harness.sockets.at(-1);
    socket.onopen();
    socket.onclose({ code: 1013 });

    const reconnectTimer = harness.activeTimers().find((timer) => timer.delay !== 30000);
    assert.ok(reconnectTimer);
    observedDelays.push(reconnectTimer.delay);
    harness.runTimer(reconnectTimer);
  }

  assert.deepEqual(observedDelays, [2200, 4400, 8800, 15000, 15000]);
});

test("negative reconnect jitter follows the exact lower-bound sequence", () => {
  const harness = createNotificationsHarness({ random: 0 });
  const observedDelays = [];

  for (let attempt = 0; attempt < 5; attempt += 1) {
    const socket = harness.sockets.at(-1);
    socket.onopen();
    socket.onclose({ code: 1013 });

    const reconnectTimer = harness.activeTimers().find((timer) => timer.delay !== 30000);
    assert.ok(reconnectTimer);
    observedDelays.push(reconnectTimer.delay);
    harness.runTimer(reconnectTimer);
  }

  assert.deepEqual(observedDelays, [1800, 3600, 7200, 13500, 13500]);
});

test("stale socket handlers cannot mutate the current connection generation", () => {
  const harness = createNotificationsHarness({ random: 0.5, unread: "0" });
  const firstSocket = harness.sockets[0];

  firstSocket.onopen();
  const firstStabilityTimer = harness.activeTimers().find((timer) => timer.delay === 30000);
  assert.ok(firstStabilityTimer);
  firstSocket.onclose({ code: 1013 });
  const firstReconnectTimer = harness.activeTimers().find((timer) => timer.delay === 2000);
  assert.ok(firstReconnectTimer);
  harness.runTimer(firstReconnectTimer);

  const secondSocket = harness.sockets[1];
  secondSocket.onopen();
  const secondStabilityTimer = harness.activeTimers().find((timer) => timer.delay === 30000);
  assert.ok(secondStabilityTimer);

  firstStabilityTimer.fn();
  firstReconnectTimer.fn();
  firstSocket.onopen();
  firstSocket.onmessage({ data: JSON.stringify({ kind: "system", title: "过期通知", body: "忽略" }) });
  firstSocket.onclose({ code: 1013 });
  firstSocket.onerror();

  assert.equal(harness.sockets.length, 2);
  assert.equal(secondSocket.closeCalls, 0);
  assert.equal(secondSocket.closed, false);
  assert.equal(secondStabilityTimer.active, true);
  assert.deepEqual(
    harness.activeTimers().filter((timer) => timer.delay === 30000).map((timer) => timer.id),
    [secondStabilityTimer.id],
  );
  assert.equal(harness.toastContainer.children.length, 0);
  assert.equal(harness.navigation.sidebar.dataset.unread, "0");
  assert.equal(
    harness.activeTimers().filter((timer) => ![5000, 30000].includes(timer.delay)).length,
    0,
  );

  secondSocket.onclose({ code: 1013 });
  const reconnectTimers = harness.activeTimers().filter((timer) => ![5000, 30000].includes(timer.delay));
  assert.deepEqual(reconnectTimers.map((timer) => timer.delay), [4000]);
});

test("a connection stable for 30 seconds resets reconnect backoff", () => {
  const harness = createNotificationsHarness({ random: 0.5 });

  harness.sockets.at(-1).onclose({ code: 1013 });
  harness.runTimer(harness.activeTimers()[0]);
  harness.sockets.at(-1).onclose({ code: 1013 });
  harness.runTimer(harness.activeTimers()[0]);

  const stableSocket = harness.sockets.at(-1);
  stableSocket.onopen();
  const stabilityTimer = harness.activeTimers().find((timer) => timer.delay === 30000);
  assert.ok(stabilityTimer);
  harness.runTimer(stabilityTimer);
  stableSocket.onclose({ code: 1013 });

  assert.equal(harness.activeTimers()[0].delay, 2000);
});

test("a parseable message resets backoff and cancels the stability timer", () => {
  const harness = createNotificationsHarness({ random: 0.5 });

  harness.sockets.at(-1).onclose({ code: 1013 });
  harness.runTimer(harness.activeTimers()[0]);
  harness.sockets.at(-1).onclose({ code: 1013 });
  harness.runTimer(harness.activeTimers()[0]);

  const socket = harness.sockets.at(-1);
  socket.onopen();
  const stabilityTimer = harness.activeTimers().find((timer) => timer.delay === 30000);
  assert.ok(stabilityTimer);
  socket.onmessage({ data: JSON.stringify({ kind: "system", title: "有效通知" }) });

  assert.equal(stabilityTimer.active, false);
  assert.ok(harness.clearedTimerIds.includes(stabilityTimer.id));
  socket.onclose({ code: 1013 });
  const reconnectTimer = harness.activeTimers().find((timer) => timer.delay === 2000);
  assert.ok(reconnectTimer);
});

test("malformed JSON does not reset backoff or clear the stability timer", () => {
  const harness = createNotificationsHarness({ random: 0.5 });

  harness.sockets.at(-1).onclose({ code: 1013 });
  harness.runTimer(harness.activeTimers()[0]);

  const socket = harness.sockets.at(-1);
  socket.onopen();
  const stabilityTimer = harness.activeTimers().find((timer) => timer.delay === 30000);
  assert.ok(stabilityTimer);

  socket.onmessage({ data: "{malformed" });

  assert.equal(stabilityTimer.active, true);
  assert.ok(!harness.clearedTimerIds.includes(stabilityTimer.id));
  socket.onclose({ code: 1013 });
  const reconnectTimer = harness.activeTimers().find((timer) => timer.delay === 4000);
  assert.ok(reconnectTimer);
});

test("notification rendering errors are not swallowed", () => {
  const harness = createNotificationsHarness();
  harness.documentObj.createElement = () => {
    throw new Error("render contract bug");
  };

  assert.throws(
    () => harness.emit({ kind: "system", title: "通知", body: "正文" }),
    /render contract bug/,
  );
});
